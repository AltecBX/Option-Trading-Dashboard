"""spike_evidence.py — what a stock does AFTER it has already run (v4.82).

The trade this exists for: a stock is up hard today, its same-day calls are
briefly rich, and the question is whether a strike above the level it has
already reached will finish out of the money.

Everything here is measured from daily prints. Nothing is modeled except
where it says so in the payload.

THE UNIT IS SIGMA, NOT PERCENT
------------------------------
A 3.6% day on a stock running at 81% annualised volatility is an ordinary
session; a 16.4% day on a 55%-vol name is a four-sigma event. Ranked on
percentage distance those two come out backwards, which is why every move
and every strike here is divided by the stock's OWN 20-day daily sigma.
That normalisation was tested before it was trusted: across volatility
quartiles (under 21% to over 49% annualised) and across price bands (under
$20 to over $100), the probability of closing a sigma beyond a three-sigma
move sits between 20.6% and 22.1%. One table serves every name, so a ticker
with only a handful of its own spikes can safely borrow the universe's.

WHAT THE MEASUREMENTS SAY
-------------------------
Two facts hold at once, and the trade lives between them:

  * A big mover rarely FINISHES at its high. At three sigma it closes
    within a whisker of the high 9.0% of the time; at four sigma 6.8%; at
    five sigma 5.1%. The bigger the move, the more it hands back.
  * But the level on the screen is often not the high yet. After a
    four-sigma move a strike a quarter-sigma above is TOUCHED 83% of the
    time and closed above 44.6%.

So the seller is not paid for the stock falling back — most of them do.
The seller is paid for the high already being in. That is a function of how
much session is left, which is the one thing daily bars cannot see, so the
tables here are conditioned on the session HIGH having reached the move and
are therefore an UPPER BOUND on the risk: they credit the stock with the
whole rest of the day. `remaining_session` scales that bound down with the
clock, MEASURED from an intraday profile when the caller supplies one and
labeled MODELED when it falls back to the clock.

The counting is honest in the way that matters here: one spike is one
session, so these are independent trials and n_eff is simply n — unlike the
overlapping windows in sp_evidence, which need a spacing correction.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

SPIKE_EVIDENCE_VERSION = "spike-evidence-1.0.0"

NEAR_HIGH_SIGMA = 0.10        # "finished at its high" tolerance
SIGMA_WINDOW = 20             # sessions behind the daily sigma
MIN_SIGMA_BARS = 12
KAPPA_DEFAULT = 60            # universe weight, in comparable sessions
Z95 = 1.959963985
_PRIOR: dict | None = None


# ── basics ──────────────────────────────────────────────────────────────────
def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


def daily_sigma(bars: list, window: int = SIGMA_WINDOW, end: int | None = None) -> float | None:
    """The stock's own daily sigma from the `window` sessions BEFORE `end`.

    Point-in-time by construction: a caller asking about session i never
    sees session i's own return."""
    n = len(bars) if end is None else end
    lo = max(1, n - window)
    r = []
    for j in range(lo, n):
        c0, c1 = _num(bars[j - 1].get("close")), _num(bars[j].get("close"))
        if c0 and c1 and c0 > 0 and c1 > 0:
            r.append(math.log(c1 / c0))
    if len(r) < MIN_SIGMA_BARS:
        return None
    m = sum(r) / len(r)
    v = sum((x - m) ** 2 for x in r) / (len(r) - 1)
    return math.sqrt(v) if v > 0 else None


def in_sigma(price: float, prev_close: float, sigma: float) -> float | None:
    """How far `price` sits above yesterday's close, in the stock's sigma."""
    p, pc = _num(price), _num(prev_close)
    if not p or not pc or p <= 0 or pc <= 0 or not sigma or sigma <= 0:
        return None
    return math.log(p / pc) / sigma


