"""bt_validate.py — validation suite (Backtest v2, B3).

The machinery that separates "a lucky parameter set" from "an edge":

  • monte_carlo      — seeded trade-order bootstrap → drawdown/final-equity
                       distributions and risk-of-ruin at the tested sizing.
  • walk_forward     — rolling IS/OOS folds over a caller-supplied runner →
                       per-fold table + walk-forward efficiency (OOS/IS).
  • sharpe_from_curve, psr, deflated_sharpe — Probabilistic / Deflated
                       Sharpe (Bailey & López de Prado): the probability the
                       observed Sharpe beats what the BEST of N tried
                       configurations would show by pure luck.
  • plateau_score    — parameter-grid robustness: a real edge sits on a
                       plateau (neighbors perform too); a lucky peak stands
                       alone.
  • regime_matrix    — trend × vol-regime cells with a concentration flag.

Everything is pure + seeded (no wall-clock, no network, no global RNG),
so results are bit-for-bit reproducible and unit-testable.
"""
from __future__ import annotations

import math
import random
from datetime import date

from metrics import _norm_cdf

EULER_GAMMA = 0.5772156649015329


# ── Curve statistics ────────────────────────────────────────────────────────
def max_drawdown_pct(curve_values: list) -> float:
    peak = -float("inf")
    dd = 0.0
    for v in curve_values:
        peak = max(peak, v)
        if peak > 0:
            dd = max(dd, (peak - v) / peak * 100.0)
    return dd


def sharpe_from_curve(equity_curve: list, rf_annual: float = 0.0) -> dict | None:
    """Annualized Sharpe/Sortino/CAGR from a DAILY equity curve
    [{"date","equity"}]. Needs ≥ 20 points. Also returns the skew/kurtosis
    of daily returns (inputs to the deflated Sharpe)."""
    if not equity_curve or len(equity_curve) < 20:
        return None
    vals = [p["equity"] for p in equity_curve]
    rets = [vals[i] / vals[i - 1] - 1.0 for i in range(1, len(vals))
            if vals[i - 1] > 0]
    n = len(rets)
    if n < 19:
        return None
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    sd = math.sqrt(var)
    rf_d = rf_annual / 252.0
    sharpe = ((mean - rf_d) / sd) * math.sqrt(252) if sd > 0 else None
    downs = [r for r in rets if r < 0]
    dvar = sum(r * r for r in downs) / n if downs else 0.0
    dsd = math.sqrt(dvar)
    sortino = ((mean - rf_d) / dsd) * math.sqrt(252) if dsd > 0 else None
    d0 = date.fromisoformat(equity_curve[0]["date"])
    d1 = date.fromisoformat(equity_curve[-1]["date"])
    yrs = max(1e-9, (d1 - d0).days / 365.0)
    cagr = ((vals[-1] / vals[0]) ** (1 / yrs) - 1.0) * 100.0 if vals[0] > 0 and yrs > 0.05 else None
    skew = (sum((r - mean) ** 3 for r in rets) / n) / (sd ** 3) if sd > 0 else 0.0
    kurt = (sum((r - mean) ** 4 for r in rets) / n) / (sd ** 4) if sd > 0 else 3.0
    return {"sharpe": round(sharpe, 2) if sharpe is not None else None,
            "sortino": round(sortino, 2) if sortino is not None else None,
            "cagr_pct": round(cagr, 2) if cagr is not None else None,
            "daily_n": n, "skew": round(skew, 3), "kurtosis": round(kurt, 3),
            "sr_raw": sharpe, "years": round(yrs, 2)}


# ── Monte Carlo ─────────────────────────────────────────────────────────────
def monte_carlo(trade_pnls: list, start_equity: float, n_paths: int = 10_000,
                seed: int = 42, ruin_dd_pct: float = 25.0) -> dict | None:
    """Trade-order bootstrap: resample the trade P/L list with replacement
    (same count) n_paths times. The QUESTION it answers: given these
    trades' distribution, what drawdowns/final equities could the same
    strategy have produced in a different ordering/draw — and how often
    does it breach the ruin threshold (default 25% drawdown)?"""
    if not trade_pnls or len(trade_pnls) < 8 or start_equity <= 0:
        return None
    rng = random.Random(seed)
    m = len(trade_pnls)
    dds, finals = [], []
    ruined = 0
    for _ in range(n_paths):
        eq = start_equity
        peak = eq
        dd = 0.0
        for _ in range(m):
            eq += trade_pnls[rng.randrange(m)]
            peak = max(peak, eq)
            if peak > 0:
                dd = max(dd, (peak - eq) / peak * 100.0)
        dds.append(dd)
        finals.append(eq)
        if dd >= ruin_dd_pct:
            ruined += 1
    dds.sort()
    finals.sort()

    def pct(sorted_list, p):
        return round(sorted_list[min(len(sorted_list) - 1,
                                     int(p / 100.0 * len(sorted_list)))], 2)
    return {
        "n_paths": n_paths, "seed": seed, "n_trades": m,
        "max_dd_pct": {"p5": pct(dds, 5), "p50": pct(dds, 50), "p95": pct(dds, 95)},
        "final_equity": {"p5": pct(finals, 5), "p50": pct(finals, 50), "p95": pct(finals, 95)},
        "ruin_threshold_dd_pct": ruin_dd_pct,
        "risk_of_ruin_pct": round(ruined / n_paths * 100.0, 2),
        "note": ("Bootstrap of the OBSERVED trades (order + draw resampled). It cannot "
                 "invent scenarios the strategy never met — a calm test window stays calm."),
    }


