"""gex_engine.py — gamma exposure arithmetic for the Gamma Exposure view.

Pure functions only: no network, no disk, no clock. `options_dashboard.py`
fetches the chain through the existing Schwab client and hands it here;
`tab-gex.jsx` renders what comes back.

═══════════════════════════════════════════════════════════════════════════
THE ASSUMPTIONS, STATED BEFORE THE FIRST NUMBER
═══════════════════════════════════════════════════════════════════════════

Gamma exposure is not a measured quantity. Nobody publishes dealer
inventory, so every GEX number anywhere — this one included — is open
interest times gamma times an ASSUMPTION about who is on which side. The
assumption is the whole model, so it is written here rather than buried.

  1. SIGN CONVENTION — calls positive, puts negative.
     The standard convention assumes dealers are net LONG call gamma and
     net SHORT put gamma, because the persistent customer flows are buying
     downside protection and selling upside calls. It is the convention
     behind the commonly quoted "zero gamma" and "gamma flip" levels, and
     it is the convention this dashboard's SPY gamma-regime read has always
     used, so the two agree by construction.

     What follows from it: POSITIVE net GEX means dealers hedge AGAINST the
     move (sell strength, buy weakness) — pinning, mean reversion, suppressed
     realized vol. NEGATIVE net GEX means they hedge WITH the move — trending,
     amplified moves.

     This assumption is wrong for individual names often enough to matter. A
     stock where the public is buying calls outright inverts it. Read the sign
     as "under the standard convention", never as a measurement.

  2. CONTRACT MULTIPLIER — 100 shares per contract.
     Standard US equity options. Passed in as `contract_size` so an index or
     a mini contract can override it rather than silently being wrong.

  3. SCALING — dollars of dealer delta per 1% move in the underlying.

         GEX = gamma x open_interest x contract_size x spot^2 x 0.01

     Gamma is delta per $1 of spot, so gamma x spot^2 x 0.01 converts it to
     dollars of delta per 1% of spot. Quoting GEX "per 1%" rather than "per
     $1" is what makes a $6 stock and a $600 stock comparable, which is the
     only reason the units are worth the extra term.

  4. OPEN INTEREST, NOT VOLUME.
     Open interest is yesterday's settled position count. It is stale by up
     to a session and it is still the right input: today's volume includes
     both openings and closings and cannot be netted into a position.

═══════════════════════════════════════════════════════════════════════════
THE GAMMA FLIP
═══════════════════════════════════════════════════════════════════════════

The flip (zero-gamma) level is the spot price at which net GEX crosses zero.
The cheap way to estimate it is to cumulatively sum the per-strike GEX in
strike order and report where the running total crosses — that is not the
same quantity, because it holds every contract's gamma fixed at the value it
has at TODAY'S spot, and gamma is precisely the thing that moves as spot
moves.

This module does it properly: it re-computes each contract's Black-Scholes
gamma at every candidate spot, using that contract's own implied volatility
and time to expiry, and finds the crossing of the resulting profile. That
needs an IV and an expiry per contract, so contracts missing either are
excluded from the PROFILE (they still count in the static per-strike totals)
and the share of open interest actually covered is reported alongside the
answer. A flip level computed from 40% of the open interest is not a flip
level, and the caller is given what it needs to say so.
"""

from __future__ import annotations

import math
from datetime import date

from metrics import _bs_gamma

GEX_VERSION = "gex-engine-1.0.0"

# Dollars of dealer delta per this much of a move in the underlying.
MOVE_PCT = 1.0
CONTRACT_SIZE = 100
# Spot grid for the flip profile: +/- this fraction of spot, in this many steps.
# 20% covers the flip for essentially every liquid name without wasting work
# on strikes that would need a crash to matter; 81 steps puts the grid at a
# quarter of a percent, finer than the level is meaningful to.
PROFILE_SPAN = 0.20
PROFILE_STEPS = 81
DEFAULT_RATE = 0.045


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or math.isinf(f) else f


def _pos(v):
    f = _num(v)
    return f if (f is not None and f > 0) else None


