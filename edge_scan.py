"""edge_scan.py — Premium Edge scanner: discovery funnel, persistence,
breach history, EM calibration, VRP backtest glue, and sizing.

The stateful half of the Premium Edge engine (premium_edge.py is the pure
math). Three-stage funnel so the app never sprays chain calls:

  Stage 1 (free): rank the watchlist_table board on features it already
          carries (price/mcap/volume gates, realized-vol rank, earnings
          proximity, day move) — zero API calls.
  Stage 2 (budgeted): for the top stage2_n names, one chain call
          (95 days out — term structure + skew + IV30 + every candidate
          contract ride in that single payload) + daily-bars history
          (cached 10 min; the ExpectedRV30 model choice is cached a week).
  Stage 3 (free): full contract/structure analysis on the deep_n best.

Every chain call defers politely when app-wide Schwab usage exceeds the
configured budget (shared 110/min cap — the juice scanner, the 0DTE tape
and normal browsing spend from the same pool).

Persistence (<data>/edge/): board.json (last scan, survives restart),
obs/SYM.json (daily §20 observations — written by premium_edge), and
fcast/SYM.json (forecast model choice + walk-forward scores + a rolling
ledger of live forecast errors, so the model's real-world accuracy is
inspectable per ticker).

Honesty labels ride everywhere: probabilities are "model", breach
frequencies are MEASURED, backtest IV is "measured (n=…)" only when real
stored IV covered the period, else "modeled" via bt_iv.
"""

from __future__ import annotations

import json
import math
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import bt_iv
import bt_options
import bt_validate as bv
import premium_edge as pe
import vol_forecast as vf

_DATA_DIR: Path | None = None
_SCHWAB = None                 # callable -> SchwabClient | None
_BOARD_FN = None               # callable -> watchlist_table board dict
_EARNINGS_FN = None            # callable(sym) -> {"next": iso|None, "past": [iso]}
_EARN_MOVES_FN = None          # callable(sym) -> {"avg_abs": float, "n": int} | None
_MACRO_FN = None               # callable -> [{"kind","date"}] upcoming macro events
_VIX_FN = None                 # callable -> {date_iso: close}
_IV_APPEND_FN = None           # callable(sym, iv_decimal) -> None (legacy iv_history)
_MARKET_OPEN_FN = None         # callable(now=None) -> bool
_ET_TZ = None

_LOCK = threading.Lock()
_STATE = {"rows": [], "as_of": None, "scanning": False, "error": None,
          "universe": 0, "scanned": 0, "thread": None, "last_scan_ts": 0.0,
          "eod_recorded_for": None}
_DETAIL_CACHE: dict = {}       # sym|intent -> (ts, payload)
_DETAIL_TTL = 60.0
_BT_JOBS: dict = {}            # job_id -> {"status", "result", ...}


def configure(schwab_getter, board_getter, earnings_fn=None, earn_moves_fn=None,
              macro_fn=None, vix_fn=None, iv_append_fn=None,
              market_open_fn=None, data_dir=None, et_tz=None) -> None:
    global _SCHWAB, _BOARD_FN, _EARNINGS_FN, _EARN_MOVES_FN, _MACRO_FN
    global _VIX_FN, _IV_APPEND_FN, _MARKET_OPEN_FN, _DATA_DIR, _ET_TZ
    _SCHWAB, _BOARD_FN = schwab_getter, board_getter
    _EARNINGS_FN, _EARN_MOVES_FN = earnings_fn, earn_moves_fn
    _MACRO_FN, _VIX_FN, _IV_APPEND_FN = macro_fn, vix_fn, iv_append_fn
    _MARKET_OPEN_FN, _ET_TZ = market_open_fn, et_tz
    _DATA_DIR = Path(data_dir) if data_dir else None
    if _DATA_DIR is not None:
        try:
            (_DATA_DIR / "edge" / "fcast").mkdir(parents=True, exist_ok=True)
        except Exception as exc:      # noqa: BLE001
            print(f"edge_scan: cannot create data dir: {exc}")
    pe.configure(data_dir)


def _now_et() -> datetime:
    return datetime.now(_ET_TZ) if _ET_TZ else datetime.now()


def _market_open() -> bool:
    try:
        return bool(_MARKET_OPEN_FN()) if _MARKET_OPEN_FN else False
    except Exception:
        return False


def _cfg() -> dict:
    c, _h = pe.config()
    return c


# ── forecast cache (choice weekly, ERV daily, live error ledger) ────────────

def _fcast_path(sym: str) -> Path | None:
    if _DATA_DIR is None:
        return None
    safe = "".join(c for c in sym.upper() if c.isalnum() or c in "-_.")
    return _DATA_DIR / "edge" / "fcast" / f"{safe}.json"


def _load_fcast(sym: str) -> dict:
    p = _fcast_path(sym)
    if p is None or not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_fcast(sym: str, data: dict) -> None:
    p = _fcast_path(sym)
    if p is None:
        return
    try:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        tmp.replace(p)
    except Exception as exc:          # noqa: BLE001
        print(f"edge_scan: fcast write failed for {sym}: {exc}")


