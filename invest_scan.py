"""invest_scan.py — the stateful half of the Investment tab.

`fundamentals.py` reads SEC filings, `invest_engine.py` does the arithmetic,
and this module owns everything with a clock, a network or a disk: the
providers, the normalized snapshot, the daily store and the payload the UI
reads.

THE ONE ARCHITECTURAL RULE HERE: the browser never talks to a provider.

Every value the UI shows arrives as a normalized field carrying its own
source, as-of date, basis and staleness. A provider is a small injected
function that either returns one of those or fails; when it fails, the last
value that was successfully stored is served instead, flagged STALE with its
age. Swapping the analyst-estimate provider for a different vendor later is a
change to one function in options_dashboard.py — no field name, no payload
shape and no line of the tab changes.

Providers, in priority order per the house data policy:

  price          Schwab first (the app's own client), then the existing
                 Yahoo chart fallback — injected, never re-implemented here.
  fundamentals   SEC EDGAR Company Facts. Reported, signed, free.
  shares         SEC cover page, so market cap is filings × live price
                 rather than a vendor's number of unknown vintage.
  estimates      the app's existing analyst client. Forward earnings are the
                 one input with no free authoritative source; when it is
                 unavailable the forward fields go N/A and the verdict falls
                 back to the trailing basis, saying so.
  10-year yield  treasury.py, the same official curve the Treasuries tab uses.

The store starts accumulating the moment the tab is first opened for a
ticker. Nothing is back-filled, because a forward estimate that was not
recorded on the day it was made cannot be recovered later — inventing one is
exactly the "fake historical forward P/E" this design refuses to build.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import invest_engine as engine

try:
    import fundamentals as _fund
except Exception:                                    # pragma: no cover
    _fund = None

SCHEMA_VERSION = "1.0"

_DATA_DIR: Path | None = None
_QUOTE_FN = None            # (symbol) -> {"price", "as_of", "source"} | None
_ESTIMATES_FN = None        # (symbol) -> normalized estimates | None
_TEN_YEAR_FN = None         # () -> {"pct", "as_of", "source"} | None
_DAILY_FN = None            # (symbol, days) -> {"bars": [...], "source": str}
_CFG_FN = None              # () -> (investment config dict, thresholds hash)

_LOCK = threading.RLock()
_SNAP_TTL = 900.0           # 15 minutes; the filings behind it move quarterly
_MEM: dict = {}             # SYMBOL -> (ts, snapshot)

# A stored value older than this is not worth serving even as a fallback:
# a share price from three weeks ago is not "stale", it is wrong.
STALE_AFTER_HOURS = {"price": 24.0, "estimates": 30 * 24.0,
                     "treasury_10y": 7 * 24.0}


def configure(quote_fn=None, estimates_fn=None, ten_year_fn=None,
              daily_fn=None, config_fn=None, data_dir=None) -> None:
    global _QUOTE_FN, _ESTIMATES_FN, _TEN_YEAR_FN, _DAILY_FN, _CFG_FN, _DATA_DIR
    _QUOTE_FN = quote_fn
    _ESTIMATES_FN = estimates_fn
    _TEN_YEAR_FN = ten_year_fn
    _DAILY_FN = daily_fn
    _CFG_FN = config_fn
    if data_dir:
        _DATA_DIR = Path(data_dir) / "invest"
        for sub in ("snapshots", "latest"):
            try:
                (_DATA_DIR / sub).mkdir(parents=True, exist_ok=True)
            except Exception:                        # pragma: no cover
                _DATA_DIR = None
                break
    else:
        _DATA_DIR = None
    if _fund is not None:
        _fund.configure(data_dir=data_dir)


_CFG_CACHE = {"cfg": None, "hash": None, "ts": 0.0}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def config(refresh: bool = False) -> tuple[dict, str]:
    """(investment config section, sha256[:16] of the FULL thresholds file).

    Same discipline as premium_edge and timing_engine: repo thresholds.json
    is the default, <data>/thresholds.json overrides key-by-key, cached 60s,
    and the hash of the effective config is stamped on every snapshot.
    """
    if _CFG_FN is not None:                          # test seam
        try:
            return _CFG_FN()
        except Exception:                            # pragma: no cover
            return {}, ""
    with _LOCK:
        if (not refresh and _CFG_CACHE["cfg"] is not None
                and time.time() - _CFG_CACHE["ts"] < 60):
            return _CFG_CACHE["cfg"], _CFG_CACHE["hash"]
        try:
            full = json.loads((Path(__file__).resolve().parent
                               / "thresholds.json").read_text())
        except Exception:                            # pragma: no cover
            full = {}
        if _DATA_DIR is not None:
            try:
                p = _DATA_DIR.parent / "thresholds.json"
                if p.exists():
                    full = _deep_merge(full, json.loads(p.read_text()))
            except Exception:
                pass
        h = hashlib.sha256(json.dumps(full, sort_keys=True,
                                      separators=(",", ":")).encode()
                           ).hexdigest()[:16]
        cfg = full.get("investment") or {}
        _CFG_CACHE.update({"cfg": cfg, "hash": h, "ts": time.time()})
        return cfg, h


# ── store ───────────────────────────────────────────────────────────────────

def _safe(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9._-]", "", (symbol or "").upper())


def _paths(symbol: str):
    if _DATA_DIR is None:
        return None, None
    s = _safe(symbol)
    if not s:
        return None, None
    return _DATA_DIR / "snapshots" / f"{s}.jsonl", _DATA_DIR / "latest" / f"{s}.json"


def _atomic_write(path: Path, text: str) -> bool:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        tmp.replace(path)
        return True
    except Exception:                                # pragma: no cover
        return False


def load_latest(symbol: str) -> dict | None:
    _hist, latest = _paths(symbol)
    if latest is None or not latest.exists():
        return None
    try:
        return json.loads(latest.read_text())
    except Exception:
        return None


def load_history(symbol: str, limit: int = 2000) -> list[dict]:
    """Every daily snapshot ever stored for this ticker, oldest first."""
    hist, _latest = _paths(symbol)
    if hist is None or not hist.exists():
        return []
    out = []
    try:
        for line in hist.read_text().splitlines()[-limit:]:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:                                # pragma: no cover
        return []
    return out


def store(snapshot: dict) -> bool:
    """Persist one snapshot: append to the daily series (one row per date,
    the last write of a day winning) and replace the latest pointer."""
    hist, latest = _paths(snapshot.get("symbol", ""))
    if hist is None:
        return False
    row = _daily_row(snapshot)
    if row is None:
        return False
    with _LOCK:
        rows = load_history(snapshot["symbol"])
        rows = [r for r in rows if r.get("date") != row["date"]]
        rows.append(row)
        rows.sort(key=lambda r: r.get("date") or "")
        ok = _atomic_write(hist, "\n".join(json.dumps(r, separators=(",", ":"))
                                           for r in rows) + "\n")
        ok = _atomic_write(latest, json.dumps(snapshot, separators=(",", ":"))) and ok
    return ok


_DAILY_FIELDS = ("price", "market_cap", "revenue_ttm", "eps_ttm",
                 "revenue_growth_pct", "eps_growth_pct", "eps_forward",
                 "eps_next_year", "forward_pe", "trailing_pe",
                 "earnings_yield_pct", "fcf_yield_pct", "free_cash_flow_ttm",
                 "estimate_change_30d_pct", "estimate_change_90d_pct",
                 "treasury_10y_pct", "shares_outstanding")


def _daily_row(snap: dict) -> dict | None:
    """The prospective daily record. Deliberately flat and small: this is the
    series a future phase re-reads thousands of times."""
    day = (snap.get("as_of") or "")[:10]
    if not day:
        return None
    row = {"date": day, "ticker": snap.get("symbol"),
           "schema": SCHEMA_VERSION}
    for f in _DAILY_FIELDS:
        row[f] = snap.get(f)
    row["sources"] = {k: (v or {}).get("source")
                      for k, v in (snap.get("provenance") or {}).items()}
    row["stale"] = sorted(k for k, v in (snap.get("provenance") or {}).items()
                          if (v or {}).get("stale"))
    return row


# ── providers ───────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _age_hours(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.astimezone()
    return (datetime.now().astimezone() - then).total_seconds() / 3600.0


def _field(value, source, as_of, basis="", reason="", stale=False,
           age_hours=None) -> dict:
    return {"value": value, "source": source, "as_of": as_of, "basis": basis,
            "reason": reason, "stale": bool(stale), "age_hours": age_hours}


def _with_fallback(name: str, live: dict | None, previous: dict | None) -> dict:
    """A provider's answer, or the last good one wearing a STALE label.

    The fallback is bounded: past the per-provider cutoff the old value stops
    being served at all, because a share price from last month presented with
    a small grey "stale" tag is still a wrong number on the screen.
    """
    if live and live.get("value") is not None:
        return live
    prev = ((previous or {}).get("provenance") or {}).get(name)
    if not prev or prev.get("value") is None:
        return live or _field(None, None, None,
                              reason="This provider returned nothing and "
                                     "nothing was stored earlier.")
    age = _age_hours(prev.get("as_of"))
    cfg, _h = config()
    cap = (cfg.get("staleness_hours") or {}).get(name, STALE_AFTER_HOURS.get(name))
    if cap is not None and (age is None or age > cap):
        return _field(None, prev.get("source"), prev.get("as_of"),
                      reason=(f"The provider is unavailable and the stored "
                              f"value is {age:.0f} hours old — past the "
                              f"{cap:.0f}-hour limit for this field, so it is "
                              f"not shown." if age is not None else
                              "The provider is unavailable and the stored "
                              "value has no usable timestamp."))
    return {**prev, "stale": True, "age_hours": age,
            "reason": "Provider unavailable — showing the last value that "
                      "was successfully recorded."}


def _price_provider(symbol: str) -> dict | None:
    if _QUOTE_FN is None:
        return None
    try:
        q = _QUOTE_FN(symbol) or {}
    except Exception:
        return None
    price = q.get("price")
    if price is None:
        return None
    return _field(float(price), q.get("source") or "quote provider",
                  q.get("as_of") or _now_iso(),
                  basis="Last traded price")


def _treasury_provider() -> dict | None:
    if _TEN_YEAR_FN is None:
        return None
    try:
        t = _TEN_YEAR_FN() or {}
    except Exception:
        return None
    if t.get("pct") is None:
        return None
    return _field(float(t["pct"]), t.get("source") or "U.S. Treasury",
                  t.get("as_of") or _now_iso(),
                  basis="Daily par yield curve, 10-year constant maturity")


def _estimates_provider(symbol: str) -> dict | None:
    if _ESTIMATES_FN is None:
        return None
    try:
        e = _ESTIMATES_FN(symbol) or {}
    except Exception:
        return None
    if not e.get("available"):
        return None
    return _field(e, e.get("source") or "analyst estimates provider",
                  e.get("as_of") or _now_iso(),
                  basis="Analyst consensus, adjusted (non-GAAP) earnings basis")


# ── the snapshot ────────────────────────────────────────────────────────────

def snapshot(symbol: str, force: bool = False) -> dict:
    """The normalized Investment snapshot for one ticker.

    Every displayed number is here exactly once, with `provenance[field]`
    carrying its source, as-of date, basis and staleness.
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        return {"symbol": "", "ok": False, "error": "symbol required"}
    with _LOCK:
        hit = _MEM.get(sym)
    if hit and not force and time.time() - hit[0] < _SNAP_TTL:
        return hit[1]

    previous = load_latest(sym)
    prov: dict = {}

    prov["price"] = _with_fallback("price", _price_provider(sym), previous)
    prov["treasury_10y"] = _with_fallback("treasury_10y", _treasury_provider(),
                                          previous)
    prov["estimates"] = _with_fallback("estimates", _estimates_provider(sym),
                                       previous)

    fund = _fund.fundamentals(sym) if _fund is not None else {
        "ok": False, "reason": "The fundamentals reader is not available.",
        "metrics": {}, "shares_outstanding": {"value": None}}
    prov["fundamentals"] = _field(
        bool(fund.get("ok")), fund.get("source") or "SEC EDGAR Company Facts",
        _iso_day(fund.get("fetched_ts")),
        basis="Reported to the SEC in the company's own filings",
        reason=fund.get("reason") or "")

    snap = _assemble(sym, fund, prov)
    with _LOCK:
        _MEM[sym] = (time.time(), snap)
        while len(_MEM) > 64:
            _MEM.pop(min(_MEM, key=lambda k: _MEM[k][0]), None)
    store(snap)
    return snap