# ── Walk-forward ────────────────────────────────────────────────────────────
def walk_forward(dates_sorted: list, runner, folds: int = 4,
                 is_frac: float = 0.7) -> dict | None:
    """Rolling IS/OOS validation over a caller-supplied runner:
        runner(date_lo, date_hi) -> {"total_pnl", "n_trades", "win_rate"}
    Each fold spans an equal slice of the signal window; the first
    is_frac of the fold is in-sample, the rest out-of-sample. WFE =
    Σ OOS pnl-per-trade / Σ IS pnl-per-trade (same-strategy consistency:
    a real edge keeps working on data that follows it)."""
    if not dates_sorted or len(dates_sorted) < folds * 8:
        return None
    n = len(dates_sorted)
    fold_rows = []
    is_ppt, oos_ppt = [], []
    for f in range(folds):
        a = int(f * n / folds)
        b = int((f + 1) * n / folds) - 1
        cut = a + int((b - a) * is_frac)
        if cut <= a or cut >= b:
            continue
        is_res = runner(dates_sorted[a], dates_sorted[cut])
        oos_res = runner(dates_sorted[cut + 1] if cut + 1 <= b else dates_sorted[b],
                         dates_sorted[b])
        row = {"fold": f + 1,
               "is_window": [dates_sorted[a], dates_sorted[cut]],
               "oos_window": [dates_sorted[min(cut + 1, b)], dates_sorted[b]],
               "is": is_res, "oos": oos_res}
        fold_rows.append(row)
        if is_res.get("n_trades"):
            is_ppt.append(is_res["total_pnl"] / is_res["n_trades"])
        if oos_res.get("n_trades"):
            oos_ppt.append(oos_res["total_pnl"] / oos_res["n_trades"])
    if not fold_rows:
        return None
    wfe = None
    if is_ppt and oos_ppt and sum(is_ppt) != 0:
        wfe = round((sum(oos_ppt) / len(oos_ppt)) / (sum(is_ppt) / len(is_ppt)), 2)
    oos_positive = sum(1 for r in fold_rows
                       if (r["oos"].get("total_pnl") or 0) > 0 and r["oos"].get("n_trades"))
    verdict = None
    if wfe is not None:
        if wfe >= 0.5 and oos_positive >= max(1, len(fold_rows) - 1):
            verdict = "consistent out of sample"
        elif wfe > 0:
            verdict = "weaker out of sample — size expectations down"
        else:
            verdict = "does NOT hold out of sample — likely curve-fit"
    return {"folds": fold_rows, "wf_efficiency": wfe,
            "oos_positive_folds": f"{oos_positive}/{len(fold_rows)}",
            "verdict": verdict}


# ── Deflated Sharpe (Bailey & López de Prado) ──────────────────────────────
def psr(sr: float, sr_benchmark: float, n_obs: int, skew: float,
        kurt: float) -> float | None:
    """Probabilistic Sharpe Ratio: P(true SR > sr_benchmark | observed sr).
    sr here is per-period (NOT annualized): pass sr_annual/√252 for daily.
    """
    if n_obs < 20:
        return None
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return None
    z = (sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom)
    return _norm_cdf(z)


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """E[max SR] across n_trials of zero-skill configurations (the hurdle
    the observed Sharpe must clear once you admit how many combinations
    were tried)."""
    if n_trials <= 1:
        return 0.0
    def inv_cdf(p):
        # Acklam-style rational approximation of Φ⁻¹ — plenty for a hurdle.
        lo, hi = -8.0, 8.0
        for _ in range(80):
            mid = (lo + hi) / 2
            if _norm_cdf(mid) < p:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    sd = math.sqrt(max(var_sr, 1e-12))
    return sd * ((1 - EULER_GAMMA) * inv_cdf(1 - 1.0 / n_trials)
                 + EULER_GAMMA * inv_cdf(1 - 1.0 / (n_trials * math.e)))


