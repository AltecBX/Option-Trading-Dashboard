"""treasury.py — US Treasuries / rates terminal backend (v1).

Data layer for the "US Treasuries" tab: what do rates, inflation
expectations, Treasury market movement and upcoming macro events imply
for a stock & options trader.

Sources (authoritative first, per the tab spec):
  • Daily par yield curve      — home.treasury.gov XML (official, EOD)
  • FRED CSV (no key needed)   — breakevens, TIPS real yields, fed funds,
                                 CPI + subindices (BLS data via FRED)
  • TreasuryDirect API         — auction results + upcoming auctions
  • CFTC public reporting API  — Traders in Financial Futures (COT)
  • yfinance                   — ^MOVE, Treasury futures, ETF proxies,
                                 cross-asset closes (delayed, labeled)

Honesty rules baked in: nothing is manufactured — a section whose source
fails returns ok=False / None fields and the UI renders "Data unavailable".
Consensus estimates and when-issued yields have no free reliable source, so
they are reported as unavailable rather than guessed. All "schedule" dates
carry a `source` string so the UI can label them.

Caching: every fetcher runs through _cached(key, ttl) — slow-moving data
(CPI, COT, auctions) is cached for hours, daily curve data ~30 min.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import requests

try:
    import yfinance as yf
    import pandas as pd
    _YF_OK = True
except Exception:
    _YF_OK = False

_UA = {"User-Agent": "JerryTrade-dashboard/1.0 (rates tab; contact: local)"}
_TIMEOUT = 20

# ── tiny TTL cache ───────────────────────────────────────────────────────────
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.RLock()


def _cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    val = fn()
    with _CACHE_LOCK:
        # Cache failures briefly too so a dead source doesn't get hammered.
        _CACHE[key] = (time.time() + (ttl if val is not None else 120), val)
    return val


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _et_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now()


# ── generic helpers ──────────────────────────────────────────────────────────
def _pctile(series: list[float], value: float) -> float | None:
    vals = [v for v in series if v is not None]
    if len(vals) < 20:
        return None
    return round(sum(1 for v in vals if v <= value) / len(vals) * 100, 0)


# Some hosts' edge filters (notably FRED behind certain egress proxies) stall
# non-curl user agents. Try both UAs, but remember per-host which one worked
# so only the first request to a host pays the failover penalty.
_CURL_UA = {"User-Agent": "curl/8.5.0", "Accept": "*/*"}
_UA_PREF: dict[str, int] = {}   # host -> index into _UA_ORDER that succeeded


def _host(url: str) -> str:
    return url.split("/")[2] if "://" in url else url


def _request(url: str):
    order = [_UA, _CURL_UA]
    pref = _UA_PREF.get(_host(url), 0)
    if pref:
        order.reverse()
    for i, ua in enumerate(order):
        try:
            r = requests.get(url, headers=ua, timeout=8 if i == 0 and len(order) > 1 else _TIMEOUT)
            if r.status_code == 200:
                _UA_PREF[_host(url)] = (1 - pref) if i == 1 else pref
                return r
            # Throttled (Socrata rate-limits anonymous callers) — one retry.
            if r.status_code in (429, 403) and i == len(order) - 1:
                time.sleep(2.5)
                r2 = requests.get(url, headers=ua, timeout=_TIMEOUT)
                if r2.status_code == 200:
                    return r2
            print(f"[treasury] GET {r.status_code} ({ua['User-Agent'][:12]}) {url[:90]}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"[treasury] GET failed ({ua['User-Agent'][:12]}) {url[:80]}: {exc}", file=sys.stderr)
    return None


def _get(url: str) -> str | None:
    r = _request(url)
    return r.text if r is not None else None


def _get_json(url: str) -> Any | None:
    r = _request(url)
    try:
        return r.json() if r is not None else None
    except Exception:
        return None


# ── FRED (no API key: fredgraph.csv) ─────────────────────────────────────────
def _fred_series(series_id: str, ttl: float = 1800, start: str | None = None) -> list[tuple[str, float]]:
    """[(iso_date, value), ...] oldest→newest. Missing obs ('.') skipped.
    Always pass a start date (cosd) — full-history CSVs are 60+ years and
    slow; default start is ~3.2 years back, plenty for 52w percentiles."""
    def fetch():
        cosd = start or (_et_now().date() - timedelta(days=1170)).isoformat()
        txt = _get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={cosd}")
        if not txt:
            return None
        out = []
        for line in txt.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) != 2:
                continue
            d, v = parts[0].strip(), parts[1].strip()
            if v in (".", ""):
                continue
            try:
                out.append((d, float(v)))
            except ValueError:
                continue
        return out or None
    return _cached(f"fred:{series_id}", ttl, fetch) or []


def _series_stats(obs: list[tuple[str, float]]) -> dict | None:
    """Latest value + 1d/5d/21d changes + 52w percentile for a daily series."""
    if len(obs) < 30:
        return None
    vals = [v for _, v in obs]
    last = vals[-1]
    def chg(n):
        return round(last - vals[-1 - n], 4) if len(vals) > n else None
    yr = vals[-252:]
    return {
        "value": last, "date": obs[-1][0],
        "d1": chg(1), "d5": chg(5), "d21": chg(21),
        "pct52w": _pctile(yr, last),
        "hi52w": round(max(yr), 4), "lo52w": round(min(yr), 4),
    }


# ── 1. Treasury daily par yield curve ────────────────────────────────────────
TENORS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
_XML_FIELDS = {
    "1M": "BC_1MONTH", "3M": "BC_3MONTH", "6M": "BC_6MONTH", "1Y": "BC_1YEAR",
    "2Y": "BC_2YEAR", "3Y": "BC_3YEAR", "5Y": "BC_5YEAR", "7Y": "BC_7YEAR",
    "10Y": "BC_10YEAR", "20Y": "BC_20YEAR", "30Y": "BC_30YEAR",
}
KEY_TENORS = {"2Y": "Fed policy expectations", "5Y": "intermediate growth & inflation",
              "10Y": "equity valuation & mortgage benchmark", "30Y": "long duration & inflation risk"}


def _curve_year(year: int) -> list[dict] | None:
    url = ("https://home.treasury.gov/resource-center/data-chart-center/"
           f"interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value={year}")
    txt = _get(url)
    if not txt:
        return None
    rows = []
    for m in re.finditer(r"<m:properties>(.*?)</m:properties>", txt, re.S):
        blk = m.group(1)
        dm = re.search(r"<d:NEW_DATE[^>]*>(\d{4}-\d{2}-\d{2})", blk)
        if not dm:
            continue
        row = {"date": dm.group(1)}
        for tenor, field in _XML_FIELDS.items():
            fm = re.search(rf"<d:{field}[^>]*>([\d.]+)</d:{field}>", blk)
            row[tenor] = float(fm.group(1)) if fm else None
        rows.append(row)
    rows.sort(key=lambda r: r["date"])
    return rows or None


def risk_free_3m_cached() -> tuple[float, str] | None:
    """Latest 3-month T-bill yield as a DECIMAL from the ALREADY-CACHED
    curve, for metrics.risk_free_rate(). Peek-only: never triggers a
    network fetch (pricing calls must stay instant), so it returns None
    until the Treasuries tab / any curve consumer has warmed the cache."""
    with _CACHE_LOCK:
        hit = _CACHE.get("curve_hist")
    rows = hit[1] if hit else None
    if not rows:
        return None
    latest = rows[-1]
    y3m = latest.get("3M")
    if y3m is None:
        return None
    return y3m / 100.0, f"3M T-bill {y3m:.2f}% (Treasury.gov, {latest.get('date')})"


def _curve_history() -> list[dict]:
    """~3 years of daily curves, oldest→newest (for percentiles & compares)."""
    def fetch():
        y = _et_now().year
        rows = []
        for yr in (y - 2, y - 1, y):
            part = _cached(f"tsyxml:{yr}", 86400 if yr < y else 1800,
                           lambda yr=yr: _curve_year(yr))
            if part:
                rows.extend(part)
        rows.sort(key=lambda r: r["date"])
        return rows or None
    return _cached("curve_hist", 1800, fetch) or []


# Market convention: spread = LONG tenor minus SHORT tenor, so positive =
# normally-sloped and negative = inverted, uniformly. (A "2s10s" quote is
# 10y − 2y; the requested 30y−5y mirror is identical to 5s30s.)
SPREAD_DEFS = [
    ("3m10y", "3M", "10Y", "3m10y (10y − 3m)"),
    ("2s10s", "2Y", "10Y", "2s10s (10y − 2y)"),
    ("2s30s", "2Y", "30Y", "2s30s (30y − 2y)"),
    ("5s10s", "5Y", "10Y", "5s10s (10y − 5y)"),
    ("5s30s", "5Y", "30Y", "5s30s (30y − 5y)"),
    ("10s30s", "10Y", "30Y", "10s30s (30y − 10y)"),
]


def get_core() -> dict:
    """Yields, curve compares, spreads, regime, expectations, MOVE, events."""
    hist = _curve_history()
    out: dict[str, Any] = {"as_of": _now_iso(), "ok": bool(hist)}
    if not hist:
        out["error"] = "Treasury.gov yield data unavailable"
        return out
    latest = hist[-1]
    out["curve_date"] = latest["date"]
    out["source"] = "U.S. Treasury daily par yield curve (official, end-of-day)"

    # Per-tenor cards
    def tseries(t):
        return [r[t] for r in hist if r.get(t) is not None]
    year_start = f"{_et_now().year}-01-01"
    cards = []
    for t in TENORS:
        pairs = [(r["date"], r[t]) for r in hist if r.get(t) is not None]
        s = [v for _, v in pairs]
        if not s:
            continue
        last = s[-1]
        yr = s[-252:]
        def bp(n, s=s, last=last):
            return round((last - s[-1 - n]) * 100, 1) if len(s) > n else None
        prior_yr = [v for d, v in pairs if d < year_start]
        cards.append({
            "tenor": t, "yield": last,
            "spark": [round(v, 3) for v in s[-90:]] if t == "10Y" else None,
            "bp1d": bp(1), "bp5d": bp(5), "bp21d": bp(21), "bp63d": bp(63),
            "bp_ytd": round((last - prior_yr[-1]) * 100, 1) if prior_yr else None,
            "pct52w": _pctile(yr, last),
            "hi52w": round(max(yr), 2), "lo52w": round(min(yr), 2),
            "key": KEY_TENORS.get(t),
        })
    out["yields"] = cards

    # Curve snapshots for the chart: current, -1d, -5d(1w), -21d(1m), -63d(3m), -252d(1y)
    snaps = {}
    for label, back in [("current", 0), ("1d", 1), ("1w", 5), ("1m", 21), ("3m", 63), ("1y", 252)]:
        if len(hist) > back:
            r = hist[-1 - back]
            snaps[label] = {"date": r["date"], "points": {t: r.get(t) for t in TENORS}}
    out["snapshots"] = snaps

    # Spreads (bp) + history percentile + direction
    def spread_series(a, b):
        return [(r["date"], round((r[b] - r[a]) * 100, 1))
                for r in hist if r.get(a) is not None and r.get(b) is not None]
    spreads = []
    for key, a, b, label in SPREAD_DEFS:
        ss = spread_series(a, b)
        if len(ss) < 30:
            continue
        vals = [v for _, v in ss]
        cur = vals[-1]
        def d(n, vals=vals, cur=cur):
            return round(cur - vals[-1 - n], 1) if len(vals) > n else None
        spreads.append({
            "key": key, "label": label, "bp": cur,
            "d1": d(1), "d5": d(5), "d21": d(21),
            "pctile": _pctile(vals, cur),
            "inverted": cur < 0,
            "trend": "steepening" if (d(5) or 0) > 1 else "flattening" if (d(5) or 0) < -1 else "flat",
        })
    # 10y − effective fed funds
    dff = _fred_series("DFF", 3600)
    if dff and latest.get("10Y") is not None:
        ff = dff[-1][1]
        spreads.append({
            "key": "10yff", "label": "10-year minus fed funds (effective)",
            "bp": round((latest["10Y"] - ff) * 100, 1), "d1": None, "d5": None, "d21": None,
            "pctile": None, "inverted": latest["10Y"] < ff,
            "trend": None, "note": f"EFFR {ff:.2f}% (FRED DFF, {dff[-1][0]})",
        })
    out["spreads"] = spreads

    # Curve regime over 1w: bull/bear × steepener/flattener + biggest mover
    def bp_change(t, back):
        s = tseries(t)
        return round((s[-1] - s[-1 - back]) * 100, 1) if len(s) > back else None
    d2, d10 = bp_change("2Y", 5), bp_change("10Y", 5)
    regime = None
    if d2 is not None and d10 is not None:
        slope = d10 - d2
        steep = "steepener" if slope > 1 else "flattener" if slope < -1 else "parallel"
        tone = "bull" if d10 < 0 else "bear"          # bull = yields falling (prices up)
        regime = {
            "label": f"{tone} {steep}" if steep != "parallel" else f"parallel {'rally' if d10 < 0 else 'selloff'}",
            "d2y_bp": d2, "d10y_bp": d10, "slope_chg_bp": round(slope, 1), "window": "5 trading days",
        }
    # Curve shape (his terminal read): inverted / partially inverted / flat /
    # normal, plus a hump note when the belly out-yields the 30y.
    try:
        y3m, y2, y10, y30 = latest.get("3M"), latest.get("2Y"), latest.get("10Y"), latest.get("30Y")
        if None not in (y3m, y2, y10, y30):
            s3m10_bp = (y10 - y3m) * 100
            s2s10_bp = (y10 - y2) * 100
            if s3m10_bp < 0 and s2s10_bp < 0:
                shape = "inverted"
                det = f"3m10y {s3m10_bp:+.0f} bp, 2s10s {s2s10_bp:+.0f} bp"
            elif s3m10_bp < 0 or s2s10_bp < 0:
                shape = "partially inverted"
                det = f"3m10y {s3m10_bp:+.0f} bp, 2s10s {s2s10_bp:+.0f} bp"
            elif s2s10_bp < 15:
                shape = "flat"
                det = f"2s10s only {s2s10_bp:+.0f} bp"
            else:
                shape = "normal (upward)"
                det = f"3m10y {s3m10_bp:+.0f} bp, 2s10s {s2s10_bp:+.0f} bp"
            belly = [(t, latest[t]) for t in ("3Y", "5Y", "7Y", "10Y", "20Y") if latest.get(t) is not None]
            if belly:
                bt, bv = max(belly, key=lambda x: x[1])
                if bv - y30 > 0.02:
                    shape += f", humped at {bt}"
                    det += f"; {bt} {bv:.2f}% > 30y {y30:.2f}%"
            out["curve_shape"] = {"label": shape, "detail": det}
    except Exception:
        pass

    moves = [(t, bp_change(t, 5)) for t in TENORS]
    moves = [(t, m) for t, m in moves if m is not None]
    if moves:
        big = max(moves, key=lambda x: abs(x[1]))
        front = [m for t, m in moves if t in ("1M", "3M", "6M", "1Y", "2Y")]
        long_ = [m for t, m in moves if t in ("10Y", "20Y", "30Y")]
        out["curve_moves"] = {
            "biggest": {"tenor": big[0], "bp5d": big[1]},
            "front_avg_bp5d": round(sum(front) / len(front), 1) if front else None,
            "long_avg_bp5d": round(sum(long_) / len(long_), 1) if long_ else None,
        }
    out["regime"] = regime

    # Inflation expectations & real yields (FRED, daily)
    exp = {}
    for key, sid, label in [
        ("be5", "T5YIE", "5y breakeven inflation"),
        ("be10", "T10YIE", "10y breakeven inflation"),
        ("f5y5y", "T5YIFR", "5y5y forward inflation"),
        ("real5", "DFII5", "5y TIPS real yield"),
        ("real10", "DFII10", "10y TIPS real yield"),
        ("real30", "DFII30", "30y TIPS real yield"),
    ]:
        st = _series_stats(_fred_series(sid))
        if st:
            st["label"] = label
            exp[key] = st
    out["expectations"] = exp
    # Decomposition: is the 10y nominal move real-yield or breakeven driven?
    # Computed on COMMON dates — the three FRED series publish with different
    # lags, and the nominal = real + breakeven identity only holds when the
    # deltas share the same window.
    try:
        nom = dict(_fred_series("DGS10"))
        rea = dict(_fred_series("DFII10"))
        bre = dict(_fred_series("T10YIE"))
        common = sorted(set(nom) & set(rea) & set(bre))
        if len(common) > 6:
            d_new, d_old = common[-1], common[-6]
            dn = (nom[d_new] - nom[d_old]) * 100
            dr = (rea[d_new] - rea[d_old]) * 100
            db = (bre[d_new] - bre[d_old]) * 100
            if abs(dn) < 3:
                verdict = "no clear move"
            elif abs(dr) >= 2 and abs(db) >= 2:
                verdict = "both real yields and inflation expectations"
            elif abs(dr) >= abs(db):
                verdict = "mostly real yields"
            else:
                verdict = "mostly inflation expectations"
            out["decomposition"] = {
                "window": "5 trading days",
                "nominal_bp": round(dn, 1), "real_bp": round(dr, 1), "breakeven_bp": round(db, 1),
                "verdict": verdict,
                "source": "FRED DGS10 / DFII10 / T10YIE",
            }
    except Exception:
        pass

    # MOVE index (delayed, via Yahoo)
    out["move"] = _cached("move", 1800, _fetch_move)

    # Events + CPI countdown
    out["events"] = _events()

    # Trader interpretation — every signal cites its inputs
    out["signals"] = _signals(out)
    return out


def _fetch_move() -> dict | None:
    if not _YF_OK:
        return None
    try:
        h = yf.Ticker("^MOVE").history(period="1y", auto_adjust=False)
        if h is None or h.empty:
            return None
        closes = [float(v) for v in h["Close"].dropna().tolist()]
        if len(closes) < 30:
            return None
        last = closes[-1]
        def d(n):
            return round(last - closes[-1 - n], 2) if len(closes) > n else None
        regime = ("low" if last < 80 else "normal" if last < 100 else
                  "elevated" if last < 130 else "high" if last < 160 else "extreme")
        return {
            "value": round(last, 2), "d1": d(1), "d5": d(5), "d21": d(21),
            "spark": [round(c, 1) for c in closes[-90:]],
            "pct52w": _pctile(closes[-252:], last), "regime": regime,
            "bands": "low <80 · normal 80–100 · elevated 100–130 · high 130–160 · extreme >160",
            "date": str(h.index[-1].date()),
            "source": "^MOVE via Yahoo Finance (delayed)",
        }
    except Exception as exc:  # noqa: BLE001
        print(f"[treasury] MOVE fetch failed: {exc}", file=sys.stderr)
        return None


# ── Events calendar ──────────────────────────────────────────────────────────
# CPI release dates per the BLS published schedule (8:30 AM ET).
CPI_SCHEDULE = {
    # 2025 (BLS schedule; Sep-data release was shutdown-delayed to Oct 24)
    "2025": ["2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10", "2025-05-13",
             "2025-06-11", "2025-07-15", "2025-08-12", "2025-09-11", "2025-10-24",
             "2025-11-13", "2025-12-18"],
    "2026": ["2026-01-13", "2026-02-11", "2026-03-11", "2026-04-10", "2026-05-12",
             "2026-06-10", "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-13",
             "2026-11-10", "2026-12-10"],
}
FOMC_2026 = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
             "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]


def _events() -> dict:
    now = _et_now()
    today = now.date()
    all_cpi = sorted(d for ds in CPI_SCHEDULE.values() for d in ds)
    next_cpi = next((d for d in all_cpi if date.fromisoformat(d) >= today), None)
    # 8:30 ET on release day: still "next" until it prints
    if next_cpi == today.isoformat() and (now.hour, now.minute) >= (8, 30):
        next_cpi = next((d for d in all_cpi if date.fromisoformat(d) > today), None)
    next_fomc = next((d for d in FOMC_2026 if date.fromisoformat(d) >= today), None)
    # Jobs report: first Friday of the month (typical schedule)
    def first_friday(y, m):
        d0 = date(y, m, 1)
        return d0 + timedelta(days=(4 - d0.weekday()) % 7)
    jobs = first_friday(today.year, today.month)
    if jobs < today or (jobs == today and (now.hour, now.minute) >= (8, 30)):
        m2 = today.month % 12 + 1
        jobs = first_friday(today.year + (1 if m2 == 1 else 0), m2)
    countdown = None
    if next_cpi:
        try:
            from zoneinfo import ZoneInfo
            rel = datetime.fromisoformat(next_cpi + "T08:30:00").replace(tzinfo=ZoneInfo("America/New_York"))
            secs = max(0, (rel - now).total_seconds())
            countdown = {"days": int(secs // 86400), "hours": int(secs % 86400 // 3600),
                         "minutes": int(secs % 3600 // 60)}
        except Exception:
            pass
    upcoming_auctions = _cached("upcoming_auctions", 21600, _fetch_upcoming_auctions)
    return {
        "next_cpi": {"date": next_cpi, "time_et": "8:30 AM ET", "countdown": countdown,
                     "consensus": None,   # no free reliable consensus source — never guessed
                     "source": "BLS published release schedule"},
        "next_fomc": {"date": next_fomc,
                      "days": (date.fromisoformat(next_fomc) - today).days if next_fomc else None,
                      "source": "Federal Reserve 2026 meeting calendar"},
        "next_jobs": {"date": jobs.isoformat(), "source": "typical schedule (first Friday, 8:30 AM ET)"},
        "note_ppi_pce": "PPI typically prints within a few days of CPI; PCE near month-end (BEA). Exact dates: bls.gov / bea.gov schedules.",
        "upcoming_auctions": upcoming_auctions or [],
    }


def _fetch_upcoming_auctions() -> list | None:
    data = _get_json("https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json")
    if not isinstance(data, list):
        return None
    out = []
    for r in data[:20]:
        out.append({
            "type": r.get("securityType"), "term": r.get("securityTerm"),
            "auction_date": (r.get("auctionDate") or "")[:10],
            "issue_date": (r.get("issueDate") or "")[:10],
            "offering": r.get("offeringAmount") or None,
        })
    return out


# ── Trader interpretation (rules cite the numbers that fired them) ───────────
def _signals(core: dict) -> list[dict]:
    sig = []
    y = {c["tenor"]: c for c in core.get("yields", [])}
    sp = {s["key"]: s for s in core.get("spreads", [])}
    exp = core.get("expectations", {})
    move = core.get("move")

    def add(label, level, detail, tone):
        sig.append({"label": label, "level": level, "detail": detail, "tone": tone})

    t10 = y.get("10Y")
    if t10 and t10["bp5d"] is not None:
        b = t10["bp5d"]
        lvl = "High" if b >= 10 else "Moderate" if b >= 5 else "Relief" if b <= -10 else "Mild relief" if b <= -5 else "Low"
        tone = "down" if b >= 5 else "up" if b <= -5 else "mut"
        add("Growth stock pressure", lvl,
            f"10y yield {'+' if b >= 0 else ''}{b} bp over 5 sessions (now {t10['yield']:.2f}%). Rising long yields compress high-duration equity valuations.", tone)
        add("Homebuilder / REIT pressure", lvl,
            f"10y (mortgage benchmark) {'+' if b >= 0 else ''}{b} bp in 5 sessions — {'rate headwind for housing & REITs' if b >= 5 else 'rate relief for housing & REITs' if b <= -5 else 'little rate impulse'}.", tone)
    s210 = sp.get("2s10s")
    if s210:
        d21 = s210.get("d21")
        state = "inverted" if s210["inverted"] else "positive"
        dirn = s210.get("trend") or "flat"
        lvl = "Support" if (not s210["inverted"] and (d21 or 0) > 5) else "Pressure" if (s210["inverted"] and (d21 or 0) < -5) else "Neutral"
        add("Financial sector (curve carry)", lvl,
            f"2s10s {s210['bp']:+.0f} bp ({state}), {d21 if d21 is not None else '—'} bp over 1 month ({dirn}). Steeper positive curve widens bank net-interest margins.",
            "up" if lvl == "Support" else "down" if lvl == "Pressure" else "mut")
    t2 = y.get("2Y")
    if t2 and t2["bp21d"] is not None:
        b = t2["bp21d"]
        add("Dollar support", "Support" if b >= 10 else "Pressure" if b <= -10 else "Neutral",
            f"2y yield {'+' if b >= 0 else ''}{b} bp over 1 month — short-rate differentials {'attract' if b >= 10 else 'repel' if b <= -10 else 'barely move'} dollar flows.",
            "up" if b >= 10 else "down" if b <= -10 else "mut")
        add("Small-cap financing pressure",
            "High" if (t2["pct52w"] or 0) >= 75 and b > 0 else "Easing" if b <= -10 else "Moderate",
            f"2y at {t2['yield']:.2f}% ({t2['pct52w']:.0f}th pctile of 52w){', still rising' if b > 0 else ''} — funding costs for leveraged small caps.",
            "down" if (t2["pct52w"] or 0) >= 75 and b > 0 else "up" if b <= -10 else "mut")
    r10 = exp.get("real10")
    if r10 and r10.get("d21") is not None:
        rb = round(r10["d21"] * 100, 1)
        add("Gold", "Pressure" if rb >= 10 else "Support" if rb <= -10 else "Neutral",
            f"10y real yield {r10['value']:.2f}%, {'+' if rb >= 0 else ''}{rb} bp over 1 month. Rising real yields raise gold's opportunity cost.",
            "down" if rb >= 10 else "up" if rb <= -10 else "mut")
    be10 = exp.get("be10")
    if be10:
        db = round((be10.get("d21") or 0) * 100, 1)
        lvl = "Elevated" if (be10["pct52w"] or 0) >= 80 else "Rising" if db >= 8 else "Contained" if (be10["pct52w"] or 50) <= 40 else "Moderate"
        add("Inflation concern", lvl,
            f"10y breakeven {be10['value']:.2f}% ({be10['pct52w']:.0f}th pctile of 52w), {db:+.1f} bp over 1 month.",
            "down" if lvl in ("Elevated", "Rising") else "up" if lvl == "Contained" else "mut")
    s3m10 = sp.get("3m10y")
    if s3m10:
        add("Recession signal (3m10y)",
            "Inverted" if s3m10["inverted"] else "Positive",
            f"3m10y at {s3m10['bp']:+.0f} bp, {s3m10.get('d21') if s3m10.get('d21') is not None else '—'} bp over 1 month ({s3m10.get('trend') or '—'}). Condition shown, not a forecast.",
            "down" if s3m10["inverted"] else "up")
    if move:
        add("Rates volatility / liquidity", move["regime"].capitalize(),
            f"MOVE {move['value']} ({move['pct52w']:.0f}th pctile of 52w), {move['d5'] if move['d5'] is not None else '—'} over 5d. Elevated MOVE = thinner Treasury liquidity, wider risk premia. (Treasury vol, NOT equity VIX.)",
            "down" if move["regime"] in ("high", "extreme") else "mut" if move["regime"] == "elevated" else "up")
    reg = core.get("regime")
    if reg:
        pref = ("long duration favored" if reg["d10y_bp"] < -3 else
                "short duration favored" if reg["d10y_bp"] > 3 else "no duration edge")
        add("Duration trade", pref.split(" favored")[0].capitalize() if "favored" in pref else "Neutral",
            f"Curve regime: {reg['label']} (2y {reg['d2y_bp']:+.1f} bp, 10y {reg['d10y_bp']:+.1f} bp over {reg['window']}). {pref}.",
            "up" if reg["d10y_bp"] < -3 else "down" if reg["d10y_bp"] > 3 else "mut")
        risk = ("Risk-off" if (move and move["regime"] in ("high", "extreme")) or reg["d10y_bp"] >= 12
                else "Risk-on" if reg["d10y_bp"] <= -5 and (not move or move["regime"] in ("low", "normal"))
                else "Mixed")
        move_txt = ", MOVE {} ({})".format(move["value"], move["regime"]) if move else ""
        add("Rates signal for risk assets", risk,
            f"10y {reg['d10y_bp']:+.1f} bp/5d{move_txt} — composite of long-yield impulse and rates volatility.",
            "up" if risk == "Risk-on" else "down" if risk == "Risk-off" else "mut")
    return sig


# ── 5/6. CPI & inflation ─────────────────────────────────────────────────────
_CPI_SERIES = [
    ("headline", "CPIAUCSL", "Headline CPI"),
    ("core", "CPILFESL", "Core CPI"),
    ("shelter", "CUSR0000SAH1", "Shelter"),
    ("services", "CUSR0000SASLE", "Services ex-energy services"),
    ("goods", "CUSR0000SACL1E", "Core goods"),
    ("food", "CPIUFDSL", "Food"),
    ("energy", "CPIENGSL", "Energy"),
    ("used_vehicles", "CUSR0000SETA02", "Used vehicles"),
    ("medical", "CPIMEDSL", "Medical care"),
    ("oer", "CUSR0000SEHC", "Owners' equivalent rent"),
]


def _mom(vals, n=1):
    if len(vals) <= n:
        return None
    return (vals[-1] / vals[-1 - n] - 1) * 100


def _ann(vals, months):
    """Annualized % change over trailing `months` months."""
    if len(vals) <= months:
        return None
    return ((vals[-1] / vals[-1 - months]) ** (12 / months) - 1) * 100


def get_inflation() -> dict:
    def build():
        out: dict[str, Any] = {"as_of": _now_iso(), "ok": False,
                               "source": "BLS via FRED (seasonally adjusted); consensus estimates have no free reliable source and are shown as unavailable"}
        rows = []
        head_vals = core_vals = None
        for key, sid, label in _CPI_SERIES:
            obs = _fred_series(sid, 43200, start="1999-01-01")
            if len(obs) < 60:
                rows.append({"key": key, "label": label, "ok": False})
                continue
            vals = [v for _, v in obs]
            yoy_series = [(vals[i] / vals[i - 12] - 1) * 100 for i in range(12, len(vals))]
            mom = _mom(vals)
            row = {
                "key": key, "label": label, "ok": True,
                "month": obs[-1][0][:7],
                "mom": round(mom, 2) if mom is not None else None,
                "mom_prev": round(_mom(vals[:-1]), 2) if len(vals) > 2 else None,
                "yoy": round(yoy_series[-1], 2) if yoy_series else None,
                "yoy_prev": round(yoy_series[-2], 2) if len(yoy_series) > 1 else None,
                "yoy_pctile_10y": _pctile(yoy_series[-120:], yoy_series[-1]) if yoy_series else None,
                "consensus": None,
            }
            if key == "headline":
                head_vals = vals
            if key == "core":
                core_vals = vals
                row["ann3m"] = round(_ann(vals, 3), 2)
                row["ann6m"] = round(_ann(vals, 6), 2)
            rows.append(row)
        out["rows"] = rows
        out["supercore"] = {"ok": False, "note": "Supercore (core services ex-shelter) is a computed BLS aggregate without a public FRED series — not estimated."}
        # Trend chart series (monthly)
        def chart_series(vals, dates):
            yoy = [{"d": dates[i][:7], "v": round((vals[i] / vals[i - 12] - 1) * 100, 2)}
                   for i in range(12, len(vals))]
            momr = [{"d": dates[i][:7], "v": round((vals[i] / vals[i - 1] - 1) * 100, 2)}
                    for i in range(1, len(vals))]
            return yoy, momr
        charts = {}
        if head_vals:
            dts = [d for d, _ in _fred_series("CPIAUCSL", 43200, start="1999-01-01")]
            charts["headline_yoy"], charts["headline_mom"] = chart_series(head_vals, dts)
        if core_vals:
            dts = [d for d, _ in _fred_series("CPILFESL", 43200, start="1999-01-01")]
            charts["core_yoy"], charts["core_mom"] = chart_series(core_vals, dts)
            charts["core_3m_ann"] = [{"d": dts[i][:7], "v": round(((core_vals[i] / core_vals[i - 3]) ** 4 - 1) * 100, 2)}
                                     for i in range(3, len(core_vals))]
            charts["core_6m_ann"] = [{"d": dts[i][:7], "v": round(((core_vals[i] / core_vals[i - 6]) ** 2 - 1) * 100, 2)}
                                     for i in range(6, len(core_vals))]
        out["charts"] = charts
        out["release_dates"] = CPI_SCHEDULE
        out["reactions"] = _cpi_reactions(head_vals, core_vals)
        out["ok"] = any(r.get("ok") for r in rows)
        return out
    return _cached("inflation", 21600, build)


def _cpi_reactions(head_vals, core_vals) -> dict:
    """Daily market reaction on past CPI release days. Close-to-close only —
    intraday 5-min/1-hour windows need intraday history we don't reliably
    have, so those columns are honestly unavailable. Hot/cool is classified
    vs the prior 6-month average core MoM (transparent rule), because there
    is no free consensus feed."""
    out = {"ok": False, "rows": [],
           "note": ("Reaction = close-to-close on release day (delayed Yahoo data). "
                    "No consensus feed → hot/cool means core MoM vs its prior 6-month average "
                    "(±0.05pp). Correlation, not causation."),
           "intraday": "First-5-minute / first-hour windows: Data unavailable (no intraday history source)."}
    try:
        rel_dates = sorted(d for ds in CPI_SCHEDULE.values() for d in ds)
        today = _et_now().date().isoformat()
        past = [d for d in rel_dates if d < today][-15:]
        dgs2 = dict(_fred_series("DGS2"))
        dgs10 = dict(_fred_series("DGS10"))
        core_obs = _fred_series("CPILFESL", 43200, start="1999-01-01")
        head_obs = _fred_series("CPIAUCSL", 43200, start="1999-01-01")
        px = {}
        if _YF_OK:
            try:
                df = yf.download("SPY QQQ IWM TLT GLD UUP", period="2y", interval="1d",
                                 auto_adjust=False, progress=False, group_by="ticker", threads=False)
                for sym in ["SPY", "QQQ", "IWM", "TLT", "GLD", "UUP"]:
                    try:
                        closes = df[sym]["Close"].dropna()
                        px[sym] = {str(k.date()): float(v) for k, v in closes.items()}
                    except Exception:
                        continue
            except Exception as exc:  # noqa: BLE001
                print(f"[treasury] cpi reaction px failed: {exc}", file=sys.stderr)
        def day_ret(sym, d):
            m = px.get(sym)
            if not m or d not in m:
                return None
            ds = sorted(m.keys())
            i = ds.index(d)
            if i == 0:
                return None
            return round((m[d] / m[ds[i - 1]] - 1) * 100, 2)
        def yld_chg(series, d):
            ds = sorted(series.keys())
            if d not in series:
                return None
            i = ds.index(d)
            if i == 0:
                return None
            return round((series[d] - series[ds[i - 1]]) * 100, 1)
        # Map release date → the data month it covered (previous month)
        core_mom_by_month = {}
        vals = [v for _, v in core_obs]
        dts = [d[:7] for d, _ in core_obs]
        for i in range(1, len(vals)):
            core_mom_by_month[dts[i]] = round((vals[i] / vals[i - 1] - 1) * 100, 2)
        head_mom_by_month = {}
        hv = [v for _, v in head_obs]
        hd = [d[:7] for d, _ in head_obs]
        for i in range(1, len(hv)):
            head_mom_by_month[hd[i]] = round((hv[i] / hv[i - 1] - 1) * 100, 2)
        rows = []
        for d in past:
            rd = date.fromisoformat(d)
            data_month = (rd.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
            core_mom = core_mom_by_month.get(data_month)
            head_mom = head_mom_by_month.get(data_month)
            # classify vs prior 6m average core MoM
            klass = None
            if core_mom is not None:
                months = sorted(core_mom_by_month.keys())
                if data_month in months:
                    i = months.index(data_month)
                    prior = [core_mom_by_month[m] for m in months[max(0, i - 6):i]]
                    if len(prior) >= 4:
                        avg = sum(prior) / len(prior)
                        klass = "hot" if core_mom > avg + 0.05 else "cool" if core_mom < avg - 0.05 else "inline"
            rows.append({
                "date": d, "data_month": data_month,
                "headline_mom": head_mom, "core_mom": core_mom, "class": klass,
                "consensus": None,
                "y2_bp": yld_chg(dgs2, d), "y10_bp": yld_chg(dgs10, d),
                "spy": day_ret("SPY", d), "qqq": day_ret("QQQ", d), "iwm": day_ret("IWM", d),
                "tlt": day_ret("TLT", d), "gld": day_ret("GLD", d), "uup": day_ret("UUP", d),
            })
        out["rows"] = rows[::-1]
        # Average absolute CPI-day move (for "what does CPI day usually do")
        def avg_abs(key):
            vals = [abs(r[key]) for r in rows if r.get(key) is not None]
            return round(sum(vals) / len(vals), 2) if len(vals) >= 5 else None
        out["avg_abs"] = {"spy": avg_abs("spy"), "qqq": avg_abs("qqq"),
                          "y10_bp": avg_abs("y10_bp"), "n": len(rows)}
        out["ok"] = bool(rows)
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


# ── 9/10/15. Futures, ETF proxies, cross-asset correlations ─────────────────
ETF_DURATION = {"BIL": 0.1, "SHY": 1.9, "IEI": 4.4, "IEF": 7.4, "TLT": 16.3,
                "EDV": 24.0, "TIP": 6.6, "SGOV": 0.1}


def get_markets() -> dict:
    def build():
        out: dict[str, Any] = {"as_of": _now_iso(), "ok": _YF_OK,
                               "source": "Yahoo Finance (delayed ~15 min)"}
        if not _YF_OK:
            out["error"] = "yfinance unavailable"
            return out
        # Treasury futures
        futs = []
        fut_defs = [("ZT=F", "ZT", "2-year note"), ("ZF=F", "ZF", "5-year note"),
                    ("ZN=F", "ZN", "10-year note"), ("ZB=F", "ZB", "30-year bond"),
                    ("UB=F", "UB", "Ultra bond")]
        try:
            fdf = yf.download(" ".join(s for s, _, _ in fut_defs), period="10d", interval="1d",
                              auto_adjust=False, progress=False, group_by="ticker", threads=False)
            for sym, code, label in fut_defs:
                try:
                    sub = fdf[sym].dropna(subset=["Close"])
                    if sub.empty:
                        futs.append({"code": code, "label": label, "ok": False})
                        continue
                    last, prev = float(sub["Close"].iloc[-1]), float(sub["Close"].iloc[-2]) if len(sub) > 1 else None
                    futs.append({
                        "code": code, "label": label, "ok": True,
                        "last": round(last, 3),
                        "chg_abs": round(last - prev, 3) if prev else None,
                        "chg_pct": round((last / prev - 1) * 100, 2) if prev else None,
                        "day_lo": round(float(sub["Low"].iloc[-1]), 3),
                        "day_hi": round(float(sub["High"].iloc[-1]), 3),
                        "volume": int(sub["Volume"].iloc[-1]) if sub["Volume"].iloc[-1] == sub["Volume"].iloc[-1] else None,
                        "date": str(sub.index[-1].date()),
                        "implied_yield": None,   # not accurately derivable from price alone (CTD/conversion) — not estimated
                        "open_interest": None,
                    })
                except Exception:
                    futs.append({"code": code, "label": label, "ok": False})
        except Exception as exc:  # noqa: BLE001
            print(f"[treasury] futures failed: {exc}", file=sys.stderr)
        out["futures"] = futs
        out["futures_note"] = "Front-month continuous, delayed. Implied yield & open interest need CME data and are omitted rather than estimated. Bond futures PRICES move opposite to yields."

        # ETF proxies
        etfs = []
        syms = list(ETF_DURATION.keys())
        try:
            edf = yf.download(" ".join(syms), period="1y", interval="1d",
                              auto_adjust=False, progress=False, group_by="ticker", threads=False)
            for sym in syms:
                try:
                    sub = edf[sym].dropna(subset=["Close"])
                    c = sub["Close"]
                    if len(c) < 30:
                        etfs.append({"sym": sym, "ok": False})
                        continue
                    last = float(c.iloc[-1])
                    def ret(n):
                        return round((last / float(c.iloc[-1 - n]) - 1) * 100, 2) if len(c) > n else None
                    vol = sub["Volume"]
                    v_last = float(vol.iloc[-1])
                    v_avg = float(vol.tail(21).mean())
                    def dma(n):
                        if len(c) < n:
                            return None
                        m = float(c.tail(n).mean())
                        return round((last / m - 1) * 100, 2)
                    etfs.append({
                        "sym": sym, "ok": True, "last": round(last, 2),
                        "d1": ret(1), "d5": ret(5), "d21": ret(21),
                        "volume": int(v_last), "rel_volume": round(v_last / v_avg, 2) if v_avg else None,
                        "dma20": dma(20), "dma50": dma(50), "dma200": dma(200),
                        "duration": ETF_DURATION.get(sym),
                        "yield": None,   # distribution yields not reliably available — not estimated
                        "date": str(sub.index[-1].date()),
                    })
                except Exception:
                    etfs.append({"sym": sym, "ok": False})
        except Exception as exc:  # noqa: BLE001
            print(f"[treasury] etfs failed: {exc}", file=sys.stderr)
        out["etfs"] = etfs
        out["etf_note"] = "Duration = approximate effective duration (static reference). ETF prices move OPPOSITE to yields."

        # Cross-asset correlations vs Δ10y (FRED DGS10)
        out["correlations"] = _correlations()
        return out
    return _cached("markets", 900, build)


def _correlations() -> dict:
    corr_syms = [("SPY", "SPY"), ("QQQ", "QQQ"), ("IWM", "IWM"), ("XLF", "XLF"),
                 ("XLRE", "XLRE"), ("XHB", "XHB"), ("GLD", "Gold (GLD)"),
                 ("UUP", "US Dollar (UUP proxy)"), ("BTC-USD", "Bitcoin"), ("CL=F", "Crude oil")]
    out = {"ok": False, "windows": [20, 60, 120, 252], "rows": [],
           "note": "Pearson correlation of daily asset returns vs daily CHANGE in the 10y yield (FRED DGS10). Correlation ≠ causation."}
    try:
        dgs10 = _fred_series("DGS10")
        if len(dgs10) < 300 or not _YF_OK:
            return out
        ymap = {d: v for d, v in dgs10}
        ydates = sorted(ymap.keys())
        ychg = {ydates[i]: ymap[ydates[i]] - ymap[ydates[i - 1]] for i in range(1, len(ydates))}
        df = yf.download(" ".join(s for s, _ in corr_syms), period="2y", interval="1d",
                         auto_adjust=False, progress=False, group_by="ticker", threads=False)
        import math
        for sym, label in corr_syms:
            try:
                closes = df[sym]["Close"].dropna()
                rets = {}
                prev_d, prev_v = None, None
                for k, v in closes.items():
                    dstr = str(k.date())
                    if prev_v:
                        rets[dstr] = float(v) / prev_v - 1
                    prev_v = float(v)
                common = sorted(set(rets) & set(ychg))
                row = {"sym": sym, "label": label}
                for w in out["windows"]:
                    ds = common[-w:]
                    if len(ds) < max(15, int(w * 0.7)):
                        row[f"w{w}"] = None
                        continue
                    xs = [ychg[d] for d in ds]
                    ys = [rets[d] for d in ds]
                    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
                    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
                    vx = math.sqrt(sum((a - mx) ** 2 for a in xs))
                    vy = math.sqrt(sum((b - my) ** 2 for b in ys))
                    row[f"w{w}"] = round(cov / (vx * vy), 2) if vx and vy else None
                out["rows"].append(row)
            except Exception:
                out["rows"].append({"sym": sym, "label": label})
        out["ok"] = bool(out["rows"])
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


# ── 12. Auctions ─────────────────────────────────────────────────────────────
def get_auctions() -> dict:
    def build():
        out: dict[str, Any] = {"as_of": _now_iso(), "ok": False,
                               "source": "TreasuryDirect auction API (official)",
                               "note": ("Strength rule: bid-to-cover and indirect share vs the average of the prior 10 auctions "
                                        "of the same security. Tail vs when-issued yield needs WI quotes (no free source) — not shown.")}
        data = _get_json("https://www.treasurydirect.gov/TA_WS/securities/auctioned?days=400&format=json")
        if not isinstance(data, list):
            out["error"] = "TreasuryDirect unavailable"
            return out
        def fnum(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        recs = []
        for r in data:
            total = fnum(r.get("totalAccepted"))
            ind, dr, pd_ = fnum(r.get("indirectBidderAccepted")), fnum(r.get("directBidderAccepted")), fnum(r.get("primaryDealerAccepted"))
            recs.append({
                "type": r.get("securityType"), "term": r.get("securityTerm"),
                "date": (r.get("auctionDate") or "")[:10],
                "settle": (r.get("issueDate") or "")[:10],
                "offering": fnum(r.get("offeringAmount")),
                "high_yield": fnum(r.get("highYield")) or fnum(r.get("highDiscountRate")),
                "btc": fnum(r.get("bidToCoverRatio")),
                "indirect_pct": round(ind / total * 100, 1) if ind and total else None,
                "direct_pct": round(dr / total * 100, 1) if dr and total else None,
                "dealer_pct": round(pd_ / total * 100, 1) if pd_ and total else None,
            })
        recs.sort(key=lambda r: r["date"])
        # Strength vs prior 10 same term+type
        by_key: dict[str, list] = {}
        for r in recs:
            key = f"{r['type']}|{r['term']}"
            prior = by_key.get(key, [])
            btc_prior = [p["btc"] for p in prior[-10:] if p["btc"]]
            ind_prior = [p["indirect_pct"] for p in prior[-10:] if p["indirect_pct"]]
            if r["btc"] and len(btc_prior) >= 4:
                btc_avg = sum(btc_prior) / len(btc_prior)
                ind_avg = sum(ind_prior) / len(ind_prior) if ind_prior else None
                d_btc = r["btc"] - btc_avg
                d_ind = (r["indirect_pct"] - ind_avg) if (ind_avg and r["indirect_pct"]) else 0
                if d_btc > 0.12 and d_ind >= 0:
                    r["strength"] = "strong"
                elif d_btc < -0.12 and d_ind <= 0:
                    r["strength"] = "weak"
                else:
                    r["strength"] = "average"
                r["vs_prior"] = {"btc_avg10": round(btc_avg, 2),
                                 "indirect_avg10": round(ind_avg, 1) if ind_avg else None,
                                 "n": len(btc_prior)}
            by_key.setdefault(key, []).append(r)
        notes_terms = ("2-Year", "3-Year", "5-Year", "7-Year", "10-Year", "20-Year", "30-Year")
        recent_notes = [r for r in recs if r["type"] in ("Note", "Bond") and any(r["term"].startswith(t) for t in notes_terms)]
        out["recent_coupons"] = recent_notes[-20:][::-1]
        out["recent_bills"] = [r for r in recs if r["type"] == "Bill"][-12:][::-1]
        out["ok"] = bool(recs)
        return out
    return _cached("auctions", 21600, build)


# ── 13. Fed expectations ─────────────────────────────────────────────────────
_MONTH_CODES = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
                7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}


def get_fed() -> dict:
    def build():
        out: dict[str, Any] = {"as_of": _now_iso(), "ok": False}
        up = _fred_series("DFEDTARU", 3600)
        lo = _fred_series("DFEDTARL", 3600)
        if up and lo:
            out["target"] = {"upper": up[-1][1], "lower": lo[-1][1], "date": up[-1][0],
                             "source": "FRED DFEDTARU/DFEDTARL (official target range)"}
            out["ok"] = True
        today = _et_now().date()
        meetings = [d for d in FOMC_2026 if date.fromisoformat(d) >= today]
        out["meetings"] = meetings
        out["next_meeting"] = {"date": meetings[0], "days": (date.fromisoformat(meetings[0]) - today).days} if meetings else None
        out["meetings_source"] = "Federal Reserve 2026 FOMC calendar"
        # Market-implied path from CME 30-day fed funds futures (ZQ) via Yahoo.
        path = []
        if _YF_OK and meetings:
            months = []
            d0 = today.replace(day=1)
            for i in range(10):
                m = (d0.month - 1 + i) % 12 + 1
                yy = d0.year + (d0.month - 1 + i) // 12
                months.append((yy, m))
            tickers = [f"ZQ{_MONTH_CODES[m]}{str(y)[-2:]}.CBT" for y, m in months]
            try:
                zq = yf.download(" ".join(tickers), period="5d", interval="1d",
                                 auto_adjust=False, progress=False, group_by="ticker", threads=False)
                for (y, m), tk in zip(months, tickers):
                    try:
                        c = zq[tk]["Close"].dropna()
                        if c.empty:
                            continue
                        last, prev = float(c.iloc[-1]), (float(c.iloc[-2]) if len(c) > 1 else None)
                        path.append({"month": f"{y}-{m:02d}", "implied_rate": round(100 - last, 3),
                                     "d1_bp": round(((100 - last) - (100 - prev)) * 100, 1) if prev else None})
                    except Exception:
                        continue
            except Exception as exc:  # noqa: BLE001
                print(f"[treasury] ZQ failed: {exc}", file=sys.stderr)
        out["implied_path"] = path
        out["implied_note"] = ("Implied avg fed funds rate = 100 − ZQ futures price per month "
                               "(CME 30-day FF futures via Yahoo, delayed). Per-meeting cut/hike "
                               "probabilities need CME FedWatch data — shown unavailable, not estimated.")
        if path and out.get("target"):
            mid = (out["target"]["upper"] + out["target"]["lower"]) / 2
            dec = next((p for p in path if p["month"].endswith("-12")), path[-1])
            out["yearend"] = {"implied_rate": dec["implied_rate"], "month": dec["month"],
                              "cuts_25bp": round((mid - dec["implied_rate"]) / 0.25, 1)}
        return out
    return _cached("fed", 3600, build)


# ── 14. COT positioning ──────────────────────────────────────────────────────
_COT_MARKETS = [("2Y", "UST 2Y NOTE"), ("5Y", "UST 5Y NOTE"),
                ("10Y", "UST 10Y NOTE"), ("30Y", "UST BOND")]


def _cot_week_file() -> dict[str, dict] | None:
    """Fallback: CFTC's own weekly TFF futures-only file (FinFutWk.txt).
    Socrata rate-limits anonymous callers per IP — shared cloud egress IPs
    (Railway) get throttled hard, which showed up as 'CFTC unavailable'.
    This file is the same data, latest week only (no percentile history).
    Column indices verified against the Socrata API for the same report:
    7 OI, 8/9 dealer L/S, 11/12 asset-mgr L/S, 14/15 lev L/S,
    24.. weekly change block (OI, dealer L/S/sp, AM L/S/sp, lev L/S/sp)."""
    def fetch():
        txt = _get("https://www.cftc.gov/dea/newcot/FinFutWk.txt")
        if not txt:
            return None
        import csv as _csv
        out = {}
        for row in _csv.reader(txt.splitlines()):
            if not row or " - " not in (row[0] or ""):
                continue
            name = row[0].split(" - ")[0].strip()
            try:
                g = lambda i: int(row[i])
                out[name] = {
                    "date": row[2].strip(),
                    "open_interest": g(7),
                    "dealer": {"net": g(8) - g(9), "wk_chg": g(25) - g(26)},
                    "asset_mgr": {"net": g(11) - g(12), "wk_chg": g(28) - g(29)},
                    "lev_funds": {"net": g(14) - g(15), "wk_chg": g(31) - g(32)},
                }
            except (ValueError, IndexError):
                continue
        return out or None
    return _cached("cot_weekfile", 21600, fetch)


def get_cot() -> dict:
    def build():
        out: dict[str, Any] = {"as_of": _now_iso(), "ok": False,
                               "source": "CFTC Traders in Financial Futures (futures only), weekly",
                               "note": ("Net = long − short contracts. Percentile over ~3 years of weekly reports. "
                                        "Crowded = net at ≥90th or ≤10th percentile. Large positions are context, "
                                        "not automatic buy/sell signals.")}
        import urllib.parse
        rows = []
        for code, name in _COT_MARKETS:
            qs = urllib.parse.urlencode({
                "$where": f"contract_market_name='{name}'",
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": "160",
            })
            data = _get_json(f"https://publicreporting.cftc.gov/resource/gpe5-46if.json?{qs}")
            if not isinstance(data, list) or not data:
                # Socrata throttled → CFTC's own weekly file (no percentiles).
                wk = (_cot_week_file() or {}).get(name)
                if wk:
                    def wgrp(g):
                        return {"net": g["net"], "wk_chg": g["wk_chg"], "pctile": None, "crowded": None}
                    rows.append({
                        "code": code, "ok": True, "date": wk["date"],
                        "open_interest": wk["open_interest"],
                        "asset_mgr": wgrp(wk["asset_mgr"]), "lev_funds": wgrp(wk["lev_funds"]),
                        "dealer": wgrp(wk["dealer"]),
                        "noncommercial": {"net": wk["asset_mgr"]["net"] + wk["lev_funds"]["net"], "pctile": None},
                        "fallback": "CFTC weekly file (percentiles need the throttled history API)",
                    })
                else:
                    rows.append({"code": code, "ok": False})
                continue
            def nets(field_l, field_s):
                seq = []
                for r in reversed(data):
                    try:
                        seq.append(int(r[field_l]) - int(r[field_s]))
                    except (KeyError, TypeError, ValueError):
                        seq.append(None)
                return seq
            am = nets("asset_mgr_positions_long", "asset_mgr_positions_short")
            lev = nets("lev_money_positions_long", "lev_money_positions_short")
            dlr = nets("dealer_positions_long_all", "dealer_positions_short_all")
            latest = data[0]
            def grp(seq, chg_l, chg_s):
                cur = seq[-1]
                if cur is None:
                    return None
                try:
                    wk = int(latest[chg_l]) - int(latest[chg_s])
                except (KeyError, TypeError, ValueError):
                    wk = None
                pct = _pctile([v for v in seq if v is not None], cur)
                return {"net": cur, "wk_chg": wk, "pctile": pct,
                        "crowded": ("long" if pct is not None and pct >= 90 else
                                    "short" if pct is not None and pct <= 10 else None)}
            noncomm = [(a if a is not None else 0) + (l if l is not None else 0) for a, l in zip(am, lev)]
            rows.append({
                "code": code, "ok": True,
                "date": (latest.get("report_date_as_yyyy_mm_dd") or "")[:10],
                "open_interest": int(latest.get("open_interest_all") or 0),
                "asset_mgr": grp(am, "change_in_asset_mgr_long", "change_in_asset_mgr_short"),
                "lev_funds": grp(lev, "change_in_lev_money_long", "change_in_lev_money_short"),
                "dealer": grp(dlr, "change_in_dealer_long_all", "change_in_dealer_short_all"),
                "noncommercial": {"net": noncomm[-1], "pctile": _pctile(noncomm, noncomm[-1])},
            })
        out["rows"] = rows
        out["ok"] = any(r.get("ok") for r in rows)
        return out
    return _cached("cot", 43200, build)


# ── 16. Rate-sensitivity board (watchlist scan) ─────────────────────────────
_SENSE_LOCK = threading.RLock()
_SENSE: dict[str, Any] = {"scanning": False, "scanned": 0, "total": 0,
                          "last_scan": None, "rows": [], "error": None}


def _sense_worker(symbols: list[str]) -> None:
    import math
    try:
        if not _YF_OK:
            raise RuntimeError("yfinance unavailable")
        factors = {}
        for key, sid in [("y2", "DGS2"), ("y10", "DGS10"), ("y30", "DGS30"), ("real10", "DFII10")]:
            obs = _fred_series(sid, 3600)
            m = {d: v for d, v in obs}
            ds = sorted(m)
            factors[key] = {ds[i]: (m[ds[i]] - m[ds[i - 1]]) * 100 for i in range(1, len(ds))}  # bp
        # curve factor: Δ(10y − 2y)
        common0 = sorted(set(factors["y2"]) & set(factors["y10"]))
        factors["curve"] = {d: factors["y10"][d] - factors["y2"][d] for d in common0}
        rows = []
        CH = 60
        for i in range(0, len(symbols), CH):
            part = symbols[i:i + CH]
            try:
                df = yf.download(" ".join(part), period="1y", interval="1d",
                                 auto_adjust=False, progress=False, group_by="ticker", threads=False)
                multi = isinstance(df.columns, pd.MultiIndex)
                for sym in part:
                    try:
                        closes = (df[sym]["Close"] if multi else df["Close"]).dropna()
                        rets = {}
                        prev = None
                        for k, v in closes.items():
                            dstr = str(k.date())
                            if prev:
                                rets[dstr] = (float(v) / prev - 1) * 100
                            prev = float(v)
                        row = {"ticker": sym}
                        ok_any = False
                        for fkey, fmap in factors.items():
                            common = sorted(set(rets) & set(fmap))[-120:]
                            n = len(common)
                            if n < 60:
                                row[fkey] = {"n": n, "ok": False}
                                continue
                            xs = [fmap[d] for d in common]
                            ys = [rets[d] for d in common]
                            mx, my = sum(xs) / n, sum(ys) / n
                            cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / n
                            vx = sum((a - mx) ** 2 for a in xs) / n
                            vy = sum((b - my) ** 2 for b in ys) / n
                            if vx <= 0 or vy <= 0:
                                row[fkey] = {"n": n, "ok": False}
                                continue
                            corr = cov / math.sqrt(vx * vy)
                            beta = cov / vx * 10          # % move per +10bp
                            t = corr * math.sqrt(max(n - 2, 1)) / math.sqrt(max(1e-9, 1 - corr * corr))
                            conf = "high" if abs(t) >= 3 else "medium" if abs(t) >= 2 else "insufficient"
                            row[fkey] = {"n": n, "ok": conf != "insufficient",
                                         "corr": round(corr, 2), "beta10bp": round(beta, 2),
                                         "t": round(t, 1), "conf": conf}
                            ok_any = ok_any or conf != "insufficient"
                        if ok_any:
                            rows.append(row)
                    except Exception:
                        continue
            except Exception:
                pass
            with _SENSE_LOCK:
                _SENSE["scanned"] = min(len(symbols), i + CH)
            time.sleep(0.3)
        with _SENSE_LOCK:
            _SENSE["rows"] = rows
            _SENSE["last_scan"] = _now_iso()
            _SENSE["error"] = None
    except Exception as exc:  # noqa: BLE001
        with _SENSE_LOCK:
            _SENSE["error"] = str(exc)
    finally:
        with _SENSE_LOCK:
            _SENSE["scanning"] = False


def trigger_sense(symbols: list[str] | None, force: bool = False) -> dict:
    with _SENSE_LOCK:
        if _SENSE["scanning"] and not force:
            return {"started": False, "reason": "already scanning"}
        syms = list(dict.fromkeys(symbols or []))
        if not syms:
            return {"started": False, "reason": "watchlist empty"}
        _SENSE.update({"scanning": True, "scanned": 0, "total": len(syms)})
    threading.Thread(target=_sense_worker, args=(syms,), daemon=True).start()
    return {"started": True, "total": len(syms)}


def get_sense() -> dict:
    with _SENSE_LOCK:
        return {"as_of": _now_iso(),
                "status": {k: _SENSE[k] for k in ("scanning", "scanned", "total", "last_scan", "error")},
                "rows": list(_SENSE["rows"]),
                "note": ("Beta = % stock move per +10bp factor move, last 120 trading days vs FRED daily changes. "
                         "Confidence from the t-statistic (high ≥3, medium ≥2). Below 2 → insufficient, no conclusion shown.")}
