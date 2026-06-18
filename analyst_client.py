"""analyst_client.py — Analyst price targets and rating changes.

Combines two free data sources:

  Finnhub (https://finnhub.io)
    /api/v1/stock/price-target  — current target consensus
                                   (avg, high, low, numAnalysts, lastUpdated)
    /api/v1/stock/recommendation — recent recommendation breakdown
                                   (strongBuy/buy/hold/sell/strongSell counts
                                    over the last 4 months)

  yfinance (already in the stack)
    Ticker(sym).upgrades_downgrades  — per-firm rating changes with
                                        date, firm, action, fromGrade, toGrade
    Ticker(sym).analyst_price_targets — alternative to Finnhub aggregates,
                                         used as fallback

The client returns a single normalized payload that the frontend can
consume directly. If Finnhub is unconfigured or any source fails, we
degrade gracefully to whatever data we can get from the other source.

Caching:
  - In-memory TTL cache keyed by symbol (default 30 min — analyst
    targets change at most a few times per week, frequent polling
    burns Finnhub free-tier quota for no reason)
  - The cache is process-local; restarts wipe it (intentional — if
    you restart the server it's usually because something changed).

Stdlib-only — no external dependencies beyond yfinance which is
already a dashboard dependency.
"""
from __future__ import annotations
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# yfinance is imported lazily so this module loads even when yfinance
# is missing — the Finnhub-only path still works.
try:
    import yfinance as yf  # noqa: F401
    _YF_OK = True
except Exception:
    _YF_OK = False


# ── Configuration ─────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
ENV_PATHS = [
    Path(os.environ.get("HOME", "~")).expanduser() / ".jerry-dashboard" / ".env",
    HERE / ".env",
]

FINNHUB_BASE = "https://finnhub.io/api/v1"
TIMEOUT_SEC = 6.0
TTL_SEC = 30 * 60  # 30 min — analyst data changes at most a few times per week


def _load_env() -> dict:
    """Read .env from stable dir then project. Same pattern as schwab_client."""
    env = dict(os.environ)
    for p in ENV_PATHS:
        if not p.exists():
            continue
        try:
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in env:
                    env[k] = v
        except Exception:
            pass
    return env


# ── Finnhub thin client ───────────────────────────────────────────────
def _finnhub_get(path: str, params: dict, api_key: str) -> dict | list | None:
    """GET helper. Returns None on error so callers can degrade gracefully."""
    if not api_key:
        return None
    params = dict(params)
    params["token"] = api_key
    url = f"{FINNHUB_BASE}{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "jerry-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 401/403 = bad key OR endpoint moved to paid plan
        # (e.g. /stock/price-target went paid in 2024-2025).
        # 429 = rate limited; 404 = symbol not covered.
        # We log once per session per (path, code) combo to avoid log
        # spam on expected 403s for paid endpoints we attempt anyway.
        global _FINNHUB_403_LOGGED
        try:
            _FINNHUB_403_LOGGED
        except NameError:
            _FINNHUB_403_LOGGED = set()
        key_403 = (path, e.code)
        if e.code != 403 or key_403 not in _FINNHUB_403_LOGGED:
            sys.stderr.write(f"[analyst] Finnhub {path} HTTP {e.code} for {params.get('symbol', '?')}\n")
            if e.code == 403:
                _FINNHUB_403_LOGGED.add(key_403)
        return None
    except Exception as e:
        sys.stderr.write(f"[analyst] Finnhub {path} error: {type(e).__name__}: {e}\n")
        return None


# ── Rating normalization ───────────────────────────────────────────────
# Different sources return different rating strings. We normalize to a
# canonical set so the frontend logic is consistent.
_RATING_NORMALIZE = {
    # Bullish
    "strong buy": "Strong Buy",
    "buy": "Buy",
    "outperform": "Outperform",
    "overweight": "Overweight",
    "accumulate": "Buy",
    "add": "Buy",
    "positive": "Buy",
    "long-term buy": "Buy",
    # Neutral
    "hold": "Hold",
    "neutral": "Hold",
    "market perform": "Hold",
    "equal-weight": "Hold",
    "in-line": "Hold",
    "sector perform": "Hold",
    "peer perform": "Hold",
    # Bearish
    "underperform": "Underperform",
    "underweight": "Underperform",
    "sell": "Sell",
    "strong sell": "Strong Sell",
    "reduce": "Sell",
    "negative": "Sell",
}

