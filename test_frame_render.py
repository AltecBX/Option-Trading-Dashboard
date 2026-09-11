"""test_frame_render.py (v4.92) — the frame stays on screen, with real data.

This file exists because of one defect that shipped.

The permanent frame put the shell on a single grid track. A `1fr` track does
not cap its item: it grows to the item's MIN-CONTENT width. The bottom feeds
are one unbroken line of headlines, so on the live deployment — with sixty
real headlines and forty ticker quotes — `.frame-bottom` measured 83,838
pixels wide, the shell's column grew with it, and `.frame-top` and
`.frame-body`, which centre themselves inside that column, ended up at
x = 41,119. Everything except the fixed rails and the tapes was off screen.

Every check that existed passed. The static CSS guards passed, the free
variable lint passed, the load harness passed, and thirty destinations were
walked in a real browser at two viewports. They all passed because the news
feed CANNOT REACH ITS SOURCE in the sandbox, so the tape had no width to
inflate the track with. The app was tested where the data was empty and
shipped where the data is not.

So these checks feed the tape the shape of payload production actually
returns, and then assert the only thing that matters: every part of the frame
is still inside the viewport. A layout bug that needs real data to appear
needs real-sized data in the test.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "fixtures" / "vendor"
CDN = {"react@18.3.1/umd/react.production.min.js": "react.js",
       "react-dom@18.3.1/umd/react-dom.production.min.js": "react-dom.js",
       "lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js": "lwc.js"}
CHROMIUM = os.environ.get("PLAYWRIGHT_CHROMIUM", "/opt/pw-browsers/chromium")

# What the live feed looks like: sixty headlines, each a full sentence, each
# tagged with a ticker. Shorter than production, never longer — a guard that
# under-feeds the tape is the guard that missed this.
HEADLINES = [{
    "title": ("GURUFOCUS.COM  The Lovesac Co (LOVE) (Q2 2027) Earnings Call "
              "Highlights: Record Non-Q4 Sales and Tariff Headwinds %d" % i),
    "ticker": ["LOVE", "FLWS", "META", "AAPL", "NVDA",
               "TSLA", "MSFT", "AMD", "SPY", "QQQ"][i % 10],
    "url": "https://example.invalid/%d" % i,
    "source": "GuruFocus",
    "ts": 1781056495,
} for i in range(60)]

_SKIP: list = []


def _why_skip() -> str | None:
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


@unittest.skipIf(_why_skip(), _why_skip() or "")
class TheFrameStaysOnScreen(unittest.TestCase):
    server = None
    tmp = None
    base = ""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        port = _free_port()
        cls.base = f"http://127.0.0.1:{port}"
        env = dict(os.environ, JERRY_NO_NET="1", JERRY_DATA_DIR=cls.tmp.name,
                   PORT=str(port))
        env.pop("API_KEY", None)
        cls.log = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        cls.server = subprocess.Popen(
            [sys.executable, "options_dashboard.py", "--serve", "--port", str(port)],
            cwd=str(HERE), env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        for _ in range(90):
            try:
                urllib.request.urlopen(f"{cls.base}/api/prefs", timeout=2)
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

    def _measure(self, width, height):
        """Open the app with a production-sized news feed and measure the
        frame. Returns (geometry, page errors)."""
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        kw = {"executable_path": CHROMIUM} if Path(CHROMIUM).exists() else {}
        browser = pw.chromium.launch(**kw)
        ctx = browser.new_context(viewport={"width": width, "height": height},
                                  is_mobile=width <= 900, has_touch=width <= 900)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        def route(r):
            url = r.request.url
            for frag, local in CDN.items():
                if frag in url:
                    r.fulfill(path=str(VENDOR / local))
                    return
            # THE POINT OF THIS FILE: a tape with real headlines in it.
            if "/api/finviz_news" in url:
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"items": HEADLINES}))
                return
            if "/api/quote" in url:
                syms = []
                if "tickers=" in url:
                    syms = url.split("tickers=")[-1].split("&")[0].replace("%2C", ",").split(",")
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"results": {
                              s: {"last": 123.45, "change_pct": 1.23} for s in syms if s}}))
                return
            r.continue_()

        page.route("**/*", route)
        page.add_init_script(
            "try{localStorage.setItem('jerry_active_tab_v1','trade');"
            "localStorage.setItem('weeklyOptionsTimer.tweaks.v1',"
            "JSON.stringify({theme:'dark'}))}catch(e){}")
        page.goto(f"{self.base}/", wait_until="domcontentloaded")
        page.wait_for_selector(".shell", timeout=30000)
        page.wait_for_timeout(6000)
        geo = page.evaluate(
            """() => {
              const box = s => { const e = document.querySelector(s); if (!e) return null;
                const r = e.getBoundingClientRect();
                return {l: Math.round(r.left), t: Math.round(r.top),
                        w: Math.round(r.width), h: Math.round(r.height)}; };
              return {
                vw: window.innerWidth, vh: window.innerHeight,
                shell: box('.shell'), top: box('.frame-top'),
                body: box('.frame-body'), bottom: box('.frame-bottom'),
                charts: box('.mko-grid'), main: box('.main'),
                appbar: box('.appbar'), secnav: box('.secnav'),
                drawer: box('.sidebar'), statusline: box('.statusline'),
                // The INNER rail on each side — Daily Low on the left, Daily
                // High on the right. The gap between these and the first card
                // is the band that has to stay small.
                railL: box('.lrail.lrail--daily:not(.rrail)'),
                railR: box('.rrail.rrail--daily'),
                tiles: document.querySelectorAll('.mko-tile').length,
                headlines: document.querySelectorAll('.nt-item, .newsticker a').length,
                bodyScrollW: Math.round(document.documentElement.scrollWidth),
              };
            }""")
        return geo, errors, (pw, browser)

    def _close(self, handles):
        pw, browser = handles
        browser.close()
        pw.stop()

    def _check(self, width, height):
        geo, errors, handles = self._measure(width, height)
        try:
            self.assertFalse(errors, f"page errors at {width}x{height}: {errors[:3]}")
            # The tape actually has content — otherwise this test proves
            # nothing, which is exactly how the defect got through.
            self.assertGreater(
                geo["headlines"], 20,
                f"the tape drew {geo['headlines']} headlines at {width}x{height}; "
                "with an empty tape this check cannot see the bug it exists for")
            for part in ("top", "body", "bottom"):
                b = geo[part]
                self.assertIsNotNone(b, f".frame-{part} is missing at {width}x{height}")
                self.assertGreaterEqual(
                    b["l"], 0,
                    f".frame-{part} starts at x={b['l']} at {width}x{height} — "
                    "the grid track was inflated by the tape's content width")
                self.assertLessEqual(
                    b["l"] + b["w"], geo["vw"] + 2,
                    f".frame-{part} runs to x={b['l'] + b['w']} in a {geo['vw']}px "
                    "viewport")
            self.assertLessEqual(
                geo["bodyScrollW"], geo["vw"] + 2,
                f"the document is {geo['bodyScrollW']}px wide in a {geo['vw']}px viewport")
            self.assertEqual(10, geo["tiles"], "the ten market charts must all be there")
            c = geo["charts"]
            self.assertTrue(c and c["l"] >= 0 and c["l"] + c["w"] <= geo["vw"] + 2,
                            f"the chart grid is off screen at {width}x{height}: {c}")
        finally:
            self._close(handles)

    def test_the_frame_is_on_screen_on_a_wide_desktop(self):
        self._check(2160, 1200)

    def _check_workspace_share(self, width, height, floor):
        """The frame is permanent, which means it is also permanent OVERHEAD.
        At 1440x900 it had grown to 526 of 900 pixels — app bar, a market
        regime line, ten charts, a context strip, an opportunity ribbon and
        four navigation rows — leaving 374 for the tool you came to use, and
        an embedded partner chart got 278 of those. Keeping the charts is the
        point of the frame; letting the chrome around them outweigh the
        workspace is not. This is the rule, not any one of the paddings that
        add up to it."""
        geo, errors, handles = self._measure(width, height)
        try:
            self.assertFalse(errors, f"page errors at {width}x{height}: {errors[:3]}")
            self.assertEqual(10, geo["tiles"],
                             "the ten charts come first — this check must never be "
                             "satisfied by dropping them")
            main = geo["main"]
            self.assertIsNotNone(main, f"no workspace at {width}x{height}")
            share = main["h"] / geo["vh"]
            self.assertGreaterEqual(
                share, floor,
                f"the workspace is {main['h']}px of a {geo['vh']}px window "
                f"({share:.0%}); the frame around it has grown back")
        finally:
            self._close(handles)

    def test_the_workspace_gets_most_of_a_laptop_screen(self):
        # Measured in this harness: 416/900 = 46% after, 340/900 = 38% before.
        # The floor sits below the measured value with room for a pixel or
        # two of font-metric drift, and well above what the old chrome left.
        self._check_workspace_share(1440, 900, 0.45)

    def test_the_workspace_gets_most_of_a_big_screen(self):
        # Measured in this harness: 578/1117 = 52% after, 481/1117 = 43% before.
        self._check_workspace_share(2152, 1117, 0.50)

    def _check_gutter(self, width, height):
        """The rails are fixed to the window edges and the content column is
        centred between them. Those are two independent sums, and when they
        disagree the difference shows up as a stripe of plain background
        between the inner rail and the first card — 34px of it at 2152x1117,
        and up to 88px on a 2560px monitor, because the content was capped at
        a hard-coded 1600px while the rails stopped growing at 190px. The cap
        is now derived from the rail width, so this measures the thing you
        can actually see: how much dead space is left over."""
        geo, errors, handles = self._measure(width, height)
        try:
            self.assertFalse(errors, f"page errors at {width}x{height}: {errors[:3]}")
            bar = geo["appbar"]
            self.assertIsNotNone(bar, f"the app bar is missing at {width}x{height}")
            for side, rail in (("left", geo["railL"]), ("right", geo["railR"])):
                self.assertIsNotNone(rail, f"the inner {side} rail is missing at "
                                           f"{width}x{height} — it should show above 2080px")
                # box() returns left and width, not a right edge.
                gap = (bar["l"] - (rail["l"] + rail["w"])) if side == "left" \
                    else (rail["l"] - (bar["l"] + bar["w"]))
                self.assertGreaterEqual(
                    gap, 0,
                    f"the content column runs UNDER the {side} rail at {width}x{height}")
                self.assertLessEqual(
                    gap, 20,
                    f"{gap}px of dead background between the {side} rail and the "
                    f"first card at {width}x{height} — the content column's width "
                    "no longer follows the rails'")
        finally:
            self._close(handles)

    def test_no_dead_band_between_the_rails_and_the_content(self):
        self._check_gutter(2152, 1117)

    def test_no_dead_band_on_a_monitor_wide_enough_to_cap_the_rails(self):
        # Past ~2400px the rails stop growing at 190px. This is the width where
        # a fixed 1600px content column left the most room unused.
        self._check_gutter(2560, 1400)

    def test_no_dead_band_on_a_4k_monitor(self):
        # The fix for the two widths above was first written with a second cap,
        # `min(2200px, …)`. Above 2988px that cap won over the subtraction, the
        # rails stayed at 190px, and the band came back at 438px a side — worse
        # than the 34px the fix was for. Neither of the checks above could see
        # it, because neither is wide enough to reach the crossover. This one
        # is: 3840 is a 4K monitor, and it is the width a cap fails at.
        self._check_gutter(3840, 1600)

    def test_the_frame_is_on_screen_on_a_laptop(self):
        self._check(1440, 900)

    def test_the_frame_is_on_screen_on_a_phone(self):
        self._check(440, 956)

    def test_the_frame_is_on_screen_in_landscape(self):
        self._check(956, 440)


if __name__ == "__main__":
    unittest.main(verbosity=2)
