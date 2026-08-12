"""ewhispers.py — the weekly Earnings Whispers calendar from @eWhispers on X
(v3.87).

Earnings Whispers posts a weekly earnings-calendar image on its public X
account every weekend, e.g. "#earnings for the week of August 10, 2026 …"
followed by the calendar picture and a cashtag list of the week's reporters.
This module finds the newest post covering the CURRENT trading week and
serves it to the dashboard's Earnings area — automatically, week after week,
without anyone editing a URL.

Sources, in fallback order (never a broken card):
  1. X API v2 recent search, when X_BEARER_TOKEN is set. One polite request
     every few hours; candidates are scored on several signals (author,
     image, week phrase, parsed week date, cashtag count, daily-post
     negatives) so small wording changes don't break detection. The image
     comes from the API's own media metadata (pbs.twimg.com) — the
     supported way to display it natively.
  2. The last successfully verified weekly post, from the on-disk cache.
  3. A manually supplied post URL (set in the UI or EWHISPERS_MANUAL_URL).
     Hydrated through X's public oEmbed endpoint (publish.x.com/oembed —
     official, unauthenticated, returns the post text + author), so even
     with no API credentials the card shows the right week, the tickers,
     and an official X embed of the calendar.
  4. A clean labeled unavailable state.

Boundaries: only content @eWhispers posts publicly on X is used. Nothing is
read from earningswhispers.com's paid pages, no HTML scraping of x.com, no
browser automation. Credentials stay server-side; responses carry a
credentials *boolean* only. JERRY_NO_NET=1 disables all network.

Week logic: the relevant week is Monday–Friday of today's week; on
Saturday/Sunday it is the COMING week (that's when the next calendar is
posted). A newer post never displaces the current week's calendar — posts
are stored under the week they announce, and selection is by week, not by
recency.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote as _urlquote

UA = "JerryTrade-Dashboard/3.87 (+earnings whispers weekly calendar card)"
TIMEOUT = 15
CHECK_INTERVAL_SEC = 4 * 3600      # serve cache instantly; look for a newer
                                   # post at most every 4 hours
MIN_CONFIDENCE = 0.60              # accept threshold for a scored candidate
KEEP_WEEKS = 8                     # small history, not an archive
X_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
OEMBED_URL = "https://publish.x.com/oembed"
ACCOUNT = "eWhispers"

_LOCK = threading.RLock()
_DATA_DIR: Path | None = None
_SESSION_FACTORY = None
_REFRESHING = False
_LAST_ATTEMPT_MONO = 0.0           # monotonic stamp of the last refresh try,
                                   # so a failing provider is retried on the
                                   # same cadence as a healthy one, not per hit
_STATE: dict | None = None         # loaded lazily from disk


def configure(data_dir, session_factory=None) -> None:
    global _DATA_DIR, _SESSION_FACTORY, _STATE
    _DATA_DIR = Path(data_dir) / "ewhispers" if data_dir else None
    _SESSION_FACTORY = session_factory
    _STATE = None                  # re-load from the new location on next use


def _session():
    if _SESSION_FACTORY is not None:
        return _SESSION_FACTORY()
    import requests
    return requests.Session()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> date:
    """Patchable clock (test seam, same pattern as whisper_sources)."""
    return date.today()


def _log(msg: str) -> None:
    print(f"[ewhispers] {msg}", file=sys.stderr)


# ── Week math ───────────────────────────────────────────────────────────────

def trading_week(today: date | None = None) -> tuple[date, date]:
    """The relevant Mon–Fri earnings week. Mon–Fri → this week's Monday;
    Sat/Sun → the COMING Monday (the next calendar is already the one that
    matters, and that's when @eWhispers posts it)."""
    d = today or _today()
    wd = d.weekday()
    monday = d - timedelta(days=wd) if wd <= 4 else d + timedelta(days=7 - wd)
    return monday, monday + timedelta(days=4)


def week_label(week_start: str | None) -> str | None:
    try:
        d = date.fromisoformat(week_start)
        return f"Week of {d.strftime('%B')} {d.day}, {d.year}"
    except Exception:
        return None


_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
for _m, _i in list(_MONTHS.items()):
    _MONTHS[_m[:3]] = _i           # aug → 8; "sept" handled below
_MONTHS["sept"] = 9

# "week of August 10, 2026" / "week of Aug 10th" / "week beginning 8/10/26"
_WEEK_TEXT_RE = re.compile(
    r"week\s+(?:of|beginning|starting)\s+"
    r"(?:(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?"
    r"|(?P<m>\d{1,2})[/-](?P<d>\d{1,2}))"
    r"(?:[,\s]+(?P<year>\d{4}))?", re.I)


def parse_week(text: str, ref: date | None = None) -> str | None:
    """The Monday (ISO) of the week a post announces, or None. `ref` (the
    post's publish date, else today) resolves a missing year to the nearest
    candidate — so 'week of Jan 4' posted in late December lands forward."""
    m = _WEEK_TEXT_RE.search(text or "")
    if not m:
        return None
    ref = ref or _today()
    try:
        if m.group("mon"):
            month = _MONTHS.get(m.group("mon").lower())
            day = int(m.group("day"))
        else:
            month, day = int(m.group("m")), int(m.group("d"))
        if not month or not (1 <= day <= 31):
            return None
        if m.group("year"):
            d = date(int(m.group("year")), month, day)
        else:
            options = []
            for y in (ref.year - 1, ref.year, ref.year + 1):
                try:
                    options.append(date(y, month, day))
                except ValueError:
                    continue
            if not options:
                return None
            d = min(options, key=lambda x: abs((x - ref).days))
        monday = d - timedelta(days=d.weekday())   # any weekday → its Monday
        return monday.isoformat()
    except ValueError:
        return None


# ── Post-text helpers ───────────────────────────────────────────────────────

_CASHTAG_RE = re.compile(r"\$([A-Za-z][A-Za-z]{0,5}(?:[.\-][A-Za-z]{1,3})?)\b")


def extract_tickers(text: str, cap: int = 80) -> list[str]:
    """Cashtags from the post text — structured data, no OCR. Order kept,
    deduped, uppercased."""
    seen, out = set(), []
    for m in _CASHTAG_RE.finditer(text or ""):
        sym = m.group(1).upper()
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
        if len(out) >= cap:
            break
    return out


_PHRASE_STRONG = re.compile(r"(?:#?\s?earnings\s+for\s+the\s+week"
                            r"|most\s+anticipated\s+earnings)", re.I)
_PHRASE_DAILY = re.compile(r"\breports?\s+(?:#?earnings\s+)?(?:tonight|today|"
                           r"tomorrow|this\s+(?:morning|afternoon|evening)|"
                           r"after\s+the\s+(?:bell|close)|"
                           r"before\s+the\s+(?:bell|open))", re.I)


def score_post(text: str, *, has_image: bool, is_reply: bool = False,
               is_retweet: bool = False, published: date | None = None,
               ref_week: str | None = None) -> tuple[float, str | None, list[str]]:
    """(confidence 0–1, parsed week_start, reasons). Multiple additive
    signals so a single wording change can't zero the detection; hard
    rejects only for things a weekly calendar post can never be."""
    reasons: list[str] = []
    if is_retweet:
        return 0.0, None, ["repost — rejected"]
    if is_reply:
        return 0.0, None, ["reply — rejected"]
    if not has_image:
        return 0.0, None, ["no image — rejected"]
    t = text or ""
    score = 0.0
    wk = parse_week(t, published)
    if _PHRASE_STRONG.search(t):
        score += 0.35
        reasons.append("weekly-calendar phrase")
    elif re.search(r"earnings", t, re.I) and re.search(r"\bweek\b", t, re.I):
        score += 0.15
        reasons.append("earnings + week wording")
    if wk:
        score += 0.20
        reasons.append(f"announces week of {wk}")
        if ref_week and wk == ref_week:
            score += 0.25
            reasons.append("matches the current trading week")
        elif ref_week:
            try:
                gap = abs((date.fromisoformat(wk) - date.fromisoformat(ref_week)).days)
                if gap <= 7:
                    score += 0.10
                    reasons.append("adjacent week")
            except Exception:
                pass
    n_tags = len(extract_tickers(t, cap=12))
    if n_tags >= 5:
        score += 0.10
        reasons.append(f"lists {n_tags}+ reporters")
    if _PHRASE_DAILY.search(t):
        score -= 0.40
        reasons.append("daily-report wording — penalized")
    return max(0.0, min(1.0, round(score, 3))), wk, reasons


# ── Persistence ─────────────────────────────────────────────────────────────

def _state_path() -> Path | None:
    return (_DATA_DIR / "state.json") if _DATA_DIR else None


def _load_state() -> dict:
    global _STATE
    with _LOCK:
        if _STATE is not None:
            return _STATE
        st = {"weeks": {}, "manual": None, "manual_url": None,
              "last_checked": None, "last_status": None, "last_error": None}
        p = _state_path()
        if p and p.exists():
            try:
                disk = json.loads(p.read_text())
                if isinstance(disk, dict):
                    st.update({k: disk.get(k, st[k]) for k in st})
                    if not isinstance(st["weeks"], dict):
                        st["weeks"] = {}
            except Exception as exc:  # noqa: BLE001 — corrupt file ≠ dead card
                _log(f"state unreadable, starting fresh: {exc}")
        env_url = os.environ.get("EWHISPERS_MANUAL_URL", "").strip()
        if env_url and not st.get("manual_url"):
            ok, canon, _pid = _validate_post_url(env_url)
            if ok:
                st["manual_url"] = canon
        _STATE = st
        return st


def _save_state() -> None:
    p = _state_path()
    if p is None or _STATE is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_STATE, separators=(",", ":"), allow_nan=False))
        tmp.replace(p)
    except Exception as exc:  # noqa: BLE001
        _log(f"state save failed: {exc}")


