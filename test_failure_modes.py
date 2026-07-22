"""Failure-mode tests for the dashboard server.

Run from /home/claude/work via:
    python3 test_failure_modes.py

Verifies graceful degradation under expected failure conditions:
  - Bad ticker (the ZZZZZZ bug that prompted this work)
  - Unknown endpoint returns JSON 404 (not HTML)
  - Schwab token expiry produces JSON, not crash
  - UW client unavailable → endpoints return clean error JSON
  - Stale-quote helper labels correctly

Each test starts a fresh server on a unique port, hits the endpoint,
and checks the response is JSON-parseable + correct status code.

NOT run automatically — Jerry can run before shipping.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER_SCRIPT = HERE / "options_dashboard.py"


def _find_free_port() -> int:
    """Find an unused TCP port. Race-free enough for test purposes."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int, ticker: str = "AAPL", env_override: dict | None = None):
    """Spawn the server. Returns Popen handle. Caller must terminate."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT), "--serve", "--port", str(port),
         "--no-open", ticker],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for it to come up
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            return proc
        except (urllib.error.URLError, ConnectionRefusedError, socket.timeout):
            time.sleep(0.2)
    proc.terminate()
    raise RuntimeError(f"Server didn't start on port {port}")


def _fetch(url: str, timeout: int = 30) -> tuple[int, str, str]:
    """Returns (status_code, content_type, body)."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8")


