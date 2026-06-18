"""analyst_board.py — Morning analyst upgrades/downgrades board (v1).

Aggregates the latest analyst actions (upgrades, downgrades, initiations,
reiterations, price-target raises/cuts) across a universe of liquid
tickers, enriches the names that actually moved with premarket data, and
ranks each action by an importance score so the morning game plan is
"what actually matters" rather than a raw news feed.

Data sources (all free, already in the stack):
  • analyst_client.get_analyst_data() — per-firm action history with
    date, firm, action_class, prior/new grade, prior/new target,
    target_change_pct, pt_action. (Yahoo + optional Finnhub.)
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

import json
import os
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
}
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

    score = max(0.0, min(100.0, score))
    importance = "high" if score >= 60 else "medium" if score >= 38 else "low"

    direction = _direction(
        act.get("action_class", ""), act.get("prior_grade"),
        act.get("new_grade"), act.get("pt_action"),
    )

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
    # Fundamentals + (fallback) premarket via yfinance
    if _YF_OK:
        try:
            info = yf.Ticker(symbol).info or {}
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
        except Exception:
            pass
    out.pop("_vol", None)
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
    try:
        client = analyst_client.get_client()
        # Pass 1 — collect recent action rows per ticker (cheap, cached).
        raw: dict[str, list[dict]] = {}
        for i, sym in enumerate(symbols):
            try:
                data = client.get_analyst_data(sym)
                rows = _recent_rows(data.get("history") or [], recent_days)
                if rows:
                    raw[sym] = rows
            except Exception:
                pass
            with _LOCK:
                _STATE["scanned"] = i + 1
            time.sleep(0.15)  # gentle throttle to avoid Yahoo 429s

        # Pass 2 — enrich only the names that had an action, then score.
        actions: list[dict] = []
        for sym, rows in raw.items():
            enrich = _enrich(sym)
            multi = len(rows)
            for r in rows:
                actions.append(score_action(r, enrich, multi))
            time.sleep(0.1)

        actions.sort(key=lambda a: -a["score"])
        with _LOCK:
            _STATE["actions"] = actions
            _STATE["last_scan"] = _now_iso()
            _STATE["error"] = None
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _STATE["error"] = str(exc)
    finally:
        with _LOCK:
            _STATE["scanning"] = False


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

    def loop():
        last_run_date = None
        while True:
            try:
                now = datetime.now(zone)
                now_min = now.hour * 60 + now.minute
                in_window = target_min <= now_min < target_min + 60
                if now.weekday() < 5 and in_window and last_run_date != now.date():
                    syms = []
                    if get_watchlist_fn:
                        try:
                            syms = get_watchlist_fn() or []
                        except Exception:
                            syms = []
                    res = trigger_scan(syms, recent_days=2)
                    if res.get("started"):
                        last_run_date = now.date()
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
        }
    return {
        "as_of": _now_iso(),
        "status": status,
        "count": len(actions),
        "actions": actions,
        "summary": _build_summary(actions),
    }
