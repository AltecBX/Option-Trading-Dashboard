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

# And what a populated watchlist looks like to the market-context bar: eleven
# sectors, three names each, every one carrying flow. The rotation ribbon draws
# a chip per sector with at least three names, so this is the payload that made
# it spill onto a third row on the live app. Same lesson as HEADLINES above —
# the frame's height depends on the DATA, so a test with no data cannot see it.
_SECTORS = ["Technology", "Financial", "Healthcare", "Energy", "Industrials",
            "Consumer Cyclical", "Consumer Defensive", "Basic Materials",
            "Real Estate", "Utilities", "Communication Services"]
ROTATION_ROWS = [{
    "symbol": f"{s[:2].upper()}{i}",
    "sector": s,
    "flow_available": True,
    "call_prem": 900000 + 10000 * i, "put_prem": 300000 + 5000 * i,
    "ask_call_prem": 500000, "ask_put_prem": 120000,
    "call_sweeps": 8 + i, "put_sweeps": 2,
    "net_prem": 600000, "market_cap": 5.0e10,
    "flow_net": 30, "flow_quality": 70, "flow_alerts": 6, "rel_vol": 1.6,
    "from_ma50": 4.0, "flow_agree": "agrees",
    "last": 100.0 + i, "change_pct": 1.0,
} for s in _SECTORS for i in range(3)]

# And what /api/market_context returns on a CPI morning: four macro events and
# a handful of watchlist names reporting. This is the payload that kept the
# strip at 82px on the live app while the empty sandbox showed 44 — the third
# element in this frame whose height comes from data the sandbox does not have.
MARKET_CONTEXT = {
    "gamma": {"regime": "short", "net_gex": "-2.4"},
    # Eight, not the four a quiet day brings: this row has to be WIDER than the
    # column or it cannot wrap, and a payload that fits is a payload that
    # proves nothing. The first version of this used four and passed with the
    # bug reverted.
    "macro": [
        {"event": "CPI YY", "today": True, "time": "2:00 AM"},
        {"event": "EndYear CPI Fcst/Cb Svy", "today": True, "time": "3:00 AM"},
        {"event": "Core CPI YY, NSA", "today": True, "time": "8:30 AM"},
        {"event": "CPI MM, SA", "today": True, "time": "8:30 AM"},
        {"event": "Initial Jobless Claims", "today": True, "time": "8:30 AM"},
        {"event": "Fed Funds Target Upper", "today": True, "time": "2:00 PM"},
        {"event": "U Mich Sentiment Prelim", "today": False, "time": ""},
        {"event": "Retail Sales Ex-Autos MM", "today": False, "time": ""},
    ],
    "earnings_soon": [{"sym": s, "days": d} for s, d in
                      [("ORCL", 0), ("ADBE", 0), ("CPRT", 0), ("DSGX", 0), ("M", 0)]],
}

# A populated watchlist, so the mobile board renders its real header: the
# scan note, the market-flow read and the filter row. With an empty board
# there are no stock cards to be pushed below the fold, and the check below
# would pass on a page that has the defect.
WATCHLIST_ROWS = [{
    "symbol": s, "company": f"{s} Holdings Incorporated", "sector": "Technology",
    "industry": "Semiconductors", "last": 100.0 + i, "change_pct": 1.5,
    "flow_available": True, "call_prem": 900000, "put_prem": 300000,
    "ask_call_prem": 500000, "ask_put_prem": 120000, "call_sweeps": 8,
    "put_sweeps": 2, "net_prem": 600000, "market_cap": 5.0e10, "flow_net": 30,
    "flow_quality": 70, "flow_alerts": 6, "rel_vol": 1.6, "from_ma50": 4.0,
    "flow_agree": "agrees", "volume": 5_000_000, "avg_volume": 3_000_000,
} for i, s in enumerate(
    "AAPL MSFT NVDA AMD META GOOGL AMZN TSLA NFLX CRM ORCL ADBE".split())]

