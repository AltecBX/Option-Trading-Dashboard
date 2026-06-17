#!/usr/bin/env python3
"""test_v116_push.py — verifies the push notification helpers added
in v1.16 in isolation. Does NOT actually send pushes (env vars unset
in test mode); verifies behavior when not configured + sent-log
persistence."""

import json
import os
import sys
import tempfile
import importlib.util


def _load_module(temp_dir):
    os.environ["JERRY_DATA_DIR"] = temp_dir
    # Make sure pushover env vars are NOT set in test runs.
    os.environ.pop("PUSHOVER_APP_TOKEN", None)
    os.environ.pop("PUSHOVER_USER_KEY", None)
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

    with tempfile.TemporaryDirectory(prefix="jerry-v116-test-") as tmp:
        m = _load_module(tmp)

        # Pushover not configured returns False.
        assert_("pushover_not_configured",
                m._pushover_configured() is False)

        # Send when not configured returns ok=False, configured=False.
        result = m._pushover_send("Test", "msg")
        assert_("send_when_not_configured_no_throw",
                isinstance(result, dict))
        assert_("send_when_not_configured_returns_unconfigured",
                result.get("ok") is False and result.get("configured") is False,
                f"got {result}")

        # Sent alerts: empty by default.
        sa = m._load_sent_alerts()
        assert_("sent_alerts_empty_default", sa == {}, f"got {sa}")

        # Save and reload.
        m._save_sent_alerts({"roll_flag|AAPL|abc|2025-06-20|200": "2025-05-01T10:00:00"})
        sa = m._load_sent_alerts()
        assert_("sent_alerts_save_load",
                "roll_flag|AAPL|abc|2025-06-20|200" in sa,
                f"got {sa}")

        # Bad JSON returns empty dict.
        m._SENT_ALERTS_PATH.write_text("not json")
        sa = m._load_sent_alerts()
        assert_("sent_alerts_bad_json_returns_empty", sa == {}, f"got {sa}")

        # Pushover env vars set means configured returns True.
        os.environ["PUSHOVER_APP_TOKEN"] = "test-token"
        os.environ["PUSHOVER_USER_KEY"] = "test-user"
        assert_("pushover_configured_when_envs_set",
                m._pushover_configured() is True)
        # Cleanup
        os.environ.pop("PUSHOVER_APP_TOKEN", None)
        os.environ.pop("PUSHOVER_USER_KEY", None)

    print()
    total = passed + failed
    print(f"{passed}/{total} passed, {failed} failed")
    if failed:
        for name, detail in fails:
            print(f"  · {name}  {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
