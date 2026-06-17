#!/usr/bin/env python3
"""schwab_auth.py — One-time Schwab OAuth flow.

Run this ONCE on your Mac to mint a refresh token. The flow:
  1. Reads SCHWAB_APP_KEY and SCHWAB_APP_SECRET from .env (or environment).
  2. Opens your browser to Schwab's login.
  3. You log in with your Schwab brokerage credentials, approve the
     account(s), then get redirected to https://127.0.0.1:8182/?code=...
     (the page won't load — that's expected).
  4. Copy the FULL redirected URL from your browser bar and paste it here.
  5. Script exchanges the code for tokens and writes schwab_token.json.

Re-run if:
  • Refresh token expired (7 days unused)
  • You changed your Schwab password
  • You revoked app access in the Schwab portal

Run: python3 schwab_auth.py
"""
import base64
import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _stable_data_dir() -> Path:
    env_dir = os.environ.get("JERRY_DATA_DIR", "").strip()
    if env_dir:
        d = Path(env_dir).expanduser().resolve()
    else:
        d = (Path.home() / ".jerry-dashboard").resolve()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


_STABLE_DIR = _stable_data_dir()
TOKEN_PATH = _STABLE_DIR / "schwab_token.json"
# Read .env from stable dir first, fall back to project folder
ENV_PATH_STABLE = _STABLE_DIR / ".env"
ENV_PATH_LEGACY = HERE / ".env"

CALLBACK_URL = "https://127.0.0.1:8182"
AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"


def load_env():
    """Tiny .env loader — no python-dotenv dependency required.
    Reads stable location first, falls back to project folder."""
    env = dict(os.environ)
    for path in (ENV_PATH_STABLE, ENV_PATH_LEGACY):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def main():
    env = load_env()
    app_key = env.get("SCHWAB_APP_KEY", "").strip()
    app_secret = env.get("SCHWAB_APP_SECRET", "").strip()
    if not app_key or not app_secret:
        print("ERROR: SCHWAB_APP_KEY and SCHWAB_APP_SECRET required.")
        print(f"Add them to {ENV_PATH_STABLE} (preferred) or {ENV_PATH_LEGACY}.")
        print()
        print("Format for .env file:")
        print("  SCHWAB_APP_KEY=your_app_key_here")
        print("  SCHWAB_APP_SECRET=your_app_secret_here")
        sys.exit(1)

    auth_qs = urllib.parse.urlencode({
        "client_id": app_key,
        "redirect_uri": CALLBACK_URL,
    })
    auth_link = f"{AUTH_URL}?{auth_qs}"

    print("Opening browser for Schwab login.")
    print(f"If it doesn't open: {auth_link}")
    print()
    try:
        webbrowser.open(auth_link)
    except Exception:
        pass

    print("Steps:")
    print("  1. Log in with Schwab brokerage credentials.")
    print("  2. Approve account access.")
    print("  3. Browser will redirect to https://127.0.0.1:8182/?code=...")
    print("     The page will fail to load — that's expected.")
    print("  4. Copy the FULL URL from the browser bar and paste below.")
    print()
    redirect_url = input("Paste the redirected URL here: ").strip()

    parsed = urllib.parse.urlparse(redirect_url)
    qs = urllib.parse.parse_qs(parsed.query)
    code = qs.get("code", [""])[0]
    if not code:
        print("ERROR: No 'code' parameter in URL.")
        sys.exit(1)

    # Exchange code for tokens. Schwab requires basic auth header with
    # base64(app_key:app_secret) and form-encoded body.
    auth_header = base64.b64encode(f"{app_key}:{app_secret}".encode()).decode()
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK_URL,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    req.add_header("Authorization", f"Basic {auth_header}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"ERROR: token exchange failed ({e.code}): {body}")
        sys.exit(1)

    # Persist tokens. We add `obtained_at` (epoch seconds) so the server
    # can compute when access_token expires (30 min from issue).
    import time
    data["obtained_at"] = int(time.time())
    TOKEN_PATH.write_text(json.dumps(data, indent=2))
    # tighten file perms — token grants market data access
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except Exception:
        pass

    print()
    print(f"✅ Token saved to {TOKEN_PATH}")
    print(f"   Access token expires in ~{data.get('expires_in', 1800)} seconds.")
    print(f"   Refresh token good for 7 days from now.")
    print()
    print("You can now start the dashboard server normally:")
    print("  python3 options_dashboard.py --serve --host 0.0.0.0 --port 8765")


if __name__ == "__main__":
    main()
