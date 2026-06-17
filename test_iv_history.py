#!/usr/bin/env python3
"""test_iv_history.py — verifies the IV rank/percentile helpers added
in v1.14. Runs in isolation against a temp dir so it cannot pollute
the real ~/.jerry-dashboard/iv_history/. Run from the active dir:

    python3 test_iv_history.py
"""

import json
import os
import shutil
import sys
import tempfile
import importlib.util


def _load_module(temp_dir):
    """Load options_dashboard.py with JERRY_DATA_DIR pointed at a temp."""
    os.environ["JERRY_DATA_DIR"] = temp_dir
    spec = importlib.util.spec_from_file_location("od", "options_dashboard.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    passed = 0
    failed = 0
    fails = []

    def assert_(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            failed += 1
            fails.append((name, detail))
            print(f"  FAIL  {name}{(' · ' + detail) if detail else ''}")

    with tempfile.TemporaryDirectory(prefix="jerry-iv-test-") as tmp:
        m = _load_module(tmp)

        # Empty history returns nulls and zero days.
        r = m._iv_history_compute_rank([], 0.45)
        assert_("empty_history_returns_nulls",
                r["iv_rank"] is None and r["iv_pct"] is None and r["iv_rank_days"] == 0,
                f"got {r}")

        # Append same day twice, expect dedupe to 1 entry.
        m._iv_history_append("AAPL", 0.30)
        m._iv_history_append("AAPL", 0.32)
        p = m._iv_history_path("AAPL")
        data = json.loads(p.read_text())
        assert_("dedupe_within_day",
                len(data) == 1 and abs(data[0]["iv"] - 0.32) < 1e-6,
                f"got {data}")

        # Manually populate 30 days of history with ascending IV.
        history = [{"date": f"2025-01-{i:02d}", "iv": 0.20 + i * 0.01}
                   for i in range(1, 31)]
        p.write_text(json.dumps(history))
        loaded = m._iv_history_load("AAPL")
        assert_("load_30_entries", len(loaded) == 30,
                f"got {len(loaded)}")

        # Current IV at the max → IV rank should be 100.
        r = m._iv_history_compute_rank(loaded, 0.50)
        assert_("rank_at_max_is_100",
                abs(r["iv_rank"] - 100.0) < 0.5,
                f"got {r}")

        # Current IV at the min → IV rank should be 0.
        r = m._iv_history_compute_rank(loaded, 0.21)
        assert_("rank_at_min_is_0",
                abs(r["iv_rank"] - 0.0) < 0.5,
                f"got {r}")

        # Current IV at midpoint → rank around 50.
        r = m._iv_history_compute_rank(loaded, 0.355)
        assert_("rank_at_mid_around_50",
                40 <= r["iv_rank"] <= 60,
                f"got {r}")

        # Below 20 entries → null rank, but days field counts entries.
        short_hist = [{"date": f"2025-02-{i:02d}", "iv": 0.30}
                      for i in range(1, 11)]
        r = m._iv_history_compute_rank(short_hist, 0.30)
        assert_("short_history_returns_null_rank",
                r["iv_rank"] is None and r["iv_pct"] is None and r["iv_rank_days"] == 10,
                f"got {r}")

        # Sanity bounds: IV outside (0, 10] is filtered on load.
        corrupt = [
            {"date": "2025-03-01", "iv": 0.30},
            {"date": "2025-03-02", "iv": 99.0},
            {"date": "2025-03-03", "iv": -0.5},
            {"date": "2025-03-04", "iv": 0.45},
        ]
        p.write_text(json.dumps(corrupt))
        loaded = m._iv_history_load("AAPL")
        assert_("corrupt_entries_filtered",
                len(loaded) == 2,
                f"got {len(loaded)}")

        # Trim to 252 entries on append.
        big_hist = [{"date": f"2024-{1 + i // 30:02d}-{1 + i % 30:02d}",
                     "iv": 0.20 + (i % 50) * 0.005}
                    for i in range(300)]
        p.write_text(json.dumps(big_hist))
        m._iv_history_append("AAPL", 0.40)
        data = json.loads(p.read_text())
        assert_("trims_to_252_max",
                len(data) <= 252,
                f"got {len(data)}")

        # IV Percentile sanity: midpoint should be roughly 50%.
        symmetric = [{"date": f"2025-04-{(i % 28) + 1:02d}",
                      "iv": 0.20 + (i % 41) * 0.01}
                     for i in range(50)]
        r = m._iv_history_compute_rank(symmetric, 0.40)
        assert_("pct_midpoint_around_50",
                40 <= r["iv_pct"] <= 60,
                f"got {r}")

    print()
    total = passed + failed
    print(f"{passed}/{total} passed, {failed} failed")
    if failed:
        print()
        print("Failures:")
        for name, detail in fails:
            print(f"  · {name}  {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
