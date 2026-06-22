#!/usr/bin/env python3
"""
options_dashboard.py
--------------------
Single-file launcher: pulls live data from Yahoo Finance, computes the same
weekly stats as the Streamlit script, and bakes everything into a
self-contained dashboard.html that opens in your browser.

USAGE
-----
    python options_dashboard.py                    # AAPL, 12 weeks, Monday baseline
    python options_dashboard.py TSLA --weeks 26
    python options_dashboard.py NVDA --baseline friday --buffer 1.5

REQUIREMENTS
-----------
    pip install yfinance pandas numpy
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import socket
import functools
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = None
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    import numpy as np
    import pandas as pd
    import yfinance as yf
except ImportError:
    sys.exit("Missing dependency.  Run:  pip install yfinance pandas numpy")


# NaN-safe JSON. Python's json emits a bare `NaN`/`Infinity` token, which
# is invalid JSON and makes the browser's JSON.parse throw
# ("Unexpected token 'N'"). Any non-finite float (e.g. a week with no
# data → y_close = NaN) is mapped to null before serialization. All API
# responses go through _dumps so this can never leak again.
_RAW_DUMPS = json.dumps


def _json_safe(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


def _dumps(o, **kw):
    return _RAW_DUMPS(_json_safe(o), **kw)

# Schwab integration — primary data source when configured + authed.
# Module is stdlib-only so import never fails. Falls back to yfinance
# when Schwab returns None (auth failure, rate limit, network).
try:
    from schwab_client import get_client as _get_schwab_client
    _SCHWAB_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    print(f"[schwab] module load failed: {_exc}", file=sys.stderr)
    _SCHWAB_AVAILABLE = False
    _get_schwab_client = None  # type: ignore

# Level Reprice (v1.28) — Jerry's verified, dependency-free pricer.
# Imported as-is; the math is not reimplemented here. Used by the
# /api/reprice and /api/fade endpoints. Stdlib-only so import is safe.
try:
    import option_reprice as _reprice
    _REPRICE_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    print(f"[reprice] module load failed: {_exc}", file=sys.stderr)
    _REPRICE_AVAILABLE = False
    _reprice = None  # type: ignore

# Unusual Whales — secondary signal source for unusual options flow,
# Greek exposure, market tide, and per-strike volume/premium. Stdlib-
# only wrapper so import never breaks startup; returns None when no
# UW_API_KEY is set or when calls fail.
try:
    import unusual_whales_client as _uw_client
    _UW_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    print(f"[uw] module load failed: {_exc}", file=sys.stderr)
    _UW_AVAILABLE = False
    _uw_client = None  # type: ignore

# Analyst price targets and rating changes — combines Finnhub aggregates
# with yfinance per-firm history. Stdlib + yfinance, no other deps.
# Falls back gracefully when Finnhub unconfigured.
try:
    import analyst_client as _analyst_client
    _ANALYST_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    print(f"[analyst] module load failed: {_exc}", file=sys.stderr)
    _ANALYST_AVAILABLE = False
    _analyst_client = None  # type: ignore

# Morning analyst board — scans a universe for fresh analyst actions,
# enriches movers with premarket data, and ranks by importance.
try:
    import analyst_board as _analyst_board
    _ANALYST_BOARD_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    print(f"[analyst_board] module load failed: {_exc}", file=sys.stderr)
    _ANALYST_BOARD_AVAILABLE = False
    _analyst_board = None  # type: ignore

# Pre-market movers scanner — batch-quotes the universe for the biggest
# gappers, enriches top movers, tags catalysts.
try:
    import movers as _movers
    _MOVERS_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    print(f"[movers] module load failed: {_exc}", file=sys.stderr)
    _MOVERS_AVAILABLE = False
    _movers = None  # type: ignore

# Trend / momentum screener — MA stack, 52wk high/low, RSI, streaks.
try:
    import trend as _trend
    _TREND_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    print(f"[trend] module load failed: {_exc}", file=sys.stderr)
    _TREND_AVAILABLE = False
    _trend = None  # type: ignore

# Volatility-rank screener — realized-vol rank for premium selling.
try:
    import ivrank as _ivrank
    _IVRANK_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    print(f"[ivrank] module load failed: {_exc}", file=sys.stderr)
    _IVRANK_AVAILABLE = False
    _ivrank = None  # type: ignore

# Swing pattern recognition — low→high swings, rhythm, projected targets.
try:
    import swings as _swings
    _SWINGS_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    print(f"[swings] module load failed: {_exc}", file=sys.stderr)
    _SWINGS_AVAILABLE = False
    _swings = None  # type: ignore

try:
    import news as _news
    _NEWS_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    print(f"[news] module load failed: {_exc}", file=sys.stderr)
    _NEWS_AVAILABLE = False
    _news = None  # type: ignore

try:
    import watchlist_table as _wltable
    _WLTABLE_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    print(f"[watchlist_table] module load failed: {_exc}", file=sys.stderr)
    _WLTABLE_AVAILABLE = False
    _wltable = None  # type: ignore

# Track which source served the most recent ticker request, exposed via
# /api/data_source so the frontend can show a status badge.
_LAST_SOURCE: dict = {"source": "yfinance", "schwab_status": None}


# ── Diagnostic logging ─────────────────────────────────────────────────
# Backend exceptions used to be swallowed silently in 29+ places, making
# "why is this ticker showing —" impossible to debug. This helper writes
# structured warnings to stderr (which jerry/launchctl captures into
# ~/.jerry-dashboard/server.log) so post-mortems are actually possible.
#
# Format: [warn] HH:MM:SS  TICKER  where  ExceptionType: message
# Example: [warn] 14:32:09  SNDK    flow_score  TimeoutError: ...
#
# Use this whenever you'd otherwise write `except Exception: pass`.
_LOG_LOCK = __import__("threading").Lock()

def _log_warn(symbol: str | None, where: str, exc: BaseException) -> None:
    """Log a swallowed exception with context. Never raises — even if
    logging itself fails, we silently move on so the original handler
    behavior (don't crash the request) is preserved."""
    try:
        ts = datetime.now().strftime("%H:%M:%S")
        # Coerce symbol to string defensively — callers sometimes pass
        # ints, None, or other types. `(symbol or "—")[:10]` works for
        # strings/None but explodes on ints.
        sym = (str(symbol) if symbol else "—")[:10]
        msg = f"{type(exc).__name__}: {exc}"[:200]
        line = f"[warn] {ts}  {sym:<8}  {where[:30]:<30}  {msg}\n"
        with _LOG_LOCK:
            sys.stderr.write(line)
            sys.stderr.flush()
    except Exception:
        pass


def _schwab() -> object | None:
    """Returns the singleton Schwab client if it's configured + has a
    valid token file. Returns None if Schwab isn't usable, which signals
    callers to fall through to yfinance."""
    if not _SCHWAB_AVAILABLE or _get_schwab_client is None:
        return None
    try:
        c = _get_schwab_client()
        return c if c.is_configured() else None
    except Exception:
        return None

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "index.html"
OUT = HERE / "options_dashboard.html"


# ═══════════════════════════════════════════════════════════════════════════
#  Mirrors of the Streamlit logic (same formulas, same baseline toggle)
# ═══════════════════════════════════════════════════════════════════════════
def round_strike(px: float) -> float:
    if px <= 0:
        return 0.0
    inc = 0.5 if px < 25 else (1.0 if px < 200 else 5.0)
    return round(px / inc) * inc


def load_weekly_data(symbol: str, num_weeks: int, friday_baseline: bool) -> pd.DataFrame:
    stock = yf.Ticker(symbol)
    end = datetime.today()
    start = end - timedelta(days=num_weeks * 10 + 14)
    df = stock.history(start=start, end=end, auto_adjust=False)
    if df.empty:
        return pd.DataFrame()
    df = df[["Open", "High", "Low", "Close"]].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df["day"] = df.index.dayofweek
    df["week_start"] = df.index - pd.to_timedelta(df.index.dayofweek, unit="D")
    # Prior close for day-over-day excursion math. For each row this is the
    # close of the prior trading day. Monday's prior close is the previous
    # Friday's close, which crosses the weekend gap and that's intentional —
    # an options seller cares about gap+intraday move on Monday.
    df["prev_close"] = df["Close"].shift(1)

    workdays = df[(df["day"] >= 0) & (df["day"] <= 4)]
    rows = []
    today_date = datetime.today().date()
    names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
    for mon, grp in workdays.groupby("week_start"):
        if grp.empty:
            continue
        # Skip in progress weeks. A week counts as complete only when its
        # last traded day is strictly before today. This handles holiday
        # shortened weeks (Good Friday, Thanksgiving Friday, etc.) too,
        # since the gate only requires the most recent traded day to be
        # in the past, not specifically Friday.
        if grp.index[-1].date() >= today_date:
            continue
        # Use the first and last available trading day of the week as the
        # weekly anchors. In a normal week these are Mon and Fri. On a
        # Good Friday week they become Mon and Thu. On an MLK Monday week
        # they become Tue and Fri. The Field names below preserve client
        # compatibility — `monday_open` and `friday_close` now mean
        # "first trading day open" and "last trading day close".
        first_row = grp.iloc[0]
        last_row = grp.iloc[-1]
        week_open = float(first_row["Open"])
        week_close = float(last_row["Close"])
        week_high = float(grp["High"].max())
        week_low = float(grp["Low"].min())
        if friday_baseline:
            # Friday baseline mode wants the close of the prior week's
            # last trading day, not specifically a Friday. The original
            # ffill on Friday closes alone misses Good Friday weeks where
            # the prior week's last day was Thursday. Use the first row's
            # `prev_close` instead, which is general purpose.
            prior = first_row.get("prev_close")
            if prior is None or pd.isna(prior) or float(prior) <= 0:
                continue
            baseline = float(prior)
        else:
            baseline = week_open
        high_ret = (week_high / baseline - 1) * 100
        low_ret = (week_low / baseline - 1) * 100
        close_ret = (week_close / baseline - 1) * 100
        # Open return = Monday's open vs the baseline (prior Friday close
        # in friday_baseline mode, or the open itself in monday_open mode
        # which produces 0%). This is the gap that opens the week.
        open_ret = (week_open / baseline - 1) * 100
        high_day = int(grp["High"].idxmax().dayofweek)
        low_day = int(grp["Low"].idxmin().dayofweek)
        # Per day high/low excursion vs the day's prior close. This captures
        # the typical day-over-day intraday move on each weekday — including
        # the weekend gap on Mondays. NOT vs the weekly baseline. Days with
        # no trading data or a missing prior close (first available row) are
        # omitted so the client can skip them when averaging.
        day_breakdown = {}
        for dow_idx in range(5):
            day_rows = grp[grp["day"] == dow_idx]
            if day_rows.empty:
                continue
            prior = day_rows["prev_close"].iloc[0]
            if pd.isna(prior) or float(prior) <= 0:
                continue
            prior_close = float(prior)
            day_high = float(day_rows["High"].max())
            day_low = float(day_rows["Low"].min())
            day_breakdown[names[dow_idx]] = {
                "high": (day_high / prior_close - 1) * 100,
                "low": (day_low / prior_close - 1) * 100,
            }
        rows.append({
            "week_start": mon.strftime("%Y-%m-%d"),
            "baseline": baseline,
            "monday_open": week_open,
            "friday_close": week_close,
            "week_high": week_high,
            "week_low": week_low,
            "high_return": high_ret,
            "low_return": low_ret,
            "close_return": close_ret,
            "open_return": open_ret,
            "high_day": high_day,
            "low_day": low_day,
            "high_day_name": names.get(high_day, "Fri"),
            "low_day_name": names.get(low_day, "Fri"),
            "day_breakdown": day_breakdown,
        })
    out = pd.DataFrame(rows).sort_values("week_start", ascending=False).reset_index(drop=True)
    return out.head(num_weeks)


def load_current_week(symbol: str, friday_baseline: bool) -> dict | None:
    # Try Schwab first. We need monday_open, current_price, and prior
    # week's close; Schwab gives us live current price + history bars.
    sc = _schwab()
    if sc is not None:
        try:
            quote = sc.get_quote(symbol)
            bars = sc.get_price_history(symbol, days=30)
            if quote and bars and quote.get("last") is not None:
                today = datetime.today().date()
                monday = today - timedelta(days=today.weekday())
                # Find Monday's open and prior week's last close in bars
                monday_open = None
                prior_close = None
                for b in bars:
                    try:
                        bd = datetime.fromisoformat(b["date"]).date()
                    except Exception:
                        continue
                    if bd == monday and monday_open is None:
                        monday_open = b.get("open")
                    elif bd < monday:
                        prior_close = b.get("close")  # keeps last one before monday
                # Fallbacks if bars don't cover this Monday yet
                if monday_open is None:
                    # Use today's open from the quote
                    monday_open = quote.get("open") or quote.get("close_prev") or quote["last"]
                current_price = float(quote["last"])
                baseline = (prior_close if friday_baseline and prior_close is not None
                            else monday_open)
                _LAST_SOURCE["source"] = "schwab"
                return {
                    "monday_open": float(monday_open),
                    "current_price": current_price,
                    "baseline_price": float(baseline),
                    "week_start": monday.strftime("%Y-%m-%d"),
                }
        except Exception as exc:  # noqa: BLE001
            print(f"[schwab] load_current_week fallback: {exc}", file=sys.stderr)
    # Fallback: yfinance
    _LAST_SOURCE["source"] = "yfinance"
    stock = yf.Ticker(symbol)
    recent = stock.history(period="10d", auto_adjust=False)
    if recent.empty:
        return None
    recent.index = pd.to_datetime(recent.index).tz_localize(None)
    today = datetime.today().date()
    monday = today - timedelta(days=today.weekday())
    mon_open_series = recent[recent.index.date == monday]["Open"]
    if not mon_open_series.empty:
        monday_open = float(mon_open_series.iloc[0])
    else:
        # Monday fallback: find the first weekday >= this Monday's date.
        wk = recent[(recent.index.date >= monday) & (recent.index.dayofweek < 5)]
        monday_open = float(wk["Open"].iloc[0]) if not wk.empty else float(recent["Open"].iloc[-1])
    current_price = float(recent["Close"].iloc[-1])
    # Prior week's last trading day close. In a normal week this is the
    # prior Friday, but on a Good Friday week the prior week's last day
    # is Thursday. Look for the last weekday strictly before this Monday.
    prior_week = recent[(recent.index.date < monday) & (recent.index.dayofweek < 5)]
    prev_fri_close = float(prior_week["Close"].iloc[-1]) if not prior_week.empty else None
    baseline = prev_fri_close if friday_baseline else monday_open
    if baseline is None:
        baseline = monday_open
    return {
        "monday_open": monday_open,
        "current_price": current_price,
        "baseline_price": float(baseline),
        "week_start": monday.strftime("%Y-%m-%d"),
    }


def load_daily(symbol: str, days: int = 90) -> list[dict]:
    """Fetch daily OHLC for `days` of display, with extra leading bars used
    only for indicator warmup (MACD plus 200-day MA). Each row carries
    pre-computed `macd`, `signal`, `hist`, `ma50`, and `ma200` values.
    Indicator values that haven't warmed up yet are omitted from the row."""
    end = datetime.today()
    # 200 trading days requires ~290 calendar days. Add a buffer so MA200
    # is reliable across the entire 90-day display window for any ticker
    # with sufficient listing history.
    warmup_calendar_days = 320
    df = None

    # Try Schwab first
    sc = _schwab()
    if sc is not None:
        try:
            bars = sc.get_price_history(symbol, days=days + warmup_calendar_days)
            if bars and len(bars) > 30:
                df = pd.DataFrame(bars)
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
                df = df.rename(columns={"open": "Open", "high": "High",
                                          "low": "Low", "close": "Close",
                                          "volume": "Volume"})
                df.index = pd.to_datetime(df.index).tz_localize(None)
                _LAST_SOURCE["source"] = "schwab"
        except Exception as exc:  # noqa: BLE001
            print(f"[schwab] load_daily fallback: {exc}", file=sys.stderr)
            df = None

    # Fallback: yfinance
    if df is None or df.empty:
        _LAST_SOURCE["source"] = "yfinance"
        stock = yf.Ticker(symbol)
        start = end - timedelta(days=days + warmup_calendar_days)
        df = stock.history(start=start, end=end, auto_adjust=False)
        if df.empty:
            return []
        df.index = pd.to_datetime(df.index).tz_localize(None)
    closes = df["Close"].astype(float).tolist()

    def ema(vals, period):
        """EMA seeded with SMA of the first `period` non-NaN values. Skips
        any NaN inputs by carrying the previous EMA value forward, so a
        single bad bar doesn't poison the entire downstream series."""
        out = [None] * len(vals)
        if len(vals) < period:
            return out
        seed_end = -1
        for i in range(period - 1, len(vals)):
            window = vals[i - period + 1:i + 1]
            if all(v == v and v is not None for v in window):
                seed_end = i
                break
        if seed_end < 0:
            return out
        out[seed_end] = sum(vals[seed_end - period + 1:seed_end + 1]) / period
        k = 2 / (period + 1)
        for i in range(seed_end + 1, len(vals)):
            v = vals[i]
            if v != v or v is None:
                out[i] = out[i - 1]
            else:
                out[i] = v * k + out[i - 1] * (1 - k)
        return out

    def sma(vals, period):
        """Simple moving average. Handles NaN by skipping that index."""
        out = [None] * len(vals)
        for i in range(period - 1, len(vals)):
            window = vals[i - period + 1:i + 1]
            if any((v is None) or (v != v) for v in window):
                continue
            out[i] = sum(window) / period
        return out

    e12 = ema(closes, 12)
    e26 = ema(closes, 26)
    macd_line = [None if (a is None or b is None) else a - b for a, b in zip(e12, e26)]
    first_valid = next((i for i, v in enumerate(macd_line) if v is not None), -1)
    signal = [None] * len(closes)
    if first_valid >= 0 and first_valid + 9 <= len(closes):
        s = sum(macd_line[first_valid:first_valid + 9])
        signal[first_valid + 8] = s / 9
        k = 2 / 10
        for i in range(first_valid + 9, len(closes)):
            signal[i] = macd_line[i] * k + signal[i - 1] * (1 - k)
    hist = [None if (a is None or b is None) else a - b for a, b in zip(macd_line, signal)]
    ma50 = sma(closes, 50)
    ma200 = sma(closes, 200)
    # EMA21 — matches TradingView's ta.ema(close, 21). Same EMA helper
    # as MACD: SMA seed for first `period` values, then standard EMA.
    ema21 = ema(closes, 21)

    rows = []
    for i, (ts, r) in enumerate(df.iterrows()):
        # Anchor each date to noon ET so JS `new Date(date)` lands on
        # the correct trading-session date in any browser timezone.
        # ISO date alone gets parsed as UTC midnight which renders as
        # the previous calendar day in any timezone west of UTC.
        row = {
            "date": ts.strftime("%Y-%m-%d") + "T12:00:00-04:00",
            "open": float(r.Open), "high": float(r.High),
            "low": float(r.Low), "close": float(r.Close),
        }
        if macd_line[i] is not None:
            row["macd"] = macd_line[i]
        if signal[i] is not None:
            row["signal"] = signal[i]
        if hist[i] is not None:
            row["hist"] = hist[i]
        if ma50[i] is not None:
            row["ma50"] = ma50[i]
        if ma200[i] is not None:
            row["ma200"] = ma200[i]
        if ema21[i] is not None:
            row["ema21"] = ema21[i]
        rows.append(row)

    return rows[-days:] if len(rows) > days else rows


def _normalize_flow_trade(a: dict) -> dict:
    """UW flow alert shapes vary across endpoints and over time. Coerce
    one alert to a stable dict the frontend can render without
    defensively checking every field."""
    def _f(*keys, default=None, cast=None):
        for k in keys:
            v = a.get(k)
            if v is None:
                continue
            if cast is None:
                return v
            try:
                return cast(v)
            except (TypeError, ValueError):
                continue
        return default

    t = (_f("type", "option_type", default="") or "").lower()
    side = "call" if t.startswith("c") else ("put" if t.startswith("p") else "?")
    is_sweep = bool(_f("has_sweep", "is_sweep", default=False) or
                    _f("trade_code", default="") == "SWEEP" or
                    "sweep" in str(_f("flags", default="") or "").lower())
    ask_pct_raw = _f("ask_side_perc", "ask_perc", default=0.5, cast=float) or 0.5
    ask_pct = max(0.0, min(1.0, float(ask_pct_raw)))
    side_label = "ask" if ask_pct >= 0.6 else ("bid" if ask_pct <= 0.4 else "mid")
    sentiment = "bullish" if (side == "call" and side_label == "ask") or (side == "put" and side_label == "bid") \
        else ("bearish" if (side == "put" and side_label == "ask") or (side == "call" and side_label == "bid") else "neutral")
    return {
        "ts": _f("executed_at", "trade_time", "created_at", default=None),
        "side": side,
        "strike": _f("strike", default=None, cast=float),
        "expiry": _f("expiry", "expiration", default=None),
        "price": _f("price", "trade_price", default=None, cast=float),
        "size": _f("size", "volume", default=None, cast=int),
        "premium": _f("total_premium", "premium", default=0.0, cast=float),
        "open_interest": _f("open_interest", default=None, cast=int),
        "volume": _f("volume", default=None, cast=int),
        "iv": _f("implied_volatility", "iv", default=None, cast=float),
        "delta": _f("delta", default=None, cast=float),
        "ask_side_pct": ask_pct,
        "side_label": side_label,
        "is_sweep": is_sweep,
        "sentiment": sentiment,
        "vol_oi_ratio": (
            (_f("volume", default=0, cast=int) or 0) / max(1, (_f("open_interest", default=1, cast=int) or 1))
        ),
    }


def _compute_premium_richness(uw: Any, symbol: str) -> dict:
    """Identify whether options premium for `symbol` is unusually
    attractive to a covered-call seller. Pulls ticker-level options
    volume + state + IV references and produces a Premium Richness
    Score 0-100 plus a short verdict.

    Inputs (best-effort across UW response variations):
      - total_volume, total_premium today
      - put_call_ratio
      - iv_rank / iv_pct (when present)
      - implied_movement (ATM straddle)
    """
    out = {
        "symbol": symbol,
        "score": 0,
        "verdict": "Insufficient data",
        "verdict_class": "verdict-wait",
        "reason": "",
        "stats": {},
        "data_available": False,
    }
    try:
        vol_data = uw.ticker_options_volume(symbol) or {}
        state = uw.stock_state(symbol) or {}
    except Exception:  # noqa: BLE001
        return out

    def _f(d, *keys, default=None, cast=None):
        if not isinstance(d, dict):
            return default
        for k in keys:
            v = d.get(k)
            if v is None:
                continue
            if cast is None:
                return v
            try:
                return cast(v)
            except (TypeError, ValueError):
                continue
        return default

    total_volume = _f(vol_data, "total_volume", "volume", default=None, cast=int)
    total_premium = _f(vol_data, "total_premium", "premium", default=None, cast=float)
    avg_volume = _f(vol_data, "avg_volume", "avg_30d_volume", default=None, cast=float)
    pcr = _f(vol_data, "put_call_ratio", "pc_ratio", default=None, cast=float)
    iv_rank = _f(state, "iv_rank", default=None, cast=float)
    iv_pct = _f(state, "iv_percentile", "iv_pct", default=None, cast=float)
    implied_move = _f(state, "implied_move", "atm_iv", default=None, cast=float)
    last_price = _f(state, "last", "price", "last_price", default=None, cast=float)

    # Some data was returned. Build score from whatever signals we have.
    available = any(v is not None for v in (total_volume, total_premium, iv_rank, iv_pct, pcr))
    if not available:
        return out

    score = 0
    weights_used = 0

    # Volume vs avg — high relative volume = catalyst-driven flow.
    if total_volume and avg_volume and avg_volume > 0:
        rel_vol = total_volume / avg_volume
        # 1.0x = 30, 2.0x = 60, 4.0x = 90.
        import math
        score += max(0, min(100, 30 * math.log2(max(rel_vol, 0.5)) + 30))
        weights_used += 1

    # IV rank — when high, premium is rich vs the stock's own history.
    if iv_rank is not None:
        score += max(0, min(100, iv_rank))
        weights_used += 1
    elif iv_pct is not None:
        score += max(0, min(100, iv_pct))
        weights_used += 1

    # PCR signal — extreme reads (≤0.5 or ≥2.0) suggest one-sided positioning,
    # which usually means one side of premium is very rich.
    if pcr is not None:
        if pcr >= 2.0 or pcr <= 0.5:
            score += 70
            weights_used += 1
        elif pcr >= 1.5 or pcr <= 0.7:
            score += 50
            weights_used += 1

    if weights_used == 0:
        return out

    final_score = int(round(score / weights_used))
    final_score = max(0, min(100, final_score))

    if final_score >= 75:
        verdict = "Premium is rich"
        verdict_class = "verdict-sell"
        reason = "Implied vol is elevated and/or volume is well above average. Conditions favor selling premium."
    elif final_score >= 60:
        verdict = "Premium is moderately rich"
        verdict_class = "verdict-partial"
        reason = "Decent edge for sellers, but not a fat pitch. Flow context matters."
    elif final_score >= 40:
        verdict = "Premium is fair"
        verdict_class = "verdict-wait"
        reason = "Pricing reflects historical norms. No special edge to selling."
    else:
        verdict = "Premium is thin"
        verdict_class = "verdict-avoid"
        reason = "IV and volume are subdued. Selling premium here gives up upside without much credit."

    return {
        "symbol": symbol,
        "score": final_score,
        "verdict": verdict,
        "verdict_class": verdict_class,
        "reason": reason,
        "stats": {
            "total_volume": total_volume,
            "avg_volume": avg_volume,
            "total_premium": total_premium,
            "put_call_ratio": pcr,
            "iv_rank": iv_rank,
            "iv_percentile": iv_pct,
            "implied_move": implied_move,
            "last_price": last_price,
        },
        "data_available": True,
    }


def _compute_momentum_score(uw: Any, symbol: str) -> dict:
    """Score a ticker's intraday momentum opportunity by blending
    today's price action (gap, % from open, RVOL) with UW flow score.

    Returns:
      {
        symbol, score 0-100, verdict, verdict_class, reason,
        stats: {gap_pct, open, last, from_open_pct, rvol,
                flow_overall, flow_bullish, flow_bearish, flow_quality},
        data_available
      }
    """
    out = {
        "symbol": symbol,
        "score": 0,
        "verdict": "No data",
        "verdict_class": "verdict-wait",
        "reason": "",
        "stats": {},
        "data_available": False,
    }

    # Pull today's session data. Use Schwab if available (live), fall
    # back to yfinance via load_daily for prev close / avg volume.
    sc = _schwab()
    session_open = last_close = today_vol = prev_close = None
    avg_vol = None
    if sc is not None:
        try:
            quote = sc.get_quote(symbol)
            if quote:
                last_close = float(quote.get("last") or 0) or None
                prev_close = float(quote.get("close_prev") or 0) or None
                # Today's open from intraday bars
                bars = sc.get_intraday(symbol)
                if bars:
                    bars = [b for b in bars if b.get("open") is not None]
                    if bars:
                        session_open = float(bars[0].get("open"))
                        today_vol = sum((b.get("volume") or 0) for b in bars)
        except Exception as exc:
            _log_warn(symbol, "momentum.intraday", exc)
    # Fall back to daily bars if intraday data unavailable
    if session_open is None or last_close is None:
        try:
            daily = load_daily(symbol, days=5)
            if daily and len(daily) >= 1:
                today_bar = daily[-1]
                session_open = session_open or float(today_bar.get("open") or 0) or None
                last_close = last_close or float(today_bar.get("close") or 0) or None
                if len(daily) >= 2:
                    prev_close = prev_close or float(daily[-2].get("close") or 0) or None
                today_vol = today_vol or int(today_bar.get("volume") or 0) or None
        except Exception as exc:
            _log_warn(symbol, "momentum.daily_fallback", exc)
    if session_open is None or last_close is None:
        return out

    # 20-day average volume
    try:
        daily_for_avg = load_daily(symbol, days=25)
        if daily_for_avg and len(daily_for_avg) >= 2:
            prior_vols = [d.get("volume") or 0 for d in daily_for_avg[:-1] if d.get("volume")]
            if prior_vols:
                avg_vol = sum(prior_vols) / len(prior_vols)
    except Exception as exc:
        _log_warn(symbol, "momentum.avg_vol", exc)

    gap_pct = None
    if prev_close and prev_close > 0:
        gap_pct = ((session_open - prev_close) / prev_close) * 100.0
    from_open_pct = ((last_close - session_open) / session_open) * 100.0
    rvol = (today_vol / avg_vol) if (today_vol and avg_vol and avg_vol > 0) else None

    # UW flow score for the ticker
    flow_overall = flow_bull = flow_bear = flow_quality = None
    flow_reason = None
    if uw is not None:
        try:
            fs = _compute_flow_score(uw, symbol, last_close)
            if fs.get("data_available"):
                flow_overall = fs.get("overall")
                flow_bull = fs.get("bullish")
                flow_bear = fs.get("bearish")
                flow_quality = fs.get("quality")
                flow_reason = fs.get("reason")
        except Exception as exc:
            _log_warn(symbol, "momentum.flow_score", exc)

    # ── Score computation ──
    # Bullish momentum is the bull case for this scanner (intraday
    # long opportunities); bearish momentum is mirror. Final score
    # 0-100 where 50 = neutral. Above 65 = strong bullish, below 35 =
    # strong bearish.
    bull_points = 0.0
    bear_points = 0.0

    # Price action: gap up is bullish, gap down is bearish.
    if gap_pct is not None:
        if gap_pct >= 1.0:
            bull_points += min(20, gap_pct * 4)  # 5% gap = 20 pts
        elif gap_pct <= -1.0:
            bear_points += min(20, abs(gap_pct) * 4)
    # Position vs open (holding gains = bullish; fading = bearish)
    if from_open_pct >= 0.5:
        bull_points += min(20, from_open_pct * 4)
    elif from_open_pct <= -0.5:
        bear_points += min(20, abs(from_open_pct) * 4)
    # RVOL: high relative volume amplifies whichever direction is winning.
    if rvol is not None and rvol >= 1.5:
        rvol_bonus = min(15, (rvol - 1.0) * 10)
        if bull_points > bear_points:
            bull_points += rvol_bonus
        elif bear_points > bull_points:
            bear_points += rvol_bonus
    # UW flow bias: very strong amplifier when present.
    if flow_overall is not None and flow_quality is not None and flow_quality >= 30:
        if flow_overall >= 60:
            bull_points += min(35, (flow_overall - 50) * 1.4)
        elif flow_overall <= 40:
            bear_points += min(35, (50 - flow_overall) * 1.4)

    score = int(round(50 + (bull_points - bear_points) / 2))
    score = max(0, min(100, score))

    # Verdict classification
    has_flow = flow_overall is not None
    flow_aligned = (
        has_flow and (
            (bull_points > bear_points and flow_overall >= 55) or
            (bear_points > bull_points and flow_overall <= 45)
        )
    )
    flow_diverges = (
        has_flow and (
            (bull_points > bear_points + 10 and flow_overall <= 45) or
            (bear_points > bull_points + 10 and flow_overall >= 55)
        )
    )

    if score >= 70 and flow_aligned:
        verdict = "Flow-confirmed bullish breakout"
        verdict_class = "verdict-sell"
        reason = f"Price ({from_open_pct:+.2f}% from open) + UW flow agree on upside. Bull setup."
    elif score >= 70:
        verdict = "Bullish momentum"
        verdict_class = "verdict-sell"
        reason = f"Price action bullish (gap {gap_pct:+.2f}%, +{from_open_pct:.2f}% from open). Flow not yet confirming."
    elif score <= 30 and flow_aligned:
        verdict = "Flow-confirmed bearish breakdown"
        verdict_class = "verdict-avoid"
        reason = f"Price + UW flow agree on downside."
    elif score <= 30:
        verdict = "Bearish momentum"
        verdict_class = "verdict-avoid"
        reason = f"Price action bearish ({from_open_pct:+.2f}% from open). Flow not yet confirming."
    elif flow_diverges:
        verdict = "Flow divergence"
        verdict_class = "verdict-partial"
        reason = "Price and flow disagree. Watch for which side wins."
    elif rvol is not None and rvol < 0.7:
        verdict = "Avoid · low liquidity"
        verdict_class = "verdict-wait"
        reason = f"RVOL {rvol:.2f}x is below normal. Thin tape, momentum trades unreliable."
    elif flow_quality is not None and flow_quality < 25 and abs(score - 50) < 10:
        verdict = "Noisy · ignore"
        verdict_class = "verdict-wait"
        reason = "Flow is weak and price is uncommitted."
    else:
        verdict = "Mild lean"
        verdict_class = "verdict-wait"
        reason = "No strong setup yet."

    return {
        "symbol": symbol,
        "score": score,
        "verdict": verdict,
        "verdict_class": verdict_class,
        "reason": reason,
        "stats": {
            "gap_pct": round(gap_pct, 2) if gap_pct is not None else None,
            "session_open": round(session_open, 2),
            "last": round(last_close, 2),
            "from_open_pct": round(from_open_pct, 2),
            "rvol": round(rvol, 2) if rvol is not None else None,
            "today_volume": today_vol,
            "avg_volume": int(avg_vol) if avg_vol else None,
            "flow_overall": flow_overall,
            "flow_bullish": flow_bull,
            "flow_bearish": flow_bear,
            "flow_quality": flow_quality,
            "flow_reason": flow_reason,
        },
        "data_available": True,
    }


def _compute_flow_score(uw: Any, symbol: str, current_price: float = 0.0) -> dict:
    """Build a decision-grade flow score for `symbol` from UW data.

    Returns:
      {
        "symbol": str,
        "overall": int 0-100,             # 50 = neutral
        "bullish": int 0-100,
        "bearish": int 0-100,
        "quality": int 0-100,             # 0 = noise, 100 = high-conviction
        "cc_risk": int 0-100,             # 0 = safe to sell calls, 100 = avoid
        "verdict": str,                   # human-readable
        "verdict_class": str,             # "verdict-sell" | "verdict-wait" | "verdict-avoid" | "verdict-partial"
        "reason": str,                    # why this verdict
        "stats": {                        # raw inputs for transparency
          "total_call_premium": float,
          "total_put_premium": float,
          "ask_side_call_premium": float,
          "ask_side_put_premium": float,
          "call_sweeps": int,
          "put_sweeps": int,
          "call_above_strike_premium": float,  # bullish flow above current price
          "put_below_strike_premium": float,   # bearish flow below current price
          "alert_count": int,
        },
        "data_available": bool,
      }

    Scoring model (relative-to-ticker):
      - Pull today's flow_alerts for the symbol.
      - Sum call premium, put premium, ask-side variants, sweep counts.
      - Bullish = blend of (ask-side call share, call sweep ratio, premium magnitude).
      - Bearish = mirror for puts.
      - Quality = total premium / alert_count (avg trade size signal) +
                  ask-side concentration (high = directional conviction) +
                  premium magnitude vs a noise floor.
      - CC Risk = bullish call premium concentrated AT or ABOVE current
                  price (the calls Jerry would be selling). Even if
                  overall bullish is moderate, calls ABOVE strike are
                  exactly what threatens a covered-call writer.
    """
    # Default empty payload if UW has no flow for this symbol today.
    empty = {
        "symbol": symbol,
        "overall": 50, "bullish": 0, "bearish": 0, "quality": 0, "cc_risk": 0,
        "verdict": "No unusual flow", "verdict_class": "verdict-wait",
        "reason": "Today's options flow is quiet. Standard covered-call decisioning applies.",
        "stats": {
            "total_call_premium": 0.0, "total_put_premium": 0.0,
            "ask_side_call_premium": 0.0, "ask_side_put_premium": 0.0,
            "call_sweeps": 0, "put_sweeps": 0,
            "call_above_strike_premium": 0.0, "put_below_strike_premium": 0.0,
            "alert_count": 0,
        },
        "data_available": False,
    }

    alerts = uw.flow_alerts(symbol, limit=200, min_premium=0)
    if not alerts:
        return empty

    # Aggregate — UW alert payloads vary; tolerate missing fields.
    total_call_prem = 0.0
    total_put_prem = 0.0
    ask_call_prem = 0.0
    ask_put_prem = 0.0
    call_sweeps = 0
    put_sweeps = 0
    call_above = 0.0
    put_below = 0.0
    n_alerts = 0
    for a in alerts:
        try:
            t = (a.get("type") or a.get("option_type") or "").lower()
            prem = float(a.get("total_premium") or a.get("premium") or 0)
            if prem <= 0:
                continue
            n_alerts += 1
            # Ask-side share — UW flags this multiple ways; use whatever is present.
            ask_pct = a.get("ask_side_perc")
            if ask_pct is None:
                ask_pct = a.get("ask_perc") or 0.5
            try:
                ask_pct = float(ask_pct)
            except (TypeError, ValueError):
                ask_pct = 0.5
            ask_share = prem * max(0.0, min(1.0, ask_pct))
            # Sweep flag — different keys observed across UW responses.
            is_sweep = bool(a.get("has_sweep") or a.get("is_sweep") or
                            (a.get("trade_code") == "SWEEP") or
                            ((a.get("flags") or []) and "sweep" in str(a.get("flags")).lower()))
            try:
                strike = float(a.get("strike") or 0)
            except (TypeError, ValueError):
                strike = 0.0
            if t.startswith("c"):
                total_call_prem += prem
                ask_call_prem += ask_share
                if is_sweep:
                    call_sweeps += 1
                # Calls AT or ABOVE current price are the dangerous ones
                # for covered-call writers.
                if current_price > 0 and strike >= current_price:
                    call_above += ask_share
            elif t.startswith("p"):
                total_put_prem += prem
                ask_put_prem += ask_share
                if is_sweep:
                    put_sweeps += 1
                if current_price > 0 and strike <= current_price:
                    put_below += ask_share
        except Exception:  # noqa: BLE001
            continue

    if n_alerts == 0:
        return empty

    # ── Bullish sub-score ──
    # Ask-side call concentration + sweep density + total premium magnitude.
    call_ask_share = (ask_call_prem / total_call_prem) if total_call_prem > 0 else 0.0
    call_sweep_share = (call_sweeps / max(1, n_alerts))
    # Magnitude factor: log-scaled. $50K = 30, $500K = 60, $5M = 90.
    import math
    mag_factor = 0.0
    if ask_call_prem > 0:
        mag_factor = max(0, min(100, 30 * math.log10(max(ask_call_prem, 1) / 5000.0)))
    bullish = int(round(
        0.45 * (call_ask_share * 100) +
        0.20 * (call_sweep_share * 100) +
        0.35 * mag_factor
    ))
    bullish = max(0, min(100, bullish))

    # ── Bearish sub-score (mirror) ──
    put_ask_share = (ask_put_prem / total_put_prem) if total_put_prem > 0 else 0.0
    put_sweep_share = (put_sweeps / max(1, n_alerts))
    put_mag = 0.0
    if ask_put_prem > 0:
        put_mag = max(0, min(100, 30 * math.log10(max(ask_put_prem, 1) / 5000.0)))
    bearish = int(round(
        0.45 * (put_ask_share * 100) +
        0.20 * (put_sweep_share * 100) +
        0.35 * put_mag
    ))
    bearish = max(0, min(100, bearish))

    # ── Quality sub-score ──
    # Total ask-side premium (conviction) + sweep prevalence + alert count.
    total_ask = ask_call_prem + ask_put_prem
    quality_mag = 0.0
    if total_ask > 0:
        quality_mag = max(0, min(100, 30 * math.log10(max(total_ask, 1) / 5000.0)))
    quality_count = max(0, min(100, n_alerts * 5))  # 20+ alerts = full credit
    sweep_share = ((call_sweeps + put_sweeps) / max(1, n_alerts))
    quality = int(round(
        0.50 * quality_mag +
        0.25 * quality_count +
        0.25 * (sweep_share * 100)
    ))
    quality = max(0, min(100, quality))

    # ── Covered Call Risk sub-score ──
    # Concentration of bullish flow AT or ABOVE current price is the
    # exact signal that threatens a covered-call writer. Even a moderate
    # overall bullish reading is dangerous if the flow is targeting
    # strikes at-or-above where Jerry would sell.
    if current_price > 0 and ask_call_prem > 0:
        above_share = call_above / ask_call_prem  # 0-1
    else:
        above_share = 0.0
    cc_risk_mag = 0.0
    if call_above > 0:
        cc_risk_mag = max(0, min(100, 30 * math.log10(max(call_above, 1) / 5000.0)))
    # Heavy weight on above-strike concentration; bonus for bullish strength.
    cc_risk = int(round(
        0.55 * (above_share * 100) +
        0.25 * cc_risk_mag +
        0.20 * bullish
    ))
    cc_risk = max(0, min(100, cc_risk))

    # ── Overall flow score (50 = neutral) ──
    # Bullish above 50, bearish below 50, scaled by quality (low quality
    # means the score tilts back toward 50 — noise should not move us).
    raw_tilt = (bullish - bearish) / 2.0  # -50..+50
    quality_factor = quality / 100.0
    overall = int(round(50 + raw_tilt * quality_factor))
    overall = max(0, min(100, overall))

    # ── Verdict for the covered-call use case ──
    # User decision: override fires when Bullish ≥ 70 AND CC Risk ≥ 70.
    verdict = "Flow neutral"
    verdict_class = "verdict-wait"
    reason = "Flow signal is mixed or quiet. Standard covered-call decisioning applies."

    if bullish >= 70 and cc_risk >= 70:
        verdict = "Avoid selling calls"
        verdict_class = "verdict-avoid"
        reason = (f"Aggressive bullish call flow targeting strikes at or above ${current_price:.2f}. "
                  f"Ask-side call premium dominates ({call_ask_share:.0%} ask-side). "
                  f"Selling calls into this risks immediate assignment.")
    elif bullish >= 60 and cc_risk >= 60:
        verdict = "Sell higher strike only"
        verdict_class = "verdict-partial"
        reason = (f"Bullish flow is meaningful and concentrated above current price. "
                  f"If selling calls, push strike further OTM than usual to avoid the targeted zone.")
    elif bearish >= 70 and quality >= 50:
        verdict = "Downside risk elevated"
        verdict_class = "verdict-partial"
        reason = (f"Heavy put flow ({put_ask_share:.0%} ask-side). "
                  f"Covered calls collect premium but offer limited downside protection. "
                  f"Consider collar or reduce stock exposure.")
    elif bullish < 40 and bearish < 40 and quality < 30:
        verdict = "Noisy flow — ignore"
        verdict_class = "verdict-wait"
        reason = "Total flow magnitude is low and direction is mixed. Not enough conviction to act on."
    elif overall >= 55 and bullish < 60:
        verdict = "Mild bullish lean"
        verdict_class = "verdict-sell"
        reason = "Slight positive bias but not strong enough to threaten a covered-call sale at typical OTM strikes."
    elif overall <= 45 and bearish < 60:
        verdict = "Mild bearish lean"
        verdict_class = "verdict-sell"
        reason = "Slight negative bias. Covered-call premium may compensate, but watch for follow-through."
    else:
        verdict = "Flow supports selling calls"
        verdict_class = "verdict-sell"
        reason = "Flow is neutral-to-fading. Premium harvest looks clean at the usual delta target."

    return {
        "symbol": symbol,
        "overall": overall,
        "bullish": bullish,
        "bearish": bearish,
        "quality": quality,
        "cc_risk": cc_risk,
        "verdict": verdict,
        "verdict_class": verdict_class,
        "reason": reason,
        "stats": {
            "total_call_premium": round(total_call_prem, 2),
            "total_put_premium": round(total_put_prem, 2),
            "ask_side_call_premium": round(ask_call_prem, 2),
            "ask_side_put_premium": round(ask_put_prem, 2),
            "call_sweeps": call_sweeps,
            "put_sweeps": put_sweeps,
            "call_above_strike_premium": round(call_above, 2),
            "put_below_strike_premium": round(put_below, 2),
            "alert_count": n_alerts,
        },
        "data_available": True,
    }


# ─── Pure math layer ───────────────────────────────────────────────
# EMA, RSI, ATR and Black-Scholes price and greeks live in metrics.py
# (v1.40 split). Names re-imported so call sites are unchanged.
from metrics import (  # noqa: F401
    _ema,
    _rsi,
    _atr,
    _norm_cdf,
    _bs_delta,
    _bs_theta,
    _bs_gamma,
    _bs_vega,
    _bs_price,
)



def backtest_ema_pullback(symbol: str, direction: str = "long",
                          lookback_days: int = 365,
                          ema_fast: int = 9, ema_med: int = 21,
                          ema_slow: int = 50, slope_bars: int = 10) -> dict:
    """Backtest the EMA pullback strategy on daily bars.

    Configurable EMA periods (default 9/21/50) and trend-slope lookback
    (default 10 bars). The strategy logic is identical regardless of
    period choice — only the indicator values change.

    Long setup (rules ALL must be true):
      Trend: close > fast > med > slow EMA, slow EMA rising over slope_bars.
      Pullback: low of (current bar OR prior 1-2 bars) touched fast EMA from above
                (low <= fast EMA × 1.005).
      Confirmation: bar closes back above fast EMA AND closes green AND closes
                    above prior bar's high.
      Filter: RSI(14) between 40 and 70 at confirmation bar.
      Filter: confirmation bar's range <= 1.5 × ATR(14) (no climax candle).

    Short setup mirrors the above for downtrends.
    """
    direction = direction.lower().strip()
    if direction not in ("long", "short"):
        return {"error": "direction must be 'long' or 'short'"}
    # Sanity-clamp EMA periods so users can't pass garbage and crash the loop.
    ema_fast = max(2, min(200, int(ema_fast or 9)))
    ema_med = max(ema_fast + 1, min(300, int(ema_med or 21)))
    ema_slow = max(ema_med + 1, min(500, int(ema_slow or 50)))
    slope_bars = max(2, min(50, int(slope_bars or 10)))

    bars = load_daily(symbol, days=lookback_days)
    if not bars or len(bars) < ema_slow + slope_bars + 5:
        return {"error": f"insufficient daily bars (need ≥ {ema_slow + slope_bars + 5})"}

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    opens = [b["open"] for b in bars]
    ema9 = _ema(closes, ema_fast)
    ema21 = _ema(closes, ema_med)
    ema50 = _ema(closes, ema_slow)
    rsi14 = _rsi(closes, 14)
    atr14 = _atr(bars, 14)

    trades: list[dict] = []

    in_trade = False
    entry_idx = entry_price = stop = 0
    entry_date = ""
    bars_in_trade = 0

    # Need ema_slow + slope_bars bars of warmup
    start = ema_slow + slope_bars
    for i in range(start, len(bars)):
        if any(x[i] is None for x in (ema9, ema21, ema50, rsi14, atr14)):
            continue
        if any(x[i - slope_bars] is None for x in (ema50,)):
            continue

        c = closes[i]; h = highs[i]; l = lows[i]; o = opens[i]
        e9 = ema9[i]; e21 = ema21[i]; e50 = ema50[i]
        r = rsi14[i]; a = atr14[i]
        prev_h = highs[i - 1]; prev_l = lows[i - 1]

        # Manage open trade first
        if in_trade:
            bars_in_trade += 1
            exit_reason = None
            exit_price = None
            if direction == "long":
                if l <= stop:
                    exit_reason = "stop"
                    exit_price = stop
                elif c < e21 and bars_in_trade >= 1:
                    exit_reason = "trend_break"
                    exit_price = c
                elif bars_in_trade >= 10:
                    exit_reason = "time_stop"
                    exit_price = c
            else:  # short
                if h >= stop:
                    exit_reason = "stop"
                    exit_price = stop
                elif c > e21 and bars_in_trade >= 1:
                    exit_reason = "trend_break"
                    exit_price = c
                elif bars_in_trade >= 10:
                    exit_reason = "time_stop"
                    exit_price = c

            if exit_reason:
                pnl_pct = ((exit_price - entry_price) / entry_price * 100.0
                           if direction == "long"
                           else (entry_price - exit_price) / entry_price * 100.0)
                risk_per_share = abs(entry_price - stop)
                r_multiple = ((exit_price - entry_price) / risk_per_share
                              if direction == "long"
                              else (entry_price - exit_price) / risk_per_share) if risk_per_share > 0 else 0
                trades.append({
                    "entry_date": entry_date,
                    "exit_date": bars[i]["date"][:10] if isinstance(bars[i]["date"], str) else str(bars[i]["date"])[:10],
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price, 2),
                    "stop": round(stop, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "r_multiple": round(r_multiple, 2),
                    "bars_held": bars_in_trade,
                    "exit_reason": exit_reason,
                })
                in_trade = False

        # Look for new entry (only if not in trade)
        if not in_trade:
            # Trend filter
            if direction == "long":
                trend_ok = (c > e9 > e21 > e50) and (e50 > ema50[i - slope_bars])
            else:
                trend_ok = (c < e9 < e21 < e50) and (e50 < ema50[i - slope_bars])
            if not trend_ok:
                continue

            # Pullback: confirmation candle must follow a touch of 9 EMA
            # within prior 1-2 bars (i.e. yesterday or day before).
            if direction == "long":
                touched = (prev_l <= ema9[i - 1] * 1.005) or \
                          (lows[i - 2] <= ema9[i - 2] * 1.005 if ema9[i - 2] else False)
            else:
                touched = (prev_h >= ema9[i - 1] * 0.995) or \
                          (highs[i - 2] >= ema9[i - 2] * 0.995 if ema9[i - 2] else False)
            if not touched:
                continue

            # Confirmation candle (today)
            if direction == "long":
                conf = (c > e9) and (c > o) and (c > prev_h)
            else:
                conf = (c < e9) and (c < o) and (c < prev_l)
            if not conf:
                continue

            # RSI filter
            if direction == "long":
                if not (40 <= r <= 70):
                    continue
            else:
                if not (30 <= r <= 60):
                    continue

            # No climax candle
            today_range = h - l
            if today_range > 1.5 * a:
                continue

            # Need bar i+1 for entry — guard against last bar
            if i + 1 >= len(bars):
                continue

            entry_idx = i + 1
            entry_price = opens[entry_idx]
            if direction == "long":
                stop = l - 0.10 * a
            else:
                stop = h + 0.10 * a
            entry_date = bars[entry_idx]["date"][:10] if isinstance(bars[entry_idx]["date"], str) else str(bars[entry_idx]["date"])[:10]
            in_trade = True
            bars_in_trade = 0

    # Close any open trade at last bar
    if in_trade and trades and entry_price > 0:
        last = bars[-1]
        last_close = last["close"]
        pnl_pct = ((last_close - entry_price) / entry_price * 100.0
                   if direction == "long"
                   else (entry_price - last_close) / entry_price * 100.0)
        risk_per_share = abs(entry_price - stop)
        r_multiple = ((last_close - entry_price) / risk_per_share
                      if direction == "long"
                      else (entry_price - last_close) / risk_per_share) if risk_per_share > 0 else 0
        trades.append({
            "entry_date": entry_date,
            "exit_date": last["date"][:10] if isinstance(last["date"], str) else str(last["date"])[:10],
            "entry_price": round(entry_price, 2),
            "exit_price": round(last_close, 2),
            "stop": round(stop, 2),
            "pnl_pct": round(pnl_pct, 2),
            "r_multiple": round(r_multiple, 2),
            "bars_held": bars_in_trade,
            "exit_reason": "open_at_end",
        })

    # Stats
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    n_trades = len(trades)
    win_rate = (len(wins) / n_trades * 100.0) if n_trades else 0.0
    avg_win_pct = (sum(t["pnl_pct"] for t in wins) / len(wins)) if wins else 0.0
    avg_loss_pct = (sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else 0.0
    expectancy_pct = (win_rate / 100.0 * avg_win_pct) + ((1 - win_rate / 100.0) * avg_loss_pct)
    sum_wins = sum(t["pnl_pct"] for t in wins)
    sum_losses_abs = abs(sum(t["pnl_pct"] for t in losses))
    profit_factor = (sum_wins / sum_losses_abs) if sum_losses_abs > 0 else (float('inf') if sum_wins > 0 else 0.0)

    # ── REALISTIC PERFORMANCE METRICS ──
    # The OLD equity curve compounded pnl_pct/100 each trade, which is
    # equivalent to "bet 100% of capital every trade" — not how anyone
    # actually trades. That math made winners look like get-rich-quick
    # and losers look catastrophic.
    #
    # Two honest replacements, both reported:
    #
    # 1) total_R: sum of R-multiples. R = (exit_price - entry_price) /
    #    risk_per_share where risk_per_share = entry_price - stop. This is
    #    the standard Van Tharp / Tom Basso strategy report. It says
    #    "strategy made N units of risk over the year." Independent of
    #    position size and capital. Most useful single number.
    #
    # 2) equity curve at 1% risk: simulate a 1% account-equity risk per
    #    trade. New equity = equity × (1 + 0.01 × R_multiple). This is
    #    realistic position sizing — what the strategy actually returns
    #    if you risk 1% of equity per trade.
    sum_r = sum(t.get("r_multiple", 0) for t in trades)
    avg_r = (sum_r / n_trades) if n_trades else 0.0
    win_r_avg = (sum(t["r_multiple"] for t in wins) / len(wins)) if wins else 0.0
    loss_r_avg = (sum(t["r_multiple"] for t in losses) / len(losses)) if losses else 0.0
    expectancy_R = (win_rate / 100.0 * win_r_avg) + ((1 - win_rate / 100.0) * loss_r_avg)

    # Equity curve at 1% risk per trade
    RISK_PCT = 0.01  # 1% of equity per trade — Jerry's preferred risk size
    equity = 1.0
    eq_pts = []
    peak = equity
    max_dd = 0.0
    for t in trades:
        # Each R worth of stop-distance = RISK_PCT of equity, so trade
        # P&L in equity terms is R × RISK_PCT.
        equity *= (1.0 + t.get("r_multiple", 0) * RISK_PCT)
        eq_pts.append({"date": t["exit_date"], "equity": round(equity, 4)})
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    total_return_pct = (equity - 1.0) * 100.0

    # Profit factor must be JSON-serialisable (no inf).
    if profit_factor == float('inf'):
        profit_factor = 999.99

    return {
        "symbol": symbol.upper(),
        "direction": direction,
        "lookback_days": lookback_days,
        "ema_fast": ema_fast,
        "ema_med": ema_med,
        "ema_slow": ema_slow,
        "slope_bars": slope_bars,
        "bars_tested": len(bars),
        "n_trades": n_trades,
        "win_rate_pct": round(win_rate, 1),
        "avg_win_pct": round(avg_win_pct, 2),
        "avg_loss_pct": round(avg_loss_pct, 2),
        "expectancy_pct": round(expectancy_pct, 2),
        # New honest metrics — see comments above
        "total_R": round(sum_r, 2),                       # sum of R-multiples
        "avg_R": round(avg_r, 2),                         # average R per trade
        "expectancy_R": round(expectancy_R, 3),           # R-weighted expectancy
        "win_R_avg": round(win_r_avg, 2),
        "loss_R_avg": round(loss_r_avg, 2),
        "risk_pct_per_trade": RISK_PCT * 100,             # 1.0 — for label
        "profit_factor": round(profit_factor, 2),
        "max_dd_pct": round(max_dd, 2),
        "total_return_pct": round(total_return_pct, 2),   # NOW: 1% risk model
        "trades": trades,
        "equity_curve": eq_pts,
    }


def ema_pullback_setup_state(symbol: str, direction: str = "long",
                             ema_fast: int = 9, ema_med: int = 21,
                             ema_slow: int = 50, slope_bars: int = 10) -> dict:
    """Live setup state for the EMA pullback strategy on `symbol`.
    Used by the watchlist scanner to flag tickers currently in setup.

    Configurable EMA periods (default 9/21/50) and trend-slope lookback
    (default 10 bars). Same logic as the backtest.

    Returns one of these states:
      "no_trend"      — fails trend filter; not a candidate
      "in_trend"      — trend OK but no recent fast EMA touch
      "pulled_back"   — trend OK, recent fast EMA touch, awaiting confirmation
      "confirmed"     — confirmation candle printed today (entry tomorrow)
    """
    direction = direction.lower().strip()
    if direction not in ("long", "short"):
        return {"error": "direction must be 'long' or 'short'"}
    ema_fast = max(2, min(200, int(ema_fast or 9)))
    ema_med = max(ema_fast + 1, min(300, int(ema_med or 21)))
    ema_slow = max(ema_med + 1, min(500, int(ema_slow or 50)))
    slope_bars = max(2, min(50, int(slope_bars or 10)))

    bars = load_daily(symbol, days=max(120, ema_slow + slope_bars + 30))
    if not bars or len(bars) < ema_slow + slope_bars + 5:
        return {"symbol": symbol, "state": "no_data"}

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    opens = [b["open"] for b in bars]
    ema9 = _ema(closes, ema_fast)
    ema21 = _ema(closes, ema_med)
    ema50 = _ema(closes, ema_slow)
    rsi14 = _rsi(closes, 14)
    atr14 = _atr(bars, 14)

    i = len(bars) - 1
    if any(x[i] is None for x in (ema9, ema21, ema50, rsi14, atr14)) or ema50[i - slope_bars] is None:
        return {"symbol": symbol, "state": "no_data"}

    c = closes[i]; h = highs[i]; l = lows[i]; o = opens[i]
    e9 = ema9[i]; e21 = ema21[i]; e50 = ema50[i]
    r = rsi14[i]; a = atr14[i]
    prev_h = highs[i - 1]; prev_l = lows[i - 1]

    # Trend
    if direction == "long":
        trend_ok = (c > e9 > e21 > e50) and (e50 > ema50[i - slope_bars])
    else:
        trend_ok = (c < e9 < e21 < e50) and (e50 < ema50[i - slope_bars])
    if not trend_ok:
        return {
            "symbol": symbol, "state": "no_trend",
            "close": round(c, 2), "ema9": round(e9, 2),
            "ema21": round(e21, 2), "ema50": round(e50, 2),
            "rsi14": round(r, 1),
        }

    # Pullback (last 1-2 bars touched 9 EMA from the trend side)
    if direction == "long":
        touched = (prev_l <= ema9[i - 1] * 1.005) or \
                  (lows[i - 2] <= ema9[i - 2] * 1.005 if ema9[i - 2] else False)
    else:
        touched = (prev_h >= ema9[i - 1] * 0.995) or \
                  (highs[i - 2] >= ema9[i - 2] * 0.995 if ema9[i - 2] else False)

    # Confirmation
    if direction == "long":
        conf = touched and (c > e9) and (c > o) and (c > prev_h) \
               and (40 <= r <= 70) and ((h - l) <= 1.5 * a)
    else:
        conf = touched and (c < e9) and (c < o) and (c < prev_l) \
               and (30 <= r <= 60) and ((h - l) <= 1.5 * a)

    if conf:
        # Suggested stop
        if direction == "long":
            stop_px = round(l - 0.10 * a, 2)
        else:
            stop_px = round(h + 0.10 * a, 2)
        return {
            "symbol": symbol, "state": "confirmed",
            "close": round(c, 2), "ema9": round(e9, 2),
            "ema21": round(e21, 2), "ema50": round(e50, 2),
            "rsi14": round(r, 1), "atr14": round(a, 2),
            "suggested_stop": stop_px,
            "suggested_entry": "next bar open",
        }
    if touched:
        return {
            "symbol": symbol, "state": "pulled_back",
            "close": round(c, 2), "ema9": round(e9, 2),
            "ema21": round(e21, 2), "ema50": round(e50, 2),
            "rsi14": round(r, 1), "atr14": round(a, 2),
        }
    return {
        "symbol": symbol, "state": "in_trend",
        "close": round(c, 2), "ema9": round(e9, 2),
        "ema21": round(e21, 2), "ema50": round(e50, 2),
        "rsi14": round(r, 1), "atr14": round(a, 2),
    }


def next_friday(from_: date | None = None) -> date:
    d = from_ or date.today()
    offset = (4 - d.weekday()) % 7 or 7
    return d + timedelta(days=offset)


def load_option_chain(
    symbol: str,
    target_fri: date,
    target_exp: str | None = None,
) -> tuple[list, list, str | None, list[str]]:
    # Try Schwab first.
    sc = _schwab()
    if sc is not None:
        try:
            chain = sc.get_option_chain(symbol)
            if chain and chain.get("expirations"):
                # Filter to weekly Fridays >= target_fri (same logic as yf path)
                valid = []
                for e in chain["expirations"]:
                    try:
                        ed = pd.Timestamp(e).date()
                    except Exception:
                        continue
                    if ed.weekday() == 4 and ed >= target_fri:
                        valid.append(ed)
                valid.sort()
                if valid:
                    expirations_str = [e.strftime("%Y-%m-%d") for e in valid[:13]]
                    if target_exp:
                        try:
                            req = pd.Timestamp(target_exp).date()
                            exp = req if req in valid else valid[0]
                        except Exception:
                            exp = valid[0]
                    else:
                        exp = valid[0]
                    exp_str = exp.strftime("%Y-%m-%d")
                    legs = chain["chains"].get(exp_str)
                    if legs:
                        # Schwab already provides Greeks. Pass them through;
                        # back-fill any missing fields with our own BS calc
                        # using the underlying's last as spot.
                        spot = chain.get("underlying", {}).get("last") or 0
                        days_to_exp = max((exp - date.today()).days, 1)
                        T = days_to_exp / 365.0
                        def _normalize_rows(rows, side):
                            out = []
                            for r in rows:
                                strike = float(r.get("strike", 0))
                                bid = float(r.get("bid") or 0)
                                ask = float(r.get("ask") or 0)
                                last = float(r.get("last") or 0)
                                iv = float(r.get("iv") or 0)
                                delta = r.get("delta")
                                theta = r.get("theta")
                                gamma = r.get("gamma")
                                vega = r.get("vega")
                                # Backfill greeks from BS if Schwab returned None
                                if delta is None or delta != delta:
                                    delta = _bs_delta(spot, strike, T, iv, side)
                                if theta is None or theta != theta:
                                    theta = _bs_theta(spot, strike, T, iv, side)
                                if gamma is None or gamma != gamma:
                                    gamma = _bs_gamma(spot, strike, T, iv)
                                if vega is None or vega != vega:
                                    vega = _bs_vega(spot, strike, T, iv)
                                mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else last
                                out.append({
                                    "strike": strike,
                                    "bid": bid or mid * 0.97,
                                    "ask": ask or mid * 1.03,
                                    "last": last or mid,
                                    "volume": int(r.get("volume") or 0),
                                    "openInterest": int(r.get("openInterest") or 0),
                                    "iv": iv,
                                    "delta": delta,
                                    "theta": theta,
                                    "gamma": gamma,
                                    "vega": vega,
                                })
                            return sorted(out, key=lambda x: x["strike"])
                        _LAST_SOURCE["source"] = "schwab"
                        return (_normalize_rows(legs.get("calls", []), "call"),
                                _normalize_rows(legs.get("puts", []), "put"),
                                exp_str,
                                expirations_str)
        except Exception as exc:  # noqa: BLE001
            print(f"[schwab] load_option_chain fallback: {exc}", file=sys.stderr)
    # Fallback: yfinance
    _LAST_SOURCE["source"] = "yfinance"
    stock = yf.Ticker(symbol)
    try:
        exps = stock.options
    except Exception as exc:
        _log_warn(symbol, "yf.options", exc)
        return [], [], None, []
    valid = sorted([
        pd.Timestamp(e).date() for e in exps
        if pd.Timestamp(e).date().weekday() == 4 and pd.Timestamp(e).date() >= target_fri
    ])
    if not valid:
        return [], [], None, []

    # Limit the picker list to roughly the next 13 weekly Fridays so the
    # dropdown stays scannable. The full list of monthlies remains in yf.
    expirations_str = [e.strftime("%Y-%m-%d") for e in valid[:13]]

    if target_exp:
        try:
            req = pd.Timestamp(target_exp).date()
            exp = req if req in valid else valid[0]
        except Exception:
            exp = valid[0]
    else:
        exp = valid[0]

    try:
        opt = stock.option_chain(exp.strftime("%Y-%m-%d"))
    except Exception as exc:
        _log_warn(symbol, "yf.option_chain", exc)
        return [], [], None, expirations_str

    def safe_float(v, default=0.0):
        try:
            f = float(v)
            return default if f != f else f  # NaN check
        except (TypeError, ValueError):
            return default

    def safe_int(v, default=0):
        return int(safe_float(v, default))

    spot = float(stock.history(period="1d", auto_adjust=False)["Close"].iloc[-1])
    days_to_exp = max((exp - date.today()).days, 1)
    T = days_to_exp / 365.0

    def to_rows(df, side):
        out = []
        for r in df.itertuples():
            strike = safe_float(getattr(r, "strike", 0))
            bid = safe_float(getattr(r, "bid", 0))
            ask = safe_float(getattr(r, "ask", 0))
            last = safe_float(getattr(r, "lastPrice", 0))
            iv = safe_float(getattr(r, "impliedVolatility", 0))
            mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else last
            out.append({
                "strike": strike,
                "bid": bid or mid * 0.97,
                "ask": ask or mid * 1.03,
                "last": last or mid,
                "volume": safe_int(getattr(r, "volume", 0)),
                "openInterest": safe_int(getattr(r, "openInterest", 0)),
                "iv": iv,
                "delta": _bs_delta(spot, strike, T, iv, side),
                "theta": _bs_theta(spot, strike, T, iv, side),
                "gamma": _bs_gamma(spot, strike, T, iv),
                "vega": _bs_vega(spot, strike, T, iv),
            })
        return sorted(out, key=lambda x: x["strike"])

    return to_rows(opt.calls, "call"), to_rows(opt.puts, "put"), exp.strftime("%Y-%m-%d"), expirations_str




def _hv_realized(closes: list[float], window: int = 20) -> float | None:
    """Annualized realized volatility from a list of close prices.
    Returns None if the closes list is too short."""
    import math
    if len(closes) < window + 1:
        return None
    # Log returns over the trailing `window` days
    rets = []
    for i in range(len(closes) - window, len(closes)):
        if i <= 0:
            continue
        if closes[i - 1] <= 0 or closes[i] <= 0:
            continue
        rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def _strike_for_delta(spot: float, target_delta: float, T: float, sigma: float,
                       side: str, r: float = 0.045) -> float:
    """Find the strike that yields the target delta via binary search.
    side='call' → target_delta should be 0..1 (e.g. 0.20).
    side='put'  → target_delta should be -1..0 (e.g. -0.20).
    Returns the strike rounded to nearest dollar.
    """
    import math
    if spot <= 0 or T <= 0 or sigma <= 0:
        return spot
    # For a call: as strike increases, delta decreases. Binary search the
    # strike between spot * 0.5 and spot * 1.8.
    lo = spot * 0.5
    hi = spot * 1.8
    for _ in range(40):
        mid = (lo + hi) / 2
        d = _bs_delta(spot, mid, T, sigma, side, r)
        if side == "call":
            # higher strike = lower delta
            if d > target_delta:
                lo = mid
            else:
                hi = mid
        else:
            # higher strike = LESS negative put delta (closer to zero)
            if d < target_delta:
                hi = mid
            else:
                lo = mid
    return round((lo + hi) / 2)


# ─────────────────────────────────────────────────────────────────────────
# Earnings IV crush ladder (#3) — for the past N earnings dates, compute
# implied move (synthetic ATM straddle as % of spot) vs realized move
# (actual abs % move next day).
# ─────────────────────────────────────────────────────────────────────────
def _ttl_memoize(ttl_seconds: float):
    """In-memory TTL memoization for pure-ish per-symbol builders. Keys on the
    call args, serves a cached result while fresh, and never caches failures
    (None or a dict carrying an "error" key) so one upstream hiccup can't pin a
    bad result. Thread-safe."""
    def deco(fn):
        store: dict = {}
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                key = (args, tuple(sorted(kwargs.items())))
            except Exception:
                return fn(*args, **kwargs)   # unhashable args — skip cache
            now = time.time()
            with lock:
                hit = store.get(key)
                if hit is not None and (now - hit[0]) < ttl_seconds:
                    return hit[1]
            val = fn(*args, **kwargs)
            if val is not None and not (isinstance(val, dict) and val.get("error")):
                with lock:
                    store[key] = (time.time(), val)
                    if len(store) > 256:
                        store.pop(min(store, key=lambda k: store[k][0]), None)
            return val
        return wrapper
    return deco


@_ttl_memoize(900)        # earnings dates don't change intraday
def build_earnings_ladder(symbol: str, n_events: int = 8) -> dict:
    """For the past N earnings events on `symbol`, compute:
        - implied_move_pct: synthetic ATM straddle / spot the day before
        - realized_move_pct: abs % open-to-close move the day after
        - winner: 'sellers' if implied > realized, 'buyers' otherwise
    Returns {ticker, events: [...], summary: {sellers_pct, n}, source}.
    Synthetic IV = HV20 (B1 approach). Confidence ~0.83 for ATM moves.
    """
    import math
    out = {"ticker": symbol, "events": [], "summary": {}, "source": "synthetic-hv20"}
    try:
        import yfinance as yf
        import pandas as pd
    except Exception as exc:
        out["error"] = f"yfinance not available: {exc}"
        return out
    try:
        tk = yf.Ticker(symbol)
        # earnings_dates: yfinance returns a DataFrame indexed by earnings
        # datetime, with columns including 'EPS Estimate', 'Reported EPS',
        # 'Surprise(%)'. We just need the index dates.
        ed = None
        try:
            ed = tk.get_earnings_dates(limit=n_events * 4)  # over-fetch then trim
        except Exception:
            ed = getattr(tk, "earnings_dates", None)
        if ed is None or len(ed) == 0:
            out["error"] = "No earnings history available"
            return out
        # Filter to past events only (yfinance includes upcoming).
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        # yfinance index is timezone-aware DatetimeIndex
        past = []
        for ts in ed.index:
            try:
                # Some yfinance versions return tz-naive; handle both
                dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                if dt.tzinfo is None:
                    from datetime import timezone as _tz
                    dt = dt.replace(tzinfo=_tz.utc)
                if dt < now:
                    past.append(dt)
            except Exception:
                continue
        past.sort(reverse=True)
        past = past[:n_events]
        if not past:
            out["error"] = "No past earnings found"
            return out
        # Pull a wide history range covering the earliest event
        oldest = min(past).date()
        from datetime import timedelta
        hist_start = (oldest - timedelta(days=60)).isoformat()
        hist_end = (max(past).date() + timedelta(days=10)).isoformat()
        hist = tk.history(start=hist_start, end=hist_end, auto_adjust=False)
        if hist is None or len(hist) == 0:
            out["error"] = "Price history empty"
            return out
        hist_dates = [d.date() for d in hist.index]
        closes = list(hist["Close"].astype(float))
        opens = list(hist["Open"].astype(float))
        # For each earnings event, compute implied move and realized move
        sellers_won = 0
        events_out = []
        for earn_dt in past:
            earn_date = earn_dt.date()
            # Find the trading day on or just before earnings — yfinance
            # earnings dates are in ET so the relevant close depends on
            # whether the report was BMO (before market open) or AMC
            # (after market close). Without that detail, use the close
            # immediately preceding earn_date as the pre-earnings spot.
            pre_idx = None
            for i in range(len(hist_dates) - 1, -1, -1):
                if hist_dates[i] < earn_date:
                    pre_idx = i
                    break
            # Post-earnings open is the next trading day's open after earnings
            post_idx = None
            for i in range(len(hist_dates)):
                if hist_dates[i] > earn_date:
                    post_idx = i
                    break
            if pre_idx is None or post_idx is None or pre_idx < 21:
                continue
            spot = closes[pre_idx]
            # HV20 leading into earnings, used as IV proxy
            window_closes = closes[pre_idx - 21:pre_idx + 1]
            iv_proxy = _hv_realized(window_closes, window=20)
            if iv_proxy is None or iv_proxy <= 0 or spot <= 0:
                continue
            # Synthetic ATM straddle for ~next-Friday expiration (~3 DTE
            # average for typical earnings overlay; use 5 calendar days)
            T = 5.0 / 365.0
            call_px = _bs_price(spot, spot, T, iv_proxy, "call")
            put_px = _bs_price(spot, spot, T, iv_proxy, "put")
            implied_move_dollars = call_px + put_px
            implied_move_pct = (implied_move_dollars / spot) * 100.0
            # Realized: abs open-to-close % move on the post-earnings day
            post_open = opens[post_idx]
            post_close = closes[post_idx]
            realized_move_pct = abs((post_close - post_open) / post_open) * 100.0
            # Winner attribution
            sellers = implied_move_pct > realized_move_pct
            if sellers:
                sellers_won += 1
            events_out.append({
                "date": earn_date.isoformat(),
                "spot": round(spot, 2),
                "iv_proxy_pct": round(iv_proxy * 100, 1),
                "implied_move_pct": round(implied_move_pct, 2),
                "realized_move_pct": round(realized_move_pct, 2),
                "winner": "sellers" if sellers else "buyers",
                "edge_pct": round(implied_move_pct - realized_move_pct, 2),
            })
        out["events"] = events_out
        if events_out:
            n = len(events_out)
            out["summary"] = {
                "n": n,
                "sellers_pct": round(100.0 * sellers_won / n, 1),
                "avg_implied": round(sum(e["implied_move_pct"] for e in events_out) / n, 2),
                "avg_realized": round(sum(e["realized_move_pct"] for e in events_out) / n, 2),
                "avg_edge": round(sum(e["edge_pct"] for e in events_out) / n, 2),
            }
    except Exception as exc:  # noqa: BLE001
        import traceback
        out["error"] = f"{exc}"
        out["trace"] = traceback.format_exc()[:500]
    return out


# ─────────────────────────────────────────────────────────────────────────
# Walk-forward backtest (#5) — every Monday in the test window, "open" a
# strategy at synthetic strikes, hold to Friday expiration, mark P/L.
# Synthetic prices use HV20 as IV proxy (B1 approach).
# ─────────────────────────────────────────────────────────────────────────
def backtest_strategy(symbol: str, strategy: str, weeks: int = 52,
                      target_delta: float = 0.20) -> dict:
    """Walk-forward weekly backtest. Strategies supported:
    covered_call, cash_secured_put, short_strangle, iron_condor,
    bull_put_spread, jade_lizard, wheel.
    Returns weekly P/L series + summary stats. All P/L in $/share unless
    flagged otherwise. Conservative bias: spread/slippage ignored.
    """
    import math
    out = {
        "ticker": symbol,
        "strategy": strategy,
        "weeks_requested": weeks,
        "target_delta": target_delta,
        "trades": [],
        "summary": {},
        "source": "synthetic-hv20",
    }
    try:
        import yfinance as yf
    except Exception as exc:
        out["error"] = f"yfinance not available: {exc}"
        return out
    try:
        from datetime import date, timedelta
        end = date.today()
        # Pull enough history: need 30 extra days for HV warmup
        start = end - timedelta(days=int(weeks * 7) + 60)
        tk = yf.Ticker(symbol)
        hist = tk.history(start=start.isoformat(), end=end.isoformat(), auto_adjust=False)
        if hist is None or len(hist) < 30:
            out["error"] = "Insufficient price history"
            return out
        dates_idx = [d.date() for d in hist.index]
        closes = list(hist["Close"].astype(float))
        # Find every Monday in the dataset that has a corresponding Friday
        # 4 trading days later. We iterate and score each cycle.
        trades = []
        i = 21  # need 20 days of history for HV
        while i < len(dates_idx) - 5:
            d = dates_idx[i]
            if d.weekday() != 0:  # Monday
                i += 1
                continue
            # Friday = same week. Skip if we don't have ~4 trading days ahead
            # in the dataset.
            friday_idx = None
            for j in range(i + 1, min(i + 8, len(dates_idx))):
                if dates_idx[j].weekday() == 4:
                    friday_idx = j
                    break
            if friday_idx is None:
                i += 1
                continue
            spot = closes[i]
            spot_exp = closes[friday_idx]
            iv = _hv_realized(closes[max(0, i - 21):i + 1], window=20)
            if iv is None or iv <= 0:
                i += 1
                continue
            dte = (dates_idx[friday_idx] - d).days
            T = dte / 365.0
            # Build the strategy's legs at entry, compute entry credit/debit
            # via _bs_price, then compute expiration P/L from intrinsic
            # value at spot_exp.
            legs = _build_legs_for_backtest(strategy, spot, T, iv, target_delta)
            if not legs:
                i += 1
                continue
            entry_credit = 0.0
            for leg in legs:
                # qty positive = long (we paid premium); qty negative = short (we received)
                px = _bs_price(spot, leg["strike"], T, iv, leg["type"])
                entry_credit += -leg["sign"] * px  # short=+credit, long=−credit
            # Expiration P/L: intrinsic at spot_exp for each leg
            exp_pl = 0.0
            for leg in legs:
                intrinsic = (max(0.0, spot_exp - leg["strike"]) if leg["type"] == "call"
                             else max(0.0, leg["strike"] - spot_exp))
                # Long = +intrinsic gained; short = −intrinsic owed
                exp_pl += leg["sign"] * intrinsic
            total_pl = entry_credit + exp_pl
            trades.append({
                "monday": d.isoformat(),
                "friday": dates_idx[friday_idx].isoformat(),
                "spot_open": round(spot, 2),
                "spot_close": round(spot_exp, 2),
                "iv": round(iv, 4),
                "credit": round(entry_credit, 2),
                "exp_value": round(exp_pl, 2),
                "pl": round(total_pl, 2),
                "win": total_pl > 0,
                "strikes": [leg["strike"] for leg in legs],
            })
            i = friday_idx + 1  # jump to after this Friday
        if not trades:
            out["error"] = "No valid weekly cycles found"
            return out
        # Summary stats
        n = len(trades)
        wins = sum(1 for t in trades if t["win"])
        total_pl = sum(t["pl"] for t in trades)
        avg_pl = total_pl / n
        # Max drawdown over running cumulative P/L
        cum = 0.0; peak = 0.0; max_dd = 0.0
        for t in trades:
            cum += t["pl"]
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        # Sharpe-ish: mean / stdev, annualized roughly by sqrt(52)
        mean_pl = avg_pl
        var = sum((t["pl"] - mean_pl) ** 2 for t in trades) / (n - 1) if n > 1 else 0
        stdev = math.sqrt(var)
        sharpe = (mean_pl / stdev) * math.sqrt(52) if stdev > 0 else 0.0
        # Annualized return as % of spot using avg spot over period
        avg_spot = sum(t["spot_open"] for t in trades) / n
        annual_pct = (total_pl / avg_spot) * 100.0 * (52.0 / n) if avg_spot > 0 and n > 0 else 0.0
        out["trades"] = trades
        out["summary"] = {
            "n_cycles": n,
            "wins": wins,
            "win_rate_pct": round(100.0 * wins / n, 1),
            "total_pl": round(total_pl, 2),
            "avg_pl": round(avg_pl, 2),
            "max_drawdown": round(max_dd, 2),
            "sharpe_approx": round(sharpe, 2),
            "annual_return_pct": round(annual_pct, 2),
            "avg_spot": round(avg_spot, 2),
        }
    except Exception as exc:  # noqa: BLE001
        import traceback
        out["error"] = f"{exc}"
        out["trace"] = traceback.format_exc()[:500]
    return out


def _build_legs_for_backtest(strategy: str, spot: float, T: float,
                              iv: float, target_delta: float) -> list:
    """Returns list of {type, strike, sign} legs for the strategy.
    sign=+1 for long, -1 for short. The strikes use _strike_for_delta
    for premium-selling strategies; defined-risk strategies use natural
    wing widths.
    """
    legs = []
    # 0.20 delta short call, 0.20 delta short put — the foundation
    sc_strike = _strike_for_delta(spot, target_delta, T, iv, "call")
    sp_strike = _strike_for_delta(spot, -target_delta, T, iv, "put")
    if strategy == "covered_call":
        # Long stock + short OTM call. Stock leg approximated by holding
        # shares; for backtest purposes use put-call parity: long stock
        # = long ATM call + short ATM put. But that distorts results.
        # Simpler: for covered call we model JUST the call premium leg
        # since the stock P/L is independent of IV and a straight
        # underlying P/L plot. Note this clearly in summary.
        legs.append({"type": "call", "strike": sc_strike, "sign": -1})
    elif strategy == "cash_secured_put":
        legs.append({"type": "put", "strike": sp_strike, "sign": -1})
    elif strategy == "short_strangle":
        legs.append({"type": "call", "strike": sc_strike, "sign": -1})
        legs.append({"type": "put",  "strike": sp_strike, "sign": -1})
    elif strategy == "iron_condor":
        # Short strangle + long wings ~$5 further OTM
        wing = max(2.0, round(spot * 0.02))
        legs.append({"type": "call", "strike": sc_strike, "sign": -1})
        legs.append({"type": "call", "strike": sc_strike + wing, "sign": +1})
        legs.append({"type": "put",  "strike": sp_strike, "sign": -1})
        legs.append({"type": "put",  "strike": sp_strike - wing, "sign": +1})
    elif strategy == "bull_put_spread":
        wing = max(2.0, round(spot * 0.02))
        legs.append({"type": "put", "strike": sp_strike, "sign": -1})
        legs.append({"type": "put", "strike": sp_strike - wing, "sign": +1})
    elif strategy == "jade_lizard":
        # Short put + bear call spread. Width chosen so total credit ≥ width
        wing = max(2.0, round(spot * 0.02))
        legs.append({"type": "put",  "strike": sp_strike, "sign": -1})
        legs.append({"type": "call", "strike": sc_strike, "sign": -1})
        legs.append({"type": "call", "strike": sc_strike + wing, "sign": +1})
    elif strategy == "wheel":
        # Per-week the wheel simulates a CSP. If assigned (spot < strike at
        # expiry), real wheel switches to covered call next week. Modeling
        # the full state machine is more complex; the simplified version
        # backtests as a sequence of CSPs which captures the income side.
        legs.append({"type": "put", "strike": sp_strike, "sign": -1})
    else:
        return []
    return legs



def check_earnings(symbol: str) -> tuple[bool, str | None]:
    stock = yf.Ticker(symbol)
    try:
        earnings = stock.earnings_dates
        if earnings is None or earnings.empty:
            return False, None
        today = date.today()
        next_week_end = today + timedelta(days=(6 - today.weekday()))
        for d in earnings.index:
            ed = d.date()
            if today <= ed <= next_week_end:
                return True, ed.strftime("%Y-%m-%d")
        # also return upcoming-but-further date for display
        upcoming = [d.date() for d in earnings.index if d.date() >= today]
        return False, upcoming[0].strftime("%Y-%m-%d") if upcoming else None
    except Exception as exc:
        _log_warn(symbol, "check_earnings", exc)
        return False, None


def load_earnings_history(symbol: str, weeks: int) -> dict:
    """Return earnings dates within the lookback window plus the next future date.

    Used by the front-end to mark earnings on the candlestick and weekly
    returns charts. Always returns ISO date strings (YYYY-MM-DD).
    """
    stock = yf.Ticker(symbol)
    try:
        earnings = stock.earnings_dates
    except Exception:
        return {"past": [], "next": None}
    if earnings is None or earnings.empty:
        return {"past": [], "next": None}
    today = date.today()
    cutoff = today - timedelta(days=weeks * 7 + 14)
    past, future = [], []
    for d in earnings.index:
        try:
            ed = d.date()
        except Exception:
            continue
        if cutoff <= ed <= today:
            past.append(ed.strftime("%Y-%m-%d"))
        elif ed > today:
            future.append(ed.strftime("%Y-%m-%d"))
    past.sort()
    future.sort()
    return {"past": past, "next": future[0] if future else None}


# ═══════════════════════════════════════════════════════════════════════════
#  Build payload + bake HTML
# ═══════════════════════════════════════════════════════════════════════════
def build_scan_snapshot(symbol: str) -> dict:
    """Lightweight scan-mode snapshot for the watchlist scanner.

    Returns just the bits needed to compute a 'best strategy here, right
    now' score on the frontend. Skips daily bars, full chain rendering,
    and position valuation. Aims for ~1 second per symbol on a warm
    yfinance cache.

    Output schema:
      symbol, price, change_pct, hv20 (annualized), iv30_avg, iv_rank,
      richness, expected_high, expected_low, dte_front,
      put_safe_pct, call_safe_pct, earnings_in_days, error?
    """
    out = {"symbol": symbol}
    try:
        # ── Try Schwab first for price + chain ───────────────────────
        sc = _schwab()
        price = None
        prev = None
        # Schwab path
        if sc is not None:
            try:
                q = sc.get_quote(symbol)
                if q and q.get("last"):
                    price = float(q["last"])
                    if q.get("close_prev"):
                        prev = float(q["close_prev"])
            except Exception as exc:  # noqa: BLE001
                print(f"[scan] schwab quote failed for {symbol}: {exc}", file=sys.stderr)
        # Fallback to yfinance for any missing piece
        if price is None or prev is None:
            stock = yf.Ticker(symbol)
            hist = stock.history(period="2d", auto_adjust=False)
            if hist.empty and price is None:
                return {"symbol": symbol, "error": "no price data"}
            if price is None:
                price = float(hist["Close"].iloc[-1])
            if prev is None and not hist.empty:
                prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price
        out["price"] = round(price, 4)
        out["change_pct"] = round(((price - prev) / prev) * 100.0, 3) if prev else 0.0

        # Front expiration + ATM IV. Try Schwab chain first; fall back to yf.
        iv30_avg = None
        front_exp = None
        atm_call_iv = atm_put_iv = None
        chain_handled = False
        if sc is not None:
            try:
                full = sc.get_option_chain(symbol)
                if full and full.get("expirations"):
                    today = date.today()
                    valid = []
                    for e in full["expirations"]:
                        try:
                            ed = pd.Timestamp(e).date()
                        except Exception:
                            continue
                        if ed >= today:
                            valid.append(ed)
                    valid.sort()
                    if valid:
                        front_exp = valid[0].strftime("%Y-%m-%d")
                        legs = full["chains"].get(front_exp)
                        if legs:
                            calls = legs.get("calls") or []
                            puts = legs.get("puts") or []
                            if calls:
                                atm_c = min(calls, key=lambda r: abs(r.get("strike", 0) - price))
                                if atm_c.get("iv") not in (None, 0):
                                    atm_call_iv = float(atm_c["iv"])
                            if puts:
                                atm_p = min(puts, key=lambda r: abs(r.get("strike", 0) - price))
                                if atm_p.get("iv") not in (None, 0):
                                    atm_put_iv = float(atm_p["iv"])
                            chain_handled = True
            except Exception as exc:  # noqa: BLE001
                print(f"[scan] schwab chain failed for {symbol}: {exc}", file=sys.stderr)
        # Fallback: yfinance chain
        if not chain_handled:
            stock_for_chain = yf.Ticker(symbol)
            try:
                exps = list(stock_for_chain.options or [])
            except Exception:
                exps = []
            if exps:
                front_exp = exps[0]
                try:
                    ch = stock_for_chain.option_chain(front_exp)
                    cdf = ch.calls
                    pdf = ch.puts
                    if not cdf.empty:
                        cidx = (cdf["strike"] - price).abs().idxmin()
                        atm_call_iv = float(cdf.loc[cidx, "impliedVolatility"]) if "impliedVolatility" in cdf.columns else None
                    if not pdf.empty:
                        pidx = (pdf["strike"] - price).abs().idxmin()
                        atm_put_iv = float(pdf.loc[pidx, "impliedVolatility"]) if "impliedVolatility" in pdf.columns else None
                except Exception:
                    pass
        if atm_call_iv and atm_put_iv:
            iv30_avg = (atm_call_iv + atm_put_iv) / 2.0
        elif atm_call_iv:
            iv30_avg = atm_call_iv
        elif atm_put_iv:
            iv30_avg = atm_put_iv

        # DTE to front expiration
        if front_exp:
            try:
                fe_dt = datetime.strptime(front_exp, "%Y-%m-%d").date()
                out["dte_front"] = max(1, (fe_dt - date.today()).days)
            except Exception:
                out["dte_front"] = 7
        else:
            out["dte_front"] = 7

        out["iv30_avg"] = round(iv30_avg, 4) if iv30_avg else None

        # yfinance still needed for HV history + earnings dates regardless
        # of which source provided price/chain.
        stock = yf.Ticker(symbol)

        # 20-day HV (annualized).
        try:
            hv_hist = stock.history(period="2mo", auto_adjust=False)["Close"]
            rets = hv_hist.pct_change().dropna()
            if len(rets) >= 20:
                hv20 = float(rets.tail(20).std() * (252 ** 0.5))
                out["hv20"] = round(hv20, 4)
        except Exception:
            out["hv20"] = None

        # Richness = IV / HV. Above 1.2 = juicy premium.
        if out.get("iv30_avg") and out.get("hv20"):
            out["richness"] = round(out["iv30_avg"] / out["hv20"], 3)
        else:
            out["richness"] = None

        # ── IV rank / IV percentile (v1.14) ─────────────────────────
        # Persist today's iv30_avg, then compute rank and percentile
        # from the local history. Best-effort: any failure leaves
        # iv_rank/iv_pct null and the rest of the snapshot unaffected.
        out["iv_rank"] = None
        out["iv_pct"] = None
        out["iv_rank_days"] = 0
        if out.get("iv30_avg"):
            try:
                _iv_history_append(symbol, out["iv30_avg"])
                hist = _iv_history_load(symbol)
                rank = _iv_history_compute_rank(hist, out["iv30_avg"])
                out["iv_rank"] = rank["iv_rank"]
                out["iv_pct"] = rank["iv_pct"]
                out["iv_rank_days"] = rank["iv_rank_days"]
            except Exception as exc:  # noqa: BLE001
                print(f"[scan] iv_rank failed for {symbol}: {exc}", file=sys.stderr)

        # Expected weekly range from front IV: spot * (1 ± IV * sqrt(dte/365))
        if iv30_avg and out.get("dte_front"):
            move = iv30_avg * ((out["dte_front"] / 365.0) ** 0.5)
            out["expected_high"] = round(price * (1 + move), 2)
            out["expected_low"] = round(price * (1 - move), 2)
        else:
            out["expected_high"] = None
            out["expected_low"] = None

        # Safety percentages — distance from current price to the expected
        # range bounds, used by scoring to prefer setups with cushion.
        if out.get("expected_high") and out.get("expected_low"):
            out["call_safe_pct"] = round(((out["expected_high"] - price) / price) * 100.0, 2)
            out["put_safe_pct"]  = round(((price - out["expected_low"])  / price) * 100.0, 2)
        else:
            out["call_safe_pct"] = None
            out["put_safe_pct"]  = None

        # Earnings proximity
        try:
            ed = stock.earnings_dates
            if ed is not None and not ed.empty:
                today = date.today()
                future = [d.date() for d in ed.index if d.date() >= today]
                if future:
                    out["earnings_in_days"] = (min(future) - today).days
                else:
                    out["earnings_in_days"] = None
            else:
                out["earnings_in_days"] = None
        except Exception:
            out["earnings_in_days"] = None

    except Exception as exc:  # noqa: BLE001
        return {"symbol": symbol, "error": str(exc)}
    return out


@_ttl_memoize(60)         # basing profile only shifts on new bars
def build_basing_profile(symbol: str, lookback_weeks: int = 12) -> dict:
    """Build a mean-reversion / basing profile for today's session.

    Combines:
      - Today's % move vs yesterday's close
      - Typical same-weekday close-to-close behavior over lookback
      - Time-at-price profile (TPO-like) from 1-min bars
      - Volume-at-price profile from 1-min bars
      - Value Area High/Low (70% volume) around POC

    The frontend uses these to assess if the stock is stretched beyond
    its normal weekday close behavior AND holding a base at the POC.
    """
    out: dict = {"symbol": symbol}
    sc = _schwab()
    if sc is None:
        return {"symbol": symbol, "error": "schwab not configured"}

    # ── Today's move ────────────────────────────────────────────────
    quote = sc.get_quote(symbol)
    if not quote or quote.get("last") is None:
        return {"symbol": symbol, "error": "no live quote"}
    last_price = float(quote["last"])
    prev_close = float(quote.get("close_prev") or 0) or None
    today_pct = ((last_price - prev_close) / prev_close * 100.0) if prev_close else None

    # ── Typical same-weekday close-to-prior-close behavior ──────────
    # Use stock's daily history for the lookback window. Group by weekday,
    # compute distribution of close-to-prior-close % for that weekday.
    today_dow = datetime.now(_ET if _ET else None).weekday()  # 0=Mon
    daily_bars = sc.get_price_history(symbol, days=max(60, lookback_weeks * 7 + 14))
    typical = {"median": None, "p10": None, "p90": None, "samples": 0}
    if daily_bars and len(daily_bars) > 5:
        same_dow_returns = []
        for i in range(1, len(daily_bars)):
            try:
                d_iso = daily_bars[i]["date"][:10]
                d_obj = datetime.strptime(d_iso, "%Y-%m-%d").date()
                if d_obj.weekday() != today_dow:
                    continue
                prior = float(daily_bars[i - 1]["close"])
                close = float(daily_bars[i]["close"])
                if prior <= 0:
                    continue
                pct = (close - prior) / prior * 100.0
                same_dow_returns.append(pct)
            except (KeyError, ValueError, TypeError):
                continue
        # Last lookback_weeks observations (newest first means slice tail)
        same_dow_returns = same_dow_returns[-lookback_weeks:]
        if same_dow_returns:
            sorted_r = sorted(same_dow_returns)
            n = len(sorted_r)
            typical = {
                "median": sorted_r[n // 2],
                "p10": sorted_r[max(0, int(n * 0.10) - 1)] if n >= 10 else sorted_r[0],
                "p90": sorted_r[min(n - 1, int(n * 0.90))] if n >= 10 else sorted_r[-1],
                "samples": n,
            }

    # ── Stretched? ──────────────────────────────────────────────────
    stretched = False
    if today_pct is not None and typical["p10"] is not None and typical["p90"] is not None:
        max_normal = max(abs(typical["p10"]), abs(typical["p90"]))
        stretched = abs(today_pct) > 1.5 * max_normal

    # ── Intraday profile from 1-min bars ────────────────────────────
    bars = sc.get_intraday(symbol)
    # Defensive filter: keep only bars whose timestamp falls on today's
    # date in ET. Schwab can return yesterday's bars before market open
    # or in the first minutes after open if their cache hasn't rolled.
    if bars and _ET is not None:
        today_et = datetime.now(_ET).date()
        bars = [
            b for b in bars
            if b.get("ts")
            and datetime.fromtimestamp(b["ts"] / 1000.0, tz=_ET).date() == today_et
        ]
    bins_out: list[dict] = []
    poc_price = None
    tpo_price = None
    val_high = None
    val_low = None
    holding_base = False
    session_open = None
    session_high = None
    session_low = None

    if bars and len(bars) > 5:
        # Bin width: tighter of (0.2% of price) or ATR-ish proxy from
        # bars themselves. Round to a sensible cents step.
        bin_width = max(0.05, round(last_price * 0.002, 2))
        # Find session min/max from bars to size the histogram
        all_low = min((b["low"] for b in bars if b.get("low")), default=last_price)
        all_high = max((b["high"] for b in bars if b.get("high")), default=last_price)
        # Build bins. Use price floor to bin index.
        from math import floor
        def bin_idx(p: float) -> int:
            return int(floor((p - all_low) / bin_width))
        n_bins = bin_idx(all_high) + 1
        if n_bins <= 0 or n_bins > 5000:  # sanity cap
            n_bins = 1
        time_buckets = [0.0] * n_bins
        vol_buckets = [0.0] * n_bins
        # 2D heatmap: rows = price bins, cols = 15-min time buckets across
        # the regular 9:30am-4:00pm session (390 min / 15 = 26 cols).
        # Each cell holds minutes spent at that price during that window.
        # Time buckets sized by minute-of-session derived from each bar's
        # epoch-ms timestamp converted to ET.
        TIME_BIN_MIN = 15
        N_TIME_BINS = 26  # 9:30am..4:00pm in 15-min slots
        heatmap = [[0.0] * N_TIME_BINS for _ in range(n_bins)]
        # Parallel volume heatmap: same shape, holds shares traded per cell.
        # Lets the frontend swap between Time and Volume views with one click.
        vol_heatmap = [[0.0] * N_TIME_BINS for _ in range(n_bins)]
        # Session OHLC for today's bars (in chronological order). The first
        # bar's open is today's open; running min/max of high/low across all
        # bars is the session high/low.
        try:
            session_open = float(bars[0].get("open") or last_price)
        except Exception:
            session_open = last_price
        highs = [b.get("high") for b in bars if b.get("high")]
        lows = [b.get("low") for b in bars if b.get("low")]
        session_high = float(max(highs)) if highs else last_price
        session_low = float(min(lows)) if lows else last_price
        try:
            from zoneinfo import ZoneInfo as _ZI_local
            _ET_local = _ZI_local("America/New_York")
        except Exception:
            _ET_local = None
        def _time_bin_idx(epoch_ms: int) -> int:
            try:
                ts = epoch_ms / 1000.0
                if _ET_local is not None:
                    dt = datetime.fromtimestamp(ts, tz=_ET_local)
                else:
                    dt = datetime.fromtimestamp(ts)
                # minutes since 9:30am ET
                mins = (dt.hour * 60 + dt.minute) - (9 * 60 + 30)
                if mins < 0:
                    return 0
                idx = mins // TIME_BIN_MIN
                if idx >= N_TIME_BINS:
                    return N_TIME_BINS - 1
                return int(idx)
            except Exception:
                return 0
        for b in bars:
            lo = b.get("low") or 0
            hi = b.get("high") or 0
            vol = b.get("volume") or 0
            ts_ms = b.get("ts") or 0
            if hi <= 0 or lo <= 0 or hi < lo:
                continue
            i_lo = max(0, bin_idx(lo))
            i_hi = min(n_bins - 1, bin_idx(hi))
            n_touched = max(1, i_hi - i_lo + 1)
            # Each 1-min bar contributes 1 minute total, distributed
            # evenly across the bins it touched. Volume similarly split.
            t_per = 1.0 / n_touched
            v_per = vol / n_touched
            t_bin = _time_bin_idx(ts_ms)
            for j in range(i_lo, i_hi + 1):
                time_buckets[j] += t_per
                vol_buckets[j] += v_per
                heatmap[j][t_bin] += t_per
                vol_heatmap[j][t_bin] += v_per

        # Build output bin list
        for i in range(n_bins):
            price_lo = all_low + i * bin_width
            bins_out.append({
                "price": round(price_lo + bin_width / 2, 4),
                "time_min": round(time_buckets[i], 2),
                "volume": int(vol_buckets[i]),
                # Per-time-bucket minutes spent at this price (length 26).
                # Frontend renders as heatmap row: 9:30 → 16:00 in 15-min cells.
                "heat": [round(v, 3) for v in heatmap[i]],
                # Per-time-bucket shares traded at this price (length 26).
                # Same shape as heat — frontend toggles between the two.
                "vol_heat": [int(v) for v in vol_heatmap[i]],
            })
        # POC = bin with most volume
        if vol_buckets and any(v > 0 for v in vol_buckets):
            poc_idx = max(range(n_bins), key=lambda i: vol_buckets[i])
            poc_price = round(all_low + poc_idx * bin_width + bin_width / 2, 4)
            # Value area: expand from POC outward until ≥70% volume captured
            total_vol = sum(vol_buckets)
            if total_vol > 0:
                target = total_vol * 0.70
                lo_i = hi_i = poc_idx
                captured = vol_buckets[poc_idx]
                while captured < target and (lo_i > 0 or hi_i < n_bins - 1):
                    above = vol_buckets[hi_i + 1] if hi_i < n_bins - 1 else -1
                    below = vol_buckets[lo_i - 1] if lo_i > 0 else -1
                    if above >= below:
                        hi_i += 1
                        captured += above
                    else:
                        lo_i -= 1
                        captured += below
                val_low = round(all_low + lo_i * bin_width, 4)
                val_high = round(all_low + (hi_i + 1) * bin_width, 4)
        # TPO = bin with most time
        if time_buckets and any(t > 0 for t in time_buckets):
            tpo_idx = max(range(n_bins), key=lambda i: time_buckets[i])
            tpo_price = round(all_low + tpo_idx * bin_width + bin_width / 2, 4)

        # Holding base: last 30 min of bars stay within 0.5% of POC
        if poc_price and len(bars) >= 30:
            tail = bars[-30:]
            in_band = sum(
                1 for b in tail
                if b.get("close") is not None
                and abs(float(b["close"]) - poc_price) / poc_price <= 0.005
            )
            holding_base = in_band >= 25  # at least 25 of last 30

    bounce_signal = bool(stretched and holding_base and today_pct is not None and today_pct < 0)

    # ── Sell-now-or-wait verdict ─────────────────────────────────────
    # Single one-line recommendation built from existing signals:
    #   - typical_dow.p90  → upper bound of normal Wed (etc.) close
    #   - today_pct        → where today is
    #   - holding_base     → last 30 min within 0.5% of POC
    #   - stretched        → today_pct outside 1.5x normal range
    # Logic mirrors the basic covered-call timing rules:
    #   * Stretched up + holding base = good time to sell calls
    #   * Stretched up + still trending = wait, sell partial
    #   * Inside normal range, no signal = wait
    #   * Stretched DOWN = avoid (premium will be poor + further upside likely)
    verdict = None
    reason = None
    if today_pct is not None and typical.get("p90") is not None and typical.get("p10") is not None:
        # Local copy of the day-of-week label since `out` hasn't been
        # updated with `today_dow` yet at this point in the function.
        dow_label = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][today_dow]
        p90 = typical["p90"]
        p10 = typical["p10"]
        median = typical.get("median") or 0.0
        # Where is today within the typical Mon-Fri range, expressed as 0..100%
        # using p10 as floor and p90 as ceiling. >100% = above 90th percentile.
        if p90 > p10:
            stretch_pct = (today_pct - p10) / (p90 - p10) * 100.0
        else:
            stretch_pct = 50.0
        stretch_pct = round(stretch_pct, 1)
        if today_pct < 0 and stretched:
            verdict = "Avoid"
            reason = f"Stretched DOWN ({today_pct:+.2f}%). Premium poor and risk of further weakness."
        elif stretch_pct >= 90 and holding_base:
            verdict = "Sell now"
            reason = f"At {stretch_pct:.0f}% of typical {dow_label} range and holding base near POC."
        elif stretch_pct >= 90:
            verdict = "Sell partial"
            reason = f"At {stretch_pct:.0f}% of typical {dow_label} range but momentum still active."
        elif stretch_pct >= 70:
            verdict = "Sell partial"
            reason = f"At {stretch_pct:.0f}% of typical {dow_label} range. Reasonable premium zone."
        elif stretch_pct >= 40:
            verdict = "Wait"
            reason = f"Only {stretch_pct:.0f}% of typical {dow_label} range. Better levels likely."
        else:
            verdict = "Wait"
            reason = f"At {stretch_pct:.0f}% of typical {dow_label} range. Too early. Expect more upside."

    out.update({
        "last_price": last_price,
        "prev_close": prev_close,
        "today_pct": today_pct,
        "today_dow": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][today_dow],
        "typical_dow": typical,
        "stretched": stretched,
        "bins": bins_out,
        "poc_price": poc_price,
        "tpo_price": tpo_price,
        "value_area_high": val_high,
        "value_area_low": val_low,
        "holding_base": holding_base,
        "bounce_signal": bounce_signal,
        # Heatmap metadata: 15-min cells across the 9:30am-4:00pm session.
        "time_bin_min": 15,
        "n_time_bins": 26,
        # Session reference values
        "session_open": session_open,
        "session_high": session_high,
        "session_low": session_low,
        "session_volume": int(sum(b.get("volume") or 0 for b in (bars or []))),
        # One-line covered-call timing verdict
        "verdict": verdict,
        "verdict_reason": reason,
    })
    return out


def build_weekly_range(symbol: str) -> dict:
    """Compute the implied weekly range and suggested 0.20 delta strikes
    for selling OTM calls and puts. Uses ATM straddle price as the
    market's implied weekly move — same logic the earnings ladder uses
    for IV crush analysis.

    Output schema:
      symbol, spot, monday_open?, dte, atm_iv, implied_move_pct,
      implied_high, implied_low, call_strike_20d, put_strike_20d,
      call_credit_20d, put_credit_20d, total_credit_20d,
      preferred_strategy?, error?

    Notes on accuracy:
      • Implied move = (ATM call mid + ATM put mid) / spot. Defines the
        1-standard-deviation range pricing.
      • implied_high/low = spot ± straddle_price.
      • 0.20 delta strikes located via interpolation across the chain.
      • Falls back gracefully on missing data — partial output is OK.
    """
    out: dict = {"symbol": symbol}
    try:
        # Try Schwab first via the same wrapper layer used elsewhere.
        sc = _schwab()
        spot = None
        chain = None
        front_exp_str = None
        # ── Fetch quote + chain ──────────────────────────────────────
        if sc is not None:
            try:
                q = sc.get_quote(symbol)
                if q and q.get("last"):
                    spot = float(q["last"])
                full = sc.get_option_chain(symbol)
                if full and full.get("expirations"):
                    # Use closest weekly Friday >= today
                    today = date.today()
                    valid = []
                    for e in full["expirations"]:
                        try:
                            ed = pd.Timestamp(e).date()
                        except Exception:
                            continue
                        if ed.weekday() == 4 and ed >= today:
                            valid.append(ed)
                    valid.sort()
                    if valid:
                        front_exp_str = valid[0].strftime("%Y-%m-%d")
                        chain = full["chains"].get(front_exp_str)
                        if not spot and full.get("underlying", {}).get("last"):
                            spot = float(full["underlying"]["last"])
            except Exception as exc:  # noqa: BLE001
                print(f"[weekly_range] schwab fetch failed for {symbol}: {exc}", file=sys.stderr)
        # Fallback: yfinance
        if not chain or not spot:
            stock = yf.Ticker(symbol)
            hist = stock.history(period="2d", auto_adjust=False)
            if hist.empty:
                return {"symbol": symbol, "error": "no price data"}
            spot = float(hist["Close"].iloc[-1])
            try:
                exps = list(stock.options or [])
            except Exception:
                return {"symbol": symbol, "error": "no chain"}
            today = date.today()
            valid = []
            for e in exps:
                try:
                    ed = pd.Timestamp(e).date()
                except Exception:
                    continue
                if ed.weekday() == 4 and ed >= today:
                    valid.append(ed)
            valid.sort()
            if not valid:
                return {"symbol": symbol, "spot": round(spot, 2), "error": "no weekly Friday"}
            front_exp_str = valid[0].strftime("%Y-%m-%d")
            try:
                opt = stock.option_chain(front_exp_str)
            except Exception:
                return {"symbol": symbol, "spot": round(spot, 2), "error": "chain fetch failed"}
            # Convert yf DataFrames to the same shape Schwab returns
            calls = []
            puts = []
            for _, r in opt.calls.iterrows():
                calls.append({
                    "strike": float(r.get("strike", 0) or 0),
                    "bid": float(r.get("bid") or 0),
                    "ask": float(r.get("ask") or 0),
                    "last": float(r.get("lastPrice") or 0),
                    "iv": float(r.get("impliedVolatility") or 0),
                })
            for _, r in opt.puts.iterrows():
                puts.append({
                    "strike": float(r.get("strike", 0) or 0),
                    "bid": float(r.get("bid") or 0),
                    "ask": float(r.get("ask") or 0),
                    "last": float(r.get("lastPrice") or 0),
                    "iv": float(r.get("impliedVolatility") or 0),
                })
            chain = {"calls": sorted(calls, key=lambda r: r["strike"]),
                     "puts":  sorted(puts,  key=lambda r: r["strike"])}

        out["spot"] = round(spot, 2)
        out["expiration"] = front_exp_str
        try:
            fe = datetime.strptime(front_exp_str, "%Y-%m-%d").date()
            dte = max(1, (fe - date.today()).days)
        except Exception:
            dte = 5
        out["dte"] = dte

        # Monday open this week (for projection baseline)
        try:
            today = date.today()
            monday = today - timedelta(days=today.weekday())
            if sc is not None:
                bars = sc.get_price_history(symbol, days=10)
                if bars:
                    for b in bars:
                        if b.get("date") == monday.isoformat():
                            out["monday_open"] = round(float(b.get("open", 0)), 2)
                            break
            if "monday_open" not in out:
                stk = yf.Ticker(symbol)
                wk = stk.history(period="7d", auto_adjust=False)
                if not wk.empty:
                    wk.index = pd.to_datetime(wk.index).tz_localize(None)
                    mon = wk[wk.index.date == monday]
                    if not mon.empty:
                        out["monday_open"] = round(float(mon["Open"].iloc[0]), 2)
        except Exception:
            pass

        calls = chain.get("calls") or []
        puts = chain.get("puts") or []
        if not calls or not puts:
            out["error"] = "empty chain"
            return out

        # Helper: bid/ask mid, fall back to last
        def mid(row):
            b = float(row.get("bid") or 0)
            a = float(row.get("ask") or 0)
            if b > 0 and a > 0:
                return (b + a) / 2.0
            l = float(row.get("last") or 0)
            return l if l > 0 else 0.0

        # ── ATM straddle = implied move ─────────────────────────────
        # Closest call strike to spot
        atm_call = min(calls, key=lambda r: abs((r.get("strike") or 0) - spot))
        atm_put = min(puts, key=lambda r: abs((r.get("strike") or 0) - spot))
        atm_call_px = mid(atm_call)
        atm_put_px = mid(atm_put)
        atm_iv = None
        try:
            ivs = []
            if atm_call.get("iv"): ivs.append(float(atm_call["iv"]))
            if atm_put.get("iv"):  ivs.append(float(atm_put["iv"]))
            if ivs: atm_iv = sum(ivs) / len(ivs)
        except Exception:
            pass
        out["atm_iv"] = round(atm_iv, 4) if atm_iv else None

        if atm_call_px > 0 and atm_put_px > 0:
            straddle = atm_call_px + atm_put_px
            out["implied_move_dollars"] = round(straddle, 2)
            out["implied_move_pct"] = round((straddle / spot) * 100.0, 2)
            out["implied_high"] = round(spot + straddle, 2)
            out["implied_low"] = round(spot - straddle, 2)
        elif atm_iv:
            # Last-resort: theoretical straddle from IV + sqrt(t)
            move = atm_iv * ((dte / 365.0) ** 0.5)
            out["implied_move_pct"] = round(move * 100.0, 2)
            out["implied_high"] = round(spot * (1 + move), 2)
            out["implied_low"] = round(spot * (1 - move), 2)

        # ── 0.20 delta strikes ──────────────────────────────────────
        # Many chains include delta in greeks (Schwab does); yf doesn't.
        # If delta is missing on a row, fall back to BS using the row's IV.
        T = dte / 365.0

        def with_delta(rows, side):
            out_rows = []
            for r in rows:
                strike = float(r.get("strike") or 0)
                if strike <= 0: continue
                d = r.get("delta")
                if d is None or d != d:  # NaN check
                    iv = float(r.get("iv") or 0)
                    if iv <= 0: continue
                    d = _bs_delta(spot, strike, T, iv, side)
                out_rows.append((strike, abs(float(d)), r))
            return out_rows

        call_with = with_delta(calls, "call")
        put_with = with_delta(puts, "put")

        def find_target(rows, target_abs_delta=0.20):
            """Find the strike whose abs delta is closest to target.
            Among multiple, prefer the one furthest OTM (safer)."""
            if not rows: return None
            best = min(rows, key=lambda r: abs(r[1] - target_abs_delta))
            return best

        call_pick = find_target(call_with, 0.20)
        put_pick = find_target(put_with, 0.20)
        if call_pick:
            strike, d, row = call_pick
            out["call_strike_20d"] = strike
            out["call_delta"] = round(d, 3)
            out["call_credit_20d"] = round(mid(row), 2)
        if put_pick:
            strike, d, row = put_pick
            out["put_strike_20d"] = strike
            out["put_delta"] = round(d, 3)
            out["put_credit_20d"] = round(mid(row), 2)
        if "call_credit_20d" in out and "put_credit_20d" in out:
            out["total_credit_20d"] = round(out["call_credit_20d"] + out["put_credit_20d"], 2)

    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out



# Last good payload per (symbol, weeks, baseline, expiration), served
# with a stale flag when a fresh build_payload fails (v1.39). Capped so
# memory stays bounded across many tickers.
_TICKER_LAST_GOOD: dict = {}
_TICKER_LG_LOCK = threading.Lock()
_TICKER_LG_MAX = 40
# Short-TTL "fresh" cache: re-selecting a symbol you just viewed (tab flip,
# back-and-forth) serves the built payload instantly instead of rebuilding it.
# The live price is overlaid separately by the frontend's quote polling, so a
# few seconds of staleness on the heavy payload (bars/chain/earnings) is fine.
_TICKER_FRESH: dict = {}
_TICKER_TTL = 10.0  # seconds
# Per-symbol swing analysis is a full year of pivots + a UW flow read; cache so
# flipping back to a symbol (or the Patterns tab re-mounting) is instant.
_SWINGS_CACHE: dict = {}
_SWINGS_LOCK = threading.Lock()
_SWINGS_TTL = 90.0  # seconds

def build_payload(
    ticker: str,
    weeks: int,
    friday_baseline: bool,
    target_exp: str | None = None,
) -> dict:
    weekly = load_weekly_data(ticker, weeks, friday_baseline)
    if weekly.empty:
        # CRITICAL: this used to call sys.exit() — fine when run as a CLI
        # but fatal in server mode because SystemExit isn't caught by
        # `except Exception` and kills the handler thread, leaving the
        # client with "Empty reply from server" or "Unexpected token '<'".
        # Raise a regular ValueError so the endpoint's try/except returns
        # a clean JSON 500 to the frontend.
        raise ValueError(f"No data for {ticker}.")
    target_fri = next_friday()

    # These six loaders are independent and each is a network round-trip
    # (Schwab / yfinance / option chain). Run them concurrently so symbol-load
    # latency is the slowest single call, not the sum of all of them. yfinance
    # builds an independent Ticker per call and the Schwab client is read-mostly
    # with its own TTL cache, so this is safe.
    def _load_info():
        try:
            return yf.Ticker(ticker).info or {}
        except Exception:
            return {}

    with ThreadPoolExecutor(max_workers=6) as _ex:
        f_current = _ex.submit(load_current_week, ticker, friday_baseline)
        f_daily = _ex.submit(load_daily, ticker, 260)
        f_chain = _ex.submit(load_option_chain, ticker, target_fri, target_exp)
        f_earn = _ex.submit(check_earnings, ticker)
        f_earnhist = _ex.submit(load_earnings_history, ticker, weeks)
        f_info = _ex.submit(_load_info)
        current = f_current.result()
        daily = f_daily.result()
        calls, puts, exp, expirations = f_chain.result()
        has_earnings, earnings_date = f_earn.result()
        earnings_history = f_earnhist.result()
        info = f_info.result()
    name = info.get("shortName") or info.get("longName") or ticker
    sector = info.get("sector") or ""
    # Dividend yield (v1.33). Computed from the dollar dividend rate over
    # the price, both in dollars, so there is no fraction-versus-percent
    # ambiguity. The old approach trusted info["dividendYield"], whose
    # format flipped between yfinance versions and turned AAPL's 0.35
    # percent into 35 percent. dividendRate is forward annual dollars;
    # trailingAnnualDividendRate is the trailing dollar amount. Price and
    # rate come from the same info dict so they stay consistent.
    div_yield = None
    try:
        drate = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
        iprice = (info.get("currentPrice") or info.get("regularMarketPrice")
                  or info.get("previousClose"))
        if drate and iprice and float(iprice) > 0:
            div_yield = round(float(drate) / float(iprice) * 100, 2)
    except Exception:
        div_yield = None

    # Valuation multiples (v1.70) for the sidebar.
    def _num(x, dp=1):
        try:
            v = float(x)
            return round(v, dp) if v == v else None    # drop NaN
        except (TypeError, ValueError):
            return None
    pe = _num(info.get("trailingPE"))
    forward_pe = _num(info.get("forwardPE"))

    # Days until the next earnings report (earnings_date is the next upcoming
    # date from check_earnings; may be None for ETFs / no coverage).
    days_to_earnings = None
    if earnings_date:
        try:
            ed = datetime.strptime(earnings_date, "%Y-%m-%d").date()
            days_to_earnings = (ed - date.today()).days
        except Exception:
            days_to_earnings = None

    rows = weekly.to_dict("records")

    cur_price = current["current_price"] if current else float(rows[-1]["friday_close"])
    atm = round_strike(cur_price)

    # Vol Rank — percentile rank of current realized vol within the past
    # 252 trading days. Uses rolling 30-day annualized stdev of log returns.
    # This is "HV Rank" — a free proxy for IV Rank since we don't have a
    # year of historical option IV. Useful for "is vol elevated right now"
    # which is the question premium-sellers actually need answered.
    vol_rank = None
    vol_pct = None
    hv_current = None
    if len(daily) >= 31:
        import math
        closes_full = [d["close"] for d in daily]
        # Reuse the daily bars already fetched by load_daily(260) — about a
        # year of trading days — instead of re-downloading another full year of
        # history here. Eliminates one upstream round-trip on every symbol load.
        try:
            if len(closes_full) >= 31:
                full_closes = closes_full
                # Log returns
                logrets = []
                for i in range(1, len(full_closes)):
                    a, b = full_closes[i - 1], full_closes[i]
                    if a > 0 and b > 0:
                        logrets.append(math.log(b / a))
                    else:
                        logrets.append(0.0)
                # Rolling 30-day annualized stdev
                window = 30
                hv_series = []
                for i in range(window - 1, len(logrets)):
                    w = logrets[i - window + 1:i + 1]
                    mean = sum(w) / len(w)
                    var = sum((x - mean) ** 2 for x in w) / max(1, len(w) - 1)
                    hv_series.append(math.sqrt(var) * math.sqrt(252))
                if hv_series:
                    hv_current = hv_series[-1]
                    sorted_hv = sorted(hv_series)
                    rank_pos = sum(1 for v in sorted_hv if v <= hv_current)
                    vol_pct = (rank_pos / len(sorted_hv)) * 100.0
                    # IV Rank-style: where is current within (min, max)?
                    hv_min, hv_max = sorted_hv[0], sorted_hv[-1]
                    if hv_max > hv_min:
                        vol_rank = ((hv_current - hv_min) / (hv_max - hv_min)) * 100.0
                    else:
                        vol_rank = 50.0
        except Exception:
            pass

    return {
        "ticker": ticker.upper(),
        "fetchedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "expiration": exp or target_fri.strftime("%Y-%m-%d"),
        "expirations": expirations,
        "baselineMode": "friday" if friday_baseline else "monday",
        "rows": rows,
        "daily": daily,
        "current": {
            "current": cur_price,
            "baseline": current["baseline_price"] if current else float(rows[-1]["friday_close"]),
            "monday_open": current["monday_open"] if current else cur_price,
            "name": name,
            "sector": sector,
            "dividend_yield": div_yield,
            "pe": pe,
            "forward_pe": forward_pe,
            "earnings": has_earnings,
            "earningsDate": earnings_date,
            "next_earnings": earnings_date,
            "days_to_earnings": days_to_earnings,
            "week_start": current["week_start"] if current else target_fri.strftime("%Y-%m-%d"),
        },
        "chain": {"calls": calls, "puts": puts, "atm": atm},
        "volRank": vol_rank,
        "volPct": vol_pct,
        "hvCurrent": hv_current,
        "earningsHistory": earnings_history,
    }


SHIM_TEMPLATE = """
<script id="__live_data" type="application/json">__JSON__</script>
<script>
(function () {
  const CACHE = {};
  const PRESETS = {};

  function hydrate(rows) {
    return rows.map(r => Object.assign({}, r, { week_start: new Date(r.week_start) }));
  }
  function hydrateDaily(daily) {
    return daily.map(d => Object.assign({}, d, { date: new Date(d.date) }));
  }

  function install(payload) {
    const sym = payload.ticker;
    const rows = hydrate(payload.rows);
    const daily = hydrateDaily(payload.daily);
    const cur = Object.assign({}, payload.current, {
      week_start: new Date(payload.current.week_start),
    });
    CACHE[sym] = { rows, daily, current: cur, chain: payload.chain, expiration: payload.expiration };
    // Keep CACHE bounded — drop the least recently inserted symbols once we
    // exceed the limit. 30 is plenty for a typical day's rotation.
    const _MAX_CACHE = 30;
    const _keys = Object.keys(CACHE);
    if (_keys.length > _MAX_CACHE) {
      // Drop oldest (insertion order is preserved in JS objects)
      _keys.slice(0, _keys.length - _MAX_CACHE).forEach(k => { delete CACHE[k]; });
    }
    PRESETS[sym] = {
      name: cur.name, current: cur.current, baseline: cur.baseline,
      vol: 0.02, drift: 0.001, earningsThisWeek: !!cur.earnings, sector: cur.sector,
    };
    window.__LIVE = payload;
    const banner = document.getElementById("__live_banner");
    if (banner) {
      banner.textContent = "● LIVE · " + sym + " · " + payload.fetchedAt + " · baseline: " + payload.baselineMode;
    }
    return CACHE[sym];
  }

  function buildWeekly(symbol)      { const e = CACHE[symbol]; return e ? { rows: e.rows, current: e.current } : { rows: [], current: { current: 0, baseline: 0, name: symbol, sector: "", earnings: false } }; }
  function buildDaily(symbol)       { return (CACHE[symbol] || {}).daily || []; }
  function buildOptionChain(symbol) { return (CACHE[symbol] || {}).chain || { calls: [], puts: [], atm: 0 }; }
  function nextFriday() {
    const sym = (window.__LIVE && window.__LIVE.ticker) || Object.keys(CACHE)[0];
    const exp = (CACHE[sym] || {}).expiration;
    return exp ? new Date(exp + "T16:00:00") : new Date();
  }
  function median(arr) {
    if (!arr.length) return 0;
    const s = [...arr].sort((a, b) => a - b);
    const m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  function mode(arr) {
    const c = new Map();
    for (const v of arr) c.set(v, (c.get(v) || 0) + 1);
    let best = null, bc = -1;
    for (const [k, n] of c) if (n > bc) { best = k; bc = n; }
    return best;
  }
  function roundStrike(px) {
    if (px <= 0) return 0;
    const inc = px < 25 ? 0.5 : px < 200 ? 1.0 : 5.0;
    return Math.round(px / inc) * inc;
  }

  window.MockData = {
    PRESETS, buildWeekly, buildDaily, buildOptionChain,
    nextFriday, median, mode, roundStrike,
  };
  window.__installLive = install;

  install(JSON.parse(document.getElementById("__live_data").textContent));
})();
</script>
"""


def bake(payload: dict) -> str:
    if not TEMPLATE.exists():
        sys.exit(f"Could not find dashboard template at {TEMPLATE}")
    html = TEMPLATE.read_text(encoding="utf-8")
    json_blob = _dumps(payload, default=str)
    shim = SHIM_TEMPLATE.replace("__JSON__", json_blob)
    if 'src="data.js"' in html:
        html = html.replace('<script src="data.js"></script>', shim)
    else:
        html = html.replace("</head>", shim + "\n</head>")

    earnings_warn = ""
    if payload["current"].get("earnings"):
        earnings_warn = (
            f'<div style="position:fixed;top:48px;right:8px;z-index:9999;'
            f'background:#7f1d1d;color:#fecaca;padding:6px 10px;border-radius:8px;'
            f'font:600 11px ui-monospace,monospace;border:1px solid #991b1b">'
            f'⚠ EARNINGS THIS WEEK · {payload["current"].get("earningsDate","")}</div>'
        )
    banner = (
        f'<div id="__live_banner" style="position:fixed;top:8px;right:8px;z-index:9999;'
        f'background:#0f172a;color:#a3e635;padding:6px 10px;border-radius:8px;'
        f'font:500 11px ui-monospace,monospace;border:1px solid #1e293b">'
        f'● LIVE · {payload["ticker"]} · {payload["fetchedAt"]} · '
        f'baseline: {payload["baselineMode"]}</div>{earnings_warn}'
    )
    html = html.replace("<body>", "<body>" + banner)
    return html


# ═══════════════════════════════════════════════════════════════════════════
#  Live server: serves the dashboard + a /api/ticker?symbol=XXX endpoint
#  the page calls when the user types a new ticker.
# ═══════════════════════════════════════════════════════════════════════════
# ─── Ticker autocomplete index ────────────────────────────────────────────
# SEC publishes an authoritative list of all US-listed company tickers. The
# file is roughly 1MB and is fetched once on first use, then cached in memory.
# ETFs and indices are not in the SEC list (they are not corporate filers),
# so we ship a hand maintained list of the popular ones below. If the SEC
# fetch fails (network blocked, rate limit), the equity list and ETF list
# below still serve as a usable fallback covering the most traded names.

# Hand maintained list of ~250 of the most actively traded US equities,
# weighted toward names with weekly options. Not exhaustive. Used as a
# fallback when the SEC fetch fails, and merged with the SEC index when it
# succeeds (dedupe keeps the SEC entry with the proper company name).
_TOP_EQUITIES = [
    # Mega cap tech
    ("AAPL", "Apple Inc."), ("MSFT", "Microsoft Corp."), ("NVDA", "NVIDIA Corp."),
    ("GOOGL", "Alphabet Inc. Class A"), ("GOOG", "Alphabet Inc. Class C"),
    ("AMZN", "Amazon.com Inc."), ("META", "Meta Platforms Inc."),
    ("TSLA", "Tesla Inc."), ("AVGO", "Broadcom Inc."), ("ORCL", "Oracle Corp."),
    ("CRM", "Salesforce Inc."), ("ADBE", "Adobe Inc."), ("NFLX", "Netflix Inc."),
    ("AMD", "Advanced Micro Devices"), ("INTC", "Intel Corp."), ("QCOM", "Qualcomm Inc."),
    ("CSCO", "Cisco Systems"), ("IBM", "IBM Corp."), ("TXN", "Texas Instruments"),
    ("MU", "Micron Technology"), ("AMAT", "Applied Materials"), ("LRCX", "Lam Research"),
    ("KLAC", "KLA Corp."), ("ASML", "ASML Holding"), ("TSM", "Taiwan Semiconductor"),
    ("ARM", "Arm Holdings"), ("PANW", "Palo Alto Networks"), ("CRWD", "CrowdStrike"),
    ("FTNT", "Fortinet"), ("ZS", "Zscaler"), ("NET", "Cloudflare"), ("SNOW", "Snowflake"),
    ("DDOG", "Datadog"), ("MDB", "MongoDB"), ("WDAY", "Workday"), ("NOW", "ServiceNow"),
    ("INTU", "Intuit"), ("UBER", "Uber Technologies"), ("LYFT", "Lyft"),
    ("SHOP", "Shopify"), ("SQ", "Block Inc."), ("PYPL", "PayPal"), ("COIN", "Coinbase"),
    ("HOOD", "Robinhood"), ("SOFI", "SoFi Technologies"), ("AFRM", "Affirm Holdings"),
    ("RBLX", "Roblox"), ("U", "Unity Software"), ("DOCU", "DocuSign"),
    ("ZM", "Zoom Communications"), ("TEAM", "Atlassian"), ("OKTA", "Okta"),
    ("TWLO", "Twilio"), ("PLTR", "Palantir"), ("SMCI", "Super Micro Computer"),
    ("DELL", "Dell Technologies"), ("HPQ", "HP Inc."), ("HPE", "Hewlett Packard Enterprise"),
    ("ANET", "Arista Networks"), ("MRVL", "Marvell Technology"), ("ON", "ON Semiconductor"),
    ("LITE", "Lumentum Holdings"), ("COHR", "Coherent Corp."), ("WOLF", "Wolfspeed"),
    # Banks and financials
    ("JPM", "JPMorgan Chase"), ("BAC", "Bank of America"), ("WFC", "Wells Fargo"),
    ("GS", "Goldman Sachs"), ("MS", "Morgan Stanley"), ("C", "Citigroup"),
    ("USB", "US Bancorp"), ("PNC", "PNC Financial"), ("TFC", "Truist Financial"),
    ("SCHW", "Charles Schwab"), ("BLK", "BlackRock"), ("AXP", "American Express"),
    ("V", "Visa Inc."), ("MA", "Mastercard"), ("BRK.B", "Berkshire Hathaway B"),
    ("ICE", "Intercontinental Exchange"), ("CME", "CME Group"), ("SPGI", "S&P Global"),
    ("MCO", "Moody's"), ("COF", "Capital One"), ("DFS", "Discover Financial"),
    ("AIG", "American International Group"), ("MET", "MetLife"), ("PRU", "Prudential Financial"),
    ("ALL", "Allstate"), ("PGR", "Progressive"), ("TRV", "Travelers"), ("CB", "Chubb"),
    # Healthcare and pharma
    ("UNH", "UnitedHealth Group"), ("JNJ", "Johnson & Johnson"), ("LLY", "Eli Lilly"),
    ("PFE", "Pfizer"), ("ABBV", "AbbVie"), ("MRK", "Merck"), ("TMO", "Thermo Fisher"),
    ("ABT", "Abbott Laboratories"), ("DHR", "Danaher"), ("BMY", "Bristol-Myers Squibb"),
    ("AMGN", "Amgen"), ("GILD", "Gilead Sciences"), ("VRTX", "Vertex Pharmaceuticals"),
    ("REGN", "Regeneron"), ("BIIB", "Biogen"), ("MRNA", "Moderna"), ("BNTX", "BioNTech"),
    ("CVS", "CVS Health"), ("CI", "Cigna"), ("HUM", "Humana"), ("ELV", "Elevance Health"),
    ("MDT", "Medtronic"), ("BSX", "Boston Scientific"), ("SYK", "Stryker"),
    ("ISRG", "Intuitive Surgical"), ("HCA", "HCA Healthcare"), ("ZTS", "Zoetis"),
    ("NVO", "Novo Nordisk"), ("ALNY", "Alnylam Pharmaceuticals"), ("CRSP", "CRISPR Therapeutics"),
    # Consumer and retail
    ("WMT", "Walmart Inc."), ("COST", "Costco Wholesale"), ("HD", "Home Depot"),
    ("LOW", "Lowe's Companies"), ("TGT", "Target Corp."), ("KR", "Kroger"),
    ("DG", "Dollar General"), ("DLTR", "Dollar Tree"), ("BBY", "Best Buy"),
    ("TJX", "TJX Companies"), ("ROST", "Ross Stores"), ("ULTA", "Ulta Beauty"),
    ("LULU", "Lululemon Athletica"), ("NKE", "Nike Inc."), ("SBUX", "Starbucks"),
    ("MCD", "McDonald's"), ("CMG", "Chipotle Mexican Grill"), ("YUM", "Yum! Brands"),
    ("DRI", "Darden Restaurants"), ("DPZ", "Domino's Pizza"), ("WING", "Wingstop"),
    ("PG", "Procter & Gamble"), ("KO", "Coca-Cola"), ("PEP", "PepsiCo"),
    ("MDLZ", "Mondelez International"), ("CL", "Colgate-Palmolive"), ("KMB", "Kimberly-Clark"),
    ("EL", "Estee Lauder"), ("MO", "Altria Group"), ("PM", "Philip Morris International"),
    ("BTI", "British American Tobacco"), ("DEO", "Diageo"), ("STZ", "Constellation Brands"),
    ("BUD", "Anheuser-Busch InBev"), ("TAP", "Molson Coors"), ("MNST", "Monster Beverage"),
    ("KDP", "Keurig Dr Pepper"), ("CELH", "Celsius Holdings"),
    # Industrials, energy, autos
    ("BA", "Boeing"), ("CAT", "Caterpillar"), ("DE", "Deere & Company"),
    ("HON", "Honeywell"), ("LMT", "Lockheed Martin"), ("RTX", "RTX Corp."),
    ("NOC", "Northrop Grumman"), ("GD", "General Dynamics"), ("GE", "General Electric"),
    ("MMM", "3M"), ("EMR", "Emerson Electric"), ("ETN", "Eaton Corp."),
    ("UPS", "United Parcel Service"), ("FDX", "FedEx"), ("UNP", "Union Pacific"),
    ("CSX", "CSX Corp."), ("NSC", "Norfolk Southern"), ("ODFL", "Old Dominion Freight"),
    ("DAL", "Delta Air Lines"), ("UAL", "United Airlines"), ("AAL", "American Airlines"),
    ("LUV", "Southwest Airlines"), ("SAVE", "Spirit Airlines"),
    ("F", "Ford Motor"), ("GM", "General Motors"), ("STLA", "Stellantis"),
    ("RIVN", "Rivian"), ("LCID", "Lucid Group"), ("NIO", "NIO Inc."),
    ("LI", "Li Auto"), ("XPEV", "XPeng"), ("FSR", "Fisker"),
    ("XOM", "Exxon Mobil"), ("CVX", "Chevron"), ("COP", "ConocoPhillips"),
    ("OXY", "Occidental Petroleum"), ("MPC", "Marathon Petroleum"), ("PSX", "Phillips 66"),
    ("VLO", "Valero Energy"), ("EOG", "EOG Resources"), ("PXD", "Pioneer Natural Resources"),
    ("SLB", "Schlumberger"), ("HAL", "Halliburton"), ("BKR", "Baker Hughes"),
    ("FANG", "Diamondback Energy"), ("DVN", "Devon Energy"), ("APA", "APA Corp."),
    ("CCJ", "Cameco"), ("URA", "Global X Uranium"),
    # Comms, media, entertainment
    ("DIS", "Walt Disney"), ("CMCSA", "Comcast"), ("VZ", "Verizon"),
    ("T", "AT&T"), ("TMUS", "T-Mobile US"), ("CHTR", "Charter Communications"),
    ("WBD", "Warner Bros. Discovery"), ("PARA", "Paramount Global"), ("FOX", "Fox Corp."),
    ("FOXA", "Fox Corp. A"), ("PINS", "Pinterest"), ("SNAP", "Snap Inc."),
    ("SPOT", "Spotify Technology"), ("RDDT", "Reddit Inc."), ("BABA", "Alibaba"),
    ("PDD", "PDD Holdings"), ("JD", "JD.com"), ("BIDU", "Baidu"), ("NTES", "NetEase"),
    # Real estate and utilities
    ("PLD", "Prologis"), ("AMT", "American Tower"), ("CCI", "Crown Castle"),
    ("EQIX", "Equinix"), ("PSA", "Public Storage"), ("O", "Realty Income"),
    ("SPG", "Simon Property Group"), ("WELL", "Welltower"), ("AVB", "AvalonBay Communities"),
    ("EQR", "Equity Residential"), ("VICI", "VICI Properties"),
    ("NEE", "NextEra Energy"), ("DUK", "Duke Energy"), ("SO", "Southern Co."),
    ("AEP", "American Electric Power"), ("D", "Dominion Energy"), ("EXC", "Exelon"),
    ("XEL", "Xcel Energy"), ("ED", "Consolidated Edison"), ("WEC", "WEC Energy Group"),
    # Materials
    ("LIN", "Linde plc"), ("APD", "Air Products"), ("SHW", "Sherwin-Williams"),
    ("DD", "DuPont"), ("DOW", "Dow Inc."), ("FCX", "Freeport-McMoRan"),
    ("NUE", "Nucor"), ("STLD", "Steel Dynamics"), ("CLF", "Cleveland-Cliffs"),
    ("X", "United States Steel"), ("AA", "Alcoa"), ("NEM", "Newmont"),
    ("GOLD", "Barrick Gold"), ("AEM", "Agnico Eagle Mines"),
    # Crypto-adjacent and high-volume retail favorites
    ("MSTR", "MicroStrategy"), ("MARA", "Marathon Digital"), ("RIOT", "Riot Platforms"),
    ("CLSK", "CleanSpark"), ("WULF", "TeraWulf"),
    ("AMC", "AMC Entertainment"), ("GME", "GameStop"), ("BBBY", "Bed Bath & Beyond"),
    ("BB", "BlackBerry"), ("NOK", "Nokia"), ("FUBO", "FuboTV"), ("DKNG", "DraftKings"),
    ("PENN", "Penn Entertainment"), ("MGM", "MGM Resorts"), ("LVS", "Las Vegas Sands"),
    ("WYNN", "Wynn Resorts"), ("CZR", "Caesars Entertainment"),
    ("CAR", "Avis Budget Group"), ("HTZ", "Hertz Global Holdings"),
    ("MAR", "Marriott International"), ("HLT", "Hilton Worldwide"),
    ("H", "Hyatt Hotels"), ("CHH", "Choice Hotels"),
    # ADRs and others popular for options
    ("SE", "Sea Limited"), ("MELI", "MercadoLibre"), ("GLOB", "Globant"),
    ("CHWY", "Chewy"), ("ETSY", "Etsy"), ("EBAY", "eBay"), ("W", "Wayfair"),
    ("ABNB", "Airbnb"), ("BKNG", "Booking Holdings"), ("EXPE", "Expedia Group"),
    # Additional S&P 500 names that didn't fit categories above
    ("CARR", "Carrier Global"), ("OTIS", "Otis Worldwide"),
    ("TT", "Trane Technologies"), ("PH", "Parker Hannifin"),
    ("ITW", "Illinois Tool Works"), ("ROK", "Rockwell Automation"),
    ("EMR", "Emerson Electric"), ("PCAR", "PACCAR"),
    ("IR", "Ingersoll Rand"), ("DOV", "Dover Corp."),
    ("FAST", "Fastenal"), ("GWW", "W.W. Grainger"),
    ("URI", "United Rentals"), ("WM", "Waste Management"),
    ("RSG", "Republic Services"), ("WMB", "Williams Companies"),
    ("EPD", "Enterprise Products Partners"), ("ET", "Energy Transfer"),
    ("MPLX", "MPLX LP"), ("KMI", "Kinder Morgan"), ("OKE", "ONEOK"),
    ("HES", "Hess Corp."), ("CTRA", "Coterra Energy"),
    ("EQT", "EQT Corp."), ("MRO", "Marathon Oil"),
    ("CHK", "Chesapeake Energy"), ("AR", "Antero Resources"),
    ("RRC", "Range Resources"), ("SM", "SM Energy"),
    ("SWN", "Southwestern Energy"), ("PR", "Permian Resources"),
    ("CIVI", "Civitas Resources"), ("CPE", "Callon Petroleum"),
    # Financials, payments, insurance
    ("WBA", "Walgreens Boots Alliance"), ("MCK", "McKesson"),
    ("CAH", "Cardinal Health"), ("ABC", "AmerisourceBergen"),
    ("HUM", "Humana"), ("CNC", "Centene"), ("MOH", "Molina Healthcare"),
    ("ICUI", "ICU Medical"), ("EW", "Edwards Lifesciences"),
    ("BAX", "Baxter International"), ("BDX", "Becton Dickinson"),
    ("HOLX", "Hologic"), ("RMD", "ResMed"),
    ("DXCM", "DexCom"), ("PODD", "Insulet"),
    ("ALGN", "Align Technology"), ("WAT", "Waters Corp."),
    ("MTD", "Mettler-Toledo"), ("IDXX", "IDEXX Laboratories"),
    ("A", "Agilent Technologies"), ("ILMN", "Illumina"),
    ("CRL", "Charles River Laboratories"), ("LH", "LabCorp"),
    ("DGX", "Quest Diagnostics"), ("IQV", "IQVIA Holdings"),
    ("PKI", "PerkinElmer"), ("TFX", "Teleflex"),
    ("STE", "STERIS plc"), ("WST", "West Pharmaceutical Services"),
    # Banks, insurance, asset managers
    ("RJF", "Raymond James Financial"), ("LPLA", "LPL Financial"),
    ("TROW", "T. Rowe Price"), ("NTRS", "Northern Trust"),
    ("STT", "State Street"), ("BK", "Bank of New York Mellon"),
    ("FITB", "Fifth Third Bancorp"), ("RF", "Regions Financial"),
    ("HBAN", "Huntington Bancshares"), ("KEY", "KeyCorp"),
    ("CFG", "Citizens Financial"), ("MTB", "M&T Bank"),
    ("ZION", "Zions Bancorporation"), ("CMA", "Comerica"),
    ("FCNCA", "First Citizens BancShares"), ("PNFP", "Pinnacle Financial Partners"),
    ("WAL", "Western Alliance Bancorp"),
    ("AON", "Aon plc"), ("MMC", "Marsh & McLennan"),
    ("AJG", "Arthur J. Gallagher"), ("WTW", "Willis Towers Watson"),
    ("BRO", "Brown & Brown"), ("AFL", "Aflac"),
    ("HIG", "Hartford Financial"), ("LNC", "Lincoln National"),
    ("PFG", "Principal Financial"), ("UNM", "Unum Group"),
    ("RGA", "Reinsurance Group of America"), ("EG", "Everest Group"),
    ("RNR", "RenaissanceRe Holdings"),
    # Industrials, transports
    ("EXPD", "Expeditors International"), ("CHRW", "C.H. Robinson"),
    ("XPO", "XPO Inc."), ("KNX", "Knight-Swift Transportation"),
    ("WERN", "Werner Enterprises"), ("LSTR", "Landstar System"),
    ("R", "Ryder System"), ("JBHT", "J.B. Hunt Transport"),
    ("GD", "General Dynamics"), ("HII", "Huntington Ingalls Industries"),
    ("LDOS", "Leidos Holdings"), ("LHX", "L3Harris Technologies"),
    ("BAH", "Booz Allen Hamilton"), ("CACI", "CACI International"),
    ("KBR", "KBR Inc."), ("TDG", "TransDigm Group"),
    ("HEI", "HEICO Corp."), ("AXON", "Axon Enterprise"),
    ("ALK", "Alaska Air Group"), ("JBLU", "JetBlue Airways"),
    ("HA", "Hawaiian Holdings"), ("ALGT", "Allegiant Travel"),
    ("CPA", "Copa Holdings"), ("RYAAY", "Ryanair Holdings"),
    # Tech and software extras
    ("ADSK", "Autodesk"), ("ANSS", "Ansys"), ("CDNS", "Cadence Design Systems"),
    ("SNPS", "Synopsys"), ("KLAC", "KLA Corp."), ("CTSH", "Cognizant"),
    ("INFY", "Infosys"), ("WIT", "Wipro"), ("ACN", "Accenture plc"),
    ("EPAM", "EPAM Systems"), ("DXC", "DXC Technology"),
    ("LDOS", "Leidos"), ("FFIV", "F5 Inc."), ("AKAM", "Akamai Technologies"),
    ("VRSN", "VeriSign"), ("GDDY", "GoDaddy"), ("WIX", "Wix.com"),
    ("CRWV", "CoreWeave"), ("NBIS", "Nebius Group"),
    ("APP", "AppLovin"), ("UNITY", "Unity Software"),
    ("PATH", "UiPath"), ("AI", "C3.ai"), ("BBAI", "BigBear.ai"),
    ("SOUN", "SoundHound AI"), ("IONQ", "IonQ"),
    ("RGTI", "Rigetti Computing"), ("QUBT", "Quantum Computing"),
    ("MARA", "Marathon Digital"), ("RIOT", "Riot Platforms"),
    ("CIFR", "Cipher Mining"), ("BITF", "Bitfarms"),
    ("HUT", "Hut 8"), ("CAN", "Canaan"),
    # Nuclear, SMR, energy transition
    ("OKLO", "Oklo Inc."), ("SMR", "NuScale Power"),
    ("NNE", "Nano Nuclear Energy"), ("LEU", "Centrus Energy"),
    ("UEC", "Uranium Energy"), ("UUUU", "Energy Fuels"),
    ("DNN", "Denison Mines"), ("URG", "Ur-Energy"),
    ("VST", "Vistra Corp."), ("NRG", "NRG Energy"),
    ("CEG", "Constellation Energy"), ("PCG", "PG&E"),
    ("ENPH", "Enphase Energy"), ("SEDG", "SolarEdge Technologies"),
    ("FSLR", "First Solar"), ("RUN", "Sunrun"),
    ("NOVA", "Sunnova Energy"), ("ARRY", "Array Technologies"),
    ("SHLS", "Shoals Technologies"), ("PLUG", "Plug Power"),
    ("BE", "Bloom Energy"), ("BLDP", "Ballard Power Systems"),
    ("FCEL", "FuelCell Energy"), ("CWEN", "Clearway Energy"),
    # Defense, aerospace contractors (extras)
    ("PLD", "Prologis"), ("DLR", "Digital Realty Trust"),
    ("WELL", "Welltower"), ("VTR", "Ventas"),
    ("PEAK", "Healthpeak Properties"), ("EQR", "Equity Residential"),
    ("ESS", "Essex Property Trust"), ("MAA", "Mid-America Apartment Communities"),
    ("UDR", "UDR Inc."), ("CPT", "Camden Property Trust"),
    ("INVH", "Invitation Homes"), ("AMH", "American Homes 4 Rent"),
    ("HST", "Host Hotels & Resorts"), ("PK", "Park Hotels & Resorts"),
    ("RHP", "Ryman Hospitality"), ("STAG", "STAG Industrial"),
    ("REXR", "Rexford Industrial Realty"), ("EGP", "EastGroup Properties"),
    # Misc S&P 500 / Russell 1000 names commonly traded for options
    ("DHI", "D.R. Horton"), ("LEN", "Lennar"), ("PHM", "PulteGroup"),
    ("NVR", "NVR Inc."), ("TOL", "Toll Brothers"), ("KBH", "KB Home"),
    ("MTH", "Meritage Homes"), ("TPH", "Tri Pointe Homes"),
    ("BLDR", "Builders FirstSource"), ("BCC", "Boise Cascade"),
    ("LPX", "Louisiana-Pacific"), ("WY", "Weyerhaeuser"),
    ("EXP", "Eagle Materials"), ("VMC", "Vulcan Materials"),
    ("MLM", "Martin Marietta Materials"), ("MAS", "Masco Corp."),
    ("FBHS", "Fortune Brands"), ("AOS", "A.O. Smith"),
    ("WSO", "Watsco Inc."), ("POOL", "Pool Corp."),
    ("LECO", "Lincoln Electric"),
    # Consumer discretionary extras
    ("VFC", "VF Corp."), ("HBI", "Hanesbrands"), ("PVH", "PVH Corp."),
    ("RL", "Ralph Lauren"), ("TPR", "Tapestry"), ("CPRI", "Capri Holdings"),
    ("DECK", "Deckers Outdoor"), ("CROX", "Crocs"), ("SKX", "Skechers"),
    ("UAA", "Under Armour"), ("FL", "Foot Locker"), ("DKS", "Dick's Sporting Goods"),
    ("ASO", "Academy Sports + Outdoors"), ("HIBB", "Hibbett Sports"),
    ("BBY", "Best Buy"), ("M", "Macy's"), ("KSS", "Kohl's"),
    ("JWN", "Nordstrom"), ("GPS", "Gap Inc."), ("ANF", "Abercrombie & Fitch"),
    ("AEO", "American Eagle Outfitters"), ("URBN", "Urban Outfitters"),
    ("BURL", "Burlington Stores"), ("GO", "Grocery Outlet"),
    ("FIVE", "Five Below"), ("OLLI", "Ollie's Bargain Outlet"),
    ("PSMT", "PriceSmart"), ("BJ", "BJ's Wholesale Club"),
    # Media and streaming extras
    ("TKO", "TKO Group Holdings"), ("FUBO", "FuboTV"),
    ("CURI", "CuriosityStream"), ("DLB", "Dolby Laboratories"),
    ("IMAX", "IMAX Corp."), ("AMC", "AMC Entertainment"),
    ("CNK", "Cinemark Holdings"), ("MAR", "Marriott"),
    ("LYV", "Live Nation Entertainment"), ("MSGS", "Madison Square Garden Sports"),
    ("MSGE", "Madison Square Garden Entertainment"),
    ("WWE", "TKO Group (WWE)"), ("EDR", "Endeavor Group"),
    # Industrials and growth
    ("DAL", "Delta Air Lines"), ("UAL", "United Airlines"),
    ("LUV", "Southwest Airlines"), ("AAL", "American Airlines"),
    # Cannabis and other specialty
    ("TLRY", "Tilray Brands"), ("CGC", "Canopy Growth"),
    ("ACB", "Aurora Cannabis"), ("CRON", "Cronos Group"),
    ("SNDL", "SNDL Inc."), ("CURLF", "Curaleaf Holdings"),
    ("GTBIF", "Green Thumb Industries"), ("TCNNF", "Trulieve Cannabis"),
    # Fintech, insurtech
    ("LMND", "Lemonade"), ("ROOT", "Root Inc."), ("METC", "Ramaco Resources"),
    ("UPST", "Upstart Holdings"), ("LC", "LendingClub"),
    ("FICO", "Fair Isaac"), ("EFX", "Equifax"),
    ("TRU", "TransUnion"), ("MORN", "Morningstar"),
    ("FDS", "FactSet Research Systems"), ("MSCI", "MSCI Inc."),
    ("NDAQ", "Nasdaq Inc."), ("CBOE", "CBOE Global Markets"),
    ("MKTX", "MarketAxess Holdings"), ("TW", "Tradeweb Markets"),
    # Comm services + smaller IT
    ("FOXF", "Fox Factory Holding"), ("ROL", "Rollins Inc."),
    ("CMG", "Chipotle Mexican Grill"), ("DPZ", "Domino's Pizza"),
    ("WING", "Wingstop"), ("TXRH", "Texas Roadhouse"),
    ("DRI", "Darden Restaurants"), ("CAVA", "CAVA Group"),
    ("SHAK", "Shake Shack"), ("EAT", "Brinker International"),
]


_ETF_LIST = [
    {"symbol": "SPY",  "name": "SPDR S&P 500 ETF Trust", "type": "ETF"},
    {"symbol": "QQQ",  "name": "Invesco QQQ Trust", "type": "ETF"},
    {"symbol": "IWM",  "name": "iShares Russell 2000 ETF", "type": "ETF"},
    {"symbol": "DIA",  "name": "SPDR Dow Jones Industrial Average ETF", "type": "ETF"},
    {"symbol": "VOO",  "name": "Vanguard S&P 500 ETF", "type": "ETF"},
    {"symbol": "VTI",  "name": "Vanguard Total Stock Market ETF", "type": "ETF"},
    {"symbol": "VEA",  "name": "Vanguard FTSE Developed Markets ETF", "type": "ETF"},
    {"symbol": "VWO",  "name": "Vanguard FTSE Emerging Markets ETF", "type": "ETF"},
    {"symbol": "EFA",  "name": "iShares MSCI EAFE ETF", "type": "ETF"},
    {"symbol": "EEM",  "name": "iShares MSCI Emerging Markets ETF", "type": "ETF"},
    {"symbol": "TLT",  "name": "iShares 20+ Year Treasury Bond ETF", "type": "ETF"},
    {"symbol": "IEF",  "name": "iShares 7-10 Year Treasury Bond ETF", "type": "ETF"},
    {"symbol": "SHY",  "name": "iShares 1-3 Year Treasury Bond ETF", "type": "ETF"},
    {"symbol": "AGG",  "name": "iShares Core US Aggregate Bond ETF", "type": "ETF"},
    {"symbol": "BND",  "name": "Vanguard Total Bond Market ETF", "type": "ETF"},
    {"symbol": "HYG",  "name": "iShares iBoxx High Yield Corporate Bond ETF", "type": "ETF"},
    {"symbol": "LQD",  "name": "iShares iBoxx Investment Grade Corporate Bond ETF", "type": "ETF"},
    {"symbol": "GLD",  "name": "SPDR Gold Shares", "type": "ETF"},
    {"symbol": "IAU",  "name": "iShares Gold Trust", "type": "ETF"},
    {"symbol": "SLV",  "name": "iShares Silver Trust", "type": "ETF"},
    {"symbol": "GDX",  "name": "VanEck Gold Miners ETF", "type": "ETF"},
    {"symbol": "GDXJ", "name": "VanEck Junior Gold Miners ETF", "type": "ETF"},
    {"symbol": "USO",  "name": "United States Oil Fund", "type": "ETF"},
    {"symbol": "UNG",  "name": "United States Natural Gas Fund", "type": "ETF"},
    {"symbol": "XLF",  "name": "Financial Select Sector SPDR Fund", "type": "ETF"},
    {"symbol": "XLK",  "name": "Technology Select Sector SPDR Fund", "type": "ETF"},
    {"symbol": "XLE",  "name": "Energy Select Sector SPDR Fund", "type": "ETF"},
    {"symbol": "XLV",  "name": "Health Care Select Sector SPDR Fund", "type": "ETF"},
    {"symbol": "XLU",  "name": "Utilities Select Sector SPDR Fund", "type": "ETF"},
    {"symbol": "XLY",  "name": "Consumer Discretionary Select Sector SPDR Fund", "type": "ETF"},
    {"symbol": "XLP",  "name": "Consumer Staples Select Sector SPDR Fund", "type": "ETF"},
    {"symbol": "XLI",  "name": "Industrial Select Sector SPDR Fund", "type": "ETF"},
    {"symbol": "XLB",  "name": "Materials Select Sector SPDR Fund", "type": "ETF"},
    {"symbol": "XLC",  "name": "Communication Services Select Sector SPDR Fund", "type": "ETF"},
    {"symbol": "XLRE", "name": "Real Estate Select Sector SPDR Fund", "type": "ETF"},
    {"symbol": "ARKK", "name": "ARK Innovation ETF", "type": "ETF"},
    {"symbol": "ARKG", "name": "ARK Genomic Revolution ETF", "type": "ETF"},
    {"symbol": "ARKW", "name": "ARK Next Generation Internet ETF", "type": "ETF"},
    {"symbol": "TQQQ", "name": "ProShares UltraPro QQQ", "type": "ETF"},
    {"symbol": "SQQQ", "name": "ProShares UltraPro Short QQQ", "type": "ETF"},
    {"symbol": "SPXL", "name": "Direxion Daily S&P 500 Bull 3x", "type": "ETF"},
    {"symbol": "SPXS", "name": "Direxion Daily S&P 500 Bear 3x", "type": "ETF"},
    {"symbol": "SOXL", "name": "Direxion Daily Semiconductor Bull 3x", "type": "ETF"},
    {"symbol": "SOXS", "name": "Direxion Daily Semiconductor Bear 3x", "type": "ETF"},
    {"symbol": "TNA",  "name": "Direxion Daily Small Cap Bull 3x", "type": "ETF"},
    {"symbol": "TZA",  "name": "Direxion Daily Small Cap Bear 3x", "type": "ETF"},
    {"symbol": "FAS",  "name": "Direxion Daily Financial Bull 3x", "type": "ETF"},
    {"symbol": "FAZ",  "name": "Direxion Daily Financial Bear 3x", "type": "ETF"},
    {"symbol": "UVXY", "name": "ProShares Ultra VIX Short Term Futures", "type": "ETF"},
    {"symbol": "VXX",  "name": "iPath Series B S&P 500 VIX Short Term Futures ETN", "type": "ETF"},
    {"symbol": "BITO", "name": "ProShares Bitcoin Strategy ETF", "type": "ETF"},
    {"symbol": "IBIT", "name": "iShares Bitcoin Trust ETF", "type": "ETF"},
    {"symbol": "FBTC", "name": "Fidelity Wise Origin Bitcoin Fund", "type": "ETF"},
    {"symbol": "GBTC", "name": "Grayscale Bitcoin Trust", "type": "ETF"},
    {"symbol": "ETHE", "name": "Grayscale Ethereum Trust", "type": "ETF"},
    {"symbol": "MDY",  "name": "SPDR S&P MidCap 400 ETF", "type": "ETF"},
    {"symbol": "SCHD", "name": "Schwab US Dividend Equity ETF", "type": "ETF"},
    {"symbol": "JEPI", "name": "JPMorgan Equity Premium Income ETF", "type": "ETF"},
    {"symbol": "JEPQ", "name": "JPMorgan Nasdaq Equity Premium Income ETF", "type": "ETF"},
    {"symbol": "EWZ",  "name": "iShares MSCI Brazil ETF", "type": "ETF"},
    {"symbol": "FXI",  "name": "iShares China Large-Cap ETF", "type": "ETF"},
    {"symbol": "EWJ",  "name": "iShares MSCI Japan ETF", "type": "ETF"},
    {"symbol": "INDA", "name": "iShares MSCI India ETF", "type": "ETF"},
    {"symbol": "ITB",  "name": "iShares US Home Construction ETF", "type": "ETF"},
    {"symbol": "XHB",  "name": "SPDR S&P Homebuilders ETF", "type": "ETF"},
    {"symbol": "SMH",  "name": "VanEck Semiconductor ETF", "type": "ETF"},
    {"symbol": "SOXX", "name": "iShares Semiconductor ETF", "type": "ETF"},
    {"symbol": "IBB",  "name": "iShares Biotechnology ETF", "type": "ETF"},
    {"symbol": "XBI",  "name": "SPDR S&P Biotech ETF", "type": "ETF"},
    {"symbol": "KRE",  "name": "SPDR S&P Regional Banking ETF", "type": "ETF"},
    {"symbol": "KBE",  "name": "SPDR S&P Bank ETF", "type": "ETF"},
    {"symbol": "VNQ",  "name": "Vanguard Real Estate ETF", "type": "ETF"},
]

_TICKER_INDEX: list[dict] | None = None


def load_ticker_index() -> list[dict]:
    """Lazy load. SEC company_tickers.json + hand maintained equity & ETF
    fallbacks. Cached after first call."""
    global _TICKER_INDEX
    if _TICKER_INDEX is not None:
        return _TICKER_INDEX
    items: list[dict] = []
    seen: set[str] = set()

    # Hand maintained equity fallback first, so even if SEC is unreachable
    # we still resolve AAPL, NVDA, WMT, etc. The popular flag boosts these
    # in autocomplete ranking ahead of obscure same-prefix tickers.
    for sym, name in _TOP_EQUITIES:
        items.append({"symbol": sym, "name": name, "type": "EQUITY", "popular": True})
        seen.add(sym)

    try:
        # SEC requires a User-Agent in the format "Name email@example.com" per
        # https://www.sec.gov/os/accessing-edgar-data. Browser-style UAs are
        # blocked with 403. Generic project tags also fail.
        url = "https://www.sec.gov/files/company_tickers.json"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "WeeklyOptionsTimer admin@example.com",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        added = 0
        for v in data.values():
            sym = (v.get("ticker") or "").upper().strip()
            name = (v.get("title") or "").strip()
            if sym and sym not in seen:
                items.append({"symbol": sym, "name": name, "type": "EQUITY"})
                seen.add(sym)
                added += 1
        sys.stderr.write(f"loaded {added} additional tickers from SEC ({len(items)} total)\n")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"SEC ticker fetch failed: {exc}. "
            f"Using hand maintained list of {len(items)} equities + ETFs only.\n"
        )
    for etf in _ETF_LIST:
        if etf["symbol"] not in seen:
            items.append({**etf, "popular": True})
            seen.add(etf["symbol"])
    _TICKER_INDEX = items
    return items