def _prune_weeks(st: dict) -> None:
    keys = sorted(st["weeks"])
    for k in keys[:-KEEP_WEEKS]:
        st["weeks"].pop(k, None)


# ── URL validation / sanitization ───────────────────────────────────────────

_POST_URL_RE = re.compile(
    r"^https://(?:www\.|mobile\.)?(?:x|twitter)\.com/"
    r"(?P<user>eWhispers)/status(?:es)?/(?P<id>\d{5,25})"
    r"(?:/photo/\d+)?/?(?:[?#].*)?$", re.I)


def _validate_post_url(url: str) -> tuple[bool, str | None, str | None]:
    """(ok, canonical_url, post_id). Only @eWhispers status URLs are
    accepted — the card must never be pointed at arbitrary content."""
    m = _POST_URL_RE.match((url or "").strip())
    if not m:
        return False, None, None
    pid = m.group("id")
    return True, f"https://x.com/{ACCOUNT}/status/{pid}", pid


def _safe_image_url(url: str | None) -> str | None:
    """Only X's own media host is ever handed to the frontend."""
    if isinstance(url, str) and url.startswith("https://pbs.twimg.com/"):
        return url
    return None


def _display_image_url(url: str | None) -> str | None:
    """pbs media URLs accept a size variant; ask for 'large' so the calendar
    is legible. '…/abc.jpg' → '…/abc?format=jpg&name=large'."""
    url = _safe_image_url(url)
    if not url:
        return None
    m = re.match(r"^(https://pbs\.twimg\.com/media/[\w\-]+)\.(jpg|jpeg|png|webp)$", url)
    if m:
        fmt = "jpg" if m.group(2) == "jpeg" else m.group(2)
        return f"{m.group(1)}?format={fmt}&name=large"
    return url


