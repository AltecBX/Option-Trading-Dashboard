"""monitor_uptime.py — is the dashboard actually up, and still private?

Runs from GitHub Actions, NOT from Railway. A monitor that lives on the
machine it is watching tells you nothing on the morning that machine is the
problem — which is the morning it matters.

WHY THE OBVIOUS CHECK WOULD HAVE MISSED THE OUTAGE IT WAS BUILT FOR

On 2026-09-15 the dashboard was down from before the open until mid-morning:
Railway's certificate for the custom domain had expired and Cloudflare, on
Full (strict), refused to talk to it. Error 526.

A naive monitor asks for the front page and calls 2xx/3xx healthy. That
monitor would have reported GREEN for the whole outage. Cloudflare Access
answers an unauthenticated request with a 302 to its login page BEFORE
Cloudflare ever contacts Railway — so the 302 says the Access gate is alive
and says nothing whatsoever about the app behind it.

So the health probe carries the Access service token. It goes all the way
through: Cloudflare -> the origin's TLS -> the app -> JSON. Anything short
of that is a failure with a name.

AND THE OPPOSITE FAILURE

The fix for a 526 involves taking Cloudflare out of the path for a few
minutes (grey cloud). Forget to put it back and the dashboard is on the
open internet with no login. That is not hypothetical; it is step 7 of the
runbook, and steps get skipped.

So the second probe sends NO credentials and demands to be turned away. A
200 there means the front door is open to anyone, which is reported as
loudly as an outage.

THE CHECK THAT PREVENTS RATHER THAN REPORTS

Given the origin host, the certificate's expiry is readable directly, and a
warning fourteen days out turns a market-hours outage into a calm evening's
work. The origin host is optional: without it the other two probes still
run, and the monitor says the check was skipped rather than implying it
passed.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

SCHEMA = "uptime/v1"

TIMEOUT = 20
CERT_WARN_DAYS = 14        # warn this far ahead of expiry
CERT_CRITICAL_DAYS = 3
RUNBOOK = "OPERATIONS.md -> Fix \"Invalid SSL certificate\" / Error 526"

# Alert on the first failure, then once an hour while it stays broken. At a
# ten-minute cadence that is every sixth consecutive failure: enough to keep
# a real outage in front of you, few enough that the phone stays usable.
REALERT_EVERY = 6


# ── pure decision logic (unit-tested; no network) ───────────────────────────

def classify_health(status, body, error):
    """What a health probe result means, in words worth waking up to."""
    if error:
        low = str(error).lower()
        if "certificate" in low and ("expire" in low or "verify" in low):
            return (False, "ORIGIN CERTIFICATE",
                    "Railway's certificate for the site has expired or cannot "
                    f"be verified. {RUNBOOK}")
        return (False, "UNREACHABLE", f"No answer from the site: {error}")
    if status == 526:
        return (False, "ORIGIN CERTIFICATE",
                "Cloudflare reached Railway but refused its certificate "
                f"(526). {RUNBOOK}")
    if status in (521, 522, 523):
        return (False, "ORIGIN DOWN",
                f"Cloudflare cannot reach Railway ({status}). Check the "
                "Railway service is deployed and in credit.")
    if status in (301, 302, 303, 307, 308):
        return (False, "BLOCKED BY ACCESS",
                "The health probe was sent to the Cloudflare Access login "
                "instead of the app. The service token is missing, expired, "
                "or no longer allowed by the Access policy.")
    if status == 403:
        return (False, "FORBIDDEN",
                "Cloudflare Access rejected the service token.")
    if status != 200:
        return (False, f"HTTP {status}", f"The site answered {status}.")
    if not _looks_like_app(body):
        return (False, "WRONG BODY",
                "The site answered 200 but not with the app's data.")
    return (True, "OK", "Reached the app through Cloudflare.")


def _looks_like_app(body):
    """A 200 from an error page is still a 200. Insist on the app's JSON."""
    if not body:
        return False
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return False
    return isinstance(data, dict) and bool(data)


def classify_guard(status, error):
    """The unauthenticated probe. Being turned away is the PASS."""
    if error:
        # The guard probe is secondary: if the site is unreachable the health
        # probe already said so, and saying it twice helps nobody.
        return (True, "UNKNOWN", f"Could not test the login gate: {error}")
    if status in (301, 302, 303, 307, 308, 403):
        return (True, "GUARDED", "A stranger is sent to the login.")
    return (False, "WIDE OPEN",
            f"The site answered {status} with no login. Cloudflare Access is "
            "not protecting it — if the DNS record was left on 'DNS only' "
            "(grey cloud), set it back to Proxied (orange).")


