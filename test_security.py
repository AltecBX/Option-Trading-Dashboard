"""test_security.py (v3.64) — static allowlist, traversal, auth defaults,
CORS behavior, and the startup security gate. Boots the real handler on a
loopback port with isolated persistence; no external network, no paid keys.
Run:  python3 -m unittest test_security
"""
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

_TMP = tempfile.mkdtemp(prefix="jerry_sec_")
os.environ["JERRY_DATA_DIR"] = _TMP
os.environ.pop("API_KEY", None)
os.environ.pop("ALLOWED_ORIGIN", None)

import options_dashboard as od  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

PORT = 8993
BASE = f"http://127.0.0.1:{PORT}"


def _get(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class TestStaticAllowlist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", PORT), od.DashboardHandler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    # ── allowed files ────────────────────────────────────────────────
    def test_root_html_served_no_cache(self):
        code, headers, body = _get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"<script", body)
        self.assertIn("no-cache", headers.get("Cache-Control", ""))

    def test_dist_asset_immutable_and_gzipped(self):
        code, headers, _ = _get("/dist/app-lib.min.js?v=test",
                                {"Accept-Encoding": "gzip"})
        self.assertEqual(code, 200)
        self.assertIn("immutable", headers.get("Cache-Control", ""))
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertIn("javascript", headers.get("Content-Type", ""))

    def test_dist_css_served(self):
        code, headers, _ = _get("/dist/styles.min.css?v=test")
        self.assertEqual(code, 200)
        self.assertIn("immutable", headers.get("Cache-Control", ""))

    def test_config_js_no_cache(self):
        code, headers, _ = _get("/config.js")
        self.assertEqual(code, 200)
        self.assertIn("no-cache", headers.get("Cache-Control", ""))

    def test_assets_image_served(self):
        code, headers, _ = _get("/assets/favicon-32.png?v=test")
        self.assertEqual(code, 200)
        self.assertIn("image/png", headers.get("Content-Type", ""))

    # ── denied files ─────────────────────────────────────────────────
    def test_python_source_denied(self):
        for p in ("/options_dashboard.py", "/storage.py", "/schwab_client.py",
                  "/metrics.py", "/earnings_scan.py"):
            code, _, _ = _get(p)
            self.assertEqual(code, 404, p)

    def test_state_and_deploy_files_denied(self):
        for p in ("/watchlist_seed.json", "/Procfile", "/requirements.txt",
                  "/package.json", "/.gitignore", "/.env",
                  "/HANDOFF_AUDIT.md", "/IMPLEMENTATION_LOG.md",
                  "/schwab_token.json", "/server.log"):
            code, _, _ = _get(p)
            self.assertEqual(code, 404, p)

    def test_git_metadata_denied(self):
        for p in ("/.git/config", "/.git/HEAD", "/.github/workflows/ci.yml"):
            code, _, _ = _get(p)
            self.assertEqual(code, 404, p)

    def test_unminified_sources_denied(self):
        # Only dist/* is servable JS now; readable compiled files are not.
        for p in ("/app.js", "/app-cards.js", "/app.jsx", "/styles.css"):
            code, _, _ = _get(p)
            self.assertEqual(code, 404, p)

    def test_traversal_denied(self):
        for p in ("/dist/../options_dashboard.py",
                  "/dist/..%2foptions_dashboard.py",
                  "/assets/../storage.py",
                  "/dist/%2e%2e/schwab_client.py",
                  "/dist/sub/dir.js"):
            code, _, _ = _get(p)
            self.assertNotEqual(code, 200, p)

    def test_dist_hidden_or_wrong_ext_denied(self):
        for p in ("/dist/.hidden.js", "/dist/app.min.js.gz", "/dist/notes.txt"):
            code, _, _ = _get(p)
            self.assertEqual(code, 404, p)

    # ── auth ─────────────────────────────────────────────────────────
    def test_api_open_without_key_in_dev(self):
        code, _, body = _get("/api/prefs")
        self.assertEqual(code, 200)

    def test_api_requires_key_when_set(self):
        os.environ["API_KEY"] = "sekrit"
        try:
            code, _, _ = _get("/api/prefs")
            self.assertEqual(code, 401)
            code, _, _ = _get("/api/prefs", {"X-API-Key": "wrong"})
            self.assertEqual(code, 401)
            code, _, _ = _get("/api/prefs", {"X-API-Key": "sekrit"})
            self.assertEqual(code, 200)
            # static stays reachable without the key (it's the public shell)
            code, _, _ = _get("/")
            self.assertEqual(code, 200)
        finally:
            os.environ.pop("API_KEY", None)

    # ── CORS ─────────────────────────────────────────────────────────
    def test_no_cors_header_by_default(self):
        code, headers, _ = _get("/api/prefs")
        self.assertEqual(code, 200)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_cors_header_when_configured(self):
        os.environ["ALLOWED_ORIGIN"] = "https://example.test"
        try:
            _, headers, _ = _get("/api/prefs")
            self.assertEqual(headers.get("Access-Control-Allow-Origin"),
                             "https://example.test")
        finally:
            os.environ.pop("ALLOWED_ORIGIN", None)


