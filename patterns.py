# Per-stock pattern discovery engine (app v3.44).
#
# Learns each stock's "personality": an event-study sweep over a grid of
# parameterized event templates whose thresholds ADAPT to the stock's own
# return/gap/drawdown distributions (percentile-based) — so the engine finds
# behaviors specific to that name instead of matching preset chart patterns.
#
# Statistical honesty (the request demands it and every result reports it):
#   - Signals use information available at the event bar only; outcomes are
#     measured strictly forward (no look-ahead, no leakage).
#   - History splits into IN-SAMPLE (first 70%, where the claim is fitted)
#     and OUT-OF-SAMPLE (last 30%, where it is validated). Both hit rates
#     are shown; divergence crushes the confidence score.
#   - Each claim is tested against the BASELINE rate — how often the same
#     move happens after any random day — with a binomial z-test; patterns
#     that don't beat baseline are marked "possibly random".
#   - Small samples (n<8 total or <3 out-of-sample) are flagged and capped.
#   - Occurrences are spaced apart so overlapping windows don't double count.
#
# Data reality: Schwab daily bars reach ~2 years (~500 bars). Same-day gap
# outcomes are measured from that day's OHLC, which cannot order intraday
# highs and lows — sequence-dependent claims are therefore NOT generated,
# and MFE/MAE are labeled approximations. Sector-relative context is not
# available (no sector index data); market context uses SPY regime,
# volatility and volume buckets, and by-year counts instead.

from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime
from pathlib import Path

_schwab_getter = lambda: None
_universe_fn = lambda: {"starred": [], "all": []}
_notify_fn = None
_data_dir: Path | None = None

def configure(schwab_getter, universe_fn, data_dir, notify_fn=None):
    global _schwab_getter, _universe_fn, _data_dir, _notify_fn
    _schwab_getter = schwab_getter
    _universe_fn = universe_fn
    _data_dir = Path(data_dir) if data_dir else None
    _notify_fn = notify_fn


# ── small stats helpers (stdlib only) ───────────────────────────────────────

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
    """One-sided normal-approx binomial test: P(X >= hits | p0)."""
    if n <= 0 or p0 <= 0 or p0 >= 1:
        return 1.0
    mu = n * p0
    sd = math.sqrt(n * p0 * (1 - p0))
    if sd <= 0:
        return 1.0
    z = (hits - 0.5 - mu) / sd     # continuity correction
    return 0.5 * (1.0 - math.erf(z / math.sqrt(2.0)))

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


# ── event templates ─────────────────────────────────────────────────────────
# Each detector answers: did the event fire at bar i, using info ≤ bar i?
# `ref` says where outcomes are measured from ("close" of bar i, or "open").

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
    ok = r <= -p["x"] if p["dir"] == "down" else r >= p["x"]
    if not ok:
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
    # Fire on the CROSSING day only, so a long slump isn't 50 occurrences.
    hi2 = max(b["close"] for b in bars[max(0, i - 1 - n):i])
    dd_prev = (hi2 - bars[i - 1]["close"]) / hi2 * 100.0 if hi2 > 0 else 0
    return dd_prev < p["x"]

_FAMILIES = {
    "surge": {"det": _det_surge, "ref": "close"},
    "gap": {"det": _det_gap, "ref": "open"},
    "shock_vol": {"det": _det_shock_vol, "ref": "close"},
    "new_extreme": {"det": _det_new_extreme, "ref": "close"},
    "consec": {"det": _det_consec, "ref": "close"},
    "drawdown": {"det": _det_drawdown, "ref": "close"},
}


def _event_grid(bars):
    """Build the per-stock parameter grid from ITS OWN distributions."""
    closes = [b["close"] for b in bars]
    grid = []
    # W-day move thresholds at the stock's own 90th / 97th percentiles.
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
    # Gap thresholds at the stock's own 85th / 95th gap percentiles.
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
    # One-day shock on heavy volume: 8th/92nd percentile day, 2x volume.
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
    grid.append(("consec", {"n": 3, "dir": "down"}))
    grid.append(("consec", {"n": 3, "dir": "up"}))
    # Drawdown crossings at the stock's own 80th / 95th drawdown percentiles.
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
    return fam

def _event_dir(fam, p):
    d = p.get("dir", "down")
    if fam == "new_extreme":
        return "up" if d == "high" else "down"
    if fam == "drawdown":
        return "down"
    return d


