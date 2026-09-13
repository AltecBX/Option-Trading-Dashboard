"""weekly_sell.py — the strike engine behind the Weekly Option Selling Setup.

The panel used to answer "where should I sell?" with two numbers: the worst
weekly low and the best weekly high of the last N weeks. Those are the two
most extreme things the stock ever did — a 32-week worst low of -18% and a
best high of +45% describe a crash and a melt-up, not next Friday. Selling
off them is either far too wide to pay anything or, when the stock is calm,
far too tight. A 0.20 delta rule has the opposite problem: it is one number
from one model, it ignores what this stock actually does, it ignores whether
the strike can be filled, and it ignores whether the premium is worth the
risk at all.

This module answers the question the way a desk does, in four steps.

  1. HORIZON. Risk is measured over the number of TRADING SESSIONS between
     now and the expiry on the screen — not a calendar week. A Monday sale
     of a Friday option carries five sessions; a Thursday sale carries two.
     Every number below is time-matched to that count.

  2. WHAT THIS STOCK ACTUALLY DOES. For every day in the daily history, the
     next `k` sessions are replayed: how far did price travel DOWN at worst,
     how far UP at worst, and where did it CLOSE. That yields a distribution
     of real k-session outcomes for this symbol — hundreds of them — instead
     of a single extreme. Touching a level and finishing beyond it are kept
     apart on purpose: the first is what scares you mid-week, the second is
     what actually assigns you, and only the second costs money.

  3. WHAT THE MARKET IS CHARGING. The same band from the option market's own
     implied volatility, on the same horizon and at the same odds, so the
     two are directly comparable. The SELL ZONE drawn on screen keeps
     whichever leg is more cautious, each side independently.

  4. WHICH STRIKE PAYS FOR THE RISK. A strike qualifies only when BOTH
     assignment reads clear the limit — the share of matched windows that
     finished through it, and the option market's own implied odds from the
     chain's delta. Both are printed beside the strike, so a refusal always
     names a number the reader can see. (An earlier version compared the
     strike against an interpolated price line instead, which could refuse a
     strike whose own printed history rate was comfortably inside the
     limit.) Survivors are ranked led by EXPECTED VALUE measured on the real
     distribution from step 2: the credit collected, minus the average loss
     over every historical window that would have finished through the
     strike, per dollar of collateral, annualized. Income breaks the ties
     expected value cannot — on a fairly priced chain every strike has about
     the same edge, and without it the ranking drifts to the safest, emptiest
     strike on the board. Liquidity is a hard gate and a scored term,
     because a strike that cannot be filled is not a trade.

When nothing clears the limit, the answer is the best strike at a stated
looser line, tagged `relaxed` — or, if even that fails, no trade and the
reason. "Don't sell anything this week" is a real answer.

Nothing here is manufactured. Missing inputs remove a term and say so; a
week with no strike worth selling returns "none" and says why.

Pure stdlib, no network, no pandas — every number below is reproducible
from its inputs, which is what makes it testable.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from statistics import NormalDist
from typing import Any, Sequence

SCHEMA = "weekly_sell/v1"

# ── Knobs. Every one of these is a stated policy, not a magic constant. ────
MAX_ITM = 0.15             # THE SELL ZONE. A strike qualifies when this
                           # stock FINISHED through it in no more than 15%
                           # of matched windows. Assignment is what costs
                           # money, so the gate is measured on where price
                           # closed, not where it poked to.
RELAXED_ITM = 0.25         # When nothing clears 15% — a jumpy tape, a thin
                           # chain — the engine says so and shows the best
                           # strike at this looser line rather than going
                           # blank. The pick is tagged `relaxed`.
TARGET_TOUCH = 0.10        # reported per strike, never a gate: how often the
                           # level was merely reached. Touching scares you;
                           # finishing through assigns you.
TYPICAL_P = 0.25           # the ordinary week: where the middle half of
                           # matched windows actually CLOSED. Same measure as
                           # the zone, one line looser, so it always sits
                           # inside it instead of crossing it confusingly.
MIN_CREDIT = 0.05          # below a nickel the trade is commission and risk
MIN_CREDIT_YIELD = 0.001   # ...and a nickel is a different trade on a $12
                           # stock than on a $600 one, so the credit must
                           # also clear 0.10% of the collateral it ties up.
                           # Without this the engine drifted to far-OTM
                           # lottery tickets on expensive names: safest
                           # score, no income, nobody would sell them.
MAX_SPREAD_PCT = 0.18      # bid/ask wider than this is not fillable
SPREAD_TICK_FLOOR = 0.05   # ...but a penny-wide market on a cheap far-OTM
                           # strike is normal and fillable. A 10c/13c quote
                           # is 26% of mid and a perfectly good trade, so the
                           # gate is the WIDER of the percentage and this
                           # absolute tolerance. Percent alone would throw
                           # away exactly the safe strikes the engine wants.
SPREAD_REF_PRICE = 0.20    # same idea for the score: spreads are measured
                           # against mid or this floor, whichever is larger.
MIN_OI = 10                # open interest floor
MIN_VOLUME = 10            # or today's volume floor (either one passes)
MIN_SAMPLE = 40            # fewer matched windows than this = no history leg
TOP_N = 3                  # alternates shown beside the pick
SCAN_RANGE = 0.60          # strikes further than this from spot are not
                           # scored at all. Wide enough that even a name
                           # running at triple-digit implied vol keeps its
                           # whole sell zone inside it; narrow enough that a
                           # 500-strike chain is not a full pass over every
                           # window for strikes nobody would ever sell.
REPORT_RANGE = 0.30        # strikes shipped for the panel's own lookups
EV_FULL_SCORE = 30.0       # annualized EV% that earns a full EV sub-score
INCOME_FULL_SCORE = 40.0   # annualized return on collateral that earns a
                           # full income sub-score
DAYS_PER_YEAR = 365.0
SESSIONS_PER_YEAR = 252.0

_ND = NormalDist()


# ── small helpers ─────────────────────────────────────────────────────────
def _f(v: Any) -> float | None:
    """float() that returns None instead of raising, and rejects NaN/inf."""
    try:
        if v is None:
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _as_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _is_trading_day(d: date) -> bool:
    """Weekday that is not a US equity market holiday. Falls back to a plain
    weekday test if the holiday calendar cannot be imported."""
    if d.weekday() >= 5:
        return False
    try:
        import capture_health
        return d not in capture_health.holidays(d.year)
    except Exception:
        return True


def sessions_until(expiry: Any, today: Any) -> int | None:
    """Trading sessions of risk between now and `expiry`, inclusive of today
    when today is itself a session — today can still move, so a seller owns
    it. Returns None when the expiry is unreadable, 0 when it has passed."""
    exp, now = _as_date(expiry), _as_date(today)
    if exp is None or now is None:
        return None
    if exp < now:
        return 0
    n, d = 0, now
    while d <= exp:
        if _is_trading_day(d):
            n += 1
        d += timedelta(days=1)
    return n


def percentile(values: Sequence[float], p: float) -> float | None:
    """Linear-interpolated percentile, p in [0, 1]. Plain and explicit so the
    number in the UI can be re-derived by hand from the same list."""
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    p = min(1.0, max(0.0, p))
    pos = p * (len(vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


# ── step 2: what this stock actually does ─────────────────────────────────
def forward_windows(bars: Sequence[dict], k: int, *,
                    drop_last_dated: date | None = None) -> list[dict]:
    """Replay every k-session window in the daily history.

    For each bar t that has k complete bars after it, returns the worst DOWN
    travel, the worst UP travel and the terminal close of those k sessions,
    each as a fraction of the close at t. Windows overlap — that is on
    purpose: it uses every observation the history contains, and the panel
    reports the count so the sample is never oversold as independent.

    `drop_last_dated` removes a final in-progress bar (today's, while the
    session is open) whose high/low/close are only part of a day.
    """
    if not bars or not k or k < 1:
        return []
    rows = []
    for b in bars:
        c = _f(b.get("close"))
        h = _f(b.get("high"))
        lo = _f(b.get("low"))
        d = _as_date(b.get("date"))
        if c is None or h is None or lo is None or c <= 0:
            continue
        rows.append({"date": d, "close": c, "high": h, "low": lo})
    if drop_last_dated is not None and rows and rows[-1]["date"] == drop_last_dated:
        rows.pop()
    out = []
    for i in range(len(rows) - k):
        c0 = rows[i]["close"]
        seg = rows[i + 1: i + 1 + k]
        if len(seg) < k:
            break
        out.append({
            "date": rows[i]["date"],
            "low": min(s["low"] for s in seg) / c0 - 1.0,
            "high": max(s["high"] for s in seg) / c0 - 1.0,
            "term": seg[-1]["close"] / c0 - 1.0,
        })
    return out


def history_band(windows: Sequence[dict], p: float) -> dict:
    """The level only `p` of matched windows REACHED, each side — the path
    band. This is what "it got tested" means."""
    return {
        "low": percentile([w["low"] for w in windows], p),
        "high": percentile([w["high"] for w in windows], 1.0 - p),
    }


def terminal_band(windows: Sequence[dict], p: float) -> dict:
    """The level only `p` of matched windows CLOSED beyond, each side — the
    assignment band, and the one the sell zone is built on."""
    return {
        "low": percentile([w["term"] for w in windows], p),
        "high": percentile([w["term"] for w in windows], 1.0 - p),
    }


# ── step 3: what the market is charging ───────────────────────────────────
def implied_band(sigma_k: float | None, p: float, *, touch: bool = True) -> dict:
    """Lognormal band at probability `p` for a horizon whose standard
    deviation is `sigma_k` (already time-scaled).

    With `touch`, the band is the level reached at some point with
    probability p. A driftless walk touches a barrier about twice as often
    as it closes beyond it, so the terminal quantile at p/2 is used — which
    is what makes this comparable to the historical path band rather than
    roughly half as wide.
    """
    if not sigma_k or sigma_k <= 0 or not (0 < p < 1):
        return {"low": None, "high": None}
    q = p / 2.0 if touch else p
    z = _ND.inv_cdf(q)                    # negative for q < 0.5
    return {"low": math.exp(z * sigma_k) - 1.0,
            "high": math.exp(-z * sigma_k) - 1.0}


def sigma_for_sessions(iv_annual: float | None, k: int) -> float | None:
    """Annual implied vol scaled to a k-session horizon."""
    iv = _f(iv_annual)
    if iv is None or iv <= 0 or not k:
        return None
    if iv > 3:                            # chains quote 42 or 0.42
        iv /= 100.0
    return iv * math.sqrt(k / SESSIONS_PER_YEAR)


# ── step 4: score every strike the chain offers ───────────────────────────
def _mid(o: dict) -> float | None:
    bid, ask = _f(o.get("bid")) or 0.0, _f(o.get("ask")) or 0.0
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    last = _f(o.get("last")) or 0.0
    return (bid or ask or last) or None


def liquidity(o: dict, mid: float | None) -> dict:
    """How fillable this strike is: the spread first, then the crowd."""
    bid, ask = _f(o.get("bid")) or 0.0, _f(o.get("ask")) or 0.0
    oi = int(_f(o.get("openInterest")) or 0)
    vol = int(_f(o.get("volume")) or 0)
    spread = (ask - bid) if (bid > 0 and ask > 0) else None
    spread_pct = (spread / mid) if (spread is not None and mid and mid > 0) else None
    # Scored against mid or SPREAD_REF_PRICE, whichever is larger, so a
    # one-cent market on a 10c option is not graded as a 10% disaster.
    scored_pct = (spread / max(mid or 0.0, SPREAD_REF_PRICE)) if spread is not None else None
    s_score = 0.0 if scored_pct is None else max(0.0, min(100.0, 100.0 - scored_pct * 300.0))
    oi_score = min(100.0, math.log10(oi + 1) / 3.0 * 100.0)      # 1000 OI = full
    vol_score = min(100.0, math.log10(vol + 1) / 2.7 * 100.0)    # 500 vol = full
    score = s_score * 0.60 + oi_score * 0.25 + vol_score * 0.15
    grade = "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"
    return {"spread_pct": None if spread_pct is None else spread_pct * 100.0,
            "spread": spread, "oi": oi, "volume": vol,
            "score": score, "grade": grade}


def evaluate_strike(o: dict, side: str, spot: float, windows: Sequence[dict],
                    dte_cal: float, em_dollars: float | None) -> dict | None:
    """Every number the panel shows for one strike, on the real distribution.

    touch_pct  — matched windows whose worst travel reached the strike.
    itm_pct    — matched windows that FINISHED through the strike. This is
                 the assignment number; touch is the scare number.
    ev         — mean of (credit - shortfall past the strike at expiry) over
                 every matched window: what this trade paid on average, on
                 this stock's own history, per share.
    """
    strike = _f(o.get("strike"))
    mid = _mid(o)
    if strike is None or strike <= 0 or mid is None or mid <= 0 or not spot or spot <= 0:
        return None
    put = side == "put"
    credit = mid
    breakeven = strike - credit if put else strike + credit
    cushion_pct = ((spot - breakeven) / spot if put else (breakeven - spot) / spot) * 100.0
    # Cash-secured put: the cash actually tied up. Covered call: the shares.
    collateral = (strike - credit) if put else spot
    if collateral <= 0:
        return None

    n = len(windows)
    touch_n = itm_n = 0
    ev_sum = 0.0
    for w in windows:
        worst = spot * (1.0 + (w["low"] if put else w["high"]))
        term = spot * (1.0 + w["term"])
        if (worst <= strike) if put else (worst >= strike):
            touch_n += 1
        if (term < strike) if put else (term > strike):
            itm_n += 1
        loss = max(0.0, strike - term) if put else max(0.0, term - strike)
        ev_sum += credit - loss
    ev = (ev_sum / n) if n else None

    rbp_pct = credit / collateral * 100.0
    ann_pct = rbp_pct * (DAYS_PER_YEAR / dte_cal) if dte_cal and dte_cal > 0 else None
    ev_pct = (ev / collateral * 100.0) if ev is not None else None
    ev_ann_pct = (ev_pct * (DAYS_PER_YEAR / dte_cal)
                  if (ev_pct is not None and dte_cal and dte_cal > 0) else None)

    liq = liquidity(o, mid)
    delta = _f(o.get("delta"))
    p_otm = None
    if delta is not None and abs(delta) <= 1:
        p_otm = (1.0 - abs(delta)) * 100.0
    em_mult = None
    if em_dollars and em_dollars > 0:
        em_mult = abs(spot - breakeven) / em_dollars

    # ── the ranking. Expected value leads; safety, fillability and the
    # cushion against the market's own priced move follow.
    ev_score = 0.0 if ev_ann_pct is None else max(0.0, min(100.0, ev_ann_pct / EV_FULL_SCORE * 100.0))
    safe_score = 100.0 - (itm_n / n * 100.0 if n else 100.0)
    em_score = 0.0 if em_mult is None else max(0.0, min(100.0, em_mult * 66.0))
    # Income, separate from edge. On a fairly priced chain every strike has
    # about the same expected value, and without this term the tie broke
    # toward whichever strike was safest and emptiest — the one that pays
    # nothing. Between two strikes history likes equally, take the one that
    # actually pays.
    income_score = 0.0 if ann_pct is None else max(0.0, min(100.0, ann_pct / INCOME_FULL_SCORE * 100.0))
    parts = [(0.35, ev_score), (0.15, income_score),
             (0.25, safe_score), (0.15, liq["score"])]
    if em_mult is not None:
        parts.append((0.10, em_score))
    wsum = sum(w for w, _ in parts)
    score = sum(w * v for w, v in parts) / wsum if wsum else 0.0

    return {
        "strike": strike, "bid": _f(o.get("bid")), "ask": _f(o.get("ask")),
        "mid": mid, "credit": credit, "breakeven": breakeven,
        "cushion_pct": cushion_pct, "collateral": collateral,
        "rbp_pct": rbp_pct, "ann_pct": ann_pct,
        "touch_n": touch_n, "touch_pct": (touch_n / n * 100.0) if n else None,
        "itm_n": itm_n, "itm_pct": (itm_n / n * 100.0) if n else None,
        "sample": n,
        "ev": ev, "ev_pct": ev_pct, "ev_ann_pct": ev_ann_pct,
        # A cash-secured put's loss is cash. A covered call's "loss" is
        # upside given up on shares already owned — the same arithmetic,
        # a different sentence, and the panel must not confuse them.
        "ev_basis": "cash secured" if put else "vs holding the shares",
        "delta": delta, "p_otm": p_otm,
        "oi": liq["oi"], "volume": liq["volume"], "spread_pct": liq["spread_pct"],
        "spread": liq["spread"],
        "liq_score": liq["score"], "liq_grade": liq["grade"],
        "em_mult": em_mult, "score": score,
        "iv": _f(o.get("iv")), "theta": _f(o.get("theta")),
    }


def _gate(row: dict, side: str, max_itm: float) -> str | None:
    """Why this strike is not sellable, or None when it is.

    The two assignment tests are made on the very numbers the panel prints
    next to the strike — this stock's own history, and the option market's
    implied odds — so a reader can always see why a strike was refused. An
    earlier version compared the strike against an interpolated price line
    instead, which could refuse a strike whose printed history rate was
    comfortably inside the limit. Order matters: the first failure is the
    one reported.
    """
    limit = max_itm * 100.0
    if row["itm_pct"] is not None and row["itm_pct"] > limit:
        return f"history finished through it {row['itm_pct']:.0f}% of the time"
    if row["p_otm"] is not None and (100.0 - row["p_otm"]) > limit:
        return f"the option market puts assignment at {100.0 - row['p_otm']:.0f}%"
    if row["credit"] < MIN_CREDIT:
        return "pays less than a nickel"
    if row["rbp_pct"] is not None and row["rbp_pct"] < MIN_CREDIT_YIELD * 100.0:
        return "pays too little for the collateral it ties up"
    spread = row.get("spread")
    if spread is not None and spread > max(SPREAD_TICK_FLOOR, MAX_SPREAD_PCT * row["mid"]):
        return "bid/ask too wide to fill"
    if row["oi"] < MIN_OI and row["volume"] < MIN_VOLUME:
        return "almost no open interest or volume"
    return None


def _why(row: dict, side: str, band_source: str) -> list[str]:
    """The plain-English case for this strike, in the order that matters."""
    out = []
    if row["touch_pct"] is not None:
        out.append(f"history reached it in {row['touch_pct']:.0f}% of matched windows "
                   f"({row['touch_n']} of {row['sample']})")
    if row["itm_pct"] is not None:
        out.append(f"finished through it {row['itm_pct']:.0f}% of the time")
    if row["ev_ann_pct"] is not None:
        if side == "put":
            out.append(f"selling it averaged {row['ev_ann_pct']:+.0f}% a year on that history, "
                       f"on the cash it ties up")
        else:
            out.append(f"against simply holding the shares it averaged "
                       f"{row['ev_ann_pct']:+.0f}% a year on that history")
    if row["em_mult"] is not None:
        out.append(f"breakeven sits {row['em_mult']:.1f}× the market's own expected move away")
    out.append(f"fill quality {row['liq_grade']} "
               f"({row['oi']:,} open interest"
               + (f", {row['spread_pct']:.0f}% spread)" if row["spread_pct"] is not None else ")"))
    out.append("sell zone came from " + ("this stock's own history" if band_source == "history"
                                         else "the option market's implied move"))
    return out


# Fields the panel needs for any strike, gated or not, so the frontend can
# measure the user's OWN picked strike against the same history without a
# second round trip or a second implementation of this arithmetic.
# Fields constant across a side (sample size, the EV basis) are left out —
# they live once on the plan and the pick, not 80 times in this list.
_TRIM = ("strike", "mid", "credit", "breakeven", "cushion_pct", "touch_pct",
         "itm_pct", "ev_ann_pct", "ann_pct", "rbp_pct", "liq_grade", "oi",
         "delta", "p_otm", "score")


def _trim(row: dict, gate: str | None) -> dict:
    out = {k: row.get(k) for k in _TRIM}
    out["gate"] = gate
    return out


def pick_side(chain: Sequence[dict], side: str, spot: float, windows: Sequence[dict],
              dte_cal: float, max_itm: float, em_dollars: float | None,
              band_source: str, *, top_n: int = TOP_N) -> dict:
    """Score every strike on this side and return the winner plus alternates."""
    scored, rejected, every = [], {}, []
    for o in chain or []:
        # A strike halfway to zero can never be the pick, and scoring it
        # costs a full pass over every window. On a wide chain that is most
        # of the work for none of the answer.
        k = _f(o.get("strike"))
        if k is None or (spot and abs(k - spot) / spot > SCAN_RANGE):
            continue
        row = evaluate_strike(o, side, spot, windows, dte_cal, em_dollars)
        if row is None:
            continue
        reason = _gate(row, side, max_itm)
        if spot and abs(row["strike"] - spot) / spot <= REPORT_RANGE:
            every.append(_trim(row, reason))
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        scored.append(row)
    every.sort(key=lambda r: r["strike"])
    if not scored:
        near = None
        if rejected:
            near = max(rejected.items(), key=lambda kv: kv[1])[0]
        return {"pick": None, "alts": [], "all": every,
                "note": ("No strike beyond the sell zone is worth selling this expiry"
                         + (f" — {near}." if near else "."))}
    scored.sort(key=lambda r: (-r["score"], -(r["ev_ann_pct"] or 0)))
    best = dict(scored[0])
    best["why"] = _why(best, side, band_source)
    return {"pick": best, "alts": [_trim(r, None) for r in scored[1:1 + top_n]],
            "all": every, "note": None}


# ── walls: where the open interest actually sits ──────────────────────────
def oi_walls(calls: Sequence[dict], puts: Sequence[dict], spot: float) -> dict:
    """The heaviest open interest each side of spot — the levels dealers are
    hedging around, and the ones a seller likes to sit behind."""
    def heaviest(rows, below):
        best = None
        for o in rows or []:
            k, oi = _f(o.get("strike")), int(_f(o.get("openInterest")) or 0)
            if k is None or oi <= 0:
                continue
            if below and k > spot:
                continue
            if not below and k < spot:
                continue
            if best is None or oi > best["oi"]:
                best = {"strike": k, "oi": oi}
        return best
    return {"put": heaviest(puts, True), "call": heaviest(calls, False)}


# ── the whole plan, in one call ───────────────────────────────────────────
def atm_iv(calls: Sequence[dict], puts: Sequence[dict], spot: float) -> float | None:
    """Implied vol at the money: the average of the call and put IV at the
    strike nearest spot. This is the market's own read on the horizon."""
    def nearest_iv(rows):
        best, bd = None, None
        for o in rows or []:
            k, iv = _f(o.get("strike")), _f(o.get("iv"))
            if k is None or iv is None or iv <= 0:
                continue
            d = abs(k - spot)
            if bd is None or d < bd:
                best, bd = iv, d
        return best
    ivs = [v for v in (nearest_iv(calls), nearest_iv(puts)) if v]
    return sum(ivs) / len(ivs) if ivs else None


