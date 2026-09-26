"""live_scan.py — the Live Scanner: what is moving right now, and why (v5.29).

Jerry saw an open-source "Edge Scanner" (a day-trading alert feed built on
1-minute bars for 300 symbols) and asked for the same idea, made far better.
This module is the engine behind the app's version. It is built on what the
app already has, so it costs almost nothing to run:

* ONE batch quote per ~300 names, every 30 seconds while the market is open
  and every 60 seconds before it. Jerry's whole watchlist (about 1,300
  names) is ~5 calls a minute against a 110-a-minute budget. No streaming
  connection, no per-symbol bar fetches.

* Everything is derived from those quotes and the watchlist board: the day's
  high and low, the gap from yesterday's close, pre-market prices and
  volume, relative volume against the 20-day average, 5- and 15-minute moves
  from the scanner's own tick history, yesterday's range from the scanner's
  own end-of-day record, and the 52-week range from the board.

What makes it better than a feed of alerts:

1. Every alert says WHY in a sentence — "Broke above yesterday's high of
   $212.40 on 3.1x normal volume" — not a code.
2. Every alert is GRADED. The scanner keeps watching the stock after it
   fires and records how far it went in the alert's direction after 15, 30
   and 60 minutes. Each setup therefore carries its own measured track
   record ("followed through 58% of 212 times, +0.21% on average after 30
   minutes"), so a setup that does not work shows it.
3. Setup Check answers "why didn't this alert?" for any symbol: each setup,
   whether its trigger is live, and exactly which condition stopped it.
4. Setups are data (setups.json), edited on screen: rename, tune the
   trigger, set the conditions, the cooldown, and whether it pushes to the
   phone.

Nothing here raises into the server. Every public function returns data;
failures land in the snapshot's `error` so the screen can say what went
wrong. Dependencies are injected by configure() so the module is testable
with fake quotes and a fake clock.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from collections import deque
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

import market_calendar as _cal

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None

LIVE_SCAN_VERSION = "live-scan-1.0.0"

# ── cadence and budget ──────────────────────────────────────────────────────
SWEEP_OPEN_S = 30          # while the market is open
SWEEP_PRE_S = 30           # 4:00 to the open (was 60 until v5.31)
CHUNK = 300                # symbols per quote call
PRE_START = dtime(4, 0)
POST_END = dtime(20, 0)
TICKS_KEEP = 90            # 45 minutes of 30-second ticks: the breakout reads 5 + 30
ALERTS_KEEP_DAYS = 30      # graded alert history used for track records
MAX_ALERTS_DAY = 5000      # a runaway setup cannot fill the disk
PUSH_PER_HOUR = 12         # the phone is not a firehose
RANK_N = 25

# Base filter for the ranking lists: without it "top gainers" is a list of
# three-dollar stocks nobody can trade options on.
RANK_MIN_PRICE = 5.0
RANK_MIN_AVG_VOLUME = 500_000

# How a normal US session's volume accumulates, as a share of the day, at
# each half hour from the open. Volume is U-shaped — heavy at the open and
# close — so dividing by the fraction of TIME elapsed would call every stock
# "3x volume" at 9:45. Approximate, and the same for every name; it only has
# to put 10:00 and 15:30 on a common footing.
_VOL_CURVE = [(0, 0.0), (30, 0.13), (60, 0.22), (90, 0.29), (120, 0.35),
              (150, 0.41), (180, 0.46), (210, 0.51), (240, 0.56), (270, 0.61),
              (300, 0.67), (330, 0.74), (360, 0.83), (390, 1.0)]

# ── the triggers ────────────────────────────────────────────────────────────
# side: "long" / "short" / "either" (decided by the move's sign).
# once: fires at most once per symbol per day (a level crossing), otherwise
#       the setup's cooldown decides how often.
# params: the knobs the setup editor shows, with defaults and bounds.
TRIGGERS = {
    "new_hod": {
        "label": "New high of day", "side": "long", "once": False, "session": "open",
        "help": "The stock trades above every price it has printed today.",
        "params": {"after_min": (5, 0, 120, "Ignore the first N minutes after the open")},
    },
    "new_lod": {
        "label": "New low of day", "side": "short", "once": False, "session": "open",
        "help": "The stock trades below every price it has printed today.",
        "params": {"after_min": (5, 0, 120, "Ignore the first N minutes after the open")},
    },
    "range_break_up": {
        "label": "Breakout on volume", "side": "long", "once": False, "session": "open",
        "help": "Breaks above its highest price of the last N minutes while the last 5 minutes trade at least X times the volume of the 5-minute blocks before.",
        "params": {"lookback_min": (15, 5, 30, "Range to break (minutes)"),
                   "vol_mult": (2.0, 1.0, 10.0, "5-minute volume vs the blocks before (x)")},
    },
    "range_break_down": {
        "label": "Breakdown on volume", "side": "short", "once": False, "session": "open",
        "help": "Breaks below its lowest price of the last N minutes on at least X times the recent 5-minute volume.",
        "params": {"lookback_min": (15, 5, 30, "Range to break (minutes)"),
                   "vol_mult": (2.0, 1.0, 10.0, "5-minute volume vs the blocks before (x)")},
    },
    "gap_up_holding": {
        "label": "Gap up holding", "side": "long", "once": True, "session": "open",
        "help": "Opened at least X% above yesterday's close and is still at or above its opening price N minutes in.",
        "params": {"min_gap": (2.0, 0.5, 20.0, "Minimum gap (%)"),
                   "after_min": (15, 5, 120, "Check after N minutes")},
    },
    "gap_up_fading": {
        "label": "Gap up fading", "side": "short", "once": True, "session": "open",
        "help": "Opened at least X% above yesterday's close and has fallen back below its opening price by Y%.",
        "params": {"min_gap": (2.0, 0.5, 20.0, "Minimum gap (%)"),
                   "fade": (0.5, 0.0, 10.0, "Below the open by (%)")},
    },
    "gap_down_recovering": {
        "label": "Gap down recovering", "side": "long", "once": True, "session": "open",
        "help": "Opened at least X% below yesterday's close and has climbed back above its opening price by Y%.",
        "params": {"min_gap": (2.0, 0.5, 20.0, "Minimum gap (%)"),
                   "recover": (0.5, 0.0, 10.0, "Above the open by (%)")},
    },
    "gap_down_extending": {
        "label": "Gap down extending", "side": "short", "once": True, "session": "open",
        "help": "Opened at least X% below yesterday's close and is making new lows N minutes in.",
        "params": {"min_gap": (2.0, 0.5, 20.0, "Minimum gap (%)"),
                   "after_min": (15, 5, 120, "Check after N minutes")},
    },
    "above_prior_high": {
        "label": "Above yesterday's high", "side": "long", "once": True, "session": "open",
        "help": "Trades above yesterday's high. Needs one full day of scanning to know yesterday's range.",
        "params": {},
    },
    "below_prior_low": {
        "label": "Below yesterday's low", "side": "short", "once": True, "session": "open",
        "help": "Trades below yesterday's low. Needs one full day of scanning to know yesterday's range.",
        "params": {},
    },
    "high_52w": {
        "label": "New 52-week high", "side": "long", "once": True, "session": "open",
        "help": "Trades above its highest close of the last year.",
        "params": {},
    },
    "low_52w": {
        "label": "New 52-week low", "side": "short", "once": True, "session": "open",
        "help": "Trades below its lowest close of the last year.",
        "params": {},
    },
    "move_5m": {
        "label": "Fast 5-minute move", "side": "either", "once": False, "session": "open",
        "help": "Moves at least X% in five minutes, either way.",
        "params": {"pct": (1.0, 0.2, 10.0, "Move in 5 minutes (%)")},
    },
    "rvol_surge": {
        "label": "Volume surge", "side": "either", "once": True, "session": "open",
        "help": "Trading at least X times its normal volume for this time of day. Long if up on the day, short if down.",
        "params": {"mult": (3.0, 1.5, 20.0, "Times normal volume")},
    },
    # v5.34 — levels from Unusual Whales, fetched for the stocks in play
    # (see refresh_levels): where dealers' hedging flips, and the prices
    # the dark pools traded the most shares at.
    "gamma_flip_cross": {
        "label": "Crossed the gamma flip", "side": "either", "once": False, "session": "open",
        "help": "Crosses the gamma flip from Unusual Whales. Above it dealers sell rallies and buy dips, so moves calm; below it they chase the move, so swings get bigger. Levels are fetched for stocks on a ranked list.",
        "params": {},
    },
    "dark_pool_level": {
        "label": "Crossed a dark pool level", "side": "either", "once": False, "session": "open",
        "help": "Crosses one of the three prices where the most shares traded in dark pools today (Unusual Whales): prices big money cared about, which often act as support or resistance. Levels are fetched for stocks on a ranked list.",
        "params": {"min_shares": (100_000, 0, 50_000_000, "Dark pool shares at the level at least")},
    },
    "premarket_mover": {
        "label": "Pre-market mover", "side": "either", "once": True, "session": "pre",
        "help": "Before the open: at least X% from yesterday's close on at least N shares traded.",
        "params": {"pct": (2.0, 0.5, 30.0, "Move from yesterday's close (%)"),
                   "min_volume": (50_000, 0, 5_000_000, "Pre-market shares traded")},
    },
}

# The conditions every setup can carry. (default, min, max, label)
CONDITIONS = {
    "min_price": (10.0, 0.0, 10_000.0, "Price at least ($)"),
    "max_price": (0.0, 0.0, 100_000.0, "Price at most ($, 0 = no limit)"),
    "min_avg_volume": (1_000_000, 0, 1_000_000_000, "Average daily volume at least"),
    "min_market_cap": (2e9, 0, 1e13, "Market cap at least ($)"),
    "min_rvol": (0.0, 0.0, 50.0, "Relative volume at least (x)"),
    "min_change": (0.0, 0.0, 100.0, "Move on the day at least (%, either way)"),
}

DEFAULT_SETUPS = [
    ("new_hod", "New high of day", {}, {"min_rvol": 1.2}, 900),
    ("new_lod", "New low of day", {}, {"min_rvol": 1.2}, 900),
    ("range_break_up", "Breakout on volume", {}, {}, 1200),
    ("range_break_down", "Breakdown on volume", {}, {}, 1200),
    ("gap_up_holding", "Gap up holding", {}, {}, 0),
    ("gap_up_fading", "Gap up fading", {}, {}, 0),
    ("gap_down_recovering", "Gap down recovering", {}, {}, 0),
    ("gap_down_extending", "Gap down extending", {}, {}, 0),
    ("above_prior_high", "Above yesterday's high", {}, {"min_rvol": 1.0}, 0),
    ("below_prior_low", "Below yesterday's low", {}, {"min_rvol": 1.0}, 0),
    ("high_52w", "New 52-week high", {}, {}, 0),
    ("low_52w", "New 52-week low", {}, {}, 0),
    ("move_5m", "Fast 5-minute move", {}, {}, 1800),
    ("rvol_surge", "Volume surge", {}, {}, 0),
    ("premarket_mover", "Pre-market mover", {}, {}, 0),
    ("gamma_flip_cross", "Crossed the gamma flip", {}, {"min_rvol": 1.0}, 1800),
    ("dark_pool_level", "Crossed a dark pool level", {}, {"min_rvol": 1.0}, 1200),
]
# The triggers a setups.json written before a release already knew. A
# trigger added later is offered once to a saved list (see _load_setups);
# after that, deleting it from the list sticks.
TRIGGERS_BEFORE_V534 = frozenset({
    "new_hod", "new_lod", "range_break_up", "range_break_down", "gap_up_holding",
    "gap_up_fading", "gap_down_recovering", "gap_down_extending", "above_prior_high",
    "below_prior_low", "high_52w", "low_52w", "move_5m", "rvol_surge", "premarket_mover"})

# ── Unusual Whales levels (v5.34) ──────────────────────────────────────────
LEVEL_SYMS = 40            # stocks in play that get levels
LEVEL_TTL_S = 600          # a symbol's levels are refetched after this
LEVEL_FETCH_PER_PASS = 6   # fetches per refresh pass, so one pass stays short
LEVEL_LISTS = ("movers_5m", "rvol", "gainers", "losers", "active")

# Grading: minutes after an alert at which its move is recorded.
HORIZONS = (15, 30, 60)
# A graded alert "followed through" when it was in profit, in its own
# direction, this far after it fired.
FOLLOW_HORIZON = 30

# ── injected dependencies ──────────────────────────────────────────────────
_QUOTES_FN = None      # (symbols) -> {SYM: quote}
_UNIVERSE_FN = None    # () -> [board rows]
_NOTIFY_FN = None      # (title, message, priority=0) -> None
_NOW_FN = None         # () -> aware datetime in Eastern
_LEVELS_FN = None      # (symbol) -> {"gamma_flip": float|None, "dark": [[price, shares], ...]} | None
_DATA_DIR: Path | None = None

_LOCK = threading.RLock()
_STATE: dict = {}


def _fresh_state() -> dict:
    return {
        "day": None,             # the session date the state belongs to
        "sym": {},               # SYM -> per-symbol live state
        "alerts": [],            # today's alerts, newest last
        "pending": [],           # alerts still being graded
        "prior": {},             # SYM -> [high, low, close] of the prior session
        "levels": {},            # SYM -> {"at", "gamma_flip", "dark"} from Unusual Whales
        "rankings": {},
        "last_sweep": None, "phase": None, "error": None,
        "universe_n": 0, "quoted_n": 0, "sweeps": 0,
        "pushes": deque(maxlen=200),
        "scanning": False, "thread": None,
    }


_STATE.update(_fresh_state())
_SETUPS: list = []


def configure(quotes_fn=None, universe_fn=None, notify_fn=None, now_fn=None,
              data_dir=None, levels_fn=None) -> None:
    global _QUOTES_FN, _UNIVERSE_FN, _NOTIFY_FN, _NOW_FN, _DATA_DIR, _LEVELS_FN
    with _LOCK:
        _QUOTES_FN, _UNIVERSE_FN, _NOTIFY_FN, _NOW_FN = quotes_fn, universe_fn, notify_fn, now_fn
        _LEVELS_FN = levels_fn
        _DATA_DIR = Path(data_dir) / "live_scan" if data_dir else None
        keep = {"scanning": _STATE.get("scanning"), "thread": _STATE.get("thread")}
        _STATE.clear()
        _STATE.update(_fresh_state())
        _STATE.update(keep)
        _SETUPS[:] = _load_setups()
        _RECORDS_CACHE.update({"at": 0.0, "val": None})


# ── time ───────────────────────────────────────────────────────────────────
def _now() -> datetime:
    if _NOW_FN is not None:
        try:
            return _NOW_FN()
        except Exception:  # noqa: BLE001
            pass
    return datetime.now(_ET) if _ET is not None else datetime.now()


def phase(now: datetime | None = None) -> str:
    """"pre" (4:00 to the open), "open", "post" (the close to 20:00),
    "closed" (overnight, weekends, holidays)."""
    n = now or _now()
    span = _cal.session_span(n.date())
    if span is None:
        return "closed"
    o, c = span
    t = n.time()
    if PRE_START <= t < o:
        return "pre"
    if o <= t < c:
        return "open"
    if c <= t < POST_END:
        return "post"
    return "closed"


def minutes_since_open(now: datetime) -> float:
    span = _cal.session_span(now.date())
    if span is None:
        return 0.0
    o = now.replace(hour=span[0].hour, minute=span[0].minute, second=0, microsecond=0)
    return max(0.0, (now - o).total_seconds() / 60.0)


def volume_fraction(now: datetime) -> float:
    """Share of a normal day's volume that has usually traded by `now`."""
    m = minutes_since_open(now)
    span = _cal.session_span(now.date())
    if span is None:
        return 1.0
    full = _cal.session_seconds(now.date()) / 60.0 or 390.0
    # A half day compresses the curve rather than cutting it off.
    m = m * 390.0 / full
    if m <= 0:
        return 0.0
    for (m0, f0), (m1, f1) in zip(_VOL_CURVE, _VOL_CURVE[1:]):
        if m <= m1:
            return f0 + (f1 - f0) * (m - m0) / (m1 - m0)
    return 1.0


