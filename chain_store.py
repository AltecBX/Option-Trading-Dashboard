"""chain_store.py — EOD option-chain snapshots (Backtest v2, B2).

The self-building real-data layer: every live chain the app already
fetches (juice scans, ticker loads, EM engine) can be snapshotted once
per symbol per day. Backtests then price ENTRY fills from REAL bid/ask
where a snapshot exists for that date, and fall back to the model
elsewhere — with each trade labeled `real_quote` / `modeled`, and the
run reporting its real-quote coverage. Accuracy compounds forever: after
a few months of normal app use, short-DTE tests on watchlist names fill
mostly from real markets.

Storage: one JSON per symbol under <data_dir>/chains/SYM.json —
  {"YYYY-MM-DD": {"spot": 123.45,
                  "exps": {"YYYY-MM-DD": {"c": [[strike,bid,ask,iv,delta,oi]...],
                                          "p": [...]}}}}
Compact filters: expiries ≤ 75 calendar DTE, strikes within ±30% of
spot. Atomic writes, trimmed to the newest 500 dates. Thread-safe.
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


def configure(data_dir) -> None:
    global _DIR
    _DIR = Path(data_dir) / "chains" if data_dir else None


def _path(sym: str) -> Path | None:
    if _DIR is None:
        return None
    return _DIR / f"{sym.upper()}.json"


def record(sym: str, chain_payload: dict, today: str | None = None) -> bool:
    """Snapshot a live chain payload (the app's standard shape:
    {underlying:{last}, expirations:[...], chains:{exp:{calls,puts}}}).
    Once per symbol per day; best-effort, never raises."""
    try:
        p = _path(sym)
        if p is None or not chain_payload:
            return False
        d = today or date.today().isoformat()
        if _RECORDED_TODAY.get(sym.upper()) == d:
            return False
        spot = float((chain_payload.get("underlying") or {}).get("last") or 0)
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
                                 round(float(r.get("bid") or 0), 4),
                                 round(float(r.get("ask") or 0), 4),
                                 round(float(r.get("iv") or 0), 4),
                                 round(float(r.get("delta") or 0), 4),
                                 int(r.get("openInterest") or 0)])
                if rows:
                    packed[key] = rows
            if packed:
                exps_out[str(exp)[:10]] = packed
        if not exps_out:
            return False
        with _LOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if p.exists():
                try:
                    data = json.loads(p.read_text())
                except Exception:
                    data = {}
            data[d] = {"spot": round(spot, 4), "exps": exps_out}
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


def lookup(store: dict, day: str, right: str, strike: float,
           want_dte: float) -> dict | None:
    """Real quote for (day, right, ~strike, ~dte) from a loaded store.
    Same-day snapshots only (a quote from another day is NOT that day's
    market). Picks the expiry closest to the wanted DTE (within ±40%)
    and the nearest strike (within 1.5% of spot). → {bid, ask, mid, iv,
    expiry} or None."""
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
        for k, bid, ask, iv, delta, oi in rows:
            if spot > 0 and abs(k - strike) / spot > 0.015:
                continue
            score = (abs(dte - want_dte), abs(k - strike))
            if bid > 0 and ask > bid and (best is None or score < best[0]):
                best = (score, {"bid": bid, "ask": ask,
                                "mid": round((bid + ask) / 2, 4),
                                "iv": iv or None, "expiry": exp,
                                "strike": k})
    return best[1] if best else None


def coverage(store: dict, dates: list) -> float:
    """Fraction of the given dates that have a snapshot."""
    if not dates:
        return 0.0
    have = sum(1 for d in dates if d in store)
    return have / len(dates)
