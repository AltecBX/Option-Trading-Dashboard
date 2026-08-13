"""recovery_fit.py — the historical study behind recovery.py (offline tool).

Replays every watchlist symbol's daily history through the SAME detection
code the live scanner uses (recovery.iter_days), collects Early/Confirmed
Recovery signals chronologically, labels each one by racing the prior high
against the invalidation level over the next 30 trading days, and fits the
probability model recovery.py serves (recovery_model.json).

Anti-overfit protocol:
  * Chronological split: train ≤ 2021-12-31, validation 2022-2023,
    out-of-sample test ≥ 2024-01-01.  Hyperparameters (L2 strength) and
    feature pruning are chosen on validation only; the test period is
    scored once with the frozen recipe and reported in meta.
  * Per-symbol signal spacing (16 bars) so overlapping outcome windows
    can't inflate the sample (patterns.py convention).
  * Structural-parameter sweep (--sweep) evaluates neighbouring configs on
    train+validation only and reports the whole grid, so robust plateaus
    are visible instead of a single lucky peak.
  * Serving tables (deciles/cohorts) are refit on the full sample with the
    frozen recipe — disclosed in meta/notes along with the survivorship
    limitation (universe = today's watchlist).

Usage:
  python3 recovery_fit.py --bars <dir of SYM.json.gz> --sectors sectors.json \
      --out recovery_model.json [--sweep] [--min-symbols 100]

This script is an offline research tool: it is NOT imported by the server
and never runs in CI (its tests exercise the functions on synthetic data).
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import recovery as rec

TRAIN_END = "2021-12-31"
VAL_END = "2023-12-31"
HORIZON = 60              # race horizon (trading days)
SPACING = (HORIZON + 1) // 2   # min bars between signals per symbol

RR_BUCKETS = [(i / 10, (i + 1) / 10) for i in range(10)]
DEPTH_BUCKETS = [(0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.25),
                 (0.25, 0.35), (0.35, 0.60)]
DIST_BUCKETS = [(0.0, 0.03), (0.03, 0.06), (0.06, 0.10), (0.10, 0.15),
                (0.15, 0.25), (0.25, 1.0)]
DSL_BUCKETS = [(0, 5), (5, 10), (10, 20), (20, 40), (40, 80), (80, 10_000)]


# ── data loading ────────────────────────────────────────────────────────────

def load_bars(bars_dir: Path, sym: str):
    p = bars_dir / f"{sym.replace('^', '_')}.json.gz"
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def close_map(bars):
    return {b["date"]: b["close"] for b in (bars or [])}


# ── signal generation (chronological, spaced, same code as the scanner) ─────

def collect_signals(bars_dir: Path, symbols: list[str], sectors: dict,
                    cfg: dict | None, progress=None) -> list[dict]:
    spy = close_map(load_bars(bars_dir, "SPY"))
    vix = close_map(load_bars(bars_dir, "^VIX"))
    etf_closes = {etf: close_map(load_bars(bars_dir, etf))
                  for etf in sorted(set(rec._SECTOR_ETF.values()))}
    signals: list[dict] = []
    eff = {**rec.CFG, **(cfg or {})}
    for si, sym in enumerate(symbols):
        bars = load_bars(bars_dir, sym)
        if not bars or len(bars) < eff["min_history"] + HORIZON:
            continue
        sec = sectors.get(sym)
        etf = rec._SECTOR_ETF.get(sec or "")
        sector_close = etf_closes.get(etf) if etf else None
        prev_in = False
        last_sig = -10_000
        for i, setup in rec.iter_days(bars, cfg, spy_close=spy,
                                      sector_close=sector_close):
            in_model = (setup["stage"] in rec.MODEL_STAGES
                        and setup["days_since_low"] >= eff["min_days_since_low"]
                        and (setup.get("dollar_vol") or 0) >= eff["min_dollar_vol"]
                        and eff["rr_lo"] <= setup["recovery_ratio"] < eff["rr_hi"])
            fire = in_model and not prev_in and (i - last_sig) >= SPACING
            prev_in = in_model
            if not fire:
                continue
            out = rec.label_outcome(bars, i, setup["prior_high"],
                                    setup["invalidation"], horizon=HORIZON)
            if out is None:
                continue
            undecided = (not out["win"] and not out["fail"]
                         and out["bars_available"] < HORIZON)
            if undecided:
                continue                      # dataset ends mid-race
            last_sig = i
            signals.append({
                "symbol": sym, "date": setup["date"], "sector": sec,
                "vix": vix.get(setup["date"]),
                "setup": setup, "outcome": out,
                "features": rec.model_features(setup),
            })
        if progress and (si + 1) % 200 == 0:
            progress(si + 1, len(symbols), len(signals))
    signals.sort(key=lambda s: s["date"])
    return signals


# ── logistic regression (numpy IRLS, L2 on weights, not intercept) ──────────

def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0,
                 iters: int = 30) -> tuple[np.ndarray, float]:
    """L2-penalized logistic regression via IRLS (Newton with the full
    Hessian — k is ~20 so the solve is trivial).  Intercept unpenalized."""
    n, k = X.shape
    Xb = np.hstack([X, np.ones((n, 1))])
    beta = np.zeros(k + 1)
    beta[-1] = float(np.log(max(y.mean(), 1e-6) / max(1 - y.mean(), 1e-6)))
    pen = np.full(k + 1, l2)
    pen[-1] = 0.0
    for _ in range(iters):
        z = np.clip(Xb @ beta, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        g = Xb.T @ (p - y) + pen * beta
        w_diag = np.maximum(p * (1 - p), 1e-9)
        H = (Xb * w_diag[:, None]).T @ Xb + np.diag(pen + 1e-9)
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        beta -= step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return beta[:-1], float(beta[-1])


def predict(X: np.ndarray, w: np.ndarray, b: float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(X @ w + b, -30, 30)))


def auc(y: np.ndarray, p: np.ndarray) -> float | None:
    pos = p[y == 1]
    neg = p[y == 0]
    if not len(pos) or not len(neg):
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    r_pos = ranks[:len(pos)].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2)
                 / (len(pos) * len(neg)))


# ── matrices ────────────────────────────────────────────────────────────────

def matrix(signals, names, mu=None, sd=None):
    F = np.array([[s["features"][nm] for nm in names] for s in signals],
                 dtype=float)
    y = np.array([1.0 if s["outcome"]["win"] else 0.0 for s in signals])
    if mu is None:
        mu = F.mean(axis=0)
        sd = F.std(axis=0)
        sd[sd < 1e-9] = 1.0
    X = (F - mu) / sd
    return X, y, mu, sd


def split(signals):
    tr = [s for s in signals if s["date"] <= TRAIN_END]
    va = [s for s in signals if TRAIN_END < s["date"] <= VAL_END]
    te = [s for s in signals if s["date"] > VAL_END]
    return tr, va, te


# ── stats helpers ───────────────────────────────────────────────────────────

def _med(vals):
    v = sorted(v for v in vals if v is not None)
    n = len(v)
    if not n:
        return None
    return round(v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2, 2)


def outcome_stats(sig_list) -> dict:
    n = len(sig_list)
    if not n:
        return {"n": 0}
    outs = [s["outcome"] for s in sig_list]
    wins = [o for o in outs if o["win"]]
    return {
        "n": n,
        "p_win": round(sum(o["win"] for o in outs) / n, 4),
        "p_fail": round(sum(o["fail"] for o in outs) / n, 4),
        "p_exceed": round(sum(o["exceeded"] for o in outs) / n, 4),
        "hit5": round(sum(o["hit5"] for o in outs) / n, 4),
        "hit10": round(sum(o["hit10"] for o in outs) / n, 4),
        "hit20": round(sum(o["hit20"] for o in outs) / n, 4),
        "hit30": round(sum(o["hit30"] for o in outs) / n, 4),
        "median_days": _med([o["days_to_target"] for o in wins]),
        "avg_days": (round(sum(o["days_to_target"] for o in wins) / len(wins), 1)
                     if wins else None),
        "median_fwd30": _med([o["fwd30"] for o in outs]),
        "median_mfe": _med([o["mfe"] for o in outs]),
        "median_mae": _med([o["mae"] for o in outs]),
    }


def bucketize(signals, key_fn, buckets, labels=None) -> list[dict]:
    out = []
    for bi, (lo, hi) in enumerate(buckets):
        grp = [s for s in signals
               if (v := key_fn(s)) is not None and lo <= v < hi]
        row = {"bucket": (labels[bi] if labels else f"{lo:g}–{hi:g}"),
               "lo": lo, "hi": hi, **outcome_stats(grp)}
        out.append(row)
    return out


def flag_cohort(signals, key_fn, label_true, label_false) -> list[dict]:
    t = [s for s in signals if key_fn(s)]
    f = [s for s in signals if not key_fn(s)]
    return [{"bucket": label_true, **outcome_stats(t)},
            {"bucket": label_false, **outcome_stats(f)}]


# ── the full fit pipeline ───────────────────────────────────────────────────

ALL_FEATURES = list(rec.model_features({
    "recovery_ratio": 0.5, "depth": 0.2, "dist_to_high": 0.1,
}).keys())


def run_fit(signals, verbose=True) -> dict:
    tr, va, te = split(signals)
    log = print if verbose else (lambda *a, **k: None)
    log(f"signals: train={len(tr)} val={len(va)} test={len(te)}")
    if len(tr) < 300 or len(va) < 100:
        raise SystemExit("not enough historical signals to fit honestly")

    names = list(ALL_FEATURES)
    Xtr, ytr, mu, sd = matrix(tr, names)
    Xva, yva, _, _ = matrix(va, names, mu, sd)

    # hyperparameter: L2 strength on validation AUC
    best = None
    for l2 in (0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0):
        w, b = fit_logistic(Xtr, ytr, l2=l2)
        a = auc(yva, predict(Xva, w, b))
        log(f"  l2={l2:<5} val_auc={a:.4f}")
        if best is None or (a or 0) > best[0]:
            best = (a, l2, w, b)
    val_auc, l2, w, b = best
    log(f"chosen l2={l2}  val_auc={val_auc:.4f}")

    # drop-one importance on validation → prune features that don't help
    base_auc = val_auc
    importance = {}
    for j, nm in enumerate(names):
        keep = [k for k in range(len(names)) if k != j]
        w2, b2 = fit_logistic(Xtr[:, keep], ytr, l2=l2)
        a2 = auc(yva, predict(Xva[:, keep], w2, b2))
        importance[nm] = round(base_auc - (a2 or 0), 5)
    dropped = [nm for nm in names
               if importance[nm] <= 0 and abs(w[names.index(nm)]) < 0.05]
    kept = [nm for nm in names if nm not in dropped]
    log(f"dropped ({len(dropped)}): {dropped}")

    Xtr2, ytr2, mu2, sd2 = matrix(tr, kept)
    Xva2, yva2, _, _ = matrix(va, kept, mu2, sd2)
    w2, b2 = fit_logistic(Xtr2, ytr2, l2=l2)
    val_auc2 = auc(yva2, predict(Xva2, w2, b2))
    log(f"pruned model val_auc={val_auc2:.4f} (was {val_auc:.4f})")
    if (val_auc2 or 0) < (val_auc or 0) - 0.005:
        kept, w2, b2, mu2, sd2, val_auc2 = names, w, b, mu, sd, val_auc

    # ── frozen recipe → one look at the test period ──
    Xte, yte, _, _ = matrix(te, kept, mu2, sd2)
    pte = predict(Xte, w2, b2)
    test_auc = auc(yte, pte)
    # calibration bins built on train+val, applied to test
    trva = tr + va
    Xtv, ytv, mu3, sd3 = matrix(trva, kept)
    w3, b3 = fit_logistic(Xtv, ytv, l2=l2)
    ptv = predict(Xtv, w3, b3)
    edges = np.quantile(ptv, np.linspace(0, 1, 11))
    edges[0], edges[-1] = 0.0, 1.0
    cal = []
    for d in range(10):
        m = (ptv >= edges[d]) & (ptv < edges[d + 1])
        cal.append(float(ytv[m].mean()) if m.sum() >= 20 else None)
    Xte3, yte3, _, _ = matrix(te, kept, mu3, sd3)
    pte3 = predict(Xte3, w3, b3)
    pcal = np.empty(len(pte3))
    for j, p in enumerate(pte3):
        d = min(9, max(0, int(np.searchsorted(edges, p, side="right") - 1)))
        pcal[j] = cal[d] if cal[d] is not None else float(ytv.mean())
    test_brier = float(np.mean((pcal - yte3) ** 2)) if len(te) else None
    base_brier = float(np.mean((yte3.mean() - yte3) ** 2)) if len(te) else None
    # test calibration table (predicted decile → observed rate)
    test_cal = []
    for d in range(10):
        m = (pte3 >= edges[d]) & (pte3 < edges[d + 1])
        test_cal.append({
            "decile": d + 1, "n": int(m.sum()),
            "predicted": round(cal[d], 4) if cal[d] is not None else None,
            "observed": round(float(yte3[m].mean()), 4) if m.sum() >= 15 else None,
        })
    log(f"TEST: auc={test_auc:.4f} brier={test_brier:.4f} "
        f"(base-rate brier={base_brier:.4f})")

    # ── final serving model: frozen recipe refit on ALL signals ──
    Xall, yall, muA, sdA = matrix(signals, kept)
    wA, bA = fit_logistic(Xall, yall, l2=l2)
    pall = predict(Xall, wA, bA)
    edgesA = np.quantile(pall, np.linspace(0, 1, 11))
    edgesA[0], edgesA[-1] = 0.0, 1.0
    deciles = []
    for d in range(10):
        m = (pall >= edgesA[d]) & (pall < edgesA[d + 1])
        grp = [signals[j] for j in np.nonzero(m)[0]]
        deciles.append({"decile": d + 1, "lo": round(float(edgesA[d]), 4),
                        "hi": round(float(edgesA[d + 1]), 4),
                        **outcome_stats(grp)})
    p_wins = [d["p_win"] for d in deciles if d.get("p_win") is not None]

    per_year = {}
    for s in signals:
        per_year.setdefault(s["date"][:4], []).append(s)
    top_cut = edgesA[7]
    stability = []
    for yr in sorted(per_year):
        grp = per_year[yr]
        Xy, yy, _, _ = matrix(grp, kept, muA, sdA)
        py = predict(Xy, wA, bA)
        top = [g for g, pv in zip(grp, py) if pv >= top_cut]
        stability.append({"year": yr, "n": len(grp),
                          "p_win": outcome_stats(grp)["p_win"],
                          "top3_n": len(top),
                          "top3_p_win": outcome_stats(top)["p_win"] if len(top) >= 15 else None})

    return {
        "feature_names": kept,
        "weights": {nm: round(float(wA[j]), 5) for j, nm in enumerate(kept)},
        "intercept": round(float(bA), 5),
        "feature_mean": {nm: round(float(muA[j]), 6) for j, nm in enumerate(kept)},
        "feature_std": {nm: round(float(sdA[j]), 6) for j, nm in enumerate(kept)},
        "l2": l2,
        "deciles": deciles,
        "p_floor": min(p_wins) if p_wins else 0.2,
        "p_ceil": max(p_wins) if p_wins else 0.8,
        "stability": stability,
        "feature_report": {
            "importance_val_auc_drop": importance,
            "dropped": dropped,
            "val_auc": round(float(val_auc2 or 0), 4),
        },
        "test": {"auc": round(float(test_auc), 4) if test_auc else None,
                 "brier": round(test_brier, 4) if test_brier else None,
                 "base_rate_brier": round(base_brier, 4) if base_brier else None,
                 "n": len(te), "calibration": test_cal},
    }


def build_cohorts(signals) -> dict:
    f = lambda s: s["features"]  # noqa: E731
    return {
        "recovery_ratio": bucketize(signals, lambda s: f(s)["recovery_ratio"],
                                    RR_BUCKETS),
        "depth": bucketize(signals, lambda s: f(s)["depth"], DEPTH_BUCKETS),
        "dist_to_high": bucketize(signals, lambda s: f(s)["dist_to_high"],
                                  DIST_BUCKETS),
        "days_since_low": bucketize(
            signals, lambda s: s["setup"]["days_since_low"], DSL_BUCKETS),
        "higher_low": flag_cohort(signals,
                                  lambda s: s["setup"]["has_higher_low"],
                                  "higher low", "no higher low"),
        "broke_bounce": flag_cohort(signals,
                                    lambda s: s["setup"]["broke_bounce"],
                                    "broke bounce high", "below bounce high"),
        "relvol": bucketize(signals, lambda s: s["setup"].get("relvol"),
                            [(0, 0.8), (0.8, 1.2), (1.2, 1.8), (1.8, 100)],
                            ["<0.8", "0.8–1.2", "1.2–1.8", ">1.8"]),
        "stage": [
            {"bucket": st, **outcome_stats(
                [s for s in signals if s["setup"]["stage"] == st])}
            for st in ("early", "confirmed")],
    }


def build_regimes(signals) -> dict:
    reg = [
        {"bucket": r or "unknown", **outcome_stats(
            [s for s in signals if s["setup"].get("regime") == r])}
        for r in ("uptrend", "chop", "downtrend")]
    vix = bucketize(signals, lambda s: s.get("vix"),
                    [(0, 15), (15, 25), (25, 200)],
                    ["VIX <15", "VIX 15–25", "VIX >25"])
    sectors = {}
    for s in signals:
        sectors.setdefault(s.get("sector") or "Unknown", []).append(s)
    sec_rows = [{"bucket": k, **outcome_stats(v)}
                for k, v in sorted(sectors.items(), key=lambda kv: -len(kv[1]))
                if len(v) >= 30]
    return {"spy": reg, "vix": vix, "sector": sec_rows}


# ── structural parameter sweep (train+val only) ─────────────────────────────

SWEEP_GRID = [
    {"name": "baseline", "cfg": {}},
    {"name": "depth≥5%", "cfg": {"min_depth": 0.05}},
    {"name": "depth≥8%", "cfg": {"min_depth": 0.08}},
    {"name": "depth≥15%", "cfg": {"min_depth": 0.15}},
    {"name": "lookback 126", "cfg": {"lookback": 126}},
    {"name": "lookback 504", "cfg": {"lookback": 504}},
    {"name": "significance 5", "cfg": {"min_significance": 5}},
    {"name": "significance 30", "cfg": {"min_significance": 30}},
    {"name": "stop=corr low", "cfg": {"inval_mode": "corr_low"}},
    {"name": "zigzag 15%", "cfg": {"zz_frac": 0.15}},
    {"name": "zigzag 35%", "cfg": {"zz_frac": 0.35}},
]


def run_sweep(bars_dir, symbols, sectors, verbose=True) -> list[dict]:
    rows = []
    for spec in SWEEP_GRID:
        t0 = time.time()
        sig = collect_signals(bars_dir, symbols, sectors, spec["cfg"])
        tv = [s for s in sig if s["date"] <= VAL_END]
        st = outcome_stats(tv)
        per_year = {}
        for s in tv:
            per_year.setdefault(s["date"][:4], []).append(s)
        yr_wins = [outcome_stats(v)["p_win"] for y, v in sorted(per_year.items())
                   if len(v) >= 50]
        rows.append({
            "name": spec["name"], "cfg": spec["cfg"],
            "n": st["n"], "p_win": st.get("p_win"),
            "median_fwd30": st.get("median_fwd30"),
            "hit20": st.get("hit20"),
            "year_p_win_min": min(yr_wins) if yr_wins else None,
            "year_p_win_max": max(yr_wins) if yr_wins else None,
        })
        if verbose:
            print(f"  sweep {spec['name']:<16} n={st['n']:<6} "
                  f"p_win={st.get('p_win')} ({time.time() - t0:.0f}s)")
    return rows


# ── main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True)
    ap.add_argument("--sectors", default=None)
    ap.add_argument("--out", default="recovery_model.json")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--report", default=None)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VAL",
                    help="config override chosen from a sweep, e.g. "
                         "--set min_depth=0.08 (recorded in the artifact and "
                         "used for signal generation)")
    args = ap.parse_args()

    cfg_over: dict = {}
    for kv in args.set:
        k, _, v = kv.partition("=")
        if k not in rec.CFG:
            raise SystemExit(f"unknown config key: {k}")
        cur = rec.CFG[k]
        cfg_over[k] = (v if isinstance(cur, str)
                       else int(v) if isinstance(cur, int) and not isinstance(cur, bool)
                       else float(v))

    bars_dir = Path(args.bars)
    sectors = {}
    if args.sectors and Path(args.sectors).exists():
        sectors = {k: v for k, v in json.loads(
            Path(args.sectors).read_text()).items() if v}
    wl = json.loads((Path(__file__).parent / "watchlist_seed.json").read_text())
    symbols = [s["symbol"] for s in wl["symbols"] if s.get("symbol")]
    symbols = [s for s in symbols
               if (bars_dir / f"{s.replace('^', '_')}.json.gz").exists()]
    print(f"universe: {len(symbols)} symbols with history")

    if cfg_over:
        print(f"config overrides: {cfg_over}")
    t0 = time.time()
    signals = collect_signals(
        bars_dir, symbols, sectors, cfg_over or None,
        progress=lambda a, b, n: print(f"  {a}/{b} symbols, {n} signals"))
    print(f"collected {len(signals)} signals in {time.time() - t0:.0f}s "
          f"({signals[0]['date']} → {signals[-1]['date']})")

    fit = run_fit(signals)
    cohorts = build_cohorts(signals)
    regimes = build_regimes(signals)

    sweep = None
    if args.sweep:
        print("running structural sweep (train+val only)…")
        sweep = run_sweep(bars_dir, symbols, sectors)

    dates = [s["date"] for s in signals]
    artifact = {
        "version": 1,
        "config": cfg_over,    # deltas vs recovery.CFG chosen from the sweep
        "meta": {
            "fitted": datetime.now(timezone.utc).isoformat(),
            "data_from": dates[0], "data_to": dates[-1],
            "n_signals": len(signals), "n_symbols": len({s["symbol"] for s in signals}),
            "train_end": TRAIN_END, "val_end": VAL_END,
            "test_period": f"{VAL_END} → {dates[-1]}",
            "test_auc": fit["test"]["auc"], "test_brier": fit["test"]["brier"],
            "test_base_rate_brier": fit["test"]["base_rate_brier"],
            "test_n": fit["test"]["n"],
            "spacing_bars": SPACING, "horizon": HORIZON,
            "limitations": (
                "Universe is today's watchlist (survivorship bias — delisted "
                "stocks absent, absolute hit rates run hot). Daily bars can't "
                "order a same-day touch of target and stop; those count as "
                "failures. Serving tables refit on the full sample with the "
                "recipe frozen on train/validation; the test metrics above "
                "are the honest out-of-sample evidence."),
        },
        "feature_names": fit["feature_names"],
        "weights": fit["weights"], "intercept": fit["intercept"],
        "feature_mean": fit["feature_mean"], "feature_std": fit["feature_std"],
        "l2": fit["l2"],
        "deciles": fit["deciles"],
        "p_floor": fit["p_floor"], "p_ceil": fit["p_ceil"],
        "cohorts": cohorts, "regimes": regimes,
        "stability": fit["stability"],
        "feature_report": fit["feature_report"],
        "test": fit["test"],
        "sweep": sweep,
        "notes": [
            "Probabilities are empirical hit rates by model-score decile: "
            f"P(prior high touched before the invalidation level within {HORIZON} "
            "trading days of an Early/Confirmed Recovery signal).",
            f"Signals are spaced ≥{SPACING} bars apart per symbol so outcome "
            "windows don't overlap.",
            "Ambiguous same-bar touches of target and stop count as failures "
            "(conservative).",
        ],
    }
    Path(args.out).write_text(json.dumps(artifact, indent=1))
    print(f"wrote {args.out}")
    if args.report:
        Path(args.report).write_text(json.dumps(
            {"signals": len(signals), "fit": fit["test"],
             "stability": fit["stability"]}, indent=1))


if __name__ == "__main__":
    main()