class TestOriginSecret(unittest.TestCase):
    """v5.49: the Railway address is a back door. Cloudflare Access guards
    the dashboard's own domain, but the *.up.railway.app address served the
    page, and config.js with the API key in it, to anyone. With
    ORIGIN_SECRET set, only requests carrying it in X-Origin-Secret (which
    a Cloudflare Transform Rule adds to the traffic it forwards) are
    served; everything else gets a bare 403. Unset, nothing changes."""

    SECRET = "test-origin-secret-0123456789abcdef"

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), od.DashboardHandler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def tearDown(self):
        os.environ.pop("ORIGIN_SECRET", None)
        os.environ.pop("API_KEY", None)

    def _req(self, path, headers=None, method="GET", data=None):
        req = urllib.request.Request(self.base + path, headers=headers or {},
                                     method=method, data=data)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _with(self, secret=None):
        return {"X-Origin-Secret": secret or self.SECRET}

    def test_unset_changes_nothing(self):
        os.environ.pop("ORIGIN_SECRET", None)
        for p in ("/", "/config.js", "/api/prefs", "/dist/styles.min.css?v=t"):
            code, _, _ = self._req(p)
            self.assertEqual(200, code, p)

    def test_set_refuses_a_request_without_the_header(self):
        os.environ["ORIGIN_SECRET"] = self.SECRET
        for p in ("/", "/index.html", "/config.js", "/api/prefs",
                  "/dist/app-lib.min.js?v=t", "/manifest.webmanifest", "/nope"):
            code, headers, body = self._req(p)
            self.assertEqual(403, code, p)
            self.assertNotIn(b"<script", body, p)
            self.assertNotIn(b"apiKey", body, p)
            self.assertIn(b"origin check failed", body, p)
            self.assertIn("no-store", headers.get("Cache-Control", ""), p)

    def test_config_js_is_not_handed_out(self):
        # The point of it all: config.js carries the API key.
        os.environ["ORIGIN_SECRET"] = self.SECRET
        code, _, body = self._req("/config.js")
        self.assertEqual(403, code)
        self.assertLess(len(body), 100, "a bare refusal, not the file")

    def test_a_wrong_or_partial_secret_is_refused(self):
        os.environ["ORIGIN_SECRET"] = self.SECRET
        for bad in ("wrong", self.SECRET[:-1], self.SECRET + "x", self.SECRET.upper()):
            code, _, _ = self._req("/", self._with(bad))
            self.assertEqual(403, code, bad)

    def test_the_right_secret_is_served(self):
        os.environ["ORIGIN_SECRET"] = self.SECRET
        code, _, body = self._req("/", self._with())
        self.assertEqual(200, code)
        self.assertIn(b"<script", body)
        for p in ("/config.js", "/api/prefs", "/dist/styles.min.css?v=t"):
            code, _, _ = self._req(p, self._with())
            self.assertEqual(200, code, p)

    def test_every_method_is_checked(self):
        os.environ["ORIGIN_SECRET"] = self.SECRET
        for method, path, data in (("HEAD", "/", None), ("OPTIONS", "/api/prefs", None),
                                   ("PUT", "/api/watchlist", b'{"symbols": []}'),
                                   ("POST", "/api/prefs", b"{}")):
            code, _, body = self._req(path, method=method, data=data,
                                      headers={"Content-Type": "application/json"})
            self.assertEqual(403, code, method)
        code, _, _ = self._req("/", self._with(), method="HEAD")
        self.assertEqual(200, code, "HEAD with the secret")

    def test_the_api_key_still_applies_behind_it(self):
        os.environ["ORIGIN_SECRET"] = self.SECRET
        os.environ["API_KEY"] = "sekrit"
        code, _, _ = self._req("/api/prefs", self._with())
        self.assertEqual(401, code, "the origin secret is not the API key")
        code, _, _ = self._req("/api/prefs", {**self._with(), "X-API-Key": "sekrit"})
        self.assertEqual(200, code)

    def test_two_secrets_while_rotating(self):
        os.environ["ORIGIN_SECRET"] = f"{self.SECRET}-new, {self.SECRET}"
        for s in (self.SECRET, self.SECRET + "-new"):
            code, _, _ = self._req("/", self._with(s))
            self.assertEqual(200, code, s)
        code, _, _ = self._req("/", self._with("-new"))
        self.assertEqual(403, code)

    def test_the_health_check_answers_and_says_nothing(self):
        # Railway's own health check cannot carry the secret. /healthz
        # answers it, with two bytes and nothing about the app.
        os.environ["ORIGIN_SECRET"] = self.SECRET
        for p in ("/healthz", "/healthz?probe=1"):
            code, headers, body = self._req(p)
            self.assertEqual(200, code, p)
            self.assertEqual(b"ok", body, p)
            self.assertIn("text/plain", headers.get("Content-Type", ""))
        code, _, _ = self._req("/healthz", method="HEAD")
        self.assertEqual(200, code)
        for p in ("/healthz/../config.js", "/healthz/x", "/api/healthz", "/healthzz"):
            code, _, body = self._req(p)
            self.assertEqual(403, code, p)
        code, _, _ = self._req("/healthz", method="PUT", data=b"{}")
        self.assertEqual(403, code, "only GET and HEAD are exempt")