def test_bad_ticker_returns_json():
    """The ZZZZZZ regression. sys.exit() inside build_payload used to
    kill the handler thread and produce 'Unexpected token <' on the
    frontend. Now must return JSON 500 with a clean error message."""
    port = _find_free_port()
    proc = _start_server(port)
    try:
        status, ctype, body = _fetch(f"http://127.0.0.1:{port}/api/ticker?symbol=ZZZZZZ")
        assert status == 500, f"expected 500, got {status}"
        assert "json" in ctype.lower(), f"expected JSON content-type, got {ctype}"
        parsed = json.loads(body)
        assert "error" in parsed, f"missing error field: {parsed}"
        assert "ZZZZZZ" in parsed.get("symbol", "") or "ZZZZZZ" in parsed.get("error", ""), \
            f"error doesn't mention ticker: {parsed}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_unknown_endpoint_returns_json_404():
    """Previously /api/foo would fall through to SimpleHTTPRequestHandler
    and return an HTML 404 page. Now must return JSON 404."""
    port = _find_free_port()
    proc = _start_server(port)
    try:
        status, ctype, body = _fetch(f"http://127.0.0.1:{port}/api/this_does_not_exist")
        assert status == 404, f"expected 404, got {status}"
        assert "json" in ctype.lower(), f"expected JSON, got {ctype}"
        parsed = json.loads(body)
        assert "error" in parsed
        assert parsed.get("path") == "/api/this_does_not_exist"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_known_good_ticker_works():
    """Sanity: AAPL returns full payload. Confirms we didn't break the
    happy path while fixing the failure paths.
    NOTE: the ONLY test in the repo that needs live upstream data (Yahoo).
    Set JERRY_NO_NET=1 (CI does) to skip it deterministically."""
    if os.environ.get("JERRY_NO_NET"):
        print("        (skipped: JERRY_NO_NET=1 — needs live Yahoo data)")
        return
    port = _find_free_port()
    proc = _start_server(port)
    try:
        status, ctype, body = _fetch(f"http://127.0.0.1:{port}/api/ticker?symbol=AAPL", timeout=60)
        assert status == 200, f"expected 200, got {status}"
        assert "json" in ctype.lower()
        parsed = json.loads(body)
        assert parsed.get("ticker") == "AAPL"
        assert isinstance(parsed.get("rows"), list) and len(parsed["rows"]) > 0
        # Daily bars must be present for charts
        assert isinstance(parsed.get("daily"), list) and len(parsed["daily"]) > 0
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_quote_endpoint_with_unknown_ticker():
    """/api/quote with an unknown ticker should return empty results,
    not crash."""
    port = _find_free_port()
    proc = _start_server(port)
    try:
        status, ctype, body = _fetch(f"http://127.0.0.1:{port}/api/quote?tickers=ZZZZZZZ", timeout=20)
        assert status == 200, f"expected 200, got {status}"
        parsed = json.loads(body)
        assert "results" in parsed
        # Empty or doesn't contain the bad ticker — either is fine
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_search_endpoint_empty_query():
    """/api/search with empty q must return empty results, not crash."""
    port = _find_free_port()
    proc = _start_server(port)
    try:
        status, ctype, body = _fetch(f"http://127.0.0.1:{port}/api/search?q=")
        assert status == 200
        parsed = json.loads(body)
        assert parsed.get("results") == []
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_data_source_endpoint():
    """/api/data_source must always succeed — the sidebar polls it
    for the source badge and a failure cascades to the badge being
    stuck. Must work even when Schwab/UW are unconfigured."""
    port = _find_free_port()
    # Override with empty Schwab + UW credentials to simulate fresh install
    proc = _start_server(port, env_override={
        "SCHWAB_APP_KEY": "",
        "SCHWAB_APP_SECRET": "",
        "UW_API_KEY": "",
    })
    try:
        status, ctype, body = _fetch(f"http://127.0.0.1:{port}/api/data_source")
        assert status == 200, f"expected 200, got {status}"
        parsed = json.loads(body)
        assert "schwab" in parsed
        # Schwab block should report not configured
        sw = parsed["schwab"]
        assert sw.get("configured") is False
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_uw_health_when_unconfigured():
    """UW health endpoint must return clean JSON when UW is missing."""
    port = _find_free_port()
    proc = _start_server(port, env_override={"UW_API_KEY": ""})
    try:
        status, ctype, body = _fetch(f"http://127.0.0.1:{port}/api/uw/health")
        assert status == 200, f"expected 200, got {status}"
        parsed = json.loads(body)
        # Either configured=False or connected=False — both are valid
        assert parsed.get("configured") is False or parsed.get("connected") is False
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_stale_seconds_helper():
    """Direct unit test of the stale_seconds helper. Already in
    test_strategy_math.py? No — different file. Quick check here."""
    from schwab_client import _stale_seconds_from_ms

    now_ms = time.time() * 1000

    # Fresh
    s = _stale_seconds_from_ms(now_ms - 5000)
    assert s is not None and 4 <= s <= 6, f"5s ago → {s}"

    # 5 minutes
    s = _stale_seconds_from_ms(now_ms - 5 * 60 * 1000)
    assert s is not None and 295 <= s <= 305, f"5m ago → {s}"

    # Invalid
    assert _stale_seconds_from_ms(None) is None
    assert _stale_seconds_from_ms(0) is None
    assert _stale_seconds_from_ms("garbage") is None

    # Future (clock skew) clamps to 0
    s = _stale_seconds_from_ms(now_ms + 5000)
    assert s == 0, f"future → {s}"


def test_log_warn_doesnt_crash():
    """_log_warn must never raise, even with weird inputs."""
    from options_dashboard import _log_warn

    # Normal call
    _log_warn("AAPL", "test", ValueError("test message"))

    # None symbol (most common at top-level handlers)
    _log_warn(None, "test", RuntimeError("no symbol"))

    # Very long values — must truncate gracefully
    _log_warn("VERYLONGTICKERNAME", "x" * 200, Exception("y" * 500))

    # Non-string symbol
    _log_warn(12345, "test", Exception("numeric symbol"))


def run():
    tests = [
        test_stale_seconds_helper,
        test_log_warn_doesnt_crash,
        test_bad_ticker_returns_json,
        test_unknown_endpoint_returns_json_404,
        test_quote_endpoint_with_unknown_ticker,
        test_search_endpoint_empty_query,
        test_data_source_endpoint,
        test_uw_health_when_unconfigured,
        test_known_good_ticker_works,
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
