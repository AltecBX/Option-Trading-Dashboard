"""
short_trend.py — short-interest trend alerts for high-debt stocks (v5.40).

Jerry: "Add short interest trend alerts for high debt stocks." The trade he
described: rates rise, the most indebted companies sell off, and the smart
money shorts them. This watches for the shorting.

THE DATA. FINRA's official short-interest report: every US stock's shares
sold short, twice a month (settled on the 15th and the last business day,
published about eight business days later), with days to cover. Read
through `hf_sources.finra_short_interest`, the app's existing reader. The
last SETTLEMENTS_KEEP reports (about three months) are kept on disk, every
symbol, about half a megabyte each, so a stock added to the watchlist
tomorrow already has its history.

THE ALERT. On a stock the Debt & rates screen rates High or Elevated, with
at least MIN_FLOAT_PCT of its tradable shares short:
  jump    short interest up JUMP_PCT or more in the newest report
  build   up BUILD_REPORTS reports in a row (about six weeks) and up
          BUILD_PCT or more over that run
Report-to-report changes of 10-20% either way are ordinary (measured on
Jerry's 53 table stocks, Oct 2026), which is why a single jump needs 10%
and a build needs both the run and the size.

One phone push per new report, naming the stocks, when the report is
fresh (published within PUSH_FRESH_DAYS). Every alert is kept for
ALERTS_KEEP_DAYS and listed in the app.

Pure functions plus one small store; the fetchers, the board and the push
are injected by configure(). Never raises into the server.
"""

from __future__ import annotations

import json
import math
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

# ── knobs ────────────────────────────────────────────────────────────────────
SETTLEMENTS_KEEP = 6        # reports kept: about three months
JUMP_PCT = 10.0             # newest report up this much: a jump
BUILD_REPORTS = 3           # this many rising reports in a row ...
BUILD_PCT = 15.0            # ... adding up to this much: a build
MIN_FLOAT_PCT = 2.0         # below this share of the float short, a rise is noise
DEBT_LEVELS = ("High", "Elevated")
ALERTS_KEEP_DAYS = 120
SHORTED_MIN_PCT = 5.0       # the Live Scanner's list: at least this much of the float short ...
SHORTED_KEEP = 40           # ... and at most this many stocks on it
PUSH_FRESH_DAYS = 7         # a report published longer ago than this is not news
HISTORY_WAIT_DAYS = 3       # how long a new report waits for a missing earlier one
CHECK_EVERY_S = 3 * 3600    # the background check

_LOCK = threading.RLock()
_DATA_DIR: Optional[Path] = None
_FETCH_FN: Optional[Callable[[str], Mapping]] = None          # settlement -> {SYM: row}
_SETTLEMENTS_FN: Optional[Callable[[date], list]] = None      # today -> [settlement, ...] newest first
_PUBLIC_ON_FN: Optional[Callable[[str], Optional[str]]] = None
_BOARD_FN: Optional[Callable[[], Any]] = None                 # () -> the watchlist board {rows, status} (or its rows)
_NOTIFY_FN: Optional[Callable[..., Any]] = None               # (title, message) -> None
_TODAY_FN: Optional[Callable[[], date]] = None
_STORE: dict = {}
_THREAD: Optional[threading.Thread] = None


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def configure(data_dir=None, fetch_fn=None, settlements_fn=None, public_on_fn=None,
              board_fn=None, notify_fn=None, today_fn=None) -> None:
    global _DATA_DIR, _FETCH_FN, _SETTLEMENTS_FN, _PUBLIC_ON_FN, _BOARD_FN, _NOTIFY_FN, _TODAY_FN
    with _LOCK:
        _DATA_DIR = Path(data_dir) / "short_trend" if data_dir else None
        _FETCH_FN, _SETTLEMENTS_FN, _PUBLIC_ON_FN = fetch_fn, settlements_fn, public_on_fn
        _BOARD_FN, _NOTIFY_FN, _TODAY_FN = board_fn, notify_fn, today_fn
        _STORE.clear()
        _STORE.update(_load())


def _today() -> date:
    if _TODAY_FN is not None:
        try:
            return _TODAY_FN()
        except Exception:  # noqa: BLE001
            pass
    return date.today()


