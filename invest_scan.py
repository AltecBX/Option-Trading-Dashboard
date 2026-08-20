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
from datetime import (date, datetime, time as dtime, timedelta,
                      timezone, tzinfo)
from pathlib import Path

import invest_engine as engine
import fair_value as fv
import structures as _structs
import bank_model as _bank
import reit_model as _reit
import insurance_model as _ins
import broker_model as _brk
import business_routing as _route
import filing_tables as _tables
import cross_check as _xcheck
import capture_health as _health
import invest_audit as _audit
import forward_test as _forward
import covered_call as _cc
import chain_store
import bt_iv

try:
    import invest_options as _opts
except Exception:                                    # pragma: no cover
    _opts = None
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
_CHAIN_FN = None            # (symbol) -> normalized option chain | None
_RATE_FN = None             # (years) -> {"pct","as_of","source"} | None
_EARN_MOVES_FN = None       # (symbol) -> {"avg_abs","n"} | None
_ACTIONS_FN = None          # (symbol) -> {"dividends": {iso: amount}} | None
_CC_CHAIN_FN = None         # (symbol) -> the near-dated chain, for capture
_TABLES_FN = None           # (symbol, wanted) -> filing-table readings
_ALERT_FN = None            # (title, message) -> whatever the app's push does

_LOCK = threading.RLock()
_SNAP_TTL = 900.0           # 15 minutes; the filings behind it move quarterly
_MEM: dict = {}             # SYMBOL -> (ts, snapshot)

# A stored value older than this is not worth serving even as a fallback:
# a share price from three weeks ago is not "stale", it is wrong.
STALE_AFTER_HOURS = {"price": 24.0, "estimates": 30 * 24.0,
                     "treasury_10y": 7 * 24.0}


def configure(quote_fn=None, estimates_fn=None, ten_year_fn=None,
              daily_fn=None, config_fn=None, data_dir=None,
              earnings_fn=None, events_fn=None, benchmark_fn=None,
              chain_fn=None, rate_fn=None, earn_moves_fn=None,
              actions_fn=None, cc_chain_fn=None, tables_fn=None,
              alert_fn=None) -> None:
    global _QUOTE_FN, _ESTIMATES_FN, _TEN_YEAR_FN, _DAILY_FN, _CFG_FN, _DATA_DIR
    global _EARNINGS_FN, _EVENTS_FN, _BENCHMARK_FN
    global _CHAIN_FN, _RATE_FN, _EARN_MOVES_FN, _ACTIONS_FN, _CC_CHAIN_FN
    global _TABLES_FN, _ALERT_FN
    _TABLES_FN = tables_fn
    if alert_fn is not None:
        _ALERT_FN = alert_fn
    _ACTIONS_FN = actions_fn
    _CC_CHAIN_FN = cc_chain_fn
    _QUOTE_FN = quote_fn
    _ESTIMATES_FN = estimates_fn
    _TEN_YEAR_FN = ten_year_fn
    _DAILY_FN = daily_fn
    _CFG_FN = config_fn
    _EARNINGS_FN = earnings_fn
    _EVENTS_FN = events_fn
    _BENCHMARK_FN = benchmark_fn
    _CHAIN_FN = chain_fn
    _RATE_FN = rate_fn
    _EARN_MOVES_FN = earn_moves_fn
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
    # The capture log lives beside the snapshots and is deliberately its own
    # store: it is operational, it can be thrown away, and no stored
    # recommendation depends on it.
    try:
        _health.configure(data_dir,
                          keep_days=(config()[0] or {}).get("capture_keep_days"))
    except Exception:                                # pragma: no cover
        _health.configure(data_dir)
    if _fund is not None:
        _fund.configure(data_dir=data_dir)
    if _opts is not None:
        _opts.configure(chain_fn=chain_fn, bars_fn=daily_fn, rate_fn=rate_fn,
                        earnings_fn=earnings_fn, earn_moves_fn=earn_moves_fn,
                        data_dir=data_dir,
                        # Long-dated observations are dated by the same
                        # exchange clock as everything else, so an evening
                        # capture cannot land on tomorrow.
                        today_fn=lambda: market_now().date())


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
        # The hash goes onto every stored recommendation. On its own it is a
        # fingerprint of rules nobody kept — so the rules themselves are
        # written down beside it, once, under that hash.
        archive_config(full, h)
        _CFG_CACHE.update({"cfg": cfg, "hash": h, "ts": time.time()})
        return cfg, h


# ── the configuration archive ───────────────────────────────────────────────
#
# A stored recommendation carries a config hash so a future scoring pass can
# tell which rules produced it. That only works if the rules can still be
# read. Thresholds change — every phase of this work has changed some — and
# once they do, a hash on a year-old row identifies a configuration that
# exists nowhere.
#
# So each distinct configuration is written once, under its own hash, and
# never rewritten. A hash already on disk is left exactly as it is: the
# whole point is that what generated an old recommendation is recoverable
# unchanged.

def _config_dir() -> Path | None:
    return (_DATA_DIR / "config") if _DATA_DIR is not None else None


def archive_config(full: dict, cfg_hash: str) -> bool:
    """Write one configuration under its hash. Returns True if it was new."""
    d = _config_dir()
    if d is None or not cfg_hash or not full:
        return False
    p = d / f"{cfg_hash}.json"
    if p.exists():
        return False
    try:
        d.mkdir(parents=True, exist_ok=True)
        payload = {"config_hash": cfg_hash, "first_seen": _now_iso(),
                   "thresholds": full}
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=True))
        # Never clobber: another worker may have written it a moment ago,
        # and the first writer's copy is the one that matters.
        if p.exists():
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(p)
    except Exception:                                # pragma: no cover
        return False
    return True


def load_config_archive(cfg_hash: str) -> dict | None:
    """The exact configuration behind a stored recommendation, or None."""
    d = _config_dir()
    if d is None or not cfg_hash:
        return None
    p = d / f"{re.sub(r'[^0-9a-f]', '', cfg_hash.lower())}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:                                # pragma: no cover
        return None


def archived_configs() -> list:
    """Every configuration ever archived, oldest first."""
    d = _config_dir()
    if d is None or not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            got = json.loads(p.read_text())
        except Exception:                            # pragma: no cover
            continue
        out.append({"config_hash": got.get("config_hash") or p.stem,
                    "first_seen": got.get("first_seen"),
                    "bytes": p.stat().st_size})
    return sorted(out, key=lambda r: r.get("first_seen") or "")


# thresholds.json groups the Phase 2 knobs under headings so the file stays
# readable and each group carries its own explanation. The code wants one
# flat dict, so the groups are folded up here rather than every caller
# having to know which heading a key lives under.
_CFG_GROUPS = ("verdict", "scorecard", "value_trap", "regime", "cycle",
               "fair_value", "expected_return", "implied_expectations",
               "structures", "contracts",
               "bank", "reit", "covered_call", "forward_test",
               "insurance", "broker", "chain_capture",
               # Phase 6 and 7. These were documented in thresholds.json and
               # never folded into the flat config, so every one of them read
               # its module default and the file was decoration. Folding them
               # in changes no value — each entry already matches its
               # default — and makes the file mean what it says.
               "routing", "filing_tables", "insurance_basis", "cross_check",
               "capture_health")


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

    # Phase 3 state, flattened. Same discipline as Phase 2: a row written
    # before these keys existed simply lacks them, and a reader must treat a
    # missing key as "not recorded that day" rather than as a zero. Nothing
    # already on disk is ever rewritten.
    fair = snap.get("fair_value") or {}
    row["fair_value_bear"] = fair.get("bear")
    row["fair_value_base"] = fair.get("base")
    row["fair_value_bull"] = fair.get("bull")
    row["fair_value_confidence"] = fair.get("confidence_level")
    row["fair_value_spread"] = (fair.get("confidence") or {}).get("spread")
    row["fair_value_method"] = fair.get("base_method")
    row["credited_fair_value"] = fair.get("credited")
    row["buy_zone"] = fair.get("buy_zone")
    row["premium_to_buy_zone_pct"] = fair.get("premium_to_buy_zone_pct")

    er = snap.get("expected_return") or {}
    for s in ("bear", "base", "bull"):
        cell = (er.get("scenarios") or {}).get(s) or {}
        row[f"expected_price_{s}"] = cell.get("price_end")
        row[f"expected_cagr_{s}_pct"] = cell.get("total_cagr_pct")
    row["expected_cagr_weighted_pct"] = er.get("weighted_total_cagr_pct")
    row["expected_horizon_years"] = er.get("years")

    imp = snap.get("implied_expectations") or {}
    row["implied_fcf_growth_pct"] = (imp.get("implied") or {}).get("growth_pct")
    row["implied_growth_min_pct"] = (imp.get("grid") or {}).get("min_pct")
    row["implied_growth_max_pct"] = (imp.get("grid") or {}).get("max_pct")
    row["expectations_gap_pp"] = (imp.get("gap") or {}).get("gap_pp")

    row["scenario_probabilities"] = snap.get("scenario_probabilities")
    row["dividends_per_share_ttm"] = (snap.get("dividends") or {}).get("value")

    structs = snap.get("structures") or {}
    comp = structs.get("comparison") or {}
    row["preferred_structure"] = comp.get("preferred")
    row["comparison_expiration"] = comp.get("expiration")
    row["comparison_toss_up"] = comp.get("toss_up")
    row["structure_returns_pct"] = {
        r.get("kind"): r.get("weighted_annualized_pct")
        for r in (comp.get("rows") or []) if r.get("eligible")}
    put = structs.get("put") or {}
    best_put = put.get("best") or {}
    row["csp_strike"] = (best_put.get("contract") or {}).get("strike")
    row["csp_expiration"] = best_put.get("expiration")
    row["csp_credit"] = (best_put.get("contract") or {}).get("credit")
    row["csp_clears_hurdle"] = put.get("clears_hurdle")
    leaps = next((r for r in (comp.get("rows") or [])
                  if r.get("kind") == _structs.LEAPS), None) or {}
    row["leaps_strike"] = (leaps.get("contract") or {}).get("strike")
    row["leaps_expiration"] = leaps.get("expiration")
    row["leaps_debit"] = (leaps.get("contract") or {}).get("debit")
    bw = next((r for r in (comp.get("rows") or [])
               if r.get("kind") == _structs.BUY_WRITE), None) or {}
    row["buy_write_call_strike"] = (bw.get("contract") or {}).get("call_strike")
    row["buy_write_credit"] = (bw.get("contract") or {}).get("call_credit")

    entry = snap.get("entry") or {}
    row["entry_verdict"] = entry.get("verdict")
    row["entry_reason"] = (entry.get("reasons") or [None])[0]
    row["entry_flip_trigger"] = (entry.get("what_would_change") or [None])[0]

    # Phase 4 state. Same discipline once more: a row written before these
    # keys existed simply lacks them, and the forward-validation engine
    # treats a missing key as "not recorded that day" rather than as a zero.
    bank = snap.get("bank") or {}
    if bank:
        row["bank_price_to_tangible_book"] = (
            bank.get("price_to_tangible_book") or {}).get("value")
        row["bank_price_to_book"] = (bank.get("price_to_book") or {}).get("value")
        row["bank_rotce_pct"] = (
            bank.get("return_on_tangible_common_equity_pct") or {}).get("value")
        row["bank_efficiency_ratio_pct"] = (
            bank.get("efficiency_ratio_pct") or {}).get("value")
        row["bank_capital_ratio_pct"] = (bank.get("capital_ratio") or {}).get("value")
        row["bank_charge_off_rate_pct"] = (
            bank.get("charge_off_rate_pct") or {}).get("value")
    reit = snap.get("reit") or {}
    if reit:
        row["reit_ffo_per_share"] = (reit.get("ffo_per_share") or {}).get("value")
        row["reit_price_to_ffo"] = (reit.get("price_to_ffo") or {}).get("value")
        row["reit_payout_of_ffo_pct"] = (
            reit.get("payout_of_ffo_pct") or {}).get("value")
        row["reit_property_type"] = reit.get("property_type")
        row["reit_ffo_complete"] = (reit.get("ffo") or {}).get("complete")
    row["fair_value_model"] = (snap.get("fair_value") or {}).get("model")

    # Phase 5 state. Same discipline a third time: a row written before these
    # keys existed simply lacks them, nothing already on disk is rewritten,
    # and a reader treats a missing key as "not recorded that day".
    ins = snap.get("insurance") or {}
    if ins:
        row["insurance_subtype"] = ins.get("subtype")
        row["insurance_metric_basis"] = ins.get("metric_basis")
        row["insurance_price_to_book"] = (
            ins.get("price_to_book") or {}).get("value")
        row["insurance_price_to_tangible_book"] = (
            ins.get("price_to_tangible_book") or {}).get("value")
        row["insurance_roe_pct"] = (
            ins.get("return_on_equity_pct") or {}).get("value")
        row["insurance_combined_ratio_pct"] = (
            ins.get("combined_ratio_pct") or {}).get("value")
        row["insurance_loss_ratio_pct"] = (
            ins.get("loss_ratio_pct") or {}).get("value")
        row["insurance_reserve_development_pct"] = (
            ins.get("reserve_development_pct_premiums") or {}).get("value")
        row["insurance_reserve_development_state"] = (
            ins.get("reserve_development_state") or {}).get("state")
        row["insurance_premium_growth_pct"] = (
            ins.get("premium_growth_pct") or {}).get("value")
    brk = snap.get("broker") or {}
    if brk:
        row["broker_subtype"] = brk.get("subtype")
        row["broker_is_broker_dealer"] = (
            brk.get("broker_evidence") or {}).get("is_broker")
        row["broker_price_to_book"] = (
            brk.get("price_to_book") or {}).get("value")
        row["broker_price_to_tangible_book"] = (
            brk.get("price_to_tangible_book") or {}).get("value")
        row["broker_roe_pct"] = (
            brk.get("return_on_equity_pct") or {}).get("value")
        row["broker_assets_to_equity"] = (
            brk.get("assets_to_equity") or {}).get("value")
        row["broker_compensation_ratio_pct"] = (
            brk.get("compensation_ratio_pct") or {}).get("value")
        row["broker_net_interest_share_pct"] = (
            brk.get("net_interest_share_of_revenue_pct") or {}).get("value")
        row["broker_has_banking_operation"] = brk.get("has_banking_operation")
    row["fair_value_subtype"] = (snap.get("fair_value") or {}).get("subtype")
    row["risk_flags"] = [a.get("key")
                         for a in ((snap.get("value_trap") or {})
                                   .get("active") or [])]

    # Phase 6 state. Same discipline a fourth time: nothing already on disk
    # is rewritten, and a reader treats a missing key as "not recorded that
    # day". What is added is WHY this company went to the engine it went to,
    # so a future scoring pass can tell a change of answer from a change of
    # model.
    routing = snap.get("routing") or {}
    if routing:
        row["business_class"] = routing.get("business_class")
        row["primary_model"] = routing.get("model")
        row["routing_confidence"] = routing.get("confidence")
        row["secondary_businesses"] = list(routing.get("secondary") or [])
        row["routing_evidence"] = [
            {"business": e.get("business"), "share_pct": e.get("share_pct"),
             "measure": e.get("measure"), "material": e.get("material")}
            for e in (routing.get("exposures") or []) if e.get("material")]
    prof = _fund.business_description(snap.get("symbol") or "") or {}
    if prof.get("extraction_confidence"):
        row["business_text_confidence"] = prof.get("extraction_confidence")
        row["business_text_method"] = (prof.get("extraction") or {}).get("method")
        row["business_text_accession"] = (prof.get("extraction") or {}).get("accession")
    hyb = snap.get("hybrid") or {}
    if hyb:
        row["hybrid_case"] = hyb.get("case")
        row["hybrid_reliable"] = hyb.get("reliable")
        row["hybrid_disagreement_pct"] = hyb.get("disagreement_pct")
    cross = snap.get("cross_check") or {}
    if cross.get("checks"):
        row["reconstruction_state"] = cross.get("state")
        row["reconstruction_checks"] = [
            {"measure": c.get("measure"), "published": c.get("published"),
             "reconstructed": c.get("reconstructed"),
             "difference_pct": c.get("difference_pct"), "state": c.get("state")}
            for c in cross["checks"]]
    ca = snap.get("client_assets") or {}
    if ca.get("available"):
        row["client_assets"] = (ca.get("assets") or {}).get("value")
        row["client_assets_as_of"] = (ca.get("assets") or {}).get("as_of")
        row["client_assets_growth_pct"] = (
            ca.get("assets_growth_pct") or {}).get("value")
        row["net_new_assets"] = (ca.get("net_new") or {}).get("value")
        row["client_asset_provenance"] = (
            (ca.get("assets") or {}).get("provenance") or {})

    # Phase 7. What kind of insurer this was taken to be, how that was
    # reached, and whether its underwriting ratios had an honest basis —
    # recorded on the day, because a later reading of the same filing could
    # differ and the recommendation was made on this one.
    ins = snap.get("insurance") or {}
    iclass = ins.get("classification") or {}
    if iclass.get("primary"):
        row["insurer_subtype"] = iclass.get("primary")
        row["insurer_subtype_method"] = iclass.get("method")
        row["insurer_subtype_confidence"] = iclass.get("confidence")
        row["insurer_secondary_exposure"] = list(iclass.get("secondary") or [])
        row["insurer_segment_scores"] = iclass.get("segment_scores") or {}
    compat = ins.get("metric_basis_compatibility") or {}
    if compat:
        row["insurer_metric_basis_ok"] = compat.get("ok")
        if not compat.get("ok"):
            row["insurer_metric_basis_reason"] = compat.get("reason")
    mix = ins.get("business_mix") or {}
    if mix.get("policyholder_liabilities_pct") is not None:
        row["insurer_spread_share_pct"] = mix["policyholder_liabilities_pct"]

    # How every filing-table figure in this snapshot was read: the unit, and
    # which statement in the filing settled it.
    units_used = {}
    for name, r in ((snap.get("filing_tables") or {}).get("readings")
                    or {}).items():
        prov = r.get("provenance") or {}
        units_used[name] = {"unit": prov.get("resolved_unit"),
                            "unit_source": prov.get("unit_source"),
                            "unit_confidence": prov.get("unit_confidence"),
                            "period": prov.get("period"),
                            "scope": prov.get("scope")}
    if units_used:
        row["table_units"] = units_used

    # The exact contract and the exact quote behind the preferred structure,
    # so a future scoring pass can settle up against what was actually
    # recommended rather than against a better contract chosen after the
    # fact. Recorded prospectively for exactly that reason.
    row.update(_recommended_contract(snap))
    bench = snap.get("benchmark") or {}
    row["benchmark_symbol"] = bench.get("symbol")
    row["benchmark_close"] = bench.get("close")
    row["benchmark_as_of"] = bench.get("as_of")

    row["config_hash"] = snap.get("config_hash")
    return row