# Numeric scores for consensus calculation — higher = more bullish.
_RATING_SCORE = {
    "Strong Buy": 5.0,
    "Buy": 4.0,
    "Outperform": 4.0,
    "Overweight": 4.0,
    "Hold": 3.0,
    "Underperform": 2.0,
    "Underweight": 2.0,
    "Sell": 1.0,
    "Strong Sell": 0.0,
}


def _normalize_rating(s: str | None) -> str | None:
    if not s:
        return None
    key = str(s).strip().lower()
    if key in _RATING_NORMALIZE:
        return _RATING_NORMALIZE[key]
    # Try partial match (e.g. "Buy / Outperform")
    for needle, canonical in _RATING_NORMALIZE.items():
        if needle in key:
            return canonical
    # Unknown rating — return as-is (capitalize for display)
    return str(s).strip().title()


def _consensus_label_from_score(score: float | None) -> str:
    if score is None:
        return "—"
    if score >= 4.5: return "Strong Buy"
    if score >= 3.5: return "Buy"
    if score >= 2.5: return "Hold"
    if score >= 1.5: return "Sell"
    return "Strong Sell"


# ── Action classification ──────────────────────────────────────────────
# Map yfinance/Finnhub action strings to canonical action types.
_ACTION_MAP = {
    "up": "upgrade",
    "down": "downgrade",
    "main": "reiterate",
    "reit": "reiterate",
    "init": "initiate",
    "start": "initiate",
}

def _classify_action(raw_action: str | None, prior: str | None, new: str | None) -> str:
    """Return one of: upgrade, downgrade, reiterate, initiate, target_change, unknown.

    Prefers explicit action string from yfinance, falls back to inferring
    from prior→new rating change.
    """
    if raw_action:
        key = str(raw_action).strip().lower()
        for needle, action in _ACTION_MAP.items():
            if needle in key:
                return action
    # Infer from rating change
    if prior and new:
        ps = _RATING_SCORE.get(_normalize_rating(prior) or "", 3.0)
        ns = _RATING_SCORE.get(_normalize_rating(new) or "", 3.0)
        if ns > ps + 0.5: return "upgrade"
        if ns < ps - 0.5: return "downgrade"
        return "reiterate"
    if new and not prior:
        return "initiate"
    return "unknown"


