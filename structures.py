"""structures.py — the Phase 3 structure comparator for the Investment tab.

Pure functions only: no network, no disk, no clock, no chain client. Given a
set of scenario prices at ONE expiration and the quotes for the contracts
involved, this ranks the ways of taking the same view.

THE ONE RULE THAT MAKES THE COMPARISON MEAN ANYTHING:

    Identical capital. Identical horizon. Identical scenario prices.

Every structure below starts with the same comparison account, sized at

    C = 100 × the current share price

which is what one round lot costs and therefore what one option contract
controls. Whatever a structure does not spend stays in the account and earns
the matching Treasury yield to expiration. A structure is then judged on what
the WHOLE account is worth at expiration, never on what its own premium did.

That last point is the entire reason this module exists. Ranking an option on
return-on-premium, return-on-margin or return-on-buying-power-reduction makes
leverage look like skill: a contract that risks a tenth of the capital and
makes a tenth of the money posts a spectacular percentage and an unremarkable
dollar result, and the comparison silently rewards it for the money it did
NOT put to work. Marking every structure against the same account removes the
choice of denominator, which is where that flattery lives.

Two further refusals:

* Options are marked at their OWN expiration, and shares are marked on the
  same date. Comparing a three-year share thesis against a 45-day put is a
  comparison of two different questions.
* Scenario weights are ASSUMPTIONS. They are shown, they are adjustable, and
  the ranking is re-run under nearby weightings. When the winner changes, the
  answer is TOSS UP rather than a preference with two decimal places.
"""

from __future__ import annotations

import math

COMPARATOR_VERSION = "invest-structures-1.0.0"

SHARES = "SHARES"
PUT = "PORTFOLIO SECURED PUT"
LEAPS = "LEAPS"
BUY_WRITE = "BUY-WRITE"
SPREAD = "BULL CALL SPREAD"

STRUCTURE_KINDS = (SHARES, PUT, LEAPS, BUY_WRITE, SPREAD)

SCENARIOS = ("bear", "base", "bull")

CONTRACT_MULTIPLIER = 100

DEFAULTS = {
    "toss_up_margin_pct": 0.5,        # annualized points between #1 and #2
    "probability_shift": 0.05,        # how far the weights are stress-tested
    "min_dividend_extrinsic_cushion": 1.0,   # ratio of extrinsic to dividend
}


def cfg_get(cfg, key):
    v = (cfg or {}).get(key)
    return DEFAULTS[key] if v is None else v


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def dividend_at(dividend_fv_per_share, scenario: str) -> float:
    """Dividends carried to the horizon, per share, for one scenario.

    Accepts a single number or a per-scenario dict, because a bear path pays
    smaller dividends than a bull one and rounding that away would quietly
    hand the pessimistic case money it never received.
    """
    src = dividend_fv_per_share
    if isinstance(src, dict):
        return _num(src.get(scenario)) or 0.0
    return _num(src) or 0.0


def comparison_capital(price, contracts: int = 1) -> float | None:
    """C = 100 × price per contract compared."""
    p = _num(price)
    if p is None or p <= 0:
        return None
    return p * CONTRACT_MULTIPLIER * max(1, int(contracts))


def growth_factor(rate_pct, years) -> float:
    """(1 + r)^T for cash left in the comparison account."""
    r, y = _num(rate_pct), _num(years)
    if r is None or y is None or y <= 0:
        return 1.0
    return (1.0 + r / 100.0) ** y


def _out(kind, eligible, reason="", **kw) -> dict:
    base = {"kind": kind, "eligible": bool(eligible), "reason": reason,
            "capital_allocated": None, "unused_capital": None,
            "notional": None, "notional_note": "", "max_loss": None,
            "breakeven": None, "terminal": {}, "weighted_wealth": None,
            "weighted_return_pct": None, "weighted_annualized_pct": None,
            "worst_pnl": None, "worst_scenario": None,
            "liquidity": None, "events_crossed": None, "greeks": None,
            "contract": None, "notes": []}
    base.update(kw)
    return base


