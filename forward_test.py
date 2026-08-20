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


def _close_on(series: list, day: str):
    """The close for exactly this day, or None if the series has no such day."""
    for r in series:
        if r[0] == day:
            return r[1]
        if r[0] > day:
            break
    return None


# ── has the price basis changed under a stored recommendation? ──────────────
#
# A recommendation records the share price it was made at. Price history
# providers return SPLIT-ADJUSTED series, so after a ten-for-one split the
# same day reads at a tenth of what was recorded, and a flat stock scores
# −90%. The recommended option is worse: a $480 put settled against a $48
# underlying looks catastrophically assigned.
#
# The test is safe against a real price move BY CONSTRUCTION, because both
# numbers describe THE SAME DAY. A stock that fell forty percent that day
# fell forty percent in the recorded price and in the close alike; only a
# re-basing of the series can make the two disagree. What is left is
# ordinary drift — the recorded price is the last trade when the snapshot
# was taken and the close is the official one — which is small.
#
# Nothing is repaired. An option contract especially is never re-struck:
# the Options Clearing Corporation's adjustments do not always equal simple
# split arithmetic, so a row whose basis moved is refused and left alone.

# How far the recorded price and that same day's close may differ and still
# be the same share. Intraday drift between a capture and the close is
# comfortably inside this; the smallest split in common use is three-for-two,
# which is not.
BASIS_TOLERANCE = 0.25

# Ratios a corporate action produces, for naming what happened. A ratio that
# lands on none of them is still refused — an unexplained re-basing is not
# more trustworthy than an explained one.
_SPLIT_RATIOS = sorted(
    {float(k) for k in (2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 30, 50, 100)}
    | {1.0 / k for k in (2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 30, 50, 100)}
    | {1.5, 2.0 / 3.0})
_SPLIT_TOLERANCE = 0.02


def _snap_split(ratio):
    for cand in _SPLIT_RATIOS:
        if abs(ratio / cand - 1.0) <= _SPLIT_TOLERANCE:
            return cand
    return None


def basis_change(recorded_price, series_close,
                 tolerance: float = BASIS_TOLERANCE) -> dict | None:
    """Do the recorded price and that same day's close describe one share?

    Returns None when they do, and the evidence when they do not.
    """
    a, b = _num(recorded_price), _num(series_close)
    if a is None or b is None or a <= 0 or b <= 0:
        return None
    ratio = a / b
    if abs(ratio - 1.0) <= tolerance:
        return None
    split = _snap_split(ratio)
    if split is not None and split > 1:
        what = (f"a {split:.0f}-for-1 split" if abs(split - round(split)) < 1e-9
                else f"a {split:.4g}-for-1 split")
    elif split is not None:
        what = f"a 1-for-{1.0 / split:.0f} reverse split"
    else:
        what = "a restated price series"
    return {"recorded": a, "series_close": b, "ratio": ratio, "split": split,
            "what": what,
            "reason": (f"The recommendation was written at ${a:,.2f} and the "
                       f"price history now reports ${b:,.2f} for that same "
                       f"day — consistent with {what}. Nothing is adjusted: "
                       f"an option's terms after a corporate action are set "
                       f"by the clearing corporation and do not always equal "
                       f"simple split arithmetic, so this row is left as it "
                       f"was written and not scored.")}


# ── one observation ─────────────────────────────────────────────────────────

# ── is this row scorable at all ─────────────────────────────────────────────
#
# A stored recommendation is immutable. When something it needed was never
# written down, the row is not repaired and not deleted — it is marked as not
# eligible, with the reason, and left exactly as it is. Excluding it is how
# the calibration stays honest; rewriting it would destroy the evidence of
# what was actually recorded that day.
#
# The requirements are CONDITIONAL. BUY SHARES names no option and needs
# none; a bull call spread needs both of its legs. Marking a row corrupt for
# missing a field its own recommendation never had any use for would throw
# away good days to satisfy a checklist.

ELIGIBLE = "ELIGIBLE"
NOT_ELIGIBLE = "NOT ELIGIBLE FOR FORWARD VALIDATION"

