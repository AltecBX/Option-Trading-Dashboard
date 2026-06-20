"""swings.py — swing pattern recognition + real-time decision core (v2).

For one ticker: detect major swing lows and swing highs from daily bars
via a percentage zig-zag, measure every low→high up-move AND every
high→low down-move, derive the stock's typical rhythm (days + % move) in
each direction, and — for the move that's happening RIGHT NOW — answer the
trader's real questions:

  • Where am I in this move vs how this stock usually runs?
  • Is the move early / developing / mature / extended / exhausted?
  • How much more upside (or downside) is typical from here?
  • Am I about to sell a long (or cover a short) too early?
  • Is this move stronger or weaker than similar past moves?
  • A concrete trade plan: entry zone, invalidation, targets, holding
    window, exit warnings, and the reason to stay.

Free data only: yfinance daily OHLCV. Stdlib + yfinance / numpy.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    import yfinance as yf
    import numpy as np
    _OK = True
except Exception:
    _OK = False


# ───────────────────────── zig-zag + date helpers ──────────────────────────

def _zigzag(highs, lows, pct):
    """Percentage zig-zag → alternating (idx, price, kind) pivots using
    intraday highs for tops and lows for bottoms. The final pivot is the
    in-progress extreme (may be unconfirmed)."""
    n = len(highs)
    if n < 3:
        return []
    pivots = [(0, lows[0], "low")]
    direction = 1            # 1 = in up-move (seeking high), -1 = down-move
    ext_i, ext_p = 0, highs[0]
    for i in range(1, n):
        if direction == 1:
            if highs[i] > ext_p:
                ext_p, ext_i = highs[i], i
            elif lows[i] <= ext_p * (1 - pct):
                pivots.append((ext_i, ext_p, "high"))
                direction, ext_p, ext_i = -1, lows[i], i
        else:
            if lows[i] < ext_p:
                ext_p, ext_i = lows[i], i
            elif highs[i] >= ext_p * (1 + pct):
                pivots.append((ext_i, ext_p, "low"))
                direction, ext_p, ext_i = 1, highs[i], i
    pivots.append((ext_i, ext_p, "high" if direction == 1 else "low"))
    return pivots


def _busday_offset(date_str: str, n: int) -> str:
    try:
        d = np.datetime64(date_str, "D")
        return str(np.busday_offset(d, n, roll="forward"))
    except Exception:
        return date_str


# ──────────────────── benchmarks + earnings (phase 2) ──────────────────────

def _fetch_bench(period: str = "1y") -> dict:
    """Date→close maps for SPY and QQQ in one batched download (free)."""
    out: dict[str, dict] = {}
    try:
        df = yf.download("SPY QQQ", period=period, interval="1d",
                         progress=False, group_by="ticker", threads=False)
        for sym in ("SPY", "QQQ"):
            try:
                closes = df[sym]["Close"].dropna()
                out[sym] = {d.strftime("%Y-%m-%d"): float(v)
                            for d, v in closes.items()}
            except Exception:
                pass
    except Exception:
        pass
    return out


def _fetch_earnings(symbol: str) -> set:
    """Past earnings dates as numpy day values (best-effort; degrades to ∅)."""
    out: set = set()
    try:
        t = yf.Ticker(symbol)
        ed = None
        try:
            ed = t.get_earnings_dates(limit=16)
        except Exception:
            ed = getattr(t, "earnings_dates", None)
        if ed is not None and len(ed):
            for ts in ed.index:
                try:
                    out.add(np.datetime64(ts.to_pydatetime().date(), "D"))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def _after_earnings(date_str: str, earnings: set, max_days: int = 5) -> bool:
    """True when this date launched within max_days after an earnings report."""
    if not earnings:
        return False
    try:
        d = np.datetime64(date_str, "D")
    except Exception:
        return False
    for e in earnings:
        diff = (d - e) / np.timedelta64(1, "D")
        if 0 <= diff <= max_days:
            return True
    return False


def _bench_return(bench_map: dict, start_date: str, last_date: str):
    """Benchmark % return between start and last (nearest prior trading day)."""
    if not bench_map:
        return None
    def lookup(target):
        if target in bench_map:
            return bench_map[target]
        prior = [d for d in bench_map if d <= target]
        return bench_map[max(prior)] if prior else None
    a = lookup(start_date); b = lookup(last_date)
    if a and b and a > 0:
        return (b - a) / a * 100.0
    return None


def _rel_strength(direction, from_date, last_date, stock_move, bench):
    """Stock move vs SPY/QQQ over the same window. 'leading' = stronger than
    the market in the move's own direction."""
    out: dict[str, Any] = {}
    for sym in ("SPY", "QQQ"):
        br = _bench_return(bench.get(sym, {}), from_date, last_date)
        if br is None:
            continue
        out[sym.lower() + "_ret"] = round(br, 1)
        out["vs_" + sym.lower()] = round(stock_move - br, 1)
    if "vs_spy" not in out:
        return None
    vs = out["vs_spy"]
    if direction == "up":
        out["leading"] = vs >= 2.0
        out["lagging"] = vs <= -2.0
    else:
        out["leading"] = vs <= -2.0       # falling more than the market
        out["lagging"] = vs >= 2.0
    sign = "+" if vs >= 0 else ""
    rel = "leading" if out["leading"] else "lagging" if out["lagging"] else "tracking"
    out["note"] = f"{rel} SPY ({sign}{vs}% vs market over the move)"
    return out