# The structure kinds whose recommended contract is recorded in full. A
# BUY SHARES verdict has no contract, and WAIT and AVOID have nothing to
# settle up.
def _recommended_contract(snap: dict) -> dict:
    """The exact option that was recommended, and what it was quoted at.

    Forward validation has to score the contract the app named on the day it
    named it. Storing the strike and expiration alone is not enough: without
    the quote there is no entry price to measure a return from, and picking
    one up later from a chain that has since moved is the same lookahead the
    whole validation engine exists to prevent. So the quote goes in the row
    beside the contract, prospectively, and is never revised.
    """
    out: dict = {}
    structs = snap.get("structures") or {}
    comp = structs.get("comparison") or {}
    preferred = comp.get("preferred")
    entry = snap.get("entry") or {}
    out["recommended_structure"] = entry.get("verdict") or preferred
    row = next((r for r in (comp.get("rows") or [])
                if r.get("kind") == preferred), None)
    contract = (row or {}).get("contract") or {}
    if not contract:
        # A recommendation with no contract is an honest state — BUY SHARES,
        # WAIT, AVOID — and is recorded as such rather than left ambiguous.
        out["recommended_contract"] = None
        out["recommended_contract_reason"] = (
            "This recommendation names no option contract."
            if preferred in (None, "", "BUY SHARES") else
            "No contract was attached to the preferred structure.")
        return out
    liq = row.get("liquidity") or {}
    out["recommended_contract"] = {
        "structure": preferred,
        "expiration": row.get("expiration") or contract.get("expiration"),
        "dte": row.get("dte") or contract.get("dte"),
        "strike": contract.get("strike"),
        "call_strike": contract.get("call_strike"),
        "long_strike": contract.get("long_strike"),
        "short_strike": contract.get("short_strike"),
        "credit": contract.get("credit") or contract.get("call_credit"),
        "debit": contract.get("debit"),
        "bid": liq.get("bid"), "ask": liq.get("ask"), "mid": liq.get("mid"),
        "spread_pct": liq.get("spread_pct"),
        "open_interest": liq.get("open_interest"),
        "volume": liq.get("volume"),
        "delta": contract.get("delta"), "iv": contract.get("iv"),
        "greek_source": contract.get("greek_source"),
        "quote_source": (structs.get("chain_source") or "unknown"),
        "quote_as_of": snap.get("as_of"),
        "underlying_price": snap.get("price"),
    }
    out["recommended_contract_reason"] = ""
    return out


# ── providers ───────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Now, on the exchange's clock.

    This stamp becomes the DATE OF A STORED RECOMMENDATION, so it decides
    which trading day a snapshot belongs to. Read from the container's clock
    it was wrong every evening: this app runs on a UTC container, and at
    eight in the evening in New York the container's date is already
    tomorrow. Every snapshot, chain and long-dated observation taken after
    the close was filed under the NEXT trading day — a day that had not
    happened yet — and the capture log, which already used the exchange
    clock, called the real day complete while its data sat under the wrong
    date.
    """
    return market_now().isoformat(timespec="seconds")


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

# Phase 4. A bank's own history of price to tangible book and a property
# trust's own history of price to funds from operations run through the SAME
# point-in-time engine as everything above: each day's price against only
# the figures that had been filed by that day. Adding measures rather than
# building a second history is the point — one engine, one regime detector,
# one distribution, one definition of what "point in time" means.
BANK_MEASURES = ("price_to_tangible_book", "price_to_book")
REIT_MEASURES = ("price_to_ffo", "dividend_yield_pct")
# Phase 5. An insurer and a broker are both read against book value, so both
# reuse the SAME point-in-time per-share series the bank model builds. One
# engine, one definition of what book value per share was on a given day.
INSURANCE_MEASURES = ("price_to_book", "price_to_tangible_book")
BROKER_MEASURES = ("price_to_book", "price_to_tangible_book")

MEASURE_LABEL = {
    "earnings_yield_pct": "Trailing earnings yield",
    "fcf_yield_pct": "Free cash flow yield",
    "trailing_pe": "Price to earnings, trailing",
    "price_to_tangible_book": "Price to tangible book value",
    "price_to_book": "Price to book value",
    "price_to_ffo": "Price to funds from operations",
    "dividend_yield_pct": "Distribution yield",
}
# For a yield, higher is cheaper. For a multiple, lower is cheaper.
MEASURE_CHEAP_HIGH = {"earnings_yield_pct": True, "fcf_yield_pct": True,
                      "trailing_pe": False, "price_to_tangible_book": False,
                      "price_to_book": False, "price_to_ffo": False,
                      "dividend_yield_pct": True}


def _extra_measures(btype: str | None) -> tuple:
    if btype == "BANK":
        return BANK_MEASURES
    if btype == "REIT":
        return REIT_MEASURES
    if btype == "INSURANCE":
        return INSURANCE_MEASURES
    if btype == "BROKER":
        return BROKER_MEASURES
    return ()


def valuation_history(symbol: str, years: int = 5, raw: bool = False,
                      business_type: str | None = None) -> dict:
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

    extra = _extra_measures(business_type)
    per_share_pts = {}
    if business_type in ("BANK", "INSURANCE", "BROKER"):
        # One point-in-time book-value engine for all three. A bank, an
        # insurer and a broker are read against the same per-share figures,
        # so building a second copy of the same series would only create a
        # way for two screens to disagree.
        per_share_pts = {k: _pit_lookup(v) for k, v in
                         (_bank.point_in_time_series(_fund, facts) or {}).items()}
    elif business_type == "REIT":
        per_share_pts = {k: _pit_lookup(v) for k, v in
                         (_reit.point_in_time_series(_fund, facts) or {}).items()}
        per_share_pts["dividends_per_share"] = _pit_lookup(
            _fund.ttm_series(facts, "dividends_per_share"))

    series = {m: [] for m in VALUATION_MEASURES + extra}
    for day, price in closes:
        for measure, key, invert in (
                ("price_to_tangible_book", "tangible_book_per_share", False),
                ("price_to_book", "book_per_share", False),
                ("price_to_ffo", "ffo_per_share", False),
                ("dividend_yield_pct", "dividends_per_share", True)):
            if measure not in extra:
                continue
            pt = _as_of(per_share_pts.get(key) or [], day)
            v = None if pt is None else _f(pt.get("value"))
            if v is None or v <= 0:
                continue
            series[measure].append(
                {"date": day,
                 "value": (v / price * 100.0) if invert else (price / v),
                 "period_end": pt.get("period_end")})
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
    if raw:
        # The full per-day arrays, for the Phase 3 fair value engine. Kept
        # OUT of the response by default: three measures × two windows is
        # about seven thousand floats, and the browser needs the percentiles
        # rather than the observations they were taken from.
        out["raw_values"] = {}
        for measure, pts in series.items():
            block = {"all": [p["value"] for p in pts]}
            if pts:
                for win in (3, 5):
                    cut = (date.fromisoformat(pts[-1]["date"])
                           - timedelta(days=int(win * 365.25))).isoformat()
                    block[f"{win}y"] = [p["value"] for p in pts if p["date"] >= cut]
            out["raw_values"][measure] = block
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

def _prior_reading(symbol: str, keys: tuple) -> dict:
    """What this app itself recorded for these fields about a year ago.

    Nothing is recomputed and nothing is rewritten: this reads the stored
    daily rows forward, exactly as the forward-validation engine does. A
    ticker with no year-old row simply has no year-earlier reading, and the
    signals that need one report themselves as not measurable rather than
    quietly comparing today against today.
    """
    rows = load_history(symbol)
    if not rows:
        return {}
    target = (date.today() - timedelta(days=365)).isoformat()
    best = None
    for r in rows:
        day = str(r.get("date") or "")[:10]
        if not day or day > target:
            continue
        if best is None or day > str(best.get("date") or "")[:10]:
            best = r
    if best is None:
        return {}
    out = {k: best.get(k) for k in keys if best.get(k) is not None}
    if out:
        out["_as_of"] = best.get("date")
    return out


def value_trap_block(symbol: str, facts: dict, snap: dict, quality: dict,
                     valuation: dict, revisions: dict, cfg: dict,
                     insurance: dict | None = None,
                     broker: dict | None = None) -> dict:
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

    # Business-specific deterioration, graded by the SAME engine rather than
    # in a score of its own. An insurer whose old reserves are proving
    # inadequate can therefore reach HIGH RISK by the ordinary route — and
    # HIGH RISK is what stops the entry engine recommending anything bullish,
    # which is exactly what a cheap insurer with deteriorating reserves needs.
    extra: dict = {}
    if (insurance or {}).get("available"):
        prior = _prior_reading(symbol, ("insurance_roe_pct",))
        signals.update(_ins.risk_signals(
            insurance, prior_roe=prior.get("insurance_roe_pct"), cfg=cfg))
        extra.update(_ins.RISK_SIGNALS)
    if (broker or {}).get("available"):
        prior = _prior_reading(symbol, ("broker_roe_pct",
                                        "broker_assets_to_equity",
                                        "broker_compensation_ratio_pct"))
        signals.update(_brk.risk_signals(broker, prior={
            "return_on_equity_pct": prior.get("broker_roe_pct"),
            "assets_to_equity": prior.get("broker_assets_to_equity"),
            "compensation_ratio_pct":
                prior.get("broker_compensation_ratio_pct")}, cfg=cfg))
        extra.update(_brk.RISK_SIGNALS)

    return engine.value_trap(signals, cfg, extra_labels=extra or None)


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


# ══════════════════════════════════════════════════════════════════════════
# PHASE 3 — what it is worth, what today's price implies, and how to own it
# ══════════════════════════════════════════════════════════════════════════


def _window_values(vhist: dict, measure: str, window: str = "5y") -> list:
    """The full per-day array for one measure, five years falling back to
    three. Percentiles need the observations, not a summary of them."""
    raw = ((vhist or {}).get("raw_values") or {}).get(measure) or {}
    for win in (window, "5y", "3y", "all"):
        vals = raw.get(win)
        if vals and len(vals) >= 60:
            return vals
    return []


MIN_GROWTH_WINDOWS = 6


def eps_growth_history(facts: dict, horizon_years: float = 3.0,
                       tolerance_days: int = 45) -> dict:
    """Every compound growth rate this company has actually posted over a
    window the same length as the one being projected.

    Two things here were learned the hard way.

    MEASURE OVER THE HORIZON BEING PROJECTED. Percentiles of ONE-year growth
    are not percentiles of three-year growth: Apple's 75th-percentile single
    year is +39%, its 75th-percentile three-year run is +13.5%, and
    compounding the first of those over three years produces a bull case of
    seven hundred dollars a share. Measuring compound growth over the same
    length of window as the projection removes that error at the source
    instead of clamping it afterwards.

    ONLY WHERE THE SHARE BASIS IS ONE BASIS. Per-share figures from before an
    unrestated stock split are on a different share, and dividing one by the
    other measures the split. The window starts after the last such break.

    Falls back to one-year rates, labelled, when the clean window is too short
    to hold enough compound windows.
    """
    out = {"values": [], "horizon_matched": False, "horizon_years": horizon_years,
           "n": 0, "basis": {}, "reason": ""}
    if _fund is None:
        out["reason"] = "The fundamentals reader is not available."
        return out
    basis = _fund.consistent_basis_from(facts)
    out["basis"] = basis
    return _growth_from_series(_fund.ttm_series(facts, "eps"), basis, out,
                               horizon_years, tolerance_days,
                               "trailing earnings points")


def ffo_growth_history(facts: dict, horizon_years: float = 3.0,
                       tolerance_days: int = 45) -> dict:
    """The same measurement on reconstructed funds from operations per share.

    Identical machinery, a different per-share figure — because for a
    property trust it is funds from operations rather than earnings that the
    price is a multiple of, and percentiles of one-year growth are no more
    the percentiles of three-year growth here than they were for earnings.
    """
    out = {"values": [], "horizon_matched": False,
           "horizon_years": horizon_years, "n": 0, "basis": {}, "reason": ""}
    if _fund is None:
        out["reason"] = "The fundamentals reader is not available."
        return out
    basis = _fund.consistent_basis_from(facts)
    out["basis"] = basis
    series = (_reit.point_in_time_series(_fund, facts) or {}).get(
        "ffo_per_share") or []
    return _growth_from_series(series, basis, out, horizon_years,
                               tolerance_days,
                               "reconstructed funds-from-operations points")


def _growth_from_series(rows, basis, out, horizon_years, tolerance_days,
                        what: str) -> dict:
    ends = []
    for r in rows or []:
        try:
            when = date.fromisoformat(r["period_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if basis.get("from") and r["period_end"] < basis["from"]:
            continue
        ends.append((when, r["value"]))
    if len(ends) < 2:
        out["reason"] = (f"Only {len(ends)} {what} sit inside the window "
                         f"where this company's per-share figures share one "
                         f"share basis. " + (basis.get("reason") or ""))
        return out

    def windows(span_years: float) -> list:
        got = []
        for when, value in ends:
            target = when - timedelta(days=int(365.25 * span_years))
            best, best_gap = None, None
            for other, prior in ends:
                gap = abs((other - target).days)
                if gap <= tolerance_days and (best_gap is None or gap < best_gap):
                    best, best_gap = prior, gap
            if best is None or best <= 0 or value is None or value <= 0:
                continue
            got.append(((value / best) ** (1.0 / span_years) - 1.0) * 100.0)
        return got

    compound = windows(float(horizon_years))
    if len(compound) >= MIN_GROWTH_WINDOWS:
        out.update({"values": compound, "n": len(compound),
                    "horizon_matched": True,
                    "note": (f"{len(compound)} overlapping {horizon_years:.0f}-year "
                             f"compound growth rates from this company's own "
                             f"reported history.")})
        return out
    single = windows(1.0)
    out.update({"values": single, "n": len(single), "horizon_matched": False,
                "note": (f"Only {len(compound)} overlapping "
                         f"{horizon_years:.0f}-year windows fit inside the "
                         f"comparable history, so these are {len(single)} "
                         f"ONE-year growth rates instead. A one-year rate "
                         f"compounded over {horizon_years:.0f} years spreads "
                         f"wider than the company's actual multi-year record, "
                         f"which is why the clamp matters more here.")})
    if len(single) < 4:
        out["reason"] = (f"Only {len(single)} usable growth readings inside "
                         f"the comparable window. "
                         + (basis.get("reason") or ""))
    return out


def fcf_history(facts: dict) -> list:
    return [r["value"] for r in _fcf_ttm_series(facts)] if _fund else []


def _dividends(facts: dict) -> dict:
    """Trailing dividends per share, or the reason there are none."""
    if _fund is None:
        return {"value": None, "reason": "The fundamentals reader is not "
                                         "available."}
    m = _fund.metric(facts, "dividends_per_share")
    return {"value": m.get("value"), "basis": m.get("basis"),
            "concept": m.get("concept"), "period_end": m.get("period_end"),
            "filed": m.get("filed"),
            "reason": m.get("reason") or ""}


def bank_block(snap: dict, facts: dict, cfg: dict) -> dict:
    """The bank measures, for a filer the business-type gate called a bank."""
    return _bank.metrics(_fund, facts, price=snap.get("price"),
                         shares_outstanding=snap.get("shares_outstanding"),
                         cfg=cfg)


def reit_block(snap: dict, facts: dict, cfg: dict) -> dict:
    """The property-trust measures, including the property type read from
    the trust's own annual report."""
    profile = _fund.business_description(snap.get("symbol") or "") or {}
    return _reit.metrics(_fund, facts, price=snap.get("price"),
                         shares_outstanding=snap.get("shares_outstanding"),
                         property_type=profile.get("property_type"), cfg=cfg)


def insurance_block(snap: dict, facts: dict, cfg: dict) -> dict:
    """The insurer measures, for a filer the business-type gate called an
    insurer — once its own annual report has said what kind."""
    profile = _fund.business_description(snap.get("symbol") or "") or {}
    iclass = profile.get("insurer_classification") or {}
    got = _ins.metrics(_fund, facts, price=snap.get("price"),
                       shares_outstanding=snap.get("shares_outstanding"),
                       subtype=profile.get("insurer_subtype"), cfg=cfg,
                       secondary=iclass.get("secondary") or [])
    # How the subtype was reached, so a reader can see whether it came from
    # the report's own words or from the segments it is organised into.
    got["classification"] = iclass
    return got


