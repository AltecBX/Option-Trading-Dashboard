"""analyst_board.py — Morning analyst upgrades/downgrades board (v1).

Aggregates the latest analyst actions (upgrades, downgrades, initiations,
reiterations, price-target raises/cuts) across a universe of liquid
tickers, enriches the names that actually moved with premarket data, and
ranks each action by an importance score so the morning game plan is
"what actually matters" rather than a raw news feed.

Data sources (already in the stack):
  • Unusual Whales /api/screener/analysts — the whole market's analyst
    tape in one call, polled every two minutes through the trading day
    (the "fast lane", v5.00). Rows carry the minute a note printed and
    the analyst's name. This is what puts a 10:40 AM upgrade on the
    board at 10:42 instead of tomorrow at 8.
  • analyst_client.get_analyst_data() — per-firm action history with
    date, firm, action_class, prior/new grade, prior/new target,
    target_change_pct, pt_action. (Yahoo + optional Finnhub.) The daily
    sweep, for depth: Yahoo carries prior targets and rating wording.
  • schwab_client quotes — premarket / extended-hours price + volume.
  • yfinance .info — market cap, sector, average volume.

Design for rate limits: the universe scan only pulls the (cached)
analyst history per ticker; the expensive premarket/market-cap/sector
enrichment runs ONLY for the handful of names that had a recent action.
The scan runs in a background thread that updates a daily cache and
exposes progress, so the HTTP endpoint never blocks.

Honest limitations (free data):
  • Coverage is the scanned universe (+ your watchlist), not the entire
    market — free analyst data is per-ticker and rate-limited.
  • "Additional news" and "technical levels" scoring factors are coarse
    approximations; flagged in each action's `reasons`.
"""
from __future__ import annotations

import gc
import json
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False
try:
    import sector_map as _sector_map    # SEC SIC → sector when Yahoo is quiet (v4.76)
except Exception:                       # pragma: no cover
    _sector_map = None

import analyst_client

try:
    import schwab_client
    _SCHWAB_OK = True
except Exception:
    _SCHWAB_OK = False


# ── Scan universe ──────────────────────────────────────────────────────
# A curated set of liquid names most likely to be discussed premarket on
# CNBC/Bloomberg when an analyst call hits. The user's watchlist is merged
# in at scan time. Kept as a tunable constant so it's trivial to expand.
UNIVERSE: list[str] = [
    # Mega-cap tech / comms
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "ORCL",
    "AMD", "CRM", "ADBE", "NFLX", "INTC", "QCOM", "TXN", "CSCO", "IBM",
    "MU", "AMAT", "LRCX", "KLAC", "ARM", "PLTR", "SMCI", "DELL", "NOW",
    "SNOW", "PANW", "CRWD", "UBER", "ABNB", "SHOP", "SQ", "PYPL", "COIN",
    # Semis / hardware extras
    "ASML", "ON", "MRVL", "MCHP", "ADI", "WDC", "STX",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "AXP", "V", "MA",
    "BX", "KKR", "COF", "USB", "PNC", "BRK-B",
    # Healthcare / pharma / biotech
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "AMGN",
    "GILD", "BMY", "CVS", "ISRG", "VRTX", "REGN", "MRNA", "HUM",
    # Consumer / retail
    "WMT", "COST", "HD", "LOW", "TGT", "NKE", "SBUX", "MCD", "CMG",
    "DIS", "PG", "KO", "PEP", "LULU", "DECK", "RCL", "CCL",
    # Industrials / energy / materials
    "BA", "CAT", "DE", "GE", "HON", "LMT", "RTX", "UPS", "FDX", "UNP",
    "XOM", "CVX", "COP", "SLB", "OXY", "FCX", "NEM", "LIN",
    # Autos / EV / movers
    "F", "GM", "RIVN", "LCID", "NIO",
    # Telecom / media / misc high-volume
    "T", "VZ", "TMUS", "CMCSA",
    # Popular high-beta / meme-ish movers that get analyst calls
    "SOFI", "RBLX", "DKNG", "AFRM", "HOOD", "DAL", "AAL", "UAL", "MARA",
    "RIOT", "ROKU", "PINS", "SNAP", "ZM", "DOCU", "TTD", "NET", "DDOG",
    # International / ADRs (mostly not in the S&P 500)
    "BABA", "PDD", "JD", "BIDU", "LI", "XPEV", "NU", "SE", "MELI", "GRAB",
    "STLA", "SONY", "TSM", "NVO", "AZN", "SAP", "SHEL", "BP", "RIO", "TME",
    "BEKE",
    # Recent IPOs / secular-growth names with heavy coverage
    "RDDT", "CART", "CAVA", "TOST", "DASH", "U", "PATH", "AI", "IOT",
    "SOUN", "IONQ", "RGTI", "QBTS", "RKLB", "ASTS", "ACHR", "JOBY", "CHPT",
    "RUN", "PLUG", "CVNA", "CARG", "W", "CHWY", "DUOL", "HIMS", "OSCR",
    "APP", "DJT", "SMR", "OKLO", "TLN", "TEM", "PENN", "BYD",
    # Crypto-adjacent
    "MSTR", "CLSK", "HUT", "BITF", "CIFR", "WULF", "IREN", "BTBT",
    # Biotech movers
    "NVAX", "VKTX", "CRSP", "NTLA", "BEAM", "RXRX", "SAVA", "BNTX",
    # Fintech / retail / meme
    "UPST", "OPEN", "LMND", "ROOT", "GME", "AMC",
]

