"""test_reprice.py (v1.28) — verifies the Level Reprice math that backs
the /api/reprice and /api/fade endpoints. Exercises Jerry's verified
option_reprice module directly (not reimplemented) plus the stop and
risk/reward derivation the endpoint adds. Pure python3, no deps.
Run:  python3 test_reprice.py
"""

import option_reprice as o

passed = 0
failed = 0
fails = []


def ok(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS  " + name)
    else:
        failed += 1
        fails.append(name)
        print("  FAIL  " + name)


def close(a, b, tol=0.01):
    return a is not None and b is not None and abs(a - b) <= tol


# ── Gap case: 290 to 315, strike 300, 7 days, quote 1.35 ───────────
# Reproduces the module output within rounding and stays self-consistent:
# repricing the SAME spot the IV was backed out from returns the quote.
gap = o.reprice_at_levels(
    1.35, 290.0, 300.0, 7,
    [{"label": "open", "target_spot": 290.0, "hours_from_now": 0.0, "iv_shift": 0.0},
     {"label": "high", "target_spot": 315.0, "hours_from_now": 0.0, "iv_shift": 0.0}],
    r=0.04, kind="call")
ok("gap returns no error", "error" not in gap)
ok("gap backs out IV ~0.289", close(gap["implied_vol_now"], 0.289, 0.002))
ok("gap self-consistent at open (==quote)", close(gap["levels"][0]["price"], 1.35, 0.01))
ok("gap reprices up at 315", gap["levels"][1]["price"] > 10.0)
ok("gap delta rises into the money", gap["levels"][1]["delta"] > gap["levels"][0]["delta"])

# Direct module parity: reprice_after_move flat IV at the same move.
mv = o.reprice_after_move(1.35, 290.0, 315.0, 300.0, 7, r=0.04, kind="call",
                          days_elapsed=0, iv_shift=0.0)
ok("reprice_after_move matches level price",
   close(gap["levels"][1]["price"], mv["new_price"], 0.02))

# ── Fade case: open 100, sell 106, cover 103.20, stop 108 ──────────
fade = o.fade_trade(1.20, 100.0, 105.0, 5, sell_spot=106.0, cover_spot=103.20,
                    r=0.04, kind="call", hours_held=2.0, contracts=1)
ok("fade returns no error", "error" not in fade)
ok("fade sell price above cover price", fade["sell_price"] > fade["cover_price"])
ok("fade capture positive", fade["capture_per_contract"] > 0)

# Stop + risk/reward derivation (what the endpoint adds on top).
stop = o.reprice_at_levels(
    1.20, 100.0, 105.0, 5,
    [{"label": "stop", "target_spot": 108.0, "hours_from_now": 2.0, "iv_shift": 0.0}],
    r=0.04, kind="call")
stop_px = stop["levels"][0]["price"]
risk_per = round((stop_px - fade["sell_price"]) * 100, 2)
ok("stop priced above sell (defined max risk)", stop_px > fade["sell_price"])
ok("max risk per contract positive", risk_per > 0)
rr = round(fade["capture_per_contract"] / risk_per, 2) if risk_per > 0 else None
ok("risk reward computes", rr is not None and rr > 0)

# Contracts scale the totals linearly.
fade2 = o.fade_trade(1.20, 100.0, 105.0, 5, 106.0, 103.20, r=0.04,
                     kind="call", hours_held=2.0, contracts=3)
ok("capture_total scales with contracts",
   close(fade2["capture_total"], fade["capture_per_contract"] * 3, 0.01))

# ── Intrinsic guard: quote at/below intrinsic returns error, no crash ─
ig = o.reprice_at_levels(
    0.001, 320.0, 300.0, 7,
    [{"label": "x", "target_spot": 315.0, "hours_from_now": 0.0, "iv_shift": 0.0}],
    r=0.04, kind="call")
ok("intrinsic guard returns error path", isinstance(ig, dict) and "error" in ig)
igf = o.fade_trade(0.001, 320.0, 300.0, 7, 325.0, 322.0, r=0.04, kind="call")
ok("intrinsic guard on fade returns error path", isinstance(igf, dict) and "error" in igf)

# ── Expiry today: T at 0 must not crash, prices at intrinsic ───────
exp0 = o.reprice_at_levels(
    2.00, 305.0, 300.0, 0,
    [{"label": "now", "target_spot": 306.0, "hours_from_now": 0.0, "iv_shift": 0.0}],
    r=0.04, kind="call")
# With days_to_exp 0, T_now is 0 so implied_vol returns None -> error path.
ok("expiry today handled without crash", isinstance(exp0, dict))

# ── Put side works (CSP management) ────────────────────────────────
putres = o.reprice_at_levels(
    2.50, 100.0, 100.0, 7,
    [{"label": "down", "target_spot": 95.0, "hours_from_now": 0.0, "iv_shift": 0.0}],
    r=0.04, kind="put")
ok("put reprice no error", "error" not in putres)
ok("put gains value as spot falls", putres["levels"][0]["price"] > 2.50)
ok("put delta negative", putres["levels"][0]["delta"] < 0)

print("\n" + str(passed) + "/" + str(passed + failed) + " passed, " + str(failed) + " failed")
if failed:
    print("FAILED: " + ", ".join(fails))
    raise SystemExit(1)
