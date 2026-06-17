"""
Option reprice estimator for gap moves.
Back out implied volatility from the current quote, then reprice with
Black Scholes at the new spot and one fewer day. This captures delta,
gamma, and theta exactly within the model. The only free input is the
IV assumption at the open, which you can scenario.
No external dependencies.
"""

import math


def normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(S, K, T, r, sigma, kind="call"):
    """Black Scholes price. T in years, r and sigma annualized."""
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if kind == "call":
        return S * normal_cdf(d1) - K * math.exp(-r * T) * normal_cdf(d2)
    return K * math.exp(-r * T) * normal_cdf(-d2) - S * normal_cdf(-d1)


def implied_vol(price, S, K, T, r, kind="call"):
    """Solve for the IV that reproduces the market price via bisection.
    Returns None if price is at or below intrinsic (no IV solution)."""
    intrinsic = max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)
    if price <= intrinsic + 1e-8:
        return None
    lo, hi = 1e-4, 5.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, r, mid, kind) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def greeks(S, K, T, r, sigma, kind="call"):
    """delta, gamma, theta per day, vega per 1 vol point."""
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    pdf = normal_pdf(d1)
    gamma = pdf / (S * sigma * math.sqrt(T))
    vega = S * pdf * math.sqrt(T) / 100.0
    if kind == "call":
        delta = normal_cdf(d1)
        theta = (-(S * pdf * sigma) / (2 * math.sqrt(T))
                 - r * K * math.exp(-r * T) * normal_cdf(d2)) / 365.0
    else:
        delta = normal_cdf(d1) - 1.0
        theta = (-(S * pdf * sigma) / (2 * math.sqrt(T))
                 + r * K * math.exp(-r * T) * normal_cdf(-d2)) / 365.0
    return delta, gamma, theta, vega


def reprice_after_move(current_price, S_old, S_new, K, days_to_exp,
                       r=0.04, kind="call", days_elapsed=1, iv_shift=0.0):
    """Estimate the option price after the underlying moves.
    iv_shift is absolute vol points, e.g. -0.05 for a 5 point IV crush.
    Returns implied IV now, IV used, and the new price."""
    T_old = days_to_exp / 365.0
    iv = implied_vol(current_price, S_old, K, T_old, r, kind)
    if iv is None:
        return {"error": "current price at or below intrinsic, no IV solution"}
    iv_new = max(iv + iv_shift, 1e-4)
    T_new = max(days_to_exp - days_elapsed, 0) / 365.0
    new_price = bs_price(S_new, K, T_new, r, iv_new, kind)
    return {
        "implied_vol_now": round(iv, 4),
        "implied_vol_used": round(iv_new, 4),
        "new_price": round(new_price, 2),
    }


if __name__ == "__main__":
    # Your example. Strike and expiry assumed since they were not given.
    base = reprice_after_move(
        current_price=1.35, S_old=290.0, S_new=315.0, K=300.0,
        days_to_exp=7, r=0.04, kind="call", days_elapsed=1, iv_shift=0.0,
    )
    print("backed out IV now:", base["implied_vol_now"])
    d, g, t, v = greeks(290.0, 300.0, 7 / 365.0, 0.04, base["implied_vol_now"])
    print(f"starting delta {d:.3f}  gamma {g:.4f}  theta/day {t:.3f}  vega {v:.3f}")
    print("repriced flat IV:", base["new_price"])
    print("your delta only method:", round(1.35 + 0.21 * 25, 2))
    print("--- IV scenario sweep at the open ---")
    for shift in (-0.10, -0.05, 0.0, 0.05):
        out = reprice_after_move(1.35, 290.0, 315.0, 300.0, 7, iv_shift=shift)
        print(f"IV shift {shift:+.2f}  ->  {out['new_price']}")


# ----------------------------------------------------------------------
# Intraday level based repricing.
# Reprice the same option at any list of target stock levels so you can
# set a sell to open price at one level and a buy to close price at another.
# Same backed out IV from the live quote drives every level.
# hours_from_now lets you decay time within the day if entry and exit are
# hours apart. iv_shift is absolute vol points at that level.
# ----------------------------------------------------------------------


