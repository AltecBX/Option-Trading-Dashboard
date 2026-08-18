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
try:
    import peers as _peers
except Exception:                                    # pragma: no cover
    _peers = None

SCHEMA_VERSION = "1.0"

_DATA_DIR: Path | None = None
_QUOTE_FN = None            # (symbol) -> {"price", "as_of", "source"} | None
_ESTIMATES_FN = None        # (symbol) -> normalized estimates | None
_TEN_YEAR_FN = None         # () -> {"pct", "as_of", "source"} | None
_DAILY_FN = None            # (symbol, days) -> {"bars": [...], "source": str}
_CFG_FN = None              # () -> (investment config dict, thresholds hash)
_EARNINGS_FN = None         # (symbol) -> {"next": iso, "last": iso} | None
_EVENTS_FN = None           # (symbol) -> {"kind","label","date"} | None
_BENCHMARK_FN = None        # (symbol) -> sector/benchmark symbol | None

_LOCK = threading.RLock()
_SNAP_TTL = 900.0           # 15 minutes; the filings behind it move quarterly
_MEM: dict = {}             # SYMBOL -> (ts, snapshot)

# A stored value older than this is not worth serving even as a fallback:
# a share price from three weeks ago is not "stale", it is wrong.
STALE_AFTER_HOURS = {"price": 24.0, "estimates": 30 * 24.0,
                     "treasury_10y": 7 * 24.0}


def configure(quote_fn=None, estimates_fn=None, ten_year_fn=None,
              daily_fn=None, config_fn=None, data_dir=None,
              earnings_fn=None, events_fn=None, benchmark_fn=None) -> None:
    global _QUOTE_FN, _ESTIMATES_FN, _TEN_YEAR_FN, _DAILY_FN, _CFG_FN, _DATA_DIR
    global _EARNINGS_FN, _EVENTS_FN, _BENCHMARK_FN
    _QUOTE_FN = quote_fn
    _ESTIMATES_FN = estimates_fn
    _TEN_YEAR_FN = ten_year_fn
    _DAILY_FN = daily_fn
    _CFG_FN = config_fn
    _EARNINGS_FN = earnings_fn
    _EVENTS_FN = events_fn
    _BENCHMARK_FN = benchmark_fn
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
        cfg = _flatten_cfg(full.get("investment") or {})
        _CFG_CACHE.update({"cfg": cfg, "hash": h, "ts": time.time()})
        return cfg, h


# thresholds.json groups the Phase 2 knobs under headings so the file stays
# readable and each group carries its own explanation. The code wants one
# flat dict, so the groups are folded up here rather than every caller
# having to know which heading a key lives under.
_CFG_GROUPS = ("verdict", "scorecard", "value_trap", "regime", "cycle")


def _flatten_cfg(cfg: dict) -> dict:
    out = {k: v for k, v in (cfg or {}).items()
           if k not in _CFG_GROUPS and not k.startswith("_")}
    for group in _CFG_GROUPS:
        for k, v in ((cfg or {}).get(group) or {}).items():
            if not k.startswith("_"):
                out[k] = v
    return out


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
                 "treasury_10y_pct", "shares_outstanding",
                 # Phase 2 — the state that produced the day's verdict
                 "net_margin_pct", "sic", "valuation_window",
                 "target_yield_pct")


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

    # Phase 2 blocks, flattened to scalars. History is never rewritten: a row
    # written before these existed simply lacks the keys, and a reader must
    # treat a missing key as "not recorded that day" rather than as a zero.
    for dim in ("quality", "growth", "valuation", "revisions"):
        block = snap.get(dim) or {}
        row[f"{dim}_score"] = block.get("score")
        row[f"{dim}_label"] = block.get("label")
    val = snap.get("valuation") or {}
    row["valuation_self_percentile"] = val.get("self_percentile")
    row["valuation_peer_percentile"] = val.get("peer_percentile")
    row["regime_shifted"] = (val.get("regime") or {}).get("shifted")
    trap = snap.get("value_trap") or {}
    row["value_trap_level"] = trap.get("level")
    row["value_trap_signals"] = [a.get("key") for a in (trap.get("active") or [])]
    row["business_type"] = (snap.get("business_type") or {}).get("type")
    row["earnings_cycle"] = (snap.get("earnings_cycle") or {}).get("state")
    peers_block = snap.get("peers") or {}
    row["peer_level"] = peers_block.get("level")
    row["peer_n"] = len(peers_block.get("rows") or [])
    row["peer_aggregate_pe"] = (peers_block.get("valuation") or {}).get("aggregate_pe")
    row["verdict"] = (snap.get("verdict") or {}).get("verdict")
    under = snap.get("underreaction") or {}
    row["underreaction_score"] = under.get("score")
    row["config_hash"] = snap.get("config_hash")
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
    # No verdict here on purpose. The Phase 2 verdict reads the four
    # dimensions, the value-trap state and the business type, none of which
    # exist yet at this point; payload() computes it once they do.
    _cfg, cfg_hash = config()
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