def yahoo_search(q: str) -> list[dict]:
    """Local autocomplete over the SEC + ETF + curated equity index.

    Function name is preserved for backwards compatibility with the HTTP
    handler. Despite the name, this no longer calls Yahoo, which started
    blocking non browser callers.
    """
    q = (q or "").upper().strip()
    if not q:
        return []
    items = load_ticker_index()
    prefix: list[dict] = []
    contains: list[dict] = []
    name_match: list[dict] = []
    q_lower = q.lower()
    for it in items:
        sym = it["symbol"]
        if sym == q:
            prefix.insert(0, it)  # exact symbol match always first
        elif sym.startswith(q):
            prefix.append(it)
        elif q in sym:
            contains.append(it)
        elif q_lower in (it.get("name") or "").lower():
            name_match.append(it)

    # Within each bucket, sort by:
    #   1. exact match flag
    #   2. popular flag — curated names (AAPL, NVDA, WMT, popular ETFs) rank
    #      ahead of obscure same-prefix tickers like NVA or AAA*
    #   3. type rank — equities outrank ETFs which outrank mutual funds
    #   4. shorter symbols (more likely to be the canonical name)
    #   5. alphabetical
    type_rank = {"EQUITY": 0, "ETF": 1, "MUTUALFUND": 2}
    def sort_key(x):
        return (
            0 if x["symbol"] == q else 1,
            0 if x.get("popular") else 1,
            type_rank.get(x.get("type") or "", 3),
            len(x["symbol"]),
            x["symbol"],
        )
    prefix.sort(key=sort_key)
    contains.sort(key=sort_key)
    name_match.sort(key=sort_key)
    return (prefix + contains + name_match)[:12]


