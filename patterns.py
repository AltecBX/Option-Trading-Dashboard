# Per-stock behavioral discovery engine v2 (app v3.45).
#
# v1 (v3.44) was an event-study over adaptive thresholds. v2 upgrades it into
# a behavioral discovery system:
#
#   • 10-year adjusted daily history (Schwab periodType=year period=10).
#   • UNSUPERVISED SHAPE DISCOVERY: rolling 5-day return shapes are clustered
#     (leader clustering on vol-normalized vectors); recurring shapes the app
#     was never programmed to look for become patterns when what follows them
#     beats baseline. Near-duplicate discoveries are removed by occurrence
#     overlap (Jaccard).
#   • FIRST-TOUCH racing: for every pattern, which level gets hit first —
#     the target or the stop — with same-bar ambiguity counted separately
#     and conservatively.
#   • VALIDATION STACK: in-sample fit / out-of-sample check, 4-fold
#     walk-forward stability, bootstrap confidence intervals on the hit
#     rate, Benjamini-Hochberg multiple-testing correction across ALL
#     candidates searched, minimum sample rules, per-year and per-regime
#     stability, and overlap-purged occurrences. Patterns are labeled
#     reliable / unstable / weakening / likely random / insufficient sample.
#   • CONTEXT: SPY trend, QQQ trend, sector-ETF trend (sector from the
#     watchlist board), market volatility state, stock volatility state,
#     gap direction, relative volume — with an explicit "works best / fails
#     in" readout. Historical earnings/news/IV/flow context is NOT available
#     (no historical feeds) and is said so; current earnings proximity is
#     shown on the live setup instead.
#   • ACTIONABILITY score: net expected value after estimated spread and
#     slippage, OOS performance, sample, consistency, reward/risk (MFE vs
#     MAE), speed, liquidity, and recent performance — hit rate alone never
#     ranks a pattern.
#   • CURRENT SETUP: which patterns are triggered on the latest bar, how
#     close today is to past occurrences, the expected move band, the
#     probability of target-before-stop, typical time, and the invalidation
#     price. Top 3 actionable setups ranked first.
#   • NL RESEARCH: questions are parsed by the Backtest Lab's deterministic
#     grammar into visible conditions and answered with the same event-study
#     machinery (backtest._Ctx evaluates them — no look-ahead by
#     construction).
#   • INTRADAY SEQUENCE MINING (background job): each session's minute bars
#     are tokenized into an ordered event grammar (gap, opening-range break,
#     holds above open 30m, pulls back to VWAP, reclaims morning high,
#     afternoon events, power hour) and frequent ordered sequences are mined
#     with EXACT outcomes measured from the completion minute to the close.
#     Minute data reaches back ~6 months, but every mined day is archived on
#     disk, so coverage GROWS the longer the app runs.

from __future__ import annotations

import json
import math
import random
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

_schwab_getter = lambda: None
_universe_fn = lambda: {"starred": [], "all": []}
_notify_fn = None
_board_getter = lambda: {}
_minute_day_fn = lambda sym, d: None
_data_dir: Path | None = None

def configure(schwab_getter, universe_fn, data_dir, notify_fn=None,
              board_getter=None, minute_day_fn=None):
    global _schwab_getter, _universe_fn, _data_dir, _notify_fn, _board_getter, _minute_day_fn
    _schwab_getter = schwab_getter
    _universe_fn = universe_fn
    _data_dir = Path(data_dir) if data_dir else None
    _notify_fn = notify_fn
    if board_getter:
        _board_getter = board_getter
    if minute_day_fn:
        _minute_day_fn = minute_day_fn

HIST_DAYS = 3650          # ~10 years of adjusted daily bars
MIN_OCC = 15              # minimum occurrences for any claim
FDR_Q = 0.10              # Benjamini-Hochberg false-discovery threshold

_SECTOR_ETF = {
    "technology": "XLK", "information technology": "XLK",
    "financial services": "XLF", "financials": "XLF", "financial": "XLF",
    "healthcare": "XLV", "health care": "XLV",
    "consumer cyclical": "XLY", "consumer discretionary": "XLY",
    "consumer defensive": "XLP", "consumer staples": "XLP",
    "energy": "XLE", "industrials": "XLI", "industrial": "XLI",
    "basic materials": "XLB", "materials": "XLB",
    "real estate": "XLRE", "utilities": "XLU",
    "communication services": "XLC", "communications": "XLC",
}


# ── stats helpers ───────────────────────────────────────────────────────────

def _q(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)

def _quantile(vals, p):
    return _q(sorted(vals), p)

def _median(vals):
    return _quantile(vals, 0.5) if vals else None

def _binom_p(hits, n, p0):
    if n <= 0 or p0 <= 0 or p0 >= 1:
        return 1.0
    mu, sd = n * p0, math.sqrt(n * p0 * (1 - p0))
    if sd <= 0:
        return 1.0
    z = (hits - 0.5 - mu) / sd
    return 0.5 * (1.0 - math.erf(z / math.sqrt(2.0)))

def _bh_fdr(pvals):
    """Benjamini-Hochberg q-values for a list of p-values (same order)."""
    n = len(pvals)
    order = sorted(range(n), key=lambda i: pvals[i])
    q = [0.0] * n
    prev = 1.0
    for rank in range(n, 0, -1):
        i = order[rank - 1]
        val = min(prev, pvals[i] * n / rank)
        q[i] = val
        prev = val
    return q

def _bootstrap_ci(hits_bools, iters=400):
    """Percentile bootstrap 5–95% CI on the hit rate."""
    n = len(hits_bools)
    if n == 0:
        return (0.0, 0.0)
    rng = random.Random(1234)
    rates = []
    for _ in range(iters):
        s = sum(hits_bools[rng.randrange(n)] for _ in range(n))
        rates.append(s / n)
    rates.sort()
    return (round(_q(rates, 0.05) * 100, 1), round(_q(rates, 0.95) * 100, 1))

def _sma_at(vals, i, n):
    if i + 1 < n:
        return None
    return sum(vals[i + 1 - n:i + 1]) / n

def _rv20(closes, i):
    if i < 21:
        return None
    rets = [math.log(closes[k] / closes[k - 1]) for k in range(i - 19, i + 1) if closes[k - 1] > 0]
    if len(rets) < 5:
        return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1) * 252)

def _est_spread_pct(price):
    if not price or price <= 0:
        return 0.001
    if price < 5:
        return 0.004
    if price < 20:
        return 0.0015
    if price < 100:
        return 0.0006
    return 0.0003


# ── event detectors (info ≤ bar i only) ─────────────────────────────────────

def _det_surge(bars, i, p):
    w = p["w"]
    if i < w:
        return False
    c0, c1 = bars[i - w]["close"], bars[i]["close"]
    if not c0:
        return False
    r = (c1 - c0) / c0 * 100.0
    return r >= p["x"] if p["dir"] == "up" else r <= -p["x"]

def _det_gap(bars, i, p):
    if i == 0:
        return False
    pc = bars[i - 1]["close"]
    if not pc:
        return False
    g = (bars[i]["open"] - pc) / pc * 100.0
    return g >= p["x"] if p["dir"] == "up" else g <= -p["x"]

def _det_shock_vol(bars, i, p):
    if i < 21:
        return False
    pc = bars[i - 1]["close"]
    if not pc:
        return False
    r = (bars[i]["close"] - pc) / pc * 100.0
    if not (r <= -p["x"] if p["dir"] == "down" else r >= p["x"]):
        return False
    av = sum(b.get("volume") or 0 for b in bars[i - 20:i]) / 20
    return av > 0 and (bars[i].get("volume") or 0) >= p["mult"] * av

def _det_new_extreme(bars, i, p):
    n = p["lookback"]
    if i < n:
        return False
    closes = [b["close"] for b in bars[i - n:i]]
    return bars[i]["close"] > max(closes) if p["dir"] == "high" else bars[i]["close"] < min(closes)