# ── the store ────────────────────────────────────────────────────────────────
def _path() -> Optional[Path]:
    return _DATA_DIR / "store.json" if _DATA_DIR else None


def _empty() -> dict:
    # notified: reports whose alerts were raised; pushed: reports whose push
    # the provider confirmed (retried until then, while fresh).
    return {"settlements": {}, "alerts": [], "notified": [], "pushed": []}


def _load() -> dict:
    p = _path()
    if p is None or not p.exists():
        return _empty()
    try:
        v = json.loads(p.read_text())
    except (OSError, ValueError):
        return _empty()
    if not isinstance(v, dict):
        return _empty()
    out = _empty()
    out.update({k: v.get(k) or out[k] for k in out})
    return out


def _save() -> None:
    p = _path()
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(_STORE, separators=(",", ":")))
    tmp.replace(p)


def compact(rows: Mapping[str, Mapping]) -> dict:
    """FINRA's rows reduced to what a trend needs: {SYM: [short, days_to_cover]}."""
    out = {}
    for sym, r in (rows or {}).items():
        s = _f((r or {}).get("short"))
        if s is None:
            continue
        out[str(sym).upper()] = [s, _f(r.get("days_to_cover"))]
    return out


# ── the trend ────────────────────────────────────────────────────────────────
def series(store: Mapping, symbol: str) -> list:
    """[(settlement, short, days_to_cover), ...] oldest first, from the kept reports."""
    sym = str(symbol or "").upper()
    out = []
    for st in sorted((store.get("settlements") or {}))[-SETTLEMENTS_KEEP:]:
        row = (store["settlements"][st] or {}).get(sym)
        if row and _f(row[0]) is not None:
            out.append((st, float(row[0]), _f(row[1]) if len(row) > 1 else None))
    return out


def trend(points: Sequence) -> Optional[dict]:
    """The newest report against the one before, the run of rising reports
    ending at the newest, and the change over that run."""
    pts = [p for p in points if p[1] is not None and p[1] > 0]
    if len(pts) < 2:
        return None
    changes = [(b[1] / a[1] - 1.0) * 100.0 for a, b in zip(pts, pts[1:])]
    rises = 0
    for c in reversed(changes):
        if c > 0:
            rises += 1
        else:
            break
    run_start = pts[-1 - rises] if rises else pts[-1]
    return {"settlement": pts[-1][0], "short": pts[-1][1], "days_to_cover": pts[-1][2],
            "prev_short": pts[-2][1], "change_pct": round(changes[-1], 1),
            "rises": rises,
            "run_pct": round((pts[-1][1] / run_start[1] - 1.0) * 100.0, 1) if rises else 0.0,
            "run_from": run_start[0] if rises else None,
            "points": [{"settlement": p[0], "short": p[1], "days_to_cover": p[2]} for p in pts]}


def _shares(v: float) -> str:
    return f"{v / 1e9:.2f}B" if v >= 1e9 else f"{v / 1e6:.1f}M" if v >= 1e6 else f"{v / 1e3:.0f}K"


def _day(iso: str) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return str(iso)
    return f"{d.strftime('%b')} {d.day}"


def judge(row: Mapping, t: Optional[Mapping]) -> Optional[dict]:
    """An alert for one watchlist row, or None. The row brings the debt
    rating and the float; the trend brings the shorts."""
    level = row.get("debt_risk")
    if level not in DEBT_LEVELS or not t:
        return None
    flt = _f(row.get("float_shares"))
    pct_float = (t["short"] / flt * 100.0) if (flt and flt > 0) else _f(row.get("short_pct"))
    if pct_float is None or pct_float < MIN_FLOAT_PCT:
        return None
    jump = t["change_pct"] >= JUMP_PCT
    build = t["rises"] >= BUILD_REPORTS and t["run_pct"] >= BUILD_PCT
    if not (jump or build):
        return None
    sym = str(row.get("symbol") or "").upper()
    art = "an" if str(level)[:1] in "AEIOU" else "a"
    parts = [f"Shorts are piling into {sym}, {art} {level}-debt stock: short interest "
             f"{t['change_pct']:+.0f}% to {_shares(t['short'])} shares in the {_day(t['settlement'])} report "
             f"({pct_float:.1f}% of its float"
             + (f", {t['days_to_cover']:.1f} days to cover" if t.get("days_to_cover") else "") + ")"]
    if t["rises"] >= 2:
        parts.append(f"rising {t['rises']} reports in a row ({t['run_pct']:+.0f}% since the "
                     f"{_day(t['run_from'])} report)")
    why = (row.get("debt_reasons") or [None])[0]
    text = ", ".join(parts) + "." + (f" {why}" if why else "")
    return {"symbol": sym, "settlement": t["settlement"], "kind": "jump" if jump else "build",
            "level": level, "change_pct": t["change_pct"], "rises": t["rises"], "run_pct": t["run_pct"],
            "short": t["short"], "pct_float": round(pct_float, 2), "days_to_cover": t.get("days_to_cover"),
            "mtd": _f(row.get("mtd")), "text": text}


