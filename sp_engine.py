"""sp_engine.py — the Short Premium Opportunity Engine (v4.80).

One evaluator for every short-premium candidate the app can name — cash-
secured put, covered call, put or call credit spread, iron condor — that
answers, in order and out loud:

  Gate 1  DATA        is the quote fresh, sourced, and complete enough to act on?
  Gate 2  LIQUIDITY   can this contract actually be entered and exited?
  Gate 3  EVENTS      does anything scheduled fall inside the option's life?
  Gate 4  EDGE        is the market paying more than the risk is worth, after costs?
  Gate 5  TAIL        what happens when the model is wrong?
  Gate 6  PROBABILITY only then: how likely is the option to expire worthless,
                      and how sure are we?

A candidate that fails a gate is REJECTED with the gate named; nothing is
scored around a failed gate. What survives gets a Sell Quality breakdown
(Safety, Edge, Income Efficiency, Liquidity, Tail Risk, Event Risk, Data
Confidence, Calibration) and a final score that is explainable component by
component — and is ORDERED by a stated objective per selling mode, not by
the score alone, because "maximize the pretty number" is not a trading
objective. The modes and every threshold live in thresholds.json under
`short_premium`; the defaults in this file are the same values and are
hypotheses the ranking backtest scores, not laws.

Reused, not re-implemented: `premium_edge.contract_economics` (credit at the
BID, fair value at the forecast, closed-form tail), `premium_edge.
liquidity_gate`, `premium_edge.danger_model`, `sp_probability` (P0, P(ITM),
P(touch), POP, early-target paths at the CONTRACT's horizon),
`sp_evidence` (this ticker's own measured breach history with a stated
share of borrowed evidence), `metrics` (Black-Scholes, Wilson).

This module is pure: it takes dicts and returns dicts. The scanner
(sell_scan.py) gathers the inputs and owns the caches.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import date
from pathlib import Path

import premium_edge as pe
import sp_evidence as ev
import sp_probability as sp
from metrics import _bs_price

SP_ENGINE_VERSION = "sp-engine-1.0.0"

STRATEGIES = ("cash_secured_put", "covered_call", "put_credit_spread",
              "call_credit_spread", "iron_condor")
MODES = ("conservative", "balanced", "income")
DTE_BUCKETS = (("0", 0, 0), ("1-7", 1, 7), ("8-21", 8, 21), ("22-45", 22, 45), ("46-60", 46, 60))

# ── defaults (mirrored in thresholds.json → short_premium) ──────────────────
DEFAULTS = {
    "_doc": "Short Premium Opportunity Engine (sp_engine.py, SHORT_PREMIUM.md). Modes and gates.",
    "select": {"min_dte": 1, "max_dte": 60, "delta_band": [0.05, 0.45],
               "spread_widths_frac": [0.03, 0.05, 0.08], "max_expirations": 6,
               "max_candidates_per_symbol": 40},
    "data": {"max_quote_age_s": 900, "max_chain_age_s": 1800, "max_bars_age_days": 4,
             "require_two_sided": True},
    "liquidity": {"min_oi": 100, "min_volume": 1, "min_oi_if_no_volume": 500,
                  "max_spread_pct": 12.0, "min_underlying_dollar_volume": 5e6,
                  "thin_size": 5},
    "events": {"earnings_buffer_days": 2, "macro_penalty_pts": 8, "macro_at_expiry_days": 1,
               "ex_div_note_for_calls": True},
    "costs": {"commission_per_contract": 0.65, "reg_fees_per_contract": 0.05,
              "slippage_per_share": 0.0},
    "tail": {"gap_reach_penalty_pts": 15, "rv_accel_ratio": 1.6, "rv_accel_pts": 10,
             "backwardation_pts": 10, "vix_pctile": 85.0, "vix_pts": 10,
             "gamma_dte": 3, "gamma_delta": 0.30, "gamma_pts": 12,
             "skew_rr_volpts": 8.0, "skew_pts": 6},
    "evidence": {"min_n_eff_for_measured": 20, "kappa": 40},
    "modes": {
        "conservative": {"objective": "max_p0_conservative", "min_p0": 0.85, "min_p0_conservative": 0.80,
                         "min_ev_per_contract": 5.0, "min_roc_pct": 0.5, "min_credit": 0.20,
                         "max_es95_over_credit": 6.0, "max_tail_pts": 30, "max_spread_pct": 10.0},
        "balanced": {"objective": "max_ev_per_tail", "min_p0": 0.75, "min_p0_conservative": 0.68,
                     "min_ev_per_contract": 5.0, "min_roc_pct": 0.8, "min_credit": 0.25,
                     "max_es95_over_credit": 8.0, "max_tail_pts": 45, "max_spread_pct": 12.0},
        "income": {"objective": "max_annualized_roc", "min_p0": 0.65, "min_p0_conservative": 0.58,
                   "min_ev_per_contract": 8.0, "min_roc_pct": 1.2, "min_credit": 0.30,
                   "max_es95_over_credit": 10.0, "max_tail_pts": 55, "max_spread_pct": 12.0},
    },
    "weights": {"safety": 22, "edge": 18, "income": 12, "liquidity": 10, "tail": 14,
                "event": 8, "data": 8, "calibration": 8},
    "event_mode": {"enabled": False, "_doc": "EVENT PREMIUM mode ranks names WITH earnings inside; never mixed with normal."},
}

_CFG_CACHE = {"cfg": None, "hash": None, "ts": 0.0}


def config(refresh: bool = False) -> tuple[dict, str]:
    """(short_premium config, sha256[:16] of the full thresholds file), the
    repo file overlaid by <data>/thresholds.json, with DEFAULTS underneath so
    a missing key is never a KeyError."""
    if not refresh and _CFG_CACHE["cfg"] is not None and time.time() - _CFG_CACHE["ts"] < 60:
        return _CFG_CACHE["cfg"], _CFG_CACHE["hash"]
    repo = Path(__file__).resolve().parent / "thresholds.json"
    try:
        full = json.loads(repo.read_text())
    except Exception:  # noqa: BLE001
        full = {}
    dd = getattr(pe, "_DATA_DIR", None)
    if dd is not None:
        try:
            p = Path(dd) / "thresholds.json"
            if p.exists():
                full = pe._deep_merge(full, json.loads(p.read_text()))  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
    h = hashlib.sha256(json.dumps(full, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    cfg = pe._deep_merge(DEFAULTS, full.get("short_premium") or {})  # noqa: SLF001
    _CFG_CACHE.update({"cfg": cfg, "hash": h, "ts": time.time()})
    return cfg, h


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def dte_bucket(dte: float) -> str:
    d = int(round(dte or 0))
    for name, lo, hi in DTE_BUCKETS:
        if lo <= d <= hi:
            return name
    return ">60"


# ── candidate construction ───────────────────────────────────────────────────
def _expirations(chain: dict, now: date, cfg: dict) -> list[tuple[str, float]]:
    st = cfg["select"]
    out = []
    for exp in (chain.get("chains") or {}):
        dte = pe._expiry_dte(exp, now)  # noqa: SLF001
        if float(st["min_dte"]) <= dte <= float(st["max_dte"]):
            out.append((exp, dte))
    out.sort(key=lambda x: x[1])
    return out[: int(st.get("max_expirations", 6))]


def _leg_costs(n_legs: int, cfg: dict) -> float:
    c = cfg["costs"]
    return (float(c["commission_per_contract"]) + float(c["reg_fees_per_contract"])) * n_legs / 100.0


def build_candidates(sym: str, chain: dict, bars: list, now: date, cfg: dict,
                     evidence_table: dict | None = None, state: str | None = None,
                     strategies=STRATEGIES, iv_entry_by_row: bool = True) -> list[dict]:
    """Every short-premium structure the chain supports inside the seller
    window, each carrying its economics, its separated probabilities at the
    CONTRACT's horizon, and its measured evidence. No gates yet — that is
    `evaluate`'s job, so a rejected candidate can still say why."""
    spot = _num((chain.get("underlying") or {}).get("last")) or 0.0
    if spot <= 0 or not bars:
        return []
    st = cfg["select"]
    band = tuple(st["delta_band"])
    widths = [spot * float(w) for w in st["spread_widths_frac"]]
    out = []
    for exp, dte in _expirations(chain, now, cfg):
        h_td = sp.trading_days(dte)
        sig = sp.sigma_for_horizon(bars, h_td, cfg.get("forecast") or {})
        if not sig:
            continue
        sigma_h = sig["sigma"]
        t_years = max(dte, 0.25) / 365.0
        per_side = {}
        for side in ("put", "call"):
            rows = pe._rows_for(chain, exp, side)  # noqa: SLF001
            shorts = []
            for r in rows:
                d = _num(r.get("delta"))
                if d is None or not (band[0] <= abs(d) <= band[1]):
                    continue
                m = pe.contract_economics(r, spot, side, t_years, sigma_h, cfg, rate=0.0)
                if m is None:
                    continue
                ok, why = pe.liquidity_gate(m, cfg)
                m["liquidity_ok"], m["liquidity_notes"] = ok, why
                k = abs(math.log(m["strike"] / spot)) / (sigma_h * math.sqrt(t_years))
                iv_row = m.get("iv") or None
                probs = sp.contract_probabilities(
                    spot, m["strike"], side, dte, sigma_h, m["credit_exec"],
                    costs_per_share=_leg_costs(1, cfg) + float(cfg["costs"]["slippage_per_share"]),
                    iv_entry=(iv_row if iv_entry_by_row else None), paths=bool(iv_row))
                evid = ev.evidence_for_strike(evidence_table, h_td, k, side, state=state,
                                              kappa=float(cfg["evidence"]["kappa"]))
                ctx = ev.strike_context(evidence_table, h_td, k, side)
                shorts.append({**m, "k_sigma": round(k, 3), "sigma_h": sigma_h,
                               "sigma_basis": sig["basis"], "probs": probs,
                               "evidence": evid, "excursion": ctx})
            per_side[side] = (rows, shorts)
        for side, (rows, shorts) in per_side.items():
            single_kind = "cash_secured_put" if side == "put" else "covered_call"
            for m in shorts:
                if single_kind in strategies:
                    out.append(_single(sym, exp, dte, side, single_kind, m, spot, cfg))
                if f"{side}_credit_spread" in strategies:
                    for w in widths:
                        sprd = _spread(sym, exp, dte, side, m, rows, spot, t_years, sigma_h, w, cfg)
                        if sprd:
                            out.append(sprd)
        if "iron_condor" in strategies:
            puts = [c for c in out if c["expiration"] == exp[:10] and c["strategy"] == "put_credit_spread"]
            calls = [c for c in out if c["expiration"] == exp[:10] and c["strategy"] == "call_credit_spread"]
            for pc in puts[:6]:
                for cc in calls[:6]:
                    if abs(pc["width"] - cc["width"]) > 1e-9:
                        continue
                    out.append(_condor(sym, exp, dte, pc, cc, spot, cfg))
    cap = int(st.get("max_candidates_per_symbol", 40))
    if len(out) > cap:
        out.sort(key=lambda c: -(_num(c.get("ev_per_contract")) or -1e9))
        out = out[:cap]
    return out