def _enrich_swings(swings, kind, vols, closes, earnings):
    """Per-swing tags for the filter set: volume ratio, broke resistance /
    support, failed breakout, after-earnings."""
    prev_ext = None
    for s in swings:
        lo_i = s.get("_lo_i"); hi_i = s.get("_hi_i")
        start_i = lo_i if kind == "up" else hi_i
        end_i = hi_i if kind == "up" else lo_i
        # Volume ratio: avg during the swing vs the ~50 bars before it.
        ratio = None
        try:
            seg = vols[start_i:end_i + 1]
            base = vols[max(0, start_i - 50):start_i] or vols[:start_i] or seg
            sv = sum(seg) / len(seg) if seg else 0.0
            bv = sum(base) / len(base) if base else 0.0
            ratio = round(sv / bv, 2) if bv > 0 else None
        except Exception:
            ratio = None
        s["vol_ratio"] = ratio
        s["above_avg_vol"] = bool(ratio is not None and ratio >= 1.2)
        # Broke prior resistance (up) / support (down), and whether it held.
        level_broken = None
        if kind == "up":
            broke = prev_ext is not None and s["high_price"] > prev_ext
            level_broken = prev_ext if broke else None
            prev_ext = max(prev_ext, s["high_price"]) if prev_ext is not None else s["high_price"]
        else:
            broke = prev_ext is not None and s["low_price"] < prev_ext
            level_broken = prev_ext if broke else None
            prev_ext = min(prev_ext, s["low_price"]) if prev_ext is not None else s["low_price"]
        s["broke_resistance"] = bool(broke)
        # Failed breakout: broke the level but closed back through it within ~10 bars.
        failed = False
        if broke and level_broken is not None and hi_i is not None and lo_i is not None:
            try:
                after = closes[(end_i + 1):(end_i + 11)]
                if kind == "up":
                    failed = any(c < level_broken for c in after)
                else:
                    failed = any(c > level_broken for c in after)
            except Exception:
                failed = False
        s["failed_breakout"] = bool(failed)
        start_date = s["low_date"] if kind == "up" else s["high_date"]
        s["after_earnings"] = _after_earnings(start_date, earnings)


# ───────────────────────────── indicators ──────────────────────────────────

def _sma(closes, n):
    if len(closes) < n:
        return None
    return float(np.mean(closes[-n:]))


def _rsi(closes, n=14):
    c = np.asarray(closes, dtype=float)
    if len(c) < n + 1:
        return None
    d = np.diff(c)
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    ru = float(up[:n].mean())
    rd = float(dn[:n].mean())
    for i in range(n, len(d)):
        ru = (ru * (n - 1) + up[i]) / n
        rd = (rd * (n - 1) + dn[i]) / n
    if rd == 0:
        return 100.0
    rs = ru / rd
    return 100.0 - 100.0 / (1.0 + rs)


def _indicators(opens, highs, lows, closes, vols) -> dict:
    last = closes[-1]
    ma20 = _sma(closes, 20)
    ma50 = _sma(closes, 50)
    ma200 = _sma(closes, 200)
    rsi = _rsi(closes, 14)
    avg_vol = float(np.mean(vols[-20:])) if len(vols) >= 5 else None
    today_vol = float(vols[-1]) if vols else None
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    body = c - o
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    close_loc = ((c - l) / rng) if rng > 0 else 0.5   # 0=at low, 1=at high
    prev_close = closes[-2] if len(closes) >= 2 else o
    gap_pct = ((o - prev_close) / prev_close * 100.0) if prev_close else 0.0
    return {
        "rsi14": round(rsi, 1) if rsi is not None else None,
        "ma20": round(ma20, 2) if ma20 is not None else None,
        "ma50": round(ma50, 2) if ma50 is not None else None,
        "ma200": round(ma200, 2) if ma200 is not None else None,
        "above_ma20": (ma20 is not None and last > ma20),
        "above_ma50": (ma50 is not None and last > ma50),
        "above_ma200": (ma200 is not None and last > ma200),
        "dist_ma20_pct": round((last - ma20) / ma20 * 100.0, 1) if ma20 else None,
        "avg_vol20": avg_vol,
        "today_vol": today_vol,
        "rel_vol": round(today_vol / avg_vol, 2) if (avg_vol and today_vol) else None,
        "close_loc": round(close_loc, 2),
        "gap_pct": round(gap_pct, 2),
        "upper_wick": round(upper_wick, 2),
        "lower_wick": round(lower_wick, 2),
        "body": round(body, 2),
        "candle": "up" if c >= o else "down",
    }


# ─────────────────────────── swings + rhythm ───────────────────────────────

def _build_swings(pivots, dates, kind, min_move_pct):
    """kind='up' → low→high up-moves; kind='down' → high→low down-moves."""
    out = []
    for k in range(len(pivots) - 1):
        a, b = pivots[k], pivots[k + 1]
        if kind == "up" and a[2] == "low" and b[2] == "high":
            lo_i, lo_p, _ = a
            hi_i, hi_p, _ = b
            days = hi_i - lo_i
            if days <= 0:
                continue
            pctc = (hi_p - lo_p) / lo_p * 100.0
            if pctc < min_move_pct:
                continue
            out.append({
                "low_date": dates[lo_i], "low_price": round(lo_p, 2),
                "high_date": dates[hi_i], "high_price": round(hi_p, 2),
                "trading_days": days,
                "dollar_change": round(hi_p - lo_p, 2),
                "pct_change": round(pctc, 2),
                "avg_daily_pct": round(pctc / days, 2),
                "_lo_i": lo_i, "_hi_i": hi_i,
            })
        elif kind == "down" and a[2] == "high" and b[2] == "low":
            hi_i, hi_p, _ = a
            lo_i, lo_p, _ = b
            days = lo_i - hi_i
            if days <= 0:
                continue
            pctc = (lo_p - hi_p) / hi_p * 100.0       # negative
            if abs(pctc) < min_move_pct:
                continue
            out.append({
                "high_date": dates[hi_i], "high_price": round(hi_p, 2),
                "low_date": dates[lo_i], "low_price": round(lo_p, 2),
                "trading_days": days,
                "dollar_change": round(lo_p - hi_p, 2),
                "pct_change": round(pctc, 2),
                "avg_daily_pct": round(pctc / days, 2),
                "_hi_i": hi_i, "_lo_i": lo_i,
            })
    return out


