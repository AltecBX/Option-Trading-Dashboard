"""range_scan.py — Weekly range-location scanner for premium selling (v1).

Runs the Weekly Option Selling Setup panel's range math across the whole
watchlist at once: where does THIS week's return sit between each name's
worst weekly low and best weekly high over the selected lookback? Names
hugging the bottom of their range are put-selling candidates; names at the
top are call-selling candidates — the same edge the per-ticker panel shows,
without clicking through one symbol at a time.

Per name (from batched daily OHLC, grouped into Mon–Fri weeks):
  • worst_low / best_high — extreme weekly returns vs each week's own
    baseline (Monday open, or prior week close in friday mode) — identical
    formulas to load_weekly_data in options_dashboard.py
  • curr_return — this week so far vs this week's baseline
  • pos (0–100) — location inside [worst_low, best_high]
  • bottom_prox / top_prox — 100−pos / pos (location, NOT P(OTM))
  • lows_in_by / highs_in_by — % of weeks whose extreme had already
    printed by today's weekday (the "Wed/Thu near the low" edge)

Scores are LOCATION measures from price history only. No option data is
touched here — premiums/greeks stay on the per-ticker panel.
"""
from __future__ import annotations

import gc
import threading
import time
from datetime import date, datetime, timezone
from typing import Any

try:
    import yfinance as yf
    import pandas as pd
    _OK = True
except Exception:
    _OK = False

import analyst_board

CHUNK = 60
MIN_WEEKS = 8          # skip names with fewer complete weeks than this

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "scanning": False, "scanned": 0, "total": 0, "last_scan": None,
    "rows": [], "universe_size": 0, "error": None,
    "weeks": 16, "baseline": "monday", "dow": None,
}
_THREAD: threading.Thread | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _et_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now()


def _et_dow() -> int:
    """Weekday in ET, Mon..Fri → 0..4 (weekend counts as a complete week)."""
    return min(_et_now().weekday(), 4)


def _et_today() -> date:
    """Trading-calendar 'today' in ET. The server runs in UTC, where
    date.today() rolls over at 8 PM ET — which classified the in-progress
    week as complete (polluting the extremes) and zeroed this-week returns
    for every evening scan."""
    return _et_now().date()


def _symbol_row(sub: "pd.DataFrame", sym: str, weeks: int,
                friday_baseline: bool, dow: int) -> dict | None:
    """Range-location metrics for one symbol from its daily OHLC frame.

    Mirrors load_weekly_data's weekly grouping: first/last trading day of
    each Mon–Fri week are the anchors; a week is complete only when its
    last traded day is strictly before today.
    """
    sub = sub.dropna(subset=["Open", "High", "Low", "Close"])
    if len(sub) < MIN_WEEKS * 4:
        return None
    idx = pd.to_datetime(sub.index)
    try:
        idx = idx.tz_localize(None)
    except TypeError:
        pass
    sub = sub.copy()
    sub.index = idx
    sub["week_start"] = sub.index - pd.to_timedelta(sub.index.weekday, unit="D")
    today = _et_today()

    complete, current_grp, prev_last_close = [], None, None
    for ws, grp in sub.groupby("week_start"):
        if grp.index[-1].date() < today:
            complete.append((ws, grp, prev_last_close))
        else:
            current_grp = (grp, prev_last_close)
        prev_last_close = float(grp["Close"].iloc[-1])

    complete = complete[-weeks:]
    if len(complete) < MIN_WEEKS:
        return None

    lows, highs, low_days, high_days = [], [], [], []
    for ws, grp, prior in complete:
        week_open = float(grp["Open"].iloc[0])
        if friday_baseline:
            if prior is None or prior <= 0:
                continue
            baseline = prior
        else:
            baseline = week_open
        if baseline <= 0:
            continue
        lows.append((float(grp["Low"].min()) / baseline - 1) * 100)
        highs.append((float(grp["High"].max()) / baseline - 1) * 100)
        low_days.append(int(grp["Low"].idxmin().dayofweek))
        high_days.append(int(grp["High"].idxmax().dayofweek))
    if len(lows) < MIN_WEEKS:
        return None

    worst_low, best_high = min(lows), max(highs)
    span = max(0.01, best_high - worst_low)

    # This week's baseline + live position. Pre-open Monday there may be no
    # current-week rows yet — fall back to the last close (curr_return ≈ 0).
    last_close = float(sub["Close"].iloc[-1])
    if current_grp is not None:
        grp, prior = current_grp
        if friday_baseline and prior and prior > 0:
            baseline_now = prior
        else:
            baseline_now = float(grp["Open"].iloc[0])
    else:
        baseline_now = prev_last_close if friday_baseline and prev_last_close else last_close
    if not baseline_now or baseline_now <= 0:
        return None
    curr_return = (last_close / baseline_now - 1) * 100

    raw_pos = (curr_return - worst_low) / span * 100
    pos = max(0.0, min(100.0, raw_pos))
    bottom_prox = 100 - pos
    top_prox = pos
    edge = max(bottom_prox, top_prox)
    side = "put" if bottom_prox >= top_prox else "call"

    n = len(low_days)
    lows_in_by = sum(1 for d in low_days if d <= dow) / n * 100
    highs_in_by = sum(1 for d in high_days if d <= dow) / n * 100

    return {
        "ticker": sym,
        "last": round(last_close, 2),
        "baseline": round(baseline_now, 2),
        "curr_return": round(curr_return, 2),
        "worst_low": round(worst_low, 1),
        "best_high": round(best_high, 1),
        "p_low": round(baseline_now * (1 + worst_low / 100), 2),
        "p_high": round(baseline_now * (1 + best_high / 100), 2),
        "pos": round(pos, 1),
        "outside": "below" if raw_pos < 0 else "above" if raw_pos > 100 else None,
        "bottom_prox": round(bottom_prox, 1),
        "top_prox": round(top_prox, 1),
        "edge": round(edge, 1),
        "side": side,
        "lows_in_by": round(lows_in_by, 0),
        "highs_in_by": round(highs_in_by, 0),
        "weeks_used": len(lows),
    }


