"""storage.py (v1.39) — every piece of ~/.jerry-dashboard state I/O,
split out of the options_dashboard monolith: stable data dir, request
log, IV history, dismissed and sent alert logs, trade journal, fade
stages, and the watchlist. Pure stdlib. All writes are atomic
(tmp + replace) and best effort, errors are logged to stderr and never
raised, because a disk hiccup must never take the dashboard down.
options_dashboard re-imports every name so call sites are unchanged.
"""

import json
import os
import sys
import threading
import urllib.parse  # noqa: F401  (kept for parity with prior scope)
from datetime import date, datetime, timedelta
from pathlib import Path


# ─── Watchlist storage ─────────────────────────────────────────────────────
# Server-side persisted watchlist so all devices hitting the dashboard
# see the same symbols, tags, and notes. Kept as a JSON file in the
# project root with atomic-write semantics so a crash mid-write can't
# corrupt the file. Schema version pinned so future migrations are safe.
# Watchlist + token live OUTSIDE the version folder so they survive
# every zip upgrade. Default location: ~/.jerry-dashboard/. Override
# with JERRY_DATA_DIR env var if you want a different path.
def _stable_data_dir() -> Path:
    env_dir = os.environ.get("JERRY_DATA_DIR", "").strip()
    if env_dir:
        d = Path(env_dir).expanduser().resolve()
    else:
        d = (Path.home() / ".jerry-dashboard").resolve()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[storage] could not create {d}: {exc}", file=sys.stderr)
    return d


_STABLE_DIR = _stable_data_dir()
_WATCHLIST_PATH = _STABLE_DIR / "watchlist.json"
# Safety mirror in the same dir (survives corruption of the main file) and a
# repo-baked seed (survives a wiped/ephemeral data dir on redeploy). Load order
# on a missing/bad main file: backup -> repo seed -> built-in 5 defaults.
_WATCHLIST_BAK = _STABLE_DIR / "watchlist.json.bak"


def _find_seed() -> "Path | None":
    """Locate the repo-baked seed robustly — the deployed working directory
    isn't guaranteed to match this module's path, and if the seed isn't found
    we'd wrongly fall to the bare 5-symbol default and lose the user's list."""
    cands = [
        Path(__file__).resolve().parent / "watchlist_seed.json",
        Path.cwd() / "watchlist_seed.json",
        Path("/app/watchlist_seed.json"),
    ]
    for p in cands:
        try:
            if p.exists():
                return p
        except Exception:
            continue
    return None


_WATCHLIST_SEED = _find_seed() or (Path(__file__).resolve().parent / "watchlist_seed.json")
# Records which recovery branch the last load took, for the /api/watchlist/diag
# endpoint so persistence problems are diagnosable in production.
_WATCHLIST_LOAD_INFO = {"branch": None, "seed_path": str(_WATCHLIST_SEED)}

# ── Request log (v1.39) ─────────────────────────────────────────────
# One line per /api request with method, path, status, and duration in
# milliseconds, appended to ~/.jerry-dashboard/server.log so slow
# endpoints and silent failures are visible after the fact. Rotates at
# 5MB by renaming to server.log.1 (one generation kept).
_SERVER_LOG_PATH = _STABLE_DIR / "server.log"
_SERVER_LOG_LOCK = threading.Lock()
_SERVER_LOG_MAX = 5_000_000


def _request_log(method: str, path: str, status, ms: float) -> None:
    try:
        line = (f"{datetime.now().isoformat(timespec='seconds')} "
                f"{method} {path} {status} {ms:.0f}ms\n")
        with _SERVER_LOG_LOCK:
            try:
                if (_SERVER_LOG_PATH.exists()
                        and _SERVER_LOG_PATH.stat().st_size > _SERVER_LOG_MAX):
                    _SERVER_LOG_PATH.replace(
                        _SERVER_LOG_PATH.with_suffix(".log.1"))
            except Exception:
                pass
            with open(_SERVER_LOG_PATH, "a") as fh:
                fh.write(line)
    except Exception:
        pass  # logging must never break a request


# ── IV rank/percentile helpers (v1.14) ─────────────────────────────
# Persist ATM IV30 snapshots per ticker so we can compute IV rank and
# IV percentile from local history. One JSON file per ticker, one entry
# per calendar date (deduplicated). Capped at 252 most recent entries
# (~1 trading year). All writes are best-effort: errors are logged and
# ignored so the scanner never fails because of a disk problem.
_IV_HISTORY_DIR = _STABLE_DIR / "iv_history"
_IV_HISTORY_MAX = 252