def classify_cert(days_left):
    if days_left is None:
        return (True, "SKIPPED", "No origin host configured; expiry not checked.")
    if days_left == -2:
        return (False, "CERTIFICATE WRONG DOMAIN",
                "The origin is serving a certificate that does not cover this "
                f"domain. {RUNBOOK}")
    if days_left < 0:
        return (False, "CERTIFICATE EXPIRED",
                f"The origin certificate has expired. {RUNBOOK}")
    if days_left <= CERT_CRITICAL_DAYS:
        return (False, "CERTIFICATE EXPIRING",
                f"The origin certificate expires in {days_left} days. {RUNBOOK}")
    if days_left <= CERT_WARN_DAYS:
        return (False, "CERTIFICATE EXPIRING",
                f"The origin certificate expires in {days_left} days — renew it "
                f"on a quiet evening, not during market hours. {RUNBOOK}")
    return (True, "OK", f"Origin certificate good for {days_left} more days.")


def consecutive_failures(conclusions):
    """How many runs in a row have failed, newest first, this one included."""
    n = 0
    for c in conclusions:
        if c == "failure":
            n += 1
        elif c in ("success",):
            break
        # cancelled/skipped/None: not evidence either way, keep looking
    return n


def should_alert(failures):
    """First failure, then once an hour. Never silent on a new outage."""
    if failures <= 0:
        return False
    return failures == 1 or failures % REALERT_EVERY == 0


# ── probes ──────────────────────────────────────────────────────────────────

# Cloudflare 403s the default `Python-urllib/3.x` user agent before any of
# this app's own rules run. Measured: curl got 200 and 302 from the same URLs
# that answered urllib 403 — so without a real user agent the monitor reports
# a permanent outage of a perfectly healthy site, which is worse than no
# monitor at all.
USER_AGENT = ("jerrytrade-uptime/1 "
              "(+https://github.com/AltecBX/Option-Trading-Dashboard)")


