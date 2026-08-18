"""forward_test.py — did the Investment tab's own recommendations work?
(Phase 4)

Since Phase 1 this app has written one snapshot per ticker per day: the
price, the verdict, the preferred structure, the Bear/Base/Bull range, the
buy zone, the four dimension scores, the value-trap state, the fair-value
confidence, the exact option contracts it recommended, and the hash of the
configuration that produced all of it. That store is now old enough for
some of those days to have a future.

This module reads it forward. Nothing here re-runs the engine on old data,
and nothing here rewrites what was said. A recommendation made on a Tuesday
is judged exactly as it was written down that Tuesday, against what the
price then did.

THE THREE RULES THIS ENFORCES

1. NO LOOKAHEAD. Every input comes from the stored row, which was written
   before the outcome existed. Every outcome comes from bars strictly AFTER
   that row's date. There is no path by which a later filing, a later
   estimate, a later configuration or a later price can change what the
   recommendation was — because the recommendation is not recomputed, it is
   read.

2. NO INCOMPLETE HORIZONS. A ninety-day outcome is only counted once ninety
   days have actually passed AND the price series covers them. A partial
   window would systematically favour whatever the market did most recently,
   which is exactly the bias that makes most strategy statistics useless.

3. NO SUBSTITUTION. Where a structure is scored, it is scored on the EXACT
   contract that was recommended — that strike, that expiration, that
   credit. Picking a better contract after seeing the outcome is the single
   easiest way to manufacture a good result, and it is the reason this is
   written as a separate rule rather than left to good intentions.

WHAT IT WILL NOT DO

There is no accuracy score. A single number that blends a hit rate, a
median return and a calibration check is a number nobody can act on and
everybody can be reassured by. What is reported is the sample size first,
then the medians, then the calibration, each with the count behind it, and
INSUFFICIENT SAMPLE wherever the count is too small to mean anything.

Results are separated by configuration hash where the configurations differ
materially. Combining a month run under one rule set with a month run under
another and calling it one strategy would be describing something that was
never actually run.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

FORWARD_TEST_VERSION = "invest-forward-1.0.0"

HORIZONS = (30, 90, 180, 365)

# Below this many observations a bucket says INSUFFICIENT SAMPLE and shows
# nothing else. Thirty is not a magic number and is not claimed to be — it
# is the conventional floor below which a median is an anecdote, and it is
# adjustable.
MIN_SAMPLE = 30

INSUFFICIENT = "INSUFFICIENT SAMPLE"

DEFAULTS = {
    "forward_min_sample": MIN_SAMPLE,
    # A sector-relative return needs enough of this company's own industry
    # to have been recorded on the same day.
    "forward_min_sector_peers": 5,
    # Verdicts counted as a recommendation to own, for the "did ATTRACTIVE
    # beat WAIT" comparison.
    "forward_positive_verdicts": ["BUY SHARES", "SELL PORTFOLIO SECURED PUT",
                                  "BUY LEAPS", "BUY-WRITE",
                                  "BULL CALL SPREAD", "ATTRACTIVE"],
    "forward_negative_verdicts": ["AVOID"],
    "forward_wait_verdicts": ["WAIT"],
}


def cfg_get(cfg, key):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _d(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


# ── price access ────────────────────────────────────────────────────────────

def index_bars(bars: list) -> list:
    """Daily closes as a sorted list of (date, close, high, low)."""
    out = []
    for b in bars or []:
        day = str(b.get("date") or b.get("d") or "")[:10]
        c = _num(b.get("close") if b.get("close") is not None else b.get("c"))
        h = _num(b.get("high") if b.get("high") is not None else b.get("h"))
        lo = _num(b.get("low") if b.get("low") is not None else b.get("l"))
        if day and c and c > 0:
            out.append((day, c, h or c, lo or c))
    out.sort()
    return out


def _slice(series: list, start_day: str, end_day: str) -> list:
    return [r for r in series if start_day < r[0] <= end_day]


def _close_on_or_before(series: list, day: str):
    got = None
    for r in series:
        if r[0] <= day:
            got = r
        else:
            break
    return got


# ── one observation ─────────────────────────────────────────────────────────

def outcome(row: dict, series: list, horizon: int, today: str,
            benchmark: list | None = None) -> dict | None:
    """What happened to one recommendation over one horizon, or None when
    the horizon has not completed.

    `row` is a stored daily snapshot. `series` is that ticker's price
    history. Nothing is read from `row` that was not written on its own day,
    and nothing is read from `series` before that day.
    """
    day = str(row.get("date") or "")[:10]
    d0 = _d(day)
    if d0 is None:
        return None
    entry = _num(row.get("price"))
    if entry is None or entry <= 0:
        return None
    target = (d0 + timedelta(days=int(horizon))).isoformat()
    # Rule 2: the horizon must have completed in calendar time AND the price
    # series must actually reach it. Either one alone is not enough — a
    # series that simply stops early would otherwise be scored on a shorter
    # window than the one being reported.
    if target > str(today)[:10]:
        return None
    if not series or series[-1][0] < target:
        return None
    window = _slice(series, day, target)
    if not window:
        return None
    end = window[-1][1]
    highs = [r[2] for r in window]
    lows = [r[3] for r in window]
    ret = (end / entry - 1.0) * 100.0
    mfe = (max(highs) / entry - 1.0) * 100.0
    mae = (min(lows) / entry - 1.0) * 100.0

    bench_ret = None
    if benchmark:
        b0 = _close_on_or_before(benchmark, day)
        b1 = _close_on_or_before(benchmark, target)
        if b0 and b1 and b0[1] > 0 and b1[0] > b0[0]:
            bench_ret = (b1[1] / b0[1] - 1.0) * 100.0

    bear = _num(row.get("fair_value_bear"))
    base = _num(row.get("fair_value_base"))
    bull = _num(row.get("fair_value_bull"))
    contained = None
    if bear is not None and bull is not None:
        contained = bool(bear <= end <= bull)
    distance = None
    if base and base > 0:
        distance = (end / base - 1.0) * 100.0

    return {
        "ticker": row.get("ticker"), "date": day, "horizon": horizon,
        "completed_on": target,
        # ── what was said, read not recomputed ──
        "price": entry,
        "verdict": row.get("entry_verdict") or row.get("verdict"),
        "phase2_verdict": row.get("verdict"),
        "preferred_structure": row.get("preferred_structure"),
        "fair_value_bear": bear, "fair_value_base": base,
        "fair_value_bull": bull,
        "buy_zone": _num(row.get("buy_zone")),
        "premium_to_buy_zone_pct": _num(row.get("premium_to_buy_zone_pct")),
        "quality_label": row.get("quality_label"),
        "growth_label": row.get("growth_label"),
        "valuation_label": row.get("valuation_label"),
        "valuation_self_percentile": _num(row.get("valuation_self_percentile")),
        "revisions_label": row.get("revisions_label"),
        "value_trap_level": row.get("value_trap_level"),
        "fair_value_confidence": row.get("fair_value_confidence"),
        "business_type": row.get("business_type"),
        "comparison_toss_up": row.get("comparison_toss_up"),
        "sic": row.get("sic"),
        "config_hash": row.get("config_hash"),
        # ── what happened ──
        "end_price": end,
        "return_pct": ret,
        "max_adverse_excursion_pct": mae,
        "max_favorable_excursion_pct": mfe,
        "benchmark_return_pct": bench_ret,
        "excess_return_pct": (None if bench_ret is None else ret - bench_ret),
        "range_contained_outcome": contained,
        "distance_from_base_pct": distance,
        "days_of_prices": len(window),
    }


def observations(history_by_symbol: dict, bars_by_symbol: dict, today: str,
                 benchmark_bars: list | None = None,
                 horizons=HORIZONS, cfg=None) -> dict:
    """Every completed (snapshot, horizon) pair the store can support."""
    cfg = cfg or {}
    bench = index_bars(benchmark_bars or [])
    rows, skipped = [], {"incomplete_horizon": 0, "no_prices": 0,
                         "no_price_recorded": 0}
    series_cache = {}
    for sym, hist in (history_by_symbol or {}).items():
        bars = bars_by_symbol.get(sym)
        if not bars:
            skipped["no_prices"] += len(hist or []) * len(horizons)
            continue
        series = series_cache.get(sym)
        if series is None:
            series = index_bars(bars)
            series_cache[sym] = series
        for row in hist or []:
            if _num(row.get("price")) is None:
                skipped["no_price_recorded"] += len(horizons)
                continue
            for h in horizons:
                o = outcome(row, series, h, today, bench)
                if o is None:
                    skipped["incomplete_horizon"] += 1
                else:
                    rows.append(o)
    _attach_sector_relative(rows, cfg)
    return {"rows": rows, "n": len(rows), "skipped": skipped,
            "benchmark_available": bool(bench),
            "version": FORWARD_TEST_VERSION}


def _attach_sector_relative(rows: list, cfg) -> None:
    """Each observation against the median of its own industry on its own day.

    Built entirely from the stored snapshots themselves — the same date, the
    same horizon, the same two-digit industry code — so it needs no index
    data and no sector classification beyond what is already recorded. Where
    too few of a company's own industry were recorded that day, it is left
    unavailable rather than compared against a group that is not one.
    """
    need = int(cfg_get(cfg, "forward_min_sector_peers"))
    groups: dict = {}
    for r in rows:
        sic = str(r.get("sic") or "")[:2]
        if not sic:
            continue
        groups.setdefault((sic, r["date"], r["horizon"]), []).append(r)
    for key, members in groups.items():
        if len(members) < need:
            for m in members:
                m["sector_relative_return_pct"] = None
                m["sector_note"] = (
                    f"Only {len(members)} companies in industry {key[0]} were "
                    f"recorded on {key[1]}, fewer than the {need} needed for "
                    f"a sector comparison.")
            continue
        for m in members:
            # The company is left out of the median it is measured against.
            # Including it would drag the benchmark toward the thing being
            # benchmarked, which matters most in exactly the small groups
            # this app tends to have.
            others = [x["return_pct"] for x in members if x is not m]
            med = statistics.median(others)
            m["sector_relative_return_pct"] = m["return_pct"] - med
            m["sector_median_return_pct"] = med
            m["sector_n"] = len(others)
            m["sector_note"] = (f"Against the median of the {len(others)} "
                                f"other companies in industry {key[0]} "
                                f"recorded on the same day.")
    for r in rows:
        r.setdefault("sector_relative_return_pct", None)
        r.setdefault("sector_note", "No industry code was recorded that day.")


# ── the calibration report ──────────────────────────────────────────────────

def _stats(group: list, cfg) -> dict:
    n = len(group)
    need = int(cfg_get(cfg, "forward_min_sample"))
    if n < need:
        return {"n": n, "sufficient": False, "verdict": INSUFFICIENT,
                "reason": (f"{n} observation{'s' if n != 1 else ''}, below "
                           f"the {need} this panel requires before reporting "
                           f"a median. Nothing is concluded from it.")}
    rets = [r["return_pct"] for r in group]
    ex = [r["excess_return_pct"] for r in group
          if r.get("excess_return_pct") is not None]
    sec = [r["sector_relative_return_pct"] for r in group
           if r.get("sector_relative_return_pct") is not None]
    mae = [r["max_adverse_excursion_pct"] for r in group]
    mfe = [r["max_favorable_excursion_pct"] for r in group]
    contained = [r["range_contained_outcome"] for r in group
                 if r.get("range_contained_outcome") is not None]
    dist = [r["distance_from_base_pct"] for r in group
            if r.get("distance_from_base_pct") is not None]
    return {
        "n": n, "sufficient": True, "verdict": "",
        "median_return_pct": statistics.median(rets),
        "mean_return_pct": statistics.fmean(rets),
        "hit_rate_pct": sum(1 for x in rets if x > 0) / n * 100.0,
        "median_excess_return_pct": statistics.median(ex) if ex else None,
        "excess_n": len(ex),
        "beat_benchmark_pct": (sum(1 for x in ex if x > 0) / len(ex) * 100.0
                               if ex else None),
        "median_sector_relative_pct": statistics.median(sec) if sec else None,
        "sector_n": len(sec),
        "median_max_adverse_excursion_pct": statistics.median(mae),
        "worst_max_adverse_excursion_pct": min(mae),
        "median_max_favorable_excursion_pct": statistics.median(mfe),
        "range_contained_pct": (sum(1 for x in contained if x) / len(contained)
                                * 100.0 if contained else None),
        "range_contained_n": len(contained),
        "median_distance_from_base_pct": (statistics.median(dist)
                                          if dist else None),
        "tickers": len({r["ticker"] for r in group}),
        "from": min(r["date"] for r in group),
        "to": max(r["date"] for r in group),
    }


def _by(rows: list, key, cfg) -> dict:
    groups: dict = {}
    for r in rows:
        k = key(r)
        if k is None or k == "":
            continue
        groups.setdefault(str(k), []).append(r)
    return {k: _stats(v, cfg) for k, v in sorted(groups.items())}


def _percentile_bucket(v) -> str | None:
    p = _num(v)
    if p is None:
        return None
    if p >= 80:
        return "Cheapest fifth of its own history"
    if p >= 60:
        return "Cheaper than usual"
    if p >= 40:
        return "About its usual level"
    if p >= 20:
        return "Dearer than usual"
    return "Dearest fifth of its own history"


def calibration(obs: dict, cfg=None, separate_configs: bool = True) -> dict:
    """The Investment Performance panel: sample size first, then what the
    sample says, then nothing where it says nothing."""
    cfg = cfg or {}
    rows = (obs or {}).get("rows") or []
    need = int(cfg_get(cfg, "forward_min_sample"))
    out = {"version": FORWARD_TEST_VERSION,
           "total_observations": len(rows),
           "min_sample": need,
           "horizons": {},
           "available": bool(rows),
           "reason": ""}
    if not rows:
        out["reason"] = (
            "No recommendation has aged far enough yet. The snapshot store "
            "records one row per ticker per day; the first thirty-day "
            "outcome can be measured thirty days after the first row, and "
            "the first year-long outcome a year after it. This panel fills "
            "itself in as that time passes and never back-fills.")
        return out

    configs = sorted({r.get("config_hash") for r in rows if r.get("config_hash")})
    out["config_hashes"] = configs
    out["config_note"] = _config_note(rows, configs, need)

    positive = set(cfg_get(cfg, "forward_positive_verdicts"))
    negative = set(cfg_get(cfg, "forward_negative_verdicts"))
    waiting = set(cfg_get(cfg, "forward_wait_verdicts"))

    for h in sorted({r["horizon"] for r in rows}):
        g = [r for r in rows if r["horizon"] == h]
        block = {"overall": _stats(g, cfg),
                 "by_verdict": _by(g, lambda r: r.get("verdict"), cfg),
                 "by_preferred_structure": _by(
                     g, lambda r: r.get("preferred_structure"), cfg),
                 "by_valuation_percentile": _by(
                     g, lambda r: _percentile_bucket(
                         r.get("valuation_self_percentile")), cfg),
                 "by_quality": _by(g, lambda r: r.get("quality_label"), cfg),
                 "by_growth": _by(g, lambda r: r.get("growth_label"), cfg),
                 "by_revisions": _by(g, lambda r: r.get("revisions_label"), cfg),
                 "by_value_trap": _by(g, lambda r: r.get("value_trap_level"), cfg),
                 "by_fair_value_confidence": _by(
                     g, lambda r: r.get("fair_value_confidence"), cfg),
                 "by_business_type": _by(g, lambda r: r.get("business_type"), cfg),
                 "toss_up": _toss_up(g, cfg),
                 "attractive_versus_wait": _contest(
                     g, positive, waiting, "recommended to own", "told to wait",
                     cfg),
                 "avoid_versus_rest": _contest(
                     g, negative, positive | waiting, "told to avoid",
                     "everything else", cfg),
                 }
        if separate_configs and len(configs) > 1:
            block["by_config"] = _by(g, lambda r: r.get("config_hash"), cfg)
        out["horizons"][str(h)] = block
    return out


def _config_note(rows, configs, need) -> str:
    if len(configs) <= 1:
        return ("Every observation was produced under one configuration, so "
                "they describe one set of rules.")
    counts = {c: sum(1 for r in rows if r.get("config_hash") == c)
              for c in configs}
    big = [c for c, n in counts.items() if n >= need]
    return (f"These observations span {len(configs)} different "
            f"configurations of the engine. {len(big)} of them have enough "
            f"observations to stand on their own and are broken out "
            f"separately; the combined figures mix rule sets and should be "
            f"read as a description of the store rather than as one "
            f"strategy's record.")


def _toss_up(group: list, cfg) -> dict:
    flagged = [r for r in group if r.get("comparison_toss_up")]
    n = len(group)
    return {"n_flagged": len(flagged), "n": n,
            "frequency_pct": (len(flagged) / n * 100.0) if n else None,
            "stats": _stats(flagged, cfg),
            "note": ("How often the structure comparison said the winner was "
                     "too close to call. A high frequency is information "
                     "about the structures being genuinely similar, not a "
                     "fault.")}


def _contest(group: list, a: set, b: set, a_label: str, b_label: str,
             cfg) -> dict:
    ga = [r for r in group if (r.get("verdict") or "") in a]
    gb = [r for r in group if (r.get("verdict") or "") in b]
    sa, sb = _stats(ga, cfg), _stats(gb, cfg)
    out = {"a_label": a_label, "b_label": b_label, "a": sa, "b": sb}
    if not (sa.get("sufficient") and sb.get("sufficient")):
        out["verdict"] = INSUFFICIENT
        out["reason"] = (f"{sa['n']} observations where the app {a_label} and "
                         f"{sb['n']} where it {b_label}. Both sides need at "
                         f"least {int(cfg_get(cfg, 'forward_min_sample'))} "
                         f"before a comparison means anything.")
        return out
    gap = sa["median_return_pct"] - sb["median_return_pct"]
    out["gap_pp"] = gap
    out["verdict"] = "AHEAD" if gap > 0 else ("BEHIND" if gap < 0 else "LEVEL")
    out["reason"] = (
        f"Where the app {a_label}, the median outcome was "
        f"{sa['median_return_pct']:+.1f}% over {sa['n']} observations. Where "
        f"it {b_label}, {sb['median_return_pct']:+.1f}% over {sb['n']}. That "
        f"is a gap of {gap:+.1f} percentage points. A gap measured over one "
        f"stretch of one market is a description of that stretch; it becomes "
        f"evidence only as the store grows across different conditions.")
    return out


# ── the exact recommended contracts ─────────────────────────────────────────

def structure_outcome(row: dict, series: list, today: str) -> dict:
    """What the EXACT contracts recommended on that day were worth at their
    own expirations.

    The contract is taken from the stored row and is never re-chosen. If the
    row recommended the $180 put expiring in March, that is the contract
    scored, at that strike, for that credit — whatever a better one would
    have done.
    """
    day = str(row.get("date") or "")[:10]
    spot0 = _num(row.get("price"))
    out = {"ticker": row.get("ticker"), "date": day, "price": spot0,
           "preferred_structure": row.get("preferred_structure"),
           "config_hash": row.get("config_hash"), "results": {}}
    if spot0 is None or spot0 <= 0 or not series:
        out["reason"] = "No price was recorded on that day."
        return out

    def settle(exp):
        e = str(exp or "")[:10]
        if not e or e > str(today)[:10]:
            return None
        if series[-1][0] < e:
            return None
        got = _close_on_or_before(series, e)
        return got[1] if got and got[0] > day else None

    # SHARES — the yardstick every structure is measured against.
    for name, exp in (("SHARES", row.get("comparison_expiration")),):
        s = settle(exp)
        if s is not None:
            out["results"]["SHARES"] = {
                "expiration": exp, "settle": s,
                "value_per_100": s * 100.0,
                "profit": (s - spot0) * 100.0,
                "return_pct": (s / spot0 - 1.0) * 100.0,
                "note": "A hundred shares held to the same date."}

    k = _num(row.get("csp_strike"))
    credit = _num(row.get("csp_credit"))
    s = settle(row.get("csp_expiration"))
    if k and credit is not None and s is not None:
        assigned = s < k
        # The put is secured by the FULL strike notional, which is what the
        # Phase 3 comparator required and what a cash-secured put actually
        # ties up.
        profit = credit * 100.0 - (max(0.0, k - s) * 100.0)
        out["results"]["PORTFOLIO SECURED PUT"] = {
            "strike": k, "expiration": row.get("csp_expiration"),
            "credit": credit, "settle": s, "assigned": assigned,
            "profit": profit,
            "return_on_secured_capital_pct": profit / (k * 100.0) * 100.0,
            "note": ("Assigned: the shares were bought at the strike, and the "
                     "credit reduced what was paid for them."
                     if assigned else
                     "Expired worthless and the credit was kept.")}

    k = _num(row.get("leaps_strike"))
    debit = _num(row.get("leaps_debit"))
    s = settle(row.get("leaps_expiration"))
    if k and debit is not None and s is not None:
        value = max(0.0, s - k) * 100.0
        cost = debit * 100.0
        out["results"]["LEAPS"] = {
            "strike": k, "expiration": row.get("leaps_expiration"),
            "debit": debit, "settle": s,
            "value_at_expiry": value, "profit": value - cost,
            "return_pct": ((value / cost - 1.0) * 100.0) if cost > 0 else None,
            "expired_worthless": value <= 0,
            "note": ("Expired worthless — the whole premium was lost."
                     if value <= 0 else
                     "Settled in the money at expiration.")}

    k = _num(row.get("buy_write_call_strike"))
    credit = _num(row.get("buy_write_credit"))
    s = settle(row.get("comparison_expiration"))
    if k and credit is not None and s is not None:
        capped = s > k
        shares_end = min(s, k)
        profit = (shares_end - spot0) * 100.0 + credit * 100.0
        out["results"]["BUY-WRITE"] = {
            "call_strike": k, "expiration": row.get("comparison_expiration"),
            "credit": credit, "settle": s, "called_away": capped,
            "profit": profit,
            "return_pct": profit / (spot0 * 100.0) * 100.0,
            "forfeited": max(0.0, s - k) * 100.0,
            "note": (f"Called away at the strike; ${max(0.0, s - k) * 100:,.0f} "
                     f"of the move above it was forfeited." if capped else
                     "The call expired worthless and the shares were kept.")}

    if not out["results"]:
        out["reason"] = ("No recommended contract from that day has reached "
                         "its expiration yet.")
    return out


def structure_report(history_by_symbol: dict, bars_by_symbol: dict,
                     today: str, cfg=None) -> dict:
    """Every recommended contract that has reached its expiration, grouped
    by structure."""
    cfg = cfg or {}
    per: dict = {}
    n_rows = 0
    for sym, hist in (history_by_symbol or {}).items():
        bars = bars_by_symbol.get(sym)
        if not bars:
            continue
        series = index_bars(bars)
        for row in hist or []:
            res = structure_outcome(row, series, today)
            if not res.get("results"):
                continue
            n_rows += 1
            for kind, r in res["results"].items():
                per.setdefault(kind, []).append(
                    {**r, "ticker": sym, "date": res["date"],
                     "config_hash": res["config_hash"],
                     "was_preferred": res["preferred_structure"] == kind})
    need = int(cfg_get(cfg, "forward_min_sample"))
    out = {"version": FORWARD_TEST_VERSION, "settled_recommendations": n_rows,
           "structures": {}, "min_sample": need}
    for kind, rows in sorted(per.items()):
        profits = [r["profit"] for r in rows if r.get("profit") is not None]
        pref = [r for r in rows if r["was_preferred"]]
        block = {"n": len(rows), "n_when_preferred": len(pref),
                 "sufficient": len(rows) >= need}
        if profits:
            block.update({
                "median_profit": statistics.median(profits),
                "total_profit": sum(profits),
                "win_rate_pct": sum(1 for p in profits if p > 0)
                / len(profits) * 100.0})
        if not block["sufficient"]:
            block["verdict"] = INSUFFICIENT
            block["reason"] = (f"{len(rows)} settled contract"
                               f"{'s' if len(rows) != 1 else ''}, below the "
                               f"{need} needed. Shown so the ledger is "
                               f"visible, not so a conclusion is drawn from "
                               f"it.")
        out["structures"][kind] = block
    if not per:
        out["reason"] = ("No contract this app recommended has reached its "
                         "expiration yet, so there is nothing to score. This "
                         "fills in as the recommended expirations pass.")
    return out


# ── the no-lookahead guarantee, made testable ───────────────────────────────

def lookahead_audit(row: dict, series: list, horizon: int, today: str,
                    benchmark: list | None = None) -> dict:
    """Prove that an outcome used nothing from before the recommendation and
    nothing from after the horizon.

    Written as a function rather than left to the tests so the guarantee is
    part of the module rather than part of a promise about it: it recomputes
    the outcome against a price series truncated to the horizon and asserts
    the answer is identical. If anything inside reached past the horizon, the
    two results differ and this says so.
    """
    day = str(row.get("date") or "")[:10]
    d0 = _d(day)
    if d0 is None:
        return {"ok": False, "reason": "The row carries no date."}
    target = (d0 + timedelta(days=int(horizon))).isoformat()
    full = outcome(row, series, horizon, today, benchmark)
    if full is None:
        return {"ok": True, "reason": "The horizon has not completed, so no "
                                      "outcome was produced.", "n": 0}
    truncated = [r for r in series if r[0] <= target]
    bench_t = ([r for r in (benchmark or []) if r[0] <= target]
               if benchmark else None)
    limited = outcome(row, truncated, horizon, today, bench_t)
    if limited is None:
        return {"ok": False,
                "reason": "Truncating the price series at the horizon removed "
                          "the outcome entirely, which means the full run "
                          "reached past it."}
    fields = ("return_pct", "max_adverse_excursion_pct",
              "max_favorable_excursion_pct", "end_price",
              "benchmark_return_pct", "range_contained_outcome",
              "distance_from_base_pct")
    diffs = {f: (full.get(f), limited.get(f)) for f in fields
             if full.get(f) != limited.get(f)}
    # Prices BEFORE the recommendation must be irrelevant too.
    later_only = [r for r in series if r[0] >= day]
    from_entry = outcome(row, later_only, horizon, today, benchmark)
    if from_entry is not None:
        for f in fields:
            if full.get(f) != from_entry.get(f):
                diffs.setdefault(f, (full.get(f), from_entry.get(f)))
    return {"ok": not diffs, "differences": diffs, "horizon_end": target,
            "reason": ("Identical with the price series cut off at the "
                       "horizon and with everything before the "
                       "recommendation removed."
                       if not diffs else
                       "The outcome changed when the future was withheld, "
                       "which means something read past the horizon.")}