def forecast_for(sym: str, bars: list, cfg: dict, today: str,
                 earnings_within: bool = False, earn_avg: float | None = None,
                 earn_n: int = 0) -> dict | None:
    """ExpectedRV30 with the per-ticker validated model choice. The
    (expensive) walk-forward choice is cached 7 days; the forecast value is
    recomputed on every call from current bars."""
    fc = cfg.get("forecast", {})
    cache = _load_fcast(sym)
    choice = cache.get("choice")
    if not choice or (cache.get("choice_date", "") < (date.fromisoformat(today)
                      - timedelta(days=7)).isoformat()):
        choice = vf.choose_model(bars, fc)
        cache["choice"] = {"model": choice["model"], "method": choice["method"]}
        cache["choice_date"] = today
        cache["wf_scores"] = choice.get("scores")
        _save_fcast(sym, cache)
        choice = cache["choice"]
    return vf.expected_rv30(bars, fc, choice=choice,
                            earnings_within_horizon=earnings_within,
                            earnings_hist_avg_abs_pct=earn_avg,
                            earnings_hist_n=earn_n)


def record_forecast_error(sym: str, bars: list, today: str) -> None:
    """Live error ledger: find the observation ~1 horizon ago, compare its
    stored erv30 with the realized vol since, keep the last 200 errors.
    This is the ongoing proof (or indictment) of the forecaster per ticker."""
    obs = pe.load_observations(sym)
    if not obs:
        return
    target_day = (date.fromisoformat(today) - timedelta(days=30)).isoformat()
    past = [o for o in obs if o.get("date", "") <= target_day and o.get("erv30")]
    if not past:
        return
    o = past[-1]
    closes = [float(b.get("close") or 0) for b in bars
              if (b.get("date") or "")[:10] > o["date"]]
    if len(closes) < 15:
        return
    realized = vf.rv(closes, min(21, len(closes) - 1))
    if realized is None:
        return
    cache = _load_fcast(sym)
    ledger = cache.get("errors", [])
    if any(e.get("fcast_date") == o["date"] for e in ledger):
        return
    ledger.append({"fcast_date": o["date"], "erv30": o["erv30"],
                   "rv_realized": round(realized, 4),
                   "err_volpts": round((o["erv30"] - realized) * 100.0, 2)})
    cache["errors"] = ledger[-200:]
    _save_fcast(sym, cache)


# ── danger features from bars ───────────────────────────────────────────────

def _bar_features(bars: list) -> dict:
    closes = [float(b.get("close") or 0) for b in bars]
    rv5, rv20 = vf.rv(closes, 5), vf.rv(closes, 20)
    gaps = vf.gap_stats(bars, 63) or {}
    ret20 = None
    if len(closes) >= 21 and closes[-21] > 0:
        ret20 = (closes[-1] / closes[-21] - 1.0) * 100.0
    return {
        "rv5": round(rv5, 4) if rv5 else None,
        "rv20": round(rv20, 4) if rv20 else None,
        "rv5_over_rv20": round(rv5 / rv20, 2) if rv5 and rv20 else None,
        "gap_freq_2pct": gaps.get("gap_freq_2pct"),
        "max_gap_pct": gaps.get("max_gap_pct"),
        "ret20_pct": round(ret20, 1) if ret20 is not None else None,
        "atr_pct": vf.atr_pct(bars, 14),
    }


def _vix_percentile() -> float | None:
    if _VIX_FN is None:
        return None
    try:
        series = _VIX_FN() or {}
        vals = [v for _d, v in sorted(series.items())][-252:]
        if len(vals) < 60:
            return None
        cur = vals[-1]
        return round(sum(1 for v in vals if v < cur) / len(vals) * 100.0, 1)
    except Exception:
        return None


def _macro_events_within(days: int, now: date) -> list:
    if _MACRO_FN is None:
        return []
    try:
        out = []
        for ev in _MACRO_FN() or []:
            d = ev.get("date", "")
            if d and now.isoformat() <= d <= (now + timedelta(days=days)).isoformat():
                out.append(ev)
        return out
    except Exception:
        return []


# ── per-symbol analysis (Stage 2+3) ─────────────────────────────────────────

