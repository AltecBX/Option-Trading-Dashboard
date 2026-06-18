"""ivrank.py — Volatility-rank screener for premium selling (v1).

Surfaces where option premium is rich vs cheap across the universe.

Honest note: a *true* IV rank needs a year of historical implied vol per
name, which no free source provides for ~600 tickers. This uses
realized (historical) volatility as the free proxy — realized and
implied vol track closely, so the percentile of current HV within its
own 1-year range is a sound "is vol rich or cheap here" signal for a
premium seller. The Trade tab still shows the real option IV per name
when you drill in.

Per name (from ~1y daily closes, batched via yf.download):
  • HV20  — 20-day realized vol, annualized %
  • vol rank — where current HV sits in its 1y min..max (0-100)
  • vol percentile — % of the last year below current HV
  • regime — rich / elevated / normal / cheap
  • vol trend — HV now vs ~1 month ago (expanding / contracting)
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from typing import Any

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    _OK = True
except Exception:
    _OK = False

import analyst_board

CHUNK = 60

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "scanning": False, "scanned": 0, "total": 0, "last_scan": None,
    "rows": [], "universe_size": 0, "error": None,
}
_THREAD: threading.Thread | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _vol_metrics(closes) -> dict | None:
    if closes is None or len(closes) < 60:
        return None
    logret = np.log(closes / closes.shift(1))
    hv = logret.rolling(20).std() * math.sqrt(252) * 100.0
    hv = hv.dropna()
    if len(hv) < 30:
        return None
    current = float(hv.iloc[-1])
    window = hv.tail(252)
    mn, mx = float(window.min()), float(window.max())
    rank = (current - mn) / (mx - mn) * 100.0 if mx > mn else 50.0
    pct = float((window < current).mean() * 100.0)
    month_ago = float(hv.iloc[-21]) if len(hv) >= 21 else current
    return {
        "hv": round(current, 1),
        "hv_low": round(mn, 1),
        "hv_high": round(mx, 1),
        "rank": round(max(0.0, min(100.0, rank)), 0),
        "percentile": round(pct, 0),
        "expanding": current > month_ago * 1.05,
        "contracting": current < month_ago * 0.95,
    }


def _regime(rank: float) -> str:
    if rank >= 70: return "rich"
    if rank >= 50: return "elevated"
    if rank >= 30: return "normal"
    return "cheap"


def _scan_worker(symbols: list[str]) -> None:
    try:
        if not _OK:
            raise RuntimeError("yfinance/pandas unavailable")
        rows = []
        for i in range(0, len(symbols), CHUNK):
            part = symbols[i:i + CHUNK]
            try:
                df = yf.download(" ".join(part), period="1y", interval="1d",
                                 progress=False, group_by="ticker", threads=True)
                multi = isinstance(df.columns, pd.MultiIndex)
                for sym in part:
                    try:
                        closes = (df[sym]["Close"] if multi else df["Close"]).dropna()
                    except Exception:
                        continue
                    m = _vol_metrics(closes)
                    if not m:
                        continue
                    regime = _regime(m["rank"])
                    reasons = [f"HV {m['hv']}% (1y {m['hv_low']}–{m['hv_high']})",
                               f"vol rank {int(m['rank'])} · pctile {int(m['percentile'])}"]
                    if m["expanding"]: reasons.append("vol expanding")
                    elif m["contracting"]: reasons.append("vol contracting")
                    rows.append({
                        "ticker": sym,
                        "last": round(float(closes.iloc[-1]), 2),
                        "hv": m["hv"], "hv_low": m["hv_low"], "hv_high": m["hv_high"],
                        "rank": m["rank"], "percentile": m["percentile"],
                        "regime": regime,
                        "expanding": m["expanding"], "contracting": m["contracting"],
                        "score": m["rank"],  # rank IS the premium-richness score
                        "importance": "high" if m["rank"] >= 70 else "medium" if m["rank"] >= 40 else "low",
                        "reasons": reasons,
                    })
            except Exception:
                pass
            with _LOCK:
                _STATE["scanned"] = min(len(symbols), i + CHUNK)
            time.sleep(0.3)
        rows.sort(key=lambda r: -r["rank"])
        with _LOCK:
            _STATE["rows"] = rows
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


def _summary(rows: list[dict]) -> dict:
    return {
        "richest": rows[:10],  # already sorted by rank desc
        "cheapest": sorted(rows, key=lambda r: r["rank"])[:10],
        "expanding": [r for r in rows if r.get("expanding")][:10],
        "contracting": [r for r in rows if r.get("contracting")][:10],
    }


def get_board() -> dict:
    with _LOCK:
        rows = list(_STATE["rows"])
        status = {
            "scanning": _STATE["scanning"], "scanned": _STATE["scanned"],
            "total": _STATE["total"], "last_scan": _STATE["last_scan"],
            "universe_size": _STATE["universe_size"], "error": _STATE["error"],
        }
    return {"as_of": _now_iso(), "status": status, "count": len(rows),
            "rows": rows, "summary": _summary(rows)}