def unavailable(kind: str, reason: str, **kw) -> dict:
    """A named structure that could not be built, with the reason kept.

    A comparison table that silently omits the put reads as "the put lost".
    It has to read as "the put could not be priced, and here is why".
    """
    return _out(kind, False, reason, **kw)


def _finish(row: dict, capital: float, probs: dict, years) -> dict:
    """Common tail: probability weighting, worst scenario, annualization."""
    terminal = row.get("terminal") or {}
    if not terminal or capital is None or capital <= 0:
        return row
    weighted = 0.0
    worst_pnl, worst_s = None, None
    for s in SCENARIOS:
        cell = terminal.get(s) or {}
        w = _num(cell.get("wealth"))
        if w is None:
            return row
        cell["pnl"] = w - capital
        cell["return_pct"] = (w / capital - 1.0) * 100.0
        weighted += float((probs or {}).get(s) or 0.0) * w
        if worst_pnl is None or cell["pnl"] < worst_pnl:
            worst_pnl, worst_s = cell["pnl"], s
    row["weighted_wealth"] = weighted
    row["weighted_return_pct"] = (weighted / capital - 1.0) * 100.0
    y = _num(years)
    if y and y > 0 and weighted > 0:
        row["weighted_annualized_pct"] = (
            (weighted / capital) ** (1.0 / y) - 1.0) * 100.0
    row["worst_pnl"] = worst_pnl
    row["worst_scenario"] = worst_s
    return row


# ══════════════════════════════════════════════════════════════════════════
# THE FIVE STRUCTURES
# ══════════════════════════════════════════════════════════════════════════

def shares_position(price, capital, scenario_prices, probs, years,
                    dividend_fv_per_share=0.0, rate_pct=None) -> dict:
    """100 shares, held to the same date every option below is marked at."""
    p0 = _num(price)
    c = _num(capital)
    if p0 is None or p0 <= 0 or c is None or c <= 0:
        return _out(SHARES, False, "No usable share price.")
    n = CONTRACT_MULTIPLIER
    spent = n * p0
    if spent > c + 1e-6:
        return _out(SHARES, False,
                    "One round lot costs more than the comparison capital.")
    unused = c - spent
    g = growth_factor(rate_pct, years)
    div = dividend_at(dividend_fv_per_share, "base")
    terminal = {}
    for s in SCENARIOS:
        st = _num((scenario_prices or {}).get(s))
        if st is None:
            return _out(SHARES, False,
                        f"No {s} scenario price at this horizon.")
        d = dividend_at(dividend_fv_per_share, s)
        terminal[s] = {"wealth": n * st + n * d + unused * g,
                       "stock_price": st, "dividends_per_share": d}
    row = _out(SHARES, True,
               capital_allocated=spent, unused_capital=unused,
               notional=n * p0,
               notional_note=("100 shares at today's price. Owning the shares "
                              "is the only structure here where the notional "
                              "and the capital are the same number."),
               max_loss=c - (n * div + unused * g),
               breakeven=p0 - div, terminal=terminal,
               contract={"shares": n, "entry": p0,
                         "dividends_per_share": div},
               notes=["Dividends are received as cash and carried to the "
                      "horizon at the matching Treasury yield, not added to "
                      "the price return as a percentage."])
    return _finish(row, c, probs, years)


