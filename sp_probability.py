"""sp_probability.py — the seller's probabilities, kept apart (v4.80).

One short option raises several different questions that the app used to
answer with one number, or with numbers in different units:

  P0        the option expires worthless           (finish outside the strike)
  P(ITM)    it expires in the money                (= 1 − P0, stated anyway)
  P(touch)  the strike trades at any point first   (assignment/gamma risk)
  POP       the trade makes money after costs      (finish inside breakeven)
  P(hit f)  the option loses f of its value early  (buy it back cheaply)
  P(≈0)     it becomes nearly worthless before expiry

They are computed here, side by side, from ONE volatility for the contract's
own horizon, under a named distribution, and every answer says what it is:
a model, which model, and what it assumed.

What is reused: Black-Scholes and N(·) from metrics.py (the canonical copy),
the volatility estimators in vol_forecast.py, the closed-form expected
shortfall in premium_edge.py. What is new: the horizon term structure, the
touch model with its discrete-monitoring correction, the Student-t and
empirical alternatives, the path simulation for early profit targets, and
the invariants that pin all of it.

Conventions (metrics.py): T in years = calendar days / 365; sigma is an
annualized decimal; vols from bars are √252-annualized. Drift is ZERO for
every real-world probability here — no view on direction is ever baked in.
"""
from __future__ import annotations

import bisect
import math

from metrics import _bs_price, _norm_cdf as N, wilson_interval
import premium_edge as _pe
import vol_forecast as vf

SP_PROB_VERSION = "sp-probability-1.0.0"
TD = vf.TRADING_DAYS
BGK_BETA = 0.5826                 # Broadie–Glasserman–Kou (1997), −ζ(½)/√(2π)
T_DOF_DEFAULT = 4.0               # Platen & Rendek (2008): ~4 for equity index returns

MODELS = ("lognormal", "student_t", "empirical")
MONITORING = ("continuous", "daily")

# Horizon blends: which estimators speak for which horizon.
#
# The walk-forward harness (SHORT_PREMIUM.md §validation) scored
# horizon-specific blends against the app's 30-day blend at 5/10/21/42
# sessions on 100 names × 10 years, out of sample, on BOTH halves of the
# period. Short-window blends lost at every horizon. A smoother blend with
# a 60-day voice won the recent half (2021–2026) on 66–85% of names and
# LOST the first half (2016–2021, which holds the 2020 crash) on 76–84% —
# not robust, so it was rejected under the same both-halves rule
# vol_forecast applies to per-ticker models. The evidence says: one
# validated blend, the horizon enters through √T only. Every bucket
# therefore carries the same weights by default; thresholds.json
# `short_premium.horizon_blends` can override a bucket, and the harness is
# the place to earn that.
_VALIDATED_BLEND = {"RV20": 0.30, "EWMA94": 0.35, "PARK20C": 0.35}
HORIZON_BLENDS_DEFAULT = {
    "5": dict(_VALIDATED_BLEND), "10": dict(_VALIDATED_BLEND),
    "21": dict(_VALIDATED_BLEND), "42": dict(_VALIDATED_BLEND),
}
ANCHOR_SHRINK_DEFAULT = 0.25

# Measured tail correction. The same harness found the driftless lognormal
# well calibrated to 1.25σ and UNDER-predicting beyond it: at 2σ it says
# 2.3% and 3.1% happened. The pooled ratio realized/modeled by strike
# distance is a MEASURED prior that a ticker's own breach history shrinks
# toward (see sp_evidence). Applied only beyond 1.25σ; identity nearer in.
TAIL_CORRECTION_DEFAULT = {
    # k_sigma: realized/modeled P(ITM), 100 names, 2016–2026, n≈184k per k
    "1.25": 1.00, "1.5": 1.054, "2.0": 1.345,
}


# ── small numerics ──────────────────────────────────────────────────────────
def _clamp01(p):
    if p is None:
        return None
    if p != p:
        return None
    return max(0.0, min(1.0, float(p)))


def _betacf(a, b, x, itmax=300, eps=3e-12):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / (c if abs(c) > 1e-300 else 1e-300)
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / (c if abs(c) > 1e-300 else 1e-300)
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def _betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lb = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
          + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return math.exp(lb) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lb) * _betacf(b, a, 1 - x) / b