def _iv_history_path(symbol: str) -> Path:
    safe = "".join(c for c in symbol.upper() if c.isalnum() or c in ("-", "_", "."))
    return _IV_HISTORY_DIR / f"{safe}.json"


def _iv_history_load(symbol: str) -> list:
    """Returns the persisted list of {date, iv} entries for symbol,
    sorted oldest-first. Returns [] on any error (missing file, parse
    failure, permission). Never raises."""
    p = _iv_history_path(symbol)
    if not p.exists():
        return []
    try:
        raw = p.read_text()
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        cleaned = []
        for r in data:
            if not isinstance(r, dict):
                continue
            d = r.get("date")
            iv = r.get("iv")
            if not isinstance(d, str) or not isinstance(iv, (int, float)):
                continue
            if iv <= 0 or iv > 10:
                # Sanity bounds: IV is a decimal (e.g. 0.45 = 45%). Anything
                # outside (0, 10] is corrupt and would skew rank.
                continue
            cleaned.append({"date": d, "iv": float(iv)})
        cleaned.sort(key=lambda r: r["date"])
        return cleaned
    except Exception as exc:  # noqa: BLE001
        print(f"[iv_history] load failed for {symbol}: {exc}", file=sys.stderr)
        return []


def _iv_history_append(symbol: str, iv: float) -> None:
    """Append today's IV snapshot if not already present for this date.
    Trims to the last _IV_HISTORY_MAX entries. Best-effort."""
    if iv is None or iv <= 0:
        return
    try:
        _IV_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[iv_history] mkdir failed: {exc}", file=sys.stderr)
        return
    today = date.today().strftime("%Y-%m-%d")
    history = _iv_history_load(symbol)
    # Dedupe on date: replace today's entry if it already exists. This
    # makes intra-day re-scans converge to the latest snapshot.
    history = [r for r in history if r["date"] != today]
    history.append({"date": today, "iv": round(float(iv), 6)})
    if len(history) > _IV_HISTORY_MAX:
        history = history[-_IV_HISTORY_MAX:]
    p = _iv_history_path(symbol)
    try:
        # Atomic write: tmp + rename. Avoids partial JSON on crash.
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(history, separators=(",", ":")))
        tmp.replace(p)
    except Exception as exc:  # noqa: BLE001
        print(f"[iv_history] write failed for {symbol}: {exc}", file=sys.stderr)


def _iv_history_compute_rank(history: list, current_iv: float) -> dict:
    """Compute IV rank and IV percentile from a history list.
    Returns dict with iv_rank, iv_pct, iv_rank_days. Returns null fields
    when there is insufficient history (< 20 days)."""
    out = {"iv_rank": None, "iv_pct": None, "iv_rank_days": 0}
    if not history or current_iv is None or current_iv <= 0:
        return out
    out["iv_rank_days"] = len(history)
    if len(history) < 20:
        # Below 20 entries the rank is too noisy to be useful.
        return out
    ivs = [r["iv"] for r in history if r.get("iv") is not None]
    if not ivs:
        return out
    lo = min(ivs)
    hi = max(ivs)
    if hi > lo:
        out["iv_rank"] = round(((current_iv - lo) / (hi - lo)) * 100.0, 1)
    else:
        out["iv_rank"] = 50.0  # all values equal — undefined, return midpoint
    below = sum(1 for v in ivs if v < current_iv)
    out["iv_pct"] = round((below / len(ivs)) * 100.0, 1)
    return out


# ── Dismissed alerts (v1.15) ───────────────────────────────────────
# Persists alert IDs the user has dismissed so the watchlist alerts
# endpoint does not re-surface the same fresh upgrade or downgrade
# every poll. Stored as a flat dict {id: dismissed_iso_date} so we
# can age out very old entries (90+ days) automatically. Best-effort.
_DISMISSED_ALERTS_PATH = _STABLE_DIR / "dismissed_alerts.json"


def _safe_parse_date(s: str) -> date:
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    except Exception:
        return date(1970, 1, 1)


