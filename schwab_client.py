"""schwab_client.py — Schwab Market Data wrapper for the dashboard server.

Responsibilities:
  • Load schwab_token.json from disk on startup.
  • Refresh access token every ~25 min (transparent to callers).
  • Expose: quotes(symbols), option_chain(symbol), price_history(symbol).
  • In-memory TTL cache to stay well under Schwab's 120 req/min.
  • Return None on auth failure or any error so callers can fall back
    to yfinance.

This module has no third-party dependencies — uses stdlib urllib only.
"""
import base64
import gzip
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _stable_data_dir() -> Path:
    """Same logic as options_dashboard. Token + env live in
    ~/.jerry-dashboard/ so they survive every zip upgrade."""
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
# Also accept legacy env file in project folder so existing setups
# don't break. New installs should put .env in ~/.jerry-dashboard/.
ENV_PATH_STABLE = _STABLE_DIR / ".env"
ENV_PATH = HERE / ".env"

# One-time migration: copy legacy in-folder token to stable location.
def _migrate_legacy_token():
    if TOKEN_PATH.exists():
        return
    legacy = HERE / "schwab_token.json"
    if legacy.exists():
        try:
            TOKEN_PATH.write_text(legacy.read_text())
            try:
                os.chmod(TOKEN_PATH, 0o600)
            except Exception:
                pass
            print(f"[schwab] migrated token: {legacy} -> {TOKEN_PATH}", file=sys.stderr)
        except Exception as exc:
            print(f"[schwab] token migration failed: {exc}", file=sys.stderr)


_migrate_legacy_token()