NO_DATE = "NO TRADING DAY RECORDED"
NO_PRICE = "NO SHARE PRICE RECORDED"
NO_VERDICT = "NO RECOMMENDATION RECORDED"
UNRECOVERABLE_CONFIG = "CONFIG NOT ARCHIVED"
NO_CONTRACT = "THE RECOMMENDED CONTRACT WAS NOT RECORDED"
NO_QUOTE = "THE RECOMMENDED CONTRACT CARRIES NO QUOTE"
WRONG_CONTRACT = "THE STORED CONTRACT IS NOT THE ONE RECOMMENDED"
NO_BENCHMARK = "NO BENCHMARK CLOSE RECORDED"
BENCHMARK_MISMATCH = "BENCHMARK MISMATCH"
PRICE_BASIS_CHANGED = "PRICE BASIS CHANGED"

ELIGIBILITY_NOTE = {
    NO_DATE: "Without the day it was made, there is nothing to measure from.",
    NO_PRICE: "Without the share price on the day, there is no entry to "
              "measure a return against.",
    NO_VERDICT: "Without the recommendation, there is nothing being scored.",
    UNRECOVERABLE_CONFIG: "The rules that produced this recommendation are "
                          "not in the archive and cannot be read back, so "
                          "what it meant cannot be established. Rows written "
                          "from the archive onward can be.",
    NO_CONTRACT: "This recommendation names an option, and the exact "
                 "contract was not recorded. Choosing one now from a chain "
                 "that has since moved would be the lookahead this engine "
                 "exists to prevent.",
    NO_QUOTE: "The contract was recorded without a price, so there is no "
              "entry to settle it against.",
    WRONG_CONTRACT: "The stored contract belongs to a different structure "
                    "from the one recommended. Settling up against it would "
                    "score a trade nobody was told to make, and its shape "
                    "alone cannot tell the two apart — a long-dated call and "
                    "a short put both carry a strike and a price.",
    NO_BENCHMARK: "No benchmark close was recorded on the day, so this row "
                  "can still be scored on its own return but not against "
                  "the market.",
    BENCHMARK_MISMATCH:
        "The future price series offered for the comparison belongs to a "
        "different index from the one recorded against this recommendation. "
        "Dividing one index's later close by another's starting close is not "
        "a relative return, so the row is scored on its own and not against "
        "the market.",
    PRICE_BASIS_CHANGED:
        "The share price recorded on the day and the price the history now "
        "reports for that same day are not on the same basis — a split or a "
        "restated series. The recommendation is left exactly as it was "
        "written; it simply cannot be settled up against a series measuring "
        "a different share.",
}

# What each recommendation needs from its own contract, and nothing more.
# An empty tuple means the recommendation names no option at all, which is a
# complete answer rather than a missing one.
# Two vocabularies reach this map and both are stored. `entry_verdict` is
# what the app RECOMMENDED ("BUY SHARES", "SELL PORTFOLIO SECURED PUT");
# `preferred_structure` is what the equal-capital comparator ranked first
# ("SHARES", "PORTFOLIO SECURED PUT", "LEAPS"). They are not always the same
# — a row can prefer a buy-write on the comparator and still say WAIT — and
# the recommendation is what is being validated, so it decides.
CONTRACT_REQUIRED = {
    # Recommendations that name no option, and need none.
    "BUY SHARES": (),
    "SHARES": (),
    "WAIT": (),
    "AVOID": (),
    "TOSS UP": (),
    "ATTRACTIVE": (),
    "WATCH": (),
    "SPECIALIZED MODEL REQUIRED": (),
    "HYBRID — VALUATION UNRELIABLE": (),
    "INSUFFICIENT DATA": (),
    # Recommendations that name one, and are scored on it.
    "SELL PORTFOLIO SECURED PUT": ("strike",),
    "PORTFOLIO SECURED PUT": ("strike",),
    "BUY LEAPS": ("strike",),
    "LEAPS": ("strike",),
    "BUY-WRITE": ("call_strike",),
    "BULL CALL SPREAD": ("long_strike", "short_strike"),
}

# The recommendation and the comparator name the same structures in two
# vocabularies, and the stored contract is stamped with the comparator's.
# Checking the numbers alone is not enough to tell a put from a long-dated
# call: both carry a strike and a price.
_SAME_STRUCTURE = {
    "BUY SHARES": "SHARES",
    "SELL PORTFOLIO SECURED PUT": "PORTFOLIO SECURED PUT",
    "BUY LEAPS": "LEAPS",
    "BUY-WRITE": "BUY-WRITE",
    "BULL CALL SPREAD": "BULL CALL SPREAD",
}


def _structures_agree(recommended, stored) -> bool:
    a = str(recommended or "").strip().upper()
    b = str(stored or "").strip().upper()
    if not b:
        return False              # unstamped: cannot be confirmed, so is not
    return b in (a, _SAME_STRUCTURE.get(a, a))


