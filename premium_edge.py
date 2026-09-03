"""premium_edge.py — Premium Edge engine core.

Measures whether option premium is genuinely overpriced relative to the
volatility the underlying is LIKELY to realize (ExpectedRV30 from
vol_forecast.py), then finds the exact structure/expiration/strike that
best monetizes the difference for a given trading intent.

What lives here (pure, deterministic, chain-payload in → numbers out):
  - True IV30: constant-maturity 30-calendar-day implied vol interpolated
    on TOTAL VARIANCE (iv²·T) between the bracketing expirations, from
    liquid near-ATM quotes on both calls and puts. This is deliberately
    NOT the app's legacy `iv30_avg` (front-expiry ATM) — see PREMIUM_EDGE.md.
  - Term structure: ATM IV per expiration + interpolated 7/14/30/45/60/90
    DTE marks, contango/backwardation/hump classification.
  - Skew: ATM / 25Δ / 10Δ per side, risk reversal. SIGN CONVENTION:
    put-minus-call in vol points (positive = downside puts richer), the
    credit_risk.skew_gauge convention.
  - VRP: points, ratio, variance spread — vs ExpectedRV30 and its
    earnings-adjusted variant, which powers PURE/EVENT/MIXED classification.
  - Per-contract economics under the driftless lognormal at ExpectedRV:
    P(ITM), P(touch), EV (credit − model fair value − costs), closed-form
    5% expected shortfall, premium-efficiency ratios.
  - Intent-aware structure selection (covered call / cash-secured put /
    credit spreads / iron condor — defined risk only for PREMIUM_ONLY).
  - Explainable 0-100 Premium Edge Score + signal states with hard
    data-quality gates (INSUFFICIENT DATA beats false precision).
  - Daily observation persistence (<data>/edge/obs/SYM.json) — the store
    the VRP Z-score matures against; sample sizes surfaced everywhere.

Statistical honesty: every quantity carries a basis label. Model
probabilities use the driftless real-world lognormal at ExpectedRV30 —
labeled "model", never presented as empirical. Empirical breach
frequencies (edge_scan.breach_stats) are the MEASURED counterpart.

Config: the `premium_edge` section of thresholds.json (repo defaults,
<data>/thresholds.json overlay, deep-merged — the timing_engine pattern),
sha256-stamped so persisted decisions can be replay-checked.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from metrics import _bs_price, _bs_delta, _norm_cdf, normalize_iv
import vol_forecast as vf

SCHEMA_VERSION = "1.0"
ENGINE_VERSION = "edge-1.0.0"

_DATA_DIR: Path | None = None
_OBS_LOCK = threading.Lock()
_CFG_LOCK = threading.Lock()
_CFG_CACHE = {"cfg": None, "hash": None, "ts": 0.0}
_OBS_MAX = 750                      # ~3 years of daily observations


def configure(data_dir) -> None:
    global _DATA_DIR
    _DATA_DIR = Path(data_dir) if data_dir else None
    if _DATA_DIR is not None:
        try:
            (_DATA_DIR / "edge" / "obs").mkdir(parents=True, exist_ok=True)
        except Exception as exc:      # noqa: BLE001 — storage is best-effort
            print(f"premium_edge: cannot create data dir: {exc}")


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def config(refresh: bool = False) -> tuple[dict, str]:
    """(premium_edge config section, sha256[:16] of the FULL thresholds
    file). Repo thresholds.json = defaults; <data>/thresholds.json
    overrides key-by-key; cached 60s. Same discipline as timing_engine."""
    with _CFG_LOCK:
        if (not refresh and _CFG_CACHE["cfg"] is not None
                and time.time() - _CFG_CACHE["ts"] < 60):
            return _CFG_CACHE["cfg"], _CFG_CACHE["hash"]
        repo = Path(__file__).resolve().parent / "thresholds.json"
        try:
            full = json.loads(repo.read_text())
        except Exception:
            full = {}
        if _DATA_DIR is not None:
            try:
                p = _DATA_DIR / "thresholds.json"
                if p.exists():
                    full = _deep_merge(full, json.loads(p.read_text()))
            except Exception:
                pass
        h = hashlib.sha256(json.dumps(full, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()[:16]
        cfg = full.get("premium_edge") or {}
        _CFG_CACHE.update({"cfg": cfg, "hash": h, "ts": time.time()})
        return cfg, h


# ── quote quality ───────────────────────────────────────────────────────────

def quote_ok(row: dict, cfg: dict, market_open: bool = True) -> tuple[bool, str]:
    """Is this contract row usable as a volatility observation?  Stale-age
    checks only apply while the market is open — outside the session every
    quote is legitimately frozen at the last close (the 0DTE lesson)."""
    q = cfg.get("iv_quote", {})
    bid = row.get("bid") or 0.0
    ask = row.get("ask") or 0.0
    if bid <= 0 or ask <= 0 or ask < bid:
        return False, "no_two_sided_quote"
    if bid < float(q.get("min_bid", 0.05)):
        return False, "premium_too_small"
    mid = (bid + ask) / 2.0
    if mid > 0 and (ask - bid) / mid > float(q.get("max_spread_frac", 0.35)):
        return False, "spread_too_wide"
    if market_open:
        age = row.get("quote_age_s")
        if age is not None and age > float(q.get("max_quote_age_s", 900)):
            return False, "stale_quote"
    iv = normalize_iv(row.get("iv"))
    if iv is None or iv <= 0.01 or iv > 5.0:
        return False, "iv_corrupt"
    return True, "ok"


def _expiry_dte(exp_iso: str, now: date) -> float:
    try:
        d = date.fromisoformat(exp_iso[:10])
    except (ValueError, TypeError):
        return -1.0
    return (d - now).days


def _atm_rows(rows: list, spot: float) -> list:
    """Rows sorted by ATM-ness: |delta| distance from 0.50 when delta is
    present, else strike distance from spot."""
    def key(r):
        d = r.get("delta")
        if d is not None:
            try:
                return abs(abs(float(d)) - 0.5)
            except (TypeError, ValueError):
                pass
        try:
            return abs(float(r.get("strike", 0)) - spot) / max(spot, 1e-9) + 10.0
        except (TypeError, ValueError):
            return 99.0
    return sorted(rows or [], key=key)


def atm_iv_for_expiry(calls: list, puts: list, spot: float, cfg: dict,
                      market_open: bool = True) -> dict | None:
    """ATM IV for one expiration = mean of the best valid near-ATM call and
    put IVs (both sides when both are usable — one side alone is accepted
    and labeled). None when no usable quote exists."""
    picks, sides = [], []
    for side, rows in (("call", calls), ("put", puts)):
        for r in _atm_rows(rows, spot)[:4]:
            ok, _why = quote_ok(r, cfg, market_open)
            if ok:
                picks.append(normalize_iv(r.get("iv")))
                sides.append(side)
                break
    if not picks:
        return None
    return {"iv": sum(picks) / len(picks), "sides": sides,
            "basis": "call+put ATM" if len(picks) == 2 else f"{sides[0]} only"}


# ── IV30 (total-variance interpolation) ─────────────────────────────────────

def iv30(chain: dict, now: date, cfg: dict, market_open: bool = True) -> dict | None:
    """Constant-maturity 30-calendar-day IV.

    var(T) = iv²·T with T in years (calendar/365). Linear interpolation of
    TOTAL VARIANCE between the two expirations bracketing 30 days, then
    back to vol. Falls back to the nearest single expiration (labeled) when
    30d isn't bracketed. Expiries outside [min_dte, max_dte] are ignored."""
    q = cfg.get("iv30", {})
    lo_dte = float(q.get("min_dte", 5))
    hi_dte = float(q.get("max_dte", 75))
    target = float(q.get("target_calendar_days", 30.0))
    spot = ((chain.get("underlying") or {}).get("last")
            or (chain.get("underlying") or {}).get("bid") or 0.0)
    if not spot or spot <= 0:
        return None
    marks = []
    for exp, sides in (chain.get("chains") or {}).items():
        dte = _expiry_dte(exp, now)
        if dte < lo_dte or dte > hi_dte:
            continue
        got = atm_iv_for_expiry(sides.get("calls"), sides.get("puts"), spot, cfg, market_open)
        if got:
            marks.append({"exp": exp[:10], "dte": dte, "iv": round(got["iv"], 4),
                          "basis": got["basis"]})
    if not marks:
        return None
    marks.sort(key=lambda m: m["dte"])
    below = [m for m in marks if m["dte"] <= target]
    above = [m for m in marks if m["dte"] >= target]
    if below and above and below[-1]["exp"] != above[0]["exp"]:
        a, b = below[-1], above[0]
        ta, tb = a["dte"] / 365.0, b["dte"] / 365.0
        tt = target / 365.0
        va, vb = a["iv"] ** 2 * ta, b["iv"] ** 2 * tb
        w = (tt - ta) / (tb - ta)
        vt = va + (vb - va) * w
        if vt <= 0:
            return None
        return {"iv30": round(math.sqrt(vt / tt), 4), "method": "variance_interpolation",
                "lower": a, "upper": b, "n_expiries": len(marks)}
    nearest = min(marks, key=lambda m: abs(m["dte"] - target))
    return {"iv30": nearest["iv"], "method": "nearest_expiry",
            "lower": nearest, "upper": None, "n_expiries": len(marks)}


