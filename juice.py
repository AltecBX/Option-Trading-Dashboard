"""juice.py — 0-3 DTE Premium Juice scanner (v3.22).

Finds stocks whose same-week options still carry unusually fat premium with
very little time left (the Thursday/Friday MU-style setup), ranks them with a
0-100 Juice Score, and pre-computes every premium-selling structure worth
evaluating — clearly separating defined-risk from undefined-risk.

Architecture mirrors the Reversal Radar:
  Stage 1 (free): rank the $5B+ watchlist board by realized-vol richness,
      earnings proximity and day movement — no API cost.
  Stage 2 (one light chain call per candidate): today..+3d expirations only,
      full metrics + score + strategy suggestions for the nearest expiry.
  A lazy background worker refreshes a snapshot every ~4 minutes while the
  tab is being watched; the endpoint always serves instantly.

All math that matters is pure and unit-tested; POP and buying-power figures
are clearly-documented broker approximations, not fills.
"""
from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as _dtime, timedelta

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None

from metrics import one_sigma_move

_SCHWAB_GETTER = None
_BOARD_GETTER = None
_IV_RANK_FN = None       # (symbol, iv_decimal) -> {"iv_rank", "iv_pct"} | None
_PIVOTS_FN = None        # (symbol, spot) -> {"support": [...], "resistance": [...]}

MAX_DTE = 3
STAGE2_BUDGET = 36       # chain calls per scan pass (shares the 110/min quota
                         # with the radar worker + normal browsing)
MIN_PRICE = 20.0
MIN_MARKET_CAP = 5_000_000_000
CYCLE_SECS = 240
WORKER_IDLE_SECS = 600
ROW_MAX_AGE = 900        # carried-over rows expire after 15 min


def configure(schwab_getter, board_getter, iv_rank_fn=None, pivots_fn=None) -> None:
    global _SCHWAB_GETTER, _BOARD_GETTER, _IV_RANK_FN, _PIVOTS_FN
    _SCHWAB_GETTER = schwab_getter
    _BOARD_GETTER = board_getter
    _IV_RANK_FN = iv_rank_fn
    _PIVOTS_FN = pivots_fn


def _now_et() -> datetime:
    return datetime.now(_ET) if _ET else datetime.utcnow()


def _market_open(now=None) -> bool:
    n = now or _now_et()
    return n.weekday() < 5 and _dtime(9, 30) <= n.time() < _dtime(16, 0)


def _dte_days(expiry: date, now: datetime | None = None) -> tuple[int, float]:
    """(calendar DTE, fractional trading days remaining). For 0 DTE the
    fraction is hours-to-close / 6.5 so 'premium per day left' stays honest
    at 2pm on expiration Friday."""
    n = now or _now_et()
    dte = (expiry - n.date()).days
    if dte > 0:
        return dte, float(dte)
    close = datetime.combine(n.date(), _dtime(16, 0), tzinfo=n.tzinfo)
    hours = max((close - n).total_seconds() / 3600.0, 0.3)
    return 0, max(hours / 6.5, 0.05)


def _mid(row) -> float:
    b, a = float(row.get("bid") or 0), float(row.get("ask") or 0)
    if b > 0 and a > 0:
        return (b + a) / 2.0
    l = float(row.get("last") or 0)
    return l if l > 0 else 0.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ── Strategy builders ────────────────────────────────────────────────────────
# All returns are per-share (×100 for a contract). POP figures are estimates,
# never fills, and each carries a "pop_basis" tag so the UI can say exactly
# which approximation produced it:
#   "delta"     — P(expire OTM) ≈ 1 − |short delta| (per side, summed for
#                 two-sided structures). A P(ITM) proxy, not P(touch).
#   "one_sigma" — P(close inside break-evens) = 2·Φ(credit/1σ) − 1 with the
#                 true 1σ move S·σ·√T (NOT the straddle, which is ≈1.25σ).
# Buying power for undefined risk uses the common broker formula
# max(20%·spot − OTM, 10%·strike) + credit, per side.

