#!/usr/bin/env python3
"""test_v115_storage.py — verifies the dismissed alerts and trade
journal helpers added in v1.15. Runs in isolation against a temp dir."""

import json
import os
import sys
import tempfile
import importlib.util


def _load_module(temp_dir):
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

    with tempfile.TemporaryDirectory(prefix="jerry-v115-test-") as tmp:
        m = _load_module(tmp)

        # Dismissed alerts: empty by default.
        d = m._load_dismissed_alerts()
        assert_("dismissed_empty_default", d == {}, f"got {d}")

        # Save and reload. Use today's date so the 90-day age filter
        # does not drop the entry.
        from datetime import date as _date
        today_str = _date.today().strftime("%Y-%m-%d")
        m._save_dismissed_alerts({"AAPL|test|upgrade|Goldman": today_str})
        d = m._load_dismissed_alerts()
        assert_("dismissed_save_load", "AAPL|test|upgrade|Goldman" in d,
                f"got {d}")

        # Aged out (> 90 days) entries dropped on load.
        old = {"OLD|2020-01-01|upgrade|X": "2020-01-01"}
        m._save_dismissed_alerts(old)
        d = m._load_dismissed_alerts()
        assert_("dismissed_aged_out_dropped", "OLD|2020-01-01|upgrade|X" not in d,
                f"got {d}")

        # Bad JSON returns empty dict, does not crash.
        m._DISMISSED_ALERTS_PATH.write_text("not json")
        d = m._load_dismissed_alerts()
        assert_("dismissed_bad_json_returns_empty", d == {}, f"got {d}")

        # Trade journal: empty by default.
        tj = m._load_trade_journal()
        assert_("journal_empty_default", tj == [], f"got {tj}")

        # Append and reload.
        entry = {
            "ticker": "AAPL", "type": "call", "strike": 200.0,
            "expiration": "2025-06-20", "qty": -1,
            "entry_premium": 2.50, "closed_premium": 0.30,
            "opened_at": "2025-05-01T10:00:00",
            "closed_at": "2025-05-15T15:00:00",
        }
        m._save_trade_journal([entry])
        tj = m._load_trade_journal()
        assert_("journal_save_load",
                len(tj) == 1 and tj[0]["ticker"] == "AAPL",
                f"got {tj}")

        # Bad JSON returns empty list.
        m._TRADE_JOURNAL_PATH.write_text("garbage")
        tj = m._load_trade_journal()
        assert_("journal_bad_json_returns_empty", tj == [], f"got {tj}")

        # Save list with multiple entries.
        m._save_trade_journal([entry, dict(entry, ticker="MSFT")])
        tj = m._load_trade_journal()
        assert_("journal_multiple_entries", len(tj) == 2, f"got {len(tj)}")

    print()
    total = passed + failed
    print(f"{passed}/{total} passed, {failed} failed")
    if failed:
        for name, detail in fails:
            print(f"  · {name}  {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