def _collect_occurrences(bars, det, params, spacing):
    occ = []
    last = -10_000
    for i in range(1, len(bars) - 1):
        if i - last < spacing:
            continue
        try:
            if det(bars, i, params):
                occ.append(i)
                last = i
        except Exception:
            continue
    return occ


def _fwd_stats(bars, i, F, ref):
    """Forward outcome from event bar i over F bars. ref: close|open.
    Returns (fwd_ret_pct, mfe_pct, mae_pct, days_to_peak, days_to_trough)."""
    entry = bars[i]["close"] if ref == "close" else bars[i]["open"]
    if not entry:
        return None
    end = min(len(bars) - 1, i + F) if ref == "close" else min(len(bars) - 1, i + F)
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
    exit_px = bars[end]["close"]
    fwd = (exit_px - entry) / entry * 100.0
    return fwd, mfe, mae, hi_k - i, lo_k - i


def _baseline_rate(bars, F, ref, claim_dir, claim_x):
    hits = n = 0
    for i in range(1, len(bars) - 1 - F):
        st = _fwd_stats(bars, i, F, ref)
        if st is None:
            continue
        n += 1
        v = st[0] if claim_dir == "up" else -st[0]
        if v >= claim_x:
            hits += 1
    return (hits / n if n else 0.0), n


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


