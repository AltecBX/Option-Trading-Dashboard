"""sp_evidence.py — what this stock actually did, in the model's units (v4.80).

The breach history in edge_scan (§17) answered one question per ticker:
how often did price finish or touch beyond k·σ over h sessions. This layer
keeps that measurement, makes it point-in-time in every input, and adds
what a seller needs to know about the misses:

  * finish beyond / touch, by horizon and σ-distance, with the count of
    NON-OVERLAPPING windows behind each rate (the honest denominator);
  * how far past the strike price went when it did (overshoot), how soon
    it first got there, how many times it crossed back, and the largest
    single-session gap toward the strike inside the window;
  * the same tables conditioned on the state the stock was in when the
    window opened — its volatility regime, a directional run, an unusually
    large recent move — with the conditioning rule fixed in advance.

Every rate carries a Wilson interval on its independent-window count. And
because most tickers have too few windows in a given state to speak for
themselves, `evidence_for_strike` shrinks a rate toward the next level up:
this ticker in this state → this ticker in any state → the universe prior
(fixtures/sp_universe_calibration.json, a MEASURED pooled table) — with
the weights stated, so a reader can see how much of a number is this
stock's own history and how much is borrowed.

Nothing here reads the future: the σ that places a strike at bar i uses
bars[:i+1]; the regime at bar i uses bars[:i+1]; only the window's outcome
uses bars[i+1 : i+1+h]. `test_sp_evidence` mutates future bars to prove it.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from metrics import wilson_interval
import sp_probability as sp
import vol_forecast as vf

SP_EVIDENCE_VERSION = "sp-evidence-1.0.0"
TD = vf.TRADING_DAYS

HORIZONS_DEFAULT = (5, 10, 21, 31, 42)
KS_DEFAULT = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
SIGMA_WINDOW = 20
ANCHOR_WINDOW = 252
WARMUP = 120                    # bars before the first window (σ20 + a regime read)
STEP = 1                        # every bar; overlap is accounted for in n_eff
MIN_RUN = 3                     # a "run" is 3+ consecutive closes one way
BIG_MOVE_SIGMAS = 2.0           # |5-day return| beyond 2σ√5 is "after a large move"
KAPPA_DEFAULT = 40.0            # prior strength (independent windows) for shrinkage

STATES = ("all", "vol_low", "vol_mid", "vol_high", "run_up", "run_down", "after_big_move")

_FIXTURE = Path(__file__).with_name("fixtures") / "sp_universe_calibration.json"
_PRIOR_CACHE: dict | None = None


# ── universe prior ───────────────────────────────────────────────────────────
def universe_prior() -> dict:
    """The pooled MEASURED calibration table shipped with the app, or an
    identity table with a note when the fixture is missing."""
    global _PRIOR_CACHE
    if _PRIOR_CACHE is not None:
        return _PRIOR_CACHE
    try:
        _PRIOR_CACHE = json.loads(_FIXTURE.read_text())
    except Exception:  # noqa: BLE001
        _PRIOR_CACHE = {"missing": True, "itm_by_k": {}, "touch_by_horizon": {},
                        "universe": {"n_symbols": 0}}
    return _PRIOR_CACHE


def prior_itm(h_td: float, k: float, side: str = "put") -> dict:
    """The universe-level P(finish beyond k·σ over h sessions): the driftless
    lognormal at that distance times the pooled realized/modeled ratio for
    that distance — a model corrected by measurement, labeled as such."""
    t_years = max(1e-6, float(h_td) / TD)
    s = math.sqrt(t_years)                     # σ=1 in standardized units
    strike = math.exp(-k * s) if side == "put" else math.exp(k * s)
    model = sp.p_itm(1.0, strike, 1.0, t_years, side)
    pri = universe_prior()
    ratio = _interp_ratio(pri.get("itm_by_k") or {}, k)
    p = sp._clamp01((model or 0.0) * ratio)  # noqa: SLF001
    n = int(((pri.get("itm_by_k") or {}).get(_nearest_key(pri.get("itm_by_k") or {}, k)) or {}).get("n") or 0)
    return {"p": p, "model": model, "ratio": ratio, "n_pool": n,
            "basis": ("universe prior: driftless lognormal × pooled realized/modeled ratio "
                      f"{ratio:.3f} at {k:.2f}σ" + (" (fixture missing → ratio 1)" if pri.get("missing") else ""))}


def prior_touch(h_td: float, k: float) -> dict:
    t_years = max(1e-6, float(h_td) / TD)
    model = sp.p_touch(1.0, math.exp(-k * math.sqrt(t_years)), 1.0, t_years, "continuous")
    pri = universe_prior()
    tb = pri.get("touch_by_horizon") or {}
    key = _nearest_key(tb, h_td)
    ratio = float((tb.get(key) or {}).get("ratio") or 1.0) if key else 1.0
    n = int((tb.get(key) or {}).get("n") or 0) if key else 0
    return {"p": sp._clamp01((model or 0.0) * ratio), "model": model, "ratio": ratio,  # noqa: SLF001
            "n_pool": n,
            "basis": ("universe prior: reflection principle × pooled realized/modeled touch "
                      f"ratio {ratio:.3f} at ~{key or '?'} sessions")}


def _nearest_key(table: dict, x: float) -> str | None:
    if not table:
        return None
    return min(table.keys(), key=lambda kk: abs(float(kk) - float(x)))


def _interp_ratio(table: dict, k: float) -> float:
    pts = sorted((float(kk), float((v or {}).get("ratio") or 1.0)) for kk, v in table.items())
    if not pts:
        return 1.0
    if k <= pts[0][0]:
        return pts[0][1]
    if k >= pts[-1][0]:
        return pts[-1][1]
    for (k0, f0), (k1, f1) in zip(pts, pts[1:]):
        if k0 <= k <= k1:
            return f0 + (f1 - f0) * (k - k0) / (k1 - k0)
    return 1.0


# ── the measurement ──────────────────────────────────────────────────────────
def _state_at(closes, i, sigma, anchor) -> dict:
    """The fixed-in-advance conditioning read at bar i, from bars[:i+1] only."""
    out = {"vol": None, "run": None, "big": False}
    if sigma and anchor:
        r = sigma / anchor
        out["vol"] = "vol_low" if r < 0.8 else ("vol_high" if r > 1.25 else "vol_mid")
    # run: count consecutive same-direction closes ending at i
    n_up = n_dn = 0
    j = i
    while j >= 1 and closes[j] > closes[j - 1]:
        n_up += 1; j -= 1
    j = i
    while j >= 1 and closes[j] < closes[j - 1]:
        n_dn += 1; j -= 1
    if n_up >= MIN_RUN:
        out["run"] = "run_up"
    elif n_dn >= MIN_RUN:
        out["run"] = "run_down"
    if i >= 5 and sigma and closes[i - 5] > 0:
        r5 = math.log(closes[i] / closes[i - 5])
        out["big"] = abs(r5) > BIG_MOVE_SIGMAS * sigma * math.sqrt(5.0 / TD)
    return out


def _new_cell():
    return {"n": 0, "starts": [], "put_touch": 0, "put_itm": 0, "call_touch": 0, "call_itm": 0,
            "put_over": [], "call_over": [], "put_first": [], "call_first": [],
            "put_recross": [], "call_recross": [], "gap_dn": [], "gap_up": []}


def breach_table(bars, horizons=HORIZONS_DEFAULT, ks=KS_DEFAULT, step=STEP) -> dict:
    """MEASURED per-ticker breach/touch/excursion table.

    Returns {"version", "n_bars", "cells": {state: {h: {k: cell}}}, "basis"}
    where a cell holds counts and the derived rates (see _finish_cell). The
    "all" state is every window; the others are the subsets whose opening
    bar was in that state.
    """
    closes = [float(b.get("close") or 0) for b in bars]
    highs = [float(b.get("high") or 0) for b in bars]
    lows = [float(b.get("low") or 0) for b in bars]
    opens = [float(b.get("open") or 0) for b in bars]
    n = len(bars)
    cells = {st: {h: {k: _new_cell() for k in ks} for h in horizons} for st in STATES}
    for i in range(WARMUP, n - 1, max(1, step)):
        sigma = vf.rv(closes[: i + 1], SIGMA_WINDOW)
        anchor = vf.rv(closes[: i + 1], ANCHOR_WINDOW) if i + 1 >= ANCHOR_WINDOW + 1 else None
        if not sigma or closes[i] <= 0:
            continue
        state = _state_at(closes, i, sigma, anchor)
        states = ["all"]
        if state["vol"]:
            states.append(state["vol"])
        if state["run"]:
            states.append(state["run"])
        if state["big"]:
            states.append("after_big_move")
        c0 = closes[i]
        for h in horizons:
            if i + h >= n:
                continue
            seg_h = highs[i + 1: i + 1 + h]
            seg_l = lows[i + 1: i + 1 + h]
            seg_c = closes[i + 1: i + 1 + h]
            seg_o = opens[i + 1: i + 1 + h]
            if min(seg_l) <= 0 or min(seg_c) <= 0:
                continue
            s = sigma * math.sqrt(h / TD)
            # largest single-session gap (open vs prior close), in σ_h units
            prev = [c0] + seg_c[:-1]
            gaps = [math.log(o / p) / s for o, p in zip(seg_o, prev) if o > 0 and p > 0]
            g_dn = -min(gaps) if gaps else 0.0
            g_up = max(gaps) if gaps else 0.0
            for k in ks:
                put_k = c0 * math.exp(-k * s)
                call_k = c0 * math.exp(k * s)
                for st in states:
                    cell = cells[st][h][k]
                    cell["n"] += 1
                    cell["starts"].append(i)
                    cell["gap_dn"].append(g_dn)
                    cell["gap_up"].append(g_up)
                    # puts: down-side
                    touched = [j for j, lo in enumerate(seg_l) if lo <= put_k]
                    if touched:
                        cell["put_touch"] += 1
                        cell["put_first"].append(touched[0] + 1)
                        cell["put_over"].append(math.log(put_k / min(seg_l)) / s)
                        below = [c <= put_k for c in seg_c]
                        cell["put_recross"].append(sum(1 for a, b in zip(below, below[1:]) if a != b))
                    if seg_c[-1] <= put_k:
                        cell["put_itm"] += 1
                    touched = [j for j, hi in enumerate(seg_h) if hi >= call_k]
                    if touched:
                        cell["call_touch"] += 1
                        cell["call_first"].append(touched[0] + 1)
                        cell["call_over"].append(math.log(max(seg_h) / call_k) / s)
                        above = [c >= call_k for c in seg_c]
                        cell["call_recross"].append(sum(1 for a, b in zip(above, above[1:]) if a != b))
                    if seg_c[-1] >= call_k:
                        cell["call_itm"] += 1
    out_cells = {}
    for st in STATES:
        out_cells[st] = {}
        for h in horizons:
            out_cells[st][h] = {k: _finish_cell(cells[st][h][k], h) for k in ks}
    return {"version": SP_EVIDENCE_VERSION, "n_bars": n, "horizons": list(horizons),
            "ks": list(ks), "cells": out_cells,
            "basis": ("MEASURED: strikes placed at k·σ20 (point-in-time) from each bar's close, "
                      "outcome from the next h sessions' highs/lows/closes; n_eff counts "
                      "non-overlapping windows")}


def _mean(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def _finish_cell(cell: dict, h: int) -> dict:
    n = cell["n"]
    if n == 0:
        return {"n": 0, "n_eff": 0}
    n_eff = _independent(cell["starts"], h)
    out = {"n": n, "n_eff": n_eff}
    for side in ("put", "call"):
        t, f = cell[f"{side}_touch"], cell[f"{side}_itm"]
        out[f"{side}_touch"] = round(t / n, 4)
        out[f"{side}_itm"] = round(f / n, 4)
        out[f"{side}_touch_ci"] = _ci(round(t / n * n_eff), n_eff)
        out[f"{side}_itm_ci"] = _ci(round(f / n * n_eff), n_eff)
        out[f"{side}_overshoot_sigma"] = _mean(cell[f"{side}_over"])
        out[f"{side}_first_touch_bars"] = _mean(cell[f"{side}_first"])
        out[f"{side}_recross"] = _mean(cell[f"{side}_recross"])
    out["gap_dn_sigma_max"] = round(max(cell["gap_dn"]), 3) if cell["gap_dn"] else None
    out["gap_up_sigma_max"] = round(max(cell["gap_up"]), 3) if cell["gap_up"] else None
    out["gap_dn_sigma_p95"] = _p95(cell["gap_dn"])
    out["gap_up_sigma_p95"] = _p95(cell["gap_up"])
    return out


def _p95(xs):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(0.95 * (len(s) - 1)))], 3)


def _ci(k: int, n: int) -> dict | None:
    w = wilson_interval(max(0, min(int(k), int(n))), int(n)) if n > 0 else None
    return None if not w else {"lo": round(w["lo"], 4), "hi": round(w["hi"], 4)}


def _independent(starts, h: int) -> int:
    n = 0
    last = None
    for i in sorted(starts):
        if last is None or i - last >= h:
            n += 1
            last = i
    return n


# ── the hierarchy ────────────────────────────────────────────────────────────
def shrink(x: float, n_eff: int, prior_p: float, kappa: float = KAPPA_DEFAULT) -> dict:
    """Empirical-Bayes (beta-binomial) shrinkage of a rate x with n_eff
    independent trials toward prior_p with prior strength kappa:
        p = (x·n_eff + κ·prior) / (n_eff + κ)
    The weight on the ticker's own evidence is n_eff/(n_eff+κ), stated."""
    n_eff = max(0, int(n_eff or 0))
    w_own = n_eff / (n_eff + kappa) if (n_eff + kappa) > 0 else 0.0
    p = (float(x) * n_eff + kappa * float(prior_p)) / (n_eff + kappa) if (n_eff + kappa) > 0 else float(prior_p)
    return {"p": round(sp._clamp01(p), 4), "weight_own": round(w_own, 3),  # noqa: SLF001
            "weight_prior": round(1.0 - w_own, 3), "kappa": kappa}


