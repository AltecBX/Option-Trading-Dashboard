"""intraday_option_store.py — the 0DTE intraday option TAPE (spec v3 §2/§3).

chain_store.py keeps its end-of-day role untouched; this is the separate
intraday path. It records what the timing engine needs — premium path,
delta path, distance to strike, touch events, quote quality, IV path — for
a small set of tracked contracts, at tiered cadences, inside the app's
shared Schwab request budget.

Design facts that drive the implementation:
  • One Schwab chain call per (symbol, expiry) returns EVERY contract's
    bid/ask/last/greeks/OI **plus the underlying quote in the same
    payload** — an atomic paired snapshot (§3's pairing requirement) at a
    fraction of per-contract quote cost. The client already caches chains
    for 30s (TTL_CHAIN), which is exactly the P1 cadence.
  • Budget: the SchwabClient self-caps at 110 req/min app-wide. The tape's
    slice is configured (default 30/min) with 30% headroom, and a written
    degradation ladder (thresholds.json → tape.degrade_ladder) slows tiers
    in order: discovery pauses first, then P2 stretches, then P1.
  • Amendment E: the Durable Executable High is defined in SECONDS
    (default 60), implemented as however many consecutive snapshots cover
    that span at the LIVE cadence — a cadence change cannot silently
    redefine the benchmark.

Storage (all under <data_dir>/timing/tape/):
  YYYY-MM-DD/SYM.jsonl        append-only snapshots (crash-safe: one line
                              per write, flushed)
  YYYY-MM-DD/session.json     per-contract session benchmarks (atomic
                              rewrite, §2: raw/mid/executable/durable
                              highs + admissible high)
  Retention: full resolution for the trailing N sessions (config), older
  sessions compacted to session.json + the minutes around extremes.

All timestamps ET (Step 0.5 discipline lives in timing_engine.check_clock).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, date, time as _dtime, timedelta
from pathlib import Path

_DATA_DIR: Path | None = None
_CHAIN_FN = None                 # (symbol, expiry_iso) -> normalized chain | None
_SCHWAB_GETTER = None            # () -> client | None (for rate accounting)
_CANDIDATES_FN = None            # () -> [{key, symbol, expiry, kind, strike}, ...]
_JUICE_FN = None                 # () -> [{symbol, ...}] 0DTE juice rows (discovery)
_CFG_FN = None                   # () -> (cfg, hash)
_ET = None

_LOCK = threading.Lock()
_SESSION: dict = {}              # contract_key -> live benchmark tracker
_INTEREST: dict = {}             # symbol -> last on-screen ping ts (P1 promotion)
_LAST_FETCH: dict = {}           # symbol -> ts of last chain fetch
_STATS: dict = {"snapshots_today": 0, "symbols": [], "req_last_min": 0,
                "degraded": None, "last_cycle_ts": None, "running": False,
                "day": None, "error": None}
_THREAD: dict = {"t": None}


def configure(data_dir, chain_fn, schwab_getter=None, candidates_fn=None,
              juice_fn=None, cfg_fn=None, et_tz=None) -> None:
    global _DATA_DIR, _CHAIN_FN, _SCHWAB_GETTER, _CANDIDATES_FN, _JUICE_FN
    global _CFG_FN, _ET
    _DATA_DIR = Path(data_dir)
    _CHAIN_FN = chain_fn
    _SCHWAB_GETTER = schwab_getter
    _CANDIDATES_FN = candidates_fn
    _JUICE_FN = juice_fn
    _CFG_FN = cfg_fn
    _ET = et_tz


def _now_et() -> datetime:
    return datetime.now(_ET) if _ET else datetime.utcnow()


def _cfg() -> dict:
    if _CFG_FN is None:
        return {}
    try:
        return (_CFG_FN()[0] or {}).get("tape") or {}
    except Exception:
        return {}


def _tape_dir(day: str | None = None) -> Path:
    d = (_DATA_DIR or Path(".")) / "timing" / "tape"
    if day:
        d = d / day
    d.mkdir(parents=True, exist_ok=True)
    return d


def register_interest(symbol: str) -> None:
    """On-screen ping (§3 priority 1): the frontend card calls this via its
    poll; the symbol stays P1 for tape.interest_decay_min minutes."""
    with _LOCK:
        _INTEREST[str(symbol).upper()] = time.time()


# ── Tracked-contract assembly ───────────────────────────────────────────────

def _tracked(now: datetime) -> dict:
    """{symbol: {expiry, tier, keys: set}} for contracts expiring TODAY.
    P1 = candidates + on-screen symbols; P2 = same chain call's delta
    neighborhood (free); discovery = juice-board symbols (slow tier)."""
    today = now.date().isoformat()
    cfg = _cfg()
    decay = float(cfg.get("interest_decay_min", 10)) * 60.0
    out: dict = {}
    try:
        cands = _CANDIDATES_FN() if _CANDIDATES_FN else []
    except Exception:
        cands = []
    for c in cands or []:
        if str(c.get("expiry") or "")[:10] != today:
            continue
        sym = str(c.get("symbol") or "").upper()
        rec = out.setdefault(sym, {"expiry": today, "tier": 1, "keys": set()})
        rec["keys"].add(c.get("key"))
        rec["tier"] = 1
    with _LOCK:
        hot = {s for s, ts in _INTEREST.items() if time.time() - ts < decay}
    for sym in hot:
        out.setdefault(sym, {"expiry": today, "tier": 1, "keys": set()})["tier"] = 1
    if _JUICE_FN is not None and now.weekday() == 4:
        try:
            for r in (_JUICE_FN() or [])[:12]:
                sym = str(r.get("symbol") or "").upper()
                if sym and sym not in out:
                    out[sym] = {"expiry": today, "tier": 3, "keys": set()}
        except Exception:
            pass
    return out


# ── Budget + degradation ladder ─────────────────────────────────────────────

def _cadence(tier: int, cfg: dict, degraded: str | None) -> float:
    p1 = float(cfg.get("p1_seconds", 30))
    p2 = float(cfg.get("p2_seconds", 60))
    disc = float(cfg.get("discovery_seconds", 300))
    if degraded == "p1_seconds=60":
        p1, p2 = max(p1, 60), max(p2, 120)
    elif degraded == "p2_seconds=120":
        p2 = max(p2, 120)
    if tier == 1:
        return p1
    if tier == 2:
        return p2
    return disc if degraded != "discovery_paused" or tier < 3 else float("inf")


def _rate_used_per_min() -> int | None:
    """Requests in the sliding 60s window from the client's own accounting
    (schwab_client keeps _req_log for its 110/min self-cap)."""
    try:
        sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
        if sc is None:
            return None
        if hasattr(sc, "rate_usage"):
            return int(sc.rate_usage())
        log = getattr(sc, "_req_log", None)
        if log is None:
            return None
        now = time.time()
        return sum(1 for t in log if now - t < 60)
    except Exception:
        return None


def _degradation(cfg: dict) -> str | None:
    """The written ladder (§3): which tier slows first when the shared
    budget tightens. Uses the tape's OWN projected load + the app-wide
    live usage; picks the strictest triggered rung."""
    used = _rate_used_per_min()
    if used is None:
        return None
    ladder = cfg.get("degrade_ladder") or []
    hit = None
    for rung in ladder:
        if used > float(rung.get("when_req_per_min_over", 1e9)):
            hit = rung.get("action")
    return hit


# ── Session benchmark tracker (§2 + Amendment E) ────────────────────────────

def _bench_new() -> dict:
    return {"raw_high": None, "raw_high_ts": None,
            "mid_high": None, "mid_high_ts": None,
            "exec_high": None, "exec_high_ts": None,
            "durable_high": None, "durable_high_ts": None,
            "admissible_high": None, "admissible_high_ts": None,
            "touched": False, "touch_ts": None,
            "n_snaps": 0, "_win": []}


def _bench_update(b: dict, snap: dict, durable_seconds: float,
                  admissible: bool | None) -> None:
    ts = snap["ts"]
    last, bid = snap.get("last"), snap.get("bid")
    mid = snap.get("mid")

    def hi(cur_key, val):
        if val is not None and (b[cur_key] is None or val > b[cur_key]):
            b[cur_key] = round(float(val), 4)
            b[cur_key + "_ts"] = ts

    hi("raw_high", last)
    hi("mid_high", mid)
    hi("exec_high", bid)
    if admissible and bid is not None:
        hi("admissible_high", bid)
    # Durable Executable High (Amendment E): the highest bid SUSTAINED for
    # the configured seconds = max over time of (min bid over the trailing
    # durable_seconds window). Defined in SECONDS: a cadence change alters
    # only the sampling resolution, never the benchmark itself.
    if bid is not None:
        try:
            t_ep = datetime.fromisoformat(ts).timestamp()
        except Exception:
            t_ep = None
        if t_ep is not None:
            win = b["_win"]
            win.append([t_ep, float(bid)])
            while win and t_ep - win[0][0] > durable_seconds:
                win.pop(0)
            if win and t_ep - win[0][0] >= durable_seconds - 1e-6:
                candidate = round(min(v for _, v in win), 4)
                if b["durable_high"] is None or candidate > b["durable_high"]:
                    b["durable_high"] = candidate
                    b["durable_high_ts"] = ts
    else:
        b["_win"] = []
    if snap.get("beyond_strike"):
        if not b["touched"]:
            b["touch_ts"] = ts
        b["touched"] = True
    b["n_snaps"] += 1


def session_benchmarks(contract_key: str, day: str | None = None) -> dict | None:
    """§2 session values for one contract, live from memory (today) or from
    the stored session.json (past days). Sampling bounds what these can
    see; n_snaps states the measurement basis."""
    d = day or _now_et().date().isoformat()
    with _LOCK:
        b = _SESSION.get(contract_key)
        if b is not None and _STATS.get("day") == d:
            out = {k: v for k, v in b.items() if not k.startswith("_")}
            return out
    try:
        p = _tape_dir(d) / "session.json"
        if p.exists():
            data = json.loads(p.read_text())
            return data.get(contract_key)
    except Exception:
        pass
    return None


def _persist_sessions(day: str) -> None:
    try:
        with _LOCK:
            data = {k: {x: v for x, v in b.items() if not x.startswith("_")}
                    for k, b in _SESSION.items()}
        p = _tape_dir(day) / "session.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        tmp.replace(p)
    except Exception:
        pass


# ── Snapshotting ────────────────────────────────────────────────────────────

def _snap_symbol(symbol: str, expiry: str, tier: int, keys: set,
                 cfg: dict, now: datetime) -> int:
    """One chain fetch → snapshot rows for (a) explicitly tracked contracts
    (any delta) and (b) the target-delta neighborhood (§3 P2, free in the
    same payload). Returns rows written."""
    try:
        full = _CHAIN_FN(symbol, expiry) if _CHAIN_FN else None
    except Exception:
        full = None
    if not full:
        return 0
    under = full.get("underlying") or {}
    spot = under.get("last")
    if not spot:
        return 0
    lo_d = float(cfg.get("target_delta_lo", 0.10))
    hi_d = float(cfg.get("target_delta_hi", 0.50))
    durable = float(cfg.get("durable_seconds", 60))
    day = now.date().isoformat()
    ts = now.isoformat(timespec="seconds")
    rows_out = []
    chains = (full.get("chains") or {}).get(expiry) or {}
    for kind, side in (("call", "calls"), ("put", "puts")):
        for r in (chains.get(side) or []):
            k = r.get("strike")
            if k is None:
                continue
            ckey = f"{symbol}|{expiry}|{kind}|{float(k):g}"
            d_abs = abs(r.get("delta") or 0)
            tracked_row = ckey in keys
            neighborhood = lo_d <= d_abs <= hi_d
            if not tracked_row and not neighborhood:
                continue
            bid, ask = r.get("bid"), r.get("ask")
            mid = ((bid or 0) + (ask or 0)) / 2.0 if (bid is not None and ask is not None) else None
            spread_pct = (round((ask - bid) / mid * 100.0, 1)
                          if (bid is not None and ask is not None and mid) else None)
            beyond = (spot >= k) if kind == "call" else (spot <= k)
            snap = {"ts": ts, "key": ckey, "sym": symbol, "exp": expiry,
                    "k": float(k), "kind": kind,
                    "bid": bid, "ask": ask, "mid": round(mid, 4) if mid else None,
                    "last": r.get("last"),
                    "bsz": r.get("bid_size"), "asz": r.get("ask_size"),
                    "spr_pct": spread_pct, "qage_s": r.get("quote_age_s"),
                    "spot": spot, "u_bid": under.get("bid"), "u_ask": under.get("ask"),
                    "delta": r.get("delta"), "gamma": r.get("gamma"),
                    "theta": r.get("theta"), "vega": r.get("vega"),
                    "iv": r.get("iv"), "vol": r.get("volume"),
                    "oi": r.get("openInterest"),
                    "tier": 1 if tracked_row else 2,
                    "beyond_strike": bool(beyond)}
            rows_out.append(snap)
            with _LOCK:
                b = _SESSION.setdefault(ckey, _bench_new())
                _bench_update(b, snap, durable, admissible=None)
    if rows_out:
        try:
            p = _tape_dir(day) / f"{symbol}.jsonl"
            with open(p, "a", encoding="utf-8") as f:
                for s in rows_out:
                    f.write(json.dumps(s, separators=(",", ":")) + "\n")
                f.flush()
        except Exception:
            return 0
    return len(rows_out)


# ── Collector loop ──────────────────────────────────────────────────────────

def _market_open(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return _dtime(9, 25) <= now.time() < _dtime(16, 5)


def _collector_loop() -> None:
    try:
        while True:
            now = _now_et()
            if not _market_open(now):
                break
            cfg = _cfg()
            day = now.date().isoformat()
            with _LOCK:
                if _STATS.get("day") != day:
                    _STATS.update({"day": day, "snapshots_today": 0})
                    _SESSION.clear()
            tracked = _tracked(now)
            if not tracked:
                with _LOCK:
                    _STATS.update({"last_cycle_ts": now.isoformat(timespec="seconds"),
                                   "symbols": []})
                time.sleep(20)
                continue
            degraded = _degradation(cfg)
            wrote = 0
            for sym, rec in tracked.items():
                cad = _cadence(rec["tier"], cfg, degraded)
                if cad == float("inf"):
                    continue
                last = _LAST_FETCH.get(sym, 0.0)
                if time.time() - last < cad:
                    continue
                _LAST_FETCH[sym] = time.time()
                wrote += _snap_symbol(sym, rec["expiry"], rec["tier"],
                                      rec.get("keys") or set(), cfg, now)
            with _LOCK:
                _STATS.update({"snapshots_today": _STATS["snapshots_today"] + wrote,
                               "symbols": sorted(tracked.keys()),
                               "req_last_min": _rate_used_per_min(),
                               "degraded": degraded,
                               "last_cycle_ts": now.isoformat(timespec="seconds"),
                               "error": None})
            if wrote:
                _persist_sessions(day)
            time.sleep(10)
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _STATS["error"] = str(exc)
    finally:
        with _LOCK:
            _STATS["running"] = False
            _THREAD["t"] = None


def ensure_collector() -> bool:
    """Start (or confirm) the collector thread. Idempotent; the loop exits
    outside market hours and restarts on the next call — the boot scheduler
    pings this every minute (Amendment A: the tape runs headless)."""
    with _LOCK:
        if _STATS["running"]:
            return True
        if not _market_open(_now_et()):
            return False
        _STATS["running"] = True
        t = threading.Thread(target=_collector_loop, name="timing-tape", daemon=True)
        _THREAD["t"] = t
    t.start()
    return True


def start_scheduler() -> None:
    """Boot-time daemon: wake every 60s, keep the collector alive during
    market hours, run retention compaction once after the close."""
    def loop():
        compacted = {"day": None}
        while True:
            try:
                now = _now_et()
                if _market_open(now):
                    ensure_collector()
                elif now.time() >= _dtime(16, 30) and now.weekday() < 5 \
                        and compacted["day"] != now.date().isoformat():
                    _persist_sessions(now.date().isoformat())
                    compact_retention()
                    compacted["day"] = now.date().isoformat()
            except Exception:
                pass
            time.sleep(60)
    t = threading.Thread(target=loop, name="timing-tape-sched", daemon=True)
    t.start()


# ── Retention / compaction (§3 [V3]) ────────────────────────────────────────

def compact_retention() -> dict:
    """Keep full-resolution tape for the trailing N sessions; older days
    are compacted to session.json + the ±minutes around each contract's
    exec high / touch events; the raw JSONL is then removed."""
    cfg = _cfg()
    keep_n = int(cfg.get("retention_full_sessions", 8))
    around_min = float(cfg.get("compact_keep_minutes_around_events", 10))
    base = _tape_dir()
    days = sorted([p.name for p in base.iterdir() if p.is_dir()])
    to_compact = days[:-keep_n] if len(days) > keep_n else []
    done = []
    for day in to_compact:
        ddir = base / day
        raws = list(ddir.glob("*.jsonl"))
        if not raws:
            continue
        sess_p = ddir / "session.json"
        try:
            sess = json.loads(sess_p.read_text()) if sess_p.exists() else {}
        except Exception:
            sess = {}
        keep_windows: dict = {}
        for ckey, b in sess.items():
            wins = []
            for ts_key in ("exec_high_ts", "touch_ts", "durable_high_ts"):
                ts = b.get(ts_key)
                if ts:
                    wins.append(ts)
            keep_windows[ckey] = wins
        for raw in raws:
            kept = []
            try:
                for line in raw.read_text().splitlines():
                    try:
                        s = json.loads(line)
                    except Exception:
                        continue
                    wins = keep_windows.get(s.get("key")) or []
                    ts = s.get("ts")
                    for w in wins:
                        try:
                            if abs((datetime.fromisoformat(ts)
                                    - datetime.fromisoformat(w)).total_seconds()) \
                                    <= around_min * 60.0:
                                kept.append(line)
                                break
                        except Exception:
                            continue
                cp = raw.with_suffix(".events.jsonl")
                cp.write_text("\n".join(kept) + ("\n" if kept else ""))
                raw.unlink()
            except Exception:
                continue
        done.append(day)
    return {"compacted": done, "kept_full": days[-keep_n:] if days else []}


# ── Reads for the engine/UI ─────────────────────────────────────────────────

def read_contract_day(contract_key: str, day: str | None = None) -> list:
    """All snapshots for one contract on one day (post-trade metrics §5)."""
    d = day or _now_et().date().isoformat()
    sym = contract_key.split("|", 1)[0]
    p = _tape_dir(d) / f"{sym}.jsonl"
    rows = []
    for cand in (p, p.with_suffix(".events.jsonl")):
        if not cand.exists():
            continue
        try:
            for line in cand.read_text().splitlines():
                try:
                    s = json.loads(line)
                except Exception:
                    continue
                if s.get("key") == contract_key:
                    rows.append(s)
        except Exception:
            pass
        break
    return rows


def status() -> dict:
    cfg = _cfg()
    with _LOCK:
        st = {k: v for k, v in _STATS.items()}
    est = None
    try:
        base = _tape_dir()
        est = sum(f.stat().st_size for d in base.iterdir() if d.is_dir()
                  for f in d.iterdir()) // 1024
    except Exception:
        pass
    st.update({"budget_req_per_min": cfg.get("budget_req_per_min"),
               "cadences_s": {"p1": cfg.get("p1_seconds"),
                              "p2": cfg.get("p2_seconds"),
                              "discovery": cfg.get("discovery_seconds")},
               "durable_seconds": cfg.get("durable_seconds"),
               "retention_full_sessions": cfg.get("retention_full_sessions"),
               "storage_kb": est})
    return st
