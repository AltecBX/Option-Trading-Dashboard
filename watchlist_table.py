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
import json
import math
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
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

# ── Persistence ─────────────────────────────────────────────────────
# The scanned board is cached to the stable data dir (the persistent
# /data volume on Railway) so it survives restarts and redeploys, and
# every device reads the same server-side cache. Atomic write (tmp +
# replace) so a crash mid-write can't corrupt the file.
try:
    from storage import _stable_data_dir
    _CACHE_PATH: Path | None = _stable_data_dir() / "watchlist_table.json"
except Exception as _exc:  # noqa: BLE001
    print(f"[watchlist_table] cache path unavailable: {_exc}", file=sys.stderr)
    _CACHE_PATH = None

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "scanning": False, "scanned": 0, "total": 0, "last_scan": None,
    "rows": [], "error": None, "auto_fired": [], "last_auto": None,
}
_THREAD: threading.Thread | None = None
_SCHED_THREAD: threading.Thread | None = None

# Auto-refresh slots, in ET (market timezone): 9 AM pre-open and 6 PM
# post-close, every weekday. Each slot has a catch-up window so a server
# that boots a little late still runs the missed refresh.
_AUTO_SLOTS = (9, 18)
_CATCHUP_HOURS = 3


def _persist() -> None:
    if not _CACHE_PATH:
        return
    with _LOCK:
        payload = {
            "rows": _STATE["rows"],
            "last_scan": _STATE["last_scan"],
            "auto_fired": _STATE.get("auto_fired", [])[-20:],
            "last_auto": _STATE.get("last_auto"),
        }
    try:
        tmp = _CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(_CACHE_PATH)
    except Exception as exc:  # noqa: BLE001
        print(f"[watchlist_table] persist failed: {exc}", file=sys.stderr)