def alerts_for(rows: Iterable[Mapping], store: Mapping) -> list:
    """Every alert the newest kept report gives, High before Elevated, the
    biggest jumps first."""
    out = []
    for r in rows or []:
        a = judge(r, trend(series(store, r.get("symbol"))))
        if a:
            out.append(a)
    rank = {lvl: i for i, lvl in enumerate(DEBT_LEVELS)}
    out.sort(key=lambda a: (rank.get(a["level"], 9), -a["change_pct"], -a["run_pct"]))
    return out


def row_fields(store: Mapping, symbol: str) -> dict:
    """The watchlist's Short trend column for one stock."""
    t = trend(series(store, symbol))
    if not t:
        return {}
    return {"si_chg": t["change_pct"], "si_rises": t["rises"], "si_run": t["run_pct"],
            "si_settlement": t["settlement"], "si_dtc": t.get("days_to_cover")}


# ── the check ────────────────────────────────────────────────────────────────
def latest_settlement() -> Optional[str]:
    with _LOCK:
        s = sorted(_STORE.get("settlements") or {})
    return s[-1] if s else None


def refresh(today: Optional[date] = None) -> int:
    """Fetch the kept reports that are public and not on file yet; drop
    the ones that have aged out. Returns how many were fetched."""
    if _FETCH_FN is None or _SETTLEMENTS_FN is None:
        return 0
    today = today or _today()
    try:
        wanted = list(_SETTLEMENTS_FN(today) or [])[:SETTLEMENTS_KEEP]
    except Exception:  # noqa: BLE001
        return 0
    with _LOCK:
        have = set(_STORE["settlements"])
    got = 0
    for st in wanted:
        if st in have:
            continue
        try:
            rows = compact(_FETCH_FN(st) or {})
        except Exception:  # noqa: BLE001
            rows = {}
        if not rows:
            continue                       # not published yet, or the source is down: next time
        with _LOCK:
            _STORE["settlements"][st] = rows
        got += 1
    with _LOCK:
        keep = sorted(_STORE["settlements"])[-SETTLEMENTS_KEEP:]
        for st in list(_STORE["settlements"]):
            if st not in keep:
                _STORE["settlements"].pop(st, None)
        if got:
            _save()
    return got