# ── Main client class ─────────────────────────────────────────────────
class AnalystClient:
    def __init__(self):
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[float, dict]] = {}
        env = _load_env()
        self.finnhub_key = env.get("FINNHUB_API_KEY", "").strip()

    def is_finnhub_configured(self) -> bool:
        return bool(self.finnhub_key)

    def _cache_get(self, key: str) -> dict | None:
        with self._lock:
            hit = self._cache.get(key)
            if not hit:
                return None
            expires_at, value = hit
            if time.time() > expires_at:
                self._cache.pop(key, None)
                return None
            return value

    def _cache_set(self, key: str, value: dict, ttl: float = TTL_SEC) -> None:
        with self._lock:
            self._cache[key] = (time.time() + ttl, value)

    # ── Finnhub fetchers ──────────────────────────────────────────────
    def _fetch_finnhub_target(self, symbol: str) -> dict | None:
        """Returns {targetMean, targetHigh, targetLow, targetMedian,
        numberOfAnalysts, lastUpdated} or None."""
        data = _finnhub_get("/stock/price-target", {"symbol": symbol}, self.finnhub_key)
        if not isinstance(data, dict):
            return None
        # Finnhub returns 0/0/0 for symbols it doesn't cover — treat as None
        if not data.get("targetMean") and not data.get("targetHigh"):
            return None
        return {
            "target_mean": data.get("targetMean") or None,
            "target_high": data.get("targetHigh") or None,
            "target_low": data.get("targetLow") or None,
            "target_median": data.get("targetMedian") or None,
            "num_analysts": data.get("numberOfAnalysts") or None,
            "last_updated": data.get("lastUpdated") or None,
        }

    def _fetch_finnhub_recommendation(self, symbol: str) -> list[dict] | None:
        """Returns list of monthly recommendation breakdowns, most-recent first.
        Each item: {period, strongBuy, buy, hold, sell, strongSell}."""
        data = _finnhub_get("/stock/recommendation", {"symbol": symbol}, self.finnhub_key)
        if not isinstance(data, list) or not data:
            return None
        # Finnhub returns oldest-first sometimes; sort newest-first by period
        try:
            data = sorted(data, key=lambda x: x.get("period", ""), reverse=True)
        except Exception:
            pass
        out = []
        for row in data[:6]:  # last 6 months max
            out.append({
                "period": row.get("period"),
                "strong_buy": int(row.get("strongBuy") or 0),
                "buy": int(row.get("buy") or 0),
                "hold": int(row.get("hold") or 0),
                "sell": int(row.get("sell") or 0),
                "strong_sell": int(row.get("strongSell") or 0),
            })
        return out

    # ── yfinance fetchers ─────────────────────────────────────────────
    def _fetch_yf_history(self, symbol: str) -> list[dict]:
        """Per-firm rating changes from yfinance.

        Returns up to 30 most-recent updates as a list of dicts:
          {date, firm, action, prior_grade, new_grade, prior_target,
           new_target, action_class}

        yfinance does NOT provide prior price targets per row, so
        prior_target is always None. yfinance does NOT have analyst
        names, only firm names.
        """
        if not _YF_OK:
            return []
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            # Prefer upgrades_downgrades — newer endpoint includes
            # currentPriceTarget and priorPriceTarget. Older
            # `recommendations` endpoint stops in 2018 for many tickers.
            df = None
            try:
                df = ticker.upgrades_downgrades
            except Exception:
                pass
            if df is None or (hasattr(df, "empty") and df.empty):
                # Fallback only if upgrades_downgrades unavailable
                try:
                    df = ticker.recommendations
                except Exception:
                    return []
            if df is None or (hasattr(df, "empty") and df.empty):
                return []

            df = df.copy()
            # Make the index a column if it looks like dates
            if df.index.name and "date" in str(df.index.name).lower():
                df = df.reset_index()
            # Sort newest-first by whichever date column exists
            for date_col in ("GradeDate", "Date", "date"):
                if date_col in df.columns:
                    try:
                        df = df.sort_values(date_col, ascending=False)
                    except Exception:
                        pass
                    break
            df = df.head(30)

            out = []
            for _, row in df.iterrows():
                # Date — yfinance uses GradeDate (newer) or Date (older)
                d = None
                for c in ("GradeDate", "Date", "date"):
                    if c in row and row[c] is not None:
                        try:
                            d = str(row[c])[:10]
                        except Exception:
                            pass
                        if d:
                            break
                firm = row.get("Firm") if "Firm" in row else None
                to_g = row.get("ToGrade") if "ToGrade" in row else row.get("To Grade") if "To Grade" in row else None
                from_g = row.get("FromGrade") if "FromGrade" in row else row.get("From Grade") if "From Grade" in row else None
                action = row.get("Action") if "Action" in row else None
                # Price targets — only present in newer upgrades_downgrades
                new_t = row.get("currentPriceTarget") if "currentPriceTarget" in row else None
                prior_t = row.get("priorPriceTarget") if "priorPriceTarget" in row else None
                pt_action = row.get("priceTargetAction") if "priceTargetAction" in row else None

                # Coerce targets to float (pandas can return NaN)
                try:
                    new_t = float(new_t) if new_t and str(new_t) != "nan" else None
                except Exception:
                    new_t = None
                try:
                    prior_t = float(prior_t) if prior_t and str(prior_t) != "nan" else None
                except Exception:
                    prior_t = None

                tgt_change_pct = None
                if new_t and prior_t and prior_t > 0:
                    tgt_change_pct = ((new_t - prior_t) / prior_t) * 100.0

                norm_to = _normalize_rating(to_g)
                norm_from = _normalize_rating(from_g)

                # Action class — combine the two yfinance signals
                # (Action="up/down" rating-class change, priceTargetAction="raised/lowered")
                action_class = _classify_action(action, from_g, to_g)
                # If only the price target changed and rating stayed flat,
                # classify as target_change so the UI doesn't claim "reiterate"
                if action_class == "reiterate" and tgt_change_pct is not None and abs(tgt_change_pct) > 1:
                    action_class = "target_change"

                out.append({
                    "date": d,
                    "firm": str(firm) if firm else None,
                    "analyst": None,  # yfinance doesn't expose analyst names
                    "action_raw": str(action) if action else None,
                    "action_class": action_class,
                    "prior_grade": norm_from,
                    "new_grade": norm_to,
                    "prior_target": prior_t,
                    "new_target": new_t,
                    "target_change_pct": round(tgt_change_pct, 2) if tgt_change_pct is not None else None,
                    "pt_action": str(pt_action) if pt_action else None,
                })
            return out
        except Exception as e:
            sys.stderr.write(f"[analyst] yfinance history error for {symbol}: {type(e).__name__}: {e}\n")
            return []

    def _fetch_yf_targets_fallback(self, symbol: str) -> dict | None:
        """yfinance Ticker.analyst_price_targets — fallback when Finnhub
        is unconfigured or doesn't cover the symbol."""
        if not _YF_OK:
            return None
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            try:
                tp = ticker.analyst_price_targets
            except Exception:
                tp = None
            if not isinstance(tp, dict):
                return None
            # yfinance returns: {'current': X, 'low': X, 'high': X, 'mean': X, 'median': X}
            mean = tp.get("mean")
            if not mean:
                return None
            return {
                "target_mean": mean,
                "target_high": tp.get("high"),
                "target_low": tp.get("low"),
                "target_median": tp.get("median"),
                "num_analysts": None,  # yfinance doesn't include count
                "last_updated": None,
            }
        except Exception:
            return None

    # ── Public method ─────────────────────────────────────────────────
    def get_analyst_data(self, symbol: str, current_price: float | None = None,
                         force_refresh: bool = False) -> dict:
        """Single-call API — returns a complete normalized payload for the
        frontend Analyst card.

        Schema:
          {
            "symbol": str,
            "data_available": bool,
            "source": "finnhub" | "yfinance" | "mixed" | "none",
            "current_price": float | None,
            "targets": {
              "mean", "high", "low", "median": float | None,
              "num_analysts": int | None,
              "last_updated": str | None,
              "upside_pct": float | None,         # vs current price
              "downside_to_low_pct": float | None,
              "upside_to_high_pct": float | None,
            },
            "consensus": {
              "score": float | None,              # 0-5 weighted
              "label": "Strong Buy" | "Buy" | ... | "—",
              "breakdown": {strong_buy, buy, hold, sell, strong_sell},
              "trend": "more_bullish" | "more_bearish" | "stable" | None,
            },
            "history": [up to 30 update rows, newest first],
            "verdict": {
              "tags": [list of pills like "fresh upgrade", "above avg target", ...],
              "covered_call_warnings": [list of warning strings],
              "intraday_signals": [list of signal strings],
            },
            "as_of": ISO timestamp of when this was fetched
          }
        """
        symbol = symbol.upper().strip()
        cache_key = f"{symbol}:{int(current_price * 100) if current_price else 'nope'}"
        if not force_refresh:
            hit = self._cache_get(cache_key)
            if hit is not None:
                return hit

        # ── Fetch ─────────────────────────────────────────────────────
        targets = None
        source = "none"
        if self.is_finnhub_configured():
            targets = self._fetch_finnhub_target(symbol)
            if targets:
                source = "finnhub"
        if not targets:
            targets = self._fetch_yf_targets_fallback(symbol)
            if targets:
                source = "yfinance" if source == "none" else "mixed"

        recs = None
        if self.is_finnhub_configured():
            recs = self._fetch_finnhub_recommendation(symbol)

        history = self._fetch_yf_history(symbol)
        if history:
            source = "mixed" if source != "none" else "yfinance"

        # ── Build payload ─────────────────────────────────────────────
        # Build consensus first so we can use its total analyst count as
        # a fallback when the targets block doesn't have its own count.
        # Finnhub's free /stock/price-target endpoint went paid in
        # 2024-2025 (returns 403 now), so the targets block will rarely
        # have num_analysts. /stock/recommendation is still free and
        # gives us the rating breakdown — total = sum of the 5 buckets.
        consensus_block = self._build_consensus_block(recs)
        targets_block = self._build_targets_block(targets, current_price)
        # Fill num_analysts from consensus if targets didn't provide it
        if not targets_block.get("num_analysts") and consensus_block.get("breakdown"):
            targets_block["num_analysts"] = consensus_block["breakdown"].get("total")
        # Yahoo's aggregate price-target block (high/low/mean) lags the
        # per-firm upgrades/downgrades feed, so the headline High could
        # read lower than a fresh target shown in the history rows below
        # it. Reconcile high/low (and fill mean when missing) from the
        # latest target per firm so the summary is consistent with the
        # list the user sees.
        targets_block = self._reconcile_targets_with_history(
            targets_block, history, current_price)

        payload = {
            "symbol": symbol,
            "data_available": bool(targets or history or recs),
            "source": source,
            "current_price": current_price,
            "targets": targets_block,
            "consensus": consensus_block,
            "history": history,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
        payload["verdict"] = self._build_verdict(payload)

        self._cache_set(cache_key, payload)
        return payload

    # ── Internal builders ─────────────────────────────────────────────
    def _build_targets_block(self, t: dict | None, current_price: float | None) -> dict:
        if not t:
            return {
                "mean": None, "high": None, "low": None, "median": None,
                "num_analysts": None, "last_updated": None,
                "upside_pct": None, "downside_to_low_pct": None, "upside_to_high_pct": None,
            }
        mean = t.get("target_mean")
        high = t.get("target_high")
        low = t.get("target_low")
        upside = ((mean - current_price) / current_price * 100) if (mean and current_price) else None
        upside_high = ((high - current_price) / current_price * 100) if (high and current_price) else None
        downside_low = ((low - current_price) / current_price * 100) if (low and current_price) else None
        return {
            "mean": mean,
            "high": high,
            "low": low,
            "median": t.get("target_median"),
            "num_analysts": t.get("num_analysts"),
            "last_updated": t.get("last_updated"),
            "upside_pct": upside,
            "upside_to_high_pct": upside_high,
            "downside_to_low_pct": downside_low,
        }

    def _reconcile_targets_with_history(self, tb: dict, history: list[dict] | None,
                                        current_price: float | None,
                                        days: int = 120) -> dict:
        """Pull each firm's most-recent target from the history feed (within
        `days`) and fold it into the high/low so the headline can't show a
        High below a target listed in the rows. Fills mean/num_analysts when
        the aggregate block is empty, and recomputes the derived upside %s."""
        if not history:
            return tb
        from datetime import date as _date
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
        latest_by_firm: dict[str, tuple[_date, float]] = {}
        for r in history:
            nt = r.get("new_target")
            d = r.get("date")
            if not isinstance(nt, (int, float)) or not nt or not d:
                continue
            try:
                rd = datetime.strptime(d[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            if rd < cutoff:
                continue
            firm = r.get("firm") or "?"
            prev = latest_by_firm.get(firm)
            if prev is None or rd >= prev[0]:
                latest_by_firm[firm] = (rd, float(nt))
        tgts = [v[1] for v in latest_by_firm.values()]
        if not tgts:
            return tb
        tb = dict(tb)
        hi, lo = max(tgts), min(tgts)
        tb["high"] = max(hi, tb["high"]) if tb.get("high") else hi
        tb["low"] = min(lo, tb["low"]) if tb.get("low") else lo
        if not tb.get("mean"):
            tb["mean"] = round(sum(tgts) / len(tgts), 2)
        if not tb.get("num_analysts"):
            tb["num_analysts"] = len(tgts)
        cp = current_price
        if cp:
            if tb.get("high"):
                tb["upside_to_high_pct"] = (tb["high"] - cp) / cp * 100
            if tb.get("low"):
                tb["downside_to_low_pct"] = (tb["low"] - cp) / cp * 100
            if tb.get("mean"):
                tb["upside_pct"] = (tb["mean"] - cp) / cp * 100
        return tb

    def _build_consensus_block(self, recs: list[dict] | None) -> dict:
        if not recs:
            return {"score": None, "label": "—", "breakdown": None, "trend": None}
        latest = recs[0]
        sb = latest["strong_buy"]; b = latest["buy"]; h = latest["hold"]
        s = latest["sell"]; ss = latest["strong_sell"]
        total = sb + b + h + s + ss
        if total == 0:
            return {"score": None, "label": "—", "breakdown": None, "trend": None}
        score = (sb*5 + b*4 + h*3 + s*2 + ss*1) / total
        # Trend: compare latest to month-3 if available
        trend = None
        if len(recs) >= 3:
            old = recs[2]
            old_total = old["strong_buy"] + old["buy"] + old["hold"] + old["sell"] + old["strong_sell"]
            if old_total > 0:
                old_score = (old["strong_buy"]*5 + old["buy"]*4 + old["hold"]*3 + old["sell"]*2 + old["strong_sell"]*1) / old_total
                if score > old_score + 0.15:
                    trend = "more_bullish"
                elif score < old_score - 0.15:
                    trend = "more_bearish"
                else:
                    trend = "stable"
        return {
            "score": round(score, 2),
            "label": _consensus_label_from_score(score),
            "breakdown": {
                "strong_buy": sb, "buy": b, "hold": h,
                "sell": s, "strong_sell": ss, "total": total,
            },
            "trend": trend,
        }

    def _build_verdict(self, payload: dict) -> dict:
        """Convert raw analyst data into trading-context tags and warnings.

        The dashboard supports BOTH selling covered calls (CC) and selling
        cash-secured puts (CSP). Most analyst signals affect each strategy
        differently — sometimes oppositely.

        Examples:
          - "Trading above average target" hurts CC sellers (caps upside)
            but is FAVORABLE for CSP sellers (stock farther from put strike).
          - "Fresh upgrade today" hurts CC sellers (rerating risk) but
            HELPS CSP sellers (bullish catalyst supports staying above put).
          - "Trend more bearish" hurts CSP sellers (assignment risk) but is
            slightly FAVORABLE for CC sellers (stock less likely to spike).

        We emit two separate warning lists, plus the legacy alias
        `covered_call_warnings` for backward compat with frontend code
        written before CSP support.

        Tags are short pills shown at top of the card.
        Intraday signals are independent of strategy direction.
        """
        tags: list[str] = []
        cc_warnings: list[str] = []   # selling covered calls
        csp_warnings: list[str] = []  # selling cash-secured puts
        intraday: list[str] = []

        targets = payload.get("targets") or {}
        consensus = payload.get("consensus") or {}
        history = payload.get("history") or []
        cp = payload.get("current_price")

        upside = targets.get("upside_pct")
        upside_to_high = targets.get("upside_to_high_pct")

        # Above/below average target
        if upside is not None:
            if upside < 0:
                tags.append("trading above average target")
                cc_warnings.append(
                    "Stock is above the average analyst target. Upside may already be priced in — "
                    "consider tighter strikes or skipping covered calls."
                )
                # For CSPs, being above target is generally NEUTRAL
                # (stock has cushion above your put strike) UNLESS the
                # stock is well above target where mean-reversion risk
                # could drag it back into your put strike zone.
                if upside < -10:
                    csp_warnings.append(
                        "Stock is well above the average analyst target. Mean-reversion risk could "
                        "drag price back toward your put strike — consider lower strike or wait."
                    )
            elif upside > 15:
                tags.append("trading well below average target")
                # Lots of analyst-implied upside is generally GOOD for
                # CSP sellers — gives the stock room to drift up away
                # from your put strike. Worth flagging as a positive.
                csp_warnings.append(
                    "Stock has significant analyst upside. Bullish backdrop favors short put "
                    "premium-collection if you're comfortable owning at the strike."
                )
            else:
                tags.append("trading below average target")

        # Far above the highest target — strongest overextension signal
        if upside_to_high is not None and upside_to_high < -5:
            tags.append("far above highest target")
            cc_warnings.append(
                "Stock is more than 5% above the HIGHEST analyst target — possible overextension. "
                "Higher chance of mean-reversion (good if you sold calls, bad if assigned long stock)."
            )
            csp_warnings.append(
                "Stock is more than 5% above the HIGHEST analyst target — possible overextension. "
                "Mean-reversion risk could drop price toward your put strike. Caution on selling puts here."
            )

        # Consensus tilt
        c_label = consensus.get("label")
        if c_label in ("Strong Buy", "Buy"):
            tags.append("bullish analyst support")
        elif c_label in ("Strong Sell", "Sell"):
            tags.append("bearish analyst pressure")
            csp_warnings.append(
                "Bearish consensus rating. Analysts as a group expect the stock to underperform — "
                "selling puts here means accepting assignment in a name analysts dislike."
            )

        c_trend = consensus.get("trend")
        if c_trend == "more_bullish":
            tags.append("analysts getting more bullish")
        elif c_trend == "more_bearish":
            tags.append("analysts getting more bearish")
            cc_warnings.append(
                "Analyst sentiment has turned more bearish over recent months. Downside risk may be rising."
            )
            csp_warnings.append(
                "Analyst sentiment has turned more bearish over recent months. Increased risk of "
                "stock dropping into your put strike — consider lower strike or skip."
            )

        # Recent activity classification — look at the last 14 days
        recent = []
        if history:
            today = datetime.now(timezone.utc).date()
            for h in history:
                try:
                    if h.get("date"):
                        d = datetime.fromisoformat(h["date"][:10]).date()
                        delta = (today - d).days
                        if 0 <= delta <= 14:
                            recent.append({**h, "days_ago": delta})
                except Exception:
                    continue

        upgrades = [r for r in recent if r.get("action_class") == "upgrade"]
        downgrades = [r for r in recent if r.get("action_class") == "downgrade"]
        initiations = [r for r in recent if r.get("action_class") == "initiate"]

        if upgrades:
            fresh = [u for u in upgrades if u.get("days_ago", 99) <= 1]
            if fresh:
                tags.append("fresh upgrade today")
                firm = fresh[0].get('firm') or 'an analyst'
                intraday.append(
                    f"Fresh upgrade from {firm} today. Possible bullish catalyst behind today's price action."
                )
                # For CC: bad — re-rating may push stock above strike
                cc_warnings.append(
                    "Fresh upgrade today — covered calls may cap upside if a re-rating is in progress. "
                    "Consider higher strike or wait."
                )
                # For CSP: good — bullish catalyst reduces assignment risk
                csp_warnings.append(
                    "Fresh upgrade today — bullish catalyst supports the stock staying above your put strike. "
                    "Favorable backdrop for selling puts (premium may compress from IV crush though)."
                )
            else:
                tags.append(f"recent upgrade ({len(upgrades)})")
        if downgrades:
            fresh = [d for d in downgrades if d.get("days_ago", 99) <= 1]
            if fresh:
                tags.append("fresh downgrade today")
                firm = fresh[0].get('firm') or 'an analyst'
                intraday.append(
                    f"Fresh downgrade from {firm} today. Possible bearish catalyst behind today's price action."
                )
                # For CC: a downgrade-driven dip can mean better premium
                # but also higher fundamental risk. Note both sides.
                cc_warnings.append(
                    "Fresh downgrade today — downside risk elevated, but premium may be richer due "
                    "to expanded IV. Weigh assignment-prevention vs premium-grab."
                )
                # For CSP: very bad — fresh downgrade increases chance
                # of stock dropping into and below the put strike
                csp_warnings.append(
                    "Fresh downgrade today — bearish catalyst increases chance of stock dropping into your "
                    "put strike. High assignment risk; consider skipping puts on this name today."
                )
            else:
                tags.append(f"recent downgrade ({len(downgrades)})")
        if initiations:
            tags.append(f"recent initiation ({len(initiations)})")

        if not recent:
            tags.append("no recent analyst catalyst")

        # Below average target with bullish upgrades = upside continuation
        if upside is not None and upside > 10 and upgrades:
            tags.append("upside continuation possible")

        return {
            "tags": tags[:8],   # cap to keep UI tidy
            # Strategy-specific warnings — frontend should pick the list
            # matching the user's current strategy mode.
            "call_warnings": cc_warnings,
            "put_warnings": csp_warnings,
            # Backward-compat alias — old code reads `covered_call_warnings`.
            # Will be removed in a future cleanup pass.
            "covered_call_warnings": cc_warnings,
            "intraday_signals": intraday,
            "recent_activity_count": len(recent),
            "fresh_upgrade": any(u.get("days_ago", 99) <= 1 for u in upgrades),
            "fresh_downgrade": any(d.get("days_ago", 99) <= 1 for d in downgrades),
        }


# ── Module-level singleton ─────────────────────────────────────────────
_CLIENT: AnalystClient | None = None
_CLIENT_LOCK = threading.Lock()


def get_client() -> AnalystClient:
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = AnalystClient()
    return _CLIENT