# ── X API v2 (credentialed path) ────────────────────────────────────────────

def _bearer_token() -> str:
    # X_BEARER_TOKEN is this app's name; TWITTER_BEARER_TOKEN accepted as an
    # alias since many deployments already carry one.
    return (os.environ.get("X_BEARER_TOKEN", "").strip()
            or os.environ.get("TWITTER_BEARER_TOKEN", "").strip())


def _x_get(url: str) -> tuple[int, str]:
    s = _session()
    r = s.get(url, headers={"Authorization": f"Bearer {_bearer_token()}",
                            "User-Agent": UA}, timeout=TIMEOUT)
    return r.status_code, r.text


def _x_search_candidates() -> tuple[str, list[dict]]:
    """(status, candidates) from one recent-search call. The query is kept
    BROAD on purpose (just from:account + earnings + images); the precise
    weekly-calendar verification happens in score_post, so a wording change
    on their side degrades a score signal instead of emptying the query."""
    query = f'from:{ACCOUNT} earnings has:images -is:retweet -is:reply'
    url = (f"{X_SEARCH_URL}?query={_urlquote(query)}&max_results=50"
           "&tweet.fields=created_at,text,entities,referenced_tweets"
           "&expansions=attachments.media_keys"
           "&media.fields=url,preview_image_url,width,height,type")
    try:
        status, text = _x_get(url)
    except Exception as exc:  # noqa: BLE001
        return f"unreachable: {str(exc)[:80]}", []
    if status == 429:
        return "rate_limited", []
    if status in (401, 403):
        return "invalid_credentials", []
    if status != 200:
        return f"http_{status}", []
    try:
        d = json.loads(text)
        media = {m["media_key"]: m for m in (d.get("includes", {}).get("media") or [])
                 if isinstance(m, dict) and m.get("media_key")}
        out = []
        for tw in d.get("data") or []:
            keys = (tw.get("attachments") or {}).get("media_keys") or []
            photos = [media[k] for k in keys
                      if k in media and media[k].get("type") == "photo"]
            refs = tw.get("referenced_tweets") or []
            out.append({
                "id": str(tw.get("id") or ""),
                "text": tw.get("text") or "",
                "created_at": tw.get("created_at"),
                "photos": photos,
                "is_retweet": any(r.get("type") == "retweeted" for r in refs),
                "is_reply": any(r.get("type") == "replied_to" for r in refs),
            })
        return "ok", out
    except Exception as exc:  # noqa: BLE001
        return f"bad_payload: {str(exc)[:80]}", []