def discover(symbol: str) -> dict:
    c = _schwab_getter()
    if c is None:
        return {"error": "Schwab is not connected — historical data unavailable."}
    symbol = symbol.upper().strip()
    bars = c.get_price_history(symbol, days=730) or []
    if len(bars) < 120:
        return {"error": f"Not enough daily history for {symbol} ({len(bars)} bars) — need ≥120."}
    spy = c.get_price_history("SPY", days=730) or []
    spy_reg = _spy_regimes(spy) if spy else {}
    closes = [b["close"] for b in bars]
    n_bars = len(bars)
    split = int(n_bars * 0.70)     # IS = [0, split), OOS = [split, end)
    rv_series = [None] * n_bars
    for i in range(n_bars):
        rv_series[i] = _rv20(closes, i)
    rv_vals = [v for v in rv_series if v is not None]
    rv_med = _median(rv_vals) if rv_vals else None

    patterns = []
    grid = _event_grid(bars)
    for fam, params in grid:
        spec = _FAMILIES[fam]
        w_span = params.get("w", 1)
        spacing = max(2, w_span)
        occ = _collect_occurrences(bars, spec["det"], params, spacing)
        if len(occ) < 8:
            continue
        for F in (2, 3, 5, 10):
            usable = [i for i in occ if i + F < n_bars]
            if len(usable) < 8:
                continue
            stats = {}
            for i in usable:
                st = _fwd_stats(bars, i, F, spec["ref"])
                if st is not None:
                    stats[i] = st
            if len(stats) < 8:
                continue
            is_idx = [i for i in stats if i < split]
            oos_idx = [i for i in stats if i >= split]
            if len(is_idx) < 5:
                continue

            # ── fit the claim ON IN-SAMPLE ONLY ──
            is_fwd = [stats[i][0] for i in is_idx]
            med = _median(is_fwd)
            if med is None:
                continue
            claim_dir = "up" if med > 0 else "down"
            signed = sorted((v if claim_dir == "up" else -v) for v in is_fwd)
            claim_x = _q(signed, 0.35)   # ~65% of IS occurrences beat this
            if claim_x is None or claim_x < 0.75:
                continue                  # no meaningful edge magnitude
            claim_x = round(claim_x, 1)

            def _hit(i):
                v = stats[i][0] if claim_dir == "up" else -stats[i][0]
                return v >= claim_x
            is_hits = sum(1 for i in is_idx if _hit(i))
            oos_hits = sum(1 for i in oos_idx if _hit(i))
            is_rate = is_hits / len(is_idx)
            oos_rate = (oos_hits / len(oos_idx)) if oos_idx else None
            base_rate, base_n = _baseline_rate(bars, F, spec["ref"], claim_dir, claim_x)
            all_hits = is_hits + oos_hits
            p_val = _binom_p(all_hits, len(stats), max(0.02, base_rate))

            # ── confidence: sample, consistency, significance, effect ──
            n_score = min(1.0, len(stats) / 25.0) * 30
            if oos_rate is None:
                cons_score = 0.0
            else:
                cons_score = max(0.0, 1.0 - abs(is_rate - oos_rate) / 0.35) * 25
            sig_score = max(0.0, 1.0 - min(1.0, p_val / 0.25)) * 25
            eff = max(0.0, (all_hits / len(stats)) - base_rate) / max(0.05, 1 - base_rate)
            eff_score = min(1.0, eff * 2.0) * 20
            confidence = round(n_score + cons_score + sig_score + eff_score)
            flags = []
            if len(stats) < 12:
                flags.append("small sample")
            if len(oos_idx) < 3:
                flags.append("too few out-of-sample occurrences to validate")
                confidence = min(confidence, 30)
            if p_val > 0.10:
                flags.append("not statistically distinct from baseline — may be random")
                confidence = min(confidence, 45)
            if oos_rate is not None and oos_rate < base_rate:
                flags.append("failed out-of-sample (did worse than baseline)")
                confidence = min(confidence, 25)

            # ── context buckets (best conditions) ──
            def _bucket(label_fn):
                agg = {}
                for i in stats:
                    lbl = label_fn(i)
                    if lbl is None:
                        continue
                    d = agg.setdefault(lbl, [0, 0])
                    d[0] += 1
                    d[1] += 1 if _hit(i) else 0
                return {k: {"n": v[0], "rate": round(v[1] / v[0] * 100)} for k, v in agg.items() if v[0] >= 4}
            ctx = {
                "market": _bucket(lambda i: spy_reg.get(bars[i]["date"][:10])),
                "volatility": _bucket(lambda i: None if (rv_series[i] is None or rv_med is None)
                                      else ("high vol" if rv_series[i] > rv_med else "low vol")),
                "year": _bucket(lambda i: bars[i]["date"][:4]),
            }
            best_ctx = None
            for cat, buckets in ctx.items():
                for lbl, d in buckets.items():
                    if best_ctx is None or d["rate"] > best_ctx["rate"]:
                        best_ctx = {"category": cat, "label": lbl, **d}

            ev_dir = _event_dir(fam, params)
            kind = []
            kind.append("bullish" if claim_dir == "up" else "bearish")
            kind.append("mean-reverting" if claim_dir != ev_dir else "momentum")
            kind.append("multi-day" if F > 1 else "short-term")

            fwd_all = [stats[i][0] for i in stats]
            claim_side = [(v if claim_dir == "up" else -v) for v in fwd_all]
            hit_days = [(stats[i][3] if claim_dir == "up" else stats[i][4]) for i in stats if _hit(i)]
            mfes = [stats[i][1] for i in stats]
            maes = [stats[i][2] for i in stats]

            # ── average path + occurrence paths for the chart ──
            LEAD = 5
            path_len = LEAD + F + 1
            sums = [0.0] * path_len
            cnts = [0] * path_len
            occ_paths = []
            step = max(1, len(stats) // 30)
            keys = sorted(stats.keys())
            for oi, i in enumerate(keys):
                entry = bars[i]["close"] if spec["ref"] == "close" else bars[i]["open"]
                if not entry:
                    continue
                pth = []
                for k in range(-LEAD, F + 1):
                    j = i + k
                    v = None
                    if 0 <= j < n_bars:
                        v = round((bars[j]["close"] - entry) / entry * 100.0, 2)
                        sums[k + LEAD] += v
                        cnts[k + LEAD] += 1
                    pth.append(v)
                if oi % step == 0 and len(occ_paths) < 30:
                    occ_paths.append({"date": bars[i]["date"][:10], "path": pth})
            avg_path = [round(sums[k] / cnts[k], 2) if cnts[k] else None for k in range(path_len)]

            sentence = (
                f"After {_describe_event(fam, params, symbol)}, it has "
                f"{'risen' if claim_dir == 'up' else 'fallen'} at least {claim_x}% within the next "
                f"{F} trading day{'s' if F != 1 else ''} "
                f"{round(all_hits / len(stats) * 100)}% of the time ({all_hits} of {len(stats)} occurrences; "
                f"baseline for any random day: {round(base_rate * 100)}%).")

            patterns.append({
                "id": f"{fam}:{json.dumps(params, sort_keys=True)}:{F}:{claim_dir}",
                "family": fam, "params": params, "window": F,
                "event": _describe_event(fam, params, symbol),
                "claim": {"dir": claim_dir, "min_move_pct": claim_x, "within_days": F},
                "sentence": sentence,
                "kind": kind,
                "n": len(stats), "n_is": len(is_idx), "n_oos": len(oos_idx),
                "hit_rate": round(all_hits / len(stats) * 100, 1),
                "hit_rate_is": round(is_rate * 100, 1),
                "hit_rate_oos": round(oos_rate * 100, 1) if oos_rate is not None else None,
                "baseline_rate": round(base_rate * 100, 1),
                "p_value": round(p_val, 4),
                "confidence": confidence,
                "flags": flags,
                "move": {
                    "avg": round(sum(claim_side) / len(claim_side), 2),
                    "median": round(_median(claim_side), 2),
                    "max": round(max(claim_side), 2),
                    "min": round(min(claim_side), 2),
                },
                "days_to_move_median": _median(hit_days) if hit_days else None,
                "mfe_avg": round(sum(mfes) / len(mfes), 2),
                "mae_avg": round(sum(maes) / len(maes), 2),
                "context": ctx,
                "best_context": best_ctx,
                "chart": {"lead": LEAD, "avg_path": avg_path, "occurrences": occ_paths},
                "backtest_rules": _to_backtest_rules(symbol, fam, params, claim_dir, claim_x, F),
            })

    # Rank: confidence desc; cap near-duplicates (same family+dir keeps best 3).
    patterns.sort(key=lambda p: (-p["confidence"], -p["n"]))
    kept, seen = [], {}
    for p in patterns:
        key = (p["family"], p["params"].get("dir", ""), p["claim"]["dir"])
        seen[key] = seen.get(key, 0) + 1
        if seen[key] <= 3:
            kept.append(p)
    kept = kept[:16]
    return {
        "symbol": symbol,
        "bars": n_bars,
        "from": bars[0]["date"][:10], "to": bars[-1]["date"][:10],
        "split_date": bars[split]["date"][:10],
        "patterns": kept,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "notes": [
            "Claims are fitted on the first 70% of history and validated on the last 30% — both hit rates are shown.",
            "Every hit rate is compared with the baseline chance of the same move after any random day; weak edges are flagged as possibly random.",
            "Daily bars cover ~2 years. Same-day extremes come from daily OHLC, which cannot order intraday highs vs lows — MFE/MAE are approximations.",
            "Sector-relative behavior isn't available (no sector index data); market context uses SPY regime, the stock's own volatility state, and year buckets.",
        ],
    }


def _to_backtest_rules(symbol, fam, params, claim_dir, claim_x, F):
    """Map a discovered pattern onto the Backtest Lab's rule schema."""
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
    return {
        "instrument": "stock",
        "direction": "long" if claim_dir == "up" else "short",
        "universe": {"source": "symbols", "symbols": [symbol]},
        "entry": entry,
        "exit": [{"type": "profit_pct", "value": claim_x},
                 {"type": "stop_pct", "value": round(claim_x * 1.25, 1)},
                 {"type": "time_days", "value": F}],
        "sizing": {"mode": "fixed_dollar", "value": 10000, "max_positions": 5},
        "costs": {"commission": 0.0, "slippage_bps": 5, "spread_model": "auto",
                  "min_dollar_vol_mult": 20},
        "options": None,
        "period_days": 730,
    }


# ── discovery cache (per symbol + last bar date) ────────────────────────────
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
            if len(_CACHE) > 40:
                _CACHE.pop(next(iter(_CACHE)), None)
            _CACHE[symbol] = {"_t": time.time(), "res": res}
    return res


# ── watches: pattern → live signal / alert ──────────────────────────────────

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
    w.append({
        "id": wid, "symbol": symbol.upper(),
        "family": pattern.get("family"), "params": pattern.get("params"),
        "sentence": pattern.get("sentence"), "claim": pattern.get("claim"),
        "confidence": pattern.get("confidence"),
        "created": datetime.now().isoformat(timespec="seconds"),
    })
    _save_watches(w[-40:])
    return {"ok": True, "id": wid}

def remove_watch(wid: str) -> dict:
    w = [x for x in _load_watches() if x["id"] != wid]
    _save_watches(w)
    return {"ok": True}

def check_watches() -> dict:
    """Evaluate every watched pattern on the LATEST daily bar. Called when the
    Patterns tab polls, and by the background pinger (which pushes on new
    triggers)."""
    c = _schwab_getter()
    watches = _load_watches()
    out = []
    for wt in watches:
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
    """Background thread: every 30 min during US market hours, re-check the
    watches and push (once per watch per day) when one triggers."""
    global _PINGER_STARTED
    if _PINGER_STARTED or _notify_fn is None:
        return
    _PINGER_STARTED = True

    def _loop():
        while True:
            try:
                now = datetime.now()
                if now.weekday() < 5 and 9 <= now.hour <= 16 and _load_watches():
                    res = check_watches()
                    for wt in res["watches"]:
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