def secured_put(price, capital, strike, credit, scenario_prices, probs, years,
                rate_pct=None, contract=None) -> dict:
    """Sell one put, hold the FULL strike notional against it.

    Risk is sized at strike × 100, never at the broker's buying-power
    reduction. A broker will happily let this be opened against a fraction of
    that, and every ratio computed on the fraction is a ratio computed on a
    number the market has no opinion about: if the stock goes to zero the
    obligation is the full strike, whatever the margin clerk asked for.
    """
    p0, c, k, cr = _num(price), _num(capital), _num(strike), _num(credit)
    if p0 is None or c is None or k is None or cr is None or k <= 0:
        return _out(PUT, False, "Missing strike, credit or price.")
    if cr <= 0:
        return _out(PUT, False, "There is no bid on this contract, and the "
                                "bid is the only price a resting seller is "
                                "actually promised.")
    required = k * CONTRACT_MULTIPLIER
    if required > c + 1e-6:
        return _out(PUT, False,
                    (f"Securing this put needs ${required:,.0f} of full strike "
                     f"notional, more than the ${c:,.0f} comparison capital. "
                     f"It is not comparable at this size."),
                    notional=required)
    g = growth_factor(rate_pct, years)
    premium = cr * CONTRACT_MULTIPLIER
    terminal = {}
    for s in SCENARIOS:
        st = _num((scenario_prices or {}).get(s))
        if st is None:
            return _out(PUT, False, f"No {s} scenario price at this horizon.")
        obligation = max(0.0, k - st) * CONTRACT_MULTIPLIER
        terminal[s] = {"wealth": (c + premium) * g - obligation,
                       "stock_price": st, "assigned": st < k}
    row = _out(PUT, True,
               capital_allocated=required, unused_capital=c - required,
               notional=required,
               notional_note=("Strike × 100. This is what can actually be "
                              "put to you, and it is what the risk is sized "
                              "on — never the buying-power reduction."),
               max_loss=c - ((c + premium) * g - required),
               breakeven=k - cr, terminal=terminal,
               contract={"strike": k, "credit": cr,
                         "effective_assignment_cost": k - cr,
                         "premium_dollars": premium,
                         "distance_below_price_pct":
                             (k / p0 - 1.0) * 100.0 if p0 else None,
                         **(contract or {})},
               notes=[(f"If assigned, the shares are acquired at an effective "
                       f"${k - cr:,.2f} — the strike less the premium already "
                       f"received."),
                      "The premium is received up front, so it earns the "
                      "matching Treasury yield alongside the secured cash."])
    return _finish(row, c, probs, years)


def long_call(price, capital, strike, debit, scenario_prices, probs, years,
              rate_pct=None, contract=None, kind: str = LEAPS) -> dict:
    """Buy one call. The unspent capital is not free money — it earns the
    Treasury yield, and the comparison counts it."""
    p0, c, k, ask = _num(price), _num(capital), _num(strike), _num(debit)
    if p0 is None or c is None or k is None or ask is None or k <= 0:
        return _out(kind, False, "Missing strike, price or offer.")
    if ask <= 0:
        return _out(kind, False, "There is no offer on this contract.")
    cost = ask * CONTRACT_MULTIPLIER
    if cost > c + 1e-6:
        return _out(kind, False,
                    (f"The premium of ${cost:,.0f} is more than the "
                     f"${c:,.0f} comparison capital."))
    unused = c - cost
    g = growth_factor(rate_pct, years)
    terminal = {}
    for s in SCENARIOS:
        st = _num((scenario_prices or {}).get(s))
        if st is None:
            return _out(kind, False, f"No {s} scenario price at this horizon.")
        terminal[s] = {"wealth": unused * g
                       + max(0.0, st - k) * CONTRACT_MULTIPLIER,
                       "stock_price": st, "in_the_money": st > k}
    intrinsic = max(0.0, p0 - k)
    row = _out(kind, True,
               capital_allocated=cost, unused_capital=unused,
               notional=p0 * CONTRACT_MULTIPLIER,
               notional_note=("The contract controls 100 shares, worth "
                              "this much at today's price. The money at risk "
                              "is only the premium — the two are different "
                              "numbers and both are shown."),
               max_loss=cost - unused * (g - 1.0),
               breakeven=k + ask, terminal=terminal,
               contract={"strike": k, "debit": ask,
                         "premium_dollars": cost,
                         "intrinsic": intrinsic,
                         "extrinsic": max(0.0, ask - intrinsic),
                         "extrinsic_per_year":
                             (max(0.0, ask - intrinsic) / _num(years)
                              if _num(years) else None),
                         **(contract or {})},
               notes=["A call does not receive the dividend. Where the "
                      "company pays one, the shares get cash this does not."])
    return _finish(row, c, probs, years)