def _record_from_candidate(c: dict, confidence: float, wk: str) -> dict:
    photos = c.get("photos") or []
    primary = photos[0] if photos else {}
    created = (c.get("created_at") or "")[:19]
    return {
        "post_id": c["id"],
        "post_url": f"https://x.com/{ACCOUNT}/status/{c['id']}",
        "author": ACCOUNT,
        "text": c.get("text") or "",
        "published_at": created or None,
        "week_start": wk,
        "week_end": (date.fromisoformat(wk) + timedelta(days=4)).isoformat(),
        "image_url": _display_image_url(primary.get("url")),
        "image_width": primary.get("width"),
        "image_height": primary.get("height"),
        "images": [u for u in (_display_image_url(p.get("url")) for p in photos[1:])
                   if u],
        "tickers": extract_tickers(c.get("text") or ""),
        "confidence": confidence,
        "source": "x",
        "detected_at": _now_iso(),
    }


def _refresh_from_x() -> None:
    """One search → score every candidate → store each accepted post under
    the week it ANNOUNCES. Selection-by-week is what stops a newer post
    (next week's calendar, a daily list, a chart) from displacing the
    current week's card."""
    st = _load_state()
    ref_week = trading_week()[0].isoformat()
    status, cands = _x_search_candidates()
    with _LOCK:
        st["last_checked"] = _now_iso()
        st["last_status"] = status
        st["last_error"] = None if status == "ok" else status
    if status != "ok":
        _log(f"refresh: {status} (cache keeps serving the last verified post)")
        _save_state()
        return
    accepted = 0
    for c in cands:
        pub = None
        try:
            pub = date.fromisoformat((c.get("created_at") or "")[:10])
        except Exception:
            pub = None
        conf, wk, _reasons = score_post(
            c.get("text") or "", has_image=bool(c.get("photos")),
            is_reply=c.get("is_reply", False), is_retweet=c.get("is_retweet", False),
            published=pub, ref_week=ref_week)
        if conf < MIN_CONFIDENCE or not wk:
            continue
        rec = _record_from_candidate(c, conf, wk)
        with _LOCK:
            cur = st["weeks"].get(wk)
            # Duplicate weekly posts: keep the strongest; on a tie keep the
            # newer (a re-post usually supersedes a deleted original).
            if (cur is None or conf > (cur.get("confidence") or 0)
                    or (conf == (cur.get("confidence") or 0)
                        and (rec.get("published_at") or "") >= (cur.get("published_at") or ""))):
                st["weeks"][wk] = rec
                accepted += 1
    with _LOCK:
        _prune_weeks(st)
    _save_state()
    _log(f"refresh: ok — {len(cands)} candidates, {accepted} stored/updated, "
         f"weeks held: {sorted(st['weeks'])}")


# ── oEmbed (credential-free hydration for the manual URL) ───────────────────

_TAG_RE = re.compile(r"<[^>]+>")