# The price the structure was entered at. A put is sold for a credit; a
# long-dated call and a spread are bought for a debit; a buy-write collects a
# credit for the call it writes. Any of the quoted sides will also do.
_QUOTE_KEYS = ("credit", "debit", "mid", "bid", "ask")


def contract_required_for(structure) -> tuple:
    """Which strikes a recommendation has to name to be scorable.

    An unknown structure is required to name a strike rather than waved
    through: a new structure that quietly needed no contract would be scored
    against nothing at all.
    """
    key = str(structure or "").strip().upper()
    return CONTRACT_REQUIRED.get(key, ("strike",))


def eligibility(row: dict, known_hashes=None) -> dict:
    """Can this stored row be scored exactly, and if not, what is missing.

    `known_hashes` is the set of configurations in the immutable archive.
    Pass it and a row naming rules that cannot be read back is excluded; pass
    None and the question is not asked at all, which is only right for a
    caller that has no archive to check against.
    """
    reasons = []
    day = str(row.get("date") or "")[:10]
    if not day:
        reasons.append(NO_DATE)
    if _num(row.get("price")) is None or (_num(row.get("price")) or 0) <= 0:
        reasons.append(NO_PRICE)

    # What was RECOMMENDED decides what has to have been recorded. A row
    # that said WAIT is not incomplete for lacking the put its comparator
    # happened to rank first — nobody was told to sell one.
    structure = (row.get("entry_verdict") or row.get("preferred_structure")
                 or row.get("verdict"))
    if not structure:
        reasons.append(NO_VERDICT)

    # `known_hashes is None` means the caller has no archive to check
    # against and is not asking the question — so a row with no hash at all
    # is not condemned on a question nobody asked. Production always passes
    # the archive, and there a hash that is missing and a hash that is
    # absent from the archive are the same answer: the rules cannot be read
    # back, so what the verdict meant cannot be established.
    cfg_hash = row.get("config_hash")
    if known_hashes is not None and (not cfg_hash
                                     or cfg_hash not in known_hashes):
        reasons.append(UNRECOVERABLE_CONFIG)

    # The contract, only where the recommendation actually names one.
    needs = contract_required_for(structure) if structure else ()
    contract = row.get("recommended_contract") or {}
    if needs:
        if not contract:
            reasons.append(NO_CONTRACT)
        else:
            if any(_num(contract.get(k)) is None for k in needs):
                reasons.append(NO_CONTRACT)
            if all(_num(contract.get(k)) is None for k in _QUOTE_KEYS):
                reasons.append(NO_QUOTE)
            # The comparator's preferred structure and the recommendation are
            # not always the same, and the stored contract is the
            # comparator's. A long-dated call standing in for a put has a
            # strike and a debit and would otherwise pass every field check.
            if not _structures_agree(structure, contract.get("structure")):
                reasons.append(WRONG_CONTRACT)

    # The benchmark gates only the comparison against the market. A row
    # without one is still a real recommendation with a real return.
    bench_ok = bool(row.get("benchmark_symbol")
                    and _num(row.get("benchmark_close")) is not None
                    and (_num(row.get("benchmark_close")) or 0) > 0)

    eligible = not reasons
    return {
        "eligible": eligible,
        "state": ELIGIBLE if eligible else NOT_ELIGIBLE,
        "reasons": reasons,
        "notes": [ELIGIBILITY_NOTE[r] for r in reasons],
        "structure": structure,
        "contract_required": list(needs),
        "benchmark_relative": bench_ok,
        "benchmark_reason": ("" if bench_ok else ELIGIBILITY_NOTE[NO_BENCHMARK]),
        "config_hash": cfg_hash,
        "date": day,
        "ticker": row.get("ticker"),
        "version": FORWARD_TEST_VERSION,
    }


