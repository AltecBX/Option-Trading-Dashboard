"""intraday.py — VWAP engine, day-level map, and the Reversal Radar (v3.19).

The app's core mission: find stocks near the LOW of day before they bounce
(longs) and near the HIGH of day before they fade (shorts). This module owns
every intraday primitive that mission needs:

  • VWAP + volume-weighted σ bands (the institutional anchor; "stretch" is
    measured in σ so it means the same thing on every stock)
  • Day-level map: prior day H/L/C, premarket H/L, opening range, round
    numbers, cached expected-move band — reversals happen AT levels
  • Reversal evidence: volume climax, seller/buyer deceleration, failure to
    extend, 5-minute higher-low / lower-high, RSI(5m) divergence
  • Composite 0-100 score (stretch / exhaustion / location / confirmation /
    context) with a trend-day guard that caps counter-trend scores
  • Trade tickets: entry trigger, structure stop, VWAP target, R:R
  • A background radar worker (two-stage: free quote screen across the whole
    watchlist → minute-bar analysis for the top candidates only) that serves
    an instant snapshot and auto-logs every signal ≥ LOG_SCORE for the
    hit-rate report

Dependencies are injected via configure() so this module never imports the
dashboard (no cycles) and is trivially testable with fakes.
"""
from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as _dtime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None

# ── Injected dependencies ───────────────────────────────────────────────────
_SCHWAB_GETTER = None        # () -> SchwabClient | None
_BOARD_GETTER = None         # () -> {"rows": [...]}
_EM_GETTER = None            # (symbol) -> cached expected-move payload | None
_FLOW_FN = None              # (symbol, price) -> UW flow-score dict | None
_CHAIN_FN = None             # (symbol) -> normalized option chain | None
_MINUTE_DAY_FN = None        # (symbol, date_iso) -> 1-min bars for a past day
_DATA_DIR: Path | None = None

# Radar tuning — one place, so the score can be re-weighted from the
# hit-rate report without hunting through code.
STAGE1_RANGE_PCT = 1.0       # minimum day range (as % of prev close) to care
STAGE1_POS_LONG = 0.30       # bottom 30% of day range = long candidate
STAGE1_POS_SHORT = 0.70
STAGE2_PER_SIDE = 12         # minute-bar budget: symbols analyzed per side
MIN_PRICE = 3.0
LOG_SCORE = 70               # signals at/above this are journaled automatically
TREND_CAP = 60               # max counter-trend score on a trend day
WORKER_IDLE_SECS = 300       # radar sleeps when nobody has asked in 5 min
CYCLE_SECS = 55


def configure(schwab_getter, board_getter, data_dir, em_getter=None,
              flow_fn=None, chain_fn=None, minute_day_fn=None) -> None:
    global _SCHWAB_GETTER, _BOARD_GETTER, _DATA_DIR, _EM_GETTER
    global _FLOW_FN, _CHAIN_FN, _MINUTE_DAY_FN
    _SCHWAB_GETTER = schwab_getter
    _BOARD_GETTER = board_getter
    _DATA_DIR = Path(data_dir)
    _EM_GETTER = em_getter
    _FLOW_FN = flow_fn
    _CHAIN_FN = chain_fn
    _MINUTE_DAY_FN = minute_day_fn


def _now_et() -> datetime:
    return datetime.now(_ET) if _ET else datetime.utcnow()


def market_open(now: datetime | None = None) -> bool:
    n = now or _now_et()
    if n.weekday() >= 5:
        return False
    t = n.time()
    return _dtime(9, 30) <= t < _dtime(16, 0)


def _session_open_ts_ms(now: datetime | None = None) -> int:
    n = now or _now_et()
    start = datetime.combine(n.date(), _dtime(9, 30), tzinfo=n.tzinfo)
    return int(start.timestamp() * 1000)


# ── VWAP engine ──────────────────────────────────────────────────────────────

def split_premarket(bars: list) -> tuple[list, list]:
    """Split extended-hours bars into (premarket, regular session)."""
    cut = _session_open_ts_ms()
    pm = [b for b in bars if (b.get("ts") or 0) < cut]
    reg = [b for b in bars if (b.get("ts") or 0) >= cut]
    return pm, reg


def vwap_series(bars: list) -> dict | None:
    """Session VWAP + volume-weighted σ bands, computed bar by bar.

    Returns {vwap: [...], sigma: [...], last, sigma_last, stretch} where
    stretch = (last close − vwap) / σ — the volatility-normalized distance
    that makes "-2.8σ" comparable across every symbol.
    """
    if not bars:
        return None
    cum_v = cum_pv = cum_var = 0.0
    vwap_arr, sig_arr = [], []
    for b in bars:
        v = float(b.get("volume") or 0)
        h, l, c = b.get("high"), b.get("low"), b.get("close")
        if c is None:
            vwap_arr.append(vwap_arr[-1] if vwap_arr else None)
            sig_arr.append(sig_arr[-1] if sig_arr else 0.0)
            continue
        tp = (float(h if h is not None else c) + float(l if l is not None else c) + float(c)) / 3.0
        v = v if v > 0 else 1.0
        cum_v += v
        cum_pv += tp * v
        vw = cum_pv / cum_v
        cum_var += v * (tp - vw) ** 2
        sig = math.sqrt(cum_var / cum_v) if cum_v > 0 else 0.0
        vwap_arr.append(round(vw, 4))
        sig_arr.append(sig)
    last_close = next((float(b["close"]) for b in reversed(bars) if b.get("close") is not None), None)
    vw_last = vwap_arr[-1]
    sig_last = sig_arr[-1] or 0.0
    stretch = ((last_close - vw_last) / sig_last) if (last_close is not None and vw_last and sig_last > 1e-9) else 0.0
    return {"vwap": vwap_arr, "sigma": sig_arr, "last": vw_last,
            "sigma_last": round(sig_last, 4), "stretch": round(stretch, 2)}


