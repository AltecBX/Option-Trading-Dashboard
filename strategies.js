(function () {
// strategies.jsx
// Generic leg-based options engine + 12 strategy definitions.
//
// A "leg" is { type, strike, qty, premium, dte, iv }
//   type    : "call" | "put" | "stock"
//   strike  : strike price ("call"/"put") or cost basis ("stock")
//   qty     : positive = long, negative = short, in shares (stock) or contracts (options, scaled to per-share)
//   premium : entry mid price per share (positive number)
//   dte     : days to expiration (0 for stock, 5 for next-Friday options, ~30 for back-month, etc.)
//   iv      : implied volatility (annualized decimal, e.g. 0.30) — only used for non-zero remaining time
//
// At expiration of the *nearest-DTE leg*, value each leg as:
//   - if leg.dte == evalDTE  → intrinsic value
//   - if leg.dte >  evalDTE  → Black-Scholes price with remaining time
//   - stock                  → S - costBasis
// Per-share P/L per leg = qty * (legValueAtEval - premiumPaid)
// (premium is what you paid; for shorts, qty is negative, so credit collected flips sign correctly.)

(function () {
  // ─────────────────────────────────────────────────────────────────────────
  // Black-Scholes for back-month leg valuation at front-month expiration.
  // Fixture-matched against the canonical server engine (metrics.py) by
  // test_strategy_fixtures.js via fixtures/options_math.json — same
  // conventions: T = days/365, sigma annualized decimal, r default 0.045,
  // q = 0 (this preview engine does not model dividends).
  // ─────────────────────────────────────────────────────────────────────────
  function normCdf(x) {
    // Abramowitz-Stegun 7.1.26 approximation. Max error ~7.5e-8.
    const a1 = 0.254829592,
      a2 = -0.284496736,
      a3 = 1.421413741;
    const a4 = -1.453152027,
      a5 = 1.061405429,
      p = 0.3275911;
    const sign = x < 0 ? -1 : 1;
    const ax = Math.abs(x) / Math.sqrt(2);
    const t = 1 / (1 + p * ax);
    const y = 1 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-ax * ax);
    return 0.5 * (1 + sign * y);
  }
  function bsPrice(S, K, T, sigma, isCall, r = 0.045) {
    if (T <= 1e-9 || sigma <= 1e-9 || S <= 0) {
      return Math.max(isCall ? S - K : K - S, 0);
    }
    const sqrtT = Math.sqrt(T);
    const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
    const d2 = d1 - sigma * sqrtT;
    if (isCall) return S * normCdf(d1) - K * Math.exp(-r * T) * normCdf(d2);
    return K * Math.exp(-r * T) * normCdf(-d2) - S * normCdf(-d1);
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Leg evaluation at a given underlying price S.
  // ─────────────────────────────────────────────────────────────────────────
  function legValueAt(leg, S, evalDTE) {
    if (leg.type === "stock") return S - leg.strike;
    const remainingDays = Math.max(0, (leg.dte || 0) - (evalDTE || 0));
    if (remainingDays <= 0) {
      // intrinsic
      if (leg.type === "call") return Math.max(S - leg.strike, 0);
      return Math.max(leg.strike - S, 0);
    }
    const T = remainingDays / 365;
    const sigma = leg.iv || 0.3;
    return bsPrice(S, leg.strike, T, sigma, leg.type === "call");
  }
  function evalDTEFor(legs) {
    // Nearest non-zero DTE among option legs; 0 if only stock or all expired
    const opt = legs.filter(l => l.type !== "stock" && (l.dte || 0) > 0);
    if (!opt.length) return 0;
    return Math.min(...opt.map(l => l.dte));
  }
  function pnlAt(legs, S) {
    const evalDTE = evalDTEFor(legs);
    let pl = 0;
    for (const leg of legs) {
      const v = legValueAt(leg, S, evalDTE);
      pl += leg.qty * (v - leg.premium);
    }
    return pl;
  }

  // Sample the P/L curve over a price range
  function pnlCurve(legs, lower, upper, N = 240) {
    const pts = [];
    for (let i = 0; i <= N; i++) {
      const S = lower + i / N * (upper - lower);
      pts.push({
        s: S,
        pl: pnlAt(legs, S)
      });
    }
    return pts;
  }

  // Find break-evens by walking the curve
  function breakEvens(curve) {
    const out = [];
    for (let i = 1; i < curve.length; i++) {
      const a = curve[i - 1],
        b = curve[i];
      if (a.pl <= 0 && b.pl >= 0 || a.pl >= 0 && b.pl <= 0) {
        if (a.pl === b.pl) continue;
        const t = -a.pl / (b.pl - a.pl);
        const s = a.s + t * (b.s - a.s);
        // Dedupe: when a sample lands exactly on zero, both adjacent
        // segments report the same crossing — keep one marker.
        if (out.length && Math.abs(s - out[out.length - 1]) < 1e-6) continue;
        out.push(s);
      }
    }
    return out;
  }

  // Net entry credit (positive) or debit (negative)
  function netCredit(legs) {
    // For a sold leg: qty < 0, and we receive premium, so cash = -qty * premium = |qty|*premium
    // For a bought leg: qty > 0, and we pay premium, so cash = -qty * premium (negative)
    let cash = 0;
    for (const leg of legs) {
      if (leg.type === "stock") continue;
      cash += -leg.qty * leg.premium;
    }
    return cash;
  }

  // Max profit / max loss from the sampled curve (with caveat: undefined-risk strategies cap at the sample window)
  function pnlBounds(curve) {
    let lo = Infinity,
      hi = -Infinity;
    for (const pt of curve) {
      if (pt.pl < lo) lo = pt.pl;
      if (pt.pl > hi) hi = pt.pl;
    }
    return {
      min: lo,
      max: hi
    };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Helpers used by strategy builders
  // ─────────────────────────────────────────────────────────────────────────
  function midOf(q) {
    if (!q) return 0;
    if (q.bid > 0 && q.ask > 0) return (q.bid + q.ask) / 2;
    if (q.last > 0) return q.last;
    return q.bid || q.ask || 0;
  }
  function nearest(arr, target) {
    if (!arr || !arr.length) return null;
    return arr.reduce((a, b) => Math.abs(a.strike - target) < Math.abs(b.strike - target) ? a : b);
  }

  // Approximate a back-month premium when the chain doesn't include far expirations.
  // Premium scales roughly with sqrt(T) for ATM options.
  function backMonthPremium(frontPremium, frontDTE, backDTE) {
    if (frontDTE <= 0) return frontPremium;
    return frontPremium * Math.sqrt(backDTE / frontDTE);
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Strategy library. Each entry returns { legs, stats, note } given a context.
  //
  // Context:
  //   { sugCall, sugPut, callMid, putMid, callAtSug, putAtSug,
  //     atmCall, atmPut, atmCallMid, atmPutMid,
  //     currentPrice, calls, puts, FRONT_DTE }
  //
  // FRONT_DTE = days to the front-month expiry shown in the dashboard (~5 for next-Friday).
  // ─────────────────────────────────────────────────────────────────────────

  const STRATEGIES = [
  // 1. Covered Call
  {
    key: "covered_call",
    n: "01",
    name: "Covered Call",
    tag: "income",
    direction: "neutral-bullish",
    tone: "up",
    definedRisk: false,
    build: ctx => {
      const {
        sugCall,
        callAtSug,
        callMid,
        currentPrice,
        FRONT_DTE
      } = ctx;
      const legs = [{
        type: "stock",
        strike: currentPrice,
        qty: 100,
        premium: 0,
        dte: 0,
        iv: 0
      }, {
        type: "call",
        strike: sugCall,
        qty: -100,
        premium: callMid,
        dte: FRONT_DTE,
        iv: callAtSug.iv
      }];
      return {
        legs,
        structure: `Sell the $${sugCall.toFixed(2)} call against 100 shares you already own.`,
        stats: [["Premium", `$${callMid.toFixed(2)}/sh`], ["If unchanged", `+$${callMid.toFixed(2)}/sh (${(callMid / currentPrice * 100).toFixed(2)}%)`], ["If called away", `+$${(sugCall - currentPrice + callMid).toFixed(2)}/sh (${((sugCall + callMid) / currentPrice * 100 - 100).toFixed(2)}%)`]],
        note: "Best if price stays below the strike. Steady income while you hold."
      };
    }
  },
  // 2. Cash-Secured Put
  {
    key: "cash_secured_put",
    n: "02",
    name: "Cash-Secured Put",
    tag: "income · bullish",
    direction: "neutral-bullish",
    tone: "down",
    definedRisk: false,
    build: ctx => {
      const {
        sugPut,
        putAtSug,
        putMid,
        FRONT_DTE
      } = ctx;
      const legs = [{
        type: "put",
        strike: sugPut,
        qty: -100,
        premium: putMid,
        dte: FRONT_DTE,
        iv: putAtSug.iv
      }];
      return {
        legs,
        structure: `Sell the $${sugPut.toFixed(2)} put, backed by $${(sugPut * 100).toFixed(0)} cash per contract.`,
        stats: [["Premium", `$${putMid.toFixed(2)}/sh`], ["Max profit", `$${putMid.toFixed(2)}/sh (${(putMid / sugPut * 100).toFixed(2)}% on cash)`], ["Cost basis if assigned", `$${(sugPut - putMid).toFixed(2)}`]],
        note: "Attractive if you'd be happy owning the stock at the strike."
      };
    }
  },
  // 3. Short Strangle
  {
    key: "short_strangle",
    n: "03",
    name: "Short Strangle",
    tag: "neutral · undefined risk",
    direction: "neutral",
    tone: "accent",
    definedRisk: false,
    build: ctx => {
      const {
        sugCall,
        sugPut,
        callAtSug,
        putAtSug,
        callMid,
        putMid,
        FRONT_DTE
      } = ctx;
      const totalCredit = callMid + putMid;
      const legs = [{
        type: "call",
        strike: sugCall,
        qty: -100,
        premium: callMid,
        dte: FRONT_DTE,
        iv: callAtSug.iv
      }, {
        type: "put",
        strike: sugPut,
        qty: -100,
        premium: putMid,
        dte: FRONT_DTE,
        iv: putAtSug.iv
      }];
      return {
        legs,
        structure: `Sell $${sugCall.toFixed(2)} call & $${sugPut.toFixed(2)} put.`,
        stats: [["Total credit", `$${totalCredit.toFixed(2)}/sh`], ["Upper B/E", `$${(sugCall + totalCredit).toFixed(2)}`], ["Lower B/E", `$${(sugPut - totalCredit).toFixed(2)}`]],
        note: "High win rate if stock stays inside the range. Risk is unlimited on both sides."
      };
    }
  },
  // 4. Iron Condor
  {
    key: "iron_condor",
    n: "04",
    name: "Iron Condor",
    tag: "neutral · defined risk",
    direction: "neutral",
    tone: "accent",
    definedRisk: true,
    build: ctx => {
      const {
        sugCall,
        sugPut,
        callAtSug,
        putAtSug,
        callMid,
        putMid,
        FRONT_DTE,
        calls,
        puts
      } = ctx;
      const callsAbove = calls.filter(c => c.strike > sugCall);
      const putsBelow = puts.filter(p => p.strike < sugPut);
      const callWing = callsAbove.length ? nearest(callsAbove, sugCall * 1.05) : {
        strike: sugCall,
        bid: 0,
        ask: 0,
        iv: callAtSug.iv
      };
      const putWing = putsBelow.length ? nearest(putsBelow, sugPut * 0.95) : {
        strike: sugPut,
        bid: 0,
        ask: 0,
        iv: putAtSug.iv
      };
      const callWingMid = midOf(callWing);
      const putWingMid = midOf(putWing);
      const totalCredit = callMid + putMid - callWingMid - putWingMid;
      const callWidth = callWing.strike - sugCall;
      const putWidth = sugPut - putWing.strike;
      const maxLoss = Math.max(callWidth, putWidth) - totalCredit;
      const legs = [{
        type: "call",
        strike: sugCall,
        qty: -100,
        premium: callMid,
        dte: FRONT_DTE,
        iv: callAtSug.iv
      }, {
        type: "call",
        strike: callWing.strike,
        qty: 100,
        premium: callWingMid,
        dte: FRONT_DTE,
        iv: callWing.iv || callAtSug.iv
      }, {
        type: "put",
        strike: sugPut,
        qty: -100,
        premium: putMid,
        dte: FRONT_DTE,
        iv: putAtSug.iv
      }, {
        type: "put",
        strike: putWing.strike,
        qty: 100,
        premium: putWingMid,
        dte: FRONT_DTE,
        iv: putWing.iv || putAtSug.iv
      }];
      return {
        legs,
        structure: `Strangle + buy $${callWing.strike.toFixed(2)} call & $${putWing.strike.toFixed(2)} put wings.`,
        stats: [["Net credit", `$${totalCredit.toFixed(2)}/sh`], ["Wing widths", `$${callWidth.toFixed(2)} call · $${putWidth.toFixed(2)} put`], ["Profit zone", `$${sugPut.toFixed(2)} – $${sugCall.toFixed(2)}`], ["Max loss", `$${maxLoss.toFixed(2)}/sh`]],
        note: "Defined-risk strangle. Lower margin, smaller credit."
      };
    }
  },
  // 5. Short Straddle
  {
    key: "short_straddle",
    n: "05",
    name: "Short Straddle",
    tag: "neutral · max premium",
    direction: "neutral",
    tone: "warn",
    definedRisk: false,
    build: ctx => {
      const {
        atmCall,
        atmPut,
        atmCallMid,
        atmPutMid,
        FRONT_DTE
      } = ctx;
      const totalCredit = atmCallMid + atmPutMid;
      const legs = [{
        type: "call",
        strike: atmCall.strike,
        qty: -100,
        premium: atmCallMid,
        dte: FRONT_DTE,
        iv: atmCall.iv
      }, {
        type: "put",
        strike: atmPut.strike,
        qty: -100,
        premium: atmPutMid,
        dte: FRONT_DTE,
        iv: atmPut.iv
      }];
      return {
        legs,
        structure: `Sell ATM call & put at $${atmCall.strike.toFixed(2)}.`,
        stats: [["Total credit", `$${totalCredit.toFixed(2)}/sh`], ["Upper B/E", `$${(atmCall.strike + totalCredit).toFixed(2)}`], ["Lower B/E", `$${(atmPut.strike - totalCredit).toFixed(2)}`]],
        note: "Max premium, narrowest profit zone. Bet on a quiet week."
      };
    }
  },
  // 6. Bull Put Spread
  {
    key: "bull_put_spread",
    n: "06",
    name: "Bull Put Spread",
    tag: "bullish · defined risk",
    direction: "bullish",
    tone: "up",
    definedRisk: true,
    build: ctx => {
      const {
        sugPut,
        putAtSug,
        putMid,
        FRONT_DTE,
        puts
      } = ctx;
      const putsBelow = puts.filter(p => p.strike < sugPut);
      const putWing = putsBelow.length ? nearest(putsBelow, sugPut * 0.95) : {
        strike: sugPut,
        bid: 0,
        ask: 0,
        iv: putAtSug.iv
      };
      const putWingMid = midOf(putWing);
      const credit = putMid - putWingMid;
      const width = sugPut - putWing.strike;
      const maxLoss = width - credit;
      const legs = [{
        type: "put",
        strike: sugPut,
        qty: -100,
        premium: putMid,
        dte: FRONT_DTE,
        iv: putAtSug.iv
      }, {
        type: "put",
        strike: putWing.strike,
        qty: 100,
        premium: putWingMid,
        dte: FRONT_DTE,
        iv: putWing.iv || putAtSug.iv
      }];
      return {
        legs,
        structure: `Sell $${sugPut.toFixed(2)} put & buy $${putWing.strike.toFixed(2)} put.`,
        stats: [["Net credit", `$${credit.toFixed(2)}/sh`], ["Spread width", `$${width.toFixed(2)}`], ["Max loss", `$${maxLoss.toFixed(2)}/sh`]],
        note: "Bullish-neutral. Profits if stock stays above the sold put."
      };
    }
  },
  // 7. Bear Call Spread
  {
    key: "bear_call_spread",
    n: "07",
    name: "Bear Call Spread",
    tag: "bearish · defined risk",
    direction: "bearish",
    tone: "down",
    definedRisk: true,
    build: ctx => {
      const {
        sugCall,
        callAtSug,
        callMid,
        FRONT_DTE,
        calls
      } = ctx;
      const callsAbove = calls.filter(c => c.strike > sugCall);
      const callWing = callsAbove.length ? nearest(callsAbove, sugCall * 1.05) : {
        strike: sugCall,
        bid: 0,
        ask: 0,
        iv: callAtSug.iv
      };
      const callWingMid = midOf(callWing);
      const credit = callMid - callWingMid;
      const width = callWing.strike - sugCall;
      const maxLoss = width - credit;
      const legs = [{
        type: "call",
        strike: sugCall,
        qty: -100,
        premium: callMid,
        dte: FRONT_DTE,
        iv: callAtSug.iv
      }, {
        type: "call",
        strike: callWing.strike,
        qty: 100,
        premium: callWingMid,
        dte: FRONT_DTE,
        iv: callWing.iv || callAtSug.iv
      }];
      return {
        legs,
        structure: `Sell $${sugCall.toFixed(2)} call & buy $${callWing.strike.toFixed(2)} call.`,
        stats: [["Net credit", `$${credit.toFixed(2)}/sh`], ["Spread width", `$${width.toFixed(2)}`], ["Max loss", `$${maxLoss.toFixed(2)}/sh`]],
        note: "Bearish-neutral. Profits if stock stays below the sold call."
      };
    }
  },
  // 8. Calendar Spread (long calendar at ATM)
  {
    key: "calendar_spread",
    n: "08",
    name: "Calendar Spread",
    tag: "neutral · time decay",
    direction: "neutral",
    tone: "accent",
    definedRisk: true,
    build: ctx => {
      const {
        atmCall,
        atmCallMid,
        FRONT_DTE
      } = ctx;
      const BACK_DTE = 30;
      const backPrem = backMonthPremium(atmCallMid, FRONT_DTE, BACK_DTE);
      const debit = backPrem - atmCallMid;
      const legs = [{
        type: "call",
        strike: atmCall.strike,
        qty: -100,
        premium: atmCallMid,
        dte: FRONT_DTE,
        iv: atmCall.iv
      }, {
        type: "call",
        strike: atmCall.strike,
        qty: 100,
        premium: backPrem,
        dte: BACK_DTE,
        iv: atmCall.iv
      }];
      return {
        legs,
        structure: `Sell $${atmCall.strike.toFixed(2)} front-week call, buy $${atmCall.strike.toFixed(2)} call ~30 DTE.`,
        stats: [["Net debit", `$${debit.toFixed(2)}/sh (est.)`], ["Peak P/L at", `$${atmCall.strike.toFixed(2)} (the strike)`], ["Back-month IV", `${(atmCall.iv * 100).toFixed(1)}%`], ["Max loss", `$${debit.toFixed(2)}/sh (debit paid)`]],
        note: "Profits if stock pins near the strike at front expiration. Back leg priced via Black-Scholes."
      };
    }
  },
  // 9. Diagonal Spread (sell OTM front, buy ATM back)
  {
    key: "diagonal_spread",
    n: "09",
    name: "Diagonal Spread",
    tag: "neutral-bullish · time decay",
    direction: "neutral-bullish",
    tone: "accent",
    definedRisk: true,
    build: ctx => {
      const {
        sugCall,
        callAtSug,
        callMid,
        atmCall,
        atmCallMid,
        FRONT_DTE
      } = ctx;
      const BACK_DTE = 30;
      const backPrem = backMonthPremium(atmCallMid, FRONT_DTE, BACK_DTE);
      const debit = backPrem - callMid;
      const legs = [{
        type: "call",
        strike: sugCall,
        qty: -100,
        premium: callMid,
        dte: FRONT_DTE,
        iv: callAtSug.iv
      }, {
        type: "call",
        strike: atmCall.strike,
        qty: 100,
        premium: backPrem,
        dte: BACK_DTE,
        iv: atmCall.iv
      }];
      return {
        legs,
        structure: `Sell $${sugCall.toFixed(2)} front-week call, buy $${atmCall.strike.toFixed(2)} call ~30 DTE.`,
        stats: [["Net debit", `$${debit.toFixed(2)}/sh (est.)`], ["Short strike", `$${sugCall.toFixed(2)}`], ["Long strike", `$${atmCall.strike.toFixed(2)}`], ["Best at expiry", `$${sugCall.toFixed(2)} (short strike)`]],
        note: "Time-decay play with a directional lean. Lower long strike adds bullish bias."
      };
    }
  },
  // 10. Jade Lizard
  {
    key: "jade_lizard",
    n: "10",
    name: "Jade Lizard",
    tag: "neutral-bullish · no upside risk",
    direction: "neutral-bullish",
    tone: "up",
    definedRisk: false,
    build: ctx => {
      const {
        sugCall,
        sugPut,
        callAtSug,
        putAtSug,
        callMid,
        putMid,
        FRONT_DTE,
        calls
      } = ctx;
      // Build call spread: sell sugCall, buy a wing wide enough that totalCredit > callWidth
      const callsAbove = calls.filter(c => c.strike > sugCall);
      if (!callsAbove.length) return null;
      // Pick the tightest wing that satisfies "no upside risk"
      let chosenWing = null;
      for (const wing of callsAbove.sort((a, b) => a.strike - b.strike)) {
        const wingMid = midOf(wing);
        const totalCredit = putMid + callMid - wingMid;
        const callWidth = wing.strike - sugCall;
        if (totalCredit >= callWidth) {
          chosenWing = wing;
          break;
        }
      }
      const callWing = chosenWing || nearest(callsAbove, sugCall * 1.05);
      const callWingMid = midOf(callWing);
      const totalCredit = putMid + callMid - callWingMid;
      const callWidth = callWing.strike - sugCall;
      const noUpside = totalCredit >= callWidth;
      const legs = [{
        type: "put",
        strike: sugPut,
        qty: -100,
        premium: putMid,
        dte: FRONT_DTE,
        iv: putAtSug.iv
      }, {
        type: "call",
        strike: sugCall,
        qty: -100,
        premium: callMid,
        dte: FRONT_DTE,
        iv: callAtSug.iv
      }, {
        type: "call",
        strike: callWing.strike,
        qty: 100,
        premium: callWingMid,
        dte: FRONT_DTE,
        iv: callWing.iv || callAtSug.iv
      }];
      return {
        legs,
        structure: `Sell $${sugPut.toFixed(2)} put + sell $${sugCall.toFixed(2)} call & buy $${callWing.strike.toFixed(2)} call.`,
        stats: [["Total credit", `$${totalCredit.toFixed(2)}/sh`], ["Call wing width", `$${callWidth.toFixed(2)}`], ["Upside risk", noUpside ? "None (credit ≥ width)" : `$${(callWidth - totalCredit).toFixed(2)}/sh`], ["Lower B/E", `$${(sugPut - totalCredit).toFixed(2)}`]],
        note: noUpside ? "No upside risk: credit covers the call spread. Only loses if stock crashes below the put." : "Wider wings would eliminate upside risk. Loses on a rally past the long call."
      };
    }
  },
  // 11. Call Ratio Spread (1x2, debit version)
  {
    key: "ratio_spread",
    n: "11",
    name: "Call Ratio Spread",
    tag: "moderately bullish · 1x2",
    direction: "neutral-bullish",
    tone: "warn",
    definedRisk: false,
    build: ctx => {
      const {
        atmCall,
        atmCallMid,
        sugCall,
        callAtSug,
        callMid,
        currentPrice,
        FRONT_DTE
      } = ctx;
      // Buy 1 ATM, sell 2 OTM at sugCall
      const debit = atmCallMid - 2 * callMid;
      const peakPrice = sugCall;
      const peakPL = sugCall - atmCall.strike - debit;
      const upperBE = sugCall + Math.max(peakPL, 0);
      const legs = [{
        type: "call",
        strike: atmCall.strike,
        qty: 100,
        premium: atmCallMid,
        dte: FRONT_DTE,
        iv: atmCall.iv
      }, {
        type: "call",
        strike: sugCall,
        qty: -200,
        premium: callMid,
        dte: FRONT_DTE,
        iv: callAtSug.iv
      }];
      return {
        legs,
        structure: `Buy 1 $${atmCall.strike.toFixed(2)} call, sell 2 $${sugCall.toFixed(2)} calls.`,
        stats: [["Net cost", debit >= 0 ? `$${debit.toFixed(2)}/sh debit` : `$${(-debit).toFixed(2)}/sh credit`], ["Peak P/L at", `$${peakPrice.toFixed(2)}`], ["Max profit", `$${peakPL.toFixed(2)}/sh`], ["Upper B/E", `$${upperBE.toFixed(2)}`]],
        note: "Profits in a moderate move toward the short strike. Loses past it. Naked above — manage size."
      };
    }
  },
  // 12. Long Call — directional bullish, defined risk
  {
    key: "long_call",
    n: "12",
    name: "Long Call",
    tag: "bullish · defined risk",
    direction: "bullish",
    tone: "up",
    definedRisk: true,
    build: ctx => {
      const {
        sugCall,
        callAtSug,
        callMid,
        FRONT_DTE
      } = ctx;
      const legs = [{
        type: "call",
        strike: sugCall,
        qty: 100,
        premium: callMid,
        dte: FRONT_DTE,
        iv: callAtSug.iv
      }];
      return {
        legs,
        structure: `Buy the $${sugCall.toFixed(2)} call. Pay $${callMid.toFixed(2)}/share.`,
        stats: [["Cost", `$${callMid.toFixed(2)}/sh ($${(callMid * 100).toFixed(0)}/contract)`], ["Break-even", `$${(sugCall + callMid).toFixed(2)}`], ["Max loss", `$${callMid.toFixed(2)}/sh`], ["Max profit", "Unlimited above break-even"]],
        note: "Pure directional long upside. Time decay works against you."
      };
    }
  },
  // 13. Long Put — directional bearish, defined risk
  {
    key: "long_put",
    n: "13",
    name: "Long Put",
    tag: "bearish · defined risk",
    direction: "bearish",
    tone: "down",
    definedRisk: true,
    build: ctx => {
      const {
        sugPut,
        putAtSug,
        putMid,
        FRONT_DTE
      } = ctx;
      const legs = [{
        type: "put",
        strike: sugPut,
        qty: 100,
        premium: putMid,
        dte: FRONT_DTE,
        iv: putAtSug.iv
      }];
      return {
        legs,
        structure: `Buy the $${sugPut.toFixed(2)} put. Pay $${putMid.toFixed(2)}/share.`,
        stats: [["Cost", `$${putMid.toFixed(2)}/sh`], ["Break-even", `$${(sugPut - putMid).toFixed(2)}`], ["Max loss", `$${putMid.toFixed(2)}/sh`], ["Max profit", `$${(sugPut - putMid).toFixed(2)}/sh if stock goes to zero`]],
        note: "Defined-risk bearish bet or hedge against existing long shares."
      };
    }
  },
  // 14. Long Straddle — vol expansion play
  {
    key: "long_straddle",
    n: "14",
    name: "Long Straddle",
    tag: "vol expansion · defined risk",
    direction: "neutral-vol",
    tone: "accent",
    definedRisk: true,
    build: ctx => {
      const {
        calls,
        puts,
        currentPrice,
        FRONT_DTE
      } = ctx;
      const atmCall = nearest(calls, currentPrice);
      const atmPut = nearest(puts, currentPrice);
      const cMid = midOf(atmCall),
        pMid = midOf(atmPut);
      const cost = cMid + pMid;
      const legs = [{
        type: "call",
        strike: atmCall.strike,
        qty: 100,
        premium: cMid,
        dte: FRONT_DTE,
        iv: atmCall.iv
      }, {
        type: "put",
        strike: atmPut.strike,
        qty: 100,
        premium: pMid,
        dte: FRONT_DTE,
        iv: atmPut.iv
      }];
      return {
        legs,
        structure: `Buy ATM $${atmCall.strike.toFixed(2)} call + buy ATM $${atmPut.strike.toFixed(2)} put.`,
        stats: [["Total cost", `$${cost.toFixed(2)}/sh`], ["Upper break-even", `$${(atmCall.strike + cost).toFixed(2)}`], ["Lower break-even", `$${(atmPut.strike - cost).toFixed(2)}`], ["Max loss", `$${cost.toFixed(2)}/sh if pinned at strike`]],
        note: "Profit if stock moves more than the cost. Best ahead of catalysts (earnings, FDA, etc.)."
      };
    }
  },
  // 15. Long Strangle — wider vol expansion play
  {
    key: "long_strangle",
    n: "15",
    name: "Long Strangle",
    tag: "vol expansion · defined risk",
    direction: "neutral-vol",
    tone: "accent",
    definedRisk: true,
    build: ctx => {
      const {
        sugCall,
        sugPut,
        callAtSug,
        putAtSug,
        callMid,
        putMid,
        FRONT_DTE
      } = ctx;
      const cost = callMid + putMid;
      const legs = [{
        type: "call",
        strike: sugCall,
        qty: 100,
        premium: callMid,
        dte: FRONT_DTE,
        iv: callAtSug.iv
      }, {
        type: "put",
        strike: sugPut,
        qty: 100,
        premium: putMid,
        dte: FRONT_DTE,
        iv: putAtSug.iv
      }];
      return {
        legs,
        structure: `Buy OTM $${sugCall.toFixed(2)} call + buy OTM $${sugPut.toFixed(2)} put.`,
        stats: [["Total cost", `$${cost.toFixed(2)}/sh`], ["Upper break-even", `$${(sugCall + cost).toFixed(2)}`], ["Lower break-even", `$${(sugPut - cost).toFixed(2)}`], ["Max loss", `$${cost.toFixed(2)}/sh if pinned between strikes`]],
        note: "Cheaper than a straddle but needs a bigger move to profit."
      };
    }
  },
  // 16. Bull Call Spread — debit, bullish, defined risk
  {
    key: "bull_call_spread",
    n: "16",
    name: "Bull Call Spread",
    tag: "bullish · debit · defined risk",
    direction: "bullish",
    tone: "up",
    definedRisk: true,
    build: ctx => {
      const {
        calls,
        currentPrice,
        FRONT_DTE
      } = ctx;
      const longLeg = nearest(calls, currentPrice);
      const shortLeg = nearest(calls.filter(c => c.strike >= currentPrice * 1.03), currentPrice * 1.03) || nearest(calls, currentPrice * 1.03);
      const longMid = midOf(longLeg),
        shortMid = midOf(shortLeg);
      const debit = longMid - shortMid;
      const width = shortLeg.strike - longLeg.strike;
      const legs = [{
        type: "call",
        strike: longLeg.strike,
        qty: 100,
        premium: longMid,
        dte: FRONT_DTE,
        iv: longLeg.iv
      }, {
        type: "call",
        strike: shortLeg.strike,
        qty: -100,
        premium: shortMid,
        dte: FRONT_DTE,
        iv: shortLeg.iv
      }];
      return {
        legs,
        structure: `Buy $${longLeg.strike.toFixed(2)} call, sell $${shortLeg.strike.toFixed(2)} call.`,
        stats: [["Net debit", `$${debit.toFixed(2)}/sh`], ["Max profit", `$${(width - debit).toFixed(2)}/sh`], ["Max loss", `$${debit.toFixed(2)}/sh`], ["Break-even", `$${(longLeg.strike + debit).toFixed(2)}`]],
        note: "Cheaper bullish bet than a long call. Caps upside but reduces cost."
      };
    }
  },
  // 17. Bear Put Spread — debit, bearish, defined risk
  {
    key: "bear_put_spread",
    n: "17",
    name: "Bear Put Spread",
    tag: "bearish · debit · defined risk",
    direction: "bearish",
    tone: "down",
    definedRisk: true,
    build: ctx => {
      const {
        puts,
        currentPrice,
        FRONT_DTE
      } = ctx;
      const longLeg = nearest(puts, currentPrice);
      const shortLeg = nearest(puts.filter(p => p.strike <= currentPrice * 0.97), currentPrice * 0.97) || nearest(puts, currentPrice * 0.97);
      const longMid = midOf(longLeg),
        shortMid = midOf(shortLeg);
      const debit = longMid - shortMid;
      const width = longLeg.strike - shortLeg.strike;
      const legs = [{
        type: "put",
        strike: longLeg.strike,
        qty: 100,
        premium: longMid,
        dte: FRONT_DTE,
        iv: longLeg.iv
      }, {
        type: "put",
        strike: shortLeg.strike,
        qty: -100,
        premium: shortMid,
        dte: FRONT_DTE,
        iv: shortLeg.iv
      }];
      return {
        legs,
        structure: `Buy $${longLeg.strike.toFixed(2)} put, sell $${shortLeg.strike.toFixed(2)} put.`,
        stats: [["Net debit", `$${debit.toFixed(2)}/sh`], ["Max profit", `$${(width - debit).toFixed(2)}/sh`], ["Max loss", `$${debit.toFixed(2)}/sh`], ["Break-even", `$${(longLeg.strike - debit).toFixed(2)}`]],
        note: "Cheaper bearish bet than a long put. Caps downside but reduces cost."
      };
    }
  },
  // 18. Long Call Butterfly — pin-the-strike play
  {
    key: "long_butterfly",
    n: "18",
    name: "Long Call Butterfly",
    tag: "neutral · pin play · defined risk",
    direction: "neutral",
    tone: "accent",
    definedRisk: true,
    build: ctx => {
      const {
        calls,
        currentPrice,
        FRONT_DTE
      } = ctx;
      const center = nearest(calls, currentPrice);
      const wingW = currentPrice * 0.025;
      const lowerWing = nearest(calls.filter(c => c.strike <= center.strike - wingW), center.strike - wingW) || nearest(calls, center.strike - wingW);
      const upperWing = nearest(calls.filter(c => c.strike >= center.strike + wingW), center.strike + wingW) || nearest(calls, center.strike + wingW);
      const cMid = midOf(center),
        lMid = midOf(lowerWing),
        uMid = midOf(upperWing);
      const debit = lMid + uMid - 2 * cMid;
      const width = center.strike - lowerWing.strike;
      const legs = [{
        type: "call",
        strike: lowerWing.strike,
        qty: 100,
        premium: lMid,
        dte: FRONT_DTE,
        iv: lowerWing.iv
      }, {
        type: "call",
        strike: center.strike,
        qty: -200,
        premium: cMid,
        dte: FRONT_DTE,
        iv: center.iv
      }, {
        type: "call",
        strike: upperWing.strike,
        qty: 100,
        premium: uMid,
        dte: FRONT_DTE,
        iv: upperWing.iv
      }];
      return {
        legs,
        structure: `Buy 1 $${lowerWing.strike.toFixed(2)}, sell 2 $${center.strike.toFixed(2)}, buy 1 $${upperWing.strike.toFixed(2)}.`,
        stats: [["Net debit", `$${debit.toFixed(2)}/sh`], ["Max profit", `$${(width - debit).toFixed(2)}/sh at $${center.strike.toFixed(2)}`], ["Max loss", `$${debit.toFixed(2)}/sh`], ["Profit zone", `$${(lowerWing.strike + debit).toFixed(2)} to $${(upperWing.strike - debit).toFixed(2)}`]],
        note: "Best when stock pins your center strike at expiration. Cheap defined-risk neutral play."
      };
    }
  },
  // 19. Iron Butterfly — short body + long wings, credit
  {
    key: "iron_butterfly",
    n: "19",
    name: "Short Iron Butterfly",
    tag: "neutral · credit · defined risk",
    direction: "neutral",
    tone: "accent",
    definedRisk: true,
    build: ctx => {
      const {
        calls,
        puts,
        currentPrice,
        FRONT_DTE
      } = ctx;
      const atmK = nearest(calls, currentPrice).strike;
      const atmCall = nearest(calls, atmK);
      const atmPut = nearest(puts, atmK);
      const wingW = currentPrice * 0.04;
      const callWing = nearest(calls.filter(c => c.strike >= atmK + wingW), atmK + wingW) || nearest(calls, atmK + wingW);
      const putWing = nearest(puts.filter(p => p.strike <= atmK - wingW), atmK - wingW) || nearest(puts, atmK - wingW);
      const acMid = midOf(atmCall),
        apMid = midOf(atmPut);
      const cwMid = midOf(callWing),
        pwMid = midOf(putWing);
      const credit = acMid + apMid - (cwMid + pwMid);
      const width = callWing.strike - atmK;
      const legs = [{
        type: "put",
        strike: putWing.strike,
        qty: 100,
        premium: pwMid,
        dte: FRONT_DTE,
        iv: putWing.iv
      }, {
        type: "put",
        strike: atmK,
        qty: -100,
        premium: apMid,
        dte: FRONT_DTE,
        iv: atmPut.iv
      }, {
        type: "call",
        strike: atmK,
        qty: -100,
        premium: acMid,
        dte: FRONT_DTE,
        iv: atmCall.iv
      }, {
        type: "call",
        strike: callWing.strike,
        qty: 100,
        premium: cwMid,
        dte: FRONT_DTE,
        iv: callWing.iv
      }];
      return {
        legs,
        structure: `Sell ATM straddle at $${atmK.toFixed(2)}, buy $${putWing.strike.toFixed(2)} put + $${callWing.strike.toFixed(2)} call wings.`,
        stats: [["Net credit", `$${credit.toFixed(2)}/sh`], ["Max profit", `$${credit.toFixed(2)}/sh at $${atmK.toFixed(2)}`], ["Max loss", `$${(width - credit).toFixed(2)}/sh`], ["Profit zone", `$${(atmK - credit).toFixed(2)} to $${(atmK + credit).toFixed(2)}`]],
        note: "Higher credit than iron condor but narrower profit zone."
      };
    }
  },
  // 20. Long Risk Reversal — bullish synthetic
  {
    key: "long_risk_reversal",
    n: "20",
    name: "Long Risk Reversal",
    tag: "bullish · low cost · synthetic",
    direction: "bullish",
    tone: "up",
    definedRisk: false,
    build: ctx => {
      const {
        sugCall,
        sugPut,
        callAtSug,
        putAtSug,
        callMid,
        putMid,
        FRONT_DTE
      } = ctx;
      const netDebit = callMid - putMid;
      const legs = [{
        type: "put",
        strike: sugPut,
        qty: -100,
        premium: putMid,
        dte: FRONT_DTE,
        iv: putAtSug.iv
      }, {
        type: "call",
        strike: sugCall,
        qty: 100,
        premium: callMid,
        dte: FRONT_DTE,
        iv: callAtSug.iv
      }];
      return {
        legs,
        structure: `Sell $${sugPut.toFixed(2)} put + buy $${sugCall.toFixed(2)} call.`,
        stats: [["Net cost", `${netDebit >= 0 ? "$" : "-$"}${Math.abs(netDebit).toFixed(2)}/sh`], ["Upside break-even", `$${(sugCall + Math.max(0, netDebit)).toFixed(2)}`], ["Downside risk", `Assignment at $${sugPut.toFixed(2)}`], ["Max profit", "Unlimited"]],
        note: "Synthetic long stock with skew advantage. If put IV > call IV, you collect a credit."
      };
    }
  },
  // 21. Short Risk Reversal — bearish synthetic
  {
    key: "short_risk_reversal",
    n: "21",
    name: "Short Risk Reversal",
    tag: "bearish · synthetic short",
    direction: "bearish",
    tone: "down",
    definedRisk: false,
    build: ctx => {
      const {
        sugCall,
        sugPut,
        callAtSug,
        putAtSug,
        callMid,
        putMid,
        FRONT_DTE
      } = ctx;
      const netCredit = callMid - putMid;
      const legs = [{
        type: "call",
        strike: sugCall,
        qty: -100,
        premium: callMid,
        dte: FRONT_DTE,
        iv: callAtSug.iv
      }, {
        type: "put",
        strike: sugPut,
        qty: 100,
        premium: putMid,
        dte: FRONT_DTE,
        iv: putAtSug.iv
      }];
      return {
        legs,
        structure: `Sell $${sugCall.toFixed(2)} call + buy $${sugPut.toFixed(2)} put.`,
        stats: [["Net credit", `${netCredit >= 0 ? "$" : "-$"}${Math.abs(netCredit).toFixed(2)}/sh`], ["Downside break-even", `$${(sugPut - Math.max(0, netCredit)).toFixed(2)}`], ["Upside risk", `Naked short call above $${sugCall.toFixed(2)} — undefined`], ["Max profit", `$${sugPut.toFixed(2)}/sh on stock to zero`]],
        note: "Synthetic short. Naked call upside risk — only with strong directional conviction or shares to deliver."
      };
    }
  },
  // 22. Call Ratio Backspread — bullish with vol expansion
  {
    key: "call_ratio_backspread",
    n: "22",
    name: "Call Ratio Backspread",
    tag: "bullish · vol expansion",
    direction: "bullish",
    tone: "up",
    definedRisk: true,
    build: ctx => {
      const {
        calls,
        currentPrice,
        FRONT_DTE
      } = ctx;
      const shortLeg = nearest(calls, currentPrice);
      const longLeg = nearest(calls.filter(c => c.strike >= currentPrice * 1.05), currentPrice * 1.05) || nearest(calls, currentPrice * 1.05);
      const sMid = midOf(shortLeg),
        lMid = midOf(longLeg);
      const net = sMid - 2 * lMid;
      const legs = [{
        type: "call",
        strike: shortLeg.strike,
        qty: -100,
        premium: sMid,
        dte: FRONT_DTE,
        iv: shortLeg.iv
      }, {
        type: "call",
        strike: longLeg.strike,
        qty: 200,
        premium: lMid,
        dte: FRONT_DTE,
        iv: longLeg.iv
      }];
      return {
        legs,
        structure: `Sell 1 $${shortLeg.strike.toFixed(2)} call + buy 2 $${longLeg.strike.toFixed(2)} calls.`,
        stats: [["Net credit/(debit)", `${net >= 0 ? "+$" : "-$"}${Math.abs(net).toFixed(2)}/sh`], ["Max loss zone", `Around $${longLeg.strike.toFixed(2)} at expiration`], ["Max profit", "Unlimited above upper break-even"]],
        note: "Profits big on a sharp upside move. Loses if stock pins around the long strike."
      };
    }
  },
  // 23. Put Ratio Backspread — bearish with vol expansion
  {
    key: "put_ratio_backspread",
    n: "23",
    name: "Put Ratio Backspread",
    tag: "bearish · vol expansion",
    direction: "bearish",
    tone: "down",
    definedRisk: true,
    build: ctx => {
      const {
        puts,
        currentPrice,
        FRONT_DTE
      } = ctx;
      const shortLeg = nearest(puts, currentPrice);
      const longLeg = nearest(puts.filter(p => p.strike <= currentPrice * 0.95), currentPrice * 0.95) || nearest(puts, currentPrice * 0.95);
      const sMid = midOf(shortLeg),
        lMid = midOf(longLeg);
      const net = sMid - 2 * lMid;
      const legs = [{
        type: "put",
        strike: shortLeg.strike,
        qty: -100,
        premium: sMid,
        dte: FRONT_DTE,
        iv: shortLeg.iv
      }, {
        type: "put",
        strike: longLeg.strike,
        qty: 200,
        premium: lMid,
        dte: FRONT_DTE,
        iv: longLeg.iv
      }];
      return {
        legs,
        structure: `Sell 1 $${shortLeg.strike.toFixed(2)} put + buy 2 $${longLeg.strike.toFixed(2)} puts.`,
        stats: [["Net credit/(debit)", `${net >= 0 ? "+$" : "-$"}${Math.abs(net).toFixed(2)}/sh`], ["Max loss zone", `Around $${longLeg.strike.toFixed(2)} at expiration`], ["Max profit", `$${longLeg.strike.toFixed(2)}/sh on stock to zero`]],
        note: "Profits big on a sharp downside move."
      };
    }
  },
  // 24. Long Synthetic Stock
  {
    key: "long_synthetic",
    n: "24",
    name: "Long Synthetic Stock",
    tag: "bullish · 100-share equivalent",
    direction: "bullish",
    tone: "up",
    definedRisk: false,
    build: ctx => {
      const {
        calls,
        puts,
        currentPrice,
        FRONT_DTE
      } = ctx;
      const atmK = nearest(calls, currentPrice).strike;
      const atmCall = nearest(calls, atmK);
      const atmPut = nearest(puts, atmK);
      const cMid = midOf(atmCall),
        pMid = midOf(atmPut);
      const net = cMid - pMid;
      const legs = [{
        type: "call",
        strike: atmK,
        qty: 100,
        premium: cMid,
        dte: FRONT_DTE,
        iv: atmCall.iv
      }, {
        type: "put",
        strike: atmK,
        qty: -100,
        premium: pMid,
        dte: FRONT_DTE,
        iv: atmPut.iv
      }];
      return {
        legs,
        structure: `Buy ATM $${atmK.toFixed(2)} call + sell ATM $${atmK.toFixed(2)} put.`,
        stats: [["Net debit/(credit)", `${net >= 0 ? "$" : "-$"}${Math.abs(net).toFixed(2)}/sh`], ["Equivalent stock cost", `$${(atmK + net).toFixed(2)}`], ["Max profit", "Unlimited"], ["Max loss", `$${(atmK + net).toFixed(2)}/sh on stock to zero`]],
        note: "Behaves like 100 long shares. Capital-efficient way to control 100 shares."
      };
    }
  },
  // 25. Short Synthetic Stock
  {
    key: "short_synthetic",
    n: "25",
    name: "Short Synthetic Stock",
    tag: "bearish · 100-share equivalent",
    direction: "bearish",
    tone: "down",
    definedRisk: false,
    build: ctx => {
      const {
        calls,
        puts,
        currentPrice,
        FRONT_DTE
      } = ctx;
      const atmK = nearest(calls, currentPrice).strike;
      const atmCall = nearest(calls, atmK);
      const atmPut = nearest(puts, atmK);
      const cMid = midOf(atmCall),
        pMid = midOf(atmPut);
      const net = cMid - pMid;
      const legs = [{
        type: "call",
        strike: atmK,
        qty: -100,
        premium: cMid,
        dte: FRONT_DTE,
        iv: atmCall.iv
      }, {
        type: "put",
        strike: atmK,
        qty: 100,
        premium: pMid,
        dte: FRONT_DTE,
        iv: atmPut.iv
      }];
      return {
        legs,
        structure: `Sell ATM $${atmK.toFixed(2)} call + buy ATM $${atmK.toFixed(2)} put.`,
        stats: [["Net credit/(debit)", `${net >= 0 ? "$" : "-$"}${Math.abs(net).toFixed(2)}/sh`], ["Equivalent short price", `$${(atmK + net).toFixed(2)}`], ["Max profit", `$${(atmK + net).toFixed(2)}/sh on stock to zero`], ["Max loss", "Unlimited above synthetic short price"]],
        note: "Like 100 short shares. Avoids HTB borrow fees. Naked upside risk."
      };
    }
  },
  // 26. The Wheel
  {
    key: "wheel",
    n: "26",
    name: "The Wheel",
    tag: "income system · 2 phases",
    direction: "neutral-bullish",
    tone: "up",
    definedRisk: false,
    build: ctx => {
      const {
        sugCall,
        sugPut,
        callAtSug,
        putAtSug,
        callMid,
        putMid,
        currentPrice,
        FRONT_DTE
      } = ctx;
      // Phase 1: cash-secured put. P/L curve shown is the put leg only.
      const legs = [{
        type: "put",
        strike: sugPut,
        qty: -100,
        premium: putMid,
        dte: FRONT_DTE,
        iv: putAtSug.iv
      }];
      const annualizedReturn = putMid / sugPut * (52 / Math.max(FRONT_DTE / 7, 1)) * 100;
      return {
        legs,
        structure: `Phase 1: sell $${sugPut.toFixed(2)} put. If assigned → phase 2: sell $${sugCall.toFixed(2)} calls on shares.`,
        stats: [["Phase 1 premium", `$${putMid.toFixed(2)}/sh`], ["Phase 2 premium (next cycle)", `$${callMid.toFixed(2)}/sh`], ["Cost basis if assigned", `$${(sugPut - putMid).toFixed(2)}`], ["Annualized yield (put leg)", `${annualizedReturn.toFixed(1)}%`]],
        note: "Repeating premium-generation system. P/L chart shows the active put leg. After assignment, re-run with covered call legs."
      };
    }
  }];

  // ─────────────────────────────────────────────────────────────────────────
  // Reference docs — a "cheat sheet" entry per strategy. Used by the
  // Strategy Reference card to render a quick-read explanation. Kept
  // factual and brief, not pitchy.
  // ─────────────────────────────────────────────────────────────────────────
  const STRATEGY_DOCS = {
    covered_call: {
      family: "Income · Stock + short option",
      summary: "Own 100 shares, sell an OTM call against them. Collect premium in exchange for capping upside at the strike.",
      market_view: "Neutral to mildly bullish. You expect the stock to drift sideways or up but not blow past the call strike before expiration.",
      max_profit: "Premium collected + (strike − cost basis) if assigned. Capped.",
      max_loss: "(Cost basis − 0) − premium collected. Same downside as long stock minus the premium cushion.",
      breakeven: "Cost basis − premium received.",
      ideal_iv: "Higher IV pays more premium. IV/HV ratio above 1.0 is preferred.",
      time_decay: "Works in your favor. Theta is positive on the short call.",
      assignment: "If stock closes above the strike at expiration, shares get called away at the strike. Roll up-and-out before expiry to avoid.",
      when_to_use: "Generating income on a long-term core holding you don't mind selling slightly higher. Avoid before earnings unless you specifically want to sell into the IV crush.",
      risks: "Misses upside if stock rips. Still exposed to large downside moves in the underlying."
    },
    cash_secured_put: {
      family: "Income · Cash + short option",
      summary: "Sell an OTM put on a stock you'd be willing to own at the strike, with cash set aside to buy 100 shares if assigned.",
      market_view: "Neutral to bullish. You're paid premium to wait for the stock to come to your buy price.",
      max_profit: "Premium received. Capped.",
      max_loss: "(Strike − 0) − premium. Same as if you'd bought the stock at strike, minus the premium.",
      breakeven: "Strike − premium received.",
      ideal_iv: "Higher IV pays more. IV/HV above 1.0 preferred.",
      time_decay: "Works in your favor.",
      assignment: "If stock closes below the strike at expiration, you buy 100 shares per contract at the strike.",
      when_to_use: "Wheel-strategy entry, or accumulating shares of a name you want to own at a discount. The premium reduces your effective cost basis if assigned.",
      risks: "Stock crashes well below the strike. You're then long shares with a paper loss equal to (strike − current) − premium."
    },
    short_strangle: {
      family: "Income · Two short options",
      summary: "Sell an OTM call AND an OTM put. Profit if the stock stays between the two strikes. Naked on both sides.",
      market_view: "Neutral. You expect range-bound action with no big move.",
      max_profit: "Total premium collected. Capped.",
      max_loss: "Theoretically unlimited on the call side, very large on the put side. Naked short options.",
      breakeven: "Two break-evens: call strike + total credit, and put strike − total credit.",
      ideal_iv: "High IV. Selling into IV crush is the trade. IV/HV well above 1.0.",
      time_decay: "Strongly in your favor on both legs.",
      assignment: "Either side can get assigned if it goes ITM. Manage at 50% profit or 21 DTE typically.",
      when_to_use: "Range-bound stocks, post-earnings IV crush plays, broad indices in low-vol regimes.",
      risks: "Undefined risk. A black-swan move can wipe out months of premium. Position size carefully and consider it a defined-risk iron condor instead."
    },
    iron_condor: {
      family: "Income · 4-leg defined risk",
      summary: "Short strangle wrapped with long wings further OTM. Caps the loss at the wing distance minus credit collected.",
      market_view: "Neutral. Range-bound expectation, defined-risk version of a strangle.",
      max_profit: "Net credit received.",
      max_loss: "Wing width − credit received.",
      breakeven: "Two break-evens at short strikes ± credit.",
      ideal_iv: "Higher IV is better but the long wings reduce the gross credit. IV/HV above 1.0 preferred.",
      time_decay: "Net positive, especially as expiration approaches and the short strikes stay OTM.",
      assignment: "Same as short strangle but the long wings cap any catastrophic loss.",
      when_to_use: "Higher-IV underlyings where you want premium income without naked risk. Earnings plays AFTER the announcement to capture IV crush.",
      risks: "Stock breaks through one of the short strikes and runs to the wing. Max loss is realized at expiration if pinned beyond the wing."
    },
    short_straddle: {
      family: "Income · Two short options at same strike",
      summary: "Sell ATM call AND ATM put at the same strike. Maximum premium collection. Naked on both sides.",
      market_view: "Strongly neutral. You expect very little movement and high realized volatility versus current implied volatility.",
      max_profit: "Total premium. Realized only if stock pins exactly at strike.",
      max_loss: "Unlimited on call side, large on put side.",
      breakeven: "Strike ± total premium.",
      ideal_iv: "Very high IV. This is an aggressive IV-crush play.",
      time_decay: "Theta is at maximum near ATM. Strong positive decay.",
      assignment: "One side will be ITM at expiration. Roll or close before then.",
      when_to_use: "Pinning catalysts, post-earnings, expiry-week mean-reversion. Pros only — sizing matters more than entry.",
      risks: "Sharp move in either direction. Naked short gamma. One bad day can erase a year of premium."
    },
    bull_put_spread: {
      family: "Income · Defined-risk credit",
      summary: "Sell a put + buy a further-OTM put. Net credit. Profit if stock stays above the short put strike.",
      market_view: "Neutral to bullish. Less directional than a long call but defined-risk.",
      max_profit: "Net credit collected.",
      max_loss: "Spread width − credit.",
      breakeven: "Short put strike − credit.",
      ideal_iv: "Higher IV pays more upfront credit.",
      time_decay: "Positive. Both legs lose value but the short put loses faster.",
      assignment: "Short put can be assigned if stock falls below it. The long put protects against catastrophic loss.",
      when_to_use: "Bullish bias with limited capital. Better risk/reward than naked short put when IV is moderate.",
      risks: "Stock breaks below short strike near expiration. Realizes max loss if pinned below the long put."
    },
    bear_call_spread: {
      family: "Income · Defined-risk credit",
      summary: "Sell a call + buy a further-OTM call. Net credit. Profit if stock stays below the short call strike.",
      market_view: "Neutral to bearish.",
      max_profit: "Net credit.",
      max_loss: "Spread width − credit.",
      breakeven: "Short call strike + credit.",
      ideal_iv: "Higher IV preferred.",
      time_decay: "Positive.",
      assignment: "Short call can be assigned if stock rises above it. Long call caps the upside loss.",
      when_to_use: "Bearish on a stock that's overextended or facing resistance, but you don't want unlimited risk of a naked short call.",
      risks: "Sharp upside move past the short strike. Max loss if pinned above the long call."
    },
    calendar_spread: {
      family: "Volatility · Time-spread debit",
      summary: "Sell a near-month option + buy a same-strike further-out option. Profits from time decay differential and IV expansion.",
      market_view: "Neutral. Stock pinning at the strike around front-month expiration is ideal.",
      max_profit: "Hard to compute exactly — depends on back-month IV at front-month expiration. Roughly the back-month value at front expiry minus initial debit.",
      max_loss: "Net debit paid. Capped.",
      breakeven: "Two break-evens, computed via Black-Scholes at front expiration.",
      ideal_iv: "LOW front IV (front month cheap to sell back, expensive enough to want to buy). Calendars want IV to expand, not crush.",
      time_decay: "Front leg decays faster than back leg = profit, IF stock stays near strike.",
      assignment: "If front leg goes ITM, you can be assigned. The long back-month leg replaces the position post-assignment.",
      when_to_use: "Pre-earnings (front month decays before announcement, back month holds IV) or low-vol regimes where you expect IV to expand.",
      risks: "Stock moves sharply away from strike. Front-leg credit erodes the back-leg value isn't enough to compensate."
    },
    diagonal_spread: {
      family: "Volatility · Time + strike spread debit",
      summary: "Like a calendar but with different strikes — short a near-month OTM option + long a back-month closer-to-the-money option.",
      market_view: "Mildly directional. Pick strike orientation based on bias.",
      max_profit: "Path-dependent. Maximum if stock pins at the short strike on front expiration.",
      max_loss: "Net debit if back-month leg goes worthless.",
      breakeven: "Computed via Black-Scholes; varies with IV path.",
      ideal_iv: "Low-to-moderate front IV, with expectation of IV expansion or directional move.",
      time_decay: "Net positive if stock stays in zone.",
      assignment: "Front leg can be assigned if it goes ITM. Back leg replaces.",
      when_to_use: "Income-generating directional plays. Often used as a 'poor man's covered call' — long deep ITM LEAPS as the stock substitute.",
      risks: "Sharp move in wrong direction. Back-leg vega exposure."
    },
    jade_lizard: {
      family: "Income · 3-leg credit, no upside risk",
      summary: "Short put + short call spread (short call + long higher call). Total credit > spread width = no risk to the upside.",
      market_view: "Neutral to bullish. You're willing to own shares at the put strike but expect upside to be capped.",
      max_profit: "Total credit collected.",
      max_loss: "Put strike − credit on the downside.",
      breakeven: "Put strike − credit (downside only).",
      ideal_iv: "Moderate-to-high IV. Need enough premium that the call spread credit > the call spread width.",
      time_decay: "Positive on all three legs.",
      assignment: "Put can be assigned (you take shares); call spread expires worthless if no upside.",
      when_to_use: "Stocks where put skew is rich (which is most of them). Better R:R than a naked short put when the call side adds extra credit.",
      risks: "Sharp downside move below put strike. No upside risk by design."
    },
    ratio_spread: {
      family: "Income · 1×2 directional credit",
      summary: "Sell more options than you buy, usually 2 short for 1 long, all on one side. Net credit. Naked exposure on the upside.",
      market_view: "Mildly bullish if call ratio, mildly bearish if put ratio. You expect the stock to move toward the short strike but not blow past it.",
      max_profit: "(Strike difference + credit) at the short strike.",
      max_loss: "Unlimited above the short strikes (call ratio) or down to zero (put ratio).",
      breakeven: "Short strike + max profit on the upside (or − for put ratio).",
      ideal_iv: "Higher IV preferred for credit collection.",
      time_decay: "Positive overall but depends on path.",
      assignment: "Can get assigned on multiple shorts. Make sure you have margin to manage.",
      when_to_use: "Earnings plays where you expect a contained move. Skew arbitrage when calls or puts are inflated.",
      risks: "Naked short on extra contracts. Position size critical."
    },
    wheel: {
      family: "System · Multi-week income",
      summary: "Sell cash-secured puts. If assigned, sell covered calls on the assigned shares. If shares get called away, restart with puts. Repeat.",
      market_view: "Neutral to bullish on the underlying long-term. You're willing to own the shares.",
      max_profit: "Compound premium income over many cycles, plus capital appreciation if assigned and called away above cost.",
      max_loss: "Stock crash below your cost basis when assigned. Same as if you'd bought stock outright minus all the premiums collected.",
      breakeven: "Effective cost basis = original strike − sum of all premiums collected on that share lot.",
      ideal_iv: "Moderate-to-high IV in a stock you actually want to own. Index ETFs or quality blue-chips work well.",
      time_decay: "Positive every week.",
      assignment: "Expected and welcomed. The mechanic of the strategy.",
      when_to_use: "Long-term income generation on a watchlist of high-quality names. The system smooths out timing risk.",
      risks: "Long bear market on a stock you got assigned on at higher prices. The covered-call leg can lock you into a loss if you're not patient."
    },
    long_call: {
      family: "Speculation · Long single option",
      summary: "Buy an OTM or ATM call. Pay premium upfront for unlimited upside potential.",
      market_view: "Bullish.",
      max_profit: "Unlimited.",
      max_loss: "Premium paid.",
      breakeven: "Strike + premium.",
      ideal_iv: "Lower IV preferred — you want cheap options. IV/HV below 1.0 is ideal.",
      time_decay: "Works against you.",
      assignment: "Not applicable (long).",
      when_to_use: "High-conviction directional bull bet, or hedge for a short stock position. Protective use case is a 'stock replacement' for a leveraged long.",
      risks: "Time decay erodes value. Most long calls expire worthless. Sizing must reflect expected hit rate."
    },
    long_put: {
      family: "Speculation · Long single option",
      summary: "Buy a put. Pay premium upfront. Profits if stock falls.",
      market_view: "Bearish.",
      max_profit: "Strike − premium (stock to zero).",
      max_loss: "Premium paid.",
      breakeven: "Strike − premium.",
      ideal_iv: "Lower IV preferred.",
      time_decay: "Works against you.",
      assignment: "Not applicable.",
      when_to_use: "Hedging long stock positions, or speculating on a downside move. Cleaner than shorting stock — defined risk and no margin call.",
      risks: "Time decay. Most long puts also expire worthless."
    },
    long_straddle: {
      family: "Volatility · Long two options",
      summary: "Buy an ATM call AND an ATM put. Profit if stock moves more than total premium in either direction.",
      market_view: "Volatility expansion. Neutral on direction.",
      max_profit: "Unlimited (call side) or large (put side).",
      max_loss: "Total premium paid if pinned at strike.",
      breakeven: "Strike + premium and strike − premium.",
      ideal_iv: "LOW IV at entry — you want cheap options that will expand.",
      time_decay: "Works against you. Strong negative theta near expiration.",
      assignment: "Not applicable (long).",
      when_to_use: "Pre-catalyst (earnings, FDA, court ruling) where you expect a big move but don't know direction. Exit before IV crush hits.",
      risks: "Stock barely moves. Both legs lose value to time decay. IV crush after the catalyst can wipe out gains."
    },
    long_strangle: {
      family: "Volatility · Long two options",
      summary: "Buy an OTM call AND an OTM put. Cheaper than a straddle but needs a bigger move to profit.",
      market_view: "Volatility expansion, no direction.",
      max_profit: "Unlimited / large.",
      max_loss: "Total premium paid.",
      breakeven: "Call strike + premium and put strike − premium.",
      ideal_iv: "Low IV. Same logic as straddle.",
      time_decay: "Works against you.",
      assignment: "Not applicable.",
      when_to_use: "Catalyst plays where you have less budget than for a straddle. Wider profit zone in absolute price but needs more movement.",
      risks: "Same as straddle but worse — needs a bigger move to break even."
    },
    bull_call_spread: {
      family: "Speculation · Defined-risk debit",
      summary: "Buy a call + sell a higher-strike call. Net debit. Caps both upside and downside.",
      market_view: "Bullish but with budget constraint or capped expectation.",
      max_profit: "Spread width − debit.",
      max_loss: "Debit paid.",
      breakeven: "Long strike + debit.",
      ideal_iv: "Higher IV makes this cheaper as a debit spread (because you sell IV on the short leg).",
      time_decay: "Net negative, but smaller magnitude than a long call alone.",
      assignment: "Short leg can be assigned if ITM late. Long leg covers.",
      when_to_use: "Bullish bias on a stock that you don't expect to rip past a specific level. Cheap way to get directional exposure.",
      risks: "Stock fails to move up. Caps upside if you're wrong about the ceiling."
    },
    bear_put_spread: {
      family: "Speculation · Defined-risk debit",
      summary: "Buy a put + sell a lower-strike put. Net debit. Caps both directions.",
      market_view: "Bearish, with capped downside expectation.",
      max_profit: "Spread width − debit.",
      max_loss: "Debit paid.",
      breakeven: "Long strike − debit.",
      ideal_iv: "Higher IV cheapens the spread.",
      time_decay: "Net negative.",
      assignment: "Short leg can be assigned. Long leg covers.",
      when_to_use: "Cheap way to bet on a moderate decline without paying full long-put premium.",
      risks: "Stock doesn't fall. Caps gain if it crashes hard."
    },
    long_butterfly: {
      family: "Speculation · 3-leg pin play",
      summary: "Buy 1 lower call + sell 2 ATM calls + buy 1 higher call. Cheap defined-risk bet on the stock pinning the middle strike at expiration.",
      market_view: "Strongly neutral. Pin expectation.",
      max_profit: "(Wing width − debit) at the middle strike.",
      max_loss: "Debit paid.",
      breakeven: "Lower strike + debit and higher strike − debit.",
      ideal_iv: "Higher IV cheapens the structure.",
      time_decay: "Positive in the profit zone, negative outside.",
      assignment: "Short legs can be assigned but the structure self-hedges.",
      when_to_use: "Pinning expirations on indices, or stocks with strong support/resistance at the middle strike.",
      risks: "Stock breaks out of the wing range. Max loss if pinned outside the wings."
    },
    iron_butterfly: {
      family: "Income · 4-leg defined risk",
      summary: "Short ATM straddle wrapped with long wings. Highest credit of the iron family. Narrowest profit zone.",
      market_view: "Strongly neutral, expecting the stock to pin.",
      max_profit: "Net credit at the middle strike.",
      max_loss: "Wing width − credit.",
      breakeven: "Middle strike ± credit.",
      ideal_iv: "Higher IV. The straddle body needs to pay enough to be worth the narrow profit zone.",
      time_decay: "Positive at the middle, negative outside.",
      assignment: "Self-hedged by the long wings.",
      when_to_use: "Pinning catalysts. Higher max profit than iron condor but smaller probability of profit.",
      risks: "Any meaningful move from the middle strike erodes profit fast."
    },
    long_risk_reversal: {
      family: "Synthetic · 2-leg directional",
      summary: "Sell an OTM put + buy an OTM call. Often executed at zero cost (or small credit) when put skew is rich.",
      market_view: "Bullish.",
      max_profit: "Unlimited above call strike.",
      max_loss: "Put strike (assignment); behaves like long stock below the put strike.",
      breakeven: "Call strike if executed for zero cost; otherwise call + debit or call − credit.",
      ideal_iv: "Rich put skew (typical for index ETFs). Steep skew = better entry pricing.",
      time_decay: "Roughly neutral — short put theta offsets long call theta.",
      assignment: "Short put can be assigned. You'd own shares at the put strike.",
      when_to_use: "Long-bias view with stretched put skew. Common in commodities and indices.",
      risks: "Sharp downside move. You're effectively long the underlying below the put strike."
    },
    short_risk_reversal: {
      family: "Synthetic · 2-leg directional",
      summary: "Sell an OTM call + buy an OTM put. Bearish synthetic. Best when call skew is rich.",
      market_view: "Bearish.",
      max_profit: "Stock to zero minus the put cost.",
      max_loss: "Unlimited above the call strike.",
      breakeven: "Roughly call strike, depending on net debit/credit.",
      ideal_iv: "Rich call skew (rare in equities, common in some commodities).",
      time_decay: "Roughly neutral.",
      assignment: "Short call can be assigned (you'd be short shares).",
      when_to_use: "Bearish view on a name with stretched call skew or where you want to define downside via the long put.",
      risks: "Naked short call upside. Use only with very strong directional conviction."
    },
    call_ratio_backspread: {
      family: "Volatility · 1×2 directional debit",
      summary: "Sell 1 ATM call + buy 2 OTM calls. Net debit (small) or near-zero. Profits big on a sharp upside move.",
      market_view: "Strongly bullish with high-conviction upside.",
      max_profit: "Unlimited beyond the long strike.",
      max_loss: "Realized at the long-call strike (worst pinning location).",
      breakeven: "Two break-evens — depends on debit and strikes.",
      ideal_iv: "Moderate IV with expected expansion.",
      time_decay: "Negative — you own more options than you're short.",
      assignment: "Short call can be assigned mid-run. Manage carefully.",
      when_to_use: "High-conviction breakout candidates. Earnings or news plays where you expect a sharp move.",
      risks: "Stock pins around the long strike. Realizes max loss."
    },
    put_ratio_backspread: {
      family: "Volatility · 1×2 directional debit",
      summary: "Sell 1 ATM put + buy 2 OTM puts. Same idea as call backspread but on the bear side.",
      market_view: "Strongly bearish with sharp expected move.",
      max_profit: "Large (long put strike − cost).",
      max_loss: "Realized at the long-put strike.",
      breakeven: "Two — varies with debit.",
      ideal_iv: "Moderate, expected to expand.",
      time_decay: "Negative.",
      assignment: "Short put can be assigned.",
      when_to_use: "Sharp downside conviction plays. Crash hedges.",
      risks: "Stock pins around the long put strike. Slow grind down can also hurt due to time decay."
    },
    long_synthetic: {
      family: "Synthetic · 2-leg equivalence",
      summary: "Long ATM call + short ATM put = synthetic long stock. Behaves identically to owning 100 shares.",
      market_view: "Bullish.",
      max_profit: "Unlimited.",
      max_loss: "Equivalent stock cost (strike + net debit) on stock to zero.",
      breakeven: "Strike + net debit/credit.",
      ideal_iv: "Doesn't matter — IVs offset between legs.",
      time_decay: "Roughly zero — call decay offsets put decay.",
      assignment: "Short put can be assigned.",
      when_to_use: "Capital-efficient stock alternative. Avoids HTB issues, lets you scale exposure with margin instead of cash.",
      risks: "Same as long stock. Naked downside."
    },
    short_synthetic: {
      family: "Synthetic · 2-leg equivalence",
      summary: "Short ATM call + long ATM put = synthetic short stock. Equivalent to 100 short shares.",
      market_view: "Bearish.",
      max_profit: "Strike − net cost (stock to zero).",
      max_loss: "Unlimited above strike.",
      breakeven: "Strike + net credit (or − debit).",
      ideal_iv: "Doesn't matter.",
      time_decay: "Roughly zero.",
      assignment: "Short call can be assigned.",
      when_to_use: "Avoiding HTB borrow fees. Capital-efficient short.",
      risks: "Same as short stock. Unlimited upside."
    }
  };

  // ─────────────────────────────────────────────────────────────────────────
  // Public API
  // ─────────────────────────────────────────────────────────────────────────
  window.OptionStrats = {
    STRATEGIES,
    STRATEGY_DOCS,
    pnlAt,
    pnlCurve,
    breakEvens,
    netCredit,
    pnlBounds,
    midOf,
    nearest,
    bsPrice,
    normCdf,
    backMonthPremium,
    evalDTEFor
  };
})();
})();