def _iso_day(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(
            timespec="seconds")
    except (TypeError, ValueError):
        return None


def _mv(metrics: dict, name: str):
    return (metrics.get(name) or {}).get("value")


def _assemble(sym: str, fund: dict, prov: dict) -> dict:
    metrics = fund.get("metrics") or {}
    est = (prov["estimates"] or {}).get("value") or {}
    price = (prov["price"] or {}).get("value")
    ten_year = (prov["treasury_10y"] or {}).get("value")

    rev = _mv(metrics, "revenue")
    ni = _mv(metrics, "net_income")
    eps = _mv(metrics, "eps")
    shares_avg = _mv(metrics, "diluted_shares")
    fcf = (fund.get("free_cash_flow") or {}).get("value")
    shares_out = (fund.get("shares_outstanding") or {}).get("value")

    mcap = engine.market_cap(price, shares_out)
    eps_fwd = est.get("current_year_eps")
    eps_next = est.get("next_year_eps")

    rev_g = engine.growth(rev, ((metrics.get("revenue") or {}).get("prior") or {}).get("value"))
    eps_g = engine.growth(eps, ((metrics.get("eps") or {}).get("prior") or {}).get("value"))
    fwd_g = engine.growth(eps_next, eps_fwd)

    snap = {
        "symbol": sym, "schema": SCHEMA_VERSION, "engine": engine.ENGINE_VERSION,
        "as_of": _now_iso(),
        "ok": bool(fund.get("ok")),
        "entity_name": fund.get("entity_name") or "",
        "cik": fund.get("cik"),
        "unavailable_reason": "" if fund.get("ok") else (fund.get("reason") or ""),

        "price": price,
        "shares_outstanding": shares_out,
        "market_cap": mcap,

        "revenue_ttm": rev,
        "revenue_growth_pct": rev_g["pct"],
        "revenue_growth_note": rev_g["note"],
        "net_income_ttm": ni,
        "net_margin_pct": _pct(engine.net_margin(ni, rev)),
        "eps_ttm": eps,
        "eps_growth_pct": eps_g["pct"],
        "eps_growth_note": eps_g["note"],
        "diluted_shares_ttm": shares_avg,

        "eps_forward": eps_fwd,
        "eps_next_year": eps_next,
        "forward_eps_growth_pct": fwd_g["pct"],
        "forward_eps_growth_note": fwd_g["note"],
        "estimate_change_30d_pct": est.get("change_30d_pct"),
        "estimate_change_90d_pct": est.get("change_90d_pct"),
        "estimates_available": bool(est),
        "estimates_reason": (prov["estimates"] or {}).get("reason") or "",

        "trailing_pe": engine.price_earnings(price, eps),
        "forward_pe": engine.price_earnings(price, eps_fwd),
        "earnings_yield_pct": _pct(engine.earnings_yield(eps, price)),
        "forward_earnings_yield_pct": _pct(engine.earnings_yield(eps_fwd, price)),
        "free_cash_flow_ttm": fcf,
        "fcf_yield_pct": _pct(engine.fcf_yield(fcf, mcap)),
        "price_sales": engine.safe_div(mcap, rev),
        "treasury_10y_pct": ten_year,

        "period_end": (metrics.get("eps") or {}).get("period_end")
                      or (metrics.get("revenue") or {}).get("period_end"),
        "last_filed": (metrics.get("eps") or {}).get("filed")
                      or (metrics.get("revenue") or {}).get("filed"),
        "provenance": prov,
        "metric_detail": metrics,
        "free_cash_flow_detail": fund.get("free_cash_flow"),
        "shares_detail": fund.get("shares_outstanding"),
    }
    cfg, cfg_hash = config()
    snap["verdict"] = engine.verdict(snap, cfg)
    snap["config_hash"] = cfg_hash
    return snap


def _pct(x):
    return None if x is None else x * 100.0


# ── earnings drivers ────────────────────────────────────────────────────────

def drivers(symbol: str) -> dict:
    """The Earnings Drivers panel: what moved earnings per share over the
    last year, split between revenue, margin and share count."""
    if _fund is None:
        return {"available": False, "reason": "The fundamentals reader is "
                                              "not available."}
    facts = _fund.company_facts(symbol)
    elig = _fund.eligibility(facts)
    if not elig["ok"]:
        return {"available": False, "reason": elig["reason"]}

    cur = {}
    prior = {}
    ends = {}
    for key, name in (("revenue", "revenue"), ("net_income", "net_income"),
                      ("shares", "diluted_shares")):
        m = _fund.metric(facts, name)
        cur[key] = m.get("value")
        ends[key] = m.get("period_end")
        if m.get("value") is None:
            prior[key] = None
            continue
        back = _fund._minus_a_year(m["period_end"])       # noqa: SLF001
        p = _fund.metric(facts, name, as_of=back)
        prior[key] = p.get("value")

    eps_now = _fund.metric(facts, "eps")
    eps_then = (_fund.metric(facts, "eps",
                             as_of=_fund._minus_a_year(eps_now["period_end"]))  # noqa: SLF001
                if eps_now.get("value") is not None else {"value": None})

    out = engine.decompose(prior, cur,
                           reported_eps_prior=eps_then.get("value"),
                           reported_eps_current=eps_now.get("value"))
    out["period_end"] = ends.get("revenue") or eps_now.get("period_end")
    out["prior_period_end"] = eps_then.get("period_end")
    out["inputs"] = {"current": cur, "prior": prior,
                     "reported_eps_current": eps_now.get("value"),
                     "reported_eps_prior": eps_then.get("value")}
    out["reconciles"] = engine.reconciles(out)
    return out


# ── price vs earnings history ───────────────────────────────────────────────

def history(symbol: str, years: int = 3) -> dict:
    """Normalized price against normalized trailing earnings per share.

    Three deliberate refusals live in this function:

    1. Reported earnings are plotted at the date they were FILED, not the
       date the quarter ended. A quarter that ended in March was not public
       until May, and a chart that shows it in March is showing the reader
       information nobody had.
    2. Forward earnings only appear from the first day this dashboard
       recorded one. There is no free archive of what analysts expected in
       2023, and back-filling today's estimate across last year's chart would
       manufacture a history that never happened.
    3. Both series are indexed to 100 at the first common date, so the shape
       comparison is scale-free. Prices and per-share earnings both come from
       split-restated sources, so they move on the same share basis.
    """
    sym = (symbol or "").upper().strip()
    years = 5 if int(years or 3) >= 5 else 3
    start = (date.today() - timedelta(days=int(years * 365.25 + 10))).isoformat()
    out = {"symbol": sym, "years": years, "start": start,
           "price": [], "eps_ttm": [], "eps_forward": [],
           "notes": [], "source": {}}

    bars = []
    if _DAILY_FN is not None:
        try:
            pack = _DAILY_FN(sym, int(years * 366)) or {}
            bars = pack.get("bars") or []
            out["source"]["price"] = pack.get("source") or "daily bars"
        except Exception:
            bars = []
    if not bars:
        out["notes"].append("No daily price history was available from the "
                            "app's price providers, so the chart cannot be "
                            "drawn.")
        return out
    price_pts = [{"date": str(b.get("date") or b.get("d") or "")[:10],
                  "value": _f(b.get("close") if b.get("close") is not None
                              else b.get("c"))}
                 for b in bars]
    price_pts = [p for p in price_pts
                 if p["date"] >= start and p["value"] is not None]

    eps_pts = []
    if _fund is not None:
        facts = _fund.company_facts(sym)
        if facts and _fund.eligibility(facts)["ok"]:
            for row in _fund.ttm_series(facts, "eps"):
                when = row.get("first_filed") or row.get("period_end")
                if when and when >= start:
                    eps_pts.append({"date": when, "value": row["value"],
                                    "period_end": row["period_end"],
                                    "restated_in": row.get("filed")})
            out["source"]["eps_ttm"] = "SEC EDGAR Company Facts (XBRL)"
    if not eps_pts:
        out["notes"].append("No trailing earnings history could be rebuilt "
                            "from this company's filings over this window.")

    fwd_pts = []
    for row in load_history(sym):
        if row.get("date") and row["date"] >= start and row.get("eps_forward") is not None:
            fwd_pts.append({"date": row["date"], "value": row["eps_forward"]})
    if fwd_pts:
        out["source"]["eps_forward"] = "Recorded by this dashboard, one point per day"
        out["notes"].append(
            f"The forward earnings line starts on "
            f"{_pretty(fwd_pts[0]['date'])} because that is the first day "
            f"this dashboard recorded an estimate. Nothing before it exists "
            f"to plot.")
    else:
        out["notes"].append(
            "The forward earnings line is empty: no analyst estimate has been "
            "recorded yet. It begins accumulating from the first day one is "
            "available and is never back-filled.")

    out["price"] = engine.normalize(price_pts)
    out["eps_ttm"] = engine.normalize(eps_pts)
    out["eps_forward"] = engine.normalize(fwd_pts)
    if eps_pts and not out["eps_ttm"]:
        out["notes"].append("Earnings per share was negative at the start of "
                            "this window, so there is no positive base to "
                            "index the line to. The dollar figures are in the "
                            "table above.")
    return out


def _f(v):
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _pretty(iso: str) -> str:
    try:
        return date.fromisoformat(iso[:10]).strftime("%B %-d, %Y")
    except ValueError:                               # pragma: no cover
        return iso


# ── the payload the tab reads ───────────────────────────────────────────────

def payload(symbol: str, force: bool = False, years: int = 3) -> dict:
    sym = (symbol or "").upper().strip()
    snap = snapshot(sym, force=force)
    out = dict(snap)
    out["drivers"] = drivers(sym) if snap.get("ok") else {
        "available": False,
        "reason": snap.get("unavailable_reason") or "No fundamentals."}
    out["profile"] = (_fund.business_description(sym)
                      if (_fund is not None and snap.get("ok")) else None)
    out["history"] = history(sym, years=years)
    out["stored_days"] = len(load_history(sym))
    return out


def record_daily(symbols) -> dict:
    """Take one prospective snapshot per ticker. Called by the app's daily
    scheduler so the store grows whether or not anyone opens the tab."""
    done, failed = [], []
    for sym in symbols or []:
        try:
            snapshot(sym, force=True)
            done.append(sym)
        except Exception:                            # noqa: BLE001
            failed.append(sym)
    return {"recorded": done, "failed": failed, "as_of": _now_iso()}


# ── daily recorder ──────────────────────────────────────────────────────────
#
# Snapshots accumulate two ways: opening the tab for a ticker records that
# ticker, and this thread records the starred list once a day after the
# close. Deliberately NOT the whole 1,289-name watchlist — that would be
# 1,289 SEC downloads a day for a store nothing has asked for yet. Starred
# names are the ones the owner actually follows.

_SCHED = {"started": False, "recorded_for": None}
_STARRED_FN = None
RECORD_AFTER_ET_HOUR = 17
MAX_DAILY_SYMBOLS = 60


def start_scheduler(starred_fn=None) -> bool:
    global _STARRED_FN
    if starred_fn is not None:
        _STARRED_FN = starred_fn
    with _LOCK:
        if _SCHED["started"]:
            return False
        _SCHED["started"] = True
    t = threading.Thread(target=_tick_loop, name="invest-daily", daemon=True)
    t.start()
    return True


def _tick_loop() -> None:                            # pragma: no cover
    while True:
        try:
            tick()
        except Exception:                            # noqa: BLE001
            pass
        time.sleep(900)


def tick(now: datetime | None = None) -> dict | None:
    """One scheduler beat. Records at most once per calendar day."""
    now = now or datetime.now()
    today = now.date().isoformat()
    if now.hour < RECORD_AFTER_ET_HOUR:
        return None
    with _LOCK:
        if _SCHED["recorded_for"] == today:
            return None
        _SCHED["recorded_for"] = today
    syms = []
    if _STARRED_FN is not None:
        try:
            syms = list(_STARRED_FN() or [])[:MAX_DAILY_SYMBOLS]
        except Exception:                            # noqa: BLE001
            syms = []
    if not syms:
        return None
    return record_daily(syms)
