"""movers.py — Pre-market movers scanner (v1).

The price-driven twin of analyst_board: batch-quotes the whole universe
(one Schwab call), keeps the names gapping the most, enriches only those
with market cap / sector / relative volume, tags *why* it's moving
(fresh analyst action from the board, heavy volume), and ranks them.

Cheap on rate limits: Schwab `get_quotes` pulls ~600 names in one call;
yfinance enrichment runs only for the top movers. Background scan with
progress + cache, same pattern as analyst_board.

Requires Schwab for the live/premarket quote; degrades to an empty board
if Schwab isn't configured.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

try:
    import yfinance as yf
    _YF_OK = True
except Exception:
    _YF_OK = False

import analyst_board

try:
    import schwab_client
    _SCHWAB_OK = True
except Exception:
    _SCHWAB_OK = False

# Only names gapping at least this much (abs %) count as movers.
MIN_GAP_PCT = 1.0
# Enrich (market cap / sector / rel-vol) at most this many top movers.
ENRICH_TOP_N = 60

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "scanning": False, "scanned": 0, "total": 0, "last_scan": None,
    "movers": [], "universe_size": 0, "error": None,
}
_THREAD: threading.Thread | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cap_points(mcap: float | None) -> tuple[int, str]:
    if not mcap:
        return 0, "cap unknown"
    b = mcap / 1e9
    if b >= 200: return 8, "mega cap"
    if b >= 50:  return 6, "large cap"
    if b >= 10:  return 3, "mid cap"
    if b >= 2:   return -4, "small cap"
    return -10, "micro cap (manipulation risk)"


def _enrich(symbol: str, premarket_vol: float | None) -> dict:
    out: dict[str, Any] = {"market_cap": None, "sector": None,
                           "company": None, "rel_vol": None}
    if not _YF_OK:
        return out
    try:
        info = yf.Ticker(symbol).info or {}
        out["market_cap"] = info.get("marketCap")
        out["sector"] = info.get("sector")
        out["company"] = info.get("shortName") or info.get("longName")
        avg = info.get("averageVolume") or info.get("averageDailyVolume10Day")
        v = premarket_vol or info.get("regularMarketVolume")
        if avg and v:
            out["rel_vol"] = round(v / avg, 2)
    except Exception:
        pass
    return out


def _score(gap: float, enrich: dict, has_analyst: bool) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = min(45.0, abs(gap) * 4.0)
    reasons.append(f"{gap:+.1f}% gap")
    rv = enrich.get("rel_vol")
    if isinstance(rv, (int, float)) and rv:
        score += min(25.0, rv * 5.0)
        if rv >= 1:
            reasons.append(f"{rv:.1f}x normal volume")
    mp, why = _cap_points(enrich.get("market_cap"))
    score += mp
    reasons.append(why)
    if has_analyst:
        score += 12
        reasons.append("fresh analyst action")
    return max(0.0, min(100.0, score)), reasons


def _scan_worker(symbols: list[str]) -> None:
    try:
        if not _SCHWAB_OK:
            raise RuntimeError("Schwab not available")
        sc = schwab_client.get_client()
        if not sc or not sc.is_configured():
            raise RuntimeError("Schwab not configured")

        # Tickers that had a fresh analyst action (for catalyst tagging).
        try:
            analyst_tickers = {a.get("ticker") for a in analyst_board.get_board().get("actions", [])}
        except Exception:
            analyst_tickers = set()

        # Pass 1 — batch quote the whole universe (chunked).
        quotes: dict[str, dict] = {}
        CHUNK = 200
        for i in range(0, len(symbols), CHUNK):
            part = symbols[i:i + CHUNK]
            try:
                q = sc.get_quotes(part) or {}
                quotes.update(q)
            except Exception:
                pass
            with _LOCK:
                _STATE["scanned"] = min(len(symbols), i + CHUNK)
            time.sleep(0.2)

        # Filter to movers.
        movers = []
        for sym, q in quotes.items():
            gap = q.get("change_pct")
            if q.get("last") is None or not isinstance(gap, (int, float)):
                continue
            if abs(gap) < MIN_GAP_PCT:
                continue
            movers.append((sym, q, gap))
        movers.sort(key=lambda x: -abs(x[2]))

        # Pass 2 — enrich + score the top movers.
        out = []
        for sym, q, gap in movers[:ENRICH_TOP_N]:
            pmv = q.get("extended_volume") or q.get("volume")
            enrich = _enrich(sym, pmv)
            has_analyst = sym in analyst_tickers
            score, reasons = _score(gap, enrich, has_analyst)
            out.append({
                "ticker": sym,
                "company": enrich.get("company") or q.get("name"),
                "sector": enrich.get("sector") or "Unknown",
                "gap_pct": round(gap, 2),
                "direction": "up" if gap >= 0 else "down",
                "last": q.get("last"),
                "premarket_vol": pmv,
                "rel_vol": enrich.get("rel_vol"),
                "market_cap": enrich.get("market_cap"),
                "session": q.get("session"),
                "has_analyst": has_analyst,
                "score": round(score, 1),
                "importance": "high" if score >= 55 else "medium" if score >= 32 else "low",
                "reasons": reasons,
            })
            time.sleep(0.08)

        out.sort(key=lambda m: -m["score"])
        with _LOCK:
            _STATE["movers"] = out
            _STATE["last_scan"] = _now_iso()
            _STATE["error"] = None
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _STATE["error"] = str(exc)
    finally:
        with _LOCK:
            _STATE["scanning"] = False


def trigger_scan(watchlist_syms: list[str] | None = None, force: bool = False) -> dict:
    global _THREAD
    with _LOCK:
        if _STATE["scanning"] and not force:
            return {"started": False, "reason": "already scanning"}
        syms = list(dict.fromkeys([*(watchlist_syms or []), *analyst_board._load_universe()]))
        _STATE.update({"scanning": True, "scanned": 0, "total": len(syms),
                       "universe_size": len(syms)})
    _THREAD = threading.Thread(target=_scan_worker, args=(syms,), daemon=True)
    _THREAD.start()
    return {"started": True, "total": len(syms)}


def _summary(movers: list[dict]) -> dict:
    ups = [m for m in movers if m["direction"] == "up"]
    downs = [m for m in movers if m["direction"] == "down"]
    relvol = sorted([m for m in movers if isinstance(m.get("rel_vol"), (int, float))],
                    key=lambda m: -m["rel_vol"])[:8]
    sectors: dict[str, int] = {}
    for m in movers:
        s = m.get("sector") or "Unknown"
        sectors[s] = sectors.get(s, 0) + 1
    return {
        "top_gainers": sorted(ups, key=lambda m: -m["gap_pct"])[:8],
        "top_losers": sorted(downs, key=lambda m: m["gap_pct"])[:8],
        "high_relvol": relvol,
        "with_catalyst": [m for m in movers if m.get("has_analyst")][:8],
        "active_sectors": [{"sector": k, "count": v}
                           for k, v in sorted(sectors.items(), key=lambda x: -x[1])][:6],
    }


def get_board() -> dict:
    with _LOCK:
        movers = list(_STATE["movers"])
        status = {
            "scanning": _STATE["scanning"], "scanned": _STATE["scanned"],
            "total": _STATE["total"], "last_scan": _STATE["last_scan"],
            "universe_size": _STATE["universe_size"], "error": _STATE["error"],
        }
    return {"as_of": _now_iso(), "status": status, "count": len(movers),
            "movers": movers, "summary": _summary(movers)}