def broker_block(snap: dict, facts: dict, cfg: dict) -> dict:
    """The broker measures — after the balance sheet has been asked whether
    this filer is a broker-dealer at all."""
    profile = _fund.business_description(snap.get("symbol") or "") or {}
    return _brk.metrics(_fund, facts, price=snap.get("price"),
                        shares_outstanding=snap.get("shares_outstanding"),
                        subtype=profile.get("broker_subtype"), cfg=cfg)


# ── Phase 6: which engine, and why ──────────────────────────────────────────

def routing_block(sym: str, facts: dict, sic, eps_ttm, ok: bool,
                  cfg: dict) -> dict:
    """The economic class of the business, from filed evidence.

    The SEC industry code is the starting point and not the answer. Code
    6211 holds Charles Schwab, Goldman Sachs and BlackRock; whether a filer
    is a broker, an exchange or an asset manager is settled by what is on
    its balance sheet, what its revenue is made of, and whether its accounts
    behave like an operating company's at all.
    """
    profile = _fund.business_description(sym or "") or {}
    return _route.route(
        _fund, facts, sic=sic,
        phrases=profile.get("routing_phrases") or {},
        text_confidence=profile.get("extraction_confidence"),
        eps_ttm=eps_ttm, ok=ok, cfg=cfg)


def _routed_business_type(btype: dict, routing: dict) -> dict:
    """The business-type dict every later stage reads, corrected by routing.

    The shape is unchanged from Phase 2 on purpose — `type`, `label`, `sic`,
    `allows`, `note` — because the scorecard, the valuation history, the
    verdict and the fair value all read it. What changes is the answer: an
    exchange and an asset manager are STANDARD businesses whatever their
    industry code says, and a filer whose code says insurer while its own
    annual report describes a railway and a utility group is neither one
    thing nor the other.
    """
    kind = (routing or {}).get("business_class")
    if not kind or kind == _route.UNSUPPORTED:
        return btype if kind != _route.UNSUPPORTED else engine._btype("UNSUPPORTED")
    # A loss-maker stays a loss-maker: there is no denominator whatever the
    # balance sheet looks like.
    if (btype or {}).get("type") == "UNPROFITABLE":
        return btype
    model = (routing or {}).get("model") or "STANDARD"
    out = engine._btype(model if model in engine._TYPE_ALLOWS else "STANDARD",
                        (routing or {}).get("sic"))
    out["label"] = (routing or {}).get("label") or out["label"]
    out["business_class"] = kind
    if (routing or {}).get("note"):
        out["note"] = routing["note"]
    return out


# Which table-derived measures belong to which kind of business. Nothing is
# fetched for a company that has no use for it.
TABLE_WANTS = {
    "BROKER": ("client_assets", "assets_under_administration",
               "advisory_assets", "net_new_assets"),
    "ASSET_MANAGER": ("assets_under_management", "net_flows"),
    "REIT": ("published_ffo",),
    "INSURANCE": ("published_combined_ratio", "published_loss_ratio",
                  "published_expense_ratio"),
}
# An ordinary company has none of these measures, and asking for them would
# mean fetching a dozen documents per filer to find nothing. A hybrid takes
# the wants of whichever business its model came from.


def table_block(sym: str, business_class: str) -> dict:
    """Operating measures read out of the company's own filing tables.

    Company Facts does not carry client assets or assets under management,
    because neither is a line of a financial statement. They are read from
    the tables of the filings themselves, under the rules in
    filing_tables.py, and refused whenever the label, the period or the unit
    is not certain.
    """
    if _TABLES_FN is None or not sym:
        return {"available": False, "readings": {},
                "reason": "Filing tables are not being read in this "
                          "environment."}
    wanted = TABLE_WANTS.get(business_class)
    if not wanted:
        return {"available": False, "readings": {},
                "reason": "No filing-table measure applies to this kind of "
                          "business."}
    try:
        got = _TABLES_FN(sym, wanted) or {}
    except Exception as exc:                         # pragma: no cover
        return {"available": False, "readings": {},
                "reason": f"The filing tables could not be read: {exc}"}
    readings = {k: v for k, v in (got.get("readings") or {}).items()
                if v.get("usable")}
    return {"available": bool(readings), "readings": readings,
            "history": got.get("history") or {},
            "filings_read": got.get("filings_read") or 0,
            "version": got.get("version") or _tables.TABLES_VERSION,
            "reason": "" if readings else
            ("None of this company's recent filings prints a table this app "
             "can read these measures out of. Nothing is guessed from prose.")}


def _reading(tables: dict, *names):
    for n in names:
        got = ((tables or {}).get("readings") or {}).get(n)
        if got and got.get("value") is not None:
            return got
    return None


def _fresh_reading(tables: dict, *names):
    """A reading that still describes the business today.

    Brokers report client assets every quarter and several report monthly, so
    a figure two quarters old is history. T. Rowe Price's newest readable
    assets-under-management row is from December and is left out rather than
    shown as though it were current.
    """
    got = _reading(tables, *names)
    if got is None:
        return None
    per = (got.get("provenance") or {}).get("period")
    return got if _fresh_enough(per) else None


def _yoy(tables: dict, name: str, current: dict):
    """The same measure four quarters back, when the filings hold it."""
    hist = ((tables or {}).get("history") or {}).get(name) or []
    cur_period = (current.get("provenance") or {}).get("period")
    if not cur_period or len(hist) < 2:
        return None
    want = f"{int(cur_period[:4]) - 1}{cur_period[4:]}"
    for row in hist:
        if ((row.get("provenance") or {}).get("period") or "") == want:
            return row
    return None


def client_asset_block(tables: dict, cfg: dict) -> dict:
    """How much money customers keep here, and whether it is growing.

    Phase 5 refused these outright, because Company Facts does not carry
    them. They are context for quality and growth, never a score: a broker
    with more client money is not automatically worth more.
    """
    out = {"available": False, "reason": (tables or {}).get("reason") or "",
           "assets": None, "assets_growth_pct": None,
           "net_new": None, "net_new_rate_pct": None, "basis": ""}
    assets = _fresh_reading(tables, "client_assets",
                            "assets_under_administration",
                            "assets_under_management", "advisory_assets")
    flows = _fresh_reading(tables, "net_new_assets", "net_flows")
    if assets is None and flows is None:
        return out
    out["available"] = True
    out["reason"] = ""
    if assets is not None:
        prov = assets.get("provenance") or {}
        out["assets"] = {
            "value": assets["value"], "label": assets["label"],
            "as_of": prov.get("period"), "reason": "",
            "basis": (f"{assets['label']} as the company printed it, in the "
                      f"{assets.get('form')} filed "
                      f"{_pretty(assets.get('filed') or '')}, from a row "
                      f"labelled \"{prov.get('row_label')}\" in figures "
                      f"stated in {prov.get('scale_word') or 'units'}."),
            "provenance": prov,
        }
        year_ago = _yoy(tables, assets["metric"], assets)
        if year_ago and year_ago.get("value"):
            pct = (assets["value"] / year_ago["value"] - 1.0) * 100.0
            out["assets_growth_pct"] = {
                "value": pct, "reason": "",
                "basis": (f"Against {year_ago['value']:,.0f} a year earlier, "
                          f"read from the filing for "
                          f"{(year_ago.get('provenance') or {}).get('period')}.")}
        else:
            out["assets_growth_pct"] = {
                "value": None,
                "reason": ("There is no reading of the same measure a year "
                           "earlier to compare against yet. Filings are read "
                           "forward from today and never back-filled.")}
    if flows is not None:
        prov = flows.get("provenance") or {}
        out["net_new"] = {
            "value": flows["value"], "label": flows["label"],
            "as_of": prov.get("period"), "reason": "",
            "basis": (f"{flows['label']} for the period the filing reports, "
                      f"from a row labelled \"{prov.get('row_label')}\"."),
            "provenance": prov,
        }
        if assets is not None and assets.get("value"):
            # Against the START of the period, which is what "new" means.
            start = assets["value"] - flows["value"]
            if start > 0:
                out["net_new_rate_pct"] = {
                    "value": flows["value"] / start * 100.0, "reason": "",
                    "basis": ("Net new money as a share of what clients held "
                              "at the start of the period — the closing "
                              "balance less the money that came in.")}
    return out


# How old a client-asset reading may be and still describe the business
# today. Brokers report quarterly, and several report monthly, so anything
# older than two quarters is history rather than news.
CLIENT_ASSET_MAX_AGE_DAYS = 200


def _apply_client_assets(broker: dict, block: dict) -> None:
    """Fill the broker panel's customer franchise from the filing tables.

    Phase 5 left these blank with a paragraph explaining that the filings do
    not carry them, and capped a retail broker's fair-value confidence for
    exactly that reason. Where the tables now do carry them, the blanks are
    filled and the cap lifts on its own — the cap reads the same field.
    """
    if not broker or not (block or {}).get("available"):
        return
    assets = block.get("assets") or {}
    if assets.get("value") is not None and _fresh_enough(assets.get("as_of")):
        broker["client_assets"] = {"value": assets["value"], "reason": "",
                                   "basis": assets.get("basis", ""),
                                   "as_of": assets.get("as_of"),
                                   "provenance": assets.get("provenance")}
        growth = block.get("assets_growth_pct") or {}
        if growth.get("value") is not None:
            broker["client_asset_growth_pct"] = {
                "value": growth["value"], "reason": "",
                "basis": growth.get("basis", "")}
    flows = block.get("net_new") or {}
    if flows.get("value") is not None and _fresh_enough(flows.get("as_of")):
        broker["net_new_assets"] = {"value": flows["value"], "reason": "",
                                    "basis": flows.get("basis", ""),
                                    "as_of": flows.get("as_of"),
                                    "provenance": flows.get("provenance")}


def _fresh_enough(iso: str | None) -> bool:
    if not iso:
        return False
    try:
        age = (date.today() - date.fromisoformat(iso[:10])).days
    except ValueError:
        return False
    return 0 <= age <= CLIENT_ASSET_MAX_AGE_DAYS


# ── Phase 7: published against reconstructed ────────────────────────────────

# Which published readings are comparable with a trailing-twelve-month
# reconstruction at all: a figure out of an annual report, which covers the
# year the report is about. A quarter is not a year, and comparing the two
# reports a 300% disagreement about the units of time.
ANNUAL_FORM = "10-K"


def _annual_reading(tables: dict, metric: str):
    """The newest reading of this measure that came out of an annual report.

    The comparison it enables is worth the wait: an annual figure has a year
    behind it, the reconstruction can be rebuilt as of that same year end,
    and the two are then the same measure over the same twelve months.
    """
    for row in ((tables or {}).get("history") or {}).get(metric) or []:
        if row.get("form") == ANNUAL_FORM and row.get("value") is not None:
            return row
    return None


def cross_check_block(tables: dict, reit: dict | None,
                      insurance: dict | None, facts: dict | None = None,
                      snap: dict | None = None, cfg: dict | None = None,
                      profile: dict | None = None) -> dict:
    """The company's own published figures beside the ones rebuilt from XBRL.

    Every comparison is like for like or it is not made. Where a company
    publishes an annual figure, the reconstruction is rebuilt AS OF THAT
    YEAR END so the two cover the same twelve months; where it publishes
    only a quarter, the check reports that there is nothing comparable
    rather than comparing a quarter with a year.
    """
    cfg = cfg or {}
    checks = []
    tab = tables or {}

    # ── property trusts ────────────────────────────────────────────────
    pub = _annual_reading(tab, "published_ffo")
    if pub is not None and _reit is not None and facts:
        prov = pub.get("provenance") or {}
        at = prov.get("period")
        try:
            rebuilt = _reit.metrics(
                _fund, facts, price=(snap or {}).get("price"),
                shares_outstanding=(snap or {}).get("shares_outstanding"),
                property_type=(profile or {}).get("property_type"),
                as_of=at, cfg=cfg)
        except Exception:                                # pragma: no cover
            rebuilt = {}
        rec = (rebuilt.get("ffo") or {})
        checks.append(_xcheck.compare(
            "Funds from operations", pub["value"], rec.get("value"), "USD",
            published_basis=prov.get("basis") or _tables.FFO_COMMON,
            reconstructed_basis=_tables.FFO_COMMON,
            published_period=at, reconstructed_period=rec.get("period_end"),
            published_window="FULL YEAR", reconstructed_window="FULL YEAR",
            published_scope=prov.get("scope") or "",
            provenance=prov, cfg=cfg,
            note=(f"Published in the annual report filed "
                  f"{_pretty(pub.get('filed') or '')}, from a row labelled "
                  f"\"{prov.get('row_label')}\", against the same twelve "
                  f"months rebuilt from the income statement.")))
    elif (reit or {}).get("available"):
        checks.append(_xcheck.compare(
            "Funds from operations", None, (reit.get("ffo") or {}).get("value"),
            "USD", cfg=cfg))

    # ── insurers ───────────────────────────────────────────────────────
    for metric, key, label in (
            ("published_combined_ratio", "combined_ratio_pct",
             "Combined ratio"),
            ("published_loss_ratio", "loss_ratio_pct", "Loss ratio"),
            ("published_expense_ratio", "expense_ratio_pct",
             "Expense ratio")):
        pub = _annual_reading(tab, metric)
        if pub is None:
            if (insurance or {}).get("available"):
                checks.append(_xcheck.compare(
                    label, None, (insurance.get(key) or {}).get("value"),
                    "percent", cfg=cfg))
            continue
        prov = pub.get("provenance") or {}
        at = prov.get("period")
        try:
            rebuilt = _ins.metrics(
                _fund, facts, price=(snap or {}).get("price"),
                shares_outstanding=(snap or {}).get("shares_outstanding"),
                subtype=(profile or {}).get("insurer_subtype"), as_of=at,
                cfg=cfg,
                secondary=((profile or {}).get("insurer_classification")
                           or {}).get("secondary") or []) if facts else {}
        except Exception:                                # pragma: no cover
            rebuilt = {}
        rec = (rebuilt.get(key) or {})
        checks.append(_xcheck.compare(
            label, pub["value"], rec.get("value"), "percent",
            published_period=at, reconstructed_period=rec.get("period_end"),
            published_window="FULL YEAR", reconstructed_window="FULL YEAR",
            published_scope=prov.get("scope") or "",
            provenance=prov, cfg=cfg,
            note=(f"Published in the annual report filed "
                  f"{_pretty(pub.get('filed') or '')}, from a row labelled "
                  f"\"{prov.get('row_label')}\" in a table the company "
                  f"heads as {(prov.get('scope') or 'unstated').lower()}.")))

    out = _xcheck.report(checks, cfg)
    # Every asset and flow measure the filings supplied, each on its own
    # period, scope and unit — kept apart rather than reconciled.
    out["measures"] = _xcheck.audit_measures(tab.get("readings") or {}, cfg)
    return out


def _with_article(name: str) -> str:
    return ("an " if name[:1] in "aeiou" else "a ") + name


def hybrid_block(snap: dict, routing: dict, vhist: dict, peer_payload: dict,
                 facts: dict, cfg: dict, blocks: dict) -> dict:
    """What to do when a company is genuinely two financial businesses.

    Three answers, and no fourth:

      A. One of the businesses has a model that can actually run. That model
         is used and the others are disclosed.
      B. Two models both run and their base values agree inside the existing
         fair-value agreement band. The normal confidence machinery resolves
         it and both ranges are shown.
      C. Two models both run and disagree. The answer is that there is no
         single fair value, which is a WAIT — not an average of two numbers
         that describe different companies.

    Nothing here weights by segment. Segment revenue and segment income are
    not in SEC Company Facts, so a sum of the parts would be a guess.
    """
    out = {"is_hybrid": True, "case": "", "reason": "", "reliable": True,
           "primary": routing.get("primary"),
           "secondary": list(routing.get("secondary") or []),
           "valuations": [], "disagreement_pct": None,
           "version": _route.ROUTING_VERSION}

    def value_as(kind: str):
        model = _route.CLASS_MODEL.get(kind) or "STANDARD"
        forced = dict(snap)
        forced["business_type"] = {**(snap.get("business_type") or {}),
                                   "type": model}
        got = fair_value_block(forced, vhist, peer_payload, facts, cfg,
                               bank=blocks.get("bank"),
                               reit=blocks.get("reit"),
                               insurance=blocks.get("insurance"),
                               broker=blocks.get("broker"))
        return model, got

    tried = []
    for kind in [routing.get("primary")] + list(routing.get("secondary") or []):
        if not kind:
            continue
        model, got = value_as(kind)
        base = (got or {}).get("base")
        tried.append({"business": kind, "label": _route.CLASS_LABEL.get(kind, kind),
                      "model": model, "base": base,
                      "available": bool((got or {}).get("available")),
                      "reason": (got or {}).get("reason") or ""})
    out["valuations"] = tried

    usable = [t for t in tried if t["available"] and t["base"]]
    if len(usable) <= 1:
        out["case"] = "ONE MODEL RUNS"
        if usable:
            out["reason"] = (
                f"Of the {len(tried)} businesses this company is in, only the "
                f"{usable[0]['label'].lower()} model can be built from its "
                f"filings, so that is the one used. The others are disclosed "
                f"rather than valued.")
        else:
            out["reliable"] = False
            out["reason"] = ("None of the models for the businesses this "
                             "company is in can be built from its filings.")
        return out

    bases = [t["base"] for t in usable]
    spread = (max(bases) - min(bases)) / min(bases) * 100.0 if min(bases) > 0 else None
    out["disagreement_pct"] = spread
    # The same band the fair-value engine already calls agreement, so a
    # hybrid is not held to a stricter standard than a single business.
    band = float(fv.cfg_get(cfg, "spread_high_max")) * 100.0
    if spread is not None and spread <= band:
        out["case"] = "MODELS AGREE"
        out["reason"] = (
            f"Both businesses can be valued and their base values are "
            f"{spread:,.0f}% apart, inside the {band:,.0f}% band this app "
            f"calls agreement, so the usual confidence machinery resolves it.")
        return out
    out["case"] = "MODELS DISAGREE"
    out["reliable"] = False
    names = " and ".join(_with_article(t["label"].lower()) for t in usable)
    out["reason"] = (
        f"This company is {names} at once, and the two models disagree by "
        f"{spread:,.0f}% about what it is worth — "
        + "; ".join(f"{t['label'].lower()} ${t['base']:,.2f}" for t in usable)
        + ". There is no single fair value to act on, so none is shown.")
    return out


