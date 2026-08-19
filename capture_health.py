"""capture_health.py — did today's data actually get captured?

The Investment tab's option history cannot be back-filled. There is no
source of historical option chains this app can reach, so a day that goes
uncaptured is gone for good, and the only thing worse than losing a day is
not finding out for six months — during a backtest, when the answer turns
out to rest on a store with a hole in it.

So every prospective capture writes down what it tried and what happened,
and the next morning the app can say COMPLETE, PARTIAL or MISSED for the
day before. Five things are expected on a trading day:

  the Investment snapshot        the valuation state, per followed ticker
  the option chain               the near-dated end-of-day chain
  the LEAPS observation          the long-dated contracts and their volatility
  the benchmark reference        the sector benchmark's close, for validation
  the recommended contract       the exact contract the app recommended, priced

WHAT THIS IS NOT

  * It is not a back-fill. A missed option chain stays missed. This module
    reports the hole; it never fills it.
  * It is not a second store of prices. It records attempts and outcomes,
    lives in its own directory, and is deleted-and-rebuilt safe. The
    Investment recommendation history is immutable and separate.
  * It is not a guess about market hours. A Saturday is NOT EXPECTED, not
    MISSED, and neither is Thanksgiving. The calendar is computed from the
    rules the exchange publishes rather than typed out year by year, so it
    does not expire.

WHY A CALENDAR AT ALL

Because without one, a capture that ran on Sunday against Friday's stale
quotes would be recorded as a Sunday chain, and a backtest would later fill
a Sunday from it. Both the capture and the health report ask the same
question first: was this a day the market traded?
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

CAPTURE_HEALTH_VERSION = "invest-capture-health-1.0.0"

# ── what is expected ────────────────────────────────────────────────────────

SNAPSHOT = "investment_snapshot"
CHAIN = "option_chain"
LEAPS = "leaps_observation"
BENCHMARK = "benchmark_snapshot"
CONTRACT = "recommended_quote"

KINDS = (SNAPSHOT, CHAIN, LEAPS, BENCHMARK, CONTRACT)

KIND_LABEL = {
    SNAPSHOT: "Investment snapshot",
    CHAIN: "End-of-day option chain",
    LEAPS: "Long-dated contract observation",
    BENCHMARK: "Sector benchmark close",
    CONTRACT: "Recommended contract quote",
}

KIND_NOTE = {
    SNAPSHOT: "The whole valuation state for one ticker on one day. This is "
              "what forward validation scores later, so a day without it is "
              "a day that can never be scored.",
    CHAIN: "The near-dated option chain after the close. This is the one "
           "that can never be recovered: there is no source of historical "
           "chains this app can reach, so a missed day stays missed.",
    LEAPS: "The long-dated contracts around the money and their implied "
           "volatility, which is what gives a LEAPS its own volatility "
           "history instead of a borrowed one.",
    BENCHMARK: "The close of the sector benchmark this ticker will be "
               "measured against. Recorded on the day the recommendation is "
               "made so the benchmark cannot be chosen afterwards.",
    CONTRACT: "The exact contract the app recommended, with the price it "
              "was quoted at. Without it, a later result is about a "
              "strategy rather than about a trade.",
}

COMPLETE, PARTIAL, MISSED, NOT_EXPECTED = (
    "COMPLETE", "PARTIAL", "MISSED", "NOT EXPECTED")

HEALTHY, HEALTH_PARTIAL, FAILURE = "HEALTHY", "PARTIAL", "CAPTURE FAILURE"


# ── the market calendar ─────────────────────────────────────────────────────
#
# Computed, not tabulated. A hard-coded table of dates is a thing that stops
# being true on a date nobody remembers; these rules have not changed in
# decades and Juneteenth, the only recent addition, has a start year.

def easter(year: int) -> date:
    """Easter Sunday, by the anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lam = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * lam) // 433
    month = (h + lam - 7 * m + 90) // 25
    day = (h + lam - 7 * m + 33 * month + 19) % 32
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(days=7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), 1 if month == 12 else month + 1, 1) \
        - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date | None:
    """A fixed-date holiday moved off the weekend, the way the exchange
    moves it: back to Friday when it falls on a Saturday, forward to Monday
    when it falls on a Sunday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def holidays(year: int) -> dict:
    """Every day the US equity market is closed in this year, and its name."""
    out: dict[date, str] = {}

    def put(d, name):
        if d is not None and d.year == year:
            out[d] = name

    # New Year's Day falling on a Saturday is the one exception to the
    # observance rule: the exchange does not close the Friday before it.
    ny = date(year, 1, 1)
    put(ny if ny.weekday() < 5 else
        (ny + timedelta(days=1) if ny.weekday() == 6 else None),
        "New Year's Day")
    put(_nth_weekday(year, 1, 0, 3), "Martin Luther King, Jr. Day")
    put(_nth_weekday(year, 2, 0, 3), "Washington's Birthday")
    put(easter(year) - timedelta(days=2), "Good Friday")
    put(_last_weekday(year, 5, 0), "Memorial Day")
    if year >= 2022:
        put(_observed(date(year, 6, 19)), "Juneteenth National Independence Day")
    put(_observed(date(year, 7, 4)), "Independence Day")
    put(_nth_weekday(year, 9, 0, 1), "Labor Day")
    put(_nth_weekday(year, 11, 3, 4), "Thanksgiving Day")
    put(_observed(date(year, 12, 25)), "Christmas Day")
    return out


def _as_date(day) -> date:
    if isinstance(day, date):
        return day
    return date.fromisoformat(str(day)[:10])


def is_trading_day(day) -> bool:
    d = _as_date(day)
    return d.weekday() < 5 and d not in holidays(d.year)


def why_not_trading(day) -> str:
    d = _as_date(day)
    if d.weekday() >= 5:
        return "a weekend"
    name = holidays(d.year).get(d)
    return name if name else ""


def trading_days(start, end) -> list:
    """Every trading day from start to end, both included."""
    a, b = _as_date(start), _as_date(end)
    out, d = [], a
    while d <= b:
        if is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def previous_trading_day(day) -> str:
    d = _as_date(day) - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(d):
            return d.isoformat()
        d -= timedelta(days=1)
    return d.isoformat()                                 # pragma: no cover


# ── the log ─────────────────────────────────────────────────────────────────
#
# One small JSON per day, in its own directory. This is operational data:
# it can be thrown away and rebuilt from tomorrow onward without touching a
# single stored recommendation, which is exactly why it lives apart from
# them.

_DIR: Path | None = None
_LOCK = threading.Lock()
_MEM: dict = {}
KEEP_DAYS = 400


def configure(data_dir=None, keep_days=None) -> None:
    global _DIR, KEEP_DAYS
    _MEM.clear()
    if keep_days:
        KEEP_DAYS = int(keep_days)
    if not data_dir:
        _DIR = None
        return
    _DIR = Path(data_dir) / "invest" / "capture"
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
    except Exception:                                    # pragma: no cover
        _DIR = None


def _path(day: str) -> Path | None:
    if _DIR is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        return None
    return _DIR / f"{day}.json"


def day_log(day) -> dict:
    """What was attempted on one day."""
    d = _as_date(day).isoformat()
    if d in _MEM:
        return _MEM[d]
    p = _path(d)
    if p is not None and p.exists():
        try:
            got = json.loads(p.read_text())
            _MEM[d] = got
            return got
        except Exception:                                # pragma: no cover
            pass
    return {"date": d, "kinds": {}, "version": CAPTURE_HEALTH_VERSION}


def _write(log: dict) -> bool:
    p = _path(log["date"])
    _MEM[log["date"]] = log
    if p is None:
        return False
    try:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(log, separators=(",", ":"), sort_keys=True))
        tmp.replace(p)
    except Exception:                                    # pragma: no cover
        return False
    return True


def record(kind: str, symbol: str, ok: bool, day=None, source: str = "",
           records: int = 0, reason: str = "", late: bool = False,
           at: str | None = None, attempted: bool = True) -> dict:
    """Write down one capture attempt.

    `late` marks a capture made outside the normal window — after a restart,
    say. It is stamped rather than hidden, because a quote taken at eight in
    the evening is a different thing from one taken at the close, and a
    reader is entitled to know which they are looking at.
    """
    d = _as_date(day or date.today()).isoformat()
    sym = (symbol or "").upper().strip()
    entry = {"attempted": bool(attempted), "ok": bool(ok),
             "at": at or datetime.now().isoformat(timespec="seconds"),
             "source": source or "", "records": int(records or 0),
             "reason": reason or "", "late": bool(late)}
    with _LOCK:
        log = dict(day_log(d))
        kinds = dict(log.get("kinds") or {})
        bucket = dict(kinds.get(kind) or {})
        bucket[sym] = entry
        kinds[kind] = bucket
        log["kinds"] = kinds
        log["trading_day"] = is_trading_day(d)
        log["version"] = CAPTURE_HEALTH_VERSION
        _write(log)
        _prune()
    return entry


def _prune() -> None:
    if _DIR is None:
        return
    try:
        files = sorted(_DIR.glob("????-??-??.json"))
    except Exception:                                    # pragma: no cover
        return
    for old in files[:-KEEP_DAYS]:
        try:
            old.unlink()
            _MEM.pop(old.stem, None)
        except Exception:                                # pragma: no cover
            pass


def captured(kind: str, symbol: str, day=None) -> bool:
    """Has this already been captured successfully today?

    Asked before a capture runs, so a restart does not repeat work that
    already succeeded, and asked after one so a second attempt on the same
    day is recognised as unnecessary rather than as a failure.
    """
    d = _as_date(day or date.today()).isoformat()
    got = ((day_log(d).get("kinds") or {}).get(kind) or {}).get(
        (symbol or "").upper().strip())
    return bool(got and got.get("ok"))


# ── what the log says ───────────────────────────────────────────────────────

def expect(expected: dict, day=None) -> dict:
    """Write down which tickers each kind of capture was DUE for on a day.

    Without this, a past day can only be judged against the watchlist as it
    stands NOW — so starring a ticker this morning would report yesterday as
    having missed it, and unstarring one would make a real miss disappear.
    The list is unioned rather than replaced: a ticker that was due at five
    was due, whatever the watchlist says by eight.
    """
    d = _as_date(day or date.today()).isoformat()
    with _LOCK:
        log = dict(day_log(d))
        was = dict(log.get("expected") or {})
        for kind, syms in (expected or {}).items():
            merged = set(was.get(kind) or [])
            merged |= {(s or "").upper().strip() for s in (syms or []) if s}
            was[kind] = sorted(merged)
        log["expected"] = was
        log["trading_day"] = is_trading_day(d)
        log["version"] = CAPTURE_HEALTH_VERSION
        _write(log)
    return was


def expected_on(day, fallback: dict | None = None) -> dict:
    """What a given day was actually due, and where that answer came from.

    Two answers only: what the day itself recorded as due, and — failing
    that — the watchlist as it stands now, which is a guess about the past
    and is labelled as one.

    Deliberately NOT "what the day was seen attempting". A ticker the run
    never reached leaves no trace at all, so judging a day against its own
    attempts would call every abandoned run complete — which is the one
    failure this whole report exists to catch.
    """
    d = _as_date(day).isoformat()
    recorded = day_log(d).get("expected") or {}
    if any(recorded.get(k) for k in KINDS):
        return {"expected": {k: list(recorded.get(k) or []) for k in KINDS},
                "basis": "recorded",
                "note": ("what this day itself wrote down as due, before it "
                         "tried to do any of it.")}
    return {"expected": {k: list((fallback or {}).get(k) or []) for k in KINDS},
            "basis": "watchlist now",
            "note": ("the watchlist as it stands now. This day wrote down "
                     "nothing about what it was due — it predates that being "
                     "recorded, or it never ran — so the current list is "
                     "standing in for it. Starring a ticker since then would "
                     "make this day look as though it missed one.")}


def day_status(day, expected: dict | None = None) -> dict:
    """COMPLETE, PARTIAL, MISSED or NOT EXPECTED, per kind and overall.

    `expected` maps each kind to the tickers that should have been captured.
    It is a FALLBACK: a day that recorded what it was due is judged against
    that instead, because the watchlist as it stands now is not evidence
    about a day that has already happened.

    A weekend or a market holiday expects nothing and is reported as such,
    because calling a Saturday a failure would bury the days that are.
    """
    d = _as_date(day).isoformat()
    log = day_log(d)
    trading = is_trading_day(d)
    basis = expected_on(d, expected)
    expected = basis["expected"]
    out = {"date": d, "trading_day": trading, "kinds": {},
           "state": NOT_EXPECTED, "missing": {},
           "expected_basis": basis["basis"], "expected_note": basis["note"],
           "reason": "", "version": CAPTURE_HEALTH_VERSION}
    if not trading:
        out["reason"] = (
            f"{_pretty(d)} was {why_not_trading(d) or 'not a trading day'}, "
            f"so nothing was expected and nothing is missing.")
        return out

    states = []
    for kind in KINDS:
        want = sorted({(s or "").upper().strip()
                       for s in ((expected or {}).get(kind) or [])})
        have = (log.get("kinds") or {}).get(kind) or {}
        ok = sorted(s for s in want if (have.get(s) or {}).get("ok"))
        failed = sorted(s for s in want if s not in ok)
        block = {"kind": kind, "label": KIND_LABEL[kind],
                 "note": KIND_NOTE[kind],
                 "expected": len(want), "successful": len(ok),
                 "missing": failed[:40],
                 "late": sorted(s for s in ok if (have.get(s) or {}).get("late")),
                 "reasons": sorted({(have.get(s) or {}).get("reason") or ""
                                    for s in failed} - {""})[:4]}
        if not want:
            block["state"] = NOT_EXPECTED
        elif not ok:
            block["state"] = MISSED
        elif failed:
            block["state"] = PARTIAL
        else:
            block["state"] = COMPLETE
        out["kinds"][kind] = block
        if want:
            states.append(block["state"])
            if failed:
                out["missing"][kind] = failed[:40]

    if not states:
        out["state"] = NOT_EXPECTED
        out["reason"] = ("Nothing was expected on this day: no ticker is "
                         "being followed, so there is nothing to capture.")
    elif all(s == COMPLETE for s in states):
        out["state"] = COMPLETE
        out["reason"] = ("Everything expected on this trading day was "
                         "captured.")
    elif all(s == MISSED for s in states):
        out["state"] = MISSED
        out["reason"] = (
            f"Nothing was captured on {_pretty(d)}, which was a trading day. "
            f"The Investment snapshot can be rebuilt from filings; the option "
            f"chain for that day cannot, and is gone.")
    else:
        out["state"] = PARTIAL
        n = sum(len(v) for v in out["missing"].values())
        out["reason"] = (
            f"{n} expected capture{'s' if n != 1 else ''} did not happen on "
            f"{_pretty(d)}. What is listed below is what is missing.")
    return out


def health(expected: dict | None = None, days_back: int = 5,
           today=None) -> dict:
    """One state for the whole capture system, with the reasons behind it.

    HEALTHY means the last trading day is complete. PARTIAL means something
    was missed and most of it arrived. CAPTURE FAILURE means a whole trading
    day produced nothing, which is the state worth being told about.
    """
    end = _as_date(today or date.today())
    days = []
    d = end
    while len(days) < max(1, days_back) and (end - d).days < 30:
        if is_trading_day(d):
            days.append(d.isoformat())
        d -= timedelta(days=1)
    rows = [day_status(x, expected) for x in days]
    considered = [r for r in rows if r["state"] != NOT_EXPECTED]
    out = {"state": HEALTHY, "reason": "", "days": rows,
           "last_trading_day": days[0] if days else None,
           "last_successful": None, "alert": "",
           "version": CAPTURE_HEALTH_VERSION}
    for r in rows:
        if r["state"] == COMPLETE:
            out["last_successful"] = r["date"]
            break
    if not considered:
        out["reason"] = ("Nothing has been expected on the trading days "
                         "looked at, so there is nothing to report.")
        return out
    newest = considered[0]
    if newest["state"] == MISSED:
        out["state"] = FAILURE
        missing = sum(len(v) for v in newest["missing"].values())
        out["reason"] = newest["reason"]
        out["alert"] = (f"Investment data capture produced nothing on "
                        f"{_pretty(newest['date'])} — {missing} expected "
                        f"captures across {len(newest['missing'])} kinds.")
    elif newest["state"] == PARTIAL:
        out["state"] = HEALTH_PARTIAL
        syms = sorted({s for v in newest["missing"].values() for s in v})
        out["reason"] = newest["reason"]
        out["alert"] = (
            f"Investment data capture incomplete for {len(syms)} followed "
            f"symbol{'s' if len(syms) != 1 else ''} on "
            f"{_pretty(newest['date'])}: {', '.join(syms[:8])}"
            + ("…" if len(syms) > 8 else ""))
    else:
        out["reason"] = newest["reason"]
    older = [r for r in considered[1:] if r["state"] in (MISSED, PARTIAL)]
    if older:
        out["earlier_gaps"] = [{"date": r["date"], "state": r["state"]}
                               for r in older]
    return out


def symbol_coverage(symbol: str, first_day: str | None = None,
                    today=None, days_back: int = 120) -> dict:
    """Which trading days this ticker was captured on, and which it was not.

    Counted from the first day anything was captured for it, because a
    ticker followed since Tuesday has not missed the Monday before.
    """
    sym = (symbol or "").upper().strip()
    end = _as_date(today or date.today())
    start = _as_date(first_day) if first_day else end - timedelta(days=days_back)
    days = trading_days(max(start, end - timedelta(days=days_back * 2)), end)
    out = {"symbol": sym, "trading_days": len(days), "kinds": {},
           "version": CAPTURE_HEALTH_VERSION}
    for kind in KINDS:
        ok, missing = [], []
        for d in days:
            got = ((day_log(d).get("kinds") or {}).get(kind) or {}).get(sym)
            (ok if (got or {}).get("ok") else missing).append(d)
        out["kinds"][kind] = {
            "label": KIND_LABEL[kind], "captured": len(ok),
            "first": ok[0] if ok else None, "last": ok[-1] if ok else None,
            "missing_days": [d for d in missing if not ok or d >= ok[0]][-30:],
            "coverage_pct": (len(ok) / len(days) * 100.0) if days else None,
        }
    return out


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _pretty(iso) -> str:
    try:
        y, m, d = (int(x) for x in str(iso)[:10].split("-"))
        return f"{_MONTHS[m - 1]} {d}, {y}"
    except Exception:
        return str(iso or "")