# ── persistence ────────────────────────────────────────────────────────────
def _path(name: str) -> Path | None:
    return (_DATA_DIR / name) if _DATA_DIR else None


def _read_json(name: str, default):
    p = _path(name)
    if not p or not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return default


def _write_json(name: str, obj) -> None:
    p = _path(name)
    if not p:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, separators=(",", ":")))
        tmp.replace(p)
    except Exception:  # noqa: BLE001
        pass


# ── setups ─────────────────────────────────────────────────────────────────
def _default_setup(trigger, name, params, conds, cooldown) -> dict:
    spec = TRIGGERS[trigger]
    return {
        "id": trigger, "name": name, "trigger": trigger, "enabled": True,
        "params": {k: params.get(k, v[0]) for k, v in spec["params"].items()},
        "conditions": {k: conds.get(k, v[0]) for k, v in CONDITIONS.items()},
        "cooldown_s": int(cooldown), "notify": False,
    }


def default_setups() -> list:
    return [_default_setup(*d) for d in DEFAULT_SETUPS]


def _clamp(v, lo, hi, default):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:
        return default
    return max(lo, min(hi, f))


def validate_setup(s: dict) -> dict | None:
    """One setup as the editor sent it, made safe: a known trigger, every
    number inside its bounds, unknown keys dropped. None when the trigger is
    not one this engine has — never a guess at what was meant."""
    if not isinstance(s, dict):
        return None
    trig = s.get("trigger")
    if trig not in TRIGGERS:
        return None
    spec = TRIGGERS[trig]
    params = s.get("params") or {}
    conds = s.get("conditions") or {}
    sid = str(s.get("id") or trig).strip()[:48] or trig
    name = str(s.get("name") or spec["label"]).strip()[:60] or spec["label"]
    return {
        "id": "".join(ch for ch in sid if ch.isalnum() or ch in "_-") or trig,
        "name": name, "trigger": trig, "enabled": bool(s.get("enabled", True)),
        "params": {k: _clamp(params.get(k, d), lo, hi, d) for k, (d, lo, hi, _l) in spec["params"].items()},
        "conditions": {k: _clamp(conds.get(k, d), lo, hi, d) for k, (d, lo, hi, _l) in CONDITIONS.items()},
        "cooldown_s": int(_clamp(s.get("cooldown_s", 600), 0, 86_400, 600)),
        "notify": bool(s.get("notify", False)),
    }