def _by_delta(rows: list, target: float, side: str) -> dict | None:
    """The strike whose |delta| is closest to target, restricted OTM."""
    best, best_d = None, 9e9
    for r in rows:
        d = r.get("delta")
        if d is None or d != d or not r.get("strike"):
            continue
        ad = abs(float(d))
        if ad <= 0.01 or ad >= 0.6:
            continue
        diff = abs(ad - target)
        if diff < best_d:
            best, best_d = r, diff
    return best


def _wing(rows: list, short_strike: float, direction: int, min_width: float) -> dict | None:
    """First strike at least min_width beyond the short strike (direction
    +1 = higher, -1 = lower) with a real quote."""
    cands = [r for r in rows if r.get("strike")
             and (r["strike"] - short_strike) * direction >= min_width
             and (_mid(r) > 0 or (r.get("bid") or 0) >= 0)]
    if not cands:
        return None
    return min(cands, key=lambda r: (r["strike"] - short_strike) * direction)


def _strangle_bp(spot: float, put_k: float, call_k: float, credit: float) -> float:
    def side(strike, otm):
        return max(0.20 * spot - otm, 0.10 * strike)
    p = side(put_k, max(spot - put_k, 0))
    c = side(call_k, max(call_k - spot, 0))
    return round((max(p, c) + credit) * 100, 0)


