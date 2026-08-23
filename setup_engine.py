"""setup_engine.py — one explained recommendation from every layer the app
already computes.

Pure functions only: no network, no disk, no clock. `setup_scan.py` gathers
the blocks from the modules that own them and calls in here; the Best Setup
card renders what comes out. Nothing in this file re-derives a number another
module already produces — it decides what they mean together.

═══════════════════════════════════════════════════════════════════════════
THE ONE THING THIS MODULE EXISTS TO GET RIGHT
═══════════════════════════════════════════════════════════════════════════

DELTA IS ALREADY A PROBABILITY. A 15-delta short call is the market saying
"about 15% chance this finishes in the money". Selling a 40-delta call
instead does not keep that win rate and add premium; it lowers the win rate
to roughly 60% and pays you for the difference. That is arithmetic, and no
amount of pattern, streak or gamma evidence changes it.

So there is exactly ONE honest reason to sell closer to the money: the
market's probability is wrong for THIS symbol in THIS state. Delta is a
RISK-NEUTRAL probability, priced off implied volatility. What actually
happens is a REAL-WORLD probability. When a stock's implied volatility
persistently exceeds what it goes on to realize, the two differ, and the
gap is real money.

This module therefore never reasons "the move looks extended, so sell a
higher delta". It asks a measurable question instead:

    In this state, how often did this stock actually travel far enough to
    reach that strike inside the life of the option — and how does that
    compare to what the option's delta implies?

Two independent answers are used, and both carry their sample size:

  MEASURED   swing_projection.horizon_hit / baseline_hit_rate — did price
             move X% within N trading days from a bar in this state, versus
             the same question asked of every ordinary bar. Real outcomes,
             this symbol, overlapping windows counted honestly.
  MODELLED   premium_edge's p_itm_model / p_touch_model — a driftless
             lognormal at ExpectedRV rather than at implied volatility.
             Already the real-world distribution, already validated.

A higher delta is recommended only when BOTH point the same way and the
measured sample is big enough to mean anything. Otherwise the engine returns
the conservative default band and says, in the risks, exactly which evidence
it did not have. An engine that quietly sizes up on thin data does not
improve a win rate; it just takes longer to find out.

═══════════════════════════════════════════════════════════════════════════
WHAT GAMMA EXPOSURE IS ALLOWED TO DO
═══════════════════════════════════════════════════════════════════════════

Never a standalone signal. Dealer positioning is not published; every GEX
figure is open interest times gamma times an assumption (see gex_engine).
It is used here only as a STRUCTURE modifier on a trade the other layers
already justify:

  supports   a positive-gamma wall sits between spot and the short strike —
             dealer hedging leans against price reaching it
  neutral    no meaningful concentration in the way
  opposes    the path to the strike runs through negative gamma, where
             hedging amplifies rather than damps the move
  veto       spot is below the flip AND the strike sits in negative gamma
             close by — the case where an adverse move can accelerate

`opposes` costs confidence and pulls the delta ceiling back. `veto` refuses
the trade outright rather than quietly discounting it.
"""

from __future__ import annotations

import math

SETUP_VERSION = "setup-engine-1.0.0"

# ── policy ────────────────────────────────────────────────────────────────
# The default band, and the floor the engine returns to whenever evidence is
# missing. It is Jerry's own 15-20 delta rule, which works because it is a
# high-probability sale; nothing here is allowed to leave it without a
# measured reason.
DEFAULT_DELTA_BAND = (0.15, 0.22)
# The hardest the engine may ever push, even on excellent evidence. A short
# option at 0.45 delta is close to a coin flip on direction; past that the
# trade is a directional bet wearing a premium-selling costume.
MAX_DELTA_CEILING = 0.45
# The win rate the delta ceiling is solved for. This is the promise the
# recommendation is making, and it is held CONSTANT while the strike moves —
# which is the whole point: more premium at the same measured odds, not more
# premium at odds nobody stated.
TARGET_WIN_PCT = 85.0
# Below this many observations a measured rate is a coincidence with a
# decimal point. The engine reports it and refuses to act on it.
MIN_MEASURED_N = 30
# How much better than the unconditional baseline the conditional rate must
# be before "this state is different" is a claim rather than noise.
MIN_EDGE_POINTS = 5.0


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (f != f or math.isinf(f)) else f