def _peer_shares(row: dict, facts: dict):
    """A peer's share count, from the peer row or from its own filings.

    The peer builder collects what the Phase 2 comparison needed and a share
    count was not part of it, so tangible book value per share came out
    unavailable for every comparable bank and the whole peer method reported
    zero usable multiples. Read it from the same filings the rest of the
    peer's figures come from rather than adding a field to a payload that
    other screens already depend on.
    """
    got = _f(row.get("shares_outstanding"))
    if got:
        return got
    if _fund is None or not facts:
        return None
    return (_fund.shares_outstanding(facts) or {}).get("value")


def _bank_peer_inputs(symbol: str, peer_payload: dict, cfg: dict) -> dict:
    """Price to tangible book and profitability for each comparable bank.

    Each peer is measured by the same module on the same filings the subject
    is, so the comparison is like for like rather than the subject's careful
    figure against whatever a data vendor called book value.
    """
    rows = []
    for r in (peer_payload or {}).get("rows") or []:
        sym = r.get("symbol")
        if not sym or sym == symbol:
            continue
        facts = _fund.company_facts(sym) if _fund is not None else None
        if not facts:
            continue
        m = _bank.metrics(_fund, facts, price=r.get("price"),
                          shares_outstanding=_peer_shares(r, facts), cfg=cfg)
        rows.append({
            "symbol": sym,
            "price_to_tangible_book": (m.get("price_to_tangible_book") or {}).get("value"),
            "return_on_tangible_common_equity_pct":
                (m.get("return_on_tangible_common_equity_pct") or {}).get("value")})
    out = _bank.peer_inputs(rows)
    out["level"] = (peer_payload or {}).get("level")
    out["rows"] = rows
    return out


def _reit_peer_inputs(symbol: str, peer_payload: dict, property_type,
                      cfg: dict) -> dict:
    """Price to funds from operations for each comparable trust, narrowed to
    the same kind of property where enough of them exist."""
    rows = []
    for r in (peer_payload or {}).get("rows") or []:
        sym = r.get("symbol")
        if not sym or sym == symbol:
            continue
        facts = _fund.company_facts(sym) if _fund is not None else None
        if not facts:
            continue
        prof = _fund.business_description(sym) or {}
        m = _reit.metrics(_fund, facts, price=r.get("price"),
                          shares_outstanding=_peer_shares(r, facts),
                          property_type=prof.get("property_type"), cfg=cfg)
        rows.append({"symbol": sym,
                     "price_to_ffo": (m.get("price_to_ffo") or {}).get("value"),
                     "property_type": prof.get("property_type")})
    narrowed = _reit.property_type_peers(rows, property_type, cfg)
    mults = [r["price_to_ffo"] for r in narrowed["rows"]]
    return {"multiples": mults,
            "base_multiple": fv.quantile(mults, 0.5) if mults else None,
            "level": (peer_payload or {}).get("level"),
            "matched": narrowed["matched"], "n": narrowed["n"],
            "property_type": narrowed["property_type"],
            "reason": narrowed["reason"], "rows": narrowed["rows"]}


def _insurance_peer_inputs(symbol: str, peer_payload: dict, subtype,
                           cfg: dict) -> dict:
    """Price to book and profitability for each comparable insurer, narrowed
    to insurers writing the same kind of business where enough exist.

    Each peer is measured by the same module on the same filings the subject
    is, so a car insurer is never compared against a life insurer's book
    value computed by somebody else's definition.
    """
    rows = []
    for r in (peer_payload or {}).get("rows") or []:
        sym = r.get("symbol")
        if not sym or sym == symbol:
            continue
        facts = _fund.company_facts(sym) if _fund is not None else None
        if not facts:
            continue
        prof = _fund.business_description(sym) or {}
        sub = prof.get("insurer_subtype")
        if not sub:
            continue
        m = _ins.metrics(_fund, facts, price=r.get("price"),
                         shares_outstanding=_peer_shares(r, facts),
                         subtype=sub, cfg=cfg)
        rows.append({
            "symbol": sym, "subtype": sub,
            "price_to_book": (m.get("price_to_book") or {}).get("value"),
            "price_to_tangible_book":
                (m.get("price_to_tangible_book") or {}).get("value"),
            "return_on_equity_pct":
                (m.get("return_on_equity_pct") or {}).get("value")})
    narrowed = _ins.subtype_peers(rows, subtype, cfg)
    out = _ins.peer_inputs(narrowed["rows"])
    out.update({"level": (peer_payload or {}).get("level"),
                "matched": narrowed["matched"], "subtype": narrowed["subtype"],
                "reason": narrowed["reason"], "rows": narrowed["rows"]})
    return out


def _broker_peer_inputs(symbol: str, peer_payload: dict, subtype,
                        cfg: dict) -> dict:
    """The same for brokers — and every candidate has to clear the
    broker-dealer test before it counts as a comparable, so an asset manager
    sharing the industry code never lands in the group."""
    rows = []
    for r in (peer_payload or {}).get("rows") or []:
        sym = r.get("symbol")
        if not sym or sym == symbol:
            continue
        facts = _fund.company_facts(sym) if _fund is not None else None
        if not facts:
            continue
        prof = _fund.business_description(sym) or {}
        m = _brk.metrics(_fund, facts, price=r.get("price"),
                         shares_outstanding=_peer_shares(r, facts),
                         subtype=prof.get("broker_subtype"), cfg=cfg)
        if not (m.get("broker_evidence") or {}).get("is_broker"):
            continue
        rows.append({
            "symbol": sym, "subtype": m.get("subtype"),
            "price_to_book": (m.get("price_to_book") or {}).get("value"),
            "price_to_tangible_book":
                (m.get("price_to_tangible_book") or {}).get("value"),
            "return_on_equity_pct":
                (m.get("return_on_equity_pct") or {}).get("value")})
    narrowed = _brk.subtype_peers(rows, subtype, cfg)
    out = _brk.peer_inputs(narrowed["rows"])
    out.update({"level": (peer_payload or {}).get("level"),
                "matched": narrowed["matched"], "subtype": narrowed["subtype"],
                "reason": narrowed["reason"], "rows": narrowed["rows"]})
    return out


def fair_value_block(snap: dict, vhist: dict, peer_payload: dict, facts: dict,
                     cfg: dict, bank: dict | None = None,
                     reit: dict | None = None,
                     insurance: dict | None = None,
                     broker: dict | None = None) -> dict:
    """Bear / Base / Bull with the methods laid out beside each other.

    Which methods depends on what the business is. A bank is valued against
    tangible book and its own profitability, a property trust against funds
    from operations, an insurer and a broker against book value and what
    they earn on it, and everything else against earnings and cash. A
    specialized filer whose own model refused — an insurer whose subtype
    could not be read, a filer in a broker's industry code that is not a
    broker — falls through to no valuation at all rather than to the generic
    one, because half a model is worse than an honest refusal.
    """
    btype = (snap.get("business_type") or {}).get("type")
    if btype == "BANK" and bank is not None:
        methods = _bank.methods(
            {**bank, "eps_ttm": snap.get("eps_ttm")}, vhist,
            _bank_peer_inputs(snap.get("symbol") or "", peer_payload, cfg),
            ten_year_pct=snap.get("treasury_10y_pct"), cfg=cfg)
        out = fv.fair_value(methods, price=snap.get("price"), cfg=cfg,
                            business_type=snap.get("business_type"))
        out["model"] = "BANK"
        return out
    if btype == "REIT" and reit is not None:
        cap, why = _reit.confidence_cap(reit)
        methods = _reit.methods(
            reit, vhist,
            _reit_peer_inputs(snap.get("symbol") or "", peer_payload,
                              reit.get("property_type"), cfg), cfg=cfg)
        out = fv.fair_value(methods, price=snap.get("price"), cfg=cfg,
                            business_type=snap.get("business_type"),
                            confidence_cap=cap, confidence_cap_reason=why)
        out["model"] = "REIT"
        return out
    if btype == "INSURANCE" and (insurance or {}).get("available"):
        cap, why = _ins.confidence_cap(insurance)
        methods = _ins.methods(
            {**insurance, "eps_ttm": snap.get("eps_ttm")}, vhist,
            _insurance_peer_inputs(snap.get("symbol") or "", peer_payload,
                                   insurance.get("subtype"), cfg),
            ten_year_pct=snap.get("treasury_10y_pct"), cfg=cfg)
        out = fv.fair_value(methods, price=snap.get("price"), cfg=cfg,
                            business_type=snap.get("business_type"),
                            confidence_cap=cap, confidence_cap_reason=why)
        out["model"] = "INSURANCE"
        out["subtype"] = insurance.get("subtype")
        return out
    if btype == "BROKER" and (broker or {}).get("available"):
        cap, why = _brk.confidence_cap(broker)
        methods = _brk.methods(
            {**broker, "eps_ttm": snap.get("eps_ttm")}, vhist,
            _broker_peer_inputs(snap.get("symbol") or "", peer_payload,
                                broker.get("subtype"), cfg),
            ten_year_pct=snap.get("treasury_10y_pct"), cfg=cfg)
        out = fv.fair_value(methods, price=snap.get("price"), cfg=cfg,
                            business_type=snap.get("business_type"),
                            confidence_cap=cap, confidence_cap_reason=why)
        out["model"] = "BROKER"
        out["subtype"] = broker.get("subtype")
        return out
    # A specialized filer whose own model could not run. The generic path
    # below would be refused anyway — `fair_value` will not accept
    # earnings-and-cash-flow methods for a bank — but it would explain the
    # refusal in general terms when this module knows the specific one.
    specialized = {"INSURANCE": insurance, "BROKER": broker,
                   "BANK": bank, "REIT": reit}.get(btype)
    if btype in fv.SPECIALIZED_TYPES and specialized is not None \
            and not specialized.get("available"):
        return {"available": False, "verdict": fv.SPECIALIZED,
                "confidence": {"level": "UNRELIABLE", "spread": None,
                               "reason": fv.SPECIALIZED},
                "methods": [], "model": None,
                "business_type": btype,
                "reason": specialized.get("reason") or
                ((snap.get("business_type") or {}).get("note") or "")}
    eps = snap.get("eps_ttm")
    ey = _window_values(vhist, "earnings_yield_pct")
    fy = _window_values(vhist, "fcf_yield_pct")
    regime_shifted = bool((vhist.get("regime") or {}).get("shifted"))
    window = snap.get("valuation_window") or "5-year"

    peer_rows = (peer_payload or {}).get("rows") or []
    peer_mults = [r.get("trailing_pe") for r in peer_rows
                  if r.get("trailing_pe") and r.get("symbol") != snap.get("symbol")]
    agg = ((peer_payload or {}).get("valuation") or {}).get("aggregate_pe")

    norm = fv.normalize_fcf(fcf_history(facts),
                            periods=int(cfg.get("fcf_normalize_periods",
                                                fv.DEFAULTS["fcf_normalize_periods"])))
    methods = [
        fv.method_self_history(eps, ey, cfg, regime_shifted=regime_shifted,
                               window_label=window),
        fv.method_peers_trailing(eps, peer_mults, aggregate_pe=agg,
                                 level=(peer_payload or {}).get("level"),
                                 cfg=cfg),
        fv.method_peers_forward(snap.get("eps_forward"), [],
                                level=(peer_payload or {}).get("level"),
                                cfg=cfg),
        fv.method_fcf(norm.get("value"), snap.get("shares_outstanding"), fy,
                      cfg=cfg),
    ]
    out = fv.fair_value(methods, price=snap.get("price"), cfg=cfg,
                        business_type=snap.get("business_type"))
    out["normalized_fcf"] = norm
    return out


def expected_return_block(snap: dict, vhist: dict, facts: dict,
                          peer_payload: dict, cfg: dict,
                          probabilities=None, reit: dict | None = None) -> dict:
    """The three-year scenario bridge from today's price to terminal wealth.

    For a property trust the SAME bridge runs on reconstructed funds from
    operations and the multiple of it, rather than on earnings and the price
    to earnings — because a trust's earnings are a depreciation schedule and
    its price is a multiple of what its buildings produce. The arithmetic is
    identical; only the per-share figure and the multiple that prices it
    change, and both change together so the bases never cross.
    """
    horizon = float(cfg.get("horizon_years", fv.DEFAULTS["horizon_years"]))
    is_reit = bool(reit and (reit.get("ffo_per_share") or {}).get("value"))
    if is_reit:
        hist = ffo_growth_history(facts, horizon_years=horizon)
        per_share = (reit["ffo_per_share"] or {}).get("value")
        multiple_measure, agg = "price_to_ffo", None
        basis_label = ("Reconstructed funds from operations per share, at "
                       "this trust's own multiple of it")
    else:
        hist = eps_growth_history(facts, horizon_years=horizon)
        per_share = snap.get("eps_ttm")
        multiple_measure = "trailing_pe"
        agg = ((peer_payload or {}).get("valuation") or {}).get("aggregate_pe")
        basis_label = ("GAAP trailing earnings per share, at this company's "
                       "own price to earnings")
    growth = fv.growth_scenarios(hist.get("values"), cfg)
    growth["horizon_matched"] = hist.get("horizon_matched")
    growth["history_note"] = hist.get("note") or ""
    growth["basis"] = hist.get("basis") or {}
    if not growth.get("available") and hist.get("reason"):
        growth["reason"] = hist["reason"]
    multiples = fv.multiple_scenarios(_window_values(vhist, multiple_measure),
                                      cfg, fallback=agg)
    years = horizon
    rate = _rate(years)
    div = _dividends(facts)
    out = fv.expected_return(
        snap.get("price"), per_share, growth, multiples, years=years,
        dps_ttm=div.get("value"), rate_pct=rate.get("pct"), cfg=cfg,
        probabilities=probabilities,
        reversion_years=float(cfg.get("multiple_reversion_years",
                                      fv.DEFAULTS["multiple_reversion_years"])))
    out["dividends_detail"] = div
    out["rate"] = rate
    out["per_share_basis"] = basis_label
    out["forward_growth_context"] = {
        "value": snap.get("forward_eps_growth_pct"),
        "basis": "Analyst consensus, adjusted (non-GAAP) earnings",
        "note": ("Shown beside the scenario growth rates as a cross-check and "
                 "deliberately NOT mixed into them: the scenarios compound a "
                 "GAAP trailing earnings figure, and switching bases halfway "
                 "through would put most of the answer in the switch."),
        "reason": snap.get("estimates_reason") or "",
    }
    return out


def implied_expectations_block(snap: dict, facts: dict, cfg: dict) -> dict:
    """What growth today's enterprise value is already paying for."""
    # Stated up front so EVERY return path carries it. "No consensus" is a
    # fact about the world, not a consequence of this particular filer's
    # balance sheet being unreadable, and it should read the same either way.
    out = {"available": False, "reason": "",
           "consensus_growth": {
               "available": False,
               "reason": ("No free source publishes a five-year "
                          "free-cash-flow consensus. This dashboard will not "
                          "print one it does not have.")}}
    # This audit discounts free cash flow against enterprise value. For a
    # bank, borrowing IS the raw material, so enterprise value is not a
    # price to pay for the business; for a property trust, the cash cycle
    # runs through buying and selling buildings rather than through
    # operations. Refused for both rather than run on quantities that do not
    # mean there what they mean elsewhere.
    btype = (snap.get("business_type") or {}).get("type")
    if btype in fv.SPECIALIZED_TYPES:
        out["reason"] = (
            (snap.get("business_type") or {}).get("note") or "") + \
            " This audit discounts free cash flow against enterprise value, " \
            "and neither quantity carries its usual meaning for this kind " \
            "of business, so no market-implied growth rate is solved for."
        return out
    norm = fv.normalize_fcf(fcf_history(facts),
                            periods=int(cfg.get("fcf_normalize_periods",
                                                fv.DEFAULTS["fcf_normalize_periods"])))
    out["normalized_fcf"] = norm
    nd = _fund.net_debt(facts) if _fund is not None else {"value": None}
    mcap = engine._num(snap.get("market_cap"))
    out["net_debt"] = nd
    if mcap is None:
        out["reason"] = "The market value of the company is not available."
        return out
    if nd.get("value") is None:
        out["reason"] = ("Net debt could not be built from this filer's "
                         "balance sheet, and enterprise value without it "
                         "would understate what a levered company costs to "
                         "buy outright. " + (nd.get("reason") or ""))
        return out
    ev = mcap + nd["value"]
    out["enterprise_value"] = ev
    if not norm.get("available"):
        out["reason"] = norm.get("reason")
        return out

    disc = fv.discount_rate(snap.get("treasury_10y_pct"), cfg=cfg)
    out["discount_rate"] = disc
    if not disc.get("available"):
        out["reason"] = disc.get("reason")
        return out
    years = int(cfg.get("reverse_dcf_years", fv.DEFAULTS["reverse_dcf_years"]))
    gt = float(cfg.get("terminal_growth_pct", fv.DEFAULTS["terminal_growth_pct"]))
    solved = fv.implied_growth(ev, norm["value"], years, disc["pct"], gt)
    grid = fv.implied_growth_grid(
        ev, norm["value"], years, disc["pct"],
        terminal_growths_pct=cfg.get("sensitivity_terminal_growths_pct"),
        rate_step_pct=float(cfg.get("sensitivity_rate_step_pct",
                                    fv.DEFAULTS["sensitivity_rate_step_pct"])))

    hist = fcf_history(facts)
    hist_cagr = None
    if len(hist) >= 5:
        span_years = max(1.0, (len(hist) - 1) / 4.0)
        hist_cagr = fv.cagr(hist[0], hist[-1], span_years)
    out.update({
        "available": bool(solved.get("available")),
        "implied": solved, "grid": grid,
        "terminal_growth_pct": gt, "years": years,
        "historical_fcf_growth_pct": hist_cagr,
        "historical_note": ("Compound annual growth of trailing free cash "
                            "flow across the reported history."
                            if hist_cagr is not None else
                            "Not enough free-cash-flow history to compute a "
                            "realized growth rate."),
        "consensus_growth": {
            "available": False,
            "reason": ("No free source publishes a five-year free-cash-flow "
                       "consensus. This dashboard will not print one it does "
                       "not have.")},
        "gap": fv.expectations_gap(solved.get("growth_pct"), hist_cagr),
        "reason": "" if solved.get("available") else solved.get("reason", ""),
        "note": ("This is an expectations audit, not a valuation. It solves "
                 "for ONE unknown — the growth the price is paying for — and "
                 "leaves the discount rate and the terminal growth rate as "
                 "stated assumptions varied across the grid."),
    })
    return out