def _interp_cell_rate(cells_h: dict, k: float, field: str) -> tuple[float | None, int, int]:
    """Rate at σ-distance k from a horizon's cells, linearly interpolated
    between the two nearest measured k's. Returns (rate, n, n_eff) with the
    smaller n/n_eff of the bracket."""
    ks = sorted(kk for kk in cells_h.keys() if (cells_h[kk] or {}).get("n"))
    if not ks:
        return None, 0, 0
    if k <= ks[0]:
        c = cells_h[ks[0]]
        return c.get(field), c["n"], c["n_eff"]
    if k >= ks[-1]:
        c = cells_h[ks[-1]]
        return c.get(field), c["n"], c["n_eff"]
    for k0, k1 in zip(ks, ks[1:]):
        if k0 <= k <= k1:
            a, b = cells_h[k0], cells_h[k1]
            t = (k - k0) / (k1 - k0)
            r = a.get(field) + (b.get(field) - a.get(field)) * t
            return r, min(a["n"], b["n"]), min(a["n_eff"], b["n_eff"])
    return None, 0, 0


def nearest_horizon(table: dict, h_td: float) -> int | None:
    hs = table.get("horizons") or []
    if not hs:
        return None
    return min(hs, key=lambda h: abs(h - h_td))


def evidence_for_strike(table: dict | None, h_td: float, k: float, side: str,
                        state: str | None = None, kappa: float = KAPPA_DEFAULT) -> dict:
    """The hierarchical MEASURED estimate of P(finish ITM) and P(touch) for
    a strike k sigmas out over h sessions, for this ticker in `state`.

    Levels: this ticker in this state → this ticker in any state → the
    universe prior. Each level is shrunk toward the next with prior
    strength `kappa` (in independent windows), and the answer reports the
    weight the ticker's own history ended up carrying. Wilson intervals
    are on the ticker's own independent-window count at the level used.
    """
    side = "put" if side == "put" else "call"
    pri_i = prior_itm(h_td, k, side)
    pri_t = prior_touch(h_td, k)
    out = {"version": SP_EVIDENCE_VERSION, "h_td": h_td, "k_sigma": round(k, 3), "side": side,
           "state": state or "all", "prior": {"itm": pri_i, "touch": pri_t}, "levels": []}
    if not table or not table.get("cells"):
        out.update({"p_itm": pri_i["p"], "p_touch": pri_t["p"], "n": 0, "n_eff": 0,
                    "weight_own": 0.0, "basis": "universe prior only (no ticker history)"})
        return out
    h = nearest_horizon(table, h_td)
    cells_all = (table["cells"].get("all") or {}).get(h) or {}
    # level 2: ticker, any state, shrunk toward the prior
    r_i, n2, ne2 = _interp_cell_rate(cells_all, k, f"{side}_itm")
    r_t, _, _ = _interp_cell_rate(cells_all, k, f"{side}_touch")
    if r_i is None:
        out.update({"p_itm": pri_i["p"], "p_touch": pri_t["p"], "n": 0, "n_eff": 0,
                    "weight_own": 0.0, "basis": "universe prior only (no measurable windows)"})
        return out
    l2_i = shrink(r_i, ne2, pri_i["p"], kappa)
    l2_t = shrink(r_t, ne2, pri_t["p"], kappa)
    out["levels"].append({"level": "ticker_all_states", "n": n2, "n_eff": ne2,
                          "itm_raw": round(r_i, 4), "touch_raw": round(r_t, 4),
                          "itm": l2_i, "touch": l2_t})
    p_i, p_t, n_used, ne_used, w_own = l2_i["p"], l2_t["p"], n2, ne2, l2_i["weight_own"]
    basis = f"this ticker, any state ({ne2} independent windows) shrunk toward the universe prior"
    # level 1: ticker in state, shrunk toward level 2
    if state and state != "all":
        cells_st = (table["cells"].get(state) or {}).get(h) or {}
        r1_i, n1, ne1 = _interp_cell_rate(cells_st, k, f"{side}_itm")
        r1_t, _, _ = _interp_cell_rate(cells_st, k, f"{side}_touch")
        if r1_i is not None and ne1 > 0:
            l1_i = shrink(r1_i, ne1, p_i, kappa)
            l1_t = shrink(r1_t, ne1, p_t, kappa)
            out["levels"].append({"level": f"ticker_in_{state}", "n": n1, "n_eff": ne1,
                                  "itm_raw": round(r1_i, 4), "touch_raw": round(r1_t, 4),
                                  "itm": l1_i, "touch": l1_t})
            p_i, p_t, n_used, ne_used = l1_i["p"], l1_t["p"], n1, ne1
            w_own = l1_i["weight_own"]
            basis = (f"this ticker in state {state} ({ne1} independent windows) shrunk toward "
                     f"its own all-state rate, itself shrunk toward the universe prior")
    ci_i = _ci(round(p_i * ne_used), ne_used) if ne_used else None
    ci_t = _ci(round(p_t * ne_used), ne_used) if ne_used else None
    out.update({"p_itm": p_i, "p_touch": p_t, "p_itm_ci": ci_i, "p_touch_ci": ci_t,
                "n": n_used, "n_eff": ne_used, "weight_own": w_own,
                "horizon_used": h, "basis": basis})
    return out


