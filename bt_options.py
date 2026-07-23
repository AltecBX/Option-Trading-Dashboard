"""bt_options.py — options LIFECYCLE engine (Backtest v2, phase B1).

The single leg-based simulator for every options structure the app can
express (the Lab's old engine could only go LONG a single leg — this one
natively models the premium-selling structures the user actually trades,
with the management rules that define that style).

Design contract (see BACKTEST_UPGRADE.md §B1):
  • Pure and dependency-injected: prices come from metrics._bs_price via a
    caller-supplied iv series; bars/dividends/earnings are plain lists.
    Nothing here touches the network, so every path is unit-testable with
    deterministic fixtures.
  • Time: DTE in CALENDAR days; expiry resolved against real bar dates.
    T = metrics.year_fraction(days_left).
  • All P/L is DOLLARS per position (contracts × 100 already applied).
  • Every modeled mechanism is labeled: results carry `model_notes`, and
    each simulated trade records how it was priced and why it closed.
  • Conservatism: when a management stop and a profit target could both
    trigger inside the same daily bar, the STOP is assumed to fire first
    (worst-case bar ordering, same rule as the stock engine).

Structures: short_put (CSP), covered_call, short_strangle, iron_condor,
put_credit_spread, call_credit_spread, iron_fly, long_call, long_put.
The wheel emerges from short_put + assignment + covered_call re-entry.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from metrics import _bs_delta, _bs_price, year_fraction

CONTRACT = 100                      # shares per contract
COMMISSION_PER_CONTRACT = 0.65      # per leg, per contract, per side
REG_FEES_PER_CONTRACT = 0.05        # SEC/ORF/OCC rounding — documented estimate
ASSIGN_EXTRINSIC = 0.03             # short ITM + extrinsic below this → assumed assigned
PIN_PCT = 0.002                     # |spot−strike|/spot at expiry below this → pin-risk flag


# ── Fill model ──────────────────────────────────────────────────────────────
def option_spread(mid: float, spot: float, strike: float, dte_left: float) -> float:
    """Half-spread PAID per fill, in premium dollars/share. Wider for
    near-dated, far-OTM and cheap options — a documented model, not data:
      base       1.2% of mid, floor $0.02
      near-dated +2% of mid scaled by 5/DTE (0DTE markets are wide)
      far OTM    +1% of mid when strike is >10% from spot (thin wings)
    Capped at 50% of mid so junk options can't fill at negative prices."""
    if mid <= 0:
        return 0.02
    add = mid * 0.012
    add += mid * 0.02 * min(1.0, 5.0 / max(dte_left, 1.0))
    if spot > 0 and abs(spot - strike) / spot > 0.10:
        add += mid * 0.01
    return max(0.02, min(add, mid * 0.5))


def leg_costs(n_legs: int, contracts: int) -> float:
    """Commission + regulatory fees for one SIDE (open or close)."""
    return n_legs * contracts * (COMMISSION_PER_CONTRACT + REG_FEES_PER_CONTRACT)


# ── Structure builders ──────────────────────────────────────────────────────
# A leg: {"right": "put"|"call", "strike": float, "qty": ±1}  (per contract;
# qty −1 = short). Stock ownership (covered call) is tracked separately so
# assignment can hand shares back and forth.

def _snap(raw: float) -> float:
    inc = 0.5 if raw < 25 else (1.0 if raw < 100 else (5.0 if raw < 500 else 10.0))
    return round(raw / inc) * inc


def strike_by_delta(spot: float, iv: float, dte: float, target: float,
                    right: str) -> float:
    """Strike whose |delta| ≈ target (decimal, e.g. 0.30), bisection."""
    T = year_fraction(dte)
    lo, hi = spot * 0.4, spot * 2.5
    raw = spot
    for _ in range(48):
        mid = (lo + hi) / 2
        d = _bs_delta(spot, mid, T, iv, right)
        ad = abs(d)
        if right == "call":
            lo, hi = (mid, hi) if ad > target else (lo, mid)
        else:
            lo, hi = (lo, mid) if ad > target else (mid, hi)
        raw = mid
    return _snap(raw)