def _rhythm(swings):
    """Percentile band of move size (abs %) and duration across swings."""
    if len(swings) < 2:
        return None
    d = np.array([s["trading_days"] for s in swings], dtype=float)
    p = np.array([abs(s["pct_change"]) for s in swings], dtype=float)
    r = {
        "count": len(swings),
        "days_min": int(d.min()), "days_max": int(d.max()),
        "days_p25": int(round(np.percentile(d, 25))),
        "days_p75": int(round(np.percentile(d, 75))),
        "days_median": round(float(np.median(d)), 1),
        "pct_min": round(float(p.min()), 1), "pct_max": round(float(p.max()), 1),
        "pct_p25": round(float(np.percentile(p, 25)), 1),
        "pct_p75": round(float(np.percentile(p, 75)), 1),
        "pct_median": round(float(np.median(p)), 1),
        "pct_avg": round(float(p.mean()), 1),
    }
    for s in swings:
        s["matches_rhythm"] = (
            r["days_p25"] <= s["trading_days"] <= r["days_p75"] and
            r["pct_p25"] <= abs(s["pct_change"]) <= r["pct_p75"]
        )
    return r


# ───────────────────────── active-move decision core ───────────────────────

def _confidence(matched):
    if matched >= 5:
        return "high"
    if matched >= 2:
        return "medium"
    return "low"


def _maturity(cur, r):
    """Where the current move sits in the historical size distribution."""
    if cur < r["pct_p25"]:
        return "early"
    if cur < r["pct_median"]:
        return "developing"
    if cur < r["pct_p75"]:
        return "mature"
    if cur < r["pct_max"]:
        return "extended"
    return "exhausted"


def _exhaustion(direction, cur, days, r, ind):
    score = 0.0
    f = []
    if cur >= r["pct_median"]:
        score += 18; f.append(f"move ≥ median ({cur:.1f}% vs {r['pct_median']:.1f}%)")
    if cur >= r["pct_p75"]:
        score += 16; f.append("in top quartile of past moves")
    if cur >= r["pct_max"]:
        score += 14; f.append("exceeds the largest prior move")
    if days >= r["days_median"]:
        score += 8; f.append(f"duration ≥ median ({days}d vs {r['days_median']:.0f}d)")
    if days >= r["days_p75"]:
        score += 8; f.append("duration in top quartile")
    rsi = ind.get("rsi14")
    if rsi is not None:
        if direction == "up" and rsi >= 70:
            score += 14; f.append(f"RSI overbought ({rsi:.0f})")
        elif direction == "down" and rsi <= 30:
            score += 14; f.append(f"RSI oversold ({rsi:.0f})")
    dm = ind.get("dist_ma20_pct")
    if dm is not None:
        if direction == "up" and dm >= 15:
            score += 10; f.append(f"stretched {dm:.0f}% above 20-DMA")
        elif direction == "down" and dm <= -15:
            score += 10; f.append(f"stretched {abs(dm):.0f}% below 20-DMA")
    body = abs(ind.get("body") or 0)
    if direction == "up" and ind.get("upper_wick", 0) > max(body, 0.01) * 1.3:
        score += 10; f.append("upper-wick rejection today")
    if direction == "down" and ind.get("lower_wick", 0) > max(body, 0.01) * 1.3:
        score += 10; f.append("lower-wick rejection today")
    rv = ind.get("rel_vol")
    if rv is not None and rv < 0.7:
        score += 5; f.append(f"volume fading ({rv:.1f}x)")
    return round(max(0.0, min(100.0, score)), 0), f


def _continuation(direction, cur, days, r, ind):
    score = 0.0
    f = []
    cl = ind.get("close_loc", 0.5)
    if direction == "up" and cl >= 0.66:
        score += 15; f.append("strong close near the day's high")
    if direction == "down" and cl <= 0.34:
        score += 15; f.append("weak close near the day's low")
    if direction == "up":
        if ind.get("above_ma20"): score += 10; f.append("above 20-DMA")
        if ind.get("above_ma50"): score += 8; f.append("above 50-DMA")
        if ind.get("above_ma200"): score += 5; f.append("above 200-DMA")
    else:
        if not ind.get("above_ma20"): score += 10; f.append("below 20-DMA")
        if not ind.get("above_ma50"): score += 8; f.append("below 50-DMA")
        if not ind.get("above_ma200"): score += 5; f.append("below 200-DMA")
    rsi = ind.get("rsi14")
    if rsi is not None:
        if direction == "up" and 50 <= rsi <= 70:
            score += 12; f.append(f"RSI trending ({rsi:.0f})")
        elif direction == "down" and 30 <= rsi <= 50:
            score += 12; f.append(f"RSI trending down ({rsi:.0f})")
    if cur < r["pct_p75"]:
        score += 15; f.append("below the typical exhaustion zone — room left")
    rv = ind.get("rel_vol")
    if rv is not None and rv >= 1.0:
        score += 10; f.append(f"above-average volume ({rv:.1f}x)")
    if days < r["days_median"]:
        score += 8; f.append("early in the typical time window")
    return round(max(0.0, min(100.0, score)), 0), f


def _trend_state(direction, maturity, exh, cont):
    if direction == "up":
        if exh >= 65 or maturity == "exhausted":
            return "Exhaustion / reversal risk"
        if maturity == "extended":
            return "Extended"
        if maturity == "early":
            return "Breaking out" if cont >= 50 else "Accumulating"
        return "Continuation" if cont >= 45 else "Stalling"
    else:
        if exh >= 65 or maturity == "exhausted":
            return "Capitulation / reversal risk"
        if maturity == "extended":
            return "Extended decline"
        if maturity == "early":
            return "Breaking down" if cont >= 50 else "Rolling over"
        return "Continuation (down)" if cont >= 45 else "Basing"


def _similar_move(swings, cur, days, exclude_dates):
    """Closest completed swing by normalized (size, duration) distance."""
    cands = [s for s in swings
             if (s.get("low_date"), s.get("high_date")) != exclude_dates]
    if not cands:
        return None
    med_pct = float(np.median([abs(s["pct_change"]) for s in cands])) or 1.0
    med_days = float(np.median([s["trading_days"] for s in cands])) or 1.0

    def dist(s):
        return (abs(abs(s["pct_change"]) - cur) / med_pct
                + abs(s["trading_days"] - days) / med_days)

    best = min(cands, key=dist)
    pace_now = cur / days if days else 0.0
    pace_then = abs(best["pct_change"]) / best["trading_days"] if best["trading_days"] else 0.0
    hotter = pace_now > pace_then * 1.1
    cooler = pace_now < pace_then * 0.9
    pace = "running hotter than" if hotter else "running cooler than" if cooler else "tracking close to"
    return {
        "swing": {k: v for k, v in best.items() if not k.startswith("_")},
        "total_pct": round(abs(best["pct_change"]), 1),
        "total_days": best["trading_days"],
        "pace_now": round(pace_now, 2),
        "pace_comp": round(pace_then, 2),
        "note": (f"Closest past move: {abs(best['pct_change']):.1f}% over "
                 f"{best['trading_days']}d. You're {pace} that pace "
                 f"({pace_now:.2f}%/day vs {pace_then:.2f}%/day)."),
    }