def year_fraction(expiration: str, today: date | None) -> float | None:
    """Calendar years from `today` to `expiration` (YYYY-MM-DD).

    Calendar years, not trading years: it is what the broker's own implied
    volatility was solved against, and mixing a 252-day year into an IV
    quoted on a 365-day year misprices gamma by about 20%.

    An expiration that has already passed returns None rather than a negative
    or clamped number — an expired contract has no gamma and should drop out,
    not contribute a degenerate one.
    """
    if not expiration or today is None:
        return None
    try:
        y, m, d = str(expiration)[:10].split("-")
        exp = date(int(y), int(m), int(d))
    except Exception:  # noqa: BLE001
        return None
    days = (exp - today).days
    if days < 0:
        return None
    # Expiration day itself is priced as a few hours, not zero: a zero would
    # divide by zero in the gamma formula and drop 0-DTE — the single expiry
    # with the most gamma in it — out of the profile entirely.
    return max(days, 0.25) / 365.0


def contract_gex(gamma, open_interest, spot, kind: str,
                 contract_size: int = CONTRACT_SIZE,
                 move_pct: float = MOVE_PCT) -> float | None:
    """One contract's gamma exposure in dollars per `move_pct`% of spot.

    Returns None when gamma, open interest or spot is missing — not 0.0,
    because a missing greek and a genuinely flat strike are different facts
    and only one of them should be quietly summed into a total.
    """
    g = _num(gamma)
    oi = _num(open_interest)
    s = _pos(spot)
    if g is None or oi is None or s is None:
        return None
    if oi <= 0 or g <= 0:
        # Zero open interest carries no exposure. A negative or zero gamma on
        # a long option is a bad quote, not a short position.
        return 0.0 if oi <= 0 else None
    sign = 1.0 if str(kind).lower().startswith("c") else -1.0
    return sign * g * oi * contract_size * s * s * (move_pct / 100.0)


def by_strike(calls, puts, spot, contract_size: int = CONTRACT_SIZE,
              move_pct: float = MOVE_PCT) -> list[dict]:
    """Per-strike GEX and open interest, ascending by strike.

    Every row carries both the exposure and the raw open interest so the
    view's GEX/OI toggle is a render-time choice, not a second request.
    """
    rows: dict[float, dict] = {}

    def _add(seq, kind):
        for c in (seq or []):
            k = _num(c.get("strike"))
            if k is None or k <= 0:
                continue
            r = rows.get(k)
            if r is None:
                r = rows[k] = {"strike": k, "call_gex": 0.0, "put_gex": 0.0,
                               "call_oi": 0, "put_oi": 0,
                               "call_missing": 0, "put_missing": 0}
            oi = _num(c.get("openInterest")) or 0.0
            r[f"{kind}_oi"] += int(oi)
            g = contract_gex(c.get("gamma"), oi, spot, kind,
                             contract_size, move_pct)
            if g is None:
                r[f"{kind}_missing"] += 1
            else:
                r[f"{kind}_gex"] += g

    _add(calls, "call")
    _add(puts, "put")
    out = []
    for k in sorted(rows):
        r = rows[k]
        r["net_gex"] = r["call_gex"] + r["put_gex"]
        r["total_oi"] = r["call_oi"] + r["put_oi"]
        out.append(r)
    return out