# ── term structure ──────────────────────────────────────────────────────────

TERM_TARGETS = (7, 14, 30, 45, 60, 90)


def term_structure(chain: dict, now: date, cfg: dict, earnings_date: str | None = None,
                   market_open: bool = True) -> dict | None:
    """ATM IV per expiration (≤ ~120d) + interpolated marks at the standard
    tenors + shape classification. hump = an expiry whose IV exceeds both
    neighbors by hump_volpts (usually the earnings expiry — flagged)."""
    t = cfg.get("term", {})
    spot = ((chain.get("underlying") or {}).get("last") or 0.0)
    if not spot or spot <= 0:
        return None
    rows = []
    for exp, sides in (chain.get("chains") or {}).items():
        dte = _expiry_dte(exp, now)
        if dte < 0.5 or dte > float(t.get("max_dte", 120)):
            continue
        got = atm_iv_for_expiry(sides.get("calls"), sides.get("puts"), spot, cfg, market_open)
        if got:
            has_earn = bool(earnings_date and exp[:10] >= earnings_date >= now.isoformat())
            rows.append({"exp": exp[:10], "dte": round(dte, 1), "iv": round(got["iv"], 4),
                         "covers_earnings": has_earn})
    if len(rows) < 2:
        return None
    rows.sort(key=lambda r: r["dte"])
    # earliest expiry covering earnings is THE earnings expiry
    seen_earn = False
    for r in rows:
        if r["covers_earnings"] and not seen_earn:
            seen_earn = True
        elif r["covers_earnings"]:
            r["covers_earnings"] = False

    def interp(days):
        below = [r for r in rows if r["dte"] <= days]
        above = [r for r in rows if r["dte"] >= days]
        if below and above and below[-1]["exp"] != above[0]["exp"]:
            a, b = below[-1], above[0]
            ta, tb, tt = a["dte"] / 365.0, b["dte"] / 365.0, days / 365.0
            va, vb = a["iv"] ** 2 * ta, b["iv"] ** 2 * tb
            vt = va + (vb - va) * (tt - ta) / (tb - ta)
            return round(math.sqrt(max(vt, 1e-9) / tt), 4)
        if below or above:
            near = min(rows, key=lambda r: abs(r["dte"] - days))
            if abs(near["dte"] - days) <= days * 0.5:
                return near["iv"]
        return None

    marks = {f"iv{d}": interp(d) for d in TERM_TARGETS}
    # shape from the interpolated short vs long end
    front = marks.get("iv7") or rows[0]["iv"]
    back = marks.get("iv60") or marks.get("iv90") or rows[-1]["iv"]
    ratio = front / back if back else None
    inv_th = float(t.get("backwardation_ratio", 1.08))
    con_th = float(t.get("contango_ratio", 0.97))
    if ratio is None:
        shape = "unknown"
    elif ratio >= inv_th:
        shape = "backwardation"
    elif ratio <= con_th:
        shape = "contango"
    else:
        shape = "flat"
    humps = []
    hump_pts = float(t.get("hump_volpts", 3.0)) / 100.0
    for i in range(1, len(rows) - 1):
        if (rows[i]["iv"] - rows[i - 1]["iv"] >= hump_pts
                and rows[i]["iv"] - rows[i + 1]["iv"] >= hump_pts):
            humps.append({"exp": rows[i]["exp"], "dte": rows[i]["dte"],
                          "excess_volpts": round((rows[i]["iv"] - max(rows[i - 1]["iv"], rows[i + 1]["iv"])) * 100, 1),
                          "covers_earnings": rows[i]["covers_earnings"]})
    richest = max(rows, key=lambda r: r["iv"])
    return {"rows": rows, "marks": marks, "shape": shape,
            "front_back_ratio": round(ratio, 3) if ratio else None,
            "humps": humps,
            "richest": {"exp": richest["exp"], "dte": richest["dte"], "iv": richest["iv"],
                        "covers_earnings": richest["covers_earnings"]}}