def _rate(years) -> dict:
    if _RATE_FN is None:
        return {"pct": None, "reason": "No Treasury provider is wired."}
    try:
        r = _RATE_FN(years) or {}
    except Exception:                                # noqa: BLE001
        return {"pct": None, "reason": "The Treasury curve was unreachable."}
    return {"pct": r.get("pct"), "as_of": r.get("as_of"),
            "source": r.get("source"), "tenor": r.get("tenor"),
            "reason": "" if r.get("pct") is not None else
                      "No yield was returned for this horizon."}


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
        why = snap.get("unavailable_reason") or "No reported fundamentals."
        out["fair_value"] = {"available": False, "reason": why,
                             "confidence": {"level": "UNRELIABLE",
                                            "reason": why},
                             "confidence_level": "UNRELIABLE", "methods": []}
        out["expected_return"] = {"available": False, "reason": why}
        out["implied_expectations"] = {"available": False, "reason": why}
        out["dividends"] = {"value": None, "reason": why}
        out["structures"] = {"available": False, "reason": why,
                             "comparison": {"available": False},
                             "put": {"available": False}}
        out["scenario_probabilities"] = fv.scenario_probabilities(cfg)
        out["entry"] = {"verdict": "INSUFFICIENT DATA", "reasons": [why],
                        "what_would_change": [
                            "Reported fundamentals have to be readable before "
                            "anything can be valued or any structure priced."]}
        out["plan"] = {"available": False,
                       "reason": "No position is recommended."}
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

    # 2. What kind of business this is, BEFORE the valuation history — a
    #    bank's own history is of price to tangible book and a property
    #    trust's is of price to funds from operations, so the history has to
    #    know which measures to build.
    meta = _fund.sic_metadata(sym) or {}
    btype = engine.business_type(meta.get("sic"), out.get("eps_ttm"),
                                 ok=bool(snap.get("ok")))
    # Phase 6: the industry code starts the answer and the filings finish it.
    # An exchange, an asset manager and a broker all share code 6211, and
    # only the balance sheet and the revenue mix can tell them apart.
    routing = routing_block(sym, facts, meta.get("sic"), out.get("eps_ttm"),
                            bool(snap.get("ok")), cfg)
    out["routing"] = routing
    btype = _routed_business_type(btype, routing)
    out["business_type"] = btype
    out["sic"] = meta.get("sic")
    out["sic_description"] = meta.get("sic_description") or ""

    out["bank"] = (bank_block(out, facts, cfg)
                   if btype.get("type") == "BANK" else None)
    out["reit"] = (reit_block(out, facts, cfg)
                   if btype.get("type") == "REIT" else None)
    out["insurance"] = (insurance_block(out, facts, cfg)
                        if btype.get("type") == "INSURANCE" else None)
    out["broker"] = (broker_block(out, facts, cfg)
                     if btype.get("type") == "BROKER" else None)

    # What the company prints in its own filing tables and XBRL never
    # carries: client assets, assets under management, a published combined
    # ratio, a published funds-from-operations figure.
    tables = table_block(sym, routing.get("business_class")
                         if routing.get("business_class") != _route.HYBRID
                         else (routing.get("primary") or ""))
    out["filing_tables"] = tables
    out["client_assets"] = client_asset_block(tables, cfg)
    if out.get("broker") is not None:
        _apply_client_assets(out["broker"], out["client_assets"])

    # The benchmark this ticker will be scored against, recorded on the day
    # the recommendation is made. Choosing it later — after seeing which
    # index made the recommendation look best — would be exactly the kind of
    # after-the-fact selection the forward-validation engine refuses.
    out["benchmark"] = _benchmark_reference(sym)

    # 3. Valuation against its own history.
    vhist = valuation_history(sym, years=5, raw=True,
                              business_type=btype.get("type"))
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

    # 5. The four vectors.
    out["quality"] = quality_block(sym, facts, btype, peer_payload)
    out["growth"] = growth_block(out, out["drivers"], peer_payload)
    est = ((snap.get("provenance") or {}).get("estimates") or {}).get("value") or {}
    out["revisions"] = revisions_block(out, est, cfg)

    # 6. Is the cheapness real?
    out["value_trap"] = value_trap_block(sym, facts, out, out["quality"],
                                         out["valuation"], out["revisions"],
                                         cfg, insurance=out.get("insurance"),
                                         broker=out.get("broker"))

    # 7. Context.
    out["earnings_cycle"] = _cycle(sym, cfg)
    out["drawdowns"] = _drawdowns(sym, years=5)
    out["underreaction"] = _underreaction(sym, out, peer_payload)

    # 8. The price that would change the answer.
    out["target_yield_pct"] = _target_yield(vhist, cfg)

    out["verdict"] = engine.verdict(out, cfg)

    # ── Phase 3 ──
    # Order matters again: the fair value sets the buy zone, the buy zone
    # gates which put strikes may even be considered, and the entry verdict
    # reads both plus the Phase 2 state above it.
    probs = fv.scenario_probabilities(cfg)
    out["scenario_probabilities"] = probs
    out["cross_check"] = cross_check_block(
        tables, out.get("reit"), out.get("insurance"), facts=facts, snap=out,
        cfg=cfg, profile=_fund.business_description(sym) or {})
    out["fair_value"] = fair_value_block(out, vhist, peer_payload, facts, cfg,
                                         bank=out.get("bank"),
                                         reit=out.get("reit"),
                                         insurance=out.get("insurance"),
                                         broker=out.get("broker"))
    # A company that is two financial businesses at once gets no single fair
    # value unless the models agree about one.
    out["hybrid"] = None
    if routing.get("business_class") == _route.HYBRID:
        out["hybrid"] = hybrid_block(
            out, routing, vhist, peer_payload, facts, cfg,
            {"bank": out.get("bank"), "reit": out.get("reit"),
             "insurance": out.get("insurance"), "broker": out.get("broker")})
        if not out["hybrid"].get("reliable"):
            out["fair_value"] = {
                **out["fair_value"], "available": False,
                "verdict": "HYBRID — VALUATION UNRELIABLE",
                "confidence": {"level": "UNRELIABLE", "spread": None,
                               "reason": out["hybrid"]["reason"]},
                "reason": out["hybrid"]["reason"]}
    # A reconstruction the company's own published figure contradicts is not
    # trusted at full confidence, whatever the methods agree on.
    if (out.get("cross_check") or {}).get("state") == _xcheck.MATERIAL:
        worst = max((c["difference_pct"] for c in out["cross_check"]["checks"]
                     if c.get("difference_pct") is not None), default=0.0)
        note = (f"The figure rebuilt from this company's XBRL differs from the "
                f"one it publishes in its own filing tables by {worst:,.0f}%. "
                f"Until that is explained the valuation built on the rebuilt "
                f"figure is not treated as reliable.")
        out["fair_value"] = fv.cap_confidence(out["fair_value"], "LOW", note) \
            if hasattr(fv, "cap_confidence") else {
                **out["fair_value"],
                "confidence": {**(out["fair_value"].get("confidence") or {}),
                               "level": "LOW", "reason": note}}
    out["expected_return"] = expected_return_block(out, vhist, facts,
                                                   peer_payload, cfg, probs,
                                                   reit=out.get("reit"))
    out["implied_expectations"] = implied_expectations_block(out, facts, cfg)
    out["dividends"] = _dividends(facts)

    path = {
        "price": out.get("price"),
        # The scenario path compounds whatever per-share figure the multiple
        # prices. For a property trust that is funds from operations, for
        # everything else it is earnings — the same substitution the bridge
        # above makes, kept consistent so the two never disagree.
        "eps_ttm": ((out["reit"].get("ffo_per_share") or {}).get("value")
                    if out.get("reit") else out.get("eps_ttm")),
        "per_share_basis": (out["expected_return"] or {}).get("per_share_basis"),
        "growth": (out["expected_return"] or {}).get("growth"),
        "multiples": (out["expected_return"] or {}).get("multiples"),
        "dps_ttm": (out["dividends"] or {}).get("value"),
        "probabilities": probs,
    }
    out["scenario_path"] = path
    if _opts is not None:
        out["structures"] = _opts.build(sym, out, out["fair_value"], path, cfg,
                                        probabilities=probs, record=force)
        out["entry"] = (out["structures"] or {}).get("entry")
        out["plan"] = (out["structures"] or {}).get("plan")
    else:                                            # pragma: no cover
        out["structures"] = {"available": False,
                             "reason": "The options layer is not available."}
        out["entry"] = {"verdict": "INSUFFICIENT DATA",
                        "reasons": ["The options layer is not available."],
                        "what_would_change": []}
        out["plan"] = {"available": False, "reason": ""}

    # The raw per-day arrays were only needed by the fair value engine. They
    # are several thousand floats and the browser has the percentiles.
    vhist.pop("raw_values", None)

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


def _benchmark_reference(symbol: str) -> dict:
    """The sector benchmark for a ticker and its close today.

    Stored in the snapshot rather than looked up at scoring time. A
    benchmark-relative result is only honest if the benchmark was chosen
    before the outcome was known.
    """
    bench = _benchmark_symbol(symbol)
    out = {"symbol": bench, "close": None, "as_of": None, "reason": ""}
    if not bench:
        out["reason"] = ("No sector benchmark is mapped for this ticker, so "
                         "its result will be reported on its own rather than "
                         "against one.")
        return out
    if _DAILY_FN is None:
        out["reason"] = "No daily price history provider is wired."
        return out
    try:
        bars = (_DAILY_FN(bench, 10) or {}).get("bars") or []
    except Exception:                                # noqa: BLE001
        bars = []
    for b in reversed(bars):
        c = _f(b.get("close") if b.get("close") is not None else b.get("c"))
        if c and c > 0:
            out["close"] = c
            out["as_of"] = str(b.get("date") or b.get("d") or "")[:10]
            break
    if out["close"] is None:
        out["reason"] = f"No usable close is available for {bench}."
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


# ── the watchlist scanner ───────────────────────────────────────────────────
#
# There is deliberately NO summed investment score here. A column that added
# Quality to Growth to Valuation would let one strong reading carry a weak one
# and would be sortable, which is worse: it would become the column everybody
# sorts by. The independent readings stay independent and the table is sorted
# on whichever one the question is about.

_SCAN_FIELDS = ("price", "quality_label", "growth_label", "valuation_label",
                "revisions_label", "value_trap_level", "fair_value_base",
                "buy_zone", "premium_to_buy_zone_pct", "preferred_structure",
                "entry_verdict", "verdict", "fair_value_confidence",
                "business_type", "expected_cagr_weighted_pct")

_SCAN_BUILDING: set = set()


def _scan_row(sym: str, snap: dict) -> dict:
    fair = snap.get("fair_value") or {}
    comp = (snap.get("structures") or {}).get("comparison") or {}
    row = {"symbol": sym, "as_of": snap.get("as_of"),
           "name": snap.get("entity_name") or "",
           "price": snap.get("price"),
           "quality_label": (snap.get("quality") or {}).get("label"),
           "quality_score": (snap.get("quality") or {}).get("score"),
           "growth_label": (snap.get("growth") or {}).get("label"),
           "growth_score": (snap.get("growth") or {}).get("score"),
           "valuation_label": (snap.get("valuation") or {}).get("label"),
           "valuation_score": (snap.get("valuation") or {}).get("score"),
           "revisions_label": (snap.get("revisions") or {}).get("label"),
           "revisions_score": (snap.get("revisions") or {}).get("score"),
           "value_trap_level": (snap.get("value_trap") or {}).get("level"),
           "business_type": (snap.get("business_type") or {}).get("type"),
           "fair_value_base": fair.get("base"),
           "fair_value_confidence": fair.get("confidence_level"),
           "buy_zone": fair.get("buy_zone"),
           "premium_to_buy_zone_pct": fair.get("premium_to_buy_zone_pct"),
           "preferred_structure": comp.get("preferred"),
           "entry_verdict": (snap.get("entry") or {}).get("verdict"),
           "entry_reason": ((snap.get("entry") or {}).get("reasons") or [""])[0],
           "verdict": (snap.get("verdict") or {}).get("verdict"),
           "expected_cagr_weighted_pct":
               (snap.get("expected_return") or {}).get("weighted_total_cagr_pct"),
           # Which model produced the fair value, so a bank and a software
           # company sitting in the same list are never read as though the
           # same arithmetic was applied to both.
           "fair_value_model": fair.get("model") or "STANDARD",
           "status": "recorded"}
    # The one measure that means most for each kind of business, so a bank
    # and a property trust appear with a valuation figure of their own
    # rather than with a blank where a price-to-earnings would have gone.
    bank, reit = snap.get("bank") or {}, snap.get("reit") or {}
    ins, brk = snap.get("insurance") or {}, snap.get("broker") or {}
    row["headline_multiple"] = None
    row["headline_multiple_label"] = ""
    if ins.get("available"):
        row["headline_multiple"] = (ins.get("price_to_book") or {}).get("value")
        row["headline_multiple_label"] = "Price to book"
        row["insurance_subtype"] = ins.get("subtype")
        row["insurance_combined_ratio_pct"] = (
            ins.get("combined_ratio_pct") or {}).get("value")
        row["insurance_reserve_development_state"] = (
            ins.get("reserve_development_state") or {}).get("state")
    elif brk.get("available"):
        row["headline_multiple"] = (brk.get("price_to_book") or {}).get("value")
        row["headline_multiple_label"] = "Price to book"
        row["broker_subtype"] = brk.get("subtype")
        row["broker_roe_pct"] = (
            brk.get("return_on_equity_pct") or {}).get("value")
    elif bank.get("available"):
        row["headline_multiple"] = (bank.get("price_to_tangible_book") or {}).get("value")
        row["headline_multiple_label"] = "Price to tangible book"
        row["bank_rotce_pct"] = (
            bank.get("return_on_tangible_common_equity_pct") or {}).get("value")
    elif reit.get("available"):
        row["headline_multiple"] = (reit.get("price_to_ffo") or {}).get("value")
        row["headline_multiple_label"] = "Price to funds from operations"
        row["reit_property_type"] = reit.get("property_type")
    elif snap.get("trailing_pe"):
        row["headline_multiple"] = snap.get("trailing_pe")
        row["headline_multiple_label"] = "Price to earnings, trailing"
    routing = snap.get("routing") or {}
    row["business_class"] = routing.get("business_class")
    row["business_class_label"] = routing.get("label")
    hyb = snap.get("hybrid") or {}
    if hyb:
        row["hybrid_case"] = hyb.get("case")
        row["hybrid_reliable"] = hyb.get("reliable")
    cross = snap.get("cross_check") or {}
    if (cross.get("checks") or []):
        row["reconstruction_state"] = cross.get("state")
    return row


# ── Phase 5: how real the option data is ────────────────────────────────────

CAPTURE_DEFAULTS = {
    # The furthest expiration the covered-call capture asks for. The longest
    # tenor the simulator sells is thirty to forty-five days, and a roll
    # window on top of that reaches about fifty.
    "capture_max_dte": 50,
    # Strikes around the money to request. Wide enough for a delta target,
    # a five-percent-above-spot rule and a fair-value-aware strike that can
    # sit a quarter above the price; narrow enough that a daily capture of a
    # watchlist is a small request rather than a whole chain.
    "capture_strike_count": 50,
}


