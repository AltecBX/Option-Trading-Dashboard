"""whisper_sources.py — expectation-data source adapters for Priced-for-
Perfection (v3.68).

Collects whisper numbers and consensus/calendar data from external providers,
aggregates them with per-source labeling, and feeds the whisper slot the
perfection model was built for. NOTHING is ever inferred or manufactured: a
source that cannot be read reports WHY (no_data / blocked / unreachable /
layout_changed), and the manual, source-attributed entry path plus quick-open
links remain the fallback.

Provider reality (probed live, 2026-08-08, and re-checked by the fixtures):
  • Earnings Whispers — public, unauthenticated JSON the site's own pages
    call: /api/getstocksdata/{SYM} (pre-event: whisper, consensus EPS,
    consensus revenue, next earnings date, release-time slot, confirm date)
    and /api/epsdetails/{SYM} (the LAST reported event: actual vs estimate
    vs the whisper that stood for it, high/low estimate range). Extracted
    politely: 6h cache, ≥2s between requests per domain, 2 retries with
    backoff, strict validation; any shape change degrades to a labeled
    "layout_changed" status — never to garbage numbers.
  • WhisperNumber — the site's TLS endpoint does not verify from this
    deployment and its data sits behind registration. We do NOT disable
    TLS verification and we do NOT automate a logged-in session (that is
    credentialed scraping of subscription data). The adapter attempts a
    plain public read, reports "unreachable"/"no public data", and the
    quick-open link + manual attributed entry are the supported path.
  • Seeking Alpha — public endpoints answer with a PerimeterX anti-bot
    challenge (403). We do not build captcha/bot-detection evasion. The
    adapter reports "blocked"; SA's calendar/consensus content is already
    covered by the app's yfinance estimates. Quick-open link provided.

Point-in-time: every collected quote is appended (once per source per UTC
day) to data_dir/whisper_snaps/{SYM}.jsonl with its as-of stamp and the
event it was collected FOR. Reads for an event only accept snapshots dated
BEFORE that event — post-earnings data can never leak into a pre-earnings
view. The last-event whisper from epsdetails is stored as post-event
history for reaction classification and is never offered as an upcoming
whisper. Manual entries are tied to the upcoming event at entry time.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from perfection import _num

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 20
RETRIES = 2                 # on timeouts/5xx only — 4xx never retries
RATE_MIN_INTERVAL = 2.0     # seconds between requests to the same domain
TTL_SEC = 6 * 3600
CONSENSUS_CONFLICT_PCT = 15.0   # provider vs app consensus divergence flag
WHISPER_SANITY_PCT = 100.0      # |whisper − consensus| beyond this = parse garbage

QUICK_LINKS = {
    "earningswhispers": "https://www.earningswhispers.com/stocks/{sym}",
    "whispernumber": "https://www.whispernumber.com/quotes/{sym}",
    "seekingalpha": "https://seekingalpha.com/symbol/{sym}/earnings",
}
RELEASE_TIME_MAP = {1: "BMO", 2: "DMT", 3: "AMC"}

_LOCK = threading.RLock()
_CACHE: dict = {}
_LAST_REQ: dict = {}        # domain -> monotonic ts
_DATA_DIR: Path | None = None
_SESSION_FACTORY = None


def configure(data_dir, session_factory=None) -> None:
    global _DATA_DIR, _SESSION_FACTORY
    _DATA_DIR = Path(data_dir) / "whisper" if data_dir else None
    _SESSION_FACTORY = session_factory


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session():
    if _SESSION_FACTORY is not None:
        return _SESSION_FACTORY()
    import requests
    return requests.Session()


def _rate_wait(domain: str) -> None:
    with _LOCK:
        last = _LAST_REQ.get(domain, 0.0)
        wait = RATE_MIN_INTERVAL - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    with _LOCK:
        _LAST_REQ[domain] = time.monotonic()


def _get(url: str, referer: str | None = None, accept: str = "application/json"):
    """Polite fetch: rate-limited per domain, retried on transient failures.
    Returns (status_code, content_type, text) or raises the final error."""
    domain = url.split("/")[2]
    headers = {"User-Agent": UA, "Accept": accept}
    if referer:
        headers["Referer"] = referer
    last_exc = None
    for attempt in range(RETRIES + 1):
        _rate_wait(domain)
        try:
            s = _session()
            r = s.get(url, headers=headers, timeout=TIMEOUT)
            if r.status_code >= 500 and attempt < RETRIES:
                time.sleep(1.5 * (attempt + 1))
                continue
            return r.status_code, r.headers.get("Content-Type", ""), r.text
        except Exception as exc:  # noqa: BLE001 — timeout/conn/SSL
            last_exc = exc
            if attempt < RETRIES:
                time.sleep(1.5 * (attempt + 1))
    raise last_exc


def _cache_get(key):
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < TTL_SEC:
            return hit[1]
    return None


def _cache_set(key, value):
    with _LOCK:
        _CACHE[key] = (time.time(), value)


# ─────────────────────────── provider adapters ─────────────────────────────
# Each returns {"status": "ok"|..., "quote": {...}|None}. quote fields:
#   source, kind ("provider_whisper"|"community_estimate"|"consensus_only"),
#   whisper_eps, whisper_revenue, consensus_eps, consensus_revenue,
#   earnings_date, session, confirmed, range, contributor_count,
#   source_url, asof

def fetch_earningswhispers(symbol: str) -> dict:
    sym = symbol.upper().strip()
    key = f"ew:{sym}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    url = f"https://www.earningswhispers.com/api/getstocksdata/{sym}"
    referer = QUICK_LINKS["earningswhispers"].format(sym=sym)
    try:
        status, ctype, text = _get(url, referer=referer)
    except Exception as exc:  # noqa: BLE001
        out = {"status": "unreachable", "error": str(exc)[:120], "quote": None}
        _cache_set(key, out)
        return out
    if status == 204 or (status == 200 and not text.strip()):
        out = {"status": "no_data", "quote": None}
        _cache_set(key, out)
        return out
    if status != 200:
        out = {"status": f"http_{status}", "quote": None}
        _cache_set(key, out)
        return out
    if "json" not in ctype.lower():
        # The endpoint answered with HTML — the site's shape changed.
        out = {"status": "layout_changed", "quote": None}
        _cache_set(key, out)
        return out
    try:
        d = json.loads(text)
    except Exception:
        out = {"status": "layout_changed", "quote": None}
        _cache_set(key, out)
        return out
    if not isinstance(d, dict) or (d.get("ticker") or "").upper() != sym:
        out = {"status": "layout_changed", "quote": None}
        _cache_set(key, out)
        return out
    consensus = _num(d.get("consensusEst"))
    whisper = _num(d.get("whisper"))
    # Validation: a whisper wildly away from the same provider's own
    # consensus is parse garbage, not information.
    if whisper is not None and consensus is not None and consensus != 0 \
            and abs(whisper - consensus) / abs(consensus) * 100.0 > WHISPER_SANITY_PCT:
        whisper = None
        validation_note = "whisper failed sanity check vs provider consensus — discarded"
    else:
        validation_note = None
    ed = None
    try:
        raw = d.get("nextEPSDate")
        if raw:
            ed = str(raw)[:10]
            date.fromisoformat(ed)          # must parse or we drop it
    except Exception:
        ed = None
    quote = {
        "source": "earningswhispers",
        "source_name": "Earnings Whispers",
        "kind": "provider_whisper" if whisper is not None else "consensus_only",
        "whisper_eps": whisper,
        "whisper_revenue": None,            # not provided pre-event by this endpoint
        "consensus_eps": consensus,
        "consensus_revenue": _num(d.get("revenueEst")),
        "earnings_date": ed,
        "session": RELEASE_TIME_MAP.get(d.get("releaseTime"), "unknown"),
        "confirmed": bool(d.get("confirmDate")),
        "range": None,
        "contributor_count": None,
        "source_url": referer,
        "asof": _now_iso(),
        "validation_note": validation_note,
    }
    out = {"status": "ok", "quote": quote}
    _cache_set(key, out)
    return out


def fetch_ew_last_event(symbol: str) -> dict:
    """POST-EVENT history from /api/epsdetails: the last reported quarter's
    actual vs estimate vs the whisper that stood for it (+ high/low range).
    Used ONLY to classify that past event — never as an upcoming whisper."""
    sym = symbol.upper().strip()
    key = f"ew_last:{sym}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    url = f"https://www.earningswhispers.com/api/epsdetails/{sym}"
    try:
        status, ctype, text = _get(url, referer=QUICK_LINKS["earningswhispers"].format(sym=sym))
    except Exception as exc:  # noqa: BLE001
        out = {"status": "unreachable", "error": str(exc)[:120], "event": None}
        _cache_set(key, out)
        return out
    if status != 200 or "json" not in ctype.lower():
        out = {"status": "no_data" if status == 204 else "layout_changed", "event": None}
        _cache_set(key, out)
        return out
    try:
        d = json.loads(text)
        assert isinstance(d, dict) and (d.get("ticker") or "").upper() == sym
    except Exception:
        out = {"status": "layout_changed", "event": None}
        _cache_set(key, out)
        return out
    lo, hi = _num(d.get("lowEstimate")), _num(d.get("highEstimate"))
    out = {"status": "ok", "event": {
        "source": "earningswhispers",
        "event_datetime": str(d.get("epsDate") or "")[:19] or None,
        "event_date": str(d.get("epsDate") or "")[:10] or None,
        "eps_actual": _num(d.get("eps")),
        "consensus_eps": _num(d.get("estimate")),
        "whisper_eps": _num(d.get("whisper")),
        "estimate_range": [lo, hi] if (lo is not None and hi is not None) else None,
        "revenue_actual": _num(d.get("revenue")),
        "consensus_revenue": _num(d.get("revenueEstimate")),
        "asof": _now_iso(),
    }}
    _cache_set(key, out)
    return out


def fetch_whispernumber(symbol: str) -> dict:
    """WhisperNumber.com: attempted plain public read with normal TLS
    verification. Their data sits behind registration and the endpoint does
    not verify from this deployment — we never disable verification and
    never automate a logged-in session. Expect 'unreachable'/'no_public_data';
    the quick-open link + manual attributed entry are the supported path."""
    sym = symbol.upper().strip()
    key = f"wn:{sym}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    url = QUICK_LINKS["whispernumber"].format(sym=sym)
    try:
        status, ctype, text = _get(url, accept="text/html")
    except Exception as exc:  # noqa: BLE001
        out = {"status": "unreachable", "error": str(exc)[:120], "quote": None}
        _cache_set(key, out)
        return out
    if status != 200:
        out = {"status": f"http_{status}", "quote": None}
        _cache_set(key, out)
        return out
    # Best-effort public-page parse; anything ambiguous → no data, never a guess.
    m = re.search(r'whisper\s*(?:number)?[^$\-\d]{0,40}\$?\s*(-?\d+\.\d{1,2})', text, re.I)
    if not m:
        out = {"status": "no_public_data", "quote": None}
        _cache_set(key, out)
        return out
    out = {"status": "ok", "quote": {
        "source": "whispernumber", "source_name": "WhisperNumber",
        "kind": "community_estimate",
        "whisper_eps": _num(m.group(1)), "whisper_revenue": None,
        "consensus_eps": None, "consensus_revenue": None,
        "earnings_date": None, "session": "unknown", "confirmed": False,
        "range": None, "contributor_count": None,
        "source_url": url, "asof": _now_iso(), "validation_note": None,
    }}
    _cache_set(key, out)
    return out


def fetch_seekingalpha(symbol: str) -> dict:
    """Seeking Alpha: public endpoints sit behind a PerimeterX anti-bot
    challenge. We do not evade bot detection — a challenge answer is
    reported as 'blocked'. (Its calendar/consensus content is already
    covered by the app's yfinance estimates.)"""
    sym = symbol.upper().strip()
    key = f"sa:{sym}"
    hit = _cache_get(key)
    if hit is not None:
        return hit
    url = f"https://seekingalpha.com/api/v3/symbols/{sym.lower()}/earnings_estimates"
    try:
        status, ctype, text = _get(url)
    except Exception as exc:  # noqa: BLE001
        out = {"status": "unreachable", "error": str(exc)[:120], "quote": None}
        _cache_set(key, out)
        return out
    if status == 403 or "captcha" in text[:2000].lower() or "px-captcha" in text[:2000]:
        out = {"status": "blocked", "quote": None}
        _cache_set(key, out)
        return out
    if status != 200 or "json" not in ctype.lower():
        out = {"status": f"http_{status}" if status != 200 else "layout_changed", "quote": None}
        _cache_set(key, out)
        return out
    try:
        d = json.loads(text)
        rows = d.get("data") or []
        est = rows[0].get("attributes", {}) if rows else {}
        eps = _num(est.get("eps_normalized_actual") or est.get("eps_estimate") or est.get("consensus_eps"))
        rev = _num(est.get("revenue_estimate") or est.get("consensus_revenue"))
        if eps is None and rev is None:
            out = {"status": "no_data", "quote": None}
        else:
            out = {"status": "ok", "quote": {
                "source": "seekingalpha", "source_name": "Seeking Alpha",
                "kind": "consensus_only",        # SA publishes consensus, not whispers
                "whisper_eps": None, "whisper_revenue": None,
                "consensus_eps": eps, "consensus_revenue": rev,
                "earnings_date": None, "session": "unknown", "confirmed": False,
                "range": None, "contributor_count": None,
                "source_url": QUICK_LINKS["seekingalpha"].format(sym=sym),
                "asof": _now_iso(), "validation_note": None,
            }}
    except Exception:
        out = {"status": "layout_changed", "quote": None}
    _cache_set(key, out)
    return out


ADAPTERS = {
    "earningswhispers": fetch_earningswhispers,
    "whispernumber": fetch_whispernumber,
    "seekingalpha": fetch_seekingalpha,
}


# ───────────────────── manual entries + point-in-time store ────────────────

def _manual_path() -> Path | None:
    if _DATA_DIR is None:
        return None
    return _DATA_DIR / "manual_entries.json"


def _load_manual() -> dict:
    p = _manual_path()
    if p is None or not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def add_manual(symbol: str, source: str, eps=None, revenue=None,
               url: str | None = None, note: str | None = None,
               next_earnings: str | None = None) -> dict:
    """Store a user-supplied, source-attributed expectation. Tied to the
    upcoming event at entry time; retained forever (point-in-time)."""
    sym = symbol.upper().strip()
    eps_v, rev_v = _num(eps), _num(revenue)
    if eps_v is None and rev_v is None:
        return {"ok": False, "error": "eps or revenue required"}
    if not (source or "").strip():
        return {"ok": False, "error": "source attribution required"}
    entry = {
        "asof": _now_iso(), "source": str(source).strip()[:80],
        "kind": "user_supplied",
        "whisper_eps": eps_v, "whisper_revenue": rev_v,
        "source_url": (str(url).strip()[:300] or None) if url else None,
        "note": (str(note).strip()[:300] or None) if note else None,
        "next_earnings": next_earnings,
    }
    p = _manual_path()
    if p is None:
        return {"ok": False, "error": "storage not configured"}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = _load_manual()
        data.setdefault(sym, []).append(entry)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False))
        tmp.replace(p)
        return {"ok": True, "entry": entry}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:120]}