# ── skew ────────────────────────────────────────────────────────────────────

def _iv_at_delta(rows: list, target_abs_delta: float, spot: float, cfg: dict,
                 market_open: bool = True) -> dict | None:
    """Valid-quote row nearest the |delta| target. Requires the row's own
    delta (Schwab supplies it); tolerance keeps 10Δ honest when the chain
    is coarse."""
    best, best_gap = None, 1.0
    for r in rows or []:
        d = r.get("delta")
        if d is None:
            continue
        try:
            gap = abs(abs(float(d)) - target_abs_delta)
        except (TypeError, ValueError):
            continue
        if gap < best_gap:
            ok, _ = quote_ok(r, cfg, market_open)
            if ok:
                best, best_gap = r, gap
    if best is None or best_gap > float(cfg.get("skew", {}).get("delta_tolerance", 0.10)):
        return None
    return {"strike": best.get("strike"), "iv": normalize_iv(best.get("iv")),
            "delta": round(abs(float(best.get("delta"))), 3)}


def skew(chain: dict, now: date, cfg: dict, market_open: bool = True) -> dict | None:
    """Skew at the expiry nearest 30 DTE (within [10, 60]).

    SIGN CONVENTION (documented, tested): risk reversal = put IV − call IV
    in vol points; POSITIVE = downside puts richer (credit_risk
    convention). put_skew_volpts = put25 − ATM; call_skew_volpts = call25 −
    ATM. 10Δ values reported only when liquid."""
    spot = ((chain.get("underlying") or {}).get("last") or 0.0)
    if not spot or spot <= 0:
        return None
    best_exp, best_gap = None, 1e9
    for exp in (chain.get("chains") or {}):
        dte = _expiry_dte(exp, now)
        if 10 <= dte <= 60 and abs(dte - 30) < best_gap:
            best_exp, best_gap = exp, abs(dte - 30)
    if best_exp is None:
        return None
    sides = chain["chains"][best_exp]
    calls, puts = sides.get("calls") or [], sides.get("puts") or []
    atm = atm_iv_for_expiry(calls, puts, spot, cfg, market_open)
    p25 = _iv_at_delta(puts, 0.25, spot, cfg, market_open)
    c25 = _iv_at_delta(calls, 0.25, spot, cfg, market_open)
    p10 = _iv_at_delta(puts, 0.10, spot, cfg, market_open)
    c10 = _iv_at_delta(calls, 0.10, spot, cfg, market_open)
    if not atm or not (p25 or c25):
        return None
    out = {
        "exp": best_exp[:10], "dte": round(_expiry_dte(best_exp, now), 1),
        "atm_iv": round(atm["iv"], 4),
        "put25_iv": round(p25["iv"], 4) if p25 else None,
        "call25_iv": round(c25["iv"], 4) if c25 else None,
        "put10_iv": round(p10["iv"], 4) if p10 else None,
        "call10_iv": round(c10["iv"], 4) if c10 else None,
        "convention": "put_minus_call (positive = puts richer)",
    }
    if p25 and c25:
        out["rr25_volpts"] = round((p25["iv"] - c25["iv"]) * 100.0, 2)
    if p10 and c10:
        out["rr10_volpts"] = round((p10["iv"] - c10["iv"]) * 100.0, 2)
    if p25:
        out["put_skew_volpts"] = round((p25["iv"] - atm["iv"]) * 100.0, 2)
    if c25:
        out["call_skew_volpts"] = round((c25["iv"] - atm["iv"]) * 100.0, 2)
    return out


# ── VRP ─────────────────────────────────────────────────────────────────────