def buy_write(price, capital, call_strike, call_credit, scenario_prices, probs,
              years, dividend_fv_per_share=0.0, rate_pct=None,
              contract=None, cfg=None) -> dict:
    """Buy 100 shares and sell ONE call against them.

    Deliberately a single expiration. It does NOT model selling a call every
    week and rolling it: that path depends on where the stock went between
    each roll, and pretending one contract's economics describe a year of
    rolling would be the most flattering possible assumption dressed as a
    calculation.
    """
    p0, c = _num(price), _num(capital)
    k, cr = _num(call_strike), _num(call_credit)
    if p0 is None or c is None or k is None or cr is None or k <= 0:
        return _out(BUY_WRITE, False, "Missing call strike, credit or price.")
    if cr <= 0:
        return _out(BUY_WRITE, False, "There is no bid on the call.")
    n = CONTRACT_MULTIPLIER
    spent = n * p0
    if spent > c + 1e-6:
        return _out(BUY_WRITE, False,
                    "One round lot costs more than the comparison capital.")
    div = dividend_at(dividend_fv_per_share, "base")
    premium = cr * n
    unused = c - spent + premium
    g = growth_factor(rate_pct, years)
    terminal = {}
    for s in SCENARIOS:
        st = _num((scenario_prices or {}).get(s))
        if st is None:
            return _out(BUY_WRITE, False,
                        f"No {s} scenario price at this horizon.")
        called = max(0.0, st - k) * n
        d = dividend_at(dividend_fv_per_share, s)
        terminal[s] = {"wealth": n * st - called + unused * g + n * d,
                       "stock_price": st, "called_away": st > k,
                       "dividends_per_share": d}
    extrinsic = max(0.0, cr - max(0.0, p0 - k))
    cushion = float(cfg_get(cfg, "min_dividend_extrinsic_cushion"))
    early = bool(div > 0 and extrinsic < div * cushion and p0 > k)
    row = _out(BUY_WRITE, True,
               capital_allocated=spent - premium, unused_capital=unused,
               notional=n * p0,
               notional_note=("100 shares at today's price. The short call "
                              "caps the upside; it does not reduce what is "
                              "at risk below."),
               max_loss=c - (unused * g + n * div),
               breakeven=p0 - cr - div, terminal=terminal,
               contract={"call_strike": k, "call_credit": cr,
                         "premium_dollars": premium,
                         "extrinsic": extrinsic,
                         "max_gain": (k - p0 + cr + div) * n,
                         "early_assignment_risk": early,
                         **(contract or {})},
               notes=(["Upside is capped at the call strike. Above it the "
                       "shares are called away and the rest of the move "
                       "belongs to the buyer."]
                      + ([f"Early-assignment risk: the call's extrinsic value "
                          f"of ${extrinsic:,.2f} is smaller than the "
                          f"${div:,.2f} of dividends due before expiration, "
                          f"and it is in the money. A holder who wants the "
                          f"dividend can exercise early."] if early else [])))
    return _finish(row, c, probs, years)