def manual_for_event(symbol: str, next_earnings: str | None) -> list[dict]:
    """Entries that belong to THIS upcoming event: tied to it at entry time,
    and entered before it happened. Old-event and post-event entries never
    leak forward."""
    rows = _load_manual().get(symbol.upper().strip()) or []
    out = []
    for e in rows:
        if next_earnings and e.get("next_earnings") and e["next_earnings"] != next_earnings:
            continue
        if next_earnings and str(e.get("asof", ""))[:10] > next_earnings:
            continue
        out.append(e)
    return out


def _snap_dir() -> Path | None:
    if _DATA_DIR is None:
        return None
    return _DATA_DIR / "snaps"


def _store_snapshot(symbol: str, quote: dict) -> None:
    """Append-only, once per source per UTC day. Never rewritten."""
    d = _snap_dir()
    if d is None or not quote:
        return
    try:
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{symbol.upper()}.jsonl"
        today = date.today().isoformat()
        if p.exists():
            for line in p.read_text().splitlines():
                try:
                    r = json.loads(line)
                    if r.get("date") == today and r.get("source") == quote.get("source"):
                        return
                except Exception:
                    continue
        rec = {"date": today, "asof": quote.get("asof"), "source": quote.get("source"),
               "kind": quote.get("kind"), "whisper_eps": quote.get("whisper_eps"),
               "consensus_eps": quote.get("consensus_eps"),
               "consensus_revenue": quote.get("consensus_revenue"),
               "for_earnings": quote.get("earnings_date")}
        with p.open("a") as f:
            f.write(json.dumps(rec, separators=(",", ":"), allow_nan=False) + "\n")
    except Exception:
        pass