def _us_date(s: str) -> str:
    """ISO YYYY-MM-DD → M-D-YYYY for human-facing notes."""
    try:
        y, m, d = s.split("-")
        return f"{int(m)}-{int(d)}-{y}"
    except Exception:
        return s


def _key_levels(pivots, dates, cur_price):
    """Horizontal support (prior swing lows) and resistance (prior swing
    highs) from confirmed pivots, clustered so near-equal touches merge.
    The active running extreme (last pivot) is excluded."""
    lows, highs = [], []
    for i, (idx, p, k) in enumerate(pivots):
        if i == len(pivots) - 1:        # active, unconfirmed extreme
            continue
        (lows if k == "low" else highs).append((dates[idx], float(p)))

    def cluster(levels):
        levels = sorted(levels, key=lambda x: x[1])
        out = []
        for d, p in levels:
            if out and abs(p - out[-1][1]) / out[-1][1] < 0.012:
                pd, pp = out[-1]
                out[-1] = (max(pd, d), (pp + p) / 2.0)   # merge, keep recent date
            else:
                out.append((d, p))
        return out

    supports = cluster(lows)
    resistances = cluster(highs)
    below = [(d, p) for d, p in supports if p < cur_price * 0.999]
    above = [(d, p) for d, p in resistances if p > cur_price * 1.001]
    nearest_support = max(below, key=lambda x: x[1]) if below else None
    nearest_resist = min(above, key=lambda x: x[1]) if above else None
    return supports, resistances, nearest_support, nearest_resist


def _flow_read(direction, flow):
    """Distil a UW flow score into a direction-aligned read for the move.
    Returns (delta_continuation, delta_exhaustion, factor, summary) or None."""
    if not flow or not flow.get("data_available"):
        return None
    bull = float(flow.get("bullish") or 0)
    bear = float(flow.get("bearish") or 0)
    q = float(flow.get("quality") or 0)
    net = bull - bear                          # >0 bullish tape
    aligned = net if direction == "up" else -net   # >0 = flow confirms the move
    label = "bullish" if net > 8 else "bearish" if net < -8 else "mixed"

    # Flow-vs-price agreement breakdown (premiums, sweep pressure, hedge).
    st = flow.get("stats") or {}
    bull_prem = float(st.get("total_call_premium") or 0)
    bear_prem = float(st.get("total_put_premium") or 0)
    call_sweeps = int(st.get("call_sweeps") or 0)
    put_sweeps = int(st.get("put_sweeps") or 0)

    def _press(n):
        return "strong" if n >= 5 else "moderate" if n >= 2 else "weak"

    tot_prem = bull_prem + bear_prem
    put_share = (bear_prem / tot_prem) if tot_prem > 0 else 0.0
    hedge = "heavy" if put_share >= 0.55 else "light" if put_share <= 0.35 else "moderate"
    agrees = "neutral" if label == "mixed" else (
        "agrees" if ((direction == "up" and label == "bullish") or
                     (direction == "down" and label == "bearish")) else "disagrees")

    summary = {
        "bullish": round(bull), "bearish": round(bear), "quality": round(q),
        "net": round(net), "label": label,
        "verdict": flow.get("verdict"),
        "bull_premium": round(bull_prem), "bear_premium": round(bear_prem),
        "call_sweep_pressure": _press(call_sweeps),
        "put_hedge_pressure": hedge,
        "call_sweeps": call_sweeps, "put_sweeps": put_sweeps,
        "agrees_with_price": agrees,
        "data_available": True,
    }
    # Quality gates how much the tape is allowed to move the scores.
    weight = round(min(12.0, abs(aligned) * 0.18 * (q / 100.0)), 1)
    if aligned >= 10 and weight >= 1:
        return (weight, 0.0,
                f"options flow confirms ({label} tape, quality {round(q)})", summary)
    if aligned <= -10 and weight >= 1:
        return (0.0, weight,
                f"options flow fading the move ({label} tape, quality {round(q)})", summary)
    return (0.0, 0.0, f"options flow {label} but not decisive (quality {round(q)})", summary)


def _confidence_block(targets, days_active, cur_abs, r, ind, direction):
    """Confidence in the projection, with the reasons that drove it."""
    matched = targets[1]["matched"]            # how many past moves reached median
    in_dur = r["days_p25"] <= days_active <= r["days_p75"]
    in_size = r["pct_p25"] <= cur_abs <= r["pct_p75"] or cur_abs < r["pct_p25"]
    if direction == "up":
        ma_ok = bool(ind.get("above_ma20") and ind.get("above_ma50"))
        ma_txt = ("price is above the 20 & 50-DMA" if ma_ok
                  else "price is below a key moving average")
    else:
        ma_ok = bool((not ind.get("above_ma20")) and (not ind.get("above_ma50")))
        ma_txt = ("price is below the 20 & 50-DMA" if ma_ok
                  else "price is back above a key moving average")
    reasons = []
    if matched >= 2:
        reasons.append(f"{matched} similar past moves matched")
    else:
        reasons.append(f"only {matched} similar move{'' if matched == 1 else 's'} matched")
    reasons.append("current rhythm is inside the normal duration" if in_dur
                   else "current duration is outside the normal range")
    if not in_size:
        reasons.append("the move is already outside the normal size range")
    reasons.append(ma_txt)
    score = int(matched >= 5) + int(in_dur) + int(in_size) + int(ma_ok)
    if matched < 2 or (not in_size and not in_dur):
        level = "low"
    elif score >= 3:
        level = "high"
    else:
        level = "medium"
    return {"level": level, "matched": matched, "reasons": reasons}