def build_structure(name: str, spot: float, iv: float, dte: float,
                    params: dict | None = None) -> dict | None:
    """→ {"legs": [...], "stock": 0|100, "defined_risk": bool,
         "width": float|None}  or None when it can't be built."""
    p = params or {}
    delta = float(p.get("target_delta") or 0.30)
    wing_delta = float(p.get("wing_delta") or 0.10)
    if spot <= 0 or iv is None or iv <= 0.01 or dte <= 0:
        return None       # degenerate vol → delta targeting is meaningless

    def K(t, right):
        return strike_by_delta(spot, iv, dte, t, right)

    if name == "short_put":
        return {"legs": [{"right": "put", "strike": K(delta, "put"), "qty": -1}],
                "stock": 0, "defined_risk": False, "width": None}
    if name == "covered_call":
        return {"legs": [{"right": "call", "strike": K(delta, "call"), "qty": -1}],
                "stock": CONTRACT, "defined_risk": True, "width": None}
    if name == "short_strangle":
        return {"legs": [{"right": "put", "strike": K(delta, "put"), "qty": -1},
                         {"right": "call", "strike": K(delta, "call"), "qty": -1}],
                "stock": 0, "defined_risk": False, "width": None}
    if name in ("put_credit_spread", "call_credit_spread"):
        right = "put" if name.startswith("put") else "call"
        short_k = K(delta, right)
        long_k = K(wing_delta, right)
        if short_k == long_k:
            long_k = _snap(long_k * (0.97 if right == "put" else 1.03))
        if (right == "put" and long_k >= short_k) or (right == "call" and long_k <= short_k):
            return None
        return {"legs": [{"right": right, "strike": short_k, "qty": -1},
                         {"right": right, "strike": long_k, "qty": 1}],
                "stock": 0, "defined_risk": True, "width": abs(short_k - long_k)}
    if name == "iron_condor":
        sp, lp = K(delta, "put"), K(wing_delta, "put")
        sc, lc = K(delta, "call"), K(wing_delta, "call")
        if not (lp < sp < sc < lc):
            return None
        return {"legs": [{"right": "put", "strike": sp, "qty": -1},
                         {"right": "put", "strike": lp, "qty": 1},
                         {"right": "call", "strike": sc, "qty": -1},
                         {"right": "call", "strike": lc, "qty": 1}],
                "stock": 0, "defined_risk": True,
                "width": max(sp - lp, lc - sc)}
    if name == "iron_fly":
        k = _snap(spot)
        lp, lc = K(wing_delta, "put"), K(wing_delta, "call")
        if not (lp < k < lc):
            return None
        return {"legs": [{"right": "put", "strike": k, "qty": -1},
                         {"right": "call", "strike": k, "qty": -1},
                         {"right": "put", "strike": lp, "qty": 1},
                         {"right": "call", "strike": lc, "qty": 1}],
                "stock": 0, "defined_risk": True, "width": max(k - lp, lc - k)}
    if name in ("long_call", "long_put"):
        right = "call" if name == "long_call" else "put"
        strike_cfg = p.get("strike") or {"mode": "delta", "value": delta * 100}
        if strike_cfg.get("mode") == "atm":
            kk = _snap(spot)
        elif strike_cfg.get("mode") == "otm_pct":
            v = float(strike_cfg.get("value") or 5)
            kk = _snap(spot * (1 + v / 100) if right == "call" else spot * (1 - v / 100))
        elif strike_cfg.get("mode") == "itm_pct":
            v = float(strike_cfg.get("value") or 5)
            kk = _snap(spot * (1 - v / 100) if right == "call" else spot * (1 + v / 100))
        else:
            kk = K(abs(float(strike_cfg.get("value") or 30)) / 100.0, right)
        return {"legs": [{"right": right, "strike": kk, "qty": 1}],
                "stock": 0, "defined_risk": True, "width": None}
    return None


# ── Pricing helpers ─────────────────────────────────────────────────────────
def amer_price(spot, strike, T, iv, right) -> float:
    """American-style floor: a listed option never trades below intrinsic
    (the European BS value CAN — deep ITM puts price below K−S because of
    the Ke^{-rT} discount). All position marks use this floor; the gap
    between the two is exactly the early-exercise signal used below."""
    euro = _bs_price(spot, strike, T, iv, right)
    intr = max(0.0, (spot - strike) if right == "call" else (strike - spot))
    return max(euro, intr)


def legs_value(legs, spot, iv, days_left) -> float:
    """Net value of the option legs in premium $/share terms: Σ qty × price
    (American-floored). For a pure short structure this is negative;
    |value| is the cost to buy it back."""
    T = year_fraction(max(0.0, days_left))
    total = 0.0
    for L in legs:
        total += L["qty"] * amer_price(spot, L["strike"], T, iv, L["right"])
    return total


