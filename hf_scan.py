"""hf_scan.py — the Hedge Fund Pulse board: gather, answer, remember.

The stateful half of the aggregate layer. It fetches what the sources will
give, hands it to the pure `hf_pulse` math, and keeps every weekly answer
forever so "what changed from last week" is a record rather than a claim.

It also assembles the weekly report (`hf_report`) from three inputs — this
board, the Named Fund Watch and the prime-broker headlines — and keeps every
build of it.

Four things it is careful about:

  * **One reading per week, stored under its ISO week.** The CFTC report is
    the clock: positions as of Tuesday, published Friday at 3:30 PM ET. A
    re-run in the same week overwrites that week's snapshot; a run in a new
    week starts a new one and the previous becomes the comparison.

  * **Reports append, snapshots replace.** The board is a measurement and the
    latest read of it wins. A report is a record of what was known when it
    was written, so a rebuild adds a revision beside the old one instead of
    erasing it.

  * **A sector roll-up says what it covers.** Short interest arrives for
    twenty-two thousand symbols and the app can place a few thousand of
    them in a sector. The roll-up uses what it can place and reports the
    count, because a sector total built from a tenth of the market is a
    real number about a tenth of the market and nothing more.

  * **Nothing here can name a fund.** Every row it builds is REGULATORY or
    FLOW PROXY, and `hf_sources.evidence()` refuses to attach a fund name to
    either. The store checks again before writing.

HEDGE_FUND_INTEL.md §5b, §5e.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import hf_grade as GR
import hf_press as PRESS
import hf_pulse as P
import hf_report as RPT
import hf_sources as S

HF_SCAN_VERSION = "1.2.0"

DEFAULTS = {
    "_doc": ("Hedge Fund Pulse (hf_scan.py, HEDGE_FUND_INTEL.md). The aggregate layer. "
             "Cadences and the two thresholds the verdicts use."),
    "pulse": {
        "refresh_hours": 12,        # the underlying sources are cached to their own cadence
        "cftc_weeks": 170,          # ~3¼ years of weekly history for percentiles
        "short_volume_days": 20,    # how many daily files build the short-share history
        "sector_min_symbols": 25,   # below this a sector short-interest roll-up is not reported
        "crowded_percentile": 90,   # at or beyond this, on its own history, an input is crowded
        "crowded_inputs": 2,        # this many crowded inputs makes a crowded trade
    },
    "report": {
        "press_max_age_days": 10,   # a prime-broker note describes the week just ended
        "rebuild_hours": 24,        # a stored week is refreshed at most this often
        "keep_revisions": 12,       # revisions kept per week before the oldest is dropped
    },
}

_DATA_DIR: Path | None = None
_UW_GETTER = None
_SECTOR_FN = None            # (symbol) -> sector label | None
_SECTOR_NORM = None          # (label) -> one of the app's eleven names | None
_NOW_FN = None
_FUNDS_FN = None             # () -> the Named Fund Watch snapshot
_LOCK = threading.RLock()
_STATE: dict = {"board": None, "as_of": None, "refreshing": False, "thread": None,
                "error": None, "sources": {}, "week": None,
                "report": None, "report_week": None, "report_at": None,
                "report_refreshing": False, "report_error": None,
                "grades": None, "grades_at": None, "grades_refreshing": False,
                "grades_error": None, "grades_retry_at": None}

SECTOR_ETFS = {"XLB": "Materials", "XLC": "Communication Services", "XLE": "Energy",
               "XLF": "Financials", "XLI": "Industrials", "XLK": "Technology",
               "XLP": "Consumer Staples", "XLRE": "Real Estate", "XLU": "Utilities",
               "XLV": "Health Care", "XLY": "Consumer Discretionary"}
# The names Unusual Whales uses for the sector tide, mapped onto the app's.
UW_TIDE_SECTORS = {"Basic Materials": "Materials", "Communication Services": "Communication Services",
                   "Consumer Cyclical": "Consumer Discretionary", "Consumer Defensive": "Consumer Staples",
                   "Energy": "Energy", "Financial Services": "Financials", "Healthcare": "Health Care",
                   "Industrials": "Industrials", "Real Estate": "Real Estate",
                   "Technology": "Technology", "Utilities": "Utilities"}


def configure(data_dir=None, uw_getter=None, sector_fn=None, sector_norm=None, now_fn=None,
              funds_fn=None) -> None:
    """`funds_fn` is the Named Fund Watch snapshot, injected rather than
    imported. `hf_watch` must not import `hf_scan`, and the report needs
    both halves; passing the payload in keeps the dependency one-way and
    lets a test build a report from a fixture with no watch module at all."""
    global _DATA_DIR, _UW_GETTER, _SECTOR_FN, _SECTOR_NORM, _NOW_FN, _FUNDS_FN
    _DATA_DIR = Path(data_dir) if data_dir else None
    _UW_GETTER, _SECTOR_FN, _SECTOR_NORM, _NOW_FN = uw_getter, sector_fn, sector_norm, now_fn
    _FUNDS_FN = funds_fn
    if _DATA_DIR is not None:
        for sub in ("pulse", "reports"):
            try:
                (_DATA_DIR / "hf" / sub).mkdir(parents=True, exist_ok=True)
            except Exception:  # noqa: BLE001
                pass
    _load_latest()
    _load_latest_report()
    _load_grades()


def config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        import premium_edge as pe
        full = json.loads((Path(__file__).resolve().parent / "thresholds.json").read_text())
        cfg = pe._deep_merge(cfg, full.get("hedge") or {})  # noqa: SLF001
        dd = getattr(pe, "_DATA_DIR", None)
        if dd:
            p = Path(dd) / "thresholds.json"
            if p.exists():
                cfg = pe._deep_merge(cfg, (json.loads(p.read_text()).get("hedge") or {}))  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass
    return cfg


def _knob(name: str):
    try:
        return (config().get("pulse") or {}).get(name, DEFAULTS["pulse"][name])
    except Exception:  # noqa: BLE001
        return DEFAULTS["pulse"][name]


def _report_knob(name: str):
    try:
        return (config().get("report") or {}).get(name, DEFAULTS["report"][name])
    except Exception:  # noqa: BLE001
        return DEFAULTS["report"][name]


def _now() -> datetime:
    if _NOW_FN:
        try:
            n = _NOW_FN()
            if isinstance(n, datetime):
                return n
        except Exception:  # noqa: BLE001
            pass
    return datetime.now(timezone.utc)


def _today() -> date:
    return _now().date()


def week_key(d: date) -> str:
    """ISO year and week — the identity of a stored reading. The CFTC
    publishes once a week, so a week is the natural unit of this record."""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def long_date(s) -> str | None:
    try:
        d = date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None
    return f"{d.strftime('%B')} {d.day}, {d.year}"


# ── the weekly store ────────────────────────────────────────────────────────

def _pulse_dir() -> Path | None:
    return None if _DATA_DIR is None else _DATA_DIR / "hf" / "pulse"


def _snap_path(week: str) -> Path | None:
    d = _pulse_dir()
    return None if d is None else d / f"{week}.json"


def _save_snapshot(board: dict) -> None:
    if not S.attribution_ok(_all_inputs(board)):
        raise ValueError("refusing to store a pulse reading that attributes anonymous data to a fund")
    p = _snap_path(board["week"])
    if p is None:
        return
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(board), encoding="utf-8")
        tmp.replace(p)
    except Exception:  # noqa: BLE001
        pass


def _all_inputs(board: dict) -> list[dict]:
    rows = []
    for q in (board.get("questions") or []):
        rows.extend(q.get("inputs") or [])
    for r in ((board.get("sectors") or {}).get("rows") or []):
        rows.extend(r.get("inputs") or [])
    return rows


def history(limit: int = 60) -> list[dict]:
    """Every stored week, newest first — the record of how positioning moved."""
    d = _pulse_dir()
    if d is None or not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json"), reverse=True)[:limit]:
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out.append({"week": b.get("week"), "as_of": b.get("as_of"),
                    "cftc_as_of": b.get("cftc_as_of"),
                    "verdicts": {k: (b.get(k) or {}).get("verdict") for k in
                                 ("exposure", "leverage", "longs", "shorts")},
                    "crowded": [r.get("market") for r in ((b.get("crowding") or {}).get("crowded") or [])],
                    "n_changes": (b.get("changed") or {}).get("n")})
    return out


def snapshot_for(week: str) -> dict | None:
    p = _snap_path(week)
    if p is None or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _prior_snapshot(this_week: str) -> dict | None:
    """The most recent stored reading from a DIFFERENT week. Re-running in
    the same week must compare against last week, not against itself."""
    d = _pulse_dir()
    if d is None or not d.exists():
        return None
    for p in sorted(d.glob("*.json"), reverse=True):
        if p.stem == this_week:
            continue
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
    return None


def _load_latest() -> None:
    h = history(1)
    if not h:
        return
    b = snapshot_for(h[0]["week"])
    if b:
        with _LOCK:
            _STATE.update({"board": b, "as_of": b.get("as_of"), "week": b.get("week")})


# ── gathering ───────────────────────────────────────────────────────────────

def _sector_of(sym: str):
    if _SECTOR_FN is None:
        return None
    try:
        raw = _SECTOR_FN(sym)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    if _SECTOR_NORM is None:
        return str(raw)
    try:
        return _SECTOR_NORM(raw) or None
    except Exception:  # noqa: BLE001
        return None


def _uw():
    if _UW_GETTER is None:
        return None
    try:
        return _UW_GETTER()
    except Exception:  # noqa: BLE001
        return None


def gather_etf_flows() -> dict | None:
    """Creations and redemptions in the sector ETFs — the money actually
    moving into or out of each sector's wrapper, over the last five
    sessions. Everyone's money, not hedge funds' alone, which is why it
    corroborates and never decides."""
    uw = _uw()
    if uw is None or not hasattr(uw, "sector_flow"):
        return None
    try:
        data = uw.sector_flow()
    except Exception:  # noqa: BLE001
        return None
    rows = (data or {}).get("data") if isinstance(data, dict) else data
    if not rows:
        return None
    by_sector, net_all, as_of = {}, 0.0, None
    for r in rows:
        t = str(r.get("ticker") or "").upper()
        flows = r.get("in_out_flow") or []
        net = sum(float(f.get("change") or 0) for f in flows)
        if flows:
            as_of = max(as_of or "", max(str(f.get("date") or "") for f in flows))
        net_all += net
        sec = SECTOR_ETFS.get(t)
        if sec:
            by_sector[sec] = {"etf": t, "net": net, "days": len(flows), "as_of": as_of}
    return {"by_sector": by_sector, "net_all": net_all, "as_of": as_of,
            "n_etfs": len(rows)} if by_sector else None


def gather_tide() -> dict | None:
    """Net options premium by sector — call premium minus put premium over
    the session, from institutional-sized flow. Anonymous, and a proxy."""
    uw = _uw()
    if uw is None or not hasattr(uw, "sector_tide"):
        return None
    out = {}
    for uw_name, ours in UW_TIDE_SECTORS.items():
        try:
            data = uw.sector_tide(uw_name)
        except Exception:  # noqa: BLE001
            continue
        # The UW client unwraps the envelope and hands back the rows as a
        # bare list; only the raw endpoint returns {data, date}. Both shapes
        # arrive here, so neither may be assumed.
        rows = data.get("data") if isinstance(data, dict) else data
        if not rows:
            continue
        last = rows[-1]
        try:
            net = float(last.get("net_call_premium") or 0) - float(last.get("net_put_premium") or 0)
        except (TypeError, ValueError):
            continue
        as_of = data.get("date") if isinstance(data, dict) else None
        out[ours] = {"net_premium": net, "as_of": as_of or last.get("date"),
                     "points": len(rows)}
    return out or None


def gather_short_interest() -> tuple[dict | None, dict | None]:
    """The latest published settlement, market-wide and rolled up by sector.

    FINRA's own rows carry the previous period's figure, so one pull gives
    both the level and the change — there is no need to fetch two reports
    and difference them."""
    settlements = S.finra_settlements(_today())
    for sett in settlements[:2]:
        rows = S.finra_short_interest(sett)
        if not rows:
            continue
        total = sum(r["short"] for r in rows.values() if r.get("short") is not None)
        prev = sum(r["short_prev"] for r in rows.values() if r.get("short_prev") is not None)
        agg = {"settlement": sett, "total": total, "prev": prev, "change": total - prev,
               "n_symbols": len(rows), "rows": rows}
        by_sector: dict[str, dict] = {}
        placed = 0
        for sym, r in rows.items():
            sec = _sector_of(sym)
            if not sec or r.get("short") is None:
                continue
            placed += 1
            s = by_sector.setdefault(sec, {"short": 0.0, "prev": 0.0, "n_symbols": 0,
                                           "settlement": sett})
            s["short"] += r["short"]
            s["prev"] += r.get("short_prev") or 0.0
            s["n_symbols"] += 1
        floor = int(_knob("sector_min_symbols"))
        by_sector = {k: {**v, "change": v["short"] - v["prev"]}
                     for k, v in by_sector.items() if v["n_symbols"] >= floor}
        agg["placed"] = placed
        agg["sector_coverage"] = {"placed": placed, "of": len(rows),
                                  "sectors": len(by_sector), "min_symbols": floor}
        return agg, by_sector
    return None, None


def gather_short_volume() -> dict | None:
    """The short share of daily volume, and where today's sits in the recent
    range. Pressure, not positions: most of it is market-maker inventory
    covered the same session."""
    import market_calendar as _cal
    days = int(_knob("short_volume_days"))
    shares, sessions = [], []
    d = _today()
    tries = 0
    while len(shares) < days and tries < days * 2:
        tries += 1
        d = _cal.previous_session(d)
        got = S.finra_short_volume(d)
        if got and got.get("market_share") is not None:
            shares.append(got["market_share"])
            sessions.append(d.isoformat())
    if not shares:
        return None
    change = (shares[0] - shares[1]) if len(shares) > 1 else None
    return {"share": shares[0], "change": change, "session": sessions[0],
            "n_days": len(shares), "history": shares,
            "percentile": P.percentile(shares, shares[0])}


def gather_press() -> dict | None:
    """The prime-broker channel: what the wires quoted the banks saying about
    hedge fund positioning this week.

    Corroboration only. `hf_press` throws away most of what comes back — the
    performance stories, the foreign books, the headlines with no bank cited
    — and what survives enters a verdict at half weight and can never create
    one."""
    items = S.prime_broker_news()
    if not items:
        return None
    got = PRESS.build(items, _today(), max_age_days=int(_report_knob("press_max_age_days")))
    return got or None


def gather() -> dict:
    """Every aggregate source, with whatever failed named rather than
    silently missing."""
    out, failed = {}, []
    try:
        out["cftc"] = S.cftc_all()
    except Exception as exc:  # noqa: BLE001
        out["cftc"], _ = {}, failed.append(f"CFTC: {exc}")
    if not out.get("cftc"):
        failed.append("CFTC Traders in Financial Futures returned nothing")
    for name, fn, key in (("Sector ETF flows", gather_etf_flows, "etf_flows"),
                          ("Sector options tide", gather_tide, "tide"),
                          ("Daily short volume", gather_short_volume, "short_volume"),
                          ("Prime broker press", gather_press, "press"),
                          ("Form PF leverage", S.ofr_leverage, "ofr")):
        try:
            out[key] = fn()
        except Exception as exc:  # noqa: BLE001
            out[key] = None
            failed.append(f"{name}: {exc}")
        if not out.get(key):
            failed.append(f"{name} unavailable")
    try:
        out["short_interest"], out["sector_si"] = gather_short_interest()
    except Exception as exc:  # noqa: BLE001
        out["short_interest"], out["sector_si"] = None, None
        failed.append(f"Short interest: {exc}")
    if not out.get("short_interest"):
        failed.append("FINRA short interest not published yet for a settled period")
    out["_failed"] = failed
    return out


# ── building ────────────────────────────────────────────────────────────────

def build() -> dict:
    """One reading: gather, answer, stamp, store."""
    raw = gather()
    week = week_key(_today())
    prior = _prior_snapshot(week)
    press = raw.get("press") or {}
    board = P.build(
        raw.get("cftc") or {},
        etf_flows=raw.get("etf_flows"), tide=raw.get("tide"),
        short_interest=raw.get("short_interest"), short_volume=raw.get("short_volume"),
        sector_si=raw.get("sector_si"), ofr=raw.get("ofr"), prior=prior,
        prime_broker=press.get("quotes"))
    cftc_dates = [m.get("as_of") for m in (raw.get("cftc") or {}).values() if m.get("as_of")]
    board.update({
        "week": week, "as_of": _now().isoformat(timespec="seconds"),
        "cftc_as_of": max(cftc_dates) if cftc_dates else None,
        "scan_version": HF_SCAN_VERSION,
        "sources": {
            "cftc_markets": len(raw.get("cftc") or {}),
            "cftc_weeks": {k: v.get("weeks") for k, v in (raw.get("cftc") or {}).items()},
            "short_interest": (raw.get("short_interest") or {}).get("settlement"),
            "short_interest_symbols": (raw.get("short_interest") or {}).get("n_symbols"),
            "sector_coverage": (raw.get("short_interest") or {}).get("sector_coverage"),
            "short_volume_session": (raw.get("short_volume") or {}).get("session"),
            "short_volume_days": (raw.get("short_volume") or {}).get("n_days"),
            "etf_flow_as_of": (raw.get("etf_flows") or {}).get("as_of"),
            "tide_sectors": len(raw.get("tide") or {}),
            "ofr_as_of": (raw.get("ofr") or {}).get("lev_top10", {}).get("as_of"),
            "press_quotes": press.get("n_quotes") or 0,
            "press_captured": press.get("n_captured") or 0,
            "press_banks": press.get("banks") or [],
        },
        "press": press or None,
        "unavailable": raw.get("_failed") or [],
        "dates": {"cftc_as_of": long_date(max(cftc_dates) if cftc_dates else None),
                  "short_interest": long_date((raw.get("short_interest") or {}).get("settlement")),
                  "short_volume": long_date((raw.get("short_volume") or {}).get("session")),
                  "ofr": long_date((raw.get("ofr") or {}).get("lev_top10", {}).get("as_of"))},
    })
    # The full short-interest table is thousands of rows and is not part of
    # the answer — only the crowded names it produced are kept.
    _save_snapshot(board)
    with _LOCK:
        _STATE.update({"board": board, "as_of": board["as_of"], "week": week, "error": None})
    return board


def _stale() -> bool:
    with _LOCK:
        b, ts = _STATE["board"], _STATE["as_of"]
    if not b or not ts:
        return True
    if b.get("week") != week_key(_today()):
        return True          # a new week always earns a fresh reading
    try:
        then = datetime.fromisoformat(ts)
    except ValueError:
        return True
    return (_now() - then).total_seconds() > float(_knob("refresh_hours")) * 3600.0


def _kick() -> None:
    if not S.available():
        return
    with _LOCK:
        if _STATE["refreshing"]:
            return
        _STATE["refreshing"] = True

    def run():
        try:
            build()
        except Exception as exc:  # noqa: BLE001
            with _LOCK:
                _STATE["error"] = str(exc)[:300]
        finally:
            with _LOCK:
                _STATE["refreshing"] = False

    t = threading.Thread(target=run, name="hf-pulse", daemon=True)
    with _LOCK:
        _STATE["thread"] = t
    t.start()


def refresh_now() -> dict:
    build()
    return status()


# ── payloads ────────────────────────────────────────────────────────────────

def snapshot() -> dict:
    if _stale():
        _kick()
    with _LOCK:
        b, refreshing, err = _STATE["board"], _STATE["refreshing"], _STATE["error"]
    if not b:
        return {"ok": True, "available": False, "refreshing": refreshing, "error": err,
                "version": HF_SCAN_VERSION, "pulse_version": P.HF_PULSE_VERSION,
                "note": ("The Pulse has not been read yet. It gathers on first look and once a "
                         "week after that, on the CFTC's schedule."),
                "history": history(12)}
    out = dict(b)
    out.update({"ok": True, "available": True, "refreshing": refreshing, "error": err,
                "version": HF_SCAN_VERSION, "history": history(12)})
    return out


def headline() -> dict:
    """The one sentence the Named Fund Watch shows under its own heading.

    Deliberately narrow: what the universe did, on what evidence, with the
    date. It is handed to the fund cards as a separate block and can never
    become part of a fund's own record."""
    with _LOCK:
        b = _STATE["board"]
    if not b:
        return {"available": False, "class": S.INFERENCE,
                "note": ("The aggregate layer has not been read yet. Nothing here is about "
                         "any one fund.")}
    ex, sec = b.get("exposure") or {}, b.get("sectors") or {}
    bits = []

    def streak_words(q) -> str:
        """Only a streak with a direction is worth saying. A streak of zero
        weeks means this week had no clear direction, and appending that to
        a verdict produces "reducing — no clear direction this week"."""
        st = q.get("streak") or {}
        return f" ({st['word']})" if st.get("weeks") and st.get("direction") else ""

    if ex.get("verdict") and ex["verdict"] != P.NO_DATA:
        bits.append(f"Overall exposure: {ex['verdict'].lower()}{streak_words(ex)}")
    for r in (sec.get("most_sold") or [])[:2]:
        bits.append(f"{r['sector']}: selling{streak_words(r)}")
    for r in (sec.get("most_bought") or [])[:2]:
        bits.append(f"{r['sector']}: buying{streak_words(r)}")
    return {"available": bool(bits), "class": S.INFERENCE, "week": b.get("week"),
            "as_of": b.get("cftc_as_of"), "as_of_text": (b.get("dates") or {}).get("cftc_as_of"),
            "text": " · ".join(bits) if bits else None,
            "confidence": (ex.get("confidence") or {}).get("level"),
            "note": ("This is the hedge fund universe as a whole, from anonymous sources. "
                     "It is not about any one fund and never becomes part of one.")}


