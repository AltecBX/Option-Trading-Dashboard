"""trend.py — Trend / momentum screener (v1).

Answers "what's still trending higher/lower" across the universe. Pulls
~1y of daily closes in batched yfinance downloads (chunked, no per-ticker
calls), then scores each name's trend from price action only:

  • position vs the 20 / 50 / 200-day moving averages (the MA stack)
  • golden/death cross (50 vs 200)
  • proximity to the 52-week high / low
  • RSI(14)
  • up/down day streak

Names are ranked by trend strength and split bull vs bear. Background
scan + progress + cache, same pattern as movers/analyst_board.
"""
from __future__ import annotations

import gc
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

CHUNK = 60  # tickers per batched download

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "scanning": False, "scanned": 0, "total": 0, "last_scan": None,
    "rows": [], "universe_size": 0, "error": None,
}
_THREAD: threading.Thread | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _signals(closes) -> dict | None:
    if closes is None or len(closes) < 60:
        return None
    last = float(closes.iloc[-1])
    ma20 = float(closes.tail(20).mean())
    ma50 = float(closes.tail(50).mean())
    ma200 = float(closes.tail(200).mean()) if len(closes) >= 200 else None
    # RSI(14)
    delta = closes.diff()
    up = delta.clip(lower=0).rolling(14).mean()
    down = (-delta.clip(upper=0)).rolling(14).mean()
    rs = up / down.replace(0, np.nan)
    rsi_series = 100 - 100 / (1 + rs)
    rsi = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None
    # streak of consecutive up/down days
    diffs = list(closes.diff().dropna())
    streak = 0
    prev = None
    for d in reversed(diffs):
        s = 1 if d > 0 else -1 if d < 0 else 0
        if s == 0:
            break
        if prev is None:
            prev = s; streak = s
        elif s == prev:
            streak += s
        else:
            break
    hi = float(closes.tail(252).max())
    lo = float(closes.tail(252).min())
    from_high = (last - hi) / hi * 100 if hi else None
    from_low = (last - lo) / lo * 100 if lo else None
    return {"last": last, "ma20": ma20, "ma50": ma50, "ma200": ma200,
            "rsi": rsi, "streak": streak, "from_high": from_high, "from_low": from_low}


def _score(sig: dict) -> dict:
    last, ma50, ma200 = sig["last"], sig["ma50"], sig["ma200"]
    rsi = sig["rsi"]; streak = sig["streak"]
    fh = sig["from_high"]; fl = sig["from_low"]
    up = 0.0; ur = []
    dn = 0.0; dr = []
    if ma200 and last > ma200: up += 20; ur.append("above 200-DMA")
    if last > ma50: up += 12; ur.append("above 50-DMA")
    if ma200 and ma50 > ma200: up += 10; ur.append("golden cross")
    if fh is not None and fh >= -3: up += 15; ur.append("near 52wk high")
    if rsi is not None and 50 <= rsi <= 72: up += 8; ur.append(f"RSI {rsi:.0f}")
    if streak >= 3: up += min(10.0, streak * 2); ur.append(f"{streak}d up streak")
    if ma200 and last < ma200: dn += 20; dr.append("below 200-DMA")
    if last < ma50: dn += 12; dr.append("below 50-DMA")
    if ma200 and ma50 < ma200: dn += 10; dr.append("death cross")
    if fl is not None and fl <= 3: dn += 15; dr.append("near 52wk low")
    if rsi is not None and 28 <= rsi <= 50: dn += 8; dr.append(f"RSI {rsi:.0f}")
    if streak <= -3: dn += min(10.0, abs(streak) * 2); dr.append(f"{abs(streak)}d down streak")
    if dn > up:
        direction, score, reasons = "down", dn, dr
    else:
        direction, score, reasons = "up", up, ur
    score = max(0.0, min(100.0, score))
    return {"direction": direction, "score": round(score, 1),
            "importance": "high" if score >= 55 else "medium" if score >= 32 else "low",
            "reasons": reasons}


def _scan_worker(symbols: list[str]) -> None:
    analyst_board.HEAVY_SCAN_LOCK.acquire()
    try:
        if not _OK:
            raise RuntimeError("yfinance/pandas unavailable")
        rows = []
        for i in range(0, len(symbols), CHUNK):
            part = symbols[i:i + CHUNK]
            df = None
            try:
                df = yf.download(" ".join(part), period="1y", interval="1d",
                                 progress=False, group_by="ticker", threads=False)
                multi = isinstance(df.columns, pd.MultiIndex)
                for sym in part:
                    try:
                        closes = (df[sym]["Close"] if multi else df["Close"]).dropna()
                    except Exception:
                        continue
                    sig = _signals(closes)
                    if not sig:
                        continue
                    sc = _score(sig)
                    rows.append({
                        "ticker": sym,
                        "last": round(sig["last"], 2),
                        "rsi": round(sig["rsi"], 1) if sig["rsi"] is not None else None,
                        "from_high": round(sig["from_high"], 1) if sig["from_high"] is not None else None,
                        "from_low": round(sig["from_low"], 1) if sig["from_low"] is not None else None,
                        "above_ma200": (sig["ma200"] is not None and sig["last"] > sig["ma200"]),
                        "streak": sig["streak"],
                        "new_high": sig["from_high"] is not None and sig["from_high"] >= -0.5,
                        "new_low": sig["from_low"] is not None and sig["from_low"] <= 0.5,
                        "overbought": sig["rsi"] is not None and sig["rsi"] >= 70,
                        "oversold": sig["rsi"] is not None and sig["rsi"] <= 30,
                        **sc,
                    })
            except Exception:
                pass
            finally:
                del df
                gc.collect()
            with _LOCK:
                _STATE["scanned"] = min(len(symbols), i + CHUNK)
            time.sleep(0.3)
        rows.sort(key=lambda r: -r["score"])
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
        gc.collect()
        analyst_board.HEAVY_SCAN_LOCK.release()


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
    ups = [r for r in rows if r["direction"] == "up"]
    downs = [r for r in rows if r["direction"] == "down"]
    return {
        "strongest_up": ups[:10],
        "strongest_down": sorted(downs, key=lambda r: -r["score"])[:10],
        "new_highs": [r for r in rows if r.get("new_high")][:10],
        "new_lows": [r for r in rows if r.get("new_low")][:10],
        "overbought": [r for r in rows if r.get("overbought")][:10],
        "oversold": [r for r in rows if r.get("oversold")][:10],
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