# Full universe = S&P 500 (fetched + cached daily) ∪ the curated movers
# above ∪ the user's watchlist → ~600 names. The S&P 500 list comes from a
# stable GitHub-hosted constituents CSV; if that fetch ever fails we fall
# back to just the curated list so a scan still runs.
_SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
_UNIVERSE_CACHE: dict | None = None  # {"date": iso, "syms": [...]}


def _data_dir() -> Path:
    d = os.environ.get("JERRY_DATA_DIR", "").strip()
    p = Path(d).expanduser() if d else (Path.home() / ".jerry-dashboard")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def _fetch_sp500() -> list[str]:
    req = urllib.request.Request(_SP500_URL, headers={"User-Agent": "Mozilla/5.0"})
    csv = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
    out = []
    for line in csv.strip().splitlines()[1:]:  # skip header
        sym = line.split(",")[0].strip().strip('"').upper()
        if sym:
            out.append(sym.replace(".", "-"))  # BRK.B -> BRK-B for yfinance
    return out


def _load_universe() -> list[str]:
    """S&P 500 (fetched, cached for the day) merged with the curated movers
    list. Falls back to the curated list alone if the fetch fails."""
    global _UNIVERSE_CACHE
    today = datetime.now(timezone.utc).date().isoformat()
    if _UNIVERSE_CACHE and _UNIVERSE_CACHE.get("date") == today:
        return _UNIVERSE_CACHE["syms"]
    cache_file = _data_dir() / "universe_cache.json"
    try:
        if cache_file.exists():
            j = json.loads(cache_file.read_text())
            if j.get("date") == today and j.get("syms"):
                _UNIVERSE_CACHE = j
                return j["syms"]
    except Exception:
        pass
    try:
        sp = _fetch_sp500()
    except Exception:
        sp = []
    syms = list(dict.fromkeys([*UNIVERSE, *sp]))
    if len(syms) < 200:  # fetch failed/short — use curated, don't cache it
        return list(dict.fromkeys(UNIVERSE))
    payload = {"date": today, "syms": syms}
    try:
        cache_file.write_text(json.dumps(payload))
    except Exception:
        pass
    _UNIVERSE_CACHE = payload
    return syms


# ── Analyst-firm reputation tiers (subset; everything else = baseline) ──
FIRM_TIER_1 = {  # bulge bracket / most market-moving
    "goldman sachs", "morgan stanley", "jpmorgan", "jp morgan", "j.p. morgan",
    "bank of america", "bofa securities", "bofa", "merrill", "citigroup", "citi",
    "wells fargo", "ubs", "barclays", "deutsche bank", "evercore", "evercore isi",
}
FIRM_TIER_2 = {  # top research / well-followed
    "jefferies", "wedbush", "piper sandler", "td cowen", "cowen", "mizuho",
    "truist", "rbc capital", "rbc", "baird", "stifel", "raymond james",
    "oppenheimer", "bernstein", "redburn atlantic", "wolfe research", "guggenheim",
    "keybanc", "needham", "loop capital", "hsbc", "scotiabank", "bmo capital",
    "bmo", "william blair", "canaccord", "susquehanna", "melius",
}

# ── Module state ───────────────────────────────────────────────────────
# Only one heavy universe scan (analyst / movers / trend / ivrank) may run
# at a time. On a small free-tier container, two 600-name scans at once is
# what tips it into OOM. The other scanner modules import and hold this.
HEAVY_SCAN_LOCK = threading.Semaphore(1)

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "scanning": False,
    "scanned": 0,
    "total": 0,
    "last_scan": None,       # iso str
    "started": None,
    "actions": [],           # ranked list (built after each scan)
    "universe_size": 0,
    "recent_days": 2,
    "error": None,
    # Fast lane — the market-wide Unusual Whales poll (see refresh_fast_lane).
    "fast_last": None,       # iso str of the last successful poll
    "fast_last_ts": 0.0,     # epoch of the last attempt, for the cadence gate
    "fast_added": 0,         # rows the last poll added
    "fast_error": None,
    "pushed": [],            # "TICKER|firmkey|date" keys already sent, persisted
}

# Fast lane cadence. Two days of tape so a note that printed after the
# close still sits on tomorrow's board; a two-minute poll between 4 AM and
# 8 PM ET on weekdays, which is when notes print.
FAST_LANE_DAYS = 2
FAST_LANE_EVERY_SEC = 120
FAST_LANE_START_MIN = 4 * 60
FAST_LANE_END_MIN = 20 * 60
# Enriching a name costs a Schwab quote plus a Yahoo .info; a poll that
# brings twelve new names would otherwise stall the scheduler thread.
FAST_LANE_ENRICH_CAP = 6
_PUSHED_CAP = 600
_THREAD: threading.Thread | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _firm_tier_points(firm: str | None) -> tuple[int, str]:
    if not firm:
        return 0, "unknown firm"
    f = firm.strip().lower()
    for name in FIRM_TIER_1:
        if name in f:
            return 12, "top-tier firm"
    for name in FIRM_TIER_2:
        if name in f:
            return 7, "well-followed firm"
    return 2, "other firm"