def _decision(direction, maturity, exh, cont, hold_signal, broke_res, next_level):
    """Single top-of-card action from a fixed vocabulary, with drivers."""
    near = next_level is not None and abs(next_level.get("pct_away", 99)) <= 4
    near_px = next_level.get("price") if next_level else None
    d = []
    if direction == "up":
        if maturity == "exhausted" or exh >= 70:
            act = "Take partial"; d.append(f"move looks exhausted (exhaustion {exh:.0f})")
        elif maturity == "extended":
            act = "Trail only"; d.append("move is extended vs history")
        elif maturity == "mature":
            if hold_signal:
                act = "Hold"; d.append("momentum still favors continuation")
            elif exh >= 55:
                act = "Take partial"; d.append(f"exhaustion building (exhaustion {exh:.0f})")
            else:
                act = "Trail only"; d.append("mid-move — protect the gain")
        else:  # early / developing
            if broke_res and cont >= 58:
                act = "Add on breakout"; d.append("broke resistance on strong momentum")
            elif near and cont >= 50:
                act = "Add on pullback"; d.append(f"near support ${near_px:.2f} with momentum intact")
            elif cont >= 50:
                act = "Hold"; d.append("trend intact — let it develop")
            else:
                act = "No new trade"; d.append("momentum not yet confirmed")
    else:
        if maturity == "exhausted" or exh >= 70:
            act = "Cover fully"; d.append(f"down-move looks exhausted (exhaustion {exh:.0f})")
        elif maturity == "extended":
            act = "Cover partial"; d.append("decline is extended vs history")
        elif maturity == "mature":
            if exh >= 55:
                act = "Cover partial"; d.append(f"exhaustion building (exhaustion {exh:.0f})")
            elif hold_signal:
                act = "Hold"; d.append("downside momentum still favors continuation")
            else:
                act = "Trail only"; d.append("mid-move — protect the gain")
        else:
            if broke_res and cont >= 58:
                act = "Short trigger active"; d.append("broke support on strong momentum")
            elif cont >= 45:
                act = "Short watch"; d.append("rolling over but not yet confirmed")
            else:
                act = "No new trade"; d.append("downside not yet confirmed")
    if cont >= 60:
        d.append(f"continuation {cont:.0f}/100")
    return {"action": act, "drivers": d[:3]}


def _attach_details(swings, kind, pivots, dates, r):
    """Per-swing 'what happened before/after' detail for row expansion."""
    if not r:
        return
    posmap: dict[int, int] = {}
    for pos, (idx, _p, _k) in enumerate(pivots):
        posmap.setdefault(idx, pos)
    med = r["pct_median"]
    for s in swings:
        lo_i = s.get("_lo_i"); hi_i = s.get("_hi_i")
        if lo_i is None or hi_i is None:
            continue
        if kind == "up":
            start_i, start_p, end_i, end_p = lo_i, s["low_price"], hi_i, s["high_price"]
        else:
            start_i, start_p, end_i, end_p = hi_i, s["high_price"], lo_i, s["low_price"]
        detail: dict[str, Any] = {}
        # Before — the leg into the swing's start.
        pos = posmap.get(start_i)
        if pos is not None and pos - 1 >= 0:
            pi, pp, _ = pivots[pos - 1]
            bpct = (start_p - pp) / pp * 100.0 if pp else 0.0
            bdays = start_i - pi
            detail["before"] = (f"Fell {abs(bpct):.0f}% over {bdays}d into the swing low"
                                if kind == "up" else
                                f"Rallied {abs(bpct):.0f}% over {bdays}d into the swing high")
        # Median target and how far past it the move ran.
        if kind == "up":
            mt = start_p * (1 + med / 100.0); reached = end_p >= mt
            beyond = (end_p - mt) / mt * 100.0 if reached else None
        else:
            mt = start_p * (1 - med / 100.0); reached = end_p <= mt
            beyond = (mt - end_p) / mt * 100.0 if reached else None
        detail["median_target"] = round(mt, 2)
        detail["beyond_median"] = (
            f"Hit the median target (${mt:.2f}), then ran {beyond:.0f}% further to the extreme"
            if reached else f"Never reached the median target (${mt:.2f})")
        # After — the reversal off the extreme.
        epos = posmap.get(end_i)
        if epos is not None and epos + 1 < len(pivots):
            ni, np_, _ = pivots[epos + 1]
            rpct = (np_ - end_p) / end_p * 100.0 if end_p else 0.0
            rdays = ni - end_i
            detail["after"] = (f"Pulled back {abs(rpct):.0f}% over {rdays}d after the high"
                               if kind == "up" else
                               f"Bounced {abs(rpct):.0f}% over {rdays}d after the low")
        else:
            detail["after"] = "Still in progress after the extreme"
        # Sell-at-target vs hold.
        hold_gain = abs(s["pct_change"])
        if reached:
            edge = hold_gain - med
            if edge >= 5:
                detail["hold_vs_target"] = f"Holding beat selling at target by +{edge:.0f} pts"
            elif edge <= -2:
                detail["hold_vs_target"] = f"Selling at target was better by {abs(edge):.0f} pts"
            else:
                detail["hold_vs_target"] = "Holding vs selling at target was roughly a wash"
        else:
            detail["hold_vs_target"] = "Selling at the median target was the better exit"
        s["detail"] = detail