def strike_context(table: dict | None, h_td: float, k: float, side: str) -> dict | None:
    """The excursion facts for a strike distance from the ticker's own all-state
    table: overshoot when breached, first-touch timing, recrossings, worst
    gap toward the strike. MEASURED; None when there is no table."""
    if not table or not table.get("cells"):
        return None
    h = nearest_horizon(table, h_td)
    cells = (table["cells"].get("all") or {}).get(h) or {}
    ks = sorted(kk for kk in cells.keys() if (cells[kk] or {}).get("n"))
    if not ks:
        return None
    kk = min(ks, key=lambda x: abs(x - k))
    c = cells[kk]
    return {"k_measured": kk, "horizon_used": h, "n": c["n"], "n_eff": c["n_eff"],
            "overshoot_sigma": c.get(f"{side}_overshoot_sigma"),
            "first_touch_bars": c.get(f"{side}_first_touch_bars"),
            "recross": c.get(f"{side}_recross"),
            "gap_toward_strike_sigma_max": c.get("gap_dn_sigma_max" if side == "put" else "gap_up_sigma_max"),
            "gap_toward_strike_sigma_p95": c.get("gap_dn_sigma_p95" if side == "put" else "gap_up_sigma_p95"),
            "basis": "MEASURED from this ticker's own windows at the nearest σ-distance"}


def current_state(bars) -> dict:
    """The conditioning state as of the LAST bar — the state a trade opened
    today would be measured against."""
    closes = [float(b.get("close") or 0) for b in bars]
    i = len(closes) - 1
    if i < SIGMA_WINDOW + 1:
        return {"vol": None, "run": None, "big": False, "states": ["all"]}
    sigma = vf.rv(closes, SIGMA_WINDOW)
    anchor = vf.rv(closes, ANCHOR_WINDOW) if len(closes) >= ANCHOR_WINDOW + 1 else None
    st = _state_at(closes, i, sigma, anchor)
    states = ["all"] + [x for x in (st["vol"], st["run"]) if x] + (["after_big_move"] if st["big"] else [])
    st["states"] = states
    st["sigma20"] = round(sigma, 4) if sigma else None
    st["anchor"] = round(anchor, 4) if anchor else None
    return st