def _market_cap_points(mcap: float | None) -> tuple[int, str]:
    """Larger caps score higher — small caps are easier to manipulate."""
    if not mcap:
        return 0, "cap unknown"
    b = mcap / 1e9
    if b >= 200: return 8, "mega cap"
    if b >= 50:  return 6, "large cap"
    if b >= 10:  return 3, "mid cap"
    if b >= 2:   return -3, "small cap (manipulation risk)"
    return -8, "micro cap (manipulation risk)"


def _direction(action_class: str, prior_grade, new_grade, pt_action) -> str:
    """bull / bear / neutral from the action."""
    if action_class == "upgrade":
        return "bull"
    if action_class == "downgrade":
        return "bear"
    pa = (pt_action or "").lower()
    if action_class == "target_change":
        if "rais" in pa: return "bull"
        if "cut" in pa or "lower" in pa or "reduc" in pa: return "bear"
    if action_class == "initiate":
        g = (new_grade or "").lower()
        if any(w in g for w in ("buy", "outperform", "overweight", "positive")): return "bull"
        if any(w in g for w in ("sell", "underperform", "underweight", "negative")): return "bear"
    if action_class == "reiterate":
        g = (new_grade or "").lower()
        if any(w in g for w in ("buy", "outperform", "overweight")): return "bull"
        if any(w in g for w in ("sell", "underperform", "underweight")): return "bear"
    return "neutral"


def _action_base_points(action_class: str) -> tuple[int, str]:
    return {
        "upgrade":       (30, "rating upgrade"),
        "downgrade":     (30, "rating downgrade"),
        "initiate":      (18, "new coverage initiated"),
        "target_change": (10, "price-target change only"),
        "reiterate":     (5,  "reiterated rating"),
    }.get(action_class, (4, "analyst action"))


def score_action(act: dict, enrich: dict, multi_count: int) -> dict:
    """Return the action enriched with score (0-100), importance label,
    direction, and a human-readable list of scoring reasons."""
    reasons: list[str] = []
    score = 0.0

    base, why = _action_base_points(act.get("action_class", ""))
    score += base
    reasons.append(why)

    # Price-target move magnitude (raise/cut size)
    tpct = act.get("target_change_pct")
    if isinstance(tpct, (int, float)) and tpct:
        mag = min(10.0, abs(tpct) * 0.6)
        score += mag
        if abs(tpct) >= 5:
            reasons.append(f"PT moved {tpct:+.1f}%")

    # Firm reputation
    fp, fwhy = _firm_tier_points(act.get("firm"))
    score += fp
    if fp >= 7:
        reasons.append(fwhy)

    # Market cap
    mp, mwhy = _market_cap_points(enrich.get("market_cap"))
    score += mp
    reasons.append(mwhy)

    # Premarket move (abs %)
    pm = enrich.get("premarket_pct")
    if isinstance(pm, (int, float)):
        score += min(20.0, abs(pm) * 2.2)
        if abs(pm) >= 2:
            reasons.append(f"premarket {pm:+.1f}%")

    # Premarket volume vs average (conviction)
    vr = enrich.get("vol_ratio")
    if isinstance(vr, (int, float)) and vr:
        score += min(12.0, vr * 6.0)
        if vr >= 0.5:
            reasons.append(f"heavy early volume ({vr:.1f}x normal)")

    # Multiple firms acting on the same ticker today
    if multi_count > 1:
        score += min(16.0, (multi_count - 1) * 8.0)
        reasons.append(f"{multi_count} firms acted today")

    direction = _direction(
        act.get("action_class", ""), act.get("prior_grade"),
        act.get("new_grade"), act.get("pt_action"),
    )

    # Additional news beyond the analyst note — a real catalyst stacking
    # on the call. yfinance counts the rating itself, so require >= 2.
    nc = enrich.get("news_count")
    if isinstance(nc, int) and nc >= 2:
        score += min(10.0, nc * 3.0)
        reasons.append(f"extra news today ({nc} items)")

    # Technical posture: reward when price confirms the call's direction
    # (above the 200-DMA for a bull call, below it for a bear call).
    a200 = enrich.get("above_ma200")
    a50 = enrich.get("above_ma50")
    if direction == "bull":
        if a200 is True: score += 6; reasons.append("above 200-DMA (uptrend)")
        elif a50 is True: score += 3; reasons.append("above 50-DMA")
    elif direction == "bear":
        if a200 is False: score += 6; reasons.append("below 200-DMA (downtrend)")
        elif a50 is False: score += 3; reasons.append("below 50-DMA")

    score = max(0.0, min(100.0, score))
    importance = "high" if score >= 60 else "medium" if score >= 38 else "low"

    # Suspicious / weak: a big premarket move on a weak action, or a move
    # with no real rating change behind it.
    suspicious = False
    if isinstance(pm, (int, float)) and abs(pm) >= 4 and act.get("action_class") in ("reiterate", "target_change") and base <= 10:
        suspicious = True
        reasons.append("big move vs weak analyst action — verify catalyst")

    return {
        **act,
        "ticker": enrich.get("ticker"),
        "sector": enrich.get("sector") or "Unknown",
        "company": enrich.get("company"),
        "market_cap": enrich.get("market_cap"),
        "premarket_pct": pm,
        "vol_ratio": vr,
        "news_count": enrich.get("news_count"),
        "above_ma50": enrich.get("above_ma50"),
        "above_ma200": enrich.get("above_ma200"),
        "score": round(score, 1),
        "importance": importance,
        "direction": direction,
        "multi_count": multi_count,
        "suspicious": suspicious,
        "reasons": reasons,
    }


