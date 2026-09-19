"""Year to date, one definition (v5.20).

The sidebar's ticker card and its watchlist chips ask the same question:
what has this stock done this year? The answer is the live price against
last year's final close — the same anchor the watchlist board's YTD column
measures from (`watchlist_table._period_ret`).

Two pieces:

* `base_close(daily, year)` — the anchor itself: the last bar dated before
  January 1 of the given year. The year is the CLOCK's, not the latest
  bar's: from January 1 until the first bar of the new year prints, the
  latest bar is still dated last year, and taking its year would anchor two
  year-ends back and call all of last year "year to date" (Codex, #406).

* `bases(symbols)` — that anchor for a handful of symbols at once, cached.
  The base only changes when the year does, so a symbol costs one history
  fetch per day at most, and nothing at all once the cache is warm. The
  cache also carries the latest close, so a chip still reads correctly
  outside market hours when no live quote is polled.

Nothing here raises: a symbol whose history cannot be loaded, or whose bars
stop short of last year, is simply absent from the answer, and the line it
would have drawn stays off rather than showing a number measured from the
wrong day.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

# The most chips a sidebar can ask about in one call — the same cap
# /api/quote uses, for the same reason: this is a foreground request.
MAX_SYMBOLS = 25

# How far back to ask for bars. A year plus the run-up to it, with room for
# a symbol whose history is thin around the turn of the year.
LOOKBACK_DAYS = 400

_LOCK = threading.RLock()
_CACHE: dict[str, dict] = {}   # SYM -> {"year": int, "day": "YYYY-MM-DD", "base": float, "last": float}
_DATA_DIR: Path | None = None
_BARS_FN = None
_NOW_FN = None


def configure(data_dir=None, bars_fn=None, now_fn=None) -> None:
    """Wire the module to its host: where to persist, how to get bars for a
    symbol (`bars_fn(symbol, days) -> [{"date": ..., "close": ...}]`), and
    what time it is (Eastern, so the year turns when the market's does)."""
    global _DATA_DIR, _BARS_FN, _NOW_FN
    with _LOCK:
        _DATA_DIR = Path(data_dir) if data_dir else None
        _BARS_FN = bars_fn
        _NOW_FN = now_fn
        _CACHE.clear()
        _CACHE.update(_load_json("ytd_bases.json", {}))


# ── persistence ─────────────────────────────────────────────────────────────
def _path(name: str) -> Path | None:
    return (_DATA_DIR / name) if _DATA_DIR else None


def _load_json(name: str, default):
    p = _path(name)
    if not p or not p.exists():
        return default
    try:
        out = json.loads(p.read_text())
        return out if isinstance(out, dict) else default
    except Exception:  # noqa: BLE001
        return default


def _save() -> None:
    p = _path("ytd_bases.json")
    if not p:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_CACHE, separators=(",", ":")))
        tmp.replace(p)
    except Exception:  # noqa: BLE001
        pass


# ── the clock ───────────────────────────────────────────────────────────────
def _now() -> datetime:
    if _NOW_FN is not None:
        try:
            return _NOW_FN()
        except Exception:  # noqa: BLE001
            pass
    return datetime.now()


# ── the anchor ──────────────────────────────────────────────────────────────
def base_close(daily, year=None) -> float | None:
    """The close year to date is measured from: the last bar dated before
    January 1 of `year` (the current year when not given). None when the
    bars stop short of last year. Dates are ISO strings, so the year is the
    first four characters and compares as text."""
    try:
        if not daily:
            return None
        if year is None:
            year = _now().year
        year = str(year)
        for row in reversed(daily):
            d = str(row.get("date") or "")[:4]
            if len(d) == 4 and d < year:
                close = row.get("close")
                return float(close) if close is not None and float(close) > 0 else None
        return None
    except Exception:  # noqa: BLE001
        return None


def _last_close(daily) -> float | None:
    try:
        for row in reversed(daily):
            close = row.get("close")
            if close is not None and float(close) > 0:
                return float(close)
    except Exception:  # noqa: BLE001
        pass
    return None


# ── many symbols at once ────────────────────────────────────────────────────
def bases(symbols) -> dict[str, dict]:
    """`{"SYM": {"base": <last year's final close>, "last": <latest close>}}`
    for the symbols that have one. Cached per symbol per day, so a warm
    sidebar costs nothing and a cold one costs one history fetch a name."""
    now = _now()
    today = now.strftime("%Y-%m-%d")
    year = now.year
    wanted, seen = [], set()
    for s in (symbols or []):
        sym = str(s or "").strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            wanted.append(sym)
        if len(wanted) >= MAX_SYMBOLS:
            break

    out: dict[str, dict] = {}
    misses: list[str] = []
    with _LOCK:
        for sym in wanted:
            row = _CACHE.get(sym)
            if isinstance(row, dict) and row.get("year") == year and row.get("day") == today:
                # A remembered miss (no base) is remembered as such: asking
                # the provider again today would give the same answer.
                if row.get("base") is not None:
                    out[sym] = {"base": row["base"], "last": row.get("last")}
            else:
                misses.append(sym)

    if misses and _BARS_FN is not None:
        fresh: dict[str, dict] = {}
        for sym in misses:
            try:
                daily = _BARS_FN(sym, LOOKBACK_DAYS)
            except Exception:  # noqa: BLE001
                daily = None
            if not daily:
                # A fetch that FAILED is not a remembered miss: the answer
                # may be there on the next poll, and caching the failure for
                # a day would hide the chip until tomorrow.
                continue
            base = base_close(daily, year)
            fresh[sym] = {"year": year, "day": today,
                          "base": base, "last": _last_close(daily)}
            if base is not None:
                out[sym] = {"base": base, "last": fresh[sym]["last"]}
        if fresh:
            with _LOCK:
                _CACHE.update(fresh)
                _save()
    return out
