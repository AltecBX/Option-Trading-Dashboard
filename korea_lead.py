"""korea_lead.py — the stateful side of KOREA LEAD.

korea_lead_engine.py is the mathematics. This module owns everything with a
clock, a network or a disk:

  Korea      four Yahoo series — KOSPI, Samsung Electronics, SK Hynix and
             USD/KRW — fetched once and cached on disk, dated on SEOUL's
             calendar because that is the calendar their sessions happen on.
  U.S.       daily bars through the loader the rest of the app already uses,
             injected, so the Schwab-first path and its cache are reused
             rather than re-implemented.
  Session    where Seoul is in its trading day right now, from zoneinfo.
  Cache      bars on disk; statistics in memory, keyed by the target, the
             lookback, the signal definition and the last session date in
             each market — so a cached answer can only be served for the
             exact question it answered.

Every dependency is injected through configure(), so the tests run with no
network at all.

WHAT THIS MODULE WILL NOT DO

  It will not invent a Korean session. If KOSPI cannot be read there is no
  opening-gap bias, and the payload says NO DATA rather than falling back
  to Samsung or to yesterday.

  It will not put USD/KRW in the model. Yahoo stamps the USD/KRW daily bar
  on a London day, and a London day closes after New York opens. A "daily
  close" that may have been set at 4pm ET cannot be used to predict a 9:30
  ET open without reading the future, and there is no intraday FX history
  reaching back years to sample honestly instead. So the currency is shown
  as context, labelled as context, and excluded from every statistic. That
  is a smaller feature than including it, and it is the only correct one.

  It will not roll a Korean session forward onto a later U.S. session. When
  the U.S. was closed, that Korean observation is skipped and counted.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

import korea_lead_engine as kle
import korea_research_engine as _kre

try:
    from zoneinfo import ZoneInfo
    _KST = ZoneInfo("Asia/Seoul")
    _ET = ZoneInfo("America/New_York")
except Exception:                      # pragma: no cover - stdlib since 3.9
    _KST = _ET = None

MODULE_VERSION = "korea-lead-1.0.0"

# ── the Korean series ───────────────────────────────────────────────────────
# Yahoo symbols, each verified against the provider before this file was
# written: ^KS11 answers as the KOSPI Composite Index on the KSE in
# Asia/Seoul; 005930.KS as Samsung Electronics Co., Ltd. and 000660.KS as
# SK hynix Inc., both EQUITY in Asia/Seoul; KRW=X as the USD/KRW currency
# pair — and note its exchange timezone comes back Europe/London, which is
# the whole reason it is kept out of the model below.

KOSPI = "kospi"
SAMSUNG = "samsung"
HYNIX = "hynix"
USDKRW = "usdkrw"

KOREA_SYMBOLS = {
    KOSPI: "^KS11",
    SAMSUNG: "005930.KS",
    HYNIX: "000660.KS",
    USDKRW: "KRW=X",
}

KOREA_LABEL = {
    KOSPI: "KOSPI",
    SAMSUNG: "Samsung Electronics",
    HYNIX: "SK Hynix",
    USDKRW: "US Dollar / Korean Won",
}

# The three that carry the signal. USD/KRW is deliberately not among them.
SIGNAL_SERIES = (KOSPI, SAMSUNG, HYNIX)

# Quick targets. Presets only — any U.S. ticker with enough matched history
# works, and the panel follows whatever the rest of Gap Scan is looking at.
TARGET_PRESETS = ["QQQ", "SMH", "SOXX", "MU", "NVDA", "AVGO", "AMD", "MRVL",
                  "WDC", "STX", "SNDK"]
# Controls, kept separate from the presets because they answer a different
# question. SPY says whether this is just broad risk appetite; IGV says
# whether Korea is talking about all of technology or about chips.
CONTROL_TARGETS = ["SPY", "IGV"]

WINDOWS = {
    "60d": {"label": "60 sessions", "kind": "sessions", "size": 60},
    "1y": {"label": "1 year", "kind": "days", "size": 365},
    "3y": {"label": "3 years", "kind": "days", "size": 365 * 3},
    "max": {"label": "All available", "kind": "all", "size": None},
}
DEFAULT_WINDOW = "1y"

# Seoul's regular session. 09:00 to 15:30 KST; the closing single-price
# auction runs from 15:20, so the printed close settles at 15:30.
KOREA_OPEN = dtime(9, 0)
KOREA_CLOSE = dtime(15, 30)

SESSION_BEFORE = "BEFORE OPEN"
SESSION_LIVE = "SESSION IN PROGRESS"
SESSION_CLOSED = "CLOSED"
SESSION_NON_TRADING = "NOT A TRADING DAY"

# How far back to ask each loader for. Korea's own history reaches the late
# 1990s; the U.S. loader is asked for the same span so MAX means the same
# thing on both sides of the pair.
HISTORY_DAYS = 2600

_BARS_TTL_LIVE_S = 300.0        # Seoul still trading: today's bar moves
_BARS_TTL_CLOSED_S = 3600.0     # Seoul done: today's bar is settled
_US_BARS_TTL_S = 300.0          # a ten-year daily series cannot move faster
_QUOTE_TTL_S = 20.0

# ── injected dependencies ───────────────────────────────────────────────────

_DAILY_FN = None        # (sym, days) -> {"bars": [...], "source": str}
_KOREA_FN = None        # (yahoo_sym, days) -> {"bars": [...], "source", "meta"}
_QUOTE_FN = None        # (sym) -> quote dict | None
_NOW_FN = None          # () -> aware datetime
_DATA_DIR: Path | None = None

_LOCK = threading.Lock()
_BARS_MEM: dict = {}            # yahoo symbol -> (fetched_ts, pack)
_US_MEM: dict = {}              # symbol -> (fetched_ts, daily bar pack)
_OBS_MEM: dict = {}             # symbol -> (identity, matched observations)
_STATS_MEM: dict = {}           # cache key -> (built_ts, study)
_QUOTE_MEM: dict = {}           # symbol -> (fetched_ts, quote)


def configure(daily_fn=None, korea_fn=None, quote_fn=None, data_dir=None,
              now_fn=None) -> None:
    """Wire the providers. `korea_fn` defaults to the built-in Yahoo chart
    reader; tests pass their own and never touch the network."""
    global _DAILY_FN, _KOREA_FN, _QUOTE_FN, _DATA_DIR, _NOW_FN
    _DAILY_FN = daily_fn
    _KOREA_FN = korea_fn or _yahoo_daily
    _QUOTE_FN = quote_fn
    _NOW_FN = now_fn
    if data_dir:
        _DATA_DIR = Path(data_dir)
        try:
            (_DATA_DIR / "korea" / "bars").mkdir(parents=True, exist_ok=True)
        except Exception as exc:      # pragma: no cover
            print(f"[korea] storage init failed: {exc}")
    with _LOCK:
        _BARS_MEM.clear()
        _US_MEM.clear()
        _OBS_MEM.clear()
        _STATS_MEM.clear()
        _QUOTE_MEM.clear()


# ── configuration ───────────────────────────────────────────────────────────
# Same discipline as the rest of the app: the repo file holds the defaults,
# <data>/thresholds.json overrides key by key, and the whole file is hashed
# so a result can be traced back to the settings that produced it. Nothing
# here is a finding — these numbers decide how conservative the wording is
# and where a series stops being credible, and every one of them is meant
# to be argued with.

_CFG_CACHE = {"cfg": None, "hash": None, "ts": 0.0}

_CFG_DEFAULTS = {
    "max_credible_move_pct": kle.MAX_CREDIBLE_MOVE_PCT,
    "implied_min_n": 8,
    "near_zero_expected_pct": 0.15,
    "edge_gates": dict(kle.EDGE_GATES),
}


def config(refresh: bool = False) -> tuple[dict, str]:
    """(korea_lead section merged over the defaults, hash of the whole file)."""
    import hashlib
    with _LOCK:
        if not refresh and _CFG_CACHE["cfg"] is not None \
                and time.time() - _CFG_CACHE["ts"] < 60:
            return _CFG_CACHE["cfg"], _CFG_CACHE["hash"]
    full = {}
    try:
        full = json.loads((Path(__file__).resolve().parent
                           / "thresholds.json").read_text())
    except Exception:
        full = {}
    try:
        if _DATA_DIR and (_DATA_DIR / "thresholds.json").exists():
            over = json.loads((_DATA_DIR / "thresholds.json").read_text())
            section = (over or {}).get("korea_lead")
            if isinstance(section, dict):
                full.setdefault("korea_lead", {})
                for k, v in section.items():
                    if isinstance(v, dict) and isinstance(
                            full["korea_lead"].get(k), dict):
                        full["korea_lead"][k].update(v)
                    else:
                        full["korea_lead"][k] = v
    except Exception:
        pass
    # Keys beginning with an underscore are prose for whoever edits the
    # file. They document a setting; they are never one, so they are not
    # carried into the merged configuration.
    cfg = dict(_CFG_DEFAULTS)
    for k, v in (full.get("korea_lead") or {}).items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            merged = dict(cfg[k])
            merged.update({kk: vv for kk, vv in v.items()
                           if not kk.startswith("_")})
            cfg[k] = merged
        else:
            cfg[k] = v
    h = hashlib.sha256(json.dumps(full, sort_keys=True,
                                  separators=(",", ":")).encode()).hexdigest()[:16]
    with _LOCK:
        _CFG_CACHE.update({"cfg": cfg, "hash": h, "ts": time.time()})
    return cfg, h


def _max_move() -> float:
    try:
        return float(config()[0]["max_credible_move_pct"])
    except (KeyError, TypeError, ValueError):    # pragma: no cover
        return kle.MAX_CREDIBLE_MOVE_PCT


def _now_kst() -> datetime:
    if _NOW_FN is not None:
        got = _NOW_FN()
        if got is not None:
            return got.astimezone(_KST) if _KST else got
    return datetime.now(_KST) if _KST else datetime.now()


def _now_et() -> datetime:
    if _NOW_FN is not None:
        got = _NOW_FN()
        if got is not None:
            return got.astimezone(_ET) if _ET else got
    return datetime.now(_ET) if _ET else datetime.now()


# ── Korean bars: the Yahoo chart endpoint ───────────────────────────────────

def _yahoo_daily(symbol: str, days: int = HISTORY_DAYS) -> dict:
    """Daily bars for one Yahoo symbol, dated on ITS OWN exchange's calendar.

    The chart endpoint returns UTC epoch timestamps plus the exchange's UTC
    offset, and the offset is applied before the date is taken. Measured
    against this app's own series: KOSPI and the two Korean stocks are
    stamped at 00:00 UTC, which is 09:00 in Seoul, so for them the two
    dates happen to agree. USD/KRW is not — its bars are stamped at London
    midnight, so the bar belonging to a given London day carries the
    PREVIOUS UTC date, and reading it as UTC would file every currency
    observation one day early. The offset is applied to all four rather
    than to the one that currently needs it, because which series needs it
    is a property of the provider and can change without notice.

    That same stamp is the evidence behind keeping USD/KRW out of the model
    entirely: a bar running from London midnight to London midnight has its
    close set at the END of that window — around 7pm in New York — which is
    hours after the U.S. opening price it would be used to predict.

    The OHLC block from this endpoint is adjusted for splits and spinoffs
    (verified against a 10-for-1 split and a spinoff in this app's own
    target list, neither of which appears as an overnight move). It is NOT
    adjusted for dividends, which is correct here: an ex-dividend gap is a
    real gap that a real opening price really had.
    """
    if os.environ.get("JERRY_NO_NET"):
        raise RuntimeError("JERRY_NO_NET is set — no outbound request made")
    # Never "max". Asked for the longest available daily history this
    # endpoint answers 200 with MONTHLY bars — about twelve a year instead
    # of two hundred and forty-five — and says nothing about the swap. Ten
    # years is the longest range that still comes back genuinely daily, and
    # it already reaches further than the U.S. loader beside it, so MAX
    # loses nothing by stopping here. The density guard in the engine is
    # the backstop if this ever changes again.
    rng = "10y" if days >= 1800 else ("5y" if days >= 900 else "2y")
    url = ("https://query2.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol) + f"?range={rng}&interval=1d")
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0 Safari/537.36"),
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    res = ((data.get("chart") or {}).get("result") or [None])[0]
    if not res:
        raise ValueError(f"no chart result for {symbol}")
    meta = res.get("meta") or {}
    offset = int(meta.get("gmtoffset") or 0)
    stamps = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    bars = []
    for i, ts in enumerate(stamps):
        try:
            o, h, lo, c = (q["open"][i], q["high"][i], q["low"][i], q["close"][i])
        except (KeyError, IndexError):
            continue
        if None in (o, h, lo, c):
            continue
        day = datetime.fromtimestamp(ts + offset, timezone.utc).date().isoformat()
        bars.append({"date": day, "open": float(o), "high": float(h),
                     "low": float(lo), "close": float(c)})
    return {"bars": bars, "source": "yahoo chart",
            "meta": {"symbol": meta.get("symbol"),
                     "name": meta.get("longName") or meta.get("shortName"),
                     "timezone": meta.get("exchangeTimezoneName"),
                     "currency": meta.get("currency"),
                     "market_time": meta.get("regularMarketTime"),
                     "market_price": meta.get("regularMarketPrice"),
                     "prev_close": (meta.get("chartPreviousClose")
                                    or meta.get("previousClose"))}}


def _bars_path(symbol: str) -> Path | None:
    if not _DATA_DIR:
        return None
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in symbol)
    return _DATA_DIR / "korea" / "bars" / f"{safe}.json"


def _read_disk(symbol: str) -> dict | None:
    p = _bars_path(symbol)
    if not p or not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        return d if isinstance(d, dict) and d.get("bars") else None
    except Exception:
        return None


def _write_disk(symbol: str, pack: dict) -> None:
    p = _bars_path(symbol)
    if not p:
        return
    try:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(pack, separators=(",", ":")))
        tmp.replace(p)
    except Exception as exc:          # pragma: no cover
        print(f"[korea] bar cache write failed {symbol}: {exc}")


def korea_bars(name: str, force: bool = False) -> dict:
    """Cached daily bars for one Korean series.

    Refresh cadence follows Seoul, not the wall clock: while the session is
    running today's bar keeps moving and is re-read every few minutes; once
    Seoul has closed the bar is settled and an hourly refresh is plenty.
    A failed fetch falls back to the cache on disk and says so — stale
    honest data beats a blank panel, provided the staleness is visible.
    """
    symbol = KOREA_SYMBOLS.get(name, name)
    ttl = _BARS_TTL_LIVE_S if session_state()["state"] == SESSION_LIVE \
        else _BARS_TTL_CLOSED_S
    with _LOCK:
        hit = _BARS_MEM.get(symbol)
    if hit and not force and time.time() - hit[0] < ttl:
        return hit[1]
    disk = _read_disk(symbol)
    if disk and not force:
        try:
            age = time.time() - float(disk.get("fetched_ts") or 0)
        except (TypeError, ValueError):
            age = ttl + 1
        if age < ttl:
            with _LOCK:
                _BARS_MEM[symbol] = (time.time() - age, disk)
            return disk
    try:
        got = _KOREA_FN(symbol, HISTORY_DAYS) or {}
        bars = got.get("bars") or []
        if not bars:
            raise ValueError("empty series")
        pack = {"symbol": symbol, "name": name, "bars": bars,
                "source": got.get("source") or "unknown",
                "meta": got.get("meta") or {},
                "fetched_ts": time.time(),
                "fetched": _now_et().isoformat(timespec="seconds"),
                "ok": True, "error": None}
        _write_disk(symbol, pack)
    except Exception as exc:          # noqa: BLE001
        if disk:
            pack = dict(disk)
            pack["ok"] = True
            pack["stale"] = True
            pack["error"] = f"refresh failed, serving cached bars: {exc}"
        else:
            pack = {"symbol": symbol, "name": name, "bars": [],
                    "source": None, "meta": {}, "fetched_ts": time.time(),
                    "fetched": _now_et().isoformat(timespec="seconds"),
                    "ok": False, "error": str(exc)[:200]}
    with _LOCK:
        _BARS_MEM[symbol] = (time.time(), pack)
    return pack


def us_bars(symbol: str, force: bool = False) -> dict:
    """Daily bars for the U.S. target, through the injected loader — the
    same Schwab-first path the rest of the app already uses.

    Memoised for a few minutes on top of that loader. One panel load asks
    for this series more than once (the statistics, the matched set, and
    the lookback comparison all start from it), and while the loader's own
    network call is cached, everything around it is not: each call re-runs
    the indicator pass over ten years of bars and asks for a fresh quote to
    splice today in. None of that can change between two calls a
    millisecond apart, and a ten-year daily series cannot meaningfully
    change more than once a session.
    """
    key = symbol.upper()
    with _LOCK:
        hit = _US_MEM.get(key)
    if hit and not force and time.time() - hit[0] < _US_BARS_TTL_S:
        return hit[1]
    if not _DAILY_FN:
        return {"bars": [], "source": None, "ok": False,
                "error": "no U.S. daily loader is wired"}
    try:
        pack = _DAILY_FN(key, HISTORY_DAYS) or {}
    except Exception as exc:          # noqa: BLE001
        return {"bars": [], "source": None, "ok": False, "error": str(exc)[:200]}
    bars = pack.get("bars") or []
    out = {"bars": bars, "source": pack.get("source"), "ok": bool(bars),
           "error": None if bars else f"no daily history for {key}"}
    if bars:
        with _LOCK:
            _US_MEM[key] = (time.time(), out)
            if len(_US_MEM) > 24:
                _US_MEM.pop(next(iter(_US_MEM)), None)
    return out


# ── where Seoul is right now ────────────────────────────────────────────────

def session_state(now=None) -> dict:
    """Seoul's position in its own trading day, on Seoul's clock.

    A weekend is NOT A TRADING DAY rather than CLOSED, because the two mean
    different things to the reader: closed says today's number is final,
    not-a-trading-day says there is no today's number. Korean public
    holidays are not enumerated here — no calendar for them ships with this
    app, and inventing one would be worse than saying so. What is used
    instead is the fact of the data: if Seoul's latest bar is not today's
    Seoul date, the panel says which session it is actually showing.
    """
    n = now.astimezone(_KST) if (now is not None and _KST) else (now or _now_kst())
    today = n.date()
    weekend = today.weekday() >= 5
    if weekend:
        state = SESSION_NON_TRADING
    elif n.time() < KOREA_OPEN:
        state = SESSION_BEFORE
    elif n.time() < KOREA_CLOSE:
        state = SESSION_LIVE
    else:
        state = SESSION_CLOSED
    return {
        "state": state,
        "final": state in (SESSION_CLOSED, SESSION_NON_TRADING),
        "seoul_date": today.isoformat(),
        "seoul_time": n.strftime("%H:%M"),
        "seoul_now": n.isoformat(timespec="seconds"),
        "opens": KOREA_OPEN.strftime("%H:%M"),
        "closes": KOREA_CLOSE.strftime("%H:%M"),
        "zone": "Asia/Seoul",
    }


def _us_session(now=None) -> dict:
    """Whether New York has opened yet — which decides whether the number
    to compare against Korea is a premarket quote or the official open."""
    n = now.astimezone(_ET) if (now is not None and _ET) else (now or _now_et())
    t = n.time()
    if n.weekday() >= 5:
        phase = "weekend"
    elif t < dtime(9, 30):
        phase = "premarket"
    elif t < dtime(16, 0):
        phase = "regular"
    else:
        phase = "after"
    return {"phase": phase, "et_date": n.date().isoformat(),
            "et_time": n.strftime("%H:%M"), "opened": phase in ("regular", "after")}


# ── today's Korean readings ─────────────────────────────────────────────────

def korea_today(force: bool = False) -> dict:
    """The latest close-to-close move for every Korean series, with the
    session date each one belongs to and whether it is settled.

    Two dates have to agree before any of this becomes a signal, and both
    checks live here rather than at the call sites:

    A SERIES IS ONLY TODAY'S IF ITS OWN SESSION SAYS SO. On a Korean
    holiday, a provider delay, or a failed refresh serving the stored copy,
    the newest bar belongs to an EARLIER session. That move already had its
    U.S. session — the one sharing its date — so handing it to today's open
    is exactly the roll-forward the historical alignment refuses to do. The
    signal is withheld and the reason is stated.

    AND THE CHIP NAMES ARE ONLY CONFIRMATION IF THEY TRADED THE SAME DAY.
    A Samsung reading from yesterday next to a KOSPI reading from today is
    not agreement or disagreement about anything; it is two different days
    being compared. Such a name is passed through as unreadable, which the
    confirmation logic already reports by name and already refuses to call
    STRONG.
    """
    sess = session_state()
    out = {"session": sess, "series": {}, "as_of": None, "sources": {}}
    newest = None
    for name in (KOSPI, SAMSUNG, HYNIX, USDKRW):
        pack = korea_bars(name, force=force)
        bars = pack.get("bars") or []
        rows = kle.close_to_close(bars, max_move_pct=_max_move())
        last_date = kle.bar_date(bars[-1]) if bars else None
        pct = rows.get(last_date) if last_date else None
        last_close = bars[-1].get("close") if bars else None
        provisional = bool(last_date and last_date == sess["seoul_date"]
                           and sess["state"] == SESSION_LIVE)
        out["series"][name] = {
            "key": name,
            "label": KOREA_LABEL[name],
            "symbol": KOREA_SYMBOLS[name],
            "ok": bool(pack.get("ok")) and pct is not None,
            "pct": None if pct is None else round(pct, 3),
            "close": last_close,
            "session_date": last_date,
            "provisional": provisional,
            "is_today": bool(last_date and last_date == sess["seoul_date"]),
            "in_model": name in SIGNAL_SERIES,
            "stale": bool(pack.get("stale")),
            "error": pack.get("error"),
            "source": pack.get("source"),
            "name_from_provider": (pack.get("meta") or {}).get("name"),
            "timezone": (pack.get("meta") or {}).get("timezone"),
            "n_bars": len(bars),
        }
        # How unusual today's move is against THIS index's own trailing
        # year. A fixed "KOSPI above one and a half percent is major" ages
        # badly — it fires constantly in a calm year and never in a violent
        # one — where a percentile carries the volatility regime with it.
        days = sorted(rows)
        if len(days) >= 60 and pct is not None:
            hist = [abs(rows[d]) for d in days[-253:-1]]
            share = _kre.percentile_of(abs(pct), hist)
            out["series"][name]["abs_percentile"] = (
                None if share is None else round(share, 1))
            out["series"][name]["trailing_n"] = len(hist)
            out["series"][name]["unusual"] = bool(
                share is not None and share >= 90.0
                and name in SIGNAL_SERIES)
        else:
            out["series"][name]["abs_percentile"] = None
            out["series"][name]["trailing_n"] = 0
            out["series"][name]["unusual"] = False
        out["sources"][name] = {"symbol": KOREA_SYMBOLS[name],
                                "source": pack.get("source"),
                                "fetched": pack.get("fetched"),
                                "bars": len(bars)}
        if last_date and (newest is None or last_date > newest):
            newest = last_date
    out["as_of"] = newest
    fx = out["series"][USDKRW]
    fx["excluded_reason"] = (
        "Shown as context only, never used in a statistic. This app's FX "
        "source stamps the USD/KRW daily bar on a London day, and a London "
        "day is still open when New York opens — so its 'close' may have "
        "been set hours AFTER the U.S. opening price it would be used to "
        "predict. Using it would be reading the future. Intraday FX history "
        "does not reach back far enough to sample it honestly instead, so "
        "the currency stays out of the model until it can be sampled at the "
        "Korean close.")
    # Is there a Korean signal for the U.S. session this panel is about?
    # Only when KOSPI's own newest session IS the current Seoul date. That
    # single test covers every way this goes wrong: a Korean holiday, a
    # weekend, a provider running a day behind, a failed refresh serving
    # the stored copy — and it correctly withholds the signal in the
    # evening, when Seoul's next session has not happened yet.
    kospi = out["series"][KOSPI]
    signal_day = kospi["session_date"]
    if not kospi["ok"]:
        ok, why = False, ("KOSPI could not be read"
                          + (f": {kospi['error']}" if kospi.get("error") else "."))
    elif not kospi["is_today"]:
        ok, why = False, (
            f"The newest Korean session on file is {_pretty(signal_day)}, not "
            f"{_pretty(sess['seoul_date'])}. That move already had its own "
            f"U.S. session — the one sharing its date — so it is not carried "
            f"forward onto this one. Korea is either closed today, or the "
            f"data has not arrived yet.")
    else:
        ok, why = True, None
    out["signal"] = {"ok": ok, "pct": kospi["pct"] if ok else None,
                     "session_date": signal_day, "reason": why,
                     "provisional": kospi["provisional"]}

    # Chip confirmation compares one session against itself or not at all.
    def _same_day(name):
        s = out["series"][name]
        if not s["ok"] or s["session_date"] != signal_day:
            s["off_session"] = bool(s["ok"] and s["session_date"] != signal_day)
            return None
        s["off_session"] = False
        return s["pct"]

    out["chip_confirmation"] = kle.chip_confirmation(
        out["signal"]["pct"], _same_day(SAMSUNG), _same_day(HYNIX))

    hot = [out["series"][n] for n in SIGNAL_SERIES
           if out["series"][n].get("unusual")]
    out["unusual"] = {
        "any": bool(hot),
        "headline": (None if not hot else "UNUSUAL KOREA MOVE"),
        # Phrased as "larger than 91% of" rather than "at the 91st
        # percentile": same number, and it does not need the reader to
        # know what a percentile is.
        "detail": (None if not hot else " · ".join(
            f"{v['label']} {v['pct']:+.2f}% is larger than "
            f"{v['abs_percentile']:.0f}% of its own trailing year"
            for v in hot)),
    }
    return out


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _pretty(iso) -> str:
    """"August 20, 2026" — the house date format, never ISO on screen."""
    try:
        y, m, d = str(iso)[:10].split("-")
        return f"{_MONTHS[int(m) - 1]} {int(d)}, {y}"
    except (ValueError, IndexError, TypeError):
        return str(iso or "an unknown date")


# ── the U.S. side, live ─────────────────────────────────────────────────────

def us_today(symbol: str) -> dict:
    """What the U.S. target is actually doing right now.

    Before 9:30 that is the premarket quote against yesterday's close —
    the true premarket gap, and the number Korea Lead is about. From 9:30
    onward the honest comparison is the OFFICIAL opening gap, because the
    last trade has stopped being an opening price and started being an
    intraday one. Which of the two is being shown is stated in `basis`, and
    the panel never silently swaps one for the other.
    """
    out = {"symbol": symbol.upper(), "ok": False, "gap_pct": None,
           "basis": None, "basis_label": None, "price": None,
           "prev_close": None, "open": None, "quote_age_s": None,
           "as_of": None, "error": None}
    us = _us_session()
    out["us_session"] = us
    if not _QUOTE_FN:
        out["error"] = "no quote provider is wired"
        return out
    key = symbol.upper()
    with _LOCK:
        hit = _QUOTE_MEM.get(key)
    if hit and time.time() - hit[0] < _QUOTE_TTL_S:
        q = hit[1]
    else:
        try:
            q = _QUOTE_FN(key) or {}
        except Exception as exc:      # noqa: BLE001
            out["error"] = str(exc)[:200]
            return out
        with _LOCK:
            _QUOTE_MEM[key] = (time.time(), q)
    if not q:
        out["error"] = f"no live quote for {key}"
        return out
    prev = _f(q.get("close_prev"))
    last = _f(q.get("last"))
    opened = _f(q.get("open"))
    out.update({"price": last, "prev_close": prev, "open": opened,
                "quote_age_s": _f(q.get("stale_seconds"))})
    if not prev or prev <= 0:
        out["error"] = f"no prior close for {key}"
        return out
    if us["opened"] and opened and opened > 0:
        out.update({"ok": True, "basis": "official_open",
                    "basis_label": "official opening gap",
                    "gap_pct": round((opened / prev - 1.0) * 100.0, 3)})
    elif last and last > 0:
        out.update({"ok": True,
                    "basis": "premarket" if not us["opened"] else "last_trade",
                    "basis_label": ("premarket gap" if not us["opened"]
                                    else "last trade against yesterday's close "
                                         "— the session has opened, so this is "
                                         "no longer an opening gap"),
                    "gap_pct": round((last / prev - 1.0) * 100.0, 3)})
    else:
        out["error"] = f"no usable price for {key}"
    out["as_of"] = _now_et().isoformat(timespec="seconds")
    return out


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


# ── the historical study ────────────────────────────────────────────────────

def _window_slice(obs: list, window: str) -> list:
    spec = WINDOWS.get(window) or WINDOWS[DEFAULT_WINDOW]
    if not obs or spec["kind"] == "all":
        return list(obs)
    if spec["kind"] == "sessions":
        return obs[-int(spec["size"]):]
    try:
        last = date.fromisoformat(obs[-1]["date"])
    except ValueError:                # pragma: no cover
        return list(obs)
    cutoff = (last - timedelta(days=int(spec["size"]))).isoformat()
    return [o for o in obs if o["date"] >= cutoff]


def observations(symbol: str, force: bool = False) -> dict:
    """Every matched Korea/U.S. session pair for one target, newest last.

    `through` is yesterday's U.S. session — today is deliberately not in
    its own history. Before the open today's U.S. bar does not exist; after
    the open it exists but is unfinished, and either way scoring today
    against a set that contains today is not a measurement.
    """
    sym = (symbol or "").upper()
    kospi = korea_bars(KOSPI, force=force)
    us = us_bars(sym, force=force)
    # Identity of the inputs, not a clock: the same bars always produce the
    # same matched set, so a cached set is reused only while both series
    # end where they ended when it was built.
    ident = (len(kospi.get("bars") or []),
             kle.bar_date((kospi.get("bars") or [{}])[-1]),
             len(us.get("bars") or []),
             kle.bar_date((us.get("bars") or [{}])[-1]),
             _us_session()["et_date"])
    with _LOCK:
        hit = _OBS_MEM.get(sym)
    if hit and hit[0] == ident and not force:
        return dict(hit[1])
    out = {"symbol": sym, "observations": [], "skipped": {},
           "ok": False, "error": None,
           "sources": {"korea": {"symbol": KOREA_SYMBOLS[KOSPI],
                                 "source": kospi.get("source"),
                                 "bars": len(kospi.get("bars") or []),
                                 "fetched": kospi.get("fetched"),
                                 "stale": bool(kospi.get("stale"))},
                       "us": {"symbol": sym,
                              "source": us.get("source"),
                              "bars": len(us.get("bars") or [])}}}
    if not (kospi.get("bars") or []):
        out["error"] = ("KOSPI history is unavailable, so no Korea Lead "
                        "statistics can be produced: "
                        + str(kospi.get("error") or "unknown reason"))
        return out
    if not us.get("bars"):
        out["error"] = us.get("error") or f"no U.S. daily history for {sym}"
        return out
    # Both series must actually be daily. A provider that quietly answers
    # with weekly or monthly bars still returns a well-formed series, and
    # correlating a monthly Korean return against a daily U.S. open would
    # produce a number that looks entirely reasonable and means nothing.
    for label, pack in (("KOSPI", kospi), (sym, us)):
        grain = kle.is_daily_series(pack.get("bars"))
        out["sources"]["korea" if label == "KOSPI" else "us"]["spacing_days"] \
            = grain["spacing_days"]
        if not grain["daily"]:
            out["error"] = (f"The {label} history is not usable: "
                            f"{grain['reason']}.")
            return out
    korea = kle.close_to_close(kospi["bars"], max_move_pct=_max_move())
    extras = {}
    for name in (SAMSUNG, HYNIX):
        pack = korea_bars(name, force=force)
        if pack.get("bars"):
            extras[name] = kle.close_to_close(pack["bars"],
                                             max_move_pct=_max_move())
        out["sources"][name] = {"symbol": KOREA_SYMBOLS[name],
                                "source": pack.get("source"),
                                "bars": len(pack.get("bars") or []),
                                "ok": bool(pack.get("bars"))}
    measures = kle.us_measures(us["bars"], max_move_pct=_max_move())
    through = _through_date(measures)
    aligned = kle.align(korea, measures, through=through, extras=extras)
    out.update({"observations": aligned["observations"],
                "skipped": aligned["skipped"], "through": through,
                "ok": bool(aligned["observations"])})
    if not out["ok"]:
        out["error"] = ("Korea and this ticker have no completed trading day "
                        "in common in the available history.")
    with _LOCK:
        _OBS_MEM[sym] = (ident, out)
        if len(_OBS_MEM) > 24:
            _OBS_MEM.pop(next(iter(_OBS_MEM)), None)
    return dict(out)


def _through_date(measures: dict) -> str | None:
    """The last U.S. session allowed into history: the most recent COMPLETED
    one. Today is excluded by date, so an in-progress bar spliced into the
    daily feed by the live quote cannot become an observation."""
    if not measures:
        return None
    today_et = _us_session()["et_date"]
    past = [d for d in sorted(measures) if d < today_et]
    return past[-1] if past else None


def _stats_key(symbol: str, window: str, obs: list) -> str:
    """Cache identity: the target, the lookback, the signal definition, the
    engine version, the hash of the active settings, and the exact span of
    sessions being measured.

    The settings hash is in here because without it the rest of this
    sentence was false. Editing the thresholds overlay changes how the
    edges are worded but does NOT change the observation span, so the old
    key kept hitting — and kept serving edge wordings, and a config hash,
    computed under the previous settings — until the next trading day
    rolled the span over on its own.
    """
    first = obs[0]["date"] if obs else "-"
    last = obs[-1]["date"] if obs else "-"
    return "|".join((symbol.upper(), window, kle.SIGNAL_DEFINITION,
                     kle.ENGINE_VERSION, config()[1], first, last,
                     str(len(obs))))


def study(symbol: str, window: str = DEFAULT_WINDOW, force: bool = False) -> dict:
    """The measured relationship between Korea and one U.S. target, over one
    lookback. Cached in memory against the exact observation span."""
    base = observations(symbol, force=force)
    if not base["ok"]:
        return {"ok": False, "error": base["error"], "symbol": base["symbol"],
                "window": window, "sources": base["sources"]}
    obs = _window_slice(base["observations"], window)
    key = _stats_key(symbol, window, obs)
    with _LOCK:
        hit = _STATS_MEM.get(key)
    if hit and not force:
        out = dict(hit[1])
        out["cached"] = True
        return out
    cfg, cfg_hash = config()
    main = kle.study(obs, signal="korea", gates=cfg["edge_gates"])
    out = {
        "ok": True, "symbol": symbol.upper(), "window": window,
        "window_label": (WINDOWS.get(window) or WINDOWS[DEFAULT_WINDOW])["label"],
        "engine": kle.ENGINE_VERSION,
        "signal_definition": kle.SIGNAL_DEFINITION,
        "config_hash": cfg_hash,
        "sources": base["sources"], "skipped": base["skipped"],
        "through": base.get("through"), "cached": False,
        **main,
        # Samsung and SK Hynix measured the same way against the same
        # target, so the details drawer can show whether the chip names
        # carry more than the index does. Not combined into anything.
        "chip_signals": {
            name: kle.measure_stats(obs, "opening_gap", signal=name)
            for name in (SAMSUNG, HYNIX)
        },
    }
    with _LOCK:
        _STATS_MEM[key] = (time.time(), out)
        if len(_STATS_MEM) > 64:
            oldest = min(_STATS_MEM, key=lambda k: _STATS_MEM[k][0])
            _STATS_MEM.pop(oldest, None)
    return dict(out)


def relationship(symbol: str, force: bool = False) -> dict:
    """How strong the Korea/this-ticker relationship is NOW, against how
    strong it has been.

    A five-year average can hold a relationship that has since halved, or
    inverted, and read as healthy the whole way down. So the recent window
    and the long window are both computed and both shown, and when they
    disagree about which way the relationship even runs the answer is
    UNSTABLE rather than an average of the two.
    """
    base = observations(symbol, force=force)
    out = {"symbol": symbol.upper(), "ok": False, "recent": None,
           "long": None, "health": None, "spark": [], "reason": None}
    if not base["ok"]:
        out["reason"] = base["error"]
        return out
    rows = base["observations"]
    # Only the tail is needed: the current value of each window, plus
    # enough 60-session points behind it to draw the trail. Rolling the
    # whole ten years to throw away all but the end of it was costing
    # seconds on a panel that reloads whenever the target changes.
    SPARK = 120
    recent = _kre.rolling_correlation(rows[-(60 + SPARK):], "korea",
                                      "opening_gap", 60)
    longer = _kre.rolling_correlation(rows[-(252 + 1):], "korea",
                                      "opening_gap", 252)
    r_now = recent[-1] if recent else None
    l_now = longer[-1] if longer else None
    out.update({
        "ok": bool(r_now or l_now),
        "recent": r_now and {"window": "60 sessions", **r_now},
        "long": l_now and {"window": "1 year", **l_now},
        # A short trail of the 60-session correlation, for a sparkline. The
        # shape is the point: a number that has been sliding for months is
        # a different thing from the same number holding steady.
        "spark": [x["r"] for x in recent[-SPARK:]],
        "health": _kre.relationship_health(
            r_now["r"] if r_now else None, l_now["r"] if l_now else None,
            r_now["n"] if r_now else 0, l_now["n"] if l_now else 0),
    })
    return out


def window_comparison(symbol: str, force: bool = False) -> list:
    """The same opening-gap measurement across every lookback, side by side.

    A 60-session result and a three-year result are not the same claim, and
    putting them in one table is the cheapest way to make that obvious.
    """
    base = observations(symbol, force=force)
    if not base["ok"]:
        return []
    rows = []
    for w, spec in WINDOWS.items():
        obs = _window_slice(base["observations"], w)
        st = kle.measure_stats(obs, "opening_gap")
        rows.append({"window": w, "label": spec["label"], "n": st["n"],
                     "first_date": obs[0]["date"] if obs else None,
                     "pearson": st["pearson"], "spearman": st["spearman"],
                     "same_direction": st["same_direction"]})
    return rows


# ── the payload ─────────────────────────────────────────────────────────────

def payload(symbol: str, window: str = DEFAULT_WINDOW,
            force: bool = False) -> dict:
    """Everything the Korea Lead panel renders, already decided here.

    The frontend displays this; it does not reproduce any of the research.
    Every section carries its own sample size, source and freshness, so no
    number on screen can appear without the evidence behind it.
    """
    sym = (symbol or "").strip().upper()
    win = window if window in WINDOWS else DEFAULT_WINDOW
    now_et = _now_et()
    korea = korea_today(force=force)
    out = {
        "as_of": now_et.isoformat(timespec="seconds"),
        "as_of_pretty": now_et.strftime("%B %-d, %Y at %-I:%M %p") + " ET",
        "module": MODULE_VERSION, "engine": kle.ENGINE_VERSION,
        "signal_definition": kle.SIGNAL_DEFINITION,
        "config_hash": config()[1],
        "symbol": sym, "window": win,
        "window_label": WINDOWS[win]["label"],
        "windows": [{"key": k, "label": v["label"]} for k, v in WINDOWS.items()],
        "presets": list(TARGET_PRESETS), "controls": list(CONTROL_TARGETS),
        "session": korea["session"],
        "korea": korea,
        "target": None, "opening_gap": None, "premarket_comparison": None,
        "after_open": None, "diagnostics": None,
        "ok": False, "error": None,
    }
    if not sym or len(sym) > 8:
        out["error"] = "A U.S. ticker is required."
        return out
    # The gated signal, not the raw series reading. When KOSPI's newest
    # session is not the current Seoul one there is no signal for this U.S.
    # open, and the panel says which session it actually has.
    sig = korea["signal"]
    kospi_pct = sig["pct"]
    st = study(sym, win, force=force)
    out["sources"] = st.get("sources")
    if not st.get("ok"):
        out["error"] = st.get("error")
        return out
    # The same matched set the statistics above were built from — cached by
    # the identity of the two bar series, so this is a dictionary lookup
    # rather than a second alignment pass.
    obs = _window_slice(observations(sym)["observations"], win)
    cfg, cfg_hash = config()
    implied = kle.implied_gap(obs, kospi_pct,
                              min_n=int(cfg["implied_min_n"]))
    if not sig["ok"]:
        # Say WHY there is no signal. "Korea has not produced a usable move
        # for today yet" is true but useless next to "the newest Korean
        # session on file is August 19".
        implied["reason"] = sig["reason"]
    live = us_today(sym)
    bucket_med = (implied.get("distribution") or {}).get("median_pct")
    cmp_ = kle.premarket_comparison(
        bucket_med, live.get("gap_pct") if live.get("ok") else None,
        near_zero_pct=float(cfg["near_zero_expected_pct"]))

    # A SECOND, independent estimate of today's gap: a line fitted through
    # every matched session, rather than the median of the sessions that
    # landed in today's bucket. Two methods that disagree are reported as
    # disagreeing — never averaged into a third number nothing supports.
    reg = _kre.regression_estimate(obs, "korea", "opening_gap", kospi_pct)
    estimates = _kre.compare_estimates(
        bucket_med, reg.get("expected_pct") if reg.get("ok") else None)

    # Today's residual against every residual this pair has produced before,
    # so "two points light" is judged against what two points light has
    # historically meant HERE rather than against a round number.
    # Built from the FULL matched history, never from the chosen lookback.
    # How unusual today's distance is, is a property of the PAIR — and a
    # one-year window holds fewer sessions than the point-in-time residual
    # needs to warm up at all, so windowing it here meant the label simply
    # never appeared on the lookback the panel opens with.
    hist_resid = []
    if reg.get("ok"):
        pit = [dict(o) for o in observations(sym)["observations"]]
        _kre.expanding_residual(pit, "opening_gap", "korea",
                                min_train=_kre.MIN_TRAIN_N, out_key="_r")
        hist_resid = [o["_r"] for o in pit if o.get("_r") is not None]
    residual = _kre.residual_context(
        cmp_.get("residual_pct"), hist_resid,
        expected_pct=bucket_med,
        actual_pct=live.get("gap_pct") if live.get("ok") else None)
    out.update({
        "ok": True,
        "target": {
            "symbol": sym, "n": st["n"], "window": win,
            "first_date": st["first_date"], "last_date": st["last_date"],
            "is_control": sym in CONTROL_TARGETS,
            "premarket": live,
        },
        "opening_gap": {
            "bias": kle.opening_gap_bias(kospi_pct, implied),
            "implied": implied,
            "stats": st["measures"]["opening_gap"],
            "edge": st["opening_gap_edge"],
            "buckets_up": [b for b in st["buckets"] if b["sign"] == "up"],
            "buckets_down": [b for b in st["buckets"] if b["sign"] == "down"],
        },
        "premarket_comparison": cmp_,
        "residual": residual,
        "estimates": {
            "bucket_pct": bucket_med,
            "regression": reg,
            "agreement": estimates,
        },
        "relationship": relationship(sym, force=force),
        "after_open": {
            "edge": st["after_open_edge"],
            "stats": st["measures"]["open_to_close"],
            "note": ("Measured from the 9:30 opening price to the 4:00 close "
                     "— a separate question from where the stock opens, and "
                     "on the evidence so far a much weaker one. The opening "
                     "gap above and this are never combined into one call."),
        },
        "diagnostics": {
            "measures": st["measures"],
            "chip_signals": st["chip_signals"],
            "windows": window_comparison(sym, force=force),
            "skipped": st["skipped"],
            "through": st.get("through"),
            "cached": st.get("cached"),
            "max_credible_move_pct": kle.MAX_CREDIBLE_MOVE_PCT,
        },
    })
    return out
