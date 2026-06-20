"""news.py — aggregated per-ticker news from free sources.

Pulls recent headlines for one ticker from Yahoo Finance (via yfinance, no
key) and, when a Finnhub key is configured, Finnhub company news — then
merges, de-dupes by headline, and sorts newest-first. A TradingView/Finviz-
style feed for whatever symbol is selected.

Free only. Short TTL cache so flipping tabs / tickers doesn't re-hit the
upstreams every render.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = timezone.utc

try:
    import yfinance as yf
    _YF = True
except Exception:
    _YF = False

FINNHUB_BASE = "https://finnhub.io/api/v1"
_TTL = 300  # seconds
_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.RLock()


def _now() -> float:
    return time.time()


def _age(ts: float) -> str:
    if not ts:
        return ""
    d = max(0.0, _now() - ts)
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)}m ago"
    if d < 86400:
        return f"{int(d // 3600)}h ago"
    return f"{int(d // 86400)}d ago"


def _iso(ts: float):
    try:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()
    except Exception:
        return None


def _parse_iso(s: str):
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def _et_labels(ts: float):
    """(M-D-YYYY, h:mmam/pm, YYYY-MM-DD) in US Eastern for one timestamp."""
    if not ts:
        return None, None, None
    try:
        dt = datetime.fromtimestamp(ts, _ET)
        date_label = f"{dt.month}-{dt.day}-{dt.year}"
        time_label = dt.strftime("%I:%M%p").lstrip("0").lower()
        return date_label, time_label, dt.strftime("%Y-%m-%d")
    except Exception:
        return None, None, None


def _day_changes(symbol: str) -> dict:
    """Date (YYYY-MM-DD) → that day's % price change, so each headline can
    carry the stock's move on its day (Finviz-style tag)."""
    if not _YF:
        return {}
    try:
        h = yf.Ticker(symbol).history(period="3mo", interval="1d")
        if h is None or h.empty:
            return {}
        out, prev = {}, None
        for idx, c in h["Close"].items():
            c = float(c)
            if c != c:  # NaN
                continue
            if prev is not None and prev > 0:
                out[idx.strftime("%Y-%m-%d")] = round((c - prev) / prev * 100.0, 2)
            prev = c
        return out
    except Exception:
        return {}


def _yf_news(symbol: str) -> list[dict]:
    """Yahoo Finance news. Tolerates both the legacy flat shape and the
    newer {'content': {...}} shape yfinance returns."""
    if not _YF:
        return []
    try:
        items = yf.Ticker(symbol).news or []
    except Exception:
        return []
    out = []
    for it in items:
        try:
            c = it.get("content") if isinstance(it.get("content"), dict) else None
            if c:
                title = (c.get("title") or "").strip()
                prov = (c.get("provider") or {}).get("displayName") or "Yahoo Finance"
                url = (((c.get("clickThroughUrl") or {}) or {}).get("url")
                       or ((c.get("canonicalUrl") or {}) or {}).get("url"))
                ts = _parse_iso(c.get("pubDate") or c.get("displayTime") or "")
                summary = c.get("summary") or c.get("description") or ""
            else:
                title = (it.get("title") or "").strip()
                prov = it.get("publisher") or "Yahoo Finance"
                url = it.get("link")
                ts = it.get("providerPublishTime")
                summary = ""
            if not title:
                continue
            out.append({
                "title": title, "source": prov, "url": url,
                "ts": float(ts) if ts else 0.0,
                "summary": (summary or "").strip()[:300], "origin": "Yahoo",
            })
        except Exception:
            continue
    return out


def _finnhub_news(symbol: str) -> list[dict]:
    """Finnhub company news (last ~7 days). Adds extra sources/outlets."""
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        return []
    today = datetime.now(timezone.utc).date()
    frm = (today - timedelta(days=7)).isoformat()
    url = (f"{FINNHUB_BASE}/company-news?symbol={urllib.parse.quote(symbol)}"
           f"&from={frm}&to={today.isoformat()}&token={key}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "jerry-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            items = json.loads(resp.read().decode("utf-8")) or []
    except Exception:
        return []
    out = []
    for it in items:
        try:
            title = (it.get("headline") or "").strip()
            if not title:
                continue
            out.append({
                "title": title, "source": it.get("source") or "Finnhub",
                "url": it.get("url"), "ts": float(it.get("datetime") or 0),
                "summary": (it.get("summary") or "").strip()[:300],
                "image": it.get("image") or None, "origin": "Finnhub",
            })
        except Exception:
            continue
    return out


def _key(title: str) -> str:
    return "".join(ch.lower() for ch in title if ch.isalnum())[:80]


def _build(symbol: str, limit: int) -> dict:
    items = _yf_news(symbol) + _finnhub_news(symbol)
    seen: dict[str, dict] = {}
    for it in items:
        k = _key(it["title"])
        if not k:
            continue
        cur = seen.get(k)
        if cur is None:
            seen[k] = it
            continue
        # Keep the richer / newer entry; prefer one that has a URL.
        if (it.get("url") and not cur.get("url")) or (it["ts"] or 0) > (cur["ts"] or 0):
            seen[k] = it
    merged = sorted(seen.values(), key=lambda x: -(x["ts"] or 0))[:limit]
    changes = _day_changes(symbol)
    last_key = max(changes) if changes else None        # latest session we have
    for it in merged:
        ts = it["ts"] or 0.0
        it["published"] = _iso(ts)
        it["age"] = _age(ts)
        date_label, time_label, day_key = _et_labels(ts)
        it["date_label"] = date_label
        it["time_label"] = time_label
        # The stock's move on the headline's day. For the most recent
        # headlines whose session close isn't in daily history yet, fall
        # back to the latest available session (never to an older one).
        chg = None
        if day_key:
            if day_key in changes:
                chg = changes[day_key]
            elif last_key and day_key >= last_key:
                chg = changes[last_key]
        it["day_change"] = chg
        it.pop("ts", None)
    sources = sorted({it["source"] for it in merged if it.get("source")})
    return {"symbol": symbol, "count": len(merged), "items": merged,
            "sources": sources, "as_of": datetime.now(timezone.utc).isoformat()}


def get_news(symbol: str, limit: int = 40) -> dict:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"symbol": symbol, "error": "symbol required", "items": [], "sources": []}
    now = _now()
    with _LOCK:
        cached = _CACHE.get(symbol)
        if cached and now - cached[0] < _TTL:
            return cached[1]
    result = _build(symbol, limit)
    with _LOCK:
        _CACHE[symbol] = (now, result)
    return result