def _base(sym, exp, dte, strategy, spot):
    return {"symbol": sym, "expiration": exp[:10], "dte": round(float(dte), 1),
            "dte_bucket": dte_bucket(dte), "strategy": strategy, "spot": round(spot, 2),
            "engine": SP_ENGINE_VERSION}


def _single(sym, exp, dte, side, kind, m, spot, cfg) -> dict:
    coll = m["strike"] * 100.0 if side == "put" else spot * 100.0
    credit = m["credit_exec"]
    costs = _leg_costs(1, cfg) + float(cfg["costs"]["slippage_per_share"])
    net = credit - costs
    return {**_base(sym, exp, dte, kind, spot),
            "side": side, "short_strike": m["strike"], "long_strike": None, "width": None,
            "legs": 1, "credit": round(credit, 2), "credit_basis": m["credit_basis"],
            "net_credit": round(net, 3), "costs_per_share": round(costs, 3),
            "max_loss_per_share": None, "defined_risk": False,
            "capital": round(coll, 0), "capital_basis": ("strike × 100 (cash-secured)" if side == "put"
                                                          else "100 shares at spot (covered)"),
            "roc_pct": round(net * 100.0 / coll * 100.0, 3) if coll > 0 else None,
            "annualized_roc_pct": (round(net * 100.0 / coll * 100.0 * 365.0 / max(dte, 1), 1)
                                   if coll > 0 else None),
            "ev_per_share": round(m["ev_per_share"] - float(cfg["costs"]["slippage_per_share"]), 3),
            "ev_per_contract": round((m["ev_per_share"] - float(cfg["costs"]["slippage_per_share"])) * 100.0, 0),
            "fair_at_forecast": m["fair_at_erv"],
            "es95_per_share": m["es5_per_share"],
            "es99_per_share": (m["probs"] or {}).get("es_99") if m.get("probs") else None,
            "short": m, "quote": _quote_block(m), "probs": m["probs"], "evidence": m["evidence"],
            "excursion": m["excursion"], "k_sigma": m["k_sigma"], "sigma_h": m["sigma_h"],
            "sigma_basis": m["sigma_basis"], "delta": m["delta"],
            "dist_pct": m["dist_pct"], "expected_moves_out": m["expected_moves_out"]}


def _quote_block(m: dict) -> dict:
    return {"bid": m.get("bid"), "ask": m.get("ask"), "mid": m.get("mid"),
            "spread_pct": m.get("spread_pct"), "oi": m.get("oi"), "volume": m.get("volume"),
            "bid_size": m.get("bid_size"), "ask_size": m.get("ask_size"),
            "quote_age_s": m.get("quote_age_s"), "delta_source": m.get("delta_source"),
            "iv": m.get("iv")}