def _load_persisted() -> None:
    if not _CACHE_PATH or not _CACHE_PATH.exists():
        return
    try:
        data = json.loads(_CACHE_PATH.read_text())
        if not isinstance(data, dict):
            return
        with _LOCK:
            _STATE["rows"] = data.get("rows") or []
            _STATE["last_scan"] = data.get("last_scan")
            _STATE["auto_fired"] = data.get("auto_fired") or []
            _STATE["last_auto"] = data.get("last_auto")
        print(f"[watchlist_table] loaded {len(_STATE['rows'])} cached rows "
              f"(last scan {_STATE['last_scan']})", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[watchlist_table] cache load failed: {exc}", file=sys.stderr)


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
    chg = (round((closes[-1] - closes[-2]) / closes[-2] * 100.0, 2)
           if len(closes) >= 2 and closes[-2] else None)
    return {
        "last": round(last, 2),
        "change": chg,
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


def _num(v):
    """Coerce to a finite float or None (handles NaN, strings, etc.)."""
    try:
        f = float(v)
        return f if f == f and f not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _fundamentals(symbol: str) -> dict:
    out = {"company": None, "market_cap": None, "pe": None, "forward_pe": None,
           "sector": None, "industry": None, "next_earnings": None, "days_to_earnings": None}
    try:
        t = yf.Ticker(symbol)
    except Exception:
        return out

    # .info is the rich source (name / sector / industry / P/E) but Yahoo
    # throttles it hard during a big scan, returning an empty dict on a
    # 429. Retry a few times with backoff so a transient throttle doesn't
    # leave the whole row blank — the user needs every field populated.
    info = {}
    for attempt in range(4):
        try:
            info = t.info or {}
        except Exception:
            info = {}
        if info.get("shortName") or info.get("longName") or info.get("marketCap"):
            break
        time.sleep(0.5 * (attempt + 1))

    try:
        out["company"] = info.get("shortName") or info.get("longName")
        out["market_cap"] = info.get("marketCap")
        pe = _num(info.get("trailingPE")); fpe = _num(info.get("forwardPE"))
        out["pe"] = round(pe, 1) if pe is not None else None
        out["forward_pe"] = round(fpe, 1) if fpe is not None else None
        out["sector"] = info.get("sector")
        out["industry"] = info.get("industry")

        # fast_info is a lighter endpoint that survives throttling better —
        # backfill market cap (and infer P/E from price/EPS) when .info
        # came back thin.
        if out["market_cap"] is None:
            try:
                mc = getattr(t.fast_info, "market_cap", None)
                if mc:
                    out["market_cap"] = mc
            except Exception:
                pass
        if out["pe"] is None:
            eps = _num(info.get("trailingEps") or info.get("epsTrailingTwelveMonths"))
            price = _num(info.get("currentPrice") or info.get("regularMarketPrice"))
            if eps and price and eps > 0:
                out["pe"] = round(price / eps, 1)

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
                    # Gentle throttle between per-symbol .info calls so
                    # Yahoo doesn't start 429-ing and blanking out rows.
                    time.sleep(0.15)
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
        _persist()  # cache to /data so the board survives restarts/redeploys
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
            "error": _STATE["error"], "last_auto": _STATE.get("last_auto"),
            "auto_slots_et": list(_AUTO_SLOTS),
        }
    sectors = sorted({r["sector"] for r in rows if r.get("sector")})
    industries = sorted({r["industry"] for r in rows if r.get("industry")})
    return {"as_of": _now_iso(), "status": status, "count": len(rows),
            "rows": rows, "sectors": sectors, "industries": industries}


# ── Auto-refresh scheduler ──────────────────────────────────────────
def _now_et() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        # Fallback if the tz database is unavailable: approximate ET as
        # UTC-4. DST-unaware, but only shifts the trigger by an hour.
        return datetime.now(timezone.utc) - timedelta(hours=4)


def _maybe_auto_scan(get_symbols) -> None:
    now = _now_et()
    if now.weekday() >= 5:  # Saturday/Sunday — markets closed
        return
    for hour in _AUTO_SLOTS:
        slot = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if now < slot:
            continue
        if (now - slot) > timedelta(hours=_CATCHUP_HOURS):
            continue  # window passed; wait for the next slot
        key = f"{now.date().isoformat()}:{hour:02d}"
        with _LOCK:
            if key in _STATE.get("auto_fired", []):
                continue
            if _STATE["scanning"]:
                return
        syms = []
        try:
            syms = get_symbols() or []
        except Exception:
            syms = []
        if not syms:
            return
        # Record the slot BEFORE firing and persist immediately, so a
        # restart inside the window can't re-trigger the heavy scan.
        with _LOCK:
            fired = _STATE.setdefault("auto_fired", [])
            fired.append(key)
            _STATE["auto_fired"] = fired[-20:]
            _STATE["last_auto"] = {"slot": key, "at": _now_iso()}
        _persist()
        res = trigger_scan(syms)
        print(f"[watchlist_table] auto-scan {key} ET "
              f"({res.get('total')} names): {res}", file=sys.stderr)
        return


def start_scheduler(get_symbols) -> None:
    """Run a watchlist-table refresh at 9 AM and 6 PM ET each weekday.
    Idempotent; checks once a minute so a restart within a slot's catch-up
    window still runs the missed refresh. Slot stamps are persisted to
    /data so a restart can't re-trigger the same heavy scan."""
    global _SCHED_THREAD
    if _SCHED_THREAD is not None and _SCHED_THREAD.is_alive():
        return

    def loop():
        while True:
            try:
                _maybe_auto_scan(get_symbols)
            except Exception as exc:  # noqa: BLE001
                print(f"[watchlist_table] scheduler error: {exc}", file=sys.stderr)
            time.sleep(60)

    _SCHED_THREAD = threading.Thread(target=loop, daemon=True)
    _SCHED_THREAD.start()


# Load any cached board at import so the table is populated immediately
# after a restart/redeploy, before the first scan or scheduler tick.
_load_persisted()