def _seed_token_from_env() -> None:
    """Bootstrap the token from a SCHWAB_TOKEN_JSON env var. This is how you
    re-authenticate on a host where you can't drop a file onto the volume
    (e.g. Railway): run schwab_auth.py locally, paste the resulting
    schwab_token.json contents into the SCHWAB_TOKEN_JSON variable, redeploy.
    We only overwrite the on-disk token when the env carries a DIFFERENT
    refresh_token (a fresh re-auth) or there's no token on disk — so a normal
    rotated access token on the volume isn't clobbered on every restart."""
    raw = os.environ.get("SCHWAB_TOKEN_JSON", "").strip()
    if not raw:
        return
    try:
        env_tok = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        print(f"[schwab] SCHWAB_TOKEN_JSON parse failed: {exc}", file=sys.stderr)
        return
    if not isinstance(env_tok, dict) or "refresh_token" not in env_tok:
        return
    disk_tok = {}
    if TOKEN_PATH.exists():
        try:
            disk_tok = json.loads(TOKEN_PATH.read_text())
        except Exception:
            disk_tok = {}
    if disk_tok.get("refresh_token") == env_tok.get("refresh_token"):
        return  # same token already on disk — leave the rotated copy alone
    env_tok.setdefault("obtained_at", int(time.time()))
    env_tok.setdefault("expires_in", 1800)
    try:
        TOKEN_PATH.write_text(json.dumps(env_tok))
        try:
            os.chmod(TOKEN_PATH, 0o600)
        except Exception:
            pass
        print("[schwab] seeded token from SCHWAB_TOKEN_JSON env", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[schwab] env token seed failed: {exc}", file=sys.stderr)


_seed_token_from_env()

API_BASE = "https://api.schwabapi.com"
TOKEN_URL = f"{API_BASE}/v1/oauth/token"
AUTHORIZE_URL = f"{API_BASE}/v1/oauth/authorize"
# Must match a callback registered on your Schwab app. Default is the
# loopback used by schwab_auth.py; override with SCHWAB_REDIRECT_URI if you
# register the deployed app's /api/broker/schwab/callback for one-click auth.
REDIRECT_URI = os.environ.get("SCHWAB_REDIRECT_URI", "https://127.0.0.1:8182").strip()
QUOTES_URL = f"{API_BASE}/marketdata/v1/quotes"
CHAINS_URL = f"{API_BASE}/marketdata/v1/chains"
HISTORY_URL_TPL = f"{API_BASE}/marketdata/v1/pricehistory"

# Cache TTLs (seconds). Quotes refresh fast (faster than the frontend
# poll interval so each poll gets fresh data), history is mostly stable.
TTL_QUOTE = 4
TTL_CHAIN = 30
TTL_HISTORY = 600  # 10 min — daily bars don't update intraday


def _stale_seconds_from_ms(trade_time_ms: int | float | None) -> int | None:
    """Convert a Schwab tradeTime (epoch milliseconds) to seconds since
    that trade printed. Returns None if trade_time_ms is missing/invalid.
    Used by callers to label stale quotes — e.g. an illiquid pre-market
    ticker whose 'live' price is actually 4 hours old.

    Negative values can occur if the local clock is ahead of Schwab's
    server clock; clamp to 0 to avoid showing nonsense.
    """
    if not trade_time_ms:
        return None
    try:
        diff_sec = int((time.time() * 1000 - float(trade_time_ms)) / 1000)
        return max(0, diff_sec)
    except (TypeError, ValueError):
        return None


def _load_env() -> dict:
    """Read .env from stable dir first, fall back to project folder.
    Allows a one-time setup to live in ~/.jerry-dashboard/.env without
    breaking existing in-folder configs."""
    env = dict(os.environ)
    for path in (ENV_PATH_STABLE, ENV_PATH):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


class SchwabClient:
    """Thread-safe Schwab Market Data client with auto token refresh + cache."""

    def __init__(self):
        self._lock = threading.RLock()
        self._cache: dict = {}  # key -> (expires_at_epoch, value)
        self._req_log: list = []  # epoch timestamps for rate accounting
        self._refresh_blocked_until = 0.0  # cooldown after a failed refresh
        self._auth_error: str | None = None  # last refresh failure (for status)
        env = _load_env()
        self.app_key = env.get("SCHWAB_APP_KEY", "").strip()
        self.app_secret = env.get("SCHWAB_APP_SECRET", "").strip()
        self.token_data: dict | None = None
        self._load_token_from_disk()

    # ── Auth / token management ─────────────────────────────────────────
    def _load_token_from_disk(self) -> None:
        if not TOKEN_PATH.exists():
            return
        try:
            self.token_data = json.loads(TOKEN_PATH.read_text())
        except Exception as exc:  # noqa: BLE001
            print(f"[schwab] failed to read token file: {exc}", file=sys.stderr)
            self.token_data = None

    def _save_token_to_disk(self) -> None:
        if not self.token_data:
            return
        # Atomic write: write to a sibling .tmp file, fsync, then os.replace.
        # If the server is killed mid-write (jerry stop, OS shutdown, OOM)
        # the original token file is never partially overwritten — either
        # the old one survives or the new one is fully in place. Without
        # this, a crash during write can leave an empty/truncated token
        # file and force a full re-OAuth from scratch.
        try:
            tmp_path = TOKEN_PATH.with_suffix(TOKEN_PATH.suffix + ".tmp")
            payload = json.dumps(self.token_data, indent=2)
            # Open with explicit fsync so the bytes hit disk before rename.
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    # fsync may fail on some filesystems; the rename below
                    # is still atomic at the directory entry level.
                    pass
            os.replace(tmp_path, TOKEN_PATH)
            try:
                os.chmod(TOKEN_PATH, 0o600)
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            print(f"[schwab] failed to save token: {exc}", file=sys.stderr)
            # Clean up the .tmp if it exists so we don't leave debris.
            try:
                tmp_path = TOKEN_PATH.with_suffix(TOKEN_PATH.suffix + ".tmp")
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    def is_configured(self) -> bool:
        """True if app credentials are set AND a token file exists."""
        return bool(self.app_key and self.app_secret and self.token_data)

    def status(self) -> dict:
        """Status payload for /api/data_source endpoint."""
        if not self.app_key or not self.app_secret:
            return {"configured": False, "reason": "missing_credentials"}
        if not self.token_data:
            return {"configured": False, "reason": "no_token_file"}
        access_age = int(time.time()) - self.token_data.get("obtained_at", 0)
        access_remaining = max(0, self.token_data.get("expires_in", 1800) - access_age)
        # Refresh token expires 7 days from issue
        refresh_age_days = access_age / 86400
        refresh_remaining_days = max(0, 7 - refresh_age_days)
        return {
            "configured": True,
            "access_remaining_sec": access_remaining,
            "refresh_remaining_days": round(refresh_remaining_days, 2),
            "needs_refresh_soon": refresh_remaining_days < 1,
            "auth_error": self._auth_error,
            "needs_reauth": bool(self._auth_error),
        }

    def _access_token_valid(self) -> bool:
        if not self.token_data:
            return False
        obtained = self.token_data.get("obtained_at", 0)
        ttl = self.token_data.get("expires_in", 1800)
        # Refresh proactively 60s before expiry
        return (time.time() - obtained) < (ttl - 60)

    def _refresh_access_token(self) -> bool:
        """Use refresh_token to mint a fresh access_token. Returns True on
        success. On failure (e.g. refresh token expired), we log and the
        caller will route through yfinance fallback.

        Double-checked locking: a concurrent caller may have refreshed
        already while we were waiting for the lock. If so, return True
        without making a redundant network call (which Schwab can reject
        if the refresh token was rotated, breaking auth).
        """
        with self._lock:
            if not self.token_data or "refresh_token" not in self.token_data:
                return False
            # Re-check validity now that we hold the lock — another thread
            # may have refreshed between our caller's check and our acquire.
            if self._access_token_valid():
                return True
            # After a failure (e.g. expired refresh token) back off so we
            # don't hammer Schwab and flood the log on every single request.
            if time.time() < self._refresh_blocked_until:
                return False
            refresh_token = self.token_data["refresh_token"]
            auth_header = base64.b64encode(
                f"{self.app_key}:{self.app_secret}".encode()
            ).decode()
            body = urllib.parse.urlencode({
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }).encode()
            req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
            req.add_header("Authorization", f"Basic {auth_header}")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    new_data = json.loads(resp.read())
                # Schwab returns a new access_token; refresh_token is
                # usually unchanged but we update it if present
                self.token_data["access_token"] = new_data.get("access_token", "")
                self.token_data["expires_in"] = new_data.get("expires_in", 1800)
                self.token_data["obtained_at"] = int(time.time())
                if "refresh_token" in new_data:
                    self.token_data["refresh_token"] = new_data["refresh_token"]
                self._save_token_to_disk()
                self._auth_error = None
                return True
            except urllib.error.HTTPError as e:
                raw = e.read()
                if (e.headers.get("Content-Encoding") or "").lower() == "gzip":
                    try:
                        raw = gzip.decompress(raw)
                    except Exception:
                        pass
                body_txt = raw.decode("utf-8", errors="replace")[:200]
                # Expired/invalid refresh token (Schwab refresh tokens last
                # 7 days). Back off 5 min so we don't spam; re-auth needed.
                self._refresh_blocked_until = time.time() + 300
                self._auth_error = f"refresh failed ({e.code}): {body_txt}"
                print(f"[schwab] token refresh failed ({e.code}); pausing 5 min — "
                      f"re-auth needed if this persists: {body_txt}", file=sys.stderr)
                return False
            except Exception as exc:  # noqa: BLE001
                self._refresh_blocked_until = time.time() + 60
                self._auth_error = f"refresh error: {exc}"
                print(f"[schwab] token refresh error; pausing 60s: {exc}", file=sys.stderr)
                return False

    def authorize_url(self) -> str | None:
        """Schwab login URL for the in-app reconnect flow."""
        if not self.app_key:
            return None
        return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
            "client_id": self.app_key, "redirect_uri": REDIRECT_URI,
        })

    def exchange_code(self, redirect_url: str) -> tuple:
        """Exchange the authorization-code redirect (the full URL Schwab sent
        the browser to, containing ?code=...) — or a bare code — for a fresh
        token set, and persist it. Returns (ok, error)."""
        if not self.app_key or not self.app_secret:
            return False, "Schwab app key/secret not configured"
        raw = (redirect_url or "").strip()
        code = ""
        if "code=" in raw:
            try:
                q = urllib.parse.urlparse(raw).query or raw.split("?", 1)[-1]
                code = urllib.parse.parse_qs(q).get("code", [""])[0]
            except Exception:
                code = ""
        else:
            code = raw
        code = code.strip()
        if not code:
            return False, "No authorization code found in the pasted URL"
        auth_header = base64.b64encode(
            f"{self.app_key}:{self.app_secret}".encode()).decode()
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        }).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
        req.add_header("Authorization", f"Basic {auth_header}")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            rb = e.read()
            if (e.headers.get("Content-Encoding") or "").lower() == "gzip":
                try:
                    rb = gzip.decompress(rb)
                except Exception:
                    pass
            return False, f"{e.code}: {rb.decode('utf-8', 'replace')[:200]}"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        if "access_token" not in data or "refresh_token" not in data:
            return False, "Schwab response missing tokens"
        data["obtained_at"] = int(time.time())
        with self._lock:
            self.token_data = data
            self._refresh_blocked_until = 0.0
            self._auth_error = None
            self._save_token_to_disk()
        return True, None

    def _ensure_token(self) -> str | None:
        """Returns a valid access token or None if we can't get one."""
        if not self.token_data:
            return None
        if not self._access_token_valid():
            if not self._refresh_access_token():
                return None
        return self.token_data.get("access_token") or None

    # ── Rate limiting ───────────────────────────────────────────────────
    def _rate_check(self) -> bool:
        """Returns True if we're under the 120/min limit. We log every
        outbound request and prune entries older than 60s. If we're
        already at 110+, refuse to call so we never get throttled."""
        now = time.time()
        with self._lock:
            self._req_log = [t for t in self._req_log if now - t < 60]
            if len(self._req_log) >= 110:
                return False
            self._req_log.append(now)
        return True

    # ── Cache helpers ───────────────────────────────────────────────────
    def _cache_get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            expires, value = entry
            if time.time() > expires:
                del self._cache[key]
                return None
            return value

    def _cache_set(self, key: str, value: Any, ttl: int) -> None:
        with self._lock:
            self._cache[key] = (time.time() + ttl, value)

    # ── HTTP helper ─────────────────────────────────────────────────────
    def _get(self, url: str, params: dict) -> dict | None:
        token = self._ensure_token()
        if not token:
            return None
        if not self._rate_check():
            return None
        qs = urllib.parse.urlencode(params)
        full_url = f"{url}?{qs}"
        req = urllib.request.Request(full_url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            # 401 likely means our access token is stale despite the
            # local check (clock drift, manual revocation). Try one
            # forced refresh + retry.
            if e.code == 401:
                if self._refresh_access_token():
                    new_token = self.token_data.get("access_token", "") if self.token_data else ""
                    req.headers["Authorization"] = f"Bearer {new_token}"
                    try:
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            return json.loads(resp.read())
                    except Exception:
                        return None
            print(f"[schwab] {url} HTTP {e.code}", file=sys.stderr)
            return None
        except Exception as exc:  # noqa: BLE001
            print(f"[schwab] {url} error: {exc}", file=sys.stderr)
            return None

    # ── Public market data methods ──────────────────────────────────────
    def get_quote(self, symbol: str) -> dict | None:
        """Single-symbol quote. Returns dict with at least
        {symbol, last, change_pct, bid, ask} or None on failure.

        Picks the most recent print between regular session and
        extended session by comparing `tradeTime`. This works in all
        cases:
          - Pre-market: regular tradeTime is yesterday, extended is
            today's pre-market print → extended wins.
          - Regular hours: regular tradeTime updates on every print,
            extended sticks at yesterday's after-hours close →
            regular wins.
          - Post-market: regular tradeTime froze at 4pm, extended
            updates with after-hours prints → extended wins.
          - Overnight / weekends: both blocks stop updating; last
            wins is the last legitimate print of the prior session.
        """
        symbol = symbol.upper().strip()
        cache_key = f"quote:{symbol}"
        hit = self._cache_get(cache_key)
        if hit is not None:
            return hit
        data = self._get(QUOTES_URL, {"symbols": symbol, "fields": "quote,reference,extended"})
        if not data:
            return None
        sym_data = data.get(symbol)
        if not sym_data:
            return None
        q = sym_data.get("quote", {}) or {}
        ref = sym_data.get("reference", {}) or {}
        ext = sym_data.get("extended", {}) or {}
        regular_last = q.get("lastPrice")
        ext_last = ext.get("lastPrice")
        regular_close = q.get("closePrice")
        # tradeTime is milliseconds since epoch. Pick whichever block
        # has the newer print as the live "last" price.
        regular_trade_time = q.get("tradeTime") or 0
        ext_trade_time = ext.get("tradeTime") or 0
        use_extended = (
            ext_last is not None
            and ext_last > 0
            and ext_trade_time > regular_trade_time
        )
        last_price = ext_last if use_extended else regular_last
        # Change % vs prior regular close. During pre-market this is
        # today's pre-market move. During regular hours it equals the
        # netPercentChange Schwab already computed.
        change_pct = q.get("netPercentChange")
        if use_extended and regular_close:
            try:
                change_pct = ((float(ext_last) - float(regular_close)) / float(regular_close)) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        out = {
            "symbol": symbol,
            "last": last_price,
            "regular_last": regular_last,
            "extended_last": ext_last,
            "bid": ext.get("bidPrice") if use_extended else q.get("bidPrice"),
            "ask": ext.get("askPrice") if use_extended else q.get("askPrice"),
            "open": q.get("openPrice"),
            "high": q.get("highPrice"),
            "low": q.get("lowPrice"),
            "close_prev": regular_close,
            "volume": q.get("totalVolume"),
            "extended_volume": ext.get("totalVolume"),
            "change": q.get("netChange"),
            "change_pct": change_pct,
            "name": ref.get("description"),
            "exchange": ref.get("exchangeName"),
            "session": "extended" if use_extended else "regular",
            "source": "schwab",
            # Stale-quote tracking. tradeTime is the milliseconds-since-epoch
            # of the picked price's last actual trade. stale_seconds is how
            # long ago that trade printed. Frontend can tooltip / label.
            # Common cases:
            #   - stale_seconds < 60       → fresh, no label needed
            #   - stale_seconds 60-300     → "delayed" — minor liquidity gap
            #   - stale_seconds > 300      → "stale" — illiquid ticker, halt,
            #                                 or session boundary; price may
            #                                 not reflect current value
            "trade_time_ms": (ext_trade_time if use_extended else regular_trade_time) or None,
            "stale_seconds": _stale_seconds_from_ms(ext_trade_time if use_extended else regular_trade_time),
        }
        self._cache_set(cache_key, out, TTL_QUOTE)
        return out

    def get_quotes(self, symbols: list[str]) -> dict | None:
        """Multi-symbol quote in a single API call. Returns
        {symbol: quote_dict, ...} for each symbol that resolved.
        """
        if not symbols:
            return {}
        clean = [s.upper().strip() for s in symbols if s and s.strip()]
        if not clean:
            return {}
        # Cache check — only call API for misses
        out: dict = {}
        misses: list = []
        for sym in clean:
            hit = self._cache_get(f"quote:{sym}")
            if hit is not None:
                out[sym] = hit
            else:
                misses.append(sym)
        if not misses:
            return out
        data = self._get(QUOTES_URL, {
            "symbols": ",".join(misses),
            "fields": "quote,reference,extended",
        })
        if not data:
            # Return what we have from cache, plus None for misses
            return out if out else None
        for sym in misses:
            sym_data = data.get(sym)
            if not sym_data:
                continue
            q = sym_data.get("quote", {}) or {}
            ref = sym_data.get("reference", {}) or {}
            ext = sym_data.get("extended", {}) or {}
            regular_last = q.get("lastPrice")
            ext_last = ext.get("lastPrice")
            regular_close = q.get("closePrice")
            regular_trade_time = q.get("tradeTime") or 0
            ext_trade_time = ext.get("tradeTime") or 0
            use_extended = (
                ext_last is not None
                and ext_last > 0
                and ext_trade_time > regular_trade_time
            )
            last_price = ext_last if use_extended else regular_last
            change_pct = q.get("netPercentChange")
            if use_extended and regular_close:
                try:
                    change_pct = ((float(ext_last) - float(regular_close)) / float(regular_close)) * 100.0
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            entry = {
                "symbol": sym,
                "last": last_price,
                "regular_last": regular_last,
                "extended_last": ext_last,
                "bid": ext.get("bidPrice") if use_extended else q.get("bidPrice"),
                "ask": ext.get("askPrice") if use_extended else q.get("askPrice"),
                "open": q.get("openPrice"),
                "high": q.get("highPrice"),
                "low": q.get("lowPrice"),
                "close_prev": regular_close,
                "volume": q.get("totalVolume"),
                "extended_volume": ext.get("totalVolume"),
                "change": q.get("netChange"),
                "change_pct": change_pct,
                "name": ref.get("description"),
                "exchange": ref.get("exchangeName"),
                "session": "extended" if use_extended else "regular",
                "source": "schwab",
                "trade_time_ms": (ext_trade_time if use_extended else regular_trade_time) or None,
                "stale_seconds": _stale_seconds_from_ms(ext_trade_time if use_extended else regular_trade_time),
            }
            out[sym] = entry
            self._cache_set(f"quote:{sym}", entry, TTL_QUOTE)
        return out

    def get_quotes_raw(self, symbols: list[str]) -> dict | None:
        """Raw multi-symbol quote passthrough. Returns the Schwab response
        dict keyed by whatever symbol Schwab echoes back (which, for
        futures, may be the active-contract key — e.g. you ask for "/ES"
        and Schwab answers under "/ESM25"). No caching, no strict
        per-symbol matching — the caller decides how to match keys.
        Used by the market-overview strip so futures/index quotes resolve
        even when Schwab normalizes or rolls the symbol.
        """
        if not symbols:
            return {}
        clean = [s.strip() for s in symbols if s and s.strip()]
        if not clean:
            return {}
        data = self._get(QUOTES_URL, {
            "symbols": ",".join(clean),
            "fields": "quote,reference,extended",
        })
        if not data or not isinstance(data, dict):
            return None
        return data

    def get_option_chain(self, symbol: str, expiration: str | None = None,
                         strike_count: int = 60) -> dict | None:
        """Returns a chain payload normalized to the dashboard's expected
        shape: {expirations: [...], chains: {expiry: {calls: [...], puts: [...]}}}.
        Pass expiration as YYYY-MM-DD to filter to one date. strike_count
        controls how many strikes around the money Schwab returns; the
        Level Reprice chain asks for a wider band so the expected-move
        strike is always listed.
        """
        symbol = symbol.upper().strip()
        try:
            sc = int(strike_count)
        except (TypeError, ValueError):
            sc = 60
        sc = max(10, min(sc, 250))
        cache_key = f"chain:{symbol}:{expiration or 'all'}:{sc}"
        hit = self._cache_get(cache_key)
        if hit is not None:
            return hit
        params = {
            "symbol": symbol,
            "contractType": "ALL",
            "strikeCount": sc,
            "includeUnderlyingQuote": "true",
        }
        if expiration:
            params["fromDate"] = expiration
            params["toDate"] = expiration
        data = self._get(CHAINS_URL, params)
        if not data or data.get("status") == "FAILED":
            return None
        # Schwab chain shape: callExpDateMap / putExpDateMap, where each
        # is {"YYYY-MM-DD:DTE": {strike_str: [contract, ...]}}.
        # Normalize into a flat structure the existing code understands.
        out = self._normalize_chain(data)
        if out:
            self._cache_set(cache_key, out, TTL_CHAIN)
        return out

    @staticmethod
    def _normalize_chain(data: dict) -> dict:
        """Translate Schwab chain to {expirations, chains, underlying}."""
        underlying = data.get("underlying") or {}
        spot = underlying.get("last") or data.get("underlyingPrice")
        result = {
            "underlying": {
                "symbol": data.get("symbol"),
                "last": spot,
                "bid": underlying.get("bid"),
                "ask": underlying.get("ask"),
                "name": underlying.get("description"),
            },
            "expirations": [],
            "chains": {},
            "source": "schwab",
        }
        for kind, src_key in (("calls", "callExpDateMap"), ("puts", "putExpDateMap")):
            for exp_key, strikes in (data.get(src_key) or {}).items():
                # exp_key looks like "2026-05-02:5"
                exp_date = exp_key.split(":")[0]
                if exp_date not in result["chains"]:
                    result["chains"][exp_date] = {"calls": [], "puts": []}
                    result["expirations"].append(exp_date)
                for strike_str, contracts in strikes.items():
                    if not contracts:
                        continue
                    c = contracts[0]
                    try:
                        strike = float(strike_str)
                    except (TypeError, ValueError):
                        continue
                    result["chains"][exp_date][kind].append({
                        "strike": strike,
                        "bid": c.get("bid"),
                        "ask": c.get("ask"),
                        "last": c.get("last"),
                        "volume": c.get("totalVolume", 0),
                        "openInterest": c.get("openInterest", 0),
                        "iv": (c.get("volatility") or 0) / 100.0 if c.get("volatility") else None,
                        "delta": c.get("delta"),
                        "theta": c.get("theta"),
                        "gamma": c.get("gamma"),
                        "vega": c.get("vega"),
                    })
        result["expirations"] = sorted(set(result["expirations"]))
        for exp in result["chains"]:
            result["chains"][exp]["calls"].sort(key=lambda r: r["strike"])
            result["chains"][exp]["puts"].sort(key=lambda r: r["strike"])
        return result

    def get_price_history(self, symbol: str, days: int = 260) -> list[dict] | None:
        """Daily bars going back `days` calendar days. Returns list of
        {date, open, high, low, close, volume} dicts, oldest first.
        """
        symbol = symbol.upper().strip()
        cache_key = f"hist:{symbol}:{days}"
        hit = self._cache_get(cache_key)
        if hit is not None:
            return hit
        # Schwab uses periodType=year, period=1 for ~1y. For shorter
        # ranges we still pull a year and slice locally — saves API calls
        # on repeated chart redraws.
        params = {
            "symbol": symbol,
            "periodType": "year",
            "period": 1 if days <= 260 else 2,
            "frequencyType": "daily",
            "frequency": 1,
            "needExtendedHoursData": "false",
        }
        data = self._get(f"{HISTORY_URL_TPL}", params)
        if not data:
            return None
        bars = data.get("candles") or []
        out = []
        # Lazy-import zoneinfo. Schwab daily bars are timestamped at the
        # session OPEN in some Schwab API versions, in others at the close
        # or midnight. Converting via America/New_York and taking the date
        # part keeps every bar on its actual trading-session date and
        # never lands on a weekend.
        try:
            from zoneinfo import ZoneInfo
            ET = ZoneInfo("America/New_York")
        except Exception:
            ET = None
        from datetime import datetime, timezone, timedelta
        for b in bars:
            ts = b.get("datetime", 0) / 1000.0
            if ET is not None:
                dt_et = datetime.fromtimestamp(ts, tz=ET)
            else:
                # Fallback: treat as UTC then shift -5h to approximate ET
                dt_et = datetime.fromtimestamp(ts, tz=timezone.utc) + timedelta(hours=-5)
            d = dt_et.date()
            # Belt-and-suspenders: if conversion still lands on a weekend,
            # snap to the prior Friday (handles edge cases with daylight
            # savings transitions or odd Schwab timestamps).
            if d.weekday() == 5:    # Saturday → Friday
                d = d - timedelta(days=1)
            elif d.weekday() == 6:  # Sunday → Friday
                d = d - timedelta(days=2)
            out.append({
                # Anchor to noon ET so `new Date(d.date)` in any browser
                # timezone lands on this trading session's calendar date.
                # ISO date alone gets parsed as UTC midnight which renders
                # as the previous day in any timezone west of UTC.
                "date": d.isoformat() + "T12:00:00-04:00",
                "open": b.get("open"),
                "high": b.get("high"),
                "low": b.get("low"),
                "close": b.get("close"),
                "volume": b.get("volume"),
            })
        # Trim to requested days
        out = out[-days:]
        if out:
            self._cache_set(cache_key, out, TTL_HISTORY)
        return out

    def get_intraday(self, symbol: str, minutes_back: int = 480,
                     extended: bool = False) -> list[dict] | None:
        """Today's 1-minute bars. Returns list of
        {ts (epoch ms), open, high, low, close, volume} dicts, oldest first.
        Cache TTL is short (30s) so polling stays cheap.

        Uses an explicit `startDate` set to today's 9:30 AM ET so Schwab
        returns today's session only — not the most recent complete day,
        which is what `period=day, period=1` returns before mid-morning.
        With extended=True the window starts at 4:00 AM ET and includes
        pre-market prints, so callers can derive premarket high/low.
        """
        symbol = symbol.upper().strip()
        cache_key = f"intraday:{symbol}:{minutes_back}:{'x' if extended else 'r'}"
        hit = self._cache_get(cache_key)
        if hit is not None:
            return hit
        # Compute today 9:30 AM ET as epoch ms (Schwab requires ms).
        try:
            from zoneinfo import ZoneInfo
            ET = ZoneInfo("America/New_York")
        except Exception:
            ET = None
        from datetime import datetime, time as _time
        start_t = _time(4, 0) if extended else _time(9, 30)
        if ET is not None:
            now_et = datetime.now(ET)
            session_start = datetime.combine(now_et.date(), start_t, tzinfo=ET)
        else:
            now_et = datetime.now()
            session_start = datetime.combine(now_et.date(), start_t)
        start_epoch_ms = int(session_start.timestamp() * 1000)
        # endDate = now in epoch ms. Schwab requires startDate+endDate
        # together (or periodType+period together) — startDate alone returns 400.
        end_epoch_ms = int(now_et.timestamp() * 1000)
        params = {
            "symbol": symbol,
            "periodType": "day",
            "frequencyType": "minute",
            "frequency": 1,
            "startDate": start_epoch_ms,
            "endDate": end_epoch_ms,
            "needExtendedHoursData": "true" if extended else "false",
        }
        data = self._get(f"{HISTORY_URL_TPL}", params)
        if not data:
            return None
        bars = data.get("candles") or []
        out = [
            {
                "ts": b.get("datetime", 0),
                "open": b.get("open"),
                "high": b.get("high"),
                "low": b.get("low"),
                "close": b.get("close"),
                "volume": b.get("volume", 0),
            }
            for b in bars
        ]
        if out:
            # 30s cache — intraday data updates fast
            self._cache_set(cache_key, out, 30)
        return out

    def get_intraday_day(self, symbol: str, date_iso: str) -> list[dict] | None:
        """1-minute bars for one specific PAST date (regular session).
        Used by the Reversal Radar's hit-rate report to resolve signals that
        fired while nobody was watching — exact first-touch instead of a
        close-price approximation. Cached 6h (history never changes).
        """
        symbol = symbol.upper().strip()
        cache_key = f"intradayday:{symbol}:{date_iso}"
        hit = self._cache_get(cache_key)
        if hit is not None:
            return hit
        try:
            from zoneinfo import ZoneInfo
            ET = ZoneInfo("America/New_York")
        except Exception:
            ET = None
        from datetime import datetime, time as _time, date as _date
        try:
            d = _date.fromisoformat(str(date_iso)[:10])
        except (TypeError, ValueError):
            return None
        if ET is not None:
            start = datetime.combine(d, _time(9, 30), tzinfo=ET)
            end = datetime.combine(d, _time(16, 15), tzinfo=ET)
        else:
            start = datetime.combine(d, _time(9, 30))
            end = datetime.combine(d, _time(16, 15))
        params = {
            "symbol": symbol,
            "periodType": "day",
            "frequencyType": "minute",
            "frequency": 1,
            "startDate": int(start.timestamp() * 1000),
            "endDate": int(end.timestamp() * 1000),
            "needExtendedHoursData": "false",
        }
        data = self._get(f"{HISTORY_URL_TPL}", params)
        if not data:
            return None
        out = [
            {"ts": b.get("datetime", 0), "open": b.get("open"), "high": b.get("high"),
             "low": b.get("low"), "close": b.get("close"), "volume": b.get("volume", 0)}
            for b in (data.get("candles") or [])
        ]
        if out:
            self._cache_set(cache_key, out, 6 * 3600)
        return out

    # ── Account methods (v1.17 broker import phase 1) ─────────────────
    # The Schwab Trader API exposes accounts under:
    #   GET /trader/v1/accounts/accountNumbers   (list account hashes)
    #   GET /trader/v1/accounts/{hash}?fields=positions
    # The hash is a non-PII surrogate the API uses instead of the raw
    # account number. We always fetch the full positions list since
    # filtering happens client-side.
    def diag_accounts(self) -> dict:
        """Non-PII diagnostic for the account-list call. Surfaces the HTTP
        status and response shape (no account numbers) so we can tell an
        OAuth-scope error (403) apart from an empty list or a rate block."""
        out = {"token": False, "rate_ok": True, "status": None,
               "kind": None, "count": None, "error": None}
        token = self._ensure_token()
        out["token"] = bool(token)
        if not token:
            out["error"] = "no access token"
            return out
        if not self._rate_check():
            out["rate_ok"] = False
            out["error"] = "rate limited (110/min cap hit)"
            return out
        url = "https://api.schwabapi.com/trader/v1/accounts/accountNumbers"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                out["status"] = getattr(resp, "status", 200)
                body = json.loads(resp.read())
                out["kind"] = type(body).__name__
                if isinstance(body, (list, dict)):
                    out["count"] = len(body)
                if isinstance(body, dict):
                    out["error"] = str(body.get("message") or body.get("error") or "")[:200] or None
        except urllib.error.HTTPError as e:
            out["status"] = e.code
            try:
                out["error"] = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                out["error"] = f"HTTP {e.code}"
        except Exception as exc:  # noqa: BLE001
            out["error"] = str(exc)[:200]
        return out

    def get_account_numbers(self) -> list | None:
        """Returns list of account dicts {accountNumber, hashValue}.
        None on failure or when not configured. The hashValue is what
        the positions endpoint takes."""
        cache_key = "schwab.accounts.list"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = "https://api.schwabapi.com/trader/v1/accounts/accountNumbers"
        data = self._get(url, {})
        if data is None:
            return None
        if not isinstance(data, list):
            return None
        # Cache for 1 hour. Account hashes do not change often.
        self._cache_set(cache_key, data, 3600)
        return data

    def get_account_positions(self, account_hash: str) -> dict | None:
        """Returns the full account payload with positions. Schema is
        the upstream Schwab response so callers can read whatever they
        need. None on failure."""
        if not account_hash:
            return None
        cache_key = f"schwab.account.{account_hash}.positions"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"https://api.schwabapi.com/trader/v1/accounts/{account_hash}"
        data = self._get(url, {"fields": "positions"})
        if data is None:
            return None
        # Cache for 60s. Positions change as fills happen but polling
        # too aggressively is wasteful.
        self._cache_set(cache_key, data, 60)
        return data

    # ── Position normalization (v1.17) ────────────────────────────────
    # Translates Schwab's position payload into the shape the dashboard
    # already uses internally. Handles equity, single-leg options.
    @staticmethod
    def normalize_positions(account_payload: dict) -> list:
        """Returns a list of position dicts in the dashboard's format.
        Each entry has keys consumed by PositionsCard:
          ticker, type ("call"/"put"/"stock"), strike, expiration,
          qty (negative = short), entryPrice (avg cost per share),
          contracts, openedAt, source ("schwab"), schwab_id
        Skips anything Schwab returns that we can't classify (e.g.
        complex multi-leg legacy positions, mutual funds, bonds)."""
        out = []
        if not isinstance(account_payload, dict):
            return out
        # Schwab nests the meaningful body under either 'securitiesAccount'
        # or 'positions' depending on response shape.
        sa = account_payload.get("securitiesAccount") or account_payload
        positions = sa.get("positions") or []
        if not isinstance(positions, list):
            return out
        for p in positions:
            try:
                inst = p.get("instrument") or {}
                asset_type = (inst.get("assetType") or "").upper()
                # Long/short qty. Schwab splits long and short quantities.
                long_qty = float(p.get("longQuantity") or 0)
                short_qty = float(p.get("shortQuantity") or 0)
                qty = long_qty - short_qty  # negative = net short
                if abs(qty) < 1e-9:
                    continue
                avg_price = float(p.get("averagePrice") or 0)
                if asset_type == "EQUITY":
                    out.append({
                        "ticker": (inst.get("symbol") or "").upper().strip(),
                        "type": "stock",
                        "strike": None,
                        "expiration": None,
                        "qty": int(round(qty)),
                        "entryPrice": avg_price,
                        "contracts": None,
                        "source": "schwab",
                        "schwab_id": inst.get("cusip") or inst.get("symbol"),
                    })
                    continue
                if asset_type == "OPTION":
                    # Schwab option payload exposes putCall, strikePrice,
                    # underlyingSymbol, expirationDate (or expirationYear/
                    # Month/Day fields). Composite parsing: prefer the
                    # discrete fields when present.
                    put_call = (inst.get("putCall") or "").upper()
                    if put_call not in ("CALL", "PUT"):
                        continue
                    underlying = (inst.get("underlyingSymbol") or "").upper().strip()
                    strike = inst.get("strikePrice")
                    if strike is None:
                        continue
                    exp_iso = None
                    # Try discrete fields first (most reliable).
                    ey = inst.get("expirationYear")
                    em = inst.get("expirationMonth")
                    ed = inst.get("expirationDay")
                    if ey and em and ed:
                        try:
                            exp_iso = f"{int(ey):04d}-{int(em):02d}-{int(ed):02d}"
                        except (ValueError, TypeError):
                            exp_iso = None
                    if not exp_iso:
                        # Fall back to expirationDate (often "2025-06-20T00:00:00.000Z")
                        ed_str = inst.get("expirationDate") or ""
                        if ed_str:
                            exp_iso = ed_str[:10]
                    if not exp_iso:
                        continue
                    # Convert qty: a short option is -contracts. Schwab
                    # gives the contract count directly in long/short.
                    contracts = int(round(abs(qty)))
                    signed_qty = -contracts if qty < 0 else contracts
                    out.append({
                        "ticker": underlying,
                        "type": put_call.lower(),
                        "strike": float(strike),
                        "expiration": exp_iso,
                        "qty": signed_qty,
                        "entryPrice": avg_price,
                        "contracts": contracts,
                        "source": "schwab",
                        "schwab_id": inst.get("symbol") or inst.get("cusip"),
                    })
                    continue
                # Skip anything else for now: mutual funds, fixed income,
                # complex multi-leg classifications. Logged for debugging.
                # Phase 2 may add covered-call pair detection.
            except Exception as exc:  # noqa: BLE001
                print(f"[schwab.normalize] position parse failed: {exc}", file=sys.stderr)
                continue
        return out


# Single shared instance — created on import, used by the server
_client_instance: SchwabClient | None = None
_client_lock = threading.Lock()


def get_client() -> SchwabClient:
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            if _client_instance is None:
                _client_instance = SchwabClient()
    return _client_instance