# ─── Persistence layer ─────────────────────────────────────────────
# All ~/.jerry-dashboard state I/O lives in storage.py (v1.39 split).
# Every name is re-imported here so existing call sites and tests keep
# working unchanged.
from storage import (  # noqa: F401
    _stable_data_dir,
    _STABLE_DIR,
    _WATCHLIST_PATH,
    _SERVER_LOG_PATH,
    _SERVER_LOG_LOCK,
    _SERVER_LOG_MAX,
    _request_log,
    _IV_HISTORY_DIR,
    _IV_HISTORY_MAX,
    _iv_history_path,
    _iv_history_load,
    _iv_history_append,
    _iv_history_compute_rank,
    _DISMISSED_ALERTS_PATH,
    _safe_parse_date,
    _load_dismissed_alerts,
    _save_dismissed_alerts,
    _TRADE_JOURNAL_PATH,
    _load_trade_journal,
    _save_trade_journal,
    _FADE_STAGES_PATH,
    _load_fade_stages,
    _save_fade_stages,
    _SENT_ALERTS_PATH,
    _PUSH_RESEND_HOURS,
    _load_sent_alerts,
    _save_sent_alerts,
    _load_watchlist,
    _save_watchlist,
    _validate_watchlist_payload,
    _watchlist_diag,
    _load_prefs,
    _save_prefs,
    _validate_prefs_payload,
)