def status() -> dict:
    with _LOCK:
        b = _STATE["board"]
        return {"version": HF_SCAN_VERSION, "pulse_version": P.HF_PULSE_VERSION,
                "as_of": _STATE["as_of"], "week": _STATE["week"],
                "refreshing": _STATE["refreshing"], "error": _STATE["error"],
                "available": bool(b), "weeks_stored": len(history(200)),
                "cftc_as_of": (b or {}).get("cftc_as_of"),
                "unavailable": (b or {}).get("unavailable") or [],
                "online": S.available()}


# ══════════════════════════════════════════════════════════════════════════
# PHASE 3 — the weekly report: assemble, store forever, compare.
#
# One document per ISO week under hf/reports/<week>.json. A re-run inside the
# same week does NOT overwrite: it appends a revision, because a report is a
# record of what was known at a moment and a later rebuild is a second
# moment, not a correction of the first. The newest revision is the current
# report; the older ones stay readable.
# ══════════════════════════════════════════════════════════════════════════

def _report_dir() -> Path | None:
    return None if _DATA_DIR is None else _DATA_DIR / "hf" / "reports"


def _report_path(week: str) -> Path | None:
    d = _report_dir()
    return None if d is None else d / f"{week}.json"


def week_start(d: date) -> str:
    """The Monday of the ISO week — the boundary "filed this week" uses."""
    return (d - timedelta(days=d.weekday())).isoformat()