def chain_readiness(symbol: str, store: dict | None = None,
                    days=None) -> dict:
    """How much REAL option history exists for this ticker, and what that
    makes any run built on it.

    Three states and no fourth: a run is a REAL CHAIN BACKTEST, or PART REAL
    and part model, or a MODEL-BASED ESTIMATE. They are never averaged into
    a single "accuracy" figure, because a model fill and a real fill are
    different KINDS of number rather than the same number known to different
    precisions.
    """
    sym = (symbol or "").upper().strip()
    store = chain_store.load(sym) if store is None else store
    out = chain_store.readiness(store, days)
    out["symbol"] = sym
    cov = out.get("window_coverage_pct")
    if not out["days"]:
        out["mode"] = _cc.BASIS_MODEL
        out["mode_note"] = (
            "Every option price in this run came from the model, because no "
            "end-of-day chain has been captured for this ticker yet. The "
            "shape of the answer is right and the level depends on a "
            "volatility assumption.")
    elif cov is not None and cov >= 99.0:
        out["mode"] = _cc.BASIS_REAL
        out["mode_note"] = ("Every day this run walked through has a captured "
                            "chain behind it.")
    else:
        out["mode"] = _cc.BASIS_MIXED
        out["mode_note"] = (
            f"{out['days']} day{'s' if out['days'] != 1 else ''} of real "
            f"end-of-day chains have been captured for this ticker, covering "
            f"{cov:.0f}% of the days this run walks through. The rest are "
            f"model prices. Real fills and model fills are counted "
            f"separately below and are never blended into one number."
            if cov is not None else
            f"{out['days']} day{'s' if out['days'] != 1 else ''} of real "
            f"end-of-day chains have been captured for this ticker.")
    out["grows_only_forward"] = True
    out["backfill_note"] = (
        "There is no source of historical option chains this app can reach, "
        "so this figure can only be raised by letting the app keep running. "
        "Nothing here is ever back-filled.")
    return out


def capture_chains(symbols, cfg: dict | None = None) -> dict:
    """Ask for the near-dated chain of each ticker so it lands in the store.

    The capture is deliberately NARROW: expirations out to about fifty days
    and a bounded ring of strikes around the money. That is exactly what the
    covered-call tenors need — weekly, two to three weeks, one to one and a
    half months — with room for a strike a quarter above the price. Pulling
    a whole chain every day for a watchlist would be a large request for
    data no part of this app reads.

    Nothing is back-filled and nothing already stored for today is replaced.
    """
    cfg = cfg or (config()[0])
    today = _market_today()
    out = {"captured": [], "skipped": [], "failed": [], "not_expected": [],
           "as_of": _now_iso(), "day": today, "late": False,
           "max_dte": int(cfg.get("capture_max_dte",
                                  CAPTURE_DEFAULTS["capture_max_dte"])),
           "strike_count": int(cfg.get("capture_strike_count",
                                       CAPTURE_DEFAULTS["capture_strike_count"]))}
    # A quote taken on a Saturday is Friday's quote wearing Saturday's date.
    # Storing it would put a chain in the history for a day the market never
    # traded, and a backtest would later fill from it.
    if not _health.is_trading_day(today):
        out["reason"] = (
            f"The market did not trade on {_pretty(today)} — it was "
            f"{_health.why_not_trading(today) or 'not a trading day'} — so no "
            f"chain was asked for. A quote taken now would be the previous "
            f"session's, stored under today's date.")
        out["not_expected"] = [_safe(x) for x in (symbols or []) if _safe(x)]
        return out
    if _CC_CHAIN_FN is None:
        out["reason"] = ("No option-chain provider is wired for capture, so "
                         "no chains can be recorded today.")
        for sym in symbols or []:
            if _safe(sym):
                _health.record(_health.CHAIN, _safe(sym), False, day=today,
                               reason="no option-chain provider is wired")
        return out
    out["late"] = _after_capture_window()
    for sym in symbols or []:
        s = _safe(sym)
        if not s:
            continue
        if _health.captured(_health.CHAIN, s, today):
            out["skipped"].append(s)
            continue
        try:
            payload_ = _CC_CHAIN_FN(s, out["max_dte"], out["strike_count"])
        except Exception as exc:                     # noqa: BLE001
            payload_, why = None, str(exc)[:120]
        else:
            why = "the provider returned no chain"
        if not payload_:
            out["failed"].append(s)
            _health.record(_health.CHAIN, s, False, day=today, reason=why)
            continue
        # `record` returns False for two very different reasons: a chain is
        # already stored for today, which is the correct outcome, and the
        # payload was unusable or the write failed, which is a lost day. The
        # store itself is asked which happened — marking the second as a
        # success would make tomorrow skip the symbol for a chain that does
        # not exist, and a day that goes uncaptured cannot be recovered.
        wrote = chain_store.record(s, payload_, today=today,
                                   at=_now_iso(),
                                   source=payload_.get("source"),
                                   event=_chain_event(s))
        stored = wrote or today in (chain_store.load(s) or {})
        (out["captured"] if wrote else
         out["skipped"] if stored else out["failed"]).append(s)
        _health.record(_health.CHAIN, s, stored, day=today,
                       source=payload_.get("source") or "",
                       records=len(payload_.get("chains") or {}),
                       late=out["late"],
                       reason=("" if wrote else
                               "a chain was already stored for today, and "
                               "the first capture of a day is the one kept"
                               if stored else
                               "the provider returned a chain and the store "
                               "would not keep it — no spot, no usable "
                               "expirations, or the write failed"))
    return out


def _chain_event(symbol: str) -> dict | None:
    """What was going on around this ticker when the chain was captured.

    An option price a week before earnings is not the same observation as
    one a week after, and a snapshot that does not say which is a number
    without a context. Kept small — this rides along with every stored day.
    """
    if _EARNINGS_FN is None:
        return None
    try:
        e = _EARNINGS_FN(symbol) or {}
    except Exception:                                # noqa: BLE001
        return None
    nxt = str(e.get("next") or "")[:10]
    if not nxt:
        return None
    try:
        days = (date.fromisoformat(nxt) - date.today()).days
    except ValueError:
        return None
    return {"next_earnings": nxt, "days_to_earnings": days}


# ── Phase 4: the covered-call simulator ─────────────────────────────────────

def covered_call(symbol: str, years: int = 3, policies=None) -> dict:
    """Run the covered-call policies over this ticker's own price history.

    The fair-value-aware strike rules need a fair value ON EACH DAY, and the
    only honest source of one is what this app actually recorded that day.
    Today's fair value applied to two years of history would be lookahead of
    exactly the kind the forward-validation engine exists to prevent, so
    stored snapshots supply it and the days before the store began simply
    carry none — which makes those rules behave as their plain delta or
    percentage version until the record starts.
    """
    sym = (symbol or "").upper().strip()
    cfg, _hash = config()
    out = {"symbol": sym, "available": False, "reason": "",
           "version": _cc.COVERED_CALL_VERSION,
           "tenors": _cc.TENORS, "strike_rules": _cc.STRIKE_RULES,
           "roll_rules": _cc.ROLL_RULES,
           "assignment_modes": _cc.ASSIGNMENT_MODES}
    if _DAILY_FN is None:
        out["reason"] = "No daily price history provider is wired."
        return out
    try:
        bars = (_DAILY_FN(sym, int(years * 366)) or {}).get("bars") or []
    except Exception as exc:                          # noqa: BLE001
        out["reason"] = f"The price history could not be loaded: {exc}"
        return out
    if len(bars) < 60:
        out["reason"] = (f"Only {len(bars)} daily closes are available for "
                         f"{sym}. A covered-call run needs a price history "
                         f"to walk through.")
        return out

    fv_by_day = {}
    for row in load_history(sym):
        day = str(row.get("date") or "")[:10]
        if not day:
            continue
        fv_by_day[day] = {"base": row.get("fair_value_base"),
                          "credited": row.get("credited_fair_value"),
                          "buy_zone": row.get("buy_zone")}
    divs = {}
    if _ACTIONS_FN is not None:
        try:
            divs = (_ACTIONS_FN(sym) or {}).get("dividends") or {}
        except Exception:                             # noqa: BLE001
            divs = {}
    facts = _fund.company_facts(sym) if _fund is not None else None
    dps = (_dividends(facts) or {}).get("value") if facts else None
    rate = _rate(1.0)

    # Volatility: the same series the options backtester uses, calibrated
    # against this ticker's own realized volatility. Never a fixed guess.
    iv_series = _iv_path(sym, bars)
    store = chain_store.load(sym)

    runs = _cc.compare_policies(
        bars, iv_series, policies or _cc.default_policies(), cfg=cfg,
        sym_store=store, dividends=divs, dividend_rate_ttm=dps,
        fair_value_by_day=fv_by_day, rate_pct=rate.get("pct"))
    out.update(runs)
    out["readiness"] = chain_readiness(
        sym, store, [str(b.get("date") or b.get("d") or "")[:10]
                     for b in bars])
    out["fair_value_days_recorded"] = len(fv_by_day)
    out["fair_value_note"] = (
        f"The fair-value-aware rules used the {len(fv_by_day)} day"
        f"{'s' if len(fv_by_day) != 1 else ''} of fair value this app has "
        f"actually recorded for {sym}. Days before the record began carry "
        f"none, so on those days those rules fall back to their delta or "
        f"percentage form. Nothing here applies today's valuation to a past "
        f"day.")
    out["chain_days_stored"] = len(store)
    return out


def _iv_path(symbol: str, bars: list, earnings_dates=None) -> list:
    """One implied volatility per bar, from the options backtester's own
    model — this ticker's realized volatility through that bar, scaled by
    the documented implied-to-realized ratio and lifted around its earnings
    dates.

    The stored long-dated implied volatility is deliberately NOT used to
    calibrate this. Those observations are of contracts a year or more out,
    and the calls being sold here expire in weeks; Phase 3 refused to
    compare long-dated implied volatility against short-dated realized
    volatility, and reusing it as a calibration here would be the same
    mistake wearing a different hat.
    """
    # The filter has to match the simulator's exactly. One bar kept here and
    # dropped there shifts every volatility reading after it by a day, which
    # is the kind of error that produces plausible numbers forever.
    rows = [{"date": str(b.get("date") or b.get("d") or "")[:10],
             "close": _f(b.get("close") if b.get("close") is not None
                         else b.get("c"))}
            for b in bars or []]
    rows = [r for r in rows if r["date"] and r["close"] and r["close"] > 0]
    if not rows:
        return []
    try:
        scalers, _src = bt_iv.vix_scaler_series([r["date"] for r in rows], {})
        return bt_iv.build_iv_series(rows, bt_iv.DEFAULT_RATIO, scalers,
                                     earnings_dates or [])
    except Exception:                                 # noqa: BLE001
        return [None] * len(rows)


# ── Phase 4: forward validation ─────────────────────────────────────────────

# ── Phase 7: is the prospective capture actually happening? ─────────────────

# Today, before its capture window. Not a failure and not a success: the
# fifth state the panel can be in, and the only one that is allowed to
# disappear on its own as the clock moves.
NOT_DUE_YET = "NOT DUE YET"


def data_readiness(symbols=None, today: str | None = None) -> dict:
    """How much real, prospectively captured data exists — and whether it is
    still being captured.

    Everything the forward work rests on is collected going forward and can
    never be back-filled, so the two questions that matter are how much there
    is and whether today added to it. Both are answered from the stores
    themselves rather than from an assumption that the scheduler ran.
    """
    day = today or _market_today()
    syms = [_safe(s) for s in (symbols or []) if _safe(s)]
    if not syms and _STARRED_FN is not None:
        try:
            syms = [_safe(s) for s in (_STARRED_FN() or []) if _safe(s)]
        except Exception:                            # noqa: BLE001
            syms = []
    syms = syms[:MAX_DAILY_SYMBOLS]
    expected = expected_today(syms, day)
    # Today is not a failure until today's capture window has passed. At ten
    # in the morning nothing has been captured because nothing was due, and
    # calling that a capture failure would make the panel cry wolf every
    # morning until it stopped being read. The health summary looks at the
    # last day whose window HAS passed; today's own pending state is shown
    # separately, beside it.
    due = (_health.is_trading_day(day)
           and market_now().hour >= _capture_hour()
           and day == _market_today())
    settled = day if (due or day != _market_today()) else \
        _health.previous_trading_day(day)
    out = {
        "as_of": _now_iso(), "day": day, "symbols": syms,
        # When this answer was computed, on the exchange's clock, and which
        # clock that is. The panel is a snapshot of a moment; without the
        # moment on it a reader cannot tell a healthy day from a page that
        # has been open since lunchtime.
        "calculated_at": _now_iso(),
        "market_clock": market_clock(),
        "trading_day": _health.is_trading_day(day),
        "not_trading_because": _health.why_not_trading(day),
        "capture_due_yet": bool(due or day != _market_today()),
        "today": _health.day_status(day, expected),
        "previous": _health.day_status(_health.previous_trading_day(day),
                                       expected),
        "health": _health.health(expected, days_back=5, today=settled),
        "health_day": settled,
        "capture_hour_et": _capture_hour(),
        "version": _health.CAPTURE_HEALTH_VERSION,
        "backfill_note": (
            "Nothing here is ever back-filled. There is no source of "
            "historical option chains this app can reach, so a trading day "
            "that goes uncaptured stays uncaptured — which is why a missed "
            "day is reported the next morning rather than discovered in a "
            "backtest months later."),
    }

    snapshot_days, chain_days, leaps_days, real_days = set(), set(), set(), 0
    per_symbol = []
    for sym in syms:
        rows = load_history(sym)
        snaps = {(r.get("date") or "")[:10] for r in rows if r.get("date")}
        snapshot_days |= snaps
        store = chain_store.load(sym)
        chain = sorted(store or {})
        chain_days |= set(chain)
        obs = []
        if _opts is not None:
            try:
                obs = [(o.get("date") or "")[:10]
                       for o in _opts.load_leaps_observations(sym)]
            except Exception:                        # noqa: BLE001
                obs = []
        leaps_days |= set(obs)
        cov = _health.symbol_coverage(sym, first_day=(min(snaps) if snaps
                                                      else None), today=day)
        per_symbol.append({
            "symbol": sym,
            "snapshot_days": len(snaps),
            "first_snapshot": min(snaps) if snaps else None,
            "last_snapshot": max(snaps) if snaps else None,
            "chain_days": len(chain),
            "first_chain": chain[0] if chain else None,
            "last_chain": chain[-1] if chain else None,
            "leaps_days": len(set(obs)),
            "missing_expected_days":
                (cov["kinds"][_health.CHAIN]["missing_days"] or [])[-10:],
            "chain_coverage_pct": cov["kinds"][_health.CHAIN]["coverage_pct"],
            "captured_today": _health.captured(_health.SNAPSHOT, sym, day),
        })
        real_days += len(chain)
    out["symbol_rows"] = sorted(per_symbol, key=lambda r: r["symbol"])
    out["investment_snapshot_days"] = len(snapshot_days)
    out["real_chain_days"] = len(chain_days)
    out["leaps_observation_days"] = len(leaps_days)
    out["first_snapshot"] = min(snapshot_days) if snapshot_days else None
    out["first_chain"] = min(chain_days) if chain_days else None
    out["last_chain"] = max(chain_days) if chain_days else None
    out["last_successful_capture"] = out["health"].get("last_successful")
    out["symbols_missing_today"] = sorted(
        {s for v in (out["today"].get("missing") or {}).values() for s in v})
    if not out["capture_due_yet"]:
        out["today"]["state"] = NOT_DUE_YET
        out["today"]["reason"] = (
            f"Today's capture runs after {_capture_hour()}:00 in New York. "
            f"Nothing is missing yet; what is listed below is simply what is "
            f"still to come. The state above describes "
            f"{_pretty(settled)}, the last day whose capture window has "
            f"passed. Read at "
            f"{market_now().strftime('%-I:%M %p')} New York time.")
    elif out["today"]["state"] == _health.MISSED:
        # Past the window on a trading day with nothing captured is not
        # "missed one of several" — it is the whole day gone, and the option
        # chain for it cannot be bought back. It gets the word that means so.
        out["today"]["state"] = _health.FAILURE
    # After the capture window, today has an answer. NOT DUE YET past the
    # hour meant the clock was wrong, and that symptom is never allowed to
    # stand in for a state again.
    if out["capture_due_yet"] and out["today"]["state"] == NOT_DUE_YET:
        raise AssertionError(                        # pragma: no cover
            "today's capture window has passed and the state is NOT DUE YET")

    # Real-chain coverage over the trading days since capture began. Days
    # before the first capture are not counted as missed, because nothing
    # was expected of them.
    if chain_days:
        span = _health.trading_days(min(chain_days), day)
        out["chain_coverage_pct"] = (
            len([d for d in span if d in chain_days]) / len(span) * 100.0
            if span else None)
        out["chain_span_days"] = len(span)
    else:
        out["chain_coverage_pct"] = None
        out["chain_span_days"] = 0

    out["forward"] = _forward_readiness(snapshot_days, day)
    return out


def _forward_readiness(snapshot_days: set, day: str) -> dict:
    """When forward validation can first say anything, and how many
    observations are ageing toward each horizon.

    This adds no verdict of any kind. An incomplete horizon is not scored,
    and until enough of them complete the answer stays INSUFFICIENT SAMPLE.
    """
    out = {"first_snapshot": min(snapshot_days) if snapshot_days else None,
           "horizons": [], "verdict": _forward.INSUFFICIENT,
           "reason": (
               "Forward validation scores a recommendation only once its "
               "whole horizon has passed. Nothing is scored early, nothing "
               "is annualised from a partial window, and no verdict is given "
               "until there are enough completed observations for one to "
               "mean anything.")}
    if not snapshot_days:
        out["reason"] = ("No snapshot has been recorded yet, so there is "
                         "nothing ageing toward a result.")
        return out
    first = min(snapshot_days)
    for horizon in _forward.HORIZONS:
        eligible = date.fromisoformat(first) + timedelta(days=horizon)
        ageing = sum(1 for d in snapshot_days
                     if date.fromisoformat(d) + timedelta(days=horizon)
                     > date.fromisoformat(day))
        complete = len(snapshot_days) - ageing
        out["horizons"].append({
            "days": horizon,
            "first_eligible_date": eligible.isoformat(),
            "first_eligible_pretty": _pretty(eligible.isoformat()),
            "reached": eligible.isoformat() <= day,
            "ageing": ageing, "complete": complete,
            "needed": _forward.MIN_SAMPLE,
        })
    return out


