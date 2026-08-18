"""invest_options.py — Phase 3: from a fair value to an actual position.

`fair_value.py` decides what the business is worth, `structures.py` compares
ways of owning it on identical capital, and this module is the half with a
clock, a network and a disk: the option chain, the volatility context, the
two optimizers, the prospective long-dated observation store, and the entry
verdict that reads all of it.

Nothing here re-implements anything the dashboard already owns:

  chain            injected — the same Schwab-first loader every other tab uses
  volatility       vol_forecast.py, through edge_scan's validated per-ticker
                   model choice where it is available
  IV30, term, VRP  premium_edge.py
  probabilities    premium_edge.p_itm / touch_prob / expected shortfall
  Black-Scholes    metrics.py
  Treasury         injected from treasury.py's official curve
  payoff maths     structures.py

TWO HORIZONS, DELIBERATELY NOT MIXED.

The Structure Comparator runs at ONE long-dated expiration, because a
three-year view of a business and a forty-five-day option are answers to
different questions and ranking them against each other would be arithmetic
rather than comparison. The short-dated put optimizer runs on its own, in
the chain coverage the rest of the app already uses, and carries the
market-risk block with it.

That market-risk block is not decoration. Over forty-five days the
fundamental scenarios barely separate — earnings hardly move and a multiple
does not re-rate — so a put judged only on Bear/Base/Bull would look safe by
construction. What a stock can do in six weeks is a volatility question, and
it is answered with volatility numbers, kept in their own column, and never
presented as the same distribution.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import fair_value as fv
import structures as st

try:
    import premium_edge as pe
except Exception:                                    # pragma: no cover
    pe = None
try:
    import vol_forecast as vfc
except Exception:                                    # pragma: no cover
    vfc = None
try:
    import edge_scan as _edge
except Exception:                                    # pragma: no cover
    _edge = None
try:
    from metrics import _bs_delta, _bs_price, normalize_iv
except Exception:                                    # pragma: no cover
    _bs_delta = _bs_price = normalize_iv = None

OPTIONS_VERSION = "invest-options-1.0.0"
SCHEMA_VERSION = "1.0"

SCENARIOS = st.SCENARIOS

_LOCK = threading.RLock()
_DATA_DIR: Path | None = None
_CHAIN_FN = None            # (symbol) -> normalized chain dict | None
_BARS_FN = None             # (symbol, days) -> {"bars": [...]}
_RATE_FN = None             # (years) -> {"pct", "as_of", "source"} | None
_EARNINGS_FN = None         # (symbol) -> {"next": iso, "last": iso}
_EARN_MOVES_FN = None       # (symbol) -> {"avg_abs": pct, "n": int}
_TODAY_FN = None            # () -> date, a test seam only

_CHAIN_TTL = 600.0
_CHAIN_MEM: dict = {}

DEFAULTS = {
    "csp_min_dte": 7,
    "csp_max_dte": 75,
    "leaps_min_dte": 270,
    "leaps_max_dte": 730,
    "max_candidates_per_side": 60,
    "spread_long_band_pct": 20.0,     # long strike within ±this of spot
    "spread_short_min_pct": 5.0,      # short strike at least this above long
    "spread_short_max_pct": 60.0,
    "max_spread_combos": 240,
    "min_open_interest": 25,
    "max_bid_ask_spread_pct": 25.0,
    "leaps_iv_history_days": 400,
    "leaps_tenor_tolerance": 0.30,
}


def cfg_get(cfg, key):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


def configure(chain_fn=None, bars_fn=None, rate_fn=None, earnings_fn=None,
              earn_moves_fn=None, data_dir=None, today_fn=None) -> None:
    global _CHAIN_FN, _BARS_FN, _RATE_FN, _EARNINGS_FN, _EARN_MOVES_FN
    global _DATA_DIR, _TODAY_FN
    _CHAIN_FN = chain_fn
    _BARS_FN = bars_fn
    _RATE_FN = rate_fn
    _EARNINGS_FN = earnings_fn
    _EARN_MOVES_FN = earn_moves_fn
    _TODAY_FN = today_fn
    if data_dir:
        _DATA_DIR = Path(data_dir) / "invest"
        try:
            (_DATA_DIR / "leaps").mkdir(parents=True, exist_ok=True)
        except Exception:                            # pragma: no cover
            _DATA_DIR = None
    else:
        _DATA_DIR = None


def available() -> bool:
    return _CHAIN_FN is not None


def _today() -> date:
    if _TODAY_FN is not None:
        try:
            return _TODAY_FN()
        except Exception:                            # pragma: no cover
            pass
    return date.today()


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _safe(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9._-]", "", (symbol or "").upper())


# ── the chain ───────────────────────────────────────────────────────────────

def chain(symbol: str, force: bool = False) -> dict | None:
    """The full option chain from the app's own loader, cached ten minutes.

    Deliberately the WHOLE chain rather than a short window: the LEAPS
    optimizer needs expirations one to two years out, which the short-dated
    paths in this app never request.
    """
    sym = (symbol or "").upper().strip()
    if not sym or _CHAIN_FN is None:
        return None
    with _LOCK:
        hit = _CHAIN_MEM.get(sym)
    if hit and not force and time.time() - hit[0] < _CHAIN_TTL:
        return hit[1]
    try:
        ch = _CHAIN_FN(sym)
    except Exception:                                # noqa: BLE001
        ch = None
    if not ch or not ch.get("chains"):
        return None
    with _LOCK:
        _CHAIN_MEM[sym] = (time.time(), ch)
        while len(_CHAIN_MEM) > 12:
            _CHAIN_MEM.pop(min(_CHAIN_MEM, key=lambda k: _CHAIN_MEM[k][0]), None)
    return ch


def dte_of(exp_iso: str, today: date | None = None) -> float | None:
    try:
        d = date.fromisoformat(str(exp_iso)[:10])
    except (TypeError, ValueError):
        return None
    return float((d - (today or _today())).days)


def expirations_between(ch: dict, lo_dte: float, hi_dte: float,
                        today: date | None = None) -> list[tuple[str, float]]:
    out = []
    for exp in (ch or {}).get("chains") or {}:
        d = dte_of(exp, today)
        if d is not None and lo_dte <= d <= hi_dte:
            out.append((exp, d))
    out.sort(key=lambda x: x[1])
    return out


def rows_for(ch: dict, exp: str, side: str) -> list:
    sides = ((ch or {}).get("chains") or {}).get(exp) or {}
    return sides.get("calls" if side == "call" else "puts") or []


def spot_of(ch: dict):
    return _num(((ch or {}).get("underlying") or {}).get("last"))


def quote_quality(row: dict, cfg=None, spot=None, side=None) -> dict:
    """Is this a contract anybody could actually trade, at a price that exists?

    Open interest and the bid-ask spread, both displayed. A contract that
    fails is not silently dropped — it is listed with the reason, because
    "no candidate found" and "every candidate was untradeable" are different
    answers.

    The arbitrage checks below are not fussiness. A deep-in-the-money
    long-dated call quoted BELOW its own intrinsic value is free money, which
    means it is a stale quote rather than an opportunity — and on identical
    capital it wins the structure comparison outright, because the comparison
    is doing exactly what it was built to do with a price that is not real.
    Apple's December-2027 fifteen-dollar call came back offered at $254
    against $290.93 of intrinsic value and topped the ranking at 17.7% a year
    until this check was added.
    """
    cfg = cfg or {}
    bid, ask = _num(row.get("bid")), _num(row.get("ask"))
    oi = _num(row.get("openInterest"))
    vol = _num(row.get("volume"))
    mid = (bid + ask) / 2.0 if (bid is not None and ask is not None) else None
    spread_pct = ((ask - bid) / mid * 100.0
                  if mid and mid > 0 and bid is not None and ask is not None
                  else None)
    why = []
    broken = []
    min_oi = float(cfg_get(cfg, "min_open_interest"))
    max_sp = float(cfg_get(cfg, "max_bid_ask_spread_pct"))
    if bid is None or bid <= 0:
        why.append("no bid")
    if ask is None or ask <= 0:
        why.append("no offer")
    if oi is not None and oi < min_oi:
        why.append(f"open interest {oi:.0f} below {min_oi:.0f}")
    if spread_pct is not None and spread_pct > max_sp:
        why.append(f"bid-ask spread {spread_pct:.0f}% wider than {max_sp:.0f}%")
    if bid is not None and ask is not None and bid > 0 and ask > 0 and bid > ask:
        broken.append("the quote is crossed — the bid is above the offer")
    sp, k = _num(spot), _num(row.get("strike"))
    intrinsic = None
    if sp is not None and k is not None and side in ("call", "put"):
        intrinsic = max(0.0, (sp - k) if side == "call" else (k - sp))
        if ask is not None and ask > 0 and ask < intrinsic * 0.99:
            broken.append(f"offered at ${ask:,.2f} against ${intrinsic:,.2f} "
                          f"of intrinsic value — a stale quote, not an "
                          f"opportunity")
        if bid is not None and bid > 0 and side == "put" and bid > k:
            broken.append("bid above the strike, which a put can never be "
                          "worth")
    notes = why + broken
    return {"ok": not notes, "priceable": not broken,
            "bid": bid, "ask": ask, "mid": mid,
            "spread_pct": spread_pct, "open_interest": oi, "volume": vol,
            "intrinsic": intrinsic, "notes": notes, "broken": broken,
            "label": "tradeable" if not notes else "; ".join(notes)}


def contract_greeks(row: dict, spot, strike, t_years, side: str,
                    rate_pct=None) -> dict:
    """Whatever the chain supplied, with the gaps filled from Black-Scholes.

    Some feeds carry greeks and some carry only a price and an implied
    volatility. Filling delta from the app's own Black-Scholes rather than
    leaving it blank keeps the LEAPS table complete; the source of each
    number is reported so nothing pretends to be a broker's greek when it is
    a model's.
    """
    iv = normalize_iv(row.get("iv")) if normalize_iv else _num(row.get("iv"))
    delta = _num(row.get("delta"))
    source = "chain"
    if delta is None and iv and _bs_delta is not None and spot and strike \
            and t_years and t_years > 0:
        r = (_num(rate_pct) or 4.0) / 100.0
        delta = _bs_delta(float(spot), float(strike), float(t_years),
                          float(iv), side, r=r)
        source = "Black-Scholes at the contract's own implied volatility"
    return {"delta": delta, "delta_source": source, "iv": iv,
            "theta": _num(row.get("theta")), "gamma": _num(row.get("gamma")),
            "vega": _num(row.get("vega"))}


# ── volatility context ──────────────────────────────────────────────────────

def _bars(symbol: str, days: int):
    if _BARS_FN is None:
        return []
    try:
        pack = _BARS_FN(symbol, days) or {}
    except Exception:                                # noqa: BLE001
        return []
    return pack.get("bars") or pack if isinstance(pack, (list, dict)) else []


def rate_for(years) -> dict:
    """The Treasury yield matched to the holding period."""
    if _RATE_FN is None:
        return {"pct": None, "reason": "No Treasury provider is wired."}
    try:
        out = _RATE_FN(years) or {}
    except Exception:                                # noqa: BLE001
        return {"pct": None, "reason": "The Treasury curve was unreachable."}
    return {"pct": _num(out.get("pct")), "as_of": out.get("as_of"),
            "source": out.get("source"), "tenor": out.get("tenor"),
            "reason": "" if out.get("pct") is not None else
                      "No yield was returned for this horizon."}


def market_risk(symbol: str, ch: dict, bars: list, today: date | None = None,
                earnings_next: str | None = None) -> dict:
    """SHORT-HORIZON volatility context, from the app's own Premium Edge.

    This describes what the stock can do over the next few weeks. It is NOT
    the fundamental Bear/Base/Bull path and is never averaged with it: one is
    a view of the business, the other is a view of the distribution, and a
    number that blended them would describe neither.
    """
    if pe is None or not bars:
        return {"available": False,
                "reason": "The volatility engine or the price history is "
                          "not available."}
    today = today or _today()
    cfg, _h = pe.config()
    horizon_end = (today + timedelta(days=30)).isoformat()
    earnings_within = bool(earnings_next
                           and today.isoformat() <= earnings_next <= horizon_end)
    earn_avg, earn_n = None, 0
    if _EARN_MOVES_FN is not None:
        try:
            m = _EARN_MOVES_FN(symbol) or {}
            earn_avg, earn_n = _num(m.get("avg_abs")), int(m.get("n") or 0)
        except Exception:                            # noqa: BLE001
            pass
    erv = None
    if _edge is not None:
        try:
            erv = _edge.forecast_for(symbol, bars, cfg, today.isoformat(),
                                     earnings_within, earn_avg, earn_n)
        except Exception:                            # noqa: BLE001
            erv = None
    if erv is None and vfc is not None:
        erv = vfc.expected_rv30(bars, cfg.get("forecast", {}),
                                earnings_within_horizon=earnings_within,
                                earnings_hist_avg_abs_pct=earn_avg,
                                earnings_hist_n=earn_n)
    if erv is None:
        return {"available": False,
                "reason": ("There is not enough daily price history to build "
                           "a volatility forecast for this ticker.")}
    iv = pe.iv30(ch, today, cfg) if ch else None
    term = pe.term_structure(ch, today, cfg, earnings_date=earnings_next) \
        if ch else None
    vrp = pe.vrp_block(iv["iv30"], erv) if iv else None
    macro: list = []
    cls = pe.classify_premium(vrp, earnings_within, macro, cfg) if vrp else None
    return {
        "available": True, "reason": "",
        "erv30": erv.get("erv30"), "erv30_event": erv.get("erv30_event"),
        "erv_method": erv.get("method"), "erv_quality": erv.get("quality"),
        "iv30": (iv or {}).get("iv30"), "iv30_method": (iv or {}).get("method"),
        "term": term, "vrp": vrp, "classification": cls,
        "earnings_inside_30d": earnings_within,
        "earnings_date": earnings_next,
        "note": ("A short-horizon volatility view: what this stock can do "
                 "over the next few weeks. It sits beside the fundamental "
                 "scenarios, never inside them — neither one is the whole "
                 "distribution."),
    }


def realized_vol_context(bars: list, dte: float) -> dict:
    """LONG-HORIZON realized volatility, matched to the contract's tenor.

    ExpectedRV30 is a thirty-day forecast. Holding a two-year contract's
    implied volatility up against it and calling the difference an edge
    compares a two-year price to a one-month forecast, which is a
    term-structure error dressed as a signal. What a long contract can
    honestly be judged against is what this stock's volatility has actually
    been over windows of the same length.
    """
    if vfc is None or not bars:
        return {"available": False,
                "reason": "No price history is available for this ticker."}
    closes = [float(b.get("close") or 0) for b in bars
              if _num(b.get("close")) and float(b.get("close")) > 0]
    if len(closes) < 260:
        return {"available": False, "n_bars": len(closes),
                "reason": (f"Only {len(closes)} daily closes are available — "
                           f"too few to measure volatility over a window "
                           f"anything like this contract's length.")}
    tenor_td = max(60, min(len(closes) - 1, int(round((dte or 365) / 365.0 * 252))))
    out = {"available": True, "reason": "", "n_bars": len(closes),
           "tenor_trading_days": tenor_td,
           "rv_tenor": vfc.rv(closes, tenor_td),
           "rv_1y": vfc.rv(closes, min(252, len(closes) - 1)),
           "rv_3y": vfc.rv(closes, min(756, len(closes) - 1)),
           "note": ("Realized volatility measured over a window the same "
                    "length as this contract, plus one-year and three-year "
                    "context. ExpectedRV30 is deliberately absent: it is a "
                    "thirty-day forecast and this is not a thirty-day "
                    "contract.")}
    return out


# ── prospective long-dated observation store ────────────────────────────────
#
# chain_store.py deliberately keeps only short expirations, because that is
# what the option backtester prices against, and widening it would multiply
# the size of a store that already works. Long-dated observations get their
# own small file instead. Nothing is back-filled: there is no free archive of
# what a January-2028 call cost last March, and inventing one would be the
# same fabrication this dashboard refuses everywhere else.

def _leaps_path(symbol: str) -> Path | None:
    if _DATA_DIR is None:
        return None
    s = _safe(symbol)
    return (_DATA_DIR / "leaps" / f"{s}.jsonl") if s else None


def record_leaps_observation(symbol: str, spot, rows: list,
                             today: str | None = None) -> bool:
    """One row a day: the near-the-money long-dated contracts and their
    implied volatility, so a tenor-matched history eventually exists."""
    p = _leaps_path(symbol)
    if p is None or not rows:
        return False
    day = (today or _today().isoformat())[:10]
    rec = {"date": day, "spot": _num(spot), "schema": SCHEMA_VERSION,
           "rows": [{"exp": r.get("exp"), "dte": r.get("dte"),
                     "strike": r.get("strike"), "iv": r.get("iv"),
                     "delta": r.get("delta"), "mid": r.get("mid"),
                     "open_interest": r.get("open_interest")}
                    for r in rows[:12]]}
    with _LOCK:
        existing = load_leaps_observations(symbol)
        existing = [x for x in existing if x.get("date") != day]
        existing.append(rec)
        existing.sort(key=lambda x: x.get("date") or "")
        existing = existing[-int(DEFAULTS["leaps_iv_history_days"]):]
        try:
            tmp = p.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(json.dumps(x, separators=(",", ":"))
                                     for x in existing) + "\n")
            tmp.replace(p)
        except Exception:                            # pragma: no cover
            return False
    return True


def load_leaps_observations(symbol: str) -> list:
    p = _leaps_path(symbol)
    if p is None or not p.exists():
        return []
    out = []
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:                                # pragma: no cover
        return []
    return out


def leaps_iv_history(symbol: str, dte: float, current_iv=None,
                     cfg=None) -> dict:
    """Where this contract's implied volatility sits against this ticker's
    own long-dated history — once there is one."""
    cfg = cfg or {}
    tol = float(cfg_get(cfg, "leaps_tenor_tolerance"))
    obs = load_leaps_observations(symbol)
    vals = []
    for rec in obs:
        for r in rec.get("rows") or []:
            d, iv = _num(r.get("dte")), _num(r.get("iv"))
            if d and iv and dte and abs(d - dte) <= tol * dte:
                vals.append(iv)
    if len(vals) < 20:
        return {"available": False, "n": len(vals),
                "reason": (f"This dashboard has recorded {len(vals)} "
                           f"long-dated observations near this tenor. It "
                           f"started recording them on the day Phase 3 "
                           f"shipped and never back-fills, so a tenor-matched "
                           f"implied-volatility history does not exist yet."),
                "recording": _DATA_DIR is not None}
    cur = _num(current_iv)
    srt = sorted(vals)
    pct = None
    if cur is not None:
        below = sum(1 for v in srt if v < cur)
        equal = sum(1 for v in srt if v == cur)
        pct = (below + 0.5 * equal) / len(srt) * 100.0
    return {"available": True, "n": len(vals), "percentile": pct,
            "median": srt[len(srt) // 2], "min": srt[0], "max": srt[-1],
            "reason": "",
            "note": "Measured only against observations within "
                    f"{tol:.0%} of this contract's own tenor."}


# ══════════════════════════════════════════════════════════════════════════
# CANDIDATE BUILDERS
# ══════════════════════════════════════════════════════════════════════════

def scenario_prices(path: dict, years: float, cfg=None) -> dict:
    """Bear / base / bull share prices at ONE horizon, from the same fair
    value assumptions the three-year bridge uses."""
    cfg = cfg or {}
    rate = rate_for(years)
    rev = _num(cfg_get(cfg, "multiple_reversion_years")) \
        if "multiple_reversion_years" in (cfg or {}) \
        else fv.DEFAULTS["multiple_reversion_years"]
    er = fv.expected_return(
        path.get("price"), path.get("eps_ttm"), path.get("growth"),
        path.get("multiples"), years=years, dps_ttm=path.get("dps_ttm"),
        rate_pct=rate.get("pct"), cfg=cfg,
        probabilities=path.get("probabilities"), reversion_years=rev)
    if not er.get("available"):
        return {"available": False, "reason": er.get("reason"),
                "rate": rate, "years": years}
    prices, divs = {}, {}
    for s in SCENARIOS:
        cell = (er.get("scenarios") or {}).get(s) or {}
        prices[s] = cell.get("price_end")
        divs[s] = (cell.get("dividends") or {}).get("value") or 0.0
    if any(prices[s] is None for s in SCENARIOS):
        return {"available": False, "years": years, "rate": rate,
                "reason": "One of the three scenarios could not be priced at "
                          "this horizon."}
    return {"available": True, "years": years, "prices": prices,
            "dividends": divs, "rate": rate, "detail": er, "reason": ""}


def put_candidates(symbol, ch, exp, dte, spot, buy_zone, path, probs, cfg=None,
                   market=None, max_rows=None) -> list:
    """Every put at this expiration that could be sold at or below the price
    we actually want to own the shares at.

    The fundamental acquisition price comes FIRST. A strike above the buy
    zone is not considered however rich the premium is, because a put sold
    above the price you wanted to pay is not a way of buying the business
    cheaply — it is a bet with the business attached.
    """
    cfg = cfg or {}
    zone = _num(buy_zone)
    out = []
    years = max(1e-6, (dte or 0) / 365.0)
    sc = scenario_prices(path, years, cfg)
    capital = st.comparison_capital(spot)
    rate_pct = (sc.get("rate") or {}).get("pct")
    erv = (market or {}).get("erv30_event") or (market or {}).get("erv30")
    edge_cfg, _h = (pe.config() if pe is not None else ({}, ""))
    rows = rows_for(ch, exp, "put")
    limit = int(max_rows or cfg_get(cfg, "max_candidates_per_side"))
    for row in sorted(rows, key=lambda r: -(_num(r.get("strike")) or 0))[:limit * 3]:
        k = _num(row.get("strike"))
        if k is None or k <= 0:
            continue
        if zone is not None and k > zone + 1e-9:
            continue
        q = quote_quality(row, cfg, spot=spot, side="put")
        if not q["priceable"] or q["bid"] is None or q["bid"] <= 0:
            continue
        built = st.secured_put(spot, capital, k, q["bid"],
                               sc.get("prices") or {}, probs, years,
                               rate_pct=rate_pct)
        econ = None
        if pe is not None and erv:
            try:
                econ = pe.contract_economics(row, spot, "put", years, erv,
                                             edge_cfg,
                                             rate=(rate_pct or 4.0) / 100.0)
            except Exception:                        # noqa: BLE001
                econ = None
        greeks = contract_greeks(row, spot, k, years, "put", rate_pct)
        annual = None
        if q["bid"] and k > 0 and years > 0:
            annual = q["bid"] / k * 100.0 / years
        built["contract"] = {**(built.get("contract") or {}),
                             "expiration": exp, "dte": dte,
                             "annualized_on_notional_pct": annual,
                             "below_buy_zone_pct":
                                 ((zone - k) / zone * 100.0
                                  if zone and zone > 0 else None),
                             **greeks}
        built["liquidity"] = q
        built["market_risk"] = econ
        built["expiration"] = exp
        built["dte"] = dte
        out.append(built)
        if len(out) >= limit:
            break
    return out


def call_candidates(symbol, ch, exp, dte, spot, path, probs, cfg=None,
                    kind: str = st.LEAPS, max_rows=None) -> list:
    """Every long call at this expiration, priced on the same account."""
    cfg = cfg or {}
    years = max(1e-6, (dte or 0) / 365.0)
    sc = scenario_prices(path, years, cfg)
    capital = st.comparison_capital(spot)
    rate_pct = (sc.get("rate") or {}).get("pct")
    out = []
    rows = rows_for(ch, exp, "call")
    limit = int(max_rows or cfg_get(cfg, "max_candidates_per_side"))
    # Ordered by distance from the money, not by strike. A long-dated chain
    # can list ninety strikes from $175 to $800 on a $480 stock, and a cap
    # applied to a list sorted by strike would spend the whole budget on deep
    # in-the-money contracts and never reach the ones anybody trades.
    for row in sorted(rows,
                      key=lambda r: abs((_num(r.get("strike")) or 0) - (spot or 0))
                      )[:limit * 3]:
        k = _num(row.get("strike"))
        if k is None or k <= 0:
            continue
        q = quote_quality(row, cfg, spot=spot, side="call")
        if not q["priceable"] or q["ask"] is None or q["ask"] <= 0:
            continue
        built = st.long_call(spot, capital, k, q["ask"],
                             sc.get("prices") or {}, probs, years,
                             rate_pct=rate_pct, kind=kind)
        greeks = contract_greeks(row, spot, k, years, "call", rate_pct)
        intrinsic = max(0.0, spot - k)
        extrinsic = max(0.0, q["ask"] - intrinsic)
        built["contract"] = {**(built.get("contract") or {}),
                             "expiration": exp, "dte": dte,
                             "intrinsic": intrinsic, "extrinsic": extrinsic,
                             "extrinsic_per_year":
                                 extrinsic / years if years > 0 else None,
                             **greeks}
        built["liquidity"] = q
        built["expiration"] = exp
        built["dte"] = dte
        out.append(built)
        if len(out) >= limit:
            break
    return out


def buy_write_candidates(symbol, ch, exp, dte, spot, path, probs, cfg=None,
                         max_rows=None) -> list:
    cfg = cfg or {}
    years = max(1e-6, (dte or 0) / 365.0)
    sc = scenario_prices(path, years, cfg)
    capital = st.comparison_capital(spot)
    rate_pct = (sc.get("rate") or {}).get("pct")
    out = []
    limit = int(max_rows or cfg_get(cfg, "max_candidates_per_side"))
    for row in sorted(rows_for(ch, exp, "call"),
                      key=lambda r: _num(r.get("strike")) or 0):
        k = _num(row.get("strike"))
        if k is None or k <= spot:            # a covered call is written above
            continue
        q = quote_quality(row, cfg, spot=spot, side="call")
        if not q["priceable"] or q["bid"] is None or q["bid"] <= 0:
            continue
        built = st.buy_write(spot, capital, k, q["bid"],
                             sc.get("prices") or {}, probs, years,
                             dividend_fv_per_share=sc.get("dividends") or 0.0,
                             rate_pct=rate_pct, cfg=cfg)
        built["contract"] = {**(built.get("contract") or {}),
                             "expiration": exp, "dte": dte,
                             **contract_greeks(row, spot, k, years, "call",
                                               rate_pct)}
        built["liquidity"] = q
        built["expiration"] = exp
        built["dte"] = dte
        out.append(built)
        if len(out) >= limit:
            break
    return out


def spread_candidates(symbol, ch, exp, dte, spot, path, probs, cfg=None) -> list:
    """Bull call spreads at real quoted strikes, bounded so the search does
    not become a cross-product of the whole chain."""
    cfg = cfg or {}
    years = max(1e-6, (dte or 0) / 365.0)
    sc = scenario_prices(path, years, cfg)
    capital = st.comparison_capital(spot)
    rate_pct = (sc.get("rate") or {}).get("pct")
    band = float(cfg_get(cfg, "spread_long_band_pct")) / 100.0
    lo_gap = float(cfg_get(cfg, "spread_short_min_pct")) / 100.0
    hi_gap = float(cfg_get(cfg, "spread_short_max_pct")) / 100.0
    combo_cap = int(cfg_get(cfg, "max_spread_combos"))
    calls = rows_for(ch, exp, "call")
    longs = [r for r in calls
             if _num(r.get("strike")) is not None
             and abs(_num(r["strike"]) / spot - 1.0) <= band
             and quote_quality(r, cfg, spot=spot, side="call")["priceable"]]
    out, combos = [], 0
    for lr in longs:
        k1 = _num(lr["strike"])
        q1 = quote_quality(lr, cfg, spot=spot, side="call")
        for sr in calls:
            k2 = _num(sr.get("strike"))
            if k2 is None or k2 <= k1:
                continue
            gap = k2 / k1 - 1.0
            if gap < lo_gap or gap > hi_gap:
                continue
            q2 = quote_quality(sr, cfg, spot=spot, side="call")
            if not q2["priceable"] or not q2["bid"]:
                continue
            combos += 1
            if combos > combo_cap:
                break
            built = st.bull_call_spread(spot, capital, k1, q1["ask"], k2,
                                        q2["bid"], sc.get("prices") or {},
                                        probs, years, rate_pct=rate_pct)
            if not built.get("eligible"):
                continue
            built["contract"] = {**(built.get("contract") or {}),
                                 "expiration": exp, "dte": dte}
            built["liquidity"] = {"long": q1, "short": q2,
                                  "ok": q1["ok"] and q2["ok"],
                                  "label": "; ".join(
                                      q1["notes"] + q2["notes"]) or "tradeable"}
            built["expiration"] = exp
            built["dte"] = dte
            out.append(built)
        if combos > combo_cap:
            break
    return out


def _best(rows, prefer_liquid: bool = True):
    """Highest return on the FULL comparison capital, tradeable ones first."""
    usable = [r for r in rows or [] if r.get("eligible")
              and _num(r.get("weighted_annualized_pct")) is not None]
    if not usable:
        return None
    liquid = [r for r in usable if ((r.get("liquidity") or {}).get("ok"))]
    pool = liquid if (prefer_liquid and liquid) else usable
    return max(pool, key=lambda r: _num(r.get("weighted_annualized_pct")))


# ══════════════════════════════════════════════════════════════════════════
# THE COMPARISON, THE OPTIMIZERS AND THE ENTRY VERDICT
# ══════════════════════════════════════════════════════════════════════════

ENTRY_VERDICTS = ("BUY SHARES", "SELL PORTFOLIO SECURED PUT", "BUY LEAPS",
                  "BUY-WRITE", "BULL CALL SPREAD", "WAIT", "AVOID", "TOSS UP",
                  "SPECIALIZED MODEL REQUIRED", "INSUFFICIENT DATA")

_KIND_TO_VERDICT = {
    st.SHARES: "BUY SHARES",
    st.PUT: "SELL PORTFOLIO SECURED PUT",
    st.LEAPS: "BUY LEAPS",
    st.BUY_WRITE: "BUY-WRITE",
    st.SPREAD: "BULL CALL SPREAD",
}

VERDICT_DEFAULTS = {
    "min_put_premium_over_treasury_pp": 1.0,
    "max_leaps_expirations": 6,
    "max_csp_expirations": 8,
}


def _vd(cfg, key):
    v = (cfg or {}).get(key)
    return VERDICT_DEFAULTS[key] if v is None else v


def required_put_bid(capital, strike, expected_obligation, years, rate_pct,
                     target_pct) -> float | None:
    """The bid at which a put would clear the hurdle, in dollars per share.

    Inverting the comparison rather than scanning for it: the wealth of a
    secured put is (capital + premium) × (1+r)^T − the expected obligation,
    so the premium that reaches a target annualized return is plain algebra.
    This is the number the WAIT sentence quotes, so it has to be exact rather
    than "a bit more than this".
    """
    c, y = _num(capital), _num(years)
    obl, tgt = _num(expected_obligation), _num(target_pct)
    if c is None or y is None or y <= 0 or obl is None or tgt is None:
        return None
    g = st.growth_factor(rate_pct, y)
    if g <= 0:
        return None
    target_wealth = c * ((1.0 + tgt / 100.0) ** y)
    premium_dollars = (target_wealth + obl) / g - c
    return premium_dollars / st.CONTRACT_MULTIPLIER


def _expected_obligation(row: dict, probs: dict) -> float | None:
    total = 0.0
    for s in SCENARIOS:
        cell = (row.get("terminal") or {}).get(s) or {}
        stp = _num(cell.get("stock_price"))
        k = _num((row.get("contract") or {}).get("strike"))
        if stp is None or k is None:
            return None
        total += float(probs.get(s) or 0.0) * max(0.0, k - stp) \
            * st.CONTRACT_MULTIPLIER
    return total


def best_short_put(symbol, ch, spot, buy_zone, path, probs, cfg=None,
                   market=None, today=None) -> dict:
    """The short-dated put optimizer.

    Nothing here is decided by a delta band, an implied-volatility rank or a
    days-to-expiration rule. Those are all displayed — they are useful to
    read — but the choice is made on what the whole account is worth, which
    is a measurement rather than a convention.
    """
    cfg = cfg or {}
    zone = _num(buy_zone)
    out = {"available": False, "candidates": [], "best": None, "reason": "",
           "buy_zone": zone}
    if zone is None:
        out["reason"] = ("There is no buy zone yet, and the acquisition price "
                         "has to come before the strike. A put chosen without "
                         "one is a premium trade wearing an investment label.")
        return out
    lo = float(cfg_get(cfg, "csp_min_dte"))
    hi = float(cfg_get(cfg, "csp_max_dte"))
    exps = expirations_between(ch, lo, hi, today)[:int(_vd(cfg, "max_csp_expirations"))]
    if not exps:
        out["reason"] = (f"No expirations between {lo:.0f} and {hi:.0f} days "
                         f"are listed in the chain this dashboard can see.")
        return out
    rows = []
    for exp, dte in exps:
        rows.extend(put_candidates(symbol, ch, exp, dte, spot, zone, path,
                                   probs, cfg, market=market))
    if not rows:
        out["reason"] = (f"No put is listed at or below the ${zone:,.2f} buy "
                         f"zone with a bid on it. Raising the strike to find "
                         f"premium would be selling a put above the price "
                         f"this analysis says the shares are worth, which is "
                         f"the opposite of the point.")
        out["headline"] = "WAIT — INSUFFICIENT PREMIUM AT DESIRED OWNERSHIP PRICE"
        return out
    best = _best(rows)
    out["candidates"] = sorted(
        rows, key=lambda r: -(_num(r.get("weighted_annualized_pct")) or -1e9))[:24]
    out["best"] = best
    out["available"] = best is not None
    if best is None:
        out["reason"] = ("Puts exist at or below the buy zone but none has a "
                         "usable two-sided quote.")
        out["headline"] = "WAIT — INSUFFICIENT PREMIUM AT DESIRED OWNERSHIP PRICE"
        return out

    # Is the premium worth the capital it ties up? Against the Treasury
    # yield, which is what the same cash would earn doing nothing.
    rate = rate_for(best.get("dte", 30) / 365.0)
    hurdle = (_num(rate.get("pct")) or 0.0) \
        + float(_vd(cfg, "min_put_premium_over_treasury_pp"))
    got = _num(best.get("weighted_annualized_pct"))
    out["hurdle_pct"] = hurdle
    out["clears_hurdle"] = bool(got is not None and got >= hurdle)
    obl = _expected_obligation(best, probs)
    capital = st.comparison_capital(spot)
    need = required_put_bid(capital, (best.get("contract") or {}).get("strike"),
                            obl, (best.get("dte") or 30) / 365.0,
                            rate.get("pct"), hurdle)
    out["required_bid"] = need
    if not out["clears_hurdle"]:
        out["headline"] = "WAIT — INSUFFICIENT PREMIUM AT DESIRED OWNERSHIP PRICE"
        out["reason"] = (
            f"The best put at or below the buy zone is the "
            f"${(best.get('contract') or {}).get('strike'):,.2f} strike "
            f"expiring {_pretty(best.get('expiration'))}, and on the full "
            f"${capital:,.0f} of strike notional it works out at "
            f"{got:.2f}% a year against {hurdle:.2f}% for simply holding the "
            f"cash. It is not worth the obligation."
            + (f" Reconsider if the bid rises above ${need:,.2f}."
               if need is not None else ""))
    return out


def best_leaps_expirations(ch, cfg=None, today=None) -> list:
    lo = float(cfg_get(cfg or {}, "leaps_min_dte"))
    hi = float(cfg_get(cfg or {}, "leaps_max_dte"))
    return expirations_between(ch, lo, hi, today)[
        :int(_vd(cfg or {}, "max_leaps_expirations"))]


def build_comparison(symbol, ch, spot, buy_zone, path, probs, cfg=None,
                     today=None, next_earnings=None, bars=None) -> dict:
    """The Structure Comparator, at ONE long-dated expiration.

    Every long expiration in the window is priced, the best LEAPS across all
    of them decides the comparison date, and then SHARES, the put, the
    buy-write and the spread are all marked at that same date. That is what
    makes the ranking a comparison rather than five separate calculations
    printed next to each other.
    """
    cfg = cfg or {}
    out = {"available": False, "reason": "", "expiration": None, "dte": None,
           "rows": [], "leaps_pool": []}
    exps = best_leaps_expirations(ch, cfg, today)
    if not exps:
        lo = float(cfg_get(cfg, "leaps_min_dte"))
        hi = float(cfg_get(cfg, "leaps_max_dte"))
        out["reason"] = (f"No expiration between {lo:.0f} and {hi:.0f} days "
                         f"out is listed for {symbol}. Long-dated contracts "
                         f"do not exist on every ticker.")
        return out
    pool = []
    for exp, dte in exps:
        pool.extend(call_candidates(symbol, ch, exp, dte, spot, path, probs,
                                    cfg, kind=st.LEAPS))
    best_call = _best(pool)
    if best_call is None:
        out["reason"] = ("Long-dated calls are listed but none of them has a "
                         "usable offer.")
        return out
    exp = best_call["expiration"]
    dte = best_call["dte"]
    years = max(1e-6, dte / 365.0)
    sc = scenario_prices(path, years, cfg)
    if not sc.get("available"):
        out["reason"] = sc.get("reason") or "No scenario prices at this horizon."
        return out
    capital = st.comparison_capital(spot)
    rate_pct = (sc.get("rate") or {}).get("pct")

    rows = [
        st.shares_position(spot, capital, sc["prices"], probs, years,
                           dividend_fv_per_share=sc.get("dividends") or 0.0,
                           rate_pct=rate_pct),
        best_call,
    ]
    put_pool = put_candidates(symbol, ch, exp, dte, spot, buy_zone, path,
                              probs, cfg)
    best_put_here = _best(put_pool)
    if best_put_here is not None:
        rows.append(best_put_here)
    else:
        rows.append(st.unavailable(st.PUT, (
            f"No put at this expiration sits at or below the "
            f"${buy_zone:,.2f} buy zone with a bid on it."
            if _num(buy_zone) else
            "There is no buy zone, so no strike can be qualified.")))
    bw_pool = buy_write_candidates(symbol, ch, exp, dte, spot, path, probs, cfg)
    best_bw = _best(bw_pool)
    rows.append(best_bw if best_bw is not None else
                st.unavailable(st.BUY_WRITE,
                        "No call above the current price at this expiration "
                        "has a bid on it."))
    sp_pool = spread_candidates(symbol, ch, exp, dte, spot, path, probs, cfg)
    best_sp = _best(sp_pool)
    rows.append(best_sp if best_sp is not None else
                st.unavailable(st.SPREAD,
                        "No pair of quoted strikes at this expiration makes a "
                        "debit spread."))

    events = st.expected_events(next_earnings, exp,
                                (today or _today()).isoformat())
    for r in rows:
        r["events_crossed"] = events
    result = st.compare(rows, probs, years, cfg)
    result.update({
        "available": result.get("available"), "expiration": exp, "dte": dte,
        "years": years, "capital": capital, "rate": sc.get("rate"),
        "scenario_prices": sc.get("prices"), "dividends": sc.get("dividends"),
        "leaps_pool": sorted(
            pool, key=lambda r: -(_num(r.get("weighted_annualized_pct")) or -1e9))[:24],
        "events": events,
        "iv_context": (leaps_iv_history(symbol,
                                        dte,
                                        (best_call.get("contract") or {}).get("iv"),
                                        cfg)),
        "realized_vol": realized_vol_context(bars or [], dte),
        "downside_context": downside_context(
            spot, years, realized_vol_context(bars or [], dte),
            sc.get("prices")),
        "horizon_note": (
            f"Every row is marked on {_pretty(exp)}, {dte:.0f} days out, on "
            f"the same ${capital:,.0f} account and the same three scenario "
            f"prices. Cash a structure does not spend earns "
            f"{(_num(rate_pct) or 0):.2f}% — the matching Treasury yield."),
    })
    return result


def downside_context(spot, years, realized_vol: dict, scenario_prices: dict,
                     quantile: float = 0.05) -> dict:
    """What the fundamental bear scenario does NOT say.

    Over a year and a half the fundamental bear is a mild number: earnings
    barely move and a multiple only travels part of the way to its target. It
    is a view of the business, and a business view is not a price
    distribution. So the same horizon is also priced through a lognormal at
    this stock's own realized volatility over windows of that length, and the
    one-in-twenty outcome is printed beside the bear case.

    Neither number is the truth. Showing only the first one would let a
    structure that loses badly on a real fall look safe by construction.
    """
    s0 = _num(spot)
    y = _num(years)
    sigma = _num((realized_vol or {}).get("rv_tenor")) \
        or _num((realized_vol or {}).get("rv_1y"))
    bear = _num((scenario_prices or {}).get("bear"))
    if s0 is None or y is None or y <= 0 or sigma is None or sigma <= 0:
        return {"available": False,
                "reason": ((realized_vol or {}).get("reason")
                           or "No realized-volatility history for this ticker.")}
    z = 1.6448536269514722 if abs(quantile - 0.05) < 1e-9 else 1.6448536269514722
    drift = -0.5 * sigma * sigma * y
    tail = s0 * math.exp(drift - z * sigma * math.sqrt(y))
    return {
        "available": True, "reason": "",
        "sigma": sigma, "years": y, "quantile": quantile,
        "tail_price": tail, "tail_pct": (tail / s0 - 1.0) * 100.0,
        "bear_price": bear,
        "bear_pct": None if bear is None else (bear / s0 - 1.0) * 100.0,
        "note": (f"The fundamental bear case is a view of the BUSINESS. A "
                 f"lognormal at this stock's own {sigma * 100:.0f}% realized "
                 f"volatility over windows this long puts a one-in-twenty "
                 f"outcome at ${tail:,.2f}, "
                 f"{(tail / s0 - 1.0) * 100.0:.0f}% below today. The "
                 f"structures below are ranked on the fundamental scenarios; "
                 f"this is what the same horizon looks like as a "
                 f"distribution, and the two answer different questions."),
    }


def _pretty(iso) -> str:
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%B %-d, %Y")
    except (TypeError, ValueError):
        return str(iso or "")


def entry_verdict(snap: dict, fair: dict, comparison: dict, put_block: dict,
                  cfg=None) -> dict:
    """BUY SHARES / SELL PORTFOLIO SECURED PUT / BUY LEAPS / BUY-WRITE /
    BULL CALL SPREAD / WAIT / AVOID / TOSS UP.

    Every WAIT, AVOID and TOSS UP names the exact thing that would change it.
    A verdict that cannot say what it is waiting for is a mood.
    """
    cfg = cfg or {}
    reasons: list[str] = []
    changes: list[str] = []
    btype = (snap.get("business_type") or {}).get("type")
    trap = (snap.get("value_trap") or {}).get("level")
    phase2 = (snap.get("verdict") or {}).get("verdict")
    price = _num(snap.get("price"))

    def done(label, blocked_by=None):
        return {"verdict": label, "reasons": reasons,
                "what_would_change": changes, "blocked_by": blocked_by,
                "version": OPTIONS_VERSION}

    if btype in ("BANK", "INSURANCE", "BROKER", "REIT"):
        reasons.append((snap.get("business_type") or {}).get("note") or "")
        changes.append("A model built for this business type — book value and "
                       "net interest margin for a lender, funds from "
                       "operations for a property trust — would have to exist "
                       "first. Forcing the generic one on it would produce a "
                       "fair value with nothing behind it.")
        return done("SPECIALIZED MODEL REQUIRED", "business type")

    if price is None or price <= 0:
        reasons.append("There is no current share price.")
        changes.append("A live quote has to be available before any structure "
                       "can be priced.")
        return done("INSUFFICIENT DATA", "price")

    if trap == "HIGH RISK":
        reasons.append(f"Value trap risk is HIGH — "
                       + "; ".join(a.get("label", "").lower()
                                   for a in ((snap.get("value_trap") or {})
                                             .get("active") or [])) + ".")
        changes.append("Nothing bullish is recommended while several "
                       "deterioration signals fire at once. Those signals "
                       "would have to stop before cheapness counts as an "
                       "opportunity.")
        return done("AVOID", "value trap")

    if phase2 == "AVOID":
        for r in (snap.get("verdict") or {}).get("reasons") or []:
            reasons.append(r)
        changes.extend((snap.get("verdict") or {}).get("what_would_change") or [])
        return done("AVOID", "business verdict")

    # Stated here rather than inherited from the business verdict above. Every
    # method that could produce a fair value prices earnings or cash
    # generation, so a loss-maker has nothing to price — and the honest answer
    # to that is AVOID, not the WAIT that a missing fair value would otherwise
    # fall through to.
    if btype == "UNPROFITABLE":
        eps = _num(snap.get("eps_ttm"))
        reasons.append(
            f"The company is losing money — trailing earnings are "
            f"${eps:,.2f} a share." if eps is not None else
            "The company is not profitable.")
        changes.append("It would have to earn a profit. Every valuation "
                       "method here prices earnings or cash generation, and "
                       "there is none to price.")
        return done("AVOID", "unprofitable")

    if not fair or not fair.get("available"):
        reasons.append((fair or {}).get("reason")
                       or "No fair value could be built.")
        changes.append("A defensible Bear/Base/Bull needs either a long "
                       "enough valuation history, a real peer group, or "
                       "positive normalized cash flow. None of them is "
                       "available for this ticker yet.")
        return done("WAIT", "fair value")

    conf = fair.get("confidence_level")
    zone = _num(fair.get("buy_zone"))
    if conf == "UNRELIABLE":
        reasons.append((fair.get("confidence") or {}).get("reason") or "")
        changes.append("The valuation methods would have to converge. Until "
                       "they do, there is no precise discount to act on and "
                       "pretending otherwise would be the most expensive kind "
                       "of confidence.")
        return done("WAIT", "fair value confidence")

    if zone is None:
        reasons.append("A buy zone could not be computed.")
        changes.append("A pessimistic value and a credited value are both "
                       "needed to set the price worth paying.")
        return done("WAIT", "buy zone")

    reasons.append(f"Fair value {conf} confidence — Bear "
                   f"${_num(fair.get('bear')) or 0:,.2f}, Base "
                   f"${_num(fair.get('base')) or 0:,.2f}, Bull "
                   f"${_num(fair.get('bull')) or 0:,.2f}, on "
                   f"{fair.get('base_method_label')}.")
    reasons.append(f"Buy zone ${zone:,.2f} against a price of ${price:,.2f}.")

    if price <= zone:
        if not (comparison or {}).get("available"):
            reasons.append((comparison or {}).get("reason") or
                           "No option chain is available for this ticker.")
            changes.append("The price is inside the buy zone, so the shares "
                           "themselves qualify. A structure comparison needs "
                           "an option chain, which is not available here.")
            return done("BUY SHARES", None)
        if comparison.get("toss_up"):
            reasons.append((comparison.get("sensitivity") or {}).get("reason") or "")
            changes.append(
                "The ranking is not robust to the scenario weights, which are "
                "assumptions rather than measurements. Moving them by a few "
                "points changes the answer, so the honest reading is that "
                "these structures are equivalent at this price. Choose on "
                "what you want the position to DO — shares for the business, "
                "a call for a bounded loss — rather than on the ranking.")
            return done("TOSS UP", "probability sensitivity")
        pref = comparison.get("preferred")
        top = next((r for r in comparison.get("rows") or []
                    if r.get("kind") == pref), None)
        if top:
            reasons.append(
                f"On identical capital to {_pretty(comparison.get('expiration'))}, "
                f"{pref} returns {_num(top.get('weighted_annualized_pct')) or 0:.2f}% "
                f"a year probability-weighted, with a worst case of "
                f"${_num(top.get('worst_pnl')) or 0:,.0f} on "
                f"${_num(comparison.get('capital')) or 0:,.0f}.")
        changes.append(
            f"This stops being the answer if the price rises back above the "
            f"${zone:,.2f} buy zone, if the value-trap check starts firing, "
            f"or if the fair value methods diverge enough to drop confidence "
            f"below its current {conf}.")
        return done(_KIND_TO_VERDICT.get(pref, "WAIT"), None)

    # Above the buy zone. The put is the only structure that gets paid to
    # wait at the price we actually want.
    gap = (price / zone - 1.0) * 100.0
    best_put = (put_block or {}).get("best")
    if best_put and (put_block or {}).get("clears_hurdle"):
        k = _num((best_put.get("contract") or {}).get("strike"))
        cr = _num((best_put.get("contract") or {}).get("credit"))
        reasons.append(
            f"The price is {gap:.1f}% above the buy zone, so the shares are "
            f"not a buy here. The ${k:,.2f} put expiring "
            f"{_pretty(best_put.get('expiration'))} sits at or below the buy "
            f"zone and pays ${cr:,.2f}, an effective purchase price of "
            f"${k - cr:,.2f} if it is assigned.")
        changes.append(
            f"This becomes BUY SHARES if the price falls to ${zone:,.2f}. It "
            f"becomes WAIT if the bid on that strike falls below "
            f"${(_num((put_block or {}).get('required_bid')) or 0):,.2f}, "
            f"where the premium stops paying for the obligation.")
        return done("SELL PORTFOLIO SECURED PUT", None)

    reasons.append(f"The price is {gap:.1f}% above the buy zone.")
    if (put_block or {}).get("reason"):
        reasons.append(put_block["reason"])
    changes.append(_wait_line(fair, price, zone, put_block))
    return done("WAIT", "price above buy zone")


def _wait_line(fair: dict, price: float, zone: float, put_block: dict) -> str:
    """The exact two numbers that would turn WAIT into something else."""
    mos = _num(fair.get("margin_of_safety")) or 0.0
    credit = _num(fair.get("confidence_credit"))
    bear = _num(fair.get("bear"))
    bull = _num(fair.get("bull"))
    bits = [f"Buy zone is ${zone:,.2f}. Current price is ${price:,.2f}. "
            f"Reconsider below ${zone:,.2f}"]
    if credit and credit > 0 and bear is not None and mos < 1.0:
        needed_credited = price / (1.0 - mos)
        needed_base = bear + (needed_credited - bear) / credit
        if bull is not None and needed_base > bull:
            # Quoting a base value above the OPTIMISTIC case as a trigger
            # reads like a live possibility and is not one. Say what it
            # actually means instead.
            bits.append(f", or if the valuation methods converge — at the "
                        f"current {fair.get('confidence_level')} confidence "
                        f"the base value would have to reach "
                        f"${needed_base:,.2f}, which is above the "
                        f"${bull:,.2f} optimistic case, so raising the base "
                        f"alone will not do it")
        else:
            bits.append(f", or if base fair value rises above "
                        f"${needed_base:,.2f} at the current "
                        f"{fair.get('confidence_level')} confidence")
    elif credit == 0:
        bits.append(", and with confidence at UNRELIABLE no rise in the base "
                    "value alone would move it — the methods have to agree "
                    "first")
    need = _num((put_block or {}).get("required_bid"))
    if need is not None:
        bits.append(f". A put at the buy zone would qualify if its bid rose "
                    f"above ${need:,.2f}")
    return "".join(bits) + "."


# ── management plan ─────────────────────────────────────────────────────────

def management_plan(verdict_label: str, snap: dict, fair: dict,
                    comparison: dict, put_block: dict) -> dict:
    """What would end the position, decided before it is opened.

    No broker execution, no alerts, no automation — a written condition per
    exit, so the decision to close is made on the thesis rather than on the
    screen colour of the day.
    """
    base = _num(fair.get("base")) if fair else None
    zone = _num(fair.get("buy_zone")) if fair else None
    common = [
        {"trigger": "Thesis invalidation",
         "detail": ("The quality inputs behind this — return on invested "
                    "capital, cash conversion, the operating margin trend — "
                    "turn down together. One weak quarter is noise; the "
                    "trend moving is the thesis changing.")},
        {"trigger": "Fair value reached",
         "detail": (f"The price reaches the base fair value of "
                    f"${base:,.2f}. That is where the discount this position "
                    f"was opened for has been paid out."
                    if base else "The price reaches base fair value.")},
        {"trigger": "Estimate revisions deteriorate",
         "detail": ("Analysts start cutting rather than raising, with enough "
                    "coverage for it to mean anything. Falling estimates "
                    "against a rising price is the sequence that turns a "
                    "holding into a hope.")},
        {"trigger": "Value trap check flips",
         "detail": ("The deterioration check moves to HIGH RISK. That is the "
                    "one condition under which this dashboard will not call "
                    "anything bullish, and it applies after entry as much as "
                    "before it.")},
    ]
    out = {"available": True, "verdict": verdict_label, "common": common,
           "specific": [], "note": ("No orders are placed anywhere in this "
                                    "dashboard. This is a written plan, not "
                                    "an automation.")}

    if verdict_label == "SELL PORTFOLIO SECURED PUT":
        best = (put_block or {}).get("best") or {}
        k = _num((best.get("contract") or {}).get("strike"))
        cr = _num((best.get("contract") or {}).get("credit"))
        notional = _num(best.get("notional"))
        out["specific"] = [
            {"trigger": "Full notional exposure",
             "detail": (f"${notional:,.0f} — strike × 100. That is what can "
                        f"be put to you, and it is the number the position "
                        f"should be sized against, never the buying-power "
                        f"reduction the broker shows."
                        if notional else "Strike × 100.")},
            {"trigger": "Assignment price",
             "detail": (f"${k:,.2f} less the ${cr:,.2f} already received — an "
                        f"effective ${k - cr:,.2f} a share."
                        if k is not None and cr is not None else "")},
            {"trigger": "Rolling",
             "detail": ("If the thesis is intact, rolling down and out for a "
                        "NET CREDIT may be considered. If the thesis has "
                        "broken, do not roll simply to avoid realizing the "
                        "loss — that converts a decision into a habit.")},
            {"trigger": "If assigned",
             "detail": ("Feed the shares into the covered-call workflow this "
                        "dashboard already has. There is deliberately no "
                        "second call-selling engine here.")},
        ]
    elif verdict_label == "BUY LEAPS":
        top = next((r for r in (comparison or {}).get("rows") or []
                    if r.get("kind") == st.LEAPS), None) or {}
        c = top.get("contract") or {}
        out["specific"] = [
            {"trigger": "Time to expiration",
             "detail": (f"Expires {_pretty(c.get('expiration'))}. Extrinsic "
                        f"value decays fastest in the last few months, so a "
                        f"decision belongs well before then, not at the end."
                        if c.get("expiration") else "")},
            {"trigger": "Base fair value reached",
             "detail": (f"At ${base:,.2f} the position has done what it was "
                        f"opened to do." if base else "")},
            {"trigger": "Do not hold mechanically",
             "detail": ("A long call held to expiration because it was bought "
                        "to be held to expiration is not a plan. If the "
                        "fundamental thesis changes, the contract is closed "
                        "on the thesis, not on the calendar.")},
            {"trigger": "No dividend",
             "detail": ("A call receives none of the dividends the shares "
                        "would have paid over the same period.")},
        ]
    elif verdict_label == "BUY-WRITE":
        top = next((r for r in (comparison or {}).get("rows") or []
                    if r.get("kind") == st.BUY_WRITE), None) or {}
        c = top.get("contract") or {}
        out["specific"] = [
            {"trigger": "Upside cap",
             "detail": (f"Gains stop at the ${_num(c.get('call_strike')) or 0:,.2f} "
                        f"strike. Above it the shares are called away.")},
            {"trigger": "Early assignment",
             "detail": ("Where the call is in the money and its remaining "
                        "extrinsic value is smaller than the dividend due, a "
                        "holder can exercise early to capture the dividend."
                        if c.get("early_assignment_risk") else
                        "The call's extrinsic value currently exceeds the "
                        "dividends due before expiration, so early exercise "
                        "would cost the holder money.")},
            {"trigger": "One expiration only",
             "detail": ("This is a single covered call, not a model of "
                        "selling one every week. That path depends on where "
                        "the stock went between rolls and belongs in a "
                        "simulator, not in a comparison table.")},
        ]
    elif verdict_label == "BUY SHARES":
        out["specific"] = [
            {"trigger": "Add-down level",
             "detail": (f"The buy zone is ${zone:,.2f}. Below it the same "
                        f"analysis says the same thing more strongly — which "
                        f"is only true while the fair value inputs are "
                        f"unchanged." if zone else "")},
        ]
    elif verdict_label == "BULL CALL SPREAD":
        top = next((r for r in (comparison or {}).get("rows") or []
                    if r.get("kind") == st.SPREAD), None) or {}
        c = top.get("contract") or {}
        out["specific"] = [
            {"trigger": "Bounded on both sides",
             "detail": (f"The most this can make is "
                        f"${_num(c.get('max_gain')) or 0:,.0f} and the most it "
                        f"can lose is ${_num(c.get('debit_dollars')) or 0:,.0f}. "
                        f"Neither number moves with the thesis.")},
        ]
    else:
        out["available"] = False
        out["reason"] = ("No position is recommended, so there is nothing to "
                         "manage. The conditions above are what would change "
                         "the answer.")
    return out


# ══════════════════════════════════════════════════════════════════════════
# THE PAYLOAD
# ══════════════════════════════════════════════════════════════════════════

def build(symbol: str, snap: dict, fair: dict, path: dict, cfg=None,
          probabilities=None, record: bool = False) -> dict:
    """Everything Phase 3 adds for one ticker, assembled once."""
    cfg = cfg or {}
    sym = (symbol or "").upper().strip()
    probs = probabilities or fv.scenario_probabilities(cfg)
    today = _today()
    out = {"symbol": sym, "available": False, "reason": "",
           "probabilities": probs, "version": OPTIONS_VERSION,
           "comparison": {"available": False}, "put": {"available": False},
           "market_risk": {"available": False}}

    spot = _num(snap.get("price"))
    btype = (snap.get("business_type") or {}).get("type")
    if btype in ("BANK", "INSURANCE", "BROKER", "REIT"):
        out["reason"] = (snap.get("business_type") or {}).get("note") or ""
        out["entry"] = entry_verdict(snap, fair, out["comparison"], out["put"], cfg)
        out["plan"] = management_plan(out["entry"]["verdict"], snap, fair,
                                      out["comparison"], out["put"])
        return out
    if not available():
        out["reason"] = ("No option chain provider is wired, so no structure "
                         "can be priced.")
        out["entry"] = entry_verdict(snap, fair, out["comparison"], out["put"], cfg)
        out["plan"] = management_plan(out["entry"]["verdict"], snap, fair,
                                      out["comparison"], out["put"])
        return out

    ch = chain(sym)
    if not ch:
        out["reason"] = (f"No option chain is available for {sym}. Some "
                         f"tickers have no listed options at all, and the "
                         f"chain provider is not always reachable.")
        out["entry"] = entry_verdict(snap, fair, out["comparison"], out["put"], cfg)
        out["plan"] = management_plan(out["entry"]["verdict"], snap, fair,
                                      out["comparison"], out["put"])
        return out
    spot = spot or spot_of(ch)
    bars = _bars(sym, 1100)
    nxt = None
    if _EARNINGS_FN is not None:
        try:
            nxt = (_EARNINGS_FN(sym) or {}).get("next")
        except Exception:                            # noqa: BLE001
            nxt = None
    market = market_risk(sym, ch, bars, today, nxt)
    zone = _num((fair or {}).get("buy_zone"))

    comparison = build_comparison(sym, ch, spot, zone, path, probs, cfg,
                                  today=today, next_earnings=nxt, bars=bars)
    put_block = best_short_put(sym, ch, spot, zone, path, probs, cfg,
                               market=market, today=today)
    out.update({"available": True, "spot": spot,
                "market_risk": market, "comparison": comparison,
                "put": put_block, "expirations_seen": len(ch.get("chains") or {}),
                "chain_source": ch.get("source")})
    out["entry"] = entry_verdict(snap, fair, comparison, put_block, cfg)
    out["plan"] = management_plan(out["entry"]["verdict"], snap, fair,
                                  comparison, put_block)

    if record:
        try:
            rows = []
            for r in (comparison.get("leaps_pool") or [])[:12]:
                c = r.get("contract") or {}
                rows.append({"exp": r.get("expiration"), "dte": r.get("dte"),
                             "strike": c.get("strike"), "iv": c.get("iv"),
                             "delta": c.get("delta"),
                             "mid": (r.get("liquidity") or {}).get("mid"),
                             "open_interest":
                                 (r.get("liquidity") or {}).get("open_interest")})
            if rows:
                record_leaps_observation(sym, spot, rows, today.isoformat())
        except Exception:                            # noqa: BLE001
            pass
    return out