def _recent_rows(history: list[dict], days: int) -> list[dict]:
    """Keep action rows whose date is within `days` calendar days of now."""
    if not history:
        return []
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days))
    out = []
    for r in history:
        d = r.get("date")
        if not d:
            continue
        try:
            rd = datetime.strptime(d[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if rd >= cutoff:
            out.append(r)
    return out


def _enrich(symbol: str) -> dict:
    """Premarket move/volume + market cap + sector for one active name.
    Schwab for the live/premarket quote, yfinance .info for fundamentals.
    Best-effort: any missing piece is left as None."""
    out: dict[str, Any] = {
        "ticker": symbol, "premarket_pct": None, "vol_ratio": None,
        "market_cap": None, "sector": None, "company": None,
        "news_count": None, "above_ma50": None, "above_ma200": None,
    }
    # Premarket move + volume via Schwab (handles extended session)
    if _SCHWAB_OK:
        try:
            sc = schwab_client.get_client()
            if sc and sc.is_configured():
                q = sc.get_quote(symbol)
                if q:
                    out["premarket_pct"] = q.get("change_pct")
                    vol = q.get("extended_volume") or q.get("volume")
                    out["_vol"] = vol
        except Exception:
            pass
    # Fundamentals + (fallback) premarket + news catalyst + technicals via
    # yfinance (one Ticker, reused).
    if _YF_OK:
        try:
            t = yf.Ticker(symbol)
            info = t.info or {}
            out["market_cap"] = info.get("marketCap")
            out["sector"] = info.get("sector")
            out["company"] = info.get("shortName") or info.get("longName")
            avg_vol = info.get("averageVolume") or info.get("averageDailyVolume10Day")
            v = out.get("_vol") or info.get("preMarketVolume") or info.get("regularMarketVolume")
            if avg_vol and v:
                out["vol_ratio"] = round(v / avg_vol, 2)
            if out["premarket_pct"] is None:
                pmp = info.get("preMarketChangePercent")
                if pmp is not None:
                    out["premarket_pct"] = round(pmp, 2)
            # Additional news in the last 48h (beyond the analyst note).
            try:
                cutoff = time.time() - 48 * 3600
                cnt = 0
                for it in (t.news or []):
                    ts = it.get("providerPublishTime")
                    if ts is None and isinstance(it.get("content"), dict):
                        pd = it["content"].get("pubDate") or ""
                        try:
                            ts = datetime.strptime(pd[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                        except Exception:
                            ts = None
                    if isinstance(ts, (int, float)) and ts >= cutoff:
                        cnt += 1
                out["news_count"] = cnt
            except Exception:
                pass
            # Technical posture: price vs 50/200-day moving averages.
            try:
                hist = t.history(period="1y", interval="1d")
                closes = [c for c in list(hist["Close"]) if c == c]  # drop NaN
                if len(closes) >= 50:
                    last = closes[-1]
                    out["above_ma50"] = last >= sum(closes[-50:]) / 50
                    if len(closes) >= 200:
                        out["above_ma200"] = last >= sum(closes[-200:]) / 200
            except Exception:
                pass
        except Exception:
            pass
    out.pop("_vol", None)
    # No sector from Yahoo is Yahoo being quiet, not a company without one.
    # The SEC's classification fills the blank so the sector rollups on the
    # board count the name instead of filing it under "Unknown".
    if not out.get("sector") and _sector_map is not None:
        try:
            hint = _sector_map.sector_hint(symbol)
        except Exception:
            hint = {}
        if hint.get("sector"):
            out["sector"] = hint["sector"]
            out["sector_source"] = "sec"
    return out


def _build_summary(actions: list[dict]) -> dict:
    """Group ranked actions into the morning game-plan buckets."""
    bull = [a for a in actions if a["direction"] == "bull"]
    bear = [a for a in actions if a["direction"] == "bear"]

    sector_pos: dict[str, int] = {}
    sector_neg: dict[str, int] = {}
    for a in actions:
        s = a.get("sector") or "Unknown"
        if a["direction"] == "bull":
            sector_pos[s] = sector_pos.get(s, 0) + 1
        elif a["direction"] == "bear":
            sector_neg[s] = sector_neg.get(s, 0) + 1

    def _sector_list(d):
        return [{"sector": k, "count": v} for k, v in sorted(d.items(), key=lambda x: -x[1])][:6]

    def _dedupe(rows):
        # actions are pre-sorted by score, so the first row per ticker is
        # its highest-scoring action — one clean row per name.
        seen: dict[str, dict] = {}
        for r in rows:
            t = r.get("ticker")
            if t and t not in seen:
                seen[t] = r
        return list(seen.values())

    multi = _dedupe([a for a in actions if a.get("multi_count", 1) > 1])
    biggest_pm = _dedupe(sorted(
        [a for a in actions if isinstance(a.get("premarket_pct"), (int, float))],
        key=lambda a: -abs(a["premarket_pct"])))[:8]
    meaningful = _dedupe([a for a in actions if a["importance"] == "high" and not a["suspicious"]])[:10]
    suspicious = _dedupe([a for a in actions if a["suspicious"]])[:8]
    watch = _dedupe(sorted(actions, key=lambda a: -a["score"]))[:10]

    return {
        "top_bullish": _dedupe(bull)[:8],
        "top_bearish": _dedupe(bear)[:8],
        "sectors_positive": _sector_list(sector_pos),
        "sectors_negative": _sector_list(sector_neg),
        "multi_action": multi[:8],
        "biggest_premarket": biggest_pm,
        "meaningful": meaningful,
        "suspicious": suspicious,
        "watch_after_open": watch,
    }


def _scan_worker(symbols: list[str], recent_days: int) -> None:
    """Background scan: collect recent actions, enrich active names, score."""
    HEAVY_SCAN_LOCK.acquire()
    try:
        client = analyst_client.get_client()
        # Pass 1 — collect recent action rows per ticker (cheap, cached).
        raw: dict[str, list[dict]] = {}
        src_by_sym: dict[str, str] = {}
        for i, sym in enumerate(symbols):
            try:
                # fast=False: the sweep is for depth. Freshness comes from
                # the fast lane's ONE market-wide call, not 600 per-ticker ones.
                data = client.get_analyst_data(sym, fast=False)
                rows = _recent_rows(data.get("history") or [], recent_days)
                if rows:
                    raw[sym] = rows
                    src_by_sym[sym] = data.get("source") or "analyst"
            except Exception:
                pass
            with _LOCK:
                _STATE["scanned"] = i + 1
            if i % 50 == 0:
                gc.collect()  # keep yfinance's per-call growth in check
            time.sleep(0.15)  # gentle throttle to avoid Yahoo 429s

        # Pass 2 — enrich only the names that had an action, then score.
        actions: list[dict] = []
        for sym, rows in raw.items():
            enrich = _enrich(sym)
            multi = len(rows)
            for r in rows:
                r.setdefault("source", src_by_sym.get(sym, "analyst"))
                actions.append(score_action(r, enrich, multi))
            time.sleep(0.1)

        with _LOCK:
            # The sweep must not wipe what the fast lane found: a note that
            # printed at 10:40 and reached the board at 10:42 is exactly the
            # row Yahoo has not caught up to yet, and this sweep reads Yahoo.
            kept = [a for a in _STATE["actions"]
                    if a.get("source") == analyst_client.UW_SOURCE]
            merged = _merge_actions(actions, _recent_rows(kept, max(recent_days, FAST_LANE_DAYS)))
            merged.sort(key=lambda a: -(a.get("score") or 0))
            _STATE["actions"] = merged
            _STATE["last_scan"] = _now_iso()
            _STATE["error"] = None
        _persist_board()  # survive restarts/redeploys so the board is re-readable
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _STATE["error"] = str(exc)
        gc.collect()
    finally:
        with _LOCK:
            _STATE["scanning"] = False
        gc.collect()
        HEAVY_SCAN_LOCK.release()


def trigger_scan(watchlist_syms: list[str] | None = None,
                 recent_days: int = 2, force: bool = False) -> dict:
    """Kick off a background scan if one isn't already running."""
    global _THREAD
    with _LOCK:
        if _STATE["scanning"] and not force:
            return {"started": False, "reason": "already scanning"}
        syms = list(dict.fromkeys([*(watchlist_syms or []), *_load_universe()]))
        _STATE.update({
            "scanning": True, "scanned": 0, "total": len(syms),
            "started": _now_iso(), "universe_size": len(syms),
            "recent_days": recent_days,
        })
    _THREAD = threading.Thread(target=_scan_worker, args=(syms, recent_days), daemon=True)
    _THREAD.start()
    return {"started": True, "total": len(syms)}


# ── Fast lane ──────────────────────────────────────────────────────────
# The morning sweep reads Yahoo per ticker, once a day at 8 AM. Yahoo can
# trail a note by a session, and a note that prints at 10:40 AM is not on
# an 8 AM board at all. The fast lane asks Unusual Whales for the whole
# market's analyst tape in ONE call every two minutes and folds new rows
# into the same board, scored the same way, so the Watchlist section, the
# in-table badge, the gap catalyst and the push all see it within minutes.

def _action_key(a: dict) -> tuple[str, str, str]:
    return (str(a.get("ticker") or "").upper(),
            analyst_client.firm_key(a.get("firm")),
            str(a.get("date") or "")[:10])


def _merge_actions(primary: list[dict], secondary: list[dict]) -> list[dict]:
    """One row per (ticker, firm, day). A primary row wins a collision but
    takes the clock, the analyst and the timestamp from the secondary row
    when it has none — those are what the fast lane knows and Yahoo does
    not. Secondary rows with no counterpart are appended."""
    out: list[dict] = []
    seen: dict[tuple, int] = {}
    for a in primary:
        seen[_action_key(a)] = len(out)
        out.append(dict(a))
    for b in secondary:
        k = _action_key(b)
        i = seen.get(k)
        if i is None:
            seen[k] = len(out)
            out.append(dict(b))
            continue
        for fld in ("time_et", "ts", "analyst"):
            if b.get(fld) and not out[i].get(fld):
                out[i][fld] = b[fld]
    return out


def _prior_from_board(ticker: str | None, firm: str | None, date: str | None):
    """The same firm's most recent earlier target on this ticker, from the
    rows already on the board (the daily sweep carries Yahoo's priors)."""
    if not ticker or not firm or not date:
        return None
    best = None
    with _LOCK:
        rows = list(_STATE["actions"])
    for a in rows:
        if str(a.get("ticker") or "").upper() != str(ticker).upper():
            continue
        if str(a.get("date") or "")[:10] >= date[:10]:
            continue
        if not analyst_client.same_firm(a.get("firm"), firm):
            continue
        nt = a.get("new_target")
        if not isinstance(nt, (int, float)) or not nt:
            continue
        if best is None or str(a.get("date"))[:10] > best[0]:
            best = (str(a.get("date"))[:10], float(nt))
    return best[1] if best else None


def fetch_uw_recent(days: int = FAST_LANE_DAYS, max_pages: int = 4) -> list[dict] | None:
    """The market-wide tape newer than `days` ago, paged by time. None when
    UW is unconfigured or the first call fails — an empty tape is a real
    answer (a quiet holiday), and None is not."""
    try:
        import unusual_whales_client as _uw
    except Exception:
        return None
    client = _uw.get_client()
    if client is None:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    newer = cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rows: list[dict] = []
    older: str | None = None
    for _ in range(max_pages):
        page = client.analyst_ratings(limit=500, newer_than=newer, older_than=older)
        if page is None:
            return None if not rows else rows
        if not isinstance(page, list):
            break
        rows.extend(r for r in page if isinstance(r, dict))
        if len(page) < 500:
            break
        last = page[-1].get("timestamp")
        if not last or last == older:
            break
        older = str(last)
    return rows


def refresh_fast_lane(watchlist_syms: list[str] | None = None, notify_fn=None,
                      days: int = FAST_LANE_DAYS, rows: list[dict] | None = None) -> dict:
    """Fold the latest Unusual Whales analyst tape into the board.

    New rows are scored like the sweep's. Watchlist names without a row on
    the board yet are enriched (capped per poll); every other new name gets
    the sector UW sent and nothing invented. A new upgrade, downgrade,
    initiation or target change on a watchlist name is pushed once — the
    key is persisted so a restart cannot send it again.

    `rows` lets a test hand the tape in; production fetches it."""
    if rows is None:
        rows = fetch_uw_recent(days)
    with _LOCK:
        _STATE["fast_last_ts"] = time.time()
    if rows is None:
        with _LOCK:
            _STATE["fast_error"] = "unusual whales unavailable"
        return {"ok": False, "reason": "unusual whales unavailable"}

    norm = analyst_client.normalize_uw_rows(rows, prior_lookup=_prior_from_board)
    norm = _recent_rows(norm, days)
    wl = {str(s).upper() for s in (watchlist_syms or []) if s}
    with _LOCK:
        existing = list(_STATE["actions"])
        pushed = set(_STATE["pushed"])
    existing_keys = {_action_key(a) for a in existing}
    per_ticker: dict[str, int] = {}
    for r in norm:
        per_ticker[r.get("ticker") or ""] = per_ticker.get(r.get("ticker") or "", 0) + 1

    # Enrichment: reuse what the board already knows about a ticker.
    enrich_by: dict[str, dict] = {}
    for a in existing:
        tk = str(a.get("ticker") or "").upper()
        if tk and tk not in enrich_by:
            enrich_by[tk] = {k: a.get(k) for k in (
                "ticker", "sector", "company", "market_cap", "premarket_pct",
                "vol_ratio", "news_count", "above_ma50", "above_ma200")}
    enriched = 0
    new_rows: list[dict] = []
    for r in norm:
        if _action_key(r) in existing_keys:
            continue
        tk = str(r.get("ticker") or "").upper()
        en = enrich_by.get(tk)
        if en is None:
            if tk in wl and enriched < FAST_LANE_ENRICH_CAP:
                try:
                    en = _enrich(tk)
                except Exception:
                    en = {"ticker": tk}
                enriched += 1
            else:
                en = {"ticker": tk}
            if not en.get("sector") and r.get("sector"):
                en["sector"] = r["sector"]
            enrich_by[tk] = en
        new_rows.append(score_action(r, en, per_ticker.get(tk, 1)))

    merged = _merge_actions(existing, new_rows)
    merged.sort(key=lambda a: -(a.get("score") or 0))
    today_et = datetime.now(analyst_client._et_zone()).date().isoformat()

    # Push: watchlist names, real actions, today, once.
    sent = 0
    if notify_fn and wl:
        for a in new_rows:
            tk = str(a.get("ticker") or "").upper()
            if tk not in wl or a.get("date") != today_et:
                continue
            if a.get("action_class") not in ("upgrade", "downgrade", "initiate", "target_change"):
                continue
            key = "|".join(_action_key(a))
            if key in pushed:
                continue
            try:
                title, body = compose_action_push(a)
                notify_fn(title, body)
                pushed.add(key)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[analyst_board] fast-lane push failed: {exc}", file=sys.stderr)

    with _LOCK:
        _STATE["actions"] = merged
        _STATE["fast_last"] = _now_iso()
        _STATE["fast_added"] = len(new_rows)
        _STATE["fast_error"] = None
        _STATE["pushed"] = sorted(pushed)[-_PUSHED_CAP:]
    if new_rows or sent:
        _persist_board()
    return {"ok": True, "seen": len(norm), "added": len(new_rows), "pushed": sent}


def compose_action_push(a: dict) -> tuple[str, str]:
    """'▲ PLTR: DA Davidson raised target to $250' / the detail line.
    Every figure is quoted from the row; nothing is inferred."""
    arrow = {"bull": "▲", "bear": "▼"}.get(a.get("direction"), "•")
    tk = a.get("ticker") or "?"
    firm = a.get("firm") or "An analyst"
    cls = a.get("action_class")
    nt, pt = a.get("new_target"), a.get("prior_target")
    grade = a.get("new_grade")
    if cls == "upgrade":
        what = f"upgraded to {grade}" if grade else "upgraded"
    elif cls == "downgrade":
        what = f"downgraded to {grade}" if grade else "downgraded"
    elif cls == "initiate":
        what = f"initiated at {grade}" if grade else "initiated coverage"
    else:
        way = "raised" if (a.get("target_change_pct") or 0) > 0 else "cut"
        what = f"{way} target to ${nt:.0f}" if nt else f"{way} target"
    title = f"{arrow} {tk}: {firm} {what}"
    bits = []
    if cls in ("upgrade", "downgrade", "initiate") and nt:
        bits.append(f"target ${nt:.0f}")
    if pt and nt and a.get("target_change_pct") is not None:
        bits.append(f"from ${pt:.0f} ({a['target_change_pct']:+.0f}%)")
    if grade and cls not in ("upgrade", "downgrade", "initiate"):
        bits.append(grade)
    if a.get("analyst"):
        bits.append(a["analyst"])
    if a.get("time_et"):
        bits.append(f"{a['time_et']} ET")
    bits.append("Unusual Whales")
    return title, " · ".join(bits)


def fast_lane_due(now: datetime | None = None) -> bool:
    """Weekdays, 4 AM to 8 PM ET, no more often than the cadence."""
    now = now or datetime.now(analyst_client._et_zone())
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    if not (FAST_LANE_START_MIN <= mins < FAST_LANE_END_MIN):
        return False
    with _LOCK:
        last = _STATE.get("fast_last_ts") or 0.0
    return (time.time() - last) >= FAST_LANE_EVERY_SEC


# ── Auto-scan scheduler ───────────────────────────────────────────────
_SCHED_THREAD: threading.Thread | None = None


def _compose_push(board: dict) -> tuple[str, str]:
    """Concise morning push: top bullish/bearish calls + high-impact count."""
    s = board.get("summary", {})
    acts = board.get("actions", [])
    n_high = sum(1 for a in acts if a.get("importance") == "high")

    def line(a):
        d = "▲" if a.get("direction") == "bull" else "▼" if a.get("direction") == "bear" else "•"
        extra = f" ·{a['multi_count']}f" if a.get("multi_count", 1) > 1 else ""
        return f"{d}{a.get('ticker')} {int(a.get('score', 0))}{extra}"

    parts = []
    if s.get("top_bullish"):
        parts.append("Bull: " + ", ".join(line(a) for a in s["top_bullish"][:3]))
    if s.get("top_bearish"):
        parts.append("Bear: " + ", ".join(line(a) for a in s["top_bearish"][:3]))
    if s.get("multi_action"):
        parts.append("Multi-firm: " + ", ".join(a.get("ticker") for a in s["multi_action"][:4]))
    body = "\n".join(parts) or "No notable analyst actions this morning."
    title = f"Jerry • {len(acts)} analyst calls ({n_high} high-impact)"
    return title, body


def start_scheduler(get_watchlist_fn=None, notify_fn=None, hour: int = 8,
                    minute: int = 0, tz: str = "America/New_York") -> None:
    """Run one scan each weekday morning in a 1-hour window starting at
    `hour:minute` (market timezone). Idempotent; checks once a minute so a
    server restart before/within the window still catches the morning.
    After the scan finishes, `notify_fn(title, message)` (if given) is
    called with the top calls."""
    global _SCHED_THREAD
    if _SCHED_THREAD is not None:
        return
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(tz)
    except Exception:
        zone = timezone.utc  # fall back to UTC if tz database is unavailable

    target_min = hour * 60 + minute
    stamp_file = _data_dir() / "last_autoscan.txt"

    def _already_ran(today_iso: str) -> bool:
        try:
            return stamp_file.read_text().strip() == today_iso
        except Exception:
            return False

    def _mark_ran(today_iso: str) -> None:
        try:
            stamp_file.write_text(today_iso)
        except Exception:
            pass

    def loop():
        while True:
            try:
                now = datetime.now(zone)
                now_min = now.hour * 60 + now.minute
                in_window = target_min <= now_min < target_min + 60
                today_iso = now.date().isoformat()
                # Fast lane first: it is one call, and it is the part of
                # this loop Jerry is waiting on at 7:26 AM.
                if fast_lane_due(now):
                    syms = []
                    if get_watchlist_fn:
                        try:
                            syms = get_watchlist_fn() or []
                        except Exception:
                            syms = []
                    try:
                        res = refresh_fast_lane(syms, notify_fn)
                        if res.get("added") or res.get("pushed"):
                            print(f"[analyst_board] fast lane +{res.get('added')} rows, "
                                  f"{res.get('pushed')} pushed", file=sys.stderr)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[analyst_board] fast lane failed: {exc}", file=sys.stderr)
                # The stamp is persisted to /data and written BEFORE the
                # scan, so a crash/restart inside the window can never
                # re-trigger the heavy scan and crash-loop the container.
                if now.weekday() < 5 and in_window and not _already_ran(today_iso):
                    _mark_ran(today_iso)
                    syms = []
                    if get_watchlist_fn:
                        try:
                            syms = get_watchlist_fn() or []
                        except Exception:
                            syms = []
                    res = trigger_scan(syms, recent_days=2)
                    if res.get("started"):
                        print(f"[analyst_board] auto-scan started "
                              f"{now.isoformat()} ({res.get('total')} names)",
                              file=__import__("sys").stderr)
                        # Wait for completion (~up to 15 min), then push.
                        if notify_fn:
                            for _ in range(60):
                                time.sleep(15)
                                if not get_board()["status"]["scanning"]:
                                    break
                            try:
                                b = get_board()
                                if b.get("count"):
                                    title, msg = _compose_push(b)
                                    notify_fn(title, msg)
                            except Exception:
                                pass
            except Exception:
                pass
            time.sleep(60)

    _SCHED_THREAD = threading.Thread(target=loop, daemon=True)
    _SCHED_THREAD.start()


def get_board() -> dict:
    """Current board snapshot — status + ranked actions + game-plan summary."""
    with _LOCK:
        actions = list(_STATE["actions"])
        status = {
            "scanning": _STATE["scanning"],
            "scanned": _STATE["scanned"],
            "total": _STATE["total"],
            "last_scan": _STATE["last_scan"],
            "universe_size": _STATE["universe_size"],
            "recent_days": _STATE["recent_days"],
            "error": _STATE["error"],
            "fast_lane": {
                "source": analyst_client.UW_SOURCE,
                "last": _STATE["fast_last"],
                "added": _STATE["fast_added"],
                "error": _STATE["fast_error"],
                "every_sec": FAST_LANE_EVERY_SEC,
            },
        }
    return {
        "as_of": _now_iso(),
        "status": status,
        "count": len(actions),
        "actions": actions,
        "summary": _build_summary(actions),
    }


# ── Disk persistence ──────────────────────────────────────────────────
# The board lives in memory, so without this a Railway restart/redeploy (or
# idle recycle) wipes the morning scan and the user has to re-scan. Persist
# the ranked actions to the stable data dir after every scan and restore
# them on startup so the board is still there when you come back.
def _persist_path() -> Path:
    return _data_dir() / "analyst_board.json"


def _persist_board() -> None:
    with _LOCK:
        payload = {
            "actions": _STATE["actions"],
            "last_scan": _STATE["last_scan"],
            "recent_days": _STATE["recent_days"],
            "universe_size": _STATE["universe_size"],
            "fast_last": _STATE["fast_last"],
            "pushed": _STATE["pushed"],
        }
    try:
        path = _persist_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        print(f"[analyst_board] persist failed: {exc}", file=sys.stderr)


def _restore_board() -> None:
    try:
        path = _persist_path()
        if not path.exists():
            return
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return
        with _LOCK:
            _STATE["actions"] = data.get("actions") or []
            _STATE["last_scan"] = data.get("last_scan")
            _STATE["recent_days"] = data.get("recent_days") or _STATE["recent_days"]
            _STATE["universe_size"] = data.get("universe_size") or 0
            _STATE["fast_last"] = data.get("fast_last")
            _STATE["pushed"] = [k for k in (data.get("pushed") or []) if isinstance(k, str)][-_PUSHED_CAP:]
        print(f"[analyst_board] restored {len(_STATE['actions'])} cached actions "
              f"(last scan {_STATE['last_scan']})", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[analyst_board] cache load failed: {exc}", file=sys.stderr)


_restore_board()