class TestConfigKeyFromTheEnvironment(unittest.TestCase):
    """v5.49: config.js gets the API key from Railway's API_KEY as it is
    served, so the key is not committed to the repo and rotating it is one
    change on Railway. Unset (local use), config.js is the file as it is."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), od.DashboardHandler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def tearDown(self):
        os.environ.pop("API_KEY", None)

    def _config(self, headers=None):
        req = urllib.request.Request(self.base + "/config.js", headers=headers or {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8")

    def _key_the_page_sees(self, js):
        # Run config.js the way the page does and read back what it set.
        import json
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        out = subprocess.run([node, "-e", "const window = {};" + js + ";process.stdout.write("
                              "JSON.stringify(window.__APP_CONFIG))"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(0, out.returncode, out.stderr)
        return json.loads(out.stdout).get("apiKey")

    def test_the_repo_holds_no_key(self):
        import re
        from pathlib import Path
        text = (Path(od.__file__).parent / "config.js").read_text()
        m = re.search(r'apiKey:\s*"([^"]*)"', text)
        self.assertIsNotNone(m)
        self.assertEqual("", m.group(1), "the API key is committed to the repo")

    def test_unset_serves_the_file(self):
        code, headers, js = self._config()
        self.assertEqual(200, code)
        self.assertIn("no-cache", headers.get("Cache-Control", ""))
        self.assertEqual("", self._key_the_page_sees(js))

    def test_set_puts_the_key_in(self):
        os.environ["API_KEY"] = "rotated-key-123"
        for h in ({}, {"Accept-Encoding": "gzip"}):
            code, headers, js = self._config(h)
            self.assertEqual(200, code)
            self.assertIn("no-cache", headers.get("Cache-Control", ""))
            self.assertIsNone(headers.get("Content-Encoding"))
            self.assertEqual("rotated-key-123", self._key_the_page_sees(js))

    def test_a_key_with_quotes_stays_a_string(self):
        os.environ["API_KEY"] = 'a"b\\c</script>'
        _, _, js = self._config()
        self.assertEqual('a"b\\c</script>', self._key_the_page_sees(js))

class TestStartupGate(unittest.TestCase):
    def test_loopback_never_blocked(self):
        self.assertEqual(od.check_deploy_security("127.0.0.1"), [])
        self.assertEqual(od.check_deploy_security("localhost"), [])

    def test_public_bind_without_key_refused(self):
        os.environ.pop("API_KEY", None)
        problems = od.check_deploy_security("0.0.0.0")
        self.assertTrue(problems and "API_KEY" in problems[0])

    def test_public_bind_with_key_allowed(self):
        os.environ["API_KEY"] = "k"
        try:
            self.assertEqual(od.check_deploy_security("0.0.0.0"), [])
        finally:
            os.environ.pop("API_KEY", None)

    def test_escape_hatch(self):
        os.environ["DANGEROUSLY_DISABLE_AUTH"] = "1"
        try:
            self.assertEqual(od.check_deploy_security("0.0.0.0"), [])
        finally:
            os.environ.pop("DANGEROUSLY_DISABLE_AUTH", None)

    def test_wildcard_origin_on_public_bind_refused(self):
        os.environ["API_KEY"] = "k"
        os.environ["ALLOWED_ORIGIN"] = "*"
        try:
            problems = od.check_deploy_security("0.0.0.0")
            self.assertTrue(any("ALLOWED_ORIGIN" in p for p in problems))
        finally:
            os.environ.pop("API_KEY", None)
            os.environ.pop("ALLOWED_ORIGIN", None)


if __name__ == "__main__":
    unittest.main()