def check(today: Optional[date] = None) -> dict:
    """Refresh the reports; when the newest has not been alerted on yet and
    the watchlist has been scanned, raise its alerts, keep them, and push one
    summary if the report is fresh. Safe to call often."""
    today = today or _today()
    fetched = refresh(today)
    st = latest_settlement()
    if not st:
        return {"ok": False, "reason": "no FINRA report on file yet", "fetched": fetched}
    with _LOCK:
        done = st in (_STORE.get("notified") or [])
    new: list = []
    if not done:
        missing = _missing_before(st, today)
        if missing is None or missing:
            # A report is judged once, on the reports before it: one whose
            # earlier reports failed to download would lose its jumps and
            # builds for good (Codex, #428). It waits for them a few days,
            # then is judged with what is on file.
            return {"ok": False, "reason": "waiting for earlier FINRA reports", "settlement": st,
                    "missing": missing, "fetched": fetched}
        try:
            board = _BOARD_FN() if _BOARD_FN else []
        except Exception:  # noqa: BLE001
            board = []
        if isinstance(board, Mapping):
            rows = list(board.get("rows") or [])
            status = board.get("status") or {}
            # The watchlist publishes after every chunk of a scan: a report
            # read against half a board would never see the rest of it
            # (Codex, #428). It waits for a finished scan.
            if status.get("scanning") or not status.get("last_scan"):
                return {"ok": False, "reason": "waiting for the watchlist scan to finish",
                        "settlement": st, "fetched": fetched}
        else:
            rows = list(board or [])
        if not any(r.get("debt_risk") for r in rows):
            # The watchlist has not been scanned with debt ratings yet: the
            # report waits for it rather than being marked as seen with nothing.
            return {"ok": False, "reason": "waiting for the watchlist scan", "settlement": st, "fetched": fetched}
        with _LOCK:
            new = alerts_for(rows, _STORE)
            stamp = datetime.now().isoformat(timespec="seconds")
            for a in new:
                a["raised"] = stamp
            cutoff = (today - timedelta(days=ALERTS_KEEP_DAYS)).isoformat()
            kept = [a for a in (_STORE.get("alerts") or [])
                    if a.get("settlement", "") >= cutoff and a.get("settlement") != st]
            _STORE["alerts"] = kept + new
            _STORE["notified"] = (list(_STORE.get("notified") or []) + [st])[-24:]
            _save()
    pushed = _push(st, today)
    return {"ok": True, "settlement": st, "new": len(new), "pushed": pushed, "fetched": fetched}


def _missing_before(st: str, today: date) -> Optional[list]:
    """The kept reports older than `st` not on file yet; None when the
    schedule cannot be read. Empty once HISTORY_WAIT_DAYS have passed since
    `st` went public: it is judged with what there is rather than never."""
    public = (_PUBLIC_ON_FN(st) if _PUBLIC_ON_FN else None) or st
    try:
        if (today - date.fromisoformat(public)).days > HISTORY_WAIT_DAYS:
            return []
    except ValueError:
        return []
    try:
        wanted = list(_SETTLEMENTS_FN(today) or [])[:SETTLEMENTS_KEEP] if _SETTLEMENTS_FN else []
    except Exception:  # noqa: BLE001
        return None
    with _LOCK:
        have = set(_STORE.get("settlements") or {})
    return sorted(s for s in wanted if s < st and s not in have)


def _push(st: str, today: date) -> bool:
    """Send the report's push if it has alerts, is fresh and has not been
    delivered. Delivered means the provider said so: the app's push returns
    {"ok": False} on a failed send rather than raising, and a report marked
    sent on a failure would never be pushed (Codex, #428). Retried on each
    check until it goes through or the report stops being fresh."""
    with _LOCK:
        if st in (_STORE.get("pushed") or []):
            return False
        alerts = [a for a in (_STORE.get("alerts") or []) if a.get("settlement") == st]
    if not alerts or _NOTIFY_FN is None:
        return False
    public = _PUBLIC_ON_FN(st) if _PUBLIC_ON_FN else None
    if public:
        try:
            if (today - date.fromisoformat(public)).days > PUSH_FRESH_DAYS:
                return False
        except ValueError:
            pass
    names = ", ".join(f"{a['symbol']} {a['change_pct']:+.0f}%" for a in alerts[:6])
    more = f" and {len(alerts) - 6} more" if len(alerts) > 6 else ""
    try:
        res = _NOTIFY_FN(f"Shorts piling into {len(alerts)} high-debt stock{'s' if len(alerts) != 1 else ''}",
                         f"FINRA {_day(st)} report: {names}{more}.")
    except Exception:  # noqa: BLE001
        return False
    ok = res is not None and not (isinstance(res, Mapping) and not res.get("ok", True))
    if ok:
        with _LOCK:
            _STORE["pushed"] = (list(_STORE.get("pushed") or []) + [st])[-24:]
            _save()
    return ok