def t_cdf(x: float, dof: float = T_DOF_DEFAULT) -> float:
    """Student-t CDF (regularized incomplete beta; stdlib only)."""
    p = 0.5 * _betai(dof / 2.0, 0.5, dof / (dof + x * x))
    return 1.0 - p if x > 0 else p


def t_unit_scale(dof: float = T_DOF_DEFAULT) -> float:
    """Multiply a unit-variance z by 1/scale to get a t-variate with the same
    variance: t_ν has variance ν/(ν−2)."""
    return math.sqrt((dof - 2.0) / dof)


# ── horizon volatility ──────────────────────────────────────────────────────
def horizon_bucket(h_td: float) -> str:
    h = float(h_td or 0)
    if h <= 7:
        return "5"
    if h <= 15:
        return "10"
    if h <= 31:
        return "21"
    return "42"


def sigma_for_horizon(bars, h_td: float, cfg: dict | None = None) -> dict | None:
    """The volatility the contract's OWN horizon is judged against.

    Returns {sigma, basis, bucket, components, anchor, n_bars} or None when
    history is too short. `sigma` is annualized; the caller converts with
    √T. Every bucket reproduces vol_forecast's ExpectedRV30 blend by default
    (same estimators, same anchor shrink) — see HORIZON_BLENDS_DEFAULT for
    the measured reason — so nothing already on screen changes.
    """
    cfg = cfg or {}
    if not bars or len(bars) < int(cfg.get("min_history_bars", 120)):
        return None
    cands = vf.candidates(bars, cfg)
    closes = [float(b.get("close") or 0) for b in bars]
    anchor = vf.rv(closes, int(cfg.get("anchor_window", vf.ANCHOR_WINDOW)))
    shrink = float(cfg.get("anchor_shrink", ANCHOR_SHRINK_DEFAULT))
    blends = dict(HORIZON_BLENDS_DEFAULT)
    blends.update(cfg.get("horizon_blends") or {})
    bucket = horizon_bucket(h_td)
    weights = blends.get(bucket) or HORIZON_BLENDS_DEFAULT["21"]
    tot = sum(w for k, w in weights.items() if cands.get(k))
    if tot <= 0:
        return None
    core = sum(cands[k] * w for k, w in weights.items() if cands.get(k)) / tot
    sigma = shrink * anchor + (1.0 - shrink) * core if anchor else core
    if not sigma or sigma <= 0:
        return None
    return {
        "sigma": round(sigma, 5), "bucket": bucket,
        "basis": (f"forecast for a {int(round(h_td))}-session horizon "
                  f"(blend {', '.join(f'{k} {w:.2f}' for k, w in weights.items() if cands.get(k))}"
                  f"; long-run anchor {shrink:.2f})"),
        "components": {k: round(v, 5) for k, v in cands.items()},
        "anchor": round(anchor, 5) if anchor else None,
        "n_bars": len(bars),
    }


def trading_days(dte_calendar: float) -> float:
    """Calendar days → sessions (252/365), floored at a quarter session so a
    same-day contract still has a horizon."""
    return max(0.25, float(dte_calendar or 0) * TD / 365.0)