def profile(calls, puts, spot, expiration_of, today: date | None,
            rate: float = DEFAULT_RATE,
            contract_size: int = CONTRACT_SIZE,
            move_pct: float = MOVE_PCT,
            span: float = PROFILE_SPAN,
            steps: int = PROFILE_STEPS) -> dict:
    """Net GEX as a function of hypothetical spot, with gamma re-computed at
    every point from Black-Scholes.

    `expiration_of` is a callable taking a contract dict and returning its
    expiration string — the chain is keyed by expiry above the contract, so
    the expiry has to be carried in rather than read off the row.

    Returns {points: [{spot, net_gex}], flip, flip_bracketed, covered_oi_pct,
    contracts_used, contracts_skipped}. `flip` is the LOWEST upward crossing
    (net GEX going from negative to positive as spot rises), which is the
    level the convention names: below it dealers amplify moves, above it they
    dampen them. `flip_bracketed` is False when the profile never changes
    sign inside the grid — the honest answer is then "no flip within +/-20%",
    not a number extrapolated off the end.
    """
    s0 = _pos(spot)
    if s0 is None:
        return {"points": [], "flip": None, "flip_bracketed": False,
                "covered_oi_pct": None, "contracts_used": 0,
                "contracts_skipped": 0,
                "reason": "No underlying price, so there is nothing to price gamma against."}
    prepared = []
    used_oi = 0.0
    total_oi = 0.0
    skipped = 0
    for seq, kind in ((calls, "call"), (puts, "put")):
        for c in (seq or []):
            oi = _num(c.get("openInterest")) or 0.0
            total_oi += max(oi, 0.0)
            k = _pos(c.get("strike"))
            iv = _pos(c.get("iv"))
            T = year_fraction(expiration_of(c), today)
            if k is None or iv is None or T is None or oi <= 0:
                if oi > 0:
                    skipped += 1
                continue
            prepared.append((k, iv, T, oi, 1.0 if kind == "call" else -1.0))
            used_oi += oi
    if not prepared:
        return {"points": [], "flip": None, "flip_bracketed": False,
                "covered_oi_pct": 0.0, "contracts_used": 0,
                "contracts_skipped": skipped,
                "reason": ("No contract carried both an implied volatility and a "
                           "usable expiration, so the gamma profile cannot be rebuilt.")}
    n = max(3, int(steps))
    lo, hi = s0 * (1.0 - span), s0 * (1.0 + span)
    step = (hi - lo) / (n - 1)
    points = []
    for i in range(n):
        s = lo + step * i
        net = 0.0
        for k, iv, T, oi, sign in prepared:
            g = _bs_gamma(s, k, T, iv, r=rate)
            if g:
                net += sign * g * oi * contract_size * s * s * (move_pct / 100.0)
        points.append({"spot": round(s, 4), "net_gex": net})
    flip = None
    bracketed = False
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        if a["net_gex"] < 0 <= b["net_gex"]:
            bracketed = True
            gap = b["net_gex"] - a["net_gex"]
            t = 0.0 if gap == 0 else (-a["net_gex"]) / gap
            flip = round(a["spot"] + t * (b["spot"] - a["spot"]), 2)
            break
    return {
        "points": [{"spot": p["spot"], "net_gex": round(p["net_gex"], 2)}
                   for p in points],
        "flip": flip, "flip_bracketed": bracketed,
        "covered_oi_pct": (round(used_oi * 100.0 / total_oi, 1)
                           if total_oi > 0 else None),
        "contracts_used": len(prepared), "contracts_skipped": skipped,
        "span_pct": round(span * 100.0, 1),
        "reason": ("" if bracketed else
                   f"Net gamma exposure does not change sign anywhere within "
                   f"{span * 100:.0f}% of spot, so there is no flip level to quote."),
    }


def summarize(strikes: list[dict], spot=None) -> dict:
    """Net / call / put totals and the strikes carrying the most exposure.

    `largest_positive` and `largest_negative` are the single strikes with the
    most positive and most negative NET exposure — the levels where dealer
    hedging is most concentrated. They are reported as None rather than as
    the least-bad strike when no strike is actually on that side, so an
    all-positive board does not invent a "largest negative".
    """
    call = sum(r.get("call_gex") or 0.0 for r in (strikes or []))
    put = sum(r.get("put_gex") or 0.0 for r in (strikes or []))
    net = call + put
    pos = [r for r in (strikes or []) if (r.get("net_gex") or 0.0) > 0]
    neg = [r for r in (strikes or []) if (r.get("net_gex") or 0.0) < 0]
    top_pos = max(pos, key=lambda r: r["net_gex"]) if pos else None
    top_neg = min(neg, key=lambda r: r["net_gex"]) if neg else None
    oi_rows = [r for r in (strikes or []) if (r.get("total_oi") or 0) > 0]
    top_oi = max(oi_rows, key=lambda r: r["total_oi"]) if oi_rows else None
    return {
        "net_gex": round(net, 2), "call_gex": round(call, 2),
        "put_gex": round(put, 2),
        "regime": ("long" if net > 0 else "short" if net < 0 else "flat"),
        "largest_positive": ({"strike": top_pos["strike"],
                              "net_gex": round(top_pos["net_gex"], 2)}
                             if top_pos else None),
        "largest_negative": ({"strike": top_neg["strike"],
                              "net_gex": round(top_neg["net_gex"], 2)}
                             if top_neg else None),
        "largest_oi": ({"strike": top_oi["strike"],
                        "open_interest": top_oi["total_oi"]}
                       if top_oi else None),
        "total_call_oi": sum(r.get("call_oi") or 0 for r in (strikes or [])),
        "total_put_oi": sum(r.get("put_oi") or 0 for r in (strikes or [])),
        "strikes": len(strikes or []),
        "spot": _num(spot),
    }