def _read_week_file(week: str) -> dict | None:
    p = _report_path(week)
    if p is None or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def save_report(rep: dict) -> dict | None:
    """Append this build as a new revision of its week.

    The attribution check runs again here for the same reason the pulse store
    runs it: a file on disk outlives the code that wrote it, and a stored row
    that named a fund against anonymous data would be a permanent error."""
    rows = []
    for c in rep.get("conclusions") or []:
        rows.extend(c.get("inputs") or [])
    if not S.attribution_ok(rows):
        raise ValueError("refusing to store a report that attributes anonymous data to a fund")
    week = rep.get("week")
    p = _report_path(week) if week else None
    if p is None:
        return None
    doc = _read_week_file(week) or {"week": week, "revisions": []}
    revs = doc.get("revisions") or []
    # Number from the builds that have HAPPENED, not from the ones still on
    # disk. Once a week passes the retention limit the list holds only the
    # survivors, so counting them reused the highest number: build 14 wrote
    # a second revision 13, and asking for revision 13 returned the older of
    # the two. The count of builds is its own field for the same reason.
    built = max([int(doc.get("n_revisions") or 0)]
                + [int(r.get("revision") or 0) for r in revs]) + 1
    revs.append({"revision": built, "built_at": rep.get("built_at"), "report": rep})
    keep = int(_report_knob("keep_revisions"))
    doc["revisions"] = revs[-keep:] if keep > 0 else revs
    doc["week"] = week
    doc["n_revisions"] = built
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(p)
    except Exception:  # noqa: BLE001
        return None
    return doc