# ── the empirical library ───────────────────────────────────────────────────
def standardized_moves(bars, h_td: int, warmup: int = 120, step: int = 1,
                       sigma_window: int = 20) -> dict:
    """The distribution of what this history actually did, in the units the
    model speaks: z = ln(S_{i+h}/S_i) / (σ_i·√T) with σ_i the trailing
    close-to-close vol at bar i (point-in-time — nothing after bar i is
    used to standardize). Also the standardized maximum excursions up and
    down over the window, which is what a touch probability needs.

    Returns sorted lists: {"z": [...], "up": [...], "dn": [...], "n": int,
    "h": h}. Pool several histories by concatenating and re-sorting.
    """
    h = max(1, int(h_td))
    closes = [float(b.get("close") or 0) for b in bars]
    highs = [float(b.get("high") or 0) for b in bars]
    lows = [float(b.get("low") or 0) for b in bars]
    zs, ups, dns = [], [], []
    n = len(bars)
    for i in range(max(warmup, sigma_window + 1), n - h, max(1, step)):
        sig = vf.rv(closes[: i + 1], sigma_window)
        if not sig or closes[i] <= 0:
            continue
        s = sig * math.sqrt(h / TD)
        c0 = closes[i]
        cT = closes[i + h]
        seg_h = [x for x in highs[i + 1: i + 1 + h] if x > 0]
        seg_l = [x for x in lows[i + 1: i + 1 + h] if x > 0]
        if cT <= 0 or len(seg_h) < h * 0.8 or len(seg_l) < h * 0.8:
            continue
        zs.append(math.log(cT / c0) / s)
        ups.append(math.log(max(seg_h) / c0) / s)
        dns.append(-math.log(min(seg_l) / c0) / s)
    zs.sort(); ups.sort(); dns.sort()
    return {"z": zs, "up": ups, "dn": dns, "n": len(zs), "h": h}


def merge_libraries(*libs) -> dict:
    out = {"z": [], "up": [], "dn": [], "n": 0, "h": None}
    for lib in libs:
        if not lib:
            continue
        out["z"].extend(lib.get("z") or [])
        out["up"].extend(lib.get("up") or [])
        out["dn"].extend(lib.get("dn") or [])
        out["h"] = out["h"] or lib.get("h")
    out["z"].sort(); out["up"].sort(); out["dn"].sort()
    out["n"] = len(out["z"])
    return out


def _emp_tail_below(sorted_vals, x):
    return bisect.bisect_right(sorted_vals, x) / max(1, len(sorted_vals))


def _emp_tail_above(sorted_vals, x):
    return 1.0 - bisect.bisect_left(sorted_vals, x) / max(1, len(sorted_vals))


# ── terminal probabilities ──────────────────────────────────────────────────
def z_distance(spot: float, strike: float, sigma: float, t_years: float) -> float | None:
    """Signed strike distance in standard deviations of the terminal
    log-move: positive = strike above spot."""
    if not all(x and x > 0 for x in (spot, strike, sigma, t_years)):
        return None
    return math.log(strike / spot) / (sigma * math.sqrt(t_years))


def p_itm(spot, strike, sigma, t_years, side, model="lognormal", lib=None,
          dof=T_DOF_DEFAULT) -> float | None:
    """P(the short option finishes in the money), drift zero.

    lognormal — driftless real-world lognormal, E[S_T]=S (log-drift −½σ²),
                the model premium_edge already uses; identical numbers.
    student_t — same variance, Student-t(dof) terminal log-move: fatter
                tails, so far strikes are reached more often and near ones
                slightly less.
    empirical — the standardized-move library `lib` (see standardized_moves):
                how often THIS history, in vol units, actually finished that
                far. No distributional assumption; needs lib["n"] ≥ 200.
    """
    z = z_distance(spot, strike, sigma, t_years)
    if z is None:
        return None
    s = sigma * math.sqrt(t_years)
    if model == "lognormal":
        below = N(z + 0.5 * s)           # P(ln S_T/S ≤ ln K/S) with mean −½s²
    elif model == "student_t":
        # shift by the same ½s² drift so the mean matches, scale to unit var
        below = t_cdf((z + 0.5 * s) / t_unit_scale(dof), dof)
    elif model == "empirical":
        if not lib or (lib.get("n") or 0) < 200:
            return None
        below = _emp_tail_below(lib["z"], z)
    else:
        raise ValueError(f"unknown model {model!r}")
    p = below if side == "put" else 1.0 - below
    return _clamp01(p)


def p_expire_worthless(spot, strike, sigma, t_years, side, **kw) -> float | None:
    """P0 — the seller's number. One minus P(ITM), stated in its own right so
    no reader has to do the subtraction."""
    p = p_itm(spot, strike, sigma, t_years, side, **kw)
    return None if p is None else _clamp01(1.0 - p)