def eligibility_report(history_by_symbol: dict, known_hashes=None) -> dict:
    """Every stored row, sorted into what can be scored and what cannot."""
    rows, by_reason, days = [], {}, {}
    for sym, hist in sorted((history_by_symbol or {}).items()):
        for row in hist or []:
            got = eligibility(row, known_hashes)
            got["ticker"] = got["ticker"] or sym
            rows.append(got)
            for r in got["reasons"]:
                by_reason[r] = by_reason.get(r, 0) + 1
            d = days.setdefault(got["date"] or "(no date)",
                                {"day": got["date"], "eligible": 0,
                                 "not_eligible": 0, "reasons": {}})
            d["eligible" if got["eligible"] else "not_eligible"] += 1
            for r in got["reasons"]:
                d["reasons"][r] = d["reasons"].get(r, 0) + 1
    ok = [r for r in rows if r["eligible"]]
    return {
        "rows": rows, "n": len(rows), "eligible": len(ok),
        "not_eligible": len(rows) - len(ok),
        "benchmark_relative": sum(1 for r in ok if r["benchmark_relative"]),
        "by_reason": by_reason,
        "by_day": [days[k] for k in sorted(days)],
        "reason": (
            f"{len(ok)} of {len(rows)} stored recommendations can be scored "
            f"exactly." + (
                " The rest are excluded and left exactly as they are: "
                + ", ".join(f"{n} for {r.lower()}"
                            for r, n in sorted(by_reason.items()))
                + ". Nothing is repaired — a row that did not record what it "
                  "needed cannot be made to have recorded it."
                if by_reason else
                " Nothing is excluded.")),
        "version": FORWARD_TEST_VERSION,
    }


def outcome(row: dict, series: list, horizon: int, today: str,
            benchmark: list | None = None,
            benchmark_symbol: str | None = None) -> dict | None:
    """What happened to one recommendation over one horizon, or None when
    the horizon has not completed.

    `row` is a stored daily snapshot. `series` is that ticker's price
    history. Nothing is read from `row` that was not written on its own day,
    and nothing is read from `series` before that day.

    `benchmark` is the future series of the index this row was recorded
    against, and `benchmark_symbol` names it. Where the name is given and
    does not match what the row recorded, the comparison against the market
    is refused rather than computed from two different indexes.
    """
    day = str(row.get("date") or "")[:10]
    d0 = _d(day)
    if d0 is None:
        return None
    entry = _num(row.get("price"))
    if entry is None or entry <= 0:
        return None
    # The series must still be measuring the same share this was written
    # against. A split re-bases it, and a flat stock then scores −90%.
    basis = basis_change(entry, _close_on(series, day))
    if basis is not None:
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

    # The comparison against the market needs what the benchmark was worth
    # on the day the call was made, recorded on that day. Without it the row
    # is still perfectly scorable on its own return — it simply cannot be
    # measured against anything, and says so rather than borrowing a close
    # from a series fetched later.
    # The baseline is the close RECORDED ON THE DAY, not the one a series
    # fetched later reports for that date. They can differ — a provider
    # correction, a distribution adjustment — and taking the later one would
    # measure the entry against a number that did not exist when the call
    # was made. That is the lookahead this engine exists to refuse, and it
    # is the whole reason the close is written into the row prospectively.
    # The series is used only for the far end of the window, which had not
    # happened yet and could not have been recorded.
    bench_ret = None
    bench_note = ""
    b_entry = _num(row.get("benchmark_close"))
    row_bench = row.get("benchmark_symbol")
    has_bench = bool(row_bench and b_entry is not None and b_entry > 0)
    # The series offered has to be the index this row was recorded against.
    # A dict is resolved by name; a bare series is checked against the name
    # the caller gives for it. Either way a technology holding recorded
    # against XLK is never settled up using XLE.
    if isinstance(benchmark, dict):
        benchmark_symbol = row_bench
        benchmark = benchmark.get(row_bench)
    mismatch = bool(has_bench and benchmark_symbol
                    and str(benchmark_symbol).upper() != str(row_bench).upper())
    if mismatch:
        benchmark = None
    if not has_bench:
        bench_note = ELIGIBILITY_NOTE[NO_BENCHMARK]
    elif mismatch:
        bench_note = ELIGIBILITY_NOTE[BENCHMARK_MISMATCH]
    elif benchmark:
        b1 = _close_on_or_before(benchmark, target)
        if b1 and b1[0] > day:
            bench_ret = (b1[1] / b_entry - 1.0) * 100.0
            bench_note = ("Measured from the benchmark close recorded on the "
                          "day, not from a price fetched afterwards.")
        else:
            bench_note = ("The benchmark series does not reach the end of "
                          "this window.")
    else:
        bench_note = "No benchmark price series was available to compare to."

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
        "benchmark_symbol": row.get("benchmark_symbol"),
        "benchmark_close_on_the_day": _num(row.get("benchmark_close")),
        "benchmark_return_pct": bench_ret,
        "benchmark_relative_eligible": bench_ret is not None,
        "benchmark_note": bench_note,
        "excess_return_pct": (None if bench_ret is None else ret - bench_ret),
        "range_contained_outcome": contained,
        "distance_from_base_pct": distance,
        "days_of_prices": len(window),
    }


