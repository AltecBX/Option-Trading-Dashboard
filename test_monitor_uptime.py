"""Tests for monitor_uptime — the decisions, not the network.

The monitor exists because of one outage, and it would be worthless if it
repeated the mistake that outage taught. So the first test here is the one
that pins it: a 302 from Cloudflare Access is NOT health. Anybody who later
"simplifies" this monitor into `if status < 400: ok` has to delete a test
that says, in words, why that is wrong.
"""
import datetime as dt
import os
import unittest

os.environ.setdefault("JERRY_NO_NET", "1")

import monitor_uptime as M


class TheLessonFromTheOutage(unittest.TestCase):
    def test_an_access_redirect_is_not_health(self):
        # Cloudflare Access answers BEFORE Cloudflare talks to Railway. On
        # 2026-09-15 the origin certificate was expired for hours while this
        # exact response was being returned to anyone without a token.
        ok, code, msg = M.classify_health(302, "", None)
        self.assertFalse(ok, "a 302 to the Access login was read as healthy")
        self.assertEqual("BLOCKED BY ACCESS", code)
        self.assertIn("service token", msg)

    def test_a_526_names_the_certificate_and_the_runbook(self):
        ok, code, msg = M.classify_health(526, "", None)
        self.assertFalse(ok)
        self.assertEqual("ORIGIN CERTIFICATE", code)
        self.assertIn("OPERATIONS.md", msg)

    def test_an_expired_certificate_seen_directly_is_named(self):
        ok, code, msg = M.classify_health(
            None, "", Exception("SSL certificate problem: certificate has expired"))
        self.assertFalse(ok)
        self.assertEqual("ORIGIN CERTIFICATE", code)

    def test_two_hundred_with_the_apps_json_is_health(self):
        ok, code, _ = M.classify_health(200, '{"version": 1, "tab_order": []}', None)
        self.assertTrue(ok)
        self.assertEqual("OK", code)

    def test_two_hundred_from_an_error_page_is_not_health(self):
        # A proxy or a parked page can answer 200 with HTML. Insist on JSON.
        ok, code, _ = M.classify_health(200, "<html>it works</html>", None)
        self.assertFalse(ok)
        self.assertEqual("WRONG BODY", code)

    def test_cloudflare_cannot_reach_railway(self):
        for status in (521, 522, 523):
            ok, code, msg = M.classify_health(status, "", None)
            self.assertFalse(ok)
            self.assertEqual("ORIGIN DOWN", code)
            self.assertIn("credit", msg)


class TheFrontDoorStaysShut(unittest.TestCase):
    """Step 7 of the runbook puts the orange cloud back. Steps get skipped."""

    def test_being_turned_away_is_the_pass(self):
        for status in (302, 403):
            ok, code, _ = M.classify_guard(status, None)
            self.assertTrue(ok, f"{status} from a stranger should be a pass")
            self.assertEqual("GUARDED", code)

    def test_a_stranger_reaching_the_app_is_the_loudest_failure(self):
        ok, code, msg = M.classify_guard(200, None)
        self.assertFalse(ok, "the dashboard answered a stranger and that passed")
        self.assertEqual("WIDE OPEN", code)
        self.assertIn("grey cloud", msg)

    def test_an_unreachable_site_does_not_double_report(self):
        # The health probe already said the site is down; saying it twice
        # buries the one line that matters.
        ok, code, _ = M.classify_guard(None, Exception("timed out"))
        self.assertTrue(ok)
        self.assertEqual("UNKNOWN", code)


class TheCertificateWarnsBeforeItBreaks(unittest.TestCase):
    def test_plenty_of_time_is_quiet(self):
        ok, code, _ = M.classify_cert(60)
        self.assertTrue(ok)
        self.assertEqual("OK", code)

    def test_the_two_week_warning_says_do_it_on_a_quiet_evening(self):
        ok, code, msg = M.classify_cert(M.CERT_WARN_DAYS)
        self.assertFalse(ok)
        self.assertEqual("CERTIFICATE EXPIRING", code)
        self.assertIn("market hours", msg)

    def test_the_day_before_the_window_is_still_quiet(self):
        self.assertTrue(M.classify_cert(M.CERT_WARN_DAYS + 1)[0])

    def test_expired_and_wrong_domain_are_different_sentences(self):
        self.assertEqual("CERTIFICATE EXPIRED", M.classify_cert(-1)[1])
        self.assertEqual("CERTIFICATE WRONG DOMAIN", M.classify_cert(-2)[1])
        self.assertNotIn("days ago", M.classify_cert(-1)[2])

    def test_no_origin_host_is_skipped_not_passed(self):
        ok, code, msg = M.classify_cert(None)
        self.assertTrue(ok, "a skipped check must not fail the run")
        self.assertEqual("SKIPPED", code)
        self.assertIn("not checked", msg)


class TheAlertingIsLoudOnceNotForever(unittest.TestCase):
    def test_a_new_outage_always_alerts(self):
        self.assertTrue(M.should_alert(1))

    def test_it_does_not_alert_every_ten_minutes_for_hours(self):
        self.assertFalse(M.should_alert(2))
        self.assertFalse(M.should_alert(5))

    def test_it_speaks_up_again_about_once_an_hour(self):
        self.assertTrue(M.should_alert(M.REALERT_EVERY))
        self.assertTrue(M.should_alert(M.REALERT_EVERY * 2))

    def test_a_healthy_run_never_alerts(self):
        self.assertFalse(M.should_alert(0))

    def test_counting_stops_at_the_last_success(self):
        self.assertEqual(3, M.consecutive_failures(
            ["failure", "failure", "failure", "success", "failure"]))

    def test_a_cancelled_run_is_not_evidence_either_way(self):
        self.assertEqual(2, M.consecutive_failures(
            ["failure", "cancelled", "failure", "success"]))

    def test_no_history_reads_as_a_new_failure_so_it_still_alerts(self):
        # Losing the history must make it noisier, never quieter.
        self.assertTrue(M.should_alert(M.consecutive_failures(["failure"])))


class TheAlertItselfMustNotGoMissing(unittest.TestCase):
    """GitHub Actions exports every env key the workflow lists, so a secret
    that was never set arrives as "" rather than absent — and "" beats
    os.environ.get(key, default). The URL became "/topic", urlopen refused
    it, the error was caught and logged, and the alert saying the site was
    down was quietly dropped. A monitor may fail any way but silently."""

    def test_an_empty_server_variable_still_means_the_default(self):
        self.assertEqual(M.NTFY_DEFAULT, M.ntfy_base(""))
        self.assertEqual(M.NTFY_DEFAULT, M.ntfy_base("   "))

    def test_an_absent_server_variable_still_means_the_default(self):
        self.assertEqual(M.NTFY_DEFAULT, M.ntfy_base(None))

    def test_a_real_server_is_honoured_and_trimmed(self):
        self.assertEqual("https://ntfy.example.com",
                         M.ntfy_base("  https://ntfy.example.com/  "))

    def test_the_result_is_never_a_relative_url(self):
        for raw in ("", "   ", None, "/", "https://ntfy.sh/"):
            self.assertTrue(M.ntfy_base(raw).startswith("http"),
                            f"{raw!r} produced a URL urlopen cannot post to")


class TheCertificateMath(unittest.TestCase):
    def test_days_left_is_counted_from_now(self):
        now = dt.datetime(2026, 9, 15, tzinfo=dt.timezone.utc)
        stamp = "Oct 15 13:25:47 2026 GMT"
        expires = dt.datetime.strptime(stamp, "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=dt.timezone.utc)
        self.assertEqual(30, (expires - now).days)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