def build(chain: dict, expirations: list[str] | None, today: date | None,
          spot=None, rate: float = DEFAULT_RATE,
          contract_size: int = CONTRACT_SIZE,
          strike_window_pct: float | None = 25.0) -> dict:
    """The whole view for one underlying and one or more expirations.

    `chain` is the dashboard's normalized shape — {underlying, expirations,
    chains: {expiry: {calls, puts}}} — exactly what
    `SchwabClient.get_option_chain()` returns, so nothing has to reshape it
    on the way in.

    `strike_window_pct` trims the strike ladder to +/- that percentage of
    spot. Far strikes carry almost no gamma and their bars are invisible, but
    they still stretch the axis until the strikes that matter are a pixel
    wide. The trim is reported (`strikes_outside_window`) so nothing is
    silently dropped.
    """
    chains = (chain or {}).get("chains") or {}
    und = (chain or {}).get("underlying") or {}
    s = _pos(spot) or _pos(und.get("last"))
    exps = [e for e in (expirations or []) if e in chains]
    if not exps:
        exps = sorted(chains.keys())[:1]
    # Shallow-copy each contract with its expiry attached. The chain is keyed
    # by expiry ABOVE the contract, so once the legs are flattened the expiry
    # is gone — and the gamma profile needs a time to expiry per contract.
    calls, puts = [], []
    for e in exps:
        leg = chains.get(e) or {}
        for c in (leg.get("calls") or []):
            calls.append(dict(c, _expiration=e))
        for p in (leg.get("puts") or []):
            puts.append(dict(p, _expiration=e))
    rows = by_strike(calls, puts, s, contract_size)
    outside = 0
    if s and strike_window_pct:
        lo = s * (1.0 - strike_window_pct / 100.0)
        hi = s * (1.0 + strike_window_pct / 100.0)
        kept = [r for r in rows if lo <= r["strike"] <= hi]
        outside = len(rows) - len(kept)
        # Never trim the ladder to nothing: a name whose listed strikes all sit
        # outside the window (a stock that has moved a long way since the
        # strikes were listed) keeps its full ladder.
        if kept:
            rows = kept
        else:
            outside = 0
    prof = profile(calls, puts, s, lambda c: c.get("_expiration"), today,
                   rate=rate, contract_size=contract_size)
    summary = summarize(rows, s)
    return {
        "ok": bool(rows),
        "symbol": und.get("symbol") or (chain or {}).get("symbol"),
        "spot": s,
        "expirations_used": exps,
        "strikes": rows,
        "strikes_outside_window": outside,
        "strike_window_pct": strike_window_pct,
        "summary": summary,
        "profile": prof,
        "contract_size": contract_size,
        "move_pct": MOVE_PCT,
        "convention": (
            "Gamma exposure = gamma x open interest x "
            f"{contract_size} x spot squared x 0.01, so every figure is dollars of "
            "dealer delta per 1% move in the underlying. Calls count positive and "
            "puts negative, the standard convention that assumes dealers are net "
            "long call gamma and net short put gamma. Dealer positioning is not "
            "published anywhere, so this is a modelling assumption, not a "
            "measurement."),
        "version": GEX_VERSION,
    }
