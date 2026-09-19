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
from datetime import date, datetime, timedelta
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
    "direction": "up", "fresh_today": True, "source": "unusual whales",
    # v5.00: a fast-lane row carries the minute it printed and the analyst.
    "time_et": "09:31", "analyst": "Keith Weiss",
} for s in "AAPL MSFT NVDA AMD META GOOGL AMZN TSLA NFLX".split()]
# The fast lane's stamp, as /api/watchlist_analyst reports it.
FAST_LANE = {"source": "unusual whales", "last": "2026-09-11T13:32:00Z",
             "added": 1, "error": None, "every_sec": 120}

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


def sell_payload(symbol="DELL", strikes=range(82, 119)):
    """A whole /api/ticker payload whose sell plan is built by the REAL
    engine, not hand-written.

    A stub plan would let the panel pass with the engine broken, and a
    hand-set zone would let it pass with the arithmetic wrong. This walks a
    synthetic price path with a real stock's jumpiness, hands the daily bars
    and a quoted chain to weekly_sell.build_plan, and ships whatever comes
    back — so the render test fails if either the engine or the panel does.
    """
    import math
    import random
    from statistics import NormalDist

    import weekly_sell

    rnd = random.Random(5)
    closes, px = [], 100.0
    for i in range(420):
        px *= 1 + rnd.gauss(0, 0.019) + (0.06 if i % 53 == 0 else 0)
        closes.append(px)
    closes = [c * 100.0 / closes[-1] for c in closes]

    d, bars = date(2024, 6, 3), []
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        bars.append({"date": d.isoformat() + "T12:00:00-04:00",
                     "open": c, "high": c * 1.009, "low": c * 0.991, "close": c,
                     "volume": 1000000})
        d += timedelta(days=1)
    last = date.fromisoformat(bars[-1]["date"][:10])
    expiry = last + timedelta(days=(4 - last.weekday()) % 7 or 7)

    # Weekly rows, the shape load_weekly_data returns.
    rows, wk = [], last - timedelta(days=last.weekday())
    for i in range(32):
        mon = wk - timedelta(days=7 * (i + 1))
        base = closes[-(i + 1) * 5] if (i + 1) * 5 < len(closes) else closes[0]
        rows.append({
            "week_start": mon.isoformat(), "baseline": base,
            "monday_open": base, "friday_close": base * 1.004,
            "week_high": base * 1.03, "week_low": base * 0.97,
            "high_return": 3.0 + i * 0.4, "low_return": -3.0 - i * 0.45,
            "close_return": 0.4, "open_return": 0.0,
            "high_day": i % 5, "low_day": (i + 2) % 5,
            "high_day_name": ["Mon", "Tue", "Wed", "Thu", "Fri"][i % 5],
            "low_day_name": ["Mon", "Tue", "Wed", "Thu", "Fri"][(i + 2) % 5],
            "day_breakdown": {},
        })

    spot = 100.0
    # A chain priced by Black-Scholes at one implied vol, not by eye. Hand
    # numbers made far-OTM weeklies worth dollars, which handed the engine a
    # fake edge and would have let a broken ranking pass this test.
    sessions = weekly_sell.sessions_until(expiry, last) or 5
    T = sessions / 252.0
    vol, rate = 0.30, 0.04
    nd = NormalDist()

    def quote(strike, side):
        d1 = ((math.log(spot / strike) + (rate + vol * vol / 2) * T)
              / (vol * math.sqrt(T)))
        d2 = d1 - vol * math.sqrt(T)
        disc = math.exp(-rate * T)
        if side == "call":
            mid = spot * nd.cdf(d1) - strike * disc * nd.cdf(d2)
            delta = nd.cdf(d1)
        else:
            mid = strike * disc * nd.cdf(-d2) - spot * nd.cdf(-d1)
            delta = nd.cdf(d1) - 1
        mid = max(0.01, round(mid, 2))
        half = max(0.01, round(mid * 0.02, 2))
        return {"strike": float(strike), "bid": round(mid - half, 2),
                "ask": round(mid + half, 2), "last": mid,
                "volume": 120, "openInterest": 900, "iv": vol,
                "delta": round(delta, 4), "theta": -0.05, "gamma": 0.01,
                "vega": 0.1, "delta_est": False, "theta_est": False}

    def leg(side):
        return [quote(k, side) for k in strikes]

    calls, puts = leg("call"), leg("put")

    plan = weekly_sell.build_plan(
        spot=spot, bars=bars, calls=calls, puts=puts, expiration=expiry.isoformat(),
        now=datetime.combine(last, datetime.min.time()) + timedelta(hours=13))

    return {
        "ticker": symbol, "fetchedAt": "2026-09-11 13:31",
        "expiration": expiry.isoformat(), "expirations": [expiry.isoformat()],
        "baselineMode": "friday", "rows": rows, "daily": bars,
        "current": {"current": spot, "baseline": rows[0]["baseline"],
                    "monday_open": spot, "name": symbol, "sector": "Technology",
                    # v5.19: Jerry's PLTR numbers, the ones that wrapped.
                    "dividend_yield": None, "pe": 153.1, "forward_pe": 76.5,
                    "ytd_base": round(spot * 0.8, 2),
                    "earnings": False, "earningsDate": None, "next_earnings": None,
                    "days_to_earnings": None, "week_start": wk.isoformat()},
        "chain": {"calls": calls, "puts": puts, "atm": spot},
        "sellPlan": plan,
        "volRank": None, "volPct": None, "volRankN": None,
        "volRankKind": "hv_proxy", "hvCurrent": None,
        # A past earnings week inside the window, so the chart draws its
        # amber shading and the legend carries BOTH colours — which is what
        # makes "this week is not an earnings week" a checkable claim.
        "earningsHistory": {"past": [bars[len(bars) // 2]["date"][:10]], "next": None},
    }


def _dt_key(label: str):
    """'Jul 6' -> a sortable date. The strip labels its bars this way."""
    return datetime.strptime(label + " 2026", "%b %d %Y")


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

    def _measure(self, width, height, tab="trade", init="", ticker_payload=None,
                 timezone=None, safe_area=None, starred=None, ytd_bases=None):
        """Open the app with a production-sized news feed and measure the
        frame. Returns (geometry, page errors)."""
        from playwright.sync_api import sync_playwright
        self._tab = tab
        self._ticker_payload = ticker_payload
        # v5.20: the sidebar's watchlist chips and what they read. Both are
        # off unless a test asks, so no other test's geometry moves.
        self._starred = starred
        self._ytd_bases = ytd_bases
        pw = sync_playwright().start()
        kw = {"executable_path": CHROMIUM} if Path(CHROMIUM).exists() else {}
        browser = pw.chromium.launch(**kw)
        ctx = browser.new_context(viewport={"width": width, "height": height},
                                  is_mobile=width <= 900, has_touch=width <= 900,
                                  **({"timezone_id": timezone} if timezone else {}))
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
                # `status` matters as much as `rows`. Without a `last_scan`
                # the board draws "No scan yet …" and never draws the scan
                # line or the "N unscanned" hint — and the hint is what made
                # the live status block 55px tall, because the Scan button
                # inside it is a 38px tap target sitting in an 11.5px
                # sentence. A stub with no status is a stub that cannot see
                # the tallest thing on the real screen.
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps({
                              "rows": rows,
                              "status": {"last_scan": "2026-09-11T13:31:00Z",
                                         "universe_size": len(rows),
                                         "scanning": False}}))
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
                                           "fast_lane": FAST_LANE,
                                           "detected_at": "2026-09-11T09:31:00Z"}))
                return
            # The symbol payload, when a test needs the panels that live on
            # real bars and a real chain rather than the empty-state copy.
            if "/api/ticker" in url and self._ticker_payload is not None:
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps(self._ticker_payload))
                return
            if self._starred is not None and url.rstrip("/").endswith("/api/watchlist"):
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"version": 1, "tag_order": [],
                                           "symbols": [{"symbol": sym, "starred": True, "tags": []}
                                                       for sym in self._starred]}))
                return
            if "/api/ytd_base" in url:
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"results": self._ytd_bases or {}}))
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
        # A payload is keyed by ITS symbol. Without opening the app on that
        # symbol the live cache never matches, every panel silently falls
        # back to mock data, and a test meant to measure the real thing
        # measures a fixture of the app's own invention instead.
        if self._ticker_payload is not None:
            page.add_init_script(
                "try{localStorage.setItem('weeklyOptionsTimer.settings.v1',"
                + json.dumps(json.dumps({
                    "ticker": self._ticker_payload.get("ticker", "DELL"),
                    "weeks": 32, "baseline": "friday"}))
                + ")}catch(e){}")
        if init:
            page.add_init_script(init)
        # v5.18: an iPhone in home-screen mode reports its notch and home
        # indicator as safe-area insets, and the stylesheet lays the page out
        # around them with env(). Chromium can emulate exactly those numbers,
        # so a phone test can measure the geometry Jerry's screenshots show
        # instead of guessing at it.
        if safe_area:
            if len(safe_area) == 2:       # upright: (top, bottom)
                top, bottom = safe_area
                left = right = 0
            else:                          # on its side: (top, right, bottom, left)
                top, right, bottom, left = safe_area
            cdp = ctx.new_cdp_session(page)
            cdp.send("Emulation.setSafeAreaInsetsOverride",
                     {"insets": {"top": top, "left": left, "bottom": bottom, "right": right}})
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
                tabbar: box('.tab-bar'), frameCol: box('.frame-col'),
                posture: box('.posture-card'),
                focus: document.body.classList.contains('focus-frame'),
                tabGroups: document.querySelectorAll('.tab-bar .tab-grp').length,
                tabBtns: document.querySelectorAll('.tab-bar .tab-btn').length,
                tabOpen: (() => { const e = document.querySelector('.tab-grp.open'); return e ? e.innerText.trim() : null; })(),
                // v5.14: the divider between the group names and the open
                // group's tools, and the size of the four words above it.
                nav: (() => {
                  const g = document.querySelector('.tab-groups');
                  const grp = document.querySelector('.tab-grp');
                  const last = [...document.querySelectorAll('.tab-grp')].pop();
                  const btn = document.querySelector('.tab-row-btns .tab-btn');
                  if (!g || !grp) return null;
                  const cs = getComputedStyle(g);
                  return {
                    dividerPx: parseFloat(cs.borderRightWidth),
                    dividerColor: cs.borderRightColor,
                    dividerH: Math.round(g.getBoundingClientRect().height),
                    grpFont: parseFloat(getComputedStyle(grp).fontSize),
                    grpH: Math.round(grp.getBoundingClientRect().height),
                    gapToTools: (last && btn)
                      ? Math.round(btn.getBoundingClientRect().left
                                   - last.getBoundingClientRect().right)
                      : null,
                  };
                })(),
                // v5.10: the strike engine's panel, and the chain-order
                // chart. `clipped` is the guard that matters — a sentence
                // that does not fit its column must wrap, never overflow.
                sell: (() => {
                  const c = document.querySelector('.wos-card');
                  if (!c) return {found: false, text: '', plain: '', clipped: []};
                  const clipped = [];
                  for (const e of c.querySelectorAll('*')) {
                    const cs = getComputedStyle(e);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                    if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') continue;
                    if (e.scrollWidth > e.clientWidth + 1 && e.clientWidth > 0) {
                      // Name the child that sticks out, not just the box it
                      // sticks out of — the parent is never the culprit.
                      const pr = e.getBoundingClientRect();
                      let worst = null;
                      for (const k of e.children) {
                        const r = k.getBoundingClientRect();
                        const over = Math.round(r.right - pr.right);
                        if (over > 0 && (!worst || over > worst.over))
                          worst = {cls: String(k.getAttribute('class') || k.tagName).split(' ')[0],
                                   over: over};
                      }
                      clipped.push(String(e.getAttribute('class') || e.tagName).split(' ')[0]
                                   + ' ' + e.scrollWidth + '>' + e.clientWidth
                                   + (worst ? ' via ' + worst.cls + ' +' + worst.over : ''));
                    }
                  }
                  const pl = c.querySelector('.wos-plain');
                  return {found: true, text: (c.innerText || ''),
                          plain: pl ? pl.innerText.trim() : '',
                          clipped: clipped.slice(0, 8)};
                })(),
                oi: (() => {
                  const row = document.querySelector('.oi-chart-row');
                  const hot = document.querySelector('.oi-hot');
                  const kind = el => {
                    const k = String(el.getAttribute('class') || '');
                    if (k.indexOf('oi-bar-strike') >= 0) return 'strike';
                    if (k.indexOf('call') >= 0) return 'call';
                    if (k.indexOf('put') >= 0) return 'put';
                    return k;
                  };
                  return {rowOrder: row ? [...row.children].map(kind) : [],
                          hot: hot ? hot.innerText.replace(/\\s+/g, ' ').trim() : ''};
                })(),
                // v5.11: the app bar's clock and its icon row.
                clock: (() => {
                  const c = document.querySelector('.ab-clock');
                  const mkt = c && c.querySelector('.ab-mkt');
                  const dot = c && c.querySelector('.ab-mkt-dot');
                  const when = c && c.querySelector('.ab-when');
                  if (!c) return null;
                  return {
                    order: [...c.children].map(e =>
                      String(e.getAttribute('class') || '').split(' ')[0]),
                    state: mkt ? mkt.innerText.trim() : null,
                    stateCls: mkt ? String(mkt.getAttribute('class') || '') : '',
                    stateColor: mkt ? getComputedStyle(mkt).color : null,
                    dotColor: dot ? getComputedStyle(dot).backgroundColor : null,
                    when: when ? when.innerText.trim() : null,
                    whenColor: when ? getComputedStyle(when).color : null,
                    icons: [...document.querySelectorAll('.ab-right .ab-icon')]
                             .map(b => (b.innerText || '').trim()),
                  };
                })(),
                // v5.12: the returns card, its recap, and the two chart
                // colours that must not be the same one.
                returns: (() => {
                  const cards = [...document.querySelectorAll('.card')].filter(c => {
                    const k = c.querySelector('.kicker');
                    return k && /weekly returns history/i.test(k.innerText);
                  });
                  const c = cards[0];
                  if (!c) return null;
                  const r = c.getBoundingClientRect();
                  const kids = [...c.children];
                  const last = kids.length ? kids[kids.length - 1].getBoundingClientRect() : null;
                  const sw = [...c.querySelectorAll('.legend .item')].map(it => ({
                    label: it.innerText.trim(),
                    color: (() => { const s2 = it.querySelector('.swatch');
                      return s2 ? getComputedStyle(s2).backgroundColor : null; })(),
                  }));
                  const recap = c.querySelector('.wrc');
                  return {
                    h: Math.round(r.height),
                    dead: last ? Math.round(r.bottom - last.bottom) : null,
                    swatches: sw,
                    recapTiles: c.querySelectorAll('.wrc-tile').length,
                    recapBars: c.querySelectorAll('.wrc-svg rect').length,
                    recapText: recap ? recap.innerText.replace(/\\s+/g, ' ').trim() : '',
                    // v5.15: every line drawn across this chart has to be
                    // readable. The dotted extremes were labelled from the
                    // start; the three dashed medians were not. Collect every
                    // axis-gutter label with its y, so a guard can check both
                    // that the medians are named and that no two labels sit
                    // on top of one another.
                    gutter: (() => {
                      const main = c.querySelector('svg');
                      if (!main) return [];
                      const box = main.getBoundingClientRect();
                      return [...main.querySelectorAll('text.axis-text')]
                        .filter(t => {
                          const r2 = t.getBoundingClientRect();
                          // the gutter ends at the plot's left edge (padL); a wider
                          // window here swallows the first x-axis date label
                          return r2.width > 0 && r2.right <= box.left + box.width * 0.075;
                        })
                        .map(t => ({ text: t.textContent.trim(),
                                     y: Math.round(t.getBoundingClientRect().top),
                                     median: t.classList.contains('wk-median'),
                                     weight: getComputedStyle(t).fontWeight }))
                        .sort((a, b) => a.y - b.y);
                    })(),
                    legendRows: (() => {
                      const l = c.querySelector('.legend');
                      if (!l) return null;
                      const tops = new Set([...l.querySelectorAll('.item')]
                        .map(e => Math.round(e.getBoundingClientRect().top)));
                      return {rows: tops.size,
                              h: Math.round(l.getBoundingClientRect().height)};
                    })(),
                    // Each strip bar titles its own week. First and last
                    // are what pin the strip's direction to its label.
                    // The two SVGs read as one picture, so week i must sit
                    // at the same x in both. Close ring vs range bar.
                    grid: (() => {
                      const main = c.querySelector('svg');
                      const strip = c.querySelector('.wrc-svg');
                      if (!main || !strip) return null;
                      const mid = (el) => { const r = el.getBoundingClientRect();
                        return r.left + r.width / 2; };
                      const rings = [...main.querySelectorAll('circle')]
                        .filter(x => x.getAttribute('r') === '3.6').map(mid);
                      const bars = [...strip.querySelectorAll('rect')].map(mid);
                      const n = Math.min(rings.length, bars.length);
                      let worst = 0;
                      for (let i = 0; i < n; i++)
                        worst = Math.max(worst, Math.abs(bars[i] - rings[i]));
                      return {pairs: n, rings: rings.length, bars: bars.length,
                              worst: Math.round(worst * 10) / 10};
                    })(),
                    axisWeeks: [...c.querySelectorAll('.chart-svg text')]
                      .map(t => t.textContent.trim())
                      .filter(x => /^[A-Z][a-z]{2} [0-9]{1,2}$/.test(x)),
                    recapBarWeeks: [...c.querySelectorAll('.wrc-svg rect title')]
                      .map(t => t.textContent.split(' \\u00b7')[0].trim()),
                  };
                })(),
                doc: {scrollW: document.documentElement.scrollWidth},
                // The smallest visible text in the permanent frame, and who
                // it is. A caption is only "small" if a person reads it, so
                // an element counts when it has a text node of its own.
                smallText: (() => {
                  const out = [];
                  // On a phone the market band mounts in .main and the
                  // sidebar is a drawer; the workspace is where the
                  // captions are, so it is in scope on both.
                  for (const s of ['.frame-top', '.mobile-header', '.tab-bar', '.sidebar', '.main']) {
                    const root = document.querySelector(s); if (!root) continue;
                    for (const e of root.querySelectorAll('*')) {
                      // Chart tick labels live in <svg>; they are axis
                      // annotations, not captions a person reads as text.
                      if (e.closest('svg')) continue;
                      const cs = getComputedStyle(e);
                      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                      if (![...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
                      const fs = parseFloat(cs.fontSize);
                      if (fs < 10) out.push({band: s, cls: String(e.getAttribute('class') || e.tagName).split(' ')[0], size: fs,
                                             txt: (e.innerText || e.textContent || '').trim().slice(0, 20)});
                    }
                  }
                  return out.slice(0, 12); })(),
                // A rail that is mounted and display:none is the exact defect
                // the frame was built to end: fetched, polling, unreachable.
                // Counting them is not enough — they were all four THERE in
                // landscape, just invisible — so this counts the ones a
                // person could actually see, and the tabbed card that is
                // supposed to replace them.
                waaLine: (() => {
                  const s = document.querySelector('.waa-quiet > summary');
                  return s ? s.innerText.replace(/\\s+/g, ' ').trim() : null; })(),
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
                // v5.17: the phone shows the desktop's grouped bar. Which
                // tools a person can actually see and tap, and how tall the
                // bar is for it.
                tabBarPhone: (() => {
                  const bar = document.querySelector('.tab-bar');
                  if (!bar || getComputedStyle(bar).display === 'none') return null;
                  const vis = (sel) => [...bar.querySelectorAll(sel)]
                    .filter(b => b.getBoundingClientRect().width > 0).map(b => b.textContent.trim());
                  return {h: Math.round(bar.getBoundingClientRect().height),
                          groups: vis('.tab-grp'), tools: vis('.tab-btn')}; })(),
                // Where the first tool on Trade begins inside the workspace,
                // and what stands in front of it.
                firstTool: (() => {
                  const m = document.querySelector('.main');
                  const c = document.querySelector('.st-card');
                  return (m && c) ? Math.round(c.getBoundingClientRect().top
                                               - m.getBoundingClientRect().top) : null; })(),
                phoneBand: box('.phone-band'),
                // v5.20: the watchlist chips, each with whatever year-to-date
                // reading it carries. NOT `chips` — that name was taken by
                // the rotation ribbon's chip COUNT, and shadowing it turned
                // a number into a list under a test that reads it as one.
                wlChips: [...document.querySelectorAll('.sb-section .sb-preset-row .preset-pill')]
                  .filter(c => c.closest('.sb-section').textContent.includes('Watchlist'))
                  .map(c => { const y = c.querySelector('.pp-ytd'); const r = c.getBoundingClientRect();
                    return {sym: (c.childNodes[0] || {}).textContent, ytd: y ? y.textContent.trim() : null,
                            cls: y ? y.className : null, lines: Math.round(r.height),
                            clip: c.scrollWidth - c.clientWidth}; }),
                // v5.19: the P/E line and the YTD line under it. `clip` is
                // how much of the text is past the box; a wrapped line
                // shows as height instead.
                sbPe: (() => { const e = document.querySelector('.sb-pe'); if (!e) return null;
                  return {h: Math.round(e.getBoundingClientRect().height), w: Math.round(e.getBoundingClientRect().width),
                          clip: e.scrollWidth - e.clientWidth, text: e.textContent.trim()}; })(),
                sbYtd: (() => { const e = document.querySelector('.sb-ytd'); if (!e) return null;
                  return {h: Math.round(e.getBoundingClientRect().height), clip: e.scrollWidth - e.clientWidth,
                          text: e.textContent.trim(), cls: e.className}; })(),
                // v5.18: the phone's action bar and header, the body's own
                // padding, and the top inset the page actually resolved.
                bottombar: box('.mobile-bottombar'),
                mobileHeader: box('.mobile-header'),
                bodyPad: [getComputedStyle(document.body).paddingTop,
                          getComputedStyle(document.body).paddingBottom],
                insetTop: (() => { const d = document.createElement('div');
                  d.style.cssText = 'position:fixed;top:0;width:1px;height:env(safe-area-inset-top,0px);pointer-events:none;';
                  document.body.appendChild(d); const h = Math.round(d.getBoundingClientRect().height);
                  d.remove(); return h; })(),
                // The harness runs with the network off, so a throttle
                // banner sits in the workspace that production does not
                // show; it is measured so the tool's position can be read
                // net of it.
                errBannerH: (() => { const e = document.querySelector('.main > .error-banner');
                  return e ? Math.round(e.getBoundingClientRect().height) : 0; })(),
                // What stands in front of the first tool, by name and height.
                mainFirst: (() => {
                  const m = document.querySelector('.main');
                  return m ? [...m.children].filter(c => c.getBoundingClientRect().height > 0).slice(0, 8).map(c => ({
                    cls: String(c.className || c.tagName).split(' ').slice(0, 3).join(' '),
                    tab: c.getAttribute('data-tab'),
                    inner: c.firstElementChild ? String(c.firstElementChild.className || '').split(' ').slice(0, 3).join(' ') : null,
                    h: Math.round(c.getBoundingClientRect().height)})) : null; })(),
                labelClip: [...document.querySelectorAll('.mko-label')]
                  .filter(e => e.scrollWidth > e.clientWidth + 1).length,
                // The phone header's quote must not paint under the buttons
                // beside it: how many pixels of it do not fit.
                identClip: (() => { const e = document.querySelector('.mh-ident');
                  return e ? Math.max(0, e.scrollWidth - e.clientWidth) : null; })(),
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
            # "Its top edge is inside the workspace" is too weak a rule, and
            # the live deployment proved it: v4.97 measured 409px down a 415px
            # workspace — six pixels of a 120px card, a sliver under the
            # filters. That passed `top < height` and is not a stock list you
            # can read. A card is about 120px, so most of one has to be on
            # screen for the destination to have opened on its stocks.
            # "Its top edge is inside the workspace" is too weak a rule, and
            # the live deployment proved it: v4.97 measured 409px down a 415px
            # workspace — SIX pixels of a 120px card, a sliver under the
            # filters. That passed `top < height` and is not a stock list you
            # can read.
            #
            # So the check is how much of the first card you can actually see.
            # The floor is 64 — about half a card — and it is deliberately not
            # the 101px this now measures: a floor is a regression line, not a
            # target, and it belongs in the GAP between the defect (6px) and
            # the fix (101px). Twice this round a threshold set flush against
            # one measurement went red on a machine that rounded differently.
            room = geo["main"]["h"] - top
            self.assertGreaterEqual(
                room, 64,
                f"the first stock begins {top}px down a {geo['main']['h']}px "
                f"workspace, leaving {room}px of it visible — the Watchlist "
                "still opens on everything except stocks")
            # Folding the board is only half of it: the one line left behind
            # has to carry the number. The stub above is a populated board
            # mid-scan, which is what the live one looks like during the
            # morning scan, and the first draft answered "scanning…" there —
            # hiding the count at exactly the hour it is read.
            line = geo["waaLine"]
            self.assertIsNotNone(line, "the analyst board is not folded at all")
            self.assertIn(
                f"{len(ANALYST_ACTIONS)} actions", line,
                f"the folded board says {line!r} — a scan in progress has "
                "replaced the count rather than qualifying it")
            # v5.00: the board is fed every two minutes from Unusual Whales,
            # not only by the 8 AM sweep. The stub reports a fast-lane stamp,
            # so the summary line has to say "live" — "scanned 9:31 AM" alone
            # tells a reader at 11:30 that the board is two hours old.
            self.assertIn(
                "live", line,
                f"the folded board says {line!r} — the fast-lane stamp the "
                "server sent is not on the line")
        finally:
            self._close(handles)

    def test_focus_gives_the_tool_the_frames_height(self):
        """Measured at 1900×1200: 449px of permanent frame before the
        workspace began — ten charts in two rows, three context strips and a
        posture card — and 649px of window left for the tool. Focus keeps
        every one of those things on the page and gives most of that height
        back: the charts become one row of numbers, the context strip folds,
        the posture card keeps its verdict. It is opt-in and remembered, so
        the default frame is measured first and must not have moved."""
        geo0, errors0, h0 = self._measure(1900, 1200)
        try:
            self.assertFalse(errors0, f"page errors: {errors0[:3]}")
            self.assertFalse(geo0["focus"], "focus is on by default — it is opt-in")
            base = geo0["main"]["h"]
        finally:
            self._close(h0)
        geo, errors, handles = self._measure(
            1900, 1200,
            init="try{localStorage.setItem('jerry_focus_frame_v1','1')}catch(e){}")
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            self.assertTrue(geo["focus"], "the remembered switch did not take")
            self.assertEqual(10, geo["tiles"], "focus dropped charts; it folds them")
            self.assertIsNotNone(geo["posture"], "the posture card is gone in focus")
            self.assertIsNotNone(geo["charts"], "the ten instruments are gone in focus")
            self.assertLessEqual(
                geo["charts"]["h"], 60,
                f"the instruments are {geo['charts']['h']}px tall in focus — "
                "that is still two rows of charts, not one row of numbers")
            gained = geo["main"]["h"] - base
            # Measured 190px; the floor sits in the gap, not on the number.
            self.assertGreaterEqual(
                gained, 140,
                f"focus gave the workspace {gained}px ({base} → {geo['main']['h']}); "
                "the frame is still charging the tool for the charts")
        finally:
            self._close(handles)
        # A narrow desktop: the band is ~700px at 1300 wide, where ten columns
        # would be 65px tiles with the price clipped. Focus keeps five columns
        # there and gets two compact rows — still well under the 178px of
        # charts, and every figure still whole.
        geo, errors, handles = self._measure(
            1300, 1000,
            init="try{localStorage.setItem('jerry_focus_frame_v1','1')}catch(e){}")
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            self.assertTrue(geo["focus"])
            self.assertEqual(10, geo["tiles"])
            self.assertLessEqual(
                geo["charts"]["h"], 100,
                f"the instruments are {geo['charts']['h']}px tall in focus at 1300 wide")
            self.assertGreaterEqual(
                geo["charts"]["w"] / geo["tiles"] * 2, 120,
                "ten tiles in one row at this width — they cannot hold a price")
        finally:
            self._close(handles)

    def test_the_navigation_is_one_row_and_nothing_is_lost(self):
        """Four navigation rows were 100px of the workspace column on every
        destination. One row: the four group names on the left, the open
        group's tools on the right, and the open group follows the active
        tab. Every destination is still at most two clicks away."""
        geo, errors, handles = self._measure(1900, 1200)
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            bar = geo["tabbar"]
            self.assertIsNotNone(bar, "no navigation bar on the desktop")
            # Measured 32px; the floor is in the gap between that and the
            # two-row shape (~56) this must never quietly become.
            self.assertLessEqual(
                bar["h"], 44,
                f"the navigation is {bar['h']}px tall — that is more than one row")
            self.assertEqual(4, geo["tabGroups"], "the four group names are the way to every tool")
            # innerText carries the CSS text-transform, so compare case-blind.
            self.assertEqual("workspace", (geo["tabOpen"] or "").lower(),
                             "on the Trade tab the open group is not the one Trade is in")
            self.assertGreaterEqual(geo["tabBtns"], 5,
                                    "the open group's tools are not on the row")
        finally:
            self._close(handles)

    def test_the_bar_is_visibly_two_halves(self):
        """The four group names and the open group's tools are different
        kinds of thing, and the line between them said so in one pixel of
        the same hairline that edges a card — 21px of it in a 23px row. At
        arm's length the bar read as one strip. Measured after: 2px of the
        brighter line, the full height of the row, 30px of air across it,
        and the four words themselves a point larger."""
        geo, errors, handles = self._measure(1900, 1200)
        try:
            nav = geo["nav"]
            self.assertIsNotNone(nav, "no group names in the navigation")
            self.assertGreaterEqual(
                nav["dividerPx"], 2,
                f"the divider is {nav['dividerPx']}px — a hairline again")
            # --line-2 is lighter than --line; in oklch the first number is
            # the lightness, and this asserts the divider is not the card
            # edge's colour by reading it rather than by naming a variable.
            self.assertNotIn("0.3 0.012", nav["dividerColor"],
                             "the divider is back on --line, the card edge")
            # Measured: 23px of divider against 22px group buttons — it
            # crosses the row rather than floating inside it.
            self.assertGreater(
                nav["dividerH"], nav["grpH"],
                "the divider stops short of the row instead of crossing it")
            self.assertGreaterEqual(
                nav["gapToTools"], 24,
                f"only {nav['gapToTools']}px between CONNECTED and the tools")
            self.assertGreaterEqual(
                nav["grpFont"], 11.5,
                f"the group names are {nav['grpFont']}px, not the 11.5 asked for")
        finally:
            self._close(handles)

    def test_nothing_a_person_reads_in_the_frame_is_under_ten_pixels(self):
        """Measured with a probe that lists every visible text element under
        10.5px band by band: eight- and nine-pixel uppercase mono captions in
        the posture card, the context strip, the ribbon and the sidebar.
        Contrast already cleared AA; size was the strain of a screen read
        all day. On a desktop, nothing with its own text is under 10px."""
        geo, errors, handles = self._measure(1900, 1200)
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            small = geo["smallText"]
            self.assertEqual(
                [], small,
                "text under 10px in the permanent frame: "
                + "; ".join(f"{s['band']} {s['cls']} {s['size']}px {s['txt']!r}" for s in small))
        finally:
            self._close(handles)

    def test_nothing_a_person_reads_is_under_ten_pixels_on_a_phone_either(self):
        """v5.04: the same floor on a 440px phone. The probe there listed the
        desktop's classes (the market band mounts in the workspace on a
        phone) plus the tile's points-change and the "Jump to" label. The
        phone floors — first stock card visible, filter row, landscape —
        are measured by their own guards in this file with these sizes."""
        geo, errors, handles = self._measure(440, 956)
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            small = geo["smallText"]
            self.assertEqual(
                [], small,
                "text under 10px on a phone: "
                + "; ".join(f"{s['band']} {s['cls']} {s['size']}px {s['txt']!r}" for s in small))
        finally:
            self._close(handles)

    def test_empty_daily_rails_hand_their_width_to_the_tool(self):
        """On a screen wide enough for the four rails, the two DAILY rails are
        empty for the whole of pre-market — the hours this dashboard is read
        hardest — and each held 190px to say "no names yet". Empty, they are
        now a 30px label on its side, and the frame between them takes the
        380px back. The sandbox has no session data, which is exactly the
        pre-market case."""
        geo, errors, handles = self._measure(2560, 1300)
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            for side in ("railL", "railR"):
                rail = geo[side]
                self.assertIsNotNone(rail, f"{side} is not rendered at 2560px")
                self.assertLessEqual(
                    rail["w"], 34,
                    f"{side} is {rail['w']}px wide with nothing in it")
            # Full rails: 2560 − 2·(2·190 + 14) = 1772. Collapsed: ~2092.
            self.assertGreaterEqual(
                geo["body"]["w"], 2000,
                f"the frame is {geo['body']['w']}px wide between two empty rails "
                "— it did not take the width back")
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

    def test_the_phone_has_the_desktops_navigation(self):
        """v5.17. Jerry, from his phone: "I can't do anything on my mobile
        phone. Please optimize it so I use it like I use it on my desktop."
        The section bar was display:none on phones from v4.92, with the
        bottom bar's Tabs picker in its place — every destination one tap
        away, and none of them visible. The phone now shows the same grouped
        bar, compact: the four groups on one line and the open group's tools
        on the next, and Trade's market band folds into one line so the
        first tool is on the first screen."""
        geo, errors, handles = self._measure(440, 956)
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            bar = geo["tabBarPhone"]
            self.assertIsNotNone(bar, "the section bar is hidden on the phone")
            self.assertEqual(["Workspace", "Scan", "Research", "Connected"], bar["groups"],
                             "the four groups are not all visible on the phone")
            for t in ("Trade", "Analyze", "Watchlist", "Manage"):
                self.assertIn(t, bar["tools"], f"{t} is not a visible tap target on the phone")
            self.assertLessEqual(bar["h"], 84, f"the bar is {bar['h']}px tall on a phone — two short lines, not more")
            # The tool is the first thing. Measured 158px behind the jump
            # control and the folded band; the floor is a regression line.
            self.assertIsNotNone(geo["firstTool"], "no tool on the Trade screen")
            ahead = geo["firstTool"] - geo["errBannerH"]
            self.assertLessEqual(ahead, 200,
                                 f"the first tool begins {ahead}px down the workspace (net of the "
                                 f"harness's {geo['errBannerH']}px offline banner) — the market band "
                                 "is standing in front of it again")
            self.assertIsNotNone(geo["phoneBand"], "the market band fold is gone from Trade")
            self.assertLessEqual(geo["phoneBand"]["h"], 80,
                                 f"the folded band is {geo['phoneBand']['h']}px — it is not folded")
            self.assertEqual(10, geo["tiles"], "the ten charts are still there")
            self.assertEqual(0, geo["identClip"], f"the header quote overflows by {geo['identClip']}px")
            # Focus is the phone's default: the charts as numbers, one tap
            # from the header to bring them back. The desktop default is
            # measured off in test_focus_gives_the_tool_the_frames_height.
            self.assertTrue(geo["focus"], "focus is off by default on a phone")
            self.assertLessEqual(geo["charts"]["h"], 150,
                                 f"the charts are {geo['charts']['h']}px on a phone by default")
        finally:
            self._close(handles)

    def test_focus_on_the_phone_shrinks_the_charts_without_clipping_them(self):
        """The desktop's Focus switch, reached from the phone header. The ten
        tiles become four columns of numbers; every label still fits."""
        geo, errors, handles = self._measure(
            440, 956, init="try{localStorage.setItem('jerry_focus_frame_v1','1')}catch(e){}")
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            self.assertTrue(geo["focus"], "the remembered switch did not take on the phone")
        finally:
            self._close(handles)
        # And the switch works the other way: a remembered "off" brings the
        # full charts back on the phone.
        geo, errors, handles = self._measure(
            440, 956, init="try{localStorage.setItem('jerry_focus_frame_v1','0')}catch(e){}")
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            self.assertFalse(geo["focus"], "a remembered 'off' did not take on the phone")
            self.assertGreaterEqual(geo["charts"]["h"], 200, "the full charts did not come back")
        finally:
            self._close(handles)
        geo, errors, handles = self._measure(
            440, 956, init="try{localStorage.setItem('jerry_focus_frame_v1','1')}catch(e){}")
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            self.assertEqual(10, geo["tiles"], "focus dropped charts on the phone")
            self.assertLessEqual(geo["charts"]["h"], 150,
                                 f"the instruments are {geo['charts']['h']}px tall in focus on a phone")
            self.assertEqual(0, geo["labelClip"], f"{geo['labelClip']} tile labels are clipped in focus")
            # Measured 476 in the render harness (518 in the sandbox); the
            # floor is a regression line, not a target.
            self.assertGreaterEqual(geo["main"]["h"], 440,
                                    f"the workspace is {geo['main']['h']}px in focus on a phone")
        finally:
            self._close(handles)
        # Codex on #404 (P2, both correct): the smallest supported phone. At
        # 320 wide, four focus columns clipped six of the ten labels and the
        # fifth header control pushed the quote under the buttons. Three
        # columns and an icon-only weather pill there.
        geo, errors, handles = self._measure(
            320, 568, init="try{localStorage.setItem('jerry_focus_frame_v1','1')}catch(e){}")
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            self.assertEqual(10, geo["tiles"])
            self.assertEqual(0, geo["labelClip"], f"{geo['labelClip']} tile labels are clipped in focus at 320px")
            self.assertEqual(0, geo["identClip"], f"the header quote overflows by {geo['identClip']}px at 320px")
            self.assertIsNotNone(geo["tabBarPhone"], "the section bar is hidden at 320px")
        finally:
            self._close(handles)

    def test_the_phones_bottom_bar_is_not_under_the_home_indicator(self):
        """v5.18. Jerry, from his iPhone, on v5.17: "But the bottom is still
        cut off." The action bar's lower half was below the screen, and had
        been in his v5.16 screenshot too. The body carried the safe-area
        insets as padding (the ≤760px notch rule) while the shell was
        100dvh tall — so on an iPhone in home-screen mode the shell began
        59px down and ran 59px past the bottom edge, where body
        overflow:hidden cut it. Measured with the insets an iPhone reports
        (59 top, 34 bottom): shell bottom 1015 on a 956px screen, bar
        933–981. The shell carries the insets itself now."""
        geo, errors, handles = self._measure(440, 956, safe_area=(59, 34))
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            self.assertEqual(59, geo["insetTop"], "the harness did not emulate the iPhone insets")
            self.assertEqual(["0px", "0px"], geo["bodyPad"],
                             f"the body is padded {geo['bodyPad']} — the insets are outside the shell again")
            shell = geo["shell"]
            self.assertLessEqual(shell["t"] + shell["h"], geo["vh"],
                                 f"the shell ends {shell['t'] + shell['h'] - geo['vh']}px below the screen")
            bar = geo["bottombar"]
            self.assertIsNotNone(bar, "no action bar on the phone")
            self.assertGreaterEqual(bar["h"], 40, f"the action bar is {bar['h']}px — not a tap target")
            self.assertLessEqual(bar["t"] + bar["h"], geo["vh"] - 34,
                                 f"the action bar ends at {bar['t'] + bar['h']}px on a {geo['vh']}px screen "
                                 "whose bottom 34px is the home indicator")
            hdr = geo["mobileHeader"]
            self.assertIsNotNone(hdr, "no phone header")
            self.assertGreaterEqual(hdr["t"], 59, f"the header begins at {hdr['t']}px — under the notch")
        finally:
            self._close(handles)

    def test_a_phone_on_its_side_keeps_clear_of_the_notch_and_home_indicator(self):
        """Codex on #405: the upright fix lives under max-width:900px, and a
        phone on its side is 956px wide — its own branch, where a later
        short-viewport rule also reset the footer's bottom inset. On its
        side an iPhone reports the notch on one edge, its mirror on the
        other (59px each) and a 21px home indicator below. Measured before:
        the app bar began 24px in, under the notch, and the status line sat
        inside the indicator's 21px."""
        geo, errors, handles = self._measure(956, 440, safe_area=(0, 59, 21, 59))
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            self.assertEqual(["0px", "0px"], geo["bodyPad"], f"the body is padded {geo['bodyPad']}")
            shell = geo["shell"]
            self.assertLessEqual(shell["t"] + shell["h"], geo["vh"], "the shell runs past the bottom")
            appbar = geo["appbar"]
            self.assertIsNotNone(appbar, "no app bar in landscape")
            self.assertGreaterEqual(appbar["l"], 59, f"the app bar begins {appbar['l']}px in — under the notch")
            main = geo["main"]
            self.assertLessEqual(main["l"] + main["w"], geo["vw"] - 59,
                                 f"the workspace ends at {main['l'] + main['w']}px on a {geo['vw']}px screen "
                                 "whose last 59px is the notch's mirror")
            status = geo["statusline"]
            self.assertIsNotNone(status, "no status line in landscape")
            self.assertLessEqual(status["t"] + status["h"], geo["vh"] - 21,
                                 f"the status line ends at {status['t'] + status['h']}px on a {geo['vh']}px "
                                 "screen whose bottom 21px is the home indicator")
        finally:
            self._close(handles)

    def test_the_pe_line_is_one_line_with_the_ytd_under_it(self):
        """v5.19. Jerry, from his phone, on the sidebar: "The P/E 153.1 ·
        Fwd 76.5 should always be on 1 line. Also I want to put the YTD %
        underneath this." The line lived in the price column beside the
        logo, about 120px wide in the phone drawer, and 'P/E 153.1 · Fwd
        76.5' needs ~136px — so 76.5 dropped to a second line. Both lines
        now run the width of the card under the ticker row; YTD is the
        live price against last year's final close."""
        payload = sell_payload()
        base = payload["current"]["ytd_base"]
        # The harness answers every live quote with 123.45, and YTD is the
        # LIVE price against the base — not the payload's own close — so the
        # expected figure is built from the stub, which also proves which
        # price the line reads.
        live = 123.45
        pct = (live - base) / base * 100
        expected = f"YTD {'+' if pct >= 0 else ''}{pct:.1f}%"
        for w, h in ((390, 844), (440, 956), (1440, 900)):
            geo, errors, handles = self._measure(w, h, ticker_payload=payload)
            try:
                self.assertFalse(errors, f"page errors at {w}px: {errors[:3]}")
                pe = geo["sbPe"]
                self.assertIsNotNone(pe, f"no P/E line at {w}px")
                self.assertEqual("P/E 153.1 · Fwd 76.5", pe["text"])
                self.assertLessEqual(pe["h"], 18, f"the P/E line is {pe['h']}px tall at {w}px — it wrapped")
                self.assertEqual(0, pe["clip"], f"the P/E line is cut off by {pe['clip']}px at {w}px")
                ytd = geo["sbYtd"]
                self.assertIsNotNone(ytd, f"no YTD line under the P/E at {w}px")
                self.assertEqual(expected, ytd["text"], f"YTD at {w}px is not the live price against the base")
                self.assertIn("up" if pct >= 0 else "down", ytd["cls"].split())
                self.assertLessEqual(ytd["h"], 18, f"the YTD line is {ytd['h']}px tall at {w}px")
                self.assertEqual(0, ytd["clip"], f"the YTD line is cut off at {w}px")
            finally:
                self._close(handles)

    def test_the_watchlist_chips_carry_their_year_to_date(self):
        """v5.20. Jerry, after the ticker card's YTD line: "Now make the YTD
        show on the watchlist chips too." Each chip reads the price against
        last year's final close, the same anchor the card above it uses.

        The arithmetic is checked, not just the presence of a number, and
        each chip checks a different path. DOUBLE and HALF are not the
        open symbol, so nothing polls a quote for them and they read their
        latest close: 100 against 50 is +100%, 100 against 200 is -50%.
        LIVE is the open symbol, so the harness's quote (123.45) reaches
        it; its stored close is deliberately far away, so reading +0.0%
        instead of +709.7% is what proves the live price wins. NOBASE
        keeps its bare symbol rather than showing a placeholder."""
        starred = ["LIVE", "DOUBLE", "HALF", "NOBASE"]
        geo, errors, handles = self._measure(
            1440, 900, starred=starred,
            init="try{localStorage.setItem('weeklyOptionsTimer.settings.v1',"
                 "JSON.stringify({ticker:'LIVE',weeks:32,baseline:'friday'}))}catch(e){}",
            ytd_bases={"LIVE": {"base": 123.45, "last": 999.0},
                       "DOUBLE": {"base": 50.0, "last": 100.0},
                       "HALF": {"base": 200.0, "last": 100.0}})
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            chips = {c["sym"]: c for c in geo["wlChips"]}
            self.assertEqual(set(starred), set(chips), f"the chips are {list(chips)}")
            self.assertEqual("+100.0%", chips["DOUBLE"]["ytd"])
            self.assertIn("up", chips["DOUBLE"]["cls"].split())
            self.assertEqual("-50.0%", chips["HALF"]["ytd"])
            self.assertIn("down", chips["HALF"]["cls"].split())
            self.assertEqual("+0.0%", chips["LIVE"]["ytd"],
                             "the open symbol's chip read its stored close, not the live quote")
            self.assertIsNone(chips["NOBASE"]["ytd"],
                              "a symbol with no base drew a number anyway")
            for sym, c in chips.items():
                self.assertLessEqual(c["lines"], 30, f"the {sym} chip is {c['lines']}px — it wrapped")
                self.assertEqual(0, c["clip"], f"the {sym} chip is cut off by {c['clip']}px")
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

    def test_the_nav_band_costs_the_workspace_column_not_the_sidebar(self):
        """The section switcher spanned BOTH columns at the foot of the top
        frame, so the sidebar started below it: about 130px of sidebar spent on
        a bar that only ever steers the workspace. Measured at 1900x1200 the
        sidebar was 695px tall to the workspace's 695.

        It now sits in the workspace's own column, over the thing it steers,
        and the sidebar begins level with it. The point of the move is that the
        workspace pays exactly what it paid before — the bar crossed the frame
        boundary, it did not take anything new — so this asserts both halves:
        the sidebar gained, and the workspace did not lose."""
        geo, errors, handles = self._measure(1900, 1200)
        try:
            self.assertFalse(errors, f"page errors: {errors[:3]}")
            self.assertEqual(10, geo["tiles"], "the ten charts are still there")
            bar, side, main = geo["tabbar"], geo["sidebar"], geo["main"]
            for name, box in (("section bar", bar), ("sidebar", side)):
                self.assertIsNotNone(box, f"the {name} is missing")
            # Aligned with the workspace, not with the window.
            self.assertEqual(
                main["l"], bar["l"],
                f"the section bar starts at x={bar['l']} and the workspace at "
                f"x={main['l']} — it is not over the thing it steers")
            self.assertLessEqual(
                abs(bar["w"] - main["w"]), 2,
                f"the section bar is {bar['w']}px wide over a {main['w']}px "
                "workspace — it is still spanning the sidebar")
            # The sidebar starts level with it rather than below it.
            self.assertLessEqual(
                abs(side["t"] - bar["t"]), 2,
                f"the sidebar starts at y={side['t']} and the bar at "
                f"y={bar['t']} — the sidebar is still paying for the bar")
            # And the workspace did not lose height to pay for that. The gain
            # is the bar's OWN height, whatever that is this version (100px
            # as four rows, ~29 as one) — pinning a number here pinned the
            # four rows.
            self.assertGreater(bar["h"], 0, "the section bar has no height")
            self.assertGreaterEqual(
                side["h"], main["h"] + bar["h"] - 2,
                f"the sidebar is {side['h']}px against a {main['h']}px "
                f"workspace and a {bar['h']}px bar — it did not gain the bar's height")
            self.assertGreaterEqual(
                main["h"], 600,
                f"the workspace is {main['h']}px tall at 1900x1200; it paid "
                "for the move it was supposed to be neutral on")
        finally:
            self._close(handles)

    # ── v5.10: the strike engine's panel, and the chain-order chart ──────
    def _sell_probe(self, width=1900, height=1200, payload=None):
        """Open Analyze with a real engine result behind it and read back
        what the panel actually drew."""
        pay = payload if payload is not None else sell_payload()
        geo, errs, handles = self._measure(width, height, tab="analyze",
                                           ticker_payload=pay)
        try:
            self.assertEqual(errs, [], f"the page threw while drawing: {errs[:2]}")
            return geo, pay
        finally:
            self._close(handles)

    def test_the_selling_panel_names_a_strike_instead_of_an_extreme(self):
        # The complaint this was built for: the panel used to headline the
        # worst weekly low of the lookback — a crash, not next Friday. It now
        # has to name a sell zone AND a specific strike, and the zone has to
        # sit far inside that extreme.
        geo, pay = self._sell_probe()
        w = geo["sell"]
        self.assertTrue(w["found"], "the weekly selling panel did not render")
        self.assertIn("SELL ZONE", w["text"].upper())
        plan = pay["sellPlan"]
        self.assertTrue(plan["ok"])
        for side in ("put", "call"):
            strike = plan[side]["pick"]["strike"]
            self.assertIn(f"{strike:,.2f}", w["text"],
                          f"the {side} strike the engine chose is not on screen")
        worst = min(r["low_return"] for r in pay["rows"])
        self.assertLess(abs(plan["band"]["zone"]["low"]["pct"]), abs(worst) * 0.75,
                        "the sell zone is as wide as the worst week in the lookback")

    def test_the_panel_says_in_words_what_it_is_recommending(self):
        # Jerry reads the page, not the tooltips. One plain sentence has to
        # carry the strike, the odds and the payoff without a hover.
        geo, pay = self._sell_probe()
        plain = geo["sell"]["plain"]
        self.assertTrue(plain, "the plain-English line is missing")
        self.assertIn("Sell the", plain)
        self.assertIn("closed", plain)
        self.assertIn("%", plain)

    def test_no_line_in_the_selling_panel_is_cut_off(self):
        # The day-of-week line used to be nowrap inside a column narrower
        # than the sentence, so "weekly HIGH already in: 100%" ran off the
        # end. Nothing in this card may overflow its own box.
        geo, _ = self._sell_probe()
        self.assertEqual(geo["sell"]["clipped"], [],
                         "text is running past the edge of its box")

    def test_the_selling_panel_survives_a_phone(self):
        geo, pay = self._sell_probe(width=440, height=956)
        self.assertTrue(geo["sell"]["found"])
        self.assertEqual(geo["sell"]["clipped"], [])
        self.assertLessEqual(geo["doc"]["scrollW"], 440 + 1,
                             "the panel pushed the page sideways on a phone")

    def test_the_chain_chart_puts_calls_left_and_puts_right(self):
        # Every option chain in the world is laid out this way. The chart had
        # them mirrored, so the eye had to translate on every glance.
        geo, _ = self._sell_probe()
        order = geo["oi"]["rowOrder"]
        self.assertEqual(order, ["call", "strike", "put"],
                         f"the chart is laid out {order}, not calls-left")

    def test_the_chart_names_the_busiest_strikes_without_scrolling(self):
        geo, pay = self._sell_probe()
        hot = geo["oi"]["hot"]
        self.assertTrue(hot, "the heaviest-strike strip did not render")
        self.assertIn("OPEN INTEREST", hot.upper())
        self.assertIn("VOLUME", hot.upper())
        self.assertIn("calls", hot)
        self.assertIn("puts", hot)

    def test_a_week_with_nothing_worth_selling_says_so(self):
        # A chain that only quotes strikes right at the money. Every one of
        # them is assigned far too often, so the honest answer is "don't",
        # and the panel has to give it rather than draw an empty column.
        pay = sell_payload(strikes=range(98, 103))
        plan = pay["sellPlan"]
        self.assertFalse(plan["ok"])
        self.assertIsNone(plan["put"]["pick"])
        geo, _ = self._sell_probe(payload=pay)
        text = geo["sell"]["text"]
        self.assertIn("SELL ZONE", text.upper())
        self.assertIn("worth selling", text)
        self.assertEqual(geo["sell"]["clipped"], [])

    def test_a_payload_with_no_plan_still_draws_the_panel(self):
        # An old cached payload, or a symbol the engine cannot measure. The
        # panel falls back to range location and says so rather than vanishing.
        pay = sell_payload()
        pay.pop("sellPlan")
        geo, _ = self._sell_probe(payload=pay)
        self.assertTrue(geo["sell"]["found"], "the panel disappeared without a plan")
        self.assertIn("RANGE LOCATION", geo["sell"]["text"].upper())

    def test_the_app_bar_has_one_way_into_the_shortcuts_not_two(self):
        # The bar's "?" and the status line's "Shortcuts" opened the same
        # sheet. The status line keeps it, beside Search.
        geo, _, handles = self._measure(1900, 1200)
        try:
            self.assertNotIn("?", geo["clock"]["icons"],
                             "the app bar still carries its own shortcuts button")
        finally:
            self._close(handles)

    def test_the_clock_leads_with_whether_you_can_trade(self):
        # State first and coloured, then a divider, then the date. Whether
        # the market is shut is what the glance is for.
        geo, _, handles = self._measure(1900, 1200)
        try:
            c = geo["clock"]
            self.assertIsNotNone(c, "the app bar clock did not render")
            self.assertEqual(c["order"], ["ab-mkt", "ab-clock-sep", "ab-when"])
            self.assertIn(c["state"], ("Markets Open", "Pre-Market",
                                       "After Hours", "Markets Closed"))
            # v5.12: the DOT carries the state and the label stays neutral.
            # One coloured thing in the row, not two saying the same thing —
            # so these two must now DIFFER, and the label must match the
            # date beside it rather than the dot.
            self.assertNotEqual(c["stateColor"], c["dotColor"],
                                "the label is coloured like the dot again")
            self.assertEqual(c["stateColor"], c["whenColor"],
                             "the label does not match the date beside it")
            # ...and a shut market is the red one.
            if c["state"] == "Markets Closed":
                self.assertIn("ab-mkt-shut", c["stateCls"])
        finally:
            self._close(handles)

    def test_the_date_is_short_and_keeps_its_seconds(self):
        # "Sun, September 13, 2026 3:08:24 PM ET" spent the bar on the least
        # useful part. "Sun SEP 13, 3:08:24 PM ET" says the same thing.
        geo, _, handles = self._measure(1900, 1200)
        try:
            when = geo["clock"]["when"]
            self.assertRegex(when, r"^[A-Z][a-z]{2} [A-Z]{3} \d{1,2}, "
                                   r"\d{1,2}:\d{2}:\d{2} [AP]M ET$",
                             f"the clock reads {when!r}")
            self.assertNotRegex(when, r"\b(19|20)\d{2}\b", "the year is back")
        finally:
            self._close(handles)

    def test_the_returns_card_does_not_end_in_empty_space(self):
        # It drew its chart and stopped, leaving ~200px of empty card while
        # the card beside it ran to the bottom. Whatever fills it, the
        # bottom of the card has to be close to the bottom of its content.
        geo, _, handles = self._measure(1900, 1200, tab="analyze",
                                        ticker_payload=sell_payload())
        try:
            r = geo["returns"]
            self.assertIsNotNone(r, "the weekly returns card did not render")
            self.assertLess(r["dead"], 60,
                            f"{r['dead']}px of unused card under the last thing in it")
        finally:
            self._close(handles)

    def test_every_line_across_the_returns_chart_carries_its_value(self):
        """Five lines cross this chart. The two DOTTED ones — the best high
        and worst low in the window — were labelled on the axis from the
        start. The three DASHED ones, the typical week's high, low and
        close, were not: the only place their values appeared was the
        behaviour-summary card above, which never says it is describing
        them. Now every drawn line names its own number, no two labels
        overlap, and the legend still fits on one row."""
        geo, _, handles = self._measure(1900, 1200, tab="analyze",
                                        ticker_payload=sell_payload())
        try:
            r = geo["returns"]
            gut = r["gutter"]
            self.assertTrue(gut, "no labels in the chart's axis gutter")
            pct = [g for g in gut if g["text"].endswith("%")]
            # Two extremes + three medians + whatever generic ticks survive.
            self.assertGreaterEqual(
                len(pct), 5,
                f"only {len(pct)} labelled values in the gutter: "
                + ", ".join(g["text"] for g in gut))
            # BOLD is the record, NORMAL is the typical: both kinds present.
            bold = [g for g in pct if int(g["weight"]) >= 700]
            self.assertEqual(2, len(bold),
                             "the two extremes are not the only bold labels")
            # A label sitting on another label is a label nobody can read.
            for a, b in zip(gut, gut[1:]):
                self.assertGreaterEqual(
                    b["y"] - a["y"], 11,
                    f"{a['text']!r} and {b['text']!r} overlap in the gutter")
            # Three dashed lines are drawn, so three medians are named.
            self.assertEqual(
                3, len([g for g in gut if g["median"]]),
                "a dashed line was drawn without its value: "
                + ", ".join(g["text"] for g in gut if g["median"]))
            # And the dashes are named, not left to be guessed.
            labels = {sw["label"] for sw in r["swatches"]}
            self.assertIn("Typical week", labels,
                          "nothing in the legend says what the dashed lines are")
            self.assertEqual(1, r["legendRows"]["rows"],
                             "the legend wrapped onto a second row")
        finally:
            self._close(handles)

    def test_two_medians_on_the_same_value_both_keep_their_label(self):
        """Codex, on the first cut of this: a label dropped to avoid a
        collision leaves its dashed line drawn and unexplained, which makes
        the legend's promise that every value is on the axis false. A week
        that closes on its low is enough to do it — the median close lands
        on the median low. Both labels are kept and one is stepped clear."""
        payload = sell_payload()
        lows = sorted(r["low_return"] for r in payload["rows"])
        n = len(lows)
        med_low = (lows[n // 2] if n % 2 else (lows[n // 2 - 1] + lows[n // 2]) / 2)
        # Every week now closes exactly at the typical week's low, so the
        # median close and the median low are the SAME number.
        for r in payload["rows"]:
            r["close_return"] = med_low
        geo, _, handles = self._measure(1900, 1200, tab="analyze",
                                        ticker_payload=payload)
        try:
            gut = geo["returns"]["gutter"]
            meds = [g for g in gut if g["median"]]
            self.assertEqual(
                3, len(meds),
                "a median label was dropped instead of moved: "
                + ", ".join(g["text"] for g in meds))
            # The two equal ones are both there, and readable.
            same = [g for g in meds if g["text"] == f"{med_low:.1f}%"]
            self.assertEqual(
                2, len(same),
                f"the two medians at {med_low:.1f}% are not both labelled: "
                + ", ".join(g["text"] for g in meds))
            self.assertGreaterEqual(
                abs(same[0]["y"] - same[1]["y"]), 10,
                "the two equal medians are printed on top of each other")
            for a, b in zip(gut, gut[1:]):
                self.assertGreaterEqual(
                    b["y"] - a["y"], 10,
                    f"{a['text']!r} and {b['text']!r} overlap in the gutter")
        finally:
            self._close(handles)

    def test_this_week_is_not_coloured_like_an_earnings_week(self):
        # Every past earnings week is shaded amber. The week in progress was
        # shaded the same amber, so the one column that is NOT an earnings
        # week looked like one.
        geo, _, handles = self._measure(1900, 1200, tab="analyze",
                                        ticker_payload=sell_payload())
        try:
            sw = {s["label"]: s["color"] for s in geo["returns"]["swatches"]}
            self.assertIn("This week", sw)
            self.assertIn("Earnings week", sw,
                          "the fixture lost its past-earnings week")
            self.assertNotEqual(sw["This week"], sw["Earnings week"],
                                "this week and an earnings week share one colour")
        finally:
            self._close(handles)

    def test_the_recap_says_what_the_weeks_add_up_to(self):
        geo, _, handles = self._measure(1900, 1200, tab="analyze",
                                        ticker_payload=sell_payload())
        try:
            r = geo["returns"]
            self.assertEqual(r["recapTiles"], 4)
            self.assertGreater(r["recapBars"], 8, "the range strip drew no bars")
            for phrase in ("WEEKS CLOSED GREEN", "TYPICAL WEEK RANGE", "RANGE TREND"):
                self.assertIn(phrase, r["recapText"])
            # The strip says "oldest to newest". A comparator that returns
            # NaN leaves an array untouched, so a strip drawn backwards looks
            # exactly like one drawn forwards — nothing errors, the label
            # just becomes a lie. Read the bars' own week labels and check.
            weeks = r["recapBarWeeks"]
            self.assertGreater(len(weeks), 8, "the strip bars carry no week labels")
            import datetime as _dt
            parsed = [_dt.datetime.strptime(w + " 2026", "%b %d %Y") for w in weeks]
            self.assertEqual(parsed, sorted(parsed),
                             f"the strip is labelled oldest to newest but runs {weeks[0]} to {weeks[-1]}")
        finally:
            self._close(handles)

    def test_the_recap_fits_a_phone_without_clipping(self):
        geo, _, handles = self._measure(440, 956, tab="analyze",
                                        ticker_payload=sell_payload())
        try:
            self.assertEqual(geo["returns"]["recapTiles"], 4)
            self.assertLessEqual(geo["doc"]["scrollW"], 441,
                                 "the recap pushed the page sideways")
        finally:
            self._close(handles)

    def test_both_weekly_charts_render_from_raw_unhydrated_rows(self):
        """The shape data.js has NOT touched.

        Every live path hands these components Dates, because hydrateRows
        converts them first — which means no test that goes through
        /api/ticker can reach the raw-string branch at all. This one mounts
        both components directly, off the page's own React, with rows
        exactly as the server sends them: week_start as an ISO string.

        Two different failures live down here. A string comparator returns
        NaN, which leaves the array untouched and draws the strip backwards
        in silence; and fmtDate calls toLocaleDateString, which a string
        does not have, so the render throws. Order AND survival are checked.
        """
        geo, errors, handles = self._measure(1900, 1200, tab="analyze",
                                             ticker_payload=sell_payload())
        try:
            self.assertEqual(errors, [], f"the page threw before the test ran: {errors[:2]}")
            page = handles[1].contexts[0].pages[0]
            out = page.evaluate("""() => {
              const mk = (iso, hi, lo, cl) => ({
                week_start: iso, high_return: hi, low_return: lo,
                close_return: cl, open_return: 0.4,
                high_day: 1, low_day: 3, high_day_name: 'Tue', low_day_name: 'Thu',
              });
              // Deliberately newest-first, the order the server sends.
              const raw = [
                mk('2026-09-07', 6, -4, 1), mk('2026-08-31', 9, -7, -2),
                mk('2026-08-24', 4, -3, 2), mk('2026-08-17', 12, -9, -5),
                mk('2026-08-10', 5, -5, 3), mk('2026-08-03', 7, -2, 4),
                mk('2026-07-27', 3, -8, -1), mk('2026-07-20', 8, -6, 2),
                mk('2026-07-13', 11, -4, 5), mk('2026-07-06', 6, -6, -3),
              ];
              const colors = {up:'#16a34a', down:'#dc2626', warn:'#d97706',
                              now:'#3b6fd4', accent:'#16a34a', fg2:'#999', fg3:'#777'};
              const host = document.createElement('div');
              host.style.cssText = 'position:absolute;left:-9999px;width:900px';
              document.body.appendChild(host);
              const out = {sorted: null, recapThrew: null, chartThrew: null};
              try {
                out.sorted = window._weekRows(raw)
                  .map(r => r.week_start.toISOString().slice(0, 10));
              } catch (e) { out.sorted = 'THREW: ' + e.message; }
              const mount = (el) => {
                const d = document.createElement('div');
                host.appendChild(d);
                const root = ReactDOM.createRoot(d);
                ReactDOM.flushSync(() => root.render(el));
                return d;
              };
              try {
                const d = mount(React.createElement(window.WeeklyRecap,
                  {rows: raw, colors: colors}));
                out.recapThrew = false;
                out.recapBars = [...d.querySelectorAll('.wrc-svg rect title')]
                  .map(t => t.textContent.split(' \\u00b7')[0].trim());
                out.recapTiles = d.querySelectorAll('.wrc-tile').length;
              } catch (e) { out.recapThrew = String(e && e.message || e); }
              try {
                mount(React.createElement(window.ReturnsChart, {
                  rows: raw, medianHigh: 6, medianLow: -5, medianClose: 1,
                  currentReturn: 2, colors: colors, earnings: {past: []},
                }));
                out.chartThrew = false;
              } catch (e) { out.chartThrew = String(e && e.message || e); }
              host.remove();
              return out;
            }""")
            self.assertEqual(out["recapThrew"], False,
                             f"the recap threw on raw rows: {out['recapThrew']}")
            self.assertEqual(out["chartThrew"], False,
                             f"the returns chart threw on raw rows: {out['chartThrew']}")
            self.assertEqual(out["sorted"], sorted(out["sorted"]),
                             f"raw rows did not sort oldest-first: {out['sorted']}")
            self.assertEqual(out["recapTiles"], 4)
            self.assertEqual(out["recapBars"], sorted(
                out["recapBars"], key=lambda w: _dt_key(w)),
                f"the strip drew raw rows out of order: {out['recapBars']}")
        finally:
            self._close(handles)

    def test_week_labels_are_mondays_in_a_browser_west_of_utc(self):
        """The weeks the server sends start on Monday. So must the labels.

        `new Date("2026-09-07")` is midnight UTC, which is the PREVIOUS day
        anywhere west of Greenwich — so in New York every Monday week_start
        rendered as the Sunday before it, across the chart axis, the recap
        strip and both tooltips. It was invisible to every earlier test
        because Playwright runs in UTC unless told otherwise, and invisible
        in review because the dates still looked like plausible dates.

        The browser is put in New York on purpose: a fixture in UTC cannot
        see this class of bug at all.
        """
        pay = sell_payload()
        geo, errors, handles = self._measure(1900, 1200, tab="analyze",
                                             ticker_payload=pay,
                                             timezone="America/New_York")
        try:
            self.assertEqual(errors, [], f"the page threw: {errors[:2]}")
            r = geo["returns"]
            labels = r["axisWeeks"] + r["recapBarWeeks"]
            self.assertGreater(len(labels), 8, "no week labels were drawn")
            # Built from the payload the server actually sent, not from a
            # hardcoded year: every drawn label must be one of THESE days.
            # Off by one in either direction lands outside the set.
            expected = set()
            for row in pay["rows"]:
                d = date.fromisoformat(row["week_start"])
                self.assertEqual(d.weekday(), 0,
                                 f"the fixture itself is wrong: {row['week_start']}")
                expected.add(f"{d.strftime('%b')} {d.day}")
            for lab in labels:
                self.assertIn(lab, expected,
                              f"week label {lab!r} is not one of the Mondays the "
                              f"server sent — the day before it, most likely")
        finally:
            self._close(handles)

    def test_the_range_strip_lines_up_with_the_chart_above_it(self):
        """Two SVGs in one card are read as one picture.

        The strip first shipped with its own geometry — no axis padding, and
        one fewer slot because it has no NOW column — so its bars sat 59px
        left of the chart's at one end and 72px right at the other. Week i
        has to be at the same x in both, so both now derive it from one
        shared grid. This measures the drawn pixels, not the constants.
        """
        geo, errors, handles = self._measure(1900, 1200, tab="analyze",
                                             ticker_payload=sell_payload())
        try:
            self.assertEqual(errors, [], f"the page threw: {errors[:2]}")
            g = geo["returns"]["grid"]
            self.assertIsNotNone(g, "one of the two charts did not render")
            self.assertGreater(g["pairs"], 8, "too few weeks to compare")
            self.assertEqual(g["rings"], g["bars"],
                             "the two charts are drawing different week counts")
            self.assertLessEqual(g["worst"], 1.5,
                                 f"a range bar sits {g['worst']}px from its week "
                                 f"in the chart above")
        finally:
            self._close(handles)

    def test_a_bar_with_a_missing_price_does_not_take_the_page_down(self):
        """lightweight-charts throws "Value is null" out of its Candlestick
        constructor if any one of open/high/low/close is missing, and the throw
        escapes into the page — one uncaught error per render attempt, a dozen
        in ten seconds, and the chart never draws.

        Two of the three call sites filtered on `close != null` alone, which is
        the field a forming bar is least likely to be missing; the third did not
        filter at all. It reached production and only showed itself when the
        market was open, because that is when an incomplete bar exists: every
        check in this repo passed at 7am and twelve of them went red at 9:31.

        A test that needs the bell to ring is not a test, so this one puts the
        hole in the data itself. `MockData.buildDaily` is what feeds the chart
        in the sandbox, so it is wrapped before the app loads and one bar in the
        middle of the series has its `open` removed."""
        holed = """
          (() => {
            let real = undefined;
            Object.defineProperty(window, 'MockData', {
              configurable: true,
              get() { return real; },
              set(v) {
                real = v;
                if (v && typeof v.buildDaily === 'function') {
                  const inner = v.buildDaily.bind(v);
                  v.buildDaily = (...a) => {
                    const rows = inner(...a);
                    if (rows && rows.length > 6) {
                      // Not the last bar: a guard that only skips the newest
                      // row would pass this while still breaking on a hole
                      // anywhere else in the history.
                      rows[rows.length - 4] = { ...rows[rows.length - 4],
                                                open: null };
                      rows[3] = { ...rows[3], high: null };
                    }
                    return rows;
                  };
                }
              },
            });
          })();
        """
        geo, errors, handles = self._measure(1440, 900, init=holed)
        try:
            self.assertFalse(
                errors,
                "a bar with a missing price threw out of the chart and into "
                f"the page: {errors[:3]}")
            # And the rest of the series still draws — dropping the incomplete
            # bar must not mean dropping the chart.
            self.assertEqual(10, geo["tiles"],
                             "the market strip stopped rendering as well")
            self.assertIsNotNone(geo["main"], "the workspace is gone")
        finally:
            self._close(handles)


    def test_two_bars_on_the_same_day_do_not_take_the_page_down(self):
        """The mirror of the test above, and the one that was missing.

        A series is INDEXED BY TIME. lightweight-charts requires those times
        to be strictly ascending; hand it a repeat and its index can no longer
        find a bar, so the renderer throws "Value is null" on every frame and
        the chart never draws.

        This is not hypothetical. The app appends a live bar for today and
        asks "does the series already end with today?" — a question it
        answered by projecting the bar's date into New York. A daily bar's
        date is a CALENDAR date (mock bars are built at local midnight, live
        ones hydrated from a bare YYYY-MM-DD), so the projection landed on the
        previous evening, the test never matched, and today's live bar was
        appended beside today's real one. It only appeared while the market
        was open, which is why every check in this repo passed at 11:00 UTC
        and ten of them went red at 15:30 — including CI, on `main`.

        So the duplicate goes into the data here, and no bell has to ring."""
        doubled = """
          (() => {
            let real = undefined;
            Object.defineProperty(window, 'MockData', {
              configurable: true,
              get() { return real; },
              set(v) {
                real = v;
                if (v && typeof v.buildDaily === 'function') {
                  const inner = v.buildDaily.bind(v);
                  v.buildDaily = (...a) => {
                    const rows = inner(...a);
                    if (rows && rows.length > 6) {
                      // Today twice — exactly what the live bar did — and a
                      // step BACKWARDS in the middle, which a guard that only
                      // looked at the newest row would sail past.
                      rows.push({ ...rows[rows.length - 1] });
                      rows.splice(4, 0, { ...rows[2] });
                    }
                    return rows;
                  };
                }
              },
            });
          })();
        """
        geo, errors, handles = self._measure(1440, 900, init=doubled)
        try:
            self.assertFalse(
                errors,
                "two bars on one day threw out of the chart and into the "
                f"page: {errors[:3]}")
            self.assertEqual(10, geo["tiles"],
                             "the market strip stopped rendering as well")
            self.assertIsNotNone(geo["main"], "the workspace is gone")
        finally:
            self._close(handles)


if __name__ == "__main__":
    unittest.main(verbosity=2)