def whisper_for_event(symbol: str, event_date: str) -> dict | None:
    """The last whisper snapshot recorded STRICTLY BEFORE an event (leakage
    guard) and collected FOR that event. None when we hold none — the past
    is never backfilled."""
    d = _snap_dir()
    if d is None:
        return None
    p = d / f"{symbol.upper()}.jsonl"
    if not p.exists():
        return None
    best = None
    try:
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("whisper_eps") is None:
                continue
            if r.get("for_earnings") and r["for_earnings"] != event_date:
                continue
            if str(r.get("date", "")) >= str(event_date):
                continue                       # post/at-event snapshot: excluded
            if best is None or r["date"] > best["date"]:
                best = r
    except Exception:
        return None
    return best


# ─────────────────────────────── aggregation ───────────────────────────────

def _median(vals):
    xs = sorted(v for v in (_num(x) for x in vals) if v is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def collect(symbol: str, consensus_eps=None, consensus_revenue=None,
            next_earnings: str | None = None, adapters: dict | None = None) -> dict:
    """Run every adapter + manual entries → the model's whisper input and the
    UI panel. `adapters` is injectable for tests."""
    sym = symbol.upper().strip()
    if os.environ.get("JERRY_NO_NET") == "1":
        return {
            "model_input": {"available": False,
                            "note": "network disabled (JERRY_NO_NET) — no sources queried"},
            "panel": {"sources": [], "statuses": {}, "quick_links": _links(sym),
                      "manual_entries": [], "note": "network disabled"},
        }
    statuses: dict = {}
    quotes: list[dict] = []
    for name, fn in (adapters or ADAPTERS).items():
        try:
            res = fn(sym)
        except Exception as exc:  # noqa: BLE001
            res = {"status": "error", "error": str(exc)[:120], "quote": None}
        statuses[name] = {k: v for k, v in res.items() if k != "quote"}
        q = res.get("quote")
        if q:
            quotes.append(q)
            _store_snapshot(sym, q)

    manual = manual_for_event(sym, next_earnings)
    for e in manual:
        quotes.append({
            "source": f"user:{e['source']}", "source_name": f"You (from {e['source']})",
            "kind": "user_supplied",
            "whisper_eps": e.get("whisper_eps"), "whisper_revenue": e.get("whisper_revenue"),
            "consensus_eps": None, "consensus_revenue": None,
            "earnings_date": e.get("next_earnings"), "session": "unknown",
            "confirmed": False, "range": None, "contributor_count": 1,
            "source_url": e.get("source_url"), "asof": e.get("asof"),
            "validation_note": None, "note": e.get("note"),
        })

    cands = [q for q in quotes if _num(q.get("whisper_eps")) is not None]
    med = _median([q["whisper_eps"] for q in cands])
    rev_cands = [q for q in quotes if _num(q.get("whisper_revenue")) is not None]
    med_rev = _median([q["whisper_revenue"] for q in rev_cands])

    # Consensus conflict check: providers may carry a different consensus
    # than the app's published one (different vintage). Surface it — and if
    # they diverge badly, compute the headline gap against the SAME
    # provider's pair instead of mixing sources.
    conflicts = []
    provider_pairs = []
    for q in quotes:
        pc = _num(q.get("consensus_eps"))
        if pc is not None and _num(consensus_eps) is not None and consensus_eps != 0:
            div = abs(pc - consensus_eps) / abs(consensus_eps) * 100.0
            if div > CONSENSUS_CONFLICT_PCT:
                conflicts.append(f"{q['source_name']} consensus ${pc:.2f} differs "
                                 f"{div:.0f}% from the app's published ${consensus_eps:.2f} "
                                 f"(different vintage/quarter?)")
        if _num(q.get("whisper_eps")) is not None and pc is not None and pc != 0:
            provider_pairs.append((q["whisper_eps"] - pc) / abs(pc) * 100.0)

    eps_gap = None
    gap_basis = None
    if med is not None:
        base = _num(consensus_eps)
        if base and not conflicts:
            eps_gap = (med - base) / abs(base) * 100.0
            gap_basis = "median whisper vs app published consensus"
        elif provider_pairs:
            eps_gap = _median(provider_pairs)
            gap_basis = "whisper vs the SAME provider's own consensus (published-consensus vintage conflict)"
        elif base:
            eps_gap = (med - base) / abs(base) * 100.0
            gap_basis = "median whisper vs app published consensus"
    rev_gap = None
    if med_rev is not None and _num(consensus_revenue):
        rev_gap = (med_rev - consensus_revenue) / consensus_revenue * 100.0

    # Confidence ladder (identity, freshness, dispersion, count):
    #   provider whisper, fresh (≤7d)                    → high
    #   provider whisper stale, or user entry WITH url   → medium
    #   only user entries without attribution links      → low
    #   candidate dispersion >10% caps at medium, >20% → low.
    conf = None
    if cands:
        provider_fresh = any(
            q["kind"] == "provider_whisper" and _age_days(q.get("asof")) <= 7 for q in cands)
        provider_any = any(q["kind"] == "provider_whisper" for q in cands)
        user_with_url = any(q["kind"] == "user_supplied" and q.get("source_url") for q in cands)
        conf = "high" if provider_fresh else ("medium" if (provider_any or user_with_url) else "low")
        if med and len(cands) > 1:
            spread = max(q["whisper_eps"] for q in cands) - min(q["whisper_eps"] for q in cands)
            disp = abs(spread / med) * 100.0 if med else 0.0
            if disp > 20:
                conf = "low"
            elif disp > 10 and conf == "high":
                conf = "medium"

    rng = None
    if len(cands) >= 2:
        rng = [min(q["whisper_eps"] for q in cands), max(q["whisper_eps"] for q in cands)]

    available = bool(cands)
    model_input = {
        "available": available,
        "confidence": conf,
        "eps_gap_pct": round(eps_gap, 2) if eps_gap is not None else None,
        "revenue_gap_pct": round(rev_gap, 2) if rev_gap is not None else None,
        "median_eps": round(med, 4) if med is not None else None,
        "range": rng,
        "sources": [q["source_name"] for q in cands],
        "source_count": len(cands),
        "asof": max((q.get("asof") or "" for q in cands), default=None) or None,
        "note": None if available else
        "No reliable whisper estimate available — automated sources returned none "
        "and no user-supplied entry exists for this event. Nothing is inferred.",
        "gap_basis": gap_basis,
    }
    panel = {
        "sources": [
            {k: q.get(k) for k in ("source", "source_name", "kind", "whisper_eps",
                                   "whisper_revenue", "consensus_eps", "consensus_revenue",
                                   "earnings_date", "session", "confirmed", "range",
                                   "contributor_count", "source_url", "asof",
                                   "validation_note", "note")}
            for q in quotes],
        "statuses": statuses,
        "quick_links": _links(sym),
        "manual_entries": manual,
        "conflicts": conflicts,
        "median_eps": model_input["median_eps"],
        "eps_gap_pct": model_input["eps_gap_pct"],
        "revenue_gap_pct": model_input["revenue_gap_pct"],
        "confidence": conf,
        "gap_basis": gap_basis,
        "kind_legend": {
            "provider_whisper": "the provider's own published whisper number",
            "community_estimate": "aggregated individual-investor expectation",
            "consensus_only": "published sell-side consensus (no whisper)",
            "user_supplied": "entered by you, with the source you read it from",
        },
    }
    return {"model_input": model_input, "panel": panel}


def _age_days(asof: str | None) -> float:
    try:
        dt = datetime.fromisoformat(str(asof).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    except Exception:
        return 999.0


def _links(sym: str) -> dict:
    return {k: v.format(sym=sym) for k, v in QUICK_LINKS.items()}