def report_for(week: str, revision: int | None = None) -> dict | None:
    """A stored week's report — the newest revision unless one is named.

    Normalised on the way out, never on disk: an older report that predates
    a field the card reads gets it filled in here, and the stored bytes stay
    exactly what was written."""
    doc = _read_week_file(week)
    revs = (doc or {}).get("revisions") or []
    if not revs:
        return None
    if revision is None:
        return RPT.normalize(revs[-1].get("report"))
    for r in revs:
        if int(r.get("revision") or 0) == int(revision):
            return RPT.normalize(r.get("report"))
    return None


def report_revisions(week: str) -> list[dict]:
    doc = _read_week_file(week)
    return [{"revision": r.get("revision"), "built_at": r.get("built_at"),
             "built_at_text": RPT.long_date(r.get("built_at"))}
            for r in (doc or {}).get("revisions") or []]


def report_history(limit: int = 60) -> list[dict]:
    """Every stored week, newest first, one line each."""
    d = _report_dir()
    if d is None or not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json"), reverse=True)[:limit]:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        revs = doc.get("revisions") or []
        if not revs:
            continue
        row = RPT.digest(revs[-1].get("report") or {})
        row["n_revisions"] = doc.get("n_revisions") or len(revs)
        out.append(row)
    return out


def _prior_report(this_week: str) -> dict | None:
    """The newest stored report from a DIFFERENT week. Rebuilding inside the
    same week must diff against last week, never against this week's own
    earlier revision — otherwise "what changed" would report the noise
    between two builds an hour apart."""
    d = _report_dir()
    if d is None or not d.exists():
        return None
    for p in sorted(d.glob("*.json"), reverse=True):
        if p.stem == this_week:
            continue
        rep = report_for(p.stem)
        if rep:
            return rep
    return None