# ── Closed-position trade journal (v1.15) ──────────────────────────
# Persists every closed position so the win-rate tracker can compute
# realized P/L, win rate, average premium, best and worst trades.
# Append-only with idempotent dedupe on a composite key.


# ── Level Reprice live quote + saved fades (v1.28) ─────────────────


def _live_contract_mid(symbol: str, strike: float, kind: str,
                       target_exp: str | None):
    """Live mid for one option contract via the SAME chain path the rest
    of the app uses (load_option_chain → Schwab primary, yfinance
    fallback, extended hours honored). Returns dict with mid, bid, ask,
    last, iv, source, exp, or None when the contract is not found.
    Mid is (bid+ask)/2 falling back to last, identical to the chain."""
    try:
        tgt = pd.Timestamp(target_exp).date() if target_exp else None
    except Exception:
        tgt = None
    target_fri = next_friday()
    if tgt is not None and tgt >= date.today():
        target_fri = tgt
    calls, puts, exp_str, _exps = load_option_chain(
        symbol, target_fri, target_exp)
    legs = calls if kind == "call" else puts
    if not legs:
        return None
    # Nearest strike match (chains are pre-sorted by strike).
    best = min(legs, key=lambda r: abs(float(r.get("strike", 0)) - strike))
    if abs(float(best.get("strike", 0)) - strike) > 0.51:
        return None  # no contract at (or adjacent to) that strike
    bid = float(best.get("bid") or 0)
    ask = float(best.get("ask") or 0)
    last = float(best.get("last") or 0)
    mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else last
    return {
        "mid": round(mid, 4) if mid else None,
        "bid": bid or None,
        "ask": ask or None,
        "last": last or None,
        "iv": float(best.get("iv") or 0) or None,
        "strike": float(best.get("strike", 0)),
        "exp": exp_str,
        "source": _LAST_SOURCE.get("source"),
    }


