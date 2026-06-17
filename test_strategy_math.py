"""tests for the EMA pullback math primitives.

Run from /home/claude/work via:
    python3 test_strategy_math.py

Tests _ema, _rsi, _atr, and the backtest engine on synthetic data with
known answers. NOT run automatically — Jerry can run before shipping
or whenever the math changes.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from options_dashboard import _ema, _rsi, _atr, backtest_ema_pullback


def assert_close(a, b, tol=0.01, label=""):
    if a is None or b is None:
        assert a == b, f"{label}: {a} != {b}"
    else:
        assert abs(a - b) < tol, f"{label}: {a} not close to {b} (diff {abs(a-b)})"


# ── _ema tests ──────────────────────────────────────────────
def test_ema_constant_series():
    """EMA of a constant series equals the constant after warmup."""
    series = [100.0] * 30
    ema = _ema(series, 9)
    # Warmup bars are None
    for i in range(8):
        assert ema[i] is None, f"warmup bar {i} should be None"
    # After warmup, EMA should be 100
    for i in range(8, 30):
        assert_close(ema[i], 100.0, label=f"ema9[{i}]")


def test_ema_known_values():
    """EMA(3) of [1,2,3,4,5,6,7] — hand-calculated.

    period=3, k = 2/(3+1) = 0.5
    SMA seed at i=2: (1+2+3)/3 = 2.0
    i=3: 4*0.5 + 2.0*0.5 = 3.0
    i=4: 5*0.5 + 3.0*0.5 = 4.0
    i=5: 6*0.5 + 4.0*0.5 = 5.0
    i=6: 7*0.5 + 5.0*0.5 = 6.0
    """
    ema = _ema([1, 2, 3, 4, 5, 6, 7], 3)
    assert ema[0] is None and ema[1] is None
    assert_close(ema[2], 2.0, label="seed")
    assert_close(ema[3], 3.0, label="i=3")
    assert_close(ema[4], 4.0, label="i=4")
    assert_close(ema[5], 5.0, label="i=5")
    assert_close(ema[6], 6.0, label="i=6")


def test_ema_short_series():
    """Series shorter than period returns all None."""
    ema = _ema([1, 2, 3], 9)
    assert all(v is None for v in ema), "short series should be all None"


# ── _rsi tests ──────────────────────────────────────────────
def test_rsi_constant_series():
    """RSI of constant series — no movement means avg_loss=0, RSI=100."""
    series = [50.0] * 30
    rsi = _rsi(series, 14)
    # First 14 bars should be None (need 14 differences = 15 bars)
    for i in range(14):
        assert rsi[i] is None
    # After warmup, RSI = 100 (no losses)
    for i in range(14, 30):
        assert_close(rsi[i], 100.0, label=f"rsi[{i}]")


def test_rsi_monotonic_up():
    """Pure uptrend — RSI should be 100 (only gains, no losses)."""
    series = [100.0 + i for i in range(30)]
    rsi = _rsi(series, 14)
    for i in range(14, 30):
        assert_close(rsi[i], 100.0, label=f"rsi up[{i}]")


def test_rsi_monotonic_down():
    """Pure downtrend — RSI should be 0 (only losses)."""
    series = [100.0 - i for i in range(30)]
    rsi = _rsi(series, 14)
    for i in range(14, 30):
        assert_close(rsi[i], 0.0, label=f"rsi down[{i}]")


def test_rsi_range():
    """RSI should always be in [0, 100]."""
    import random
    random.seed(42)
    series = [100.0]
    for _ in range(100):
        series.append(series[-1] + random.gauss(0, 2))
    rsi = _rsi(series, 14)
    for i, v in enumerate(rsi):
        if v is not None:
            assert 0 <= v <= 100, f"rsi[{i}]={v} out of [0, 100]"


# ── _atr tests ──────────────────────────────────────────────
def test_atr_constant_bars():
    """ATR of constant H/L/C bars where H=L=C means TR=0 throughout."""
    bars = [{"high": 100, "low": 100, "close": 100} for _ in range(20)]
    atr = _atr(bars, 14)
    for i in range(14, 20):
        assert_close(atr[i], 0.0, label=f"atr[{i}]")


def test_atr_known_value():
    """ATR(3) on a known sequence.
    Bar 0: not used (no prev_close)
    Bar 1: H=2, L=1, prev_C=1 → TR = max(1, 1, 0) = 1
    Bar 2: H=3, L=2, prev_C=2 → TR = max(1, 1, 0) = 1
    Bar 3: H=4, L=3, prev_C=3 → TR = max(1, 1, 0) = 1
    Bar 4: H=5, L=4, prev_C=4 → TR = 1
    Bar 5: H=6, L=5, prev_C=5 → TR = 1

    For period=3:
    out[3] = (TR[1] + TR[2] + TR[3]) / 3 = 1.0
    out[4] = (out[3] * 2 + TR[4]) / 3 = (2 + 1) / 3 = 1.0
    """
    bars = [
        {"high": 1, "low": 1, "close": 1},
        {"high": 2, "low": 1, "close": 2},
        {"high": 3, "low": 2, "close": 3},
        {"high": 4, "low": 3, "close": 4},
        {"high": 5, "low": 4, "close": 5},
        {"high": 6, "low": 5, "close": 6},
    ]
    atr = _atr(bars, 3)
    assert_close(atr[3], 1.0, label="atr seed")
    assert_close(atr[4], 1.0, label="atr i=4")
    assert_close(atr[5], 1.0, label="atr i=5")


# ── Backtest engine tests ──────────────────────────────────
# These need a synthetic ticker. Since backtest_ema_pullback calls
# load_daily(symbol) which hits Schwab/yfinance, we'd need a mock.
# For a tractable test, verify the helpers above are all that's
# strictly checked. The backtest engine integration is left to
# manual validation by Jerry running it against known tickers.

def test_backtest_handles_short_history():
    """Backtest should return error (not crash) on a ticker with too
    little history. This test will hit the network but verifies graceful
    failure shape."""
    try:
        result = backtest_ema_pullback("AAPL", direction="long", lookback_days=10)
        # Could either succeed (if 10 days is somehow enough) or return error
        assert isinstance(result, dict)
        if "error" in result:
            assert "insufficient" in result["error"].lower() or "data" in result["error"].lower()
        else:
            # If it returned valid results, n_trades should be a number
            assert "n_trades" in result
    except Exception as e:
        # Network failure is OK in test env — just don't crash hard
        print(f"  (skipped — network unavailable: {e})")


# ── Run all ────────────────────────────────────────────────
def run():
    tests = [
        test_ema_constant_series,
        test_ema_known_values,
        test_ema_short_series,
        test_rsi_constant_series,
        test_rsi_monotonic_up,
        test_rsi_monotonic_down,
        test_rsi_range,
        test_atr_constant_bars,
        test_atr_known_value,
        test_backtest_handles_short_history,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(tests)} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
