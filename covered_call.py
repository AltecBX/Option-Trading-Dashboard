"""covered_call.py — what repeatedly selling calls against 100 shares
actually does over time (Phase 4).

The Phase 3 comparator prices a buy-write at ONE expiration and stops there,
which is the honest thing to do when the question is "which structure should
I open today". It is not the question people actually ask about covered
calls. That question is: if I own a hundred shares and keep selling calls
against them, month after month, where do I end up — and is it better than
having done nothing?

Those are different questions because the strategy is PATH DEPENDENT. A call
sold in January caps a rally that happens in February; the shares are then
either assigned away at the strike, or bought back at a loss, or rolled into
a further-out call whose credit does not cover the loss on the one it
replaced. None of that is visible in a single-expiration comparison, and
repeating one expiration's result twelve times is not a model of it. This
module walks the actual lifecycle, day by day:

    own shares → sell a call → the call expires, is assigned, is bought back,
    or is rolled → sell the next call

WHAT IT REFUSES TO DO

A roll never erases a loss. Rolling is closing one option and opening
another, and this records both legs: the realized loss on the option being
closed stays in the ledger permanently, and the credit on the new one is a
separate event. A simulator that nets them into "rolled for a credit" is
reporting a profit it did not make.

An option win rate is never presented as a result. A ninety-five percent win
rate on calls that repeatedly cap a rising stock loses to owning the shares,
and that is the comparison this reports: terminal wealth against buy and
hold, on identical starting capital. The win rate is one line of context
inside that.

No delta, tenor or roll rule is declared best. The point of a simulator is
to test them, so `compare_policies` runs them side by side and reports what
happened, with the sample size that says how much to trust it.

REAL AGAINST MODELLED

Option fills come from `chain_store` — the app's own end-of-day chain
snapshots — wherever a snapshot exists for that day, and from Black-Scholes
against a modelled volatility path everywhere else. Every fill is labelled,
and the result carries the share of fills that were real. A run whose fills
were all modelled is reported as MODEL-BASED ESTIMATE and never as a
backtest. Historical option quotes are never invented: where there is no
snapshot, the price is a model output and says so.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import bt_options as bto
import chain_store
import metrics as mx

COVERED_CALL_VERSION = "invest-coveredcall-1.0.0"

CONTRACT = bto.CONTRACT

# ── the policy space ────────────────────────────────────────────────────────

TENORS = {
    "WEEKLY": {"label": "Weekly", "target_dte": 7,
               "note": "About one week to expiration."},
    "BIWEEKLY": {"label": "Fourteen to twenty-one days", "target_dte": 17,
                 "note": "Between two and three weeks to expiration."},
    "MONTHLY": {"label": "Thirty to forty-five days", "target_dte": 38,
                "note": "Between one and one and a half months."},
}

STRIKE_RULES = {
    "DELTA": {"label": "Delta target",
              "note": "The strike whose call delta is closest to the target. "
                      "No delta is treated as correct here; it is a setting "
                      "to be tested."},
    "PERCENT": {"label": "Percent above the share price",
                "note": "A fixed distance above the price on the day the "
                        "call is sold."},
    "FAIR_VALUE": {"label": "Never below fair value",
                   "note": "The strike is pushed up to the base fair value "
                           "when a delta or percentage rule would have put "
                           "it below. Selling a call below what the business "
                           "is worth is agreeing to hand it over cheaply for "
                           "a premium."},
    "BUY_ZONE": {"label": "Never below the credited fair value",
                 "note": "The strictest version: the strike is pushed up to "
                         "the credited fair value — the confidence-adjusted "
                         "figure the buy zone is built from."},
}

ROLL_RULES = {
    "HOLD": {"label": "Hold to expiration",
             "note": "Never rolled. The call runs to expiration and is "
                     "assigned or expires."},
    "DTE": {"label": "Roll near expiration",
            "note": "Closed and replaced once it is inside the roll window, "
                    "whether it is winning or losing."},
    "DELTA": {"label": "Roll when it goes in the money",
              "note": "Closed and replaced once the call's delta passes the "
                      "threshold — that is, once assignment becomes likely."},
    "BELOW_FAIR_VALUE": {"label": "Roll when the strike falls below fair value",
                         "note": "Closed and replaced when a rising fair "
                                 "value leaves the strike below what the "
                                 "business is now judged to be worth."},
}

ASSIGNMENT_MODES = {
    "END": {"label": "Stop when assigned",
            "note": "The shares are sold at the strike and the run ends "
                    "there, holding cash."},
    "RE_ENTER": {"label": "Buy back only when the price is right",
                 "note": "After assignment the shares are bought back only "
                         "when the price is at or below the buy zone — the "
                         "same test the Investment tab applies to a new "
                         "purchase. Buying back the next morning regardless "
                         "of price is not re-entry, it is a round trip paid "
                         "for in commissions."},
    "RE_ENTER_ALWAYS": {"label": "Always buy back",
                        "note": "Shares are repurchased at the next open "
                                "whatever the price. Included so the "
                                "valuation-aware rule has something to be "
                                "measured against, not as a recommendation."},
}

DEFAULTS = {
    "cc_delta_target": 0.30,
    "cc_percent_otm": 0.05,
    "cc_roll_dte": 3,
    "cc_roll_delta": 0.70,
    "cc_min_otm_pct": 0.0,
    "cc_cash_rate_pct": 4.0,
    "cc_roll_only_for_credit": False,
    # A fill is only taken when the modelled or quoted premium is worth
    # taking; below this the call is skipped and the reason recorded.
    "cc_min_credit": 0.05,
    "cc_dte_tolerance": 0.6,
}

REAL = "REAL CHAIN"
MODEL = "MODEL"

BASIS_REAL = "REAL CHAIN BACKTEST"
BASIS_MIXED = "PART REAL CHAIN, PART MODEL-BASED ESTIMATE"
BASIS_MODEL = "MODEL-BASED ESTIMATE"


def cfg_get(cfg, key):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _d(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


# ── option pricing for one day ──────────────────────────────────────────────

def quote(sym_store: dict, day: str, strike: float, expiry: str, spot: float,
          iv: float, rate_pct: float, div_yield: float = 0.0) -> dict:
    """One call's price on one day, from a real snapshot where one exists.

    The returned `price` is a MID. The side of the market actually paid is
    applied by the caller through `bt_options.option_spread`, so selling and
    buying the same contract on the same day correctly costs something.
    """
    dte = 0.0
    d0, d1 = _d(day), _d(expiry)
    if d0 and d1:
        dte = max(0.0, (d1 - d0).days)
    real = chain_store.lookup(sym_store or {}, day, "call", strike, dte)
    if real and real.get("mid"):
        return {"price": float(real["mid"]), "source": REAL,
                "iv": real.get("iv"), "dte": dte,
                "strike": real.get("strike", strike),
                "expiry": real.get("expiry", expiry)}
    t = max(dte, 0.0) / 365.0
    px = mx._bs_price(spot, strike, t, max(iv or 0.0, 1e-6), "call",
                      r=rate_pct / 100.0, q=div_yield)
    return {"price": max(0.0, px), "source": MODEL, "iv": iv, "dte": dte,
            "strike": strike, "expiry": expiry}


def _delta(spot, strike, dte, iv, rate_pct, div_yield=0.0) -> float:
    return mx._bs_delta(spot, strike, max(dte, 0.0) / 365.0,
                        max(iv or 0.0, 1e-6), "call",
                        r=rate_pct / 100.0, q=div_yield)


# ── choosing the contract ───────────────────────────────────────────────────

def pick_expiry(day: str, target_dte: int, expiries=None, cfg=None) -> str | None:
    """The expiration to sell into. Real listed expirations where the caller
    supplied them, otherwise the nearest Friday to the target."""
    cfg = cfg or {}
    d0 = _d(day)
    if d0 is None:
        return None
    if expiries:
        tol = float(cfg_get(cfg, "cc_dte_tolerance"))
        best = None
        for e in expiries:
            de = _d(e)
            if de is None:
                continue
            dte = (de - d0).days
            if dte <= 0 or abs(dte - target_dte) > max(3, target_dte * tol):
                continue
            score = abs(dte - target_dte)
            if best is None or score < best[0]:
                best = (score, e)
        return best[1] if best else None
    # Weekly options settle on Fridays; the nearest Friday to the target is
    # the honest stand-in when the caller has no expiration calendar.
    d = d0 + timedelta(days=int(target_dte))
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d.isoformat()


def pick_strike(rule: str, spot: float, dte: float, iv: float,
                rate_pct: float, fair_base=None, fair_credited=None,
                cfg=None, div_yield: float = 0.0) -> dict:
    """The strike to sell, and the reason it is that one.

    The fair-value rules never LOWER a strike. They raise a strike that a
    delta or percentage rule put below what the business is judged to be
    worth, because agreeing to sell a business below its value in exchange
    for a premium is a decision that should be made deliberately rather than
    fallen into by a delta setting.
    """
    cfg = cfg or {}
    floor_pct = float(cfg_get(cfg, "cc_min_otm_pct"))
    base = None
    why = ""
    if rule in ("DELTA", "FAIR_VALUE", "BUY_ZONE"):
        target = float(cfg_get(cfg, "cc_delta_target"))
        base = bto.strike_by_delta(spot, max(iv or 0.0, 1e-6),
                                   max(dte, 1.0), target, "call")
        why = f"the strike nearest {target:.2f} delta"
    if rule == "PERCENT" or base is None:
        pct = float(cfg_get(cfg, "cc_percent_otm"))
        base = bto._snap(spot * (1.0 + pct))
        why = f"{pct * 100:.0f}% above the share price"
    if floor_pct > 0:
        floor = bto._snap(spot * (1.0 + floor_pct))
        if floor > base:
            base, why = floor, (f"{why}, raised to the "
                                f"{floor_pct * 100:.0f}% minimum distance "
                                f"above the price")
    raised = False
    if rule == "FAIR_VALUE" and _num(fair_base):
        f = bto._snap(_num(fair_base))
        if f > base:
            base, raised = f, True
            why = (f"{why}, raised to the ${f:,.2f} base fair value so the "
                   f"shares are never agreed away below what the business is "
                   f"judged to be worth")
    if rule == "BUY_ZONE" and _num(fair_credited):
        f = bto._snap(_num(fair_credited))
        if f > base:
            base, raised = f, True
            why = (f"{why}, raised to the ${f:,.2f} credited fair value")
    return {"strike": base, "reason": why, "raised_by_fair_value": raised}


# ── the ledger ──────────────────────────────────────────────────────────────

def _event(kind, day, **kw) -> dict:
    return {"kind": kind, "date": day, **kw}


def _daily_rate_factor(rate_pct: float) -> float:
    return (1.0 + max(0.0, rate_pct) / 100.0) ** (1.0 / 365.0)


def simulate(bars: list, iv_series: list, policy: dict, cfg=None,
             sym_store: dict | None = None, dividends: dict | None = None,
             dividend_rate_ttm=None, expiries_by_day: dict | None = None,
             fair_value_by_day: dict | None = None,
             rate_pct: float | None = None, contracts: int = 1) -> dict:
    """Walk one covered-call policy through a price history, day by day.

    `bars` is the app's standard daily series. `iv_series` is one implied
    volatility per bar — from `bt_iv.build_iv_series`, the same model the
    options backtester uses. `policy` names the tenor, strike rule, roll rule
    and what happens on assignment.

    Returns the full ledger, the closing position and the measured result,
    beside a buy-and-hold run over the same days on the same capital.
    """
    cfg = cfg or {}
    out = {"available": False, "reason": "", "version": COVERED_CALL_VERSION,
           "policy": dict(policy or {})}
    rows = []
    for i, b in enumerate(bars or []):
        day = str(b.get("date") or b.get("d") or "")[:10]
        close = _num(b.get("close") if b.get("close") is not None else b.get("c"))
        high = _num(b.get("high") if b.get("high") is not None else b.get("h"))
        if day and close and close > 0:
            rows.append({"date": day, "close": close, "high": high or close,
                         "iv": _num(iv_series[i]) if i < len(iv_series or [])
                         else None})
    if len(rows) < 30:
        out["reason"] = (f"Only {len(rows)} usable trading days are "
                         f"available. A covered-call run needs a price "
                         f"history to run through.")
        return out

    tenor = TENORS.get(policy.get("tenor") or "MONTHLY", TENORS["MONTHLY"])
    strike_rule = policy.get("strike_rule") or "DELTA"
    roll_rule = policy.get("roll_rule") or "HOLD"
    assign_mode = policy.get("assignment") or "RE_ENTER"
    r = float(rate_pct if rate_pct is not None
              else cfg_get(cfg, "cc_cash_rate_pct"))
    growth = _daily_rate_factor(r)
    roll_dte = float(cfg_get(cfg, "cc_roll_dte"))
    roll_delta = float(cfg_get(cfg, "cc_roll_delta"))
    min_credit = float(cfg_get(cfg, "cc_min_credit"))
    credit_only = bool(cfg_get(cfg, "cc_roll_only_for_credit"))

    start_price = rows[0]["close"]
    capital = start_price * CONTRACT * contracts
    shares = CONTRACT * contracts
    cash = 0.0
    open_call = None
    ledger, trades = [], []
    premium_collected = 0.0
    close_cost = 0.0
    dividends_received = 0.0
    interest_earned = 0.0
    upside_forfeited = 0.0
    real_fills = model_fills = 0
    skipped = 0
    short_of_cash = False

    # Dividends. An ex-date map is the real thing; where there is none, the
    # filed trailing rate is accrued evenly and SAID to be accrued evenly,
    # because reporting zero income for a dividend payer is a bigger error
    # than spreading a known annual rate across the days it was earned over.
    div_map = {str(k)[:10]: _num(v) or 0.0 for k, v in (dividends or {}).items()}
    div_daily = 0.0
    div_basis = ""
    if div_map:
        div_basis = ("Dividends are credited on their actual ex-dividend "
                     "dates.")
    elif _num(dividend_rate_ttm):
        div_daily = _num(dividend_rate_ttm) / 365.0
        div_basis = (f"No ex-dividend calendar is available, so the "
                     f"${_num(dividend_rate_ttm):,.2f} filed trailing "
                     f"twelve-month rate per share is accrued evenly across "
                     f"the days held. The total is right; the individual "
                     f"dates are not.")
    else:
        div_basis = ("No dividend is on file for this company, so none is "
                     "credited.")

    equity_curve = []

    def mark(day, close_px, call):
        """Account value if everything were closed at today's marks."""
        v = cash + shares * close_px
        if call:
            q = quote(sym_store, day, call["strike"], call["expiry"],
                      close_px, call["iv_now"], r, 0.0)
            v -= q["price"] * CONTRACT * contracts
        return v

    for idx, row in enumerate(rows):
        day, px, iv = row["date"], row["close"], row["iv"]
        if cash:
            gained = cash * (growth - 1.0)
            cash += gained
            interest_earned += gained

        if shares:
            if div_map.get(day):
                amt = div_map[day] * shares
                cash += amt
                dividends_received += amt
                ledger.append(_event("DIVIDEND", day, amount=amt,
                                     per_share=div_map[day]))
            elif div_daily:
                amt = div_daily * shares
                cash += amt
                dividends_received += amt

        fv_today = (fair_value_by_day or {}).get(day) or {}

        # ── settle or manage an open call ──────────────────────────────
        if open_call is not None:
            open_call["iv_now"] = iv or open_call["iv_open"]
            dte_left = max(0.0, (_d(open_call["expiry"]) - _d(day)).days)
            expiring = dte_left <= 0
            if expiring:
                # Assignment is decided at the close, the same convention
                # the options backtester uses.
                if px > open_call["strike"]:
                    proceeds = open_call["strike"] * shares
                    forfeited = (px - open_call["strike"]) * shares
                    upside_forfeited += forfeited
                    cash += proceeds - bto.leg_costs(1, contracts)
                    ledger.append(_event(
                        "ASSIGNED", day, strike=open_call["strike"],
                        shares=shares, proceeds=proceeds,
                        share_price=px, upside_forfeited=forfeited))
                    trades.append(_close_trade(open_call, day, 0.0,
                                               "ASSIGNED", px))
                    shares = 0
                else:
                    ledger.append(_event("EXPIRED", day,
                                         strike=open_call["strike"],
                                         share_price=px))
                    trades.append(_close_trade(open_call, day, 0.0,
                                               "EXPIRED", px))
                open_call = None
            else:
                want_roll, why = _roll_wanted(
                    roll_rule, dte_left, roll_dte, roll_delta, px,
                    open_call, iv, r, fv_today)
                if want_roll:
                    q = quote(sym_store, day, open_call["strike"],
                              open_call["expiry"], px,
                              iv or open_call["iv_open"], r)
                    pay = q["price"] + bto.option_spread(
                        q["price"], px, open_call["strike"], dte_left)
                    cost = pay * CONTRACT * contracts + bto.leg_costs(1, contracts)
                    # A roll does NOT net. The loss on the option being
                    # bought back is realized here and stays realized.
                    if credit_only:
                        nxt = _price_next(rows, idx, sym_store, strike_rule,
                                          tenor, cfg, r, fv_today,
                                          expiries_by_day, contracts)
                        if nxt is None or nxt["credit"] <= pay:
                            open_call["iv_now"] = iv or open_call["iv_open"]
                            equity_curve.append(
                                {"date": day, "value": mark(day, px, open_call)})
                            continue
                    cash -= cost
                    close_cost += cost
                    (real_fills, model_fills) = _tally(q, real_fills, model_fills)
                    ledger.append(_event(
                        "CLOSED", day, strike=open_call["strike"],
                        paid=pay, cost=cost, reason=why, source=q["source"]))
                    trades.append(_close_trade(open_call, day, pay, "CLOSED", px))
                    open_call = None

        # ── re-enter after assignment ──────────────────────────────────
        if not shares:
            buy = False
            if assign_mode == "RE_ENTER_ALWAYS":
                buy = True
            elif assign_mode == "RE_ENTER":
                zone = _num(fv_today.get("buy_zone"))
                if zone and px <= zone:
                    buy = True
            if buy:
                n = CONTRACT * contracts
                if cash >= px * n:
                    cash -= px * n
                    shares = n
                    ledger.append(_event(
                        "BOUGHT", day, price=px, shares=n,
                        reason=("the price is at or below the buy zone"
                                if assign_mode == "RE_ENTER"
                                else "unconditional re-entry")))
                elif not short_of_cash:
                    # Recorded once rather than every day: after a rally the
                    # strike that sold the shares can be well below what
                    # buying them back now costs, and a run that quietly sits
                    # in cash from then on looks like a decision rather than
                    # the arithmetic it actually is.
                    short_of_cash = True
                    ledger.append(_event(
                        "COULD NOT BUY BACK", day, price=px,
                        cash=cash, needed=px * n,
                        reason=(f"Buying a hundred shares back at "
                                f"${px:,.2f} needs ${px * n:,.0f} and the "
                                f"account holds ${cash:,.0f}. The shares "
                                f"were sold at a strike below today's price, "
                                f"so the position cannot be rebuilt without "
                                f"adding money.")))

        # ── sell the next call ─────────────────────────────────────────
        if shares and open_call is None and idx < len(rows) - 1:
            exp = pick_expiry(day, tenor["target_dte"],
                              (expiries_by_day or {}).get(day), cfg)
            if exp:
                dte = max(1.0, (_d(exp) - _d(day)).days)
                iv_use = iv or _fallback_iv(rows, idx)
                pick = pick_strike(strike_rule, px, dte, iv_use, r,
                                   fv_today.get("base"),
                                   fv_today.get("credited"), cfg)
                q = quote(sym_store, day, pick["strike"], exp, px, iv_use, r)
                got = q["price"] - bto.option_spread(q["price"], px,
                                                     pick["strike"], dte)
                if got >= min_credit:
                    credit = got * CONTRACT * contracts - bto.leg_costs(
                        1, contracts)
                    cash += credit
                    premium_collected += credit
                    (real_fills, model_fills) = _tally(q, real_fills,
                                                       model_fills)
                    open_call = {"strike": pick["strike"], "expiry": exp,
                                 "opened": day, "credit_per_share": got,
                                 "credit": credit, "iv_open": iv_use,
                                 "iv_now": iv_use, "source": q["source"],
                                 "spot_at_open": px,
                                 "reason": pick["reason"],
                                 "raised_by_fair_value":
                                     pick["raised_by_fair_value"]}
                    ledger.append(_event(
                        "SOLD", day, strike=pick["strike"], expiry=exp,
                        credit=credit, per_share=got, source=q["source"],
                        reason=pick["reason"],
                        raised_by_fair_value=pick["raised_by_fair_value"]))
                else:
                    skipped += 1

        equity_curve.append({"date": day, "value": mark(day, px, open_call)})

    end_px = rows[-1]["close"]
    final = cash + shares * end_px
    if open_call is not None:
        q = quote(sym_store, rows[-1]["date"], open_call["strike"],
                  open_call["expiry"], end_px,
                  open_call["iv_now"] or open_call["iv_open"], r)
        final -= q["price"] * CONTRACT * contracts
        ledger.append(_event("OPEN AT END", rows[-1]["date"],
                             strike=open_call["strike"],
                             expiry=open_call["expiry"],
                             marked_at=q["price"], source=q["source"]))

    years = max((_d(rows[-1]["date"]) - _d(rows[0]["date"])).days, 1) / 365.25
    hold = _buy_and_hold(rows, capital, div_map, div_daily, contracts)

    out.update({
        "available": True,
        "from": rows[0]["date"], "to": rows[-1]["date"],
        "days": len(rows), "years": years,
        "starting_capital": capital,
        "shares_at_end": shares,
        "cash_at_end": cash,
        "terminal_wealth": final,
        "total_return_pct": (final / capital - 1.0) * 100.0 if capital else None,
        "cagr_pct": _cagr(capital, final, years),
        "max_drawdown_pct": _max_drawdown(equity_curve),
        "premium_income": premium_collected,
        "option_close_cost": close_cost,
        "premium_net": premium_collected - close_cost,
        "dividend_income": dividends_received,
        "dividend_basis": div_basis,
        "cash_interest": interest_earned,
        "cash_rate_pct": r,
        "upside_forfeited": upside_forfeited,
        "share_pnl": _share_pnl(ledger, capital, shares, end_px),
        "buy_and_hold": hold,
        "versus_buy_and_hold": (final - hold["terminal_wealth"]),
        "versus_buy_and_hold_pct": (
            (final / hold["terminal_wealth"] - 1.0) * 100.0
            if hold["terminal_wealth"] else None),
        "ledger": ledger,
        "trades": trades,
        "equity_curve": equity_curve[::max(1, len(equity_curve) // 400)],
        "skipped_because_premium_too_small": skipped,
        "could_not_buy_back": short_of_cash,
        **_trade_stats(trades, ledger, capital, rows),
        **_fill_basis(real_fills, model_fills),
    })
    return out


def _tally(q, real, model):
    return (real + 1, model) if q["source"] == REAL else (real, model + 1)


def _fallback_iv(rows, idx) -> float:
    """Realized volatility over the last sixty days, when the volatility
    series has no value for this day. Never a hardcoded guess."""
    window = [r["close"] for r in rows[max(0, idx - 60): idx + 1]]
    if len(window) < 20:
        return 0.25
    rets = [math.log(window[i] / window[i - 1]) for i in range(1, len(window))
            if window[i - 1] > 0]
    if len(rets) < 10:
        return 0.25
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return max(0.05, math.sqrt(var * 252.0))


def _price_next(rows, idx, sym_store, strike_rule, tenor, cfg, r, fv_today,
                expiries_by_day, contracts):
    """What the replacement call would fetch, for a credit-only roll rule."""
    row = rows[idx]
    exp = pick_expiry(row["date"], tenor["target_dte"],
                      (expiries_by_day or {}).get(row["date"]), cfg)
    if not exp:
        return None
    dte = max(1.0, (_d(exp) - _d(row["date"])).days)
    iv = row["iv"] or _fallback_iv(rows, idx)
    pick = pick_strike(strike_rule, row["close"], dte, iv, r,
                       fv_today.get("base"), fv_today.get("credited"), cfg)
    q = quote(sym_store, row["date"], pick["strike"], exp, row["close"], iv, r)
    got = q["price"] - bto.option_spread(q["price"], row["close"],
                                         pick["strike"], dte)
    return {"credit": got, "strike": pick["strike"], "expiry": exp}


def _roll_wanted(rule, dte_left, roll_dte, roll_delta, px, call, iv, r,
                 fv_today) -> tuple[bool, str]:
    if rule == "HOLD":
        return False, ""
    if rule == "DTE":
        if dte_left <= roll_dte:
            return True, (f"inside {roll_dte:.0f} days of expiration")
        return False, ""
    if rule == "DELTA":
        d = _delta(px, call["strike"], dte_left, iv or call["iv_open"], r)
        if d >= roll_delta:
            return True, (f"the call's delta reached {d:.2f}, at or above "
                          f"the {roll_delta:.2f} threshold")
        return False, ""
    if rule == "BELOW_FAIR_VALUE":
        base = _num(fv_today.get("base"))
        if base and call["strike"] < base:
            return True, (f"the strike of ${call['strike']:,.2f} is below "
                          f"the ${base:,.2f} fair value")
        return False, ""
    return False, ""


def _close_trade(call, day, paid_per_share, outcome, spot) -> dict:
    """One completed short call, with its own realized profit or loss.

    The profit is the credit received less what it cost to get out of it.
    Assignment closes the option at zero cost — the cost of assignment falls
    on the shares, which is recorded separately as forfeited upside, so that
    a capped rally is never hidden inside an option that "won".
    """
    days = max(0, (_d(day) - _d(call["opened"])).days)
    realized = (call["credit_per_share"] - paid_per_share) * CONTRACT
    return {"opened": call["opened"], "closed": day, "days": days,
            "strike": call["strike"], "expiry": call["expiry"],
            "credit_per_share": call["credit_per_share"],
            "paid_per_share": paid_per_share,
            "realized": realized, "won": realized > 0,
            "outcome": outcome, "source": call["source"],
            "spot_at_open": call["spot_at_open"], "spot_at_close": spot,
            "raised_by_fair_value": call.get("raised_by_fair_value", False)}


def _trade_stats(trades, ledger, capital, rows) -> dict:
    n = len(trades)
    assigned = sum(1 for t in trades if t["outcome"] == "ASSIGNED")
    rolled = sum(1 for t in trades if t["outcome"] == "CLOSED")
    won = sum(1 for t in trades if t["won"])
    days = [t["days"] for t in trades if t["days"] > 0]
    start_price = rows[0]["close"] if rows else None
    prem = [t["credit_per_share"] / t["spot_at_open"] * 100.0
            for t in trades if t.get("spot_at_open")]
    return {
        "calls_sold": n,
        "calls_assigned": assigned,
        "calls_rolled_or_closed": rolled,
        "calls_expired": n - assigned - rolled,
        "call_win_rate_pct": (won / n * 100.0) if n else None,
        "call_win_rate_note": (
            "The share of individual calls that made money. This is NOT a "
            "measure of whether the strategy worked: a call assigned in a "
            "rally counts as a loss here and may have been the most "
            "profitable month of the run, and a high win rate on calls that "
            "repeatedly cap a rising stock still loses to owning it. The "
            "comparison that matters is terminal wealth against buy and "
            "hold."),
        "assignment_rate_pct": (assigned / n * 100.0) if n else None,
        "roll_rate_pct": (rolled / n * 100.0) if n else None,
        "average_days_in_trade": (sum(days) / len(days)) if days else None,
        "average_premium_pct_of_notional": (sum(prem) / len(prem)) if prem else None,
        "average_premium_note": (
            "Each call's credit as a percentage of the FULL value of the "
            "hundred shares it was sold against, not of any margin figure."),
        "starting_share_price": start_price,
    }


def _fill_basis(real: int, model: int) -> dict:
    total = real + model
    pct = (real / total * 100.0) if total else 0.0
    if total == 0:
        basis, note = BASIS_MODEL, "No option fills were taken."
    elif real == total:
        basis = BASIS_REAL
        note = ("Every option fill came from a real end-of-day chain "
                "snapshot recorded by this app on that trading day.")
    elif real == 0:
        basis = BASIS_MODEL
        note = ("No chain snapshot existed for any of these days, so every "
                "option price is a Black-Scholes value against a modelled "
                "volatility path. Historical option quotes are never "
                "invented here: this is a model output and is labelled as "
                "one. It becomes a real backtest as the app's own chain "
                "store fills in going forward.")
    else:
        basis = BASIS_MIXED
        note = (f"{pct:.0f}% of option fills came from real chain snapshots "
                f"and the rest are model values, each labelled in the "
                f"ledger.")
    return {"fill_basis": basis, "real_fill_pct": pct, "fills": total,
            "real_fills": real, "model_fills": model, "fill_note": note}


def _buy_and_hold(rows, capital, div_map, div_daily, contracts) -> dict:
    """The same money, in the same shares, doing nothing.

    The only fair comparison: identical starting capital, identical days,
    identical dividend treatment. Dividends are held as cash rather than
    reinvested, because the covered-call run holds them as cash too and one
    of the two reinvesting would be a difference in the accounting rather
    than in the strategy.
    """
    shares = CONTRACT * contracts
    cash = 0.0
    for row in rows:
        if div_map.get(row["date"]):
            cash += div_map[row["date"]] * shares
        elif div_daily:
            cash += div_daily * shares
    final = cash + shares * rows[-1]["close"]
    years = max((_d(rows[-1]["date"]) - _d(rows[0]["date"])).days, 1) / 365.25
    return {"terminal_wealth": final, "dividend_income": cash,
            "shares": shares, "starting_capital": capital,
            "total_return_pct": (final / capital - 1.0) * 100.0 if capital else None,
            "cagr_pct": _cagr(capital, final, years),
            "max_drawdown_pct": _max_drawdown(
                [{"date": r["date"], "value": r["close"] * shares}
                 for r in rows]),
            "note": ("The identical capital in the identical shares over the "
                     "identical days, with dividends held as cash exactly as "
                     "the covered-call run holds them.")}


def _share_pnl(ledger, capital, shares_held, end_price) -> float:
    """Profit or loss on the SHARES alone, with no option cash in it.

    What came out of the shares (assignment proceeds, plus whatever is still
    held marked at the last close) less what went into them (the opening
    purchase, plus every repurchase after an assignment). Kept separate from
    premium so the two are never conflated: a covered-call run that made
    money on premium while losing more on the shares is a losing run, and
    that has to be visible.
    """
    out = sum(e.get("proceeds", 0.0) for e in ledger
              if e["kind"] == "ASSIGNED") + shares_held * end_price
    into = capital + sum(e.get("price", 0.0) * e.get("shares", 0)
                         for e in ledger if e["kind"] == "BOUGHT")
    return out - into


def _cagr(start, end, years):
    if not start or start <= 0 or end is None or end <= 0 or years <= 0:
        return None
    return ((end / start) ** (1.0 / years) - 1.0) * 100.0


def _max_drawdown(curve) -> float | None:
    vals = [_num(p.get("value")) for p in curve or []]
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None
    peak, worst = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1.0)
    return worst * 100.0


# ── comparing policies ──────────────────────────────────────────────────────

def policy_label(policy: dict) -> str:
    t = TENORS.get(policy.get("tenor") or "", {}).get("label", "?")
    s = STRIKE_RULES.get(policy.get("strike_rule") or "", {}).get("label", "?")
    r = ROLL_RULES.get(policy.get("roll_rule") or "", {}).get("label", "?")
    a = ASSIGNMENT_MODES.get(policy.get("assignment") or "", {}).get("label", "?")
    return f"{t} · {s} · {r} · {a}"


def compare_policies(bars, iv_series, policies, cfg=None, **kw) -> dict:
    """Run several policies over the same history and report what happened.

    Ranked by terminal wealth, because that is the thing being decided. No
    policy is described as best in general: this is one company over one
    stretch of one market, and the sample line says so.
    """
    runs = []
    for p in policies or []:
        res = simulate(bars, iv_series, p, cfg=cfg, **kw)
        if res.get("available"):
            res["label"] = policy_label(p)
            runs.append(res)
    if not runs:
        return {"available": False,
                "reason": "None of these policies could be run over the "
                          "price history available."}
    runs.sort(key=lambda x: -(x.get("terminal_wealth") or 0.0))
    hold = runs[0]["buy_and_hold"]
    beat = [r for r in runs if (r.get("terminal_wealth") or 0)
            > (hold.get("terminal_wealth") or 0)]
    return {
        "available": True,
        "rows": [{k: r[k] for k in
                  ("label", "policy", "terminal_wealth", "total_return_pct",
                   "cagr_pct", "max_drawdown_pct", "premium_income",
                   "premium_net", "dividend_income", "cash_interest",
                   "upside_forfeited", "calls_sold", "call_win_rate_pct",
                   "assignment_rate_pct", "roll_rate_pct",
                   "average_days_in_trade",
                   "average_premium_pct_of_notional",
                   "versus_buy_and_hold", "versus_buy_and_hold_pct",
                   "fill_basis", "real_fill_pct", "shares_at_end")}
                 for r in runs],
        "buy_and_hold": hold,
        "best": runs[0]["label"],
        "n_beat_buy_and_hold": len(beat),
        "n_policies": len(runs),
        "from": runs[0]["from"], "to": runs[0]["to"],
        "years": runs[0]["years"],
        "verdict_note": (
            f"Over {runs[0]['years']:.1f} years of this one company's price "
            f"history, {len(beat)} of {len(runs)} policies finished ahead of "
            f"simply owning the shares. That is a description of what "
            f"happened here, not evidence that any of these rules works in "
            f"general — one company over one stretch of one market is a "
            f"single observation, and the fill basis line says how much of "
            f"it came from real option prices."),
        "version": COVERED_CALL_VERSION,
    }


def default_policies() -> list:
    """A spread across the families worth testing, deliberately without a
    recommended one."""
    return [
        {"tenor": "WEEKLY", "strike_rule": "DELTA", "roll_rule": "HOLD",
         "assignment": "RE_ENTER"},
        {"tenor": "BIWEEKLY", "strike_rule": "DELTA", "roll_rule": "HOLD",
         "assignment": "RE_ENTER"},
        {"tenor": "MONTHLY", "strike_rule": "DELTA", "roll_rule": "HOLD",
         "assignment": "RE_ENTER"},
        {"tenor": "MONTHLY", "strike_rule": "PERCENT", "roll_rule": "HOLD",
         "assignment": "RE_ENTER"},
        {"tenor": "MONTHLY", "strike_rule": "FAIR_VALUE", "roll_rule": "HOLD",
         "assignment": "RE_ENTER"},
        {"tenor": "MONTHLY", "strike_rule": "BUY_ZONE", "roll_rule": "HOLD",
         "assignment": "RE_ENTER"},
        {"tenor": "MONTHLY", "strike_rule": "DELTA", "roll_rule": "DTE",
         "assignment": "RE_ENTER"},
        {"tenor": "MONTHLY", "strike_rule": "DELTA", "roll_rule": "DELTA",
         "assignment": "RE_ENTER"},
        {"tenor": "MONTHLY", "strike_rule": "FAIR_VALUE",
         "roll_rule": "BELOW_FAIR_VALUE", "assignment": "RE_ENTER"},
        {"tenor": "MONTHLY", "strike_rule": "DELTA", "roll_rule": "HOLD",
         "assignment": "END"},
    ]
