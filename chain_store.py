"""chain_store.py — end-of-day option-chain snapshots.

The self-building real-data layer: every live chain the app already fetches
(juice scans, ticker loads, the expected-move engine, and from Phase 5 the
covered-call capture below) is snapshotted once per symbol per day.
Backtests then price fills from REAL bid/ask where a snapshot exists for
that date and fall back to the model elsewhere — with each fill labelled
`real_quote` or `modeled`, and the run reporting its real-quote coverage.
Accuracy compounds forever: after a few months of normal use, short-dated
tests on followed names fill mostly from real markets.

NEVER BACK-FILLED. There is no source of historical option chains this app
can reach, and inventing one would poison every backtest built on top of it.
A date with no snapshot stays empty for good. That is why coverage is
reported rather than assumed, and why the covered-call simulator labels its
runs REAL CHAIN BACKTEST, PART REAL, or MODEL-BASED ESTIMATE.

STORAGE

One JSON per symbol under <data_dir>/chains/SYM.json:

  {"YYYY-MM-DD": {"spot": 123.45, "v": 2, "src": "schwab", "ts": "...",
                  "ev": {...},
                  "exps": {"YYYY-MM-DD": {"c": [[row], ...], "p": [...]}}}}

A row is

  [strike, bid, ask, iv, delta, open interest, last, volume, quality]

The first six are the version-1 layout and are read unchanged, so every
snapshot taken before Phase 5 keeps working exactly as it did. The last
three were added because Phase 5 asked that each stored observation retain
its last trade, its volume, its source and its quality; a version-1 row
simply has none of them, and a reader must treat that as "not recorded that
day" rather than as zero.

Expirations beyond 75 calendar days and strikes further than 30% from spot
are dropped. That band is what the short-dated work needs — the covered-call
tenors run to 45 days and its strike rules reach at most a quarter above
spot — and keeping it tight is what makes a daily snapshot of a watchlist
affordable. Long-dated contracts have their own observation path in
`invest_options.py` and are deliberately not stored here.

Atomic writes, trimmed to the newest 500 dates, thread-safe.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path

_DIR: Path | None = None
_LOCK = threading.Lock()
_RECORDED_TODAY: dict = {}         # sym -> date already written this process

MAX_DTE = 75
STRIKE_BAND = 0.30
MAX_DAYS_KEPT = 500

SCHEMA = 2

# Quote quality, stored as a small code per row so a day of chain costs
# bytes rather than kilobytes. A model fill and a real fill are already
# told apart elsewhere; this says how good the REAL one was, because a
# two-sided market a penny wide and a one-sided market are both "real" and
# only one of them is a price anybody could have traded at.
Q_UNKNOWN, Q_TWO_SIDED, Q_WIDE, Q_ONE_SIDED, Q_STALE = 0, 1, 2, 3, 4

QUALITY_LABEL = {
    Q_UNKNOWN: "NOT RECORDED",
    Q_TWO_SIDED: "TWO SIDED",
    Q_WIDE: "WIDE",
    Q_ONE_SIDED: "ONE SIDED",
    Q_STALE: "STALE",
}

QUALITY_NOTE = {
    Q_TWO_SIDED: "A real two-sided market with a spread inside a quarter of "
                 "the mid price. A fill at the mid is a fair assumption.",
    Q_WIDE: "A real two-sided market, but the spread is more than a quarter "
            "of the mid price. The mid is a long way from either side.",
    Q_ONE_SIDED: "Only one side of the market was quoted, so there is no mid "
                 "to fill at.",
    Q_STALE: "The quote had not moved for long enough that it may not "
             "describe the market at the time it was captured.",
    Q_UNKNOWN: "This snapshot predates quote-quality recording.",
}

# A spread wider than this share of the mid is called wide.
WIDE_SPREAD = 0.25
# A quote older than this many seconds when captured is called stale.
STALE_SECONDS = 900


def configure(data_dir) -> None:
    global _DIR
    _DIR = Path(data_dir) / "chains" if data_dir else None


def _path(sym: str) -> Path | None:
    if _DIR is None:
        return None
    return _DIR / f"{sym.upper()}.json"


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def quality_of(row: dict) -> int:
    """How tradeable the quote in a live chain row actually was."""
    bid, ask = _f(row.get("bid")), _f(row.get("ask"))
    if bid <= 0 or ask <= bid:
        return Q_ONE_SIDED
    age = row.get("quote_age_s")
    try:
        if age is not None and float(age) > STALE_SECONDS:
            return Q_STALE
    except (TypeError, ValueError):
        pass
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return Q_ONE_SIDED
    return Q_WIDE if (ask - bid) / mid > WIDE_SPREAD else Q_TWO_SIDED


def record(sym: str, chain_payload: dict, today: str | None = None,
           source: str | None = None, event: dict | None = None) -> bool:
    """Snapshot a live chain payload (the app's standard shape:
    {underlying:{last}, expirations:[...], chains:{exp:{calls,puts}}}).

    Once per symbol per day; best-effort, never raises. `source` names the
    provider the chain came from and `event` carries whatever the caller
    knows about the state of the world that day — an earnings date inside
    the window, a dividend, an index event — so a fill priced off this
    snapshot can later be read in context rather than as a bare number.
    """
    try:
        p = _path(sym)
        if p is None or not chain_payload:
            return False
        d = today or date.today().isoformat()
        if _RECORDED_TODAY.get(sym.upper()) == d:
            return False
        spot = _f((chain_payload.get("underlying") or {}).get("last"))
        if spot <= 0:
            return False
        exps_out = {}
        for exp, sides in (chain_payload.get("chains") or {}).items():
            try:
                dte = (date.fromisoformat(str(exp)[:10]) - date.fromisoformat(d)).days
            except ValueError:
                continue
            if not (0 <= dte <= MAX_DTE):
                continue
            packed = {}
            for key, side in (("c", "calls"), ("p", "puts")):
                rows = []
                for r in (sides.get(side) or []):
                    k = r.get("strike")
                    if not k or abs(k - spot) / spot > STRIKE_BAND:
                        continue
                    rows.append([round(float(k), 2),
                                 round(_f(r.get("bid")), 4),
                                 round(_f(r.get("ask")), 4),
                                 round(_f(r.get("iv")), 4),
                                 round(_f(r.get("delta")), 4),
                                 int(_f(r.get("openInterest"))),
                                 round(_f(r.get("last")), 4),
                                 int(_f(r.get("volume"))),
                                 quality_of(r)])
                if rows:
                    packed[key] = rows
            if packed:
                exps_out[str(exp)[:10]] = packed
        if not exps_out:
            return False
        day_row = {"spot": round(spot, 4), "exps": exps_out, "v": SCHEMA,
                   "src": (source or chain_payload.get("source")
                           or "unknown"),
                   "ts": datetime.now().astimezone().isoformat(timespec="seconds")}
        if event:
            day_row["ev"] = event
        with _LOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if p.exists():
                try:
                    data = json.loads(p.read_text())
                except Exception:
                    data = {}
            # Never rewrite a day already on disk. A second capture on the
            # same date would replace a morning snapshot with an afternoon
            # one and silently change what every backtest built on it said.
            if d in data:
                _RECORDED_TODAY[sym.upper()] = d
                return False
            data[d] = day_row
            if len(data) > MAX_DAYS_KEPT:
                for old in sorted(data.keys())[:-MAX_DAYS_KEPT]:
                    data.pop(old, None)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")))
            tmp.replace(p)
            _RECORDED_TODAY[sym.upper()] = d
        return True
    except Exception:
        return False


def load(sym: str) -> dict:
    p = _path(sym)
    if p is None or not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _unpack(row: list) -> dict:
    """One stored row, whichever schema wrote it.

    A version-1 row carries six numbers and knows nothing about the last
    trade, the volume or the quality of the quote. Those come back as None
    rather than as zero: "not recorded that day" and "nothing traded" are
    different statements and a reader must not confuse them.
    """
    out = {"strike": row[0], "bid": row[1], "ask": row[2],
           "iv": row[3] or None, "delta": row[4], "open_interest": row[5],
           "last": None, "volume": None, "quality": Q_UNKNOWN}
    if len(row) > 6:
        out["last"] = row[6] or None
    if len(row) > 7:
        out["volume"] = row[7]
    if len(row) > 8:
        out["quality"] = row[8]
    return out


def lookup(store: dict, day: str, right: str, strike: float,
           want_dte: float) -> dict | None:
    """Real quote for (day, right, ~strike, ~dte) from a loaded store.

    Same-day snapshots only — a quote from another day is NOT that day's
    market. Picks the expiry closest to the wanted days-to-expiration
    (within ±40%) and the nearest strike (within 1.5% of spot).
    """
    snap = store.get(day)
    if not snap:
        return None
    spot = snap.get("spot") or 0
    best = None
    d0 = date.fromisoformat(day)
    for exp, sides in (snap.get("exps") or {}).items():
        dte = (date.fromisoformat(exp) - d0).days
        if dte <= 0 or abs(dte - want_dte) > max(3, want_dte * 0.4):
            continue
        rows = sides.get("c" if right == "call" else "p") or []
        for raw in rows:
            r = _unpack(raw)
            if spot > 0 and abs(r["strike"] - strike) / spot > 0.015:
                continue
            score = (abs(dte - want_dte), abs(r["strike"] - strike))
            if r["bid"] > 0 and r["ask"] > r["bid"] \
                    and (best is None or score < best[0]):
                best = (score, {
                    "bid": r["bid"], "ask": r["ask"],
                    "mid": round((r["bid"] + r["ask"]) / 2, 4),
                    "iv": r["iv"], "expiry": exp, "strike": r["strike"],
                    "last": r["last"], "volume": r["volume"],
                    "open_interest": r["open_interest"],
                    "quality": r["quality"],
                    "quality_label": QUALITY_LABEL.get(r["quality"], ""),
                    "source": snap.get("src") or "unknown",
                    "captured_at": snap.get("ts"),
                    "event": snap.get("ev") or None})
    return best[1] if best else None


def coverage(store: dict, dates: list) -> float:
    """Fraction of the given dates that have a snapshot."""
    if not dates:
        return 0.0
    have = sum(1 for d in dates if d in store)
    return have / len(dates)


def readiness(store: dict, dates=None) -> dict:
    """How much REAL option history exists for this symbol, in plain terms.

    This is the number that decides whether a covered-call run is a real
    backtest or an estimate, so it is reported rather than inferred from the
    fills afterwards. It never grows backwards: the only way to raise it is
    to keep the app running and let tomorrow arrive.
    """
    days = sorted(store or {})
    out = {"days": len(days), "first": days[0] if days else None,
           "last": days[-1] if days else None,
           "schema_2_days": sum(1 for d in days
                                if (store[d] or {}).get("v", 1) >= 2),
           "expirations": 0, "contracts": 0, "sources": [],
           "window_days": None, "window_coverage_pct": None, "reason": ""}
    srcs = set()
    for d in days:
        snap = store[d] or {}
        if snap.get("src"):
            srcs.add(snap["src"])
        exps = snap.get("exps") or {}
        out["expirations"] += len(exps)
        for sides in exps.values():
            for rows in sides.values():
                out["contracts"] += len(rows)
    out["sources"] = sorted(srcs)
    if dates:
        out["window_days"] = len(dates)
        out["window_coverage_pct"] = coverage(store, list(dates)) * 100.0
    if not days:
        out["reason"] = (
            "No end-of-day option chain has been captured for this ticker "
            "yet. There is no source of historical chains this app can "
            "reach, so this cannot be back-filled — it fills in from the day "
            "capture starts and never before it.")
    return out
