"""peers.py — who is this company actually comparable to, and what are they
being valued at?

A valuation percentile against a company's own history answers "is this
expensive for THIS stock". It cannot answer "is this expensive for this KIND
of stock", and a business whose whole industry re-rated will look normal
against itself the entire way down. That second question needs peers.

WHERE THE GROUPING COMES FROM

The SEC assigns every filer a Standard Industrial Classification code and
carries it on the company's own submissions record — free, on every filer,
and the same field the company files under. That is the backbone here. It is
not a perfect taxonomy (Microsoft and Shopify share 7372, "Services-Prepackaged
Software"), which is exactly why a curated override exists and why the level
actually used is always named on screen.

THE HIERARCHY, most specific first:

  DIRECT PEERS  a curated list, or the same four-digit SIC code
  INDUSTRY      the same three-digit SIC group
  SECTOR        the same two-digit SIC major group
  BENCHMARK     everything else with usable figures

A group needs at least five members to be a distribution rather than a
handful of companies. Below that it falls back a level and says so.

HOW THE GROUP IS VALUED

Never an arithmetic average of the members' price/earnings ratios. One member
earning almost nothing produces a P/E in the hundreds and drags the average
somewhere no member is; averaging ratios also silently weights a $2bn company
the same as a $2tn one. The group is valued in aggregate instead:

    Aggregate P/E = total market value of the group
                    ÷ total earnings of the members that earned anything

which is what an index-level multiple actually means. The median member's P/E
is shown beside it, and the number of loss-makers excluded is stated, because
"eleven of nineteen were profitable" is part of the answer.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import invest_engine as engine

try:
    import fundamentals as _fund
except Exception:                                    # pragma: no cover
    _fund = None
try:
    import sec_filings as _sec
except Exception:                                    # pragma: no cover
    _sec = None

SCHEMA_VERSION = "1.0"

MIN_GROUP = 5                # below this a "peer group" is anecdote
MAX_GROUP = 24               # each member costs a filings download
_BUILD_TTL = 12 * 3600.0     # peers move at the speed of quarterly filings
_INDEX_TTL = 90 * 86400.0    # a company's SIC code effectively never changes

_ATOM = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
         "&CIK={cik:010d}&type=10-K&dateb=&owner=include&count=1&output=atom")
_SIC_RE = re.compile(rb"assigned-sic[^>]*>\s*(\d{3,4})\s*<")

_LOCK = threading.RLock()
_DATA_DIR: Path | None = None
_UNIVERSE_FN = None          # () -> {"starred": [...], "all": [...]} or [...]
_QUOTE_FN = None             # (symbol) -> {"price": float, ...} | None
_INDEX: dict | None = None   # SYMBOL -> {"sic": "3571", "name": str, "ts": float}
_BUILDS: dict = {}           # SYMBOL -> (ts, payload)
_INFLIGHT: set = set()

# A short built-in list for names where the four-digit code is too broad to
# be useful on its own. Deliberately small: this is a seed, and the file at
# <data>/invest/peers_curated.json is the one meant to be edited.
CURATED: dict[str, list[str]] = {
    "AAPL": ["MSFT", "GOOGL", "AMZN", "META", "SONY", "DELL", "HPQ"],
    "MSFT": ["GOOGL", "AMZN", "ORCL", "CRM", "ADBE", "SAP", "IBM"],
    "NVDA": ["AMD", "AVGO", "INTC", "QCOM", "TXN", "MU", "ARM"],
    "GOOGL": ["META", "MSFT", "AMZN", "AAPL", "NFLX", "PINS", "SNAP"],
    "AMZN": ["WMT", "COST", "TGT", "BABA", "EBAY", "SHOP", "MELI"],
    "META": ["GOOGL", "SNAP", "PINS", "RDDT", "NFLX", "MSFT", "AMZN"],
    "COST": ["WMT", "TGT", "BJ", "KR", "DG", "DLTR", "SAM"],
    "WMT": ["COST", "TGT", "KR", "DG", "DLTR", "BJ", "AMZN"],
    "MU": ["NVDA", "AMD", "INTC", "TXN", "AVGO", "QCOM", "WDC"],
    "SHOP": ["AMZN", "EBAY", "MELI", "SQ", "PYPL", "WIX", "BIGC"],
}


def configure(universe_fn=None, quote_fn=None, data_dir=None) -> None:
    global _UNIVERSE_FN, _QUOTE_FN, _DATA_DIR, _INDEX
    _UNIVERSE_FN = universe_fn
    _QUOTE_FN = quote_fn
    if data_dir:
        _DATA_DIR = Path(data_dir) / "invest"
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:                            # pragma: no cover
            _DATA_DIR = None
    else:
        _DATA_DIR = None
    with _LOCK:
        _INDEX = None


def available() -> bool:
    return _fund is not None and _sec is not None and _sec.available()


# ── the SIC index ───────────────────────────────────────────────────────────
#
# One small JSON file mapping ticker to SIC code. Built from EDGAR's atom
# endpoint rather than the full submissions record: 17KB against 164KB for
# the same four digits, which is the difference between an index that can be
# warmed over the whole watchlist and one that cannot.

def _index_path() -> Path | None:
    return None if _DATA_DIR is None else _DATA_DIR / "sic_index.json"


def _load_index() -> dict:
    global _INDEX
    with _LOCK:
        if _INDEX is not None:
            return _INDEX
        path = _index_path()
        data = {}
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text()) or {}
            except Exception:
                data = {}
        _INDEX = data
        return _INDEX


def _save_index() -> None:
    path = _index_path()
    if path is None or _INDEX is None:
        return
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_INDEX, separators=(",", ":"), sort_keys=True))
        tmp.replace(path)
    except Exception:                                # pragma: no cover
        pass


def sic_light(symbol: str) -> str | None:
    """Four-digit SIC for one ticker, off EDGAR's small atom response."""
    if not available():
        return None
    cik = _sec.cik_for(symbol)
    if not cik:
        return None
    try:
        raw = _sec._fetch(_ATOM.format(cik=int(cik)), limit=200_000)  # noqa: SLF001
    except Exception:
        return None
    m = _SIC_RE.search(raw)
    return m.group(1).decode().zfill(4) if m else None