def validation(symbols=None, benchmark: str | None = None) -> dict:
    """How the recommendations this app already made have turned out.

    Reads the snapshot store forward. Nothing is recomputed and nothing is
    rewritten: each stored row is judged exactly as it was written, against
    prices that came after it.
    """
    cfg, _hash = config()
    out = {"available": False, "reason": "",
           "version": _forward.FORWARD_TEST_VERSION}
    syms = list(symbols or [])
    if not syms and _STARRED_FN is not None:
        try:
            syms = list(_STARRED_FN() or [])
        except Exception:                             # noqa: BLE001
            syms = []
    if not syms:
        out["reason"] = ("No tickers are being tracked, so there are no "
                         "recorded recommendations to follow forward.")
        return out
    if _DAILY_FN is None:
        out["reason"] = "No daily price history provider is wired."
        return out

    hist, bars = {}, {}
    for sym in syms:
        rows = load_history(sym)
        if not rows:
            continue
        hist[sym] = rows
        try:
            bars[sym] = (_DAILY_FN(sym, 900) or {}).get("bars") or []
        except Exception:                             # noqa: BLE001
            bars[sym] = []
    bench_bars = []
    bench = benchmark or _benchmark_symbol(syms[0] if syms else None)
    if bench:
        try:
            bench_bars = (_DAILY_FN(bench, 900) or {}).get("bars") or []
        except Exception:                             # noqa: BLE001
            bench_bars = []

    today = _market_today()
    # The archive is what makes a stored verdict mean something a year on.
    # A row naming rules that are not in it is excluded from scoring and left
    # exactly as it is — see forward_test.eligibility.
    archived = {r["config_hash"] for r in archived_configs()}
    obs = _forward.observations(hist, bars, today, bench_bars, cfg=cfg,
                                known_hashes=archived)
    out["observations"] = {k: obs[k] for k in
                           ("n", "skipped", "benchmark_available",
                            "excluded_by_reason", "excluded_note",
                            "config_archive_checked")}
    out["eligibility"] = _forward.eligibility_report(hist, archived)
    out["calibration"] = _forward.calibration(obs, cfg)
    out["structures"] = _forward.structure_report(hist, bars, today, cfg,
                                                  known_hashes=archived)
    out["benchmark"] = bench
    out["tickers_with_history"] = len(hist)
    out["stored_rows"] = sum(len(v) for v in hist.values())
    out["recording"] = recording_audit(hist)
    out["available"] = True
    out["reason"] = out["calibration"].get("reason") or ""
    return out


# What every stored day must carry for a future scoring pass to settle up
# against the recommendation exactly as it was made.
REQUIRED_FOR_SCORING = (
    ("date", "the trading day it belongs to"),
    ("ticker", "which company"),
    ("price", "the share price on the day"),
    ("config_hash", "the exact rule version that produced it"),
    ("entry_verdict", "the recommendation itself"),
    ("preferred_structure", "which structure was preferred"),
    ("recommended_contract", "the exact contract and the quote it carried"),
    ("benchmark_symbol", "the benchmark it will be measured against"),
    ("benchmark_close", "what that benchmark was worth on the day"),
    ("fair_value_bear", "the bear case behind the recommendation"),
    ("fair_value_base", "the fair value behind the recommendation"),
    ("fair_value_bull", "the bull case behind the recommendation"),
    ("fair_value_confidence", "how sure that fair value was"),
    ("buy_zone", "the price at which it said to buy"),
    ("quality_label", "the quality reading on the day"),
    ("growth_label", "the growth reading on the day"),
    ("valuation_label", "the valuation reading on the day"),
    ("revisions_label", "the analyst-revision reading on the day"),
    ("value_trap_level", "the value-trap reading on the day"),
)


def recording_audit(hist: dict) -> dict:
    """Is today's recording complete enough for tomorrow's scoring?

    This looks FORWARD, not backward. Old rows are never rewritten, so a row
    written before a field existed will always lack it and that is correct.
    What matters is whether the rows being written NOW carry everything a
    future exact scoring pass needs — and if one does not, saying so today is
    the only chance to fix it before another year of rows goes by.
    """
    latest = []
    for sym, rows in (hist or {}).items():
        if rows:
            latest.append((sym, rows[-1]))
    out = {"tickers": len(latest), "complete": 0, "fields": [],
           "missing_examples": [], "reason": ""}
    if not latest:
        out["reason"] = ("Nothing has been recorded yet, so there is nothing "
                         "to check the recording against.")
        return out
    for key, what in REQUIRED_FOR_SCORING:
        have = [s for s, r in latest if r.get(key) is not None]
        out["fields"].append({
            "field": key, "what": what, "n": len(have),
            "of": len(latest),
            "complete": len(have) == len(latest),
            "missing": sorted(s for s, r in latest if r.get(key) is None)[:8]})
    out["complete"] = sum(1 for f in out["fields"] if f["complete"])
    gaps = [f for f in out["fields"] if not f["complete"]]
    if not gaps:
        out["reason"] = (
            f"Every one of the {len(latest)} tickers being recorded carries "
            f"all {len(REQUIRED_FOR_SCORING)} things a future scoring pass "
            f"needs. Rows already on disk are never revised, so this says "
            f"what is being written from today onward.")
    else:
        out["missing_examples"] = [f["field"] for f in gaps]
        out["reason"] = (
            f"{len(gaps)} of {len(REQUIRED_FOR_SCORING)} required fields are "
            f"not being recorded for every ticker: "
            + ", ".join(f["field"] for f in gaps) +
            ". A recommendation missing one of these cannot be scored "
            "exactly later, and nothing can be filled in after the fact.")
    return out


def _store_coverage(path, pattern: str, count_fn) -> tuple:
    """How many tickers a store holds, and the most days any one of them has.

    Measured over everything on disk rather than over the tickers being
    audited: the bytes belong to every ticker ever stored, so dividing them
    by a filtered subset would report a per-ticker rate several times too
    large.
    """
    try:
        if path is None or not Path(path).is_dir():
            return 0, 0
        syms = [p.name.split(".")[0] for p in Path(path).glob(pattern)]
    except Exception:                                # pragma: no cover
        return 0, 0
    days = 0
    for s in syms[:200]:
        try:
            days = max(days, int(count_fn(s) or 0))
        except Exception:                            # noqa: BLE001
            continue
    return len(syms), days


def day_report(symbols=None, day: str | None = None) -> dict:
    """One trading day, one row per followed ticker, component by component.

    The question this answers is not "did something run" but "is this day
    usable" — which is a different and stricter thing. A day with a snapshot
    and no chain is a day the covered-call work can never use; a day whose
    recommendation names a put and did not record the put is a day that
    cannot be settled up. Both look fine in a capture log.
    """
    d = day or _market_today()
    syms = [_safe(s) for s in (symbols or []) if _safe(s)]
    if not syms and _STARRED_FN is not None:
        try:
            syms = [_safe(s) for s in (_STARRED_FN() or []) if _safe(s)]
        except Exception:                            # noqa: BLE001
            syms = []
    syms = syms[:MAX_DAILY_SYMBOLS]
    archived = {r["config_hash"] for r in archived_configs()}

    rows = []
    for sym in syms:
        row = next((r for r in reversed(load_history(sym))
                    if (r.get("date") or "")[:10] == d), None)
        chain = d in (chain_store.load(sym) or {})
        leaps = False
        if _opts is not None:
            try:
                leaps = any((o.get("date") or "")[:10] == d
                            for o in _opts.load_leaps_observations(sym))
            except Exception:                        # noqa: BLE001
                leaps = False
        fit = _forward.eligibility(row or {}, archived) if row else None
        needs = (fit or {}).get("contract_required") or []
        contract = (row or {}).get("recommended_contract") or {}
        rows.append({
            "symbol": sym,
            "snapshot": bool(row),
            "option_chain": chain,
            "leaps_observation": leaps,
            "benchmark_close": (row or {}).get("benchmark_close") is not None,
            "benchmark_symbol": (row or {}).get("benchmark_symbol") or "",
            "recommendation": (row or {}).get("entry_verdict") or "",
            "structure": (row or {}).get("preferred_structure") or "",
            "contract_required": bool(needs),
            "contract_recorded": (True if not needs else bool(contract)),
            "contract_note": ("This recommendation names no option contract, "
                              "and needs none." if row and not needs else
                              "The exact contract and its quote were recorded."
                              if row and contract else
                              "The recommendation names an option and the "
                              "contract was not recorded." if row else
                              "No snapshot was recorded for this ticker."),
            "config_archived": bool(row and (row.get("config_hash") in archived)),
            "forward_test_eligible": bool(fit and fit["eligible"]),
            "benchmark_relative_eligible": bool(fit and fit["benchmark_relative"]),
            "why_not": (fit or {}).get("reasons") or
                       ([] if row else ["NO SNAPSHOT RECORDED"]),
        })

    usable = [r for r in rows if r["forward_test_eligible"]]
    complete = [r for r in usable
                if r["option_chain"] and r["leaps_observation"]
                and r["benchmark_close"]]
    return {
        "day": d, "pretty": _pretty(d),
        "trading_day": _health.is_trading_day(d),
        "not_trading_because": _health.why_not_trading(d),
        "calculated_at": _now_iso(),
        "market_clock": market_clock(),
        "symbols": syms, "rows": rows,
        "expected": len(rows),
        "forward_test_eligible": len(usable),
        "fully_complete": len(complete),
        "first_fully_usable_day": len(rows) > 0 and len(complete) == len(rows),
        "reason": (
            f"All {len(rows)} followed tickers recorded everything this day "
            f"needed to be scored later: the valuation state, a real option "
            f"chain, a long-dated observation, the benchmark close, and the "
            f"exact contract wherever the recommendation named one."
            if rows and len(complete) == len(rows) else
            f"{len(complete)} of {len(rows)} followed tickers have a "
            f"complete, scorable record for {_pretty(d)}. "
            f"{len(usable)} can be scored on their own return. Nothing here "
            f"is repaired: a day records what it records."
            if rows else
            "No tickers are being followed, so there is nothing to report."),
        "version": _audit.AUDIT_VERSION,
    }


# ── the production audit ────────────────────────────────────────────────────
#
# One function, answering whether the next year of data will be worth
# having. It reads the stores and reports; it repairs nothing.

def production_audit(symbols=None, today: str | None = None) -> dict:
    """Is production genuinely collecting clean data every trading day?"""
    day = today or _market_today()
    syms = [_safe(s) for s in (symbols or []) if _safe(s)]
    if not syms and _STARRED_FN is not None:
        try:
            syms = [_safe(s) for s in (_STARRED_FN() or []) if _safe(s)]
        except Exception:                            # noqa: BLE001
            syms = []
    syms = syms[:MAX_DAILY_SYMBOLS]
    expected = expected_today(syms, day)
    root = _DATA_DIR.parent if _DATA_DIR is not None else None

    out = {
        "as_of": _now_iso(), "day": day, "symbols": syms,
        "market_timezone": str(getattr(market_now(), "tzinfo", "") or
                               "the container's clock"),
        "capture_hour_et": _capture_hour(),
        "home": _audit.data_home(root),
        "previous_day": _audit.previous_day(expected, today=day),
        "version": _audit.AUDIT_VERSION,
    }

    # Every path that holds something the app cannot get back, named, so the
    # question "where would I look for it" has an answer on the screen rather
    # than in a source file.
    out["paths"] = _audit.paths({
        "root": root,
        "snapshots": (_DATA_DIR / "snapshots") if _DATA_DIR else None,
        "chains": (root / "chains") if root else None,
        "leaps": (_DATA_DIR / "leaps") if _DATA_DIR else None,
        "capture": (_DATA_DIR / "capture") if _DATA_DIR else None,
        "config": (_DATA_DIR / "config") if _DATA_DIR else None,
    })
    # One line answering only "can this app be left to accumulate data", which
    # is a question about storage alone. The fuller state below it takes in
    # yesterday's capture, the retention limits and the stored records too.
    out["collection_status"], out["collection_reason"] = _audit.collection_status(
        out["home"])

    # What each store keeps, measured against the longest horizon.
    out["retention"] = _audit.retention({
        "snapshots": {"label": "Investment snapshots", "keeps": None,
                      "note": "Never trimmed. This is the recommendation "
                              "history and it is the one thing that must "
                              "never be thrown away."},
        "chains": {"label": "End-of-day option chains",
                   "keeps": chain_store.MAX_DAYS_KEPT,
                   "note": "The largest store by far, and the one that "
                           "cannot be recovered if it is lost."},
        "leaps": {"label": "Long-dated contract observations",
                  "keeps": int((config()[0] or {}).get(
                      "leaps_iv_history_days") or 0)
                  or (_opts.DEFAULTS["leaps_iv_history_days"]
                      if _opts is not None else 0),
                  "note": "One small row per ticker per day."},
        "capture_log": {"label": "Capture-health log",
                        "keeps": _health.KEEP_DAYS,
                        "note": "Operational. Losing it loses the record of "
                                "which days were captured, not the data."},
    })

    # How much is on disk, and what a year of it comes to. Every store is
    # measured against ITS OWN coverage — the snapshots may be months older
    # than the first captured chain, the long-dated observations older still,
    # and the configuration archive does not grow per ticker at all. One
    # shared denominator would understate one store and invent a rate for
    # another.
    #
    # These counts are over the tickers being audited. When the audit is
    # filtered to a handful of them the bytes on disk still belong to every
    # ticker ever stored, so the per-ticker rate would be inflated — the
    # store counts are taken over EVERYTHING on disk instead, and the
    # projection then answers "a year at the size this store already is".
    per_symbol = []
    for sym in syms:
        per_symbol.append({"symbol": sym, "snapshot_days": len(load_history(sym)),
                           "chain_days": len(chain_store.load(sym) or {})})
    out["symbol_rows"] = per_symbol
    snap_n, snap_d = _store_coverage(
        (_DATA_DIR / "snapshots") if _DATA_DIR else None, "*.jsonl",
        lambda s: len(load_history(s)))
    chain_n, chain_d = _store_coverage(
        (root / "chains") if root else None, "*.json",
        lambda s: len(chain_store.load(s) or {}))
    leaps_n, leaps_d = _store_coverage(
        (_DATA_DIR / "leaps") if _DATA_DIR else None, "*.jsonl",
        lambda s: len(_opts.load_leaps_observations(s)) if _opts else 0)
    log_days = len(list((_DATA_DIR / "capture").glob("????-??-??.json"))
                   ) if (_DATA_DIR and (_DATA_DIR / "capture").is_dir()) else 0
    out["storage"] = _audit.storage({
        "snapshots": {"label": "Investment snapshots",
                      "path": (_DATA_DIR / "snapshots") if _DATA_DIR else None,
                      "symbols": snap_n, "days": snap_d,
                      "note": "One flat row per ticker per day. Never "
                              "trimmed, so this is the store that grows "
                              "without end — and the one that must."},
        "chains": {"label": "End-of-day option chains",
                   "path": (root / "chains") if root else None,
                   "symbols": chain_n, "days": chain_d,
                   "note": "Bid, ask, implied volatility, delta, open "
                           "interest and volume for every contract in band. "
                           "The largest store by far."},
        "leaps": {"label": "Long-dated contract observations",
                  "path": (_DATA_DIR / "leaps") if _DATA_DIR else None,
                  "symbols": leaps_n, "days": leaps_d,
                  "note": "The contracts around the money a year or more "
                          "out, and what they implied. Small, and the only "
                          "volatility history a long-dated option has of "
                          "its own."},
        "capture_log": {"label": "Capture-health log",
                        "path": (_DATA_DIR / "capture") if _DATA_DIR else None,
                        "per_ticker": False, "days": log_days,
                        "coverage": (f"one file per day, over {log_days} day"
                                     f"{'' if log_days == 1 else 's'}"),
                        "note": "One small file per trading day recording "
                                "what was attempted and what succeeded. It "
                                "does not grow with the number of tickers "
                                "the way the others do. Operational: losing "
                                "it loses the record of which days were "
                                "captured, not the data."},
        "config_archive": {"label": "Configuration archive",
                           "path": (_DATA_DIR / "config") if _DATA_DIR else None,
                           "per_ticker": False, "days": 0,
                           "coverage": "does not grow daily — one copy per "
                                       "distinct rule set, written once",
                           "note": "One copy of each distinct rule set, "
                                   "written once. It grows when the rules "
                                   "change, not when a day passes, so there "
                                   "is no yearly rate to project."},
    }, symbols=len(syms), days=chain_d)

    # Can a stored recommendation still be traced back to its rules?
    archived = {r["config_hash"] for r in archived_configs()}
    out["config_archive"] = {
        "archived": len(archived),
        "current_hash": config()[1],
        "current_archived": config()[1] in archived,
        "configs": archived_configs()[-8:],
        "reason": ("Each distinct rule set is written once, under its hash, "
                   "and never rewritten — so the rules behind a stored "
                   "recommendation can still be read back exactly."
                   if archived else
                   "No configuration has been archived yet. It is written "
                   "the first time the config is read with a data directory "
                   "attached."),
    }

    # Is what is already stored believable?
    findings = []
    for sym in syms:
        # `archived` is passed even when it is empty. An empty archive means
        # NOTHING stored can be traced back to its rules, which is a finding
        # on every row — passing None instead would silently call that clean.
        findings.extend(_audit.audit_history(sym, load_history(sym), today=day,
                                             known_hashes=archived))
        findings.extend(_audit.audit_chains(sym, chain_store.load(sym),
                                            today=day))
    counts: dict = {}
    for f in findings:
        counts[f["finding"]] = counts.get(f["finding"], 0) + 1
    out["integrity"] = {
        "findings": findings[:60], "n": len(findings), "by_kind": counts,
        "clean": not findings,
        "reason": ("Nothing stored contradicts itself: no duplicate or "
                   "future-dated rows, no impossible prices, no crossed "
                   "quotes, and every recommendation names the rules behind "
                   "it." if not findings else
                   f"{len(findings)} stored records do not hold up. They are "
                   f"listed rather than mended: repairing history in place "
                   f"would destroy the evidence of what actually happened."),
    }

    # Is what is being written NOW enough to score later?
    hist = {s: load_history(s) for s in syms}
    out["recording"] = recording_audit({k: v for k, v in hist.items() if v})

    out["state"], out["reason"] = _audit_state(out)
    return out