def analyze_symbol(sym: str, intent: str = "premium_only", record: bool = False,
                   now: date | None = None) -> dict | None:
    """Full Premium Edge analysis for one symbol from live data. Returns the
    scanner row + detail payload, or an honest failure dict."""
    cfg = _cfg()
    sc = _SCHWAB() if _SCHWAB else None
    if sc is None:
        return {"symbol": sym, "error": "schwab unavailable", "data_ok": False}
    now = now or _now_et().date()
    today = now.isoformat()
    sn = cfg.get("scan", {})
    bars = sc.get_price_history(sym, days=int(cfg.get("forecast", {}).get("history_days_fetch", 900)))
    if not bars or len(bars) < int(cfg.get("forecast", {}).get("min_history_bars", 120)):
        return {"symbol": sym, "error": "insufficient price history", "data_ok": False}
    to_date = (now + timedelta(days=int(sn.get("chain_days_out", 95)))).isoformat()
    chain = sc.get_option_chain(sym, to_date=to_date,
                                strike_count=int(sn.get("chain_strike_count", 50)))
    if not chain or not chain.get("chains"):
        return {"symbol": sym, "error": "no option chain", "data_ok": False}
    spot = (chain.get("underlying") or {}).get("last") or 0.0
    is_open = _market_open()

    # earnings context
    earn_next, earn_avg, earn_n = None, None, 0
    try:
        if _EARNINGS_FN:
            e = _EARNINGS_FN(sym) or {}
            earn_next = e.get("next")
    except Exception:
        pass
    try:
        if _EARN_MOVES_FN:
            m = _EARN_MOVES_FN(sym) or {}
            earn_avg, earn_n = m.get("avg_abs"), int(m.get("n") or 0)
    except Exception:
        pass
    horizon_end = (now + timedelta(days=30)).isoformat()
    earnings_within = bool(earn_next and today <= earn_next <= horizon_end)

    erv_pack = forecast_for(sym, bars, cfg, today, earnings_within, earn_avg, earn_n)
    if erv_pack is None:
        return {"symbol": sym, "error": "forecast needs more history", "data_ok": False}

    iv = pe.iv30(chain, now, cfg, market_open=is_open)
    if iv is None:
        return {"symbol": sym, "error": "no usable IV quotes (spreads/staleness)",
                "data_ok": False}
    term = pe.term_structure(chain, now, cfg, earnings_date=earn_next, market_open=is_open)
    sk = pe.skew(chain, now, cfg, market_open=is_open)
    vrp = pe.vrp_block(iv["iv30"], erv_pack)
    macro = _macro_events_within(int(cfg.get("event", {}).get("macro_window_days", 30)), now)
    cls = pe.classify_premium(vrp, earnings_within, macro, cfg)
    hist = pe.vrp_stats(pe.load_observations(sym), vrp["vrp_points"], cfg)
    feats = _bar_features(bars)
    structures = pe.select_structures(chain, now, intent, erv_pack, cfg, term=term)
    best = (structures or {}).get("best") or {}
    danger_features = {
        "earnings_inside": earnings_within,
        "rv5_over_rv20": feats.get("rv5_over_rv20"),
        "gap_freq_2pct": feats.get("gap_freq_2pct"),
        "term_shape": (term or {}).get("shape"),
        "ret20_pct": feats.get("ret20_pct"),
        "spread_pct": best.get("spread_pct"),
        "liquidity_poor": (not best.get("liquidity_ok")) if best else None,
        "rr25_volpts": (sk or {}).get("rr25_volpts"),
        "vix_percentile": _vix_percentile(),
    }
    danger = pe.danger_model(danger_features, cfg)
    term_adv = None
    term_note = None
    if term and term.get("richest") and term["marks"].get("iv30"):
        term_adv = (term["richest"]["iv"] - term["marks"]["iv30"]) * 100.0
        term_note = (f"{term['richest']['dte']:.0f}d expiry is the richest tenor "
                     f"(+{term_adv:.1f} vol pts vs 30d)")
    score_parts = {
        "vrp_ratio": vrp["vrp_ratio"], "hist": hist,
        "best_ev_per_tail": best.get("ev_per_tail") if best.get("ev_per_tail") is not None
                            else (best.get("ev_per_share")),
        "liquidity_ok": best.get("liquidity_ok") if best else None,
        "skew_advantage": (sk or {}).get("rr25_volpts"),
        "term_advantage": term_adv, "term_note": term_note,
        "expected_moves_out": best.get("expected_moves_out"),
        "danger": danger, "premium_class": cls["class"],
    }
    scored = pe.edge_score(score_parts, cfg)
    data_ok = bool(structures and iv and erv_pack)
    signal = pe.signal_for(scored["score"], vrp["vrp_ratio"], danger["label"], data_ok, cfg)

    row = {
        "symbol": sym, "spot": round(spot, 2),
        "signal": signal, "score": scored["score"],
        "danger": danger["label"],
        "iv30": iv["iv30"], "iv30_method": iv["method"],
        "erv30": erv_pack["erv30"], "erv30_event": erv_pack["erv30_event"],
        "erv_method": erv_pack["method"],
        "vrp_points": vrp["vrp_points"], "vrp_ratio": vrp["vrp_ratio"],
        "vrp_z": hist.get("z"), "vrp_percentile": hist.get("percentile"),
        "hist_n": hist.get("n", 0), "hist_status": hist.get("status"),
        "premium_class": cls["class"], "event_share": cls.get("event_share"),
        "earnings_date": earn_next, "earnings_inside": earnings_within,
        "term_shape": (term or {}).get("shape"),
        "rr25_volpts": (sk or {}).get("rr25_volpts"),
        "best_kind": best.get("kind"), "best_expiry": (structures or {}).get("expiry"),
        "best_dte": (structures or {}).get("dte"),
        "best_strike": best.get("strike") or best.get("short_strike"),
        "best_credit": best.get("credit_exec") or best.get("credit"),
        "best_delta": best.get("delta"),
        "best_p_itm": best.get("p_itm_model"), "best_p_touch": best.get("p_touch_model"),
        "best_ev": best.get("ev_per_share"),
        "best_roc_pct": best.get("prem_pct_collateral"),
        "liquidity_ok": best.get("liquidity_ok"),
        "main_risk": scored.get("main_risk") or (danger["reasons"][0] if danger["reasons"] else None),
        "data_ok": data_ok, "as_of": today,
    }
    detail = {
        "row": row, "term": term, "skew": sk, "vrp": vrp, "hist": hist,
        "erv": erv_pack, "iv30": iv, "classification": cls,
        "structures": structures, "danger": danger,
        "score_breakdown": scored["breakdown"], "bar_features": feats,
        "intent": intent,
        "engine": {"version": pe.ENGINE_VERSION, "config_hash": pe.config()[1]},
    }
    if record:
        pe.record_observation(sym, {
            "date": today, "spot": round(spot, 2), "iv30": iv["iv30"],
            "iv30_method": iv["method"],
            "iv_marks": (term or {}).get("marks"),
            "rr25_volpts": (sk or {}).get("rr25_volpts"),
            "erv30": erv_pack["erv30"], "erv30_event": erv_pack["erv30_event"],
            "vrp_points": vrp["vrp_points"], "vrp_ratio": vrp["vrp_ratio"],
            "premium_class": cls["class"], "score": scored["score"],
            "signal": signal, "term_shape": (term or {}).get("shape"),
        })
        record_forecast_error(sym, bars, today)
        if _IV_APPEND_FN:
            try:
                _IV_APPEND_FN(sym, iv["iv30"])
            except Exception:
                pass
    return detail


