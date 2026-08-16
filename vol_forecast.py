"""vol_forecast.py — Expected realized volatility for the Premium Edge engine.

The engine's core question is whether implied volatility is rich relative to
the volatility the stock is LIKELY TO REALIZE — not relative to what it
realized last month. This module produces that forward estimate:

    ExpectedRV30 = what we expect annualized realized vol to be over the
                   next ~30 calendar days (21 trading days)

from daily OHLC bars alone (Schwab serves up to 20 years; every value here
is MEASURED from underlying price history, never modeled from option data).

Conventions (matching the house contract in metrics.py / bt_iv.py):
  - bars: oldest-first [{"date","open","high","low","close",...}] — Schwab
    shape; date sliced with [:10] everywhere.
  - vols: ANNUALIZED DECIMALS (0.32 = 32%), sqrt(252) annualization —
    identical to bt_iv.hv and every realized-vol number in the app. IV uses
    calendar/365 for option pricing; both are annualized figures and the
    IV-vs-RV comparison is made in annualized space (documented in
    PREMIUM_EDGE.md).
  - windows: TRADING DAYS. "RV30" follows the spec's naming (30 trailing
    trading days); the forecast HORIZON is 21 trading days ≈ 30 calendar.

No lookahead, ever: every estimator at index i uses bars[:i+1] only, and the
validation target at i is the realized vol of bars[i+1 .. i+H]. The tests
prove that mutating future bars cannot change the forecast at i.

Model selection is walk-forward: candidates are scored out-of-sample by
QLIKE (the standard variance-forecast loss — penalizes under-forecasting
vol harder than over-forecasting, which is the correct asymmetry for an
option SELLER). A per-ticker winner is only adopted when it beats the
global blend by a configured margin on BOTH halves of the evaluation
period; otherwise the robust global blend is used and labeled as such.
"""

from __future__ import annotations

import math

TRADING_DAYS = 252.0
HORIZON_TD_DEFAULT = 21          # ≈ 30 calendar days

# Global default blend — deliberately boring and diversified: one trailing
# estimator, one fast-decay estimator, one range-based estimator, shrunk
# toward the long-run anchor. validate_global() in test_vol_forecast.py and
# the /api/edge/forecast_check endpoint exist so these weights are checked
# against walk-forward evidence rather than trusted.
GLOBAL_WEIGHTS = {"RV20": 0.30, "EWMA94": 0.35, "PARK20": 0.35}
ANCHOR_WINDOW = 252
ANCHOR_SHRINK = 0.25             # weight on the long-run anchor


# ── primitive estimators ────────────────────────────────────────────────────

def log_returns(closes):
    """Close-to-close log returns; None-safe, skips non-positive prices."""
    out = []
    for a, b in zip(closes, closes[1:]):
        try:
            if a and b and a > 0 and b > 0:
                out.append(math.log(b / a))
        except (TypeError, ValueError):
            continue
    return out


def rv(closes, window):
    """Trailing close-to-close realized vol, annualized decimal.

    Same math as bt_iv.hv (log returns, sample variance ddof=1, sqrt(252))
    expressed over the END of the series. None when history is short."""
    rets = log_returns(closes)
    if len(rets) < window or window < 2:
        return None
    tail = rets[-window:]
    mean = sum(tail) / window
    var = sum((r - mean) ** 2 for r in tail) / (window - 1)
    if var < 0:
        return None
    return math.sqrt(var * TRADING_DAYS)


def ewma_vol(closes, lam=0.94):
    """RiskMetrics EWMA volatility, annualized decimal.

    var_t = lam·var_{t-1} + (1−lam)·r_t²; seeded with the first 20 returns'
    simple variance so the recursion starts from something sane."""
    rets = log_returns(closes)
    if len(rets) < 25:
        return None
    seed = rets[:20]
    var = sum(r * r for r in seed) / len(seed)
    for r in rets[20:]:
        var = lam * var + (1.0 - lam) * r * r
    if var <= 0:
        return None
    return math.sqrt(var * TRADING_DAYS)


