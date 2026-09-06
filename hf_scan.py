"""hf_scan.py — the Hedge Fund Pulse board: gather, answer, remember.

The stateful half of the aggregate layer. It fetches what the sources will
give, hands it to the pure `hf_pulse` math, and keeps every weekly answer
forever so "what changed from last week" is a record rather than a claim.

Three things it is careful about:

  * **One reading per week, stored under its ISO week.** The CFTC report is
    the clock: positions as of Tuesday, published Friday at 3:30 PM ET. A
    re-run in the same week overwrites that week's snapshot; a run in a new
    week starts a new one and the previous becomes the comparison.

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

import hf_pulse as P
import hf_sources as S

HF_SCAN_VERSION = "1.0.0"

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
}

_DATA_DIR: Path | None = None
_UW_GETTER = None
_SECTOR_FN = None            # (symbol) -> sector label | None
_SECTOR_NORM = None          # (label) -> one of the app's eleven names | None
_NOW_FN = None
_LOCK = threading.RLock()
_STATE: dict = {"board": None, "as_of": None, "refreshing": False, "thread": None,
                "error": None, "sources": {}, "week": None}

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


def configure(data_dir=None, uw_getter=None, sector_fn=None, sector_norm=None, now_fn=None) -> None:
    global _DATA_DIR, _UW_GETTER, _SECTOR_FN, _SECTOR_NORM, _NOW_FN
    _DATA_DIR = Path(data_dir) if data_dir else None
    _UW_GETTER, _SECTOR_FN, _SECTOR_NORM, _NOW_FN = uw_getter, sector_fn, sector_norm, now_fn
    if _DATA_DIR is not None:
        try:
            (_DATA_DIR / "hf" / "pulse").mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
    _load_latest()


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
    board = P.build(
        raw.get("cftc") or {},
        etf_flows=raw.get("etf_flows"), tide=raw.get("tide"),
        short_interest=raw.get("short_interest"), short_volume=raw.get("short_volume"),
        sector_si=raw.get("sector_si"), ofr=raw.get("ofr"), prior=prior)
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
        },
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