def resample_5m(bars: list) -> list:
    out = []
    for b in bars:
        if b.get("close") is None:
            continue
        slot = (b["ts"] // 300000) * 300000
        if out and out[-1]["ts"] == slot:
            o = out[-1]
            o["high"] = max(o["high"], b["high"] if b.get("high") is not None else b["close"])
            o["low"] = min(o["low"], b["low"] if b.get("low") is not None else b["close"])
            o["close"] = b["close"]
            o["volume"] += float(b.get("volume") or 0)
        else:
            out.append({"ts": slot, "open": b.get("open") or b["close"],
                        "high": b.get("high") or b["close"], "low": b.get("low") or b["close"],
                        "close": b["close"], "volume": float(b.get("volume") or 0)})
    return out


def atr_5m(bars_5m: list, n: int = 14) -> float | None:
    if len(bars_5m) < 3:
        return None
    trs = []
    for i in range(1, len(bars_5m)):
        h, l, pc = bars_5m[i]["high"], bars_5m[i]["low"], bars_5m[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    window = trs[-n:] if len(trs) >= n else trs
    return sum(window) / len(window) if window else None


def _rsi(closes: list, period: int = 14) -> list:
    """Wilder RSI, aligned to closes (None until warm)."""
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / period, losses / period
    out[period] = 100 - 100 / (1 + (ag / al)) if al > 0 else 100.0
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
        out[i] = 100 - 100 / (1 + (ag / al)) if al > 0 else 100.0
    return out


# ── Day-level map ────────────────────────────────────────────────────────────

def _round_levels(spot: float) -> list:
    """The one or two round numbers nearest the current price."""
    if spot <= 0:
        return []
    step = 1.0 if spot < 50 else 5.0 if spot < 250 else 10.0 if spot < 1000 else 50.0
    below = math.floor(spot / step) * step
    out = [{"price": round(below, 2), "kind": "round", "label": f"round {below:g}"}]
    above = below + step
    out.append({"price": round(above, 2), "kind": "round", "label": f"round {above:g}"})
    return out


# Put/call open-interest walls from the nearest-expiry chain. OI only
# updates overnight, so a 30-minute cache means at most ~2 chain calls per
# symbol per hour — cheap enough to include for every radar candidate.
_OI_CACHE: dict = {}
_OI_LOCK = threading.Lock()


def _oi_walls(symbol: str, spot: float) -> list:
    if _CHAIN_FN is None or not spot:
        return []
    with _OI_LOCK:
        hit = _OI_CACHE.get(symbol)
        if hit and time.time() - hit["ts"] < 1800:
            return hit["walls"]
    walls = []
    try:
        full = _CHAIN_FN(symbol) or {}
        exps = full.get("expirations") or []
        if exps:
            chain = (full.get("chains") or {}).get(exps[0]) or {}
            lo_b, hi_b = spot * 0.88, spot * 1.12
            puts = [r for r in (chain.get("puts") or [])
                    if r.get("strike") and lo_b <= r["strike"] < spot and (r.get("openInterest") or 0) > 0]
            calls = [r for r in (chain.get("calls") or [])
                     if r.get("strike") and spot < r["strike"] <= hi_b and (r.get("openInterest") or 0) > 0]
            if puts:
                pw = max(puts, key=lambda r: r["openInterest"])
                walls.append({"price": float(pw["strike"]), "kind": "putwall",
                              "label": f"put wall {pw['strike']:g} ({int(pw['openInterest']/1000)}k OI)" if pw["openInterest"] >= 1000 else f"put wall {pw['strike']:g}"})
            if calls:
                cw = max(calls, key=lambda r: r["openInterest"])
                walls.append({"price": float(cw["strike"]), "kind": "callwall",
                              "label": f"call wall {cw['strike']:g} ({int(cw['openInterest']/1000)}k OI)" if cw["openInterest"] >= 1000 else f"call wall {cw['strike']:g}"})
    except Exception:
        walls = []
    with _OI_LOCK:
        _OI_CACHE[symbol] = {"ts": time.time(), "walls": walls}
        if len(_OI_CACHE) > 300:
            oldest = sorted(_OI_CACHE, key=lambda k: _OI_CACHE[k]["ts"])[:100]
            for k in oldest:
                _OI_CACHE.pop(k, None)
    return walls


def day_levels(symbol: str, spot: float, pm_bars: list, reg_bars: list,
               daily_bars: list | None) -> list:
    """The level map a reversal has to respect: prior day H/L/C, premarket
    H/L, 15-minute opening range, nearest round numbers, and the cached
    expected-move band when the EM card has produced one. Sorted by price."""
    levels = []
    if daily_bars and len(daily_bars) >= 1:
        # daily_bars oldest-first; last entry may be TODAY once the session
        # prints — take the last bar strictly before today.
        today = _now_et().date().isoformat()
        prior = None
        for b in reversed(daily_bars):
            d = str(b.get("date") or "")[:10]
            if d and d < today:
                prior = b
                break
        if prior:
            for k, lbl in (("high", "prev day high"), ("low", "prev day low"), ("close", "prev close")):
                v = prior.get(k)
                if v:
                    levels.append({"price": round(float(v), 2), "kind": f"pd{k[0]}", "label": lbl})
    if pm_bars:
        hs = [b["high"] for b in pm_bars if b.get("high")]
        ls = [b["low"] for b in pm_bars if b.get("low")]
        if hs:
            levels.append({"price": round(max(hs), 2), "kind": "pmh", "label": "premarket high"})
        if ls:
            levels.append({"price": round(min(ls), 2), "kind": "pml", "label": "premarket low"})
    if reg_bars:
        orb = [b for b in reg_bars if b["ts"] < reg_bars[0]["ts"] + 15 * 60000]
        hs = [b["high"] for b in orb if b.get("high")]
        ls = [b["low"] for b in orb if b.get("low")]
        if hs:
            levels.append({"price": round(max(hs), 2), "kind": "orh", "label": "opening range high"})
        if ls:
            levels.append({"price": round(min(ls), 2), "kind": "orl", "label": "opening range low"})
    if spot:
        levels.extend(_round_levels(spot))
    if _EM_GETTER is not None:
        try:
            em = _EM_GETTER(symbol)
            if em and em.get("em"):
                levels.append({"price": em["em"]["upper"], "kind": "emh", "label": f"EM high ({em['expiry']})"})
                levels.append({"price": em["em"]["lower"], "kind": "eml", "label": f"EM low ({em['expiry']})"})
        except Exception:
            pass
    levels.extend(_oi_walls(symbol, spot))
    levels.sort(key=lambda x: x["price"])
    return levels


def _near_levels(price: float, levels: list, tol_pct: float = 0.35) -> list:
    if not price:
        return []
    return [lv for lv in levels if abs(lv["price"] - price) / price * 100.0 <= tol_pct]


# ── Reversal evidence ────────────────────────────────────────────────────────

def reversal_evidence(reg_bars: list, side: str) -> dict:
    """Everything the score needs about HOW price is behaving at the extreme.
    side='long' analyzes the low of day; side='short' mirrors at the high."""
    ev = {"climax": False, "climax_z": None, "climax_ts": None, "decel": False,
          "failure": False, "structure": False, "rsi_div": False,
          "extreme": None, "extreme_ts": None, "bars": len(reg_bars)}
    bars = [b for b in reg_bars if b.get("close") is not None]
    if len(bars) < 10:
        return ev
    lows = [b["low"] if b.get("low") is not None else b["close"] for b in bars]
    highs = [b["high"] if b.get("high") is not None else b["close"] for b in bars]
    vols = [float(b.get("volume") or 0) for b in bars]
    long_side = side == "long"
    ext_i = min(range(len(bars)), key=lambda i: lows[i]) if long_side \
        else max(range(len(bars)), key=lambda i: highs[i])
    extreme = lows[ext_i] if long_side else highs[ext_i]
    ev["extreme"] = round(extreme, 4)
    ev["extreme_ts"] = bars[ext_i]["ts"]

    # Volume climax: a ≥2.5σ volume spike within 0.35% of the extreme —
    # capitulation (longs) or blow-off (shorts).
    mean_v = sum(vols) / len(vols)
    var_v = sum((v - mean_v) ** 2 for v in vols) / max(len(vols) - 1, 1)
    sd_v = math.sqrt(var_v) or 1.0
    best_z, best_i = 0.0, None
    for i, b in enumerate(bars):
        z = (vols[i] - mean_v) / sd_v
        px = lows[i] if long_side else highs[i]
        if z > best_z and extreme and abs(px - extreme) / extreme * 100.0 <= 0.35:
            best_z, best_i = z, i
    if best_i is not None and best_z >= 2.5:
        ev["climax"] = True
        ev["climax_z"] = round(best_z, 1)
        ev["climax_ts"] = bars[best_i]["ts"]

    # Deceleration: directional volume in the last 10 minutes vs the 10
    # minutes around the climax/extreme. Sellers drying up = the fuel for
    # the drop is gone.
    anchor = best_i if best_i is not None else ext_i
    def dir_vol(seg):
        if long_side:
            return sum(vols[j] for j in seg if bars[j]["close"] < bars[j]["open"])
        return sum(vols[j] for j in seg if bars[j]["close"] > bars[j]["open"])
    around = range(max(0, anchor - 5), min(len(bars), anchor + 5))
    recent = range(max(0, len(bars) - 10), len(bars))
    v_around, v_recent = dir_vol(around), dir_vol(recent)
    if v_around > 0 and anchor < len(bars) - 10:
        ev["decel"] = v_recent < 0.5 * v_around

    # Failure to extend: the most recent touch of the extreme zone came on
    # much lighter volume than the first — a retest nobody sold/bought.
    touches = [i for i in range(len(bars))
               if extreme and abs((lows[i] if long_side else highs[i]) - extreme) / extreme * 100.0 <= 0.15]
    if len(touches) >= 2 and vols[touches[0]] > 0:
        ev["failure"] = vols[touches[-1]] < 0.6 * max(vols[i] for i in touches[:-1])

    # 5-minute structure: after the extreme, two rising 5m lows (longs) /
    # falling 5m highs (shorts) — the first objective sign of a turn.
    b5 = resample_5m(bars)
    after = [b for b in b5 if b["ts"] > bars[ext_i]["ts"]]
    if len(after) >= 2:
        if long_side:
            ev["structure"] = (after[-1]["low"] > extreme and after[-1]["low"] >= after[-2]["low"]
                               and min(x["low"] for x in after) > extreme * 0.999)
        else:
            ev["structure"] = (after[-1]["high"] < extreme and after[-1]["high"] <= after[-2]["high"]
                               and max(x["high"] for x in after) < extreme * 1.001)

    # RSI(5m) divergence: price at/under the prior extreme while RSI holds
    # higher (longs) — momentum quietly turning before price does.
    closes5 = [b["close"] for b in b5]
    rsi5 = _rsi(closes5, 14)
    if len(b5) >= 20:
        piv = [i for i in range(len(b5)) if rsi5[i] is not None]
        if piv:
            if long_side:
                i1 = min(piv, key=lambda i: b5[i]["low"])
                earlier = [i for i in piv if i < i1 - 3]
                if earlier:
                    i0 = min(earlier, key=lambda i: b5[i]["low"])
                    ev["rsi_div"] = (b5[i1]["low"] <= b5[i0]["low"] * 1.001
                                     and rsi5[i1] is not None and rsi5[i0] is not None
                                     and rsi5[i1] > rsi5[i0] + 2)
            else:
                i1 = max(piv, key=lambda i: b5[i]["high"])
                # NB: for shorts the FIRST high can be anywhere before the top
                earlier = [i for i in piv if i < i1 - 3]
                if earlier:
                    i0 = max(earlier, key=lambda i: b5[i]["high"])
                    ev["rsi_div"] = (b5[i1]["high"] >= b5[i0]["high"] * 0.999
                                     and rsi5[i1] is not None and rsi5[i0] is not None
                                     and rsi5[i1] < rsi5[i0] - 2)
    return ev


# ── Trend-day guard (market regime) ──────────────────────────────────────────

def _vwap_side_stats(bars: list) -> dict | None:
    """Fraction of the last 90 minutes spent above session VWAP + whether
    the index printed a fresh extreme in the last 30 minutes."""
    vw = vwap_series(bars)
    if not vw or len(bars) < 20:
        return None
    closes = [(b["close"], i) for i, b in enumerate(bars) if b.get("close") is not None]
    recent = closes[-90:]
    above = sum(1 for c, i in recent if vw["vwap"][i] is not None and c > vw["vwap"][i])
    frac_above = above / len(recent)
    lows = [b["low"] for b in bars if b.get("low") is not None]
    highs = [b["high"] for b in bars if b.get("high") is not None]
    n30 = min(30, len(lows))
    fresh_low = min(lows[-n30:]) <= min(lows) * 1.0005 if lows else False
    fresh_high = max(highs[-n30:]) >= max(highs) * 0.9995 if highs else False
    return {"frac_above": frac_above, "fresh_low": fresh_low,
            "fresh_high": fresh_high, "stretch": vw["stretch"]}


def market_regime(index_bars: dict) -> dict:
    """index_bars: {'SPY': bars, 'QQQ': bars}. Verdict drives the radar's
    trend-day guard: on a trend day counter-trend scores are capped at
    TREND_CAP so the app never talks you into fading a freight train."""
    stats = {}
    for sym, bars in (index_bars or {}).items():
        s = _vwap_side_stats(bars or [])
        if s:
            stats[sym] = s
    if not stats:
        return {"verdict": "unknown", "label": "Regime unknown — index data unavailable",
                "detail": ""}
    avg_above = sum(s["frac_above"] for s in stats.values()) / len(stats)
    pressing_low = any(s["fresh_low"] and s["frac_above"] < 0.25 for s in stats.values())
    pressing_high = any(s["fresh_high"] and s["frac_above"] > 0.75 for s in stats.values())
    if avg_above <= 0.15 and pressing_low:
        v = "trend_down"
        label = "TREND DAY DOWN — long-reversal scores capped"
        detail = "Indexes one-sided below VWAP and pressing fresh lows. Fading weakness fights the tape."
    elif avg_above >= 0.85 and pressing_high:
        v = "trend_up"
        label = "TREND DAY UP — short-reversal scores capped"
        detail = "Indexes one-sided above VWAP and pressing fresh highs. Fading strength fights the tape."
    else:
        v = "rotational"
        label = "Rotational tape — fades favored"
        detail = "Indexes are two-sided around VWAP. Mean-reversion entries have the wind at their back."
    return {"verdict": v, "label": label, "detail": detail,
            "spy_above_vwap_pct": round(stats.get("SPY", {}).get("frac_above", 0) * 100),
            "qqq_above_vwap_pct": round(stats.get("QQQ", {}).get("frac_above", 0) * 100)}


# ── Scoring + tickets ────────────────────────────────────────────────────────

def _hour_band(t) -> str:
    if t < _dtime(10, 0):
        return "9:30-10"
    if t < _dtime(12, 0):
        return "10-12"
    if t < _dtime(14, 0):
        return "12-14"
    return "14-16"


# Learned time-of-day adjustment (v3.20): once a band has ≥20 RESOLVED
# signals, its own hit rate overrides the priors — evidence beats vibes.
# Recomputed at most every 10 minutes from the signal log.
_TOD_LEARNED: dict = {"ts": 0.0, "adj": {}, "stats": {}}
_TOD_MIN_N = 20


def _tod_learned() -> dict:
    if time.time() - _TOD_LEARNED["ts"] < 600:
        return _TOD_LEARNED["adj"]
    stats: dict = {}
    for s in _load_signals():
        res = s.get("resolved")
        if not res or res.get("outcome") not in ("t1", "stop"):
            continue
        try:
            band = _hour_band(datetime.fromisoformat(s["ts_iso"]).time())
        except Exception:
            continue
        st = stats.setdefault(band, {"t1": 0, "stop": 0})
        st[res["outcome"]] += 1
    adj = {}
    for band, st in stats.items():
        n = st["t1"] + st["stop"]
        if n < _TOD_MIN_N:
            continue
        hr = st["t1"] / n
        if hr < 0.45:
            adj[band] = -5
        elif hr > 0.65:
            adj[band] = 3
    _TOD_LEARNED.update({"ts": time.time(), "adj": adj, "stats": stats})
    return adj


def _tod_context(now: datetime | None = None) -> tuple[int, str]:
    """Time-of-day weighting: priors (reversals cluster mid-morning and
    afternoon; the open drive punishes fading) PLUS a learned adjustment
    from this radar's own resolved history once a band has enough data."""
    t = (now or _now_et()).time()
    if t < _dtime(10, 0):
        base, note = -5, "open drive (9:30-10:00) — fades get punished"
    elif t < _dtime(10, 45):
        base, note = 5, "prime reversal window (10:00-10:45)"
    elif t >= _dtime(14, 0):
        base, note = 5, "afternoon reversal window (14:00+)"
    else:
        base, note = 0, ""
    learned = _tod_learned().get(_hour_band(t), 0)
    if learned:
        note = (note + " · " if note else "") + f"learned {'+' if learned > 0 else ''}{learned} from this band's own hit rate"
    return base + learned, note


def score_candidate(side: str, quote: dict, vw: dict, ev: dict, levels: list,
                    regime: dict, board_row: dict | None,
                    now: datetime | None = None) -> dict:
    """Composite 0-100. Groups: Stretch 25 · Exhaustion 25 · Location 20 ·
    Confirmation 20 · Context 10, minus penalties. Reasons list carries the
    top evidence as short human chips."""
    long_side = side == "long"
    last = quote.get("last")
    reasons, flags = [], []
    stretch = vw["stretch"] if vw else 0.0
    signed = -stretch if long_side else stretch   # positive = stretched in our direction

    # Stretch has two independent measures and takes the better one:
    #  (a) VWAP σ-distance — great intraday, but structurally muted on a
    #      steady trend day because the cumulative band widens with the move;
    #  (b) day range vs the stock's OWN typical daily move (realized vol from
    #      the board, annualized → daily σ) — catches "this is a 2.5×-normal
    #      day and price is parked at the extreme of it".
    s_vw = 0.0
    if signed >= 3:
        s_vw = 25
    elif signed >= 2:
        s_vw = 15 + (signed - 2) * 10
    elif signed >= 1:
        s_vw = 5 + (signed - 1) * 10
    if signed >= 1:
        reasons.append(f"{stretch:+.1f}σ VWAP")
    s_rng = 0.0
    rng_pct = quote.get("rng_pct")
    rvol_ann = (board_row or {}).get("rvol")
    if rng_pct and rvol_ann:
        day_sigma = (float(rvol_ann) / math.sqrt(252.0))  # typical 1-day % move
        if day_sigma > 0.2:
            mult = rng_pct / day_sigma
            if mult >= 3.0:
                s_rng = 25
            elif mult >= 1.5:
                s_rng = (mult - 1.5) / 1.5 * 25
            if mult >= 2.0:
                reasons.append(f"{mult:.1f}× normal day range")
    s_stretch = round(min(max(s_vw, s_rng), 25))

    s_exh = 0
    if ev.get("climax"):
        s_exh += 12
        ts = ev.get("climax_ts")
        hhmm = datetime.fromtimestamp(ts / 1000, _ET).strftime("%H:%M") if (ts and _ET) else ""
        reasons.append(f"vol climax {ev.get('climax_z')}σ {hhmm}".strip())
    if ev.get("decel"):
        s_exh += 7
        reasons.append("sellers drying up" if long_side else "buyers drying up")
    if ev.get("failure"):
        s_exh += 6
        reasons.append("retest on light volume")

    s_loc = 0
    ext = ev.get("extreme") or last
    hits = _near_levels(ext, levels)
    for lv in hits[:3]:
        if lv["kind"] == "round" and s_loc >= 14:
            continue
        s_loc = min(s_loc + 7, 20)
        reasons.append(f"at {lv['label']}")

    s_conf = 0
    if ev.get("structure"):
        s_conf += 8
        reasons.append("5m higher low" if long_side else "5m lower high")
    if ev.get("rsi_div"):
        s_conf += 6
        reasons.append("RSI divergence")
    # Level reclaim: back above the nearest broken level (longs) — the
    # old scanner's whole entry condition is now just 6 points of confirm.
    if last and ext:
        broken = [lv for lv in levels if (lv["price"] > ext and lv["price"] < last)] if long_side \
            else [lv for lv in levels if (lv["price"] < ext and lv["price"] > last)]
        if broken and abs(last - ext) / ext * 100.0 >= 0.3:
            s_conf += 6
            reasons.append(f"reclaimed {broken[0]['label']}" if long_side else f"lost {broken[0]['label']}")
    s_conf = min(s_conf, 20)

    s_ctx = 0
    tod, tod_note = _tod_context(now)
    s_ctx += tod
    if tod and tod_note:
        (reasons if tod > 0 else flags).append(tod_note)
    if board_row:
        fdir = (board_row.get("flow_dir") or "").lower()
        if (long_side and fdir == "bullish") or (not long_side and fdir == "bearish"):
            s_ctx += 3
            reasons.append(f"options flow {fdir}")
    s_ctx = max(min(s_ctx, 10), -5)

    score = max(0, s_stretch + s_exh + s_loc + s_conf + s_ctx)

    # Penalties: repricing (this isn't "stretched", it's a new reality) and
    # the trend-day cap.
    rvol_t = quote.get("rvol_t")
    chg = quote.get("chg_pct")
    if rvol_t and chg is not None and rvol_t >= 5 and abs(chg) >= 4:
        score = max(0, score - 15)
        flags.append(f"repricing: {chg:+.1f}% on {rvol_t:.0f}× volume — likely news, not stretch")
    verdict = (regime or {}).get("verdict")
    if (long_side and verdict == "trend_down") or (not long_side and verdict == "trend_up"):
        if score > TREND_CAP:
            score = TREND_CAP
        flags.append("counter-trend on a trend day — score capped")

    return {"score": int(round(min(score, 100))), "reasons": reasons[:5], "flags": flags,
            "groups": {"stretch": s_stretch, "exhaustion": s_exh, "location": s_loc,
                       "confirmation": s_conf, "context": s_ctx}}


def build_ticket(side: str, quote: dict, vw: dict, ev: dict, reg_bars: list) -> dict | None:
    """Structure-derived plan. Entry trigger = break of the most recent 5m
    swing against the extreme; stop = extreme ± 0.25×ATR(5m); T1 = VWAP
    (where mean-reversion actually pays); T2 = the session open."""
    last = quote.get("last")
    ext = ev.get("extreme")
    if not last or not ext or not vw:
        return None
    long_side = side == "long"
    b5 = resample_5m(reg_bars)
    a = atr_5m(b5) or (abs(last) * 0.003)
    after = [b for b in b5 if b["ts"] > (ev.get("extreme_ts") or 0)]
    if long_side:
        trigger = max((b["high"] for b in after[-3:]), default=last)
        stop = ext - 0.25 * a
        t1 = vw["last"]
        t2 = quote.get("open") or (t1 + (t1 - stop))
        if t2 < t1:
            t1, t2 = t2, t1
        risk = last - stop
        rr = (t1 - last) / risk if risk > 0 else None
    else:
        trigger = min((b["low"] for b in after[-3:]), default=last)
        stop = ext + 0.25 * a
        t1 = vw["last"]
        t2 = quote.get("open") or (t1 - (stop - t1))
        if t2 > t1:
            t1, t2 = t2, t1
        risk = stop - last
        rr = (last - t1) / risk if risk > 0 else None
    if rr is not None and rr < 0:
        rr = 0.0
    r2 = lambda v: round(float(v), 2) if v is not None else None
    return {"entry": r2(last), "trigger": r2(trigger), "stop": r2(stop),
            "t1": r2(t1), "t2": r2(t2), "rr": round(rr, 2) if rr is not None else None,
            "atr5m": r2(a)}


# ── Flow-at-extreme bonus (v3.20) ───────────────────────────────────────────
# Fresh Unusual Whales flow for candidates that already score well: aggressive
# sweeps hitting in the reversal's direction while price sits at the extreme
# is a leading signal that someone with size is betting on the turn. Budgeted
# hard: cached 5 min per symbol, max _FLOW_MAX_PER_CYCLE live calls per cycle.
_FLOW_CACHE: dict = {}
_FLOW_LOCK = threading.Lock()
_FLOW_MAX_PER_CYCLE = 8
_FLOW_CYCLE_USED = 0
FLOW_MIN_SCORE = 55          # only spend UW budget on candidates worth it


def _reset_flow_budget() -> None:
    global _FLOW_CYCLE_USED
    with _FLOW_LOCK:
        _FLOW_CYCLE_USED = 0


def _fresh_flow(symbol: str, price: float) -> dict | None:
    global _FLOW_CYCLE_USED
    if _FLOW_FN is None:
        return None
    with _FLOW_LOCK:
        hit = _FLOW_CACHE.get(symbol)
        if hit and time.time() - hit["ts"] < 300:
            return hit["res"]
        if _FLOW_CYCLE_USED >= _FLOW_MAX_PER_CYCLE:
            return None
        _FLOW_CYCLE_USED += 1
    try:
        res = _FLOW_FN(symbol, price)
    except Exception:
        res = None
    with _FLOW_LOCK:
        _FLOW_CACHE[symbol] = {"ts": time.time(), "res": res}
        if len(_FLOW_CACHE) > 200:
            for k in sorted(_FLOW_CACHE, key=lambda k: _FLOW_CACHE[k]["ts"])[:80]:
                _FLOW_CACHE.pop(k, None)
    return res


def apply_flow_bonus(side: str, symbol: str, price: float, score: int,
                     reasons: list, flags: list) -> int:
    """+5 when fresh flow agrees with the reversal; a warning flag (no score
    hit — the tape evidence already spoke) when it strongly disagrees."""
    if score < FLOW_MIN_SCORE:
        return score
    fl = _fresh_flow(symbol, price)
    if not fl or not fl.get("data_available"):
        return score
    bull = float(fl.get("bullish") or 0)
    bear = float(fl.get("bearish") or 0)
    sweeps_for = fl.get("call_sweeps" if side == "long" else "put_sweeps") or 0
    sweeps_against = fl.get("put_sweeps" if side == "long" else "call_sweeps") or 0
    net = (bull - bear) if side == "long" else (bear - bull)
    if net >= 15 or (sweeps_for >= 3 and sweeps_for > sweeps_against * 2):
        score = min(100, score + 5)
        reasons.append(f"fresh sweeps {'bullish' if side == 'long' else 'bearish'}")
    elif net <= -25:
        flags.append("live options flow leaning against this reversal")
    return score


# ── Signal log + hit-rate report ─────────────────────────────────────────────

_SIG_LOCK = threading.Lock()


def _sig_path() -> Path:
    return (_DATA_DIR or Path(".")) / "radar_signals.json"


def _load_signals() -> list:
    try:
        p = _sig_path()
        if p.exists():
            data = json.loads(p.read_text())
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _save_signals(sigs: list) -> None:
    try:
        # Keep 60 days; atomic write.
        cutoff = (_now_et().date() - timedelta(days=60)).isoformat()
        sigs = [s for s in sigs if (s.get("date") or "") >= cutoff]
        p = _sig_path()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(sigs, separators=(",", ":")))
        tmp.replace(p)
    except Exception:
        pass


def log_signal(sig: dict) -> None:
    """One auto-journaled entry per (date, symbol, side); keeps best score.
    Every signal ≥ LOG_SCORE lands here whether or not you click — that
    dataset is what the hit-rate report learns from."""
    with _SIG_LOCK:
        sigs = _load_signals()
        for s in sigs:
            if s["date"] == sig["date"] and s["symbol"] == sig["symbol"] and s["side"] == sig["side"]:
                if sig["score"] > s.get("score", 0) and not s.get("resolved"):
                    s.update({k: sig[k] for k in ("score", "reasons", "regime") if k in sig})
                _save_signals(sigs)
                return
        sigs.append(sig)
        _save_signals(sigs)


def _walk_resolve(s: dict, bars: list) -> dict | None:
    """First touch of stop or T1 after the signal fired wins; a bar hitting
    both counts as a stop (conservative). Returns the resolution or None."""
    after = [b for b in bars if b["ts"] > s.get("ts_ms", 0) and b.get("close") is not None]
    for b in after:
        lo = b.get("low") or b["close"]
        hi = b.get("high") or b["close"]
        if s["side"] == "long":
            if lo <= s["stop"]:
                return {"outcome": "stop", "r": -1.0, "ts_ms": b["ts"]}
            if hi >= s["t1"]:
                risk = s["entry"] - s["stop"]
                return {"outcome": "t1", "r": round((s["t1"] - s["entry"]) / risk, 2) if risk > 0 else 1.0,
                        "ts_ms": b["ts"]}
        else:
            if hi >= s["stop"]:
                return {"outcome": "stop", "r": -1.0, "ts_ms": b["ts"]}
            if lo <= s["t1"]:
                risk = s["stop"] - s["entry"]
                return {"outcome": "t1", "r": round((s["entry"] - s["t1"]) / risk, 2) if risk > 0 else 1.0,
                        "ts_ms": b["ts"]}
    return None


def resolve_signals_live(symbol: str, reg_bars: list) -> None:
    """Resolve today's open signals for a symbol against today's bars."""
    today = _now_et().date().isoformat()
    with _SIG_LOCK:
        sigs = _load_signals()
        changed = False
        for s in sigs:
            if s.get("resolved") or s["symbol"] != symbol or s["date"] != today:
                continue
            res = _walk_resolve(s, reg_bars)
            if res:
                s["resolved"] = res
                changed = True
        if changed:
            _save_signals(sigs)


def expire_stale_signals() -> None:
    """Resolve signals from prior days that never hit stop or T1 while the
    radar was watching. v3.20: exact first-touch via that day's minute bars
    when available (budgeted); otherwise mark-to-close via daily bars —
    honest accounting either way."""
    sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
    today = _now_et().date().isoformat()
    with _SIG_LOCK:
        sigs = _load_signals()
        changed = False
        budget = 15
        for s in sigs:
            if s.get("resolved") or s["date"] >= today or budget <= 0:
                continue
            budget -= 1
            # Preferred: replay that day's minute bars for a true resolution.
            res = None
            if _MINUTE_DAY_FN is not None:
                try:
                    bars = _MINUTE_DAY_FN(s["symbol"], s["date"]) or []
                    if bars:
                        res = _walk_resolve(s, bars)
                        if res is None:
                            # Watched the whole day: genuinely neither hit —
                            # expire at that day's last minute close.
                            close = next((b["close"] for b in reversed(bars) if b.get("close")), None)
                            if close is not None:
                                risk = abs(s["entry"] - s["stop"]) or 1e-9
                                r = round(((close - s["entry"]) if s["side"] == "long"
                                           else (s["entry"] - close)) / risk, 2)
                                res = {"outcome": "expired", "r": r, "ts_ms": None}
                except Exception:
                    res = None
            if res is None:
                # Fallback: mark at the daily close.
                r = None
                try:
                    if sc is not None:
                        dailies = sc.get_price_history(s["symbol"], days=30) or []
                        close = next((float(b["close"]) for b in dailies
                                      if str(b.get("date") or "")[:10] == s["date"] and b.get("close")), None)
                        if close is not None:
                            risk = abs(s["entry"] - s["stop"]) or 1e-9
                            r = round(((close - s["entry"]) if s["side"] == "long"
                                       else (s["entry"] - close)) / risk, 2)
                except Exception:
                    r = None
                res = {"outcome": "expired", "r": r, "ts_ms": None}
            s["resolved"] = res
            changed = True
        if changed:
            _save_signals(sigs)


def radar_report() -> dict:
    """Hit-rate by score bucket × side (+ hour band) from the auto-logged
    signals. This is how the score gets tuned from evidence, not vibes."""
    expire_stale_signals()
    sigs = _load_signals()
    def bucket(score):
        return "90+" if score >= 90 else "80-89" if score >= 80 else "70-79"
    agg = {}
    hours = {}
    for s in sigs:
        res = s.get("resolved")
        key = (bucket(s.get("score", 0)), s.get("side"))
        a = agg.setdefault(key, {"n": 0, "t1": 0, "stop": 0, "expired": 0, "open": 0, "r_sum": 0.0, "r_n": 0})
        a["n"] += 1
        if not res:
            a["open"] += 1
        else:
            a[res["outcome"]] = a.get(res["outcome"], 0) + 1
            if res.get("r") is not None:
                a["r_sum"] += res["r"]
                a["r_n"] += 1
        try:
            hr = datetime.fromisoformat(s["ts_iso"]).hour
            band = "9:30-10" if hr < 10 else "10-12" if hr < 12 else "12-14" if hr < 14 else "14-16"
            h = hours.setdefault(band, {"n": 0, "t1": 0, "stop": 0})
            h["n"] += 1
            if res and res["outcome"] in ("t1", "stop"):
                h[res["outcome"]] += 1
        except Exception:
            pass
    rows = []
    for (bkt, side), a in sorted(agg.items()):
        done = a["t1"] + a["stop"] + a["expired"]
        rows.append({"bucket": bkt, "side": side, "signals": a["n"], "open": a["open"],
                     "t1": a["t1"], "stop": a["stop"], "expired": a["expired"],
                     "hit_rate": round(a["t1"] / done * 100.0, 1) if done else None,
                     "avg_r": round(a["r_sum"] / a["r_n"], 2) if a["r_n"] else None})
    hour_rows = [{"band": b, **h,
                  "hit_rate": round(h["t1"] / (h["t1"] + h["stop"]) * 100.0, 1) if (h["t1"] + h["stop"]) else None}
                 for b, h in sorted(hours.items())]

    # Tuning (v3.20): the learned time-of-day adjustments currently applied,
    # plus plain-English suggestions once buckets have enough evidence.
    learned = dict(_tod_learned())
    suggestions = []
    for hr in hour_rows:
        n = hr["t1"] + hr["stop"]
        if n >= _TOD_MIN_N and hr["hit_rate"] is not None:
            if hr["hit_rate"] < 45:
                suggestions.append(f"{hr['band']} signals hit only {hr['hit_rate']}% ({n} resolved) — the radar now docks this window 5 points automatically.")
            elif hr["hit_rate"] > 65:
                suggestions.append(f"{hr['band']} signals hit {hr['hit_rate']}% ({n} resolved) — the radar now adds 3 points in this window automatically.")
    for row in rows:
        done = row["t1"] + row["stop"] + row["expired"]
        if done >= 15 and row["hit_rate"] is not None:
            if row["bucket"] == "70-79" and row["hit_rate"] < 45:
                suggestions.append(f"70-79 {row['side']}s hit only {row['hit_rate']}% over {done} resolved — treat that bucket as watch-only; wait for 80+.")
            if row["bucket"] == "90+" and row["hit_rate"] > 70:
                suggestions.append(f"90+ {row['side']}s hit {row['hit_rate']}% — these deserve full size.")
    return {"as_of": _now_et().isoformat(), "total_signals": len(sigs),
            "buckets": rows, "hours": hour_rows,
            "tuning": {"learned_tod": learned, "suggestions": suggestions,
                       "min_n": _TOD_MIN_N}}


# ── Radar worker ─────────────────────────────────────────────────────────────

_RADAR_LOCK = threading.Lock()
_RADAR: dict = {"long": [], "short": [], "regime": {"verdict": "unknown"},
                "as_of": None, "scanning": False, "market_open": False,
                "last_req": 0.0, "thread": None, "cycle": 0, "universe": 0,
                "error": None}


def _stage1(sc) -> tuple[list, list]:
    """Free screen across the whole watchlist from batched quotes: who is
    parked near their day extreme on a meaningful range? Returns (long
    candidates, short candidates) as quote dicts, best-first."""
    board = (_BOARD_GETTER() if _BOARD_GETTER else {}) or {}
    rows = {str(r.get("symbol") or "").upper(): r for r in (board.get("rows") or [])}
    symbols = [s for s in rows if s]
    with _RADAR_LOCK:
        _RADAR["universe"] = len(symbols)
    if not symbols:
        return [], []
    now = _now_et()
    elapsed = max(((now.hour - 9) * 60 + now.minute - 30) / 390.0, 0.15)
    quotes = {}

    def fetch(batch):
        try:
            return sc.get_quotes(batch) or {}
        except Exception:
            return {}
    batches = [symbols[i:i + 25] for i in range(0, len(symbols), 25)]
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(fetch, batches):
            quotes.update(res)

    longs, shorts = [], []
    for sym, q in quotes.items():
        sym = str(sym).upper()
        if not q:
            continue
        last, hi, lo, op, pc = (q.get("last"), q.get("high"), q.get("low"),
                                q.get("open"), q.get("close_prev"))
        vol = q.get("volume")
        if not last or not hi or not lo or last < MIN_PRICE or hi <= lo:
            continue
        rng_pct = (hi - lo) / (pc or last) * 100.0
        if rng_pct < STAGE1_RANGE_PCT:
            continue
        pos = (last - lo) / (hi - lo)
        row = rows.get(sym) or {}
        avg_v = row.get("avg_volume")
        rvol_t = (vol / (avg_v * elapsed)) if (vol and avg_v) else None
        cand = {"symbol": sym, "last": last, "open": op, "high": hi, "low": lo,
                "prev_close": pc, "volume": vol, "rng_pct": round(rng_pct, 2),
                "pos": round(pos, 3), "rvol_t": round(rvol_t, 2) if rvol_t else None,
                "chg_pct": round((last - pc) / pc * 100.0, 2) if pc else None,
                "tag": row.get("tag") or "", "company": row.get("company")}
        if pos <= STAGE1_POS_LONG:
            cand["s1_rank"] = rng_pct * (STAGE1_POS_LONG - pos + 0.05) * (min(rvol_t, 4.0) if rvol_t else 1.0)
            longs.append(cand)
        elif pos >= STAGE1_POS_SHORT:
            cand["s1_rank"] = rng_pct * (pos - STAGE1_POS_SHORT + 0.05) * (min(rvol_t, 4.0) if rvol_t else 1.0)
            shorts.append(cand)
    longs.sort(key=lambda c: -c["s1_rank"])
    shorts.sort(key=lambda c: -c["s1_rank"])
    return longs[:STAGE2_PER_SIDE * 2], shorts[:STAGE2_PER_SIDE * 2]


def _stage2_one(sc, cand: dict, side: str, regime: dict, daily_cache: dict) -> dict | None:
    """Minute-bar analysis for one candidate: VWAP, levels, evidence, score,
    ticket, sparkline. Also live-resolves any open signal on the symbol."""
    sym = cand["symbol"]
    try:
        bars = sc.get_intraday(sym, extended=True) or []
    except Exception:
        bars = []
    pm, reg = split_premarket(bars)
    if len(reg) < 10:
        return None
    vw = vwap_series(reg)
    if not vw:
        return None
    try:
        daily = daily_cache.get(sym)
        if daily is None:
            daily = sc.get_price_history(sym, days=10) or []
            daily_cache[sym] = daily
    except Exception:
        daily = []
    levels = day_levels(sym, cand["last"], pm, reg, daily)
    ev = reversal_evidence(reg, side)
    board = (_BOARD_GETTER() if _BOARD_GETTER else {}) or {}
    brow = next((r for r in (board.get("rows") or [])
                 if str(r.get("symbol") or "").upper() == sym), None)
    sc_res = score_candidate(side, cand, vw, ev, levels, regime, brow)
    # Fresh-flow bonus AFTER the base score so UW budget is only spent on
    # candidates already worth watching.
    sc_res["score"] = apply_flow_bonus(side, sym, cand["last"], sc_res["score"],
                                       sc_res["reasons"], sc_res["flags"])
    ticket = build_ticket(side, cand, vw, ev, reg)
    resolve_signals_live(sym, reg)

    closes = [b["close"] for b in reg if b.get("close") is not None]
    step = max(1, len(closes) // 40)
    spark = [round(c, 3) for c in closes[::step]][-40:]

    out = {**{k: cand[k] for k in ("symbol", "last", "open", "high", "low", "pos",
                                   "rng_pct", "rvol_t", "chg_pct", "tag", "company")},
           "side": side, "score": sc_res["score"], "reasons": sc_res["reasons"],
           "flags": sc_res["flags"], "groups": sc_res["groups"],
           "vwap": vw["last"], "stretch": vw["stretch"],
           "ticket": ticket, "spark": spark,
           "extreme_ts": ev.get("extreme_ts")}

    if sc_res["score"] >= LOG_SCORE and ticket:
        now = _now_et()
        log_signal({"id": f"{sym}-{side}-{now.date().isoformat()}",
                    "date": now.date().isoformat(), "ts_iso": now.isoformat(),
                    "ts_ms": int(time.time() * 1000),
                    "symbol": sym, "side": side, "score": sc_res["score"],
                    "spot": cand["last"], "entry": ticket["entry"],
                    "stop": ticket["stop"], "t1": ticket["t1"], "t2": ticket["t2"],
                    "reasons": sc_res["reasons"],
                    "regime": (regime or {}).get("verdict")})
    return out


def _radar_cycle(sc) -> None:
    _reset_flow_budget()
    # Regime first — it gates everything else this cycle.
    idx = {}
    for isym in ("SPY", "QQQ"):
        try:
            idx[isym] = sc.get_intraday(isym) or []
        except Exception:
            idx[isym] = []
    regime = market_regime(idx)

    with _RADAR_LOCK:
        cycle = _RADAR["cycle"]
    if cycle % 3 == 0 or not _RADAR.get("_s1"):
        longs, shorts = _stage1(sc)
        with _RADAR_LOCK:
            _RADAR["_s1"] = (longs, shorts)
    else:
        longs, shorts = _RADAR["_s1"]

    daily_cache: dict = {}
    results = {"long": [], "short": []}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [(side, ex.submit(_stage2_one, sc, c, side, regime, daily_cache))
                for side, cands in (("long", longs[:STAGE2_PER_SIDE]),
                                    ("short", shorts[:STAGE2_PER_SIDE]))
                for c in cands]
        for side, f in futs:
            try:
                r = f.result()
                if r:
                    results[side].append(r)
            except Exception:
                pass
    for side in results:
        results[side].sort(key=lambda r: -r["score"])
        results[side] = results[side][:8]

    with _RADAR_LOCK:
        _RADAR.update({"long": results["long"], "short": results["short"],
                       "regime": regime, "as_of": _now_et().isoformat(),
                       "cycle": cycle + 1, "error": None})


def _radar_loop() -> None:
    try:
        while True:
            with _RADAR_LOCK:
                idle = time.time() - _RADAR["last_req"] > WORKER_IDLE_SECS
            if idle or not market_open():
                break
            sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
            if sc is None:
                with _RADAR_LOCK:
                    _RADAR["error"] = "Schwab not connected"
                break
            t0 = time.time()
            try:
                _radar_cycle(sc)
            except Exception as exc:  # noqa: BLE001
                with _RADAR_LOCK:
                    _RADAR["error"] = str(exc)
            time.sleep(max(5.0, CYCLE_SECS - (time.time() - t0)))
    finally:
        with _RADAR_LOCK:
            _RADAR["scanning"] = False
            _RADAR["thread"] = None


def radar_snapshot() -> dict:
    """Instant snapshot for /api/radar; lazily (re)starts the worker while
    the market is open and someone is watching."""
    with _RADAR_LOCK:
        _RADAR["last_req"] = time.time()
        _RADAR["market_open"] = market_open()
        if _RADAR["market_open"] and not _RADAR["scanning"]:
            _RADAR["scanning"] = True
            t = threading.Thread(target=_radar_loop, name="radar", daemon=True)
            _RADAR["thread"] = t
            t.start()
        return {"long": list(_RADAR["long"]), "short": list(_RADAR["short"]),
                "regime": dict(_RADAR["regime"]), "as_of": _RADAR["as_of"],
                "scanning": _RADAR["scanning"], "market_open": _RADAR["market_open"],
                "universe": _RADAR["universe"], "error": _RADAR["error"]}


# ── /api/intraday payload (chart mode) ──────────────────────────────────────

def intraday_chart_payload(symbol: str) -> dict:
    """Bars + VWAP/bands + level map + today's radar signals for one symbol —
    everything the intraday chart mode draws."""
    symbol = symbol.upper().strip()
    sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
    if sc is None:
        return {"symbol": symbol, "error": "Schwab not connected"}
    bars = sc.get_intraday(symbol, extended=True) or []
    pm, reg = split_premarket(bars)
    if not reg and not pm:
        return {"symbol": symbol, "error": "no intraday bars yet"}
    vw = vwap_series(reg) if reg else None
    try:
        daily = sc.get_price_history(symbol, days=10) or []
    except Exception:
        daily = []
    last = next((b["close"] for b in reversed(reg or pm) if b.get("close") is not None), None)
    levels = day_levels(symbol, last, pm, reg, daily)
    today = _now_et().date().isoformat()
    sigs = [s for s in _load_signals() if s["symbol"] == symbol and s["date"] == today]
    out_bars = [{**b, "pm": True} for b in pm] + reg
    payload = {"symbol": symbol, "as_of": _now_et().isoformat(),
               "bars": out_bars, "levels": levels, "signals": sigs,
               "market_open": market_open()}
    if vw:
        n = len(reg)
        payload["vwap"] = {
            "ts": [b["ts"] for b in reg],
            "vwap": vw["vwap"],
            "upper1": [round(vw["vwap"][i] + vw["sigma"][i], 4) if vw["vwap"][i] is not None else None for i in range(n)],
            "lower1": [round(vw["vwap"][i] - vw["sigma"][i], 4) if vw["vwap"][i] is not None else None for i in range(n)],
            "upper2": [round(vw["vwap"][i] + 2 * vw["sigma"][i], 4) if vw["vwap"][i] is not None else None for i in range(n)],
            "lower2": [round(vw["vwap"][i] - 2 * vw["sigma"][i], 4) if vw["vwap"][i] is not None else None for i in range(n)],
            "stretch": vw["stretch"], "last": vw["last"],
        }
    return payload