def _funds() -> dict:
    if _FUNDS_FN is None:
        return {}
    try:
        return _FUNDS_FN() or {}
    except Exception:  # noqa: BLE001
        return {}


def build_report() -> dict:
    """Assemble this week's report from the board, the fund watch and the
    press, then store it as a new revision."""
    with _LOCK:
        board = _STATE["board"]
    # The board must belong to THIS week before a report is built from it.
    # Taking last week's stored reading here produced a report filed under
    # last week, which `_report_stale` then judged stale forever — so every
    # look at the card appended another revision to a week that was already
    # over, and the pulse was never re-read.
    if not board or board.get("week") != week_key(_today()):
        board = build()
    week = board.get("week") or week_key(_today())
    rep = RPT.build(board, _funds(), board.get("press"), _prior_report(week),
                    week=week, built_at=_now().isoformat(timespec="seconds"),
                    week_start=week_start(_today()))
    rep["scan_version"] = HF_SCAN_VERSION
    save_report(rep)
    with _LOCK:
        _STATE.update({"report": rep, "report_week": week, "report_at": rep["built_at"],
                       "report_error": None})
    return rep


def _report_stale() -> bool:
    with _LOCK:
        rep, ts = _STATE["report"], _STATE["report_at"]
    if not rep or not ts:
        return True
    if rep.get("week") != week_key(_today()):
        return True
    try:
        then = datetime.fromisoformat(ts)
    except ValueError:
        return True
    return (_now() - then).total_seconds() > float(_report_knob("rebuild_hours")) * 3600.0