def _load_setups() -> list:
    raw = _read_json("setups.json", None)
    if not isinstance(raw, list):
        return default_setups()
    out, seen = [], set()
    for s in raw:
        v = validate_setup(s)
        if v and v["id"] not in seen:
            seen.add(v["id"])
            out.append(v)
    if not out:
        return default_setups()
    # A saved list predates any trigger added since it was written. Offer
    # each new one once, switched on; the offer is remembered, so a setup
    # Jerry deletes stays deleted (v5.34).
    offered = _read_json("offered.json", None)
    offered = set(offered) if isinstance(offered, list) else set(TRIGGERS_BEFORE_V534)
    have = {s["trigger"] for s in out}
    added = False
    for d in default_setups():
        if d["trigger"] not in offered and d["trigger"] not in have and d["id"] not in seen:
            out.append(d)
            seen.add(d["id"])
            added = True
    all_triggers = sorted(offered | set(TRIGGERS))
    if added or sorted(offered) != all_triggers:
        _write_json("offered.json", all_triggers)
        if added:
            _write_json("setups.json", out)
    return out


def setups() -> list:
    with _LOCK:
        return copy.deepcopy(_SETUPS)


def save_setups(items) -> dict:
    """Replace the setup list. Invalid entries are refused by name, not
    silently dropped, so the editor can say which one did not save."""
    if not isinstance(items, list):
        return {"ok": False, "error": "setups must be a list"}
    out, refused, seen = [], [], set()
    for s in items:
        v = validate_setup(s)
        if v is None:
            refused.append(str((s or {}).get("name") or (s or {}).get("trigger") or "?"))
            continue
        base, n = v["id"], 2
        while v["id"] in seen:
            v["id"] = f"{base}-{n}"
            n += 1
        seen.add(v["id"])
        out.append(v)
    if not out:
        # A list with nothing valid in it is a broken client, not a request
        # to delete every setup. Keep what is there.
        return {"ok": False, "error": "no valid setups in the list — nothing was saved",
                "refused": refused, "setups": setups()}
    with _LOCK:
        _SETUPS[:] = out
        _write_json("setups.json", out)
    return {"ok": True, "setups": copy.deepcopy(out), "refused": refused}


def reset_setups() -> dict:
    return save_setups(default_setups())


# ── the per-symbol picture ─────────────────────────────────────────────────
def _num(v):
    try:
        f = float(v)
        return f if f == f and f not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _pct(a, b):
    return (a / b - 1.0) * 100.0 if (a is not None and b) else None


def _fmt_money(v) -> str:
    return "—" if v is None else (f"${v:,.2f}" if v < 1000 else f"${v:,.0f}")


def _fmt_vol(v) -> str:
    if v is None:
        return "—"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= div:
            return f"{v / div:.1f}{suf}"
    return f"{int(v)}"


def _ticks_since(ticks, cutoff_ts):
    return [t for t in ticks if t[0] >= cutoff_ts]


def _window_move(ticks, now_ts, minutes):
    """% move over the last `minutes`, from the scanner's own ticks. None
    until the scanner has watched that long."""
    if not ticks:
        return None
    cutoff = now_ts - minutes * 60
    older = [t for t in ticks if t[0] <= cutoff + 15]
    # The reference price has to be from about `minutes` ago, not merely
    # older: after a gap in sweeps (a restart, a slow broker) the newest
    # tick before the cutoff can be ten minutes old, and a 10-minute move
    # would be reported as a 5-minute one.
    if not older or older[-1][0] < cutoff - 90:
        return None
    return _pct(ticks[-1][1], older[-1][1])