def bull_call_spread(price, capital, long_strike, long_debit, short_strike,
                     short_credit, scenario_prices, probs, years,
                     rate_pct=None, contract=None) -> dict:
    """Buy the lower call, sell the higher one, at the prices actually quoted."""
    p0, c = _num(price), _num(capital)
    k1, a1 = _num(long_strike), _num(long_debit)
    k2, b2 = _num(short_strike), _num(short_credit)
    if None in (p0, c, k1, a1, k2, b2) or k1 <= 0 or k2 <= 0:
        return _out(SPREAD, False, "Missing a leg of the spread.")
    if k2 <= k1:
        return _out(SPREAD, False,
                    "The short strike has to sit above the long one.")
    net = a1 - b2
    if net <= 0:
        return _out(SPREAD, False,
                    "The quoted legs produce a credit rather than a debit, "
                    "which is a different structure with a different risk.")
    debit = net * CONTRACT_MULTIPLIER
    if debit > c + 1e-6:
        return _out(SPREAD, False,
                    f"The debit of ${debit:,.0f} exceeds the comparison "
                    f"capital.")
    unused = c - debit
    g = growth_factor(rate_pct, years)
    terminal = {}
    for s in SCENARIOS:
        st = _num((scenario_prices or {}).get(s))
        if st is None:
            return _out(SPREAD, False,
                        f"No {s} scenario price at this horizon.")
        payoff = (max(0.0, st - k1) - max(0.0, st - k2)) * CONTRACT_MULTIPLIER
        terminal[s] = {"wealth": unused * g + payoff, "stock_price": st}
    row = _out(SPREAD, True,
               capital_allocated=debit, unused_capital=unused,
               notional=(k2 - k1) * CONTRACT_MULTIPLIER,
               notional_note=("The width of the spread × 100 — the most the "
                              "structure can ever be worth. The most it can "
                              "lose is the debit."),
               max_loss=debit - unused * (g - 1.0),
               breakeven=k1 + net, terminal=terminal,
               contract={"long_strike": k1, "long_debit": a1,
                         "short_strike": k2, "short_credit": b2,
                         "net_debit": net, "debit_dollars": debit,
                         "width": k2 - k1,
                         "max_gain": (k2 - k1) * CONTRACT_MULTIPLIER - debit,
                         **(contract or {})},
               notes=["Gains stop at the short strike. This is the cheapest "
                      "way to own a bounded move and the wrong way to own a "
                      "business."])
    return _finish(row, c, probs, years)


# ══════════════════════════════════════════════════════════════════════════
# RANKING AND THE TOSS-UP TEST
# ══════════════════════════════════════════════════════════════════════════

def rank(rows, key: str = "weighted_annualized_pct") -> list:
    """Eligible structures, best first, on return over the FULL comparison
    capital. Ineligible ones keep their reason and sort last."""
    ok = [r for r in (rows or []) if r.get("eligible")
          and _num(r.get(key)) is not None]
    bad = [r for r in (rows or []) if r not in ok]
    ok.sort(key=lambda r: _num(r.get(key)), reverse=True)
    return ok + bad


def reweight(rows, probs, years) -> list:
    """Re-score already-built structures under a different set of weights.

    The payoffs do not change — only what they are averaged with — so this is
    a re-weighting of stored terminal wealth, not a second pass over quotes.
    """
    out = []
    for r in rows or []:
        if not r.get("eligible"):
            out.append(r)
            continue
        clone = dict(r)
        clone["terminal"] = {s: dict(v) for s, v in (r.get("terminal") or {}).items()}
        cap = _num(r.get("capital_allocated"))
        # The comparison capital is what every wealth figure was marked
        # against, and it is recoverable from any scenario cell.
        base_cap = None
        for s in SCENARIOS:
            cell = (r.get("terminal") or {}).get(s) or {}
            w, pnl = _num(cell.get("wealth")), _num(cell.get("pnl"))
            if w is not None and pnl is not None:
                base_cap = w - pnl
                break
        out.append(_finish(clone, base_cap if base_cap else cap, probs, years))
    return out


