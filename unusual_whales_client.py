#!/usr/bin/env python3
"""
unusual_whales_client.py

Lightweight Unusual Whales API wrapper. Stdlib-only so it never breaks
the dashboard's import chain. Reads UW_API_KEY from the environment.

Design goals:
- Adaptive rate limit awareness via UW response headers
  (x-uw-req-per-minute-remaining, x-uw-req-per-minute-reset,
   x-uw-daily-req-count, x-uw-token-req-limit, x-uw-minute-req-counter).
- Per-endpoint TTL cache so polling doesn't burn quota.
- Single source of truth for endpoint URLs (avoid the
  hallucinated-endpoint problem documented in UW's own substack).
- Graceful degradation: when key missing, when network fails, when
  rate-limited — return None and let the caller fall back.

NOTE on endpoint paths:
The endpoints below were verified against the published UW skill
documentation (https://unusualwhales.com/skill.md and
api.unusualwhales.com/api/openapi) at build time. If UW changes a path,
add the new one to ENDPOINTS rather than scattering URLs through code.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional


def _load_env_file() -> None:
    """Load ~/.jerry-dashboard/.env into os.environ if present.
    The jerry launcher does not export .env vars to the Python process,
    so each module that needs env-stored secrets has to load the file
    itself. This matches the pattern schwab_client uses via python-dotenv,
    but stdlib-only so this module's import never fails.
    Only sets keys that are not already in the environment.
    """
    candidates = [
        Path.home() / ".jerry-dashboard" / ".env",
        Path.cwd() / ".env",
    ]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            with open(path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    # Strip surrounding quotes if present.
                    if (len(value) >= 2 and
                        ((value[0] == value[-1] == '"') or
                         (value[0] == value[-1] == "'"))):
                        value = value[1:-1]
                    if key and key not in os.environ:
                        os.environ[key] = value
            # Only load the first .env we find.
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[uw] .env load skip {path}: {exc}", file=sys.stderr)


# Load on import so get_client() picks up UW_API_KEY without relying
# on the launcher to export it.
_load_env_file()


# Endpoint catalog. Path templates use {ticker} where applicable.
# Keep this as the only place we name UW paths.
ENDPOINTS = {
    # Flow alerts — unusual options activity firehose. Filterable by
    # ticker_symbol (single ticker), min_premium, is_otm, etc.
    "flow_alerts": "/api/option-trades/flow-alerts",
    # Option chains for a ticker — per-strike snapshot with volume, OI,
    # premium, ask/bid side execution counts, IV, greeks.
    "option_chains": "/api/stock/{ticker}/option-chains",
    # Greek exposure for a ticker — aggregate gamma/delta exposure.
    "greek_exposure": "/api/stock/{ticker}/greek-exposure",
    # Net premium per ticker — running call/put premium imbalance.
    "net_premium": "/api/stock/{ticker}/net-prem-ticks",
    # Stock state — last price, daily stats, share volume.
    "stock_state": "/api/stock/{ticker}/stock-state",
    # Market tide — broad-market call/put premium flow over the session.
    "market_tide": "/api/market/market-tide",
    # Sector flow — net premium broken down by sector.
    "sector_flow": "/api/market/sector-etfs",
    # Spike detection — sudden volume/premium spikes across the market.
    "spike": "/api/market/spike",
    # Total options volume by ticker, today.
    "ticker_options_volume": "/api/stock/{ticker}/options-volume",
}


# Per-endpoint cache TTLs in seconds. Tuned for "as fast as UW allows
# without hitting the limit" — flow alerts refresh aggressively for
# the active ticker, broad-market panels poll less often.
TTL_BY_KEY = {
    "flow_alerts": 5,
    "option_chains": 15,
    "greek_exposure": 30,
    "net_premium": 10,
    "stock_state": 5,
    "market_tide": 30,
    "sector_flow": 60,
    "spike": 15,
    "ticker_options_volume": 30,
    "_default": 15,
}


_BASE_URL = "https://api.unusualwhales.com"
_TIMEOUT_SEC = 8.0
# Safety margin — when remaining minute requests drops to/below this,
# we throttle until the reset header says we can resume.
_MIN_REMAINING_MARGIN = 3


class UWClient:
    """Small thread-safe singleton-ish UW client with caching and rate
    limit awareness. Initialize once via get_client().
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._cache: dict[str, tuple[float, Any]] = {}  # key -> (expiry_ts, payload)
        self._cache_lock = threading.Lock()
        # Latest rate limit picture, populated from response headers.
        self._rate: dict[str, Any] = {
            "req_per_minute_remaining": None,
            "req_per_minute_reset": None,  # seconds until reset
            "minute_req_counter": None,
            "daily_req_count": None,
            "token_req_limit": None,
            "last_status": None,
            "last_error": None,
            "last_call_ts": None,
        }
        self._rate_lock = threading.Lock()
        # Cooperative throttle window. When set in the future, calls block.
        self._block_until_ts = 0.0

    # ── public ──

    def health(self) -> dict[str, Any]:
        """Quick liveness check — pings stock_state for SPY (cheap call)
        so we can populate rate-limit info even on app start. Returns
        dict with configured/connected flags + rate snapshot.
        """
        out = {
            "configured": bool(self._api_key),
            "connected": False,
            "error": None,
            "rate": dict(self._rate),
        }
        if not self._api_key:
            out["error"] = "UW_API_KEY not set"
            return out
        try:
            data = self._get("stock_state", {"ticker": "SPY"})
            out["connected"] = data is not None
            if data is None:
                out["error"] = self._rate.get("last_error") or "unknown"
            out["rate"] = dict(self._rate)
        except Exception as exc:  # noqa: BLE001
            out["error"] = str(exc)
        return out

    def flow_alerts(self, ticker: str, *, limit: int = 50,
                    min_premium: int = 0) -> Optional[list[dict]]:
        """Recent unusual options trades for a ticker."""
        params = {
            "ticker_symbol": ticker.upper(),
            "limit": str(limit),
        }
        if min_premium > 0:
            params["min_premium"] = str(min_premium)
        return self._get("flow_alerts", params)

    def market_flow_alerts(self, *, limit: int = 200,
                           min_premium: int = 50000) -> Optional[list[dict]]:
        """Market-wide unusual options activity. No ticker filter — returns
        the latest unusual flow alerts across all tickers, sorted by UW's
        own ranking. Used to surface tickers NOT on the user's watchlist
        that have setups in play right now.
        """
        params = {"limit": str(limit)}
        if min_premium > 0:
            params["min_premium"] = str(min_premium)
        return self._get("flow_alerts", params)

    def option_chains(self, ticker: str) -> Optional[dict]:
        return self._get("option_chains", {"ticker": ticker.upper()})

    def greek_exposure(self, ticker: str) -> Optional[dict]:
        return self._get("greek_exposure", {"ticker": ticker.upper()})

    def net_premium(self, ticker: str) -> Optional[dict]:
        return self._get("net_premium", {"ticker": ticker.upper()})

    def stock_state(self, ticker: str) -> Optional[dict]:
        return self._get("stock_state", {"ticker": ticker.upper()})

    def market_tide(self) -> Optional[dict]:
        return self._get("market_tide", {})

    def sector_flow(self) -> Optional[dict]:
        return self._get("sector_flow", {})

    def spike(self) -> Optional[dict]:
        return self._get("spike", {})

    def ticker_options_volume(self, ticker: str) -> Optional[dict]:
        return self._get("ticker_options_volume", {"ticker": ticker.upper()})

    def rate_snapshot(self) -> dict[str, Any]:
        """Read-only copy of the latest rate-limit info."""
        with self._rate_lock:
            return dict(self._rate)

    # ── internals ──

    def _get(self, key: str, params: dict[str, str]) -> Any:
        """Fetch with TTL caching and rate-limit aware throttling.
        Returns parsed JSON 'data' field (or None on error/missing).
        """
        if not self._api_key:
            return None
        path_tmpl = ENDPOINTS.get(key)
        if path_tmpl is None:
            return None
        # Substitute {ticker} into the path. Remaining params go into
        # the query string. If a {ticker} placeholder exists but no
        # ticker is supplied, the call is malformed.
        path_params = {k: v for k, v in params.items() if "{" + k + "}" in path_tmpl}
        query_params = {k: v for k, v in params.items() if k not in path_params}
        try:
            path = path_tmpl.format(**path_params)
        except KeyError as exc:
            with self._rate_lock:
                self._rate["last_error"] = f"missing path param {exc}"
            return None
        # Build cache key from path + sorted query string. Identical
        # requests within TTL return cached payload.
        qs = urllib.parse.urlencode(sorted(query_params.items()))
        cache_key = f"{path}?{qs}"
        ttl = TTL_BY_KEY.get(key, TTL_BY_KEY["_default"])
        now = time.time()
        with self._cache_lock:
            entry = self._cache.get(cache_key)
            if entry and entry[0] > now:
                return entry[1]
        # Cooperative throttle — if we know we're near the limit, wait.
        if self._block_until_ts > now:
            wait = self._block_until_ts - now
            time.sleep(min(wait, 5.0))  # cap so caller never hangs forever
        url = _BASE_URL + path + ("?" + qs if qs else "")
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": "JerryDashboard/0.80",
        })
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
                self._read_rate_headers(resp.headers, status=resp.status)
                raw = resp.read()
                payload = json.loads(raw.decode("utf-8"))
        except urllib.request.HTTPError as exc:
            self._read_rate_headers(getattr(exc, "headers", {}), status=exc.code)
            with self._rate_lock:
                self._rate["last_error"] = f"HTTP {exc.code}"
            print(f"[uw] {key} {exc.code} {url}", file=sys.stderr)
            return None
        except Exception as exc:  # noqa: BLE001
            with self._rate_lock:
                self._rate["last_error"] = str(exc)[:120]
            print(f"[uw] {key} error: {exc}", file=sys.stderr)
            return None
        # UW returns {"data": [...]} or {"data": {...}} — peel data.
        data = payload.get("data") if isinstance(payload, dict) else payload
        with self._cache_lock:
            self._cache[cache_key] = (now + ttl, data)
        return data

    def _read_rate_headers(self, headers: Any, *, status: int | None = None) -> None:
        """Pull UW's rate-limit signals out of the response headers and
        update self._rate + the cooperative throttle window.
        """
        def _h(name: str) -> Optional[str]:
            try:
                return headers.get(name) if hasattr(headers, "get") else None
            except Exception:  # noqa: BLE001
                return None
        try:
            remaining = _h("x-uw-req-per-minute-remaining")
            reset = _h("x-uw-req-per-minute-reset")
            minute_counter = _h("x-uw-minute-req-counter")
            daily = _h("x-uw-daily-req-count")
            token_limit = _h("x-uw-token-req-limit")
        except Exception:  # noqa: BLE001
            remaining = reset = minute_counter = daily = token_limit = None
        with self._rate_lock:
            if remaining is not None:
                try:
                    self._rate["req_per_minute_remaining"] = int(remaining)
                except (TypeError, ValueError):
                    pass
            if reset is not None:
                try:
                    self._rate["req_per_minute_reset"] = int(reset)
                except (TypeError, ValueError):
                    pass
            if minute_counter is not None:
                try:
                    self._rate["minute_req_counter"] = int(minute_counter)
                except (TypeError, ValueError):
                    pass
            if daily is not None:
                try:
                    self._rate["daily_req_count"] = int(daily)
                except (TypeError, ValueError):
                    pass
            if token_limit is not None:
                try:
                    self._rate["token_req_limit"] = int(token_limit)
                except (TypeError, ValueError):
                    pass
            if status is not None:
                self._rate["last_status"] = status
            self._rate["last_call_ts"] = time.time()
            self._rate["last_error"] = None if status and 200 <= status < 300 else self._rate.get("last_error")
            # Set throttle window if we're at or near the minute limit.
            rem = self._rate.get("req_per_minute_remaining")
            rst = self._rate.get("req_per_minute_reset")
            if isinstance(rem, int) and isinstance(rst, int):
                if rem <= _MIN_REMAINING_MARGIN:
                    self._block_until_ts = time.time() + max(rst, 1)
                else:
                    self._block_until_ts = 0.0


_SINGLETON: Optional[UWClient] = None
_SINGLETON_LOCK = threading.Lock()


def get_client() -> Optional[UWClient]:
    """Return the process-wide UW client, or None if no key configured.
    Safe to call repeatedly; lazy init.
    """
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is not None:
            return _SINGLETON
        api_key = os.environ.get("UW_API_KEY", "").strip()
        if not api_key:
            return None
        _SINGLETON = UWClient(api_key)
        return _SINGLETON


def is_configured() -> bool:
    return bool(os.environ.get("UW_API_KEY", "").strip())