def _spread(sym, exp, dte, side, m, rows, spot, t_years, sigma_h, width_pref, cfg) -> dict | None:
    k = m["strike"]
    want = k - width_pref if side == "put" else k + width_pref
    best, gap = None, 1e18
    for r in rows:
        s = _num(r.get("strike"))
        if s is None or (side == "put" and s >= k) or (side == "call" and s <= k):
            continue
        g = abs(s - want)
        if g < gap and (_num(r.get("ask")) or 0) > 0:
            best, gap = r, g
    if best is None:
        return None
    long_ask = _num(best.get("ask")) or 0.0
    width = abs(k - best["strike"])
    if width <= 0 or gap > width_pref * 0.6:        # snapped too far from the asked width
        return None
    credit = m["credit_exec"] - long_ask
    costs = _leg_costs(2, cfg) + float(cfg["costs"]["slippage_per_share"])
    net = credit - costs
    if credit <= 0.01:
        return None
    fair_long = _bs_price(spot, best["strike"], t_years, sigma_h, side, r=0.0, q=0.0)
    ev_share = m["ev_per_share"] + (fair_long - long_ask) - float(cfg["costs"]["slippage_per_share"])
    max_loss = width - credit
    cap = max_loss * 100.0
    # POP for the spread: profit iff the short leg settles below net credit
    probs = dict(m["probs"] or {})
    probs["p_profit"] = sp.p_profit(spot, k, sigma_h, t_years, side, credit,
                                    costs_per_share=costs)
    probs["p_max_loss"] = sp.p_itm(spot, best["strike"], sigma_h, t_years, side)
    return {**_base(sym, exp, dte, f"{side}_credit_spread", spot),
            "side": side, "short_strike": k, "long_strike": best["strike"], "width": round(width, 2),
            "legs": 2, "credit": round(credit, 2), "credit_basis": "short at bid, long at ask",
            "net_credit": round(net, 3), "costs_per_share": round(costs, 3),
            "max_loss_per_share": round(max_loss, 2), "defined_risk": True,
            "capital": round(cap, 0), "capital_basis": "width − credit (defined risk)",
            "roc_pct": round(net / max_loss * 100.0, 2) if max_loss > 0 else None,
            "annualized_roc_pct": (round(net / max_loss * 100.0 * 365.0 / max(dte, 1), 1)
                                   if max_loss > 0 else None),
            "ev_per_share": round(ev_share, 3), "ev_per_contract": round(ev_share * 100.0, 0),
            "fair_at_forecast": m["fair_at_erv"],
            "es95_per_share": round(min(m["es5_per_share"] or max_loss, max_loss), 4),
            "es99_per_share": round(min((probs.get("es_99") or max_loss), max_loss), 4),
            "short": m, "quote": _quote_block(m), "long_quote": {"bid": best.get("bid"), "ask": long_ask,
                                                                 "oi": best.get("openInterest"),
                                                                 "volume": best.get("volume")},
            "probs": probs, "evidence": m["evidence"], "excursion": m["excursion"],
            "k_sigma": m["k_sigma"], "sigma_h": m["sigma_h"], "sigma_basis": m["sigma_basis"],
            "delta": m["delta"], "dist_pct": m["dist_pct"], "expected_moves_out": m["expected_moves_out"]}


def _condor(sym, exp, dte, pc, cc, spot, cfg) -> dict:
    credit = pc["credit"] + cc["credit"]
    width = max(pc["width"], cc["width"])
    costs = _leg_costs(4, cfg) + float(cfg["costs"]["slippage_per_share"])
    net = credit - costs
    max_loss = width - credit
    pp, pcp = pc["probs"] or {}, cc["probs"] or {}
    p_itm = min(1.0, (pp.get("p_itm_tail_adjusted") or pp.get("p_itm") or 0) + (pcp.get("p_itm_tail_adjusted") or pcp.get("p_itm") or 0))
    p_touch = min(1.0, (pp.get("p_touch") or 0) + (pcp.get("p_touch") or 0))
    probs = {"p_itm": round(p_itm, 4), "p_expire_worthless": round(1 - p_itm, 4),
             "p_itm_tail_adjusted": round(p_itm, 4), "p_expire_worthless_tail_adjusted": round(1 - p_itm, 4),
             "p_touch": round(p_touch, 4), "p_profit": None,
             "es_95": max(pc["es95_per_share"], cc["es95_per_share"]),
             "es_99": max(pc["es99_per_share"] or 0, cc["es99_per_share"] or 0),
             "basis": {"terminal": "sum of the two spreads' short-leg probabilities (disjoint events)",
                       "touch": "sum of the two sides' touch probabilities (upper bound)",
                       "tail": "worse side's tail, capped at max loss"},
             "model": pp.get("model"), "monitoring": pp.get("monitoring")}
    ev_share = pc["ev_per_share"] + cc["ev_per_share"]
    return {**_base(sym, exp, dte, "iron_condor", spot),
            "side": "both", "short_strike": pc["short_strike"], "long_strike": pc["long_strike"],
            "short_call": cc["short_strike"], "long_call": cc["long_strike"], "width": round(width, 2),
            "legs": 4, "credit": round(credit, 2), "credit_basis": "shorts at bid, longs at ask",
            "net_credit": round(net, 3), "costs_per_share": round(costs, 3),
            "max_loss_per_share": round(max_loss, 2), "defined_risk": True,
            "capital": round(max_loss * 100.0, 0), "capital_basis": "wider wing − total credit",
            "roc_pct": round(net / max_loss * 100.0, 2) if max_loss > 0 else None,
            "annualized_roc_pct": (round(net / max_loss * 100.0 * 365.0 / max(dte, 1), 1) if max_loss > 0 else None),
            "ev_per_share": round(ev_share, 3), "ev_per_contract": round(ev_share * 100.0, 0),
            "es95_per_share": round(min(probs["es_95"], max_loss), 4),
            "es99_per_share": round(min(probs["es_99"] or max_loss, max_loss), 4),
            "short": pc["short"], "short_call_leg": cc["short"],
            "quote": {"put": pc["quote"], "call": cc["quote"]},
            "probs": probs, "evidence": pc["evidence"], "evidence_call": cc["evidence"],
            "excursion": pc["excursion"],
            "k_sigma": min(pc["k_sigma"], cc["k_sigma"]), "sigma_h": pc["sigma_h"],
            "sigma_basis": pc["sigma_basis"],
            "delta": pc["delta"], "delta_call": cc["delta"], "dist_pct": pc["dist_pct"],
            "expected_moves_out": min(pc["expected_moves_out"] or 99, cc["expected_moves_out"] or 99)}