def _interval_volume(ticks, t0, t1):
    """Shares traded between two moments, from cumulative volume."""
    a = [t for t in ticks if t[0] <= t0]
    b = [t for t in ticks if t[0] <= t1]
    if not a or not b or a[-1][2] is None or b[-1][2] is None:
        return None
    return max(0.0, b[-1][2] - a[-1][2])


def _premarket_print(q: dict, now: datetime) -> tuple:
    """(price, volume) of this morning's newest extended-hours trade, or
    (None, None) when nothing has traded since 4:00 today.

    Jerry, at 8:55 (v5.31): "Why is this stale." The lists read Schwab's
    EXTENDED price unconditionally, and it held an old print ($133.91 on
    AKAM) while the newest pre-market trade ($126.37) sat in the regular
    quote — the price the sidebar showed, because the broker client picks
    by trade time. This picks the same way, plus the one rule a pre-market
    list needs on top: the trade has to be from this morning. A print from
    last night's after-hours is not a pre-market move."""
    ext, reg = _num(q.get("extended_last")), _num(q.get("regular_last"))
    ext_t, reg_t = _num(q.get("extended_trade_ms")), _num(q.get("regular_trade_ms"))
    if ext_t is None and reg_t is None:
        # A source without per-print times: the extended field is all
        # there is to go on.
        return ext, _num(q.get("extended_volume"))
    start = now.replace(hour=PRE_START.hour, minute=PRE_START.minute, second=0,
                        microsecond=0).timestamp() * 1000.0
    # ...and before the bell: after 9:30 a regular trade is also "after
    # 4:00", and the pre-market lists would track intraday prices all day
    # (Codex, #418). The sweep keeps the last pre-market print for later.
    span = _cal.session_span(now.date())
    o = span[0] if span else dtime(9, 30)
    end = now.replace(hour=o.hour, minute=o.minute, second=0, microsecond=0).timestamp() * 1000.0
    cands = []
    if ext and ext_t and start <= ext_t < end:
        cands.append((ext_t, ext, _num(q.get("extended_volume"))))
    if reg and reg_t and start <= reg_t < end:
        # The regular quote only carries this morning's trades when it has
        # traded this morning; then its volume is this morning's too.
        cands.append((reg_t, reg, _num(q.get("volume"))))
    if not cands:
        return None, None
    # By time ONLY: on a tie the tuple would go on to compare volumes, and
    # a missing one raises mid-sweep (Codex, #418).
    _t, price, vol = max(cands, key=lambda c: c[0])
    return price, vol


def _picture(sym: str, q: dict, row: dict, st: dict, now: datetime) -> dict:
    """Everything the triggers, conditions and rankings read for one
    symbol, computed once per sweep."""
    last = _num(q.get("regular_last")) or _num(q.get("last"))
    ext, ext_vol = _premarket_print(q, now)
    # Remember this morning's last pre-market print, so after the bell the
    # pre-market lists still show it even when the quote's own pre-market
    # fields have been overwritten by the session.
    if ext is not None:
        st["pm_keep"] = (ext, ext_vol)
    elif st.get("pm_keep"):
        ext, ext_vol = st["pm_keep"]
    prev = _num(q.get("close_prev"))
    opn = _num(q.get("open"))
    vol = _num(q.get("volume"))
    avgv = _num(row.get("avg_volume"))
    frac = volume_fraction(now)
    rvol = (vol / (avgv * frac)) if (vol and avgv and frac >= 0.02) else None
    ticks = st["ticks"]
    now_ts = now.timestamp()
    return {
        "symbol": sym, "last": last, "prev_close": prev, "open": opn,
        "high": _num(q.get("high")), "low": _num(q.get("low")),
        "volume": vol, "avg_volume": avgv, "rvol": round(rvol, 2) if rvol else None,
        "change_pct": _pct(last, prev),
        "gap_pct": _pct(opn, prev) if opn else None,
        "pm_last": ext, "pm_change_pct": _pct(ext, prev),
        "pm_volume": ext_vol,
        "move_5m": _window_move(ticks, now_ts, 5),
        "move_15m": _window_move(ticks, now_ts, 15),
        "market_cap": _num(row.get("market_cap")),
        "high_52w": _num(row.get("high_52w")), "low_52w": _num(row.get("low_52w")),
        "sector": row.get("sector"), "tag": (row.get("tag") or None),
        "name": q.get("name") or row.get("company"),
        "prior": (_STATE["prior"] or {}).get(sym),
        "minutes": minutes_since_open(now),
        "gamma_flip": ((_STATE.get("levels") or {}).get(sym) or {}).get("gamma_flip"),
        "dark_levels": ((_STATE.get("levels") or {}).get(sym) or {}).get("dark") or [],
    }


# ── trigger evaluation ─────────────────────────────────────────────────────
NO_LEVELS = ("No Unusual Whales levels for this stock yet: they are fetched for the "
             "stocks on a ranked list, refreshed every 10 minutes.")