def _load_dismissed_alerts() -> dict:
    if not _DISMISSED_ALERTS_PATH.exists():
        return {}
    try:
        raw = _DISMISSED_ALERTS_PATH.read_text()
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        cutoff = (datetime.now() - timedelta(days=90)).date()
        return {k: v for k, v in data.items()
                if isinstance(v, str) and _safe_parse_date(v) >= cutoff}
    except Exception as exc:  # noqa: BLE001
        print(f"[dismissed_alerts] load failed: {exc}", file=sys.stderr)
        return {}


def _save_dismissed_alerts(data: dict) -> None:
    try:
        tmp = _DISMISSED_ALERTS_PATH.with_suffix(_DISMISSED_ALERTS_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        tmp.replace(_DISMISSED_ALERTS_PATH)
    except Exception as exc:  # noqa: BLE001
        print(f"[dismissed_alerts] write failed: {exc}", file=sys.stderr)


_TRADE_JOURNAL_PATH = _STABLE_DIR / "trade_journal.json"


def _load_trade_journal() -> list:
    if not _TRADE_JOURNAL_PATH.exists():
        return []
    try:
        raw = _TRADE_JOURNAL_PATH.read_text()
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        print(f"[trade_journal] load failed: {exc}", file=sys.stderr)
        return []


def _save_trade_journal(data: list) -> None:
    try:
        tmp = _TRADE_JOURNAL_PATH.with_suffix(_TRADE_JOURNAL_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        tmp.replace(_TRADE_JOURNAL_PATH)
    except Exception as exc:  # noqa: BLE001
        print(f"[trade_journal] write failed: {exc}", file=sys.stderr)


_FADE_STAGES_PATH = _STABLE_DIR / "fade_stages.json"


def _load_fade_stages() -> list:
    if not _FADE_STAGES_PATH.exists():
        return []
    try:
        data = json.loads(_FADE_STAGES_PATH.read_text())
        return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        print(f"[fade_stages] load failed: {exc}", file=sys.stderr)
        return []


def _save_fade_stages(data: list) -> None:
    try:
        tmp = _FADE_STAGES_PATH.with_suffix(_FADE_STAGES_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        tmp.replace(_FADE_STAGES_PATH)
    except Exception as exc:  # noqa: BLE001
        print(f"[fade_stages] write failed: {exc}", file=sys.stderr)


# ── Push alerts via Pushover (v1.16) ───────────────────────────────
# Pushover requires two env vars set via `jerry env set`:
#   PUSHOVER_APP_TOKEN  — application token from Pushover dashboard
#   PUSHOVER_USER_KEY   — user key from Pushover account
# Both are required. If either is missing, the sender returns False
# silently so the dashboard works without push enabled.
#
# Sent-log at ~/.jerry-dashboard/sent_alerts.json prevents the same
# roll-flag from blasting the phone every poll. Keyed by
# (ticker, position_id, alert_type) with the timestamp of last send.
# An alert re-fires only after 12 hours so a stuck position does
# get reminded once a day, not every minute.
_SENT_ALERTS_PATH = _STABLE_DIR / "sent_alerts.json"
_PUSH_RESEND_HOURS = 12


def _load_sent_alerts() -> dict:
    if not _SENT_ALERTS_PATH.exists():
        return {}
    try:
        raw = _SENT_ALERTS_PATH.read_text()
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        print(f"[sent_alerts] load failed: {exc}", file=sys.stderr)
        return {}


def _save_sent_alerts(data: dict) -> None:
    try:
        tmp = _SENT_ALERTS_PATH.with_suffix(_SENT_ALERTS_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        tmp.replace(_SENT_ALERTS_PATH)
    except Exception as exc:  # noqa: BLE001
        print(f"[sent_alerts] write failed: {exc}", file=sys.stderr)


# Watchlist reads/writes share one re-entrant lock so an atomic save can't
# interleave with a concurrent read. Lazily created to match the lazy-init
# style used elsewhere in this module.
_WATCHLIST_LOCK = None


def _watchlist_lock():
    global _WATCHLIST_LOCK
    if _WATCHLIST_LOCK is None:
        _WATCHLIST_LOCK = threading.RLock()
    return _WATCHLIST_LOCK


def _default_watchlist() -> dict:
    """Initial schema seeded from the legacy 5-symbol default."""
    import time
    now = int(time.time())
    seeds = [
        ("SPY", ["index", "etf"], True),
        ("QQQ", ["index", "etf"], True),
        ("AAPL", ["mega-cap", "tech"], True),
        ("NVDA", ["mega-cap", "semis"], True),
        ("TSLA", ["mega-cap", "ev"], True),
    ]
    return {
        "version": 1,
        "symbols": [
            {
                "symbol": s,
                "tags": tags,
                "notes": "",
                "preferred_strategy": None,
                "starred": star,
                "added_at": now,
            }
            for s, tags, star in seeds
        ],
        "tag_order": ["index", "etf", "mega-cap", "tech", "semis", "ev"],
    }


def _read_watchlist_file(path: "Path") -> dict | None:
    """Read + structurally validate one watchlist file. None if absent/bad."""
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            return None
        return data
    except Exception:
        return None


def _load_watchlist() -> dict:
    """Read the watchlist, with layered recovery so the user's list is never
    silently lost:

      1. main file (watchlist.json)            — normal path
      2. backup mirror (watchlist.json.bak)    — main corrupt/missing
      3. repo seed (watchlist_seed.json)       — data dir wiped on redeploy
      4. built-in 5-symbol default             — first run, nothing else

    Recovered copies (2-4) are written back to the main file. The returned dict
    carries a transient `_seeded` flag (True only for cases 3-4, i.e. a fresh
    fallback that is NOT real saved data) so the API/clients can avoid letting a
    fallback clobber a richer list. `_seeded` is never persisted to disk."""
    with _watchlist_lock():
        data = _read_watchlist_file(_WATCHLIST_PATH)
        if data is not None:
            _WATCHLIST_LOAD_INFO["branch"] = "main"
            return data
        # 2 — restore from the backup mirror (real data, not a seed)
        bak = _read_watchlist_file(_WATCHLIST_BAK)
        if bak is not None and bak.get("symbols"):
            print("[watchlist] main missing/corrupt — restored from .bak", file=sys.stderr)
            _WATCHLIST_LOAD_INFO["branch"] = "bak"
            _save_watchlist(bak)
            return bak
        # 3 — restore from the repo-baked seed (survives an ephemeral data dir).
        # Re-resolve the path each time in case the working dir differs at runtime.
        seed_path = _find_seed() or _WATCHLIST_SEED
        _WATCHLIST_LOAD_INFO["seed_path"] = str(seed_path)
        seed = _read_watchlist_file(seed_path)
        if seed is not None and seed.get("symbols"):
            print(f"[watchlist] no saved file — seeded {len(seed['symbols'])} "
                  f"symbols from {seed_path}", file=sys.stderr)
            _WATCHLIST_LOAD_INFO["branch"] = "seed"
            _save_watchlist(seed)
            out = dict(seed)
            out["_seeded"] = True
            return out
        # 4 — last resort: built-in defaults
        print(f"[watchlist] no saved file or seed (looked at {seed_path}) — "
              "using built-in defaults", file=sys.stderr)
        _WATCHLIST_LOAD_INFO["branch"] = "default"
        wl = _default_watchlist()
        _save_watchlist(wl)
        out = dict(wl)
        out["_seeded"] = True
        return out


def _watchlist_diag() -> dict:
    """Snapshot of the watchlist persistence state for production debugging:
    where the data dir is, what files exist + their counts, the resolved seed
    path, and which branch the last load used."""
    def _count(p):
        d = _read_watchlist_file(p)
        return len(d["symbols"]) if d and isinstance(d.get("symbols"), list) else None
    seed_p = _find_seed()
    return {
        "data_dir": str(_STABLE_DIR),
        "jerry_data_dir_env": os.environ.get("JERRY_DATA_DIR") or None,
        "main_exists": _WATCHLIST_PATH.exists(), "main_count": _count(_WATCHLIST_PATH),
        "bak_exists": _WATCHLIST_BAK.exists(), "bak_count": _count(_WATCHLIST_BAK),
        "seed_path": str(seed_p) if seed_p else None,
        "seed_exists": bool(seed_p), "seed_count": _count(seed_p) if seed_p else None,
        "last_load_branch": _WATCHLIST_LOAD_INFO.get("branch"),
    }


def _save_watchlist(data: dict) -> bool:
    """Atomic write of the main file plus a backup mirror. Transient keys
    (anything starting with "_", e.g. _seeded) are stripped before writing so
    they never persist. The .bak mirror lets a later corruption of the main
    file be recovered on the next load."""
    clean = {k: v for k, v in data.items() if not str(k).startswith("_")}
    with _watchlist_lock():
        ok = _atomic_write_json(_WATCHLIST_PATH, clean)
        if ok:
            # Best-effort mirror; failure here never fails the save.
            _atomic_write_json(_WATCHLIST_BAK, clean)
        return ok


def _atomic_write_json(path: "Path", data: dict) -> bool:
    """Write to a .tmp then rename. Avoids corruption on crash mid-write."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[watchlist] write failed ({path.name}): {exc}", file=sys.stderr)
        try:
            if tmp.exists(): tmp.unlink()
        except Exception: pass
        return False


def _validate_watchlist_payload(data) -> dict | None:
    """Sanitize a posted watchlist. Caps symbol count + tag length to
    prevent abuse / runaway file size. Returns the cleaned dict or None
    if structurally invalid."""
    if not isinstance(data, dict):
        return None
    syms_in = data.get("symbols")
    if not isinstance(syms_in, list):
        return None
    out_syms = []
    seen = set()
    for entry in syms_in[:2000]:  # hard cap 2000 symbols
        if not isinstance(entry, dict): continue
        sym = (entry.get("symbol") or "").upper().strip()
        if not sym or len(sym) > 12 or sym in seen: continue
        # Allow letters, digits, and . / - for special symbols (BRK.B etc)
        if not all(c.isalnum() or c in ".-/" for c in sym): continue
        seen.add(sym)
        tags_in = entry.get("tags") or []
        tags = []
        if isinstance(tags_in, list):
            for t in tags_in[:20]:
                if isinstance(t, str) and 0 < len(t) <= 32:
                    tags.append(t.strip().lower())
        out_syms.append({
            "symbol": sym,
            "tags": tags,
            "notes": (entry.get("notes") or "")[:500] if isinstance(entry.get("notes"), str) else "",
            "preferred_strategy": (entry.get("preferred_strategy") or None) if isinstance(entry.get("preferred_strategy"), (str, type(None))) else None,
            "starred": bool(entry.get("starred")),
            "added_at": int(entry.get("added_at") or 0),
        })
    tag_order_in = data.get("tag_order") or []
    tag_order = []
    if isinstance(tag_order_in, list):
        for t in tag_order_in[:50]:
            if isinstance(t, str) and 0 < len(t) <= 32:
                tag_order.append(t.strip().lower())
    return {
        "version": 1,
        "symbols": out_syms,
        "tag_order": tag_order,
    }


# ── UI preferences (v1.98) ─────────────────────────────────────────
# Small server-side key/value store for cross-device UI preferences that
# aren't part of the watchlist — currently the user's custom top-tab
# order. Same atomic-write + survives-upgrades semantics as the watchlist.
_PREFS_PATH = _STABLE_DIR / "ui_prefs.json"


def _load_prefs() -> dict:
    if not _PREFS_PATH.exists():
        return {"version": 1, "tab_order": []}
    try:
        data = json.loads(_PREFS_PATH.read_text())
        return data if isinstance(data, dict) else {"version": 1, "tab_order": []}
    except Exception as exc:  # noqa: BLE001
        print(f"[prefs] load failed: {exc}", file=sys.stderr)
        return {"version": 1, "tab_order": []}


def _save_prefs(data: dict) -> bool:
    try:
        tmp = _PREFS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(_PREFS_PATH)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[prefs] save failed: {exc}", file=sys.stderr)
        try:
            if tmp.exists(): tmp.unlink()
        except Exception: pass
        return False


def _validate_prefs_payload(data) -> dict | None:
    """Sanitize posted UI prefs. Only known keys are kept. tab_order is a
    short list of slug-like tab ids."""
    if not isinstance(data, dict):
        return None
    tab_order = []
    raw = data.get("tab_order")
    if isinstance(raw, list):
        seen = set()
        for t in raw[:40]:
            if isinstance(t, str):
                s = t.strip().lower()
                if s and len(s) <= 24 and s not in seen and all(c.isalnum() or c in "-_" for c in s):
                    seen.add(s)
                    tab_order.append(s)
    return {"version": 1, "tab_order": tab_order}

