"""JERRY_NO_NET is enforced by the process, not promised by each module.

Before `no_net.install()`, a server started with JERRY_NO_NET=1 still
fetched /api/ticker from Yahoo: that path never checked the flag. The
render suite depended on Yahoo's mood without knowing it, and failed on
untouched code the day Yahoo rate-limited the machine.

Every check that installs the guard runs in a child process, because the
guard is process-wide and cannot be taken back. Each child points the
proxy variables at a listener this test owns. A request that slips past
the guard, whether directly, through the proxy or through curl_cffi in C,
has to knock on that listener, and the test counts the knocks.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import textwrap
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class _Listener:
    """A TCP port that counts who connects to it: the stand-in proxy."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.hits = 0
        self._stop = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        while not self._stop:
            try:
                c, _ = self.sock.accept()
                self.hits += 1
                c.close()
            except OSError:
                continue

    def close(self):
        self._stop = True
        self._t.join(timeout=2)
        self.sock.close()


def _child(code: str, env_extra: dict, proxy_port: int) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY")}
    proxy = f"http://127.0.0.1:{proxy_port}"
    env.update({"HTTPS_PROXY": proxy, "https_proxy": proxy,
                "HTTP_PROXY": proxy, "http_proxy": proxy,
                "NO_PROXY": "", "no_proxy": ""})
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          cwd=str(HERE), env=env, capture_output=True,
                          text=True, timeout=120)


# Each attempt prints REFUSED or LEAKED; nothing here may raise past it.
ATTEMPTS = """
    import socket, sys, urllib.request
    sys.path.insert(0, ".")
    import no_net
    print("INSTALLED", no_net.install())

    def attempt(name, fn):
        try:
            fn()
            print(name, "LEAKED")
        except BaseException as e:            # noqa: BLE001
            # Libraries wrap what the socket raised: urllib in URLError,
            # requests in ProxyError/ConnectionError. The refusal's own
            # message survives every wrapping.
            refused = "JERRY_NO_NET is set" in str(e) or \\
                      "JERRY_NO_NET is set" in str(getattr(e, "reason", ""))
            print(name, "REFUSED" if refused else "OTHER:" + type(e).__name__ + ":" + str(e)[:160])

    # Straight out, no proxy involved.
    attempt("socket", lambda: socket.create_connection(("93.184.216.34", 443), timeout=3))
    # urllib honours HTTPS_PROXY, which names a port on 127.0.0.1.
    attempt("urllib", lambda: urllib.request.urlopen("https://example.com/", timeout=3))
    try:
        import requests
        attempt("requests", lambda: requests.get("https://example.com/", timeout=3))
    except ImportError:
        print("requests SKIPPED")
    try:
        from curl_cffi import requests as cr
        attempt("curl_cffi", lambda: cr.Session().get(
            "https://query1.finance.yahoo.com/v8/finance/chart/AAPL", timeout=3))
    except ImportError:
        print("curl_cffi SKIPPED")
    try:
        import yfinance as yf
        # yfinance swallows most errors and hands back an empty frame. What
        # matters is that nothing reached the listener, which the parent
        # counts. Print what came back so a leak can be seen.
        df = yf.Ticker("AAPL").history(period="5d")
        print("yfinance ROWS", 0 if df is None else len(df))
    except BaseException as e:                 # noqa: BLE001
        print("yfinance RAISED", type(e).__name__)
"""


class TheFlagIsEnforced(unittest.TestCase):

    def setUp(self):
        self.proxy = _Listener()

    def tearDown(self):
        self.proxy.close()

    def test_every_way_out_is_refused_and_nothing_reaches_the_proxy(self):
        out = _child(ATTEMPTS, {"JERRY_NO_NET": "1"}, self.proxy.port)
        text = out.stdout + out.stderr
        self.assertIn("INSTALLED True", text, text[-1500:])
        for name in ("socket", "urllib", "requests", "curl_cffi"):
            line = next((l for l in out.stdout.splitlines() if l.startswith(name + " ")), "")
            self.assertTrue(line.endswith("REFUSED") or line.endswith("SKIPPED"),
                            f"{name} was not refused: {line!r}\n{text[-1500:]}")
        rows = re.search(r"yfinance ROWS (\d+)", out.stdout)
        if rows:
            self.assertEqual("0", rows.group(1), f"yfinance got data with the network off:\n{text[-1500:]}")
        self.assertEqual(0, self.proxy.hits,
                         f"{self.proxy.hits} connection(s) reached the proxy with JERRY_NO_NET set")

    def test_this_machine_is_still_reachable(self):
        """The dashboard is a local server and the tests talk to it."""
        code = """
            import sys, threading, urllib.request
            from http.server import HTTPServer, BaseHTTPRequestHandler
            sys.path.insert(0, ".")
            import no_net
            no_net.install()
            class H(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200); self.end_headers(); self.wfile.write(b"here")
                def log_message(self, *a): pass
            srv = HTTPServer(("127.0.0.1", 0), H)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            print("LOCAL", opener.open(f"http://127.0.0.1:{srv.server_port}/", timeout=5).read().decode())
        """
        out = _child(code, {"JERRY_NO_NET": "1"}, self.proxy.port)
        self.assertIn("LOCAL here", out.stdout, out.stdout + out.stderr[-1500:])

    def test_without_the_flag_nothing_is_touched(self):
        code = """
            import socket, sys
            sys.path.insert(0, ".")
            real = socket.socket.connect
            import no_net
            print("INSTALLED", no_net.install(), "UNTOUCHED", socket.socket.connect is real)
        """
        for value in ("", "0"):
            out = _child(code, {"JERRY_NO_NET": value}, self.proxy.port)
            self.assertIn("INSTALLED False UNTOUCHED True", out.stdout,
                          f"JERRY_NO_NET={value!r}: " + out.stdout + out.stderr[-800:])


class TheServerInstallsIt(unittest.TestCase):

    def test_main_seals_the_process_before_it_serves_or_bakes(self):
        """The guard lives in main(), before the serve/bake branch, and not at
        import: test files import options_dashboard and clear the flag for
        the tests that need the network path (test_gap_scan, test_ewhispers)."""
        src = (HERE / "options_dashboard.py").read_text()
        main = src[src.index("\ndef main() -> None:"):]
        at = main.find("no_net.install()")
        self.assertGreater(at, 0, "main() never installs the JERRY_NO_NET guard")
        self.assertLess(at, main.index("if args.serve:"),
                        "the guard is installed after the server starts")
        head = src[:src.index("\ndef main() -> None:")]
        self.assertNotIn("no_net.install()", head,
                         "installed at import: every test that imports the module would lose the network")

    def test_the_render_suite_refuses_a_server_that_did_not_seal_itself(self):
        src = (HERE / "test_frame_render.py").read_text()
        self.assertIn('"outbound network refused" not in log', src)
        self.assertIn('JERRY_NO_NET="1"', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
