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
    "/api/playbook",
    f"/api/perfection?symbol={S}",
    f"/api/whisper?symbol={S}",
    f"/api/site_link?site=simplywallst&symbol={S}",
    "/api/watchlist",
    "/api/watchlist_alerts",
    f"/api/weekly_range?symbol={S}",
    f"/api/pullback_profile?symbol={S}",
    f"/api/pullback_backtest?symbol={S}",
    "/api/pullback_scan",
    f"/api/backtest?symbol={S}",
    f"/api/basing?symbol={S}",
    "/api/earnings_ladder",
    "/api/ewhispers/weekly",
    "/api/ewhispers/weekly?week=2026-08-10",
    "/api/ewhispers/refresh",
    "/api/ewhispers/image?id=12345&size=large",
    "/api/recovery",
    "/api/recovery/scan",
    "/api/scan_all",
    "/api/scan_all/status",
    "/api/nl/status",
    "/api/nl/board",
    "/api/nl/strategies",
    "/api/timing/status",
    "/api/timing/contracts",
    "/api/timing/config",
    "/api/timing/tape/status",
    f"/api/timing/state?symbol={S}&strike=100&kind=call&expiry=2030-01-18",
    "/api/timing/fills",
    "/api/timing/post_trade",
    "/api/timing/replay?id=nonexistent",
    "/api/timing/replay_day?day=2026-08-14",
    "/api/edge",
    "/api/edge/config",
    f"/api/edge/detail?symbol={S}",
    f"/api/edge/history?symbol={S}",
    f"/api/edge/breach?symbol={S}",
    "/api/edge/backtest?job=nonexistent",
    "/api/edge/scan",
    "/api/sell",
    "/api/sell?mode=conservative&strategy=cash_secured_put&top=5",
    "/api/sell?mode=event",
    f"/api/sell/detail?symbol={S}",
    "/api/sell/status",
    "/api/sell/config",
    "/api/sell/predictions?days=30",
    "/api/sell/calibration",
    "/api/sell/grade",
    "/api/sell/scan",
    "/api/gap",
    "/api/gap/config",
    "/api/gap/live",
    f"/api/gap/detail?symbol={S}",
    f"/api/gap/events?symbol={S}",
    f"/api/gap/backtest?symbol={S}",
    "/api/gap/scan",
    # Korea Lead sits above the Gap Scan board. Offline it must still answer
    # 200 with a stated reason — "the Korean series could not be read" is an
    # answer, and the panel renders it. A 500 here would take the whole Gap
    # Scan tab down with it.
    f"/api/korea_lead?symbol={S}",
    f"/api/korea_lead?symbol={S}&window=3y",
    # The research layer. Offline these must still answer 200 with a stated
    # reason — the Details drawer renders that.
    f"/api/korea_research?symbol={S}",
    "/api/korea_research/matrix",
    "/api/korea_research/coverage",
    # The forward record. On a fresh container these read an empty store and
    # must answer 200 saying so — "no forward records yet" is the correct
    # answer on day one and stays correct until the mornings accumulate.
    "/api/korea_forward/coverage",
    f"/api/korea_forward/scorecard?symbol={S}",
    "/api/korea_forward/status",
    "/api/invest/config",
    f"/api/invest?symbol={S}",
    f"/api/invest/history?symbol={S}&years=5",
    f"/api/invest/valuation?symbol={S}&years=5",
    f"/api/invest/peers?symbol={S}",
    # Phase 3: the structures/entry payload and the watchlist scanner. Both
    # must answer 200 with a reason when there is no option chain to read, not
    # 500 — "no chain for this ticker" is an answer, not a failure.
    f"/api/invest/structures?symbol={S}",
    "/api/invest/scan?symbols=FAKE&budget=0",
    f"/api/invest/covered_call?symbol={S}&years=1",
    "/api/invest/validation?symbols=FAKE",
    # The two operational reports. Both must answer 200 on an app that has
    # captured nothing at all — "nothing has been captured yet" is an
    # answer, and an audit that 500s is an audit nobody can read.
    "/api/invest/readiness?symbols=FAKE",
    "/api/invest/audit?symbols=FAKE",
    "/api/invest/day?symbols=FAKE",
    "/api/recovery/research",
    f"/api/recovery/detail?symbol={S}",
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
hit("POST", "/api/whisper/manual", {"symbol": S, "source": "smoke-test", "eps": 1.23,
                                    "url": "https://example.com/note"})
hit("POST", "/api/push/test", {})
hit("POST", "/api/nl/translate", {"text": "stocks down 3% today on double volume"})
hit("POST", "/api/nl/scan", {"rules": {"universe": {"source": "symbols", "symbols": [S]},
                                       "entry": [{"type": "day_change_pct", "op": "<=",
                                                  "value": -3}]}})
hit("POST", "/api/nl/strategies", {"op": "save", "name": "smoke", "rules":
                                   {"entry": [{"type": "rsi", "op": "<=", "value": 30,
                                               "period": 14}]}})
hit("POST", "/api/nl/alerts/check", {})
hit("POST", "/api/timing/candidates", {"symbol": S, "expiry": "2030-01-18",
                                       "kind": "put", "strike": 95.0, "contracts": 2})
hit("POST", "/api/timing/fill", {"symbol": S, "expiry": "2030-01-18", "kind": "put",
                                 "strike": 95.0, "credit": 1.25, "contracts": 2,
                                 "mode": "resting"})
hit("POST", "/api/timing/intent", {"symbol": S, "kind": "put",
                                   "intent": "wheel_acceptable"})
hit("POST", "/api/timing/portfolio", {"legs": [{"symbol": S, "strike": 95.0,
                                                "kind": "put", "expiry": "2030-01-18",
                                                "credit": 1.25, "contracts": 2}]})
hit("POST", "/api/timing/manage", {"positions": [{"symbol": S, "strike": 95.0,
                                                  "kind": "put", "expiry": "2030-01-18",
                                                  "credit": 1.25, "contracts": 2}]})
hit("POST", "/api/edge/kelly", {"pnls": [10, -5, 20], "collateral": 1000})
hit("POST", "/api/edge/backtest", {"symbols": []})
hit("POST", "/api/timing/replay_day", {"day": "2020-01-03", "trades": [
    {"symbol": S, "strike": 100.0, "kind": "call", "credit": 1.0}]})
hit("POST", "/api/push/roll_flag", {"symbol": S, "strike": 105, "kind": "call"})

# PUT watchlist — force=1 to bypass the destructive-shrink guard (this test
# intentionally writes a tiny 1-symbol list over the seeded default).
hit("PUT", "/api/watchlist?force=1", {"version": 1, "symbols": [
    {"symbol": "SPY", "tags": ["etf"], "notes": "", "preferred_strategy": None,
     "starred": True, "added_at": 1781056495}]})

server.shutdown()
print(f"\n{passed}/{passed + failed} passed, {failed} failed")
if fails:
    print("FAILED: " + ", ".join(fails))
    raise SystemExit(1)