def _levels_block(pivots, dates, cur_price, direction, med_price=None):
    """Structural support/resistance read. Works with or without a rhythm-
    based median target (med_price=None when we can't project one)."""
    supports, resistances, ns, nr = _key_levels(pivots, dates, cur_price)
    next_level = None
    level_note = None
    if direction == "down" and ns:
        d, p = ns
        away = (cur_price - p) / cur_price * 100.0
        if med_price is None:
            tail = "a hold here can spark a double-bottom bounce; a decisive break on volume opens the next leg down"
        elif p > med_price:
            tail = (f"a decisive break of it on volume opens the median target "
                    f"(${med_price:.2f}); a hold here risks a double-bottom bounce")
        else:
            tail = (f"the median target (${med_price:.2f}) sits above it, so that "
                    f"projection is already within prior support")
        next_level = {"kind": "support", "price": round(p, 2), "date": d,
                      "pct_away": round(-away, 1)}
        level_note = (f"Next support ${p:.2f} ({_us_date(d)}) ~{away:.1f}% below — "
                      f"price usually reacts there first; {tail}.")
    elif direction == "up" and nr:
        d, p = nr
        away = (p - cur_price) / cur_price * 100.0
        if med_price is None:
            tail = "a stall here can spark a double-top fade; a decisive break on volume opens the next leg up"
        elif p < med_price:
            tail = (f"a decisive break of it on volume opens the median target "
                    f"(${med_price:.2f}); a stall here risks a double-top fade")
        else:
            tail = (f"the median target (${med_price:.2f}) sits below it, so that "
                    f"projection is already within prior resistance")
        next_level = {"kind": "resistance", "price": round(p, 2), "date": d,
                      "pct_away": round(away, 1)}
        level_note = (f"Next resistance ${p:.2f} ({_us_date(d)}) ~{away:.1f}% above — "
                      f"price usually reacts there first; {tail}.")
    return {
        "supports": [{"date": d, "price": round(p, 2),
                      "pct_away": round((p - cur_price) / cur_price * 100.0, 1)}
                     for d, p in reversed(supports) if p <= cur_price * 1.01][:4],
        "resistances": [{"date": d, "price": round(p, 2),
                         "pct_away": round((p - cur_price) / cur_price * 100.0, 1)}
                        for d, p in resistances if p >= cur_price * 0.99][:4],
        "next": next_level,
        "note": level_note,
    }


