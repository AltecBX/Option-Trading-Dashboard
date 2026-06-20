"""watchlist_table.py — full-metric table for the user's tracked watchlist.

One row per watchlist symbol with: price, market cap, P/E, forward P/E,
sector, industry, RSI(14), relative volume, next earnings date, period
returns (WTD/MTD/QTD/YTD), and distance from the 20/50/200-day moving
averages. Background scan (chunked, cached, shared heavy-scan lock + gc)
mirroring movers/trend/ivrank so it stays light on the free tier.

OHLC comes from one batched yf.download per chunk (cheap); fundamentals +
earnings come from a per-symbol yfinance .info / earnings call (the slow
part), so a big watchlist takes a few minutes to fully populate — same as
the other screeners.
"""
from __future__ import annotations

import gc
import math
import threading
import time
from datetime import date, datetime, timezone
from typing import Any

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    _OK = True
except Exception:
    _OK = False

import analyst_board

CHUNK = 40

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "scanning": False, "scanned": 0, "total": 0, "last_scan": None,
    "rows": [], "error": None,
}
_THREAD: threading.Thread | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rsi(closes, n: int = 14):
    if len(closes) < n + 1:
        return None
    d = np.diff(closes)
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    ru = float(up[:n].mean()); rd = float(dn[:n].mean())
    for i in range(n, len(d)):
        ru = (ru * (n - 1) + up[i]) / n
        rd = (rd * (n - 1) + dn[i]) / n
    if rd == 0:
        return 100.0
    return float(100.0 - 100.0 / (1.0 + ru / rd))


def _period_ret(close: "pd.Series", start: date):
    try:
        prior = close[close.index.date < start]
        if len(prior) == 0:
            return None
        base = float(prior.iloc[-1]); last = float(close.iloc[-1])
        return round((last - base) / base * 100.0, 2) if base > 0 else None
    except Exception:
        return None


def _price_metrics(close: "pd.Series", vol: "pd.Series") -> dict | None:
    closes = [float(x) for x in close.dropna().tolist()]
    if len(closes) < 20:
        return None
    last = closes[-1]
    sma = lambda n: float(np.mean(closes[-n:])) if len(closes) >= n else None
    ma20, ma50, ma200 = sma(20), sma(50), sma(200)
    vols = [float(x) for x in vol.dropna().tolist()] if vol is not None else []
    avgvol = float(np.mean(vols[-20:])) if len(vols) >= 5 else None
    today = date.today()
    wk = today.toordinal() - today.weekday()       # Monday of this week
    wk_start = date.fromordinal(wk)
    mo_start = today.replace(day=1)
    q = (today.month - 1) // 3 * 3 + 1
    qt_start = today.replace(month=q, day=1)
    yr_start = today.replace(month=1, day=1)
    rsi = _rsi(closes)
    return {
        "last": round(last, 2),
        "rsi": round(rsi, 1) if rsi is not None else None,
        "rel_vol": round(vols[-1] / avgvol, 2) if (avgvol and vols and vols[-1]) else None,
        "from_ma20": round((last - ma20) / ma20 * 100.0, 1) if ma20 else None,
        "from_ma50": round((last - ma50) / ma50 * 100.0, 1) if ma50 else None,
        "from_ma200": round((last - ma200) / ma200 * 100.0, 1) if ma200 else None,
        "wtd": _period_ret(close, wk_start),
        "mtd": _period_ret(close, mo_start),
        "qtd": _period_ret(close, qt_start),
        "ytd": _period_ret(close, yr_start),
    }


def _fundamentals(symbol: str) -> dict:
    out = {"company": None, "market_cap": None, "pe": None, "forward_pe": None,
           "sector": None, "industry": None, "next_earnings": None, "days_to_earnings": None}
    try:
        t = yf.Ticker(symbol)
        info = t.info or {}
        out["company"] = info.get("shortName") or info.get("longName")
        out["market_cap"] = info.get("marketCap")
        pe = info.get("trailingPE"); fpe = info.get("forwardPE")
        out["pe"] = round(float(pe), 1) if isinstance(pe, (int, float)) and pe == pe else None
        out["forward_pe"] = round(float(fpe), 1) if isinstance(fpe, (int, float)) and fpe == fpe else None
        out["sector"] = info.get("sector")
        out["industry"] = info.get("industry")
        # Next earnings date — best-effort.
        try:
            ed = t.get_earnings_dates(limit=12)
            if ed is not None and len(ed):
                today = date.today()
                upcoming = [d.date() for d in ed.index if d.date() >= today]
                if upcoming:
                    nd = min(upcoming)
                    out["next_earnings"] = nd.strftime("%Y-%m-%d")
                    out["days_to_earnings"] = (nd - today).days
        except Exception:
            pass
    except Exception:
        pass
    return out


def _scan_worker(symbols: list[str]) -> None:
    analyst_board.HEAVY_SCAN_LOCK.acquire()
    try:
        if not _OK:
            raise RuntimeError("yfinance/pandas unavailable")
        rows = []
        done = 0
        for i in range(0, len(symbols), CHUNK):
            part = symbols[i:i + CHUNK]
            df = None
            try:
                df = yf.download(" ".join(part), period="1y", interval="1d",
                                 progress=False, group_by="ticker", threads=False)
                multi = isinstance(df.columns, pd.MultiIndex)
                for sym in part:
                    try:
                        sub = df[sym] if multi else df
                        close = sub["Close"].dropna()
                        vol = sub["Volume"] if "Volume" in sub else None
                    except Exception:
                        done += 1
                        continue
                    pm = _price_metrics(close, vol)
                    if not pm:
                        done += 1
                        continue
                    fund = _fundamentals(sym)
                    row = {"symbol": sym}
                    row.update(pm)
                    row.update(fund)
                    rows.append(row)
                    done += 1
                    with _LOCK:
                        _STATE["scanned"] = done
                    time.sleep(0.05)
            except Exception:
                done = min(len(symbols), i + CHUNK)
            finally:
                del df
                gc.collect()
            with _LOCK:
                _STATE["scanned"] = min(len(symbols), max(done, i + len(part)))
        rows.sort(key=lambda r: -(r.get("market_cap") or 0))
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


def trigger_scan(symbols: list[str], force: bool = False) -> dict:
    global _THREAD
    syms = list(dict.fromkeys([s.upper().strip() for s in (symbols or []) if s]))
    with _LOCK:
        if _STATE["scanning"] and not force:
            return {"started": False, "reason": "already scanning"}
        _STATE.update({"scanning": True, "scanned": 0, "total": len(syms)})
    _THREAD = threading.Thread(target=_scan_worker, args=(syms,), daemon=True)
    _THREAD.start()
    return {"started": True, "total": len(syms)}


def get_board() -> dict:
    with _LOCK:
        rows = list(_STATE["rows"])
        status = {
            "scanning": _STATE["scanning"], "scanned": _STATE["scanned"],
            "total": _STATE["total"], "last_scan": _STATE["last_scan"],
            "error": _STATE["error"],
        }
    sectors = sorted({r["sector"] for r in rows if r.get("sector")})
    industries = sorted({r["industry"] for r in rows if r.get("industry")})
    return {"as_of": _now_iso(), "status": status, "count": len(rows),
            "rows": rows, "sectors": sectors, "industries": industries}