def legs_intrinsic(legs, spot) -> float:
    total = 0.0
    for L in legs:
        iv_ = max(0.0, (spot - L["strike"]) if L["right"] == "call" else (L["strike"] - spot))
        total += L["qty"] * iv_
    return total


def bp_requirement(structure: str, legs, spot: float, credit: float,
                   width: float | None) -> float:
    """Buying power per CONTRACT-SET in dollars (broker-formula estimates,
    same conventions as the live juice scanner)."""
    if structure == "short_put":
        k = legs[0]["strike"]
        return k * CONTRACT                                   # cash-secured
    if structure == "covered_call":
        return spot * CONTRACT                                # own the shares
    if structure == "short_strangle":
        def side(k, otm):
            return max(0.20 * spot - otm, 0.10 * k)
        pk = next(L["strike"] for L in legs if L["right"] == "put")
        ck = next(L["strike"] for L in legs if L["right"] == "call")
        p = side(pk, max(spot - pk, 0))
        c = side(ck, max(ck - spot, 0))
        return (max(p, c) + credit) * CONTRACT
    if width:                                                 # defined risk
        return max(0.0, (width - credit)) * CONTRACT
    return max(credit * 2, 0.20 * spot) * CONTRACT            # fallback


# ── Position lifecycle ──────────────────────────────────────────────────────
def simulate_position(bars: list, i_signal: int, structure: str,
                      iv_series: list, mgmt: dict, contracts: int = 1,
                      params: dict | None = None,
                      dividends: list | None = None,
                      quote_fn=None) -> dict | None:
    """One position from signal to close. Fill at bars[i_signal+1].open.

    bars: [{"date": "YYYY-MM-DD", "open","high","low","close"}...]
    iv_series: per-bar annualized decimal IV (caller's model — B1 wires the
      legacy HV20×1.1; B2 replaces it). iv_series[k] is KNOWN AT bar k.
    mgmt: {"profit_take_pct": 50, "stop_x_credit": 2.0, "exit_dte": 21,
           "roll_dte": None, "hold_to_expiry": bool}
    dividends: [("YYYY-MM-DD", amount)] ex-div dates (optional).

    Returns a trade dict or None (couldn't build/fill). trade["events"]
    is the full lifecycle audit trail for the replay UI."""
    if i_signal + 1 >= len(bars):
        return None
    p = params or {}
    dte = float(p.get("dte") or 45)
    fill = bars[i_signal + 1]
    spot0 = fill["open"]
    iv0 = iv_series[i_signal] if i_signal < len(iv_series) else None
    if not iv0 or iv0 <= 0:
        return None
    built = build_structure(structure, spot0, iv0, dte, p)
    if not built:
        return None
    legs = built["legs"]
    entry_date = date.fromisoformat(fill["date"][:10])
    expiry = entry_date + timedelta(days=int(dte))

    # Entry fill: REAL bid/ask when a same-day chain snapshot exists
    # (chain_store via quote_fn), else the modeled mid ± half-spread.
    T0 = year_fraction(dte)
    entry_cash = 0.0            # + = credit received, − = debit paid ($/share)
    n_real = 0
    used_strikes = set()
    for L in legs:
        q = None
        if quote_fn is not None:
            try:
                q = quote_fn(fill["date"][:10], L["right"], L["strike"], dte)
            except Exception:
                q = None
            # Snapping to the real listed strike must not collapse two legs
            # onto the same contract.
            if q and (q["strike"], L["right"]) in used_strikes:
                q = None
        if q:
            L["strike"] = q["strike"]                  # trade the REAL strike
            px = q["bid"] if L["qty"] < 0 else q["ask"]
            L["fill_src"] = "real"
            n_real += 1
            used_strikes.add((q["strike"], L["right"]))
        else:
            mid = _bs_price(spot0, L["strike"], T0, iv0, L["right"])
            sp = option_spread(mid, spot0, L["strike"], dte)
            px = mid - sp if L["qty"] < 0 else mid + sp   # sell at bid, buy at ask
            L["fill_src"] = "model"
        if px <= 0.01 and L["qty"] < 0:
            return None                                # can't sell worthless legs
        L["entry_px"] = round(px, 4)
        entry_cash += -L["qty"] * px
    credit = entry_cash                                # >0 for selling structures
    is_credit = credit > 0
    stock_shares = built["stock"] * contracts          # covered call ownership
    # Wheel continuity: shares already held (assigned at the put strike)
    # carry their real basis into the covered-call links.
    stock_basis = (float(p["stock_basis"]) if stock_shares and p.get("stock_basis")
                   else (spot0 if stock_shares else 0.0))

    open_costs = leg_costs(len(legs), contracts)
    div_map = {d: a for d, a in (dividends or [])}

    pt = mgmt.get("profit_take_pct")
    stop_x = mgmt.get("stop_x_credit")
    exit_dte = mgmt.get("exit_dte")
    roll_dte = mgmt.get("roll_dte")

    events = [{"date": fill["date"][:10], "type": "open",
               "detail": f"{structure} ×{contracts}, "
                         f"{'credit' if is_credit else 'debit'} ${abs(credit):.2f}/sh",
               "legs": [{k: L.get(k) for k in ("right", "strike", "qty", "entry_px", "fill_src")} for L in legs]}]
    marks = []                                         # daily {date, value} audit

    def make_trade(exit_val_per_share, exit_date, reason, extra_cash=0.0,
                   n_close_legs=None):
        """exit_val_per_share: what the option legs are WORTH at close in
        signed Σqty×px terms (shorts negative). P/L math:
          options P/L = (entry_cash + exit_val) × contracts × 100
        (entry_cash already signed: credit +, and closing shorts costs
        |exit_val| when negative)."""
        close_costs = leg_costs(n_close_legs if n_close_legs is not None else len(legs), contracts)
        opt_pl = (entry_cash + exit_val_per_share) * contracts * CONTRACT
        stock_pl = extra_cash
        net = opt_pl + stock_pl - open_costs - close_costs
        bp = bp_requirement(structure, legs, spot0, max(credit, 0.0), built["width"]) * contracts
        return {
            "symbol": None, "structure": structure, "contracts": contracts,
            "direction": "short_premium" if is_credit else "long_premium",
            "entry_date": fill["date"][:10], "exit_date": exit_date,
            "expiry": expiry.isoformat(), "dte": int(dte),
            "credit": round(credit, 4) if is_credit else round(-credit, 4),
            "is_credit": is_credit,
            "legs": [{k: L.get(k) for k in ("right", "strike", "qty", "entry_px", "fill_src")} for L in legs],
            "reason": reason,
            "pnl": round(net, 2),
            "bp": round(bp, 2),
            "pnl_on_bp": round(net / bp * 100.0, 2) if bp > 0 else None,
            "held_days": (date.fromisoformat(exit_date) - entry_date).days,
            "events": events, "marks": marks,
            "iv_entry": round(iv0, 4),
            "priced": ("real_quote" if n_real == len(legs)
                       else "mixed" if n_real else "modeled"),
        }

    # ── walk the days ──
    for k in range(i_signal + 1, len(bars)):
        b = bars[k]
        d = date.fromisoformat(b["date"][:10])
        days_left = (expiry - d).days
        iv_k = iv_series[k] if k < len(iv_series) else iv0

        # ── expiry settlement ──
        if days_left <= 0:
            settle = legs_intrinsic(legs, b["close"])
            pin = any(b["close"] > 0 and abs(b["close"] - L["strike"]) / b["close"] < PIN_PCT
                      for L in legs if L["qty"] < 0)
            stock_cash = 0.0
            reason = "expired"
            if stock_shares:
                sc = next((L for L in legs if L["right"] == "call" and L["qty"] < 0), None)
                if sc and b["close"] > sc["strike"]:
                    stock_cash = (sc["strike"] - stock_basis) * stock_shares
                    reason = "called_away"
                    events.append({"date": b["date"][:10], "type": "assigned",
                                   "detail": f"shares called away at {sc['strike']}"})
                else:
                    stock_cash = (b["close"] - stock_basis) * stock_shares
            if reason == "expired":
                events.append({"date": b["date"][:10], "type": "expired",
                               "detail": f"settled at intrinsic ${settle:.2f}/sh"
                                         + (" · PIN RISK (short strike at the money)" if pin else "")})
            t = make_trade(settle, b["date"][:10], reason,
                           extra_cash=stock_cash, n_close_legs=0)
            if pin:
                t["pin_risk"] = True
            if stock_shares and reason == "expired":
                # covered call expired OTM: shares retained; report the option
                # P/L plus the stock mark move so the wheel caller can chain.
                t["stock_retained"] = True
            return t

        if k == i_signal + 1:
            marks.append({"date": b["date"][:10],
                          "value": round(legs_value(legs, b["close"], iv_k, days_left), 4)})
            continue   # management/assignment start the day AFTER entry

        # ── management (credit structures manage vs credit; debit vs debit) ──
        val_close = legs_value(legs, b["close"], iv_k, days_left)
        marks.append({"date": b["date"][:10], "value": round(val_close, 4)})
        if is_credit:
            cost_close = -val_close      # $ to buy back
            # Worst adverse mark inside the bar (stop first — conservative):
            worst = max(-legs_value(legs, b["high"], iv_k, days_left),
                        -legs_value(legs, b["low"], iv_k, days_left))
            if stop_x is not None and worst >= credit * stop_x:
                px = credit * stop_x
                events.append({"date": b["date"][:10], "type": "stop",
                               "detail": f"buy-back at {stop_x}× credit"})
                spr = sum(option_spread(abs(L["entry_px"]), b["close"], L["strike"], days_left) for L in legs)
                return make_trade(-(px + spr), b["date"][:10], "stop")
            if pt is not None and cost_close <= credit * (1 - pt / 100.0):
                px = credit * (1 - pt / 100.0)
                events.append({"date": b["date"][:10], "type": "profit_take",
                               "detail": f"closed at {pt}% of max profit"})
                spr = sum(option_spread(abs(L["entry_px"]) * 0.5, b["close"], L["strike"], days_left) for L in legs)
                return make_trade(-(px + spr), b["date"][:10], "target")
        else:
            # Debit structures: stop_x = FRACTION of the debit allowed to be
            # lost (0.5 → stop when value falls to half the debit paid);
            # profit_take_pct = % gain on the debit.
            debit = -entry_cash
            best = max(legs_value(legs, b["high"], iv_k, days_left),
                       legs_value(legs, b["low"], iv_k, days_left))
            worst = min(legs_value(legs, b["high"], iv_k, days_left),
                        legs_value(legs, b["low"], iv_k, days_left))
            if stop_x is not None and worst <= debit * (1 - min(stop_x, 1.0)):
                px = max(0.0, debit * (1 - min(stop_x, 1.0)))
                events.append({"date": b["date"][:10], "type": "stop", "detail": "debit stop"})
                return make_trade(px - option_spread(px, b["close"], legs[0]["strike"], days_left),
                                  b["date"][:10], "stop")
            if pt is not None and best >= debit * (1 + pt / 100.0):
                px = debit * (1 + pt / 100.0)
                events.append({"date": b["date"][:10], "type": "profit_take",
                               "detail": f"+{pt}% on debit"})
                return make_trade(px - option_spread(px, b["close"], legs[0]["strike"], days_left),
                                  b["date"][:10], "target")

        # ── DTE-based exits ──
        if roll_dte is not None and days_left <= roll_dte:
            spr = sum(option_spread(max(0.02, abs(val_close)) / max(1, len(legs)),
                                    b["close"], L["strike"], days_left) for L in legs)
            events.append({"date": b["date"][:10], "type": "roll",
                           "detail": f"closed at {days_left} DTE to roll"})
            t = make_trade(val_close - spr, b["date"][:10], "roll")
            t["roll_signal_i"] = k
            return t
        if exit_dte is not None and days_left <= exit_dte:
            spr = sum(option_spread(max(0.02, abs(val_close)) / max(1, len(legs)),
                                    b["close"], L["strike"], days_left) for L in legs)
            events.append({"date": b["date"][:10], "type": "dte_exit",
                           "detail": f"closed at {days_left} DTE (rule: ≤{exit_dte})"})
            return make_trade(val_close - spr, b["date"][:10], "dte_exit")

        # ── early assignment (shorts only; labeled model). Checked LAST
        # in the day's sequence: stops/profit-takes/DTE exits are trader
        # decisions during market hours, while assignment notices arrive
        # OVERNIGHT after the close — so on a day when both could apply,
        # the trader's own exit wins (matches reality and keeps stop
        # semantics conservative rather than converting losses into
        # messier share assignments). ──
        for L in legs:
            if L["qty"] >= 0:
                continue
            spot_c = b["close"]
            intr = max(0.0, (spot_c - L["strike"]) if L["right"] == "call" else (L["strike"] - spot_c))
            if intr <= 0:
                continue
            T = year_fraction(days_left)
            extr = max(0.0, _bs_price(spot_c, L["strike"], T, iv_k, L["right"]) - intr)
            div_today = div_map.get(b["date"][:10], 0.0)
            call_div_assign = (L["right"] == "call" and div_today > 0 and div_today > extr)
            deep_itm_assign = extr < ASSIGN_EXTRINSIC
            if not (call_div_assign or deep_itm_assign):
                continue
            why = ("ex-div early exercise (dividend > extrinsic)" if call_div_assign
                   else "deep ITM (extrinsic < $%.02f)" % ASSIGN_EXTRINSIC)
            events.append({"date": b["date"][:10], "type": "assigned",
                           "detail": f"short {L['right']} {L['strike']} assigned — {why}"})
            other = [x for x in legs if x is not L]
            exit_val = -1 * intr    # we pay intrinsic on the assigned short
            exit_val += legs_value(other, spot_c, iv_k, days_left)
            stock_cash = 0.0
            reason = "assigned"
            if stock_shares and L["right"] == "call":
                stock_cash = (L["strike"] - stock_basis) * stock_shares
                exit_val += intr    # covered: delivering shares covers intrinsic
                reason = "called_away"
            elif L["right"] == "put":
                # Put assignment → long stock at strike; realize vs today's
                # close so the caller can chain into the wheel.
                stock_cash = (spot_c - L["strike"]) * CONTRACT * contracts
                exit_val += intr    # owning shares at strike offsets intrinsic paid
                reason = "assigned_shares"
            t = make_trade(exit_val, b["date"][:10], reason,
                           extra_cash=stock_cash, n_close_legs=len(other))
            t["assigned_leg"] = {"right": L["right"], "strike": L["strike"]}
            return t


    return None   # ran off history with the position open — not fairly markable


