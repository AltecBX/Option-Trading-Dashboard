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