def snapshot(symbol: Optional[str] = None) -> dict:
    """What the app shows: the newest report's alerts, the recent ones, and
    with `symbol` that stock's trend."""
    st = latest_settlement()
    with _LOCK:
        alerts = list(_STORE.get("alerts") or [])
        out = {"settlement": st, "public_on": (_PUBLIC_ON_FN(st) if (st and _PUBLIC_ON_FN) else None),
               "reports": sorted(_STORE.get("settlements") or {}),
               "alerts": [a for a in alerts if a.get("settlement") == st],
               "recent": sorted(alerts, key=lambda a: (a.get("settlement", ""), a.get("symbol", "")),
                                reverse=True)[:60],
               "rules": {"jump_pct": JUMP_PCT, "build_reports": BUILD_REPORTS, "build_pct": BUILD_PCT,
                         "min_float_pct": MIN_FLOAT_PCT, "levels": list(DEBT_LEVELS)}}
        if symbol:
            out["symbol"] = str(symbol).upper()
            out["trend"] = trend(series(_STORE, symbol))
            out["symbol_alerts"] = [a for a in alerts if a.get("symbol") == out["symbol"]]
    return out


def shorted_debt(rows: Iterable[Mapping], live: Optional[Mapping[str, Mapping]] = None,
                 store: Optional[Mapping] = None) -> dict:
    """The Live Scanner's "Shorted high-debt stocks" (v5.41): every watchlist
    stock rated High or Elevated debt with at least SHORTED_MIN_PCT of its
    float short, or with a shorts-piling-in alert on the newest report.
    Short interest is FINRA's newest report over Yahoo's float when both are
    there, Yahoo's own figure otherwise. `live` maps symbols to the
    scanner's {last, change_pct}, so the price move is today's. Alerted
    stocks first, then High before Elevated, then the most heavily shorted."""
    if store is None:
        with _LOCK:
            store = {"settlements": dict(_STORE.get("settlements") or {}),
                     "alerts": list(_STORE.get("alerts") or [])}
    sts = sorted(store.get("settlements") or {})
    newest = sts[-1] if sts else None
    alerted = {a.get("symbol"): a for a in (store.get("alerts") or []) if a.get("settlement") == newest}
    rank = {lvl: i for i, lvl in enumerate(DEBT_LEVELS)}
    out = []
    for r in rows or []:
        level = r.get("debt_risk")
        if level not in DEBT_LEVELS:
            continue
        sym = str(r.get("symbol") or "").upper()
        t = trend(series(store, sym))
        flt = _f(r.get("float_shares"))
        pct = (t["short"] / flt * 100.0) if (t and flt and flt > 0) else _f(r.get("short_pct"))
        a = alerted.get(sym)
        if not a and (pct is None or pct < SHORTED_MIN_PCT):
            continue
        q = (live or {}).get(sym) or {}
        last = _f(q.get("last")) if _f(q.get("last")) is not None else _f(r.get("last"))
        chg = _f(q.get("change_pct")) if _f(q.get("change_pct")) is not None else _f(r.get("change_pct"))
        out.append({"symbol": sym, "level": level, "debt_score": r.get("debt_score"),
                    "short_pct": round(pct, 2) if pct is not None else None,
                    "change_pct": round(chg, 2) if chg is not None else None, "last": last,
                    "si_chg": t["change_pct"] if t else None, "si_rises": t["rises"] if t else None,
                    "si_run": t["run_pct"] if t else None,
                    "days_to_cover": t.get("days_to_cover") if t else None,
                    "alert": a.get("text") if a else None,
                    "reason": (r.get("debt_reasons") or [None])[0]})
    out.sort(key=lambda x: (0 if x["alert"] else 1, rank.get(x["level"], 9), -(x["short_pct"] or 0)))
    return {"settlement": newest, "min_pct": SHORTED_MIN_PCT, "rows": out[:SHORTED_KEEP],
            "total": len(out)}


def board_fields(rows: list) -> list:
    """The watchlist rows with their Short trend fields added (copies)."""
    with _LOCK:
        if not _STORE.get("settlements"):
            return rows
        return [dict(r, **row_fields(_STORE, r.get("symbol"))) for r in rows]


# ── the background check ─────────────────────────────────────────────────────
def _loop() -> None:
    while True:
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            print(f"[short_trend] check failed: {exc}")
        time.sleep(CHECK_EVERY_S)


def start_scheduler() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(target=_loop, name="short-trend", daemon=True)
        _THREAD.start()