def _kick_report() -> None:
    if not S.available():
        return
    with _LOCK:
        if _STATE["report_refreshing"]:
            return
        _STATE["report_refreshing"] = True

    def run():
        try:
            build_report()
        except Exception as exc:  # noqa: BLE001
            with _LOCK:
                _STATE["report_error"] = str(exc)[:300]
        finally:
            with _LOCK:
                _STATE["report_refreshing"] = False

    t = threading.Thread(target=run, name="hf-report", daemon=True)
    t.start()


def _load_latest_report() -> None:
    h = report_history(1)
    if not h:
        return
    rep = report_for(h[0]["week"])
    if rep:
        with _LOCK:
            _STATE.update({"report": rep, "report_week": rep.get("week"),
                           "report_at": rep.get("built_at")})


def report(week: str | None = None, revision: int | None = None) -> dict:
    """The current report, or a stored week's. Builds lazily on first look."""
    if week:
        rep = report_for(week, revision)
        if not rep:
            return {"ok": False, "available": False, "week": week,
                    "error": f"no report stored for {week}",
                    "history": report_history(60)}
        return {"ok": True, "available": True, **rep,
                "revisions": report_revisions(week), "history": report_history(60)}
    if _report_stale():
        _kick_report()
    with _LOCK:
        rep, refreshing, err = _STATE["report"], _STATE["report_refreshing"], _STATE["report_error"]
    if not rep:
        return {"ok": True, "available": False, "refreshing": refreshing, "error": err,
                "version": RPT.HF_REPORT_VERSION, "scan_version": HF_SCAN_VERSION,
                "note": ("The weekly report has not been assembled yet. It is built from the "
                         "Pulse, the Named Fund Watch and the prime-broker headlines, once a "
                         "week on the CFTC's schedule."),
                "history": report_history(60)}
    return {"ok": True, "available": True, "refreshing": refreshing, "error": err,
            **rep, "revisions": report_revisions(rep.get("week") or ""),
            "history": report_history(60)}


def report_compare(week_a: str, week_b: str) -> dict:
    a, b = report_for(week_a), report_for(week_b)
    if not a:
        return {"ok": False, "error": f"no report stored for {week_a}"}
    if not b:
        return {"ok": False, "error": f"no report stored for {week_b}"}
    return RPT.compare(a, b)


def report_now() -> dict:
    build_report()
    return report_status()


def report_status() -> dict:
    with _LOCK:
        rep = _STATE["report"]
        # `version` describes the document being served, not the code doing
        # the serving. Between a deploy and the next rebuild those differ,
        # and a status that reported the running module's number claimed a
        # shape the stored document did not have.
        return {"version": (rep or {}).get("version") or RPT.HF_REPORT_VERSION,
                "code_version": RPT.HF_REPORT_VERSION,
                "press_version": PRESS.HF_PRESS_VERSION,
                "scan_version": HF_SCAN_VERSION,
                "week": _STATE["report_week"], "built_at": _STATE["report_at"],
                "refreshing": _STATE["report_refreshing"], "error": _STATE["report_error"],
                "available": bool(rep), "weeks_stored": len(report_history(200)),
                "n_conflicts": (rep or {}).get("n_conflicts"),
                "n_quotes": ((rep or {}).get("press") or {}).get("n_quotes"),
                "online": S.available()}