# ── Stage 1 + scan worker ───────────────────────────────────────────────────

def _stage1_candidates(cfg: dict) -> list:
    """Free screen over the watchlist_table board. Ranked by premium-seller
    interest: realized-vol rank + earnings proximity + day move."""
    sn = cfg.get("scan", {})
    try:
        board = _BOARD_FN() if _BOARD_FN else None
        rows = (board or {}).get("rows") or []
    except Exception:
        rows = []
    out = []
    for r in rows:
        try:
            last = float(r.get("last") or 0)
            mcap = float(r.get("market_cap") or 0)
            avol = float(r.get("avg_volume") or 0)
        except (TypeError, ValueError):
            continue
        if last < float(sn.get("min_price", 20.0)):
            continue
        if mcap and mcap < float(sn.get("min_market_cap", 5e9)):
            continue
        if avol and avol < float(sn.get("min_avg_volume", 1e6)):
            continue
        score = float(r.get("rvol_rank") or 0)
        dte_e = r.get("days_to_earnings")
        if isinstance(dte_e, (int, float)):
            if 0 <= dte_e <= 7:
                score += 30
            elif 0 <= dte_e <= 21:
                score += 15
        try:
            score += min(abs(float(r.get("change") or 0)) * 4, 20)
        except (TypeError, ValueError):
            pass
        out.append((score, r.get("ticker")))
    out.sort(reverse=True)
    return [t for _s, t in out if t][: int(cfg.get("scan", {}).get("stage2_n", 24))]


def _budget_ok(cfg: dict) -> bool:
    sc = _SCHWAB() if _SCHWAB else None
    if sc is None:
        return False
    try:
        return sc.rate_usage() < int(cfg.get("scan", {}).get("budget_req_per_min", 70))
    except Exception:
        return True


def _scan_worker(force: bool = False) -> None:
    cfg = _cfg()
    with _LOCK:
        _STATE["scanning"] = True
        _STATE["error"] = None
    try:
        syms = _stage1_candidates(cfg)
        with _LOCK:
            _STATE["universe"] = len(syms)
            _STATE["scanned"] = 0
        # after the close, record the day's observations exactly once
        now_et = _now_et()
        record = (not _market_open()) and now_et.hour >= 16 \
            and _STATE.get("eod_recorded_for") != now_et.date().isoformat()
        rows = []
        for sym in syms:
            waited = 0
            while not _budget_ok(cfg) and waited < 5:
                time.sleep(3)
                waited += 1
            try:
                detail = analyze_symbol(sym, intent="premium_only", record=record or _market_open())
            except Exception as exc:  # noqa: BLE001 — one bad symbol never kills a pass
                detail = {"symbol": sym, "error": str(exc), "data_ok": False}
            if detail and detail.get("row"):
                rows.append(detail["row"])
            elif detail and detail.get("error"):
                rows.append({"symbol": sym, "signal": "INSUFFICIENT DATA",
                             "data_ok": False, "error": detail["error"], "score": 0})
            with _LOCK:
                _STATE["scanned"] += 1
        rows.sort(key=lambda r: (-(r.get("score") or 0)))
        with _LOCK:
            _STATE["rows"] = rows
            _STATE["as_of"] = _now_et().isoformat(timespec="seconds")
            _STATE["last_scan_ts"] = time.time()
            if record:
                _STATE["eod_recorded_for"] = now_et.date().isoformat()
        _persist_board()
    except Exception as exc:          # noqa: BLE001
        with _LOCK:
            _STATE["error"] = str(exc)
    finally:
        with _LOCK:
            _STATE["scanning"] = False