def _analyze_active(pivots, dates, opens, highs, lows, closes, vols,
                    up_swings, down_swings, up_rhythm, down_rhythm, ind,
                    bench=None, earnings=None, flow=None):
    """Build the real-time decision block for the move in progress."""
    if len(pivots) < 2:
        return None
    last = pivots[-1]
    cur_price = closes[-1]
    n = len(dates) - 1

    if last[2] == "high":            # rising → up-move in progress from prior low
        direction = "up"
        start = None
        for p in reversed(pivots[:-1]):
            if p[2] == "low":
                start = p; break
        if start is None:
            return None
        r = up_rhythm
        swings = up_swings
        from_i, from_p = start[0], start[1]
        ext_i, ext_p = last[0], last[1]
        cur_move = (cur_price - from_p) / from_p * 100.0
        from_label, from_date = "swing low", dates[from_i]
        side = "long"
    else:                            # falling → down-move in progress from prior high
        direction = "down"
        start = None
        for p in reversed(pivots[:-1]):
            if p[2] == "high":
                start = p; break
        if start is None:
            return None
        r = down_rhythm
        swings = down_swings
        from_i, from_p = start[0], start[1]
        ext_i, ext_p = last[0], last[1]
        cur_move = (cur_price - from_p) / from_p * 100.0   # negative
        from_label, from_date = "swing high", dates[from_i]
        side = "short"

    days_active = n - from_i
    cur_abs = abs(cur_move)

    block: dict[str, Any] = {
        "direction": direction,
        "side": side,
        "from_label": from_label,
        "from_date": from_date,
        "from_price": round(from_p, 2),
        "extreme_date": dates[ext_i],
        "extreme_price": round(ext_p, 2),
        "current_price": round(cur_price, 2),
        "current_move_pct": round(cur_move, 2),
        "days_active": days_active,
    }

    if not r:
        # No rhythm to project targets from — but the live move, indicators,
        # relative strength, flow, and structural levels need no rhythm, so
        # still show them rather than collapsing to a one-liner.
        block["status"] = "no_rhythm"
        block["note"] = ("Not enough completed "
                         + ("up-moves" if direction == "up" else "down-moves")
                         + f" ≥ the move threshold yet to project targets for this "
                         + ("up" if direction == "up" else "down")
                         + "-move. Showing the live move, levels, and flow.")
        block["relative_strength"] = _rel_strength(direction, from_date, dates[-1],
                                                    cur_move, bench or {})
        fr0 = _flow_read(direction, flow)
        block["flow"] = fr0[3] if fr0 else None
        block["after_earnings"] = _after_earnings(from_date, earnings or set())
        block["key_levels"] = _levels_block(pivots, dates, cur_price, direction, None)
        return block

    maturity = _maturity(cur_abs, r)
    exh, exh_f = _exhaustion(direction, cur_abs, days_active, r, ind)
    cont, cont_f = _continuation(direction, cur_abs, days_active, r, ind)

    # Relative strength vs SPY/QQQ over the move's window, folded into scores.
    rs = _rel_strength(direction, from_date, dates[-1], cur_move, bench or {})
    if rs:
        if rs.get("leading"):
            cont = round(min(100.0, cont + 8)); cont_f.append(rs["note"])
        elif rs.get("lagging"):
            exh = round(min(100.0, exh + 6)); exh_f.append(rs["note"])

    # UW options flow — does the tape confirm or fade this move?
    flow_summary = None
    fr = _flow_read(direction, flow)
    if fr:
        d_cont, d_exh, factor, flow_summary = fr
        if d_cont:
            cont = round(min(100.0, cont + d_cont)); cont_f.append(factor)
        elif d_exh:
            exh = round(min(100.0, exh + d_exh)); exh_f.append(factor)

    # Catalyst / structure tags for the live move.
    after_earn = _after_earnings(from_date, earnings or set())
    if direction == "up":
        prior_highs = [s["high_price"] for s in swings if s.get("high_date") and s["high_date"] < dates[ext_i]]
        broke_res = bool(prior_highs and ext_p > max(prior_highs))
    else:
        prior_lows = [s["low_price"] for s in swings if s.get("low_date") and s["low_date"] < dates[ext_i]]
        broke_res = bool(prior_lows and ext_p < min(prior_lows))
    block["after_earnings"] = after_earn
    block["broke_resistance"] = broke_res

    state = _trend_state(direction, maturity, exh, cont)

    # Target ladder, projected from the from-price using the historical band.
    def target(pct_move, label, eta_days):
        if direction == "up":
            price = from_p * (1 + pct_move / 100.0)
        else:
            price = from_p * (1 - pct_move / 100.0)
        upside = (price - cur_price) / cur_price * 100.0
        matched = sum(1 for s in swings if abs(s["pct_change"]) >= pct_move - 0.001)
        return {
            "label": label,
            "pct_move": round(pct_move, 1),
            "price": round(price, 2),
            "from_here_pct": round(upside, 1),
            "reached": upside <= 0 if direction == "up" else upside >= 0,
            "eta_date": _busday_offset(from_date, max(1, int(eta_days))),
            "matched": matched,
            "confidence": _confidence(matched),
        }

    targets = [
        target(r["pct_p25"], "conservative", r["days_p25"]),
        target(r["pct_median"], "median", r["days_median"]),
        target(r["pct_p75"], "aggressive", r["days_p75"]),
        target(r["pct_max"], "extreme", r["days_max"]),
    ]
    median_t = targets[1]
    p75_t = targets[2]
    extreme_t = targets[3]

    # ── Structural levels ───────────────────────────────────────────────
    # The statistical ladder above is blind to horizontal support/resistance.
    # Price negotiates the nearest prior pivot FIRST, so surface it: the next
    # support beneath a falling stock (or resistance above a rising one) is the
    # real next decision point, and the median target may sit beyond it.
    block["key_levels"] = _levels_block(pivots, dates, cur_price, direction,
                                        median_t["price"])

    # "Do not sell / cover too early" guard: structure still favors continuation
    # and the move hasn't reached its usual exhaustion zone.
    has_room = cur_abs < r["pct_p75"]
    early_window = days_active < r["days_p75"]
    hold_signal = bool(cont >= 55 and exh < 50 and has_room)
    if direction == "up":
        block["do_not_sell_yet"] = hold_signal
        block["cover_too_early_risk"] = False
    else:
        block["cover_too_early_risk"] = hold_signal
        block["do_not_sell_yet"] = False

    remaining_to_median = round(abs(median_t["from_here_pct"]), 1) if not median_t["reached"] else 0.0

    # Confidence (with reasons) + the single top-of-card action.
    confidence = _confidence_block(targets, days_active, cur_abs, r, ind, direction)
    decision = _decision(direction, maturity, exh, cont, hold_signal,
                         block.get("broke_resistance"), block["key_levels"].get("next"))

    similar = _similar_move(swings, cur_abs, days_active,
                            exclude_dates=(dates[from_i], dates[ext_i]))

    # Trade plan.
    if direction == "up":
        entry_zone = [round(from_p, 2), round(from_p * 1.03, 2)]
        invalidation = round(from_p * 0.985, 2)
        plan = {
            "side": "long",
            "entry_zone": entry_zone,
            "entry_note": (f"Look to enter near the swing low ${from_p:.2f} on a higher-low "
                           f"or a break above the prior day's high with volume."),
            "invalidation": invalidation,
            "invalidation_note": f"Thesis breaks on a close back below ${invalidation:.2f} (under the swing low).",
            "t1": median_t["price"], "t1_pct": median_t["pct_move"],
            "t2": p75_t["price"], "t2_pct": p75_t["pct_move"],
            "stretch": extreme_t["price"], "stretch_pct": extreme_t["pct_move"],
            "holding_window": (f"{r['days_p25']}–{r['days_p75']} trading days "
                               f"(through {_us_date(_busday_offset(from_date, max(1, r['days_p75'])))})"),
        }
    else:
        entry_zone = [round(from_p * 0.97, 2), round(from_p, 2)]
        invalidation = round(from_p * 1.015, 2)
        plan = {
            "side": "short",
            "entry_zone": entry_zone,
            "entry_note": (f"Look to short near the swing high ${from_p:.2f} on a lower-high "
                           f"or a break below the prior day's low with volume."),
            "invalidation": invalidation,
            "invalidation_note": f"Thesis breaks on a close back above ${invalidation:.2f} (over the swing high).",
            "t1": median_t["price"], "t1_pct": median_t["pct_move"],
            "t2": p75_t["price"], "t2_pct": p75_t["pct_move"],
            "stretch": extreme_t["price"], "stretch_pct": extreme_t["pct_move"],
            "holding_window": (f"{r['days_p25']}–{r['days_p75']} trading days "
                               f"(through {_us_date(_busday_offset(from_date, max(1, r['days_p75'])))})"),
        }
    plan["exit_warnings"] = exh_f[:4] or ["No exhaustion flags yet."]
    plan["reason_to_stay"] = cont_f[:4] or ["Momentum support is thin here."]
    # The nearest structural level is the real first take-profit / decision zone.
    next_level = block.get("key_levels", {}).get("next")
    if next_level and abs(next_level["pct_away"]) <= 12:
        lbl = "support" if next_level["kind"] == "support" else "resistance"
        plan["first_target_level"] = next_level["price"]
        plan["exit_warnings"] = ([f"approaching prior {lbl} ${next_level['price']:.2f} — first "
                                  f"{'cover' if direction == 'down' else 'trim'} / decision zone"]
                                 + plan["exit_warnings"])[:5]
    # Fold options-flow agreement into the plan's reasons / warnings.
    if flow_summary:
        if flow_summary["agrees_with_price"] == "agrees":
            plan["reason_to_stay"] = ([f"options flow agrees with the move "
                                       f"({flow_summary['label']} tape, quality {flow_summary['quality']})"]
                                      + plan["reason_to_stay"])[:5]
        elif flow_summary["agrees_with_price"] == "disagrees":
            plan["exit_warnings"] = ([f"options flow disagrees with the move "
                                      f"({flow_summary['label']} tape)"]
                                     + plan["exit_warnings"])[:5]

    block.update({
        "status": "ok",
        "vs_history": {
            "median_pct": r["pct_median"], "avg_pct": r["pct_avg"],
            "p25_pct": r["pct_p25"], "p75_pct": r["pct_p75"], "max_pct": r["pct_max"],
            "median_days": r["days_median"], "p25_days": r["days_p25"],
            "p75_days": r["days_p75"], "max_days": r["days_max"],
            "pct_of_median_move": round(cur_abs / r["pct_median"] * 100.0, 0) if r["pct_median"] else None,
            "pct_of_median_days": round(days_active / r["days_median"] * 100.0, 0) if r["days_median"] else None,
        },
        "maturity": maturity,
        "remaining_to_median_pct": remaining_to_median,
        "targets": targets,
        "exhaustion_score": exh,
        "exhaustion_factors": exh_f,
        "continuation_score": cont,
        "continuation_factors": cont_f,
        "trend_state": state,
        "signal_note": _signal_note(direction, hold_signal, maturity, exh, cont,
                                    remaining_to_median, median_t),
        "similar_move": similar,
        "relative_strength": rs,
        "flow": flow_summary,
        "confidence": confidence,
        "decision": decision,
        "trade_plan": plan,
    })
    return block