def universe() -> list[str]:
    if _UNIVERSE_FN is None:
        return []
    try:
        u = _UNIVERSE_FN() or []
    except Exception:                                # pragma: no cover
        return []
    if isinstance(u, dict):
        u = list(u.get("all") or []) or list(u.get("starred") or [])
    return [str(s).upper().strip() for s in u if s]


def warm_index(budget: int = 40) -> dict:
    """Fill in missing SIC codes, a bounded slice at a time.

    Bounded on purpose: the whole watchlist is about 1,300 tickers, and doing
    them all in one request thread would hold a connection for minutes. A
    slice per pass fills the index over a few background rounds and it then
    stays filled, because a SIC code does not change.
    """
    idx = _load_index()
    now = time.time()
    todo = [s for s in universe()
            if s not in idx or now - float(idx[s].get("ts") or 0) > _INDEX_TTL]
    done = 0
    for sym in todo[: max(0, int(budget))]:
        code = sic_light(sym)
        with _LOCK:
            idx[sym] = {"sic": code, "ts": now}
        done += 1
    if done:
        with _LOCK:
            _save_index()
    return {"indexed": done, "remaining": max(0, len(todo) - done),
            "total": len(idx)}


def index_status() -> dict:
    idx = _load_index()
    known = sum(1 for v in idx.values() if v.get("sic"))
    uni = universe()
    return {"indexed": len(idx), "with_sic": known, "universe": len(uni),
            "missing": max(0, len(uni) - len(idx))}


# ── choosing the group ──────────────────────────────────────────────────────

def curated_for(symbol: str) -> list[str]:
    sym = (symbol or "").upper()
    if _DATA_DIR is not None:
        path = _DATA_DIR / "peers_curated.json"
        if path.exists():
            try:
                data = json.loads(path.read_text()) or {}
                got = data.get(sym) or data.get(sym.lower())
                if got:
                    return [str(s).upper().strip() for s in got if s]
            except Exception:
                pass
    return list(CURATED.get(sym, []))