def _fetch_oembed(post_url: str) -> dict | None:
    """publish.x.com/oembed — official, unauthenticated. Returns
    {text, author_ok, published_at} or None. The returned HTML is parsed for
    its text content only and is NEVER passed to the frontend."""
    url = (f"{OEMBED_URL}?url={_urlquote(post_url)}"
           "&omit_script=true&dnt=true&hide_thread=true")
    try:
        s = _session()
        r = s.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        if r.status_code != 200:
            _log(f"oembed http_{r.status_code} for {post_url}")
            return None
        d = json.loads(r.text)
    except Exception as exc:  # noqa: BLE001
        _log(f"oembed unreachable: {str(exc)[:80]}")
        return None
    author_url = str(d.get("author_url") or "")
    author_ok = author_url.rstrip("/").lower().endswith("/" + ACCOUNT.lower())
    html = str(d.get("html") or "")
    # Text lives in the <p> of the blockquote; the trailing "— Author (@handle)
    # Month D, Y" carries the publish date.
    text = _TAG_RE.sub(" ", html)
    text = re.sub(r"\s+", " ", text).strip()
    for ent, ch in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                    ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(ent, ch)
    pub = None
    m = re.search(r"\(@\w+\)\s+([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s+(\d{4})\s*$", text)
    if m and _MONTHS.get(m.group(1).lower()):
        try:
            pub = date(int(m.group(3)), _MONTHS[m.group(1).lower()],
                       int(m.group(2))).isoformat()
        except ValueError:
            pub = None
    # Drop the embed's trailing "— Author (@handle) Month D, Y" attribution
    # from the stored text; the record carries author/date as fields.
    text = re.sub(r"(?:&mdash;|—)\s*Earnings Whispers\s*\(@\w+\)\s*"
                  r"[A-Za-z]{3,9}\.?\s+\d{1,2},\s+\d{4}\s*$", "", text).strip()
    return {"text": text, "author_ok": author_ok, "published_at": pub}


def _hydrate_manual(canonical_url: str, post_id: str) -> dict:
    """Build the manual post record. With a bearer token the post could be
    hydrated via the API too, but oEmbed already provides text/author/date
    without credentials, so one code path serves both cases. The image is
    displayed through the official X embed."""
    rec = {
        "post_id": post_id, "post_url": canonical_url, "author": ACCOUNT,
        "text": None, "published_at": None, "week_start": None, "week_end": None,
        "image_url": None, "image_width": None, "image_height": None,
        "images": [], "tickers": [], "confidence": None, "source": "manual",
        "detected_at": _now_iso(),
    }
    if os.environ.get("JERRY_NO_NET") == "1":
        return rec
    oe = _fetch_oembed(canonical_url)
    if oe is None:
        return rec
    if not oe["author_ok"]:
        rec["error"] = "post is not from @eWhispers"
        return rec
    pub = None
    try:
        pub = date.fromisoformat(oe["published_at"]) if oe["published_at"] else None
    except Exception:
        pub = None
    rec["text"] = oe["text"]
    rec["published_at"] = oe["published_at"]
    rec["tickers"] = extract_tickers(oe["text"])
    wk = parse_week(oe["text"], pub)
    if wk:
        rec["week_start"] = wk
        rec["week_end"] = (date.fromisoformat(wk) + timedelta(days=4)).isoformat()
    return rec


def set_manual(url: str | None) -> dict:
    """Set (or clear, with empty input) the manually supplied post URL."""
    st = _load_state()
    if not (url or "").strip():
        with _LOCK:
            st["manual"] = None
            st["manual_url"] = None
        _save_state()
        return {"ok": True, "cleared": True}
    ok, canon, pid = _validate_post_url(url)
    if not ok:
        return {"ok": False,
                "error": "That doesn't look like an @eWhispers post link. "
                         "Paste a URL like https://x.com/eWhispers/status/123…"}
    rec = _hydrate_manual(canon, pid)
    if rec.get("error"):
        return {"ok": False, "error": rec["error"]}
    with _LOCK:
        st["manual_url"] = canon
        st["manual"] = rec
        # A hydrated manual post that announces a week is ALSO a week entry,
        # so week navigation and by-week selection treat it like any other.
        if rec.get("week_start"):
            st["weeks"][rec["week_start"]] = rec
            _prune_weeks(st)
    _save_state()
    _log(f"manual post set: {canon} (week {rec.get('week_start') or 'unknown'})")
    return {"ok": True, "post": rec}


# ── Refresh orchestration ───────────────────────────────────────────────────