# ══════════════════════════════════════════════════════════════════════════
# PHASE 4 — the outcome grader: did the reading precede the move?
#
# Every limitation section since Phase 2 has carried the line "nothing here
# is calibrated against outcomes yet." This is that calibration.
#
# Crowding can be graded across three years because it is computed from the
# CFTC series and nothing else: truncating that series at week W reproduces
# exactly what the board would have said in week W, with no data that
# arrived later. The four weekly verdicts cannot — they need short interest
# and flows that are not kept historically — so they are graded only from
# readings stored since the board started keeping them.
# ══════════════════════════════════════════════════════════════════════════

DEFAULTS["grade"] = {
    "min_history_weeks": 52,    # a reconstructed week needs this much past to rank against
    "min_graded_weeks": 20,     # below this, no share is offered as a finding
    "rebuild_hours": 168,       # once a week; the inputs move once a week
    "retry_hours": 1,           # cooldown after a build that priced nothing
    "closes_timeframe": "3Y",   # how much weekly candle history to ask for
}


def _grade_knob(name: str):
    try:
        return (config().get("grade") or {}).get(name, DEFAULTS["grade"][name])
    except Exception:  # noqa: BLE001
        return DEFAULTS["grade"][name]


def grade_symbols() -> list[str]:
    """Every proxy the grader prices, deduplicated."""
    return sorted({*GR.GRADE_PROXY.values(), *GR.SECTOR_PROXY.values(), "SPY"})


def gather_weekly_closes(symbols: list[str] | None = None) -> dict:
    """One weekly close per ISO week per proxy.

    Unusual Whales returns weekly candles whose `date` is the Monday of the
    ISO week, which is the key the board already files readings under, so
    the two line up without any date arithmetic here."""
    uw = _uw()
    if uw is None or not hasattr(uw, "ohlc"):
        return {}
    tf = str(_grade_knob("closes_timeframe"))
    out: dict[str, dict] = {}
    for sym in (symbols or grade_symbols()):
        try:
            data = uw.ohlc(sym, "1w", tf)
        except Exception:  # noqa: BLE001
            continue
        # The client unwraps the envelope for some endpoints and not others,
        # the same difference that broke the sector tide in v4.86.
        rows = data.get("data") if isinstance(data, dict) else data
        if not rows:
            continue
        by_week = {}
        for r in rows:
            try:
                d = date.fromisoformat(str(r.get("date"))[:10])
                close = float(r.get("close"))
            except (TypeError, ValueError):
                continue
            by_week[GR.week_key(d)] = close
        if by_week:
            out[sym] = by_week
    return out


def crowded_history(cftc: dict, min_history: int | None = None) -> list[dict]:
    """Crowding as it would have read in every past week.

    Point in time by construction: for week W the series is truncated so
    that W is the newest row, and `hf_pulse.crowding` sees exactly the
    history that existed then. Nothing later can leak into the percentile.

    The CFTC report describes Tuesday of week W and is published that
    Friday, so a reading dated week W was in hand before week W closed —
    which is why grading forward from week W's own close is fair rather
    than a peek."""
    if not cftc:
        return []
    floor = int(min_history if min_history is not None else _grade_knob("min_history_weeks"))
    rows = []
    # Market by market, each as deep as its OWN series allows. Slicing every
    # market together to the shortest one's depth cut all twelve down to the
    # 69 weeks the Communication Services contract has, throwing away two
    # years of the S&P's history for no reason.
    for key, m in (cftc or {}).items():
        series = m.get("series") or []
        for i in range(max(0, len(series) - floor)):
            window = series[i:]
            # `as_of` has to move with the window. Carrying the market's
            # original as_of labelled every reconstructed week with today's
            # date, so 204 rows all landed in the same week.
            one = {key: {**m, "series": window, "as_of": window[0].get("date")}}
            try:
                board = P.crowding(one)
            except Exception:  # noqa: BLE001
                continue
            for r in board.get("markets") or []:
                as_of = r.get("as_of")
                try:
                    wk = GR.week_key(date.fromisoformat(str(as_of)[:10]))
                except (TypeError, ValueError):
                    continue
                rows.append({"week": wk, "market": r.get("market"), "key": r.get("key"),
                             "state": r.get("state"), "side": r.get("side"), "as_of": as_of})
    return rows


# ── the grade store ─────────────────────────────────────────────────────────

def _grade_path() -> Path | None:
    return None if _DATA_DIR is None else _DATA_DIR / "hf" / "grades.json"


def save_grades(card: dict) -> None:
    p = _grade_path()
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(card), encoding="utf-8")
        tmp.replace(p)
    except Exception:  # noqa: BLE001
        pass


def _load_grades() -> None:
    p = _grade_path()
    if p is None or not p.exists():
        return
    try:
        card = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    with _LOCK:
        _STATE.update({"grades": card, "grades_at": card.get("as_of")})