def _persist_board() -> None:
    if _DATA_DIR is None:
        return
    try:
        p = _DATA_DIR / "edge" / "board.json"
        tmp = p.with_suffix(".json.tmp")
        with _LOCK:
            data = {"rows": _STATE["rows"], "as_of": _STATE["as_of"]}
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        tmp.replace(p)
    except Exception as exc:          # noqa: BLE001
        print(f"edge_scan: board persist failed: {exc}")


def _load_board() -> None:
    if _DATA_DIR is None:
        return
    try:
        p = _DATA_DIR / "edge" / "board.json"
        if p.exists():
            data = json.loads(p.read_text())
            with _LOCK:
                if not _STATE["rows"]:
                    _STATE["rows"] = data.get("rows") or []
                    _STATE["as_of"] = data.get("as_of")
    except Exception:
        pass


def trigger_scan(force: bool = False) -> dict:
    with _LOCK:
        if _STATE["scanning"]:
            return {"status": "already_scanning"}
        t = threading.Thread(target=_scan_worker, kwargs={"force": force},
                             daemon=True, name="edge-scan")
        _STATE["thread"] = t
    t.start()
    return {"status": "started"}


def snapshot() -> dict:
    _load_board()
    with _LOCK:
        return {"rows": list(_STATE["rows"]), "as_of": _STATE["as_of"],
                "scanning": _STATE["scanning"], "error": _STATE["error"],
                "universe": _STATE["universe"], "scanned": _STATE["scanned"],
                "market_open": _market_open()}


def detail(sym: str, intent: str = "premium_only") -> dict | None:
    key = f"{sym.upper()}|{intent}"
    hit = _DETAIL_CACHE.get(key)
    if hit and time.time() - hit[0] < _DETAIL_TTL:
        return hit[1]
    out = analyze_symbol(sym.upper(), intent=intent, record=False)
    if out is not None:
        _DETAIL_CACHE[key] = (time.time(), out)
        if len(_DETAIL_CACHE) > 40:
            oldest = min(_DETAIL_CACHE, key=lambda k: _DETAIL_CACHE[k][0])
            _DETAIL_CACHE.pop(oldest, None)
    return out


def start_scheduler() -> None:
    """60s keeper: scans every cadence_minutes while the market is open,
    plus one post-close pass (~16:05 ET) that records the day's §20
    observations. Silent when Schwab is absent."""
    def loop():
        while True:
            try:
                cfg = _cfg()
                cadence = float(cfg.get("scan", {}).get("cadence_minutes", 25)) * 60.0
                now_et = _now_et()
                due = time.time() - _STATE["last_scan_ts"] >= cadence
                eod_due = (now_et.hour == 16 and now_et.minute >= 5
                           and _STATE.get("eod_recorded_for") != now_et.date().isoformat()
                           and now_et.weekday() < 5)
                if (_SCHWAB and _SCHWAB() is not None
                        and not _STATE["scanning"]
                        and ((_market_open() and due) or eod_due)):
                    trigger_scan()
            except Exception as exc:  # noqa: BLE001
                print(f"edge_scan scheduler: {exc}")
            time.sleep(60)
    threading.Thread(target=loop, daemon=True, name="edge-scheduler").start()


# ── strike breach history (§17, MEASURED) + EM calibration (§18) ────────────

def breach_stats(bars: list, cfg: dict) -> dict | None:
    """How often would a vol-adjusted strike distance have been breached,
    historically? For every window start, strike = S·exp(∓k·σ₂₀·√T) using
    ONLY trailing data (σ₂₀ at the window start — no lookahead), then check
    touch (intraday extreme) and finish-ITM against what actually happened.
    Frequencies are MEASURED; the model column is the driftless-lognormal
    prediction with the same σ, so the gap IS the model's calibration
    error for this ticker."""
    b = cfg.get("breach", {})
    horizons = [int(h) for h in b.get("horizons_td", [5, 10, 21])]
    ks = [float(k) for k in b.get("k_sigmas", [0.5, 0.75, 1.0, 1.25, 1.5, 2.0])]
    min_w = int(b.get("min_windows", 60))
    closes = [float(x.get("close") or 0) for x in bars]
    lows = [float(x.get("low") or 0) for x in bars]
    highs = [float(x.get("high") or 0) for x in bars]
    out_rows = []
    for h in horizons:
        t_years = h / vf.TRADING_DAYS
        start = 40
        ends = len(bars) - h
        if ends - start < min_w:
            continue
        for k in ks:
            n = pt = pi = ct = ci = 0
            for i in range(start, ends, 2):      # step 2: cheap, still dense
                sigma = vf.rv(closes[: i + 1], 20)
                if not sigma or closes[i] <= 0:
                    continue
                dist = k * sigma * math.sqrt(t_years)
                put_k = closes[i] * math.exp(-dist)
                call_k = closes[i] * math.exp(dist)
                lo_seg = [x for x in lows[i + 1: i + 1 + h] if x > 0]
                hi_seg = [x for x in highs[i + 1: i + 1 + h] if x > 0]
                if len(lo_seg) < h * 0.8 or closes[i + h] <= 0:
                    continue
                n += 1
                if min(lo_seg) <= put_k:
                    pt += 1
                if closes[i + h] <= put_k:
                    pi += 1
                if max(hi_seg) >= call_k:
                    ct += 1
                if closes[i + h] >= call_k:
                    ci += 1
            if n < min_w:
                continue
            model_touch = pe.touch_prob(100.0, 100.0 * math.exp(-k * 0.30 * math.sqrt(t_years)),
                                        0.30, t_years)
            model_itm = pe.p_itm(100.0, 100.0 * math.exp(-k * 0.30 * math.sqrt(t_years)),
                                 0.30, t_years, "put")
            out_rows.append({
                "horizon_td": h, "k_sigma": k, "n": n,
                "put_touch_emp": round(pt / n, 4), "put_itm_emp": round(pi / n, 4),
                "call_touch_emp": round(ct / n, 4), "call_itm_emp": round(ci / n, 4),
                "touch_model": model_touch, "itm_model": model_itm,
                "basis": "MEASURED (trailing σ20 strike placement, no lookahead)",
            })
    if not out_rows:
        return None
    return {"rows": out_rows,
            "note": ("empirical frequencies from this ticker's own bars; model column = "
                     "driftless lognormal at the same sigma — the gap is this name's "
                     "fat-tail correction")}