# ══════════════════════════════════════════════════════════════════════════
# PHASE 2 — valuation against itself, against peers, and against deterioration
# ══════════════════════════════════════════════════════════════════════════


def _pit_lookup(series, key="first_filed"):
    """Turn a filing series into a step function of what was KNOWN on a date.

    Each point becomes effective on the day it was first filed and stays
    effective until the next one is. That is what "point in time" has to mean
    here: on 12 March a reader had the quarter filed in February, not the one
    that would be filed in May.
    """
    pts = [(str(p.get(key) or p.get("period_end") or "")[:10], p)
           for p in (series or []) if p.get("value") is not None]
    pts = [(d, p) for d, p in pts if d]
    pts.sort(key=lambda x: x[0])
    return pts


def _as_of(pts, day: str):
    """The last point effective on or before `day`, or None."""
    got = None
    for d, p in pts:
        if d <= day:
            got = p
        else:
            break
    return got


VALUATION_MEASURES = ("earnings_yield_pct", "fcf_yield_pct", "trailing_pe")

MEASURE_LABEL = {
    "earnings_yield_pct": "Trailing earnings yield",
    "fcf_yield_pct": "Free cash flow yield",
    "trailing_pe": "Price to earnings, trailing",
}
# For a yield, higher is cheaper. For a multiple, lower is cheaper.
MEASURE_CHEAP_HIGH = {"earnings_yield_pct": True, "fcf_yield_pct": True,
                      "trailing_pe": False}