def _get(url, headers=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json, text/html")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    # Redirects are the SIGNAL here (Access sends one), so never follow them.
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(4096).decode("utf-8", "replace"), None
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(4096).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        return exc.code, body, None
    except Exception as exc:  # noqa: BLE001
        return None, "", exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_health(base, cf_id, cf_secret, api_key):
    headers = {}
    if cf_id and cf_secret:
        headers["CF-Access-Client-Id"] = cf_id
        headers["CF-Access-Client-Secret"] = cf_secret
    if api_key:
        headers["X-API-Key"] = api_key
    status, body, err = _get(base.rstrip("/") + "/api/prefs", headers)
    return classify_health(status, body, err) + (status,)


def probe_guard(base):
    status, _body, err = _get(base.rstrip("/") + "/")
    return classify_guard(status, err) + (status,)


def cert_days_left(origin_host, sni, now=None):
    """Days until the ORIGIN's certificate for `sni` expires.

    None when no origin host is configured. Negative when the certificate is
    already bad — an expired or wrong-domain certificate is the ANSWER here,
    not an error to swallow, so both are reported rather than raised.

    Note it connects to the Railway host but asks for the custom domain by
    name. That is exactly what Cloudflare does, so this sees the certificate
    Cloudflare judges, not the wildcard Railway serves for its own subdomain.
    """
    if not origin_host:
        return None
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((origin_host, 443), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=sni) as tls:
                info = tls.getpeercert()
    except ssl.SSLCertVerificationError as exc:
        low = str(exc).lower()
        if "expired" in low:
            return -1
        if "match" in low or "hostname" in low:
            return -2          # a certificate, but not for this domain
        raise
    stamp = (info or {}).get("notAfter")
    if not stamp:
        raise ValueError("the server sent no certificate expiry")
    expires = _dt.datetime.strptime(stamp, "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=_dt.timezone.utc)
    return (expires - (now or _dt.datetime.now(_dt.timezone.utc))).days


# ── notification ────────────────────────────────────────────────────────────

def notify(title, message, priority=1):
    """Same two providers the app itself uses, same env var names."""
    sent = []
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic:
        server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")
        try:
            req = urllib.request.Request(f"{server}/{topic}",
                                         data=message.encode("utf-8"), method="POST")
            req.add_header("Title", title.encode("ascii", "ignore").decode() or "Jerry")
            req.add_header("Priority", {1: "5", 0: "3", -1: "2"}.get(priority, "3"))
            with urllib.request.urlopen(req, timeout=10) as resp:
                sent.append(("ntfy", resp.status))
        except Exception as exc:  # noqa: BLE001
            print(f"[ntfy] {exc}", file=sys.stderr)
    token = os.environ.get("PUSHOVER_APP_TOKEN", "").strip()
    user = os.environ.get("PUSHOVER_USER_KEY", "").strip()
    if token and user:
        try:
            body = urllib.parse.urlencode({
                "token": token, "user": user, "title": title,
                "message": message, "priority": str(int(priority))}).encode()
            req = urllib.request.Request("https://api.pushover.net/1/messages.json",
                                         data=body, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                sent.append(("pushover", resp.status))
        except Exception as exc:  # noqa: BLE001
            print(f"[pushover] {exc}", file=sys.stderr)
    return sent


def recent_conclusions(repo, workflow, token, limit=12):
    """Conclusions of this workflow's previous runs, newest first.

    Used only to decide how NOISY to be. If it cannot be read, the monitor
    treats the failure as new and alerts — erring towards telling you.
    """
    if not (repo and workflow and token):
        return []
    url = (f"https://api.github.com/repos/{repo}/actions/workflows/"
           f"{urllib.parse.quote(workflow)}/runs?status=completed&per_page={limit}")
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        print(f"[history] {exc}", file=sys.stderr)
        return []
    return [r.get("conclusion") for r in data.get("workflow_runs", [])]


# ── entry point ─────────────────────────────────────────────────────────────

def main():
    base = os.environ.get("JT_BASE", "").strip()
    if not base:
        # An unconfigured clone should sit quiet rather than cry wolf. Jerry's
        # own repo says NOT CONFIGURED in the run summary instead, which is
        # visible without being an alert.
        print("NOT CONFIGURED — set the JT_BASE secret to switch monitoring on.")
        return 0

    if not (os.environ.get("CF_ACCESS_CLIENT_ID", "").strip()
            and os.environ.get("CF_ACCESS_CLIENT_SECRET", "").strip()):
        # With a site behind Access and no token, EVERY probe returns the
        # login redirect and the monitor would report a permanent outage it
        # cannot see past. Say which secret is missing instead.
        print("MISCONFIGURED — JT_BASE is set but the Cloudflare Access service "
              "token is not. The health probe cannot reach the app without it.",
              file=sys.stderr)
        return 2

    health_ok, health_code, health_msg, health_status = probe_health(
        base, os.environ.get("CF_ACCESS_CLIENT_ID", "").strip(),
        os.environ.get("CF_ACCESS_CLIENT_SECRET", "").strip(),
        os.environ.get("JT_API_KEY", "").strip())
    guard_ok, guard_code, guard_msg, guard_status = probe_guard(base)

    origin = os.environ.get("RAILWAY_ORIGIN_HOST", "").strip()
    host = urllib.parse.urlsplit(base).hostname or ""
    try:
        days = cert_days_left(origin, host)
    except Exception as exc:  # noqa: BLE001
        days, cert_ok, cert_code, cert_msg = None, False, "CERTIFICATE UNREADABLE", str(exc)
    else:
        cert_ok, cert_code, cert_msg = classify_cert(days)

    lines = [
        f"health  {'PASS' if health_ok else 'FAIL'}  [{health_code}] {health_msg}"
        f"  (http {health_status})",
        f"guard   {'PASS' if guard_ok else 'FAIL'}  [{guard_code}] {guard_msg}"
        f"  (http {guard_status})",
        f"cert    {'PASS' if cert_ok else 'FAIL'}  [{cert_code}] {cert_msg}",
    ]
    report = "\n".join(lines)
    print(report)

    ok = health_ok and guard_ok and cert_ok
    if ok:
        return 0

    history = recent_conclusions(os.environ.get("GITHUB_REPOSITORY", ""),
                                 os.environ.get("MONITOR_WORKFLOW_FILE", "uptime.yml"),
                                 os.environ.get("GITHUB_TOKEN", ""))
    failures = consecutive_failures(["failure"] + history)
    if should_alert(failures):
        headline = next(c for ok_, c in
                        ((health_ok, health_code), (guard_ok, guard_code), (cert_ok, cert_code))
                        if not ok_)
        notify(f"Dashboard: {headline}", report + f"\n\n{base}")
    else:
        print(f"(already alerted; {failures} consecutive failures)")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
