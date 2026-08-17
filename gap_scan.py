"""gap_scan.py — stateful side of the Premarket Gap Fade & Rebound scanner.

gap_engine.py is the pure math; this module owns everything with a clock,
a network or a disk:

  Stage 1  one batched quote sweep over the universe (watchlist + analyst
           universe + SPY/QQQ/sector ETFs) → fresh premarket movers only.
  Stage 2  top candidates get premarket minute bars, an event-store
           refresh, cohort statistics, a signal with hysteresis, and a
           what-changed line.
  Store    <data>/gap/events/SYM.json — the historical event database.
           Daily-bar qualifiers reach back years; minute-path enrichment
           covers the source's ~6-month retention and is archived forever
           (compact checkpoints, not raw bars), so coverage grows every
           morning the scanner runs. PM-only qualifiers (reached the
           threshold premarket but opened small) are discovered on hinted
           days historically and on EVERY scanned day going forward.
  Journal  <data>/gap/decisions/YYYY-MM-DD.jsonl — replayable record of
           each evaluation (timing-engine discipline).

Dependencies are injected via configure() so tests run fully offline.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import gap_engine as ge

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:            # pragma: no cover
    _ET = None

# ── injected dependencies ────────────────────────────────────────────────────

_SCHWAB_FN = None            # () -> SchwabClient | None
_WATCHLIST_FN = None         # () -> {"starred": [...], "all": [...]}
_UNIVERSE_FN = None          # () -> [symbols]  (analyst board universe)
_BOARD_FN = None             # () -> watchlist_table board dict (avg vol etc.)
_DAILY_FN = None             # (sym, days) -> {"bars": [...], "source": str}
_ACTIONS_FN = None           # (sym) -> {"splits": set[date_iso], "dividends": {date: amt}}
_EARN_HIST_FN = None         # (sym) -> set[date_iso] of past report dates
_CATALYST_FN = None          # (sym) -> {"kind": str, "label": str} | None
_SECTOR_ETF_FN = None        # (sym) -> "XLK" | None
_EARN_NEXT_FN = None         # (sym) -> {"next": ISO} | None
_OFFERING_FN = None          # (sym) -> {date_iso: "OFFERING" | "DILUTION"}
_DATA_DIR: Path | None = None

_LOCK = threading.Lock()
_STATE = {"scanning": False, "scanned": 0, "total": 0, "last_scan": None,
          "universe_size": 0, "error": None, "rows": [], "as_of": None,
          "context": {}, "session": None, "price_as_of": None}
_HYST: dict = {}             # symbol -> hysteresis memory
_PREV_ROWS: dict = {}        # symbol -> previous row (what-changed)
_STORE_LOCK = threading.Lock()
_SCHED = {"started": False, "last_tick_scan": 0.0, "recorded_for": None}
_DAILY_CACHE: dict = {}      # sym -> (ts, pack); one fetch serves a whole scan


def _daily(sym: str, days: int) -> dict:
    """Injected daily loader behind a 10-minute cache — analyze/hints/ATR
    all want the same bars during one scan pass; fetch once."""
    if not _DAILY_FN:
        return {}
    key = sym.upper()
    hit = _DAILY_CACHE.get(key)
    if hit and time.time() - hit[0] < 600 and len((hit[1] or {}).get("bars") or []) >= days * 0.5:
        return hit[1]
    pack = _DAILY_FN(sym, days) or {}
    _DAILY_CACHE[key] = (time.time(), pack)
    if len(_DAILY_CACHE) > 200:
        oldest = min(_DAILY_CACHE, key=lambda k: _DAILY_CACHE[k][0])
        _DAILY_CACHE.pop(oldest, None)
    return pack


def configure(schwab_getter=None, watchlist_fn=None, universe_fn=None,
              board_fn=None, daily_fn=None, actions_fn=None,
              earn_hist_fn=None, catalyst_fn=None, sector_etf_fn=None,
              data_dir=None, earn_next_fn=None, offering_fn=None) -> None:
    global _SCHWAB_FN, _WATCHLIST_FN, _UNIVERSE_FN, _BOARD_FN, _DAILY_FN
    global _ACTIONS_FN, _EARN_HIST_FN, _CATALYST_FN, _SECTOR_ETF_FN, _DATA_DIR
    global _EARN_NEXT_FN, _OFFERING_FN
    _EARN_NEXT_FN = earn_next_fn
    _OFFERING_FN = offering_fn
    _SCHWAB_FN = schwab_getter
    _WATCHLIST_FN = watchlist_fn
    _UNIVERSE_FN = universe_fn
    _BOARD_FN = board_fn
    _DAILY_FN = daily_fn
    _ACTIONS_FN = actions_fn
    _EARN_HIST_FN = earn_hist_fn
    _CATALYST_FN = catalyst_fn
    _SECTOR_ETF_FN = sector_etf_fn
    if data_dir:
        _DATA_DIR = Path(data_dir)
        try:
            (_DATA_DIR / "gap" / "events").mkdir(parents=True, exist_ok=True)
            (_DATA_DIR / "gap" / "decisions").mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover
            print(f"[gap] storage init failed: {exc}")
        ge.configure(data_dir)
        _restore_board()


def _cfg() -> dict:
    c, _ = ge.config()
    return c


def _now_et() -> datetime:
    return datetime.now(_ET) if _ET else datetime.now()


_CTX_ETFS = ["SPY", "QQQ", "XLK", "XLF", "XLV", "XLE", "XLY", "XLP", "XLI",
             "XLB", "XLU", "XLRE", "XLC"]

_MINUTE_RETENTION_DAYS = 175   # stay inside the source's ~6-month window


# ── event store ──────────────────────────────────────────────────────────────

def _store_path(sym: str) -> Path | None:
    if not _DATA_DIR:
        return None
    safe = "".join(c for c in sym.upper() if c.isalnum() or c in ("-", "_", "."))
    return _DATA_DIR / "gap" / "events" / f"{safe}.json"


def load_store(sym: str) -> dict:
    p = _store_path(sym)
    if p and p.exists():
        try:
            d = json.loads(p.read_text())
            if isinstance(d, dict) and isinstance(d.get("events"), list):
                return d
        except Exception:
            pass
    return {"schema": ge.SCHEMA_VERSION, "symbol": sym.upper(), "events": [],
            "minute_scanned": {}, "daily_through": None, "daily_source": None,
            "updated": None}


def _save_store(sym: str, store: dict) -> None:
    p = _store_path(sym)
    if not p:
        return
    try:
        cap = int(_cfg().get("event", {}).get("max_events_per_symbol", 500))
        store["events"] = sorted(store["events"], key=lambda e: e["date"])[-cap:]
        store["updated"] = _now_et().isoformat(timespec="seconds")
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(store, separators=(",", ":")))
        tmp.replace(p)
    except Exception as exc:  # pragma: no cover
        print(f"[gap] store write failed {sym}: {exc}")


def refresh_daily_events(sym: str, store: dict, now_d: date | None = None) -> dict:
    """(Re)extract official-gap events from daily history and merge them in.
    Existing events keep their minute enrichment; new dates are added."""
    if not _DAILY_FN:
        return store
    cfg = _cfg()
    days = int(cfg.get("event", {}).get("daily_history_days", 900))
    pack = _daily(sym, days)
    bars = pack.get("bars") or []
    if len(bars) < 40:
        return store
    acts = (_ACTIONS_FN(sym) if _ACTIONS_FN else None) or {}
    earn = (_EARN_HIST_FN(sym) if _EARN_HIST_FN else None) or set()
    offers = (_OFFERING_FN(sym) if _OFFERING_FN else None) or {}
    evs = ge.extract_daily_events(
        bars, cfg, split_dates=set(acts.get("splits") or []),
        div_by_date=acts.get("dividends") or {}, earnings_dates=earn,
        offering_dates=offers)
    by_date = {e["date"]: e for e in store["events"]}
    for e in evs:
        old = by_date.get(e["date"])
        if old:
            # keep minute/pm enrichment, refresh daily-derived fields
            for k in ("outcomes",):
                merged = dict(e["outcomes"])
                for kk, vv in (old.get("outcomes") or {}).items():
                    if kk != "daily":
                        merged[kk] = vv
                e["outcomes"] = merged
            for k in ("pm", "pm_first_cross_ts", "pm_gap_max_pct"):
                if old.get(k) is not None:
                    e[k] = old[k]
            if "PM" in (old.get("qualified_by") or []):
                e["qualified_by"] = sorted(set(e["qualified_by"]) | {"PM"})
        by_date[e["date"]] = e
    store["events"] = sorted(by_date.values(), key=lambda x: x["date"])
    store["daily_through"] = ge._bar_date(bars[-1])
    store["daily_source"] = pack.get("source")
    return store


def _minute_hint_dates(sym: str, store: dict, cfg: dict, today: date) -> list:
    """Dates worth spending a minute-history call on, newest first:
    event dates without minute enrichment, plus daily days whose range
    hints a possible PM-only qualifier (§6 discovery limit: days with no
    daily hint AND no live scan are honestly invisible)."""
    ev = cfg.get("event", {})
    lo_gate = float(ev.get("minute_hint_gap_pct", 2.0))
    pm_gate = float(ev.get("pm_gap_min_pct", 5.0))
    cutoff = (today - timedelta(days=_MINUTE_RETENTION_DAYS)).isoformat()
    scanned = store.get("minute_scanned") or {}
    want = []
    for e in store["events"]:
        if e["date"] >= cutoff and e["date"] not in scanned \
                and not e.get("exclusion"):
            want.append(e["date"])
    pack = _daily(sym, int(ev.get("daily_history_days", 900)))
    bars = pack.get("bars") or []
    have = {e["date"] for e in store["events"]}
    for i in range(1, len(bars)):
        d = ge._bar_date(bars[i])
        if d < cutoff or d in have or d in scanned:
            continue
        pc = bars[i - 1].get("close")
        b = bars[i]
        if not pc or pc <= 0:
            continue
        og = abs(ge.official_gap_pct(b.get("open"), pc) or 0)
        hi_ex = (b.get("high") or 0) / pc * 100.0 - 100.0
        lo_ex = 100.0 - (b.get("low") or pc) / pc * 100.0
        # the hint gate is deliberately LOWER than the PM threshold: a +9%
        # premarket spike that faded before the open can leave only a faint
        # daily trace, so any day that moved past the hint gets one minute
        # call to check whether the premarket actually crossed
        if og >= lo_gate or hi_ex >= lo_gate or lo_ex >= lo_gate:
            want.append(d)
    return sorted(set(want), reverse=True)


def enrich_minutes(sym: str, store: dict, budget: int,
                   today: date | None = None) -> int:
    """Spend up to `budget` minute-history calls enriching this symbol's
    events with real premarket + session paths. Resumable: every scanned
    date is marked, so coverage accumulates across mornings."""
    sc = _SCHWAB_FN() if _SCHWAB_FN else None
    if not sc or budget <= 0:
        return 0
    cfg = _cfg()
    today = today or _now_et().date()
    used = 0
    for d in _minute_hint_dates(sym, store, cfg, today):
        if used >= budget:
            break
        if hasattr(sc, "rate_usage") and sc.rate_usage() >= int(
                cfg.get("scan", {}).get("budget_req_per_min", 70)):
            break
        used += 1
        bars = sc.get_intraday_day(sym, d, extended=True)
        store.setdefault("minute_scanned", {})[d] = "ok" if bars else "empty"
        if not bars:
            continue
        _apply_minute_day(sym, store, d, bars, cfg)
    if used:
        _save_store(sym, store)
    return used


def _apply_minute_day(sym: str, store: dict, d: str, bars: list, cfg: dict) -> None:
    """Fold one day's extended minute bars into the store: PM block,
    PM-qualification, and the regular-session path outcomes."""
    try:
        day = date.fromisoformat(d)
    except ValueError:
        return
    cut = ge.session_cut_ms(day, _ET)
    pm = [b for b in bars if cut - int(5.5 * 3600 * 1000) <= (b.get("ts") or 0) < cut]
    reg = [b for b in bars if (b.get("ts") or 0) >= cut]
    by_date = {e["date"]: e for e in store["events"]}
    e = by_date.get(d)
    prev_close = e.get("prev_close") if e else None
    if prev_close is None:
        pack = _daily(sym, int(cfg.get("event", {}).get("daily_history_days", 900)))
        dbars = pack.get("bars") or []
        for i in range(1, len(dbars)):
            if ge._bar_date(dbars[i]) == d:
                prev_close = dbars[i - 1].get("close")
                break
        # recording TODAY before the daily source has appended today's bar:
        # the last stored bar IS the prior session
        if prev_close is None and dbars and d > ge._bar_date(dbars[-1]):
            prev_close = dbars[-1].get("close")
    if not prev_close:
        return
    pm_gate = float(cfg.get("event", {}).get("pm_gap_min_pct", 5.0))
    cross = ge.pm_first_cross(pm, prev_close, pm_gate)
    if e is None and cross is None:
        return                      # hinted day, but never actually crossed
    if e is None:
        # PM-only qualifier: crossed premarket, opened below the official gate
        open_px = reg[0].get("open") if reg else None
        og = ge.official_gap_pct(open_px, prev_close)
        e = {"date": d, "direction": cross["direction"],
             "official_gap_pct": round(og, 2) if og is not None else None,
             "prev_close": prev_close,
             "open": open_px,
             "high": max((b.get("high") or 0) for b in reg) if reg else None,
             "low": min((b.get("low") or 1e18) for b in reg) if reg else None,
             "close": reg[-1].get("close") if reg else None,
             "rel_vol": None, "gap_vs_atr": None,
             "catalyst_kind": ge.catalyst_kind(
                 d, (_EARN_HIST_FN(sym) if _EARN_HIST_FN else None) or set(),
                 (_OFFERING_FN(sym) if _OFFERING_FN else None) or {}),
             "exclusion": None, "qualified_by": ["PM"], "outcomes": {}}
        if reg and e["open"]:
            e["outcomes"]["daily"] = ge.daily_outcomes(e)
        store["events"].append(e)
        by_date[d] = e
    if cross:
        e["pm_first_cross_ts"] = cross["ts"]
        if "PM" not in (e.get("qualified_by") or []):
            e["qualified_by"] = sorted(set(e.get("qualified_by") or []) | {"PM"})
    if pm:
        cps = ge.pm_checkpoints(pm)
        e["pm"] = {"checkpoints": cps,
                   "high": max(c[2] for c in cps) if cps else None,
                   "low": min(c[3] for c in cps) if cps else None}
        if e["pm"]["high"]:
            e["pm_gap_max_pct"] = round(
                (e["pm"]["high"] / prev_close - 1.0) * 100.0, 2) \
                if e.get("direction") == "up" else round(
                (e["pm"]["low"] / prev_close - 1.0) * 100.0, 2)
    if reg and not e.get("exclusion"):
        m = ge.minute_path_outcomes(reg, e["direction"], cfg,
                                    prev_close=prev_close)
        if m:
            e["outcomes"]["minute"] = m


# ── live analysis ────────────────────────────────────────────────────────────

def _quote_fresh(q: dict, cfg: dict) -> tuple[bool, str | None]:
    sg = cfg.get("signal", {})
    age = q.get("stale_seconds")
    if age is None and q.get("trade_time_ms"):
        age = max(0.0, time.time() - q["trade_time_ms"] / 1000.0)
    if age is None:
        return False, "no trade timestamp"
    if age > float(sg.get("max_quote_age_s", 120)):
        return False, f"quote {int(age)}s old"
    bid, ask = q.get("bid"), q.get("ask")
    if bid and ask and bid > 0 and ask > bid:
        spread = (ask - bid) / ((ask + bid) / 2.0) * 100.0
        if spread > float(sg.get("max_spread_pct", 5.0)):
            return False, f"spread {spread:.1f}%"
    return True, None


def _sector_context(sym: str, etf_gaps: dict, my_gap: float, cfg: dict) -> dict | None:
    etf = _SECTOR_ETF_FN(sym) if _SECTOR_ETF_FN else None
    if not etf or etf not in etf_gaps:
        return None
    cx = cfg.get("context", {})
    sec = etf_gaps[etf]
    same_sign = (sec > 0) == (my_gap > 0)
    driven = (same_sign and abs(sec) >= float(cx.get("sector_min_pct", 1.5))
              and abs(my_gap) < float(cx.get("isolated_ratio", 3.0)) * abs(sec))
    return {"etf": etf, "etf_gap_pct": round(sec, 2),
            "label": "SECTOR DRIVEN" if driven else "ISOLATED"}


def analyze_symbol(sym: str, q: dict, etf_gaps: dict | None = None,
                   backfill_budget: int = 0, record_journal: bool = True,
                   now: datetime | None = None) -> dict:
    """Full evaluation of one live premarket mover. Returns the board row
    plus detail blocks. Deterministic given quotes/bars/stores."""
    cfg = _cfg()
    now = now or _now_et()
    sym = sym.upper()
    prev_close = q.get("close_prev")
    price = q.get("last")
    if not prev_close or not price or prev_close <= 0:
        return {"symbol": sym, "error": "no price/prev close", "data_ok": False}
    gap = ge.live_gap_pct(price, prev_close)
    direction = "up" if gap >= 0 else "down"
    fresh, fresh_why = _quote_fresh(q, cfg)

    # premarket path (today, live)
    sc = _SCHWAB_FN() if _SCHWAB_FN else None
    pm_bars = None
    if sc:
        try:
            allb = sc.get_intraday(sym, extended=True) or []
            cut = ge.session_cut_ms(now.date(), _ET)
            pm_bars = [b for b in allb
                       if cut - int(5.5 * 3600 * 1000) <= (b.get("ts") or 0) < cut]
        except Exception:
            pm_bars = None
    pmf = ge.pm_features(pm_bars or [], prev_close,
                         as_of_ts_ms=int(now.timestamp() * 1000))
    quote_age = q.get("stale_seconds")

    # event store: refresh daily qualifiers + budgeted minute enrichment
    with _STORE_LOCK:
        store = load_store(sym)
        store = refresh_daily_events(sym, store, now.date())
        if backfill_budget:
            enrich_minutes(sym, store, backfill_budget, now.date())
        else:
            _save_store(sym, store)

    cat = (_CATALYST_FN(sym) if _CATALYST_FN else None) or {"kind": "UNTAGGED"}
    is_earn = cat.get("kind") == "EARNINGS"

    # next scheduled report — a fade into an earnings date days away is a
    # different trade from one with a clear runway
    next_earn, days_to_earn = None, None
    try:
        ne = (_EARN_NEXT_FN(sym) if _EARN_NEXT_FN else None) or {}
        next_earn = (ne.get("next") or None)
        if next_earn:
            days_to_earn = (date.fromisoformat(str(next_earn)[:10]) - now.date()).days
    except Exception:
        next_earn, days_to_earn = None, None

    # gap size normalizer for cohort matching
    gap_vs_atr = None
    pack = _daily(sym, int(cfg.get("event", {}).get("daily_history_days", 900)))
    dbars = pack.get("bars") or []
    if len(dbars) >= 25:
        atr = ge._atr_pct_before(dbars, len(dbars) - 1, 20)
        if atr:
            gap_vs_atr = abs(gap) / atr

    cohort = ge.select_cohort(store["events"], direction, gap, gap_vs_atr,
                              is_earn, cfg)
    stats = ge.outcome_stats(cohort["events"], cfg)
    live_ok = bool(fresh and pmf)
    sig = ge.signal_for(direction, stats, cohort, live_ok, is_earn, cfg)

    prev_row = _PREV_ROWS.get(sym)
    escalate = bool(
        not live_ok
        or (prev_row and prev_row.get("catalyst_kind") != cat.get("kind")
            and is_earn))
    mem = ge.apply_hysteresis(_HYST.get(sym), sig["signal"], escalate, cfg)
    _HYST[sym] = mem

    sctx = _sector_context(sym, etf_gaps or {}, gap, cfg) if gap else None
    pt = f"{float(cfg.get('outcomes', {}).get('primary_target_pct', 2.0)):g}"
    pf = (stats.get("p_fav") or {}).get(pt)
    tbs = stats.get("tbs")
    quality = "UNAVAILABLE"
    if stats.get("n"):
        quality = stats.get("basis", "DAILY ONLY")
    row = {
        "symbol": sym, "price": price, "prev_close": prev_close,
        "pm_gap_pct": round(gap, 2), "direction": direction,
        "from_pm_high_pct": pmf.get("from_pm_high_pct") if pmf else None,
        "from_pm_low_pct": pmf.get("from_pm_low_pct") if pmf else None,
        # kept so the cheap live refresh can extend the known PM range
        # between full scans without refetching the premarket tape
        "pm_high": pmf.get("pm_high") if pmf else None,
        "pm_low": pmf.get("pm_low") if pmf else None,
        "trend_30m_pct": pmf.get("trend_30m_pct") if pmf else None,
        "pm_volume": pmf.get("pm_volume") if pmf else None,
        "catalyst_kind": cat.get("kind", "UNTAGGED"),
        "catalyst_label": cat.get("label"),
        # only offering/dilution tags carry one: a link straight to the
        # filing on EDGAR, so the claim can be checked at the source
        "catalyst_url": cat.get("url"),
        "next_earnings": next_earn, "days_to_earnings": days_to_earn,
        "sector": sctx,
        "p_fav": pf, "p_fav_target_pct": pt,
        "tbs_p": tbs.get("p") if tbs else None,
        "tbs_n": tbs.get("n") if tbs else None,
        "tbs_basis": (stats.get("tbs_basis") if tbs is None else "MINUTE PATH"),
        "med_adverse_pct": stats.get("mae_med_pct", stats.get("med_adv_pct")),
        "med_time_to_target_min": (stats.get("med_time_to_min") or {}).get(pt),
        "n": stats.get("n", 0), "n_minute": stats.get("n_minute", 0),
        "cohort_quality": cohort.get("quality"),
        "cohort_scope": cohort.get("scope"),
        "population": cohort.get("population"),
        "data_basis": quality,
        "quote_age_s": round(quote_age, 1) if quote_age is not None else None,
        "live_ok": live_ok, "live_why": fresh_why,
        "signal": mem["displayed"], "signal_raw": mem["raw"],
        "signal_held": mem.get("held", False),
        "signal_why": sig.get("why"),
        "ev_mean_pct": (stats.get("ev") or {}).get("mean_pct"),
        "data_ok": True,
    }
    row["what_changed"] = ge.diff_summary(prev_row, row)
    _PREV_ROWS[sym] = row
    detail = {"row": row, "stats": stats, "cohort": {
        k: v for k, v in cohort.items() if k != "events"},
        "pm": pmf, "hysteresis": mem, "catalyst": cat,
        "store_meta": {"events": len(store["events"]),
                       "minute_scanned": len(store.get("minute_scanned") or {}),
                       "daily_source": store.get("daily_source"),
                       "daily_through": store.get("daily_through")}}
    if record_journal:
        _journal(row, now)
    return detail


def _journal(row: dict, now: datetime) -> None:
    """Replay record (§38): enough to reconstruct why the signal was shown."""
    if not _DATA_DIR:
        return
    try:
        _, cfg_hash = ge.config()
        rec = {k: row.get(k) for k in (
            "symbol", "price", "pm_gap_pct", "from_pm_high_pct",
            "from_pm_low_pct", "catalyst_kind", "n", "n_minute",
            "cohort_quality", "cohort_scope", "population", "tbs_p",
            "med_adverse_pct", "data_basis", "quote_age_s", "live_ok",
            "signal", "signal_raw", "signal_held", "ev_mean_pct")}
        rec["p_fav"] = row.get("p_fav")
        rec["ts_et"] = now.isoformat(timespec="seconds")
        rec["config_hash"] = cfg_hash
        rec["engine"] = ge.ENGINE_VERSION
        p = _DATA_DIR / "gap" / "decisions" / f"{now.date().isoformat()}.jsonl"
        with _LOCK:
            with open(p, "a") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        # opportunistic 30-day prune (lexicographic works on ISO names)
        cutoff = (now.date() - timedelta(days=30)).isoformat()
        for old in p.parent.glob("*.jsonl"):
            if old.stem < cutoff:
                old.unlink(missing_ok=True)
    except Exception:
        pass


# ── the funnel ───────────────────────────────────────────────────────────────

def _universe(cfg: dict) -> list:
    syms = []
    if _WATCHLIST_FN:
        wl = _WATCHLIST_FN() or {}
        syms += [s for s in (wl.get("starred") or [])]
        syms += [s for s in (wl.get("all") or [])]
    if _UNIVERSE_FN:
        try:
            syms += list(_UNIVERSE_FN() or [])
        except Exception:
            pass
    seen, out = set(), []
    for s in syms:
        u = (s or "").upper()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _stage1(cfg: dict) -> tuple[list, dict]:
    """One quote sweep → (candidate quote rows sorted by |gap|, ETF gaps)."""
    sc = _SCHWAB_FN() if _SCHWAB_FN else None
    if not sc:
        return [], {}
    sn = cfg.get("scan", {})
    syms = _universe(cfg)
    quotes = {}
    batch = syms + [e for e in _CTX_ETFS if e not in syms]
    for i in range(0, len(batch), 200):
        part = batch[i:i + 200]
        got = sc.get_quotes(part) or {}
        quotes.update(got)
        if i + 200 < len(batch):
            time.sleep(0.2)
    etf_gaps = {}
    for e in _CTX_ETFS:
        q = quotes.get(e)
        if q and q.get("close_prev") and q.get("last"):
            g = ge.live_gap_pct(q["last"], q["close_prev"])
            if g is not None:
                etf_gaps[e] = g
    min_gap = float(sn.get("min_gap_pct", 3.0))
    min_price = float(_cfg().get("event", {}).get("min_price", 3.0))
    min_avg_vol = float(sn.get("min_avg_volume", 300000))
    board_rows = {}
    if _BOARD_FN:
        try:
            board_rows = {(r.get("ticker") or "").upper(): r
                          for r in ((_BOARD_FN() or {}).get("rows") or [])}
        except Exception:
            board_rows = {}
    cands = []
    for s in syms:
        q = quotes.get(s)
        if not q:
            continue
        pc, last = q.get("close_prev"), q.get("last")
        if not pc or not last or last < min_price:
            continue
        gap = ge.live_gap_pct(last, pc)
        if gap is None or abs(gap) < min_gap:
            continue
        br = board_rows.get(s)
        if br and (br.get("avg_volume") or 0) and br["avg_volume"] < min_avg_vol:
            continue
        cands.append((abs(gap), s, q))
    cands.sort(key=lambda t: -t[0])
    top = int(sn.get("max_candidates", 20))
    return [(s, q) for _g, s, q in cands[:top]], etf_gaps


def _scan_worker() -> None:
    cfg = _cfg()
    try:
        cands, etf_gaps = _stage1(cfg)
        with _LOCK:
            _STATE.update(total=len(cands), scanned=0)
        rows = []
        budget_each = int(cfg.get("scan", {}).get("minute_backfill_per_scan", 6))
        for i, (s, q) in enumerate(cands):
            try:
                # spread the minute-backfill budget over the first few names
                bf = budget_each if i < 4 else (1 if i < 10 else 0)
                d = analyze_symbol(s, q, etf_gaps, backfill_budget=bf)
                if d.get("row"):
                    rows.append(d["row"])
                elif d.get("error"):
                    rows.append({"symbol": s, "error": d["error"],
                                 "signal": "NO DATA", "data_ok": False})
            except Exception as exc:
                rows.append({"symbol": s, "error": str(exc)[:200],
                             "signal": "NO DATA", "data_ok": False})
            with _LOCK:
                _STATE["scanned"] = i + 1
        def rank(r):
            sig_rank = {"STRONG FADE": 0, "STRONG REBOUND": 0, "FADE": 1,
                        "REBOUND": 1, "MIXED": 2,
                        "HOLD / CONTINUATION RISK": 3,
                        "CONTINUATION LOWER RISK": 3, "NO DATA": 4}
            ev = r.get("ev_mean_pct")
            tbs = r.get("tbs_p") or 0
            return (sig_rank.get(r.get("signal"), 4),
                    -(ev if ev is not None else -99),
                    -tbs, -abs(r.get("pm_gap_pct") or 0))
        rows.sort(key=rank)
        now = _now_et()
        with _LOCK:
            _STATE.update(rows=rows, error=None,
                          as_of=now.isoformat(timespec="seconds"),
                          last_scan=now.isoformat(timespec="seconds"),
                          session="premarket" if now.time() < dtime(9, 30)
                          else "regular")
            _STATE["context"] = {
                "spy_gap_pct": round(etf_gaps["SPY"], 2) if "SPY" in etf_gaps else None,
                "qqq_gap_pct": round(etf_gaps["QQQ"], 2) if "QQQ" in etf_gaps else None,
            }
        _persist_board()
    except Exception as exc:
        with _LOCK:
            _STATE["error"] = str(exc)[:300]
    finally:
        with _LOCK:
            _STATE["scanning"] = False


def trigger_scan(force: bool = False) -> dict:
    if os.environ.get("JERRY_NO_NET"):
        return {"started": False, "reason": "JERRY_NO_NET"}
    if not (_SCHWAB_FN and _SCHWAB_FN()):
        return {"started": False, "reason": "schwab unavailable"}
    with _LOCK:
        if _STATE["scanning"] and not force:
            return {"started": False, "reason": "already scanning"}
        _STATE.update(scanning=True, scanned=0, error=None,
                      universe_size=len(_universe(_cfg())))
    threading.Thread(target=_scan_worker, name="gap-scan", daemon=True).start()
    return {"started": True}


def refresh_quotes() -> dict:
    """Cheap live-price refresh for the rows already on the board.

    A full scan is expensive (history, minute paths, event-store work) so it
    runs on a multi-minute cadence — but the PRICE and the gap it implies
    move every second, and a stale price next to a live signal is exactly
    the kind of thing that gets a trade wrong. This does ONE batched quote
    call and updates only what genuinely changes tick to tick:

      price · pm_gap_pct · the known premarket high/low (monotone: the high
      so far can only rise, the low only fall, so max/min against the new
      print is exact) · distance from that high/low · quote freshness.

    The statistics behind a row (probabilities, target-before-stop, MAE,
    cohort) are properties of HISTORY and cannot move between scans, so
    they are deliberately untouched.

    Two conditions escalate immediately (spec §29 — risk escalations bypass
    hysteresis): a quote that fails the freshness/spread gate, and a gap
    that flips sign, which invalidates the direction the cohort was built
    for. Both drop the row to NO DATA rather than letting it keep wearing a
    clean FADE."""
    sc = _SCHWAB_FN() if _SCHWAB_FN else None
    if os.environ.get("JERRY_NO_NET") or not sc:
        return {"ok": False, "reason": "quotes unavailable", "quotes": {}}
    with _LOCK:
        syms = [r["symbol"] for r in _STATE["rows"]
                if r.get("symbol") and r.get("data_ok") is not False]
    if not syms:
        return {"ok": True, "quotes": {}, "as_of": None}
    quotes: dict = {}
    for i in range(0, len(syms), 200):
        try:
            quotes.update(sc.get_quotes(syms[i:i + 200]) or {})
        except Exception as exc:
            return {"ok": False, "reason": str(exc)[:200], "quotes": {}}
    cfg = _cfg()
    now = _now_et()
    stamp = now.isoformat(timespec="seconds")
    out: dict = {}
    with _LOCK:
        for r in _STATE["rows"]:
            q = quotes.get(r.get("symbol"))
            if not q:
                continue
            price = q.get("last")
            prev_close = q.get("close_prev") or r.get("prev_close")
            if not price or not prev_close or prev_close <= 0:
                continue
            gap = ge.live_gap_pct(price, prev_close)
            fresh, why = _quote_fresh(q, cfg)
            hi = max(r.get("pm_high") or price, price)
            lo = min(r.get("pm_low") or price, price)
            age = q.get("stale_seconds")
            r.update({
                "price": price, "prev_close": prev_close,
                "pm_gap_pct": round(gap, 2) if gap is not None else None,
                "pm_high": hi, "pm_low": lo,
                "from_pm_high_pct": round((price / hi - 1.0) * 100.0, 2) if hi else None,
                "from_pm_low_pct": round((price / lo - 1.0) * 100.0, 2) if lo else None,
                "quote_age_s": round(age, 1) if age is not None else None,
                "live_ok": bool(fresh), "live_why": why,
                "price_as_of": stamp,
            })
            flipped = (gap is not None and r.get("direction")
                       and ((gap >= 0) != (r["direction"] == "up")))
            if (not fresh or flipped) and r.get("signal") != "NO DATA":
                r["signal"] = "NO DATA"
                r["signal_why"] = ("gap flipped direction since the last scan — "
                                   "the historical cohort no longer applies"
                                   if flipped else
                                   f"live quote failed the freshness gate ({why})")
                r["signal_held"] = False
                _HYST[r["symbol"]] = ge.apply_hysteresis(
                    _HYST.get(r["symbol"]), "NO DATA", True, cfg)
            out[r["symbol"]] = {
                "price": price, "pm_gap_pct": r["pm_gap_pct"],
                "from_pm_high_pct": r["from_pm_high_pct"],
                "from_pm_low_pct": r["from_pm_low_pct"],
                "quote_age_s": r["quote_age_s"], "live_ok": r["live_ok"],
                "live_why": r["live_why"], "signal": r["signal"],
                "signal_why": r["signal_why"],
            }
        _STATE["price_as_of"] = stamp
    return {"ok": True, "as_of": stamp, "quotes": out}


def get_board() -> dict:
    with _LOCK:
        return {
            "as_of": _STATE["as_of"],
            "price_as_of": _STATE.get("price_as_of"),
            "status": {k: _STATE[k] for k in
                       ("scanning", "scanned", "total", "last_scan",
                        "universe_size", "error")},
            "count": len(_STATE["rows"]),
            "rows": list(_STATE["rows"]),
            "context": dict(_STATE["context"]),
            "session": _STATE["session"],
            "engine": ge.ENGINE_VERSION,
            "note": ("Probabilities are measured same-ticker history with "
                     "Wilson intervals; signals gate on the conservative "
                     "bound. Universe is today's watchlist — statistics "
                     "carry survivorship bias (delisted gappers absent)."),
        }


def detail(symbol: str) -> dict:
    """On-demand single-symbol evaluation (detail view / clicking a row)."""
    sym = symbol.upper()
    sc = _SCHWAB_FN() if _SCHWAB_FN else None
    if os.environ.get("JERRY_NO_NET") or not sc:
        # offline: serve from the store only
        store = load_store(sym)
        return {"symbol": sym, "offline": True,
                "store_meta": {"events": len(store["events"])},
                "error": "live quote unavailable"}
    q = sc.get_quote(sym)
    if not q:
        return {"symbol": sym, "error": "no quote", "data_ok": False}
    return analyze_symbol(sym, q, {}, backfill_budget=2, record_journal=False)


def events_payload(symbol: str, limit: int = 80) -> dict:
    """The analog population, inspectable (§30): every stored event with
    its qualification, catalyst, outcomes and basis."""
    store = load_store(symbol.upper())
    evs = [e for e in store["events"]][-limit:]
    slim = []
    for e in reversed(evs):
        m = (e.get("outcomes") or {}).get("minute")
        d = (e.get("outcomes") or {}).get("daily")
        slim.append({
            "date": e["date"], "direction": e["direction"],
            "official_gap_pct": e.get("official_gap_pct"),
            "pm_gap_max_pct": e.get("pm_gap_max_pct"),
            "qualified_by": e.get("qualified_by"),
            "catalyst_kind": e.get("catalyst_kind"),
            "exclusion": e.get("exclusion"),
            "basis": "MINUTE PATH" if m else ("DAILY ONLY" if d else "UNAVAILABLE"),
            "fav_pct": (d or {}).get("fav_pct"),
            "adv_pct": (d or {}).get("adv_pct"),
            "mae_pct": (m or {}).get("mae_pct"),
            "gap_filled": (d or {}).get("gap_filled"),
            "continued": (d or {}).get("continued"),
            "delayed_open": (m or {}).get("delayed_open"),
        })
    return {"symbol": store["symbol"], "n": len(store["events"]),
            "events": slim,
            "minute_scanned": len(store.get("minute_scanned") or {}),
            "daily_source": store.get("daily_source"),
            "updated": store.get("updated")}


# ── walk-forward threshold backtest (§11/§37) ────────────────────────────────

def backtest_grid(symbol: str) -> dict:
    """Sweep targets × stops over this symbol's minute-pathed events.
    Walk-forward honesty: events are split chronologically in half; a pair
    is only 'robust' when BOTH halves have positive expectancy. All local
    math on the stored event database — no network."""
    cfg = _cfg()
    store = load_store(symbol.upper())
    oc = cfg.get("outcomes", {})
    sl = cfg.get("slippage", {})
    through = float(sl.get("stop_through_frac", 0.15))
    base_cost = 2.0 * (float(sl.get("base_bps", 5.0)) / 100.0)
    out = {"symbol": store["symbol"], "grid": [], "n_minute_events": 0}
    for direction in ("up", "down"):
        evs = sorted((e for e in store["events"]
                      if not e.get("exclusion")
                      and e.get("direction") == direction
                      and (e.get("outcomes") or {}).get("minute")),
                     key=lambda e: e["date"])
        if len(evs) < 8:
            continue
        out["n_minute_events"] += len(evs)
        half = len(evs) // 2
        for t in oc.get("targets_pct", []):
            for s in oc.get("stops_pct", []):
                key = f"t{float(t):g}_s{float(s):g}"
                rets, rets_h = [], [[], []]
                wins = 0
                for i, e in enumerate(evs):
                    p = e["outcomes"]["minute"]["pairs"].get(key)
                    if not p:
                        continue
                    if p["outcome"] == "target":
                        r = float(t) - base_cost
                        wins += 1
                    elif p["outcome"] == "stop":
                        r = -(float(s) * (1.0 + through)) - base_cost
                    else:
                        r = e["outcomes"]["minute"]["end_ret_pct"] - base_cost
                    rets.append(r)
                    rets_h[0 if i < half else 1].append(r)
                if len(rets) < 8:
                    continue
                mean = sum(rets) / len(rets)
                h1 = sum(rets_h[0]) / len(rets_h[0]) if rets_h[0] else None
                h2 = sum(rets_h[1]) / len(rets_h[1]) if rets_h[1] else None
                out["grid"].append({
                    "direction": direction, "target_pct": float(t),
                    "stop_pct": float(s), "n": len(rets),
                    "win_rate": round(wins / len(rets) * 100.0, 1),
                    "expectancy_pct": round(mean, 2),
                    "h1_pct": round(h1, 2) if h1 is not None else None,
                    "h2_pct": round(h2, 2) if h2 is not None else None,
                    "worst_pct": round(min(rets), 2),
                    "robust": bool(h1 is not None and h2 is not None
                                   and h1 > 0 and h2 > 0 and mean > 0),
                })
    out["grid"].sort(key=lambda g: -(g["expectancy_pct"]))
    out["note"] = ("expectancy in % of entry, MODELED slippage/stop-through, "
                   "measured paths; 'robust' requires positive expectancy in "
                   "BOTH chronological halves (walk-forward), judged by EV "
                   "and worst outcomes — never win rate alone")
    return out


# ── outcome recording (post-close) ───────────────────────────────────────────

def record_today() -> dict:
    """After the close, write today's full event record (real PM path +
    session outcomes) for every symbol that was on today's board. This is
    how PM-only qualifiers become permanently discoverable going forward."""
    if os.environ.get("JERRY_NO_NET"):
        return {"recorded": 0, "reason": "JERRY_NO_NET"}
    sc = _SCHWAB_FN() if _SCHWAB_FN else None
    if not sc:
        return {"recorded": 0, "reason": "schwab unavailable"}
    now = _now_et()
    if now.weekday() >= 5:
        return {"recorded": 0, "reason": "weekend"}
    today = now.date().isoformat()
    with _LOCK:
        syms = [r["symbol"] for r in _STATE["rows"] if r.get("data_ok")]
    done = 0
    cfg = _cfg()
    for s in syms:
        try:
            bars = sc.get_intraday_day(s, today, extended=True) \
                or sc.get_intraday(s, extended=True)
            if not bars:
                continue
            with _STORE_LOCK:
                store = load_store(s)
                store = refresh_daily_events(s, store, now.date())
                _apply_minute_day(s, store, today, bars, cfg)
                store.setdefault("minute_scanned", {})[today] = "ok"
                _save_store(s, store)
            done += 1
        except Exception as exc:
            print(f"[gap] record failed {s}: {exc}")
    return {"recorded": done, "date": today}


# ── persistence & scheduler ──────────────────────────────────────────────────

def _board_path() -> Path | None:
    return (_DATA_DIR / "gap" / "board.json") if _DATA_DIR else None


def _persist_board() -> None:
    p = _board_path()
    if not p:
        return
    try:
        with _LOCK:
            data = {"as_of": _STATE["as_of"], "rows": _STATE["rows"],
                    "context": _STATE["context"], "session": _STATE["session"],
                    "last_scan": _STATE["last_scan"]}
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        tmp.replace(p)
    except Exception:
        pass


def _restore_board() -> None:
    p = _board_path()
    if not p or not p.exists():
        return
    try:
        d = json.loads(p.read_text())
        with _LOCK:
            if not _STATE["rows"]:
                _STATE.update(rows=d.get("rows") or [],
                              as_of=d.get("as_of"),
                              context=d.get("context") or {},
                              session=d.get("session"),
                              last_scan=d.get("last_scan"))
    except Exception:
        pass


def _hm(s: str, default: tuple) -> tuple:
    try:
        h, m = str(s).split(":")
        return int(h), int(m)
    except Exception:
        return default


def _tick() -> None:
    cfg = _cfg()
    sn = cfg.get("scan", {})
    now = _now_et()
    if now.weekday() >= 5:
        return
    t = now.time()
    start = dtime(*_hm(sn.get("start_et", "07:00"), (7, 0)))
    end = dtime(*_hm(sn.get("end_et", "09:40"), (9, 40)))
    rec = dtime(*_hm(sn.get("record_et", "16:10"), (16, 10)))
    if start <= t <= end:
        cadence = float(sn.get("cadence_minutes", 5)) * 60.0
        if time.time() - _SCHED["last_tick_scan"] >= cadence:
            r = trigger_scan()
            if r.get("started"):
                _SCHED["last_tick_scan"] = time.time()
    elif t >= rec and _SCHED["recorded_for"] != now.date().isoformat():
        _SCHED["recorded_for"] = now.date().isoformat()
        threading.Thread(target=record_today, name="gap-record",
                         daemon=True).start()


def start_scheduler() -> None:
    if _SCHED["started"]:
        return
    _SCHED["started"] = True

    def loop():
        while True:
            try:
                _tick()
            except Exception as exc:  # pragma: no cover
                print(f"[gap] scheduler tick failed: {exc}")
            time.sleep(60)

    threading.Thread(target=loop, name="gap-sched", daemon=True).start()