def breach_for(symbol: str) -> dict:
    """Breach history + EM calibration for one symbol from the engine's own
    (configured) data source — the API-facing wrapper."""
    sc = _SCHWAB() if _SCHWAB else None
    if sc is None:
        return {"error": "schwab unavailable"}
    bars = sc.get_price_history(symbol.upper(), days=900)
    if not bars:
        return {"error": "no price history"}
    cfg = _cfg()
    return {"symbol": symbol.upper(),
            "breach": breach_stats(bars, cfg),
            "em_calibration": em_calibration(bars, cfg)}


def em_calibration(bars: list, cfg: dict) -> dict | None:
    """Does this ticker stay inside its expected move? EM basis is the
    trailing σ₂₀ at each window start (MODELED proxy for implied — labeled;
    upgrades to MEASURED implied automatically once enough stored IV history
    accrues). Reports inside rates at 1.0/1.25/1.5×EM, breach direction
    frequencies and average overshoot beyond the band."""
    h = 21
    closes = [float(x.get("close") or 0) for x in bars]
    if len(closes) < 150:
        return None
    n = in1 = in125 = in15 = up_b = dn_b = 0
    over_mags = []
    for i in range(40, len(closes) - h, 2):
        sigma = vf.rv(closes[: i + 1], 20)
        if not sigma or closes[i] <= 0 or closes[i + h] <= 0:
            continue
        em = sigma * math.sqrt(h / vf.TRADING_DAYS)
        move = abs(math.log(closes[i + h] / closes[i]))
        n += 1
        if move <= em:
            in1 += 1
        if move <= em * 1.25:
            in125 += 1
        if move <= em * 1.5:
            in15 += 1
        else:
            over_mags.append((move - em * 1.5) * 100.0)
        if move > em:
            if closes[i + h] > closes[i]:
                up_b += 1
            else:
                dn_b += 1
    if n < 60:
        return None
    return {
        "n": n, "horizon_td": h,
        "inside_1x_pct": round(in1 / n * 100.0, 1),
        "inside_125x_pct": round(in125 / n * 100.0, 1),
        "inside_15x_pct": round(in15 / n * 100.0, 1),
        "upside_breach_pct": round(up_b / n * 100.0, 1),
        "downside_breach_pct": round(dn_b / n * 100.0, 1),
        "avg_overshoot_beyond_15x_logpts": (round(sum(over_mags) / len(over_mags), 2)
                                            if over_mags else None),
        "em_basis": "MODELED (trailing sigma20 proxy for implied; upgrades to MEASURED "
                    "as stored IV history accrues)",
        "theory_inside_1x_pct": 68.3,
    }


# ── VRP threshold backtest (§6/§21) + sizing (§22) ──────────────────────────