def _det_consec(bars, i, p):
    n = p["n"]
    if i < n:
        return False
    for k in range(i - n + 1, i + 1):
        d = bars[k]["close"] - bars[k - 1]["close"]
        if (p["dir"] == "down" and d >= 0) or (p["dir"] == "up" and d <= 0):
            return False
    return True

def _det_drawdown(bars, i, p):
    n = min(252, i)
    if n < 30:
        return False
    hi = max(b["close"] for b in bars[i - n:i + 1])
    if hi <= 0:
        return False
    dd = (hi - bars[i]["close"]) / hi * 100.0
    if dd < p["x"]:
        return False
    hi2 = max(b["close"] for b in bars[max(0, i - 1 - n):i])
    dd_prev = (hi2 - bars[i - 1]["close"]) / hi2 * 100.0 if hi2 > 0 else 0
    return dd_prev < p["x"]

def _shape_vec(bars, i, w, dvol):
    """Vol-normalized return shape of the w bars ENDING at i."""
    if i < w or dvol <= 0:
        return None
    v = []
    for k in range(i - w + 1, i + 1):
        c0 = bars[k - 1]["close"]
        if not c0:
            return None
        v.append(((bars[k]["close"] - c0) / c0) / dvol)
    return v

def _shape_dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

_SHAPE_W = 5
_SHAPE_THETA = 2.1

def _det_shape(bars, i, p, _dvol_cache={}):
    key = id(bars)
    dvol = _dvol_cache.get(key)
    if dvol is None:
        closes = [b["close"] for b in bars]
        rets = [(closes[k] - closes[k - 1]) / closes[k - 1] for k in range(1, len(closes)) if closes[k - 1] > 0]
        m = sum(rets) / max(1, len(rets))
        dvol = math.sqrt(sum((r - m) ** 2 for r in rets) / max(1, len(rets) - 1)) or 0.02
        if len(_dvol_cache) > 8:
            _dvol_cache.clear()
        _dvol_cache[key] = dvol
    v = _shape_vec(bars, i, _SHAPE_W, dvol)
    if v is None:
        return False
    return _shape_dist(v, p["medoid"]) <= p.get("theta", _SHAPE_THETA)

_FAMILIES = {
    "surge": {"det": _det_surge, "ref": "close"},
    "gap": {"det": _det_gap, "ref": "open"},
    "shock_vol": {"det": _det_shock_vol, "ref": "close"},
    "new_extreme": {"det": _det_new_extreme, "ref": "close"},
    "consec": {"det": _det_consec, "ref": "close"},
    "drawdown": {"det": _det_drawdown, "ref": "close"},
    "shape": {"det": _det_shape, "ref": "close"},
    "custom": {"det": None, "ref": "close"},   # NL research (backtest._Ctx)
}