def _audit_state(out: dict) -> tuple:
    """One word for the whole thing, and the sentence behind it."""
    blockers = []
    if out["home"]["state"] == _audit.EPHEMERAL:
        blockers.append("the data directory does not survive a redeploy")
    elif out["home"]["state"] == _audit.UNKNOWN:
        # A path spelled like the volume's mount point but sitting on the
        # container's own filesystem is what a DETACHED volume looks like
        # from inside. Reading that as healthy would put a green line over
        # the exact failure this report exists to catch.
        blockers.append("the data directory cannot be confirmed to survive a "
                        "redeploy, and an unconfirmed volume is one that "
                        "loses everything when it turns out not to be there")
    if not out["retention"]["ok"]:
        blockers.append("a retention limit would delete a day before it is "
                        "needed")
    if blockers:
        return _audit.FAILURE, (
            "Production is NOT ready to accumulate data that can be relied "
            "on: " + "; and ".join(blockers) + ".")
    soft = []
    if out["previous_day"]["state"] in (_health.MISSED, _health.PARTIAL):
        soft.append(f"the previous trading day was "
                    f"{out['previous_day']['state'].lower()}")
    if not out["integrity"]["clean"]:
        soft.append(f"{out['integrity']['n']} stored records do not hold up")
    if not out["config_archive"]["current_archived"]:
        soft.append("the configuration behind today's recommendations is not "
                    "archived")
    if soft:
        return _audit.PARTIAL, (
            "Production is storing data that will survive, with something to "
            "look at: " + "; and ".join(soft) + ".")
    return _audit.HEALTHY, (
        "Production is collecting cleanly: the data survives a redeploy, "
        "the previous trading day is complete, every retention limit clears "
        "the longest validation horizon, nothing stored contradicts itself, "
        "and the rules behind every recommendation can still be read back.")


def _benchmark_symbol(sym):
    if _BENCHMARK_FN is None or not sym:
        return None
    try:
        return _BENCHMARK_FN(sym)
    except Exception:                                 # noqa: BLE001
        return None


def _scan_worker(symbols: list) -> None:              # pragma: no cover
    for sym in symbols:
        try:
            payload(sym, force=False)
        except Exception:                            # noqa: BLE001
            pass
        finally:
            with _LOCK:
                _SCAN_BUILDING.discard(sym)


def scan(symbols=None, build_budget: int = 4) -> dict:
    """The Investment scanner over a list of tickers.

    Reads the stored snapshots rather than rebuilding every ticker on every
    request: a full build reads SEC filings, a peer group and an option chain,
    and doing that for a watchlist inside one HTTP request would be a
    multi-minute page. Tickers with nothing stored are reported as such and a
    small number are built in the background per call, so the table fills in
    rather than either lying or hanging.
    """
    syms = [(_safe(s) or "") for s in (symbols or [])]
    syms = [s for s in syms if s]
    if not syms and _STARRED_FN is not None:
        try:
            syms = [_safe(s) for s in (_STARRED_FN() or [])]
        except Exception:                            # noqa: BLE001
            syms = []
    rows, missing = [], []
    for sym in syms:
        snap = load_latest(sym)
        if not snap:
            missing.append(sym)
            rows.append({"symbol": sym, "status": "not recorded yet",
                         "reason": ("This ticker has never been opened on the "
                                    "Investment tab and no snapshot exists "
                                    "for it yet.")})
            continue
        if not snap.get("ok"):
            rows.append({"symbol": sym, "status": "unavailable",
                         "name": snap.get("entity_name") or "",
                         "reason": snap.get("unavailable_reason") or ""})
            continue
        rows.append(_scan_row(sym, snap))

    queued = []
    if missing:
        with _LOCK:
            for sym in missing:
                if len(queued) >= max(0, int(build_budget)):
                    break
                if sym not in _SCAN_BUILDING:
                    _SCAN_BUILDING.add(sym)
                    queued.append(sym)
        if queued:
            threading.Thread(target=_scan_worker, args=(queued,),
                             name="invest-scan", daemon=True).start()

    return {"rows": rows, "as_of": _now_iso(), "n": len(rows),
            "n_recorded": sum(1 for r in rows if r.get("status") == "recorded"),
            "n_missing": len(missing), "building": queued,
            "note": ("No column here is a total. There is deliberately no "
                     "summed investment score: the four readings answer "
                     "different questions and adding them would let a strong "
                     "one carry a weak one.")}


def record_daily(symbols, day: str | None = None) -> dict:
    """Take one prospective snapshot per ticker. Called by the app's daily
    scheduler so the store grows whether or not anyone opens the tab.

    Every ticker's attempt is written to the capture log, whether it worked
    or not, and so are the three things that ride along with a snapshot: the
    long-dated observation, the benchmark close it will be measured against,
    and the exact contract the app recommended. A snapshot that carries none
    of those is a snapshot that cannot be validated later, and the log says
    so on the day rather than at the end of the backtest.
    """
    today = day or _market_today()
    late = _after_capture_window()
    done, failed = [], []
    for sym in symbols or []:
        s = _safe(sym)
        if not s:
            continue
        try:
            snap = payload(s, force=True)     # the full Phase 2 state
        except Exception as exc:              # noqa: BLE001
            snap, why = None, (f"the snapshot could not be built: "
                               f"{str(exc)[:100]}")
        else:
            # `payload` returns normally with ok False when the fundamentals
            # are not available. That is an honest answer and it is not a
            # captured valuation state, so it is not recorded as one — or
            # tomorrow's restart would skip a symbol whose snapshot never
            # existed.
            why = ("" if snap.get("ok") else
                   (snap.get("unavailable_reason")
                    or "the fundamentals for this ticker are not available"))
        if snap is None or why:
            failed.append(s)
            for kind in (_health.SNAPSHOT, _health.LEAPS, _health.BENCHMARK,
                         _health.CONTRACT):
                _health.record(kind, s, False, day=today, reason=why)
            continue
        done.append(s)
        _health.record(_health.SNAPSHOT, s, True, day=today, late=late,
                       source="sec+quote", records=1)
        _record_riders(s, snap, today, late)
    return {"recorded": done, "failed": failed, "as_of": _now_iso(),
            "day": today, "late": late}


def _record_riders(sym: str, snap: dict, today: str, late: bool) -> None:
    """The three things a snapshot has to carry to be worth scoring later."""
    bench = (snap or {}).get("benchmark") or {}
    _health.record(_health.BENCHMARK, sym, bench.get("close") is not None,
                   day=today, late=late, source=bench.get("symbol") or "",
                   reason=("" if bench.get("close") is not None
                           else (bench.get("reason")
                                 or "no benchmark close was available")))
    obs = []
    if _opts is not None:
        try:
            obs = [o for o in _opts.load_leaps_observations(sym)
                   if (o.get("date") or "")[:10] == today]
        except Exception:                            # noqa: BLE001
            obs = []
    _health.record(_health.LEAPS, sym, bool(obs), day=today, late=late,
                   records=len((obs[0].get("rows") if obs else []) or []),
                   reason=("" if obs else
                           "no long-dated contracts were observable for this "
                           "ticker today"))
    # The contract that matters is the one the app actually recommended —
    # the preferred structure's — not whichever long-dated call happened to
    # be priced beside it.
    got = _recommended_contract(snap)
    contract = got.get("recommended_contract") or {}
    priced = any(contract.get(k) is not None
                 for k in ("debit", "credit", "mid", "bid"))
    _health.record(_health.CONTRACT, sym, bool(priced), day=today, late=late,
                   source=contract.get("structure") or "",
                   reason=("" if priced else
                           (got.get("recommended_contract_reason")
                            or "the app made no priced contract "
                               "recommendation for this ticker today")))


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
# The container this app runs on keeps its clock in UTC, and the scheduler
# used to read it as though it were New York time. Seventeen hundred UTC is
# one in the afternoon in New York, so the "end of day" chain was captured
# in the middle of the session and the daily snapshot was taken before the
# close. Both are read on an exchange clock now.
#
# The fallback matters as much as the lookup. `zoneinfo` reads the operating
# system's time-zone database, and a slim container may not carry one — the
# `tzdata` package in requirements.txt is there so it always does. But if the
# lookup fails anyway, falling back to `datetime.now()` would silently put
# the whole app back on the container's UTC clock: captures filed under
# tomorrow's date after eight in the evening, the "end of day" chain taken at
# one in the afternoon, and a readiness panel reporting NOT DUE YET at half
# past nine at night. That failure is invisible and it corrupts the dates on
# data that cannot be collected twice.
#
# So there is no container-clock fallback. Eastern time is computed instead,
# from the rule Congress fixed in 2007: daylight saving runs from the second
# Sunday in March to the first Sunday in November, changing at two in the
# morning local time. Same principle as the market calendar in
# capture_health, which computes its holidays rather than tabulating them.
try:                                                 # pragma: no cover
    from zoneinfo import ZoneInfo as _ZoneInfo
    _MARKET_TZ = _ZoneInfo("America/New_York")
    _TZ_SOURCE = "the operating system's time-zone database"
except Exception:                                    # pragma: no cover
    _MARKET_TZ = None
    _TZ_SOURCE = ("Eastern time computed from the United States daylight "
                  "saving rule, because no time-zone database was found")


def _nth_sunday(year: int, month: int, nth: int) -> date:
    """The nth Sunday of a month."""
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)       # first Sunday
    return d + timedelta(days=7 * (nth - 1))


_EDT, _EST = timedelta(hours=-4), timedelta(hours=-5)


def _eastern_offset(utc: datetime) -> timedelta:
    """Eastern time's offset from UTC at a given INSTANT.

    Daylight saving begins at two in the morning local standard time on the
    second Sunday in March — 07:00 UTC — and ends at two in the morning local
    daylight time on the first Sunday in November, which is 06:00 UTC.
    """
    y = utc.year
    start = datetime.combine(_nth_sunday(y, 3, 2), dtime(7, 0))
    end = datetime.combine(_nth_sunday(y, 11, 1), dtime(6, 0))
    naive = utc.replace(tzinfo=None)
    return _EDT if start <= naive < end else _EST


class _EasternAt(tzinfo):
    """Eastern time at ONE instant, as a fixed offset.

    Deliberately fixed rather than a zone that transitions. A transitioning
    zone has to answer `utcoffset()` from a local wall reading, and one wall
    reading an hour before the November change is two different instants —
    01:30 daylight and 01:30 standard. Resolving that needs `fold`, whose
    handling has moved between Python versions, and a clock that stamps the
    wrong hour on a stored observation is not something to leave to the
    interpreter.

    The offset is decided once, from the instant, where there is no
    ambiguity at all. Nothing in this app does arithmetic across a change on
    one of these; it reads the hour, the date, and stamps them.
    """

    def __init__(self, offset: timedelta):
        self._offset = offset

    def utcoffset(self, dt):
        return self._offset

    def dst(self, dt):
        return timedelta(hours=1) if self._offset == _EDT else timedelta(0)

    def tzname(self, dt):
        return "EDT" if self._offset == _EDT else "EST"

    def __str__(self):
        return "America/New_York"

    def __repr__(self):                              # pragma: no cover
        return f"America/New_York ({self.tzname(None)}, computed)"

    def __eq__(self, other):                         # pragma: no cover
        return isinstance(other, _EasternAt) and other._offset == self._offset

    def __hash__(self):                              # pragma: no cover
        return hash(("eastern", self._offset))


def eastern(utc: datetime) -> datetime:
    """One UTC instant, read on the exchange's clock."""
    off = _eastern_offset(utc)
    return (utc.replace(tzinfo=None) + off).replace(tzinfo=_EasternAt(off))

MAX_DAILY_SYMBOLS = 60
# Chain capture is one network request per ticker per day against a
# rate-limited broker API, so it is bounded separately from the snapshot.
MAX_CAPTURE_SYMBOLS = 40
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


def market_now(now: datetime | None = None) -> datetime:
    """The time in New York, which is the only clock the market keeps.

    Never the container's clock. If the time-zone database is missing the
    offset is computed instead — see the note beside _MARKET_TZ. A naive
    `datetime.now()` here would be wrong by four or five hours and wrong
    silently, which on this app's data means wrong for good.
    """
    if now is not None:
        return now
    if _MARKET_TZ is not None:
        return datetime.now(_MARKET_TZ)
    return eastern(datetime.now(timezone.utc))


def market_clock() -> dict:
    """Which clock the market side of this app is actually reading.

    On screen, because the difference between the exchange's clock and the
    container's is four or five hours and every symptom of getting it wrong
    looks like something else.
    """
    now = market_now()
    return {"now": now.isoformat(timespec="seconds"),
            "pretty": now.strftime("%B %-d, %Y at %-I:%M %p"),
            "zone": str(getattr(now, "tzinfo", "") or "unknown"),
            "abbreviation": now.strftime("%Z") or "",
            "utc_offset_hours": (now.utcoffset().total_seconds() / 3600.0
                                 if now.utcoffset() else None),
            "source": _TZ_SOURCE,
            "database_found": _MARKET_TZ is not None,
            "capture_hour_et": _capture_hour(),
            "container_now": datetime.now().isoformat(timespec="seconds"),
            "note": ("Market scheduling reads this clock, never the "
                     "container's. A server in coordinated universal time is "
                     "already on tomorrow's date by eight in the evening in "
                     "New York, so a capture stamped with the container's "
                     "date would land on a trading day that has not "
                     "happened.")}


def _market_today() -> str:
    return market_now().date().isoformat()


def _capture_hour() -> int:
    try:
        return int((config()[0] or {}).get("capture_hour_et")
                   or RECORD_AFTER_ET_HOUR)
    except Exception:                                # pragma: no cover
        return RECORD_AFTER_ET_HOUR


def _after_capture_window() -> bool:
    """Is this capture happening later in the evening than it should?

    A capture that runs at eight because the app had just restarted is still
    today's market, and it is still worth taking — but it is stamped, so a
    reader can see that this day's quote was not taken at the close.
    """
    try:
        late_after = int((config()[0] or {}).get("capture_late_after_hours") or 2)
    except Exception:                                # pragma: no cover
        late_after = 2
    return market_now().hour >= _capture_hour() + late_after


def tick(now: datetime | None = None) -> dict | None:
    """One scheduler beat. Records at most once per trading day.

    Three things this has to get right, and did not before:

      * The clock is the exchange's, not the container's.
      * A weekend or a market holiday is not a capture day at all.
      * A restart after the capture window does not lose the day. Whether
        today has already been captured is read from the capture log rather
        than from a variable that dies with the process, so a container that
        comes back at eight in the evening still takes the day, and one that
        comes back after having already taken it does not take it twice.
    """
    now = market_now(now)
    today = now.date().isoformat()
    if now.hour < _capture_hour():
        return None
    if not _health.is_trading_day(today):
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
    # Write down what today is DUE before doing any of it. Judging a past day
    # against the watchlist as it stands later is not evidence about that
    # day: starring a ticker tomorrow would report today as having missed it,
    # and unstarring one would make a real miss disappear.
    try:
        _health.expect(expected_today(syms, today), day=today)
    except Exception:                                # noqa: BLE001
        pass
    # A restart in the same evening must not redo work that already
    # succeeded, and must do the work that did not.
    pending = [s for s in syms
               if not _health.captured(_health.SNAPSHOT, _safe(s), today)]
    out = record_daily(pending, day=today)
    out["already_captured"] = [s for s in syms if s not in pending]
    out["peer_index"] = warmed
    # The option chains for the followed names, captured after the close so
    # the covered-call simulator and the option backtests become real with
    # time. This cannot be caught up on later — a day that goes uncaptured
    # is gone — so it runs on the same beat as the snapshot.
    try:
        out["chains"] = capture_chains(syms[:MAX_CAPTURE_SYMBOLS])
    except Exception:                                # noqa: BLE001
        out["chains"] = None
    out["health"] = _raise_if_incomplete(syms, today)
    return out


def expected_today(symbols, day: str | None = None) -> dict:
    """Which tickers each kind of capture was due for on this day."""
    syms = [_safe(s) for s in (symbols or []) if _safe(s)]
    return {_health.SNAPSHOT: syms[:MAX_DAILY_SYMBOLS],
            _health.CHAIN: syms[:MAX_CAPTURE_SYMBOLS],
            _health.LEAPS: syms[:MAX_DAILY_SYMBOLS],
            _health.BENCHMARK: syms[:MAX_DAILY_SYMBOLS],
            _health.CONTRACT: syms[:MAX_DAILY_SYMBOLS]}


def _raise_if_incomplete(symbols, day: str) -> dict:
    """Say so, once, when a capture day did not finish.

    This reuses the app's existing push, if one is configured. It does not
    build a notification system of its own, and it does not send anything on
    a healthy day.
    """
    got = _health.health(expected_today(symbols, day), days_back=1, today=day)
    if got.get("alert") and _ALERT_FN is not None:
        try:
            _ALERT_FN("Investment data capture", got["alert"])
            got["alerted"] = True
        except Exception:                            # noqa: BLE001
            got["alerted"] = False
    return got