def peer_group(symbol: str) -> dict:
    """The most specific credible group, and the reason it is that one."""
    sym = (symbol or "").upper().strip()
    idx = _load_index()
    meta = _fund.sic_metadata(sym) if _fund is not None else None
    sic = (meta or {}).get("sic") or (idx.get(sym) or {}).get("sic")

    curated = [s for s in curated_for(sym) if s != sym]
    if len(curated) >= MIN_GROUP:
        return {"level": "DIRECT PEERS", "peers": curated[:MAX_GROUP],
                "sic": sic, "curated": True,
                "sic_description": (meta or {}).get("sic_description") or "",
                "reason": "A curated list for this ticker, which overrides the "
                          "industry code. Automated peer picking gets some "
                          "companies wrong and a hand-written list is allowed "
                          "to win."}

    if not sic:
        return {"level": None, "peers": [], "sic": None, "curated": False,
                "reason": "No industry classification is on file for this "
                          "ticker, so no peer group can be formed."}

    pool = [(s, (v.get("sic") or "")) for s, v in idx.items()
            if s != sym and v.get("sic")]
    for level, width, why in (
            ("DIRECT PEERS", 4, "companies filing under the same four-digit "
                                "industry code"),
            ("INDUSTRY", 3, "companies in the same three-digit industry group"),
            ("SECTOR", 2, "companies in the same two-digit sector")):
        members = [s for s, code in pool if code[:width] == sic[:width]]
        if len(members) >= MIN_GROUP:
            return {"level": level, "peers": sorted(members)[:MAX_GROUP],
                    "sic": sic, "curated": False,
                    "sic_description": (meta or {}).get("sic_description") or "",
                    "reason": f"{len(members)} {why} ({sic[:width]}xx). "
                              + ("" if level == "DIRECT PEERS" else
                                 f"Fewer than {MIN_GROUP} companies shared the "
                                 f"full four-digit code, so the comparison "
                                 f"widened to this level.")}
    broad = sorted(s for s, _c in pool)[:MAX_GROUP]
    if len(broad) >= MIN_GROUP:
        return {"level": "BROAD BENCHMARK", "peers": broad, "sic": sic,
                "curated": False,
                "sic_description": (meta or {}).get("sic_description") or "",
                "reason": f"Fewer than {MIN_GROUP} companies in the index share "
                          f"this company's sector, so the comparison is against "
                          f"the broad list instead. Read it as context, not as "
                          f"a like-for-like comparison."}
    return {"level": None, "peers": [], "sic": sic, "curated": False,
            "sic_description": (meta or {}).get("sic_description") or "",
            "reason": f"Only {len(pool)} companies in this dashboard's universe "
                      f"have an industry code on file so far, which is too few "
                      f"to build a peer group. The index fills in the "
                      f"background as the tab is used."}


# ── the numbers for one member ──────────────────────────────────────────────

def member_metrics(symbol: str) -> dict | None:
    """A compact valuation and quality row for one company.

    Everything comes from the caches Phase 1 already fills — company facts on
    a twelve-hour TTL and the same quote provider the subject uses — so a
    repeat visit costs nothing.
    """
    if _fund is None:
        return None
    sym = (symbol or "").upper().strip()
    facts = _fund.company_facts(sym)
    if not facts or not _fund.eligibility(facts)["ok"]:
        return None
    rev = _fund.metric(facts, "revenue")
    eps = _fund.metric(facts, "eps")
    ni = _fund.metric(facts, "net_income")
    shares = _fund.shares_outstanding(facts)
    if eps.get("value") is None or shares.get("value") is None:
        return None
    price = None
    if _QUOTE_FN is not None:
        try:
            price = (_QUOTE_FN(sym) or {}).get("price")
        except Exception:
            price = None
    price = engine._num(price)
    if price is None or price <= 0:
        return None

    mcap = engine.market_cap(price, shares["value"])
    ocf = _fund.metric(facts, "operating_cash_flow")
    cap = _fund.metric(facts, "capex")
    fcf = (ocf["value"] - cap["value"]
           if ocf.get("value") is not None and cap.get("value") is not None
           else None)
    op = _fund.metric(facts, "operating_income")

    # Quality inputs, so the Quality vector can actually be RANKED against
    # this group rather than merely claiming to be. Everything here comes out
    # of the same cached filings the valuation figures above came from, so a
    # peer costs one download whether one field is read or ten.
    tax = _fund.metric(facts, "tax_expense")
    pretax = _fund.metric(facts, "pretax_income")
    sbc = _fund.metric(facts, "share_based_comp")
    da = _fund.metric(facts, "depreciation_amortization")
    equity = _fund.instant(facts, "equity")
    nd = _fund.net_debt(facts)
    roic_pct = engine.roic(op.get("value"),
                           engine.effective_tax_rate(tax.get("value"),
                                                     pretax.get("value")),
                           equity.get("value"), nd.get("value"))
    fcf_conversion_pct = (fcf / ni["value"] * 100.0
                          if fcf is not None and ni.get("value")
                          and ni["value"] > 0 else None)
    sbc_pct_revenue = (sbc["value"] / rev["value"] * 100.0
                       if sbc.get("value") is not None and rev.get("value")
                       else None)
    ebitda = ((op["value"] + da["value"])
              if op.get("value") is not None and da.get("value") is not None
              else None)
    leverage = (nd["value"] / ebitda
                if nd.get("value") is not None and ebitda and ebitda > 0
                else None)

    prior_rev = (rev.get("prior") or {}).get("value") if rev.get("prior") else None
    if prior_rev is None and rev.get("value") is not None:
        back = _fund._minus_a_year(rev["period_end"])      # noqa: SLF001
        prior_rev = _fund.metric(facts, "revenue", as_of=back).get("value")
    prior_eps = None
    if eps.get("value") is not None:
        back = _fund._minus_a_year(eps["period_end"])      # noqa: SLF001
        prior_eps = _fund.metric(facts, "eps", as_of=back).get("value")

    return {
        "symbol": sym,
        "name": (facts.get("entityName") or "")[:48],
        "price": price,
        "market_cap": mcap,
        "eps_ttm": eps["value"],
        "net_income_ttm": ni.get("value"),
        "revenue_ttm": rev.get("value"),
        "earnings_yield_pct": _pct(engine.earnings_yield(eps["value"], price)),
        "trailing_pe": engine.price_earnings(price, eps["value"]),
        "fcf_ttm": fcf,
        "fcf_yield_pct": _pct(engine.fcf_yield(fcf, mcap)),
        "net_margin_pct": _pct(engine.net_margin(ni.get("value"), rev.get("value"))),
        "operating_margin_pct": _pct(engine.safe_div(op.get("value"),
                                                     rev.get("value"))),
        "revenue_growth_pct": engine.growth(rev.get("value"), prior_rev)["pct"],
        "eps_growth_pct": engine.growth(eps["value"], prior_eps)["pct"],
        "roic_pct": roic_pct,
        "fcf_conversion_pct": fcf_conversion_pct,
        "sbc_pct_revenue": sbc_pct_revenue,
        "leverage": leverage,
        "period_end": eps.get("period_end"),
    }