def build_strategies(spot: float, em: float, calls: list, puts: list,
                     earnings_inside: bool, atm_call: dict, atm_put: dict,
                     one_sigma: float | None = None) -> list:
    """Every structure worth evaluating, best-first, with risk clearly
    labeled. Never hides max loss; never leads with undefined risk when the
    setup argues for defined (earnings inside the window, or a wide market).
    `em` is the ATM STRADDLE price (used for strike selection / coverage);
    `one_sigma` is the true 1σ move S·σ·√T used for probability math. When
    IV is missing, 1σ falls back to straddle/1.25 (the BS relationship),
    still labeled as an estimate via pop_basis."""
    out = []
    if one_sigma is None or one_sigma <= 0:
        one_sigma = em / 1.25 if em > 0 else None
    sp = _by_delta(puts, 0.18, "put")
    sc_ = _by_delta(calls, 0.18, "call")
    # Fallback to EM-based strikes when deltas are missing (thin 0DTE wings).
    if sp is None and em > 0:
        below = [r for r in puts if r.get("strike") and r["strike"] <= spot - em]
        sp = max(below, key=lambda r: r["strike"]) if below else None
    if sc_ is None and em > 0:
        above = [r for r in calls if r.get("strike") and r["strike"] >= spot + em]
        sc_ = min(above, key=lambda r: r["strike"]) if above else None

    # ── Short strangle (UNDEFINED risk) ─────────────────────────────────
    if sp is not None and sc_ is not None and _mid(sp) > 0 and _mid(sc_) > 0:
        credit = _mid(sp) + _mid(sc_)
        pk, ck = float(sp["strike"]), float(sc_["strike"])
        dp = abs(float(sp.get("delta") or 0)) or None
        dc = abs(float(sc_.get("delta") or 0)) or None
        pop = round((1 - (dp + dc)) * 100, 0) if (dp and dc) else None
        out.append({
            "kind": "short_strangle", "risk": "undefined",
            "put_strike": pk, "call_strike": ck,
            "credit": round(credit, 2),
            "be_low": round(pk - credit, 2), "be_high": round(ck + credit, 2),
            "max_profit": round(credit * 100, 0),
            "bp": _strangle_bp(spot, pk, ck, credit),
            "pop": pop, "pop_basis": "delta",
            "em_coverage": round(min(spot - pk, ck - spot) / em, 2) if em > 0 else None,
            "put_dist_pct": round((spot - pk) / spot * 100, 1),
            "call_dist_pct": round((ck - spot) / spot * 100, 1),
            "exit_target": round(credit * 0.5, 2),
            "stop_level": round(credit * 2.0, 2),
        })

    # ── Iron condor (DEFINED) — the strangle with wings ─────────────────
    if sp is not None and sc_ is not None:
        step = max(spot * 0.01, 2.5)
        lp = _wing(puts, float(sp["strike"]), -1, step)
        lc = _wing(calls, float(sc_["strike"]), +1, step)
        if lp is not None and lc is not None:
            credit = (_mid(sp) - _mid(lp)) + (_mid(sc_) - _mid(lc))
            width = max(float(sp["strike"]) - float(lp["strike"]),
                        float(lc["strike"]) - float(sc_["strike"]))
            if credit > 0 and width > credit:
                dp = abs(float(sp.get("delta") or 0)) or None
                dc = abs(float(sc_.get("delta") or 0)) or None
                out.append({
                    "kind": "iron_condor", "risk": "defined",
                    "put_strike": float(sp["strike"]), "put_wing": float(lp["strike"]),
                    "call_strike": float(sc_["strike"]), "call_wing": float(lc["strike"]),
                    "credit": round(credit, 2),
                    "max_profit": round(credit * 100, 0),
                    "max_loss": round((width - credit) * 100, 0),
                    "ror": round(credit / (width - credit) * 100, 0),
                    "be_low": round(float(sp["strike"]) - credit, 2),
                    "be_high": round(float(sc_["strike"]) + credit, 2),
                    "pop": round((1 - (dp + dc)) * 100, 0) if (dp and dc) else None,
                    "pop_basis": "delta",
                })

    # ── Credit spreads (DEFINED), each side ──────────────────────────────
    for side, rows, sr in (("put_credit_spread", puts, sp), ("call_credit_spread", calls, sc_)):
        if sr is None:
            continue
        step = max(spot * 0.005, 1.0)
        lw = _wing(rows, float(sr["strike"]), -1 if side.startswith("put") else +1, step)
        if lw is None:
            continue
        credit = _mid(sr) - _mid(lw)
        width = abs(float(sr["strike"]) - float(lw["strike"]))
        if credit <= 0 or width <= credit:
            continue
        d = abs(float(sr.get("delta") or 0)) or None
        be = float(sr["strike"]) - credit if side.startswith("put") else float(sr["strike"]) + credit
        out.append({
            "kind": side, "risk": "defined",
            "short_strike": float(sr["strike"]), "long_strike": float(lw["strike"]),
            "credit": round(credit, 2),
            "max_profit": round(credit * 100, 0),
            "max_loss": round((width - credit) * 100, 0),
            "ror": round(credit / (width - credit) * 100, 0),
            "be": round(be, 2),
            "pop": round((1 - d) * 100, 0) if d else None,
            "pop_basis": "delta",
        })

    # ── Iron fly (DEFINED) — short the ATM straddle, buy EM wings ───────
    if atm_call is not None and atm_put is not None and em > 0:
        k = float(atm_call.get("strike") or 0)
        straddle = _mid(atm_call) + _mid(atm_put)
        wp = _wing(puts, k, -1, em * 0.9)
        wc = _wing(calls, k, +1, em * 0.9)
        if straddle > 0 and wp is not None and wc is not None:
            credit = straddle - _mid(wp) - _mid(wc)
            width = max(k - float(wp["strike"]), float(wc["strike"]) - k)
            if credit > 0 and width > credit:
                # POP = P(close inside ±credit of the short strike) under a
                # normal move: 2·Φ(credit/1σ) − 1. v3.64 fix: the divisor is
                # the true 1σ move (S·σ·√T), NOT the straddle — the straddle
                # is ≈1.25σ, so using it understated POP by ~8-10 points.
                pop = None
                if one_sigma and one_sigma > 0:
                    pop = round((2 * _norm_cdf(credit / one_sigma) - 1) * 100, 0)
                out.append({
                    "kind": "iron_fly", "risk": "defined",
                    "short_strike": k,
                    "put_wing": float(wp["strike"]), "call_wing": float(wc["strike"]),
                    "credit": round(credit, 2),
                    "max_profit": round(credit * 100, 0),
                    "max_loss": round((width - credit) * 100, 0),
                    "ror": round(credit / (width - credit) * 100, 0),
                    "be_low": round(k - credit, 2), "be_high": round(k + credit, 2),
                    "pop": pop, "pop_basis": "one_sigma",
                })

    # ── Cash-secured put / covered call quick lines ──────────────────────
    if sp is not None and _mid(sp) > 0:
        pk = float(sp["strike"])
        d = abs(float(sp.get("delta") or 0)) or None
        out.append({"kind": "csp", "risk": "defined",
                    "short_strike": pk, "credit": round(_mid(sp), 2),
                    "max_profit": round(_mid(sp) * 100, 0),
                    "bp": round(pk * 100, 0), "be": round(pk - _mid(sp), 2),
                    "pop": round((1 - d) * 100, 0) if d else None,
                    "pop_basis": "delta",
                    "yield_pct": round(_mid(sp) / pk * 100, 2)})
    if sc_ is not None and _mid(sc_) > 0:
        ck = float(sc_["strike"])
        d = abs(float(sc_.get("delta") or 0)) or None
        out.append({"kind": "covered_call", "risk": "defined",
                    "short_strike": ck, "credit": round(_mid(sc_), 2),
                    "max_profit": round(_mid(sc_) * 100, 0),
                    "be": round(ck + _mid(sc_), 2),
                    "pop": round((1 - d) * 100, 0) if d else None,
                    "pop_basis": "delta",
                    "yield_pct": round(_mid(sc_) / spot * 100, 2)})

    # Suggested order: earnings inside the window (or very expensive stock)
    # → lead with defined risk; otherwise richness order with the strangle
    # first as the reference structure.
    def rank(s):
        pref = 0
        if s["kind"] == "short_strangle":
            pref = 0 if not (earnings_inside or spot > 400) else 5
        elif s["kind"] == "iron_condor":
            pref = 1 if not earnings_inside else 0
        elif s["kind"] == "iron_fly":
            pref = 2
        elif s["kind"].endswith("credit_spread"):
            pref = 3
        else:
            pref = 4
        return pref
    out.sort(key=rank)
    return out