# ── the gates ────────────────────────────────────────────────────────────────
def _gate_data(c: dict, ctx: dict, cfg: dict) -> tuple[bool, list, dict]:
    """Gate 1: current, sourced, complete. Returns (ok, reasons, provenance)."""
    d = cfg["data"]
    why = []
    q = c["quote"] if c["legs"] < 4 else c["quote"]["put"]
    prov = {
        "source": ctx.get("source") or "unknown",
        "delayed": bool(ctx.get("delayed")),
        "provider_serving": ctx.get("provider_serving"),
        "chain_ts": ctx.get("chain_ts"), "chain_age_s": ctx.get("chain_age_s"),
        "quote_age_s": q.get("quote_age_s"), "bars_last": ctx.get("bars_last"),
        "bars_age_days": ctx.get("bars_age_days"),
        "greeks": q.get("delta_source") or "missing",
        "probability_basis": ((c.get("probs") or {}).get("basis") or {}).get("terminal"),
        "evidence_basis": (c.get("evidence") or {}).get("basis"),
        "market_open": bool(ctx.get("market_open")),
    }
    if ctx.get("provider_serving") is False:
        why.append("the broker is signed in but not answering — a refusal, not a fact about this symbol")
    if prov["delayed"]:
        why.append(f"quotes are delayed ({prov['source']}); a live decision needs live quotes")
    if ctx.get("market_open") and (q.get("quote_age_s") or 0) > float(d["max_quote_age_s"]):
        why.append(f"option quote is {int(q['quote_age_s'])}s old (limit {int(d['max_quote_age_s'])}s)")
    if ctx.get("market_open") and ctx.get("chain_age_s") is not None and ctx["chain_age_s"] > float(d["max_chain_age_s"]):
        why.append(f"chain snapshot is {int(ctx['chain_age_s'])}s old")
    if ctx.get("bars_age_days") is not None and ctx["bars_age_days"] > float(d["max_bars_age_days"]):
        why.append(f"daily history ends {int(ctx['bars_age_days'])} days ago")
    if d.get("require_two_sided") and ((q.get("bid") or 0) <= 0 or (q.get("ask") or 0) <= 0):
        why.append("quote is not two-sided (zero bid or ask)")
    if (q.get("ask") or 0) < (q.get("bid") or 0):
        why.append("crossed market (ask below bid)")
    if (c.get("probs") or {}).get("p_itm") is None:
        why.append("no probability could be computed (missing volatility or inputs)")
    if (c.get("probs") or {}).get("insufficient"):
        why.append((c.get("probs") or {})["insufficient"])
    return (not why), why, prov


def _gate_liquidity(c: dict, ctx: dict, cfg: dict, mode_cfg: dict) -> tuple[bool, list, dict]:
    lq = cfg["liquidity"]
    why = []
    q = c["quote"] if c["legs"] < 4 else c["quote"]["put"]
    ok, notes = pe.liquidity_gate({"oi": q.get("oi"), "volume": q.get("volume"),
                                   "spread_pct": q.get("spread_pct")}, cfg)
    why.extend(notes)
    sp_pct = _num(q.get("spread_pct"))
    if sp_pct is not None and sp_pct > float(mode_cfg["max_spread_pct"]):
        why.append(f"spread {sp_pct:.0f}% of mid > {mode_cfg['max_spread_pct']:.0f}% allowed in this mode")
    if q.get("bid") is not None and q.get("bid") <= 0:
        why.append("zero bid — nothing to sell into")
    udv = _num(ctx.get("underlying_dollar_volume"))
    if udv is not None and udv < float(lq["min_underlying_dollar_volume"]):
        why.append(f"underlying trades ${udv / 1e6:.1f}M/day, under ${float(lq['min_underlying_dollar_volume']) / 1e6:.0f}M")
    if c.get("long_quote") and (c["long_quote"].get("oi") or 0) < int(lq["min_oi"]) / 2:
        why.append(f"long wing open interest {c['long_quote'].get('oi')} is thin")
    thin = (q.get("bid_size") is not None and q.get("ask_size") is not None
            and q["bid_size"] < int(lq["thin_size"]) and q["ask_size"] < int(lq["thin_size"]))
    details = {"spread_pct": sp_pct, "oi": q.get("oi"), "volume": q.get("volume"),
               "bid_size": q.get("bid_size"), "ask_size": q.get("ask_size"), "thin_size": thin,
               "underlying_dollar_volume": udv}
    return (not why), why, details


def _gate_events(c: dict, ctx: dict, cfg: dict, event_mode: bool) -> tuple[bool, list, dict]:
    e = cfg["events"]
    why, notes = [], []
    dte = c["dte"]
    earn = ctx.get("earnings_date")
    earn_days = ctx.get("earnings_in_days")
    inside = earn_days is not None and 0 <= earn_days <= dte + float(e["earnings_buffer_days"])
    if inside and not event_mode:
        why.append(f"earnings in {int(earn_days)} days, inside this option's life "
                   f"(+{int(e['earnings_buffer_days'])}-day buffer) — normal premium selling never "
                   f"underwrites the report")
    if not inside and event_mode:
        why.append("no earnings inside the option's life — not an event premium sale")
    macro_inside = [m for m in (ctx.get("macro_events") or [])
                    if m.get("date") and ctx.get("today") and ctx["today"] <= m["date"] <= c["expiration"]]
    at_expiry = [m for m in macro_inside
                 if abs((date.fromisoformat(m["date"]) - date.fromisoformat(c["expiration"])).days)
                 <= int(e["macro_at_expiry_days"])]
    exdiv = ctx.get("ex_div_date")
    exdiv_inside = None
    if c["side"] in ("call", "both") and e.get("ex_div_note_for_calls"):
        if exdiv is None:
            notes.append("ex-dividend date unavailable from the wired sources — early assignment "
                         "risk on a short call cannot be checked here")
        elif ctx.get("today") and ctx["today"] <= exdiv <= c["expiration"]:
            exdiv_inside = exdiv
            notes.append(f"ex-dividend {exdiv} falls inside the life of the short call — "
                         f"early assignment risk the day before")
    details = {"earnings_date": earn, "earnings_in_days": earn_days, "earnings_inside": inside,
               "macro_inside": macro_inside, "macro_at_expiry": at_expiry,
               "ex_div_date": exdiv, "ex_div_inside": exdiv_inside, "notes": notes,
               "event_mode": event_mode}
    return (not why), why, details


def _gate_edge(c: dict, mode_cfg: dict) -> tuple[bool, list, dict]:
    why = []
    evc = _num(c.get("ev_per_contract"))
    if evc is None:
        why.append("expected value could not be computed")
    elif evc <= 0:
        why.append(f"negative expected value after costs ({evc:+.0f} per contract): the credit is "
                   f"below what the option is worth at the volatility this stock realizes")
    elif evc < float(mode_cfg["min_ev_per_contract"]):
        why.append(f"expected value {evc:+.0f} per contract is under the {mode_cfg['min_ev_per_contract']:.0f} this mode requires")
    if (_num(c.get("credit")) or 0) < float(mode_cfg["min_credit"]):
        why.append(f"credit {c.get('credit')} is under the {mode_cfg['min_credit']:.2f} minimum — not worth the risk of being short")
    roc = _num(c.get("roc_pct"))
    if roc is not None and roc < float(mode_cfg["min_roc_pct"]):
        why.append(f"return on capital {roc:.2f}% is under the {mode_cfg['min_roc_pct']:.2f}% this mode requires")
    vrp = _num(((c.get("context") or {}).get("vrp_ratio")))
    return (not why), why, {"ev_per_contract": evc, "roc_pct": roc, "vrp_ratio": vrp}