def observations(history_by_symbol: dict, bars_by_symbol: dict, today: str,
                 benchmark_bars=None,
                 horizons=HORIZONS, cfg=None, known_hashes=None) -> dict:
    """Every completed (snapshot, horizon) pair the store can support.

    Rows that cannot be scored exactly are excluded here rather than scored
    approximately — chiefly a recommendation whose rules are not in the
    immutable archive, because what its verdict MEANT cannot be established.
    Pass `known_hashes` to enforce that; pass None and the archive is not
    consulted. Nothing is ever rewritten to make a row pass.

    `benchmark_bars` is a MAP of benchmark symbol to that index's daily bars.
    Each row is compared only against the index it was recorded against —
    watchlists span sectors, so one index cannot stand in for all of them.
    A single list is still accepted for a caller with one benchmark and is
    used for every row, which is only correct when they all share it.
    """
    cfg = cfg or {}
    if isinstance(benchmark_bars, dict):
        bench = {str(k).upper(): index_bars(v or [])
                 for k, v in benchmark_bars.items() if v}
    else:
        bench = index_bars(benchmark_bars or [])
    rows, skipped = [], {"incomplete_horizon": 0, "no_prices": 0,
                         "no_price_recorded": 0, "not_eligible": 0,
                         "price_basis_changed": 0}
    excluded, series_cache = {}, {}
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
            fit = eligibility(row, known_hashes)
            if not fit["eligible"]:
                skipped["not_eligible"] += len(horizons)
                for r in fit["reasons"]:
                    excluded[r] = excluded.get(r, 0) + 1
                continue
            # A row the price history no longer measures on the same basis
            # is reported as such rather than counted among the horizons that
            # simply have not finished yet. The two mean opposite things: one
            # arrives with time, the other never will.
            day = str(row.get("date") or "")[:10]
            if basis_change(row.get("price"), _close_on(series, day)):
                skipped["price_basis_changed"] += len(horizons)
                excluded[PRICE_BASIS_CHANGED] = \
                    excluded.get(PRICE_BASIS_CHANGED, 0) + 1
                continue
            for h in horizons:
                o = outcome(row, series, h, today, bench)
                if o is None:
                    skipped["incomplete_horizon"] += 1
                else:
                    rows.append(o)
    _attach_sector_relative(rows, cfg)
    return {"rows": rows, "n": len(rows), "skipped": skipped,
            "excluded_by_reason": excluded,
            "excluded_note": (
                "Rows that could not be scored exactly were left out and left "
                "alone: " + ", ".join(f"{n} for {r.lower()}"
                                      for r, n in sorted(excluded.items()))
                + "." if excluded else
                "No stored row had to be excluded."),
            "config_archive_checked": known_hashes is not None,
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
    # A recorded strike against a re-based series is the worst case of all:
    # a $480 put settled against a $48 underlying reads as a total loss on a
    # position that was never in trouble. The contract's real terms after a
    # corporate action come from the clearing corporation, not from
    # arithmetic, so nothing is settled here.
    basis = basis_change(spot0, _close_on(series, day))
    if basis is not None:
        out["reason"] = basis["reason"]
        out["price_basis_changed"] = basis
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
                     today: str, cfg=None, known_hashes=None) -> dict:
    """Every recommended contract that has reached its expiration, grouped
    by structure.

    Same exclusion as `observations`: a row whose rules cannot be read back
    from the archive is not settled up, because the thresholds that chose
    the contract are part of what is being validated.
    """
    cfg = cfg or {}
    per: dict = {}
    n_rows, excluded = 0, 0
    for sym, hist in (history_by_symbol or {}).items():
        bars = bars_by_symbol.get(sym)
        if not bars:
            continue
        series = index_bars(bars)
        for row in hist or []:
            if not eligibility(row, known_hashes)["eligible"]:
                excluded += 1
                continue
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
           "structures": {}, "min_sample": need,
           "excluded_rows": excluded,
           "excluded_note": (
               f"{excluded} stored recommendation"
               f"{'' if excluded == 1 else 's'} could not be settled up "
               f"exactly and were left out, and left alone."
               if excluded else "No stored recommendation had to be left out.")}
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