def _event_grid(bars):
    closes = [b["close"] for b in bars]
    grid = []
    for w in (2, 5, 10):
        rets = [(closes[i] - closes[i - w]) / closes[i - w] * 100.0
                for i in range(w, len(closes)) if closes[i - w] > 0]
        ups = sorted(r for r in rets if r > 0)
        dns = sorted(-r for r in rets if r < 0)
        for qq in (0.90, 0.97):
            xu = _q(ups, qq)
            if xu and xu >= 3:
                grid.append(("surge", {"w": w, "x": round(xu, 1), "dir": "up"}))
            xd = _q(dns, qq)
            if xd and xd >= 3:
                grid.append(("surge", {"w": w, "x": round(xd, 1), "dir": "down"}))
    gaps = []
    for i in range(1, len(bars)):
        pc = closes[i - 1]
        if pc:
            gaps.append((bars[i]["open"] - pc) / pc * 100.0)
    gu = sorted(g for g in gaps if g > 0)
    gd = sorted(-g for g in gaps if g < 0)
    for qq in (0.85, 0.95):
        xu = _q(gu, qq)
        if xu and xu >= 0.75:
            grid.append(("gap", {"x": round(xu, 2), "dir": "up"}))
        xd = _q(gd, qq)
        if xd and xd >= 0.75:
            grid.append(("gap", {"x": round(xd, 2), "dir": "down"}))
    d1 = [(closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
          for i in range(1, len(closes)) if closes[i - 1] > 0]
    xd = _q(sorted(-r for r in d1 if r < 0), 0.92)
    if xd and xd >= 2:
        grid.append(("shock_vol", {"x": round(xd, 1), "mult": 2.0, "dir": "down"}))
    xu = _q(sorted(r for r in d1 if r > 0), 0.92)
    if xu and xu >= 2:
        grid.append(("shock_vol", {"x": round(xu, 1), "mult": 2.0, "dir": "up"}))
    grid.append(("new_extreme", {"lookback": 60, "dir": "high"}))
    grid.append(("new_extreme", {"lookback": 60, "dir": "low"}))
    grid.append(("new_extreme", {"lookback": 252, "dir": "high"}))
    grid.append(("new_extreme", {"lookback": 252, "dir": "low"}))
    grid.append(("consec", {"n": 3, "dir": "down"}))
    grid.append(("consec", {"n": 3, "dir": "up"}))
    grid.append(("consec", {"n": 4, "dir": "down"}))
    dds = []
    run_hi = closes[0]
    for c in closes:
        run_hi = max(run_hi, c)
        dds.append((run_hi - c) / run_hi * 100.0 if run_hi > 0 else 0.0)
    for qq in (0.80, 0.95):
        x = _quantile(dds, qq)
        if x and x >= 10:
            grid.append(("drawdown", {"x": round(x, 0)}))
    return grid


def _discover_shapes(bars):
    """Unsupervised: cluster rolling 5-day vol-normalized return shapes.
    Returns [("shape", {"medoid": [...], "theta": θ, "words": "..."}) ...]."""
    closes = [b["close"] for b in bars]
    rets = [(closes[k] - closes[k - 1]) / closes[k - 1] for k in range(1, len(closes)) if closes[k - 1] > 0]
    if len(rets) < 100:
        return []
    m = sum(rets) / len(rets)
    dvol = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) or 0.02
    stride = max(1, len(bars) // 1400)
    windows = []
    for i in range(_SHAPE_W + 21, len(bars) - 11, stride):
        v = _shape_vec(bars, i, _SHAPE_W, dvol)
        if v is not None:
            windows.append((i, v))
    # Leader clustering: first member becomes the leader; others join the
    # nearest leader within θ, else found their own cluster.
    leaders = []   # (vec, [member indices])
    for i, v in windows:
        best, bd = None, 1e9
        for li, (lv, members) in enumerate(leaders):
            d = _shape_dist(v, lv)
            if d < bd:
                best, bd = li, d
        if best is not None and bd <= _SHAPE_THETA:
            leaders[best][1].append(i)
        else:
            leaders.append((v, [i]))
    out = []
    for lv, members in leaders:
        if len(members) < MIN_OCC:
            continue
        words = []
        for z in lv:
            if z >= 1.5:
                words.append("strong up")
            elif z >= 0.5:
                words.append("up")
            elif z <= -1.5:
                words.append("strong down")
            elif z <= -0.5:
                words.append("down")
            else:
                words.append("flat")
        out.append(("shape", {"medoid": [round(z, 3) for z in lv],
                              "theta": _SHAPE_THETA,
                              "words": " → ".join(words)}))
        if len(out) >= 10:
            break
    return out


def _describe_event(fam, p, sym):
    if fam == "surge":
        return f"{sym} {'rises' if p['dir'] == 'up' else 'falls'} ≥{p['x']}% within {p['w']} trading days"
    if fam == "gap":
        return f"{sym} opens {'up' if p['dir'] == 'up' else 'down'} ≥{p['x']}% vs the prior close"
    if fam == "shock_vol":
        return f"{sym} {'drops' if p['dir'] == 'down' else 'jumps'} ≥{p['x']}% in one day on ≥{p['mult']:g}× average volume"
    if fam == "new_extreme":
        return f"{sym} closes at a new {p['lookback']}-day {p['dir']}"
    if fam == "consec":
        return f"{sym} closes {p['dir']} {p['n']} days in a row"
    if fam == "drawdown":
        return f"{sym} crosses {p['x']:g}% below its recent (≤1y) high"
    if fam == "shape":
        return f"{sym} prints a recurring 5-day shape: {p.get('words', 'discovered shape')}"
    if fam == "custom":
        return p.get("describe", f"{sym} matches your custom conditions")
    return fam

def _event_dir(fam, p):
    if fam == "new_extreme":
        return "up" if p.get("dir") == "high" else "down"
    if fam == "drawdown":
        return "down"
    if fam == "shape":
        s = sum(p.get("medoid") or [0])
        return "up" if s > 0 else "down"
    return p.get("dir", "down")


def _collect_occurrences(bars, det, params, spacing, ctx=None, conds=None):
    occ = []
    last = -10_000
    for i in range(1, len(bars) - 1):
        if i - last < spacing:
            continue
        try:
            if conds is not None:
                ok = all(ctx.check(c, i) is True for c in conds)
            else:
                ok = det(bars, i, params)
            if ok:
                occ.append(i)
                last = i
        except Exception:
            continue
    return occ


def _fwd_stats(bars, i, F, ref):
    entry = bars[i]["close"] if ref == "close" else bars[i]["open"]
    if not entry:
        return None
    end = min(len(bars) - 1, i + F)
    lo_k = hi_k = i
    mfe = mae = 0.0
    rng = range(i + 1, end + 1) if ref == "close" else range(i, end + 1)
    for k in rng:
        hi = (bars[k]["high"] - entry) / entry * 100.0
        lo = (bars[k]["low"] - entry) / entry * 100.0
        if hi > mfe:
            mfe, hi_k = hi, k
        if lo < mae:
            mae, lo_k = lo, k
    fwd = (bars[end]["close"] - entry) / entry * 100.0
    return fwd, mfe, mae, hi_k - i, lo_k - i


def _first_touch(bars, i, F, ref, up_pct, dn_pct):
    """Race: which is touched first inside F bars — entry+up_pct% (via highs)
    or entry−dn_pct% (via lows)? Daily bars can't order touches INSIDE one
    bar: when both land in the same bar the result is 'ambiguous' (and the
    headline stat counts it AGAINST the favorable side — conservative)."""
    entry = bars[i]["close"] if ref == "close" else bars[i]["open"]
    if not entry:
        return None
    up_px = entry * (1 + up_pct / 100.0)
    dn_px = entry * (1 - dn_pct / 100.0)
    end = min(len(bars) - 1, i + F)
    rng = range(i + 1, end + 1) if ref == "close" else range(i, end + 1)
    for k in rng:
        hit_up = bars[k]["high"] >= up_px
        hit_dn = bars[k]["low"] <= dn_px
        if hit_up and hit_dn:
            return ("ambiguous", k - i)
        if hit_up:
            return ("up", k - i)
        if hit_dn:
            return ("down", k - i)
    return ("none", end - i)


def _spy_regimes(spy_bars):
    closes = [b["close"] for b in spy_bars]
    out = {}
    for i, b in enumerate(spy_bars):
        s50, s200 = _sma_at(closes, i, 50), _sma_at(closes, i, 200)
        if s200 is None:
            out[b["date"][:10]] = None
        elif closes[i] > s200 and (s50 or 0) > s200:
            out[b["date"][:10]] = "uptrend"
        elif closes[i] < s200:
            out[b["date"][:10]] = "downtrend"
        else:
            out[b["date"][:10]] = "chop"
    return out

def _vol_state_series(bars):
    closes = [b["close"] for b in bars]
    rv = [_rv20(closes, i) for i in range(len(bars))]
    vals = [v for v in rv if v is not None]
    med = _median(vals) if vals else None
    out = {}
    for i, b in enumerate(bars):
        out[b["date"][:10]] = None if (rv[i] is None or med is None) else \
            ("high vol" if rv[i] > med else "low vol")
    return out

def _sector_of(symbol):
    try:
        board = _board_getter() or {}
        for r in (board.get("rows") or []):
            if (r.get("symbol") or "").upper() == symbol:
                return r.get("sector"), r.get("next_earnings"), r.get("days_to_earnings")
    except Exception:
        pass
    return None, None, None


# ── the core analyzer: one event spec → one validated pattern per window ────

def _analyze_spec(symbol, bars, fam, params, F, ref, series_ctx, conds=None, bt_ctx=None):
    spec = _FAMILIES[fam]
    w_span = params.get("w", _SHAPE_W if fam == "shape" else 1)
    spacing = max(2, w_span, (F + 1) // 2)   # overlap purge: outcome windows can't stack
    occ = _collect_occurrences(bars, spec["det"], params, spacing, ctx=bt_ctx, conds=conds)
    usable = [i for i in occ if i + F < len(bars)]
    if len(usable) < MIN_OCC:
        return None
    stats = {}
    for i in usable:
        st = _fwd_stats(bars, i, F, ref)
        if st is not None:
            stats[i] = st
    if len(stats) < MIN_OCC:
        return None
    n_bars = len(bars)
    split = int(n_bars * 0.70)
    is_idx = sorted(i for i in stats if i < split)
    oos_idx = sorted(i for i in stats if i >= split)
    if len(is_idx) < 8:
        return None

    is_fwd = [stats[i][0] for i in is_idx]
    med = _median(is_fwd)
    if med is None:
        return None
    claim_dir = "up" if med > 0 else "down"
    signed = sorted((v if claim_dir == "up" else -v) for v in is_fwd)
    claim_x = _q(signed, 0.35)
    if claim_x is None or claim_x < 0.75:
        return None
    claim_x = round(claim_x, 1)

    def _hit(i):
        v = stats[i][0] if claim_dir == "up" else -stats[i][0]
        return v >= claim_x

    keys = sorted(stats.keys())
    hit_bools = [_hit(i) for i in keys]
    is_hits = sum(1 for i in is_idx if _hit(i))
    oos_hits = sum(1 for i in oos_idx if _hit(i))
    is_rate = is_hits / len(is_idx)
    oos_rate = (oos_hits / len(oos_idx)) if oos_idx else None

    # Baseline: same claim after ANY day (sampled for speed on 10y data).
    hits = n = 0
    step = max(1, (n_bars - F) // 900)
    for i in range(1, n_bars - 1 - F, step):
        st = _fwd_stats(bars, i, F, ref)
        if st is None:
            continue
        n += 1
        v = st[0] if claim_dir == "up" else -st[0]
        if v >= claim_x:
            hits += 1
    base_rate = hits / n if n else 0.0
    all_hits = sum(hit_bools)
    p_val = _binom_p(all_hits, len(keys), max(0.02, base_rate))
    boot_ci = _bootstrap_ci(hit_bools)

    # Walk-forward: 4 chronological folds of the occurrence list.
    folds = []
    fold_sz = max(1, len(keys) // 4)
    for f in range(4):
        seg = keys[f * fold_sz: (f + 1) * fold_sz if f < 3 else len(keys)]
        if len(seg) >= 3:
            folds.append(round(sum(1 for i in seg if _hit(i)) / len(seg) * 100, 1))
    fold_spread = (max(folds) - min(folds)) if len(folds) >= 2 else None
    recent_rate = folds[-1] if folds else None
    overall_rate = all_hits / len(keys) * 100

    # First-touch race: target = the claimed move; stop = ~2/3 of it.
    tgt = claim_x
    stp = round(max(0.75, claim_x * 0.66), 1)
    ft = {"up": 0, "down": 0, "none": 0, "ambiguous": 0}
    t_hit_days, t_fail_days = [], []
    for i in keys:
        r = _first_touch(bars, i, F, ref, tgt if claim_dir == "up" else stp,
                         stp if claim_dir == "up" else tgt)
        if r is None:
            continue
        side, dys = r
        fav = "up" if claim_dir == "up" else "down"
        if side == fav:
            ft["up"] += 1
            t_hit_days.append(dys)
        elif side == "ambiguous":
            ft["ambiguous"] += 1
        elif side == "none":
            ft["none"] += 1
        else:
            ft["down"] += 1
            t_fail_days.append(dys)
    ft_n = sum(ft.values()) or 1
    p_target_first = round(ft["up"] / ft_n * 100, 1)                    # conservative: ambiguous NOT counted
    p_stop_first = round((ft["down"] + ft["ambiguous"]) / ft_n * 100, 1)

    # Reliability label.
    flags = []
    label = "reliable"
    if len(keys) < MIN_OCC:
        label = "insufficient sample"
    if len(oos_idx) < 5:
        flags.append("few out-of-sample occurrences")
    if oos_rate is not None and (is_rate - oos_rate) > 0.20:
        label = "unstable"
        flags.append("out-of-sample much worse than in-sample")
    if fold_spread is not None and fold_spread > 35:
        label = "unstable"
        flags.append("hit rate varies widely across time folds")
    if recent_rate is not None and recent_rate < overall_rate - 15:
        if label == "reliable":
            label = "weakening"
        flags.append("recent occurrences performing worse than the long-run rate")
    if oos_rate is not None and oos_rate * 100 < base_rate * 100:
        label = "likely random"
        flags.append("failed out-of-sample (did worse than baseline)")

    # Confidence (statistical) — q-value applied by the caller after FDR.
    n_score = min(1.0, len(keys) / 30.0) * 25
    cons = 0.0 if oos_rate is None else max(0.0, 1.0 - abs(is_rate - oos_rate) / 0.35) * 25
    sig = max(0.0, 1.0 - min(1.0, p_val / 0.25)) * 25
    eff = max(0.0, (all_hits / len(keys)) - base_rate) / max(0.05, 1 - base_rate)
    confidence = round(n_score + cons + sig + min(1.0, eff * 2.0) * 25)

    # Context buckets.
    spy_reg, qqq_reg, sec_reg, mvol, svol = series_ctx
    def _bucket(label_fn):
        agg = {}
        for i in stats:
            lbl = label_fn(i)
            if lbl is None:
                continue
            d = agg.setdefault(lbl, [0, 0])
            d[0] += 1
            d[1] += 1 if _hit(i) else 0
        return {k: {"n": v[0], "rate": round(v[1] / v[0] * 100)} for k, v in agg.items() if v[0] >= 5}
    dkey = lambda i: bars[i]["date"][:10]
    def _gapdir(i):
        pc = bars[i - 1]["close"] if i else None
        if not pc:
            return None
        g = (bars[i]["open"] - pc) / pc * 100.0
        return "gap up day" if g > 0.3 else ("gap down day" if g < -0.3 else "flat open")
    def _rvol(i):
        if i < 21:
            return None
        av = sum(b.get("volume") or 0 for b in bars[i - 20:i]) / 20
        if av <= 0:
            return None
        return "high rel-volume" if (bars[i].get("volume") or 0) >= 1.5 * av else "normal volume"
    ctx = {
        "SPY trend": _bucket(lambda i: spy_reg.get(dkey(i))),
        "QQQ trend": _bucket(lambda i: qqq_reg.get(dkey(i)) if qqq_reg else None),
        "sector trend": _bucket(lambda i: sec_reg.get(dkey(i)) if sec_reg else None),
        "market vol": _bucket(lambda i: mvol.get(dkey(i)) if mvol else None),
        "stock vol": _bucket(lambda i: svol.get(dkey(i)) if svol else None),
        "gap direction": _bucket(_gapdir),
        "rel volume": _bucket(_rvol),
        "year": _bucket(lambda i: dkey(i)[:4]),
    }
    best_ctx = worst_ctx = None
    for cat, buckets in ctx.items():
        if cat == "year":
            continue
        for lbl, d in buckets.items():
            item = {"category": cat, "label": lbl, **d}
            if best_ctx is None or d["rate"] > best_ctx["rate"]:
                best_ctx = item
            if worst_ctx is None or d["rate"] < worst_ctx["rate"]:
                worst_ctx = item
    ctx_note = None
    if best_ctx and worst_ctx and best_ctx["rate"] - worst_ctx["rate"] >= 15:
        ctx_note = (f"Works best in {best_ctx['label']} ({best_ctx['rate']}% over {best_ctx['n']}), "
                    f"fails in {worst_ctx['label']} ({worst_ctx['rate']}% over {worst_ctx['n']}).")

    # Move stats + paths (avg, median, p25/p75 band).
    fwd_all = [stats[i][0] for i in keys]
    claim_side = [(v if claim_dir == "up" else -v) for v in fwd_all]
    mfes = [stats[i][1] for i in keys]
    maes = [stats[i][2] for i in keys]
    LEAD = 5
    path_len = LEAD + F + 1
    cols = [[] for _ in range(path_len)]
    occ_paths = []
    step2 = max(1, len(keys) // 30)
    for oi, i in enumerate(keys):
        entry = bars[i]["close"] if ref == "close" else bars[i]["open"]
        if not entry:
            continue
        pth = []
        for k in range(-LEAD, F + 1):
            j = i + k
            v = None
            if 0 <= j < n_bars:
                v = round((bars[j]["close"] - entry) / entry * 100.0, 2)
                cols[k + LEAD].append(v)
            pth.append(v)
        if oi % step2 == 0 and len(occ_paths) < 30:
            occ_paths.append({"date": dkey(i), "path": pth,
                              "fwd": round(stats[i][0], 2)})
    def _colq(p):
        return [round(_quantile(c, p), 2) if c else None for c in cols]
    avg_path = [round(sum(c) / len(c), 2) if c else None for c in cols]

    # Liquidity + net expected value (after est. spread ×2 + 5bps slip ×2).
    last_px = bars[-1]["close"]
    adv = sum((b.get("volume") or 0) * (b["close"] or 0) for b in bars[-20:]) / 20
    cost_pct = (_est_spread_pct(last_px) + 0.0005) * 2 * 100
    ev_gross = sum(claim_side) / len(claim_side)
    ev_net = round(ev_gross - cost_pct, 2)

    # Actionability: EV, OOS, n, consistency, reward/risk, speed, liquidity, recent.
    rr = abs(sum(mfes) / len(mfes)) / max(0.3, abs(sum(maes) / len(maes)))
    if claim_dir == "down":
        rr = abs(sum(maes) / len(maes)) / max(0.3, abs(sum(mfes) / len(mfes)))
    days_med = _median(t_hit_days) if t_hit_days else F
    act = 0.0
    act += min(1.0, max(0.0, ev_net / 3.0)) * 25                       # net EV
    act += (min(1.0, (oos_rate or 0) / max(0.05, base_rate) / 2.5)) * 15  # OOS vs baseline
    act += min(1.0, len(keys) / 30.0) * 10
    act += (0.0 if fold_spread is None else max(0.0, 1 - fold_spread / 50)) * 10
    act += min(1.0, rr / 2.0) * 15                                      # reward/risk
    act += max(0.0, 1 - (days_med or F) / 10.0) * 5                     # speed
    act += min(1.0, adv / 20_000_000) * 10                              # liquidity
    act += (0.0 if recent_rate is None else min(1.0, recent_rate / 70)) * 10
    actionability = round(act)

    sentence = (
        f"After {_describe_event(fam, params, symbol)}, it has "
        f"{'risen' if claim_dir == 'up' else 'fallen'} at least {claim_x}% within the next "
        f"{F} trading day{'s' if F != 1 else ''} "
        f"{round(overall_rate)}% of the time ({all_hits} of {len(keys)} occurrences; baseline {round(base_rate * 100)}%). "
        f"It reached the +{tgt}% target before the −{stp}% stop in {p_target_first}% of occurrences.")
    if claim_dir == "down":
        sentence = sentence.replace(f"+{tgt}% target before the −{stp}% stop",
                                    f"−{tgt}% target before the +{stp}% stop")

    ev_dir = _event_dir(fam, params)
    kind = ["bullish" if claim_dir == "up" else "bearish",
            "mean-reverting" if claim_dir != ev_dir else "momentum",
            "multi-day" if F > 1 else "short-term"]

    return {
        "id": f"{fam}:{json.dumps({k: v for k, v in params.items() if k != 'medoid'}, sort_keys=True)}:{F}:{claim_dir}",
        "family": fam, "params": params, "window": F,
        "event": _describe_event(fam, params, symbol),
        "claim": {"dir": claim_dir, "min_move_pct": claim_x, "within_days": F},
        "sentence": sentence, "kind": kind,
        "n": len(keys), "n_is": len(is_idx), "n_oos": len(oos_idx),
        "hit_rate": round(overall_rate, 1),
        "hit_rate_is": round(is_rate * 100, 1),
        "hit_rate_oos": round(oos_rate * 100, 1) if oos_rate is not None else None,
        "baseline_rate": round(base_rate * 100, 1),
        "p_value": round(p_val, 4),
        "boot_ci": boot_ci,
        "folds": folds, "fold_spread": fold_spread,
        "label": label, "flags": flags,
        "confidence": confidence,
        "actionability": actionability,
        "ev_net_pct": ev_net,
        "first_touch": {"target_pct": tgt, "stop_pct": stp,
                        "p_target_first": p_target_first,
                        "p_stop_first": p_stop_first,
                        "p_neither": round(ft["none"] / ft_n * 100, 1),
                        "p_ambiguous": round(ft["ambiguous"] / ft_n * 100, 1),
                        "median_days_to_target": _median(t_hit_days),
                        "median_days_to_stop": _median(t_fail_days)},
        "move": {"avg": round(sum(claim_side) / len(claim_side), 2),
                 "median": round(_median(claim_side), 2),
                 "max": round(max(claim_side), 2), "min": round(min(claim_side), 2),
                 "p25": round(_quantile(claim_side, 0.25), 2),
                 "p75": round(_quantile(claim_side, 0.75), 2)},
        "days_to_move_median": days_med,
        "mfe_avg": round(sum(mfes) / len(mfes), 2),
        "mae_avg": round(sum(maes) / len(maes), 2),
        "context": ctx, "best_context": best_ctx, "worst_context": worst_ctx,
        "context_note": ctx_note,
        "chart": {"lead": LEAD, "avg_path": avg_path,
                  "median_path": _colq(0.5), "p25_path": _colq(0.25), "p75_path": _colq(0.75),
                  "occurrences": occ_paths},
        "occ_keys": keys,   # internal: dedupe + current-setup similarity (stripped later)
        "backtest_rules": _to_backtest_rules(symbol, fam, params, claim_dir, claim_x, stp, F),
        "options_idea": _options_idea(symbol, claim_dir, claim_x, F, days_med),
    }


def _options_idea(symbol, claim_dir, claim_x, F, days_med):
    dte = max(7, int((days_med or F) * 2))
    right = "call" if claim_dir == "up" else "put"
    return {
        "right": right, "dte": dte, "strike": "ATM",
        "note": (f"Expected {'+' if claim_dir == 'up' else '−'}{claim_x}% in ~{days_med or F} trading days → "
                 f"long {right.upper()}s ~{dte} DTE at the money give the move room without paying for excess time. "
                 f"Premiums in the backtester are MODELED (no historical option quotes) — treat as an estimate."),
    }


def _to_backtest_rules(symbol, fam, params, claim_dir, claim_x, stop_x, F):
    entry = []
    if fam == "surge":
        entry.append({"type": "move_pct", "days": params["w"],
                      "op": ">=" if params["dir"] == "up" else "<=",
                      "value": params["x"] if params["dir"] == "up" else -params["x"]})
    elif fam == "gap":
        entry.append({"type": "gap_pct", "op": ">=" if params["dir"] == "up" else "<=",
                      "value": params["x"] if params["dir"] == "up" else -params["x"]})
    elif fam == "shock_vol":
        entry.append({"type": "day_change_pct", "op": "<=" if params["dir"] == "down" else ">=",
                      "value": -params["x"] if params["dir"] == "down" else params["x"]})
        entry.append({"type": "rel_volume", "mult": params["mult"], "lookback": 20})
    elif fam == "new_extreme":
        entry.append({"type": "new_high" if params["dir"] == "high" else "new_low",
                      "lookback": params["lookback"]})
    elif fam == "consec":
        entry.append({"type": "consec_down" if params["dir"] == "down" else "consec_up",
                      "n": params["n"]})
    elif fam == "drawdown":
        entry.append({"type": "drawdown_from_high", "pct": params["x"], "lookback": 252})
    elif fam == "custom":
        entry = params.get("conds") or []
    elif fam == "shape":
        # No 1:1 backtest condition for a clustered shape — approximate with
        # the shape's net move over its window; the card says so.
        net = sum(params.get("medoid") or [0])
        entry.append({"type": "move_pct", "days": _SHAPE_W,
                      "op": ">=" if net > 0 else "<=",
                      "value": round(abs(net) * 1.0, 1) * (1 if net > 0 else -1)})
    return {
        "instrument": "stock",
        "direction": "long" if claim_dir == "up" else "short",
        "universe": {"source": "symbols", "symbols": [symbol]},
        "entry": entry,
        "exit": [{"type": "profit_pct", "value": claim_x},
                 {"type": "stop_pct", "value": stop_x},
                 {"type": "time_days", "value": F}],
        "sizing": {"mode": "fixed_dollar", "value": 10000, "max_positions": 5},
        "costs": {"commission": 0.0, "slippage_bps": 5, "spread_model": "auto",
                  "min_dollar_vol_mult": 20},
        "options": None,
        "period_days": 3650,
    }


# ── discovery orchestrator ──────────────────────────────────────────────────

def discover(symbol: str) -> dict:
    c = _schwab_getter()
    if c is None:
        return {"error": "Schwab is not connected — historical data unavailable."}
    symbol = symbol.upper().strip()
    bars = c.get_price_history(symbol, days=HIST_DAYS) or []
    if len(bars) < 250:
        return {"error": f"Not enough daily history for {symbol} ({len(bars)} bars) — need ≥250."}
    spy = c.get_price_history("SPY", days=HIST_DAYS) or []
    qqq = c.get_price_history("QQQ", days=HIST_DAYS) or []
    sector, next_earn, days_to_earn = _sector_of(symbol)
    sec_etf = _SECTOR_ETF.get((sector or "").lower())
    sec_bars = c.get_price_history(sec_etf, days=HIST_DAYS) if sec_etf else None
    series_ctx = (
        _spy_regimes(spy) if spy else {},
        _spy_regimes(qqq) if qqq else {},
        _spy_regimes(sec_bars) if sec_bars else {},
        _vol_state_series(spy) if spy else {},
        _vol_state_series(bars),
    )

    candidates = []
    grid = _event_grid(bars) + _discover_shapes(bars)
    for fam, params in grid:
        for F in (2, 3, 5, 10):
            try:
                pat = _analyze_spec(symbol, bars, fam, params, F, _FAMILIES[fam]["ref"], series_ctx)
            except Exception:
                pat = None
            if pat:
                candidates.append(pat)

    # Multiple-testing correction across EVERYTHING that was searched.
    if candidates:
        qvals = _bh_fdr([p["p_value"] for p in candidates])
        for p, qv in zip(candidates, qvals):
            p["q_value"] = round(qv, 4)
            if qv > FDR_Q:
                p["label"] = "likely random"
                if "did not survive multiple-testing correction" not in p["flags"]:
                    p["flags"].append("did not survive multiple-testing correction "
                                      f"(q={round(qv, 3)} across {len(candidates)} candidates searched)")
                p["confidence"] = min(p["confidence"], 40)
                p["actionability"] = min(p["actionability"], 40)

    # Rank by actionability; dedupe near-identical discoveries by occurrence overlap.
    candidates.sort(key=lambda p: (-p["actionability"], -p["confidence"], -p["n"]))
    kept = []
    for p in candidates:
        s = set(p["occ_keys"])
        dup = False
        for k in kept:
            inter = len(s & set(k["occ_keys"]))
            union = len(s | set(k["occ_keys"]))
            if union and inter / union > 0.5 and p["claim"]["dir"] == k["claim"]["dir"]:
                dup = True
                break
        if not dup:
            kept.append(p)
        if len(kept) >= 18:
            break

    # ── Current Setup: what's active on the LATEST bar ──
    last_i = len(bars) - 1
    last_close = bars[last_i]["close"]
    active = []
    for p in kept:
        try:
            det = _FAMILIES[p["family"]]["det"]
            trig = det(bars, last_i, p["params"]) if det else False
        except Exception:
            trig = False
        p["triggered_now"] = bool(trig)
        if not trig:
            continue
        # Similarity: today's context vs the pattern's best bucket + event freshness.
        sim = 0.5
        bc = p.get("best_context")
        today = bars[last_i]["date"][:10]
        if bc:
            cur = None
            cat = bc["category"]
            if cat == "SPY trend":
                cur = series_ctx[0].get(today)
            elif cat == "QQQ trend":
                cur = series_ctx[1].get(today)
            elif cat == "sector trend":
                cur = series_ctx[2].get(today)
            elif cat == "market vol":
                cur = series_ctx[3].get(today)
            elif cat == "stock vol":
                cur = series_ctx[4].get(today)
            if cur is not None:
                sim = 0.85 if cur == bc["label"] else 0.35
        ft = p["first_touch"]
        dirn = p["claim"]["dir"]
        up_amt = ft["target_pct"] if dirn == "up" else ft["stop_pct"]
        dn_amt = ft["stop_pct"] if dirn == "up" else ft["target_pct"]
        invalidation = last_close * (1 + (p["mae_avg"] if dirn == "up" else -p["mfe_avg"]) / 100.0 * 1.25)
        active.append({
            "id": p["id"], "sentence": p["sentence"], "label": p["label"],
            "kind": p["kind"], "window": p["window"],
            "similarity": round(sim * 100),
            "actionability_now": round(p["actionability"] * (0.5 + 0.5 * sim)),
            "expected": {"dir": dirn,
                         "median_pct": p["move"]["median"] if dirn == "up" else -p["move"]["median"],
                         "p25_pct": p["move"]["p25"] if dirn == "up" else -p["move"]["p25"],
                         "p75_pct": p["move"]["p75"] if dirn == "up" else -p["move"]["p75"]},
            "levels": {
                "target_px": round(last_close * (1 + (up_amt if dirn == "up" else -up_amt) / 100.0), 2),
                "target_prob": ft["p_target_first"],
                "stop_px": round(last_close * (1 - (dn_amt if dirn == "up" else -dn_amt) / 100.0), 2),
                "stop_prob": ft["p_stop_first"],
                "invalidation_px": round(invalidation, 2),
            },
            "typical_days": p["days_to_move_median"],
        })
    active.sort(key=lambda a: -a["actionability_now"])

    for p in kept:
        p.pop("occ_keys", None)

    return {
        "symbol": symbol, "bars": len(bars),
        "from": bars[0]["date"][:10], "to": bars[-1]["date"][:10],
        "split_date": bars[int(len(bars) * 0.7)]["date"][:10],
        "last_close": last_close,
        "sector": sector, "sector_etf": sec_etf,
        "next_earnings": next_earn, "days_to_earnings": days_to_earn,
        "candidates_searched": len(candidates),
        "patterns": kept,
        "current_setup": {"active": active[:8], "top3": active[:3],
                          "as_of": bars[last_i]["date"][:10]},
        "generated": datetime.now().isoformat(timespec="seconds"),
        "notes": [
            f"History: {len(bars)} adjusted daily bars (~{round(len(bars) / 252, 1)} years). Claims fitted on the first 70%, validated on the last 30%; 4-fold walk-forward and bootstrap CIs shown per pattern.",
            f"{len(candidates)} candidate patterns were searched — hit rates are corrected for multiple testing (Benjamini-Hochberg, q≤{FDR_Q}); anything that doesn't survive is labeled likely random.",
            "First-touch races use daily highs/lows; when target and stop land inside the SAME bar the case is 'ambiguous' and counted against the pattern (conservative). Intraday sequences (mined separately below) use exact minute ordering.",
            "Historical earnings dates, news days, IV, options flow and expected-move context are NOT available in this app's data — context uses SPY/QQQ/sector-ETF trend, volatility states, gap direction, relative volume, and year. Current earnings proximity is shown on the live setup.",
        ],
    }


# ── NL research: question → visible rules → analysis ────────────────────────

_TICKER_RX = re.compile(r"\b([A-Z]{2,5})\b")
_STOPWORDS = {"WHAT", "DOES", "AFTER", "THE", "THIS", "WHEN", "FIND", "RSI", "DTE",
              "OTM", "ITM", "ATM", "VWAP", "SPY", "QQQ", "AND", "OR", "HAS", "HAD",
              "ITS", "IT", "IN", "ON", "OF", "TO", "UP", "DOWN", "DAY", "DAYS", "MA"}

def ask(text: str, default_symbol: str = "") -> dict:
    import backtest as _bt
    text = (text or "").strip()
    if not text:
        return {"error": "Ask a question first."}
    sym = None
    for m in _TICKER_RX.finditer(text):
        w = m.group(1)
        if w not in _STOPWORDS:
            sym = w
            break
    sym = (sym or default_symbol or "").upper()
    if not sym:
        return {"error": "Couldn't find a ticker in the question — include one (e.g. NFLX) or select it in the app."}
    parsed = _bt.parse_strategy(text)
    conds = parsed["rules"]["entry"]
    warnings = parsed["warnings"]
    if not conds:
        return {"error": "No recognizable condition in the question — try phrasing like 'after rising 10% in 3 days' or 'after it gaps down 2%'.",
                "warnings": warnings, "unparsed": parsed["unparsed"]}
    c = _schwab_getter()
    if c is None:
        return {"error": "Schwab is not connected."}
    bars = c.get_price_history(sym, days=HIST_DAYS) or []
    if len(bars) < 250:
        return {"error": f"Not enough history for {sym}."}
    spy = c.get_price_history("SPY", days=HIST_DAYS) or []
    series_ctx = (_spy_regimes(spy) if spy else {}, {}, {},
                  _vol_state_series(spy) if spy else {}, _vol_state_series(bars))
    # Evaluate the parsed conditions with the backtester's own context class
    # (which needs a move_pct check — see backtest._Ctx) — same semantics as
    # a backtest entry, no look-ahead by construction.
    bt_ctx = _bt._Ctx(bars, series_ctx[0])

    def _humanize(c0):
        t = c0["type"]
        v = c0.get("value")
        if t == "move_pct":
            return f"{'rises' if (v or 0) > 0 else 'falls'} ≥{abs(v)}% within {c0.get('days')} days"
        if t == "gap_pct":
            return f"opens {'up' if (v or 0) > 0 else 'down'} ≥{abs(v)}%"
        if t == "day_change_pct":
            return f"{'gains' if (v or 0) > 0 else 'drops'} ≥{abs(v)}% in one day"
        if t == "rel_volume":
            return f"volume ≥{c0.get('mult')}× the {c0.get('lookback', 20)}-day average"
        if t == "drawdown_from_high":
            return f"sits ≥{c0.get('pct')}% below its recent high"
        if t == "rsi":
            return f"RSI({c0.get('period', 14)}) {'≤' if c0.get('op') == '<=' else '≥'} {v}"
        if t in ("consec_down", "consec_up"):
            return f"closes {'down' if t == 'consec_down' else 'up'} {c0.get('n')} days in a row"
        if t in ("new_high", "new_low"):
            return f"makes a new {c0.get('lookback')}-day {'high' if t == 'new_high' else 'low'}"
        return c0.get("label") or t
    desc = " and ".join(_humanize(cnd) for cnd in conds)
    params = {"conds": conds, "describe": f"{sym} {desc}"}
    best = None
    for F in (2, 3, 5, 10):
        try:
            pat = _analyze_spec(sym, bars, "custom", params, F, "close", series_ctx,
                                conds=conds, bt_ctx=bt_ctx)
        except Exception:
            pat = None
        if pat and (best is None or pat["confidence"] > best["confidence"]):
            best = pat
    if not best:
        return {"symbol": sym, "conditions": conds, "warnings": warnings,
                "answer": (f"That setup occurred fewer than {MIN_OCC} independent times in "
                           f"{round(len(bars) / 252, 1)} years of {sym} history — not enough to say anything honest about it."),
                "unparsed": parsed["unparsed"]}
    best.pop("occ_keys", None)
    best["triggered_now"] = False
    return {"symbol": sym, "conditions": conds, "warnings": warnings,
            "unparsed": parsed["unparsed"], "pattern": best,
            "answer": best["sentence"] + f" Reliability: {best['label']}."}


# ── intraday sequence mining (background job, exact minute ordering) ────────

def _intraday_dir():
    d = (_data_dir / "pattern_intraday") if _data_dir else None
    if d:
        d.mkdir(parents=True, exist_ok=True)
    return d

def _tokenize_day(minutes, prev_close):
    """Minute bars of one session → ordered event tokens with EXACT order."""
    if not minutes or len(minutes) < 60 or not prev_close:
        return None
    o = minutes[0]["open"]
    if not o:
        return None
    toks = []
    gap = (o - prev_close) / prev_close * 100.0
    toks.append(("gap_up" if gap >= 0.75 else "gap_down" if gap <= -0.75 else "open_flat", 0))
    n30 = min(30, len(minutes))
    or_hi = max(m["high"] for m in minutes[:n30])
    or_lo = min(m["low"] for m in minutes[:n30])
    if min(m["low"] for m in minutes[:n30]) > o * 0.997:
        toks.append(("holds_above_open_30m", n30))
    # VWAP series.
    cum_pv = cum_v = 0.0
    vwap = []
    for m in minutes:
        px = (m["high"] + m["low"] + m["close"]) / 3.0
        v = m.get("volume") or 0
        cum_pv += px * v
        cum_v += v
        vwap.append(cum_pv / cum_v if cum_v else px)
    seen = set()
    morning_hi = or_hi
    above_vwap_ago = False
    for k in range(n30, len(minutes)):
        m = minutes[k]
        px = m["close"]
        if "or_break_up" not in seen and m["high"] > or_hi:
            seen.add("or_break_up")
            toks.append(("or_break_up", k))
        if "or_break_down" not in seen and m["low"] < or_lo:
            seen.add("or_break_down")
            toks.append(("or_break_down", k))
        if k <= 120:
            morning_hi = max(morning_hi, m["high"])
        if px > vwap[k] * 1.004:
            above_vwap_ago = True
        if "pullback_to_vwap" not in seen and above_vwap_ago and px <= vwap[k] * 1.0015:
            seen.add("pullback_to_vwap")
            toks.append(("pullback_to_vwap", k))
        if "loses_vwap" not in seen and px < vwap[k] * 0.998:
            seen.add("loses_vwap")
            toks.append(("loses_vwap", k))
        if "reclaims_vwap" not in seen and "loses_vwap" in seen and px > vwap[k] * 1.002:
            seen.add("reclaims_vwap")
            toks.append(("reclaims_vwap", k))
        if "reclaims_morning_high" not in seen and k > 120 and m["high"] > morning_hi:
            seen.add("reclaims_morning_high")
            toks.append(("reclaims_morning_high", k))
    if len(minutes) >= 380:
        ph = (minutes[-1]["close"] - minutes[-60]["close"]) / minutes[-60]["close"] * 100.0
        if ph >= 0.3:
            toks.append(("power_hour_up", len(minutes) - 60))
        elif ph <= -0.3:
            toks.append(("power_hour_down", len(minutes) - 60))
    toks.sort(key=lambda t: t[1])
    closes = [m["close"] for m in minutes]
    return {"tokens": [{"tok": t, "k": k, "px": closes[min(k, len(closes) - 1)]} for t, k in toks],
            "open": o, "close": closes[-1], "prev_close": prev_close,
            "n_minutes": len(minutes)}

_TOK_WORDS = {
    "gap_up": "gaps up", "gap_down": "gaps down", "open_flat": "opens flat",
    "holds_above_open_30m": "holds above the opening price for 30 minutes",
    "or_break_up": "breaks the opening range high", "or_break_down": "breaks the opening range low",
    "pullback_to_vwap": "pulls back to VWAP", "loses_vwap": "loses VWAP",
    "reclaims_vwap": "reclaims VWAP", "reclaims_morning_high": "reclaims the morning high",
    "power_hour_up": "rallies into the close", "power_hour_down": "sells off into the close",
}

def mine_intraday(symbol: str, progress_cb=None) -> dict:
    c = _schwab_getter()
    if c is None:
        return {"error": "Schwab is not connected."}
    symbol = symbol.upper().strip()
    daily = c.get_price_history(symbol, days=400) or []
    if len(daily) < 40:
        return {"error": f"Not enough daily history for {symbol}."}
    d = _intraday_dir()
    arch_path = (d / f"{symbol}.json") if d else None
    archive = {}
    if arch_path and arch_path.exists():
        try:
            archive = json.loads(arch_path.read_text())
        except Exception:
            archive = {}
    # Fetch + tokenize sessions not yet archived (minute data ~6mo back; the
    # archive keeps everything ever mined, so coverage grows over time).
    days = [(daily[i]["date"][:10], daily[i - 1]["close"]) for i in range(1, len(daily))]
    days = days[-130:]
    todo = [(dt, pc) for dt, pc in days if dt not in archive]
    for idx, (dt, pc) in enumerate(todo):
        if progress_cb:
            progress_cb("fetching minute bars", idx + 1, len(todo))
        try:
            minutes = _minute_day_fn(symbol, dt)
            tk = _tokenize_day(minutes, pc)
            archive[dt] = tk or {"skip": True}
        except Exception:
            archive[dt] = {"skip": True}
    if arch_path:
        try:
            tmp = arch_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(archive, separators=(",", ":")))
            tmp.replace(arch_path)
        except Exception:
            pass

    sessions = {dt: v for dt, v in archive.items() if v and not v.get("skip")}
    if len(sessions) < 25:
        return {"error": f"Only {len(sessions)} usable minute sessions for {symbol} — need ≥25. "
                         "Minute data reaches back ~6 months; the archive grows as the app keeps running."}

    # Mine contiguous ordered sub-sequences (length 2–4) and measure EXACT
    # outcomes from the completion minute to the close.
    seqs = {}
    for dt, s in sorted(sessions.items()):
        toks = [t["tok"] for t in s["tokens"]]
        pxs = [t["px"] for t in s["tokens"]]
        for a in range(len(toks)):
            for ln in (2, 3, 4):
                if a + ln > len(toks):
                    break
                key = "|".join(toks[a:a + ln])
                px0 = pxs[a + ln - 1]
                if not px0:
                    continue
                rest = (s["close"] - px0) / px0 * 100.0
                seqs.setdefault(key, []).append({"date": dt, "rest": rest})
    results = []
    all_p = []
    base_all = []
    for dt, s in sessions.items():
        if s["open"]:
            base_all.append((s["close"] - s["open"]) / s["open"] * 100.0)
    for key, occ in seqs.items():
        if len(occ) < 10:
            continue
        rests = [o["rest"] for o in occ]
        med = _median(rests)
        if med is None or abs(med) < 0.15:
            continue
        dirn = "up" if med > 0 else "down"
        hits = sum(1 for r in rests if (r > 0) == (dirn == "up"))
        rate = hits / len(rests)
        base = sum(1 for r in base_all if (r > 0) == (dirn == "up")) / max(1, len(base_all))
        p = _binom_p(hits, len(rests), max(0.05, base))
        words = ", then ".join(_TOK_WORDS.get(t, t) for t in key.split("|"))
        results.append({
            "sequence": key, "n": len(occ),
            "sentence": (f"When {symbol} {words}, it has closed {'higher' if dirn == 'up' else 'lower'} "
                         f"from that point {round(rate * 100)}% of the time "
                         f"(median {'+' if med > 0 else ''}{round(med, 2)}% to the close; {len(occ)} sessions; "
                         f"baseline {round(base * 100)}%)."),
            "dir": dirn, "hit_rate": round(rate * 100, 1), "baseline": round(base * 100, 1),
            "median_rest_pct": round(med, 2), "p_value": round(p, 4),
            "dates": [o["date"] for o in occ][-8:],
        })
        all_p.append(p)
    if results:
        qv = _bh_fdr([r["p_value"] for r in results])
        for r, q in zip(results, qv):
            r["q_value"] = round(q, 4)
            r["label"] = "reliable" if (q <= FDR_Q and r["n"] >= 15) else \
                ("small sample" if r["n"] < 15 else "likely random")
    results.sort(key=lambda r: (r["label"] != "reliable", -abs(r["median_rest_pct"]) * r["n"]))
    return {"symbol": symbol, "sessions": len(sessions),
            "from": min(sessions), "to": max(sessions),
            "sequences": results[:20],
            "generated": datetime.now().isoformat(timespec="seconds"),
            "notes": ["Sequence order is EXACT (minute bars), including VWAP and morning-high mechanics.",
                      "Outcomes = move from the minute the sequence completed to that day's close.",
                      f"Minute data reaches ~6 months back; every mined session is archived, so coverage grows ({len(sessions)} sessions so far).",
                      "Sequences are FDR-corrected across everything mined; 'likely random' means exactly that."]}


# ── scan / compare across symbols ───────────────────────────────────────────

def scan_pattern(fam: str, params: dict, symbols: list | None = None) -> dict:
    c = _schwab_getter()
    if c is None:
        return {"error": "Schwab is not connected."}
    det = _FAMILIES.get(fam, {}).get("det")
    if det is None:
        return {"error": f"Can't scan family '{fam}'."}
    if not symbols:
        u = _universe_fn() or {}
        symbols = list(u.get("starred") or [])[:30] or list(u.get("all") or [])[:30]
    rows = []
    for sym in symbols[:30]:
        try:
            bars = c.get_price_history(sym, days=1000) or []
            if len(bars) < 120:
                continue
            i = len(bars) - 1
            trig = bool(det(bars, i, params))
            occ = _collect_occurrences(bars, det, params, max(2, params.get("w", 1)))
            n = hits = 0
            for j in occ:
                st = _fwd_stats(bars, j, 5, "close")
                if st is None:
                    continue
                n += 1
                hits += 1 if st[0] > 0 else 0
            rows.append({"symbol": sym, "triggered": trig, "n": n,
                         "up_rate_5d": round(hits / n * 100, 1) if n else None})
        except Exception:
            continue
    rows.sort(key=lambda r: (not r["triggered"], -(r["up_rate_5d"] or 0)))
    return {"rows": rows, "note": "Same event spec evaluated on each symbol's own ~4y history; 'up rate' = % of occurrences closing higher 5 days later. Triggered = the setup is true on the latest bar."}


# ── caches / jobs / watches ─────────────────────────────────────────────────

_CACHE: dict[str, dict] = {}
_CACHE_LOCK = threading.Lock()

def discover_cached(symbol: str) -> dict:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"error": "symbol required"}
    with _CACHE_LOCK:
        hit = _CACHE.get(symbol)
    if hit and time.time() - hit["_t"] < 6 * 3600:
        return hit["res"]
    res = discover(symbol)
    if not res.get("error"):
        with _CACHE_LOCK:
            if len(_CACHE) > 30:
                _CACHE.pop(next(iter(_CACHE)), None)
            _CACHE[symbol] = {"_t": time.time(), "res": res}
    return res

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_INTRA_RESULTS: dict[str, dict] = {}

def start_intraday_job(symbol: str) -> dict:
    symbol = (symbol or "").upper().strip()
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "symbol": symbol, "status": "running",
           "progress": {"phase": "starting", "done": 0, "total": 1},
           "started": datetime.now().isoformat(timespec="seconds")}
    with _JOBS_LOCK:
        for k in sorted(_JOBS, key=lambda k: _JOBS[k]["started"])[:-5]:
            _JOBS.pop(k, None)
        _JOBS[job_id] = job

    def _cb(phase, done, total):
        job["progress"] = {"phase": phase, "done": done, "total": total}

    def _run():
        try:
            res = mine_intraday(symbol, progress_cb=_cb)
            job["result"] = res
            job["status"] = "error" if res.get("error") else "done"
            if not res.get("error"):
                _INTRA_RESULTS[symbol] = res
        except Exception as exc:  # noqa: BLE001
            job["status"] = "error"
            job["result"] = {"error": f"Intraday mining crashed: {exc}"}

    threading.Thread(target=_run, daemon=True, name=f"pattern-intraday-{job_id}").start()
    return {"job": job_id}

def intraday_status(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return {"error": "unknown job"}
    out = {"id": job["id"], "status": job["status"], "progress": job["progress"]}
    if job["status"] in ("done", "error"):
        out["result"] = job.get("result")
    return out

def intraday_cached(symbol: str) -> dict:
    return _INTRA_RESULTS.get((symbol or "").upper().strip()) or {}


def _watches_path():
    return (_data_dir / "pattern_watches.json") if _data_dir else None

def _load_watches() -> list:
    p = _watches_path()
    if p and p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return []
    return []

def _save_watches(w: list):
    p = _watches_path()
    if not p:
        return
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(w, separators=(",", ":")))
    tmp.replace(p)

def add_watch(symbol: str, pattern: dict) -> dict:
    w = _load_watches()
    wid = f"{symbol.upper()}::{pattern.get('id')}"
    if any(x["id"] == wid for x in w):
        return {"ok": True, "id": wid, "note": "already watching"}
    w.append({"id": wid, "symbol": symbol.upper(),
              "family": pattern.get("family"), "params": pattern.get("params"),
              "sentence": pattern.get("sentence"), "claim": pattern.get("claim"),
              "confidence": pattern.get("confidence"),
              "created": datetime.now().isoformat(timespec="seconds")})
    _save_watches(w[-40:])
    return {"ok": True, "id": wid}

def remove_watch(wid: str) -> dict:
    _save_watches([x for x in _load_watches() if x["id"] != wid])
    return {"ok": True}

def check_watches() -> dict:
    c = _schwab_getter()
    out = []
    for wt in _load_watches():
        status = {"triggered": False, "checked": None}
        try:
            if c is not None:
                bars = c.get_price_history(wt["symbol"], days=400) or []
                if len(bars) > 30:
                    det = _FAMILIES.get(wt["family"], {}).get("det")
                    i = len(bars) - 1
                    if det and det(bars, i, wt["params"]):
                        status["triggered"] = True
                    status["checked"] = bars[i]["date"][:10]
        except Exception:
            pass
        out.append({**wt, **status})
    return {"watches": out}

_PINGER_STARTED = False
_PUSHED: dict[str, str] = {}

def ensure_pinger():
    global _PINGER_STARTED
    if _PINGER_STARTED or _notify_fn is None:
        return
    _PINGER_STARTED = True

    def _loop():
        while True:
            try:
                now = datetime.now()
                if now.weekday() < 5 and 9 <= now.hour <= 16 and _load_watches():
                    for wt in check_watches()["watches"]:
                        if not wt.get("triggered"):
                            continue
                        day = wt.get("checked") or now.date().isoformat()
                        if _PUSHED.get(wt["id"]) == day:
                            continue
                        _PUSHED[wt["id"]] = day
                        try:
                            _notify_fn(f"Pattern triggered: {wt['symbol']}",
                                       (wt.get("sentence") or wt["id"])[:230])
                        except Exception:
                            pass
            except Exception:
                pass
            time.sleep(1800)

    threading.Thread(target=_loop, daemon=True, name="pattern-watch-pinger").start()