def _erv_series(bars: list, cfg: dict) -> list:
    """O(n) daily ExpectedRV series (GLOBAL blend, incremental estimators)
    for backtests. erv[i] uses bars[:i+1] only."""
    fc = cfg.get("forecast", {})
    lam = float(fc.get("ewma_lambda", 0.94))
    weights = fc.get("global_weights", vf.GLOBAL_WEIGHTS)
    shrink = float(fc.get("anchor_shrink", vf.ANCHOR_SHRINK))
    closes = [float(b.get("close") or 0) for b in bars]
    n = len(bars)
    rets = [0.0] * n
    for i in range(1, n):
        if closes[i] > 0 and closes[i - 1] > 0:
            rets[i] = math.log(closes[i] / closes[i - 1])
    out = [None] * n
    ewma_var = None
    park_win, rv_win, anch_win = [], [], []
    for i in range(1, n):
        r = rets[i]
        rv_win.append(r)
        anch_win.append(r)
        if len(rv_win) > 20:
            rv_win.pop(0)
        if len(anch_win) > 252:
            anch_win.pop(0)
        try:
            h, lo = float(bars[i].get("high") or 0), float(bars[i].get("low") or 0)
            park_win.append(math.log(h / lo) ** 2 if h > 0 and lo > 0 and h >= lo else None)
        except (TypeError, ValueError):
            park_win.append(None)
        if len(park_win) > 20:
            park_win.pop(0)
        if i == 21:
            ewma_var = sum(x * x for x in rets[1:21]) / 20.0
        elif i > 21 and ewma_var is not None:
            ewma_var = lam * ewma_var + (1 - lam) * r * r
        if i < 60:
            continue
        comps = {}
        if len(rv_win) == 20:
            m = sum(rv_win) / 20
            comps["RV20"] = math.sqrt(max(sum((x - m) ** 2 for x in rv_win) / 19, 0) * 252)
        if ewma_var and ewma_var > 0:
            comps["EWMA94"] = math.sqrt(ewma_var * 252)
        pk = [x for x in park_win if x is not None]
        if len(pk) >= 16:
            comps["PARK20"] = math.sqrt(sum(pk) / len(pk) / (4 * math.log(2)) * 252)
        avail = {k: w for k, w in weights.items() if k in comps}
        if not avail:
            continue
        core = sum(comps[k] * w for k, w in avail.items()) / sum(avail.values())
        anchor = None
        if len(anch_win) >= 200:
            m = sum(anch_win) / len(anch_win)
            anchor = math.sqrt(max(sum((x - m) ** 2 for x in anch_win) / (len(anch_win) - 1), 0) * 252)
        out[i] = shrink * anchor + (1 - shrink) * core if anchor else core
    return out


def run_vrp_backtest(symbols: list, iv_history_fn=None, earnings_fn=None,
                     vix_fn=None, thresholds=None) -> dict:
    """Sweep VRP-ratio entry thresholds through bt_options' full cost /
    assignment model. IV per day is REAL stored IV30 when the store covers
    ≥60 of the period's days (labeled measured), else the bt_iv proxy
    (labeled modeled). Reports EV-first metrics including worst 1%/5% trade
    outcomes — a strategy is judged by its tails, not its win rate."""
    cfg = _cfg()
    bt = cfg.get("backtest", {})
    thresholds = thresholds or bt.get("ratio_thresholds", [1.0, 1.1, 1.2, 1.3, 1.5, 1.75])
    structure = bt.get("structure", "put_credit_spread")
    mgmt = dict(bt.get("mgmt", {"profit_take_pct": 50, "stop_x_credit": 2.0, "exit_dte": 21}))
    sc = _SCHWAB() if _SCHWAB else None
    if sc is None:
        return {"error": "schwab unavailable"}
    symbols = [s.upper() for s in symbols][:8]
    bars_by, erv_by, iv_by, iv_basis = {}, {}, {}, {}
    for sym in symbols:
        bars = sc.get_price_history(sym, days=730)
        if not bars or len(bars) < 300:
            continue
        bars_by[sym] = bars
        erv_by[sym] = _erv_series(bars, cfg)
        stored = []
        try:
            stored = (iv_history_fn(sym) or []) if iv_history_fn else []
        except Exception:
            stored = []
        by_date = {e["date"]: e["iv"] for e in stored if e.get("date") and e.get("iv")}
        dates = [b["date"][:10] for b in bars]
        matched = sum(1 for d in dates if d in by_date)
        if matched >= 60:
            last = None
            series = []
            for d in dates:
                last = by_date.get(d, last)
                series.append(last)
            iv_by[sym] = series
            iv_basis[sym] = f"measured (stored IV30, n={matched})"
        else:
            closes_by_date = {b["date"][:10]: float(b.get("close") or 0) for b in bars}
            ratio, src = bt_iv.calibrate_ratio(stored, closes_by_date)
            vix = {}
            try:
                vix = (vix_fn() or {}) if vix_fn else {}
            except Exception:
                pass
            scalers, _vsrc = bt_iv.vix_scaler_series(dates, vix)
            earn = []
            try:
                earn = (earnings_fn(sym) or []) if earnings_fn else []
            except Exception:
                pass
            iv_by[sym] = bt_iv.build_iv_series(bars, ratio, scalers, earn)
            iv_basis[sym] = f"modeled (bt_iv, ratio {src})"
    if not bars_by:
        return {"error": "no symbols with enough history"}
    grid = []
    for th in thresholds:
        signals = []
        for sym, bars in bars_by.items():
            for i in range(80, len(bars) - 50):
                iv_i, erv_i = iv_by[sym][i], erv_by[sym][i]
                if iv_i and erv_i and erv_i > 0 and iv_i / erv_i >= th:
                    signals.append((bars[i]["date"][:10], sym, i))
        signals.sort()
        res = bt_options.run_portfolio(
            signals, bars_by, iv_by, structure, mgmt,
            params={"dte": 30, "target_delta": 0.25, "wing_delta": 0.10})
        trades = res.get("trades", [])
        pnls = sorted(t["pnl"] for t in trades)
        m = {"threshold": th, "n_trades": len(trades), "n_signals": len(signals)}
        if trades:
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            m.update({
                "total_pnl": round(sum(pnls), 0),
                "expectancy": round(sum(pnls) / len(pnls), 1),
                "win_rate": round(len(wins) / len(trades) * 100.0, 1),
                "avg_win": round(sum(wins) / len(wins), 1) if wins else None,
                "avg_loss": round(sum(losses) / len(losses), 1) if losses else None,
                "profit_factor": (round(sum(wins) / abs(sum(losses)), 2)
                                  if losses and sum(losses) < 0 else None),
                "worst_1pct": round(pnls[max(0, int(len(pnls) * 0.01) - 1)], 0) if len(pnls) >= 20 else None,
                "worst_5pct": round(pnls[max(0, int(len(pnls) * 0.05) - 1)], 0) if len(pnls) >= 20 else None,
                "es_5pct": (round(sum(pnls[: max(1, int(len(pnls) * 0.05))])
                                  / max(1, int(len(pnls) * 0.05)), 0) if len(pnls) >= 20 else None),
                "assignments": sum(1 for t in trades if "assign" in (t.get("reason") or "")),
                "avg_return_on_bp_pct": (round(sum(t.get("pnl_on_bp") or 0 for t in trades)
                                               / len(trades), 2)),
            })
            curve = res.get("equity_curve") or []
            if curve:
                sh = bv.sharpe_from_curve(curve)
                if sh:
                    m["sharpe"] = sh["sharpe"]
                m["max_drawdown_pct"] = bv.max_drawdown_pct([c["equity"] for c in curve])
        grid.append(m)
    viable = [g for g in grid if g.get("n_trades", 0) >= 8 and g.get("expectancy") is not None]
    robust = None
    if viable:
        # robust choice: best expectancy whose NEIGHBORS are also positive —
        # a threshold that only works at exactly one value is curve-fit
        def nb(i):
            ns = [viable[j] for j in (i - 1, i + 1) if 0 <= j < len(viable)]
            return all((x.get("expectancy") or 0) > 0 for x in ns) if ns else True
        cands = [(g["expectancy"], i, g) for i, g in enumerate(viable) if nb(i)]
        if cands:
            robust = max(cands)[2]["threshold"]
    return {"grid": grid, "robust_threshold": robust,
            "structure": structure, "mgmt": mgmt,
            "iv_basis": iv_basis, "symbols": list(bars_by),
            "note": ("thresholds are IV/ExpectedRV ratios; z-score thresholds activate "
                     "once own-history depth exists. Costs, slippage and assignment are "
                     "bt_options' standard model. Judge by expectancy and tails, "
                     "not win rate.")}