def _pct(v, lo=0.0, hi=100.0):
    f = _num(v)
    return None if f is None else max(lo, min(hi, f))


def wilson_low(hits: int, n: int, z: float = 1.96) -> float | None:
    """Lower bound of the Wilson interval, as a percentage.

    The engine sizes on the LOWER bound, never the point estimate. Nine wins
    from ten tries is a 90% point estimate and a 59% lower bound; the second
    number is the one that should decide how much of your money is at risk.
    """
    if not n or n <= 0 or hits < 0 or hits > n:
        return None
    p = hits / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / d) * 100.0


# ══════════════════════════════════════════════════════════════════════════
# DIRECTIONAL BIAS — which side is statistically stretched
# ══════════════════════════════════════════════════════════════════════════

def directional_bias(range_block=None, streak_block=None,
                     swing_block=None) -> dict:
    """Which side of this stock looks stretched, and how much the layers
    agree.

    This is NOT a price forecast, and it never becomes one. Each contributor
    answers the same narrow question — "is the recent move long in the tooth
    by this stock's own record" — and votes with its own weight and its own
    sample size. The output biases WHICH SIDE to sell, never whether the
    stock is going up or down.

    `lean` is "fade_up" (prefer selling calls into a stretched advance),
    "fade_down" (prefer selling puts into a stretched decline), or None.
    """
    votes = []

    # ── where this week sits in the stock's own weekly range ──────────────
    rb = range_block or {}
    pos = _pct(rb.get("pos"))
    if pos is not None:
        if pos >= 75:
            votes.append({"source": "range", "lean": "fade_up",
                          "weight": (pos - 75) / 25.0,
                          "n": rb.get("weeks"),
                          "why": (f"This week sits at {pos:.0f}% of the stock's own "
                                  f"weekly range — near the top of what it normally "
                                  f"manages in a week.")})
        elif pos <= 25:
            votes.append({"source": "range", "lean": "fade_down",
                          "weight": (25 - pos) / 25.0,
                          "n": rb.get("weeks"),
                          "why": (f"This week sits at {pos:.0f}% of the stock's own "
                                  f"weekly range — near the bottom of what it "
                                  f"normally manages in a week.")})

    # ── consecutive up/down days against this stock's own record ─────────
    sb = streak_block or {}
    sdir, scount = sb.get("streak_dir"), sb.get("streak_count") or 0
    longest = sb.get("longest_up") if sdir == 1 else sb.get("longest_down")
    times = sb.get("streak_times_before")
    wr = _pct(sb.get("streak_winrate"))
    if sdir in (1, -1) and scount >= 3:
        # Weight by how far into the tail of its OWN distribution it is, not
        # by an absolute run length: four up days is unremarkable for one
        # stock and a record for another.
        stretch = (scount / longest) if longest else 0.0
        votes.append({
            "source": "streak",
            "lean": "fade_up" if sdir == 1 else "fade_down",
            "weight": max(0.0, min(1.0, (stretch - 0.5) * 2.0)),
            "n": times,
            "why": (f"{scount} {'up' if sdir == 1 else 'down'} days in a row"
                    + (f", against a record of {longest}" if longest else "")
                    + (f". The {times} prior runs that got this far went on to "
                       f"continue {wr:.0f}% of the time." if (times and wr is not None)
                       else ". No prior run of this length to learn from.")),
        })

    # ── the swing's own maturity, from the reversal engine ───────────────
    wb = swing_block or {}
    mat = (wb.get("maturity") or {})
    code = mat.get("code")
    direction = wb.get("direction")
    coh = (wb.get("cohort") or {})
    if code == "BEYOND ITS NORMAL SIZE" and direction in ("up", "down"):
        votes.append({
            "source": "swing", "lean": "fade_up" if direction == "up" else "fade_down",
            "weight": 1.0, "n": coh.get("n"),
            "why": (f"The active {direction} swing is beyond the size this stock's "
                    f"swings normally reach"
                    + (f" ({mat.get('ratio_pct'):.0f}% of its usual)."
                       if _num(mat.get("ratio_pct")) else ".")),
        })
    elif code == "AT ITS NORMAL SIZE" and direction in ("up", "down"):
        votes.append({
            "source": "swing", "lean": "fade_up" if direction == "up" else "fade_down",
            "weight": 0.5, "n": coh.get("n"),
            "why": f"The active {direction} swing has reached its normal size.",
        })

    if not votes:
        return {"lean": None, "strength": 0.0, "agreement": None, "votes": [],
                "why": ["No layer reports this stock as stretched in either "
                        "direction right now."]}

    up = sum(v["weight"] for v in votes if v["lean"] == "fade_up")
    down = sum(v["weight"] for v in votes if v["lean"] == "fade_down")
    lean = "fade_up" if up > down else "fade_down" if down > up else None
    strength = abs(up - down) / max(1e-9, up + down) if (up + down) else 0.0
    # Agreement counts only the layers that actually spoke. Two layers
    # pointing the same way with a third silent is agreement; two pointing
    # opposite ways is not, whatever the weights say.
    leaning = [v for v in votes if v["lean"]]
    same = sum(1 for v in leaning if v["lean"] == lean)
    agreement = round(same / len(leaning) * 100.0, 0) if leaning else None
    return {
        "lean": lean,
        "strength": round(strength, 3),
        "agreement": agreement,
        "votes": votes,
        "sources": sorted({v["source"] for v in leaning}),
        "why": [v["why"] for v in votes],
        "conflict": agreement is not None and agreement < 100.0,
    }


