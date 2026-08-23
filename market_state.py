"""market_state.py — the live layer under the Sectors and Market Context tabs.

`strat_states.py` is the arithmetic and knows nothing about the world. This
module is the half that owns the world: it reads the cached watchlist board,
batches live quotes through the existing Schwab client, decides what "now"
means, keeps the intraday breadth series on disk, and caches every answer so
a dashboard polling four panels costs one quote batch rather than four.

Configured the same way every other stateful module here is — the dashboard
calls `configure()` at boot with getters rather than importing anything back,
so this module has no idea `options_dashboard.py` exists.

WHERE THE NUMBERS COME FROM, AND WHAT IS NOT MEASURED

  Period extremes: `watchlist_table` already downloads five years of daily
  bars per symbol on every scan, and now collapses them into the current and
  prior high/low on each of D/W/M/Q/Y. Nothing here re-downloads a bar.

  Live extremes: today's regular-session high and low from a batched Schwab
  quote. Merged into the stored current-period extremes by max/min, which is
  exact — not an estimate, not an interpolation.

  REGULAR SESSION ONLY, AND ONLY ONCE IT HAS STARTED. Extended-hours prints
  are deliberately excluded from the live merge, and before 9:30 ET the
  states shown are the settled ones from the last close. Two reasons. Schwab's
  quote carries the REGULAR session high and low, which are stale or zero
  before the open, so merging them pre-market would compare today's states
  against yesterday's range. And a single thin pre-market print through
  yesterday's high would flip a symbol to 2U on a hundred shares, which is a
  state nobody trading the candle would agree had happened. Every payload
  carries `live: true|false` per symbol so the UI can say which one it is
  showing rather than leaving the reader to guess.

  Sector membership comes from the `sector` column the board already
  carries (yfinance's classification), mapped onto the eleven SPDR sector
  ETFs. It is a classification, not an index membership list: this is "the
  technology names on your watchlist", not "the holdings of XLK". The
  payload says so and the UI repeats it, because those two are close enough
  to be confused and far enough apart to matter.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import strat_states as ST

MARKET_STATE_VERSION = "market-state-1.0.0"

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    _ET = None

try:
    from capture_health import is_trading_day, why_not_trading
except Exception as _exc:  # noqa: BLE001
    print(f"[market_state] capture_health unavailable: {_exc}", file=sys.stderr)

    def is_trading_day(day) -> bool:      # type: ignore[misc]
        d = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
        return d.weekday() < 5

    def why_not_trading(day) -> str:      # type: ignore[misc]
        d = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
        return "a weekend" if d.weekday() >= 5 else ""


# ══════════════════════════════════════════════════════════════════════════
# PURE — SECTOR TAXONOMY
# ══════════════════════════════════════════════════════════════════════════
#
# Thirty lines of constant and two functions over it. It lives here rather
# than in its own module because it is the Sectors dashboard's own
# vocabulary and nothing else in the app needs it; `recovery.py` keeps its
# own narrower name→ETF map for tagging setups and is left alone.

SPDR_SECTORS = [
    {"etf": "XLK",  "name": "Technology",
     "aliases": ("technology", "information technology", "tech")},
    {"etf": "XLF",  "name": "Financials",
     "aliases": ("financial services", "financials", "financial")},
    {"etf": "XLV",  "name": "Health Care",
     "aliases": ("healthcare", "health care")},
    {"etf": "XLY",  "name": "Consumer Discretionary",
     "aliases": ("consumer cyclical", "consumer discretionary")},
    {"etf": "XLP",  "name": "Consumer Staples",
     "aliases": ("consumer defensive", "consumer staples")},
    {"etf": "XLE",  "name": "Energy", "aliases": ("energy",)},
    {"etf": "XLI",  "name": "Industrials", "aliases": ("industrials", "industrial")},
    {"etf": "XLB",  "name": "Materials",
     "aliases": ("basic materials", "materials")},
    {"etf": "XLU",  "name": "Utilities", "aliases": ("utilities", "utility")},
    {"etf": "XLRE", "name": "Real Estate", "aliases": ("real estate", "realestate")},
    {"etf": "XLC",  "name": "Communication Services",
     "aliases": ("communication services", "communications", "telecom")},
]

_ALIAS_TO_ETF = {}
for _s in SPDR_SECTORS:
    _ALIAS_TO_ETF[_s["etf"].lower()] = _s["etf"]
    _ALIAS_TO_ETF[_s["name"].lower()] = _s["etf"]
    for _a in _s["aliases"]:
        _ALIAS_TO_ETF[_a] = _s["etf"]
SECTOR_BY_ETF = {s["etf"]: s for s in SPDR_SECTORS}


def sector_etf(name) -> str | None:
    """Map a sector label (or an ETF ticker) onto one of the eleven SPDR
    sectors. Returns None for anything unrecognised — a name whose sector the
    board never filled in is genuinely unclassified, and putting it in a
    twelfth bucket called "Other" would make it look like a sector."""
    if not name:
        return None
    return _ALIAS_TO_ETF.get(str(name).strip().lower())


def group_by_sector(rows) -> dict:
    """{ETF: [row, ...]} over anything carrying a `sector` field. Rows whose
    sector does not map are dropped and counted by the caller."""
    out: dict[str, list] = {s["etf"]: [] for s in SPDR_SECTORS}
    for r in (rows or []):
        etf = sector_etf(r.get("sector"))
        if etf:
            out[etf].append(r)
    return out


# ══════════════════════════════════════════════════════════════════════════
# SESSION CLOCK
# ══════════════════════════════════════════════════════════════════════════

PRE_OPEN = dtime(4, 0)
REG_OPEN = dtime(9, 30)
REG_CLOSE = dtime(16, 0)
POST_CLOSE = dtime(20, 0)

PHASE_LABEL = {
    "pre": "Pre-market", "open": "Market open", "post": "After hours",
    "closed": "Market closed",
}


def _now_et() -> datetime:
    return datetime.now(_ET) if _ET else datetime.now()


def market_status(now: datetime | None = None) -> dict:
    """Which session phase it is in New York, and why.

    `live_ok` is the one field the rest of this module reads: it is True only
    during the regular session and the hours after it on a trading day, which
    is exactly when Schwab's regular-session high and low describe TODAY.
    """
    n = now or _now_et()
    today = n.date()
    trading = is_trading_day(today)
    t = n.time()
    if not trading:
        phase = "closed"
        reason = f"The US equity market is closed — {why_not_trading(today) or 'not a trading day'}."
    elif t < PRE_OPEN:
        phase = "closed"
        reason = "Before the pre-market session, which opens at 4:00 AM Eastern."
    elif t < REG_OPEN:
        phase = "pre"
        reason = "Pre-market. The regular session opens at 9:30 AM Eastern."
    elif t < REG_CLOSE:
        phase = "open"
        reason = "Regular session, 9:30 AM to 4:00 PM Eastern."
    elif t < POST_CLOSE:
        phase = "post"
        reason = "After hours. The regular session closed at 4:00 PM Eastern."
    else:
        phase = "closed"
        reason = "After the extended session, which ends at 8:00 PM Eastern."
    live_ok = trading and phase in ("open", "post")
    sess = today if live_ok else _prev_trading_day(today)
    return {
        "phase": phase, "label": PHASE_LABEL[phase], "reason": reason,
        "is_open": phase == "open",
        "trading_day": trading,
        # Live candle extremes only exist once the regular session has begun.
        "live_ok": live_ok,
        "now_et": n.replace(microsecond=0).isoformat(),
        "date": today.isoformat(),
        # The session every state on screen is read against. Not the same as
        # the calendar date — see session_date().
        "session_date": sess.isoformat() if sess else today.isoformat(),
    }


def _prev_trading_day(d: date, limit: int = 10) -> date | None:
    """The most recent trading day strictly before `d`. `limit` bounds the
    walk so a bad holiday table can never spin."""
    cur = d
    for _ in range(max(1, limit)):
        cur = cur - timedelta(days=1)
        if is_trading_day(cur):
            return cur
    return None


def session_date(now: datetime | None = None) -> date:
    """The trading session the states on screen belong to.

    NOT the calendar date, and the difference is not cosmetic. Read on a
    Sunday, the calendar date belongs to no session at all: bucketing by it
    puts the current daily candle in a Sunday bucket, finds it empty, and
    reports every symbol's daily state as unknown — a whole dashboard of
    blanks all weekend. Read pre-market, the calendar date opens a daily
    bucket that no regular-session trade has happened in yet.

    So: today once the regular session has begun, and otherwise the most
    recent completed trading day, which is the candle a trader is looking at
    in both of those cases.
    """
    n = now or _now_et()
    st = market_status(n)
    return date.fromisoformat(st["session_date"])


def today_keys(now: datetime | None = None) -> dict:
    """{timeframe: period key} for the current session. Computed once per
    request and handed to every symbol — five date operations instead of six
    thousand."""
    d = session_date(now)
    return {tf: ST.period_key(d, tf) for tf in ST.TIMEFRAMES}


# ══════════════════════════════════════════════════════════════════════════
# WIRING
# ══════════════════════════════════════════════════════════════════════════

_SCHWAB_GETTER = None      # () -> SchwabClient | None
_BOARD_GETTER = None       # (with_strat: bool) -> {"rows": [...], "status": {...}}
_DATA_DIR: Path | None = None

_LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, dict]] = {}
# One quote batch feeds every panel. 20s matches the existing breadth
# endpoint's cache so the two never disagree about the price of a stock.
QUOTE_TTL = 20.0
READ_TTL = 20.0
SECTOR_TTL = 20.0
INDEX_TTL = 45.0
MAP_TTL = 20.0
# How often the intraday breadth series takes a sample while the market is
# open. Two minutes gives ~195 points across a session — enough to see the
# shape, few enough that the file stays under a hundred kilobytes.
SAMPLE_SECONDS = 120
SERIES_KEEP_DAYS = 5

_QUOTES: tuple[float, dict] | None = None
_QUOTES_LOCK = threading.Lock()


def configure(schwab_getter=None, board_getter=None, data_dir=None,
              cfg: dict | None = None) -> None:
    global _SCHWAB_GETTER, _BOARD_GETTER, _DATA_DIR
    if schwab_getter is not None:
        _SCHWAB_GETTER = schwab_getter
    if board_getter is not None:
        _BOARD_GETTER = board_getter
    if data_dir is not None:
        _DATA_DIR = Path(data_dir)
    if cfg:
        set_config(cfg)


def set_config(cfg: dict) -> None:
    """Apply the `market_state` section of thresholds.json.

    Everything settable here is a cache lifetime, a sampling cadence or a
    display bound. No value in this section can change a state, a breadth
    count or which side of a comparison a bar lands on — those come from two
    highs and two lows and have nothing to tune.
    """
    global QUOTE_TTL, READ_TTL, SECTOR_TTL, INDEX_TTL, MAP_TTL
    global SAMPLE_SECONDS, SERIES_KEEP_DAYS

    def _f(key, cur, lo, hi):
        v = _num((cfg or {}).get(key))
        return cur if v is None else max(lo, min(hi, v))

    QUOTE_TTL = _f("quote_ttl_seconds", QUOTE_TTL, 1, 600)
    READ_TTL = _f("read_ttl_seconds", READ_TTL, 1, 600)
    SECTOR_TTL = _f("sector_ttl_seconds", SECTOR_TTL, 1, 600)
    INDEX_TTL = _f("index_ttl_seconds", INDEX_TTL, 1, 600)
    MAP_TTL = _f("map_ttl_seconds", MAP_TTL, 1, 600)
    SAMPLE_SECONDS = int(_f("series_sample_seconds", SAMPLE_SECONDS, 30, 3600))
    SERIES_KEEP_DAYS = int(_f("series_keep_days", SERIES_KEEP_DAYS, 1, 60))
    invalidate()


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _cached(key: str, ttl: float, build):
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and (now - hit[0]) < ttl:
        return hit[1]
    val = build()
    with _LOCK:
        _CACHE[key] = (now, val)
    return val


def invalidate() -> None:
    """Drop every cached answer. Called when a scan republishes the board."""
    with _LOCK:
        _CACHE.clear()


def _board() -> dict:
    if _BOARD_GETTER is None:
        return {"rows": [], "status": {}}
    try:
        return _BOARD_GETTER(True) or {"rows": [], "status": {}}
    except TypeError:
        # A getter that predates the with_strat flag.
        try:
            return _BOARD_GETTER() or {"rows": [], "status": {}}
        except Exception:  # noqa: BLE001
            return {"rows": [], "status": {}}
    except Exception:  # noqa: BLE001
        return {"rows": [], "status": {}}


def _quotes(symbols: list[str]) -> dict:
    """One batched quote pass over the whole watchlist, cached for QUOTE_TTL.

    Schwab caps a quote request's symbol list, so this batches in 250s the
    same way the existing breadth endpoint does. A failed batch is skipped,
    not retried: the symbols in it simply have no live extremes this cycle
    and are reported as `live: false` rather than holding up the other
    thousand names.
    """
    global _QUOTES
    now = time.time()
    with _QUOTES_LOCK:
        hit = _QUOTES
        if hit and (now - hit[0]) < QUOTE_TTL:
            return hit[1]
    sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
    out: dict = {}
    if sc is not None and symbols:
        for i in range(0, len(symbols), 250):
            try:
                out.update(sc.get_quotes(symbols[i:i + 250]) or {})
            except Exception:  # noqa: BLE001
                continue
    with _QUOTES_LOCK:
        _QUOTES = (now, out)
    return out


def _live_extremes(q: dict | None, live_ok: bool):
    """Today's regular-session high and low from a quote, or (None, None).

    The last trade is folded in because a quote can arrive with the high and
    low a tick behind the print that just set them. Extending the range to
    include a price that traded is always correct; it can never shrink a
    range, so the merge downstream stays idempotent.
    """
    if not live_ok or not q:
        return None, None
    hi, lo = _num(q.get("high")), _num(q.get("low"))
    last = _num(q.get("regular_last")) or _num(q.get("last"))
    if hi is None or lo is None or hi <= 0 or lo <= 0 or hi < lo:
        return None, None
    if last and last > 0:
        hi, lo = max(hi, last), min(lo, last)
    return hi, lo


def _reads() -> dict:
    """Every watchlist symbol's live state on all five timeframes.

    The single expensive call in this module and the one every panel is built
    from, so it is computed once and cached for READ_TTL.
    """
    def build():
        board = _board()
        rows = board.get("rows") or []
        status = market_status()
        keys = today_keys()
        syms = [r.get("symbol") for r in rows if r.get("symbol")]
        quotes = _quotes(syms) if status["live_ok"] else {}
        out = []
        no_strat = 0
        for r in rows:
            sym = r.get("symbol")
            stored = r.get("strat")
            if not sym:
                continue
            if not stored:
                no_strat += 1
                continue
            q = quotes.get(sym) or quotes.get(sym.upper())
            hi, lo = _live_extremes(q, status["live_ok"])
            tf = ST.live_read(stored, keys, hi, lo)
            price = (_num(q.get("last")) if q else None) or _num(r.get("last"))
            chg = (_num(q.get("change_pct")) if q else None)
            if chg is None:
                chg = _num(r.get("change"))
            out.append({
                "symbol": sym,
                "company": r.get("company") or "",
                "sector": r.get("sector") or "",
                "sector_etf": sector_etf(r.get("sector")),
                "market_cap": _num(r.get("market_cap")),
                "price": round(price, 2) if price else None,
                "change_pct": round(chg, 2) if chg is not None else None,
                "live": bool(hi is not None),
                "states": {k: v.get("state") for k, v in tf.items()},
                "detail": tf,
                "continuity": ST.continuity({k: v for k, v in tf.items()}),
            })
        return {
            "rows": out, "status": status, "keys": keys,
            "board_as_of": (board.get("status") or {}).get("last_scan"),
            "scanning": bool((board.get("status") or {}).get("scanning")),
            "symbols_without_states": no_strat,
            "quoted": sum(1 for r in out if r["live"]),
        }
    return _cached("reads", READ_TTL, build)


# ══════════════════════════════════════════════════════════════════════════
# SECTORS
# ══════════════════════════════════════════════════════════════════════════

def _sector_card(meta: dict, members: list) -> dict:
    reads = [m["detail"] for m in members]
    br = ST.breadth(reads)
    for tf, b in br.items():
        b["up_share"] = ST.directional_share(b["counts"])
    chgs = [m["change_pct"] for m in members if m["change_pct"] is not None]
    caps = [m["market_cap"] for m in members if m["market_cap"]]
    return {
        "etf": meta["etf"], "name": meta["name"],
        "constituents": len(members),
        "quoted": sum(1 for m in members if m["live"]),
        "breadth": br,
        "median_change_pct": (round(statistics.median(chgs), 2) if chgs else None),
        "market_cap": (sum(caps) if caps else None),
        # The headline sort key: the daily up-share, with the weekly as the
        # tiebreaker. Both are shares of the DIRECTIONAL names only — see
        # strat_states.directional_share for why inside and outside bars are
        # excluded rather than split down the middle.
        "up_share_d": ST.directional_share(br.get("D", {}).get("counts") or {}),
        "up_share_w": ST.directional_share(br.get("W", {}).get("counts") or {}),
    }


def sectors() -> dict:
    """The eleven sector cards: constituent count and D/W/M/Q/Y breadth."""
    def build():
        base = _reads()
        grouped = group_by_sector(base["rows"])
        cards = [_sector_card(SECTOR_BY_ETF[etf], members)
                 for etf, members in grouped.items()]
        classified = sum(c["constituents"] for c in cards)
        # Leaders and laggards by the daily up-share, sectors with no
        # directional names at all excluded (they have no rank, not a low one).
        # The two lists are made disjoint: with only a handful of sectors
        # ranked, top-3 and bottom-3 otherwise overlap and the same sector is
        # labelled both leader and laggard on the same screen.
        ranked = [c for c in cards if c["up_share_d"] is not None]
        ranked.sort(key=lambda c: (-c["up_share_d"], -c["constituents"]))
        take = min(3, len(ranked) // 2)
        leaders = [c["etf"] for c in ranked[:take]]
        laggards = [c["etf"] for c in ranked[len(ranked) - take:]][::-1] if take else []
        return {
            "ok": True,
            "sectors": cards,
            "leaders": leaders,
            "laggards": laggards,
            "timeframes": [{"key": tf, "label": ST.TIMEFRAME_LABEL[tf]}
                           for tf in ST.TIMEFRAMES],
            "states": [{"key": s, "label": ST.STATE_LABEL[s],
                        "meaning": ST.STATE_MEANING[s]} for s in ST.STATES],
            "universe": len(base["rows"]),
            "classified": classified,
            "unclassified": len(base["rows"]) - classified,
            "symbols_without_states": base["symbols_without_states"],
            "status": base["status"],
            "board_as_of": base["board_as_of"],
            "scanning": base["scanning"],
            "membership_note": (
                "Membership is the sector each company is classified under, "
                "not the published holdings of the sector ETF. These are the "
                "names on your watchlist in that sector."),
            "version": MARKET_STATE_VERSION,
        }
    return _cached("sectors", SECTOR_TTL, build)


def sector_detail(key: str) -> dict:
    """Every constituent of one sector and its state on all five
    timeframes."""
    etf = sector_etf(key)
    if not etf:
        return {"ok": False, "error": f"'{key}' is not one of the eleven sectors.",
                "sectors": [s["etf"] for s in SPDR_SECTORS]}

    def build():
        base = _reads()
        members = [r for r in base["rows"] if r["sector_etf"] == etf]
        card = _sector_card(SECTOR_BY_ETF[etf], members)
        rows = []
        for m in members:
            rows.append({
                "symbol": m["symbol"], "company": m["company"],
                "price": m["price"], "change_pct": m["change_pct"],
                "market_cap": m["market_cap"], "live": m["live"],
                "states": m["states"],
                "extremes": {tf: {"prev_high": d.get("prev_high"),
                                  "prev_low": d.get("prev_low"),
                                  "cur_high": d.get("cur_high"),
                                  "cur_low": d.get("cur_low"),
                                  "days": d.get("cur_days")}
                             for tf, d in m["detail"].items()},
                "continuity": m["continuity"],
            })
        rows.sort(key=lambda r: -(r["market_cap"] or 0))
        return {"ok": True, "sector": card, "rows": rows,
                "timeframes": [{"key": tf, "label": ST.TIMEFRAME_LABEL[tf]}
                               for tf in ST.TIMEFRAMES],
                "states": [{"key": s, "label": ST.STATE_LABEL[s],
                            "meaning": ST.STATE_MEANING[s]} for s in ST.STATES],
                "status": base["status"], "board_as_of": base["board_as_of"],
                "version": MARKET_STATE_VERSION}
    return _cached(f"sector:{etf}", SECTOR_TTL, build)


# ══════════════════════════════════════════════════════════════════════════
# INTRADAY BREADTH SERIES
# ══════════════════════════════════════════════════════════════════════════

def _series_path(day: str) -> Path | None:
    if _DATA_DIR is None:
        return None
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        return None
    return _DATA_DIR / f"strat_breadth_{day}.json"


def _load_series(day: str) -> list:
    p = _series_path(day)
    if p is None or not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save_series(day: str, points: list) -> None:
    p = _series_path(day)
    if p is None:
        return
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(points, separators=(",", ":")))
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001
        pass


def _prune_series(keep_days: int = SERIES_KEEP_DAYS) -> None:
    if _DATA_DIR is None:
        return
    try:
        cutoff = (_now_et().date() - timedelta(days=keep_days)).isoformat()
        for f in _DATA_DIR.glob("strat_breadth_*.json"):
            day = f.stem.replace("strat_breadth_", "")
            if day < cutoff:
                f.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _sample_series(status: dict, br: dict) -> list:
    """Append one point to today's series when the market is open and the
    last sample is old enough, then return the whole day.

    Only samples during the regular session. A point recorded at 3 AM would
    be the previous close repeated, and a flat line through the small hours
    reads as market data rather than as an idle poller.
    """
    day = status["date"]
    points = _load_series(day)
    if not status["is_open"]:
        return points
    now = time.time()
    if points:
        try:
            if now - float(points[-1].get("t") or 0) < SAMPLE_SECONDS:
                return points
        except Exception:  # noqa: BLE001
            pass
    point = {"t": round(now), "et": _now_et().strftime("%H:%M"),
             "counts": {tf: {s: br[tf]["counts"].get(s, 0) for s in ST.STATES}
                        for tf in br},
             "n": {tf: br[tf]["n"] for tf in br}}
    points.append(point)
    _save_series(day, points)
    _prune_series()
    return points


# ══════════════════════════════════════════════════════════════════════════
# MARKET CONTEXT
# ══════════════════════════════════════════════════════════════════════════

def context() -> dict:
    """Market breadth, the intraday breadth series, and the current-candle
    snapshot — the three whole-market panels, from one read."""
    def build():
        base = _reads()
        br = ST.breadth([r["detail"] for r in base["rows"]])
        for tf, b in br.items():
            b["up_share"] = ST.directional_share(b["counts"])
        series = _sample_series(base["status"], br)
        return {
            "ok": True,
            "breadth": br,
            "series": series,
            "series_sample_seconds": SAMPLE_SECONDS,
            "snapshot": [
                {"timeframe": tf, "label": ST.TIMEFRAME_LABEL[tf],
                 "counts": br[tf]["counts"], "pct": br[tf]["pct"],
                 "n": br[tf]["n"], "up_share": br[tf]["up_share"]}
                for tf in ST.TIMEFRAMES if tf in br
            ],
            "timeframes": [{"key": tf, "label": ST.TIMEFRAME_LABEL[tf]}
                           for tf in ST.TIMEFRAMES],
            "states": [{"key": s, "label": ST.STATE_LABEL[s],
                        "meaning": ST.STATE_MEANING[s]} for s in ST.STATES],
            "universe": len(base["rows"]),
            "quoted": base["quoted"],
            "symbols_without_states": base["symbols_without_states"],
            "status": base["status"],
            "board_as_of": base["board_as_of"],
            "scanning": base["scanning"],
            "version": MARKET_STATE_VERSION,
        }
    return _cached("context", READ_TTL, build)


# ══════════════════════════════════════════════════════════════════════════
# INDICES MATRIX
# ══════════════════════════════════════════════════════════════════════════

INDEX_SYMBOLS = [
    {"symbol": "SPY", "name": "S&P 500"},
    {"symbol": "QQQ", "name": "Nasdaq 100"},
    {"symbol": "IWM", "name": "Russell 2000"},
    {"symbol": "DIA", "name": "Dow 30"},
]

# The intraday rungs, as multiples of the 30-minute bars Schwab returns.
# 60m is two of them and 4H is eight — 9:30-13:30 and 13:30-16:00 in a
# regular session, which is how a four-hour candle is drawn on a US equity
# chart. Building both from ONE 30-minute request is the reason the
# frequency is 30 rather than 60: a 60-minute request could not produce a
# four-hour bar that starts at the open.
INTRADAY_RUNGS = [
    {"key": "60m", "label": "60 minutes", "minutes": 60},
    {"key": "4H", "label": "4 hours", "minutes": 240},
]


def _bucket_intraday(bars: list, minutes: int) -> list[dict]:
    """Group 30-minute bars into `minutes`-long candles anchored to 9:30 ET.

    Anchoring matters: bucketing by wall-clock hour puts the 9:30-10:00 half
    hour in a 9:00 bucket that never existed, and every candle after it
    straddles two real ones.
    """
    out: list[dict] = []
    if not bars:
        return out
    span = max(1, int(minutes)) * 60_000
    for b in bars:
        ts = b.get("ts")
        hi, lo = _num(b.get("high")), _num(b.get("low"))
        if not ts or hi is None or lo is None:
            continue
        if _ET is not None:
            dt = datetime.fromtimestamp(ts / 1000.0, tz=_ET)
        else:
            dt = datetime.fromtimestamp(ts / 1000.0)
        open_ms = int(datetime.combine(
            dt.date(), REG_OPEN,
            tzinfo=dt.tzinfo).timestamp() * 1000)
        if ts < open_ms:
            continue                       # pre-market print; regular session only
        idx = (ts - open_ms) // span
        key = f"{dt.date().isoformat()}#{idx}"
        if out and out[-1]["key"] == key:
            cur = out[-1]
            cur["high"] = max(cur["high"], hi)
            cur["low"] = min(cur["low"], lo)
            cur["close"] = _num(b.get("close"))
            cur["bars"] += 1
        else:
            out.append({"key": key, "start": dt.replace(microsecond=0).isoformat(),
                        "high": hi, "low": lo, "close": _num(b.get("close")),
                        "bars": 1})
    return out


def _index_row(sc, meta: dict, keys: dict, live_ok: bool, quote: dict | None) -> dict:
    sym = meta["symbol"]
    cells: dict[str, dict] = {}
    # ── intraday rungs, from one 30-minute request ──────────────────────
    bars = None
    if sc is not None:
        try:
            bars = sc.get_candles(sym, period_type="day", period=10,
                                  frequency_type="minute", frequency=30)
        except Exception:  # noqa: BLE001
            bars = None
    for rung in INTRADAY_RUNGS:
        buckets = _bucket_intraday(bars or [], rung["minutes"])
        if len(buckets) >= 2:
            prev, cur = buckets[-2], buckets[-1]
            cells[rung["key"]] = {
                "state": ST.state_of(prev["high"], prev["low"],
                                     cur["high"], cur["low"]),
                "prev_high": round(prev["high"], 2), "prev_low": round(prev["low"], 2),
                "cur_high": round(cur["high"], 2), "cur_low": round(cur["low"], 2),
                "live": True, "bars": cur["bars"],
            }
        else:
            cells[rung["key"]] = {
                "state": None, "live": False,
                "reason": ("Not enough intraday history yet — a state needs two "
                           "candles on this timeframe."),
            }
    # ── daily and above, from daily bars ────────────────────────────────
    daily = None
    if sc is not None:
        try:
            daily = sc.get_price_history(sym, days=1200)
        except Exception:  # noqa: BLE001
            daily = None
    if daily:
        D = [b["date"][:10] for b in daily]
        H = [_num(b.get("high")) for b in daily]
        L = [_num(b.get("low")) for b in daily]
        pairs = [(d, h, l) for d, h, l in zip(D, H, L) if h is not None and l is not None]
        stored = ST.read([p[0] for p in pairs], [p[1] for p in pairs],
                         [p[2] for p in pairs])
        hi, lo = _live_extremes(quote, live_ok)
        for tf, r in ST.live_read(stored, keys, hi, lo).items():
            cells[tf] = {"state": r["state"], "live": r["live"],
                         "prev_high": r["prev_high"], "prev_low": r["prev_low"],
                         "cur_high": r["cur_high"], "cur_low": r["cur_low"],
                         "days": r["cur_days"]}
    else:
        for tf in ST.TIMEFRAMES:
            cells[tf] = {"state": None, "live": False,
                         "reason": "Daily bars are not available for this symbol."}
    price = _num((quote or {}).get("last"))
    return {
        "symbol": sym, "name": meta["name"],
        "price": round(price, 2) if price else None,
        "change_pct": (round(_num((quote or {}).get("change_pct")), 2)
                       if (quote or {}).get("change_pct") is not None else None),
        "cells": cells,
        "continuity": ST.continuity(cells, timeframes=("60m", "4H", "D", "W", "M")),
    }


def indices() -> dict:
    """SPY / QQQ / IWM / DIA across 60m, 4H, D, W, M, Q, Y."""
    def build():
        status = market_status()
        keys = today_keys()
        sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
        quotes = {}
        if sc is not None:
            try:
                quotes = sc.get_quotes([m["symbol"] for m in INDEX_SYMBOLS]) or {}
            except Exception:  # noqa: BLE001
                quotes = {}
        rows = [_index_row(sc, m, keys, status["live_ok"], quotes.get(m["symbol"]))
                for m in INDEX_SYMBOLS]
        columns = ([{"key": r["key"], "label": r["label"]} for r in INTRADAY_RUNGS]
                   + [{"key": tf, "label": ST.TIMEFRAME_LABEL[tf]}
                      for tf in ST.TIMEFRAMES])
        return {
            "ok": any(any(c.get("state") for c in r["cells"].values()) for r in rows),
            "rows": rows, "columns": columns,
            "states": [{"key": s, "label": ST.STATE_LABEL[s],
                        "meaning": ST.STATE_MEANING[s]} for s in ST.STATES],
            "status": status,
            "source": "schwab" if sc is not None else None,
            "note": ("Intraday candles are built from 30-minute bars anchored to "
                     "the 9:30 AM Eastern open, regular session only."),
            "version": MARKET_STATE_VERSION,
        }
    return _cached("indices", INDEX_TTL, build)


# ══════════════════════════════════════════════════════════════════════════
# MARKET MAP
# ══════════════════════════════════════════════════════════════════════════

def market_map(limit_per_sector: int = 40) -> dict:
    """Sector-grouped rows for the treemap: market cap for the size, percent
    change for the colour.

    `limit_per_sector` keeps the biggest N names in each sector. A treemap
    with 1,285 rectangles is a texture, not a chart — the tail renders
    sub-pixel and costs more to lay out than to look at. What was dropped is
    reported per sector rather than trimmed silently.
    """
    def build():
        base = _reads()
        grouped = group_by_sector(base["rows"])
        out = []
        for etf, members in grouped.items():
            usable = [m for m in members
                      if m["market_cap"] and m["change_pct"] is not None]
            usable.sort(key=lambda m: -(m["market_cap"] or 0))
            kept = usable[:max(1, int(limit_per_sector))]
            if not kept:
                continue
            out.append({
                "etf": etf, "name": SECTOR_BY_ETF[etf]["name"],
                "market_cap": sum(m["market_cap"] for m in kept),
                "constituents": len(usable), "shown": len(kept),
                "dropped": len(usable) - len(kept),
                "median_change_pct": round(
                    statistics.median([m["change_pct"] for m in kept]), 2),
                "children": [{
                    "symbol": m["symbol"], "company": m["company"],
                    "market_cap": m["market_cap"], "price": m["price"],
                    "change_pct": m["change_pct"],
                    "state_d": m["states"].get("D"),
                    "state_w": m["states"].get("W"),
                    "live": m["live"],
                } for m in kept],
            })
        out.sort(key=lambda s: -(s["market_cap"] or 0))
        return {
            "ok": bool(out), "sectors": out,
            "limit_per_sector": limit_per_sector,
            "universe": len(base["rows"]),
            "status": base["status"], "board_as_of": base["board_as_of"],
            "scanning": base["scanning"],
            "sizing_note": ("Rectangle area is market capitalisation and colour "
                            "is today's percentage change. Sizes are relative "
                            "within the map, not to the whole market."),
            "version": MARKET_STATE_VERSION,
        }
    return _cached(f"map:{limit_per_sector}", MAP_TTL, build)