def start_backtest_job(symbols: list, iv_history_fn=None, earnings_fn=None,
                       vix_fn=None, thresholds=None) -> dict:
    job_id = f"edge-bt-{int(time.time())}"
    _BT_JOBS[job_id] = {"status": "running", "started": time.time(), "result": None}

    def run():
        try:
            res = run_vrp_backtest(symbols, iv_history_fn, earnings_fn, vix_fn, thresholds)
            _BT_JOBS[job_id].update({"status": "done", "result": res})
        except Exception as exc:      # noqa: BLE001
            _BT_JOBS[job_id].update({"status": "error", "result": {"error": str(exc)}})
    threading.Thread(target=run, daemon=True, name=job_id).start()
    return {"job": job_id}


def backtest_job(job_id: str) -> dict:
    j = _BT_JOBS.get(job_id)
    if not j:
        return {"error": "unknown job"}
    return {"status": j["status"], "result": j["result"],
            "elapsed_s": round(time.time() - j["started"], 1)}


def kelly_guidance(trade_pnls: list, collateral_per_trade: float, cfg: dict | None = None) -> dict:
    """Fractional Kelly from REAL outcome distributions — never an assumed
    win rate. Below min_outcomes it refuses with the honest reason. The
    mean/variance Kelly estimate is multiplied by kelly_fraction (default
    quarter-Kelly) and then HARD-CAPPED by max_single_position_frac; the
    caller additionally applies the drawdown-anchored cap (bt_plans)."""
    cfg = cfg or _cfg()
    sz = cfg.get("sizing", {})
    need = int(sz.get("min_outcomes", 40))
    pnls = [p for p in (trade_pnls or []) if isinstance(p, (int, float))]
    if len(pnls) < need:
        return {"status": "insufficient_outcomes", "n": len(pnls), "needed": need,
                "note": "Kelly needs a real outcome distribution — keep trading/backtesting"}
    if not collateral_per_trade or collateral_per_trade <= 0:
        return {"status": "error", "note": "collateral required"}
    rets = [p / collateral_per_trade for p in pnls]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0 or mean <= 0:
        return {"status": "no_edge", "n": len(pnls),
                "note": "mean return non-positive — Kelly says size zero"}
    full = mean / var
    frac = float(sz.get("kelly_fraction", 0.25))
    cap = float(sz.get("max_single_position_frac", 0.10))
    suggested = min(full * frac, cap)
    return {"status": "ok", "n": len(pnls),
            "full_kelly_frac": round(full, 3),
            "fractional": frac,
            "suggested_frac": round(suggested, 3),
            "capped_by": "max_single_position_frac" if full * frac > cap else "kelly",
            "basis": f"mean/variance Kelly on {len(pnls)} real outcomes, "
                     f"{frac:.0%}-Kelly, hard cap {cap:.0%}"}