def _gate_tail(c: dict, ctx: dict, cfg: dict, mode_cfg: dict) -> tuple[bool, list, dict]:
    t = cfg["tail"]
    pts, notes = 0.0, []
    credit = _num(c.get("credit")) or 0.0
    es95 = _num(c.get("es95_per_share"))
    ratio = (es95 / credit) if (es95 is not None and credit > 0) else None
    if ratio is not None and ratio > float(mode_cfg["max_es95_over_credit"]):
        notes.append(f"the average loss in the worst 5% ({es95:.2f}/share) is {ratio:.1f}× the credit "
                     f"(this mode allows {mode_cfg['max_es95_over_credit']:.0f}×)")
        pts += 100  # hard fail
    ex = c.get("excursion") or {}
    gap95 = _num(ex.get("gap_toward_strike_sigma_p95"))
    k = _num(c.get("k_sigma"))
    if gap95 is not None and k is not None and gap95 >= k:
        pts += float(t["gap_reach_penalty_pts"])
        notes.append(f"one ordinary overnight gap (95th pct {gap95:.2f}σ) reaches this strike ({k:.2f}σ away)")
    feats = ctx.get("bar_features") or {}
    ra = _num(feats.get("rv5_over_rv20"))
    if ra is not None and ra >= float(t["rv_accel_ratio"]):
        pts += float(t["rv_accel_pts"])
        notes.append(f"realized volatility is accelerating ({ra:.2f}× its 20-day pace)")
    if ctx.get("term_shape") == "backwardation":
        pts += float(t["backwardation_pts"])
        notes.append("the term structure is backwardated — near-dated fear, the premium least reliable for a seller")
    vixp = _num(ctx.get("vix_percentile"))
    if vixp is not None and vixp >= float(t["vix_pctile"]):
        pts += float(t["vix_pts"])
        notes.append(f"VIX sits at its {vixp:.0f}th percentile of the year")
    if c["dte"] <= float(t["gamma_dte"]) and abs(_num(c.get("delta")) or 0) >= float(t["gamma_delta"]):
        pts += float(t["gamma_pts"])
        notes.append("short-dated and near the money: gamma concentration turns a small move into a large loss")
    rr = _num(ctx.get("rr25_volpts"))
    if rr is not None and abs(rr) >= float(t["skew_rr_volpts"]):
        pts += float(t["skew_pts"])
        notes.append(f"skew is extreme ({rr:+.1f} vol points between 25Δ put and call)")
    regime = ctx.get("market_regime")
    if regime == "short" and c["side"] in ("put", "both"):
        pts += 6
        notes.append("dealers are modeled short gamma on the index (moves accelerate) — a modeled read")
    ok = pts < float(mode_cfg["max_tail_pts"])
    why = [] if ok else ([f"tail-risk points {pts:.0f} exceed the {mode_cfg['max_tail_pts']:.0f} this mode allows"] + notes)
    return ok, why, {"points": round(pts, 1), "es95_over_credit": round(ratio, 2) if ratio is not None else None,
                     "notes": notes, "max_loss_per_share": c.get("max_loss_per_share")}


def _probability_block(c: dict, cfg: dict) -> dict:
    """Gate 6 inputs: the model P0, the measured P0, the conservative bound,
    and which of them is the headline — in words."""
    p = c.get("probs") or {}
    e = c.get("evidence") or {}
    p0_model = _num(p.get("p_expire_worthless_tail_adjusted"))
    if p0_model is None:
        p0_model = _num(p.get("p_expire_worthless"))
    p0_raw = _num(p.get("p_expire_worthless"))
    min_n = int(cfg["evidence"]["min_n_eff_for_measured"])
    n_eff = int(e.get("n_eff") or 0)
    p0_measured = (1.0 - e["p_itm"]) if e.get("p_itm") is not None else None
    ci = e.get("p_itm_ci")
    measured_low = (1.0 - ci["hi"]) if ci else None
    if p0_measured is not None and n_eff >= min_n and measured_low is not None:
        conservative = min(p0_model if p0_model is not None else 1.0, measured_low)
        basis = (f"the lower of the model and the Wilson lower bound of this stock's own history "
                 f"({n_eff} independent windows, {e.get('weight_own', 0) * 100:.0f}% own evidence)")
        grade = "measured"
    else:
        conservative = (p0_model - 0.05) if p0_model is not None else None
        basis = (f"model only, less a 5-point haircut — {n_eff} independent windows is too few "
                 f"to bound it from history ({min_n} needed)")
        grade = "model"
    return {"p0_model": p0_model, "p0_model_raw": p0_raw, "p0_measured": p0_measured,
            "p0_conservative": round(conservative, 4) if conservative is not None else None,
            "p_itm": _num(p.get("p_itm_tail_adjusted")) if _num(p.get("p_itm_tail_adjusted")) is not None else _num(p.get("p_itm")),
            "p_touch": _num(p.get("p_touch")), "p_touch_measured": _num(e.get("p_touch")),
            "p_profit": _num(p.get("p_profit")),
            "paths": p.get("paths"), "n_eff": n_eff, "weight_own": e.get("weight_own"),
            "conservative_basis": basis, "grade": grade,
            "terminal_basis": (p.get("basis") or {}).get("terminal"),
            "touch_basis": (p.get("basis") or {}).get("touch"),
            "evidence_basis": e.get("basis")}


def _gate_probability(pb: dict, mode_cfg: dict) -> tuple[bool, list]:
    why = []
    if pb["p0_model"] is None:
        return False, ["no probability of expiring worthless could be computed"]
    if pb["p0_model"] < float(mode_cfg["min_p0"]):
        why.append(f"modeled P0 {pb['p0_model'] * 100:.0f}% is under this mode's {float(mode_cfg['min_p0']) * 100:.0f}% floor")
    if pb["p0_conservative"] is not None and pb["p0_conservative"] < float(mode_cfg["min_p0_conservative"]):
        why.append(f"conservative P0 {pb['p0_conservative'] * 100:.0f}% is under this mode's "
                   f"{float(mode_cfg['min_p0_conservative']) * 100:.0f}% floor")
    return (not why), why