def _trigger_state(trig: str, p: dict, prev_p: dict | None, st: dict, params: dict,
                   now: datetime) -> tuple:
    """(live, crossed, side, sentence). `live` is whether the condition
    holds now (Setup Check reads it); `crossed` is whether it became true on
    THIS sweep (alerts fire on that). Never raises."""
    if trig == "premarket_mover":
        # Before the bell there is no regular-session price by design; this
        # one reads the extended tape, so it is answered before that check.
        m, v = p["pm_change_pct"], p["pm_volume"]
        if m is None:
            return False, False, None, "No pre-market trade yet."
        live = abs(m) >= params.get("pct", 2.0) and (v or 0) >= params.get("min_volume", 50_000)
        return live, live, ("long" if m > 0 else "short"), \
            f"Pre-market {m:+.1f}% at {_fmt_money(p['pm_last'])} on {_fmt_vol(v)} shares."
    last, prev_close, opn = p["last"], p["prev_close"], p["open"]
    if last is None:
        return False, False, None, "No price yet."
    was = (lambda k: (prev_p or {}).get(k))
    rv = f" on {p['rvol']:.1f}x normal volume" if p.get("rvol") else ""

    if trig in ("gamma_flip_cross", "dark_pool_level"):
        prev_last = was("last")
        if trig == "gamma_flip_cross":
            flip = p.get("gamma_flip")
            if flip is None:
                return False, False, None, NO_LEVELS
            live = abs(last - flip) / flip < 0.002 if flip else False
            up = prev_last is not None and prev_last < flip <= last
            down = prev_last is not None and prev_last > flip >= last
            if up:
                return live, True, "long", (f"Crossed above the gamma flip at {_fmt_money(flip)}{rv}: dealers "
                                            "now sell rallies and buy dips, so moves should calm.")
            if down:
                return live, True, "short", (f"Fell below the gamma flip at {_fmt_money(flip)}{rv}: dealers "
                                             "now chase the move, so swings can get bigger.")
            where = "above" if last >= flip else "below"
            return live, False, None, f"Trading {where} the gamma flip at {_fmt_money(flip)}."
        levels = [(float(px), int(sh)) for px, sh in (p.get("dark_levels") or [])
                  if px and sh >= params.get("min_shares", 100_000)]
        if not levels:
            return False, False, None, NO_LEVELS
        live = any(abs(last - px) / px < 0.002 for px, _sh in levels)
        if prev_last is not None:
            for px, sh in levels:
                if prev_last < px <= last:
                    return live, True, "long", (f"Crossed above {_fmt_money(px)}{rv}, where {_fmt_vol(sh)} shares "
                                                "traded in dark pools today: a price big money cared about.")
                if prev_last > px >= last:
                    return live, True, "short", (f"Fell below {_fmt_money(px)}{rv}, where {_fmt_vol(sh)} shares "
                                                 "traded in dark pools today: a price big money cared about.")
        near = min(levels, key=lambda t: abs(last - t[0]))
        return live, False, None, (f"Nearest dark pool level {_fmt_money(near[0])} "
                                   f"({_fmt_vol(near[1])} shares), {abs(_pct(near[0], last) or 0):.1f}% away.")

    if trig in ("new_hod", "new_lod"):
        hi = trig == "new_hod"
        cur = p["high"] if hi else p["low"]
        before = was("high") if hi else was("low")
        if cur is None:
            return False, False, None, "No high/low yet."
        live = p["minutes"] >= params.get("after_min", 5) and (
            abs(last - cur) / cur < 0.001 if cur else False)
        crossed = (before is not None and p["minutes"] >= params.get("after_min", 5)
                   and ((cur > before) if hi else (cur < before)))
        word = "high" if hi else "low"
        return live, crossed, ("long" if hi else "short"), \
            f"New {word} of the day at {_fmt_money(cur)}{rv}."

    if trig in ("range_break_up", "range_break_down"):
        up = trig == "range_break_up"
        ticks = list(st["ticks"])
        if len(ticks) < 3:
            return False, False, None, "Still watching — needs a few minutes of history."
        now_ts = now.timestamp()
        lb = params.get("lookback_min", 15) * 60
        window = [t for t in ticks[:-1] if t[0] >= now_ts - lb]
        if not window:
            return False, False, None, "Still watching — needs a few minutes of history."
        level = max(t[1] for t in window) if up else min(t[1] for t in window)
        v_recent = _interval_volume(ticks, now_ts - 300, now_ts)
        v_before = _interval_volume(ticks, now_ts - 300 - 1800, now_ts - 300)
        # Average 5-minute volume over the half hour before the last five.
        base = (v_before / 6.0) if v_before else None
        mult = (v_recent / base) if (v_recent is not None and base) else None
        vol_ok = mult is not None and mult >= params.get("vol_mult", 2.0)
        beyond = last > level if up else last < level
        prev_last = was("last")
        prev_beyond = (prev_last is not None and (prev_last > level if up else prev_last < level))
        word = "above" if up else "below"
        span = int(params.get("lookback_min", 15))
        sent = (f"Broke {word} its {span}-minute {'high' if up else 'low'} of {_fmt_money(level)}"
                + (f" on {mult:.1f}x the recent 5-minute volume." if mult else "."))
        return beyond and vol_ok, beyond and vol_ok and not prev_beyond, \
            ("long" if up else "short"), sent

    if trig.startswith("gap_"):
        gap = p["gap_pct"]
        if gap is None or opn is None:
            return False, False, None, "No opening price yet."
        g = params.get("min_gap", 2.0)
        after = params.get("after_min", 15)
        if trig == "gap_up_holding":
            live = gap >= g and p["minutes"] >= after and last >= opn
            s = f"Gapped up {gap:.1f}% and is holding above its open of {_fmt_money(opn)}{rv}."
            return live, live, "long", s
        if trig == "gap_up_fading":
            lvl = opn * (1 - params.get("fade", 0.5) / 100.0)
            live = gap >= g and last < lvl
            s = f"Gapped up {gap:.1f}% but has fallen back below its open of {_fmt_money(opn)}."
            return live, live, "short", s
        if trig == "gap_down_recovering":
            lvl = opn * (1 + params.get("recover", 0.5) / 100.0)
            live = gap <= -g and last > lvl
            s = f"Gapped down {abs(gap):.1f}% and has climbed back above its open of {_fmt_money(opn)}."
            return live, live, "long", s
        if trig == "gap_down_extending":
            lo = p["low"]
            live = gap <= -g and p["minutes"] >= after and lo is not None and last <= lo * 1.001 and last < opn
            s = f"Gapped down {abs(gap):.1f}% and is still making new lows at {_fmt_money(lo)}{rv}."
            return live, live, "short", s

    if trig in ("above_prior_high", "below_prior_low"):
        pr = p.get("prior")
        if not pr:
            return False, False, None, "Yesterday's range is not known yet — the scanner records it at each close."
        up = trig == "above_prior_high"
        lvl = pr[0] if up else pr[1]
        live = last > lvl if up else last < lvl
        prev_last = was("last")
        prev_live = prev_last is not None and (prev_last > lvl if up else prev_last < lvl)
        word = "above yesterday's high" if up else "below yesterday's low"
        return live, live and not prev_live, ("long" if up else "short"), \
            f"Broke {word} of {_fmt_money(lvl)}{rv}."

    if trig in ("high_52w", "low_52w"):
        up = trig == "high_52w"
        lvl = p["high_52w"] if up else p["low_52w"]
        if not lvl:
            return False, False, None, "No 52-week range on the board for this name."
        live = last > lvl if up else last < lvl
        prev_last = was("last")
        prev_live = prev_last is not None and (prev_last > lvl if up else prev_last < lvl)
        return live, live and not prev_live, ("long" if up else "short"), \
            f"New 52-week {'high' if up else 'low'}: through {_fmt_money(lvl)}{rv}."

    if trig == "move_5m":
        m = p["move_5m"]
        if m is None:
            return False, False, None, "Still watching — needs five minutes of history."
        live = abs(m) >= params.get("pct", 1.0)
        return live, live, ("long" if m > 0 else "short"), \
            f"Moved {m:+.1f}% in five minutes, now {_fmt_money(last)}{rv}."

    if trig == "rvol_surge":
        r = p["rvol"]
        if r is None:
            return False, False, None, "Relative volume is not measurable yet."
        live = r >= params.get("mult", 3.0)
        ch = p["change_pct"] or 0.0
        return live, live, ("long" if ch >= 0 else "short"), \
            f"Trading {r:.1f}x its normal volume for this time of day, {ch:+.1f}% on the day."

    return False, False, None, "Unknown trigger."


def _blocked_by(conds: dict, p: dict) -> list:
    """The conditions this symbol fails, in words. Empty = it passes."""
    out = []
    price = p["last"] if p["last"] is not None else p.get("pm_last")
    if conds.get("min_price") and (price is None or price < conds["min_price"]):
        out.append(f"price {_fmt_money(price)} is under {_fmt_money(conds['min_price'])}")
    if conds.get("max_price") and price is not None and price > conds["max_price"]:
        out.append(f"price {_fmt_money(price)} is over {_fmt_money(conds['max_price'])}")
    if conds.get("min_avg_volume") and (p["avg_volume"] or 0) < conds["min_avg_volume"]:
        out.append(f"average volume {_fmt_vol(p['avg_volume'])} is under {_fmt_vol(conds['min_avg_volume'])}")
    if conds.get("min_market_cap") and (p["market_cap"] or 0) < conds["min_market_cap"]:
        out.append(f"market cap {_fmt_vol(p['market_cap'])} is under {_fmt_vol(conds['min_market_cap'])}")
    if conds.get("min_rvol") and (p["rvol"] or 0) < conds["min_rvol"]:
        out.append(f"relative volume {p['rvol'] or 0:.1f}x is under {conds['min_rvol']:.1f}x")
    if conds.get("min_change"):
        ch = p["change_pct"] if p["change_pct"] is not None else p.get("pm_change_pct")
        if ch is None or abs(ch) < conds["min_change"]:
            out.append(f"move on the day {0 if ch is None else ch:+.1f}% is under ±{conds['min_change']:.1f}%")
    return out