def tail_corrected(p: float | None, z: float | None, table: dict | None = None) -> float | None:
    """Apply the measured tail correction to a modeled P(ITM): multiply by
    the realized/modeled ratio at the strike's σ-distance, interpolated
    between the table's k points, identity inside 1.25σ. Clamped to [0,1].
    The result is labeled `tail_adjusted`; the raw model number is kept."""
    if p is None or z is None:
        return p
    tab = table or TAIL_CORRECTION_DEFAULT
    pts = sorted((float(k), float(v)) for k, v in tab.items())
    k = abs(float(z))
    if not pts or k <= pts[0][0]:
        return _clamp01(p)
    if k >= pts[-1][0]:
        return _clamp01(p * pts[-1][1])
    for (k0, f0), (k1, f1) in zip(pts, pts[1:]):
        if k0 <= k <= k1:
            f = f0 + (f1 - f0) * (k - k0) / (k1 - k0)
            return _clamp01(p * f)
    return _clamp01(p)


# ── touch ───────────────────────────────────────────────────────────────────
def p_touch(spot, strike, sigma, t_years, monitoring="continuous", model="lognormal",
            lib=None, side=None, dof=T_DOF_DEFAULT) -> float | None:
    """P(the strike is touched at any point before expiry), drift zero.

    Continuous monitoring: the reflection principle, 2·N(−|z|). This is the
    DEFAULT, and it is a measured choice: the app grades a touch on the
    session's high or low, which samples the path almost continuously, and
    the walk-forward harness found the plain formula the best-calibrated
    touch model at every horizon (ECE 1.2–2.6%).
    Daily monitoring: the same formula with the barrier shifted AWAY from
    spot by β·σ·√Δt (Broadie–Glasserman–Kou 1997, β≈0.5826) — correct when
    "touched" means a daily CLOSE crossed the strike, and measurably wrong
    for highs/lows (it under-predicted by 5–7 points).
    student_t: Monte Carlo would be needed for an exact answer; the
    lognormal touch is returned with the fatter-tail terminal correction
    applied as a floor (touch ≥ ITM under the same model).
    empirical: the library's standardized max-excursion tail.
    """
    z = z_distance(spot, strike, sigma, t_years)
    if z is None:
        return None
    if z == 0:
        return 1.0
    up = z > 0
    if model == "empirical":
        if not lib or (lib.get("n") or 0) < 200:
            return None
        arr = lib["up"] if up else lib["dn"]
        return _clamp01(_emp_tail_above(arr, abs(z)))
    zz = abs(z)
    if monitoring == "daily":
        dt_years = 1.0 / TD
        zz = zz + BGK_BETA * math.sqrt(dt_years / t_years)
    p = 2.0 * N(-zz)
    if model == "student_t":
        pit = p_itm(spot, strike, sigma, t_years, "call" if up else "put",
                    model="student_t", dof=dof)
        if pit is not None:
            p = max(p, pit)
    return _clamp01(p)


# ── profit after costs ──────────────────────────────────────────────────────
def breakeven(strike, credit, side):
    return strike - credit if side == "put" else strike + credit


def p_profit(spot, strike, sigma, t_years, side, credit, costs_per_share=0.0,
             model="lognormal", lib=None, dof=T_DOF_DEFAULT) -> float | None:
    """POP: P(the trade is profitable at expiry after costs) — the option
    settles at less than the net credit, i.e. spot finishes inside the
    breakeven moved by costs. Distinct from P0: a put that finishes a little
    in the money can still profit."""
    net = float(credit or 0) - float(costs_per_share or 0)
    if net <= 0:
        return 0.0
    be = breakeven(strike, net, side)
    if be <= 0:
        return None
    # profit ⇔ S_T > be (put) / S_T < be (call) ⇔ the OTHER side of "ITM at be"
    pit = p_itm(spot, be, sigma, t_years, side, model=model, lib=lib, dof=dof)
    return None if pit is None else _clamp01(1.0 - pit)


# ── expected shortfall ──────────────────────────────────────────────────────
def expected_shortfall(spot, strike, sigma, t_years, side, credit, q=0.05) -> float | None:
    """Average loss per share over the worst q of driftless-lognormal
    outcomes, net of the credit — premium_edge's closed form, exposed here
    for q ∈ {0.01, 0.05, 0.10}. MODELED."""
    try:
        return _pe._tail_es_short(spot, strike, sigma, t_years, side, credit, q=q)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return None