def _pct(x):
    return None if x is None else x * 100.0


# ── valuing the group ───────────────────────────────────────────────────────

def group_valuation(rows: list[dict]) -> dict:
    """Aggregate multiple, median member multiple, and who was excluded."""
    usable = [r for r in (rows or [])
              if r and r.get("market_cap") and r.get("eps_ttm") is not None]
    if not usable:
        return {"available": False, "n": 0,
                "reason": "No peer had both a market value and reported "
                          "earnings, so the group cannot be valued."}
    profitable, loss_makers = [], []
    for r in usable:
        ni = r.get("net_income_ttm")
        if ni is None:
            ni = (r["eps_ttm"] * r["market_cap"] / r["price"]
                  if r.get("price") else None)
        if ni is not None and ni > 0:
            profitable.append((r, ni))
        else:
            loss_makers.append(r)
    total_cap = sum(r["market_cap"] for r, _ni in profitable)
    total_earnings = sum(ni for _r, ni in profitable)
    aggregate_pe = (total_cap / total_earnings
                    if profitable and total_earnings > 0 else None)
    member_pes = [r["trailing_pe"] for r in usable
                  if r.get("trailing_pe") is not None]
    return {
        "available": aggregate_pe is not None,
        "aggregate_pe": aggregate_pe,
        "aggregate_earnings_yield_pct": (100.0 / aggregate_pe
                                         if aggregate_pe else None),
        "median_member_pe": engine.quantile(member_pes, 0.5),
        "n": len(usable), "n_profitable": len(profitable),
        "n_excluded": len(loss_makers),
        "excluded": sorted(r["symbol"] for r in loss_makers),
        "total_market_cap": total_cap, "total_earnings": total_earnings,
        "basis": "Total market value of the profitable members divided by "
                 "their total earnings — an index-level multiple, not an "
                 "average of ratios",
        "reason": "" if aggregate_pe is not None else
                  "Every member of the group is loss-making, so there is no "
                  "aggregate earnings multiple to compute.",
    }


# ── the full build (background) ─────────────────────────────────────────────

def _cache_path(symbol: str) -> Path | None:
    if _DATA_DIR is None:
        return None
    safe = re.sub(r"[^A-Z0-9._-]", "", symbol.upper())
    return _DATA_DIR / "peers" / f"{safe}.json" if safe else None


def _read_cache(symbol: str) -> dict | None:
    path = _cache_path(symbol)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_cache(symbol: str, payload: dict) -> None:
    path = _cache_path(symbol)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")))
        tmp.replace(path)
    except Exception:                                # pragma: no cover
        pass