# ══════════════════════════════════════════════════════════════════════════
# THE DELTA CEILING — the only place a higher delta can be earned
# ══════════════════════════════════════════════════════════════════════════

def measured_touch(conditional: dict | None, baseline: dict | None,
                   min_n: int = MIN_MEASURED_N,
                   min_edge: float = MIN_EDGE_POINTS) -> dict:
    """Turn measured touch rates into a verdict on whether this state is
    genuinely calmer than the stock's ordinary behaviour.

    `conditional` and `baseline` are each {distance_pct: {"rate": r, "n": n}}
    — the share of windows in which price travelled that far within the
    option's life, in this state and from any bar respectively.

    The verdict is deliberately hard to earn. It needs a real sample, and it
    needs the conditional rate to beat the baseline by more than a rounding
    error. Anything less and the answer is "no evidence", which sends the
    caller back to the default band.
    """
    cond, base = conditional or {}, baseline or {}
    rows = []
    for key in sorted(cond, key=lambda k: _num(k) or 0.0):
        c, b = cond.get(key) or {}, base.get(key) or {}
        c_rate, c_n = _pct(c.get("rate")), int(c.get("n") or 0)
        b_rate = _pct(b.get("rate"))
        if c_rate is None or c_n <= 0:
            continue
        hits = int(round(c_rate / 100.0 * c_n))
        # The share that did NOT travel that far is the seller's win rate, so
        # the conservative bound is the Wilson LOWER bound on not touching.
        keep_low = wilson_low(c_n - hits, c_n)
        rows.append({
            "distance_pct": _num(key),
            "touch_pct": round(c_rate, 1), "n": c_n,
            "baseline_touch_pct": round(b_rate, 1) if b_rate is not None else None,
            "edge_points": (round(b_rate - c_rate, 1) if b_rate is not None else None),
            "keep_pct": round(100.0 - c_rate, 1),
            "keep_pct_low": round(keep_low, 1) if keep_low is not None else None,
        })
    if not rows:
        return {"usable": False, "rows": [],
                "reason": ("No measured history for how far this stock travels "
                           "in this state over this horizon.")}
    best_n = max(r["n"] for r in rows)
    if best_n < min_n:
        return {"usable": False, "rows": rows, "max_n": best_n,
                "reason": (f"Only {best_n} comparable windows in this state — "
                           f"fewer than the {min_n} needed before a measured "
                           f"rate is worth acting on.")}
    edges = [r["edge_points"] for r in rows if r["edge_points"] is not None]
    mean_edge = (sum(edges) / len(edges)) if edges else None
    calmer = mean_edge is not None and mean_edge >= min_edge
    return {
        "usable": True, "rows": rows, "max_n": best_n,
        "mean_edge_points": round(mean_edge, 1) if mean_edge is not None else None,
        "calmer_than_usual": calmer,
        "reason": (
            f"In this state the stock reached these distances "
            f"{abs(mean_edge):.0f} points {'less' if mean_edge >= 0 else 'more'} "
            f"often than it does from an ordinary bar, over {best_n} windows."
            if mean_edge is not None else
            f"Measured over {best_n} windows in this state; no baseline to "
            f"compare against."),
    }