# ── early profit targets: a path model ──────────────────────────────────────
def profit_path_stats(spot, strike, sigma_real, iv_entry, dte_calendar, side, credit,
                      targets=(0.5, 0.75, 0.9), near_zero_frac=0.10, n_paths=4000,
                      seed=7, model="lognormal", dof=T_DOF_DEFAULT, rate=0.0) -> dict | None:
    """P(the short option loses f of its value before expiry), for each f in
    `targets`, plus P(it becomes nearly worthless), the expected number of
    calendar days to each target GIVEN it is hit, and the share of paths
    that lose money at expiry.

    MODELED, and the label says how: daily lognormal (or Student-t) steps at
    σ = `sigma_real` (the horizon forecast), the option re-priced each day by
    Black-Scholes at `iv_entry` held FLAT. Holding IV flat is the one
    assumption that cannot be measured until the app's own chain snapshots
    accumulate; it tends to understate early-target hits when IV falls after
    entry and overstate them when it rises. Seeded, deterministic.
    """
    if not all(x and x > 0 for x in (spot, strike, sigma_real, iv_entry, credit)) or dte_calendar is None:
        return None
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return None
    days = max(1, int(round(float(dte_calendar))))
    steps = max(1, int(round(trading_days(days))))
    dt = 1.0 / TD
    rng = np.random.default_rng(seed)
    if model == "student_t":
        z = rng.standard_t(dof, size=(n_paths, steps)) * t_unit_scale(dof)
    else:
        z = rng.standard_normal(size=(n_paths, steps))
    incr = -0.5 * sigma_real * sigma_real * dt + sigma_real * math.sqrt(dt) * z
    logpath = np.cumsum(incr, axis=1)
    paths = spot * np.exp(logpath)
    cal_per_step = days / steps
    hit_day = {f: np.full(n_paths, -1.0) for f in targets}
    zero_day = np.full(n_paths, -1.0)
    touched = np.zeros(n_paths, dtype=bool)
    for k in range(steps):
        s_k = paths[:, k]
        remaining_cal = max(0.0, days - (k + 1) * cal_per_step)
        T_rem = remaining_cal / 365.0
        if T_rem <= 0:
            val = np.maximum(0.0, (strike - s_k) if side == "put" else (s_k - strike))
        else:
            val = np.array([_bs_price(float(s), strike, T_rem, iv_entry, side, r=rate, q=0.0)
                            for s in s_k])
        touched |= (s_k <= strike) if side == "put" else (s_k >= strike)
        for f in targets:
            m = (hit_day[f] < 0) & (val <= credit * (1.0 - f))
            hit_day[f][m] = (k + 1) * cal_per_step
        mz = (zero_day < 0) & (val <= credit * near_zero_frac)
        zero_day[mz] = (k + 1) * cal_per_step
    terminal = paths[:, -1]
    intrinsic = np.maximum(0.0, (strike - terminal) if side == "put" else (terminal - strike))
    pnl = credit - intrinsic
    out = {
        "basis": (f"MODELED: {n_paths} daily {'Student-t' if model == 'student_t' else 'lognormal'} "
                  f"paths at σ={sigma_real:.3f}, option re-priced at IV {iv_entry:.3f} held flat, "
                  f"seed {seed}"),
        "n_paths": n_paths, "steps": steps,
        "p_touch_paths": round(float(touched.mean()), 4),
        "p_profit_expiry": round(float((pnl > 0).mean()), 4),
        "p_near_zero": round(float((zero_day >= 0).mean()), 4),
        "near_zero_frac": near_zero_frac,
        "targets": {},
    }
    for f in targets:
        hd = hit_day[f]
        hit = hd >= 0
        out["targets"][f"{int(f * 100)}"] = {
            "p_hit": round(float(hit.mean()), 4),
            "expected_days_if_hit": (round(float(hd[hit].mean()), 1) if hit.any() else None),
            "median_days_if_hit": (round(float(np.median(hd[hit])), 1) if hit.any() else None),
        }
    hz = zero_day >= 0
    out["expected_days_to_near_zero_if_hit"] = (round(float(zero_day[hz].mean()), 1)
                                               if hz.any() else None)
    return out