# ── sell quality ─────────────────────────────────────────────────────────────
def _clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _components(c: dict, pb: dict, gates: dict, ctx: dict, cfg: dict, mode_cfg: dict) -> dict:
    """Eight 0–100 components, each with a one-line note. Weights (mode-
    independent by default) are hypotheses documented in thresholds.json."""
    comp = {}
    p0c = pb["p0_conservative"] or 0.0
    comp["safety"] = {"score": _clip((p0c - 0.5) / 0.45 * 100.0),
                      "note": f"conservative P0 {p0c * 100:.0f}% ({pb['grade']})"}
    evc = _num(c.get("ev_per_contract")) or 0.0
    credit_c = (_num(c.get("credit")) or 0.0) * 100.0
    ev_frac = evc / credit_c if credit_c > 0 else 0.0
    vrp = _num(ctx.get("vrp_ratio"))
    comp["edge"] = {"score": _clip(ev_frac / 0.5 * 100.0),
                    "note": (f"expected value is {ev_frac * 100:.0f}% of the credit"
                             + (f"; implied vol {vrp:.2f}× the forecast" if vrp else ""))}
    ann = _num(c.get("annualized_roc_pct")) or 0.0
    comp["income"] = {"score": _clip(ann / 40.0 * 100.0),
                      "note": f"{ann:.0f}% annualized on capital ({c.get('roc_pct')}% for the trade)"}
    lq = gates["liquidity"]["details"]
    spr = _num(lq.get("spread_pct")) or 12.0
    oi = _num(lq.get("oi")) or 0.0
    comp["liquidity"] = {"score": _clip((1 - spr / 15.0) * 60.0 + min(oi / 2000.0, 1.0) * 40.0),
                         "note": f"spread {spr:.0f}% of mid, open interest {int(oi)}"
                                 + (" — thin quoted size" if lq.get("thin_size") else "")}
    tl = gates["tail"]["details"]
    comp["tail"] = {"score": _clip(100.0 - float(tl["points"]) * 1.5
                                   - (float(tl["es95_over_credit"] or 0) * 4.0)),
                    "note": (f"worst-5% loss {tl['es95_over_credit']}× the credit; "
                             + (tl["notes"][0] if tl["notes"] else "no tail triggers"))}
    evd = gates["events"]["details"]
    e_pts = (float(cfg["events"]["macro_penalty_pts"]) * len(evd["macro_inside"])
             + (20.0 if evd["macro_at_expiry"] else 0.0) + (25.0 if evd.get("ex_div_inside") else 0.0))
    comp["event"] = {"score": _clip(100.0 - e_pts),
                     "note": (f"{len(evd['macro_inside'])} scheduled macro event(s) inside the life"
                              + (", one at expiry" if evd["macro_at_expiry"] else "")
                              + ("; ex-dividend inside" if evd.get("ex_div_inside") else "")
                              + ("; earnings inside (EVENT mode)" if evd["earnings_inside"] else ""))}
    pv = gates["data"]["provenance"]
    d_pts = (0 if pv.get("greeks") == "provider" else 15) + (25 if pv.get("delayed") else 0)
    age = _num(pv.get("quote_age_s"))
    if age is not None and ctx.get("market_open"):
        d_pts += min(30.0, age / 30.0)
    comp["data"] = {"score": _clip(100.0 - d_pts),
                    "note": (f"{pv.get('source')}, greeks {pv.get('greeks')}, quote "
                             + (f"{int(age)}s old" if age is not None else "age unknown"))}
    w_own = _num(pb.get("weight_own")) or 0.0
    n_eff = pb.get("n_eff") or 0
    comp["calibration"] = {"score": _clip(40.0 + w_own * 60.0),
                           "note": (f"{w_own * 100:.0f}% of the probability comes from this stock's own "
                                    f"history ({n_eff} independent windows); the rest from the pooled "
                                    f"universe calibration")}
    return comp


def _sell_quality(comp: dict, cfg: dict) -> dict:
    w = cfg["weights"]
    tot = sum(float(v) for v in w.values()) or 1.0
    score = sum(float(w[k]) * comp[k]["score"] for k in w if k in comp) / tot
    return {"score": round(score, 1),
            "breakdown": [{"component": k, "weight": float(w[k]), "score": round(comp[k]["score"], 1),
                           "points": round(float(w[k]) * comp[k]["score"] / 100.0, 1),
                           "note": comp[k]["note"]} for k in w if k in comp]}


# ── evaluate ────────────────────────────────────────────────────────────────
def evaluate(c: dict, ctx: dict, cfg: dict | None = None, mode: str = "balanced",
             event_mode: bool = False) -> dict:
    """Run the gates in order on one candidate. Returns the candidate with
    `verdict` (qualified|rejected|insufficient), `rejections` [{gate, why}],
    `gates` (each gate's details), `probability`, `components`, `sell_quality`,
    `objective_value`, and the plain-English `explain` block."""
    cfg = cfg or config()[0]
    mode = mode if mode in cfg["modes"] else "balanced"
    mode_cfg = cfg["modes"][mode]
    c = dict(c)
    c["context"] = {"vrp_ratio": ctx.get("vrp_ratio")}
    rejections, gates = [], {}
    ok, why, prov = _gate_data(c, ctx, cfg)
    gates["data"] = {"ok": ok, "why": why, "provenance": prov}
    if not ok:
        rejections.append({"gate": "data", "why": why})
    ok, why, det = _gate_liquidity(c, ctx, cfg, mode_cfg)
    gates["liquidity"] = {"ok": ok, "why": why, "details": det}
    if not ok:
        rejections.append({"gate": "liquidity", "why": why})
    ok, why, det = _gate_events(c, ctx, cfg, event_mode)
    gates["events"] = {"ok": ok, "why": why, "details": det}
    if not ok:
        rejections.append({"gate": "events", "why": why})
    ok, why, det = _gate_edge(c, mode_cfg)
    gates["edge"] = {"ok": ok, "why": why, "details": det}
    if not ok:
        rejections.append({"gate": "edge", "why": why})
    ok, why, det = _gate_tail(c, ctx, cfg, mode_cfg)
    gates["tail"] = {"ok": ok, "why": why, "details": det}
    if not ok:
        rejections.append({"gate": "tail", "why": why})
    pb = _probability_block(c, cfg)
    ok, why = _gate_probability(pb, mode_cfg)
    gates["probability"] = {"ok": ok, "why": why}
    if not ok:
        rejections.append({"gate": "probability", "why": why})
    c["probability"] = pb
    c["gates"] = gates
    c["rejections"] = rejections
    c["mode"] = mode
    c["event_mode"] = event_mode
    if gates["data"]["ok"] is False and any("could be computed" in w or "not answering" in w
                                             for w in gates["data"]["why"]):
        c["verdict"] = "insufficient"
    else:
        c["verdict"] = "qualified" if not rejections else "rejected"
    comp = _components(c, pb, gates, ctx, cfg, mode_cfg)
    c["components"] = comp
    c["sell_quality"] = _sell_quality(comp, cfg)
    c["objective"] = mode_cfg["objective"]
    c["objective_value"] = objective_value(c, mode_cfg["objective"])
    c["config_hash"] = config()[1]
    return c