def required_distance(measured: dict, target_win_pct: float = TARGET_WIN_PCT) -> dict:
    """How far out of the money the strike must sit for the MEASURED keep
    rate to clear the target, interpolated between measured distances.

    This deliberately returns a DISTANCE, not a delta, and the reason is
    worth stating because the obvious alternative is circular. Converting a
    measured keep rate into an "implied delta" of (1 − keep) can only ever
    return (1 − target), whatever the data says — the target is the input.
    It looks like analysis and computes nothing.

    A distance is different, because the MARKET independently quotes a delta
    at that distance. That is where an edge can actually show up:

        history:  price stays inside 5.9% about 85% of the time in this state
        market:   the strike 5.9% away is quoted at 0.34 delta
        meaning:  the market prices 34% risk where history measured 15%

    The recommendation then sells the market's 0.34-delta strike while still
    holding an 85% measured target — more premium at the same odds. That is
    the only mechanism by which this feature can do what it claims, and it
    is entirely contingent on the measured sample being real.
    """
    if not measured.get("usable"):
        return {"ok": False, "reason": measured.get("reason") or "No measured evidence.",
                "n": measured.get("max_n"), "target_win_pct": target_win_pct}
    if not measured.get("calmer_than_usual"):
        return {"ok": False, "basis": "measured-no-edge",
                "n": measured.get("max_n"), "target_win_pct": target_win_pct,
                "reason": ("This state is not measurably calmer than this stock's "
                           "ordinary behaviour, so there is nothing to justify "
                           "selling closer to the money.")}
    rows = [r for r in measured["rows"]
            if r.get("keep_pct_low") is not None and r.get("distance_pct") is not None]
    rows.sort(key=lambda r: abs(r["distance_pct"]))
    if not rows:
        return {"ok": False, "reason": "No usable measured distances.",
                "target_win_pct": target_win_pct}
    # Walk outward to the first distance that clears the target, then step
    # BACK and interpolate: the answer usually sits between two measured
    # points, and rounding out to the next one throws away real premium.
    prev = None
    for r in rows:
        if r["keep_pct_low"] >= target_win_pct:
            if prev is None:
                # Even the closest measured distance already clears it.
                return {"ok": True, "distance_pct": abs(r["distance_pct"]),
                        "keep_pct_low": r["keep_pct_low"], "n": r["n"],
                        "interpolated": False, "target_win_pct": target_win_pct,
                        "why": (f"Over {r['n']} windows in this state, price stayed "
                                f"inside {abs(r['distance_pct']):.1f}% of spot "
                                f"{r['keep_pct_low']:.0f}% of the time on the "
                                f"conservative bound.")}
            span = r["keep_pct_low"] - prev["keep_pct_low"]
            t = 0.0 if span <= 0 else (target_win_pct - prev["keep_pct_low"]) / span
            t = max(0.0, min(1.0, t))
            d = abs(prev["distance_pct"]) + t * (abs(r["distance_pct"]) - abs(prev["distance_pct"]))
            return {"ok": True, "distance_pct": round(d, 2),
                    "keep_pct_low": target_win_pct, "n": min(prev["n"], r["n"]),
                    "interpolated": True, "target_win_pct": target_win_pct,
                    "bracket": [abs(prev["distance_pct"]), abs(r["distance_pct"])],
                    "why": (f"Over {min(prev['n'], r['n'])} windows in this state, "
                            f"price stayed inside {d:.1f}% of spot about "
                            f"{target_win_pct:.0f}% of the time on the conservative "
                            f"bound — interpolated between the "
                            f"{abs(prev['distance_pct']):.1f}% and "
                            f"{abs(r['distance_pct']):.1f}% measurements.")}
        prev = r
    widest = rows[-1]
    return {"ok": False, "basis": "measured-below-target",
            "n": widest["n"], "target_win_pct": target_win_pct,
            "widest_measured_pct": abs(widest["distance_pct"]),
            "widest_keep_pct_low": widest["keep_pct_low"],
            "reason": (f"Even at {abs(widest['distance_pct']):.1f}% out, the measured "
                       f"keep rate is only {widest['keep_pct_low']:.0f}% on the "
                       f"conservative bound — short of the "
                       f"{target_win_pct:.0f}% target, so the default band stands.")}