def vrp_block(iv30_val: float, erv_pack: dict) -> dict:
    """VRP vs the base forecast AND vs the earnings-adjusted forecast.
    (iv − erv_event) is the PURE premium after the event's historical
    variance is granted to the buyer; the gap between the two is the
    event's share of the apparent premium."""
    erv = erv_pack["erv30"]
    erv_e = erv_pack.get("erv30_event", erv)
    out = {
        "vrp_points": round((iv30_val - erv) * 100.0, 2),
        "vrp_ratio": round(iv30_val / erv, 3) if erv > 0 else None,
        "vrp_variance": round(iv30_val ** 2 - erv ** 2, 4),
        "vrp_points_ex_event": round((iv30_val - erv_e) * 100.0, 2),
        "vrp_ratio_ex_event": round(iv30_val / erv_e, 3) if erv_e > 0 else None,
    }
    gap = iv30_val ** 2 - erv ** 2
    event_var = erv_e ** 2 - erv ** 2
    out["event_share"] = round(max(0.0, min(1.0, event_var / gap)), 2) if gap > 1e-6 else None
    return out


def classify_premium(vrp: dict, earnings_inside: bool, macro_events: list,
                     cfg: dict) -> dict:
    """PURE VRP / EVENT PREMIUM / MIXED. Driven by how much of the
    IV-over-forecast variance gap the ticker's own historical earnings move
    explains (event_share); macro events inside the horizon are tagged and
    penalized in scoring but only earnings can flip the class."""
    th = cfg.get("event", {})
    share = vrp.get("event_share")
    if earnings_inside:
        if share is None:
            cls = "MIXED"
        elif share >= float(th.get("event_share_event", 0.60)):
            cls = "EVENT"
        elif share >= float(th.get("event_share_mixed", 0.20)):
            cls = "MIXED"
        else:
            cls = "PURE"
    else:
        cls = "PURE"
    return {"class": cls, "earnings_inside": bool(earnings_inside),
            "event_share": share, "macro_events": macro_events or []}


# ── observation store + Z-score ─────────────────────────────────────────────

def _obs_path(sym: str) -> Path | None:
    if _DATA_DIR is None:
        return None
    safe = "".join(c for c in sym.upper() if c.isalnum() or c in "-_.")
    return _DATA_DIR / "edge" / "obs" / f"{safe}.json"


def record_observation(sym: str, rec: dict) -> bool:
    """One observation per ticker per calendar date (today's is replaced on
    re-scan), capped at _OBS_MAX, atomic write. This store IS the future
    Z-score — §20's daily dataset."""
    p = _obs_path(sym)
    if p is None or not rec.get("date"):
        return False
    with _OBS_LOCK:
        try:
            hist = json.loads(p.read_text()) if p.exists() else []
        except Exception:
            hist = []
        hist = [h for h in hist if h.get("date") != rec["date"]]
        hist.append(rec)
        hist.sort(key=lambda h: h.get("date", ""))
        hist = hist[-_OBS_MAX:]
        try:
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(hist, separators=(",", ":")))
            tmp.replace(p)
            return True
        except Exception as exc:      # noqa: BLE001
            print(f"premium_edge: obs write failed for {sym}: {exc}")
            return False