# ── the sweep ──────────────────────────────────────────────────────────────
def _roll_day(now: datetime) -> None:
    """A new session: yesterday's final highs and lows become the prior-day
    range, and every per-day memory starts over."""
    day = now.date().isoformat()
    if _STATE["day"] == day:
        return
    # The record written at the end of the last session we saw.
    rec = _read_json("session_hl.json", {})
    if isinstance(rec, dict) and rec.get("date") and rec["date"] < day:
        _STATE["prior"] = rec.get("rows") or {}
    elif isinstance(rec, dict) and rec.get("date") == day and (rec.get("prior") or {}).get("rows"):
        # A restart in the middle of a session: today's record has already
        # replaced yesterday's as the main one, so yesterday rides along in
        # it (Codex, #416). Without this the prior-day setups went dark for
        # the rest of the day after any deploy.
        _STATE["prior"] = rec["prior"]["rows"]
    elif _STATE["day"] and _STATE["sym"]:
        _STATE["prior"] = {s: [v["pic"]["high"], v["pic"]["low"], v["pic"]["last"]]
                           for s, v in _STATE["sym"].items()
                           if v.get("pic") and v["pic"].get("high") and v["pic"].get("low")}
    else:
        _STATE["prior"] = {}
    _STATE["day"] = day
    _STATE["sym"] = {}
    _STATE["levels"] = {}
    _STATE["alerts"] = _read_json(f"alerts-{day}.json", []) or []
    _STATE["pending"] = [a for a in _STATE["alerts"] if not (a.get("grade") or {}).get("done")]


def _fired_today(sym: str) -> dict:
    """What already fired for this symbol today, from the alert log — so a
    restart in the middle of the session cannot fire a once-a-day alert a
    second time."""
    out = {}
    for a in _STATE["alerts"]:
        if a.get("symbol") == sym:
            out[a["setup_id"]] = max(out.get(a["setup_id"], 0), a["ts"])
    return out


def _save_session_hl() -> None:
    rows = {s: [v["pic"]["high"], v["pic"]["low"], v["pic"]["last"]]
            for s, v in _STATE["sym"].items()
            if v.get("pic") and v["pic"].get("high") and v["pic"].get("low")}
    if rows:
        _write_json("session_hl.json", {"date": _STATE["day"], "rows": rows,
                                        "prior": {"rows": _STATE["prior"] or {}}})


def _grade(now: datetime) -> bool:
    """Move each pending alert's record forward. True when anything changed."""
    changed = False
    now_ts = now.timestamp()
    still = []
    for a in _STATE["pending"]:
        st = _STATE["sym"].get(a["symbol"])
        g = a.setdefault("grade", {})
        pic = (st or {}).get("pic") or {}
        # Before the bell the regular-session price is cleared by design;
        # a pre-market alert is graded on the pre-market tape, and switches
        # to the regular price only once there is one (Codex, #416).
        last = pic.get("last") if pic.get("last") is not None else pic.get("pm_last")
        if last and a.get("price"):
            sign = 1.0 if a["side"] == "long" else -1.0
            r = (last / a["price"] - 1.0) * 100.0 * sign
            g["mfe"] = round(max(g.get("mfe", r), r), 2)
            g["mae"] = round(min(g.get("mae", r), r), 2)
            for h in HORIZONS:
                k = f"r{h}"
                if k not in g and now_ts >= a["ts"] + h * 60:
                    g[k] = round(r, 2)
                    changed = True
        if all(f"r{h}" in g for h in HORIZONS) or phase(now) != "open" and a.get("session") == "open":
            if not g.get("done"):
                g["done"] = True
                changed = True
        else:
            still.append(a)
    _STATE["pending"] = still
    return changed


def _maybe_push(alert: dict, setup: dict) -> None:
    if not setup.get("notify") or _NOTIFY_FN is None:
        return
    now_ts = alert["ts"]
    recent = [t for t in _STATE["pushes"] if t > now_ts - 3600]
    if len(recent) >= PUSH_PER_HOUR:
        return
    _STATE["pushes"].append(now_ts)
    try:
        arrow = "▲" if alert["side"] == "long" else "▼"
        _NOTIFY_FN(f"{arrow} {alert['symbol']} · {setup['name']}", alert["why"], 0)
    except Exception:  # noqa: BLE001
        pass


def _rank(pics: list, key, reverse=True, filt=None, n=RANK_N) -> list:
    rows = [p for p in pics if (filt is None or filt(p)) and key(p) is not None]
    rows.sort(key=key, reverse=reverse)
    return [{"symbol": p["symbol"], "last": p["last"] if p["last"] is not None else p.get("pm_last"),
             "change_pct": _r(p["change_pct"]), "rvol": p["rvol"],
             "volume": p["volume"], "move_5m": _r(p["move_5m"]),
             "gap_pct": _r(p["gap_pct"]), "pm_change_pct": _r(p["pm_change_pct"]),
             "pm_volume": p["pm_volume"], "pm_last": p.get("pm_last"),
             # Jerry's own watchlist tag first, the sector under it: the
             # lists show one group label per row so a sector moving
             # together reads at a glance (v5.32).
             "tag": p.get("tag"), "sector": p.get("sector")}
            for p in rows[:n]]


def _r(v, d=2):
    return round(v, d) if v is not None else None


def build_rankings(pics: list, ph: str) -> dict:
    liquid = (lambda p: (p["last"] or p.get("pm_last") or 0) >= RANK_MIN_PRICE
              and (p["avg_volume"] or 0) >= RANK_MIN_AVG_VOLUME)
    out = {
        "gainers": _rank(pics, lambda p: p["change_pct"], True, liquid),
        "losers": _rank(pics, lambda p: p["change_pct"], False, liquid),
        "active": _rank(pics, lambda p: p["volume"], True, liquid),
        "rvol": _rank(pics, lambda p: p["rvol"], True, liquid),
        "movers_5m": _rank(pics, lambda p: abs(p["move_5m"]) if p["move_5m"] is not None else None, True, liquid),
        "gap_up": _rank(pics, lambda p: p["gap_pct"], True, lambda p: liquid(p) and (p["gap_pct"] or 0) > 0),
        "gap_down": _rank(pics, lambda p: p["gap_pct"], False, lambda p: liquid(p) and (p["gap_pct"] or 0) < 0),
        "pm_gainers": _rank(pics, lambda p: p["pm_change_pct"], True, liquid),
        "pm_losers": _rank(pics, lambda p: p["pm_change_pct"], False, liquid),
        "pm_volume": _rank(pics, lambda p: p["pm_volume"], True, liquid),
    }
    return out