# ── the session clock ───────────────────────────────────────────────────────
def remaining_session(elapsed_frac: float, profile: list | None = None) -> dict:
    """The share of the session's VARIANCE still ahead.

    `elapsed_frac` is how much of the regular session has already gone
    (0 at the open, 1 at the bell). With a `profile` — pairs of
    (elapsed, variance share still to come) measured from the app's own
    minute bars — this is MEASURED. Without one it falls back to the clock,
    which assumes variance arrives evenly through the day; real sessions are
    front- and back-loaded, so that fallback is labeled MODELED and is the
    single biggest approximation in this module."""
    e = min(1.0, max(0.0, _num(elapsed_frac) or 0.0))
    if profile:
        pts = sorted((float(a), float(b)) for a, b in profile)
        if pts:
            if e <= pts[0][0]:
                frac, basis = pts[0][1], "MEASURED"
            elif e >= pts[-1][0]:
                frac, basis = pts[-1][1], "MEASURED"
            else:
                frac, basis = pts[-1][1], "MEASURED"
                for (a0, v0), (a1, v1) in zip(pts, pts[1:]):
                    if a0 <= e <= a1:
                        w = 0.0 if a1 == a0 else (e - a0) / (a1 - a0)
                        frac = v0 + w * (v1 - v0)
                        break
            return {"variance_left": max(0.0, min(1.0, frac)), "elapsed": e,
                    "scale": math.sqrt(max(0.0, min(1.0, frac))), "basis": basis}
    frac = 1.0 - e
    return {"variance_left": frac, "elapsed": e, "scale": math.sqrt(frac),
            "basis": "MODELED (clock; variance assumed even through the session)"}


# ── the universe prior ──────────────────────────────────────────────────────
def universe_prior(refresh: bool = False) -> dict:
    """The measured table every name falls back on (fixtures/spike_universe.json)."""
    global _PRIOR
    if _PRIOR is not None and not refresh:
        return _PRIOR
    p = Path(__file__).resolve().parent / "fixtures" / "spike_universe.json"
    try:
        _PRIOR = json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        _PRIOR = {"cells": {}, "n_sessions": 0, "n_names": 0}
    return _PRIOR


def _grid(prior: dict) -> list[float]:
    out = []
    for k in (prior.get("cells") or {}):
        v = _num(k)
        if v is not None:
            out.append(v)
    return sorted(out)


def _interp(lo_v, hi_v, lo_k, hi_k, k):
    if lo_v is None:
        return hi_v
    if hi_v is None:
        return lo_v
    if hi_k == lo_k:
        return lo_v
    w = (k - lo_k) / (hi_k - lo_k)
    return lo_v + w * (hi_v - lo_v)


def prior_cell(move_sigma: float, beyond_sigma: float) -> dict | None:
    """The universe's answer for a move of this size and a strike this far
    beyond it, interpolated on both axes of the measured grid.

    Off the end of the grid it clamps rather than extrapolating: a
    seven-sigma move is reported with the six-sigma row and says so, because
    inventing a number past the measured edge is how a table starts lying."""
    prior = universe_prior()
    cells = prior.get("cells") or {}
    if not cells:
        return None
    moves = _grid(prior)
    if not moves:
        return None
    m = max(moves[0], min(moves[-1], float(move_sigma)))
    clamped = abs(m - float(move_sigma)) > 1e-9
    lo_m = max([x for x in moves if x <= m], default=moves[0])
    hi_m = min([x for x in moves if x >= m], default=moves[-1])

    def at(mk: float) -> dict | None:
        row = cells.get(f"{mk:g}")
        if not row:
            return None
        ks = sorted(float(x) for x in (row.get("strikes") or {}))
        if not ks:
            return None
        b = max(ks[0], min(ks[-1], float(beyond_sigma)))
        b_clamped = abs(b - float(beyond_sigma)) > 1e-9
        lo_b = max([x for x in ks if x <= b], default=ks[0])
        hi_b = min([x for x in ks if x >= b], default=ks[-1])
        a, c = row["strikes"][f"{lo_b:g}"], row["strikes"][f"{hi_b:g}"]
        return {
            "p_close": _interp(a["p_close"], c["p_close"], lo_b, hi_b, b),
            "p_touch": _interp(a["p_touch"], c["p_touch"], lo_b, hi_b, b),
            "settle_sigma": _interp(a["settle_sigma"], c["settle_sigma"], lo_b, hi_b, b),
            "n": row.get("n", 0), "p_finishes_at_high": row.get("p_finishes_at_high"),
            "clamped": b_clamped,
        }

    a, c = at(lo_m), at(hi_m)
    if a is None and c is None:
        return None
    if a is None or c is None:
        out = dict(a or c)
        out["move_clamped"] = clamped
        return out
    out = {k: _interp(a.get(k), c.get(k), lo_m, hi_m, m)
           for k in ("p_close", "p_touch", "settle_sigma", "p_finishes_at_high")}
    out["n"] = min(a.get("n", 0), c.get("n", 0))
    out["clamped"] = bool(a.get("clamped") or c.get("clamped"))
    out["move_clamped"] = clamped
    return out