# ── Per-symbol analysis ──────────────────────────────────────────────────────

def analyze_symbol(symbol: str, chain_payload: dict, brow: dict | None,
                   now: datetime | None = None) -> dict | None:
    """One row of the juice board from a single ranged chain call."""
    full = chain_payload or {}
    spot = (full.get("underlying") or {}).get("last")
    if not spot:
        return None
    spot = float(spot)
    if spot < MIN_PRICE:
        return None
    n = now or _now_et()
    exps = []
    for e in (full.get("expirations") or []):
        try:
            ed = date.fromisoformat(str(e)[:10])
        except (TypeError, ValueError):
            continue
        if 0 <= (ed - n.date()).days <= MAX_DTE:
            exps.append(ed)
    if not exps:
        return None
    expiry = min(exps)
    dte, dte_frac = _dte_days(expiry, n)
    chain = (full.get("chains") or {}).get(expiry.isoformat()) or {}
    calls = chain.get("calls") or []
    puts = chain.get("puts") or []
    if not calls or not puts:
        return None
    atm_call = min(calls, key=lambda r: abs((r.get("strike") or 0) - spot))
    atm_put = min(puts, key=lambda r: abs((r.get("strike") or 0) - spot))
    c_mid, p_mid = _mid(atm_call), _mid(atm_put)
    if c_mid <= 0 or p_mid <= 0:
        return None
    straddle = c_mid + p_mid
    straddle_pct = straddle / spot * 100.0
    ivs = [float(r["iv"]) for r in (atm_call, atm_put) if r.get("iv")]
    atm_iv = sum(ivs) / len(ivs) if ivs else None

    def spread_pct(row):
        b, a = float(row.get("bid") or 0), float(row.get("ask") or 0)
        m = (b + a) / 2
        return (a - b) / m * 100.0 if (b > 0 and a > 0 and m > 0) else None
    spreads = [s for s in (spread_pct(atm_call), spread_pct(atm_put)) if s is not None]
    spr = round(sum(spreads) / len(spreads), 1) if spreads else None

    vol_c = int(atm_call.get("volume") or 0)
    vol_p = int(atm_put.get("volume") or 0)
    oi_c = int(atm_call.get("openInterest") or 0)
    oi_p = int(atm_put.get("openInterest") or 0)
    tot_vol = sum(int(r.get("volume") or 0) for r in calls + puts)
    tot_oi = sum(int(r.get("openInterest") or 0) for r in calls + puts)
    vol_oi = round(tot_vol / tot_oi, 2) if tot_oi > 0 else None

    brow = brow or {}
    hv = brow.get("rvol")            # 20d realized vol, annualized %
    iv_vs_hv = round(atm_iv * 100.0 / hv, 2) if (atm_iv and hv) else None
    ivr = ivp = None
    ivr_src = None
    if _IV_RANK_FN is not None and atm_iv:
        try:
            rk = _IV_RANK_FN(symbol, atm_iv) or {}
            ivr, ivp = rk.get("iv_rank"), rk.get("iv_pct")
            if ivr is not None:
                ivr_src = "iv_history"   # true IV rank from stored IV30 history
        except Exception:
            pass
    if ivr is None:
        ivr = brow.get("rvol_rank")      # HV-rank proxy when no IV history yet
        if ivr is not None:
            ivr_src = "hv_proxy"

    days_to_earn = brow.get("days_to_earnings")
    next_earn = brow.get("next_earnings")
    earnings_inside = bool(days_to_earn is not None and 0 <= days_to_earn <= dte)

    sup = res = None
    try:
        piv = _PIVOTS_FN(symbol, spot) if _PIVOTS_FN else None
        if piv:
            sup = (piv.get("support") or [None])[0]
            res = (piv.get("resistance") or [None])[0]
    except Exception:
        pass

    # True 1σ move for probability math (distinct from the straddle, which
    # is the market's own price for the move — ≈1.25σ under BS). Intraday
    # 0DTE floors at half a calendar day, matching the EM engine.
    one_sig = one_sigma_move(spot, atm_iv, max(dte, 0.5)) if atm_iv else None
    strategies = build_strategies(spot, straddle, calls, puts, earnings_inside,
                                  atm_call, atm_put, one_sigma=one_sig)

    # ── Juice Score ──────────────────────────────────────────────────────
    reasons, flags = [], []
    prem_per_day = straddle_pct / max(dte_frac, 0.05)
    s_rich = min(prem_per_day / 2.5, 1.0) * 25
    if prem_per_day >= 1.2:
        reasons.append(f"{straddle_pct:.1f}% straddle with {dte_frac:.1f}d left")
    if iv_vs_hv:
        s_rich += max(0.0, min((iv_vs_hv - 0.9) / 0.5, 1.0)) * 15
        if iv_vs_hv >= 1.25:
            reasons.append(f"IV {iv_vs_hv}× realized")
    s_liq = min((oi_c + oi_p) / 2000.0, 1.0) * 10 + min((vol_c + vol_p) / 2000.0, 1.0) * 7
    if spr is not None:
        s_liq += max(0.0, min((8.0 - spr) / 6.0, 1.0)) * 8
        if spr > 5:
            flags.append(f"wide markets ({spr}% spread) — hard to exit")
    s_act = min((vol_oi or 0) / 1.0, 1.0) * 10
    if vol_oi and vol_oi >= 1.5:
        reasons.append(f"{vol_oi}× volume/OI")
    s_struct = 0.0
    strangle = next((s for s in strategies if s["kind"] == "short_strangle"), None)
    if strangle:
        if sup and sup.get("price") and sup["price"] > strangle["be_low"]:
            s_struct += 7
            reasons.append(f"support {sup['price']} above put BE")
        if res and res.get("price") and res["price"] < strangle["be_high"]:
            s_struct += 8
            reasons.append(f"resistance {res['price']} below call BE")
    s_ctx = (4 if dte <= 1 else 2 if dte <= 3 else 0)
    if not earnings_inside:
        s_ctx += 3
    else:
        flags.append(f"earnings {next_earn or ''} INSIDE the window — IV is high for a reason; defined risk preferred".strip())
    if ivr is not None and ivr >= 60:
        s_ctx += 3
        reasons.append(f"vol rank {int(ivr)}")
    if spot > 400:
        flags.append("expensive underlying — undefined risk ties up heavy buying power")
    score = int(round(min(s_rich + s_liq + s_act + s_struct + s_ctx, 100)))

    return {
        "symbol": symbol, "company": brow.get("company"), "tag": brow.get("tag") or "",
        "spot": round(spot, 2), "expiry": expiry.isoformat(), "dte": dte,
        "dte_frac": round(dte_frac, 2),
        "atm_strike": atm_call.get("strike"),
        "atm_iv": round(atm_iv, 4) if atm_iv else None,
        "iv_rank": ivr, "iv_rank_src": ivr_src, "iv_pct": ivp,
        "hv20": hv, "iv_vs_hv": iv_vs_hv,
        # em_* here IS the ATM straddle (the UI labels the column "Straddle").
        "em_dollars": round(straddle, 2), "em_pct": round(straddle_pct, 2),
        "one_sigma": round(one_sig, 2) if one_sig else None,
        "call_mid": round(c_mid, 2), "put_mid": round(p_mid, 2),
        "straddle": round(straddle, 2),
        "call_bid": atm_call.get("bid"), "call_ask": atm_call.get("ask"),
        "put_bid": atm_put.get("bid"), "put_ask": atm_put.get("ask"),
        "call_vol": vol_c, "put_vol": vol_p, "call_oi": oi_c, "put_oi": oi_p,
        "total_vol": tot_vol, "total_oi": tot_oi, "vol_oi": vol_oi,
        "spread_pct": spr,
        "prem_per_day": round(prem_per_day, 2),
        "next_earnings": next_earn, "days_to_earnings": days_to_earn,
        "earnings_inside": earnings_inside,
        "support": sup, "resistance": res,
        "score": score, "reasons": reasons[:4], "flags": flags,
        "groups": {"richness": round(s_rich), "liquidity": round(s_liq),
                   "activity": round(s_act), "structure": round(s_struct),
                   "context": round(s_ctx)},
        "strategies": strategies,
    }