def sweep(now: datetime | None = None) -> dict:
    """One pass over the universe: quote, picture, rank, alert, grade.
    Returns a short summary. Safe to call from a test with fakes."""
    now = now or _now()
    ph = phase(now)
    with _LOCK:
        _roll_day(now)
        _STATE["phase"] = ph
        if ph not in ("pre", "open"):
            return {"ok": True, "phase": ph, "alerts": 0}
        rows = []
        try:
            rows = list((_UNIVERSE_FN() if _UNIVERSE_FN else []) or [])
        except Exception as exc:  # noqa: BLE001
            _STATE["error"] = f"watchlist unavailable: {exc}"
        by_sym = {}
        for r in rows:
            s = str(r.get("symbol") or r.get("ticker") or "").upper().strip()
            if s:
                by_sym[s] = r
        _STATE["universe_n"] = len(by_sym)
        setups_now = [s for s in _SETUPS if s.get("enabled")]
    if not by_sym or _QUOTES_FN is None:
        return {"ok": False, "phase": ph, "error": "no universe or no quote source"}

    syms = sorted(by_sym)
    quotes, failed = {}, 0
    for i in range(0, len(syms), CHUNK):
        try:
            got = _QUOTES_FN(syms[i:i + CHUNK]) or {}
            quotes.update({k.upper(): v for k, v in got.items() if isinstance(v, dict)})
        except Exception:  # noqa: BLE001
            failed += 1
    new_alerts = []
    with _LOCK:
        _STATE["quoted_n"] = len(quotes)
        _STATE["error"] = (None if quotes else
                           "The broker returned no quotes — check the Schwab sign-in under Manage.")
        now_ts = now.timestamp()
        pics = []
        for sym, q in quotes.items():
            st = _STATE["sym"].get(sym)
            if st is None:
                st = _STATE["sym"][sym] = {"ticks": deque(maxlen=TICKS_KEEP), "pic": None,
                                           "fired": _fired_today(sym), "live": {}, "phase": ph}
            if st.get("phase") != ph:
                # The bell: pre-market ticks are extended-hours prices and
                # volumes; mixing them into the session's 5-minute and
                # breakout windows would compare two different tapes.
                st["ticks"].clear()
                st["pic"] = None
                st["phase"] = ph
            if ph == "open":
                price, vol = _num(q.get("regular_last")) or _num(q.get("last")), _num(q.get("volume"))
            else:
                price, vol = _premarket_print(q, now)
            if price:
                st["ticks"].append((now_ts, price, vol))
            prev_pic = st["pic"]
            pic = _picture(sym, q, by_sym.get(sym, {}), st, now)
            if ph == "pre":
                # Before the bell the regular-session fields describe
                # yesterday. The pre-market fields are the live ones.
                pic.update({"last": None, "change_pct": None, "gap_pct": None,
                            "high": None, "low": None, "rvol": None, "volume": None})
            st["pic"] = pic
            pics.append(pic)
            if prev_pic is None:
                continue        # the first look at a symbol only sets the baseline
            for s in setups_now:
                spec = TRIGGERS[s["trigger"]]
                if spec["session"] != ph:
                    continue
                live, crossed, side, why = _trigger_state(s["trigger"], pic, prev_pic, st, s["params"], now)
                st["live"][s["id"]] = live
                if not crossed or side is None:
                    continue
                if _blocked_by(s["conditions"], pic):
                    continue
                last_fire = st["fired"].get(s["id"])
                if last_fire is not None and (spec["once"] or now_ts - last_fire < s["cooldown_s"]):
                    continue
                if len(_STATE["alerts"]) >= MAX_ALERTS_DAY:
                    continue
                st["fired"][s["id"]] = now_ts
                price_now = pic["last"] if pic["last"] is not None else pic.get("pm_last")
                a = {"id": f"{sym}-{s['id']}-{int(now_ts)}", "ts": now_ts,
                     "time": now.strftime("%H:%M"), "symbol": sym, "side": side,
                     "setup_id": s["id"], "setup": s["name"], "trigger": s["trigger"],
                     "session": ph, "price": price_now, "why": why,
                     "change_pct": _r(pic["change_pct"] if ph == "open" else pic["pm_change_pct"]),
                     "rvol": pic["rvol"], "grade": {}}
                _STATE["alerts"].append(a)
                _STATE["pending"].append(a)
                new_alerts.append((a, s))
        graded = _grade(now)
        _STATE["rankings"] = build_rankings(pics, ph)
        _STATE["last_sweep"] = now.isoformat()
        _STATE["sweeps"] += 1
        if new_alerts or graded:
            _write_json(f"alerts-{_STATE['day']}.json", _STATE["alerts"])
        if ph == "open" and _STATE["sweeps"] % 10 == 0:
            _save_session_hl()
    for a, s in new_alerts:
        _maybe_push(a, s)
    return {"ok": True, "phase": ph, "quoted": len(quotes), "alerts": len(new_alerts),
            "failed_chunks": failed}


# ── Unusual Whales levels (v5.34) ──────────────────────────────────────────
def levels_wanted() -> list:
    """The stocks in play, in order: the fast movers and volume surges
    first, then the day's biggest gainers, losers and most active. Each
    list is walked in turn so one list cannot take every slot."""
    with _LOCK:
        ranks = _STATE.get("rankings") or {}
        lists = [[r["symbol"] for r in (ranks.get(k) or [])] for k in LEVEL_LISTS]
    out, seen = [], set()
    for i in range(RANK_N):
        for lst in lists:
            if i < len(lst) and lst[i] not in seen:
                seen.add(lst[i])
                out.append(lst[i])
                if len(out) >= LEVEL_SYMS:
                    return out
    return out


def refresh_levels(now: datetime | None = None) -> int:
    """Fetch levels for in-play stocks whose levels are missing or older
    than LEVEL_TTL_S, a few per pass. Returns how many were fetched. The
    network calls happen outside the lock."""
    if _LEVELS_FN is None:
        return 0
    now = now or _now()
    if phase(now) != "open":
        return 0
    ts = now.timestamp()
    with _LOCK:
        have = dict(_STATE.get("levels") or {})
    due = [s for s in levels_wanted() if ts - (have.get(s) or {}).get("at", 0) >= LEVEL_TTL_S]
    done = 0
    for sym in due[:LEVEL_FETCH_PER_PASS]:
        try:
            got = _LEVELS_FN(sym)
        except Exception:  # noqa: BLE001
            got = None
        rec = {"at": ts, "gamma_flip": None, "dark": []}
        if isinstance(got, dict):
            rec["gamma_flip"] = _num(got.get("gamma_flip"))
            rec["dark"] = [[_num(px), int(_num(sh) or 0)] for px, sh in (got.get("dark") or [])
                           if _num(px)]
        with _LOCK:
            _STATE["levels"][sym] = rec
        done += 1
    return done


# ── track records ──────────────────────────────────────────────────────────
_RECORDS_CACHE: dict = {"at": 0.0, "val": None}