def delta_ceiling(measured: dict, model_touch_pct=None,
                  target_win_pct: float = TARGET_WIN_PCT,
                  default_band=DEFAULT_DELTA_BAND,
                  hard_cap: float = MAX_DELTA_CEILING) -> dict:
    """The eligibility rule for strikes, given the measured evidence.

    Returns the default delta band untouched unless the measured sample is
    real AND says this state is calmer than the stock's ordinary behaviour.
    When it does, the band opens up to `hard_cap` and a `min_distance_pct`
    is set: a strike is then eligible if it sits AT LEAST that far out of
    the money, whatever delta the market happens to quote it at.

    That is the inversion that makes the feature work. The default band is a
    guess about probability; the distance floor is a measurement of it.
    """
    band = (round(default_band[0], 3), round(default_band[1], 3))
    req = required_distance(measured, target_win_pct)
    if not req.get("ok"):
        return {"band": band, "raised": False,
                "basis": req.get("basis", "default"),
                "target_win_pct": target_win_pct, "n": req.get("n"),
                "min_distance_pct": None,
                "why": req.get("reason") or "No measured evidence."}
    # The model's own touch probability is the second opinion. If it says
    # this distance is riskier than the measurement does, the tighter of the
    # two governs — the trade only gets the benefit both methods agree on.
    mt = _pct(model_touch_pct)
    model_conflict = None
    if mt is not None and (100.0 - mt) < target_win_pct:
        model_conflict = (f"The model puts the chance of touching this distance at "
                          f"{mt:.0f}%, which is worse than the "
                          f"{100 - target_win_pct:.0f}% the measurement implies. The "
                          f"delta band stays at the default until the two agree.")
        return {"band": band, "raised": False, "basis": "model-disagrees",
                "target_win_pct": target_win_pct, "n": req.get("n"),
                "min_distance_pct": req["distance_pct"],
                "model_touch_pct": mt, "why": model_conflict}
    return {
        "band": (band[0], round(hard_cap, 3)),
        "raised": True, "basis": "measured",
        "target_win_pct": target_win_pct,
        "min_distance_pct": req["distance_pct"],
        "keep_pct_low": req["keep_pct_low"], "n": req["n"],
        "interpolated": req.get("interpolated"),
        "hard_cap": hard_cap, "model_touch_pct": mt,
        "why": (req["why"] + f" Any strike at least {req['distance_pct']:.1f}% out is "
                f"therefore eligible, whatever delta the market quotes it at — the "
                f"gap between that delta and the measured risk is the edge."),
    }


# ══════════════════════════════════════════════════════════════════════════
# GAMMA EXPOSURE AS A STRUCTURE MODIFIER
# ══════════════════════════════════════════════════════════════════════════