def build(symbol: str, subject: dict | None = None) -> dict:
    """Peer group, member rows, aggregate valuation and the subject's rank."""
    sym = (symbol or "").upper().strip()
    group = peer_group(sym)
    out = {"symbol": sym, "schema": SCHEMA_VERSION,
           "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
           "level": group.get("level"), "sic": group.get("sic"),
           "sic_description": group.get("sic_description") or "",
           "curated": group.get("curated"), "reason": group.get("reason"),
           "rows": [], "valuation": {"available": False},
           "subject": None, "ranks": {}}
    if not group.get("peers"):
        return out

    rows = []
    for peer in group["peers"]:
        try:
            m = member_metrics(peer)
        except Exception:                            # noqa: BLE001
            m = None
        if m:
            rows.append(m)
    out["rows"] = sorted(rows, key=lambda r: -(r.get("market_cap") or 0))
    out["n_requested"] = len(group["peers"])
    out["n_resolved"] = len(rows)
    if len(rows) < MIN_GROUP:
        out["reason"] = (
            f"{len(rows)} of {len(group['peers'])} candidate peers could be "
            f"priced from their filings, which is under the {MIN_GROUP} this "
            f"dashboard needs before it will rank anything against them. "
            f"The rows are shown as context only.")
        out["under_minimum"] = True
        return out

    out["valuation"] = group_valuation(rows)
    subj = subject or member_metrics(sym)
    out["subject"] = subj
    if subj:
        out["ranks"] = _ranks(subj, rows)
    return out


_RANK_FIELDS = {
    # field -> higher value is better for the SUBJECT
    "earnings_yield_pct": True,
    "fcf_yield_pct": True,
    "net_margin_pct": True,
    "operating_margin_pct": True,
    "revenue_growth_pct": True,
    "eps_growth_pct": True,
    "roic_pct": True,
    "fcf_conversion_pct": True,
    "sbc_pct_revenue": False,        # more stock issued to staff is worse
    "leverage": False,               # more debt against earnings is worse
}


def _ranks(subject: dict, rows: list[dict]) -> dict:
    out = {}
    for field, higher in _RANK_FIELDS.items():
        vals = [r.get(field) for r in rows if r.get(field) is not None]
        if len(vals) >= MIN_GROUP and subject.get(field) is not None:
            out[field] = {"percentile": engine.rank_within(subject[field], vals,
                                                           higher),
                          "n": len(vals),
                          "median": engine.quantile(vals, 0.5)}
    return out


def values_for(payload: dict, field: str) -> list:
    """Peer values for one field, for ranking the subject elsewhere."""
    return [r.get(field) for r in (payload or {}).get("rows") or []
            if r.get(field) is not None]


def get(symbol: str, subject: dict | None = None, refresh: bool = False) -> dict:
    """Cached peer payload; builds in the background on a cold cache.

    A build downloads a filings record per member, so the first request for a
    ticker returns `status: "building"` and the tab says so rather than
    hanging for half a minute.
    """
    sym = (symbol or "").upper().strip()
    with _LOCK:
        hit = _BUILDS.get(sym)
    if hit and not refresh and time.time() - hit[0] < _BUILD_TTL:
        return {**hit[1], "status": "ready"}
    cached = _read_cache(sym)
    if cached and not refresh:
        ts = float(cached.get("_ts") or 0)
        if time.time() - ts < _BUILD_TTL:
            with _LOCK:
                _BUILDS[sym] = (ts, cached)
            return {**cached, "status": "ready"}

    with _LOCK:
        if sym in _INFLIGHT:
            return {"symbol": sym, "status": "building", "rows": [],
                    "valuation": {"available": False},
                    "reason": "Building the peer group — each member's filings "
                              "are read once and then cached."}
        _INFLIGHT.add(sym)

    def _work():
        try:
            payload = build(sym, subject=subject)
            payload["_ts"] = time.time()
            with _LOCK:
                _BUILDS[sym] = (payload["_ts"], payload)
            _write_cache(sym, payload)
        except Exception:                            # noqa: BLE001
            pass
        finally:
            with _LOCK:
                _INFLIGHT.discard(sym)

    threading.Thread(target=_work, name=f"peers-{sym}", daemon=True).start()
    if cached:
        return {**cached, "status": "refreshing"}
    return {"symbol": sym, "status": "building", "rows": [],
            "valuation": {"available": False},
            "reason": "Building the peer group — each member's filings are "
                      "read once and then cached, so this is slow only the "
                      "first time."}