def load_observations(sym: str) -> list:
    p = _obs_path(sym)
    if p is None or not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def vrp_stats(history: list, current_vrp_points: float, cfg: dict) -> dict:
    """Current VRP vs this ticker's own historical VRP distribution.

    Below min_observations the stats are returned for display but status =
    'insufficient_history' and the Z-score MUST NOT be scored on — the
    caller substitutes the cross-sectional component and says so."""
    vals = [h.get("vrp_points") for h in history
            if isinstance(h.get("vrp_points"), (int, float))]
    min_n = int(cfg.get("history", {}).get("min_observations", 60))
    n = len(vals)
    if n < 8:
        return {"status": "insufficient_history", "n": n, "min_required": min_n}
    vals_sorted = sorted(vals)
    mean = sum(vals) / n
    med = vals_sorted[n // 2] if n % 2 else (vals_sorted[n // 2 - 1] + vals_sorted[n // 2]) / 2
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    std = math.sqrt(var) if var > 0 else 0.0

    def pct_at(q):
        i = (n - 1) * q
        lo, hi = int(math.floor(i)), int(math.ceil(i))
        return round(vals_sorted[lo] + (vals_sorted[hi] - vals_sorted[lo]) * (i - lo), 2)

    z = round((current_vrp_points - mean) / std, 2) if std > 0.25 else None
    pctile = round(sum(1 for v in vals if v < current_vrp_points) / n * 100.0, 1)
    return {
        "status": "ok" if n >= min_n else "insufficient_history",
        "n": n, "min_required": min_n,
        "z": z, "percentile": pctile,
        "mean": round(mean, 2), "median": round(med, 2), "std": round(std, 2),
        "p90": pct_at(0.90), "p95": pct_at(0.95), "p99": pct_at(0.99),
    }


# ── probabilities & contract economics (driftless lognormal at ERV) ─────────

def touch_prob(spot: float, strike: float, sigma: float, t_years: float) -> float | None:
    """P(price touches strike before expiry) — reflection principle on the
    driftless lognormal: 2·N(−|ln(K/S)|/(σ√T)), clamped [0,1]. Already
    through the strike → 1.0. Model probability, labeled by callers."""
    if not all(x and x > 0 for x in (spot, strike, sigma, t_years)):
        return None
    x = math.log(strike / spot)
    if x == 0:
        return 1.0
    return round(min(1.0, 2.0 * _norm_cdf(-abs(x) / (sigma * math.sqrt(t_years)))), 4)


def p_itm(spot: float, strike: float, sigma: float, t_years: float, side: str) -> float | None:
    """P(finishes ITM) under the driftless real-world lognormal
    (E[S_T] = S, log-drift −σ²/2) at σ = ExpectedRV. Conservative: no trend
    assumption either way."""
    if not all(x and x > 0 for x in (spot, strike, sigma, t_years)):
        return None
    s = sigma * math.sqrt(t_years)
    d = (math.log(strike / spot) + 0.5 * s * s) / s
    below = _norm_cdf(d)              # P(S_T <= K)
    return round(below if side == "put" else 1.0 - below, 4)


def _tail_es_short(spot, strike, sigma, t_years, side, credit, q=0.05):
    """Closed-form expected shortfall of a SHORT option: the average loss
    over the worst q-fraction of driftless-lognormal outcomes, minus the
    credit collected. Exact via the truncated lognormal partial
    expectation E[(K−S_T)⁺·1{S_T≤b}] = K·N(d) − S·N(d−s) with
    b = min(q-quantile, K) (mirrored for calls). When P(ITM) < q the tail
    contains OTM zeros and the average dilutes accordingly — that falls
    out of the formula, no special-casing. Deterministic and testable."""
    s = sigma * math.sqrt(t_years)
    if s <= 0:
        return None
    m = math.log(spot) - 0.5 * s * s          # mean of ln S_T
    zq = _z_for(q)
    if side == "put":
        a = math.exp(m - zq * s)              # lower q-quantile of S_T
        b = min(a, strike)
        if b <= 0:
            return round(max(0.0, -credit), 4)
        d = (math.log(b) - m) / s
        exp_loss = strike * _norm_cdf(d) - spot * _norm_cdf(d - s)
    else:
        a = math.exp(m + zq * s)              # upper q-quantile
        b = max(a, strike)
        d = (math.log(b) - m) / s
        exp_loss = spot * (1.0 - _norm_cdf(d - s)) - strike * (1.0 - _norm_cdf(d))
    return round(max(0.0, exp_loss / q - credit), 4)


def _z_for(q):
    # inverse normal for the handful of tail levels used; 5% is the default
    table = {0.01: 2.3263478740408408, 0.05: 1.6448536269514722, 0.10: 1.2815515655446004}
    return table.get(q, 1.6448536269514722)


def contract_economics(row: dict, spot: float, side: str, t_years: float,
                       erv: float, cfg: dict, rate: float = 0.04) -> dict | None:
    """Full §11 economics for one short contract. Credit basis = BID (the
    only number a resting seller is promised — the house rule). Fair value
    = BS at ExpectedRV with r=q=0 (real-world driftless expectation), so
    EV = bid − fair − costs is the VRP in dollars for THIS strike."""
    strike = row.get("strike")
    bid, ask = row.get("bid") or 0.0, row.get("ask") or 0.0
    if not strike or bid <= 0 or t_years <= 0 or not erv:
        return None
    ec = cfg.get("economics", {})
    mid = (bid + ask) / 2.0
    per_contract_costs = (float(ec.get("commission_per_contract", 0.65))
                          + float(ec.get("reg_fees_per_contract", 0.05))) / 100.0
    fair = _bs_price(spot, strike, t_years, erv, side, r=0.0, q=0.0)
    ev_share = bid - fair - per_contract_costs
    es_share = _tail_es_short(spot, strike, erv, t_years, side, bid,
                              q=float(ec.get("tail_q", 0.05)))
    iv_row = normalize_iv(row.get("iv"))
    delta = row.get("delta")
    if delta is None and iv_row:
        delta = _bs_delta(spot, strike, t_years, iv_row, side, r=rate)
    pt = touch_prob(spot, strike, erv, t_years)
    pi = p_itm(spot, strike, erv, t_years, side)
    intrinsic = max(0.0, (spot - strike) if side == "call" else (strike - spot))
    extrinsic = max(0.0, mid - intrinsic)
    coll = strike * 100.0 if side == "put" else spot * 100.0
    dte_cal = t_years * 365.0
    prem_pct_coll = bid * 100.0 / coll * 100.0 if coll > 0 else None
    ann = (prem_pct_coll * 365.0 / dte_cal) if (prem_pct_coll and dte_cal >= 1) else None
    em_dist = (abs(math.log(strike / spot)) / (erv * math.sqrt(t_years))) if erv > 0 else None
    out = {
        "strike": strike, "side": side,
        "bid": round(bid, 2), "ask": round(ask, 2), "mid": round(mid, 2),
        "credit_exec": round(bid, 2), "credit_basis": "bid (resting-order floor)",
        "spread_pct": round((ask - bid) / mid * 100.0, 1) if mid > 0 else None,
        "volume": row.get("volume"), "oi": row.get("openInterest"),
        # Quote provenance rides along so a consumer can gate on it: how
        # old the quote is, how much size sits at the bid and ask, and
        # whether the delta came from the broker or was computed here.
        "quote_age_s": row.get("quote_age_s"),
        "bid_size": row.get("bid_size"), "ask_size": row.get("ask_size"),
        "delta_source": ("provider" if row.get("delta") is not None
                         else ("computed" if delta is not None else None)),
        "delta": round(float(delta), 3) if delta is not None else None,
        "theta": row.get("theta"), "gamma": row.get("gamma"), "vega": row.get("vega"),
        "iv": round(iv_row, 4) if iv_row else None,
        "fair_at_erv": round(fair, 3),
        "ev_per_share": round(ev_share, 3),
        "ev_per_contract": round(ev_share * 100.0, 0),
        "es5_per_share": es_share,
        "ev_per_tail": round(ev_share / es_share, 3) if es_share and es_share > 0.01 else None,
        "p_itm_model": pi, "p_touch_model": pt,
        "prob_basis": "model (driftless lognormal at ExpectedRV30)",
        "dist_pct": round((strike / spot - 1.0) * 100.0, 2),
        "expected_moves_out": round(em_dist, 2) if em_dist is not None else None,
        "extrinsic": round(extrinsic, 2),
        "breakeven": round(strike - bid, 2) if side == "put" else round(strike + bid, 2),
        "collateral": round(coll, 0),
        "prem_pct_collateral": round(prem_pct_coll, 2) if prem_pct_coll else None,
        "annualized_pct": round(ann, 1) if ann else None,
        "theta_per_collateral": (round(abs(row.get("theta") or 0.0) * 100.0 / coll * 100.0, 3)
                                 if coll > 0 and row.get("theta") is not None else None),
    }
    return out


# ── liquidity gate ──────────────────────────────────────────────────────────

def liquidity_gate(row_or_metrics: dict, cfg: dict) -> tuple[bool, list]:
    lq = cfg.get("liquidity", {})
    why = []
    oi = row_or_metrics.get("oi") if "oi" in row_or_metrics else row_or_metrics.get("openInterest")
    vol = row_or_metrics.get("volume")
    spread = row_or_metrics.get("spread_pct")
    if oi is not None and oi < int(lq.get("min_oi", 100)):
        why.append(f"open interest {oi} < {lq.get('min_oi', 100)}")
    if vol is not None and oi is not None and vol < int(lq.get("min_volume", 1)) and oi < int(lq.get("min_oi_if_no_volume", 500)):
        why.append("no volume and thin OI")
    if spread is not None and spread > float(lq.get("max_spread_pct", 12.0)):
        why.append(f"spread {spread}% > {lq.get('max_spread_pct', 12.0)}%")
    return (len(why) == 0), why


# ── structure selection per intent ──────────────────────────────────────────

INTENTS = ("own_stock", "want_stock", "premium_only")


def _rows_for(chain: dict, exp: str, side: str) -> list:
    sides = (chain.get("chains") or {}).get(exp) or {}
    return sides.get("calls" if side == "call" else "puts") or []


def _pick_expiry(chain: dict, now: date, cfg: dict, term: dict | None) -> str | None:
    """Best expiration to sell: the term structure's richest tenor inside
    the seller window [min_dte, max_dte] — never automatically 30 DTE."""
    st = cfg.get("select", {})
    lo, hi = float(st.get("min_dte", 7)), float(st.get("max_dte", 60))
    best, best_iv = None, -1.0
    marks = {r["exp"]: r["iv"] for r in (term or {}).get("rows", [])}
    for exp in (chain.get("chains") or {}):
        dte = _expiry_dte(exp, now)
        if dte < lo or dte > hi:
            continue
        iv_here = marks.get(exp[:10], 0.0)
        # normalize richness per √T so a fat front doesn't always win on raw IV
        if iv_here > best_iv:
            best, best_iv = exp, iv_here
    return best


def _scan_side(chain, exp, side, spot, t_years, erv, cfg, rate, delta_band):
    out = []
    for r in _rows_for(chain, exp, side):
        d = r.get("delta")
        if d is None:
            continue
        try:
            ad = abs(float(d))
        except (TypeError, ValueError):
            continue
        if not (delta_band[0] <= ad <= delta_band[1]):
            continue
        m = contract_economics(r, spot, side, t_years, erv, cfg, rate)
        if m is None:
            continue
        ok, why = liquidity_gate(m, cfg)
        m["liquidity_ok"] = ok
        m["liquidity_notes"] = why
        out.append(m)
    return out


def _spread_from(short_m, rows, spot, side, t_years, erv, cfg, rate, width_pref):
    """Defined-risk vertical: long wing at ~width_pref dollars beyond the
    short strike (snapped to a real quoted strike)."""
    k = short_m["strike"]
    want = k - width_pref if side == "put" else k + width_pref
    best, gap = None, 1e18
    for r in rows:
        s = r.get("strike")
        if s is None:
            continue
        if (side == "put" and s >= k) or (side == "call" and s <= k):
            continue
        g = abs(s - want)
        if g < gap and (r.get("bid") or 0) >= 0 and (r.get("ask") or 0) > 0:
            best, gap = r, g
    if best is None:
        return None
    long_ask = best.get("ask") or 0.0
    width = abs(k - best["strike"])
    credit = short_m["credit_exec"] - long_ask
    if credit <= 0.01 or width <= 0:
        return None
    fair_long = _bs_price(spot, best["strike"], t_years, erv, side, r=0.0, q=0.0)
    ev = short_m["ev_per_share"] + (fair_long - long_ask)   # buy wing at ask, worth fair
    max_loss = width - credit
    return {
        "kind": f"{side}_credit_spread",
        "short_strike": k, "long_strike": best["strike"], "width": round(width, 2),
        "credit": round(credit, 2), "max_loss": round(max_loss, 2),
        "collateral": round(max_loss * 100.0, 0),
        "ev_per_share": round(ev, 3),
        "es5_per_share": round(min(short_m["es5_per_share"], max_loss), 4),
        "p_itm_model": short_m["p_itm_model"], "p_touch_model": short_m["p_touch_model"],
        "prem_pct_collateral": round(credit / max_loss * 100.0, 1) if max_loss > 0 else None,
        "spread_pct": short_m["spread_pct"], "oi": short_m["oi"], "volume": short_m["volume"],
        "delta": short_m["delta"],
        "liquidity_ok": short_m["liquidity_ok"], "liquidity_notes": short_m["liquidity_notes"],
    }


def select_structures(chain: dict, now: date, intent: str, erv_pack: dict,
                      cfg: dict, term: dict | None = None,
                      rate: float = 0.04) -> dict | None:
    """Ranked structures for the intent. own_stock → covered calls;
    want_stock → cash-secured puts; premium_only → defined risk only
    (put/call credit spreads + iron condor). Never an unlimited-risk short
    for premium_only. Ranking = EV per unit of tail risk, liquidity-gated."""
    st = cfg.get("select", {})
    spot = ((chain.get("underlying") or {}).get("last") or 0.0)
    erv = erv_pack.get("erv30_event") or erv_pack.get("erv30")
    if not spot or spot <= 0 or not erv:
        return None
    exp = _pick_expiry(chain, now, cfg, term)
    if exp is None:
        return None
    dte = _expiry_dte(exp, now)
    t_years = max(dte, 0.5) / 365.0
    band = tuple(st.get("short_delta_band", [0.15, 0.35]))
    width_pref = spot * float(st.get("spread_width_frac", 0.05))
    out = {"expiry": exp[:10], "dte": round(dte, 1), "intent": intent,
           "erv_used": round(erv, 4), "structures": []}

    def rank(rows):
        keyed = [r for r in rows if r.get("ev_per_share") is not None]
        return sorted(keyed, key=lambda r: (
            0 if r.get("liquidity_ok") else 1,
            -(r.get("ev_per_tail") if r.get("ev_per_tail") is not None else
              r["ev_per_share"] / 10.0)))

    if intent == "own_stock":
        calls = rank(_scan_side(chain, exp, "call", spot, t_years, erv, cfg, rate, band))
        for m in calls[:5]:
            m["kind"] = "covered_call"
            out["structures"].append(m)
    elif intent == "want_stock":
        puts = rank(_scan_side(chain, exp, "put", spot, t_years, erv, cfg, rate, band))
        for m in puts[:5]:
            m["kind"] = "cash_secured_put"
            out["structures"].append(m)
    else:  # premium_only — defined risk only
        put_shorts = rank(_scan_side(chain, exp, "put", spot, t_years, erv, cfg, rate, band))
        call_shorts = rank(_scan_side(chain, exp, "call", spot, t_years, erv, cfg, rate, band))
        put_rows = _rows_for(chain, exp, "put")
        call_rows = _rows_for(chain, exp, "call")
        pcs = ccs = None
        if put_shorts:
            pcs = _spread_from(put_shorts[0], put_rows, spot, "put", t_years, erv, cfg, rate, width_pref)
            if pcs:
                out["structures"].append(pcs)
        if call_shorts:
            ccs = _spread_from(call_shorts[0], call_rows, spot, "call", t_years, erv, cfg, rate, width_pref)
            if ccs:
                out["structures"].append(ccs)
        if pcs and ccs:
            out["structures"].append({
                "kind": "iron_condor",
                "short_put": pcs["short_strike"], "long_put": pcs["long_strike"],
                "short_call": ccs["short_strike"], "long_call": ccs["long_strike"],
                "credit": round(pcs["credit"] + ccs["credit"], 2),
                "max_loss": round(max(pcs["width"], ccs["width"]) - (pcs["credit"] + ccs["credit"]), 2),
                "collateral": round((max(pcs["width"], ccs["width"]) - (pcs["credit"] + ccs["credit"])) * 100.0, 0),
                "ev_per_share": round(pcs["ev_per_share"] + ccs["ev_per_share"], 3),
                "es5_per_share": round(max(pcs["es5_per_share"], ccs["es5_per_share"]), 4),
                "p_itm_model": round(min(1.0, (pcs["p_itm_model"] or 0) + (ccs["p_itm_model"] or 0)), 4),
                "p_touch_model": round(min(1.0, (pcs["p_touch_model"] or 0) + (ccs["p_touch_model"] or 0)), 4),
                "liquidity_ok": pcs["liquidity_ok"] and ccs["liquidity_ok"],
                "liquidity_notes": pcs["liquidity_notes"] + ccs["liquidity_notes"],
            })
    if not out["structures"]:
        return None
    out["best"] = out["structures"][0]
    return out


# ── danger model ────────────────────────────────────────────────────────────

def danger_model(features: dict, cfg: dict) -> dict:
    """JUICY vs DANGEROUS. High premium with a live reason to expect the
    move is not edge — every trigger is a MEASURED feature with its own
    threshold in config, and the reasons are always returned."""
    d = cfg.get("danger", {})
    score, reasons = 0, []

    def add(pts, why):
        nonlocal score
        score += pts
        reasons.append(why)

    if features.get("earnings_inside"):
        add(int(d.get("earnings_pts", 25)), "earnings before expiration")
    accel = features.get("rv5_over_rv20")
    if accel is not None and accel >= float(d.get("rv_accel_ratio", 1.6)):
        add(int(d.get("rv_accel_pts", 18)), f"realized vol accelerating (RV5/RV20 {accel:.2f})")
    gf = features.get("gap_freq_2pct")
    if gf is not None and gf >= float(d.get("gap_freq", 0.10)):
        add(int(d.get("gap_pts", 12)), f"gaps ≥2% on {gf*100:.0f}% of days")
    if features.get("term_shape") == "backwardation":
        add(int(d.get("backwardation_pts", 15)), "term structure inverted (front vol bid)")
    tr = features.get("ret20_pct")
    if tr is not None and abs(tr) >= float(d.get("trend_pct", 15.0)):
        add(int(d.get("trend_pts", 10)), f"large recent trend ({tr:+.0f}% / 20d)")
    sp = features.get("spread_pct")
    if sp is not None and sp >= float(d.get("spread_pct", 10.0)):
        add(int(d.get("spread_pts", 10)), f"wide spreads ({sp:.0f}%)")
    if features.get("liquidity_poor"):
        add(int(d.get("liquidity_pts", 10)), "thin option liquidity")
    rr = features.get("rr25_volpts")
    if rr is not None and abs(rr) >= float(d.get("rr_volpts", 8.0)):
        side = "downside" if rr > 0 else "upside"
        add(int(d.get("rr_pts", 8)), f"extreme {side} skew ({rr:+.1f} vol pts)")
    vixp = features.get("vix_percentile")
    if vixp is not None and vixp >= float(d.get("vix_pctile", 85.0)):
        add(int(d.get("vix_pts", 12)), f"market-wide stress (VIX {vixp:.0f}th pctile)")
    label = ("DANGEROUS" if score >= int(d.get("dangerous_at", 40))
             else "MIXED" if score >= int(d.get("mixed_at", 20)) else "JUICY")
    return {"label": label, "danger_score": min(100, score), "reasons": reasons}


# ── Premium Edge Score + signal ─────────────────────────────────────────────

def edge_score(parts: dict, cfg: dict) -> dict:
    """0-100 with a factor-by-factor breakdown. Weights are CONFIG, not
    gospel — the backtest harness exists to validate them. Statistical
    components degrade honestly: with insufficient VRP history the Z-score
    weight shifts to the cross-sectional VRP ratio and the breakdown says
    so in words."""
    w = cfg.get("score_weights", {})
    breakdown = []
    total = 0.0

    def add(key, frac, note, wkey=None, wdef=10):
        nonlocal total
        weight = float(w.get(wkey or key, wdef))
        frac = max(0.0, min(1.0, frac))
        pts = frac * weight
        total += pts
        breakdown.append({"factor": key, "pts": round(pts, 1),
                          "max": weight, "note": note})

    ratio = parts.get("vrp_ratio")
    hist = parts.get("hist") or {}
    z = hist.get("z") if hist.get("status") == "ok" else None
    z_weight = float(w.get("vrp_z", 22))
    ratio_weight = float(w.get("vrp_ratio", 18))
    if z is not None:
        add("vrp_z", min(1.0, max(0.0, z / 2.5)),
            f"VRP Z {z:+.2f} vs own history (n={hist.get('n')})", wdef=z_weight)
        add("vrp_ratio", (ratio - 1.0) / 0.5 if ratio else 0.0,
            f"IV {ratio:.2f}× expected RV" if ratio else "no ratio", wdef=ratio_weight)
    else:
        n = hist.get("n", 0)
        add("vrp_ratio", (ratio - 1.0) / 0.5 if ratio else 0.0,
            (f"IV {ratio:.2f}× expected RV — carrying the Z-score's weight too: "
             f"only {n} days of own-history (needs {hist.get('min_required', 60)})")
            if ratio else "no ratio",
            wdef=z_weight + ratio_weight)
    pct = hist.get("percentile") if hist.get("status") == "ok" else None
    if pct is not None:
        add("vrp_percentile", pct / 100.0, f"VRP at {pct:.0f}th own-history percentile", wdef=8)
    ev = parts.get("best_ev_per_tail")
    if ev is not None:
        add("ev_per_tail", min(1.0, ev / 0.5),
            f"EV/tail-risk {ev:.2f} on best structure", wdef=14)
    if parts.get("liquidity_ok") is not None:
        add("liquidity", 1.0 if parts["liquidity_ok"] else 0.0,
            "liquid" if parts["liquidity_ok"] else "liquidity below gates", wdef=10)
    sk = parts.get("skew_advantage")
    if sk is not None:
        add("skew", min(1.0, abs(sk) / 6.0),
            f"{'downside puts' if sk > 0 else 'upside calls'} rich ({sk:+.1f} vol pts)", wdef=8)
    ts = parts.get("term_advantage")
    if ts is not None:
        add("term", min(1.0, ts / 4.0), parts.get("term_note") or "term structure", wdef=6)
    dist = parts.get("expected_moves_out")
    if dist is not None:
        add("distance", min(1.0, dist / 1.5),
            f"strike {dist:.1f} expected moves out", wdef=8)
    # penalties
    danger = parts.get("danger") or {}
    dpen = float(w.get("danger_penalty", 30)) * (danger.get("danger_score", 0) / 100.0)
    if dpen > 0:
        total -= dpen
        breakdown.append({"factor": "danger", "pts": round(-dpen, 1),
                          "max": -float(w.get("danger_penalty", 30)),
                          "note": "; ".join(danger.get("reasons", [])[:3]) or "danger flags"})
    cls = parts.get("premium_class")
    if cls == "EVENT":
        epen = float(w.get("event_penalty", 18))
        total -= epen
        breakdown.append({"factor": "event", "pts": round(-epen, 1), "max": -epen,
                          "note": "premium is mostly known-event pricing, not VRP"})
    elif cls == "MIXED":
        epen = float(w.get("event_penalty", 18)) * 0.5
        total -= epen
        breakdown.append({"factor": "event", "pts": round(-epen, 1),
                          "max": -float(w.get("event_penalty", 18)),
                          "note": "part of the premium is event pricing"})
    score = int(round(max(0.0, min(100.0, total))))
    main_risk = None
    if danger.get("reasons"):
        main_risk = danger["reasons"][0]
    elif cls in ("EVENT", "MIXED"):
        main_risk = "known event inside expiration"
    return {"score": score, "breakdown": breakdown, "main_risk": main_risk}


def signal_for(score: int, vrp_ratio: float | None, danger_label: str,
               data_ok: bool, cfg: dict) -> str:
    """Signal states with hard gates. INSUFFICIENT DATA when quality gates
    failed; AVOID when the danger model says DANGEROUS regardless of score
    (maximum premium is not maximum edge)."""
    s = cfg.get("signal", {})
    if not data_ok:
        return "INSUFFICIENT DATA"
    if danger_label == "DANGEROUS":
        return "AVOID"
    if vrp_ratio is not None and vrp_ratio <= float(s.get("cheap_ratio", 0.85)):
        return "CHEAP VOL"
    if score >= int(s.get("strong_at", 80)) and (vrp_ratio or 0) >= float(s.get("strong_min_ratio", 1.20)):
        return "STRONG SELL VOL"
    if score >= int(s.get("sell_at", 65)):
        return "SELL VOL"
    if score >= int(s.get("watch_at", 45)):
        return "WATCH"
    return "FAIR"