# ── conservative bounds ─────────────────────────────────────────────────────
def conservative_bound(p_point: float | None, n_eff: int | None, z: float = 1.96) -> dict | None:
    """The lower Wilson bound on a probability that is backed by `n_eff`
    independent observations — what a seller should size on. A modeled
    probability with no observations behind it has no bound: the caller
    must say so rather than print the point estimate twice."""
    if p_point is None or not n_eff or n_eff <= 0:
        return None
    k = int(round(p_point * n_eff))
    w = wilson_interval(k, int(n_eff), z=z)
    if not w:
        return None
    return {"low": round(w["lo"], 4), "high": round(w["hi"], 4), "n_eff": int(n_eff)}


# ── everything for one contract ─────────────────────────────────────────────
def contract_probabilities(spot, strike, side, dte_calendar, sigma_h, credit,
                           costs_per_share=0.0, iv_entry=None, lib=None,
                           model="lognormal", monitoring="continuous", paths=True,
                           dof=T_DOF_DEFAULT, seed=7, tail_table=None) -> dict | None:
    """The full separated set for one short contract, all labeled.

    `sigma_h` is the horizon volatility (sigma_for_horizon). `lib` is an
    optional empirical library for the same horizon; when the chosen model
    is empirical and the library is thin the fields come back None with a
    reason, never a substitute."""
    if not all(x and x > 0 for x in (spot, strike, sigma_h)) or dte_calendar is None:
        return None
    t_years = max(1e-6, float(dte_calendar) / 365.0)
    kw = {"model": model, "lib": lib, "dof": dof}
    pit = p_itm(spot, strike, sigma_h, t_years, side, **kw)
    z = z_distance(spot, strike, sigma_h, t_years)
    pit_adj = tail_corrected(pit, z, tail_table) if model == "lognormal" else pit
    out = {
        "version": SP_PROB_VERSION,
        "model": model, "monitoring": monitoring,
        "sigma": round(sigma_h, 5), "t_years": round(t_years, 6),
        "z_distance": round(z, 4) if z is not None else None,
        "p_itm": pit,
        "p_expire_worthless": None if pit is None else _clamp01(1.0 - pit),
        # The measured tail correction (pooled realized/modeled by σ-distance),
        # identity inside 1.25σ. The seller's headline P0 is the adjusted one;
        # the raw model number stays beside it so the correction is visible.
        "p_itm_tail_adjusted": pit_adj,
        "p_expire_worthless_tail_adjusted": None if pit_adj is None else _clamp01(1.0 - pit_adj),
        "p_touch": p_touch(spot, strike, sigma_h, t_years, monitoring=monitoring,
                           model=model, lib=lib, side=side, dof=dof),
        "p_profit": p_profit(spot, strike, sigma_h, t_years, side, credit,
                             costs_per_share, model=model, lib=lib, dof=dof),
        "es_95": expected_shortfall(spot, strike, sigma_h, t_years, side, credit, q=0.05),
        "es_99": expected_shortfall(spot, strike, sigma_h, t_years, side, credit, q=0.01),
        "basis": {
            "terminal": (f"model ({model}, drift 0, σ_h {sigma_h:.3f} for "
                         f"{float(dte_calendar):.0f} calendar days)"),
            "touch": (f"model (reflection principle, {monitoring} monitoring"
                      f"{', BGK barrier shift' if monitoring == 'daily' else ''})"),
            "tail": "model (closed-form lognormal expected shortfall)",
            "tail_adjustment": ("MEASURED prior: pooled realized/modeled breach ratio by "
                                "σ-distance (100 names, 2016–2026), identity inside 1.25σ"
                                if model == "lognormal" else "none (non-lognormal model)"),
        },
        "lib_n": (lib or {}).get("n") if lib else None,
    }
    if model == "empirical" and (not lib or (lib.get("n") or 0) < 200):
        out["insufficient"] = (f"empirical library has {(lib or {}).get('n') or 0} "
                               f"standardized windows; 200 needed")
    if paths and iv_entry:
        out["paths"] = profit_path_stats(spot, strike, sigma_h, iv_entry, dte_calendar,
                                         side, credit, model=model, dof=dof, seed=seed)
    return out