def valuation_history(symbol: str, years: int = 5) -> dict:
    """What this company has actually been valued at, day by day, using only
    figures that were public on each of those days.

    The three inputs are combined per trading day:
      · the split-adjusted close
      · the trailing earnings (or free cash flow) known on that day
      · the share count known on that day, for the free-cash-flow yield

    Everything the Phase 1 reader already guarantees carries through — the
    latest restatement supplies the value so a split leaves no step, and the
    FIRST filing date supplies the timing so nothing appears before it was
    knowable.
    """
    sym = (symbol or "").upper().strip()
    years = 5 if int(years or 5) >= 5 else 3
    out = {"symbol": sym, "years": years, "available": False,
           "series": {}, "distributions": {}, "regime": {}, "reason": ""}

    if _fund is None:
        out["reason"] = "The fundamentals reader is not available."
        return out
    facts = _fund.company_facts(sym)
    elig = _fund.eligibility(facts)
    if not elig["ok"]:
        out["reason"] = elig["reason"]
        return out

    bars = []
    if _DAILY_FN is not None:
        try:
            bars = (_DAILY_FN(sym, int(years * 366)) or {}).get("bars") or []
        except Exception:
            bars = []
    if len(bars) < 200:
        out["reason"] = (f"Only {len(bars)} daily closes are available for "
                         f"{sym}. A valuation history needs a price for every "
                         f"day it measures.")
        return out

    start = (date.today() - timedelta(days=int(years * 365.25 + 5))).isoformat()
    closes = []
    for b in bars:
        d = str(b.get("date") or b.get("d") or "")[:10]
        c = _f(b.get("close") if b.get("close") is not None else b.get("c"))
        if d and c and c > 0 and d >= start:
            closes.append((d, c))
    closes.sort()
    if len(closes) < 200:
        out["reason"] = (f"Only {len(closes)} daily closes fall inside the "
                         f"{years}-year window.")
        return out

    eps_pts = _pit_lookup(_fund.ttm_series(facts, "eps"))
    ocf_pts = _pit_lookup(_fund.ttm_series(facts, "operating_cash_flow"))
    cap_pts = _pit_lookup(_fund.ttm_series(facts, "capex"))
    sh_pts = _pit_lookup(_fund.pit_series(facts, "diluted_shares"))

    series = {m: [] for m in VALUATION_MEASURES}
    for day, price in closes:
        eps = _as_of(eps_pts, day)
        if eps is not None:
            ey = engine.earnings_yield(eps["value"], price)
            if ey is not None:
                series["earnings_yield_pct"].append(
                    {"date": day, "value": ey * 100.0,
                     "period_end": eps.get("period_end")})
            pe = engine.price_earnings(price, eps["value"])
            if pe is not None:
                series["trailing_pe"].append({"date": day, "value": pe})
        ocf, cap, sh = (_as_of(ocf_pts, day), _as_of(cap_pts, day),
                        _as_of(sh_pts, day))
        if ocf is not None and cap is not None and sh is not None and sh["value"]:
            mcap = price * sh["value"]
            fy = engine.fcf_yield(ocf["value"] - cap["value"], mcap)
            if fy is not None:
                series["fcf_yield_pct"].append({"date": day, "value": fy * 100.0})

    dists = {}
    for measure, pts in series.items():
        if len(pts) < 60:
            dists[measure] = {
                "available": False, "n": len(pts),
                "reason": (f"Only {len(pts)} usable observations over "
                           f"{years} years — too few to place today against.")}
            continue
        current = pts[-1]["value"]
        block = {"available": True, "current": current,
                 "cheap_when_high": MEASURE_CHEAP_HIGH[measure],
                 "label": MEASURE_LABEL[measure], "reason": ""}
        for win in (3, 5):
            if win > years:
                continue
            cut = (date.fromisoformat(pts[-1]["date"])
                   - timedelta(days=int(win * 365.25))).isoformat()
            vals = [p["value"] for p in pts if p["date"] >= cut]
            if len(vals) < 60:
                block[f"{win}y"] = {
                    "available": False, "n": len(vals),
                    "reason": f"Only {len(vals)} observations inside {win} years."}
                continue
            d = engine.distribution(vals, current)
            # Orient the percentile so 100 always means CHEAP, whichever way
            # the underlying measure runs.
            cheap_pct = (d["percentile"] if MEASURE_CHEAP_HIGH[measure]
                         else (None if d["percentile"] is None
                               else 100.0 - d["percentile"]))
            block[f"{win}y"] = {"available": True, **d, "current": current,
                                "cheap_percentile": cheap_pct}
        dists[measure] = block

    out["available"] = any(v.get("available") for v in dists.values())
    out["series"] = {m: pts[::max(1, len(pts) // 400)] for m, pts in series.items()}
    out["distributions"] = dists
    out["regime"] = engine.regime_shift(series["earnings_yield_pct"])
    out["n_days"] = len(closes)
    out["from"] = closes[0][0]
    out["to"] = closes[-1][0]
    out["source"] = {"price": "daily closes from the app's price providers",
                     "fundamentals": "SEC EDGAR Company Facts (XBRL)"}
    if not out["available"]:
        out["reason"] = ("There is price history but not enough reported "
                         "history lined up against it to build a valuation "
                         "range.")
    return out


# ── quality ─────────────────────────────────────────────────────────────────

def quality_block(symbol: str, facts: dict, btype: dict,
                  peer_payload: dict | None = None) -> dict:
    """The six quality inputs, each scored or each explaining its absence."""
    peer_rows = (peer_payload or {}).get("rows") or []
    peer_ok = (peer_payload or {}).get("level") in ("DIRECT PEERS", "INDUSTRY",
                                                    "SECTOR")

    def peer_vals(field):
        return [r.get(field) for r in peer_rows if r.get(field) is not None] \
            if peer_ok else []

    op = _fund.metric(facts, "operating_income")
    rev = _fund.metric(facts, "revenue")
    ni = _fund.metric(facts, "net_income")
    tax = _fund.metric(facts, "tax_expense")
    pretax = _fund.metric(facts, "pretax_income")
    sbc = _fund.metric(facts, "share_based_comp")
    ocf = _fund.metric(facts, "operating_cash_flow")
    cap = _fund.metric(facts, "capex")
    equity = _fund.instant(facts, "equity")
    nd = _fund.net_debt(facts)
    shares = _fund.metric(facts, "diluted_shares")

    fcf = (ocf["value"] - cap["value"]
           if ocf.get("value") is not None and cap.get("value") is not None
           else None)
    tax_rate = engine.effective_tax_rate(tax.get("value"), pretax.get("value"))

    comps = []

    # 1. Return on invested capital
    comps.append(engine.quality_component(
        "roic",
        engine.roic(op.get("value"), tax_rate, equity.get("value"),
                    nd.get("value")),
        peer_vals("roic_pct"),
        reason=(op.get("reason") or equity.get("reason")
                or "Operating profit and shareholders' equity are both needed."),
        allowed=engine.allows(btype, "roic")))

    # 2. How much of the profit turns into cash
    comps.append(engine.quality_component(
        "fcf_conversion",
        (fcf / ni["value"] * 100.0
         if fcf is not None and ni.get("value") and ni["value"] > 0 else None),
        peer_vals("fcf_conversion_pct"),
        reason=("Free cash flow could not be built from this filer's "
                "statements." if fcf is None else
                "Net income is zero or negative, so there is no profit for "
                "cash flow to be a percentage of."),
        allowed=engine.allows(btype, "fcf")))

    # 3. Operating margin trend, in points per year
    margin_pts = _margin_points(facts)
    comps.append(engine.quality_component(
        "operating_margin_trend", engine.trend_slope(margin_pts), [],
        reason=("Operating income is not reported by this filer."
                if op.get("value") is None else
                "Fewer than four quarters of operating margin could be built."),
        allowed=engine.allows(btype, "operating_margin")))

    # 4. Share count trend — dilution or buybacks, in percent per year
    # Point-in-time, not trailing-twelve-month: a weighted-average share
    # count is not a flow to be summed, and Apple reports no separate
    # fourth-quarter figure at all, which empties any contiguity-based series.
    share_pts = [{"date": p.get("first_filed") or p.get("period_end"),
                  "value": p["value"]}
                 for p in (_fund.pit_series(facts, "diluted_shares") or [])]
    share_trend = None
    if len(share_pts) >= 4 and share_pts[-1]["value"]:
        slope = engine.trend_slope(share_pts[-20:])
        if slope is not None:
            share_trend = slope / share_pts[-1]["value"] * 100.0
    comps.append(engine.quality_component(
        "share_count_trend", share_trend, [],
        reason=("Fewer than four twelve-month share counts on file."
                if shares.get("value") is not None else shares.get("reason", ""))))

    # 5. Stock compensation as a share of revenue
    comps.append(engine.quality_component(
        "sbc_pct_revenue",
        (sbc["value"] / rev["value"] * 100.0
         if sbc.get("value") is not None and rev.get("value") else None),
        peer_vals("sbc_pct_revenue"),
        reason=(sbc.get("reason") or rev.get("reason")
                or "Share-based compensation is not separately tagged.")))

    # 6. Leverage
    da = _fund.metric(facts, "depreciation_amortization")
    ebitda = ((op["value"] + da["value"])
              if op.get("value") is not None and da.get("value") is not None
              else None)
    comps.append(engine.quality_component(
        "leverage",
        (nd["value"] / ebitda
         if nd.get("value") is not None and ebitda and ebitda > 0 else None),
        peer_vals("leverage"),
        reason=(nd.get("reason") or da.get("reason") or op.get("reason")
                or "Operating profit before depreciation is not positive, so "
                   "a debt-to-earnings ratio would not mean anything."),
        allowed=engine.allows(btype, "leverage")))

    block = engine.score_dimension(comps)
    # True only when a component was ACTUALLY ranked against peers. The label
    # is a claim on screen; it has to be earned rather than assumed from the
    # existence of a peer group.
    block["peer_ranked"] = any("ranked against" in (c.get("scored_against") or "")
                               for c in comps)
    block["inputs"] = {"operating_income": op, "revenue": rev,
                       "net_income": ni, "free_cash_flow": fcf,
                       "effective_tax_rate": tax_rate, "equity": equity,
                       "net_debt": nd, "ebitda": ebitda,
                       "share_based_comp": sbc}
    return block


def _margin_points(facts: dict) -> list[dict]:
    """Operating margin per trailing-twelve-month point, dated at filing."""
    op = _fund.ttm_series(facts, "operating_income")
    rev = _fund.ttm_series(facts, "revenue")
    by_end = {r["period_end"]: r for r in rev}
    out = []
    for o in op:
        r = by_end.get(o["period_end"])
        if r and r["value"]:
            out.append({"date": o.get("first_filed") or o["period_end"],
                        "value": o["value"] / r["value"] * 100.0,
                        "period_end": o["period_end"]})
    out.sort(key=lambda p: p["date"])
    return out


# ── growth ──────────────────────────────────────────────────────────────────

def growth_block(snap: dict, decomp: dict,
                 peer_payload: dict | None = None) -> dict:
    peer_rows = (peer_payload or {}).get("rows") or []
    peer_ok = (peer_payload or {}).get("level") in ("DIRECT PEERS", "INDUSTRY",
                                                    "SECTOR")

    def pv(field):
        return [r.get(field) for r in peer_rows if r.get(field) is not None] \
            if peer_ok else []

    comps = [
        engine.growth_component("revenue_growth", snap.get("revenue_growth_pct"),
                                pv("revenue_growth_pct"),
                                reason=snap.get("revenue_growth_note") or ""),
        engine.growth_component("eps_growth", snap.get("eps_growth_pct"),
                                pv("eps_growth_pct"),
                                reason=snap.get("eps_growth_note") or ""),
        engine.growth_component("forward_eps_growth",
                                snap.get("forward_eps_growth_pct"), [],
                                reason=snap.get("estimates_reason") or ""),
    ]
    block = engine.score_dimension(comps, min_inputs=1)
    block["drivers"] = engine.growth_drivers(decomp)
    block["peer_ranked"] = any("ranked against" in (c.get("scored_against") or "")
                               for c in comps)
    return block


# ── revisions ───────────────────────────────────────────────────────────────

def revisions_block(snap: dict, estimates: dict | None, cfg: dict) -> dict:
    est = estimates or {}
    block = engine.revisions_score(
        snap.get("estimate_change_30d_pct"), snap.get("estimate_change_90d_pct"),
        up_count=est.get("up_count"), down_count=est.get("down_count"),
        analyst_count=est.get("analyst_count"),
        min_analysts=int(cfg.get("min_analysts", engine.MIN_ANALYSTS)))
    block["current_year_eps"] = snap.get("eps_forward")
    block["next_year_eps"] = snap.get("eps_next_year")
    block["forward_growth_pct"] = snap.get("forward_eps_growth_pct")
    block["basis"] = est.get("basis") or ("Analyst consensus, adjusted "
                                          "(non-GAAP) earnings")
    block["change_basis"] = est.get("change_basis") or ""
    block["gaap_note"] = ("The trailing figures elsewhere on this tab are GAAP "
                          "as filed with the SEC. These estimates are on the "
                          "analysts' adjusted basis. The two are shown side by "
                          "side and never combined inside one ratio.")
    return block


# ── value trap ──────────────────────────────────────────────────────────────

def value_trap_block(symbol: str, facts: dict, snap: dict, quality: dict,
                     valuation: dict, revisions: dict, cfg: dict) -> dict:
    """Deterioration signals — direction of travel, not levels."""
    signals: dict = {}

    r30 = engine._num(snap.get("estimate_change_30d_pct"))
    if r30 is not None and (revisions.get("analyst_count") or 0) >= \
            int(cfg.get("min_analysts", engine.MIN_ANALYSTS)):
        cut_at = float(cfg.get("trap_estimate_cut_pct", -20.0))
        signals["estimates_falling"] = {
            "active": r30 < cut_at,
            "detail": (f"Revision breadth over 30 days is {r30:+.0f}, meaning "
                       f"more analysts are cutting than raising.")
            if r30 < cut_at else
            f"Revision breadth over 30 days is {r30:+.0f}."}

    rev_now = engine._num(snap.get("revenue_growth_pct"))
    rev_prior = _prior_growth(facts, "revenue")
    worse = engine.deteriorating(rev_now, rev_prior,
                                 min_move=float(cfg.get("trap_growth_drop_pp", 3.0)))
    if worse is not None:
        signals["revenue_deteriorating"] = {
            "active": bool(worse) and (rev_now is not None and rev_now < 0),
            "detail": (f"Revenue growth is {rev_now:+.1f}% against "
                       f"{rev_prior:+.1f}% a year earlier.")}

    margin_pts = _margin_points(facts)
    slope = engine.trend_slope(margin_pts)
    if slope is not None:
        signals["margin_deteriorating"] = {
            "active": slope < -float(cfg.get("trap_margin_slope_pp", 1.0)),
            "detail": f"Operating margin is moving {slope:+.1f} points a year."}

    fcf_series = _fcf_ttm_series(facts)
    if len(fcf_series) >= 5:
        now, then = fcf_series[-1]["value"], fcf_series[-5]["value"]
        signals["fcf_deteriorating"] = {
            "active": now < then and now < 0,
            "detail": (f"Free cash flow over the last twelve months is "
                       f"{now / 1e6:,.0f} million against {then / 1e6:,.0f} "
                       f"million a year earlier.")}

    lev = next((c for c in quality.get("components") or []
                if c["key"] == "leverage"), None)
    if lev and lev.get("value") is not None:
        prior_lev = _prior_leverage(facts)
        moved = engine.deteriorating(lev["value"], prior_lev,
                                     worse_when_lower=False, min_move=0.5)
        if moved is not None:
            signals["leverage_rising"] = {
                "active": bool(moved) and lev["value"] > 3.0,
                "detail": (f"Net debt is {lev['value']:.1f} times operating "
                           f"profit before depreciation, against "
                           f"{prior_lev:.1f} a year earlier.")}

    share_trend = next((c for c in quality.get("components") or []
                        if c["key"] == "share_count_trend"), None)
    if share_trend and share_trend.get("value") is not None:
        st = share_trend["value"]
        signals["dilution_rising"] = {
            "active": st > float(cfg.get("trap_dilution_pct", 3.0)),
            "detail": f"The share count is changing {st:+.1f}% a year."}

    # Structural change: a restatement, a reverse split or a going-concern
    # style late filing says the thing being valued is not the thing the
    # history describes. Read from the filing tagger the Gap tab already uses.
    struct = _structural_change(symbol)
    if struct is not None:
        signals["structural_change"] = struct

    # Cyclical peak: margins at the top of their own range for a business
    # whose industry code says margins mean-revert hard.
    peak = _cyclical_peak(snap, margin_pts, cfg)
    if peak is not None:
        signals["cyclical_peak"] = peak

    return engine.value_trap(signals, cfg)


def _prior_growth(facts: dict, metric: str):
    """Year-over-year growth as it stood a year ago, for a trend of a trend."""
    cur = _fund.metric(facts, metric)
    if cur.get("value") is None:
        return None
    back1 = _fund._minus_a_year(cur["period_end"])          # noqa: SLF001
    a = _fund.metric(facts, metric, as_of=back1)
    if a.get("value") is None:
        return None
    back2 = _fund._minus_a_year(a["period_end"])            # noqa: SLF001
    b = _fund.metric(facts, metric, as_of=back2)
    return engine.growth(a["value"], b.get("value"))["pct"]


def _fcf_ttm_series(facts: dict) -> list[dict]:
    ocf = {r["period_end"]: r for r in _fund.ttm_series(facts, "operating_cash_flow")}
    cap = {r["period_end"]: r for r in _fund.ttm_series(facts, "capex")}
    out = [{"period_end": end, "value": ocf[end]["value"] - cap[end]["value"],
            "first_filed": ocf[end].get("first_filed")}
           for end in sorted(set(ocf) & set(cap))]
    return out


def _prior_leverage(facts: dict):
    cur = _fund.metric(facts, "operating_income")
    if cur.get("value") is None:
        return None
    back = _fund._minus_a_year(cur["period_end"])           # noqa: SLF001
    op = _fund.metric(facts, "operating_income", as_of=back)
    da = _fund.metric(facts, "depreciation_amortization", as_of=back)
    nd = _fund.net_debt(facts, as_of=back)
    if op.get("value") is None or da.get("value") is None or nd.get("value") is None:
        return None
    ebitda = op["value"] + da["value"]
    return nd["value"] / ebitda if ebitda > 0 else None


_STRUCTURAL_KINDS = {"RESTATEMENT", "REVERSE SPLIT", "LATE FILING",
                     "AUDITOR CHANGE", "BANKRUPTCY", "DELISTING NOTICE"}


def _structural_change(symbol: str):
    """Recent filings that say the business is not what the history describes."""
    if _EVENTS_FN is None:
        return None
    try:
        hit = _EVENTS_FN(symbol)
    except Exception:                                # noqa: BLE001
        return None
    if not hit:
        return {"active": False, "detail": "No structural filing in the last year."}
    kind = (hit or {}).get("kind")
    if kind in _STRUCTURAL_KINDS:
        return {"active": True,
                "detail": f"{(hit.get('label') or kind)} — filed "
                          f"{_pretty(hit.get('date') or '')}."}
    return {"active": False, "detail": "No structural filing in the last year."}


def _cyclical_peak(snap: dict, margin_pts: list, cfg: dict):
    """Margins near the top of their own multi-year range, in a business
    whose earnings are known to swing with a commodity or a cycle."""
    btype = (snap.get("business_type") or {}).get("type")
    if btype != "CYCLICAL" or len(margin_pts) < 12:
        return None
    vals = [p["value"] for p in margin_pts]
    pct = engine.percentile_rank(vals, vals[-1])
    if pct is None:
        return None
    at = float(cfg.get("trap_cyclical_percentile", 85.0))
    return {"active": pct >= at,
            "detail": (f"Operating margin is at the {pct:.0f}th percentile of "
                       f"its own history, and this is a cyclical business — "
                       f"earnings this good are the ones that do not last.")}


# ── the payload the tab reads ───────────────────────────────────────────────

def payload(symbol: str, force: bool = False, years: int = 3) -> dict:
    """Everything the tab renders, assembled once.

    Phase 2 order of work matters: peers are needed before Quality and Growth
    can be ranked against anything, and the valuation history is needed before
    the verdict can say where today sits. Peers build in a background thread,
    so the first call for a ticker scores on absolute bands and says so, and
    the next one — seconds later — ranks properly.
    """
    sym = (symbol or "").upper().strip()
    cfg, cfg_hash = config()
    snap = snapshot(sym, force=force)
    out = dict(snap)
    out["stored_days"] = len(load_history(sym))
    out["history"] = history(sym, years=years)

    if not snap.get("ok"):
        out["drivers"] = {"available": False,
                          "reason": snap.get("unavailable_reason")
                                    or "No fundamentals."}
        out["profile"] = None
        for key in ("quality", "growth", "valuation", "revisions"):
            out[key] = {"score": None, "label": engine.NOT_RATED,
                        "reason": snap.get("unavailable_reason") or ""}
        out["value_trap"] = {"level": engine.NOT_RATED, "active": [],
                             "reason": snap.get("unavailable_reason") or ""}
        out["peers"] = {"status": "unavailable", "rows": [],
                        "reason": snap.get("unavailable_reason") or ""}
        out["valuation_history"] = {"available": False,
                                    "reason": snap.get("unavailable_reason") or ""}
        out["verdict"] = engine.verdict(out, cfg)
        return out

    facts = _fund.company_facts(sym)
    out["drivers"] = drivers(sym)
    out["profile"] = _fund.business_description(sym)

    # 1. Peers — everything relative is ranked against these.
    peer_payload = {"status": "unavailable", "rows": [], "reason":
                    "The peer engine is not available."}
    if _peers is not None:
        try:
            peer_payload = _peers.get(sym)
        except Exception as exc:                     # noqa: BLE001
            peer_payload = {"status": "error", "rows": [],
                            "reason": f"Peer group failed: {exc}"}
    out["peers"] = peer_payload

    # 2. Valuation against its own history.
    vhist = valuation_history(sym, years=5)
    out["valuation_history"] = vhist
    self_pct, window = _cheap_percentile(vhist)
    out["valuation_window"] = window

    # 3. Valuation against comparable businesses. A BROAD BENCHMARK group is
    #    deliberately NOT used for this: ranking a bank's earnings yield
    #    against a software company's is arithmetic, not comparison.
    peer_pct = None
    if peer_payload.get("level") in ("DIRECT PEERS", "INDUSTRY", "SECTOR"):
        vals = [r.get("earnings_yield_pct") for r in peer_payload.get("rows") or []
                if r.get("earnings_yield_pct") is not None]
        if len(vals) >= engine.MIN_PEERS:
            peer_pct = engine.rank_within(out.get("earnings_yield_pct"), vals, True)

    out["valuation"] = engine.valuation_score(self_pct, peer_pct,
                                              vhist.get("regime"))
    out["valuation"]["window"] = window
    out["valuation"]["regime"] = vhist.get("regime")

    # 4. Business type gates what may be computed at all.
    meta = _fund.sic_metadata(sym) or {}
    btype = engine.business_type(meta.get("sic"), out.get("eps_ttm"),
                                 ok=bool(snap.get("ok")))
    out["business_type"] = btype
    out["sic"] = meta.get("sic")
    out["sic_description"] = meta.get("sic_description") or ""

    # 5. The four vectors.
    out["quality"] = quality_block(sym, facts, btype, peer_payload)
    out["growth"] = growth_block(out, out["drivers"], peer_payload)
    est = ((snap.get("provenance") or {}).get("estimates") or {}).get("value") or {}
    out["revisions"] = revisions_block(out, est, cfg)

    # 6. Is the cheapness real?
    out["value_trap"] = value_trap_block(sym, facts, out, out["quality"],
                                         out["valuation"], out["revisions"], cfg)

    # 7. Context.
    out["earnings_cycle"] = _cycle(sym, cfg)
    out["drawdowns"] = _drawdowns(sym, years=5)
    out["underreaction"] = _underreaction(sym, out, peer_payload)

    # 8. The price that would change the answer.
    out["target_yield_pct"] = _target_yield(vhist, cfg)

    out["verdict"] = engine.verdict(out, cfg)
    out["config_hash"] = cfg_hash
    store(out)              # the enriched row replaces the base one for today
    return out


def _cheap_percentile(vhist: dict):
    """Where today sits in its own history, oriented so 100 means cheap.

    Prefers the five-year window; falls back to three when five is too thin.
    """
    dists = (vhist or {}).get("distributions") or {}
    block = dists.get("earnings_yield_pct") or {}
    for win, label in (("5y", "5-year"), ("3y", "3-year")):
        w = block.get(win)
        if w and w.get("available") and w.get("cheap_percentile") is not None:
            return w["cheap_percentile"], label
    return None, None


def _target_yield(vhist: dict, cfg: dict):
    """The earnings yield at this company's own median valuation — the level
    the "reconsider near" sentence is solved for."""
    dists = (vhist or {}).get("distributions") or {}
    block = dists.get("earnings_yield_pct") or {}
    for win in ("5y", "3y"):
        w = block.get(win)
        if w and w.get("available") and w.get("median") is not None:
            return w["median"]
    return None


def _cycle(symbol: str, cfg: dict) -> dict:
    dates = {}
    if _EARNINGS_FN is not None:
        try:
            dates = _EARNINGS_FN(symbol) or {}
        except Exception:                            # noqa: BLE001
            dates = {}
    return engine.earnings_cycle(
        date.today().isoformat(), next_date=dates.get("next"),
        last_date=dates.get("last"),
        pre_days=int(cfg.get("cycle_pre_days", 14)),
        fresh_days=int(cfg.get("cycle_fresh_days", 21)),
        stale_days=int(cfg.get("cycle_stale_days", 100)))


def _drawdowns(symbol: str, years: int = 5) -> dict:
    if _DAILY_FN is None:
        return {"available": False,
                "reason": "No daily price history provider is wired."}
    try:
        bars = (_DAILY_FN(symbol, int(years * 366)) or {}).get("bars") or []
    except Exception:                                # noqa: BLE001
        bars = []
    return engine.drawdowns([{"date": str(b.get("date") or "")[:10],
                              "close": b.get("close")} for b in bars])


def _underreaction(symbol: str, snap: dict, peer_payload: dict) -> dict:
    """EXPERIMENTAL. Two separately standardised signals, differenced.

    Both z-scores need a cross-section to standardise within, and this
    dashboard only records forward estimates from the day Phase 1 shipped —
    so on most tickers this reports that it cannot be computed yet, which is
    the honest state rather than a number filled in from nothing.
    """
    rows = load_history(symbol)
    price = engine._num(snap.get("price"))
    eps_now = engine._num(snap.get("eps_forward"))
    eps_then = None
    if rows:
        cut = (date.today() - timedelta(days=90)).isoformat()
        older = [r for r in rows if r.get("date") <= cut
                 and r.get("eps_forward") is not None]
        if older:
            eps_then = engine._num(older[-1].get("eps_forward"))
    intensity = engine.revision_intensity(eps_now, eps_then, price)

    peer_intensities = []
    for r in (peer_payload or {}).get("rows") or []:
        prows = load_history(r.get("symbol") or "")
        if not prows:
            continue
        cut = (date.today() - timedelta(days=90)).isoformat()
        older = [x for x in prows if x.get("date") <= cut
                 and x.get("eps_forward") is not None]
        latest = prows[-1] if prows else None
        if older and latest and latest.get("eps_forward") is not None \
                and latest.get("price"):
            peer_intensities.append(engine.revision_intensity(
                latest["eps_forward"], older[-1]["eps_forward"], latest["price"]))

    rel = _relative_90d(symbol)
    peer_rels = [x for x in (_relative_90d(r.get("symbol"))
                             for r in (peer_payload or {}).get("rows") or [])
                 if x is not None]
    out = engine.underreaction(engine.zscore(intensity, peer_intensities),
                               engine.zscore(rel, peer_rels))
    out["revision_intensity"] = intensity
    out["relative_return_90d_pct"] = rel
    out["revision_breadth_30d_pct"] = snap.get("estimate_change_30d_pct")
    out["analyst_count"] = (snap.get("revisions") or {}).get("analyst_count")
    cycle = snap.get("earnings_cycle") or {}
    since = cycle.get("days_since_last")
    out["earnings_inside_window"] = (None if since is None else since <= 90)
    return out


def _relative_90d(symbol: str | None):
    """Stock's 90-day return minus its benchmark's, in points."""
    if not symbol or _DAILY_FN is None:
        return None
    bench = None
    if _BENCHMARK_FN is not None:
        try:
            bench = _BENCHMARK_FN(symbol)
        except Exception:                            # noqa: BLE001
            bench = None
    a = _return_90d(symbol)
    b = _return_90d(bench) if bench else None
    return engine.relative_return(a, b)


def _return_90d(symbol: str):
    try:
        bars = (_DAILY_FN(symbol, 150) or {}).get("bars") or []
    except Exception:                                # noqa: BLE001
        return None
    closes = [_f(b.get("close")) for b in bars if _f(b.get("close"))]
    if len(closes) < 60:
        return None
    then = closes[-min(63, len(closes))]
    if not then:
        return None
    return (closes[-1] / then - 1.0) * 100.0


def record_daily(symbols) -> dict:
    """Take one prospective snapshot per ticker. Called by the app's daily
    scheduler so the store grows whether or not anyone opens the tab."""
    done, failed = [], []
    for sym in symbols or []:
        try:
            payload(sym, force=True)          # the full Phase 2 state
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
# The industry index behind peer groups is built a slice at a time.
# A ticker's SIC code never changes, so once a name is in it stays in.
PEER_INDEX_BUDGET = 120


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
    warmed = None
    if _peers is not None:
        try:
            warmed = _peers.warm_index(budget=int(PEER_INDEX_BUDGET))
        except Exception:                            # noqa: BLE001
            warmed = None
    if not syms:
        return {"recorded": [], "failed": [], "peer_index": warmed,
                "as_of": _now_iso()} if warmed else None
    out = record_daily(syms)
    out["peer_index"] = warmed
    return out