# ── this ticker's own history ───────────────────────────────────────────────
def ticker_table(bars: list, moves=(1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0),
                 beyond=(0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)) -> dict:
    """This stock's OWN record of what it does after a run of each size.

    One row per session, so every count is an independent trial."""
    events = []
    for i in range(SIGMA_WINDOW + 2, len(bars)):
        pc = _num(bars[i - 1].get("close"))
        b = bars[i]
        hi, cl = _num(b.get("high")), _num(b.get("close"))
        if not pc or not hi or not cl or pc <= 0 or hi <= 0 or cl <= 0:
            continue
        sd = daily_sigma(bars, end=i)
        if not sd:
            continue
        events.append((math.log(hi / pc) / sd, math.log(cl / pc) / sd))
    cells: dict = {}
    for m in moves:
        at = [(h, c) for h, c in events if h >= m]
        n = len(at)
        if n == 0:
            continue
        row = {"n": n, "n_eff": n,
               "p_finishes_at_high": sum(1 for h, c in at if h - c <= NEAR_HIGH_SIGMA) / n,
               "strikes": {}}
        for e in beyond:
            s = m + e
            k = sum(1 for _h, c in at if c >= s)
            t = sum(1 for h, _c in at if h >= s)
            row["strikes"][f"{e:g}"] = {
                "p_close": k / n, "p_touch": t / n,
                "settle_sigma": sum(max(c - s, 0.0) for _h, c in at) / n,
                "k": k,
            }
        cells[f"{m:g}"] = row
    return {"version": SPIKE_EVIDENCE_VERSION, "n_sessions": len(events), "cells": cells}


def _own_cell(table: dict, move_sigma: float, beyond_sigma: float) -> dict | None:
    """The ticker's own row at or below this move size — never above it, so
    the sample is always of runs at least as big as the one in front of us."""
    cells = (table or {}).get("cells") or {}
    if not cells:
        return None
    keys = sorted((float(k) for k in cells), reverse=True)
    pick = next((k for k in keys if k <= float(move_sigma) + 1e-9), None)
    if pick is None:
        return None
    row = cells[f"{pick:g}"]
    ks = sorted(float(x) for x in (row.get("strikes") or {}))
    if not ks:
        return None
    b = max(ks[0], min(ks[-1], float(beyond_sigma)))
    lo_b = max([x for x in ks if x <= b], default=ks[0])
    hi_b = min([x for x in ks if x >= b], default=ks[-1])
    a, c = row["strikes"][f"{lo_b:g}"], row["strikes"][f"{hi_b:g}"]
    return {
        "p_close": _interp(a["p_close"], c["p_close"], lo_b, hi_b, b),
        "p_touch": _interp(a["p_touch"], c["p_touch"], lo_b, hi_b, b),
        "settle_sigma": _interp(a["settle_sigma"], c["settle_sigma"], lo_b, hi_b, b),
        "n": row["n"], "n_eff": row["n_eff"], "move_level": pick,
        "p_finishes_at_high": row.get("p_finishes_at_high"),
    }


def shrink(own: float | None, n: int, prior: float | None, kappa: float = KAPPA_DEFAULT) -> float | None:
    """Empirical Bayes: the ticker's own rate pulled toward the universe by
    how little of its own evidence there is. A name with three spikes on
    file is answered almost entirely by the universe, and the payload says
    what share was its own."""
    if prior is None:
        return own
    if own is None or n <= 0:
        return prior
    return (own * n + prior * kappa) / (n + kappa)