def trigger_refresh(force: bool = False) -> dict:
    """Kick one background refresh. Serves as both the periodic check (from
    get_weekly, rate-limited) and the UI's manual Refresh button (force)."""
    global _REFRESHING, _LAST_ATTEMPT_MONO
    if os.environ.get("JERRY_NO_NET") == "1":
        return {"started": False, "reason": "network disabled (JERRY_NO_NET)"}
    if not _bearer_token():
        # Nothing to poll without credentials; the manual/oEmbed path is
        # hydrated when the URL is set, not on a cadence.
        return {"started": False, "reason": "no X API credentials (X_BEARER_TOKEN)"}
    with _LOCK:
        if _REFRESHING:
            return {"started": False, "reason": "already checking"}
        if not force and (time.monotonic() - _LAST_ATTEMPT_MONO) < CHECK_INTERVAL_SEC \
                and _LAST_ATTEMPT_MONO > 0:
            return {"started": False, "reason": "checked recently"}
        _REFRESHING = True
        _LAST_ATTEMPT_MONO = time.monotonic()

    def run():
        global _REFRESHING
        try:
            _refresh_from_x()
        except Exception as exc:  # noqa: BLE001
            st = _load_state()
            with _LOCK:
                st["last_checked"] = _now_iso()
                st["last_status"] = "error"
                st["last_error"] = str(exc)[:160]
            _save_state()
            _log(f"refresh crashed: {exc}")
        finally:
            with _LOCK:
                _REFRESHING = False

    threading.Thread(target=run, daemon=True).start()
    return {"started": True}


# ── The endpoint payload ────────────────────────────────────────────────────

def get_weekly(week: str | None = None) -> dict:
    """Instant, cache-only read (+ a background refresh kick when stale).
    `week` (ISO date, normalized to its Monday) navigates history."""
    st = _load_state()
    ref_week = trading_week()[0].isoformat()
    target = ref_week
    if week:
        try:
            d = date.fromisoformat(str(week)[:10])
            target = (d - timedelta(days=d.weekday())).isoformat()
        except ValueError:
            pass

    trigger_refresh(force=False)   # no-op when fresh/busy/no-creds/no-net

    with _LOCK:
        weeks_map = dict(st["weeks"])
        manual = st.get("manual")
        manual_url = st.get("manual_url")
        last_checked = st.get("last_checked")
        last_status = st.get("last_status")
        checking = _REFRESHING

    post, showing = None, None
    if target in weeks_map:
        post = weeks_map[target]
        showing = "current" if target == ref_week else "history"
    elif target == ref_week:
        # Current week not verified yet → manual post (even week-unknown),
        # then the most recent verified past week, clearly labeled.
        if manual and (manual.get("week_start") in (None, ref_week)):
            post, showing = manual, "manual"
        else:
            past = sorted(k for k in weeks_map if k < target)
            if past:
                post, showing = weeks_map[past[-1]], "previous"

    week_start = (post or {}).get("week_start") or target
    week_end = (post or {}).get("week_end") \
        or (date.fromisoformat(week_start) + timedelta(days=4)).isoformat()
    weeks = sorted(weeks_map)
    try:
        idx = weeks.index(week_start)
    except ValueError:
        idx = -1
    prev_week = weeks[idx - 1] if idx > 0 else None
    next_week = weeks[idx + 1] if 0 <= idx < len(weeks) - 1 else None

    creds = bool(_bearer_token())
    note = None
    if post is None:
        if not creds and not manual_url:
            note = ("Automatic detection needs an X API key (X_BEARER_TOKEN) — "
                    "or paste the current @eWhispers weekly post link below.")
        elif last_status and last_status not in ("ok", None):
            note = f"X check failed ({last_status}) — nothing cached yet."
        else:
            note = "No weekly calendar post found yet for this week."
    elif showing == "previous":
        note = (f"This week's calendar hasn't been detected yet — showing "
                f"{week_label(week_start)}.")
    elif showing == "manual":
        note = "Showing the manually supplied post."

    return {
        "available": post is not None,
        "post": post,
        "week_start": week_start,
        "week_end": week_end,
        "week_label": week_label(week_start),
        "current_week": ref_week,
        "requested_week": target,
        "is_current_week": week_start == ref_week,
        "showing": showing,
        "weeks": weeks,
        "prev_week": prev_week,
        "next_week": next_week,
        "last_checked": last_checked,
        "last_status": last_status,
        "checking": checking,
        "credentials": creds,          # boolean ONLY — the token never leaves
        "manual_url": manual_url,
        "attribution": "Calendar and post © Earnings Whispers (@eWhispers) on X",
        "note": note,
    }