def parkinson_vol(bars, window=20):
    """Parkinson high-low range volatility, annualized decimal.

    sigma² = (1/(4·ln2)) · mean(ln(H/L)²) per day. More sample-efficient
    than close-to-close for the same window, but blind to overnight gaps —
    which is why it is one voice in a blend, never the whole answer.
    Requires usable high/low on ≥80% of the window's bars."""
    rows = []
    for b in bars[-window:]:
        try:
            h, lo = float(b.get("high") or 0), float(b.get("low") or 0)
            if h > 0 and lo > 0 and h >= lo:
                rows.append(math.log(h / lo) ** 2)
        except (TypeError, ValueError):
            continue
    if len(rows) < max(5, int(window * 0.8)):
        return None
    daily_var = sum(rows) / len(rows) / (4.0 * math.log(2.0))
    return math.sqrt(daily_var * TRADING_DAYS)


def atr_pct(bars, window=14):
    """Average True Range as % of the last close — context metric only
    (it measures range in price terms, not a variance forecast)."""
    if len(bars) < window + 1:
        return None
    trs = []
    for prev, cur in zip(bars[-(window + 1):-1], bars[-window:]):
        try:
            h, lo = float(cur.get("high") or 0), float(cur.get("low") or 0)
            pc = float(prev.get("close") or 0)
            if h <= 0 or lo <= 0 or pc <= 0:
                continue
            trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        except (TypeError, ValueError):
            continue
    if len(trs) < max(5, int(window * 0.8)):
        return None
    last_close = float(bars[-1].get("close") or 0)
    if last_close <= 0:
        return None
    return (sum(trs) / len(trs)) / last_close * 100.0


def gap_stats(bars, window=63):
    """Overnight gap behavior over the trailing window (MEASURED):
    gap_vol (annualized vol of open-vs-prior-close log gaps), frequency of
    |gap| ≥ 2%, and the largest absolute gap %. Rich gap history is a
    danger-model input — close-to-close RV already contains gaps, but a
    name that moves BY gaps offers a seller no intraday exit."""
    rows = bars[-(window + 1):]
    gaps = []
    for prev, cur in zip(rows, rows[1:]):
        try:
            o, pc = float(cur.get("open") or 0), float(prev.get("close") or 0)
            if o > 0 and pc > 0:
                gaps.append(math.log(o / pc))
        except (TypeError, ValueError):
            continue
    if len(gaps) < 10:
        return None
    mean = sum(gaps) / len(gaps)
    var = sum((g - mean) ** 2 for g in gaps) / (len(gaps) - 1)
    big = sum(1 for g in gaps if abs(g) >= 0.0198)  # ln ≈ 2%
    return {
        "gap_vol": round(math.sqrt(max(var, 0.0) * TRADING_DAYS), 4),
        "gap_freq_2pct": round(big / len(gaps), 4),
        "max_gap_pct": round(max(abs(g) for g in gaps) * 100.0, 2),
        "n": len(gaps),
    }


def forward_rv(closes, i, horizon):
    """Realized vol of closes[i .. i+horizon] — the VALIDATION TARGET.
    Uses only future bars relative to i; never feed this into a live
    forecast (the no-lookahead test enforces the separation)."""
    seg = closes[i:i + horizon + 1]
    if len(seg) < horizon + 1:
        return None
    return rv(seg, horizon)


# ── candidate set ───────────────────────────────────────────────────────────

CANDIDATE_NAMES = ("RV5", "RV10", "RV20", "RV30", "RV60", "EWMA94", "PARK20")


def candidates(bars, cfg=None):
    """All candidate forecasts from history ending at bars[-1]. Values are
    annualized decimals; absent candidates are omitted (never zero)."""
    closes = [float(b.get("close") or 0) for b in bars]
    lam = float((cfg or {}).get("ewma_lambda", 0.94))
    out = {}
    for name, w in (("RV5", 5), ("RV10", 10), ("RV20", 20), ("RV30", 30), ("RV60", 60)):
        v = rv(closes, w)
        if v is not None:
            out[name] = v
    v = ewma_vol(closes, lam)
    if v is not None:
        out["EWMA94"] = v
    v = parkinson_vol(bars, 20)
    if v is not None:
        out["PARK20"] = v
    return out


