"""test_http_smoke.py (v1.39) — boots the real server with stubbed data
sources and exercises EVERY /api endpoint over HTTP. The bar: every
response arrives within the timeout, carries a JSON content type, and
parses as JSON. No HTML error pages, no hangs, no connection drops.
Status codes are free (some endpoints return 4xx/5xx JSON by design
when a data module is off); the crash class this catches is the one
that produced past regressions. Pure stdlib + the app's own deps.
Run:  python3 test_http_smoke.py
"""

import json
import os
import sys
import tempfile
import threading
import urllib.request
import urllib.error
from datetime import date, timedelta
from types import SimpleNamespace

# Isolate persistence BEFORE importing the module (it resolves the
# stable dir at import time).
_TMP = tempfile.mkdtemp(prefix="jerry_smoke_")
os.environ["JERRY_DATA_DIR"] = _TMP
os.environ.pop("API_KEY", None)  # no auth in the harness

import numpy as np
import pandas as pd

import options_dashboard as od


# ── Fake yfinance ───────────────────────────────────────────────────
def _fake_history(rows=620, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=rows)
    steps = rng.normal(0.0005, 0.018, rows)
    close = 100.0 * np.exp(np.cumsum(steps))
    high = close * (1 + np.abs(rng.normal(0.004, 0.006, rows)))
    low = close * (1 - np.abs(rng.normal(0.004, 0.006, rows)))
    openp = low + (high - low) * rng.uniform(0.2, 0.8, rows)
    vol = rng.integers(1_000_000, 9_000_000, rows)
    return pd.DataFrame({"Open": openp, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def _next_fridays(n=3):
    d = date.today()
    out = []
    while len(out) < n:
        d = d + timedelta(days=1)
        if d.weekday() == 4:
            out.append(d.strftime("%Y-%m-%d"))
    return tuple(out)


def _fake_chain_df(spot, kind):
    strikes = [round(spot * (0.7 + 0.025 * i), 2) for i in range(25)]
    rows = []
    for k in strikes:
        if kind == "call":
            intr = max(spot - k, 0)
        else:
            intr = max(k - spot, 0)
        mid = intr + max(0.05, 3.0 * np.exp(-abs(spot - k) / (0.08 * spot)))
        rows.append({
            "contractSymbol": f"FAKE{k}", "strike": k,
            "bid": round(mid * 0.97, 2), "ask": round(mid * 1.03, 2),
            "lastPrice": round(mid, 2), "impliedVolatility": 0.32,
            "openInterest": 1500, "volume": 400, "inTheMoney": intr > 0,
        })
    return pd.DataFrame(rows)


class FakeTicker:
    def __init__(self, symbol, *a, **kw):
        self.symbol = str(symbol)
        self._hist = _fake_history()

    def history(self, *a, **kw):
        period = kw.get("period") or (a[0] if a else "1y")
        n = {"1d": 2, "5d": 6, "1mo": 23, "3mo": 66, "6mo": 130,
             "1y": 252, "2y": 504, "5y": 620, "max": 620}.get(str(period), 620)
        return self._hist.tail(n).copy()

    @property
    def info(self):
        spot = float(self._hist["Close"].iloc[-1])
        return {"shortName": f"Fake {self.symbol} Inc", "sector": "Technology",
                "currentPrice": spot, "regularMarketPrice": spot,
                "previousClose": spot * 0.995, "dividendRate": 1.00,
                "trailingAnnualDividendRate": 0.96, "dividendYield": 0.45}

    @property
    def fast_info(self):
        spot = float(self._hist["Close"].iloc[-1])
        return SimpleNamespace(last_price=spot, previous_close=spot * 0.995)

    @property
    def options(self):
        return _next_fridays(3)

    def option_chain(self, expiry=None):
        spot = float(self._hist["Close"].iloc[-1])
        return SimpleNamespace(calls=_fake_chain_df(spot, "call"),
                               puts=_fake_chain_df(spot, "put"))

    @property
    def earnings_dates(self):
        idx = pd.DatetimeIndex([pd.Timestamp.today() + pd.Timedelta(days=40)])
        return pd.DataFrame({"EPS Estimate": [1.25], "Reported EPS": [None],
                             "Surprise(%)": [None]}, index=idx)


class FakeYF:
    Ticker = FakeTicker

    @staticmethod
    def download(*a, **kw):
        return _fake_history()


# ── Wire the stubs in ───────────────────────────────────────────────
od.yf = FakeYF()
od._schwab = lambda: None                       # force yfinance fallback
if hasattr(od, "_SCHWAB_AVAILABLE"):
    od._SCHWAB_AVAILABLE = False
if hasattr(od, "_UW_AVAILABLE"):
    od._UW_AVAILABLE = False                    # UW endpoints answer "off"
if hasattr(od, "_ANALYST_AVAILABLE"):
    od._ANALYST_AVAILABLE = False

# ── Boot the real server on an ephemeral port ───────────────────────
server = od.ThreadingHTTPServer(("127.0.0.1", 0), od.DashboardHandler)
PORT = server.server_address[1]
t = threading.Thread(target=server.serve_forever, daemon=True)
t.start()
BASE = f"http://127.0.0.1:{PORT}"

passed = 0
failed = 0
fails = []


def hit(method, path, body=None, timeout=60):
    global passed, failed
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    label = f"{method} {path.split('?')[0]}"
    try:
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            status, ctype, raw = resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as e:
            status, ctype, raw = e.code, e.headers.get("Content-Type", ""), e.read()
        ok_json = "json" in ctype.lower()
        try:
            json.loads(raw.decode("utf-8"))
        except Exception:
            ok_json = False
        if ok_json:
            passed += 1
            print(f"  PASS  {label} [{status}]")
        else:
            failed += 1
            fails.append(label)
            print(f"  FAIL  {label} [{status}] non-json: {raw[:80]!r}")
    except Exception as exc:  # noqa: BLE001  (timeout / connection failure)
        failed += 1
        fails.append(label)
        print(f"  FAIL  {label} ({exc})")


S = "FAKE"
# GET endpoints — params chosen to drive the real code paths.
for p in [
    f"/api/ticker?symbol={S}&weeks=8",
    f"/api/quote?symbol={S}",
    f"/api/option_quote?symbol={S}&strike=100&kind=call",
    "/api/search?q=fa",
    "/api/scan",
    "/api/watchlist",
    "/api/watchlist_alerts",
    f"/api/weekly_range?symbol={S}",
    f"/api/pullback_profile?symbol={S}",
    f"/api/pullback_backtest?symbol={S}",
    "/api/pullback_scan",
    f"/api/backtest?symbol={S}",
    f"/api/basing?symbol={S}",
    "/api/earnings_ladder",
    f"/api/earnings_iv_crush?symbol={S}",
    f"/api/analyst?symbol={S}",
    "/api/data_source",
    "/api/broker/accounts",
    "/api/broker/positions",
    "/api/push/status",
    f"/api/reprice/chain?symbol={S}",
    f"/api/strategy/ema_pullback?symbol={S}",
    "/api/strategy/ema_pullback_state",
    f"/api/trade_builder/multi_exp?symbol={S}",
    "/api/trade_journal",
    f"/api/uw/health",
    f"/api/uw/debug",
    f"/api/uw/flow_alerts?symbol={S}",
    f"/api/uw/flow_score?symbol={S}",
    f"/api/uw/flow_trades?symbol={S}",
    f"/api/uw/greek_exposure?symbol={S}",
    "/api/uw/market_dashboard",
    "/api/uw/market_scan_candidates",
    f"/api/uw/market_scan_score?symbol={S}",
    "/api/uw/market_tide",
    f"/api/uw/momentum?symbol={S}",
    f"/api/uw/net_premium?symbol={S}",
    f"/api/uw/option_chains?symbol={S}",
    f"/api/uw/premium_richness?symbol={S}",
    "/api/uw/sector_flow",
    f"/api/uw/strike_flow?symbol={S}",
    "/api/zzz_unknown",
]:
    hit("GET", p)

# POST endpoints — valid bodies where schema is known; the bar is JSON
# back, so schema rejections (4xx JSON) still pass the smoke bar.
hit("POST", "/api/reprice", {
    "kind": "call", "spot_now": 290, "strike": 300, "days_to_exp": 7,
    "r": 0.04, "current_price": 1.35,
    "levels": [{"label": "x", "target_spot": 315, "hours_from_now": 0, "iv_shift": 0}]})
hit("POST", "/api/fade", {
    "kind": "call", "spot_now": 100, "strike": 105, "days_to_exp": 5,
    "r": 0.04, "current_price": 1.20, "sell_spot": 106, "cover_spot": 103.2,
    "stop_spot": 108, "hours_held": 2, "contracts": 1})
hit("POST", "/api/fade/save", {"ticker": S, "kind": "call", "strike": 105,
                               "days_to_exp": 5, "sell_spot": 106,
                               "cover_spot": 103.2, "contracts": 1})
hit("POST", "/api/trade_journal", {"symbol": S, "side": "cc", "strike": 105,
                                   "premium": 1.2, "contracts": 1,
                                   "opened": "2026-06-01"})
hit("POST", "/api/watchlist_alerts/dismiss", {"id": "smoke-test-id"})
hit("POST", "/api/push/test", {})
hit("POST", "/api/push/roll_flag", {"symbol": S, "strike": 105, "kind": "call"})

# PUT watchlist — read current shape, write it back.
hit("PUT", "/api/watchlist", {"version": 1, "symbols": [
    {"symbol": "SPY", "tags": ["etf"], "notes": "", "preferred_strategy": None,
     "starred": True, "added_at": 1781056495}]})

server.shutdown()
print(f"\n{passed}/{passed + failed} passed, {failed} failed")
if fails:
    print("FAILED: " + ", ".join(fails))
    raise SystemExit(1)