def gex_context(gex_block: dict | None, spot, strike, side: str,
                wall_share: float = 0.25) -> dict:
    """What dealer positioning does to the PATH between spot and the strike.

    A short seller cares about one thing here: to lose, price has to travel
    from spot to the strike. If the biggest positive-gamma concentration on
    that path sits between the two, dealer hedging leans against the journey.
    If the path runs through negative gamma, hedging pushes with it.

    `wall_share` is how large a strike's net exposure must be, relative to
    the largest on the board, before it counts as a wall rather than noise.
    """
    g = gex_block or {}
    s, k = _num(spot), _num(strike)
    if not g.get("ok") or s is None or k is None or s <= 0:
        return {"verdict": "unknown", "note": ("No usable gamma exposure for this "
                                               "underlying, so market structure is "
                                               "not part of this recommendation."),
                "available": False}
    strikes = g.get("strikes") or []
    summary = g.get("summary") or {}
    prof = g.get("profile") or {}
    flip = _num(prof.get("flip"))
    lo, hi = (min(s, k), max(s, k))
    on_path = [r for r in strikes
               if lo <= (_num(r.get("strike")) or -1) <= hi]
    biggest = max((abs(_num(r.get("net_gex")) or 0.0) for r in strikes), default=0.0)
    threshold = biggest * wall_share
    pos_wall = max((r for r in on_path if (_num(r.get("net_gex")) or 0) > threshold),
                   key=lambda r: r["net_gex"], default=None)
    neg_wall = min((r for r in on_path if (_num(r.get("net_gex")) or 0) < -threshold),
                   key=lambda r: r["net_gex"], default=None)
    regime = summary.get("regime")
    below_flip = flip is not None and s < flip

    # The refusal case: dealers amplifying moves AND a negative-gamma
    # concentration sitting on the exact path price would take to hurt this
    # trade. That is the setup where an adverse move accelerates.
    if below_flip and neg_wall is not None and pos_wall is None:
        return {
            "verdict": "veto", "available": True, "regime": regime, "flip": flip,
            "negative_wall": neg_wall.get("strike"),
            "note": (f"Spot is below the estimated gamma flip of {flip:.2f}, where "
                     f"dealer hedging amplifies moves rather than damping them, and "
                     f"the largest exposure between here and the strike is negative "
                     f"(at {neg_wall.get('strike')}). An adverse move can accelerate "
                     f"through this trade."),
        }
    if pos_wall is not None and neg_wall is None:
        return {
            "verdict": "supports", "available": True, "regime": regime, "flip": flip,
            "positive_wall": pos_wall.get("strike"),
            "note": (f"A positive-gamma concentration sits at {pos_wall.get('strike')}, "
                     f"between the price and this strike. Under the standard "
                     f"convention that is where dealer hedging leans against the move "
                     f"— it has to be gone through to reach the short strike."),
        }
    if neg_wall is not None and pos_wall is None:
        return {
            "verdict": "opposes", "available": True, "regime": regime, "flip": flip,
            "negative_wall": neg_wall.get("strike"),
            "note": (f"The path to this strike runs through negative gamma at "
                     f"{neg_wall.get('strike')}, where hedging pushes with a move "
                     f"rather than against it."),
        }
    return {
        "verdict": "neutral", "available": True, "regime": regime, "flip": flip,
        "note": ("No meaningful gamma concentration sits between the price and this "
                 "strike, so market structure neither helps nor hurts this one."),
    }


GEX_DELTA_ADJUST = {"supports": 1.0, "neutral": 1.0, "opposes": 0.8,
                    "unknown": 0.9, "veto": 0.0}


def apply_gex_to_ceiling(ceiling: dict, gex: dict) -> dict:
    """Gamma never opens a trade; it can only make one smaller or stop it.

    An `opposes` reading pulls the ceiling back rather than merely costing
    confidence, because the risk it describes — an accelerating move through
    the strike — is exactly the risk a wider strike protects against.
    """
    out = dict(ceiling)
    v = gex.get("verdict", "unknown")
    factor = GEX_DELTA_ADJUST.get(v, 0.9)
    lo, hi = out["band"]
    if v == "veto":
        out["vetoed"] = True
        out["veto_note"] = gex.get("note")
        return out
    if factor < 1.0 and hi > lo:
        out["band"] = (lo, round(max(lo, hi * factor), 3))
        out["gex_pullback"] = v
        out["gex_note"] = gex.get("note")
    return out


# ══════════════════════════════════════════════════════════════════════════
# SCORING AND THE RECOMMENDATION
# ══════════════════════════════════════════════════════════════════════════

def score_contract(c: dict, gex: dict, cfg=None) -> dict:
    """Rank one candidate short contract.

    Expected value carries the ranking because it is the only component
    denominated in money the trade actually makes. Everything else either
    gates it (liquidity) or discounts it (tail risk, market structure).
    """
    cfg = cfg or {}
    ev = _num(c.get("ev_per_contract")) or 0.0
    es = _num(c.get("es5_per_share"))
    prem = _num(c.get("prem_pct_collateral")) or 0.0
    pit = _pct(c.get("p_itm_model"))
    spread = _num(c.get("spread_pct"))
    liq_ok = bool(c.get("liquidity_ok", True))
    # Reward per unit of the loss actually feared, not per unit of collateral.
    ev_per_tail = _num(c.get("ev_per_tail"))
    parts = {
        "ev_per_contract": ev,
        "premium_pct_collateral": prem,
        "p_keep_model": (round(100.0 - pit, 1) if pit is not None else None),
        "ev_per_tail": ev_per_tail,
        "tail_loss_per_share": es,
    }
    score = 0.0
    score += max(-50.0, min(50.0, ev / 2.0))            # money, bounded
    score += max(0.0, min(25.0, prem * 5.0))            # premium richness
    if pit is not None:
        score += (100.0 - pit) * 0.25                   # odds of keeping it
    if ev_per_tail is not None:
        score += max(-10.0, min(20.0, ev_per_tail * 20.0))
    if not liq_ok:
        score -= 40.0
    if spread is not None and spread > 10.0:
        score -= min(20.0, (spread - 10.0))
    v = gex.get("verdict")
    if v == "supports":
        score += 6.0
    elif v == "opposes":
        score -= 10.0
    elif v == "veto":
        score -= 1e6
    return {"score": round(score, 1), "parts": parts, "gex_verdict": v}