def atm_straddle(calls: Sequence[dict], puts: Sequence[dict], spot: float) -> float | None:
    """The at-the-money straddle mid — what the option market is charging
    for the move to this expiry, in dollars, quoted rather than modelled."""
    def nearest(rows):
        best, bd = None, None
        for o in rows or []:
            k = _f(o.get("strike"))
            if k is None:
                continue
            d = abs(k - spot)
            if bd is None or d < bd:
                best, bd = o, d
        return best
    c, p = nearest(calls), nearest(puts)
    if not c or not p:
        return None
    cm, pm = _mid(c), _mid(p)
    if not cm or not pm:
        return None
    return cm + pm


def _widen(hist: float | None, impl: float | None, low: bool) -> tuple[float | None, str]:
    """Keep the more cautious of the two legs, and say which one it was."""
    if hist is None and impl is None:
        return None, "none"
    if hist is None:
        return impl, "implied"
    if impl is None:
        return hist, "history"
    pick_hist = (hist <= impl) if low else (hist >= impl)
    return (hist, "history") if pick_hist else (impl, "implied")


def build_plan(*, spot: float, bars: Sequence[dict], calls: Sequence[dict],
               puts: Sequence[dict], expiration: Any, now: Any = None,
               earnings_date: Any = None, max_itm: float = MAX_ITM,
               target_touch: float = TARGET_TOUCH,
               user_put: dict | None = None, user_call: dict | None = None,
               em_band: dict | None = None) -> dict:
    """The Weekly Option Selling Setup's whole answer for one expiry."""
    now_dt = now if isinstance(now, datetime) else datetime.now()
    today = now_dt.date()
    out: dict[str, Any] = {"schema": SCHEMA, "ok": False, "note": None,
                           "expiration": None, "target_itm_pct": max_itm * 100.0}
    spot = _f(spot) or 0.0
    exp_d = _as_date(expiration)
    if spot <= 0 or exp_d is None:
        out["note"] = "No live price or expiry to measure from."
        return out
    out["expiration"] = exp_d.isoformat()

    k = sessions_until(exp_d, today) or 0
    out["sessions"] = k
    exp_close = datetime.combine(exp_d, datetime.min.time()) + timedelta(hours=16)
    dte_cal = max(0.05, round((exp_close - now_dt).total_seconds() / 86400.0, 2))
    out["dte_cal"] = dte_cal
    if k < 1:
        out["note"] = "This expiry has no sessions left to measure."
        return out

    # ── step 2: this stock's own k-session outcomes
    windows = forward_windows(bars or [], k, drop_last_dated=today)
    n = len(windows)
    enough = n >= MIN_SAMPLE
    blank = {"low": None, "high": None}
    term = terminal_band(windows, max_itm) if enough else blank
    term_rx = terminal_band(windows, RELAXED_ITM) if enough else blank
    typical = terminal_band(windows, TYPICAL_P) if enough else blank
    out["sample"] = {
        "n": n, "sessions": k, "enough": enough,
        "from": windows[0]["date"].isoformat() if n and windows[0]["date"] else None,
        "to": windows[-1]["date"].isoformat() if n and windows[-1]["date"] else None,
    }

    # ── step 3: the same band from the option market, at the same odds
    iv = atm_iv(calls, puts, spot)
    sigma_k = sigma_for_sessions(iv, k)
    impl = implied_band(sigma_k, max_itm, touch=False)
    impl_rx = implied_band(sigma_k, RELAXED_ITM, touch=False)

    # ── the sell zone: whichever leg is more cautious, each side alone
    low_pct, low_src = _widen(term["low"], impl["low"], low=True)
    high_pct, high_src = _widen(term["high"], impl["high"], low=False)
    rx_low, _ = _widen(term_rx["low"], impl_rx["low"], low=True)
    rx_high, _ = _widen(term_rx["high"], impl_rx["high"], low=False)
    if low_pct is None and high_pct is None:
        out["note"] = ("Not enough daily history or implied volatility to measure "
                       f"a {k}-session move.")
        return out
    def lvl(pct):
        """A percentage of spot as a price."""
        return None if pct is None else spot * (1 + pct)

    zone_low, zone_high = lvl(low_pct), lvl(high_pct)
    out["band"] = {
        "target_itm_pct": max_itm * 100.0,
        # The looser line, published so the panel can name it when a pick
        # comes back relaxed. It is never the gate — the gate is per strike.
        "relaxed_low_pct": None if rx_low is None else rx_low * 100.0,
        "relaxed_high_pct": None if rx_high is None else rx_high * 100.0,
        "relaxed_itm_pct": RELAXED_ITM * 100.0,
        "zone": {
            "low": {"price": zone_low, "pct": None if low_pct is None else low_pct * 100.0,
                    "source": low_src},
            "high": {"price": zone_high, "pct": None if high_pct is None else high_pct * 100.0,
                     "source": high_src},
        },
        "typical": {
            "low": {"price": lvl(typical["low"]),
                    "pct": None if typical["low"] is None else typical["low"] * 100.0},
            "high": {"price": lvl(typical["high"]),
                     "pct": None if typical["high"] is None else typical["high"] * 100.0},
        },
        "history": {"low": None if term["low"] is None else term["low"] * 100.0,
                    "high": None if term["high"] is None else term["high"] * 100.0},
        "implied": {"low": None if impl["low"] is None else impl["low"] * 100.0,
                    "high": None if impl["high"] is None else impl["high"] * 100.0},
        "touch_target_pct": target_touch * 100.0,
    }

    # ── the market's priced move, for the cushion term
    straddle = atm_straddle(calls, puts, spot)
    em_dollars, em_src = None, None
    if em_band and _f(em_band.get("high")) and _f(em_band.get("low")):
        em_dollars = (abs(_f(em_band["high"]) - spot) + abs(spot - _f(em_band["low"]))) / 2.0
        em_src = "engine"
    elif straddle:
        # The at-the-money straddle mid — the option market's own quoted
        # price for the move to this expiry, and the number every other
        # panel in this app calls the expected move. Roughly 0.8 of a
        # standard deviation; the fallback below is scaled to match so the
        # two sources never mean different things.
        em_dollars, em_src = straddle, "straddle"
    elif sigma_k:
        em_dollars, em_src = spot * sigma_k * 0.8, "implied vol"
    out["em"] = {"dollars": em_dollars, "source": em_src,
                 "pct": (em_dollars / spot * 100.0) if em_dollars else None}

    # ── step 4: score the chain
    def side_plan(chain, side, src):
        res = pick_side(chain, side, spot, windows, dte_cal, max_itm, em_dollars, src)
        if res["pick"] is not None:
            return res
        # Nothing cleared the 15% line. Rather than go blank, show the best
        # strike at the looser line and label it plainly as the compromise.
        soft = pick_side(chain, side, spot, windows, dte_cal, RELAXED_ITM, em_dollars, src)
        if soft["pick"] is None:
            return res
        soft["pick"]["relaxed"] = True
        soft["relaxed"] = True
        soft["note"] = (f"No strike cleared the {max_itm * 100:.0f}% assignment line. "
                        f"This is the best one at {RELAXED_ITM * 100:.0f}%.")
        return soft

    out["put"] = side_plan(puts, "put", low_src)
    out["call"] = side_plan(calls, "call", high_src)

    # the user's own picked strikes, measured the same way for comparison
    for key, src, side in (("put", user_put, "put"), ("call", user_call, "call")):
        if src and _f(src.get("strike")):
            row = evaluate_strike(src, side, spot, windows, dte_cal, em_dollars)
            if row:
                row["gate"] = _gate(row, side, max_itm)
                out[key]["user"] = row

    out["walls"] = oi_walls(calls, puts, spot)

    # ── earnings inside the horizon is a different game entirely
    ed = _as_date(earnings_date)
    out["earnings"] = {"date": ed.isoformat() if ed else None,
                       "inside": bool(ed and today <= ed <= exp_d)}

    # ── the verdict
    pp, cp = out["put"].get("pick"), out["call"].get("pick")
    if pp and cp:
        lead = "put" if pp["score"] >= cp["score"] else "call"
        other = cp if lead == "put" else pp
        best = pp if lead == "put" else cp
        gap = abs(pp["score"] - cp["score"])
        side_word = "Put side pays better" if lead == "put" else "Call side pays better"
        out["verdict"] = {
            "side": lead if gap >= 4 else "both",
            "line": (f"{side_word} here" if gap >= 4 else "Both sides are close"),
            "best": best["strike"], "other": other["strike"],
        }
    elif pp or cp:
        lead = "put" if pp else "call"
        out["verdict"] = {"side": lead, "line": f"Only the {lead} side clears the floor",
                          "best": (pp or cp)["strike"], "other": None}
    else:
        out["verdict"] = {"side": "none", "line": "Nothing worth selling this expiry",
                          "best": None, "other": None}
    if out["earnings"]["inside"]:
        out["verdict"]["line"] += " — but earnings land inside this expiry"

    out["ok"] = bool(pp or cp)
    if not out["ok"] and not out["note"]:
        out["note"] = out["put"].get("note") or out["call"].get("note")
    return out
