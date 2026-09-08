"""Does the Hedge Funds tab actually draw?

`verify_frontend.js` loads every file and mounts the app, but it never runs
a component's render body. That gap has cost six real defects, every one of
them invisible to the static checks and obvious the moment a browser drew
the panel:

  * `apiFetch` returns a Response the card never unwrapped — every panel blank
  * the sector table drew dashes because rows were flattened to scalars
  * the legacy caveat rendered only in the empty branch
  * the alerts panel said "push is set up" when it was not
  * an import-time NameError took every hedge route to 503
  * a stale server on the port served old records and looked like a bug

They were caught by harnesses that lived in a scratchpad and ran only when
I remembered. This is that harness, in the repo, running in CI.

It is hermetic: the store is built from the same offline fixtures the rest
of the suite uses, and React is served from `fixtures/vendor` — verified
byte-identical to the SRI hashes pinned in `index.html`, so the browser runs
exactly what production runs and CI never depends on a CDN.

It SKIPS rather than fails when Playwright or Chromium is absent, so a
contributor without a browser is not blocked and CI cannot go red for the
wrong reason.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "fixtures" / "vendor"

# The three scripts index.html loads from unpkg, mapped to local copies.
CDN = {"react@18.3.1/umd/react.production.min.js": "react.js",
       "react-dom@18.3.1/umd/react-dom.production.min.js": "react-dom.js",
       "lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js": "lwc.js"}

CHROMIUM = os.environ.get("PLAYWRIGHT_CHROMIUM", "/opt/pw-browsers/chromium")


_SKIP: list = []                       # memoised: _why_skip starts playwright


def _why_skip() -> str | None:
    """Why this cannot run here, or None. A missing browser must SKIP, not
    fail — a contributor without Chromium is not a broken repo. CI installs
    one, which is what turns the skip into a check."""
    if _SKIP:
        return _SKIP[0]
    why = None
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        why = "playwright is not installed"
    if why is None and (not VENDOR.is_dir()
                        or not all((VENDOR / f).exists() for f in CDN.values())):
        why = "fixtures/vendor is missing the pinned browser bundles"
    if why is None and not Path(CHROMIUM).exists():
        try:
            with sync_playwright() as pw:
                if not Path(pw.chromium.executable_path).exists():
                    raise FileNotFoundError(pw.chromium.executable_path)
        except Exception:  # noqa: BLE001
            why = "no chromium build (python3 -m playwright install chromium)"
    _SKIP.append(why)
    return why


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(data_dir: str) -> dict:
    """A stored board, a report, fund records and an alert history — built
    from the same fixtures the offline suites use, never from a live
    capture, so this test cannot drift from the rest of them."""
    sys.path.insert(0, str(HERE))
    import test_hf_scan as F          # the fake CFTC/FINRA/OFR transport
    import hf_scan as SC
    import hf_sources as S
    import hf_watch as HW

    web = F.FakeWeb()
    S._MEM.clear()                                              # noqa: SLF001
    S.configure(fetch_fn=web.fetch, post_fn=web.post, data_dir=data_dir,
                now_fn=lambda: F.NOW)
    funds = {"managers": [], "new_filings": []}
    SC.configure(data_dir=data_dir, now_fn=lambda: F.NOW, funds_fn=lambda: funds,
                 sector_fn=lambda s: {"AAPL": "Technology", "MSFT": "Technology"}.get(s))
    # The providers refuse to fetch under JERRY_NO_NET, and CI sets it for the
    # whole step. The fake transport IS the offline guarantee here, so the flag
    # is lifted for the seed and restored immediately — without this the board
    # builds empty in CI and every content check passes on an empty page,
    # which is the same shape as the bug this file exists to catch. The SERVER
    # still gets JERRY_NO_NET=1 in its own environment.
    no_net = os.environ.pop("JERRY_NO_NET", None)
    try:
        board = SC.build()
        SC.build_report()
    finally:
        if no_net is not None:
            os.environ["JERRY_NO_NET"] = no_net

    # Four managers: two readable sharing a name, one holding it as a put,
    # and an OPAQUE book holding the largest position of all — which must
    # not appear in any consensus row.
    os.makedirs(f"{data_dir}/hf/funds", exist_ok=True)
    records = [
        ("pershing", "Pershing Square Capital Management", "READABLE", "2026-06-30",
         [{"symbol": "UBER", "issuer": "Uber Technologies", "value": 2.2e9},
          {"symbol": "CMG", "issuer": "Chipotle", "value": 1.4e9}],
         {"new": [{"symbol": "UBER", "issuer": "Uber Technologies", "value": 2.2e9}],
          "reduced": [{"symbol": "HHH", "issuer": "Howard Hughes", "value": 3e8}]}),
        ("third_point", "Third Point", "READABLE", "2026-03-31",
         [{"symbol": "UBER", "issuer": "Uber Technologies", "value": 9e8},
          {"symbol": "TSLA", "issuer": "Tesla", "value": 4e8, "put_call": "Put"}],
         {"increased": [{"symbol": "UBER", "issuer": "Uber Technologies", "value": 9e8}],
          "exited": [{"symbol": "HHH", "issuer": "Howard Hughes", "value": 2e8}]}),
        ("greenlight", "Greenlight Capital", "READABLE", "2026-06-30",
         [{"symbol": "TSLA", "issuer": "Tesla", "value": 2e8, "put_call": "Put"}], {}),
        ("citadel", "Citadel Advisors", "OPAQUE", "2026-06-30",
         [{"symbol": "UBER", "issuer": "Uber Technologies", "value": 9e12}], {}),
    ]
    for key, name, turnover, as_of, top, change in records:
        with open(f"{data_dir}/hf/funds/{key}.json", "w", encoding="utf-8") as fh:
            json.dump({"key": key, "name": name, "turnover": turnover, "status": "FILING",
                       "watch_version": HW.HF_WATCH_VERSION, "cik": 1,
                       "holdings": {"as_of": as_of, "public_on": "2026-08-14", "n": len(top)},
                       "top": top, "change": change, "filings": [], "evidence": [],
                       "built_at": "2026-09-07T09:00:00-04:00"}, fh)

    with open(f"{data_dir}/hf/alerts.json", "w", encoding="utf-8") as fh:
        json.dump({"primed": True, "crowding_state": {"sec_financials": "NORMAL"},
                   "sent": [{"key": "13d:A1:elliott", "kind": "ACTIVIST FILING", "sent": True,
                             "at": "2026-09-07T09:00:00-04:00", "title": "Activist filing",
                             "message": ("Elliott Management filed a SC 13D on ACME Corp. "
                                         "Filed September 4, 2026 - 3 days ago.")}]}, fh)
    S.configure(fetch_fn=None, post_fn=None)
    S._MEM.clear()                                              # noqa: SLF001
    return board


@unittest.skipIf(_why_skip(), _why_skip() or "")
class TheHedgeTabDraws(unittest.TestCase):
    server = None
    tmp = None
    base = ""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        _seed(cls.tmp.name)
        port = _free_port()
        cls.base = f"http://127.0.0.1:{port}"
        env = dict(os.environ, JERRY_NO_NET="1", JERRY_DATA_DIR=cls.tmp.name,
                   PORT=str(port))
        env.pop("API_KEY", None)
        cls.log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        cls.server = subprocess.Popen(
            [sys.executable, "options_dashboard.py", "--serve", "--port", str(port)],
            cwd=str(HERE), env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        for _ in range(60):
            try:
                urllib.request.urlopen(f"{cls.base}/api/hf/config", timeout=2)
                return
            except Exception:  # noqa: BLE001
                if cls.server.poll() is not None:
                    break
                time.sleep(1)
        cls.tearDownClass()
        raise AssertionError("the server never came up:\n"
                             + Path(cls.log.name).read_text()[-2000:])

    @classmethod
    def tearDownClass(cls):
        if cls.server and cls.server.poll() is None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill()
        if getattr(cls, "log", None):
            try:
                cls.log.close()
                os.unlink(cls.log.name)
            except Exception:  # noqa: BLE001
                pass
        if cls.tmp:
            cls.tmp.cleanup()

    def _open(self, width=1400, height=900):
        """Open the Hedge Funds tab and return the page, with the CDN
        scripts served locally so the browser runs what production runs."""
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        kw = {"executable_path": CHROMIUM} if Path(CHROMIUM).exists() else {}
        browser = pw.chromium.launch(**kw)
        page = browser.new_page(viewport={"width": width, "height": height})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        def route(r):
            for frag, local in CDN.items():
                if frag in r.request.url:
                    r.fulfill(path=str(VENDOR / local))
                    return
            r.continue_()

        page.route("**/*", route)
        page.goto(f"{self.base}/", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        page.evaluate("""() => {
            const els = [...document.querySelectorAll('button, a, li, span, div')]
              .filter(e => e.children.length === 0 && e.textContent.trim() === 'Hedge Funds');
            const el = els.filter(e => e.getClientRects().length)[0] || els[0];
            if (el) (el.closest('button') || el).click();
        }""")
        page.wait_for_selector(".hf-root", timeout=30000)
        return pw, browser, page, errors

    # ── the checks ─────────────────────────────────────────────────────────

    def test_the_pulse_panel_and_its_sections_draw(self):
        pw, browser, page, errors = self._open()
        try:
            page.get_by_role("button", name="The Pulse").first.click()
            page.wait_for_selector(".hf-names", timeout=30000)
            page.wait_for_selector(".hf-alerts", timeout=30000)
            page.wait_for_timeout(1200)
            text = page.locator(".hf-root").inner_text()
            for want in ("Where trades are crowded",
                         "Which names the readable books agree on",
                         "Held among the ten largest",
                         "What would reach your phone"):
                self.assertIn(want.lower(), text.lower(), f"missing: {want}")
            # An EMPTY board still draws most of those headings, and an empty
            # page would pass every check in this file. This is the line that
            # says readings actually reached the panel.
            self.assertRegex(text, r"futures positions as of [A-Z][a-z]+ \d{1,2}, \d{4}",
                             "the board drew with no readings in it")
        finally:
            browser.close(); pw.stop()

    def test_an_opaque_book_never_reaches_a_consensus_row(self):
        # The seed gives Citadel the largest UBER position of anyone. If it
        # ever appears in a row, the refusal has been lost in the rendering
        # rather than in the engine — which no unit test would catch.
        pw, browser, page, errors = self._open()
        try:
            page.get_by_role("button", name="The Pulse").first.click()
            page.wait_for_selector(".hf-names", timeout=30000)
            page.wait_for_timeout(1200)
            cells = page.locator(".hf-names table td").all_inner_texts()
            self.assertTrue(cells, "the consensus tables drew no rows at all")
            self.assertFalse([c for c in cells if "Citadel" in c],
                             "an OPAQUE book reached a consensus row")
            first_table = page.locator(".hf-names table").first.inner_text()
            self.assertNotIn("TSLA", first_table,
                             "a put reached the holdings table")
        finally:
            browser.close(); pw.stop()

    # The two standing rules are not per-panel, so neither are these two
    # checks: every panel is opened and the whole tab is read.
    PANELS = (("The Pulse", ".hf-names"), ("Named Funds", ".hf-broader"),
              ("Weekly Report", ".hf-report"))

    def _each_panel(self, page):
        for name, ready in self.PANELS:
            page.get_by_role("button", name=name, exact=True).first.click()
            page.wait_for_selector(ready, timeout=30000)
            page.wait_for_timeout(1200)
            yield name

    def test_every_header_carries_a_tooltip(self):
        pw, browser, page, errors = self._open()
        try:
            for name in self._each_panel(page):
                missing = page.evaluate("""() => {
                    const out = [];
                    document.querySelectorAll('.hf-root h4, .hf-root h5, .hf-root th')
                      .forEach((el) => { if (!el.getAttribute('title'))
                          out.push(el.textContent.trim().slice(0, 50)); });
                    return out;
                }""")
                self.assertEqual(missing, [], f"headers without a tooltip on {name}")
        finally:
            browser.close(); pw.stop()

    def test_no_iso_date_reaches_the_screen(self):
        pw, browser, page, errors = self._open()
        try:
            for name in self._each_panel(page):
                text = page.locator(".hf-root").inner_text()
                self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", text),
                                  f"an ISO date is on screen on {name}")
                self.assertIsNone(re.search(r"\d{4}-W\d{1,2}", text),
                                  f"an ISO week is on screen on {name}")
        finally:
            browser.close(); pw.stop()

    def test_the_weekly_report_panel_draws(self):
        pw, browser, page, errors = self._open()
        try:
            page.get_by_role("button", name="Weekly Report").first.click()
            page.wait_for_selector(".hf-report", timeout=30000)
            page.wait_for_timeout(1200)
            text = page.locator(".hf-report").inner_text()
            for want in ("The short version", "Overall exposure",
                         "What this report cannot tell you"):
                self.assertIn(want.lower(), text.lower(), f"missing: {want}")
        finally:
            browser.close(); pw.stop()

    def test_it_draws_on_a_phone_too(self):
        pw, browser, page, errors = self._open(width=390, height=844)
        try:
            page.get_by_role("button", name="The Pulse").first.click()
            page.wait_for_selector(".hf-names", timeout=30000)
            page.wait_for_timeout(1200)
            text = page.locator(".hf-root").inner_text()
            self.assertIn("which names the readable books agree on", text.lower())
            # A table that has not collapsed scrolls the whole page sideways.
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            self.assertLessEqual(overflow, 2, "the page scrolls sideways on a phone")
        finally:
            browser.close(); pw.stop()

    def test_the_page_raises_no_javascript_errors(self):
        pw, browser, page, errors = self._open()
        try:
            page.get_by_role("button", name="The Pulse").first.click()
            page.wait_for_selector(".hf-names", timeout=30000)
            page.wait_for_timeout(1500)
            # Resource failures are the sandbox blocking fonts and the like;
            # a JavaScript error is the product's own.
            real = [e for e in errors if "Failed to load resource" not in e]
            self.assertEqual(real, [], "javascript errors on the page")
        finally:
            browser.close(); pw.stop()


if __name__ == "__main__":
    unittest.main()