def _confidence(bias: dict, ceiling: dict, gex: dict, contract: dict) -> dict:
    """How much of this recommendation is actually supported.

    Confidence falls for exactly the reasons a careful reader would discount
    it: the layers disagree, the sample is thin, market structure argues the
    other way, or the contract cannot be traded well.
    """
    reasons = []
    level = 100.0
    if bias.get("conflict"):
        level -= 25.0
        reasons.append("The layers do not all point the same way.")
    if bias.get("lean") is None:
        level -= 15.0
        reasons.append("No layer reports the stock as stretched in either direction.")
    n = ceiling.get("n")
    if not ceiling.get("raised"):
        level -= 10.0
        reasons.append("No measured evidence to sell nearer the money, so this is "
                       "the conservative default strike.")
    elif n is not None and n < MIN_MEASURED_N * 2:
        level -= 10.0
        reasons.append(f"The measured evidence rests on {n} windows — usable, but "
                       f"not deep.")
    v = gex.get("verdict")
    if v == "opposes":
        level -= 20.0
        reasons.append("Gamma positioning argues against this strike.")
    elif v == "unknown":
        level -= 10.0
        reasons.append("No gamma exposure available for this underlying.")
    if not contract.get("liquidity_ok", True):
        level -= 25.0
        reasons.append("This contract does not clear the liquidity gate.")
    sp = _num(contract.get("spread_pct"))
    if sp is not None and sp > 10.0:
        level -= 10.0
        reasons.append(f"The bid-ask spread is {sp:.0f}% of mid.")
    level = max(0.0, min(100.0, level))
    label = ("HIGH" if level >= 75 else "MODERATE" if level >= 55
             else "LOW" if level >= 35 else "WEAK")
    return {"level": round(level), "label": label, "reasons": reasons}