def deflated_sharpe(sr_annual: float, n_obs: int, skew: float, kurt: float,
                    n_trials: int, sr_trials_annual: list | None = None) -> dict | None:
    """DSR = PSR evaluated at the expected-max-of-trials hurdle. ≥0.95 —
    the Sharpe is very unlikely to be a selection artifact; ≤0.5 — the
    result is indistinguishable from picking the luckiest of your tries."""
    if n_obs < 20:
        return None
    sr_d = sr_annual / math.sqrt(252)
    if sr_trials_annual and len(sr_trials_annual) > 1:
        m = sum(sr_trials_annual) / len(sr_trials_annual)
        var_tr = sum((x - m) ** 2 for x in sr_trials_annual) / (len(sr_trials_annual) - 1)
        var_sr = var_tr / 252.0
    else:
        var_sr = 1.0 / max(n_obs - 1, 1)
    hurdle = expected_max_sharpe(max(n_trials, 1), var_sr)
    p = psr(sr_d, hurdle, n_obs, skew, kurt)
    if p is None:
        return None
    verdict = ("statistically real after accounting for trials" if p >= 0.95
               else "plausible but not proven — could be selection luck" if p >= 0.5
               else "indistinguishable from picking your luckiest try")
    return {"dsr": round(p, 3), "n_trials": n_trials,
            "hurdle_sr_annual": round(hurdle * math.sqrt(252), 3),
            "verdict": verdict}


# ── Parameter-grid robustness ───────────────────────────────────────────────
def plateau_score(grid: list, x_key: str, y_key: str, metric: str) -> dict | None:
    """grid: [{x_key, y_key, metric, ...}]. Finds the best cell and scores
    its 3×3 neighborhood: plateau = mean(neighbors)/best (1.0 = flat
    plateau, ≤0 = the peak stands alone in losing territory). The ROBUST
    recommendation is the cell with the best neighborhood mean, which is
    usually NOT the peak."""
    if not grid:
        return None
    xs = sorted({g[x_key] for g in grid})
    ys = sorted({g[y_key] for g in grid})
    by = {(g[x_key], g[y_key]): g for g in grid}
    def neigh_mean(xv, yv):
        xi, yi = xs.index(xv), ys.index(yv)
        vals = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if 0 <= xi + dx < len(xs) and 0 <= yi + dy < len(ys):
                    c = by.get((xs[xi + dx], ys[yi + dy]))
                    if c is not None and c.get(metric) is not None:
                        vals.append(c[metric])
        return sum(vals) / len(vals) if vals else None
    best = max(grid, key=lambda g: g.get(metric) or -float("inf"))
    robust = max(grid, key=lambda g: neigh_mean(g[x_key], g[y_key]) or -float("inf"))
    bm = best.get(metric) or 0.0
    nm = neigh_mean(best[x_key], best[y_key])
    return {"best": best, "robust": robust,
            "plateau": round(nm / bm, 2) if bm and nm is not None else None,
            "note": ("Pick the ROBUST cell (best 3×3-neighborhood average), not the peak — "
                     "a peak whose neighbors fail is a curve-fit signature.")}


# ── Regime matrix ───────────────────────────────────────────────────────────
def vol_terciles(vix_by_date: dict) -> dict:
    """date → 'low'/'mid'/'high' by the VIX close's tercile within the
    trailing year (past data only, same convention as bt_iv)."""
    dates = sorted(vix_by_date.keys())
    closes = [vix_by_date[d] for d in dates]
    out = {}
    for i, d in enumerate(dates):
        if i < 25:
            continue
        w = sorted(closes[max(0, i - 252):i + 1])
        v = closes[i]
        lo_cut = w[len(w) // 3]
        hi_cut = w[2 * len(w) // 3]
        out[d] = "low" if v <= lo_cut else ("high" if v >= hi_cut else "mid")
    return out


def regime_matrix(trades: list, vol_by_date: dict) -> dict | None:
    """trend (from each trade's SPY regime tag) × vol tercile → per-cell
    {n, pnl, win_rate}, plus a concentration warning when one cell holds
    >70% of the positive P/L."""
    if not trades:
        return None
    cells = {}
    for t in trades:
        trend = t.get("regime") or "unknown"
        vol = vol_by_date.get(t["entry_date"], "unknown")
        c = cells.setdefault(f"{trend}|{vol}", {"trend": trend, "vol": vol,
                                                "n": 0, "pnl": 0.0, "wins": 0})
        c["n"] += 1
        c["pnl"] = round(c["pnl"] + t["pnl"], 2)
        c["wins"] += 1 if t["pnl"] > 0 else 0
    for c in cells.values():
        c["win_rate"] = round(c["wins"] / c["n"] * 100.0, 1)
    pos = {k: c["pnl"] for k, c in cells.items() if c["pnl"] > 0}
    warning = None
    if pos:
        top_k = max(pos, key=pos.get)
        share = pos[top_k] / sum(pos.values()) * 100.0
        if share > 70 and len(cells) > 1:
            c = cells[top_k]
            warning = (f"{share:.0f}% of the positive P/L comes from ONE regime "
                       f"({c['trend']} trend / {c['vol']} vol) — the edge may not "
                       "travel outside it.")
    return {"cells": sorted(cells.values(), key=lambda c: -c["pnl"]),
            "concentration_warning": warning}