def _reprice_week_chains(symbol: str, max_days: int = 8):
    """Return the near-term expirations (next `max_days` days, every
    weekday, NOT filtered to Fridays) each with normalized legs, so the
    Level Reprice expiry picker can offer Mon/Wed/Fri and 0DTE on names
    like AAPL and the index ETFs. Mirrors the leg shape load_option_chain
    produces. Schwab primary, yfinance fallback. Returns
    {expirations: [{date, dte}], chains: {date: {calls, puts}}, source}."""
    today = date.today()
    horizon = today + timedelta(days=max_days)
    out = {"expirations": [], "chains": {}, "source": None}

    def _add(exp_str, calls, puts):
        out["chains"][exp_str] = {"calls": calls, "puts": puts}
        ed = pd.Timestamp(exp_str).date()
        out["expirations"].append({"date": exp_str, "dte": max((ed - today).days, 0)})

    # Schwab first — its raw chain already carries every expiration.
    sc = _schwab()
    if sc is not None:
        try:
            chain = sc.get_option_chain(symbol, strike_count=160)
            if chain and chain.get("chains"):
                spot = (chain.get("underlying", {}) or {}).get("last") or 0
                for exp_str in sorted(chain["chains"].keys()):
                    try:
                        ed = pd.Timestamp(exp_str).date()
                    except Exception:
                        continue
                    if ed < today or ed > horizon:
                        continue
                    dte = max((ed - today).days, 0)
                    T = max(dte, 0.5) / 365.0
                    legs = chain["chains"][exp_str]

                    def _norm(rows, side):
                        res = []
                        for r in rows:
                            strike = float(r.get("strike", 0))
                            bid = float(r.get("bid") or 0)
                            ask = float(r.get("ask") or 0)
                            last = float(r.get("last") or 0)
                            iv = float(r.get("iv") or 0)
                            delta = r.get("delta")
                            if delta is None or delta != delta:
                                delta = _bs_delta(spot, strike, T, iv, side)
                            mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else last
                            res.append({
                                "strike": strike,
                                "bid": bid or (mid * 0.97 if mid else 0),
                                "ask": ask or (mid * 1.03 if mid else 0),
                                "last": last or mid,
                                "iv": iv,
                                "delta": delta,
                            })
                        return sorted(res, key=lambda x: x["strike"])
                    _add(exp_str, _norm(legs.get("calls", []), "call"),
                         _norm(legs.get("puts", []), "put"))
                if out["expirations"]:
                    out["source"] = "schwab"
                    out["expirations"].sort(key=lambda e: e["date"])
                    _LAST_SOURCE["source"] = "schwab"
                    return out
        except Exception as exc:  # noqa: BLE001
            print(f"[reprice] schwab week chains fallback: {exc}", file=sys.stderr)

    # Fallback: yfinance, all expirations within the horizon.
    try:
        stock = yf.Ticker(symbol)
        exps = list(stock.options or [])
        spot = float(stock.history(period="1d", auto_adjust=False)["Close"].iloc[-1])
        for e in exps:
            try:
                ed = pd.Timestamp(e).date()
            except Exception:
                continue
            if ed < today or ed > horizon:
                continue
            dte = max((ed - today).days, 0)
            T = max(dte, 0.5) / 365.0
            try:
                opt = stock.option_chain(e)
            except Exception:
                continue

            def _norm(df, side):
                res = []
                for r in df.itertuples():
                    try:
                        strike = float(getattr(r, "strike", 0))
                        bid = float(getattr(r, "bid", 0) or 0)
                        ask = float(getattr(r, "ask", 0) or 0)
                        last = float(getattr(r, "lastPrice", 0) or 0)
                        iv = float(getattr(r, "impliedVolatility", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    if strike != strike:
                        continue
                    mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else last
                    res.append({
                        "strike": strike,
                        "bid": bid or (mid * 0.97 if mid else 0),
                        "ask": ask or (mid * 1.03 if mid else 0),
                        "last": last or mid,
                        "iv": iv,
                        "delta": _bs_delta(spot, strike, T, iv, side),
                    })
                return sorted(res, key=lambda x: x["strike"])
            _add(e, _norm(opt.calls, "call"), _norm(opt.puts, "put"))
        out["expirations"].sort(key=lambda e: e["date"])
        out["source"] = "yfinance" if out["expirations"] else None
        _LAST_SOURCE["source"] = "yfinance"
    except Exception as exc:  # noqa: BLE001
        print(f"[reprice] yfinance week chains failed: {exc}", file=sys.stderr)
    return out






def _pushover_configured() -> bool:
    return bool(os.environ.get("PUSHOVER_APP_TOKEN")) and bool(os.environ.get("PUSHOVER_USER_KEY"))


def _pushover_send(title: str, message: str, priority: int = 0,
                   url: str = None, url_title: str = None) -> dict:
    """Send a push notification. Returns dict with ok/status/response.
    Best-effort: any failure logged and returned in the result dict
    instead of raising. priority 0 = normal, 1 = high (bypasses quiet
    hours), -1 = silent."""
    token = os.environ.get("PUSHOVER_APP_TOKEN", "").strip()
    user = os.environ.get("PUSHOVER_USER_KEY", "").strip()
    if not token or not user:
        return {"ok": False, "error": "not configured", "configured": False}
    payload = {"token": token, "user": user, "title": title, "message": message,
               "priority": str(int(priority))}
    if url:
        payload["url"] = url
    if url_title:
        payload["url_title"] = url_title
    try:
        body = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request("https://api.pushover.net/1/messages.json",
                                     data=body, method="POST")
        with urllib.request.urlopen(req, timeout=8) as resp:
            status = resp.status
            response_body = resp.read().decode("utf-8", errors="replace")
        return {"ok": status == 200, "status": status, "response": response_body,
                "configured": True}
    except Exception as exc:  # noqa: BLE001
        print(f"[pushover] send failed: {exc}", file=sys.stderr)
        return {"ok": False, "error": str(exc), "configured": True}


def _ntfy_configured() -> bool:
    return bool(os.environ.get("NTFY_TOPIC", "").strip())


def _ntfy_send(title: str, message: str, priority: int = 0) -> dict:
    """Send a push via ntfy.sh — free, no account. Set NTFY_TOPIC to a
    private/random topic string and subscribe to it in the free ntfy app.
    Optional NTFY_SERVER overrides the default https://ntfy.sh host."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return {"ok": False, "error": "not configured", "configured": False}
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")
    pr = {1: "5", 0: "3", -1: "2"}.get(int(priority), "3")  # ntfy uses 1..5
    try:
        req = urllib.request.Request(f"{server}/{topic}",
                                     data=message.encode("utf-8"), method="POST")
        # HTTP headers must be ASCII — strip any emoji/glyphs from the title.
        req.add_header("Title", title.encode("ascii", "ignore").decode() or "Jerry")
        req.add_header("Priority", pr)
        with urllib.request.urlopen(req, timeout=8) as resp:
            status = resp.status
        return {"ok": status == 200, "status": status, "configured": True}
    except Exception as exc:  # noqa: BLE001
        print(f"[ntfy] send failed: {exc}", file=sys.stderr)
        return {"ok": False, "error": str(exc), "configured": True}


def _push_configured() -> bool:
    return _ntfy_configured() or _pushover_configured()


def _push_notify(title: str, message: str, priority: int = 0,
                 url: str = None, url_title: str = None) -> dict:
    """Unified push: prefer free ntfy, also send via Pushover if set."""
    results = {}
    sent = False
    if _ntfy_configured():
        results["ntfy"] = _ntfy_send(title, message, priority)
        sent = sent or results["ntfy"].get("ok")
    if _pushover_configured():
        results["pushover"] = _pushover_send(title, message, priority, url, url_title)
        sent = sent or results["pushover"].get("ok")
    if not results:
        return {"ok": False, "error": "no push provider configured", "configured": False}
    return {"ok": sent, "configured": True, "providers": results}


def _migrate_legacy_watchlist() -> None:
    """One-time migration: if a watchlist.json exists in the project
    folder (legacy location) but not in the stable location, copy it.
    Runs once on startup. Idempotent — checks the stable file before
    touching anything."""
    if _WATCHLIST_PATH.exists():
        return
    legacy = HERE / "watchlist.json"
    if legacy.exists():
        try:
            _WATCHLIST_PATH.write_text(legacy.read_text())
            print(f"[migrate] watchlist: {legacy} -> {_WATCHLIST_PATH}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"[migrate] watchlist failed: {exc}", file=sys.stderr)


_migrate_legacy_watchlist()


# _watchlist_lock() and _default_watchlist() live in storage.py alongside
# their only callers (_load_watchlist/_save_watchlist). They used to be
# defined here, which raised NameError at runtime because storage.py
# could not see them.


class DashboardHandler(SimpleHTTPRequestHandler):
    weeks = 12
    friday_baseline = False

    # Register MIME types not in the default SimpleHTTPRequestHandler map.
    # .webmanifest must serve as application/manifest+json or browsers
    # will not parse it and PWA install prompts will be missing the icon
    # set. (v1.18)
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".webmanifest": "application/manifest+json"}

    def _allowed_origin(self):
        # Prefer the env-configured origin (Vercel URL in prod) so the
        # backend doesn't accept calls from arbitrary websites. Empty or
        # "*" is also accepted for permissive local dev. Browsers will
        # only honor matching origins.
        return os.environ.get("ALLOWED_ORIGIN", "*")

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", self._allowed_origin())
        self.send_header("Access-Control-Allow-Headers", "X-API-Key, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")

    def handle_one_request(self):  # noqa: N802
        """Wrap every request with wall clock timing (v1.39). The log
        line lands in server.log via _request_log for /api paths only,
        so static asset noise stays out."""
        self._t0 = time.time()
        self._last_status = None
        super().handle_one_request()
        try:
            path = getattr(self, "path", "") or ""
            if path.startswith("/api/") and self._last_status is not None:
                _request_log(getattr(self, "command", "?"), path.split("?")[0],
                             self._last_status, (time.time() - self._t0) * 1000.0)
        except Exception:
            pass

    def send_response(self, code, message=None):  # noqa: N802
        self._last_status = code
        super().send_response(code, message)

    def _send_json(self, obj, status: int = 200, no_store: bool = False, **dump_kwargs):
        """Serialize obj and write a complete JSON response, replacing the
        hand rolled send_response boilerplate that was copy pasted across
        every endpoint (v1.38 cleanup). Same headers, same CORS, same
        Content-Length as the inline pattern it replaces."""
        body = _dumps(obj, **dump_kwargs).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_api_key(self) -> bool:
        # If API_KEY is unset (typical local dev) we don't enforce. In
        # production Jerry sets API_KEY on Railway and the same value in
        # the frontend config so the page can call the backend.
        expected = os.environ.get("API_KEY", "")
        if not expected:
            return True
        provided = self.headers.get("X-API-Key", "")
        return provided == expected

    def _send_unauthorized(self):
        self._send_json({"error": "unauthorized"}, status=401)

    def do_OPTIONS(self):  # noqa: N802
        # CORS preflight. Browsers send OPTIONS before GET/PUT when
        # custom headers (like X-API-Key) are involved.
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_PUT(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/") and not self._check_api_key():
            self._send_unauthorized()
            return
        if parsed.path == "/api/watchlist":
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0 or length > 1_000_000:
                    raise ValueError("invalid content length")
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8"))
                clean = _validate_watchlist_payload(payload)
                if clean is None:
                    raise ValueError("malformed watchlist payload")
                ok = _save_watchlist(clean)
                if not ok:
                    raise RuntimeError("save failed")
                body = _dumps({"ok": True, "count": len(clean["symbols"])}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/prefs":
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0 or length > 100_000:
                    raise ValueError("invalid content length")
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8"))
                clean = _validate_prefs_payload(payload)
                if clean is None:
                    raise ValueError("malformed prefs payload")
                if not _save_prefs(clean):
                    raise RuntimeError("save failed")
                self._send_json({"ok": True})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=400)
            return
        self.send_response(404); self.end_headers()

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/") and not self._check_api_key():
            self._send_unauthorized()
            return
        # ── Dismiss a watchlist alert (v1.15) ──────────────────────
        if parsed.path == "/api/watchlist_alerts/dismiss":
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0 or length > 100_000:
                    raise ValueError("invalid content length")
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8"))
                alert_id = (payload.get("id") or "").strip()
                if not alert_id:
                    raise ValueError("id required")
                d = _load_dismissed_alerts()
                d[alert_id] = datetime.now().date().strftime("%Y-%m-%d")
                _save_dismissed_alerts(d)
                self._send_json({"ok": True, "id": alert_id})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=400)
            return
        # ── Append to trade journal (v1.15) ────────────────────────
        if parsed.path == "/api/trade_journal":
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0 or length > 1_000_000:
                    raise ValueError("invalid content length")
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("expected object")
                # Required fields. Reject anything malformed so the
                # journal stays clean for win-rate computation.
                req = ["ticker", "type", "entry_premium", "closed_premium", "qty", "opened_at", "closed_at"]
                for k in req:
                    if k not in payload:
                        raise ValueError(f"missing field: {k}")
                journal = _load_trade_journal()
                # Idempotency: composite key on ticker, type, strike,
                # expiration, opened_at, closed_at. If the same trade
                # gets posted twice (double-click on close), update
                # the existing entry instead of duplicating.
                composite = (
                    payload.get("ticker", ""), payload.get("type", ""),
                    payload.get("strike"), payload.get("expiration"),
                    payload.get("opened_at"), payload.get("closed_at"),
                )
                replaced = False
                for i, entry in enumerate(journal):
                    if (entry.get("ticker"), entry.get("type"), entry.get("strike"),
                        entry.get("expiration"), entry.get("opened_at"),
                        entry.get("closed_at")) == composite:
                        journal[i] = payload
                        replaced = True
                        break
                if not replaced:
                    journal.append(payload)
                _save_trade_journal(journal)
                self._send_json({"ok": True, "count": len(journal),
                                   "replaced": replaced})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=400)
            return
        # ── Level Reprice: project premium at target spots (v1.28) ──
        if parsed.path == "/api/reprice":
            try:
                if not _REPRICE_AVAILABLE:
                    raise ValueError("reprice module unavailable")
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0 or length > 200_000:
                    raise ValueError("invalid content length")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("expected object")
                kind = "put" if str(payload.get("kind", "call")).lower() == "put" else "call"
                spot_now = float(payload["spot_now"])
                strike = float(payload["strike"])
                days_to_exp = max(int(payload["days_to_exp"]), 0)
                r = float(payload.get("r", 0.04))
                levels = payload.get("levels") or []
                if not isinstance(levels, list) or not levels:
                    raise ValueError("levels required")
                # Live mid from the same chain path the app uses, unless a
                # manual current_price is supplied for pure projection.
                live = None
                cur = payload.get("current_price")
                symbol = (payload.get("ticker") or "").strip().upper()
                if (cur is None or cur == "") and symbol:
                    live = _live_contract_mid(symbol, strike, kind, payload.get("expiration"))
                    if live and live.get("mid"):
                        cur = live["mid"]
                if cur is None or cur == "":
                    raise ValueError("no live quote available; supply current_price")
                current_price = float(cur)
                result = _reprice.reprice_at_levels(
                    current_price, spot_now, strike, days_to_exp,
                    levels, r=r, kind=kind)
                if isinstance(result, dict) and "error" in result:
                    raise ValueError(result["error"])
                # Attach live mid per level when the contract is on-chain.
                result["current_price_used"] = round(current_price, 4)
                if live:
                    result["live_quote"] = live
                self._send_json(result)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=400)
            return
        # ── Level Reprice: fade a high into a settle (v1.28) ────────
        if parsed.path == "/api/fade":
            try:
                if not _REPRICE_AVAILABLE:
                    raise ValueError("reprice module unavailable")
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0 or length > 200_000:
                    raise ValueError("invalid content length")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("expected object")
                kind = "put" if str(payload.get("kind", "call")).lower() == "put" else "call"
                spot_now = float(payload["spot_now"])
                strike = float(payload["strike"])
                days_to_exp = max(int(payload["days_to_exp"]), 0)
                r = float(payload.get("r", 0.04))
                sell_spot = float(payload["sell_spot"])
                cover_spot = float(payload["cover_spot"])
                stop_spot = payload.get("stop_spot")
                hours_held = float(payload.get("hours_held", 0.0))
                iv_at_sell = float(payload.get("iv_at_sell", 0.0))
                iv_at_cover = float(payload.get("iv_at_cover", 0.0))
                contracts = int(payload.get("contracts", 1) or 1)
                live = None
                cur = payload.get("current_price")
                symbol = (payload.get("ticker") or "").strip().upper()
                if (cur is None or cur == "") and symbol:
                    live = _live_contract_mid(symbol, strike, kind, payload.get("expiration"))
                    if live and live.get("mid"):
                        cur = live["mid"]
                if cur is None or cur == "":
                    raise ValueError("no live quote available; supply current_price")
                current_price = float(cur)
                fade = _reprice.fade_trade(
                    current_price, spot_now, strike, days_to_exp,
                    sell_spot, cover_spot, r=r, kind=kind,
                    hours_held=hours_held, iv_at_sell=iv_at_sell,
                    iv_at_cover=iv_at_cover, contracts=contracts)
                if isinstance(fade, dict) and "error" in fade:
                    raise ValueError(fade["error"])
                # Loss case priced at the stop, using the same backed-out
                # IV via reprice_at_levels. Risk = stop price minus the
                # sell price (what it costs to cover at the stop).
                if stop_spot is not None and stop_spot != "":
                    stop_rows = _reprice.reprice_at_levels(
                        current_price, spot_now, strike, days_to_exp,
                        [{"label": "stop", "target_spot": float(stop_spot),
                          "hours_from_now": hours_held, "iv_shift": iv_at_cover}],
                        r=r, kind=kind)
                    if isinstance(stop_rows, dict) and "error" in stop_rows:
                        raise ValueError(stop_rows["error"])
                    stop_px = stop_rows["levels"][0]["price"]
                    risk_per = round((stop_px - fade["sell_price"]) * 100, 2)
                    risk_total = round(risk_per * contracts, 2)
                    cap_per = fade["capture_per_contract"]
                    rr = round(cap_per / risk_per, 2) if risk_per > 0 else None
                    fade["stop_price"] = stop_px
                    fade["max_risk_per_contract"] = risk_per
                    fade["max_risk_total"] = risk_total
                    fade["risk_reward"] = rr
                # IV sweep: capture across iv shifts at sell, -0.10..+0.05.
                sweep = []
                for sh in (-0.10, -0.05, 0.0, 0.05):
                    s = _reprice.fade_trade(
                        current_price, spot_now, strike, days_to_exp,
                        sell_spot, cover_spot, r=r, kind=kind,
                        hours_held=hours_held, iv_at_sell=sh,
                        iv_at_cover=iv_at_cover, contracts=contracts)
                    if isinstance(s, dict) and "error" not in s:
                        sweep.append({"iv_shift": sh,
                                      "capture_per_contract": s["capture_per_contract"],
                                      "capture_total": s["capture_total"]})
                fade["iv_sweep"] = sweep
                fade["contracts"] = contracts
                if live:
                    fade["live_quote"] = live
                self._send_json(fade)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=400)
            return
        # ── Level Reprice: save a staged fade (v1.28) ──────────────
        if parsed.path == "/api/fade/save":
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0 or length > 200_000:
                    raise ValueError("invalid content length")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("expected object")
                payload["saved_at"] = datetime.now().isoformat(timespec="seconds")
                stages = _load_fade_stages()
                stages.append(payload)
                _save_fade_stages(stages)
                self._send_json({"ok": True, "count": len(stages)})
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=400)
            return
        # ── Push notifications: test + roll-flag (v1.16) ──────────
        if parsed.path == "/api/push/test":
            # Send a test push so the user can verify Pushover setup.
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length > 0 and length < 10_000:
                    raw = self.rfile.read(length)
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                else:
                    payload = {}
                msg = payload.get("message") or "Push notifications are working. This is a test from your dashboard."
                result = _push_notify(
                    title="Jerry Dashboard test",
                    message=msg,
                    priority=0,
                )
                self._send_json(result, status=200 if result.get("ok") else 500)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"ok": False, "error": str(exc)}, status=500)
            return
        if parsed.path == "/api/push/roll_flag":
            # Frontend POSTs here when a position trips the v109 roll
            # flag (DTE <= 7 and |delta| >= 0.40). Body includes the
            # alert key so we can dedupe. Returns ok plus skip=True
            # when the alert was sent recently.
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0 or length > 100_000:
                    raise ValueError("invalid content length")
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8"))
                ticker = (payload.get("ticker") or "").upper().strip()
                position_id = str(payload.get("position_id") or "")
                strike = payload.get("strike")
                expiration = payload.get("expiration")
                dte = payload.get("dte")
                delta = payload.get("delta")
                opt_type = (payload.get("type") or "").lower()
                if not ticker:
                    raise ValueError("ticker required")
                key = f"roll_flag|{ticker}|{position_id}|{expiration}|{strike}"
                sent = _load_sent_alerts()
                last = sent.get(key)
                if last:
                    try:
                        last_dt = datetime.fromisoformat(last)
                        age_hours = (datetime.now() - last_dt).total_seconds() / 3600.0
                        if age_hours < _PUSH_RESEND_HOURS:
                            self._send_json({"ok": True, "skip": True,
                                               "reason": f"sent {age_hours:.1f}h ago, resend in {_PUSH_RESEND_HOURS - age_hours:.1f}h"})
                            return
                    except Exception:
                        pass
                # Compose the message.
                title = f"Roll alert · {ticker}"
                lines = [
                    f"{ticker} short {opt_type or 'option'} ${strike} exp {expiration}.",
                    f"DTE {dte}d, |delta| {delta}.",
                    "Roll, close, or accept assignment.",
                ]
                message = "\n".join(lines)
                result = _push_notify(
                    title=title,
                    message=message,
                    priority=1,  # high — bypasses quiet hours
                    url="https://dashboard.jerrytrade.com/",
                    url_title="Open dashboard",
                )
                if result.get("ok"):
                    sent[key] = datetime.now().isoformat()
                    _save_sent_alerts(sent)
                self._send_json({"ok": result.get("ok"), "skip": False,
                                   "configured": result.get("configured", False),
                                   "error": result.get("error")}, status=200 if result.get("ok") else 500)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        self.send_response(404); self.end_headers()

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        # Gate every /api/* path behind the API key when set
        if parsed.path.startswith("/api/") and not self._check_api_key():
            self._send_unauthorized()
            return
        # Top-level safety net: any uncaught exception in an /api/ handler
        # or static-file fall-through should become a JSON 500, not an
        # HTML error page. The frontend's apiFetch then sees JSON and the
        # error UI can render it. Without this, exceptions bubble up to
        # http.server which writes an HTML response — which crashes the
        # frontend's r.json() with "Unexpected token '<'".
        try:
            self._dispatch_get(parsed)
        except (KeyboardInterrupt, SystemExit) as exc:
            # SystemExit can be raised by legacy CLI-style code paths
            # (e.g. sys.exit on missing data). In server mode we MUST
            # convert these to a JSON 500 — uncaught SystemExit kills
            # the handler thread and leaves the client with an empty
            # reply or an HTML page from the static fallback handler.
            _log_warn(None, f"do_GET sysexit {parsed.path[:30]}", exc)
            try:
                self._send_json({"error": f"server error: {exc}", "path": parsed.path}, status=500)
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            _log_warn(None, f"do_GET {parsed.path[:30]}", exc)
            # If headers are already sent we can't send a fresh response —
            # silently drop. Otherwise return JSON 500 so the frontend's
            # error UI renders a proper message.
            try:
                self._send_json({"error": str(exc), "path": parsed.path}, status=500)
            except Exception:
                pass

    def _dispatch_get(self, parsed) -> None:
        """Route the request. All endpoint handlers below; unmatched
        /api/* paths return JSON 404 (not HTML)."""
        if parsed.path == "/api/reprice/chain":
            # This week's expirations with normalized legs, NOT filtered
            # to Fridays, so the Level Reprice expiry picker can offer
            # Mon/Wed/Fri and 0DTE on names like AAPL and the index ETFs.
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", [""])[0] or "").strip().upper()
            try:
                days = int(qs.get("days", ["8"])[0] or "8")
            except ValueError:
                days = 8
            days = max(1, min(days, 14))
            if not symbol or len(symbol) > 8:
                body = _dumps({"error": "symbol required"}).encode("utf-8")
                self.send_response(400)
            else:
                data = _reprice_week_chains(symbol, max_days=days)
                body = _dumps(data).encode("utf-8")
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/search":
            qs = parse_qs(parsed.query)
            q = (qs.get("q", [""])[0] or "").strip()
            if not q or len(q) > 24:
                body = _dumps({"results": []}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                results = yahoo_search(q)
            except Exception as exc:  # noqa: BLE001
                results = []
                sys.stderr.write(f"search failed for {q!r}: {exc}\n")
            body = _dumps({"results": results}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/scan":
            # Lightweight scan endpoint. Returns just the bits needed
            # for the watchlist scanner card to score strategies on the
            # frontend without paying for the full payload (no daily
            # bars, no full chain, no positions math). Comma-separated
            # symbols in `tickers`. Up to 25 symbols per request.
            qs = parse_qs(parsed.query)
            raw = (qs.get("tickers", [""])[0] or "").upper().strip()
            symbols = [s.strip() for s in raw.split(",") if s.strip()][:25]
            results = []
            for sym in symbols:
                try:
                    snap = build_scan_snapshot(sym)
                    results.append(snap)
                except Exception as exc:  # noqa: BLE001
                    results.append({"symbol": sym, "error": str(exc)})
            body = _dumps({"results": results}, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/weekly_range":
            # Implied weekly range scanner. Returns ATM straddle-implied
            # range and 0.20 delta strike suggestions for each symbol.
            # Up to 25 symbols per request to bound runtime (~3s/symbol
            # on cold yfinance, ~0.5s on warm Schwab cache).
            qs = parse_qs(parsed.query)
            raw = (qs.get("tickers", [""])[0] or "").upper().strip()
            symbols = [s.strip() for s in raw.split(",") if s.strip()][:25]
            results = []
            for sym in symbols:
                try:
                    rng = build_weekly_range(sym)
                    results.append(rng)
                except Exception as exc:  # noqa: BLE001
                    results.append({"symbol": sym, "error": str(exc)})
            body = _dumps({"results": results}, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/watchlist/diag":
            try:
                self._send_json(_watchlist_diag())
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/watchlist":
            try:
                data = _load_watchlist()
                # Surface the transient fallback flag as `seeded` so the client
                # won't let a fresh seed/default overwrite a richer local list.
                seeded = bool(data.pop("_seeded", False))
                data = {**data, "seeded": seeded}
                body = _dumps(data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/prefs":
            try:
                self._send_json(_load_prefs())
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/data_source":
            # Status of the Schwab integration. Used by the frontend
            # status badge so Jerry can see at a glance whether data
            # is coming from Schwab (live) or yfinance (fallback).
            sc = None
            try:
                if _SCHWAB_AVAILABLE and _get_schwab_client is not None:
                    sc = _get_schwab_client()
            except Exception:
                sc = None
            payload = {
                "schwab": (sc.status() if sc else {"configured": False, "reason": "module_unavailable"}),
                "last_source": _LAST_SOURCE.get("source", "yfinance"),
            }
            body = _dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self._cors_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path.startswith("/api/uw/"):
            # Unusual Whales endpoints. All return JSON. When UW_API_KEY
            # is missing the client returns None and we respond with
            # {configured: false} so the frontend can hide UW UI.
            uw = None
            try:
                if _UW_AVAILABLE and _uw_client is not None:
                    uw = _uw_client.get_client()
            except Exception:
                uw = None

            def _uw_send(payload: dict, status: int = 200) -> None:
                # Always include the rate snapshot so the UI status pill
                # can reflect remaining quota even on error responses.
                if uw is not None:
                    payload.setdefault("rate", uw.rate_snapshot())
                else:
                    payload.setdefault("rate", {})
                payload.setdefault("configured", uw is not None)
                body = _dumps(payload, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            if parsed.path == "/api/uw/health":
                # Sidebar pill calls this to know if UW is reachable.
                if uw is None:
                    _uw_send({"connected": False, "error": "UW_API_KEY not set"})
                    return
                hp = uw.health()
                _uw_send(hp)
                return

            if parsed.path == "/api/uw/flow_alerts":
                qs = parse_qs(parsed.query)
                symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
                if not symbol:
                    _uw_send({"error": "symbol required", "data": []}, status=400)
                    return
                try:
                    limit = int(qs.get("limit", ["50"])[0])
                except (TypeError, ValueError):
                    limit = 50
                try:
                    min_premium = int(qs.get("min_premium", ["0"])[0])
                except (TypeError, ValueError):
                    min_premium = 0
                limit = max(1, min(200, limit))
                if uw is None:
                    _uw_send({"data": []})
                    return
                data = uw.flow_alerts(symbol, limit=limit, min_premium=min_premium)
                _uw_send({"symbol": symbol, "data": data or []})
                return

            if parsed.path == "/api/uw/option_chains":
                qs = parse_qs(parsed.query)
                symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
                if not symbol:
                    _uw_send({"error": "symbol required", "data": []}, status=400)
                    return
                if uw is None:
                    _uw_send({"data": []})
                    return
                data = uw.option_chains(symbol)
                _uw_send({"symbol": symbol, "data": data or []})
                return

            if parsed.path == "/api/uw/greek_exposure":
                qs = parse_qs(parsed.query)
                symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
                if not symbol:
                    _uw_send({"error": "symbol required", "data": []}, status=400)
                    return
                if uw is None:
                    _uw_send({"data": None})
                    return
                data = uw.greek_exposure(symbol)
                _uw_send({"symbol": symbol, "data": data})
                return

            if parsed.path == "/api/uw/net_premium":
                qs = parse_qs(parsed.query)
                symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
                if not symbol:
                    _uw_send({"error": "symbol required", "data": []}, status=400)
                    return
                if uw is None:
                    _uw_send({"data": None})
                    return
                data = uw.net_premium(symbol)
                _uw_send({"symbol": symbol, "data": data})
                return

            if parsed.path == "/api/uw/market_tide":
                if uw is None:
                    _uw_send({"data": None})
                    return
                data = uw.market_tide()
                _uw_send({"data": data})
                return

            if parsed.path == "/api/uw/sector_flow":
                if uw is None:
                    _uw_send({"data": None})
                    return
                data = uw.sector_flow()
                _uw_send({"data": data})
                return

            if parsed.path == "/api/uw/flow_score":
                # Decision-engine score for the active ticker. Pulls
                # today's unusual flow alerts and computes four
                # sub-scores plus an overall flow score, then
                # returns a verdict appropriate for covered-call
                # decision-making. See _compute_flow_score below.
                qs = parse_qs(parsed.query)
                symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
                try:
                    current_price = float(qs.get("price", ["0"])[0])
                except (TypeError, ValueError):
                    current_price = 0.0
                if not symbol:
                    _uw_send({"error": "symbol required"}, status=400)
                    return
                if uw is None:
                    _uw_send({"data": None})
                    return
                try:
                    payload = _compute_flow_score(uw, symbol, current_price)
                    _uw_send(payload)
                except Exception as exc:  # noqa: BLE001
                    _uw_send({"error": str(exc), "symbol": symbol}, status=500)
                return

            if parsed.path == "/api/uw/flow_trades":
                # Real-time trade-level flow for a ticker. Returns a
                # list of normalized alert dicts the frontend renders
                # as a feed. Reuses cached flow_alerts so it does not
                # cost extra UW quota on top of /api/uw/flow_score.
                qs = parse_qs(parsed.query)
                symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
                try:
                    limit = max(1, min(200, int(qs.get("limit", ["50"])[0])))
                except (TypeError, ValueError):
                    limit = 50
                if not symbol:
                    _uw_send({"error": "symbol required", "data": []}, status=400)
                    return
                if uw is None:
                    _uw_send({"data": []})
                    return
                alerts = uw.flow_alerts(symbol, limit=limit, min_premium=0) or []
                normalized = []
                for a in alerts:
                    try:
                        if isinstance(a, dict):
                            normalized.append(_normalize_flow_trade(a))
                    except Exception:  # noqa: BLE001
                        continue
                _uw_send({"symbol": symbol, "data": normalized})
                return

            if parsed.path == "/api/uw/strike_flow":
                # Per-strike flow snapshot for the active ticker's
                # nearest-DTE contracts. The frontend overlays this
                # on the Suggested Strikes card so Jerry sees today's
                # volume and ask-side share next to each candidate.
                qs = parse_qs(parsed.query)
                symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
                if not symbol:
                    _uw_send({"error": "symbol required", "data": []}, status=400)
                    return
                if uw is None:
                    _uw_send({"data": []})
                    return
                # Build per-strike aggregates from today's flow_alerts.
                # UW's option_chains endpoint returns full chain state
                # but is heavier; flow_alerts already covers what we
                # need for the strike card's "today's volume + ask
                # share" overlay and reuses the cache.
                alerts = uw.flow_alerts(symbol, limit=200, min_premium=0) or []
                buckets = {}  # (side, strike, expiry) -> aggregates
                for a in alerts:
                    if not isinstance(a, dict):
                        continue
                    try:
                        n = _normalize_flow_trade(a)
                    except Exception:  # noqa: BLE001
                        continue
                    side = n.get("side")
                    strike = n.get("strike")
                    expiry = n.get("expiry")
                    if side not in ("call", "put") or strike is None:
                        continue
                    key = (side, float(strike), str(expiry or ""))
                    b = buckets.setdefault(key, {
                        "side": side, "strike": float(strike), "expiry": expiry,
                        "volume": 0, "premium": 0.0, "ask_premium": 0.0,
                        "sweep_count": 0, "trade_count": 0,
                        "vol_oi_max": 0.0, "open_interest": None,
                    })
                    b["trade_count"] += 1
                    if n.get("size"):
                        b["volume"] += int(n["size"] or 0)
                    prem = float(n.get("premium") or 0)
                    b["premium"] += prem
                    b["ask_premium"] += prem * float(n.get("ask_side_pct") or 0.5)
                    if n.get("is_sweep"):
                        b["sweep_count"] += 1
                    if n.get("vol_oi_ratio") and n["vol_oi_ratio"] > b["vol_oi_max"]:
                        b["vol_oi_max"] = n["vol_oi_ratio"]
                    if n.get("open_interest") is not None:
                        b["open_interest"] = n["open_interest"]
                rows = list(buckets.values())
                # Sort by total premium desc so the card can pick top N.
                rows.sort(key=lambda r: r["premium"], reverse=True)
                _uw_send({"symbol": symbol, "data": rows})
                return

            if parsed.path == "/api/uw/premium_richness":
                # Single-ticker premium richness — used by the
                # watchlist Premium Richness scanner. Lightweight:
                # one ticker_options_volume call plus one stock_state.
                qs = parse_qs(parsed.query)
                symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
                if not symbol:
                    _uw_send({"error": "symbol required"}, status=400)
                    return
                if uw is None:
                    _uw_send({"data": None})
                    return
                try:
                    payload = _compute_premium_richness(uw, symbol)
                    _uw_send(payload)
                except Exception as exc:  # noqa: BLE001
                    _uw_send({"error": str(exc), "symbol": symbol}, status=500)
                return

            if parsed.path == "/api/uw/momentum":
                # Intraday momentum scoring: blends UW flow score with
                # session price action (gap, % from open, RVOL). Used
                # by the Momentum scanner card. One UW flow_alerts
                # call per ticker (cached).
                qs = parse_qs(parsed.query)
                symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
                if not symbol:
                    _uw_send({"error": "symbol required"}, status=400)
                    return
                try:
                    payload = _compute_momentum_score(uw, symbol)
                    _uw_send(payload)
                except Exception as exc:  # noqa: BLE001
                    _uw_send({"error": str(exc), "symbol": symbol}, status=500)
                return

            if parsed.path == "/api/uw/market_dashboard":
                # Market-wide flow snapshot. Aggregates market_tide,
                # sector_flow, and a small spike list into one payload.
                # Cached server-side (60s TTL on each underlying call).
                if uw is None:
                    _uw_send({"data": None})
                    return
                try:
                    tide = uw.market_tide() or None
                    sector = uw.sector_flow() or None
                    spike = uw.spike() or None
                    _uw_send({
                        "tide": tide,
                        "sector": sector,
                        "spike": spike,
                    })
                except Exception as exc:  # noqa: BLE001
                    _uw_send({"error": str(exc)}, status=500)
                return

            if parsed.path == "/api/uw/market_scan_candidates":
                # First step of the market-wide UW scanner. Pulls today's
                # market-wide unusual flow alerts (no ticker filter),
                # aggregates by symbol to find tickers with the most
                # unusual activity, and returns a deduped candidate list.
                # Frontend then iterates over candidates and calls
                # /api/uw/market_scan_score per ticker for the slow part
                # (flow score + earnings + IV rank) so progress can be
                # streamed.
                qs = parse_qs(parsed.query)
                try:
                    limit = max(10, min(100, int(qs.get("limit", ["50"])[0])))
                except (TypeError, ValueError):
                    limit = 50
                exclude_raw = (qs.get("exclude", [""])[0] or "").upper()
                exclude_set = {s.strip() for s in exclude_raw.split(",") if s.strip()}
                if uw is None:
                    _uw_send({"candidates": []})
                    return
                try:
                    alerts = uw.market_flow_alerts(limit=300, min_premium=50000) or []
                    # Aggregate per-symbol: total premium, ask-side share, alert count.
                    by_sym: dict[str, dict] = {}
                    for a in alerts:
                        if not isinstance(a, dict):
                            continue
                        sym = a.get("ticker_symbol") or a.get("ticker") or a.get("symbol")
                        if not sym:
                            continue
                        sym = sym.upper().strip()
                        if not sym or sym in exclude_set:
                            continue
                        try:
                            prem = float(a.get("total_premium") or a.get("premium") or 0)
                        except (TypeError, ValueError):
                            prem = 0.0
                        if prem <= 0:
                            continue
                        ap = a.get("ask_side_perc")
                        if ap is None:
                            ap = a.get("ask_perc") or 0.5
                        try:
                            ap = max(0.0, min(1.0, float(ap)))
                        except (TypeError, ValueError):
                            ap = 0.5
                        side = (a.get("type") or a.get("option_type") or "").lower()
                        is_call = side.startswith("c")
                        is_put = side.startswith("p")
                        b = by_sym.setdefault(sym, {
                            "symbol": sym, "total_premium": 0.0, "ask_premium": 0.0,
                            "alert_count": 0, "call_premium": 0.0, "put_premium": 0.0,
                        })
                        b["total_premium"] += prem
                        b["ask_premium"] += prem * ap
                        b["alert_count"] += 1
                        if is_call:
                            b["call_premium"] += prem
                        elif is_put:
                            b["put_premium"] += prem
                    # Rank by total premium magnitude — biggest "smart money" tickers first.
                    candidates = sorted(by_sym.values(),
                                        key=lambda r: r["total_premium"], reverse=True)[:limit]
                    _uw_send({"candidates": candidates})
                except Exception as exc:  # noqa: BLE001
                    _uw_send({"error": str(exc), "candidates": []}, status=500)
                return

            if parsed.path == "/api/uw/market_scan_score":
                # Per-ticker scoring for the market-wide scanner. Combines
                # the existing flow-score helper with a Schwab quote and a
                # next-earnings lookup. Earnings dates come from yfinance
                # (already cached) so they don't add UW load.
                qs = parse_qs(parsed.query)
                symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
                if not symbol:
                    _uw_send({"error": "symbol required"}, status=400)
                    return
                if uw is None:
                    _uw_send({"data": None})
                    return
                # Live quote (Schwab if available; falls back gracefully).
                last_price = None
                change_pct = None
                sc_for_quote = _schwab()
                if sc_for_quote is not None:
                    try:
                        q = sc_for_quote.get_quote(symbol)
                        if q:
                            last_price = q.get("last")
                            change_pct = q.get("change_pct")
                    except Exception:
                        pass
                # Next earnings — use the existing yfinance-backed helper.
                # Returns (within_week_bool, "YYYY-MM-DD" | None).
                next_earnings = None
                days_to_earnings = None
                earnings_class = "none"  # "imminent" | "soon" | "none"
                try:
                    _, ed_iso = check_earnings(symbol)
                    if ed_iso:
                        next_earnings = ed_iso
                        ed_date = datetime.strptime(ed_iso, "%Y-%m-%d").date()
                        days_to_earnings = (ed_date - date.today()).days
                        if 0 <= days_to_earnings <= 7:
                            earnings_class = "imminent"
                        elif 0 <= days_to_earnings <= 14:
                            earnings_class = "soon"
                except Exception:
                    pass
                # Flow score (uses cached UW data).
                flow_score = None
                try:
                    flow_score = _compute_flow_score(uw, symbol, float(last_price or 0))
                except Exception:
                    flow_score = None
                # IV rank from stock_state (lightweight).
                iv_rank = None
                try:
                    state = uw.stock_state(symbol) or {}
                    if isinstance(state, dict):
                        iv_rank_raw = state.get("iv_rank") or state.get("iv_percentile")
                        if iv_rank_raw is not None:
                            iv_rank = float(iv_rank_raw)
                except Exception:
                    pass
                # Total options premium today across ALL trades (not just
                # unusual). UW's flow_alerts only captures unusual flow,
                # which can be a small slice of the day's total — e.g.
                # AKAM might have $10M+ total premium with only $112K
                # flagged as unusual. ticker_options_volume covers the
                # full-day picture and is what the UW website chart shows.
                #
                # The response shape on basic plan can be:
                #   - dict (flat single-day):   {"total_premium": ..., ...}
                #   - dict-with-data:           {"data": {...}}  (already unwrapped by client)
                #   - list of daily dicts:      [{"date": "2026-05-01", ...}, ...]
                # We tolerate all three — pick today's row from a list, else
                # use the dict directly.
                total_premium_today = None
                total_volume_today = None
                call_premium_today = None
                put_premium_today = None
                call_volume_today = None
                put_volume_today = None
                pcr_today = None
                avg_volume_30d = None
                try:
                    raw = uw.ticker_options_volume(symbol)
                    # Normalize to a single record dict for today.
                    rec = None
                    if isinstance(raw, list) and raw:
                        # Pick the most recent entry (sort by date desc).
                        try:
                            sortable = [r for r in raw if isinstance(r, dict)]
                            sortable.sort(key=lambda r: r.get("date") or "", reverse=True)
                            rec = sortable[0] if sortable else None
                        except Exception:
                            rec = raw[-1] if isinstance(raw[-1], dict) else None
                    elif isinstance(raw, dict):
                        rec = raw

                    def _f(d, *keys):
                        if not isinstance(d, dict):
                            return None
                        for k in keys:
                            v = d.get(k)
                            if v is not None:
                                try:
                                    return float(v)
                                except (TypeError, ValueError):
                                    continue
                        return None

                    if isinstance(rec, dict):
                        total_premium_today = _f(rec, "total_premium", "premium",
                                                 "options_premium", "total_options_premium")
                        call_premium_today = _f(rec, "call_premium", "calls_premium",
                                                "total_call_premium", "bullish_premium")
                        put_premium_today = _f(rec, "put_premium", "puts_premium",
                                               "total_put_premium", "bearish_premium")
                        tv_raw = _f(rec, "total_volume", "volume", "options_volume", "total_options_volume")
                        total_volume_today = int(tv_raw) if tv_raw is not None else None
                        cv_raw = _f(rec, "call_volume", "calls_volume", "total_call_volume")
                        call_volume_today = int(cv_raw) if cv_raw is not None else None
                        pv_raw = _f(rec, "put_volume", "puts_volume", "total_put_volume")
                        put_volume_today = int(pv_raw) if pv_raw is not None else None
                        pcr_today = _f(rec, "put_call_ratio", "pc_ratio", "p_c_ratio")
                        avg_raw = _f(rec, "avg_30_day_volume", "avg_volume", "average_volume")
                        avg_volume_30d = int(avg_raw) if avg_raw is not None else None

                    # If we got call+put premium but not total, derive it.
                    if total_premium_today is None and (call_premium_today is not None or put_premium_today is not None):
                        total_premium_today = (call_premium_today or 0) + (put_premium_today or 0)
                    # Same fallback for volume — UW returns call_volume + put_volume
                    # separately on most tickers, no flat total_volume field.
                    if total_volume_today is None and (call_volume_today is not None or put_volume_today is not None):
                        total_volume_today = (call_volume_today or 0) + (put_volume_today or 0)
                    # If we got total but not call/put, leave the split as None.
                    # If we got call+put but not P/C ratio, derive it.
                    if pcr_today is None and call_premium_today and call_premium_today > 0 and put_premium_today is not None:
                        try:
                            pcr_today = put_premium_today / call_premium_today
                        except (TypeError, ZeroDivisionError):
                            pass
                except Exception:
                    pass
                # Net premium = call premium - put premium. The UW chart in
                # screenshot shows this as "Net Prem". When call_premium and
                # put_premium are available, compute it; otherwise leave None.
                net_premium_today = None
                if call_premium_today is not None and put_premium_today is not None:
                    net_premium_today = call_premium_today - put_premium_today

                # Analyst catalyst signals — lightweight subset of the full
                # analyst card. Only the fields the scanner ranks on:
                # fresh_upgrade / fresh_downgrade booleans and upside_pct.
                # The 30-min cache on the analyst client means a full scan
                # pays the cost once per ticker per session.
                analyst_signal = {
                    "fresh_upgrade": False,
                    "fresh_downgrade": False,
                    "upside_pct": None,
                    "above_high_target": False,
                }
                if _ANALYST_AVAILABLE:
                    try:
                        analyst_client = _analyst_client.get_client()
                        adata = analyst_client.get_analyst_data(symbol, current_price=last_price)
                        if adata and adata.get("data_available"):
                            v = adata.get("verdict") or {}
                            t = adata.get("targets") or {}
                            analyst_signal["fresh_upgrade"] = bool(v.get("fresh_upgrade"))
                            analyst_signal["fresh_downgrade"] = bool(v.get("fresh_downgrade"))
                            analyst_signal["upside_pct"] = t.get("upside_pct")
                            # "above highest target" = upside_to_high_pct < -5
                            uth = t.get("upside_to_high_pct")
                            if uth is not None and uth < -5:
                                analyst_signal["above_high_target"] = True
                    except Exception as exc:
                        _log_warn(symbol, "scanner.analyst", exc)

                _uw_send({
                    "symbol": symbol,
                    "last_price": last_price,
                    "change_pct": change_pct,
                    "next_earnings": next_earnings,
                    "days_to_earnings": days_to_earnings,
                    "earnings_class": earnings_class,
                    "iv_rank": iv_rank,
                    "flow_score": flow_score,
                    "total_premium_today": total_premium_today,
                    "total_volume_today": total_volume_today,
                    "call_premium_today": call_premium_today,
                    "put_premium_today": put_premium_today,
                    "call_volume_today": call_volume_today,
                    "put_volume_today": put_volume_today,
                    "net_premium_today": net_premium_today,
                    "put_call_ratio": pcr_today,
                    "avg_volume_30d": avg_volume_30d,
                    "analyst": analyst_signal,
                })
                return

            if parsed.path == "/api/uw/debug":
                # Diagnostic endpoint — dumps the raw response from any UW
                # method by name. Useful for verifying field shapes when
                # adding new columns. Example: /api/uw/debug?method=ticker_options_volume&symbol=ALAB
                qs = parse_qs(parsed.query)
                method = (qs.get("method", [""])[0] or "").strip()
                symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
                allowed = {
                    "ticker_options_volume", "stock_state", "flow_alerts",
                    "option_chains", "greek_exposure", "net_premium",
                    "market_tide", "sector_flow", "spike",
                }
                if method not in allowed or uw is None:
                    _uw_send({"error": f"unknown method or UW off; allowed: {sorted(allowed)}"}, status=400)
                    return
                try:
                    fn = getattr(uw, method, None)
                    if fn is None:
                        _uw_send({"error": f"method {method} not on client"}, status=400)
                        return
                    if method in {"market_tide", "sector_flow", "spike"}:
                        result = fn()
                    elif method == "flow_alerts":
                        result = fn(symbol or "AAPL")
                    else:
                        result = fn(symbol or "AAPL")
                    _uw_send({"method": method, "symbol": symbol, "result": result})
                except Exception as exc:  # noqa: BLE001
                    _uw_send({"error": str(exc)}, status=500)
                return

            # Unknown UW subpath — explicit 404 so client knows.
            _uw_send({"error": f"unknown UW endpoint {parsed.path}"}, status=404)
            return
        if parsed.path == "/api/basing":
            # Mean-reversion / intraday basing tool. Returns:
            #   today_pct        — % from yesterday's close
            #   typical_dow      — { median, p10, p90, samples } same weekday
            #                      close-vs-prior-close stats from lookback
            #   stretched        — bool, |today_pct| > 1.5 * max(|p10|,|p90|)
            #   poc_price        — Point of Control (highest volume bin)
            #   tpo_price        — Time Price Opportunity (most-time-spent bin)
            #   bins             — list of {price, time_min, volume} buckets
            #   value_area       — {high, low} 70% volume range around POC
            #   holding_base     — bool, last 30 min within 0.5% of POC
            #   bounce_signal    — bool, stretched AND holding_base
            #   bin_width        — width per bucket
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", ["AAPL"])[0] or "AAPL").upper().strip()
            try:
                lookback_weeks = int(qs.get("weeks", ["12"])[0])
            except (TypeError, ValueError):
                lookback_weeks = 12
            try:
                payload = build_basing_profile(symbol, lookback_weeks)
                body = _dumps(payload, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/earnings_ladder":
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", ["AAPL"])[0] or "AAPL").upper().strip()
            try:
                n = int(qs.get("n", ["8"])[0])
            except (TypeError, ValueError):
                n = 8
            n = max(1, min(20, n))
            try:
                payload = build_earnings_ladder(symbol, n)
                body = _dumps(payload, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc), "symbol": symbol}, status=500)
            return
        if parsed.path == "/api/strategy/ema_pullback":
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", ["AAPL"])[0] or "AAPL").upper().strip()
            direction = (qs.get("direction", ["long"])[0] or "long").lower().strip()
            try:
                lookback = max(120, min(1825, int(qs.get("lookback", ["365"])[0])))
            except (TypeError, ValueError):
                lookback = 365
            def _qint(name, default):
                try:
                    return int(qs.get(name, [str(default)])[0])
                except (TypeError, ValueError):
                    return default
            ema_fast = _qint("ema_fast", 9)
            ema_med = _qint("ema_med", 21)
            ema_slow = _qint("ema_slow", 50)
            slope_bars = _qint("slope_bars", 10)
            try:
                payload = backtest_ema_pullback(symbol, direction=direction,
                                                lookback_days=lookback,
                                                ema_fast=ema_fast, ema_med=ema_med,
                                                ema_slow=ema_slow, slope_bars=slope_bars)
                body = _dumps(payload, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc), "symbol": symbol}, status=500)
            return
        if parsed.path == "/api/strategy/ema_pullback_state":
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
            direction = (qs.get("direction", ["long"])[0] or "long").lower().strip()
            def _qint(name, default):
                try:
                    return int(qs.get(name, [str(default)])[0])
                except (TypeError, ValueError):
                    return default
            ema_fast = _qint("ema_fast", 9)
            ema_med = _qint("ema_med", 21)
            ema_slow = _qint("ema_slow", 50)
            slope_bars = _qint("slope_bars", 10)
            if not symbol:
                self._send_json({"error": "symbol required"}, status=400)
                return
            try:
                payload = ema_pullback_setup_state(symbol, direction=direction,
                                                   ema_fast=ema_fast, ema_med=ema_med,
                                                   ema_slow=ema_slow, slope_bars=slope_bars)
                body = _dumps(payload, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc), "symbol": symbol}, status=500)
            return
        if parsed.path == "/api/trade_builder/multi_exp":
            # Cross-expiration Trade Builder data.
            #
            # For each available expiration up to N weeks out, load the
            # chain, pick the call closest to target_delta and the put
            # closest to -target_delta, compute the trade math, and
            # return one row per expiration. Frontend renders the rows
            # as a comparison table and re-uses the same scoring logic
            # used for the single-expiration TradeBuilderCard.
            #
            # Sequential yfinance/Schwab calls — typically 5-7
            # expirations × ~0.5-1.5s each = 3-10s for a fresh fetch.
            # Each chain call hits its own cache after that, so a
            # second request is fast.
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
            try:
                target_delta = float(qs.get("delta", ["0.20"])[0])
            except (TypeError, ValueError):
                target_delta = 0.20
            try:
                max_weeks = int(qs.get("max_weeks", ["8"])[0])
            except (TypeError, ValueError):
                max_weeks = 8
            max_weeks = max(1, min(max_weeks, 13))

            if not symbol:
                self._send_json({"error": "symbol required"}, status=400)
                return

            try:
                # Get the available expirations list first
                target_fri = next_friday()
                _, _, _, expirations = load_option_chain(symbol, target_fri, None)
                if not expirations:
                    self._send_json({"symbol": symbol, "expirations": [], "rows": []})
                    return

                # Get current spot — use Schwab if available, else fall
                # back to a single yfinance .info lookup
                spot = None
                sc = _schwab()
                if sc is not None:
                    try:
                        q = sc.get_quote(symbol)
                        if q:
                            spot = q.get("last")
                    except Exception:
                        pass
                if spot is None:
                    try:
                        import yfinance as yf
                        spot = float(yf.Ticker(symbol).fast_info.last_price)
                    except Exception:
                        spot = None

                rows = []
                for exp_str in expirations[:max_weeks]:
                    try:
                        calls, puts, _exp, _exps = load_option_chain(symbol, target_fri, exp_str)
                    except Exception as exc:
                        _log_warn(symbol, f"trade_builder.{exp_str}", exc)
                        continue

                    # Pick call closest to +target_delta, put closest to -target_delta
                    def mid(o):
                        b = o.get("bid") or 0
                        a = o.get("ask") or 0
                        if b > 0:
                            return b
                        if a > 0:
                            return (b + a) / 2
                        return o.get("last") or 0

                    call_pool = [c for c in calls if c.get("delta") is not None
                                 and 0.02 < c["delta"] < 0.50]
                    put_pool  = [p for p in puts  if p.get("delta") is not None
                                 and -0.50 < p["delta"] < -0.02]

                    call_pick = None
                    put_pick = None
                    if call_pool:
                        call_pick = min(call_pool, key=lambda c: abs(c["delta"] - target_delta))
                    if put_pool:
                        put_pick = min(put_pool, key=lambda p: abs(p["delta"] + target_delta))

                    # Compute DTE
                    try:
                        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                        dte = max(1, (exp_date - date.today()).days)
                    except Exception:
                        dte = None

                    row: dict = {
                        "expiration": exp_str,
                        "dte": dte,
                        "call": None,
                        "put": None,
                    }
                    if call_pick and spot:
                        cm = mid(call_pick)
                        row["call"] = {
                            "strike": call_pick.get("strike"),
                            "delta": call_pick.get("delta"),
                            "iv": call_pick.get("iv"),
                            "mid": cm,
                            "bid": call_pick.get("bid"),
                            "ask": call_pick.get("ask"),
                            "open_interest": call_pick.get("openInterest"),
                            "volume": call_pick.get("volume"),
                            "premium_pct_of_stock": (cm / spot * 100) if spot > 0 else None,
                            "annualized_pct": (cm / spot * 365 / dte * 100) if (spot > 0 and dte) else None,
                            "pop_pct": (1 - abs(call_pick.get("delta") or 0)) * 100,
                            "breakeven": (call_pick.get("strike") or 0) + cm,
                            "max_profit_per_contract": cm * 100,
                        }
                    if put_pick and spot:
                        pm = mid(put_pick)
                        ps = put_pick.get("strike") or 0
                        row["put"] = {
                            "strike": ps,
                            "delta": put_pick.get("delta"),
                            "iv": put_pick.get("iv"),
                            "mid": pm,
                            "bid": put_pick.get("bid"),
                            "ask": put_pick.get("ask"),
                            "open_interest": put_pick.get("openInterest"),
                            "volume": put_pick.get("volume"),
                            "premium_pct_of_strike": (pm / ps * 100) if ps > 0 else None,
                            "annualized_pct": (pm / ps * 365 / dte * 100) if (ps > 0 and dte) else None,
                            "pop_pct": (1 - abs(put_pick.get("delta") or 0)) * 100,
                            "if_assigned_at": ps - pm,
                            "discount_pct": ((spot - (ps - pm)) / spot * 100) if spot > 0 else None,
                            "capital_required": ps * 100,
                            "max_profit_per_contract": pm * 100,
                        }
                    rows.append(row)

                payload = {
                    "symbol": symbol,
                    "spot": spot,
                    "target_delta": target_delta,
                    "expirations": expirations,
                    "rows": rows,
                    "as_of": datetime.now().isoformat(),
                }
                body = _dumps(payload, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                _log_warn(symbol, "api/trade_builder/multi_exp", exc)
                self._send_json({"error": str(exc), "symbol": symbol}, status=500)
            return
        if parsed.path == "/api/swings":
            if not _SWINGS_AVAILABLE:
                self._send_json({"error": "swings unavailable"}, status=503)
                return
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
            if not symbol:
                self._send_json({"error": "symbol required"}, status=400)
                return
            try:
                pct = float(qs.get("pct", ["0.12"])[0])
            except (TypeError, ValueError):
                pct = 0.12
            try:
                mm = float(qs.get("min", ["15"])[0])
            except (TypeError, ValueError):
                mm = 15.0
            period = qs.get("period", ["1y"])[0]
            pctc = max(0.03, min(0.30, pct))
            mmc = max(1.0, min(60.0, mm))
            skey = (symbol, period, round(pctc, 4), round(mmc, 2))
            now = time.time()
            with _SWINGS_LOCK:
                hit = _SWINGS_CACHE.get(skey)
            if hit is not None and (now - hit[0]) < _SWINGS_TTL:
                self._send_json(hit[1])
                return
            # Best-effort UW options-flow read, folded into the swing scores.
            # One cached per-ticker call; degrades silently if UW is off.
            flow = None
            try:
                if _UW_AVAILABLE and _uw_client is not None:
                    uw = _uw_client.get_client()
                    if uw is not None:
                        flow = _compute_flow_score(uw, symbol, 0.0)
            except Exception as exc:  # noqa: BLE001
                _log_warn(symbol, "api/swings.flow", exc)
                flow = None
            try:
                # Reuse the app's Schwab-first, cached daily history (already
                # warm from loading the symbol on Trade) instead of letting
                # swings do a fresh yfinance 1y download — the slow part of
                # opening Patterns. Only for the default 1y window.
                bars = None
                if period == "1y":
                    try:
                        bars = load_daily(symbol, 260)
                    except Exception:
                        bars = None
                res = _swings.analyze(symbol, period=period, pct=pctc,
                                      min_move_pct=mmc, flow=flow, bars=bars)
                with _SWINGS_LOCK:
                    _SWINGS_CACHE[skey] = (time.time(), res)
                    if len(_SWINGS_CACHE) > 128:
                        _SWINGS_CACHE.pop(min(_SWINGS_CACHE,
                                             key=lambda k: _SWINGS_CACHE[k][0]), None)
                self._send_json(res)
            except Exception as exc:  # noqa: BLE001
                _log_warn(symbol, "api/swings", exc)
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/news":
            if not _NEWS_AVAILABLE:
                self._send_json({"error": "news unavailable", "items": []}, status=503)
                return
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
            name = (qs.get("name", [""])[0] or "").strip()
            if not symbol:
                self._send_json({"error": "symbol required", "items": []}, status=400)
                return
            try:
                self._send_json(_news.get_news(symbol, name=name or None, limit=40))
            except Exception as exc:  # noqa: BLE001
                _log_warn(symbol, "api/news", exc)
                self._send_json({"error": str(exc), "items": []}, status=500)
            return
        if parsed.path == "/api/watchlist_table":
            if not _WLTABLE_AVAILABLE:
                self._send_json({"error": "watchlist table unavailable", "rows": []}, status=503)
                return
            try:
                self._send_json(_wltable.get_board())
            except Exception as exc:  # noqa: BLE001
                _log_warn(None, "api/watchlist_table", exc)
                self._send_json({"error": str(exc), "rows": []}, status=500)
            return
        if parsed.path == "/api/watchlist_table/scan":
            if not _WLTABLE_AVAILABLE:
                self._send_json({"error": "watchlist table unavailable"}, status=503)
                return
            try:
                wl = _load_watchlist()
                syms = [s.get("symbol") for s in (wl.get("symbols") or []) if s.get("symbol")]
                force = parse_qs(parsed.query).get("force", ["0"])[0] in ("1", "true")
                self._send_json(_wltable.trigger_scan(syms, force=force))
            except Exception as exc:  # noqa: BLE001
                _log_warn(None, "api/watchlist_table/scan", exc)
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/ivrank":
            if not _IVRANK_AVAILABLE:
                self._send_json({"error": "ivrank unavailable"}, status=503)
                return
            try:
                self._send_json(_ivrank.get_board())
            except Exception as exc:  # noqa: BLE001
                _log_warn(None, "api/ivrank", exc)
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/ivrank/scan":
            if not _IVRANK_AVAILABLE:
                self._send_json({"error": "ivrank unavailable"}, status=503)
                return
            qs = parse_qs(parsed.query)
            force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
            try:
                wl = _load_watchlist()
                syms = [s.get("symbol") for s in (wl.get("symbols") or []) if s.get("symbol")]
            except Exception:
                syms = []
            try:
                self._send_json(_ivrank.trigger_scan(syms, force=force))
            except Exception as exc:  # noqa: BLE001
                _log_warn(None, "api/ivrank/scan", exc)
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/trend":
            if not _TREND_AVAILABLE:
                self._send_json({"error": "trend unavailable"}, status=503)
                return
            try:
                self._send_json(_trend.get_board())
            except Exception as exc:  # noqa: BLE001
                _log_warn(None, "api/trend", exc)
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/trend/scan":
            if not _TREND_AVAILABLE:
                self._send_json({"error": "trend unavailable"}, status=503)
                return
            qs = parse_qs(parsed.query)
            force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
            try:
                wl = _load_watchlist()
                syms = [s.get("symbol") for s in (wl.get("symbols") or []) if s.get("symbol")]
            except Exception:
                syms = []
            try:
                self._send_json(_trend.trigger_scan(syms, force=force))
            except Exception as exc:  # noqa: BLE001
                _log_warn(None, "api/trend/scan", exc)
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/movers":
            if not _MOVERS_AVAILABLE:
                self._send_json({"error": "movers unavailable"}, status=503)
                return
            try:
                self._send_json(_movers.get_board())
            except Exception as exc:  # noqa: BLE001
                _log_warn(None, "api/movers", exc)
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/movers/scan":
            if not _MOVERS_AVAILABLE:
                self._send_json({"error": "movers unavailable"}, status=503)
                return
            qs = parse_qs(parsed.query)
            force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
            try:
                wl = _load_watchlist()
                syms = [s.get("symbol") for s in (wl.get("symbols") or []) if s.get("symbol")]
            except Exception:
                syms = []
            try:
                self._send_json(_movers.trigger_scan(syms, force=force))
            except Exception as exc:  # noqa: BLE001
                _log_warn(None, "api/movers/scan", exc)
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/analyst_board":
            # Morning analyst board: ranked actions + game-plan summary.
            if not _ANALYST_BOARD_AVAILABLE:
                self._send_json({"error": "analyst board unavailable"}, status=503)
                return
            try:
                self._send_json(_analyst_board.get_board())
            except Exception as exc:  # noqa: BLE001
                _log_warn(None, "api/analyst_board", exc)
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/analyst_board/scan":
            # Kick off a background universe scan (merges in the watchlist).
            if not _ANALYST_BOARD_AVAILABLE:
                self._send_json({"error": "analyst board unavailable"}, status=503)
                return
            qs = parse_qs(parsed.query)
            try:
                days = int(qs.get("days", ["2"])[0])
            except (TypeError, ValueError):
                days = 2
            force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
            try:
                wl = _load_watchlist()
                syms = [s.get("symbol") for s in (wl.get("symbols") or []) if s.get("symbol")]
            except Exception:
                syms = []
            try:
                res = _analyst_board.trigger_scan(syms, recent_days=max(1, min(7, days)), force=force)
                self._send_json(res)
            except Exception as exc:  # noqa: BLE001
                _log_warn(None, "api/analyst_board/scan", exc)
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/analyst":
            # Analyst price targets + rating change history.
            # Combines Finnhub aggregates with yfinance per-firm history.
            # Optional `price` query param lets the frontend pass the
            # current live price so upside/downside calculations match
            # exactly what the user sees on the dashboard.
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
            try:
                price = float(qs.get("price", [""])[0]) if qs.get("price", [""])[0] else None
            except (TypeError, ValueError):
                price = None
            force = qs.get("force", ["0"])[0] in ("1", "true", "yes")
            if not symbol:
                self._send_json({"error": "symbol required"}, status=400)
                return
            if not _ANALYST_AVAILABLE:
                self._send_json({
                    "symbol": symbol,
                    "data_available": False,
                    "source": "none",
                    "error": "analyst module not available",
                })
                return
            try:
                client = _analyst_client.get_client()
                payload = client.get_analyst_data(symbol, current_price=price, force_refresh=force)
                body = _dumps(payload, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                _log_warn(symbol, "api/analyst", exc)
                self._send_json({"error": str(exc), "symbol": symbol}, status=500)
            return
        if parsed.path == "/api/watchlist_alerts":
            # Scans every ticker in the user's watchlist for fresh
            # analyst signals (upgrades, downgrades, target raises, target
            # cuts) seen in the past N days. Returns a compact list the
            # frontend can render as a banner. Persisted dismissal log
            # at ~/.jerry-dashboard/dismissed_alerts.json so dismissing
            # a signal once does not re-surface it on every poll.
            qs = parse_qs(parsed.query)
            try:
                lookback_days = int(qs.get("lookback", ["7"])[0])
            except (TypeError, ValueError):
                lookback_days = 7
            lookback_days = max(1, min(30, lookback_days))
            include_dismissed = qs.get("include_dismissed", ["0"])[0] in ("1", "true", "yes")
            if not _ANALYST_AVAILABLE:
                self._send_json({"alerts": [], "data_available": False,
                                   "error": "analyst module not available"})
                return
            try:
                wl = _load_watchlist()
                symbols = []
                if isinstance(wl, dict):
                    raw_syms = wl.get("symbols") or wl.get("tickers") or []
                    if isinstance(raw_syms, list):
                        for s in raw_syms:
                            if isinstance(s, dict):
                                sym = (s.get("symbol") or s.get("ticker") or "").upper().strip()
                            else:
                                sym = str(s).upper().strip()
                            if sym and sym not in symbols:
                                symbols.append(sym)
                dismissed = _load_dismissed_alerts() if not include_dismissed else {}
                client = _analyst_client.get_client()
                alerts = []
                cutoff = (datetime.now() - timedelta(days=lookback_days)).date()
                for sym in symbols:
                    try:
                        adata = client.get_analyst_data(sym)
                        if not adata or not adata.get("data_available"):
                            continue
                        v = adata.get("verdict") or {}
                        history = adata.get("history") or []
                        # Walk recent firm-level changes, not just today flags.
                        for h in history[:5]:  # newest first
                            try:
                                hdate_str = h.get("date") or h.get("event_date")
                                if not hdate_str:
                                    continue
                                hdate = datetime.fromisoformat(hdate_str.replace("Z", "")).date()
                            except Exception:
                                continue
                            if hdate < cutoff:
                                continue
                            action = (h.get("action") or h.get("event") or "").lower()
                            firm = h.get("firm") or h.get("company") or "an analyst"
                            from_grade = h.get("from_grade") or h.get("from")
                            to_grade = h.get("to_grade") or h.get("to")
                            kind = None
                            if "upgrade" in action or "raised" in action:
                                kind = "upgrade"
                            elif "downgrade" in action or "lowered" in action or "cut" in action:
                                kind = "downgrade"
                            elif "target" in action and ("raise" in action or "increase" in action):
                                kind = "target_raise"
                            elif "target" in action and ("cut" in action or "lower" in action):
                                kind = "target_cut"
                            if not kind:
                                continue
                            alert_id = f"{sym}|{hdate.strftime('%Y-%m-%d')}|{kind}|{firm}"
                            if alert_id in dismissed:
                                continue
                            alerts.append({
                                "id": alert_id,
                                "symbol": sym,
                                "kind": kind,
                                "date": hdate.strftime("%Y-%m-%d"),
                                "firm": firm,
                                "from_grade": from_grade,
                                "to_grade": to_grade,
                                "fresh": v.get("fresh_upgrade") or v.get("fresh_downgrade") or False,
                            })
                    except Exception as exc:  # noqa: BLE001
                        _log_warn(sym, "watchlist_alerts.symbol", exc)
                # Newest first.
                alerts.sort(key=lambda a: a["date"], reverse=True)
                body = _dumps({
                    "alerts": alerts,
                    "lookback_days": lookback_days,
                    "scanned_count": len(symbols),
                    "data_available": True,
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                _log_warn("*", "api/watchlist_alerts", exc)
                self._send_json({"error": str(exc), "alerts": []}, status=500)
            return
        if parsed.path == "/api/trade_journal":
            # GET returns full journal sorted newest-first. Frontend
            # win-rate tracker reads this and computes stats client-side.
            try:
                journal = _load_trade_journal()
                journal.sort(key=lambda e: e.get("closed_at", ""), reverse=True)
                body = _dumps({"trades": journal,
                                   "count": len(journal)}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc), "trades": []}, status=500)
            return
        if parsed.path == "/api/earnings_iv_crush":
            # Estimates typical post-earnings IV crush for symbols with
            # earnings inside the next N days. Methodology: pull past
            # 4 to 8 earnings events. For each event, compare implied
            # 30-day move just before earnings (approximated from the
            # open-to-close gap on earnings day plus the one-week
            # realized vol around it) with the next-day actual move.
            # This is HEURISTIC, not a real IV time-series since
            # historical IV is not free. Documented as such in the UI.
            qs = parse_qs(parsed.query)
            try:
                lookback_events = int(qs.get("events", ["6"])[0])
            except (TypeError, ValueError):
                lookback_events = 6
            lookback_events = max(2, min(12, lookback_events))
            try:
                horizon_days = int(qs.get("horizon", ["14"])[0])
            except (TypeError, ValueError):
                horizon_days = 14
            horizon_days = max(1, min(45, horizon_days))
            try:
                wl = _load_watchlist()
                symbols = []
                if isinstance(wl, dict):
                    raw_syms = wl.get("symbols") or wl.get("tickers") or []
                    if isinstance(raw_syms, list):
                        for s in raw_syms:
                            if isinstance(s, dict):
                                sym = (s.get("symbol") or s.get("ticker") or "").upper().strip()
                            else:
                                sym = str(s).upper().strip()
                            if sym and sym not in symbols:
                                symbols.append(sym)
                rows = []
                today = date.today()
                for sym in symbols:
                    try:
                        stock = yf.Ticker(sym)
                        ed = None
                        try:
                            ed = stock.earnings_dates
                        except Exception:
                            ed = None
                        if ed is None or ed.empty:
                            continue
                        future_dates = sorted([d.date() for d in ed.index if d.date() >= today])
                        if not future_dates:
                            continue
                        next_earn = future_dates[0]
                        days_to = (next_earn - today).days
                        if days_to > horizon_days:
                            continue
                        # Past events. yfinance index is descending.
                        past_dates = sorted([d.date() for d in ed.index if d.date() < today], reverse=True)[:lookback_events]
                        if len(past_dates) < 2:
                            continue
                        # Pull daily history from earliest past event minus
                        # a week, through today.
                        start = (min(past_dates) - timedelta(days=10)).strftime("%Y-%m-%d")
                        try:
                            hist = stock.history(start=start, auto_adjust=False)
                        except Exception:
                            continue
                        if hist is None or hist.empty:
                            continue
                        crush_samples = []
                        for ev in past_dates:
                            # Find the bar on or just after the earnings date.
                            after = [d for d in hist.index if d.date() >= ev]
                            before = [d for d in hist.index if d.date() < ev]
                            if not after or not before:
                                continue
                            ev_bar = hist.loc[after[0]]
                            day_before = hist.loc[before[-1]]
                            # Pre-earnings 5-day realized vol (annualized) as a
                            # rough proxy for the implied 30-day vol the market
                            # was pricing in just before the print.
                            window = [d for d in before[-5:]]
                            if len(window) < 3:
                                continue
                            closes = hist.loc[window]["Close"].values
                            rets = []
                            for i in range(1, len(closes)):
                                if closes[i - 1] > 0:
                                    rets.append((closes[i] / closes[i - 1]) - 1.0)
                            if len(rets) < 2:
                                continue
                            import statistics
                            stdev = statistics.pstdev(rets)
                            pre_iv_proxy = stdev * (252.0 ** 0.5)
                            # Post-earnings 5-day realized vol.
                            after_window = after[:5]
                            if len(after_window) < 3:
                                continue
                            closes_a = hist.loc[after_window]["Close"].values
                            rets_a = []
                            for i in range(1, len(closes_a)):
                                if closes_a[i - 1] > 0:
                                    rets_a.append((closes_a[i] / closes_a[i - 1]) - 1.0)
                            if len(rets_a) < 2:
                                continue
                            stdev_a = statistics.pstdev(rets_a)
                            post_iv_proxy = stdev_a * (252.0 ** 0.5)
                            if pre_iv_proxy <= 0:
                                continue
                            # Crush = drop from pre to post, as a percent of pre.
                            crush_pct = (1.0 - (post_iv_proxy / pre_iv_proxy)) * 100.0
                            crush_samples.append(round(crush_pct, 1))
                        if not crush_samples:
                            continue
                        # Median is robust to one-off outliers.
                        sorted_c = sorted(crush_samples)
                        median_crush = sorted_c[len(sorted_c) // 2]
                        avg_crush = round(sum(crush_samples) / len(crush_samples), 1)
                        rows.append({
                            "symbol": sym,
                            "next_earnings": next_earn.strftime("%Y-%m-%d"),
                            "days_to_earnings": days_to,
                            "median_crush_pct": median_crush,
                            "avg_crush_pct": avg_crush,
                            "samples": crush_samples,
                            "sample_count": len(crush_samples),
                        })
                    except Exception as exc:  # noqa: BLE001
                        _log_warn(sym, "earnings_iv_crush.symbol", exc)
                rows.sort(key=lambda r: r["days_to_earnings"])
                body = _dumps({
                    "rows": rows,
                    "horizon_days": horizon_days,
                    "lookback_events": lookback_events,
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                _log_warn("*", "api/earnings_iv_crush", exc)
                self._send_json({"error": str(exc), "rows": []}, status=500)
            return
        if parsed.path == "/api/push/status":
            # Lightweight status check so the frontend can show a
            # "Push not configured" hint when env vars are missing.
            self._send_json({
                "configured": _push_configured(),
                "provider": "ntfy" if _ntfy_configured() else ("pushover" if _pushover_configured() else "none"),
                "ntfy_configured": _ntfy_configured(),
                "pushover_configured": _pushover_configured(),
                "user_key_set": bool(os.environ.get("PUSHOVER_USER_KEY")),
                "app_token_set": bool(os.environ.get("PUSHOVER_APP_TOKEN")),
            })
            return
        if parsed.path == "/api/broker/accounts":
            # v1.17: Schwab broker import phase 1 — list accounts.
            try:
                sc = _schwab()
                if sc is None or not sc.is_configured():
                    self._send_json({
                        "configured": False,
                        "accounts": [],
                        "reason": "Schwab not configured. Run 'jerry auth' to authenticate.",
                    })
                    return
                accounts = sc.get_account_numbers() or []
                # Strip raw account numbers from the response. We only
                # send the hashValue and a masked last-4 derived from
                # the account number for display.
                safe_accounts = []
                for a in accounts:
                    raw = (a.get("accountNumber") or "")
                    masked = "****" + raw[-4:] if len(raw) >= 4 else "****"
                    safe_accounts.append({
                        "hash": a.get("hashValue"),
                        "masked": masked,
                    })
                body = _dumps({
                    "configured": True,
                    "accounts": safe_accounts,
                    "count": len(safe_accounts),
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                _log_warn("*", "api/broker/accounts", exc)
                self._send_json({"error": str(exc), "accounts": []}, status=500)
            return
        if parsed.path == "/api/broker/positions":
            # v1.17: Schwab broker import phase 1 — read positions for
            # an account hash. Returns positions normalized into the
            # dashboard's internal format. Frontend can choose to
            # merge these with the local positions list.
            qs = parse_qs(parsed.query)
            account_hash = (qs.get("account_hash", [""])[0] or "").strip()
            if not account_hash:
                self._send_json({"error": "account_hash required"}, status=400)
                return
            try:
                sc = _schwab()
                if sc is None or not sc.is_configured():
                    self._send_json({
                        "configured": False,
                        "positions": [],
                        "reason": "Schwab not configured.",
                    })
                    return
                payload = sc.get_account_positions(account_hash)
                if payload is None:
                    self._send_json({"configured": True, "positions": [],
                                       "error": "could not fetch account positions"})
                    return
                from schwab_client import SchwabClient
                positions = SchwabClient.normalize_positions(payload)
                body = _dumps({
                    "configured": True,
                    "positions": positions,
                    "count": len(positions),
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                _log_warn("*", "api/broker/positions", exc)
                self._send_json({"error": str(exc), "positions": []}, status=500)
            return
        if parsed.path == "/api/backtest":
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", ["AAPL"])[0] or "AAPL").upper().strip()
            strategy = (qs.get("strategy", ["short_strangle"])[0] or "short_strangle").strip()
            try:
                weeks = int(qs.get("weeks", ["52"])[0])
            except (TypeError, ValueError):
                weeks = 52
            weeks = max(4, min(208, weeks))  # 1 month .. 4 years
            try:
                target_delta = float(qs.get("delta", ["0.20"])[0])
            except (TypeError, ValueError):
                target_delta = 0.20
            target_delta = max(0.05, min(0.45, target_delta))
            try:
                payload = backtest_strategy(symbol, strategy, weeks, target_delta)
                body = _dumps(payload, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc), "symbol": symbol}, status=500)
            return
        if parsed.path == "/api/pullback_backtest":
            # Backtest the open-to-low (short) or open-to-high (long)
            # thesis at a user-specified target percentage. Counts how
            # many historical days reached the target intraday — the
            # "touch" win condition. No stop-loss simulation; that
            # requires minute-level data which is out of scope here.
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
            try:
                lookback_days = int(qs.get("days", ["180"])[0])
            except (TypeError, ValueError):
                lookback_days = 180
            # Allow short lookbacks (down to 5 days). The user may want
            # to see only the most recent week or two. Stats become
            # noisy below ~20 — frontend warns when that happens.
            lookback_days = max(5, min(500, lookback_days))
            try:
                target_pct = float(qs.get("target", ["1.0"])[0])
            except (TypeError, ValueError):
                target_pct = 1.0
            target_pct = max(0.05, min(20.0, target_pct))
            direction = (qs.get("direction", ["short"])[0] or "short").lower()
            if direction not in ("short", "long"):
                direction = "short"
            # Optional gap filter: only count days where today's open gapped
            # by at least N% in the relevant direction.
            try:
                min_gap_abs = float(qs.get("min_gap", ["0"])[0])
            except (TypeError, ValueError):
                min_gap_abs = 0.0
            min_gap_abs = max(0.0, min(20.0, min_gap_abs))
            payload = {
                "symbol": symbol,
                "direction": direction,
                "target_pct": target_pct,
                "min_gap_pct": min_gap_abs,
                "lookback_days": lookback_days,
            }
            try:
                bars = load_daily(symbol, lookback_days)
                rows = []
                for i, b in enumerate(bars):
                    o = b.get("open"); h = b.get("high"); l = b.get("low")
                    c = b.get("close")
                    if not (o and h and l and c) or o <= 0:
                        continue
                    prev_c = bars[i - 1].get("close") if i > 0 else None
                    gap_pct = ((o - prev_c) / prev_c * 100.0) if (prev_c and prev_c > 0) else 0.0
                    pullback_pct = max(0.0, ((o - l) / o) * 100.0)
                    pop_pct = max(0.0, ((h - o) / o) * 100.0)
                    # Weekday for day-of-week breakdown. Date may be a
                    # datetime object or a string — handle both.
                    bar_date = b.get("date")
                    weekday_idx = None
                    try:
                        if hasattr(bar_date, "weekday"):
                            weekday_idx = bar_date.weekday()
                        elif isinstance(bar_date, str):
                            d = datetime.fromisoformat(bar_date.split("T")[0])
                            weekday_idx = d.weekday()
                    except Exception:
                        weekday_idx = None
                    rows.append({
                        "date": bar_date,
                        "open": o, "high": h, "low": l, "close": c,
                        "prev_close": prev_c,
                        "gap_pct": gap_pct,
                        "pullback_pct": pullback_pct,
                        "pop_pct": pop_pct,
                        "weekday": weekday_idx,
                    })
                if not rows:
                    payload["error"] = "no historical bars available"
                    payload["samples"] = 0
                else:
                    # Apply gap filter — direction-specific
                    if direction == "short":
                        # Short-the-open requires a strong upward gap
                        qualified = [r for r in rows if r["gap_pct"] >= min_gap_abs]
                    else:
                        # Long-the-open: positive gap (buy strength)
                        # OR gap-down (recovery). Use gap magnitude.
                        qualified = [r for r in rows if abs(r["gap_pct"]) >= min_gap_abs]
                    n_qualified = len(qualified)
                    if n_qualified == 0:
                        payload.update({
                            "samples": 0,
                            "qualified_days": 0,
                            "hits": 0,
                            "misses": 0,
                            "hit_rate": None,
                            "results": [],
                        })
                    else:
                        # Win: did intraday low (or high) touch target %?
                        hits = []
                        misses = []
                        for r in qualified:
                            move = r["pullback_pct"] if direction == "short" else r["pop_pct"]
                            if move >= target_pct:
                                hits.append(r)
                            else:
                                misses.append(r)
                        n_hits = len(hits)
                        n_misses = len(misses)
                        hit_rate = (n_hits / n_qualified) * 100.0
                        # Stats on the WIN size
                        if hits:
                            move_field = "pullback_pct" if direction == "short" else "pop_pct"
                            win_moves = [r[move_field] for r in hits]
                            win_moves.sort()
                            avg_win = sum(win_moves) / len(win_moves)
                            median_win = win_moves[len(win_moves) // 2]
                            max_win = max(win_moves)
                        else:
                            avg_win = median_win = max_win = None
                        # Average miss (how close did misses get?)
                        if misses:
                            move_field = "pullback_pct" if direction == "short" else "pop_pct"
                            miss_moves = [r[move_field] for r in misses]
                            avg_miss = sum(miss_moves) / len(miss_moves)
                            max_miss = max(miss_moves)
                        else:
                            avg_miss = max_miss = None
                        # Recent 30 days timeline for visualization.
                        # Most recent at the END of the array.
                        recent = qualified[-30:] if len(qualified) > 30 else qualified
                        recent_out = []
                        move_field = "pullback_pct" if direction == "short" else "pop_pct"
                        for r in recent:
                            recent_out.append({
                                "date": r["date"],
                                "gap_pct": round(r["gap_pct"], 2),
                                "move_pct": round(r[move_field], 2),
                                "hit": r[move_field] >= target_pct,
                            })
                        # Per-weekday breakdown — same gap filter, same
                        # target. Helps identify day-of-week patterns
                        # (e.g. Mondays gap-and-go more, Fridays fade).
                        weekday_breakdown = []
                        WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
                        for wd_idx in range(5):
                            wd_rows = [r for r in qualified if r.get("weekday") == wd_idx]
                            wd_n = len(wd_rows)
                            if wd_n == 0:
                                weekday_breakdown.append({
                                    "weekday": WEEKDAYS[wd_idx],
                                    "n": 0,
                                    "hits": 0,
                                    "hit_rate": None,
                                    "avg_move": None,
                                })
                                continue
                            move_f = "pullback_pct" if direction == "short" else "pop_pct"
                            wd_hits = sum(1 for r in wd_rows if r[move_f] >= target_pct)
                            wd_moves = [r[move_f] for r in wd_rows]
                            avg_move = sum(wd_moves) / wd_n
                            weekday_breakdown.append({
                                "weekday": WEEKDAYS[wd_idx],
                                "n": wd_n,
                                "hits": wd_hits,
                                "hit_rate": round((wd_hits / wd_n) * 100.0, 2),
                                "avg_move": round(avg_move, 2),
                            })
                        payload.update({
                            "samples": len(rows),
                            "qualified_days": n_qualified,
                            "hits": n_hits,
                            "misses": n_misses,
                            "hit_rate": round(hit_rate, 2),
                            "avg_win_size": round(avg_win, 2) if avg_win is not None else None,
                            "median_win_size": round(median_win, 2) if median_win is not None else None,
                            "max_win_size": round(max_win, 2) if max_win is not None else None,
                            "avg_miss_size": round(avg_miss, 2) if avg_miss is not None else None,
                            "max_miss_size": round(max_miss, 2) if max_miss is not None else None,
                            "recent": recent_out,
                            "weekday_breakdown": weekday_breakdown,
                        })
            except Exception as exc:  # noqa: BLE001
                payload["error"] = str(exc)
            try:
                body = _dumps(payload, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/pullback_scan":
            # Batch version of /api/pullback_profile. Accepts up to 25
            # comma-separated tickers and returns a compact summary per
            # symbol focused on gap-up behavior. Used by the watchlist
            # scanner to rank symbols by short-the-open attractiveness.
            qs = parse_qs(parsed.query)
            raw = (qs.get("tickers", [""])[0] or "").upper().strip()
            symbols = [s.strip() for s in raw.split(",") if s.strip()][:25]
            try:
                lookback_days = int(qs.get("days", ["180"])[0])
            except (TypeError, ValueError):
                lookback_days = 180
            lookback_days = max(60, min(500, lookback_days))
            results = []
            for symbol in symbols:
                try:
                    bars = load_daily(symbol, lookback_days)
                    rows = []
                    for i, b in enumerate(bars):
                        o = b.get("open"); h = b.get("high"); l = b.get("low")
                        c = b.get("close")
                        if not (o and h and l and c) or o <= 0:
                            continue
                        prev_c = bars[i - 1].get("close") if i > 0 else None
                        gap_pct = ((o - prev_c) / prev_c * 100.0) if (prev_c and prev_c > 0) else 0.0
                        pullback_pct = max(0.0, ((o - l) / o) * 100.0)
                        pop_pct = max(0.0, ((h - o) / o) * 100.0)
                        open_was_low = (l >= o - 0.005)
                        open_was_high = (h <= o + 0.005)
                        rows.append({
                            "open_pct_pullback": pullback_pct,
                            "open_pct_pop": pop_pct,
                            "open_was_low": open_was_low,
                            "open_was_high": open_was_high,
                            "gap_pct": gap_pct,
                            "open": o, "close": c,
                        })
                    if not rows:
                        results.append({"symbol": symbol, "error": "no data"})
                        continue
                    n = len(rows)
                    def _pct_of(values, p):
                        s = sorted(values)
                        if not s: return None
                        if len(s) == 1: return s[0]
                        k = (len(s) - 1) * (p / 100.0)
                        f = int(k); c = min(f + 1, len(s) - 1)
                        return s[f] + (s[c] - s[f]) * (k - f)
                    # Short side (overall): median pullback + open-eq-low
                    pulls = [r["open_pct_pullback"] for r in rows]
                    median_pullback = _pct_of(pulls, 50)
                    p75_pullback = _pct_of(pulls, 75)
                    open_eq_low_pct = (sum(1 for r in rows if r["open_was_low"]) / n) * 100.0
                    # Long side (overall): median pop + open-eq-high
                    pops = [r["open_pct_pop"] for r in rows]
                    median_pop = _pct_of(pops, 50)
                    p75_pop = _pct_of(pops, 75)
                    open_eq_high_pct = (sum(1 for r in rows if r["open_was_high"]) / n) * 100.0
                    # Gap-up subset (used for short verdict)
                    gap_rows = [r for r in rows if r["gap_pct"] >= 1.0]
                    gap_summary = None
                    if gap_rows:
                        g_pulls = [r["open_pct_pullback"] for r in gap_rows]
                        g_pops = [r["open_pct_pop"] for r in gap_rows]
                        gap_g_and_go = sum(1 for r in gap_rows if r["open_was_low"])
                        gap_summary = {
                            "n": len(gap_rows),
                            "median_pullback": _pct_of(g_pulls, 50),
                            "p75_pullback": _pct_of(g_pulls, 75),
                            "median_pop": _pct_of(g_pops, 50),
                            "p75_pop": _pct_of(g_pops, 75),
                            "gap_and_go_pct": (gap_g_and_go / len(gap_rows)) * 100.0,
                        }
                    # Gap-down subset (useful for long verdict)
                    gd_rows = [r for r in rows if r["gap_pct"] <= -1.0]
                    gap_down_summary = None
                    if gd_rows:
                        gd_pops = [r["open_pct_pop"] for r in gd_rows]
                        gd_eq_high = sum(1 for r in gd_rows if r["open_was_high"])
                        gap_down_summary = {
                            "n": len(gd_rows),
                            "median_pop": _pct_of(gd_pops, 50),
                            "p75_pop": _pct_of(gd_pops, 75),
                            "open_eq_high_pct": (gd_eq_high / len(gd_rows)) * 100.0,
                        }
                    results.append({
                        "symbol": symbol,
                        "samples": n,
                        "median_pullback": median_pullback,
                        "p75_pullback": p75_pullback,
                        "open_eq_low_pct": open_eq_low_pct,
                        "median_pop": median_pop,
                        "p75_pop": p75_pop,
                        "open_eq_high_pct": open_eq_high_pct,
                        "gap_up": gap_summary,
                        "gap_down": gap_down_summary,
                    })
                except Exception as exc:  # noqa: BLE001
                    results.append({"symbol": symbol, "error": str(exc)})
            try:
                body = _dumps({"results": results, "lookback_days": lookback_days}, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/pullback_profile":
            # Open-to-low pullback statistics for a ticker. Computes how
            # far below the opening price the stock typically trades
            # before making its intraday low. Used by the "Short the open"
            # decision card to gauge whether a strong-opening day is
            # likely to give a tradable pullback or run as gap-and-go.
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
            try:
                lookback_days = int(qs.get("days", ["180"])[0])
            except (TypeError, ValueError):
                lookback_days = 180
            lookback_days = max(60, min(500, lookback_days))
            payload = {"symbol": symbol, "samples": 0}
            try:
                bars = load_daily(symbol, lookback_days)
                # Each bar: open, high, low, close, volume, plus prev_close
                # we can derive from the previous bar's close.
                rows = []
                for i, b in enumerate(bars):
                    o = b.get("open"); h = b.get("high"); l = b.get("low")
                    c = b.get("close"); v = b.get("volume")
                    if not (o and h and l and c) or o <= 0:
                        continue
                    prev_c = bars[i - 1].get("close") if i > 0 else None
                    gap_pct = ((o - prev_c) / prev_c * 100.0) if (prev_c and prev_c > 0) else 0.0
                    # Pullback from open to intraday low, expressed as a
                    # POSITIVE percentage (so 1.5 means price dropped 1.5%
                    # below open at some point during the day).
                    pullback_pct = max(0.0, ((o - l) / o) * 100.0)
                    # Pop from open to intraday high, also POSITIVE percent.
                    # Mirror of pullback for the buy-the-open thesis.
                    pop_pct = max(0.0, ((h - o) / o) * 100.0)
                    open_was_low = (l >= o - 0.005)  # within 0.5 cents of open
                    open_was_high = (h <= o + 0.005)  # within 0.5 cents of open
                    rows.append({
                        "open_pct_pullback": pullback_pct,
                        "open_pct_pop": pop_pct,
                        "open_was_low": open_was_low,
                        "open_was_high": open_was_high,
                        "gap_pct": gap_pct,
                        "volume": v,
                        "open": o,
                        "close": c,
                        "high": h,
                        "low": l,
                        "date": b.get("date"),
                    })
                if not rows:
                    payload["error"] = "no historical bars available"
                else:
                    def _pct_stats(values: list[float]) -> dict:
                        n = len(values)
                        if n == 0:
                            return {"n": 0}
                        s = sorted(values)
                        def pct(p):
                            if n == 1:
                                return s[0]
                            k = (n - 1) * (p / 100.0)
                            f = int(k)
                            c = min(f + 1, n - 1)
                            return s[f] + (s[c] - s[f]) * (k - f)
                        return {
                            "n": n,
                            "mean": sum(values) / n,
                            "median": pct(50),
                            "p25": pct(25),
                            "p75": pct(75),
                            "p90": pct(90),
                            "max": s[-1],
                            "min": s[0],
                        }
                    def _threshold_freq(values, thresholds):
                        n = len(values)
                        if n == 0:
                            return {}
                        out = {}
                        for t in thresholds:
                            cnt = sum(1 for v in values if v >= t)
                            out[str(t)] = {
                                "count": cnt,
                                "pct": (cnt / n) * 100.0,
                            }
                        return out

                    THRESHOLDS = [0.25, 0.50, 0.75, 1.00, 1.50, 2.00, 3.00]

                    # Helper: build a full stat block for a subset of rows.
                    # Includes BOTH directions (short = open→low pullback,
                    # long = open→high pop) so the same payload can drive
                    # either trade thesis.
                    def _build_block(subset: list[dict]) -> dict:
                        if not subset:
                            return {"n": 0}
                        n = len(subset)
                        pulls = [r["open_pct_pullback"] for r in subset]
                        pops = [r["open_pct_pop"] for r in subset]
                        block = {"n": n}
                        block["short"] = _pct_stats(pulls)
                        block["short"]["open_eq_low_pct"] = (sum(1 for r in subset if r["open_was_low"]) / n) * 100.0
                        block["short"]["thresholds"] = _threshold_freq(pulls, THRESHOLDS)
                        block["long"] = _pct_stats(pops)
                        block["long"]["open_eq_high_pct"] = (sum(1 for r in subset if r["open_was_high"]) / n) * 100.0
                        block["long"]["thresholds"] = _threshold_freq(pops, THRESHOLDS)
                        return block

                    # Overall stats (all days)
                    overall = _build_block(rows)

                    # Gap-up days: prior close < open by ≥ 1%. Strong-open
                    # behavior is what we want to characterize for the
                    # short-the-open use case.
                    gap_rows = [r for r in rows if r["gap_pct"] >= 1.0]
                    gap_up = _build_block(gap_rows)
                    if gap_rows:
                        # Gap-and-go vs gap-fade vs normal pullback split
                        gap_and_go = sum(1 for r in gap_rows if r["open_was_low"])
                        gap_fade = sum(1 for r in gap_rows if (r["close"] / r["open"] - 1) * 100 <= -0.5)
                        normal_pullback = len(gap_rows) - gap_and_go - gap_fade
                        gap_up["gap_and_go_pct"] = (gap_and_go / len(gap_rows)) * 100.0
                        gap_up["gap_fade_pct"] = (gap_fade / len(gap_rows)) * 100.0
                        gap_up["normal_pullback_pct"] = (normal_pullback / len(gap_rows)) * 100.0

                    # Gap-down days for the long-the-open thesis (mirror).
                    # Stocks that gap down ≥ 1% — useful for buying weakness.
                    gap_down_rows = [r for r in rows if r["gap_pct"] <= -1.0]
                    gap_down = _build_block(gap_down_rows)

                    # Strong-gap (≥ 3%)
                    strong_gap_rows = [r for r in rows if r["gap_pct"] >= 3.0]
                    strong_gap = _build_block(strong_gap_rows) if len(strong_gap_rows) >= 10 else None

                    # High relative-volume days (top 25% of recent volume)
                    vols = [r["volume"] for r in rows if r["volume"]]
                    high_rvol = None
                    if len(vols) >= 30:
                        sorted_vols = sorted(vols)
                        vol_p75 = sorted_vols[int(len(sorted_vols) * 0.75)]
                        hr_rows = [r for r in rows if (r["volume"] or 0) >= vol_p75]
                        if len(hr_rows) >= 15:
                            high_rvol = _build_block(hr_rows)

                    payload = {
                        "symbol": symbol,
                        "samples": len(rows),
                        "lookback_days": lookback_days,
                        "overall": overall,
                        "gap_up": gap_up,
                        "gap_down": gap_down,
                        "strong_gap": strong_gap,
                        "high_rvol": high_rvol,
                    }
            except Exception as exc:  # noqa: BLE001
                payload = {"symbol": symbol, "error": str(exc)}
            try:
                body = _dumps(payload, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/option_quote":
            # Lookup current mid + greeks for a specific contract. Used by
            # the Roll Manager to value open short calls and price roll
            # candidates. Cached behind the standard chain TTL (30s).
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", [""])[0] or "").upper().strip()
            exp = qs.get("exp", [""])[0]
            try:
                strike = float(qs.get("strike", ["0"])[0])
            except (TypeError, ValueError):
                strike = 0.0
            opt_type = (qs.get("type", ["call"])[0] or "call").lower()
            results = {"found": False}
            if symbol and exp and strike > 0:
                sc = _schwab()
                chain_data = None
                if sc is not None:
                    try:
                        chain_data = sc.get_option_chain(symbol)
                    except Exception:
                        chain_data = None
                if chain_data and chain_data.get("chains"):
                    legs = chain_data["chains"].get(exp)
                    if legs:
                        rows = legs.get("calls" if opt_type == "call" else "puts") or []
                        # Find closest strike
                        if rows:
                            row = min(rows, key=lambda r: abs((r.get("strike") or 0) - strike))
                            if abs((row.get("strike") or 0) - strike) <= 0.01:
                                bid = float(row.get("bid") or 0)
                                ask = float(row.get("ask") or 0)
                                mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (bid or ask or 0)
                                results = {
                                    "found": True,
                                    "strike": float(row.get("strike") or 0),
                                    "exp": exp,
                                    "type": opt_type,
                                    "bid": bid,
                                    "ask": ask,
                                    "mid": round(mid, 2),
                                    "delta": row.get("delta"),
                                    "theta": row.get("theta"),
                                    "iv": row.get("iv"),
                                    "open_interest": row.get("open_interest"),
                                    "volume": row.get("volume"),
                                }
            try:
                body = _dumps(results, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/quote":
            # Lightweight quote endpoint for live-price polling. Accepts
            # comma-separated tickers, returns just last price + change.
            # Hits Schwab quote cache (25s TTL) so frequent polling stays
            # well under rate limits.
            qs = parse_qs(parsed.query)
            raw = (qs.get("tickers", [""])[0] or "").upper().strip()
            symbols = [s.strip() for s in raw.split(",") if s.strip()][:25]
            results = {}
            sc = _schwab()
            try:
                if sc is not None and symbols:
                    quotes = sc.get_quotes(symbols)
                    if quotes:
                        for sym, q in quotes.items():
                            if q and q.get("last"):
                                results[sym] = {
                                    "last": float(q["last"]),
                                    "change_pct": q.get("change_pct"),
                                    "session": q.get("session", "regular"),
                                    "source": "schwab",
                                    # Stale-quote labeling for illiquid
                                    # tickers / halts / session boundaries
                                    "stale_seconds": q.get("stale_seconds"),
                                    "trade_time_ms": q.get("trade_time_ms"),
                                }
                # Fallback for any missing symbols
                for sym in symbols:
                    if sym in results:
                        continue
                    try:
                        stock = yf.Ticker(sym)
                        h = stock.history(period="2d", auto_adjust=False)
                        if not h.empty:
                            last = float(h["Close"].iloc[-1])
                            prev = float(h["Close"].iloc[-2]) if len(h) >= 2 else last
                            results[sym] = {
                                "last": last,
                                "change_pct": ((last - prev) / prev) * 100.0 if prev else 0.0,
                                "source": "yfinance",
                            }
                    except Exception:
                        continue
                body = _dumps({"results": results}, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self._cors_headers()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=500)
            return
        if parsed.path == "/api/ticker":
            qs = parse_qs(parsed.query)
            symbol = (qs.get("symbol", ["AAPL"])[0] or "AAPL").upper().strip()
            try:
                weeks = int(qs.get("weeks", [str(self.weeks)])[0])
            except (TypeError, ValueError):
                weeks = self.weeks
            baseline_q = (qs.get("baseline", [""])[0] or "").lower().strip()
            if baseline_q in ("monday", "friday"):
                friday_baseline = (baseline_q == "friday")
            else:
                friday_baseline = self.friday_baseline
            target_exp = (qs.get("expiration", [""])[0] or "").strip() or None
            cache_key = (symbol, weeks, friday_baseline, target_exp)
            # Serve a recent build instantly (TTL cache) — makes flipping back
            # to a just-viewed symbol feel instant instead of a full rebuild.
            now = time.time()
            with _TICKER_LG_LOCK:
                hit = _TICKER_FRESH.get(cache_key)
            if hit is not None and (now - hit[0]) < _TICKER_TTL:
                self._send_json(hit[1], no_store=True, default=str)
                return
            try:
                payload = build_payload(symbol, weeks, friday_baseline, target_exp)
                with _TICKER_LG_LOCK:
                    _TICKER_LAST_GOOD[cache_key] = payload
                    _TICKER_FRESH[cache_key] = (time.time(), payload)
                    while len(_TICKER_LAST_GOOD) > _TICKER_LG_MAX:
                        _TICKER_LAST_GOOD.pop(next(iter(_TICKER_LAST_GOOD)))
                    while len(_TICKER_FRESH) > _TICKER_LG_MAX:
                        _TICKER_FRESH.pop(next(iter(_TICKER_FRESH)))
                self._send_json(payload, no_store=True, default=str)
            except Exception as exc:  # noqa: BLE001
                # Serve the last good payload with a stale flag rather
                # than a hard error, so one upstream hiccup does not
                # blank the dashboard (v1.39).
                with _TICKER_LG_LOCK:
                    stale = _TICKER_LAST_GOOD.get(cache_key)
                if stale is not None:
                    out = dict(stale)
                    out["stale"] = True
                    out["stale_error"] = str(exc)
                    self._send_json(out, no_store=True, default=str)
                else:
                    self._send_json({"error": str(exc), "symbol": symbol}, status=500)
            return
        # Unmatched /api/* path — return JSON 404 instead of letting
        # SimpleHTTPRequestHandler serve an HTML error page (which would
        # crash the frontend's r.json() call with "Unexpected token '<'").
        if parsed.path.startswith("/api/"):
            self._send_json({"error": "endpoint not found", "path": parsed.path}, status=404)
            return
        # Make root and /index serve the live dashboard. If a baked
        # options_dashboard.html exists, serve it; otherwise fall back to
        # the raw index.html which auto-fetches /api/ticker on load.
        if parsed.path in ("/", "/index", "/index.html"):
            self.path = "/options_dashboard.html" if (HERE / "options_dashboard.html").exists() else "/index.html"
        super().do_GET()

    def end_headers(self):  # noqa: N802
        # Disable browser caching for our static assets so users always see
        # the latest JS/CSS without needing a hard refresh.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format, *args):  # noqa: A002
        # quieter logs
        sys.stderr.write("· %s %s\n" % (self.address_string(), format % args))


def serve(host: str, port: int, weeks: int, friday_baseline: bool) -> None:
    DashboardHandler.weeks = weeks
    DashboardHandler.friday_baseline = friday_baseline
    # Prewarm the ticker autocomplete index in the background. Doing this off
    # thread keeps server startup snappy while ensuring the first /api/search
    # call is instant.
    import threading
    threading.Thread(target=load_ticker_index, daemon=True).start()
    # Auto-build the analyst board each weekday at 8:00 AM ET so the
    # morning game plan is ready before the open.
    if _ANALYST_BOARD_AVAILABLE:
        try:
            def _wl_syms():
                try:
                    wl = _load_watchlist()
                    return [s.get("symbol") for s in (wl.get("symbols") or []) if s.get("symbol")]
                except Exception:
                    return []

            def _morning_push(title, message):
                # No-op unless a push provider is configured (free ntfy via
                # NTFY_TOPIC, or Pushover).
                if _push_configured():
                    try:
                        _push_notify(title, message, priority=0)
                    except Exception as e:  # noqa: BLE001
                        print(f"[analyst_board] push failed: {e}", file=sys.stderr)

            _analyst_board.start_scheduler(
                get_watchlist_fn=_wl_syms, notify_fn=_morning_push,
                hour=8, minute=0)
        except Exception as exc:  # noqa: BLE001
            print(f"[analyst_board] scheduler start failed: {exc}", file=sys.stderr)
    # Auto-refresh the watchlist metrics table at 9 AM and 6 PM ET each
    # weekday so the board is current without a manual scan. Results are
    # cached to /data (survives restarts) and shared across all devices.
    if _WLTABLE_AVAILABLE:
        try:
            def _wlt_syms():
                try:
                    wl = _load_watchlist()
                    return [s.get("symbol") for s in (wl.get("symbols") or []) if s.get("symbol")]
                except Exception:
                    return []

            # Inject the UW options-flow scorer so the watchlist table can
            # show flow agreement per symbol. Degrades silently when UW is
            # off; the UW client self-throttles to stay within the budget.
            def _wlt_flow(symbol, price=0.0):
                try:
                    if _UW_AVAILABLE and _uw_client is not None:
                        uw = _uw_client.get_client()
                        if uw is not None:
                            return _compute_flow_score(uw, symbol, float(price or 0.0))
                except Exception:
                    return None
                return None

            _wltable.set_flow_provider(_wlt_flow)

            def _wlt_notify(title, message):
                # Morning Prime-setup push; no-op unless a provider is set up.
                if _push_configured():
                    try:
                        _push_notify(title, message, priority=0)
                    except Exception as e:  # noqa: BLE001
                        print(f"[watchlist_table] push failed: {e}", file=sys.stderr)

            _wltable.set_notify_provider(_wlt_notify)
            _wltable.start_scheduler(_wlt_syms)
        except Exception as exc:  # noqa: BLE001
            print(f"[watchlist_table] scheduler start failed: {exc}", file=sys.stderr)
    # A stalled upstream socket (yfinance has no timeout of its own) can
    # otherwise hang a request thread indefinitely. 15s applies per
    # blocking socket operation, so slow but flowing transfers are fine;
    # only a dead connection aborts (v1.39).
    socket.setdefaulttimeout(15.0)
    httpd = ThreadingHTTPServer((host, port), DashboardHandler)
    url = f"http://{host if host != '0.0.0.0' else 'localhost'}:{port}/options_dashboard.html"
    print(f"🌐  Serving {HERE}")
    print(f"    Open: {url}")
    print(f"    API:  /api/ticker?symbol=TSLA")
    print(f"    Press Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋  Stopped.")


# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser(description="Live options dashboard (bake or serve).")
    ap.add_argument("ticker", nargs="?", default="AAPL")
    ap.add_argument("--weeks", type=int, default=12)
    ap.add_argument("--baseline", choices=["monday", "friday"], default="monday",
                    help="monday = Monday Open (default); friday = previous Friday Close")
    ap.add_argument("--buffer", type=float, default=2.0,
                    help="Strike buffer %% (UI default; the dashboard slider overrides this live)")
    ap.add_argument("--open", action="store_true", help="Open the dashboard in your browser (off by default)")
    ap.add_argument("--no-open", action="store_true", help="(deprecated, kept for back-compat)")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--serve", action="store_true",
                    help="Run as a website: serve index.html + a /api/ticker endpoint that pulls live Yahoo data.")
    ap.add_argument("--bake", action="store_true",
                    help="With --serve, also bake a static options_dashboard.html on startup. Default: skip.")
    # Host and port can be overridden by env vars HOST/PORT, which is how
    # Railway (and most PaaS hosts) communicate the bind config. CLI flags
    # win if both are present so local dev is unaffected.
    default_host = os.environ.get("HOST", "127.0.0.1")
    default_port = int(os.environ.get("PORT", "8765"))
    ap.add_argument("--host", default=default_host)
    ap.add_argument("--port", type=int, default=default_port)
    args = ap.parse_args()

    ticker = args.ticker.upper().strip()

    # ── Serve mode: act as a website. Don't bake unless --bake. The frontend
    #    auto-fetches /api/ticker?symbol=… on load.
    if args.serve:
        # Remove a stale baked HTML so / serves the live index.html.
        if not args.bake:
            stale = Path(args.out)
            if stale.exists():
                try: stale.unlink()
                except OSError: pass
        else:
            print(f"📈  Baking {ticker} (weeks={args.weeks}, baseline={args.baseline})…")
            payload = build_payload(ticker, args.weeks, args.baseline == "friday")
            html = bake(payload)
            Path(args.out).resolve().write_text(html, encoding="utf-8")
            print(f"✅  Wrote {args.out}")
        serve(args.host, args.port, args.weeks, args.baseline == "friday")
        return

    # ── Bake mode (default when --serve is not passed)
    print(f"📈  Fetching {ticker} (weeks={args.weeks}, baseline={args.baseline})…")
    payload = build_payload(ticker, args.weeks, args.baseline == "friday")
    print(f"    {len(payload['rows'])} weeks · {len(payload['daily'])} daily bars · "
          f"{len(payload['chain']['calls'])} call strikes · expiration {payload['expiration']}")
    if payload["current"].get("earnings"):
        print(f"    ⚠ Earnings this week ({payload['current'].get('earningsDate')})")

    html = bake(payload)
    out_path = Path(args.out).resolve()
    out_path.write_text(html, encoding="utf-8")
    print(f"✅  Wrote {out_path}")
    if args.open and not args.no_open:
        webbrowser.open(out_path.as_uri(), new=0)


if __name__ == "__main__":
    main()