# ── Scan worker + snapshot ───────────────────────────────────────────────────

_LOCK = threading.Lock()
_STATE: dict = {"rows": [], "as_of": None, "scanning": False, "market_open": False,
                "last_req": 0.0, "universe": 0, "scanned": 0, "error": None,
                "thread": None}


def _stage1_candidates() -> list:
    """Rank the board for premium-selling potential — no API cost. High
    realized-vol rank, earnings soon, and big day moves all mean fat
    same-week premium."""
    board = (_BOARD_GETTER() if _BOARD_GETTER else {}) or {}
    rows = [r for r in (board.get("rows") or [])
            if r.get("symbol")
            and (r.get("last") or 0) >= MIN_PRICE
            and not ((r.get("market_cap") or 0) and r["market_cap"] < MIN_MARKET_CAP)
            and (r.get("avg_volume") or 0) >= 1_000_000]
    with _LOCK:
        _STATE["universe"] = len(rows)

    def key(r):
        s = float(r.get("rvol_rank") or 0)
        de = r.get("days_to_earnings")
        if de is not None and 0 <= de <= MAX_DTE:
            s += 75          # earnings inside the window = the premium event
        elif de is not None and 0 <= de <= 7:
            s += 25
        s += min(abs(r.get("change") or 0) * 6, 30)
        return -s
    rows.sort(key=key)
    return rows[:STAGE2_BUDGET]