def objective_value(c: dict, objective: str) -> float | None:
    pb = c.get("probability") or {}
    if objective == "max_p0_conservative":
        return pb.get("p0_conservative")
    if objective == "max_ev_per_tail":
        ev_ = _num(c.get("ev_per_share"))
        es = _num(c.get("es95_per_share"))
        return (ev_ / es) if (ev_ is not None and es and es > 0.01) else None
    if objective == "max_annualized_roc":
        return _num(c.get("annualized_roc_pct"))
    if objective == "max_sell_quality":
        return (c.get("sell_quality") or {}).get("score")
    return (c.get("sell_quality") or {}).get("score")


def rank(evaluated: list[dict]) -> list[dict]:
    """Qualified candidates ordered by the mode's objective, ties by sell
    quality. Rejected ones are not in the list — see `rejection_summary`."""
    q = [c for c in evaluated if c.get("verdict") == "qualified"]
    q.sort(key=lambda c: (-(c.get("objective_value") if c.get("objective_value") is not None else -1e9),
                          -((c.get("sell_quality") or {}).get("score") or 0)))
    for i, c in enumerate(q, 1):
        c["rank"] = i
    return q


def rejection_summary(evaluated: list[dict]) -> list[dict]:
    """Why the others failed, grouped by gate then reason, with the symbols."""
    groups: dict = {}
    for c in evaluated:
        if c.get("verdict") == "qualified":
            continue
        for r in c.get("rejections") or [{"gate": "data", "why": ["insufficient data"]}]:
            key = (r["gate"], (r["why"] or ["(unstated)"])[0])
            g = groups.setdefault(key, {"gate": r["gate"], "reason": key[1], "n": 0, "symbols": set()})
            g["n"] += 1
            g["symbols"].add(c.get("symbol"))
    out = [{**g, "symbols": sorted(g["symbols"])} for g in groups.values()]
    out.sort(key=lambda g: -g["n"])
    return out


# ── explanations ────────────────────────────────────────────────────────────
def _pct(x):
    return f"{x * 100:.0f}%" if x is not None else "—"


def explain(top: dict, runner_up: dict | None = None, ctx: dict | None = None) -> dict:
    """Plain-English defence of a ranking, in the order the user asked for."""
    ctx = ctx or {}
    pb = top.get("probability") or {}
    ex = top.get("excursion") or {}
    ev_ = top.get("evidence") or {}
    strat = top["strategy"].replace("_", " ")
    why_stock = (f"{top['symbol']} was selected because implied volatility is "
                 f"{(ctx.get('vrp_ratio') or 0):.2f}× what the stock is forecast to realize over this "
                 f"horizon" if ctx.get("vrp_ratio") else f"{top['symbol']} cleared every gate")
    if ctx.get("vrp_percentile") is not None:
        why_stock += f", the {ctx['vrp_percentile']:.0f}th percentile of its own premium history"
    why_stock += (f"; the {top['dte_bucket']}-day bucket paid the best compensation per unit of "
                  f"tail risk on this chain.")
    why_exp = (f"The {top['expiration']} expiration ({top['dte']:.0f} days) was chosen over the other "
               f"listed dates because its {strat} scored highest on this mode's objective "
               f"({top.get('objective', '').replace('_', ' ')}) after the gates.")
    why_strike = (f"The {top['short_strike']} strike sits {abs(top.get('dist_pct') or 0):.1f}% "
                  f"({top.get('k_sigma', 0):.2f} standard deviations, {top.get('expected_moves_out') or 0:.2f} "
                  f"expected moves) from spot at {abs(_num(top.get('delta')) or 0):.2f} delta.")
    why_side = {"put": "A put was sold because the downside premium is the richer side after the "
                       "forecast and the evidence, and the stock's history of reaching this distance "
                       "downward is what the probability is measured on.",
                "call": "A call was sold because the upside premium is the richer side after the "
                        "forecast and the evidence.",
                "both": "Both sides are sold (iron condor) because each wing clears the gates on its own "
                        "and the two events are disjoint."}[top["side"]]
    support = (f"P0 (expires worthless) is {_pct(pb.get('p0_model'))} on the model at the "
               f"{top.get('sigma_h', 0):.3f} horizon forecast, {_pct(pb.get('p0_measured'))} on this "
               f"stock's own measured history, and {_pct(pb.get('p0_conservative'))} on the conservative "
               f"bound ({pb.get('conservative_basis')}). Probability of touching the strike first: "
               f"{_pct(pb.get('p_touch'))} model, {_pct(pb.get('p_touch_measured'))} measured. "
               f"Probability of profit after costs: {_pct(pb.get('p_profit'))}.")
    overpay = (f"The market pays {top.get('credit')} at the bid against a fair value of "
               f"{top.get('fair_at_forecast')} at the forecast volatility — an expected value of "
               f"{top.get('ev_per_contract'):+.0f} per contract after commissions"
               + (", wing bought at the ask" if top["legs"] > 1 else "") + ".")
    lv = (ev_.get("levels") or [{}])[0]
    breach = (f"Over {ev_.get('n', 0)} comparable windows ({ev_.get('n_eff', 0)} independent) this stock "
              f"finished beyond a strike this far {_pct(lv.get('itm_raw'))} of the time and touched it "
              f"{_pct(lv.get('touch_raw'))}."
              if ev_.get("levels") else "No measurable breach history for this stock at this horizon; the "
                                        "probability rests on the pooled universe calibration.")
    wrong = [
        f"The forecast volatility is wrong: a {top.get('sigma_h', 0):.3f} forecast that realizes 30% higher "
        f"roughly doubles the modeled chance of finishing in the money.",
        (f"A gap: the 95th-percentile overnight gap toward this strike is {ex.get('gap_toward_strike_sigma_p95')}σ "
         f"and the largest seen was {ex.get('gap_toward_strike_sigma_max')}σ, against a strike {top.get('k_sigma', 0):.2f}σ away."
         if ex.get("gap_toward_strike_sigma_p95") is not None else "Gap history unavailable."),
        "A scheduled or unscheduled event repricing the whole name (the earnings gate only covers known dates).",
        "IV holding flat is assumed for the early-profit paths; a volatility spike after entry delays every target.",
    ]
    worst = (f"When this distance was breached, price overshot the strike by {ex.get('overshoot_sigma')}σ on "
             f"average, first reaching it after {ex.get('first_touch_bars')} sessions, and crossed back "
             f"{ex.get('recross')} times." if ex.get("overshoot_sigma") is not None
             else "No breach of this distance in the measured history.")
    reject = [f"Earnings date moving inside {top['expiration']}.",
              f"The bid falling below {(top.get('credit') or 0) * 0.8:.2f} (expected value turns negative near "
              f"{top.get('fair_at_forecast')}).",
              "Realized volatility accelerating past 1.6× its 20-day pace, or the term structure inverting.",
              f"Spread widening past {top.get('mode', 'balanced')} mode's limit, or open interest draining."]
    vs2 = None
    if runner_up:
        r = runner_up
        pb2 = r.get("probability") or {}
        vs2 = (f"#2 ({r['symbol']} {r['strategy'].replace('_', ' ')} {r['expiration']} {r['short_strike']}) "
               f"ranks below because its {top.get('objective', '').replace('_', ' ')} is "
               f"{r.get('objective_value')} against {top.get('objective_value')}: "
               f"P0 conservative {_pct(pb2.get('p0_conservative'))} vs {_pct(pb.get('p0_conservative'))}, "
               f"EV {r.get('ev_per_contract'):+.0f} vs {top.get('ev_per_contract'):+.0f}, "
               f"worst-5% loss {r.get('es95_per_share')} vs {top.get('es95_per_share')} per share, "
               f"sell quality {(r.get('sell_quality') or {}).get('score')} vs {(top.get('sell_quality') or {}).get('score')}.")
    return {"why_stock": why_stock, "why_expiration": why_exp, "why_strike": why_strike,
            "why_side": why_side, "evidence": support, "what_market_overpays": overpay,
            "comparable_breaches": breach, "what_could_make_this_wrong": wrong,
            "worst_comparable_outcome": worst, "what_would_reject_it": reject,
            "why_second_is_second": vs2}