def build_grades() -> dict:
    """Reconstruct the crowding history, price it, and grade both layers."""
    raw_cftc = S.cftc_all()
    weeks = crowded_history(raw_cftc)
    closes = gather_weekly_closes()
    readings = [{"week": h.get("week"), "verdicts": h.get("verdicts") or {}}
                for h in history(400)]
    card = GR.build(weeks, readings, closes,
                    min_n=int(_grade_knob("min_graded_weeks")),
                    source="CFTC Traders in Financial Futures, reconstructed week by week",
                    as_of=_now().isoformat(timespec="seconds"))
    card.update({"scan_version": HF_SCAN_VERSION,
                 "proxies_priced": sorted(closes),
                 "n_proxies": len(closes),
                 "cftc_markets": len(raw_cftc),
                 "min_history_weeks": int(_grade_knob("min_history_weeks")),
                 "headline": GR.headline(card),
                 "unavailable": ([] if closes else
                                 ["Weekly closes unavailable, so nothing could be graded."])})
    if not closes:
        # Nothing was priced, so nothing was graded. Storing this and
        # stamping it fresh would cache an outage as a finished answer for a
        # week: the panel would show an empty table, report itself available,
        # and never retry once the provider came back. So it is not stored.
        #
        # But "not stored" alone meant "still stale", and every look at the
        # panel then started another full reconstruction — the panel polls
        # every twenty seconds while one is running, so an outage spun the
        # CFTC and proxy calls continuously instead of waiting for the
        # provider to come back. The retry stamp below is the wait. An
        # explicit rebuild ignores it, because a person asking is not a loop.
        with _LOCK:
            _STATE["grades_error"] = ("No weekly closes were returned, so the record could not "
                                      "be graded. Nothing was stored; it will try again in "
                                      f"{int(_grade_knob('retry_hours'))} hour(s).")
            _STATE["grades_retry_at"] = (_now() + timedelta(
                hours=float(_grade_knob("retry_hours")))).isoformat(timespec="seconds")
        return card
    save_grades(card)
    with _LOCK:
        _STATE.update({"grades": card, "grades_at": card["as_of"], "grades_error": None,
                       "grades_retry_at": None})
    return card


def _grades_stale() -> bool:
    with _LOCK:
        card, ts = _STATE.get("grades"), _STATE.get("grades_at")
        retry_at = _STATE.get("grades_retry_at")
    # A failed build sets a cooldown. Until it passes the answer is "not
    # yet", not "try again right now".
    if retry_at:
        try:
            if _now() < datetime.fromisoformat(retry_at):
                return False
        except ValueError:
            pass
    if not card or not ts:
        return True
    try:
        then = datetime.fromisoformat(ts)
    except ValueError:
        return True
    return (_now() - then).total_seconds() > float(_grade_knob("rebuild_hours")) * 3600.0


def _kick_grades() -> None:
    if not S.available():
        return
    with _LOCK:
        if _STATE.get("grades_refreshing"):
            return
        _STATE["grades_refreshing"] = True

    def run():
        try:
            build_grades()
        except Exception as exc:  # noqa: BLE001
            with _LOCK:
                _STATE["grades_error"] = str(exc)[:300]
        finally:
            with _LOCK:
                _STATE["grades_refreshing"] = False

    threading.Thread(target=run, name="hf-grade", daemon=True).start()


def grades() -> dict:
    if _grades_stale():
        _kick_grades()
    with _LOCK:
        card = _STATE.get("grades")
        refreshing, err = _STATE.get("grades_refreshing"), _STATE.get("grades_error")
    if not card:
        return {"ok": True, "available": False, "refreshing": refreshing, "error": err,
                "version": GR.HF_GRADE_VERSION, "scan_version": HF_SCAN_VERSION,
                "note": ("The record has not been graded yet. It reconstructs three years of "
                         "crowding from the CFTC series and prices what followed, which takes "
                         "about a minute the first time.")}
    return {"ok": True, "available": True, "refreshing": refreshing, "error": err, **card}


def grades_now() -> dict:
    build_grades()
    return grade_status()


def grade_status() -> dict:
    with _LOCK:
        card = _STATE.get("grades")
        # The card's version, not the module's. A grade card is rebuilt
        # weekly, so after a deploy the stored one can be several versions
        # behind — and a status saying 1.0.1 while serving a 1.0.0 card is
        # how a counting bug survived a live check.
        return {"version": (card or {}).get("version") or GR.HF_GRADE_VERSION,
                "code_version": GR.HF_GRADE_VERSION,
                "scan_version": HF_SCAN_VERSION,
                "as_of": _STATE.get("grades_at"),
                "refreshing": bool(_STATE.get("grades_refreshing")),
                "error": _STATE.get("grades_error"),
                "retry_after": _STATE.get("grades_retry_at"),
                "available": bool(card),
                "n_market_weeks": (card or {}).get("n_market_weeks"),
                "n_crowded_weeks": (card or {}).get("n_crowded_weeks"),
                "n_crowded_weeks_graded": (card or {}).get("n_crowded_weeks_graded"),
                "n_readings": (card or {}).get("n_readings"),
                "n_proxies": (card or {}).get("n_proxies"),
                "online": S.available()}