# ── the answer for one strike ───────────────────────────────────────────────
def evidence_for_strike(move_sigma: float, beyond_sigma: float,
                        table: dict | None = None, kappa: float = KAPPA_DEFAULT) -> dict:
    """Everything measured about selling a strike `beyond_sigma` above a run
    of `move_sigma`, this ticker's own history shrunk toward the universe."""
    pri = prior_cell(move_sigma, beyond_sigma)
    own = _own_cell(table, move_sigma, beyond_sigma) if table else None
    n_own = int((own or {}).get("n_eff") or 0)
    p_close = shrink((own or {}).get("p_close"), n_own, (pri or {}).get("p_close"), kappa)
    p_touch = shrink((own or {}).get("p_touch"), n_own, (pri or {}).get("p_touch"), kappa)
    settle = shrink((own or {}).get("settle_sigma"), n_own, (pri or {}).get("settle_sigma"), kappa)
    at_high = shrink((own or {}).get("p_finishes_at_high"), n_own,
                     (pri or {}).get("p_finishes_at_high"), kappa)
    weight_own = (n_own / (n_own + kappa)) if n_own else 0.0
    ci = wilson(int(round((p_close or 0) * max(n_own, 1))), max(n_own, 1)) if n_own >= 20 else None
    levels = []
    if own:
        levels.append({"level": "this ticker", "n": own["n"],
                       "p_close": own["p_close"], "at_move": own.get("move_level")})
    if pri:
        levels.append({"level": "universe", "n": pri.get("n"), "p_close": pri.get("p_close")})
    return {
        "move_sigma": round(float(move_sigma), 3),
        "beyond_sigma": round(float(beyond_sigma), 3),
        "p_close_above": p_close, "p_touch": p_touch,
        "settle_sigma": settle, "p_finishes_at_high": at_high,
        "n_own": n_own, "weight_own": round(weight_own, 3),
        "ci_own": list(ci) if ci else None,
        "levels": levels,
        "grade": ("MEASURED" if n_own >= 20 else
                  "MOSTLY POOLED" if n_own > 0 else "POOLED"),
        "basis": ("this stock's own sessions shrunk toward a universe of "
                  f"{universe_prior().get('n_names', 0)} names over "
                  f"{universe_prior().get('n_sessions', 0):,} sessions; conditioned on the "
                  "session high reaching the move, so the whole remaining session is "
                  "credited to the stock"),
        "clamped": bool((pri or {}).get("clamped") or (pri or {}).get("move_clamped")),
    }


def settlement(ev: dict, sigma: float, prev_close: float,
               elapsed_frac: float | None = None, profile: list | None = None) -> dict:
    """What the call is expected to pay back, in dollars a share.

    This is the number that decides the trade: the credit on the screen
    minus this. No volatility model is involved — the settlement is measured
    and only the clock adjustment is an approximation."""
    s, pc = _num(sigma), _num(prev_close)
    base = _num(ev.get("settle_sigma"))
    if base is None or not s or not pc:
        return {"dollars": None, "basis": "UNAVAILABLE"}
    rem = remaining_session(elapsed_frac, profile) if elapsed_frac is not None else None
    scale = rem["scale"] if rem else 1.0
    return {
        "dollars": base * scale * s * pc,
        "full_session_dollars": base * s * pc,
        "sigma": base * scale,
        "session_scale": scale,
        "session_basis": (rem or {}).get("basis", "whole session credited"),
        "basis": "MEASURED settlement" + ("" if rem is None else f", scaled by the session left ({rem['basis']})"),
    }


def assess(spot: float, strike: float, prev_close: float, bars: list,
           credit: float | None = None, elapsed_frac: float | None = None,
           profile: list | None = None, table: dict | None = None,
           kappa: float = KAPPA_DEFAULT) -> dict | None:
    """The whole picture for one same-day call, from prices the caller has.

    `spot` is where the stock is now (the run it has already made), `strike`
    the call being sold, `credit` what the market pays for it. Returns None
    only when the stock's own volatility cannot be established."""
    sigma = daily_sigma(bars)
    if not sigma:
        return None
    m = in_sigma(spot, prev_close, sigma)
    k = in_sigma(strike, prev_close, sigma)
    if m is None or k is None:
        return None
    beyond = k - m
    ev = evidence_for_strike(m, beyond, table=table, kappa=kappa)
    st = settlement(ev, sigma, prev_close, elapsed_frac, profile)
    out = {"version": SPIKE_EVIDENCE_VERSION,
           "sigma_daily": sigma, "sigma_annual": sigma * math.sqrt(252),
           "move_pct": (spot / prev_close - 1) * 100.0,
           "strike_pct": (strike / prev_close - 1) * 100.0,
           "move_sigma": m, "strike_sigma": k, "beyond_sigma": beyond,
           "evidence": ev, "settlement": st}
    c = _num(credit)
    if c is not None and st.get("dollars") is not None:
        out["edge_per_share"] = c - st["dollars"]
        out["edge_per_contract"] = (c - st["dollars"]) * 100.0
        out["credit"] = c
    return out