def recommend(symbol: str, spot, side: str, expiration: str, dte,
              contracts: list, bias: dict, ceiling: dict,
              gex_block: dict | None = None, iv_block: dict | None = None,
              earnings_in_days=None) -> dict:
    """Pick the contract and explain it.

    `contracts` are premium_edge.contract_economics rows for ONE side and
    ONE expiration. The band from `ceiling` decides which of them are even
    eligible; scoring picks among those.
    """
    band = ceiling.get("band") or DEFAULT_DELTA_BAND
    floor_pct = _num(ceiling.get("min_distance_pct"))
    eligible, rejected = [], []
    for c in (contracts or []):
        d = _num(c.get("delta"))
        if d is None:
            rejected.append((c, "no delta on the quote"))
            continue
        ad = abs(d)
        if not (band[0] <= ad <= band[1]):
            rejected.append((c, f"delta {ad:.2f} outside the {band[0]:.2f}"
                                f"–{band[1]:.2f} band"))
            continue
        # The distance floor is the measured half of the rule and it binds
        # independently of delta. A strike the market happens to quote at a
        # low delta but which sits INSIDE the distance history says is safe
        # is not eligible — the measurement, not the quote, is the promise
        # this recommendation is making.
        if floor_pct is not None:
            dist = _num(c.get("dist_pct"))
            if dist is None or abs(dist) < floor_pct:
                rejected.append((c, f"only {abs(dist):.1f}% out of the money, "
                                    f"inside the {floor_pct:.1f}% the measured "
                                    f"evidence requires"
                                 if dist is not None else "no distance on the quote"))
                continue
        g = gex_context(gex_block, spot, c.get("strike"), side)
        sc = score_contract(c, g)
        eligible.append({**c, "gex": g, "scoring": sc})
    if not eligible:
        return {
            "ok": False, "symbol": symbol, "side": side,
            "reason": ("No contract on this expiration sits inside the delta band "
                       "the evidence supports."),
            "band": band, "rejected": len(rejected),
            "version": SETUP_VERSION,
        }
    best = max(eligible, key=lambda c: c["scoring"]["score"])
    if best["scoring"]["score"] <= -1e5:
        return {"ok": False, "symbol": symbol, "side": side,
                "reason": best["gex"].get("note") or "Refused on market structure.",
                "vetoed": True, "band": band, "version": SETUP_VERSION}

    conf = _confidence(bias, ceiling, best["gex"], best)
    pit = _pct(best.get("p_itm_model"))
    why = []
    if bias.get("lean"):
        why.extend(bias.get("why") or [])
    why.append(ceiling.get("why"))
    if best["gex"].get("available"):
        why.append(best["gex"]["note"])
    if pit is not None:
        why.append(f"At the volatility this stock actually realizes, this strike "
                   f"finishes in the money about {pit:.0f}% of the time — so it is "
                   f"kept about {100 - pit:.0f}% of the time.")
    ev = _num(best.get("ev_per_contract"))
    if ev is not None:
        why.append(f"Expected value is {ev:+.0f} dollars per contract against the "
                   f"credit at the bid, after costs.")

    risks = []
    if best["gex"].get("verdict") == "opposes":
        risks.append(best["gex"]["note"])
    if not best.get("liquidity_ok", True):
        risks.append("This contract fails the liquidity gate: "
                     + ", ".join(best.get("liquidity_notes") or []))
    sp = _num(best.get("spread_pct"))
    if sp is not None and sp > 10.0:
        risks.append(f"The bid-ask spread is {sp:.0f}% of mid — the credit shown "
                     f"is the bid, which is what a resting order is promised, but "
                     f"a wide market makes managing the trade expensive.")
    es = _num(best.get("es5_per_share"))
    if es is not None:
        risks.append(f"In the worst 5% of outcomes the loss averages "
                     f"{es:.2f} per share, or about {es * 100:.0f} dollars per "
                     f"contract.")
    if _num(earnings_in_days) is not None and _num(earnings_in_days) <= (_num(dte) or 0):
        risks.append(f"Earnings land in {int(_num(earnings_in_days))} days, inside "
                     f"the life of this option. Every historical rate here is "
                     f"measured across ordinary sessions, not event days.")
    if bias.get("conflict"):
        risks.append("The layers disagree about which way this stock is stretched; "
                     "the case is weaker than any single tab suggests.")
    if not ceiling.get("raised"):
        risks.append("Nothing measured supports selling nearer the money than the "
                     "default, so this is the conservative strike, not an "
                     "optimised one.")

    return {
        "ok": True, "symbol": symbol, "side": side,
        "action": ("Sell a call" if side == "call" else "Sell a put"),
        "expiration": expiration, "dte": _num(dte),
        "strike": best.get("strike"), "delta": best.get("delta"),
        "credit": best.get("credit_exec"), "credit_basis": best.get("credit_basis"),
        "premium_pct_collateral": best.get("prem_pct_collateral"),
        "annualized_pct": best.get("annualized_pct"),
        "p_keep_model": (round(100.0 - pit, 1) if pit is not None else None),
        "p_touch_model": best.get("p_touch_model"),
        "ev_per_contract": best.get("ev_per_contract"),
        "tail_loss_per_share": best.get("es5_per_share"),
        "breakeven": best.get("breakeven"), "collateral": best.get("collateral"),
        "spread_pct": best.get("spread_pct"), "open_interest": best.get("oi"),
        "liquidity_ok": best.get("liquidity_ok"),
        "band": band, "band_raised": bool(ceiling.get("raised")),
        "bias": bias, "ceiling": ceiling, "gex": best["gex"],
        "iv": iv_block or {},
        "confidence": conf,
        "why": [w for w in why if w],
        "risks": risks,
        "considered": len(eligible),
        "scoring": best["scoring"],
        "version": SETUP_VERSION,
    }
