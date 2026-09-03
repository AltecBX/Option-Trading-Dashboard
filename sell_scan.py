"""sell_scan.py — Best Sales Today: the market-wide answer (v4.80).

Which option is the best one to sell right now, across every name the app
can see, in the selling mode the user chose — and why, and what was
refused, and whether the honest answer is NO TRADE.

The funnel is the one the app already pays for:

  Stage 1  free   — the watchlist board + the Premium Edge observation
                    store rank the names most likely to carry real premium
                    (edge_scan._stage1_candidates, both slates).
  Stage 2  chains — Premium Edge fetches ONE bounded chain per name; this
                    module registers as a chain consumer and evaluates every
                    short-premium structure on that same chain. No second
                    fetch, no second budget.
  Stage 3  free   — sp_evidence builds (and caches for a day) the ticker's
                    measured breach history; sp_engine runs the gates and
                    the ranking in every mode.
  Stage 4  free   — the finalists get the early-profit path simulation,
                    the plain-English defence, and the risk pathway.

Every recommendation the board shows is written to the forward-test store
(<data>/sell/predictions/YYYY-MM-DD.jsonl) with its probabilities, its
config hash and its engine version, so sp_forward can grade it after
expiry and the calibration tables can be built from what the app actually
said, not from what it might have said.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import sp_engine as E
import sp_evidence as ev

SELL_SCAN_VERSION = "sell-scan-1.0.0"

_DATA_DIR: Path | None = None
_BOARD_FN = None
_MARKET_OPEN_FN = None
_STATUS_FN = None
_SECTOR_FN = None
_SPY_REGIME_FN = None
_NOW_FN = None
_LOCK = threading.RLock()
_STATE: dict = {"symbols": {}, "as_of": None, "error": None, "last_pass_ts": None,
                "recorded": {}}
_EVIDENCE_MEM: dict = {}          # sym -> (date_iso, table)
MODES_ALL = tuple(E.MODES) + ("event",)


def configure(data_dir=None, board_getter=None, market_open_fn=None, status_fn=None,
              sector_fn=None, spy_regime_fn=None, now_fn=None) -> None:
    """Wire providers. `status_fn()` → schwab_client.status() dict (for the
    serving flag); `sector_fn(sym)` → sector label or None; `spy_regime_fn()`
    → {"regime": "long"|"short"} or None. Registers this module as a chain
    consumer of edge_scan when that module is importable."""
    global _DATA_DIR, _BOARD_FN, _MARKET_OPEN_FN, _STATUS_FN, _SECTOR_FN, _SPY_REGIME_FN, _NOW_FN
    _DATA_DIR = Path(data_dir) if data_dir else None
    _BOARD_FN, _MARKET_OPEN_FN, _STATUS_FN = board_getter, market_open_fn, status_fn
    _SECTOR_FN, _SPY_REGIME_FN, _NOW_FN = sector_fn, spy_regime_fn, now_fn
    if _DATA_DIR is not None:
        for sub in ("evidence", "predictions"):
            try:
                (_DATA_DIR / "sell" / sub).mkdir(parents=True, exist_ok=True)
            except Exception:  # noqa: BLE001
                pass
    try:
        import edge_scan as _es
        _es.register_chain_consumer(on_chain)
    except Exception:  # noqa: BLE001
        pass
    _load_board()


def _today() -> date:
    if _NOW_FN:
        try:
            n = _NOW_FN()
            return n.date() if hasattr(n, "date") else n
        except Exception:  # noqa: BLE001
            pass
    return date.today()


# ── evidence cache ───────────────────────────────────────────────────────────
def _evidence_path(sym: str) -> Path | None:
    return None if _DATA_DIR is None else _DATA_DIR / "sell" / "evidence" / f"{sym}.json"


def evidence_table(sym: str, bars: list, cfg: dict) -> dict:
    """The ticker's measured breach table, computed at most once per
    `scan.evidence_cache_hours` (memory first, then disk)."""
    hours = float((cfg.get("scan") or {}).get("evidence_cache_hours", 20))
    today = _today().isoformat()
    hit = _EVIDENCE_MEM.get(sym)
    if hit and hit[0] == today:
        return hit[1]
    p = _evidence_path(sym)
    if p is not None and p.exists():
        try:
            disk = json.loads(p.read_text())
            age_h = (time.time() - float(disk.get("_ts") or 0)) / 3600.0
            if age_h < hours and disk.get("n_bars") == len(bars):
                tab = disk["table"]
                # JSON turns int/float keys into strings; restore the shape
                tab["cells"] = {st: {int(h): {float(k): v for k, v in ks.items()}
                                     for h, ks in hs.items()} for st, hs in tab["cells"].items()}
                _EVIDENCE_MEM[sym] = (today, tab)
                return tab
        except Exception:  # noqa: BLE001
            pass
    tab = ev.breach_table(bars)
    _EVIDENCE_MEM[sym] = (today, tab)
    if p is not None:
        try:
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"_ts": time.time(), "n_bars": len(bars), "table": tab},
                                      separators=(",", ":")))
            tmp.replace(p)
        except Exception:  # noqa: BLE001
            pass
    return tab


# ── the chain consumer ───────────────────────────────────────────────────────
def _compact(c: dict) -> dict:
    """Drop the raw contract_economics duplicates before storing; every
    field the explanations and the UI read stays at the top level."""
    out = {k: v for k, v in c.items() if k not in ("short", "short_call_leg", "context")}
    return out


def _context_for(sym: str, chain: dict, bars: list, context: dict) -> dict:
    today = context.get("today") or _today().isoformat()
    row = {}
    try:
        board = _BOARD_FN() if _BOARD_FN else {}
        for r in (board or {}).get("rows") or []:
            if r.get("symbol") == sym:
                row = r
                break
    except Exception:  # noqa: BLE001
        row = {}
    status = {}
    try:
        status = _STATUS_FN() if _STATUS_FN else {}
    except Exception:  # noqa: BLE001
        status = {}
    regime = None
    try:
        regime = ((_SPY_REGIME_FN() or {}).get("regime")) if _SPY_REGIME_FN else None
    except Exception:  # noqa: BLE001
        regime = None
    earn = context.get("earnings_date")
    earn_days = None
    if earn:
        try:
            earn_days = (date.fromisoformat(str(earn)[:10]) - date.fromisoformat(today)).days
        except Exception:  # noqa: BLE001
            earn_days = None
    last_bar = (bars[-1].get("date") or "")[:10] if bars else None
    bars_age = None
    if last_bar:
        try:
            bars_age = (date.fromisoformat(today) - date.fromisoformat(last_bar)).days
        except Exception:  # noqa: BLE001
            bars_age = None
    ages = []
    for exp, sides in (chain.get("chains") or {}).items():
        for r in (sides.get("puts") or [])[:80] + (sides.get("calls") or [])[:80]:
            a = r.get("quote_age_s")
            if a is not None:
                ages.append(a)
    spot = context.get("spot") or (chain.get("underlying") or {}).get("last") or 0.0
    avg_vol = row.get("avg_volume") or row.get("volume")
    vrp = context.get("vrp") or {}
    hist = context.get("hist") or {}
    return {
        "today": today, "source": context.get("source") or chain.get("source") or "unknown",
        "delayed": (context.get("source") or chain.get("source")) not in ("schwab", None),
        "provider_serving": status.get("serving"),
        "market_open": bool(context.get("market_open")),
        "chain_ts": datetime.now().replace(microsecond=0).isoformat(),
        "chain_age_s": (min(ages) if ages else None),
        "bars_last": last_bar, "bars_age_days": bars_age,
        "earnings_date": earn, "earnings_in_days": earn_days,
        "macro_events": context.get("macro") or [],
        "ex_div_date": None,
        "vrp_ratio": vrp.get("vrp_ratio"), "vrp_points": vrp.get("vrp_points"),
        "vrp_percentile": hist.get("percentile"),
        "iv30": (context.get("iv30") or {}).get("iv30"),
        "erv30": (context.get("erv") or {}).get("erv30"),
        "underlying_dollar_volume": (float(avg_vol) * float(spot)) if (avg_vol and spot) else None,
        "bar_features": context.get("bar_features") or {},
        "term_shape": (context.get("term") or {}).get("shape"),
        "vix_percentile": context.get("vix_percentile"),
        "rr25_volpts": (context.get("skew") or {}).get("rr25_volpts"),
        "market_regime": regime,
        "danger": (context.get("danger") or {}).get("label"),
        "sector": row.get("sector"),
    }


def on_chain(sym: str, chain: dict, bars: list, context: dict) -> dict | None:
    """Evaluate every short-premium structure on a chain Premium Edge just
    fetched, in every mode, and store the result for the board."""
    cfg, cfg_hash = E.config()
    if not chain or not chain.get("chains") or not bars:
        return None
    now = context.get("now") or _today()
    if isinstance(now, str):
        now = date.fromisoformat(now[:10])
    tab = evidence_table(sym, bars, cfg)
    state = ev.current_state(bars)
    ctx = _context_for(sym, chain, bars, context)
    cands = E.build_candidates(sym, chain, bars, now, cfg, evidence_table=tab,
                               state=state["states"][-1])
    per_mode = {}
    for mode in MODES_ALL:
        event_mode = mode == "event"
        m = "balanced" if event_mode else mode
        evald = [E.evaluate(c, ctx, cfg, mode=m, event_mode=event_mode) for c in cands]
        qualified = [_compact(c) for c in evald if c.get("verdict") == "qualified"]
        rejected = [c for c in evald if c.get("verdict") != "qualified"]
        per_mode[mode] = {
            "qualified": qualified,
            "rejection_summary": E.rejection_summary(rejected),
            "n_candidates": len(evald), "n_qualified": len(qualified),
            "insufficient": sum(1 for c in rejected if c.get("verdict") == "insufficient"),
        }
    entry = {"symbol": sym, "as_of": datetime.now().replace(microsecond=0).isoformat(),
             "spot": ctx.get("spot") or (chain.get("underlying") or {}).get("last"),
             "ctx": ctx, "state": state, "modes": per_mode, "config_hash": cfg_hash,
             "engine": E.SP_ENGINE_VERSION, "n_candidates": len(cands),
             "evidence_n_bars": tab.get("n_bars")}
    with _LOCK:
        _STATE["symbols"][sym] = entry
        _STATE["as_of"] = entry["as_of"]
        _STATE["last_pass_ts"] = time.time()
    _persist_board()
    return entry


# ── persistence ─────────────────────────────────────────────────────────────
def _board_path() -> Path | None:
    return None if _DATA_DIR is None else _DATA_DIR / "sell" / "board.json"


def _persist_board() -> None:
    p = _board_path()
    if p is None:
        return
    try:
        with _LOCK:
            data = {"symbols": _STATE["symbols"], "as_of": _STATE["as_of"],
                    "version": SELL_SCAN_VERSION}
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":"), default=str))
        tmp.replace(p)
    except Exception as exc:  # noqa: BLE001
        print(f"sell_scan: board persist failed: {exc}")


def _load_board() -> None:
    p = _board_path()
    if p is None or not p.exists():
        return
    try:
        data = json.loads(p.read_text())
        with _LOCK:
            if not _STATE["symbols"]:
                _STATE["symbols"] = data.get("symbols") or {}
                _STATE["as_of"] = data.get("as_of")
    except Exception:  # noqa: BLE001
        pass


# ── the board ────────────────────────────────────────────────────────────────
def _age_hours(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return round((datetime.now() - datetime.fromisoformat(iso)).total_seconds() / 3600.0, 2)
    except Exception:  # noqa: BLE001
        return None


def snapshot(mode: str = "balanced", strategy: str | None = None, top_n: int | None = None,
             record: bool = True) -> dict:
    """Best Sales Today for one mode: the ranked qualified contracts across
    every scanned symbol, the defence of #1, the risk pathway of the
    finalists, why the rest failed, and NO TRADE when nothing qualifies."""
    cfg, cfg_hash = E.config()
    mode = mode if mode in MODES_ALL else "balanced"
    top_n = int(top_n or (cfg.get("scan") or {}).get("top_n", 25))
    with _LOCK:
        symbols = dict(_STATE["symbols"])
        as_of = _STATE["as_of"]
    pool, rejections, per_symbol = [], [], []
    for sym, entry in symbols.items():
        pm = (entry.get("modes") or {}).get(mode) or {}
        q = pm.get("qualified") or []
        if strategy:
            q = [c for c in q if c.get("strategy") == strategy]
        for c in q:
            c2 = dict(c)
            c2["symbol_as_of"] = entry.get("as_of")
            c2["sector"] = (entry.get("ctx") or {}).get("sector")
            pool.append(c2)
        per_symbol.append({"symbol": sym, "as_of": entry.get("as_of"), "spot": entry.get("spot"),
                           "n_candidates": pm.get("n_candidates"), "n_qualified": len(q),
                           "insufficient": pm.get("insufficient"),
                           "source": (entry.get("ctx") or {}).get("source"),
                           "provider_serving": (entry.get("ctx") or {}).get("provider_serving")})
        for g in pm.get("rejection_summary") or []:
            rejections.append({**g, "symbol": sym})
    ranked = E.rank(pool)
    E.attach_paths(ranked, top_n=min(10, top_n))
    top = ranked[:top_n]
    # collapse rejections across symbols by (gate, reason)
    agg: dict = {}
    for g in rejections:
        key = (g["gate"], g["reason"])
        a = agg.setdefault(key, {"gate": g["gate"], "reason": g["reason"], "n": 0, "symbols": set()})
        a["n"] += g.get("n", 1)
        a["symbols"].add(g["symbol"])
    rej_out = sorted([{**a, "symbols": sorted(a["symbols"])} for a in agg.values()], key=lambda a: -a["n"])
    explain = None
    pathways = {}
    if top:
        t0 = top[0]
        ctx0 = (symbols.get(t0["symbol"]) or {}).get("ctx") or {}
        explain = E.explain(t0, top[1] if len(top) > 1 else None, ctx0)
        for c in top[:5]:
            pathways[f"{c['symbol']}|{c['strategy']}|{c['expiration']}|{c['short_strike']}"] = E.risk_pathway(c, cfg)
    sector_of = {s: (e.get("ctx") or {}).get("sector") for s, e in symbols.items()}
    if _SECTOR_FN:
        for s in symbols:
            if not sector_of.get(s):
                try:
                    sector_of[s] = _SECTOR_FN(s)
                except Exception:  # noqa: BLE001
                    pass
    conc = E.portfolio_concentration(top, sector_of)
    scanning = False
    try:
        import edge_scan as _es
        scanning = bool(_es.snapshot().get("scanning"))
    except Exception:  # noqa: BLE001
        pass
    out = {
        "ok": True, "version": SELL_SCAN_VERSION, "engine": E.SP_ENGINE_VERSION,
        "config_hash": cfg_hash, "mode": mode, "strategy": strategy,
        "objective": (cfg["modes"].get("balanced" if mode == "event" else mode) or {}).get("objective"),
        "as_of": as_of, "age_hours": _age_hours(as_of), "scanning": scanning,
        "no_trade": len(top) == 0,
        "no_trade_reason": (None if top else (
            "No symbol scanned yet — run the Premium Edge scan." if not symbols else
            f"Nothing qualifies in {mode} mode across {len(symbols)} scanned names: every candidate "
            f"failed at least one gate (see why_others_failed). NO TRADE is the answer.")),
        "n_symbols": len(symbols), "n_qualified": len(ranked), "shown": len(top),
        "rows": [_row(c) for c in top],
        "top_detail": top[:5],
        "why_number_one": explain,
        "risk_pathways": pathways,
        "why_others_failed": rej_out[:40],
        "per_symbol": sorted(per_symbol, key=lambda r: -(r["n_qualified"] or 0)),
        "portfolio": conc,
        "modes": list(MODES_ALL),
        "strategies": list(E.STRATEGIES),
    }
    if record and top:
        _record_predictions(top, mode, cfg_hash)
    return out


def _row(c: dict) -> dict:
    """The table row: every decision-relevant field, flat."""
    pb = c.get("probability") or {}
    q = c["quote"] if c["legs"] < 4 else c["quote"]["put"]
    prov = ((c.get("gates") or {}).get("data") or {}).get("provenance") or {}
    ev_ = (c.get("gates") or {}).get("events", {}).get("details") or {}
    return {
        "rank": c.get("rank"), "symbol": c["symbol"], "spot": c["spot"],
        "strategy": c["strategy"], "side": c["side"],
        "expiration": c["expiration"], "dte": c["dte"], "dte_bucket": c["dte_bucket"],
        "short_strike": c["short_strike"], "long_strike": c.get("long_strike"),
        "short_call": c.get("short_call"), "long_call": c.get("long_call"), "width": c.get("width"),
        "dist_pct": c.get("dist_pct"), "expected_moves_out": c.get("expected_moves_out"),
        "k_sigma": c.get("k_sigma"), "delta": c.get("delta"),
        "credit": c.get("credit"), "net_credit": c.get("net_credit"), "credit_basis": c.get("credit_basis"),
        "bid": q.get("bid"), "ask": q.get("ask"), "spread_pct": q.get("spread_pct"),
        "oi": q.get("oi"), "volume": q.get("volume"),
        "p0_model": pb.get("p0_model"), "p0_conservative": pb.get("p0_conservative"),
        "p0_measured": pb.get("p0_measured"), "p0_grade": pb.get("grade"),
        "p_touch": pb.get("p_touch"), "p_touch_measured": pb.get("p_touch_measured"),
        "p_profit": pb.get("p_profit"), "n_eff": pb.get("n_eff"), "weight_own": pb.get("weight_own"),
        "p_hit_50": ((pb.get("paths") or {}).get("targets") or {}).get("50", {}).get("p_hit"),
        "days_to_50": ((pb.get("paths") or {}).get("targets") or {}).get("50", {}).get("expected_days_if_hit"),
        "ev_per_contract": c.get("ev_per_contract"), "ev_per_share": c.get("ev_per_share"),
        "es95_per_share": c.get("es95_per_share"), "es99_per_share": c.get("es99_per_share"),
        "max_loss_per_share": c.get("max_loss_per_share"), "defined_risk": c.get("defined_risk"),
        "capital": c.get("capital"), "roc_pct": c.get("roc_pct"),
        "annualized_roc_pct": c.get("annualized_roc_pct"),
        "ev_per_tail": (round(c["ev_per_share"] / c["es95_per_share"], 3)
                        if c.get("es95_per_share") and c.get("ev_per_share") is not None else None),
        "iv": q.get("iv"), "sigma_h": c.get("sigma_h"),
        "vrp_ratio": ((c.get("gates") or {}).get("edge", {}).get("details") or {}).get("vrp_ratio"),
        "earnings_date": ev_.get("earnings_date"), "earnings_in_days": ev_.get("earnings_in_days"),
        "ex_div_warning": bool(ev_.get("ex_div_inside")) or (c["side"] in ("call", "both") and ev_.get("ex_div_date") is None),
        "macro_inside": len(ev_.get("macro_inside") or []),
        "sell_quality": (c.get("sell_quality") or {}).get("score"),
        "objective_value": c.get("objective_value"),
        "confidence": pb.get("grade"), "data_source": prov.get("source"),
        "greeks": prov.get("greeks"), "quote_age_s": prov.get("quote_age_s"),
        "data_ts": c.get("symbol_as_of"), "sector": c.get("sector"),
        "mode": c.get("mode"), "config_hash": c.get("config_hash"),
    }


def detail(sym: str, mode: str = "balanced") -> dict:
    """One symbol's full evaluation in a mode: its qualified contracts in
    rank order, the defence of its best, and every rejection."""
    sym = (sym or "").upper()
    mode = mode if mode in MODES_ALL else "balanced"
    with _LOCK:
        entry = _STATE["symbols"].get(sym)
    if not entry:
        return {"ok": False, "symbol": sym, "error": "not scanned yet — it is not in the Premium Edge "
                                                    "funnel's current slate, or the scan has not run"}
    pm = (entry.get("modes") or {}).get(mode) or {}
    ranked = E.rank(list(pm.get("qualified") or []))
    E.attach_paths(ranked, top_n=5)
    return {"ok": True, "symbol": sym, "mode": mode, "as_of": entry.get("as_of"), "spot": entry.get("spot"),
            "ctx": entry.get("ctx"), "state": entry.get("state"),
            "rows": [_row(c) for c in ranked], "top_detail": ranked[:5],
            "why_number_one": (E.explain(ranked[0], ranked[1] if len(ranked) > 1 else None, entry.get("ctx"))
                               if ranked else None),
            "risk_pathway": (E.risk_pathway(ranked[0]) if ranked else None),
            "why_others_failed": pm.get("rejection_summary") or [],
            "n_candidates": pm.get("n_candidates"), "config_hash": entry.get("config_hash")}


# ── the audit trail ─────────────────────────────────────────────────────────
def _pred_path(day: str) -> Path | None:
    return None if _DATA_DIR is None else _DATA_DIR / "sell" / "predictions" / f"{day}.jsonl"


def prediction_key(c: dict, mode: str) -> str:
    return f"{c['symbol']}|{c['strategy']}|{c['expiration']}|{c['short_strike']}|{c.get('long_strike')}|{mode}"


def _record_predictions(top: list[dict], mode: str, cfg_hash: str) -> int:
    """Append today's shown recommendations to the forward-test store, once
    each per day. Everything sp_forward needs to grade it later is on the
    line: the contract, the quotes, the probabilities, the tail, the score,
    the model version and the config hash."""
    day = _today().isoformat()
    p = _pred_path(day)
    if p is None:
        return 0
    with _LOCK:
        seen = _STATE["recorded"].setdefault(day, set())
        if len(_STATE["recorded"]) > 3:
            for k in sorted(_STATE["recorded"])[:-3]:
                _STATE["recorded"].pop(k, None)
    n = 0
    lines = []
    for c in top:
        key = prediction_key(c, mode)
        if key in seen:
            continue
        pb = c.get("probability") or {}
        q = c["quote"] if c["legs"] < 4 else c["quote"]["put"]
        rec = {
            "recorded_at": datetime.now().replace(microsecond=0).isoformat(), "day": day,
            "key": key, "mode": mode, "rank": c.get("rank"),
            "symbol": c["symbol"], "spot": c["spot"], "strategy": c["strategy"], "side": c["side"],
            "expiration": c["expiration"], "dte": c["dte"], "dte_bucket": c["dte_bucket"],
            "short_strike": c["short_strike"], "long_strike": c.get("long_strike"),
            "short_call": c.get("short_call"), "long_call": c.get("long_call"), "width": c.get("width"),
            "bid": q.get("bid"), "ask": q.get("ask"), "iv": q.get("iv"), "delta": c.get("delta"),
            "credit": c.get("credit"), "net_credit": c.get("net_credit"),
            "sigma_h": c.get("sigma_h"), "k_sigma": c.get("k_sigma"),
            "p0_model": pb.get("p0_model"), "p0_model_raw": pb.get("p0_model_raw"),
            "p0_measured": pb.get("p0_measured"), "p0_conservative": pb.get("p0_conservative"),
            "p_touch": pb.get("p_touch"), "p_touch_measured": pb.get("p_touch_measured"),
            "p_profit": pb.get("p_profit"), "n_eff": pb.get("n_eff"), "weight_own": pb.get("weight_own"),
            "p_hit_50": ((pb.get("paths") or {}).get("targets") or {}).get("50", {}).get("p_hit"),
            "p_hit_75": ((pb.get("paths") or {}).get("targets") or {}).get("75", {}).get("p_hit"),
            "p_hit_90": ((pb.get("paths") or {}).get("targets") or {}).get("90", {}).get("p_hit"),
            "ev_per_contract": c.get("ev_per_contract"), "es95_per_share": c.get("es95_per_share"),
            "es99_per_share": c.get("es99_per_share"), "max_loss_per_share": c.get("max_loss_per_share"),
            "capital": c.get("capital"), "roc_pct": c.get("roc_pct"),
            "sell_quality": (c.get("sell_quality") or {}).get("score"),
            "objective": c.get("objective"), "objective_value": c.get("objective_value"),
            "vrp_ratio": ((c.get("gates") or {}).get("edge", {}).get("details") or {}).get("vrp_ratio"),
            "iv30": None, "erv30": None,
            "engine": E.SP_ENGINE_VERSION, "scan": SELL_SCAN_VERSION, "config_hash": cfg_hash,
            "reasons": (c.get("sell_quality") or {}).get("breakdown"),
            "risks": ((c.get("gates") or {}).get("tail", {}).get("details") or {}).get("notes"),
            "market_open": ((c.get("gates") or {}).get("data") or {}).get("provenance", {}).get("market_open"),
            "source": ((c.get("gates") or {}).get("data") or {}).get("provenance", {}).get("source"),
        }
        lines.append(json.dumps(rec, separators=(",", ":"), default=str))
        seen.add(key)
        n += 1
    if lines:
        try:
            with open(p, "a") as fh:
                fh.write("\n".join(lines) + "\n")
        except Exception as exc:  # noqa: BLE001
            print(f"sell_scan: prediction append failed: {exc}")
    return n


def predictions(days: int = 120) -> list[dict]:
    """Every recorded recommendation in the last `days`, oldest first."""
    if _DATA_DIR is None:
        return []
    out = []
    d0 = _today() - timedelta(days=int(days))
    folder = _DATA_DIR / "sell" / "predictions"
    if not folder.exists():
        return []
    for p in sorted(folder.glob("*.jsonl")):
        try:
            if date.fromisoformat(p.stem) < d0:
                continue
        except Exception:  # noqa: BLE001
            continue
        try:
            for line in p.read_text().splitlines():
                if line.strip():
                    out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


def status() -> dict:
    with _LOCK:
        return {"version": SELL_SCAN_VERSION, "engine": E.SP_ENGINE_VERSION,
                "n_symbols": len(_STATE["symbols"]), "as_of": _STATE["as_of"],
                "age_hours": _age_hours(_STATE["as_of"]), "error": _STATE["error"],
                "symbols": sorted(_STATE["symbols"].keys())}
