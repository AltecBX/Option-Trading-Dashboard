"""gap_engine.py — pure math for the Premarket Gap Fade & Rebound scanner.

Answers one question with measured history: when this stock previously made
a move like today's premarket move, what happened next — how often did a
gap up fade 1/2/3/5%, how often did a gap down rebound, how often did the
target print before the stop, and how badly did it usually move against
you first?

Design rules (same discipline as premium_edge/vol_forecast):
  - Pure + stdlib: no I/O, no network, no wall clock. Every input arrives
    as an argument; gap_scan.py owns fetching, stores and schedulers.
  - No lookahead: a historical event only qualifies for a time-matched
    query at time t if it had crossed the gap threshold BY t; outcome
    windows start strictly at the entry timestamp.
  - Conservative ambiguity: target and stop inside the same minute bar
    resolve AGAINST the trade (stop first, labeled INTRABAR MODELED);
    daily-only events never claim target/stop ordering (UNKNOWN / DAILY
    ONLY).
  - No n, no probability: every rate ships with its sample size and a
    Wilson interval; signal gates use the LOWER bound.

Direction vocabulary: an "up" event is a gap UP → the studied trade is a
FADE (short from the entry reference, favorable = downside). A "down"
event is a gap DOWN → the studied trade is a REBOUND (long, favorable =
upside). All favorable/adverse math is expressed through that lens so the
two directions stay exact mirrors of each other.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from metrics import percentile, wilson_interval

ENGINE_VERSION = "gap-1.0.0"
SCHEMA_VERSION = 1

# ── config plumbing (premium_edge discipline: repo defaults, data-dir
#    overlay, deep merge, full-file hash, 60s cache) ─────────────────────────

_DATA_DIR: Path | None = None
_CFG_LOCK = threading.Lock()
_CFG_CACHE = {"cfg": None, "hash": None, "ts": 0.0}


def configure(data_dir) -> None:
    global _DATA_DIR
    _DATA_DIR = Path(data_dir) if data_dir else None
    _CFG_CACHE["ts"] = 0.0


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def config(refresh: bool = False) -> tuple[dict, str]:
    """(gap_fade section, sha256[:16] of the whole thresholds file)."""
    with _CFG_LOCK:
        if not refresh and _CFG_CACHE["cfg"] is not None \
                and time.time() - _CFG_CACHE["ts"] < 60:
            return _CFG_CACHE["cfg"], _CFG_CACHE["hash"]
        full = {}
        try:
            repo = Path(__file__).resolve().parent / "thresholds.json"
            full = json.loads(repo.read_text())
        except Exception:
            full = {}
        try:
            if _DATA_DIR:
                over = _DATA_DIR / "thresholds.json"
                if over.exists():
                    full = _deep_merge(full, json.loads(over.read_text()))
        except Exception:
            pass
        cfg = full.get("gap_fade") or {}
        h = hashlib.sha256(json.dumps(
            full, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
        _CFG_CACHE.update({"cfg": cfg, "hash": h, "ts": time.time()})
        return cfg, h


# ── gap math ─────────────────────────────────────────────────────────────────

def official_gap_pct(open_px, prev_close) -> float | None:
    """Official regular-session gap: open / prior regular close − 1, in %."""
    if not open_px or not prev_close or open_px <= 0 or prev_close <= 0:
        return None
    return (open_px / prev_close - 1.0) * 100.0


def live_gap_pct(price, prev_close) -> float | None:
    """Live (premarket) gap of the current reference price vs prior close."""
    return official_gap_pct(price, prev_close)


# ── corporate-action guard ───────────────────────────────────────────────────

_SPLIT_RATIOS = (10.0, 8.0, 5.0, 4.0, 3.0, 2.5, 2.0, 1.5,
                 1 / 1.5, 0.5, 0.4, 1 / 3, 0.25, 0.2, 0.125, 0.1)


def exclusion_for(prev_bar: dict, bar: dict, cfg: dict,
                  split_dates=None, div_by_date=None) -> str | None:
    """Why a candidate day must NOT enter the event store, or None.

    Order of evidence: declared corporate actions (when the caller has
    them) beat heuristics. The heuristic split detector needs BOTH a
    near-round price ratio and the reciprocal volume signature — a real
    50% gap has no reason to double volume by exactly the price ratio,
    but a raw (unadjusted) bar across a 2:1 split shows both.
    """
    ev = cfg.get("event", {})
    d = str(bar.get("date", ""))[:10]
    if split_dates and d in split_dates:
        return "EXCLUDE_SPLIT"
    prev_close = prev_bar.get("close")
    open_px = bar.get("open")
    if not prev_close or not open_px or prev_close <= 0 or open_px <= 0:
        return "EXCLUDE_UNRELIABLE"
    hi, lo = bar.get("high"), bar.get("low")
    if hi is None or lo is None or hi < lo or not (lo <= open_px <= hi) \
            or (bar.get("close") or 0) <= 0:
        return "EXCLUDE_UNRELIABLE"
    if div_by_date:
        div = float(div_by_date.get(d) or 0.0)
        if div > 0 and div / prev_close * 100.0 >= float(ev.get("special_div_pct", 2.0)):
            return "EXCLUDE_DIVIDEND"
    gap = abs(official_gap_pct(open_px, prev_close) or 0.0)
    if gap >= float(ev.get("max_credible_gap_pct", 150.0)):
        return "EXCLUDE_UNRELIABLE"
    ratio = open_px / prev_close
    tol = float(ev.get("split_ratio_tol", 0.04))
    if gap >= 30.0:
        pv, v = prev_bar.get("volume") or 0, bar.get("volume") or 0
        for r in _SPLIT_RATIOS:
            if abs(ratio - 1.0 / r) <= tol / max(1.0, min(r, 1.0 / r)):
                # reciprocal volume signature: shares scale by ~r on a split
                if pv > 0 and v > 0 and abs((v / pv) / r - 1.0) <= 0.5:
                    return "EXCLUDE_SPLIT"
    return None


# ── event extraction (daily bars) ────────────────────────────────────────────

def _bar_date(b: dict) -> str:
    return str(b.get("date", ""))[:10]


def extract_daily_events(daily_bars: list, cfg: dict,
                         split_dates=None, div_by_date=None,
                         earnings_dates=None) -> list[dict]:
    """Find official-gap qualifiers in daily history. One event per day:

    {date, direction, official_gap_pct, prev_close, open, high, low, close,
     rel_vol, gap_vs_atr, catalyst_kind, exclusion, qualified_by,
     outcomes: {daily: {...}}}

    Premarket-only qualifiers (§6: reached the PM threshold but opened
    small) cannot be discovered from daily bars — gap_scan adds those from
    minute data. Outcomes here are the daily-bar approximation measured
    from the official open; ordering claims are made only by the minute
    path engine.
    """
    ev = cfg.get("event", {})
    gate = float(ev.get("official_gap_min_pct", 4.0))
    min_price = float(ev.get("min_price", 3.0))
    earnings = set(earnings_dates or [])
    out = []
    vols = [b.get("volume") or 0 for b in daily_bars]
    for i in range(1, len(daily_bars)):
        prev, b = daily_bars[i - 1], daily_bars[i]
        prev_close, open_px = prev.get("close"), b.get("open")
        gap = official_gap_pct(open_px, prev_close)
        if gap is None or abs(gap) < gate:
            continue
        if (prev_close or 0) < min_price:
            continue
        exclusion = exclusion_for(prev, b, cfg, split_dates, div_by_date)
        d = _bar_date(b)
        avg20 = None
        if i >= 21:
            w = [v for v in vols[i - 20:i] if v]
            avg20 = (sum(w) / len(w)) if w else None
        atr = _atr_pct_before(daily_bars, i, 20)
        rec = {
            "date": d,
            "direction": "up" if gap > 0 else "down",
            "official_gap_pct": round(gap, 2),
            "prev_close": prev_close,
            "open": open_px, "high": b.get("high"), "low": b.get("low"),
            "close": b.get("close"),
            "rel_vol": round((b.get("volume") or 0) / avg20, 2) if avg20 else None,
            "gap_vs_atr": round(abs(gap) / atr, 2) if atr else None,
            "catalyst_kind": "EARNINGS" if _near_earnings(d, earnings) else "UNTAGGED",
            "exclusion": exclusion,
            "qualified_by": ["OFFICIAL"],
            "outcomes": {},
        }
        if not exclusion:
            rec["outcomes"]["daily"] = daily_outcomes(rec)
        out.append(rec)
    return out


def _near_earnings(d: str, earnings: set, days: int = 1) -> bool:
    """A gap on the report date or the morning after an AMC report is an
    earnings gap. Without per-report BMO/AMC timing for history we take
    the conservative union: event date == report date or the next day."""
    if not earnings:
        return False
    if d in earnings:
        return True
    try:
        dd = date.fromisoformat(d)
    except ValueError:
        return False
    for k in range(1, days + 1):
        if (dd - timedelta(days=k)).isoformat() in earnings:
            return True
    return False


def _atr_pct_before(bars: list, i: int, window: int = 20) -> float | None:
    """Trailing ATR% (simple mean TR / close) using bars strictly before i —
    the normalizer for "how big is this gap for THIS stock". Never peeks."""
    lo = max(1, i - window)
    trs = []
    for j in range(lo, i):
        b, p = bars[j], bars[j - 1]
        h, l, pc = b.get("high"), b.get("low"), p.get("close")
        if h is None or l is None or not pc:
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)) / pc * 100.0)
    if len(trs) < max(5, window // 2):
        return None
    return sum(trs) / len(trs)


def daily_outcomes(event: dict) -> dict:
    """Daily-bar outcome approximation, measured from the official open.

    For an up gap the studied trade is a short: favorable = open→low,
    adverse = open→high. For a down gap it is the exact mirror. Daily
    bars cannot order a same-day target and stop — path claims stay
    UNKNOWN / DAILY ONLY."""
    o, h, l, c = event["open"], event["high"], event["low"], event["close"]
    pc = event["prev_close"]
    if not o or h is None or l is None or c is None:
        return {}
    if event["direction"] == "up":
        fav = (o - l) / o * 100.0
        adv = (h - o) / o * 100.0
        gap_filled = l <= pc
        continued = c > o
        end_ret = (o - c) / o * 100.0          # short P/L in % if held to close
    else:
        fav = (h - o) / o * 100.0
        adv = (o - l) / o * 100.0
        gap_filled = h >= pc
        continued = c < o
        end_ret = (c - o) / o * 100.0          # long P/L in %
    return {"basis": "DAILY ONLY",
            "fav_pct": round(fav, 2), "adv_pct": round(adv, 2),
            "gap_filled": bool(gap_filled), "continued": bool(continued),
            "end_ret_pct": round(end_ret, 2)}


# ── minute path engine ───────────────────────────────────────────────────────

def session_cut_ms(day: date, et_tz) -> int:
    """09:30 ET of a SPECIFIC day as epoch ms (replay-safe, unlike the live
    splitter's default)."""
    t = datetime.combine(day, dtime(9, 30), tzinfo=et_tz) if et_tz \
        else datetime.combine(day, dtime(9, 30))
    return int(t.timestamp() * 1000)


def minute_path_outcomes(reg_bars: list, direction: str, cfg: dict,
                         prev_close: float | None = None) -> dict | None:
    """Measure the real intraday path from the official open.

    reg_bars: this day's REGULAR-SESSION minute bars (ts ascending). Entry
    is the first bar's open — the first valid official print, so halted /
    delayed opens anchor to the first real trade and get flagged.

    Returns the full targets×stops matrix plus MFE/MAE and timing:
      {basis, entry, delayed_open, mfe_pct, mae_pct, time_to: {pct: min},
       pairs: {"t2.0_s3.0": {outcome, minutes, intrabar, mae_before_pct}},
       gap_filled, continued, end_ret_pct}

    Ordering rule (spec §12): if target and stop print in the SAME minute
    bar, resolve against the trade — stop first — and mark the pair
    intrabar-modeled. Never resolve ambiguity in the strategy's favor."""
    if not reg_bars:
        return None
    oc = cfg.get("outcomes", {})
    targets = [float(x) for x in oc.get("targets_pct", [1, 1.5, 2, 2.5, 3, 4, 5])]
    stops = [float(x) for x in oc.get("stops_pct", [1, 2, 3, 4, 5])]
    levels = [float(x) for x in oc.get("prob_levels_pct", [1, 2, 3, 5])]
    horizon = int(oc.get("horizon_minutes", 390))
    entry = reg_bars[0].get("open") or reg_bars[0].get("close")
    if not entry or entry <= 0:
        return None
    t0 = reg_bars[0].get("ts") or 0
    short = direction == "up"

    def fav_of(b):
        return (entry - b["low"]) / entry * 100.0 if short \
            else (b["high"] - entry) / entry * 100.0

    def adv_of(b):
        return (b["high"] - entry) / entry * 100.0 if short \
            else (entry - b["low"]) / entry * 100.0

    bars = [b for b in reg_bars
            if b.get("high") is not None and b.get("low") is not None
            and ((b.get("ts") or 0) - t0) <= horizon * 60000]
    if not bars:
        return None
    mfe = mae = 0.0
    time_to: dict = {}
    # pair state: outcome None until decided; track MAE before decision
    pair_state = {(t, s): {"outcome": None, "minutes": None, "intrabar": False,
                           "mae_before_pct": 0.0}
                  for t in targets for s in stops}
    gap_filled = False
    for b in bars:
        f, a = fav_of(b), adv_of(b)
        minutes = int(round(((b.get("ts") or 0) - t0) / 60000.0))
        mfe, mae = max(mfe, f), max(mae, a)
        for lv in levels:
            if lv not in time_to and f >= lv:
                time_to[lv] = minutes
        if prev_close:
            if short and b["low"] <= prev_close:
                gap_filled = True
            if not short and b["high"] >= prev_close:
                gap_filled = True
        for (t, s), st in pair_state.items():
            if st["outcome"] is not None:
                continue
            hit_t, hit_s = f >= t, a >= s
            if hit_t and hit_s:
                st.update(outcome="stop", minutes=minutes, intrabar=True)
            elif hit_s:
                st.update(outcome="stop", minutes=minutes)
            elif hit_t:
                st.update(outcome="target", minutes=minutes)
            else:
                st["mae_before_pct"] = max(st["mae_before_pct"], a)
                continue
            st["mae_before_pct"] = max(st["mae_before_pct"], min(a, s))
    last = bars[-1]
    end_px = last.get("close") or entry
    end_ret = (entry - end_px) / entry * 100.0 if short \
        else (end_px - entry) / entry * 100.0
    first_min = int(round((t0 - session_cut_ms_from_ts(t0)) / 60000.0)) \
        if session_cut_ms_from_ts(t0) else 0
    delayed = first_min > int(oc.get("delayed_open_after_min", 5))
    pairs = {f"t{t:g}_s{s:g}": {"outcome": st["outcome"] or "neither",
                                "minutes": st["minutes"],
                                "intrabar": st["intrabar"],
                                "mae_before_pct": round(st["mae_before_pct"], 2)}
             for (t, s), st in pair_state.items()}
    return {"basis": "MINUTE PATH", "entry": entry,
            "delayed_open": bool(delayed), "first_bar_minute": first_min,
            "mfe_pct": round(mfe, 2), "mae_pct": round(mae, 2),
            "time_to": {f"{k:g}": v for k, v in time_to.items()},
            "pairs": pairs, "gap_filled": bool(gap_filled),
            "continued": bool(end_ret < 0),
            "end_ret_pct": round(end_ret, 2), "n_bars": len(bars)}


def session_cut_ms_from_ts(ts_ms: int) -> int | None:
    """09:30 ET of the day a timestamp falls on. Used to flag delayed opens
    without threading a tz through every caller; returns None when zoneinfo
    is unavailable (flag simply stays False)."""
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        d = datetime.fromtimestamp(ts_ms / 1000.0, et)
        return int(datetime.combine(d.date(), dtime(9, 30), tzinfo=et)
                   .timestamp() * 1000)
    except Exception:
        return None


# ── premarket features ───────────────────────────────────────────────────────

def pm_features(pm_bars: list, prev_close: float | None,
                as_of_ts_ms: int | None = None) -> dict | None:
    """Point-in-time premarket picture from extended-hours minute bars.

    Only bars with ts <= as_of_ts_ms are seen (strict no-lookahead: at
    8:30 the final PM high does not exist yet). Highs/lows are 'known so
    far', never called confirmed."""
    if not pm_bars:
        return None
    bars = [b for b in pm_bars
            if as_of_ts_ms is None or (b.get("ts") or 0) <= as_of_ts_ms]
    bars = [b for b in bars if b.get("close") is not None]
    if not bars:
        return None
    last = bars[-1]
    price = last.get("close")
    hi = max(b.get("high") if b.get("high") is not None else b["close"] for b in bars)
    lo = min(b.get("low") if b.get("low") is not None else b["close"] for b in bars)
    hi_ts = next((b["ts"] for b in bars
                  if (b.get("high") if b.get("high") is not None else b["close"]) >= hi), None)
    lo_ts = next((b["ts"] for b in bars
                  if (b.get("low") if b.get("low") is not None else b["close"]) <= lo), None)
    vol = sum(b.get("volume") or 0 for b in bars)
    # trailing 30-minute drift of the premarket tape
    t_end = last.get("ts") or 0
    win = [b for b in bars if (t_end - (b.get("ts") or 0)) <= 30 * 60000]
    trend = None
    if len(win) >= 2 and win[0].get("close"):
        trend = (win[-1]["close"] / win[0]["close"] - 1.0) * 100.0
    out = {
        "price": price,
        "pm_high": hi, "pm_low": lo,
        "from_pm_high_pct": round((price / hi - 1.0) * 100.0, 2) if hi else None,
        "from_pm_low_pct": round((price / lo - 1.0) * 100.0, 2) if lo else None,
        "min_since_high": int(round((t_end - hi_ts) / 60000.0)) if hi_ts else None,
        "min_since_low": int(round((t_end - lo_ts) / 60000.0)) if lo_ts else None,
        "trend_30m_pct": round(trend, 2) if trend is not None else None,
        "pm_volume": vol,
        "last_ts": t_end, "n_bars": len(bars),
    }
    if prev_close:
        out["pm_gap_pct"] = round((price / prev_close - 1.0) * 100.0, 2)
        out["pm_high_gap_pct"] = round((hi / prev_close - 1.0) * 100.0, 2)
        out["pm_low_gap_pct"] = round((lo / prev_close - 1.0) * 100.0, 2)
    return out


def pm_first_cross(pm_bars: list, prev_close: float, threshold_pct: float) -> dict | None:
    """First premarket moment |gap| reached threshold_pct, and which side.

    This is the §6 time-matching anchor: a day that was +9% at 8:30 but
    opened +1% qualifies as an event — but only for decision times AT OR
    AFTER the moment it actually crossed. Future premarket movement can
    never retro-qualify an earlier timestamp."""
    if not pm_bars or not prev_close or prev_close <= 0:
        return None
    up_lvl = prev_close * (1.0 + threshold_pct / 100.0)
    dn_lvl = prev_close * (1.0 - threshold_pct / 100.0)
    for b in pm_bars:
        hi = b.get("high") if b.get("high") is not None else b.get("close")
        lo = b.get("low") if b.get("low") is not None else b.get("close")
        if hi is None or lo is None:
            continue
        if hi >= up_lvl:
            return {"ts": b.get("ts"), "direction": "up"}
        if lo <= dn_lvl:
            return {"ts": b.get("ts"), "direction": "down"}
    return None


def pm_checkpoints(pm_bars: list, every_min: int = 5) -> list:
    """Compact archival form of a premarket session: [ts, price, cum_high,
    cum_low, cum_volume] every N minutes. Small enough to keep forever in
    the event store (raw minute bars leave source retention in ~6 months);
    rich enough to rebuild point-in-time features for time matching."""
    out, hi, lo, vol, last_kept = [], None, None, 0, None
    for b in pm_bars:
        c = b.get("close")
        if c is None:
            continue
        h = b.get("high") if b.get("high") is not None else c
        l = b.get("low") if b.get("low") is not None else c
        hi = h if hi is None else max(hi, h)
        lo = l if lo is None else min(lo, l)
        vol += b.get("volume") or 0
        ts = b.get("ts") or 0
        if last_kept is None or ts - last_kept >= every_min * 60000:
            out.append([ts, round(c, 4), round(hi, 4), round(lo, 4), vol])
            last_kept = ts
    if pm_bars and out and (pm_bars[-1].get("ts") or 0) != out[-1][0]:
        b = pm_bars[-1]
        c = b.get("close")
        if c is not None:
            out.append([b.get("ts") or 0, round(c, 4), round(hi, 4),
                        round(lo, 4), vol])
    return out


# ── aggregation ──────────────────────────────────────────────────────────────

def _rate(k: int, n: int) -> dict | None:
    w = wilson_interval(k, n)
    if not w:
        return None
    return {"p": round(w["p"] * 100.0, 1), "lo": round(w["lo"] * 100.0, 1),
            "hi": round(w["hi"] * 100.0, 1), "n": n, "k": k}


def outcome_stats(events: list, cfg: dict) -> dict:
    """Aggregate one direction's events into the scanner's evidence block.

    Favorable probabilities (P ≥ 1/2/3/5%) come from ALL usable events —
    the from-open favorable excursion is the same measured quantity on
    daily and minute bars. Target-before-stop, MAE-before-target and
    timing come ONLY from minute-path events; with zero minute events the
    ordering claim is refused (UNKNOWN / DAILY ONLY), never approximated.
    """
    oc = cfg.get("outcomes", {})
    levels = [float(x) for x in oc.get("prob_levels_pct", [1, 2, 3, 5])]
    pt = float(oc.get("primary_target_pct", 2.0))
    ps = float(oc.get("primary_stop_pct", 3.0))
    usable = [e for e in events if not e.get("exclusion")
              and e.get("outcomes", {}).get("daily")]
    n = len(usable)
    out = {"n": n, "engine": ENGINE_VERSION}
    if not n:
        out["basis"] = "UNAVAILABLE"
        return out
    favs = [e["outcomes"]["daily"]["fav_pct"] for e in usable]
    advs = [e["outcomes"]["daily"]["adv_pct"] for e in usable]
    out["p_fav"] = {f"{lv:g}": _rate(sum(1 for f in favs if f >= lv), n)
                    for lv in levels}
    out["med_fav_pct"] = round(percentile(favs, 0.5), 2)
    out["med_adv_pct"] = round(percentile(advs, 0.5), 2)
    out["adv_p90_pct"] = round(percentile(advs, 0.90), 2)
    out["adv_p95_pct"] = round(percentile(advs, 0.95), 2)
    worst_i = max(range(n), key=lambda i: advs[i])
    out["worst_adv_pct"] = round(advs[worst_i], 2)
    out["worst_adv_date"] = usable[worst_i]["date"]
    out["gap_fill"] = _rate(sum(1 for e in usable
                                if e["outcomes"]["daily"]["gap_filled"]), n)
    out["continuation"] = _rate(sum(1 for e in usable
                                    if e["outcomes"]["daily"]["continued"]), n)
    end_rets = [e["outcomes"]["daily"]["end_ret_pct"] for e in usable]
    out["hold_to_close_mean_pct"] = round(sum(end_rets) / n, 2)

    minute = [e for e in usable if e.get("outcomes", {}).get("minute")]
    mn = len(minute)
    out["n_minute"] = mn
    key = f"t{pt:g}_s{ps:g}"
    if mn:
        pairs = [e["outcomes"]["minute"]["pairs"].get(key) for e in minute]
        pairs = [p for p in pairs if p]
        dec = [p for p in pairs if p["outcome"] in ("target", "stop")]
        tk = sum(1 for p in pairs if p["outcome"] == "target")
        out["tbs"] = _rate(tk, len(pairs)) if pairs else None
        if out["tbs"] is not None:
            out["tbs"]["target_pct"], out["tbs"]["stop_pct"] = pt, ps
            out["tbs"]["intrabar_modeled_share"] = round(
                sum(1 for p in dec if p.get("intrabar")) / len(dec), 2) if dec else 0.0
        maes_bt = [p["mae_before_pct"] for p in pairs
                   if p["outcome"] == "target"]
        out["mae_before_target_med_pct"] = (
            round(percentile(maes_bt, 0.5), 2) if maes_bt else None)
        mfes = [e["outcomes"]["minute"]["mfe_pct"] for e in minute]
        maes = [e["outcomes"]["minute"]["mae_pct"] for e in minute]
        out["mfe_med_pct"] = round(percentile(mfes, 0.5), 2)
        out["mae_med_pct"] = round(percentile(maes, 0.5), 2)
        out["mae_p90_pct"] = round(percentile(maes, 0.90), 2)
        out["mae_p95_pct"] = round(percentile(maes, 0.95), 2)
        tt = {}
        for lv in levels:
            ts_ = [e["outcomes"]["minute"]["time_to"].get(f"{lv:g}")
                   for e in minute]
            ts_ = [t for t in ts_ if t is not None]
            if ts_:
                tt[f"{lv:g}"] = int(round(percentile(ts_, 0.5)))
        out["med_time_to_min"] = tt
        share = sum(1 for e in minute
                    if any(p.get("intrabar") for p in
                           e["outcomes"]["minute"]["pairs"].values()))
        out["basis"] = "FULL PATH MEASURED" if mn == n else \
            f"MINUTE PATH ({mn} of {n})"
        out["any_intrabar_events"] = share
        out["ev"] = _empirical_ev(minute, pt, ps, cfg)
    else:
        out["tbs"] = None
        out["basis"] = "DAILY ONLY"
        out["tbs_basis"] = "UNKNOWN / DAILY ONLY"
    return out


def _slip_pct(price: float, spread_pct: float | None, premarket: bool,
              cfg: dict) -> float:
    """Per-side MODELED slippage in %, spread-aware, worse premarket."""
    sl = cfg.get("slippage", {})
    base = float(sl.get("base_bps", 5.0)) / 100.0
    sp = max(base, float(sl.get("spread_frac", 0.5)) * (spread_pct or 0.0))
    return sp * (float(sl.get("pm_mult", 2.0)) if premarket else 1.0)


def _empirical_ev(minute_events: list, pt: float, ps: float, cfg: dict) -> dict:
    """Mean/median net simulated return over ACTUAL historical paths for the
    primary target/stop pair: +target on a target-first day, −(stop +
    stop-through) on a stop-first day, hold-to-close return otherwise; a
    modeled entry+exit cost comes off every trade. This is a full per-event
    simulation, not p·win − (1−p)·loss."""
    sl = cfg.get("slippage", {})
    through = float(sl.get("stop_through_frac", 0.15))
    key = f"t{pt:g}_s{ps:g}"
    rets = []
    for e in minute_events:
        m = e["outcomes"]["minute"]
        p = m["pairs"].get(key)
        if not p:
            continue
        cost = 2.0 * _slip_pct(m.get("entry") or 0, None, False, cfg)
        if p["outcome"] == "target":
            rets.append(pt - cost)
        elif p["outcome"] == "stop":
            rets.append(-(ps * (1.0 + through)) - cost)
        else:
            rets.append(m["end_ret_pct"] - cost)
    if not rets:
        return {"basis": "UNAVAILABLE"}
    return {"mean_pct": round(sum(rets) / len(rets), 2),
            "median_pct": round(percentile(rets, 0.5), 2),
            "worst_pct": round(min(rets), 2),
            "n": len(rets),
            "basis": "MODELED slippage on measured paths"}


# ── cohort selection (same-ticker first, earnings separated) ─────────────────

def select_cohort(events: list, direction: str, today_gap_pct: float,
                  today_gap_vs_atr: float | None, is_earnings: bool,
                  cfg: dict) -> dict:
    """Choose the comparable historical events (same ticker).

    Hard rules: same direction; excluded days never; earnings events and
    non-earnings events NEVER mix (an earnings gap is a different animal
    and its statistics must not contaminate the ordinary-gap base rate).
    Matching: prefer events whose size is within gap_match_band of
    today's (by gap-vs-ATR when both sides have it, else raw gap %);
    widen to all same-direction same-catalyst-class events when the
    matched set is too small, and say so."""
    h = cfg.get("history", {})
    band = h.get("gap_match_band", [0.5, 2.5])
    min_ev = int(h.get("min_events", 8))
    good = int(h.get("good_events", 20))
    base = [e for e in events
            if not e.get("exclusion") and e.get("direction") == direction
            and (e.get("catalyst_kind") == "EARNINGS") == bool(is_earnings)]

    def size_of(e):
        if today_gap_vs_atr and e.get("gap_vs_atr"):
            return e["gap_vs_atr"] / today_gap_vs_atr
        g = abs(e.get("official_gap_pct") or 0.0)
        if not g and e.get("pm_gap_max_pct"):
            g = abs(e["pm_gap_max_pct"])
        return g / abs(today_gap_pct) if today_gap_pct else None

    matched, ratios = [], []
    for e in base:
        r = size_of(e)
        if r is not None and band[0] <= r <= band[1]:
            matched.append(e)
            ratios.append(r)
    if len(matched) >= min_ev:
        chosen, scope = matched, "matched"
    else:
        chosen, scope = base, "all_same_direction"
        ratios = [size_of(e) for e in base]
        ratios = [r for r in ratios if r is not None]
    med_dist = percentile([abs(math.log(r)) for r in ratios if r and r > 0], 0.5) \
        if ratios else None
    if scope == "matched" and len(chosen) >= good and (med_dist or 9) <= 0.45:
        quality = "HIGH"
    elif len(chosen) >= min_ev and (med_dist or 9) <= 0.8:
        quality = "MODERATE"
    else:
        quality = "LOW"
    return {"events": chosen, "scope": scope, "quality": quality,
            "n": len(chosen),
            "population": "EARNINGS" if is_earnings else "NON-EARNINGS",
            "med_size_log_dist": round(med_dist, 2) if med_dist is not None else None,
            "dates": [e["date"] for e in chosen]}


# ── signals ──────────────────────────────────────────────────────────────────

def signal_for(direction: str, stats: dict, cohort: dict, live_ok: bool,
               is_earnings: bool, cfg: dict) -> dict:
    """Map evidence to the display signal. Conservative by construction:
    every gate reads the Wilson LOWER bound; STRONG additionally needs
    ordering evidence (target-before-stop from real minute paths), tail
    control, sample depth and no earnings catalyst. When history says
    these gaps keep going, the signal is the risk warning, not a trade."""
    sg = cfg.get("signal", {})
    h = cfg.get("history", {})
    oc = cfg.get("outcomes", {})
    up = direction == "up"
    lean = "FADE" if up else "REBOUND"
    strong = f"STRONG {lean}"
    risk = "HOLD / CONTINUATION RISK" if up else "CONTINUATION LOWER RISK"
    if not live_ok:
        return {"signal": "NO DATA", "why": "live quote failed freshness/quality gates"}
    n = stats.get("n") or 0
    if n < int(h.get("min_events", 8)):
        return {"signal": "NO DATA",
                "why": f"only {n} comparable historical events (need {h.get('min_events', 8)})"}
    pt = f"{float(oc.get('primary_target_pct', 2.0)):g}"
    pf = (stats.get("p_fav") or {}).get(pt)
    if not pf:
        return {"signal": "NO DATA", "why": "no favorable-move rate computable"}
    cont = stats.get("continuation") or {}
    if (cont.get("lo") or 0) >= float(sg.get("continuation_rate", 0.58)) * 100.0:
        return {"signal": risk,
                "why": f"history continued {cont.get('p')}% of the time (n={cont.get('n')})"}
    tbs = stats.get("tbs")
    strong_ok = (
        pf["lo"] >= float(sg.get("strong_p_fav_lo", 0.62)) * 100.0
        and n >= int(h.get("strong_min_events", 20))
        and tbs is not None
        and tbs["lo"] >= float(sg.get("strong_p_tbs_lo", 0.58)) * 100.0
        and (stats.get("mae_p90_pct") if stats.get("mae_p90_pct") is not None
             else stats.get("adv_p90_pct", 99.0)) <= float(sg.get("strong_max_adv_p90_pct", 6.0))
        and cohort.get("quality") in ("HIGH", "MODERATE")
        and not is_earnings
    )
    if strong_ok:
        return {"signal": strong,
                "why": (f"{pf['p']}% reached {pt}% (n={pf['n']}, ≥{pf['lo']}% conservative), "
                        f"target-before-stop {tbs['p']}% (n={tbs['n']}), "
                        f"90th pct adverse {stats.get('mae_p90_pct', stats.get('adv_p90_pct'))}%")}
    if pf["lo"] >= float(sg.get("lean_p_fav_lo", 0.52)) * 100.0:
        why = f"{pf['p']}% of {pf['n']} comparable gaps reached {pt}%"
        if tbs is None:
            why += " · ordering unknown (daily bars only)"
        return {"signal": lean, "why": why}
    if (cont.get("p") or 0) >= float(sg.get("continuation_rate", 0.58)) * 100.0:
        return {"signal": risk,
                "why": f"continuation in {cont.get('p')}% of {cont.get('n')} events"}
    return {"signal": "MIXED",
            "why": f"favorable-move evidence inconclusive ({pf['p']}%, n={pf['n']})"}


# ── hysteresis (pure; caller owns the memory dict) ───────────────────────────

def apply_hysteresis(mem: dict | None, raw_signal: str, escalate: bool,
                     cfg: dict) -> dict:
    """Displayed-state stability, timing-engine style but signal-shaped:
    a changed raw signal must repeat persist_evals times before the
    display flips. Escalations (NO DATA, continuation-risk, or any
    caller-flagged risk event like a fresh earnings tag or stale data)
    bypass and reset. Returns the new memory dict, which includes what
    the UI needs: displayed, raw, held, pending."""
    hz = cfg.get("hysteresis", {})
    need = int(hz.get("persist_evals", 2))
    esc_set = set(hz.get("escalate_signals", []))
    mem = dict(mem or {})
    displayed = mem.get("displayed")
    if escalate or raw_signal in esc_set or displayed is None:
        return {"displayed": raw_signal, "raw": raw_signal, "held": False,
                "pending": None, "count": 0}
    if raw_signal == displayed:
        return {"displayed": displayed, "raw": raw_signal, "held": False,
                "pending": None, "count": 0}
    count = (mem.get("count") or 0) + 1 if mem.get("pending") == raw_signal else 1
    if count >= need:
        return {"displayed": raw_signal, "raw": raw_signal, "held": False,
                "pending": None, "count": 0}
    return {"displayed": displayed, "raw": raw_signal, "held": True,
            "pending": raw_signal, "count": count, "needs": need - count}


# ── what changed ─────────────────────────────────────────────────────────────

def diff_summary(prev: dict | None, row: dict) -> str | None:
    """One concise sentence on what materially moved since last eval."""
    if not prev:
        return None
    bits = []
    pg, ng = prev.get("pm_gap_pct"), row.get("pm_gap_pct")
    if pg is not None and ng is not None and abs(ng - pg) >= 0.75:
        bits.append(f"gap {pg:+.1f}% → {ng:+.1f}%")
    pt, nt = prev.get("tbs_p"), row.get("tbs_p")
    if pt is not None and nt is not None and abs(nt - pt) >= 4:
        bits.append(f"target-before-stop {pt:.0f}% → {nt:.0f}%")
    ph, nh = prev.get("from_pm_high_pct"), row.get("from_pm_high_pct")
    if row.get("direction") == "up" and ph is not None and nh is not None \
            and nh > -0.15 and ph <= -1.0:
        bits.append("price back at the PM high")
    if prev.get("catalyst_kind") != row.get("catalyst_kind"):
        bits.append(f"catalyst now {row.get('catalyst_kind')}")
    if prev.get("signal") != row.get("signal"):
        bits.append(f"{prev.get('signal')} → {row.get('signal')}")
    if not bits:
        return None
    return "; ".join(bits)
