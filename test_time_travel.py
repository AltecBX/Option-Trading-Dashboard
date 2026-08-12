"""test_time_travel.py — run the whole suite with the clock moved forward.

WHY THIS EXISTS
On 2026-08-12 the suite went red on a test nobody had touched: a whisper
fixture reports earnings on 2026-08-11, the test filed a manual entry against
that event, and production correctly refuses an entry dated after the event it
describes. The test had been standing before that date; then it wasn't. It
blocked an unrelated PR and cost a debugging round to identify.

That test was not special. A grep finds ~60 hard-coded future dates across the
suite, any of which can become false purely by time passing. A test that
expires is worse than a missing test: it fails long after the change that
"caused" it, on someone else's PR, pointing at innocent code.

So: run everything again with the clock pushed forward. Anything that fails
here passes today only by accident of the calendar.

    python3 test_time_travel.py [days]        # default 400

Patches datetime.datetime/date and time.time BEFORE the modules under test
import them, so `from datetime import datetime` in application code picks up
the shifted clock. Not a general-purpose freezer — it exists to answer one
question: does this suite still hold N days from now?
"""
from __future__ import annotations

import os
import sys
import unittest

DEFAULT_DAYS = 400

# Deliberately not run under a shifted clock. test_schedules asserts that the
# hand-maintained CPI/FOMC calendar has not run out — it is a DATA FRESHNESS
# DEADLINE, so failing in the future is exactly what it is for, not a bug in
# the test. The normal suite enforces it against the real date; running it here
# too would just cry wolf on every time-travel run and train people to ignore
# the output.
SKIP_MODULES = {"test_schedules"}


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DAYS
    os.environ.setdefault("JERRY_NO_NET", "1")
    try:
        from freezegun import freeze_time
    except ImportError:
        print("freezegun is not installed — skipping the time-travel check.\n"
              "    pip install freezegun\n"
              "This check is advisory: it finds tests that pass today only\n"
              "because of what the date is. Not installing it does not make\n"
              "the suite wrong, it just leaves those tests unfound.")
        return 0

    import datetime as dt
    target = dt.date.today() + dt.timedelta(days=days)
    print(f"Running the suite as if today were {target.isoformat()} (+{days} days)\n")

    here = os.path.dirname(os.path.abspath(__file__))
    loader = unittest.TestLoader()

    def prune(s):
        out = unittest.TestSuite()
        for t in s:
            if isinstance(t, unittest.TestSuite):
                out.addTest(prune(t))
            elif type(t).__module__ in SKIP_MODULES:
                pass          # see SKIP_MODULES for why
            elif "test_time_travel" not in type(t).__module__:
                out.addTest(t)
        return out

    # Load OUTSIDE the freeze so import-time work is unaffected; only the test
    # run itself sees the shifted clock.
    suite = prune(loader.discover(here, pattern="test_*.py"))
    with freeze_time(target.isoformat(), tick=True):
        result = unittest.TextTestRunner(verbosity=1).run(suite)

    if not result.wasSuccessful():
        print("\n" + "=" * 70)
        print("These tests pass today only because of what the date is.")
        print("Pin the clock in the test (see Base.pin_clock in")
        print("test_whisper_sources.py) rather than editing a captured")
        print("fixture or loosening the assertion.")
        print("=" * 70)
        return 1
    print(f"\nSuite still holds {days} days from now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