def probability_sensitivity(rows, probs, years, cfg=None) -> dict:
    """Does the winner survive a reasonable change in the scenario weights?

    The weights are guesses. If moving the bear weight by five points changes
    which structure wins, then the ranking was never about the structures.
    """
    cfg = cfg or {}
    shift = float(cfg_get(cfg, "probability_shift"))
    margin = float(cfg_get(cfg, "toss_up_margin_pct"))
    ranked = rank(rows)
    eligible = [r for r in ranked if r.get("eligible")]
    if len(eligible) < 2:
        return {"available": False, "stable": True, "toss_up": False,
                "winner": eligible[0]["kind"] if eligible else None,
                "reason": "Fewer than two comparable structures.",
                "tested": []}
    winner = eligible[0]["kind"]
    runner = eligible[1]["kind"]
    gap = (_num(eligible[0].get("weighted_annualized_pct")) or 0.0) \
        - (_num(eligible[1].get("weighted_annualized_pct")) or 0.0)

    tested = []
    flips = []
    for label, adj in (("bear weight +%d points" % round(shift * 100),
                        {"bear": shift, "base": -shift, "bull": 0.0}),
                       ("bear weight −%d points" % round(shift * 100),
                        {"bear": -shift, "base": shift, "bull": 0.0}),
                       ("bull weight +%d points" % round(shift * 100),
                        {"bull": shift, "base": -shift, "bear": 0.0}),
                       ("bull weight −%d points" % round(shift * 100),
                        {"bull": -shift, "base": shift, "bear": 0.0})):
        alt = {}
        for s in SCENARIOS:
            alt[s] = max(0.0, float(probs.get(s) or 0.0) + adj.get(s, 0.0))
        total = sum(alt.values()) or 1.0
        alt = {s: v / total for s, v in alt.items()}
        alt_rank = rank(reweight(rows, alt, years))
        alt_winner = next((r["kind"] for r in alt_rank if r.get("eligible")), None)
        tested.append({"label": label, "probs": alt, "winner": alt_winner})
        if alt_winner and alt_winner != winner:
            flips.append({"label": label, "winner": alt_winner, "probs": alt})

    near_tie = gap < margin
    toss = bool(flips) or near_tie
    if flips:
        f = flips[0]
        reason = (f"{winner} and {f['winner']} swap places when the "
                  f"{f['label'].replace('weight', 'probability')} — a change "
                  f"well inside how precisely anyone knows these weights.")
    elif near_tie:
        reason = (f"{winner} leads {runner} by only {gap:.2f} points a year "
                  f"on identical capital. That is inside the noise of the "
                  f"quotes themselves.")
    else:
        reason = (f"{winner} stays ahead of {runner} across every weighting "
                  f"tested, leading by {gap:.2f} points a year.")
    return {"available": True, "stable": not toss, "toss_up": toss,
            "winner": winner, "runner_up": runner, "gap_pct": gap,
            "margin_pct": margin, "tested": tested, "flips": flips,
            "reason": reason}


def compare(rows, probs, years, cfg=None) -> dict:
    """Rank the structures and say whether the ranking means anything."""
    ranked = rank(rows)
    sens = probability_sensitivity(rows, probs, years, cfg)
    eligible = [r for r in ranked if r.get("eligible")]
    return {"available": bool(eligible), "rows": ranked,
            "ranked_kinds": [r["kind"] for r in eligible],
            "preferred": eligible[0]["kind"] if eligible else None,
            "sensitivity": sens,
            "toss_up": bool(sens.get("toss_up")),
            "probabilities": probs, "years": years,
            "capital_basis": ("Every structure is marked against the same "
                              "account of 100 × the current share price, at "
                              "the same expiration, on the same three "
                              "scenario prices. Unspent cash earns the "
                              "matching Treasury yield."),
            "reason": "" if eligible else ("No structure could be built from "
                                           "the quotes available."),
            "version": COMPARATOR_VERSION}


def expected_events(next_earnings_iso, expiry_iso, today_iso,
                    quarter_days: float = 91.31) -> dict:
    """How many earnings reports the position is expected to sit through.

    Counted from the one date that is actually known — the next report — and
    then stepped a quarter at a time. Labelled EXPECTED because the second
    and third dates are arithmetic, not a calendar entry.
    """
    from datetime import date as _date

    def _d(x):
        try:
            return _date.fromisoformat(str(x)[:10])
        except (TypeError, ValueError):
            return None

    nxt, exp, today = _d(next_earnings_iso), _d(expiry_iso), _d(today_iso)
    if exp is None or today is None:
        return {"available": False, "count": None,
                "reason": "No expiration date to measure to."}
    if nxt is None:
        return {"available": False, "count": None,
                "reason": ("The next earnings date is not available for this "
                           "ticker, so the number of reports crossed cannot "
                           "be counted.")}
    count, dates = 0, []
    cur = nxt
    while cur <= exp and count < 20:
        if cur >= today:
            count += 1
            dates.append(cur.isoformat())
        cur = _date.fromordinal(int(cur.toordinal() + round(quarter_days)))
    return {"available": True, "count": count, "dates": dates,
            "confirmed": dates[:1], "reason": "",
            "note": ("Only the first date is a published one. The rest step "
                     "forward a quarter at a time and are expected, not "
                     "scheduled.")}