# A board with rows on it, because that is what the live one has on any
# ordinary morning. Nine is a normal day's worth; the defect this guards
# against needs only that the board be non-empty.
ANALYST_ACTIONS = [{
    "symbol": s, "company": f"{s} Holdings Incorporated",
    "firm": "Morgan Stanley", "action_type": "upgrade",
    "action_date": "2026-09-11", "rating_from": "Equal-Weight",
    "rating_to": "Overweight", "prev_target": 180.0, "new_target": 240.0,
    "current_price": 200.0, "upside_pct": 20.0, "impact_score": 78,
    "direction": "up", "fresh_today": True, "source": "Benzinga",
} for s in "AAPL MSFT NVDA AMD META GOOGL AMZN TSLA NFLX".split()]

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

    def _measure(self, width, height, tab="trade"):
        """Open the app with a production-sized news feed and measure the
        frame. Returns (geometry, page errors)."""
        from playwright.sync_api import sync_playwright
        self._tab = tab
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
            # A POPULATED rotation ribbon. Without this the sandbox draws
            # "rotation pending scan…" — one short line that can never wrap,
            # so a check on the frame's height would pass with the bug in it.
            if "/api/market_context" in url:
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MARKET_CONTEXT))
                return
            if "/api/watchlist_table" in url:
                rows = WATCHLIST_ROWS if self._tab == "watchlist" else ROTATION_ROWS
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"rows": rows}))
                return
            # The analyst board sits above the stocks, and it had TWO ways to
            # be tall. Whether it was SCANNING decided whether it collapsed,
            # so which load you got was a coin flip: 329px on one run, 668px
            # on the next with identical code. And an empty stub hid the case
            # that actually matters — the live board usually HAS rows, and
            # measured on the deployment it was 629px of thirteen-column
            # table with the first stock 957px down a 415px workspace. A stub
            # gentler than production is a stub that passes a broken page, so
            # this one is a populated board mid-scan: both at once.
            if "/api/watchlist_analyst" in url:
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"actions": ANALYST_ACTIONS,
                                           "scanning": True,
                                           "detected_at": "2026-09-11T09:31:00Z"}))
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
            "try{localStorage.setItem('jerry_active_tab_v1','" + tab + "');"
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
                mctx: box('.mctx'), ribbon: box('.mctx-ribbon'),
                sidebar: box('.sidebar'), sidebarPos: (() => {
                  const e = document.querySelector('.sidebar');
                  return e ? getComputedStyle(e).position : null; })(),
                secnavOpen: box('.secnav-open'), secnavRow: box('.secnav-row'),
                // A rail that is mounted and display:none is the exact defect
                // the frame was built to end: fetched, polling, unreachable.
                // Counting them is not enough — they were all four THERE in
                // landscape, just invisible — so this counts the ones a
                // person could actually see, and the tabbed card that is
                // supposed to replace them.
                railsMounted: document.querySelectorAll('.lrail, .rrail').length,
                railsVisible: [...document.querySelectorAll('.lrail, .rrail')]
                  .filter(e => getComputedStyle(e).display !== 'none').length,
                hiloCard: box('.hlc-card'),
                firstStock: (() => {
                  const m = document.querySelector('.main');
                  const c = document.querySelector('.wl-cards > *');
                  return (m && c) ? Math.round(c.getBoundingClientRect().top
                                               - m.getBoundingClientRect().top) : null; })(),
                stockCards: document.querySelectorAll('.wl-cards > *').length,
                mctxLine: box('.mctx-line'),
                chips: document.querySelectorAll('.mctx-chip').length,
                catalysts: document.querySelectorAll('.mctx-ev').length,
                earnSyms: document.querySelectorAll('.mctx-earn-sym').length,
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
        add up to it.

        `floor` is a REGRESSION line, not a target. The first version of this
        sat eleven pixels under a locally measured value and went red on CI,
        which renders the same page twelve pixels taller — different font
        metrics, same stylesheet. The frame's height is a sum of type and
        padding, so it is renderer-dependent by a percent or two, and a floor
        that tight measures the runner rather than the layout. Each floor is
        therefore placed in the gap between what the old chrome left and what
        the new chrome leaves, with room on both sides."""
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
                f"({share:.0%}), under the {floor:.0%} floor — the frame around "
                "it has grown back. The floor is a regression line, not a "
                "target: if this is a deliberate change, say why the frame "
                "needs the height rather than lowering the number.")
        finally:
            self._close(handles)

    def test_the_market_strips_stay_one_line_each_when_busy(self):
        """The supporting strips are the frame's most data-dependent part, and
        the frame's height comes straight off the workspace. The rotation
        ribbon draws a chip per sector with three or more names — eleven of
        them on a busy day — and it used to wrap onto a third row and quietly
        take another sixteen pixels.

        The chip count is asserted FIRST and separately. Without the populated
        payload the ribbon reads "rotation pending scan…", one short line that
        cannot wrap, and a height check on it would pass no matter what the CSS
        said. That is exactly how the grid-track defect this file exists for
        got through."""
        geo, errors, handles = self._measure(2152, 1117)
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            # Every one of these counts is asserted BEFORE the heights. Each
            # of the three payloads has to have actually arrived, or the
            # element it feeds is a short placeholder that cannot wrap and the
            # height check below means nothing.
            self.assertGreaterEqual(
                geo["chips"], 8,
                f"the rotation ribbon drew {geo['chips']} chips; with an empty "
                "ribbon this check cannot see the wrap it exists for")
            self.assertGreaterEqual(
                geo["catalysts"], 8,
                f"the catalysts row drew {geo['catalysts']} events; the row has "
                "to be wider than the column or it cannot wrap, and a payload "
                "that fits proves nothing")
            self.assertGreaterEqual(
                geo["earnSyms"], 5,
                f"the earnings list drew {geo['earnSyms']} symbols; with an "
                "empty list this check cannot see the wrap it exists for")
            # nowrap on .mctx-line only stops its DIRECT children moving to a
            # new line. .mctx-events is one child holding every catalyst and
            # the whole earnings list, and it wrapped inside itself — which is
            # why the live app measured 82px here while the sandbox showed 44.
            line = geo["mctxLine"]
            self.assertIsNotNone(line, "the catalysts row is missing")
            self.assertLessEqual(
                line["h"], 26,
                f"the catalysts row is {line['h']}px tall with "
                f"{geo['catalysts']} events and {geo['earnSyms']} earnings "
                "symbols — something inside it is wrapping")
            ribbon = geo["ribbon"]
            self.assertIsNotNone(ribbon, "the rotation ribbon is missing")
            # One row of chips is about 20px. Two rows would be ~45.
            self.assertLessEqual(
                ribbon["h"], 32,
                f"the rotation ribbon is {ribbon['h']}px tall with "
                f"{geo['chips']} chips — it has wrapped onto another row, and "
                "every row it takes comes out of the workspace")
            mctx = geo["mctx"]
            self.assertLessEqual(
                mctx["h"], 60,
                f"the market context strip is {mctx['h']}px tall; it is two "
                "lines plus padding, and a third line is the workspace's")
        finally:
            self._close(handles)

    def test_the_mobile_watchlist_opens_on_its_stocks(self):
        """The destination is called Watchlist and the first stock card began
        798 pixels down a 412-pixel workspace — two screens of explanation, a
        whole-market flow read and eight filter controls above the list. All of
        that is still on the page, folded; what stays out is the view, the
        search and the count."""
        geo, errors, handles = self._measure(440, 956, tab="watchlist")
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            # The board has to have rows, or nothing can be pushed below the
            # fold and this check passes on a page that has the defect.
            self.assertGreaterEqual(
                geo["stockCards"], 8,
                f"the board drew {geo['stockCards']} stock cards; with an empty "
                "list this check cannot see the thing it exists for")
            top = geo["firstStock"]
            self.assertIsNotNone(top, "no stock card found in the workspace")
            self.assertLess(
                top, geo["main"]["h"],
                f"the first stock begins {top}px down a {geo['main']['h']}px "
                "workspace — it is below the fold on the destination named "
                "after it")
        finally:
            self._close(handles)

    def test_a_phone_on_its_side_does_not_get_a_desktop_sidebar(self):
        """956 CSS pixels is past every max-width:900px rule, so rotating the
        phone handed it the full desktop frame — including a 304px fixed
        column carrying a logo, a clock and two badges. That is a third of the
        width, and the tool you rotated the phone to read got the rest."""
        geo, errors, handles = self._measure(956, 440)
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            self.assertEqual(10, geo["tiles"], "the ten charts come first")
            self.assertEqual(
                "fixed", geo["sidebarPos"],
                "the sidebar is in the layout flow in landscape — it should be "
                "an off-canvas drawer, as it is in portrait")
            side = geo["sidebar"]
            self.assertLess(
                side["l"] + side["w"], 4,
                f"the drawer is at x={side['l']} with width {side['w']} — it is "
                "on screen rather than off-canvas")
            main = geo["main"]
            self.assertGreaterEqual(
                main["w"] / geo["vw"], 0.85,
                f"the workspace is {main['w']}px of {geo['vw']} "
                f"({main['w'] / geo['vw']:.0%}); the sidebar is still taking the width")
            # Drawering the sidebar in CSS left the COMPONENTS believing this
            # was a desktop, because useIsPhone() only ever asked about width.
            # So the four high/low rails stayed mounted behind display:none
            # and the tabbed card that replaces them never rendered: all four
            # lists fetched, polling, and unreachable — which is the defect the
            # permanent frame exists to end, reintroduced by the fix for a
            # different one. Two definitions of "phone" is the bug.
            self.assertEqual(
                0, geo["railsVisible"],
                f"{geo['railsVisible']} of {geo['railsMounted']} fixed rails "
                "are visible in a 956px window; they belong in the workspace "
                "card at this size")
            self.assertEqual(
                0, geo["railsMounted"],
                f"{geo['railsMounted']} rails are mounted but hidden — they "
                "are fetching and polling where nobody can reach them")
            self.assertIsNotNone(
                geo["hiloCard"],
                "the four lists are neither rails nor a card here — rotating "
                "the phone lost them entirely")
            # And the picker, for the same reason: the CSS called this a phone
            # while SectionNav still drew the 904px chip strip it replaces.
            self.assertIsNotNone(
                geo["secnavOpen"],
                "the jump control is still the chip strip in landscape")
            self.assertIsNone(
                geo["secnavRow"], "the chip strip is rendering here too")
        finally:
            self._close(handles)

    def test_the_phone_jump_control_is_a_picker_not_a_strip(self):
        """Seventeen chips with clipped labels in a horizontally scrolling row
        does not help you reach a panel near the bottom. The tool picker solved
        the same problem with a searchable sheet."""
        geo, errors, handles = self._measure(440, 956)
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            self.assertIsNotNone(
                geo["secnavOpen"],
                "no jump picker on a phone — the strip is still there")
            self.assertIsNone(
                geo["secnavRow"],
                "the phone is rendering the desktop chip strip as well")
            self.assertLessEqual(
                geo["secnavOpen"]["h"], 48,
                f"the picker is {geo['secnavOpen']['h']}px tall — it is one row")
        finally:
            self._close(handles)

    def test_the_workspace_gets_most_of_a_laptop_screen(self):
        # This is the load-bearing one. Before 340/900 = 38%, after 404/900 =
        # 45%, and the floor sits four points clear on both sides.
        self._check_workspace_share(1440, 900, 0.42)

    def test_the_workspace_gets_most_of_a_big_screen(self):
        # A sanity floor, not a tight regression line, and the comment says so
        # rather than the number pretending otherwise. This sandbox measures
        # 566/1117 = 51% while the LIVE deployment measures 521/1117 = 47% on
        # the same commit — real chart tiles and real rails are a few pixels
        # taller than stubbed ones, and the gap is about four points. A floor
        # set just under the sandbox reading would be a floor the running app
        # does not satisfy, which is how the first version of these numbers
        # went red on CI. So it sits under BOTH readings; the laptop check
        # above is the one that catches a regression.
        self._check_workspace_share(2152, 1117, 0.44)

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