def _scan_worker(symbols: list[str], weeks: int, friday_baseline: bool) -> None:
    analyst_board.HEAVY_SCAN_LOCK.acquire()
    try:
        if not _OK:
            raise RuntimeError("yfinance/pandas unavailable")
        dow = _et_dow()
        # Enough calendar to cover the lookback plus holidays/new listings.
        period = "6mo" if weeks <= 16 else "1y" if weeks <= 44 else "2y"
        rows = []
        for i in range(0, len(symbols), CHUNK):
            part = symbols[i:i + CHUNK]
            df = None
            try:
                df = yf.download(" ".join(part), period=period, interval="1d",
                                 auto_adjust=False, progress=False,
                                 group_by="ticker", threads=False)
                multi = isinstance(df.columns, pd.MultiIndex)
                for sym in part:
                    try:
                        sub = df[sym] if multi else df
                        r = _symbol_row(sub, sym, weeks, friday_baseline, dow)
                        if r:
                            rows.append(r)
                    except Exception:
                        continue
            except Exception:
                pass
            finally:
                del df
                gc.collect()
            with _LOCK:
                _STATE["scanned"] = min(len(symbols), i + CHUNK)
            time.sleep(0.3)
        rows.sort(key=lambda r: -r["edge"])
        with _LOCK:
            _STATE["rows"] = rows
            _STATE["last_scan"] = _now_iso()
            _STATE["error"] = None
            _STATE["dow"] = _et_dow()
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _STATE["error"] = str(exc)
    finally:
        with _LOCK:
            _STATE["scanning"] = False
        gc.collect()
        analyst_board.HEAVY_SCAN_LOCK.release()


def trigger_scan(watchlist_syms: list[str] | None = None, weeks: int = 16,
                 friday_baseline: bool = False, force: bool = False) -> dict:
    global _THREAD
    weeks = max(4, min(104, int(weeks or 16)))
    with _LOCK:
        if _STATE["scanning"] and not force:
            return {"started": False, "reason": "already scanning"}
        syms = list(dict.fromkeys(watchlist_syms or []))
        if not syms:
            return {"started": False, "reason": "watchlist empty"}
        _STATE.update({"scanning": True, "scanned": 0, "total": len(syms),
                       "universe_size": len(syms), "weeks": weeks,
                       "baseline": "friday" if friday_baseline else "monday"})
    _THREAD = threading.Thread(target=_scan_worker,
                               args=(syms, weeks, friday_baseline), daemon=True)
    _THREAD.start()
    return {"started": True, "total": len(syms), "weeks": weeks}


def _summary(rows: list[dict]) -> dict:
    near_lows = [r for r in rows if r["side"] == "put" and r["bottom_prox"] >= 66]
    near_highs = [r for r in rows if r["side"] == "call" and r["top_prox"] >= 66]
    # His edge: late in the week AND near the range low — most weeks' lows
    # are already in by Wed/Thu, so little room usually remains below.
    late_low = [r for r in near_lows if r["lows_in_by"] >= 60]
    return {
        "near_lows": near_lows[:12],
        "near_highs": near_highs[:12],
        "late_week_lows": late_low[:12],
    }


def get_board() -> dict:
    with _LOCK:
        rows = list(_STATE["rows"])
        status = {
            "scanning": _STATE["scanning"], "scanned": _STATE["scanned"],
            "total": _STATE["total"], "last_scan": _STATE["last_scan"],
            "universe_size": _STATE["universe_size"], "error": _STATE["error"],
            "weeks": _STATE["weeks"], "baseline": _STATE["baseline"],
            "dow": _STATE["dow"],
        }
    return {"as_of": _now_iso(), "status": status, "count": len(rows),
            "rows": rows, "summary": _summary(rows)}