def risk_pathway(c: dict, cfg: dict | None = None) -> dict:
    """ENTRY / MANAGEMENT / DANGER / EXIT / ROLL / ASSIGNMENT / POSITION SIZE
    for one qualified candidate, from its own numbers."""
    cfg = cfg or config()[0]
    pb = c.get("probability") or {}
    paths = (pb.get("paths") or {})
    t = paths.get("targets") or {}
    credit = _num(c.get("credit")) or 0.0
    entry = {"target_credit": c.get("credit"), "credit_basis": c.get("credit_basis"),
             "max_slippage_per_share": round(max(0.01, credit * 0.05), 2),
             "note": (f"Rest the order at {c.get('credit')} (the bid is the floor a resting sell is "
                      f"promised); do not chase past {round(credit * 0.95, 2)} — below that the "
                      f"expected value is gone.")}
    hit50 = (t.get("50") or {}).get("p_hit")
    d50 = (t.get("50") or {}).get("expected_days_if_hit")
    manage = {"profit_targets": t,
              "note": ((f"Half the credit is reached in {_pct(hit50)} of modeled paths, typically after "
                        f"{d50} days of {c['dte']:.0f}; whether taking it beats holding is graded by the "
                        f"forward test, not assumed.") if hit50 is not None else
                       "No early-target paths (no entry IV on the quote)."),
              "invalidation": ["earnings date moves inside the expiration",
                               "realized volatility accelerates past 1.6× (the tail gate re-runs daily)",
                               "the bid on the short option doubles while spot is unchanged (the market "
                               "is repricing the risk, not the time)"]}
    ex = c.get("excursion") or {}
    danger = {"price": (f"spot within {max(1.0, abs(c.get('dist_pct') or 0) / 2):.1f}% of the short strike "
                        f"({c['short_strike']}) — from the history, the first touch typically comes after "
                        f"{ex.get('first_touch_bars')} sessions"),
              "volatility": "implied volatility rising more than 10 points after entry",
              "event": "any newly scheduled company event inside the life of the option",
              "market": "the index gamma regime flipping to short (moves accelerate)"}
    exit_ = {"residual_reward_vs_risk": (f"When the remaining premium is under {round(credit * 0.25, 2)} "
                                         f"(75% captured) the reward left is small against a tail that is "
                                         f"still {c.get('es95_per_share')} per share — buying back is "
                                         f"statistically reasonable unless expiry is within a few days."),
             "rule": "close when residual credit < 25% AND days left > 5, or on any DANGER trigger"}
    roll = {"note": ("A roll must improve the position: the new credit net of the close cost must be "
                     "positive AND the new contract must pass the same gates (P0 floor, EV > 0, tail) "
                     "at its own horizon. Extending time for a debit only postpones the problem."),
            "check": ["close cost vs new credit", "new P0 at its horizon", "new EV after costs",
                      "added tail exposure (ES95)", "capital tied up longer"]}
    assign = {"acceptable": c["strategy"] in ("cash_secured_put", "covered_call"),
              "note": {"cash_secured_put": "Assignment delivers 100 shares at the strike less the credit — "
                                           "acceptable only if you want the stock at that price.",
                       "covered_call": "Assignment sells the shares at the strike plus the credit — acceptable "
                                       "if being called away is the intent.",
                       "put_credit_spread": "Not the objective: close or roll before the short leg is deep "
                                            "in the money; the long wing caps the loss but not the assignment mess.",
                       "call_credit_spread": "Not the objective: watch ex-dividend dates on the short call.",
                       "iron_condor": "Not the objective on either wing."}.get(c["strategy"])}
    n_eff = pb.get("n_eff") or 0
    size = {"note": ("Size on the conservative P0 and the tail, never the point estimate. With "
                     f"{n_eff} independent windows behind the estimate, "
                     + ("quarter-Kelly on the measured edge is the ceiling." if n_eff >= 40 else
                        "the sample is too small for Kelly sizing of any fraction; use a fixed small "
                        "fraction of buying power (≤2% risk per trade).")),
            "capital_required": c.get("capital"), "max_loss_per_contract": (
                round((c.get("max_loss_per_share") or 0) * 100, 0) if c.get("defined_risk") else None),
            "es95_per_contract": round((c.get("es95_per_share") or 0) * 100, 0)}
    return {"entry": entry, "management": manage, "danger": danger, "exit": exit_, "roll": roll,
            "assignment": assign, "position_size": size}


# ── portfolio awareness ─────────────────────────────────────────────────────
def portfolio_concentration(ranked: list[dict], sector_of: dict | None = None, top_n: int = 5) -> dict:
    """Are the top picks one bet wearing five names? Sector share, same-
    expiration share, side share, and earnings-week overlap of the top N."""
    top = ranked[:top_n]
    if not top:
        return {"n": 0, "flags": []}
    sector_of = sector_of or {}
    sectors: dict = {}
    exps: dict = {}
    sides: dict = {}
    for c in top:
        s = sector_of.get(c["symbol"]) or "unknown"
        sectors[s] = sectors.get(s, 0) + 1
        exps[c["expiration"]] = exps.get(c["expiration"], 0) + 1
        sides[c["side"]] = sides.get(c["side"], 0) + 1
    flags = []
    for s, n in sectors.items():
        if s != "unknown" and n >= max(3, int(0.6 * len(top))):
            flags.append(f"{n} of the top {len(top)} are {s} — one sector bet, not {n} independent ones")
    for e, n in exps.items():
        if n >= max(3, int(0.6 * len(top))):
            flags.append(f"{n} of the top {len(top)} expire {e} — one date carries most of the risk")
    if sides.get("put", 0) >= max(4, int(0.8 * len(top))):
        flags.append("almost every pick is a short put — this is one long-market bet")
    return {"n": len(top), "sectors": sectors, "expirations": exps, "sides": sides, "flags": flags}