# ── Chains: rolls and the wheel ─────────────────────────────────────────────
def simulate_chain(bars, i_signal, structure, iv_series, mgmt, contracts=1,
                   params=None, dividends=None, max_links=40, quote_fn=None):
    """simulate_position plus continuation logic:
      • reason "roll"            → re-enter the SAME structure next bar
      • wheel: short_put assigned → covered calls (basis = put strike) until
        called away → back to short puts.
    Returns the list of linked trades (chain_id stamped)."""
    p = dict(params or {})
    wheel = structure == "wheel"
    cur_struct = "short_put" if wheel else structure
    out = []
    i = i_signal
    basis = None
    chain_id = f"{bars[i_signal]['date'][:10]}-{structure}"
    for _ in range(max_links):
        if i + 1 >= len(bars):
            break
        pp = dict(p)
        if cur_struct == "covered_call" and basis is not None:
            pp["stock_basis"] = basis
        t = simulate_position(bars, i, cur_struct, iv_series, mgmt,
                              contracts=contracts, params=pp, dividends=dividends,
                              quote_fn=quote_fn)
        if t is None:
            break
        t["chain_id"] = chain_id
        t["chain_seq"] = len(out)
        out.append(t)
        # Where does the chain go next?
        exit_i = next((k for k in range(i + 1, len(bars))
                       if bars[k]["date"][:10] == t["exit_date"]), None)
        if exit_i is None:
            break
        if t["reason"] == "roll":
            i = exit_i
            continue
        if wheel and t["reason"] == "assigned_shares":
            basis = t["assigned_leg"]["strike"]
            cur_struct = "covered_call"
            i = exit_i
            continue
        if wheel and t["reason"] in ("called_away",):
            basis = None
            cur_struct = "short_put"
            i = exit_i
            continue
        if wheel and t.get("stock_retained"):
            # CC expired OTM, still long shares → sell another call.
            i = exit_i
            continue
        break
    return out