def track_records(days: int = 20, now: datetime | None = None, fresh: bool = False) -> dict:
    """Per setup: how its graded alerts did. {setup_id: {n, followed,
    follow_rate, avg_r30, avg_mfe}} over the last `days` sessions on disk
    plus today's. Cached for a minute: the screen polls far more often than
    a grade can change."""
    if not fresh and _RECORDS_CACHE["val"] is not None and time.time() - _RECORDS_CACHE["at"] < 60:
        return copy.deepcopy(_RECORDS_CACHE["val"])
    files = []
    if _DATA_DIR and _DATA_DIR.exists():
        files = sorted(_DATA_DIR.glob("alerts-*.json"))[-days:]
    seen, rows = set(), []
    for f in files:
        try:
            for a in json.loads(f.read_text()) or []:
                if a.get("id") not in seen:
                    seen.add(a.get("id"))
                    rows.append(a)
        except Exception:  # noqa: BLE001
            continue
    with _LOCK:
        for a in _STATE["alerts"]:
            if a.get("id") not in seen:
                seen.add(a.get("id"))
                rows.append(a)
    k = f"r{FOLLOW_HORIZON}"
    out: dict = {}
    for a in rows:
        g = a.get("grade") or {}
        if k not in g:
            continue
        s = out.setdefault(a.get("setup_id"), {"n": 0, "followed": 0, "sum": 0.0, "mfe": 0.0})
        s["n"] += 1
        s["followed"] += 1 if g[k] > 0 else 0
        s["sum"] += g[k]
        s["mfe"] += g.get("mfe", 0.0)
    for s in out.values():
        s["follow_rate"] = round(s["followed"] / s["n"] * 100.0, 0) if s["n"] else None
        s["avg_r30"] = round(s.pop("sum") / s["n"], 2) if s["n"] else None
        s["avg_mfe"] = round(s.pop("mfe") / s["n"], 2) if s["n"] else None
    _RECORDS_CACHE.update({"at": time.time(), "val": copy.deepcopy(out)})
    return out


def cleanup_history() -> None:
    if not (_DATA_DIR and _DATA_DIR.exists()):
        return
    files = sorted(_DATA_DIR.glob("alerts-*.json"))
    for f in files[:-ALERTS_KEEP_DAYS]:
        try:
            f.unlink()
        except OSError:
            pass


# ── what the screen reads ──────────────────────────────────────────────────
def snapshot(since_ts: float | None = None, limit: int = 300) -> dict:
    with _LOCK:
        alerts = _STATE["alerts"]
        if since_ts:
            alerts = [a for a in alerts if a["ts"] > since_ts]
        alerts = alerts[-limit:][::-1]
        counts: dict = {}
        for a in _STATE["alerts"]:
            counts[a["setup_id"]] = counts.get(a["setup_id"], 0) + 1
        out = {
            "version": LIVE_SCAN_VERSION,
            "phase": _STATE["phase"] or phase(),
            "last_sweep": _STATE["last_sweep"],
            "universe_n": _STATE["universe_n"], "quoted_n": _STATE["quoted_n"],
            "error": _STATE["error"], "scanning": bool(_STATE["scanning"]),
            "alerts": copy.deepcopy(alerts),
            "alerts_today": len(_STATE["alerts"]),
            "counts": counts,
            "rankings": copy.deepcopy(_STATE["rankings"]),
            "prior_known": bool(_STATE["prior"]),
        }
    out["records"] = track_records()
    return out


def check(symbol: str, now: datetime | None = None) -> dict:
    """Setup Check: for one symbol, every setup — is its trigger live, would
    the conditions let it through, and when did it last fire today."""
    sym = str(symbol or "").upper().strip()
    now = now or _now()
    with _LOCK:
        st = _STATE["sym"].get(sym)
        if not st or not st.get("pic"):
            return {"symbol": sym, "known": False,
                    "why": (f"{sym} is not on the scanned watchlist, or the scanner has not quoted it yet."
                            if sym else "No symbol.")}
        pic = dict(st["pic"])
        rows = []
        for s in _SETUPS:
            spec = TRIGGERS[s["trigger"]]
            live, _c, side, why = _trigger_state(s["trigger"], pic, pic, st, s["params"], now)
            blocked = _blocked_by(s["conditions"], pic)
            fired = st["fired"].get(s["id"])
            # One status per row, decided by eligibility FIRST: a switched-
            # off setup, or one for the other session, is never "live" on
            # the screen however its raw trigger reads (Codex, #417).
            if not s.get("enabled"):
                status, verdict = "off", "Setup is switched off."
            elif spec["session"] != (_STATE["phase"] or phase(now)):
                status = "session"
                verdict = ("Runs before the open only." if spec["session"] == "pre"
                           else "Runs while the market is open only.")
            elif fired is not None:
                status = "fired"
                verdict = f"Fired at {datetime.fromtimestamp(fired, now.tzinfo).strftime('%H:%M')}."
            elif live and blocked:
                status, verdict = "live_blocked", "Trigger is live, but " + "; ".join(blocked) + "."
            elif live:
                status = "live"
                verdict = "Trigger is live and nothing blocks it — it fires on the next crossing."
            elif blocked:
                status, verdict = "blocked", "Trigger is not live, and " + "; ".join(blocked) + "."
            else:
                status, verdict = "idle", "Trigger is not live."
            rows.append({"setup_id": s["id"], "setup": s["name"], "trigger": s["trigger"],
                         "enabled": s.get("enabled"), "live": bool(live), "side": side,
                         "detail": why, "blocked": blocked, "verdict": verdict,
                         "fired_ts": fired, "status": status})
        return {"symbol": sym, "known": True,
                "picture": {k: pic.get(k) for k in ("last", "change_pct", "gap_pct", "rvol",
                                                    "high", "low", "open", "prev_close",
                                                    "move_5m", "pm_change_pct", "prior")},
                "setups": rows}


def meta() -> dict:
    """The trigger and condition vocabulary, for the setup editor."""
    return {
        "triggers": {k: {"label": v["label"], "side": v["side"], "help": v["help"],
                         "session": v["session"], "once": v["once"],
                         "params": {p: {"default": d, "min": lo, "max": hi, "label": lab}
                                    for p, (d, lo, hi, lab) in v["params"].items()}}
                     for k, v in TRIGGERS.items()},
        "conditions": {k: {"default": d, "min": lo, "max": hi, "label": lab}
                       for k, (d, lo, hi, lab) in CONDITIONS.items()},
        "horizons": list(HORIZONS), "follow_horizon": FOLLOW_HORIZON,
    }


# ── the background loop ────────────────────────────────────────────────────
def _loop() -> None:
    last_cleanup = 0.0
    while _STATE.get("scanning"):
        wait = 120
        try:
            now = _now()
            ph = phase(now)
            if ph in ("pre", "open"):
                sweep(now)
                wait = SWEEP_OPEN_S if ph == "open" else SWEEP_PRE_S
                if ph == "open":
                    refresh_levels(now)
            elif ph == "post":
                with _LOCK:
                    if _STATE["day"] == now.date().isoformat():
                        _save_session_hl()
                        _grade(now)
                        _write_json(f"alerts-{_STATE['day']}.json", _STATE["alerts"])
                wait = 300
            if time.time() - last_cleanup > 6 * 3600:
                cleanup_history()
                last_cleanup = time.time()
        except Exception as exc:  # noqa: BLE001
            with _LOCK:
                _STATE["error"] = f"sweep failed: {exc}"
        time.sleep(wait)


def start_scheduler() -> None:
    with _LOCK:
        if _STATE.get("scanning"):
            return
        _STATE["scanning"] = True
        t = threading.Thread(target=_loop, name="live-scan", daemon=True)
        _STATE["thread"] = t
        t.start()


def stop_scheduler() -> None:
    with _LOCK:
        _STATE["scanning"] = False
