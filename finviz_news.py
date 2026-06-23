"""finviz_news.py — pulls the user's Finviz Elite news feed via the Elite
news export (CSV) and normalizes it to a simple list for the top-of-app
scrolling ticker.

Auth: reads FINVIZ_AUTH_TOKEN from the environment (the &auth=... value from
your Finviz Elite export URLs). Optionally FINVIZ_NEWS_V selects the news
view (defaults to "3"). Stdlib-only so the import never breaks the app;
results are cached in-process for a minute so the ticker can poll cheaply.
"""
from __future__ import annotations

import csv
import io
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone

_CACHE: dict = {"ts": 0.0, "data": None}
_LOCK = threading.Lock()
_TTL = 60.0  # seconds — Finviz news doesn't move faster than this matters

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _token() -> str:
    return os.environ.get("FINVIZ_AUTH_TOKEN", "").strip()


def configured() -> bool:
    return bool(_token())


def _fetch_csv(view: str, token: str) -> str:
    url = f"https://elite.finviz.com/news_export.ashx?v={view}&auth={token}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept": "text/csv,*/*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def get_news(limit: int = 60) -> dict:
    """Returns {configured, error, count, items[], as_of}. Each item:
    {title, url, source, date, category, ticker}. Never raises."""
    token = _token()
    if not token:
        return {"configured": False, "error": "FINVIZ_AUTH_TOKEN not set",
                "count": 0, "items": []}

    now = time.time()
    with _LOCK:
        cached = _CACHE["data"]
        if cached is not None and (now - _CACHE["ts"]) < _TTL:
            return cached

    view = (os.environ.get("FINVIZ_NEWS_V", "3").strip() or "3")
    try:
        text = _fetch_csv(view, token)
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "error": f"fetch failed: {exc}",
                "count": 0, "items": []}

    # A bad/expired token gets the Elite login HTML instead of CSV.
    if text.lstrip().startswith("<"):
        return {"configured": True,
                "error": "auth rejected (got HTML, not CSV) — check FINVIZ_AUTH_TOKEN",
                "count": 0, "items": []}

    items = []
    try:
        for row in csv.DictReader(io.StringIO(text)):
            g = {(k or "").strip().lower(): (v.strip() if isinstance(v, str) else v)
                 for k, v in row.items()}
            title = g.get("title") or g.get("headline")
            url = g.get("url") or g.get("link")
            if not title or not url:
                continue
            items.append({
                "title": title,
                "url": url,
                "source": g.get("source") or "",
                "date": g.get("date") or "",
                "category": g.get("category") or "",
                "ticker": g.get("ticker") or "",
            })
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "error": f"parse failed: {exc}",
                "count": 0, "items": []}

    items = items[:max(1, limit)]
    out = {"configured": True, "error": None, "count": len(items),
           "items": items, "as_of": datetime.now(timezone.utc).isoformat()}
    with _LOCK:
        _CACHE["ts"] = now
        _CACHE["data"] = out
    return out