def _blend(cands, weights, anchor, shrink):
    """Weighted blend of available candidates, shrunk toward the long-run
    anchor. Weights renormalize over the candidates actually present."""
    avail = {k: w for k, w in weights.items() if cands.get(k) is not None}
    if not avail:
        return None
    tot = sum(avail.values())
    core = sum(cands[k] * w for k, w in avail.items()) / tot
    if anchor is not None and shrink > 0:
        return shrink * anchor + (1.0 - shrink) * core
    return core


# ── walk-forward validation & model choice ──────────────────────────────────

def qlike(forecast_vol, realized_vol):
    """QLIKE loss on VARIANCES: r²/f² − ln(r²/f²) − 1. Zero when perfect;
    grows fast when vol is UNDER-forecast — the asymmetry a premium seller
    needs (an under-forecast means selling too cheap into a storm)."""
    if not forecast_vol or not realized_vol or forecast_vol <= 0 or realized_vol <= 0:
        return None
    x = (realized_vol * realized_vol) / (forecast_vol * forecast_vol)
    return x - math.log(x) - 1.0


def walk_forward_scores(bars, cfg=None):
    """Score every candidate (and the global blend) out-of-sample.

    Steps through history: at each eval index i (spaced eval_step_td apart
    to limit overlap), forecasts use bars[:i+1] only, the target is the
    realized vol of bars[i+1..i+H]. Returns per-candidate mean QLIKE,
    RMSE in vol points, mean bias in vol points, and the eval count.
    Pure function — deterministic, no clock, no I/O."""
    cfg = cfg or {}
    horizon = int(cfg.get("horizon_td", HORIZON_TD_DEFAULT))
    step = max(1, int(cfg.get("eval_step_td", 5)))
    warmup = max(80, int(cfg.get("min_history_bars", 120)))
    closes = [float(b.get("close") or 0) for b in bars]
    names = list(CANDIDATE_NAMES) + ["GLOBAL"]
    acc = {n: {"ql": [], "err": []} for n in names}
    n_evals = 0
    for i in range(warmup, len(bars) - horizon - 1, step):
        tgt = forward_rv(closes, i, horizon)
        if tgt is None or tgt <= 0:
            continue
        hist = bars[:i + 1]
        cands = candidates(hist, cfg)
        anchor = rv(closes[:i + 1], int(cfg.get("anchor_window", ANCHOR_WINDOW)))
        cands["GLOBAL"] = _blend(
            cands, cfg.get("global_weights", GLOBAL_WEIGHTS),
            anchor, float(cfg.get("anchor_shrink", ANCHOR_SHRINK)))
        n_evals += 1
        for n in names:
            f = cands.get(n)
            q = qlike(f, tgt)
            if q is not None:
                acc[n]["ql"].append(q)
                acc[n]["err"].append((f - tgt) * 100.0)
    out = {}
    for n in names:
        ql, err = acc[n]["ql"], acc[n]["err"]
        if len(ql) < 8:
            continue
        out[n] = {
            "qlike": round(sum(ql) / len(ql), 5),
            "rmse_volpts": round(math.sqrt(sum(e * e for e in err) / len(err)), 2),
            "bias_volpts": round(sum(err) / len(err), 2),
            "n": len(ql),
            # split-half QLIKE for the robustness gate
            "qlike_h1": round(sum(ql[: len(ql) // 2]) / max(1, len(ql) // 2), 5),
            "qlike_h2": round(sum(ql[len(ql) // 2:]) / max(1, len(ql) - len(ql) // 2), 5),
        }
    return {"n_evals": n_evals, "horizon_td": horizon, "scores": out}


def choose_model(bars, cfg=None):
    """Pick the forecasting model for this ticker, with discipline.

    A per-ticker candidate replaces the global blend ONLY when it beats the
    blend's out-of-sample QLIKE by min_improvement_frac AND wins on both
    halves of the eval period AND there are ≥ min_evals eval points.
    Anything less keeps the robust global blend, honestly labeled."""
    cfg = cfg or {}
    wf = walk_forward_scores(bars, cfg)
    scores = wf.get("scores", {})
    g = scores.get("GLOBAL")
    min_evals = int(cfg.get("min_evals", 40))
    min_impr = float(cfg.get("min_improvement_frac", 0.05))
    if not g:
        return {"model": "GLOBAL", "method": "global default (insufficient history to validate)",
                "scores": scores, "n_evals": wf["n_evals"]}
    best_name, best = None, None
    for name in CANDIDATE_NAMES:
        s = scores.get(name)
        if not s or s["n"] < min_evals:
            continue
        if best is None or s["qlike"] < best["qlike"]:
            best_name, best = name, s
    if (best is not None
            and best["qlike"] < g["qlike"] * (1.0 - min_impr)
            and best["qlike_h1"] < g["qlike_h1"]
            and best["qlike_h2"] < g["qlike_h2"]):
        return {"model": best_name,
                "method": f"ticker-validated {best_name} (n={best['n']}, "
                          f"QLIKE {best['qlike']} vs blend {g['qlike']})",
                "scores": scores, "n_evals": wf["n_evals"]}
    return {"model": "GLOBAL",
            "method": f"global blend (validated, n={g['n']})",
            "scores": scores, "n_evals": wf["n_evals"]}


# ── the forecast ────────────────────────────────────────────────────────────

def expected_rv30(bars, cfg=None, choice=None, earnings_within_horizon=False,
                  earnings_hist_avg_abs_pct=None, earnings_hist_n=0):
    """ExpectedRV30 — the number IV30 is judged against.

    Returns {"erv30", "erv30_event", "method", "components", "anchor",
             "n_bars", "quality", "event_adj"} or None when history is too
    short to say anything (the caller must then say INSUFFICIENT DATA, not
    guess).

    Event adjustment: when a known earnings date falls inside the horizon
    and historical earnings-move stats exist (n≥3), the expected one-day
    jump adds variance m² · 252/H to the annualized total — reported as
    erv30_event alongside the unadjusted erv30 so the VRP decomposition can
    separate PURE premium from EVENT premium. m comes from the ticker's own
    historical average absolute earnings move (MEASURED), never a guess."""
    cfg = cfg or {}
    horizon = int(cfg.get("horizon_td", HORIZON_TD_DEFAULT))
    min_bars = int(cfg.get("min_history_bars", 120))
    if len(bars) < min_bars:
        return None
    closes = [float(b.get("close") or 0) for b in bars]
    cands = candidates(bars, cfg)
    anchor = rv(closes, int(cfg.get("anchor_window", ANCHOR_WINDOW)))
    shrink = float(cfg.get("anchor_shrink", ANCHOR_SHRINK))
    model = (choice or {}).get("model", "GLOBAL")
    if model != "GLOBAL" and cands.get(model) is not None:
        # chosen single estimator still gets the anchor shrink — the
        # validation chose it WITH this treatment applied consistently
        core = cands[model]
        erv = shrink * anchor + (1.0 - shrink) * core if anchor is not None else core
        method = (choice or {}).get("method", f"ticker {model}")
    else:
        erv = _blend(cands, cfg.get("global_weights", GLOBAL_WEIGHTS), anchor, shrink)
        method = (choice or {}).get("method", "global blend")
    if erv is None or erv <= 0:
        return None
    quality = "ok" if len(bars) >= 260 and len(cands) >= 4 else "thin_history"
    out = {
        "erv30": round(erv, 4),
        "erv30_event": round(erv, 4),
        "method": method,
        "components": {k: round(v, 4) for k, v in cands.items()},
        "anchor": round(anchor, 4) if anchor is not None else None,
        "n_bars": len(bars),
        "quality": quality,
        "event_adj": None,
    }
    if earnings_within_horizon and earnings_hist_n >= 3 and earnings_hist_avg_abs_pct:
        m = float(earnings_hist_avg_abs_pct) / 100.0
        extra_var = (m * m) * (TRADING_DAYS / horizon)
        erv_event = math.sqrt(erv * erv + extra_var)
        out["erv30_event"] = round(erv_event, 4)
        out["event_adj"] = {
            "kind": "earnings",
            "hist_avg_abs_pct": round(float(earnings_hist_avg_abs_pct), 2),
            "hist_n": int(earnings_hist_n),
            "added_volpts": round((erv_event - erv) * 100.0, 2),
            "basis": f"historical earnings moves (n={int(earnings_hist_n)}, MEASURED)",
        }
    return out