def reprice_at_levels(current_price, S_now, K, days_to_exp, levels,
                      r=0.04, kind="call"):
    """levels is a list of dicts: {label, target_spot, hours_from_now, iv_shift}.
    Returns the backed out IV and a row per level with price and delta."""
    T_now = days_to_exp / 365.0
    iv = implied_vol(current_price, S_now, K, T_now, r, kind)
    if iv is None:
        return {"error": "current price at or below intrinsic, no IV solution"}
    rows = []
    for lv in levels:
        hrs = lv.get("hours_from_now", 0.0)
        iv_lv = max(iv + lv.get("iv_shift", 0.0), 1e-4)
        T_lv = max(days_to_exp - hrs / 24.0, 0.0) / 365.0
        S = lv["target_spot"]
        px = bs_price(S, K, T_lv, r, iv_lv, kind)
        d = greeks(S, K, max(T_lv, 1e-9), r, iv_lv, kind)[0] if T_lv > 0 else (
            1.0 if (kind == "call" and S > K) else 0.0)
        rows.append({
            "label": lv["label"],
            "target_spot": round(S, 2),
            "price": round(px, 2),
            "delta": round(d, 3),
        })
    return {"implied_vol_now": round(iv, 4), "levels": rows}


def fade_trade(current_price, S_now, K, days_to_exp, sell_spot, cover_spot,
               r=0.04, kind="call", hours_held=0.0, iv_at_sell=0.0,
               iv_at_cover=0.0, contracts=1):
    """Short the option at sell_spot, buy it back at cover_spot.
    Returns sell price, cover price, and net capture per contract and total.
    Capture is positive when the option is cheaper at cover than at sell."""
    out = reprice_at_levels(
        current_price, S_now, K, days_to_exp,
        levels=[
            {"label": "sell at high", "target_spot": sell_spot,
             "hours_from_now": 0.0, "iv_shift": iv_at_sell},
            {"label": "cover at settle", "target_spot": cover_spot,
             "hours_from_now": hours_held, "iv_shift": iv_at_cover},
        ],
        r=r, kind=kind,
    )
    if "error" in out:
        return out
    sell_px = out["levels"][0]["price"]
    cover_px = out["levels"][1]["price"]
    capture = sell_px - cover_px
    return {
        "implied_vol_now": out["implied_vol_now"],
        "sell_price": sell_px,
        "cover_price": cover_px,
        "capture_per_contract": round(capture * 100, 2),
        "capture_total": round(capture * 100 * contracts, 2),
    }


def demo_intraday():
    # ASSUMPTIONS, not your real trade. Stock opens 100.
    # Pattern: runs to high +6% = 106, settles +3.2% = 103.20.
    # Live call quote at the open, 100 spot, used to back out IV.
    S_open, hi, settle = 100.0, 106.0, 103.20
    dte = 5
    print("=== fade the high, compare strikes ===")
    print("sell call when stock hits 106, cover when it settles 103.20")
    print("strike | quote@100 |  sell@106 | cover@103.2 | capture/contract")
    for K, quote in ((105.0, 1.20), (107.0, 0.70), (110.0, 0.30)):
        f = fade_trade(quote, S_open, K, dte, sell_spot=hi, cover_spot=settle,
                       hours_held=2.0, kind="call")
        print(f"  {K:.0f}  |   {quote:.2f}    |   {f['sell_price']:.2f}   "
              f"|    {f['cover_price']:.2f}    |   ${f['capture_per_contract']:.2f}")
    print()
    print("=== full level sweep on the 107 call ===")
    out = reprice_at_levels(
        0.70, S_open, 107.0, dte,
        levels=[
            {"label": "open 100",      "target_spot": 100.0},
            {"label": "high 106 sell", "target_spot": 106.0, "hours_from_now": 0.5},
            {"label": "settle 103.2",  "target_spot": 103.2, "hours_from_now": 3.0},
        ],
        kind="call",
    )
    print("backed out IV:", out["implied_vol_now"])
    for row in out["levels"]:
        print(f"  {row['label']:<14} spot {row['target_spot']:.2f}  "
              f"price {row['price']:.2f}  delta {row['delta']:.3f}")


if __name__ == "__main__":
    demo_intraday()