# ── Portfolio simulator ─────────────────────────────────────────────────────
def run_portfolio(signals, bars_by_sym, iv_by_sym, structure, mgmt,
                  params=None, start_equity=100_000.0,
                  budget_per_trade=10_000.0, max_positions=5,
                  dividends_by_sym=None, progress_cb=None,
                  quote_fn_by_sym=None):
    """signals: chronological [(date_str, sym, i_signal)]. Each accepted
    signal runs simulate_chain; entries are gated by concurrent position
    count AND buying power (Σ open BP ≤ equity). Returns trades plus a
    DAILY mark-to-model equity curve (open positions marked from each
    trade's daily marks — drawdowns include open pain, unlike v1) and a
    BP-utilization series."""
    div_by = dividends_by_sym or {}
    trades = []
    skipped_bp = 0
    skipped_full = 0
    open_intervals = []          # (entry_date, exit_date, bp_dollars, sym)
    sym_busy_until = {}          # one position per symbol at a time

    for idx, (sig_date, sym, i) in enumerate(sorted(signals)):
        if progress_cb and idx % 10 == 0:
            progress_cb("simulating positions", idx + 1, len(signals))
        bars = bars_by_sym[sym]
        iv_series = iv_by_sym[sym]
        # One live position (or chain) per symbol — continuous-entry signal
        # streams re-enter the day after the previous chain closes.
        if sym_busy_until.get(sym) and sig_date <= sym_busy_until[sym]:
            continue
        open_intervals = [iv_ for iv_ in open_intervals if iv_[1] >= sig_date]
        live = [iv_ for iv_ in open_intervals if iv_[0] <= sig_date]
        if len(live) >= max_positions:
            skipped_full += 1
            continue
        bp_used = sum(iv_[2] for iv_ in live)
        # Size from budget; require at least 1 contract-set inside both the
        # budget AND remaining buying power.
        iv0 = iv_series[i] if i < len(iv_series) else None
        if not iv0:
            continue
        spot0 = bars[i + 1]["open"] if i + 1 < len(bars) else None
        if not spot0:
            continue
        probe = build_structure("short_put" if structure == "wheel" else structure,
                                spot0, iv0, float((params or {}).get("dte") or 45),
                                params)
        if not probe:
            continue
        est_credit = 0.0
        T0 = year_fraction(float((params or {}).get("dte") or 45))
        for L in probe["legs"]:
            est_credit += -L["qty"] * _bs_price(spot0, L["strike"], T0, iv0, L["right"])
        bp_set = bp_requirement("short_put" if structure == "wheel" else structure,
                                probe["legs"], spot0, max(est_credit, 0.0),
                                probe["width"])
        if bp_set <= 0:
            continue
        contracts = max(1, int(budget_per_trade // bp_set)) if bp_set <= budget_per_trade else 1
        need = bp_set * contracts
        if bp_used + need > start_equity:
            if bp_used + bp_set <= start_equity:
                contracts = 1
                need = bp_set
            else:
                skipped_bp += 1
                continue
        chain = simulate_chain(bars, i, structure, iv_series, mgmt,
                               contracts=contracts, params=params,
                               dividends=div_by.get(sym),
                               quote_fn=(quote_fn_by_sym or {}).get(sym))
        for t in chain:
            t["symbol"] = sym
        if not chain:
            continue
        trades.extend(chain)
        # Reserve capacity from the SIGNAL date (the decision moment), not
        # the T+1 fill date — otherwise every same-day signal sees zero
        # open positions and the BP/position gates never bind.
        open_intervals.append((sig_date, chain[-1]["exit_date"], need, sym))
        sym_busy_until[sym] = chain[-1]["exit_date"]

    # ── daily mark-to-model equity curve ──
    daily = {}                   # date → unrealized Δ from open marks
    realized = {}                # exit date → realized pnl
    for t in trades:
        realized[t["exit_date"]] = realized.get(t["exit_date"], 0.0) + t["pnl"]
        entry_cash = t["credit"] if t["is_credit"] else -t["credit"]
        for m in t["marks"]:
            if m["date"] >= t["exit_date"]:
                continue
            open_pl = (entry_cash + m["value"]) * t["contracts"] * CONTRACT
            daily[m["date"]] = daily.get(m["date"], 0.0) + open_pl
    all_dates = sorted(set(list(daily.keys()) + list(realized.keys())))
    eq = start_equity
    curve = []
    cum_realized = 0.0
    for d in all_dates:
        cum_realized += realized.get(d, 0.0)
        eq_d = start_equity + cum_realized + daily.get(d, 0.0)
        curve.append({"date": d, "equity": round(eq_d, 2)})
    return {"trades": trades, "equity_curve": curve,
            "skipped_bp": skipped_bp, "skipped_max_positions": skipped_full}


__all__ = ["build_structure", "strike_by_delta", "simulate_position",
           "simulate_chain", "run_portfolio", "bp_requirement",
           "option_spread", "legs_value", "legs_intrinsic", "leg_costs",
           "amer_price", "CONTRACT"]