def _signal_note(direction, hold, maturity, exh, cont, remaining, median_t):
    verb = "sell this long" if direction == "up" else "cover this short"
    if hold and remaining > 1:
        return (f"Don't {verb} yet — momentum still favors continuation and the typical "
                f"move has ~{remaining:.0f}% left to the median target "
                f"(${median_t['price']:.2f}).")
    if maturity in ("extended", "exhausted") or exh >= 65:
        return (f"Move looks {maturity}; exhaustion risk is high ({exh:.0f}/100). "
                f"Tighten stops / take partials rather than chase.")
    if cont < 40:
        return "Momentum is fading — manage risk; the easy part of this move may be over."
    return "Move is developing normally; follow the plan and let it work."


# ─────────────────────────────── entrypoint ────────────────────────────────

def analyze(symbol: str, period: str = "1y", pct: float = 0.12,
            min_move_pct: float = 15.0, flow: dict | None = None) -> dict:
    symbol = symbol.upper().strip()
    if not _OK:
        return {"symbol": symbol, "error": "yfinance unavailable"}
    try:
        hist = yf.Ticker(symbol).history(period=period, interval="1d")
    except Exception as exc:  # noqa: BLE001
        return {"symbol": symbol, "error": f"history fetch failed: {exc}"}
    if hist is None or len(hist) < 20:
        return {"symbol": symbol, "error": "not enough price history"}

    # yfinance can return a trailing NaN bar for the in-progress / latest
    # session. Drop any row missing OHLC so a NaN doesn't cascade into the
    # current-price and move math (which would falsely read as "exhausted").
    try:
        hist = hist.dropna(subset=["Open", "High", "Low", "Close"])
    except Exception:
        pass
    if hist is None or len(hist) < 20:
        return {"symbol": symbol, "error": "not enough clean price history"}

    opens = [float(x) for x in hist["Open"]]
    highs = [float(x) for x in hist["High"]]
    lows = [float(x) for x in hist["Low"]]
    closes = [float(x) for x in hist["Close"]]
    vols = [float(x) for x in hist["Volume"]] if "Volume" in hist else [0.0] * len(closes)
    dates = [d.strftime("%Y-%m-%d") for d in hist.index]

    pivots = _zigzag(highs, lows, pct)

    up_swings = _build_swings(pivots, dates, "up", min_move_pct)
    down_swings = _build_swings(pivots, dates, "down", min_move_pct)
    up_rhythm = _rhythm(up_swings)
    down_rhythm = _rhythm(down_swings)
    ind = _indicators(opens, highs, lows, closes, vols)

    # Phase-2 context: benchmarks for relative strength, earnings for catalyst
    # tagging, and per-swing tags for the filter set. All best-effort/free.
    bench = _fetch_bench(period)
    earnings = _fetch_earnings(symbol)
    _enrich_swings(up_swings, "up", vols, closes, earnings)
    _enrich_swings(down_swings, "down", vols, closes, earnings)
    _attach_details(up_swings, "up", pivots, dates, up_rhythm)
    _attach_details(down_swings, "down", pivots, dates, down_rhythm)

    analysis = _analyze_active(pivots, dates, opens, highs, lows, closes, vols,
                               up_swings, down_swings, up_rhythm, down_rhythm, ind,
                               bench=bench, earnings=earnings, flow=flow)

    current_price = round(closes[-1], 2) if closes else None

    # Backward-compatible projection block (fresh swing low → upside band).
    projection = None
    last = pivots[-1] if pivots else None
    if last and last[2] == "low" and up_rhythm:
        low_i, low_p, _ = last
        low_date = dates[low_i]
        t_lo = round(low_p * (1 + up_rhythm["pct_p25"] / 100.0), 2)
        t_hi = round(low_p * (1 + up_rhythm["pct_p75"] / 100.0), 2)
        t_med = round(low_p * (1 + up_rhythm["pct_median"] / 100.0), 2)
        projection = {
            "from_low_date": low_date,
            "from_low_price": round(low_p, 2),
            "days_so_far": (len(dates) - 1) - low_i,
            "target_low": t_lo, "target_median": t_med, "target_high": t_hi,
            "pct_low": up_rhythm["pct_p25"], "pct_high": up_rhythm["pct_p75"],
            "window_start": _busday_offset(low_date, max(1, up_rhythm["days_p25"])),
            "window_end": _busday_offset(low_date, max(1, up_rhythm["days_p75"])),
            "to_target_median_pct": round((t_med - current_price) / current_price * 100.0, 1) if current_price else None,
        }

    # Strip internal index keys before returning.
    for s in up_swings + down_swings:
        s.pop("_lo_i", None); s.pop("_hi_i", None)

    return {
        "symbol": symbol,
        "current_price": current_price,
        "params": {"period": period, "pct": pct, "min_move_pct": min_move_pct},
        "swings": up_swings,
        "down_swings": down_swings,
        "rhythm": up_rhythm,
        "down_rhythm": down_rhythm,
        "indicators": ind,
        "analysis": analysis,
        "projection": projection,
        "bars": [{"t": dates[i], "o": round(opens[i], 2), "h": round(highs[i], 2),
                  "l": round(lows[i], 2), "c": round(closes[i], 2),
                  "v": int(vols[i]) if vols[i] == vols[i] else 0}
                 for i in range(len(dates))],
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