def _scan(sc) -> None:
    n = _now_et()
    frm = n.date().isoformat()
    to = (n.date() + timedelta(days=MAX_DTE)).isoformat()
    cands = _stage1_candidates()
    results = []

    def one(r):
        sym = str(r["symbol"]).upper()
        try:
            chain = sc.get_option_chain(sym, expiration=frm, to_date=to, strike_count=40)
            return analyze_symbol(sym, chain, r)
        except Exception:
            return None
    # 4 workers, not 6 — the Schwab client hard-caps at 110 req/min across
    # the whole app; a gentler burst leaves room for the radar + browsing.
    with ThreadPoolExecutor(max_workers=4) as ex:
        for res in ex.map(one, cands):
            if res:
                results.append(res)

    # NEVER wipe a good board because one cycle got rate-limited (chain
    # calls return None when the client's 110/min budget is exhausted).
    # Merge: fresh rows replace their symbol; symbols that didn't come back
    # this cycle are carried over marked stale, expiring after ROW_MAX_AGE.
    now_ts = time.time()
    for r in results:
        r["_ts"] = now_ts
        r["stale"] = False
    fresh_syms = {r["symbol"] for r in results}
    with _LOCK:
        prev = _STATE["rows"]
        carried = 0
        for old in prev:
            if (old["symbol"] not in fresh_syms
                    and now_ts - (old.get("_ts") or 0) < ROW_MAX_AGE
                    and old.get("expiry", "") >= frm):
                o = dict(old)
                o["stale"] = True
                results.append(o)
                carried += 1
        results.sort(key=lambda r: -r["score"])
        note = None
        if cands and not fresh_syms:
            note = "rate-limited — showing the last scan; refreshing"
        elif carried and len(fresh_syms) < len(cands) * 0.5:
            note = f"partial refresh ({len(fresh_syms)}/{len(cands)}) — quota shared with the radar"
        _STATE.update({"rows": results, "as_of": _now_et().isoformat(),
                       "scanned": len(cands), "error": note})


def _loop() -> None:
    try:
        while True:
            with _LOCK:
                idle = time.time() - _STATE["last_req"] > WORKER_IDLE_SECS
            if idle or not _market_open():
                break
            sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
            if sc is None:
                with _LOCK:
                    _STATE["error"] = "Schwab not connected"
                break
            t0 = time.time()
            try:
                _scan(sc)
            except Exception as exc:  # noqa: BLE001
                with _LOCK:
                    _STATE["error"] = str(exc)
            time.sleep(max(10.0, CYCLE_SECS - (time.time() - t0)))
    finally:
        with _LOCK:
            _STATE["scanning"] = False
            _STATE["thread"] = None


def snapshot() -> dict:
    with _LOCK:
        _STATE["last_req"] = time.time()
        _STATE["market_open"] = _market_open()
        if _STATE["market_open"] and not _STATE["scanning"]:
            _STATE["scanning"] = True
            t = threading.Thread(target=_loop, name="juice", daemon=True)
            _STATE["thread"] = t
            t.start()
        return {"rows": list(_STATE["rows"]), "as_of": _STATE["as_of"],
                "scanning": _STATE["scanning"], "market_open": _STATE["market_open"],
                "universe": _STATE["universe"], "scanned": _STATE["scanned"],
                "error": _STATE["error"]}
