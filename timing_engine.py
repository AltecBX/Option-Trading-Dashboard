"""timing_engine.py — Friday 0DTE premium timing engine (spec v3.0 + v3.1).

An optimal-stopping, risk-constrained premium timing system — not another
indicator score. One job: for a short 0DTE call or put under consideration,
decide whether to sell now or wait because a meaningfully better,
risk-acceptable premium is likely before expiration.

Architecture (Phase A):
  • §1  Minute-accurate expiry math. ONE canonical clock for everything:
        T_years = seconds to the session close / (365 · 86400) — the same
        calendar-day convention as metrics.py (DAYS_PER_YEAR = 365). IV is
        backed out under this clock and reused under this clock in pricing,
        P(touch), decay and simulation. §12's "252×390" trading-clock tau is
        normalized to this same clock: both P(touch) and price consume only
        σ√T, which is invariant to the clock choice AS LONG AS backout and
        consumption share one clock — mixing clocks is the misscaling bug
        the spec forbids, and this module makes it unrepresentable.
  • §9  Monte Carlo simulation core: GBM paths (drift/range shading from the
        day regime, event windows widen ranges), repriced minute-by-minute
        via the canonical Black-Scholes primitives. Every displayed quantity
        (PBetter, expected extra credit, P(touch), P(ITM), wait edge, fill
        probabilities) reads off ONE path set — jointly coherent by
        construction (acceptance test 15).
  • §12 Analytic day-one priors stay visible next to the simulation.
  • §29/§31/§33/§34: intent-aware admissibility and breach costs,
    attention-constrained capture, final-hour mode, state hysteresis with
    margins above the Monte Carlo standard error (Amendment B).
  • §35 Reproducibility: every decision stores model version, config hash,
    input snapshot, RNG seed and path count; replay() re-derives the state
    and diffs it.

Dependencies are injected via configure() (house pattern; no dashboard
import, no cycles). Pure math + numpy; storage is JSONL/JSON under
<data_dir>/timing/. The intraday option TAPE lives in
intraday_option_store.py — this module only reads its snapshots.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import urllib.request
from datetime import date, datetime, time as _dtime, timedelta
from pathlib import Path

import numpy as np

# Canonical app-wide options math (the ONE Black-Scholes implementation).
from metrics import (_bs_price, _bs_delta, _bs_gamma, _bs_theta, _bs_vega,
                     _norm_cdf, normalize_iv, risk_free_rate)

MODEL_VERSION = "timing-1.0.0"
SCHEMA_VERSION = "1.0"
_YEAR_SECONDS = 365.0 * 86400.0          # metrics.py calendar-day convention

# ── Injected dependencies ───────────────────────────────────────────────────
_DATA_DIR: Path | None = None
_SCHWAB_GETTER = None            # () -> SchwabClient | None
_CHAIN_FN = None                 # (symbol, expiry_iso) -> normalized chain | None
_QUOTE_FN = None                 # (symbol) -> quote dict | None
_INTRADAY_FN = None              # (symbol) -> today's 1-min bars | None
_MINUTE_DAY_FN = None            # (symbol, date_iso) -> past-day 1-min bars | None
_POSITIONS_FN = None             # () -> broker positions list | None
_JUICE_FN = None                 # () -> 0DTE juice board rows | None
_PUSH_FN = None                  # (title, message) -> None
_TAPE = None                     # intraday_option_store module (optional)
_ET = None

_CFG_LOCK = threading.Lock()
_CFG_CACHE: dict = {"cfg": None, "hash": None, "ts": 0.0}
_STATE_LOCK = threading.Lock()
_HYST: dict = {}                 # contract_key -> hysteresis memory
_LAST_SEEN: dict = {}            # contract_key -> last state summary (WHAT CHANGED)
_ALERTS_SENT: dict = {}          # day -> count (attention §31 budget)
_CLOCK: dict = {"drift_s": None, "source": None, "ts": 0.0}
_EVAL_CACHE: dict = {}           # contract_key -> (ts, state) short server-side cache


def configure(data_dir, schwab_getter=None, chain_fn=None, quote_fn=None,
              intraday_fn=None, minute_day_fn=None, positions_fn=None,
              juice_fn=None, push_fn=None, tape=None, et_tz=None) -> None:
    global _DATA_DIR, _SCHWAB_GETTER, _CHAIN_FN, _QUOTE_FN, _INTRADAY_FN
    global _MINUTE_DAY_FN, _POSITIONS_FN, _JUICE_FN, _PUSH_FN, _TAPE, _ET
    _DATA_DIR = Path(data_dir)
    _SCHWAB_GETTER = schwab_getter
    _CHAIN_FN = chain_fn
    _QUOTE_FN = quote_fn
    _INTRADAY_FN = intraday_fn
    _MINUTE_DAY_FN = minute_day_fn
    _POSITIONS_FN = positions_fn
    _JUICE_FN = juice_fn
    _PUSH_FN = push_fn
    _TAPE = tape
    _ET = et_tz
    (_DATA_DIR / "timing").mkdir(parents=True, exist_ok=True)


def _now_et() -> datetime:
    return datetime.now(_ET) if _ET else datetime.utcnow()


# ── §35 Config: thresholds.json (repo defaults + data-dir overlay) ──────────

def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def config(refresh: bool = False) -> tuple[dict, str]:
    """(effective config, sha256 hash). Repo thresholds.json = defaults,
    <data_dir>/thresholds.json overrides key-by-key. Cached 60s."""
    with _CFG_LOCK:
        if (not refresh and _CFG_CACHE["cfg"] is not None
                and time.time() - _CFG_CACHE["ts"] < 60):
            return _CFG_CACHE["cfg"], _CFG_CACHE["hash"]
        repo = Path(__file__).resolve().parent / "thresholds.json"
        cfg = {}
        try:
            cfg = json.loads(repo.read_text())
        except Exception:
            cfg = {}
        if _DATA_DIR is not None:
            try:
                p = _DATA_DIR / "thresholds.json"
                if p.exists():
                    cfg = _deep_merge(cfg, json.loads(p.read_text()))
            except Exception:
                pass
        h = hashlib.sha256(json.dumps(cfg, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()[:16]
        _CFG_CACHE.update({"cfg": cfg, "hash": h, "ts": time.time()})
        return cfg, h


# ── §1 Minute-accurate session/expiry math ──────────────────────────────────

def session_close_et(d: date, cfg: dict | None = None) -> datetime:
    """The session close datetime (ET) for a given trading date, honoring
    the hand-maintained early-close calendar in config."""
    c = cfg or config()[0]
    sess = c.get("session") or {}
    early = set(sess.get("early_close_dates") or [])
    hhmm = (sess.get("early_close_et") if d.isoformat() in early
            else sess.get("regular_close_et")) or "16:00"
    hh, mm = int(hhmm[:2]), int(hhmm[3:5])
    return datetime.combine(d, _dtime(hh, mm), tzinfo=_ET) if _ET else \
        datetime.combine(d, _dtime(hh, mm))


def time_to_expiry_years(now: datetime, expiry_d: date | str,
                         cfg: dict | None = None) -> float:
    """THE canonical 0DTE time-to-expiry: actual seconds from `now` to the
    expiry date's session close, / (365·86400). Never silently 0 while
    meaningful time remains; never negative. Multi-day expiries naturally
    include nights/weekends, matching the app-wide days/365 convention at
    the daily boundary (4pm Thu → Fri close = exactly 1/365)."""
    if isinstance(expiry_d, str):
        expiry_d = date.fromisoformat(str(expiry_d)[:10])
    close = session_close_et(expiry_d, cfg)
    if now.tzinfo is None and close.tzinfo is not None:
        close = close.replace(tzinfo=None)
    return max(0.0, (close - now).total_seconds()) / _YEAR_SECONDS


def minutes_to_close(now: datetime, expiry_d: date | str,
                     cfg: dict | None = None) -> float:
    return time_to_expiry_years(now, expiry_d, cfg) * _YEAR_SECONDS / 60.0


def _t_years(minutes_remaining: float) -> float:
    return max(0.0, minutes_remaining) * 60.0 / _YEAR_SECONDS


def reprice_intraday(spot: float, target_spot: float, strike: float, side: str,
                     iv: float, minutes_now: float, minutes_at_target: float,
                     rate: float | None = None, iv_shift: float = 0.0) -> dict:
    """§1: minute-accurate reprice. Prices the option at `target_spot` with
    `minutes_at_target` remaining, using the SAME clock the IV was backed
    out under. Returns modeled premium, delta, gamma, theta per MINUTE,
    vega, and the terminal ITM probability (risk-neutral N(d2) — labeled
    analytic). Does not touch the existing daily path in option_reprice."""
    r = risk_free_rate()[0] if rate is None else rate
    sig = max(float(iv) + float(iv_shift), 1e-4)
    T_tgt = _t_years(minutes_at_target)
    px = _bs_price(target_spot, strike, T_tgt, sig, side, r=r)
    if T_tgt <= 0:
        d = 1.0 if (side == "call" and target_spot > strike) else \
            (-1.0 if (side == "put" and target_spot < strike) else 0.0)
        return {"premium": round(px, 4), "delta": d, "gamma": 0.0,
                "theta_per_min": 0.0, "vega": 0.0,
                "p_itm_terminal": 1.0 if px > 0 else 0.0,
                "T_years": 0.0, "iv_used": sig}
    delta = _bs_delta(target_spot, strike, T_tgt, sig, side, r=r)
    gamma = _bs_gamma(target_spot, strike, T_tgt, sig, r=r)
    theta_day = _bs_theta(target_spot, strike, T_tgt, sig, side, r=r)
    vega = _bs_vega(target_spot, strike, T_tgt, sig, r=r)
    sqT = math.sqrt(T_tgt)
    d1 = (math.log(target_spot / strike) + (r + 0.5 * sig * sig) * T_tgt) / (sig * sqT)
    d2 = d1 - sig * sqT
    p_itm = _norm_cdf(d2) if side == "call" else _norm_cdf(-d2)
    return {"premium": round(px, 4), "delta": round(delta, 4),
            "gamma": round(gamma, 6), "theta_per_min": round(theta_day / 1440.0, 6),
            "vega": round(vega, 4), "p_itm_terminal": round(p_itm, 4),
            "T_years": T_tgt, "iv_used": sig}


def iv_usability(bid, ask, cfg: dict | None = None) -> str:
    """§1 [V3] low-premium guard. 'ok' | 'unusable_low_premium' |
    'unusable_spread' | 'unusable_no_quote'. Backing IV out of a 0.03 bid
    is noise dressed as a parameter."""
    c = (cfg or config()[0]).get("risk") or {}
    try:
        b, a = float(bid), float(ask)
    except (TypeError, ValueError):
        return "unusable_no_quote"
    if b <= 0 or a <= 0 or a < b:
        return "unusable_no_quote"
    if b < float(c.get("iv_unusable_bid_under", 0.10)):
        return "unusable_low_premium"
    mid = (a + b) / 2.0
    if mid > 0 and (a - b) / mid * 100.0 > float(c.get("iv_unusable_spread_pct", 60.0)):
        return "unusable_spread"
    return "ok"


def implied_vol_intraday(price: float, spot: float, strike: float,
                         minutes_remaining: float, side: str,
                         rate: float | None = None) -> float | None:
    """Back IV out of an observed price with minute-accurate T (bisection
    against the canonical _bs_price). None when at/below intrinsic or T=0."""
    r = risk_free_rate()[0] if rate is None else rate
    T = _t_years(minutes_remaining)
    if T <= 0 or not spot or not strike or price is None:
        return None
    intrinsic = max(spot - strike, 0.0) if side == "call" else max(strike - spot, 0.0)
    if price <= intrinsic + 1e-8:
        return None
    lo, hi = 1e-4, 8.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if _bs_price(spot, strike, T, mid, side, r=r) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def touch_probability(spot: float, strike: float, iv: float,
                      minutes_remaining: float, side: str) -> float | None:
    """§12 analytic prior: driftless reflection form, P = 2·N(−d),
    d = ln(K/S)/(σ√τ) for calls (ln(S/K) puts), τ on the SAME canonical
    clock the IV was backed out under (see module docstring — σ√τ is the
    clock-invariant quantity; one clock everywhere is the §1 rule)."""
    T = _t_years(minutes_remaining)
    if not spot or not strike or not iv or T <= 0:
        return None
    if (side == "call" and spot >= strike) or (side == "put" and spot <= strike):
        return 1.0
    lg = math.log(strike / spot) if side == "call" else math.log(spot / strike)
    d = lg / (iv * math.sqrt(T))
    return max(0.0, min(1.0, 2.0 * _norm_cdf(-d)))


# ── Clock discipline (Step 0.5) ─────────────────────────────────────────────

def check_clock(force: bool = False) -> dict:
    """Measure local-vs-server clock drift from an HTTPS Date header
    (whole-second resolution; good enough against a 40s bound). Cached per
    config recheck interval. Unknown drift is labeled, not blocking."""
    cfg, _ = config()
    ttl = float((cfg.get("clock") or {}).get("recheck_minutes", 60)) * 60.0
    if not force and _CLOCK["ts"] and time.time() - _CLOCK["ts"] < ttl:
        return dict(_CLOCK)
    drift, src = None, None
    for url in ("https://api.schwabapi.com/", "https://www.google.com/generate_204"):
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=5) as resp:
                hdr = resp.headers.get("Date")
            if hdr:
                from email.utils import parsedate_to_datetime
                server = parsedate_to_datetime(hdr).timestamp()
                drift = round(abs(time.time() - server), 1)
                src = url.split("/")[2]
                break
        except Exception:
            continue
    _CLOCK.update({"drift_s": drift, "source": src, "ts": time.time()})
    return dict(_CLOCK)


def _clock_blocked(cfg: dict) -> str | None:
    bound = float((cfg.get("clock") or {}).get("max_drift_seconds", 40))
    d = _CLOCK.get("drift_s")
    if d is not None and d > bound:
        return f"clock drift {d:.0f}s exceeds the {bound:.0f}s bound — decision states blocked"
    return None


# ── §15 Scheduled events (features that widen ranges; optional blocks) ──────

def _todays_events(now: datetime, cfg: dict) -> list[dict]:
    """CPI (8:30 ET) / FOMC (14:00 ET) from treasury.MACRO_SCHEDULE plus the
    jobs report (first Friday, 8:30 ET). Sourced from the repo's maintained
    calendar — never invented (Amendment F)."""
    out = []
    d = now.date()
    try:
        from treasury import MACRO_SCHEDULE
        cpis = sorted(x for ds in (MACRO_SCHEDULE.get("cpi") or {}).values() for x in ds)
        if d.isoformat() in cpis:
            out.append({"kind": "cpi", "at_et": "08:30"})
        if d.isoformat() in (MACRO_SCHEDULE.get("fomc") or []):
            out.append({"kind": "fomc", "at_et": "14:00"})
    except Exception:
        pass
    first_fri = date(d.year, d.month, 1)
    first_fri += timedelta(days=(4 - first_fri.weekday()) % 7)
    if d == first_fri:
        out.append({"kind": "jobs", "at_et": "08:30"})
    return out


def _event_context(now: datetime, cfg: dict) -> dict:
    """{widen: bool, block: str|None, events: [...]} — inside the configured
    window around an event the simulated range widens; per-class block
    windows (config) hard-block decision states, visibly."""
    ev_cfg = cfg.get("events") or {}
    win = float(ev_cfg.get("range_widen_window_min", 20))
    blocks = ev_cfg.get("block_windows_min") or {}
    events = _todays_events(now, cfg)
    widen, block = False, None
    for ev in events:
        hh, mm = int(ev["at_et"][:2]), int(ev["at_et"][3:5])
        ev_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        dist_min = abs((now - ev_dt).total_seconds()) / 60.0
        if dist_min <= win:
            widen = True
        bwin = float(blocks.get(ev["kind"], 0) or 0)
        if bwin > 0 and dist_min <= bwin:
            block = f"{ev['kind'].upper()} window (±{bwin:.0f} min around {ev['at_et']} ET)"
    return {"widen": widen, "block": block, "events": events}


# ── Intents (§29) ───────────────────────────────────────────────────────────

def intent_for(symbol: str, kind: str, cfg: dict | None = None) -> str:
    c = (cfg or config()[0]).get("intents") or {}
    per = c.get("per_position") or {}
    v = per.get(f"{symbol.upper()}|{kind}") or per.get(symbol.upper()) \
        or c.get("default") or "income_only"
    return v if v in ("income_only", "wheel_acceptable") else "income_only"


def set_intent(symbol: str, kind: str | None, intent: str) -> dict:
    """Persist a per-position intent into the data-dir thresholds overlay."""
    if intent not in ("income_only", "wheel_acceptable"):
        return {"error": "intent must be income_only or wheel_acceptable"}
    if _DATA_DIR is None:
        return {"error": "engine not configured"}
    p = _DATA_DIR / "thresholds.json"
    try:
        over = json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        over = {}
    key = f"{symbol.upper()}|{kind}" if kind else symbol.upper()
    over.setdefault("intents", {}).setdefault("per_position", {})[key] = intent
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(over, indent=1))
    tmp.replace(p)
    config(refresh=True)
    return {"ok": True, "key": key, "intent": intent}


def _limits_for(intent: str, cfg: dict) -> dict:
    r = cfg.get("risk") or {}
    prof = r.get(intent) or {}
    return {"max_p_itm": float(prof.get("max_p_itm", 0.15)),
            "max_p_touch": float(prof.get("max_p_touch", 0.35)),
            "max_delta": float(prof.get("max_delta", 0.30)),
            "breach_cost_mult": float(prof.get("breach_cost_mult", 2.0))}


# ── §9 Simulation core ──────────────────────────────────────────────────────

def _seed_from(decision_id: str) -> int:
    """Deterministic RNG seed from the decision id (Amendment B): replay of
    a stored decision reruns the identical path set."""
    return int(hashlib.sha256(decision_id.encode()).hexdigest()[:12], 16)


def simulate(snap: dict, cfg: dict, seed: int) -> dict:
    """One seeded Monte Carlo run; every output reads off the same path set.

    snap: {spot, strike, kind, iv, minutes_remaining, bid, spread_frac,
           trend_dir (-1/0/+1 toward the premium side), widen (bool),
           limits {max_p_itm,max_p_touch,max_delta,breach_cost_mult},
           look_interval_min, latency_min, min_improve_frac}
    Returns the §9 joint quantities + per-minute grids the caller needs for
    limits/fill stats. Pure function of (snap, cfg, seed) — replayable.
    """
    sim = cfg.get("simulation") or {}
    n_paths = int(sim.get("paths", 2000))
    spot = float(snap["spot"]); strike = float(snap["strike"])
    kind = snap["kind"]; iv = float(snap["iv"])
    M = int(max(1, round(snap["minutes_remaining"])))
    M = min(M, 400)
    bid_now = float(snap.get("bid") or 0.0)
    spread_frac = min(max(float(snap.get("spread_frac") or 0.06), 0.02), 0.60)
    r = risk_free_rate()[0]
    lim = snap["limits"]
    sign = 1.0 if kind == "call" else -1.0

    dt = 60.0 / _YEAR_SECONDS                       # one minute, canonical clock
    sig = iv * (1.0 + (float(sim.get("trend_range_widen_frac", 0.20))
                       if snap.get("trend_dir") else 0.0)
                + (float(sim.get("event_range_widen_frac", 0.25))
                   if snap.get("widen") else 0.0))
    sig_step = sig * math.sqrt(dt)
    # Drift shading (§9): total log drift over the remaining session =
    # trend_dir × frac × σ√T_rem, spread evenly across steps. Small, few
    # parameters, estimable — labeled SIMULATION PRIOR.
    T_rem = M * dt
    shade = float(sim.get("trend_drift_shade_sigma_frac", 0.35))
    mu_total = float(snap.get("trend_dir") or 0) * shade * sig * math.sqrt(T_rem)
    mu_step = mu_total / M

    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n_paths, M))
    log_steps = (mu_step - 0.5 * sig_step * sig_step) + sig_step * z
    logS = np.cumsum(log_steps, axis=1) + math.log(spot)
    S = np.exp(logS)                                # (paths, M) spot at end of each minute

    # Per-minute conditional quantities (vectorized per step over paths).
    minutes_left = M - np.arange(1, M + 1)          # after step j, minutes remaining
    T_left = np.maximum(minutes_left * dt, 0.0)
    bid_grid = np.zeros((n_paths, M), dtype=np.float32)
    adm_grid = np.zeros((n_paths, M), dtype=bool)
    p_itm_now_grid = np.zeros((n_paths, M), dtype=np.float32)
    sqrt_T = np.sqrt(np.maximum(T_left, 1e-12))
    for j in range(M):
        Tj = T_left[j]
        Sj = S[:, j]
        if Tj <= 0:
            intr = np.maximum(sign * (Sj - strike), 0.0)
            bid_grid[:, j] = np.maximum(intr * (1.0 - spread_frac / 2.0), 0.0)
            itm = (sign * (Sj - strike)) > 0
            adm_grid[:, j] = ~itm if lim["max_p_itm"] < 1.0 else True
            p_itm_now_grid[:, j] = itm.astype(np.float32)
            continue
        sq = sqrt_T[j]
        d1 = (np.log(Sj / strike) + (r + 0.5 * sig * sig) * Tj) / (sig * sq)
        d2 = d1 - sig * sq
        nd1 = 0.5 * (1.0 + _erf_vec(d1 / math.sqrt(2.0)))
        nd2 = 0.5 * (1.0 + _erf_vec(d2 / math.sqrt(2.0)))
        disc = math.exp(-r * Tj)
        if kind == "call":
            mid_model = Sj * nd1 - strike * disc * nd2
            delta = nd1
            p_itm = nd2
            lg = np.log(np.maximum(strike / Sj, 1e-12))
        else:
            mid_model = strike * disc * (1.0 - nd2) - Sj * (1.0 - nd1)
            delta = nd1 - 1.0
            p_itm = 1.0 - nd2
            lg = np.log(np.maximum(Sj / strike, 1e-12))
        # Forward touch prob from this state (reflection, same clock/σ).
        d_touch = lg / (sig * sq)
        p_touch = np.clip(2.0 * (1.0 - 0.5 * (1.0 + _erf_vec(d_touch / math.sqrt(2.0)))), 0.0, 1.0)
        p_touch = np.where(sign * (Sj - strike) >= 0, 1.0, p_touch)
        bid_grid[:, j] = np.maximum(mid_model * (1.0 - spread_frac / 2.0), 0.0)
        p_itm_now_grid[:, j] = p_itm
        adm_grid[:, j] = ((p_itm <= lim["max_p_itm"])
                          & (p_touch <= lim["max_p_touch"])
                          & (np.abs(delta) <= lim["max_delta"]))

    # Anchor the simulated EXTRINSIC premium to the live quote: when the
    # working IV doesn't exactly reprice the observed bid (chain IV, interp,
    # spread noise), the whole future grid would sit above/below the market
    # and PBetter would be structurally biased. Scale extrinsic value only —
    # intrinsic is cash and never rescales. Labeled quote calibration; the
    # Phase B premium-surface residuals replace it.
    T_now = M * dt
    mid_model_now = _bs_price(spot, strike, T_now, sig, kind, r=r)
    bid_model_now = max(mid_model_now * (1.0 - spread_frac / 2.0), 0.0)
    intr_now = max(sign * (spot - strike), 0.0) * (1.0 - spread_frac / 2.0)
    scale = 1.0
    if bid_now > 0.02 and bid_model_now - intr_now > 0.02:
        scale = (bid_now - intr_now) / (bid_model_now - intr_now)
        scale = min(max(scale, 0.5), 2.0)
    if scale != 1.0:
        intr_grid = np.maximum(sign * (S - strike), 0.0) * (1.0 - spread_frac / 2.0)
        bid_grid = intr_grid + (bid_grid - intr_grid) * scale
        np.maximum(bid_grid, 0.0, out=bid_grid)

    # Touch / terminal ITM — joint, from the same paths (acceptance test 15).
    beyond = sign * (S - strike) >= 0
    touched = beyond.any(axis=1)
    itm_terminal = beyond[:, -1]
    p_touch_sim = float(touched.mean())
    p_itm_sim = float(itm_terminal.mean())

    min_improve = bid_now * float(snap.get("min_improve_frac", 0.08))
    thresh = bid_now + max(min_improve, 0.02)
    adm_bid = np.where(adm_grid, bid_grid, 0.0)

    # PBetter at horizons: does an ADMISSIBLE executable bid > now + margin
    # EXIST within the horizon (§6 existence; capture is the §31 question).
    horizons = list((cfg.get("simulation") or {}).get("horizons_min", [5, 15, 30, 60]))
    p_better = {}
    for h in horizons:
        hj = min(M, int(h))
        if hj <= 0:
            continue
        p_better[f"h{h}"] = float((adm_bid[:, :hj].max(axis=1) > thresh).mean())
    best_to_close = adm_bid.max(axis=1)
    p_better["close"] = float((best_to_close > thresh).mean())
    extra_close = np.maximum(best_to_close - bid_now, 0.0)
    expected_extra = float(extra_close.mean())
    # §5 Hazardous Premium: the raw (risk-ignoring) opportunity vs the
    # admissible one. The gap is premium you could only have collected by
    # violating your own limits — shown, never scored.
    best_raw = bid_grid.max(axis=1)
    p_better_raw = float((best_raw > thresh).mean())
    expected_extra_raw = float(np.maximum(best_raw - bid_now, 0.0).mean())

    # First time an admissible better bid appears (minutes), where it does.
    better_mask = adm_bid > thresh
    any_better = better_mask.any(axis=1)
    first_better = np.where(any_better, better_mask.argmax(axis=1) + 1, 0)
    exp_time_to_better = (float(first_better[any_better].mean())
                          if any_better.any() else None)

    # §31 attention-constrained capture: look times every look_interval
    # (+ latency), plus a final look just before the close. Chase mode.
    look_iv = float(snap.get("look_interval_min", 30))
    lat = float(snap.get("latency_min", 3))
    looks = []
    t = look_iv
    while t + lat < M:
        looks.append(int(t + lat) - 1)
        t += look_iv
    if M >= 3:
        looks.append(M - 2)                          # you can always act near the bell
    looks = sorted(set(i for i in looks if 0 <= i < M))
    if looks:
        look_bids = adm_bid[:, looks]
        capture = look_bids.max(axis=1)              # best admissible sale at a look
    else:
        capture = adm_bid[:, -1]
    p_better_next_look = None
    if looks:
        j0 = looks[0]
        p_better_next_look = float((adm_bid[:, j0] > thresh).mean())
        expected_extra_next_look = float(np.maximum(adm_bid[:, j0] - bid_now, 0.0).mean())
    else:
        expected_extra_next_look = 0.0

    # Intent-aware WAIT EDGE (§9/§29): E[best admissible capturable sale]
    # − CreditNow − breach penalty on paths that DIED inadmissible (never
    # captured ≥ now while the strike got run over): the waiting branch was
    # exposed to the scenario where the opportunity converted into danger.
    died = (capture <= 0.0) & touched
    intrinsic_T = np.maximum(sign * (S[:, -1] - strike), 0.0)
    penalty = lim["breach_cost_mult"] * np.where(died, intrinsic_T, 0.0)
    wait_edge = float((capture - penalty).mean() - bid_now)

    # Risk headroom: adverse spot move (toward the strike) that fits before
    # the intent's P(ITM) limit breaks, from the analytic p_itm at now.
    headroom = _risk_headroom(spot, strike, kind, sig, M * dt, r, lim["max_p_itm"])

    n = float(n_paths)
    pb_close = p_better.get("close", 0.0)
    mc_se = math.sqrt(max(pb_close * (1.0 - pb_close), 1e-4) / n)
    return {"p_touch": p_touch_sim, "p_itm": p_itm_sim,
            "p_better": p_better, "p_better_next_look": p_better_next_look,
            "p_better_raw": p_better_raw,
            "expected_extra_raw": expected_extra_raw,
            "hazardous_premium": round(max(expected_extra_raw - expected_extra, 0.0), 4),
            "expected_extra": expected_extra,
            "expected_extra_next_look": expected_extra_next_look,
            "expected_time_to_better_min": exp_time_to_better,
            "wait_edge": wait_edge, "capture_mean": float(capture.mean()),
            "died_frac": float(died.mean()),
            "risk_headroom_dollars": headroom,
            "mc_se": mc_se, "paths": n_paths, "steps": M,
            "sigma_used": sig,
            "_grids": {"S": S, "bid": bid_grid, "adm": adm_grid,
                       "minutes_left": minutes_left}}


def _erf_vec(x):
    """Vectorized erf without scipy: Abramowitz–Stegun 7.1.26 (|err|<1.5e-7,
    far below Monte Carlo noise at any configured path count)."""
    sign = np.sign(x)
    ax = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * ax)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
                - 0.284496736) * t + 0.254829592) * t * np.exp(-ax * ax)
    return sign * y


def _risk_headroom(spot, strike, kind, sig, T, r, max_p_itm) -> float | None:
    """Largest adverse move (toward the strike) keeping analytic P(ITM)
    within the limit. Bisection on spot; None when already beyond."""
    if T <= 0:
        return None

    def p_itm_at(s):
        sq = math.sqrt(T)
        d1 = (math.log(s / strike) + (r + 0.5 * sig * sig) * T) / (sig * sq)
        d2 = d1 - sig * sq
        return _norm_cdf(d2) if kind == "call" else _norm_cdf(-d2)

    if p_itm_at(spot) >= max_p_itm:
        return 0.0
    lo, hi = 0.0, abs(strike - spot) * 1.5 + spot * 0.05
    step_dir = 1.0 if kind == "call" else -1.0
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if p_itm_at(spot + step_dir * mid) < max_p_itm:
            lo = mid
        else:
            hi = mid
    return round(lo, 2)


# ── §10 Premium change decomposition + scenarios (display only) ─────────────

def decomposition(spot, strike, kind, iv, minutes_remaining, bid, spread_frac,
                  vw: dict | None, cfg: dict) -> dict:
    """dV ≈ Δ·dS + ½Γ·dS² + Θ·dt + Vega·dIV over the next 15 minutes with a
    typical |dS| (σ-scaled), plus §10 scenario rows. Explanatory ONLY —
    decisions come from the path distributions."""
    base = reprice_intraday(spot, spot, strike, kind, iv,
                            minutes_remaining, minutes_remaining)
    d, g, th_min, v = base["delta"], base["gamma"], base["theta_per_min"], base["vega"]
    dS = spot * iv * math.sqrt(15 * 60.0 / _YEAR_SECONDS)     # 1σ 15-min move
    sign = 1.0 if kind == "call" else -1.0
    decomp = {"spot": round(sign * d * dS, 3), "gamma": round(0.5 * g * dS * dS, 3),
              "theta": round(th_min * 15.0, 3), "iv": round(v * -1.0, 3),
              "execution": round(-(bid or 0) * spread_frac / 2.0, 3),
              "dS_used": round(dS, 2)}
    scen = []

    def row(label, target, mins_ahead, ivs=0.0):
        m_at = max(minutes_remaining - mins_ahead, 0.0)
        rp = reprice_intraday(spot, target, strike, kind, iv,
                              minutes_remaining, m_at, iv_shift=ivs)
        scen.append({"label": label, "target_spot": round(target, 2),
                     "premium": rp["premium"],
                     "exec_bid": round(max(rp["premium"] * (1 - spread_frac / 2), 0.0), 2),
                     "delta": rp["delta"], "p_itm": rp["p_itm_terminal"]})

    ext = _projected_extreme(spot, iv, minutes_remaining, kind, vw)
    row("spot unchanged, 15 min", spot, 15)
    row("tags projected extreme, 15 min", ext, 15)
    row("tags projected extreme, 30 min", ext, 30)
    back = spot - sign * 0.5 * dS
    row("reverses now (½σ against)", back, 10)
    row("IV −5 pts", spot, 15, ivs=-0.05)
    return {"decomposition": decomp, "scenarios": scen,
            "projected_extreme": round(ext, 2)}


def _projected_extreme(spot, iv, minutes_remaining, kind, vw: dict | None) -> float:
    """Phase A projected session extreme in the premium direction: the
    already-printed extreme extended by the remaining 1σ move (labeled
    SIMULATION PRIOR; the Phase A2 trained extension model replaces this)."""
    sig_rem = spot * iv * math.sqrt(_t_years(minutes_remaining))
    hod = (vw or {}).get("hod")
    lod = (vw or {}).get("lod")
    if kind == "call":
        base = max(spot, hod or spot)
        return base + sig_rem
    base = min(spot, lod or spot)
    return max(base - sig_rem, 0.01)


# ── §14 Three resting limits ────────────────────────────────────────────────

def seed_limits(spot, strike, kind, iv, minutes_remaining, spread_frac,
                sim_out: dict, vw: dict | None, cfg: dict) -> dict:
    """Likely / Balanced / Stretch resting limits (Phase A seed rules) with
    fill stats read off the SAME simulation paths (fill = any minute's
    executable bid ≥ limit — a standing order can be lifted by a one-second
    spike, §32 RESTING benchmark)."""
    lc = cfg.get("limits") or {}
    ext = _projected_extreme(spot, iv, minutes_remaining, kind, vw)
    sign = 1.0 if kind == "call" else -1.0
    extension = sign * (ext - spot)

    def credit_at(target):
        rp = reprice_intraday(spot, target, strike, kind, iv,
                              minutes_remaining, max(minutes_remaining - 20, 1))
        return max(rp["premium"] * (1 - spread_frac / 2.0), 0.01), rp

    likely_spot = spot + sign * float(lc.get("likely_extension_frac", 0.70)) * extension
    bal_spot = ext
    stretch_spot = None
    if vw and vw.get("vwap") and vw.get("sigma"):
        stretch_spot = vw["vwap"] + sign * float(lc.get("stretch_sigma", 2.5)) * vw["sigma"]
    if stretch_spot is None or sign * (stretch_spot - bal_spot) < 0:
        stretch_spot = spot + sign * 1.35 * extension

    grids = sim_out.get("_grids") or {}
    bid_grid = grids.get("bid")
    out = {}
    for name, tgt, mult in (("likely", likely_spot, 1.0),
                            ("balanced", bal_spot, float(lc.get("balanced_extreme_mult", 0.85))),
                            ("stretch", stretch_spot, 1.0)):
        credit, rp = credit_at(tgt)
        limit_px = round(max(credit * mult, 0.01), 2)
        fill_p = fill_t = None
        if bid_grid is not None:
            hit = bid_grid >= limit_px
            anyhit = hit.any(axis=1)
            fill_p = float(anyhit.mean())
            if anyhit.any():
                fill_t = float((hit.argmax(axis=1) + 1)[anyhit].mean())
        out[name] = {"price": limit_px, "spot_required": round(tgt, 2),
                     "fill_prob": fill_p, "expected_minutes_to_fill": fill_t,
                     "delta_if_filled": rp["delta"], "p_itm_if_filled": rp["p_itm_terminal"],
                     "p_touch_if_filled": touch_probability(tgt, strike, iv,
                                                            max(minutes_remaining - 20, 1), kind),
                     "basis": "seed rule (§14 Phase A)"}
    return out


# ── v2 heuristic cross-check (kept per spec Phase A) ────────────────────────

def session_variance_edge(minute_bars: list, iv: float, minutes_remaining: float,
                          cfg: dict) -> dict | None:
    """Remaining-session volatility edge (Premium Edge §19 integration).

    Compares what the option's IV implies for the REST of today against
    what today's own tape projects: implied remaining variance =
    iv²·T_rem (the engine's one 365-calendar clock — iv was backed out on
    it, so the product is clock-consistent total variance), forecast
    remaining variance = realized per-minute variance so far × minutes
    left (flat projection; the U-shape refinement waits on tape history).
    Positive session VRP = the premium prices more movement than the tape
    is producing — a nudge TOWARD selling now, capped at score_nudge_max
    points so it can shade but never drive the decision. Returns None
    (silently, no penalty) when disabled, early (< min_elapsed_minutes of
    tape), or the data is unusable — absence of the edge is never a block."""
    pcfg = ((cfg.get("premium_edge") or {}).get("session_vrp") or {})
    if not pcfg.get("enabled", True):
        return None
    rets, prev = [], None
    for b in (minute_bars or []):
        c = b.get("close")
        if c and prev and c > 0 and prev > 0:
            rets.append(math.log(c / prev))
        if c and c > 0:
            prev = c
    if len(rets) < float(pcfg.get("min_elapsed_minutes", 45)):
        return None
    mean = sum(rets) / len(rets)
    var_pm = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var_pm <= 0 or not iv or iv <= 0 or minutes_remaining <= 1:
        return None
    forecast_rem_var = var_pm * minutes_remaining
    implied_rem_var = iv * iv * _t_years(minutes_remaining)
    if implied_rem_var <= 0:
        return None
    edge = (implied_rem_var - forecast_rem_var) / implied_rem_var
    nudge = max(-1.0, min(1.0, edge / 0.5)) * float(pcfg.get("score_nudge_max", 6.0))
    return {
        "session_vrp_pct": round(edge * 100.0, 1),
        "implied_rem_move_pct": round(math.sqrt(implied_rem_var) * 100.0, 2),
        "forecast_rem_move_pct": round(math.sqrt(forecast_rem_var) * 100.0, 2),
        "realized_vol_ann": round(math.sqrt(var_pm * _YEAR_SECONDS / 60.0), 4),
        "elapsed_minutes": len(rets),
        "score_nudge": round(nudge, 1),
        "basis": ("MEASURED session tape vs implied (365-calendar clock); flat "
                  "remaining-minute projection"),
    }


def heuristic_check(bars: list, kind: str, credit_now: float,
                    expected_extra: float, vw: dict | None) -> dict | None:
    """The transparent v2 heuristic retained as a cross-check display:
    final = raw × M. M = 0.0 when a new session extreme printed within the
    last 2 bars and the last 3 bars closed in the trend direction; 0.5 when
    a new extreme printed within 5 bars with no rejection/bounce close yet;
    1.0 after a rejection/bounce close or 5 bars without a new extreme.
    Raw score from VWAP z, position in the projected band, modeled wait
    cost. Hard disagreement with the simulation drops confidence."""
    closes = [b for b in (bars or []) if b.get("close") is not None]
    if len(closes) < 10 or not vw:
        return None
    highs = [b.get("high", b["close"]) for b in closes]
    lows = [b.get("low", b["close"]) for b in closes]
    n = len(closes)
    up = kind == "call"
    ext_series = highs if up else lows
    ext_val = max(ext_series) if up else min(ext_series)
    ext_i = (max(range(n), key=lambda i: highs[i]) if up
             else min(range(n), key=lambda i: lows[i]))
    bars_since = n - 1 - ext_i
    last3 = closes[-3:]
    trend_closes = all(b["close"] >= b["open"] for b in last3) if up else \
        all(b["close"] <= b["open"] for b in last3)
    rejection = ((closes[-1]["close"] < ext_val * 0.9985) if up
                 else (closes[-1]["close"] > ext_val * 1.0015))
    if bars_since <= 2 and trend_closes:
        m = 0.0
    elif bars_since <= 5 and not rejection:
        m = 0.5
    else:
        m = 1.0
    stretch = vw.get("stretch") or 0.0
    signed = stretch if up else -stretch          # stretched toward premium side
    s_stretch = max(0.0, min(signed / 3.0, 1.0)) * 45.0
    wait_cost = 0.0
    if credit_now > 0:
        wait_cost = max(0.0, 1.0 - min(expected_extra / max(credit_now * 0.10, 0.03), 1.0)) * 35.0
    band_pos = 20.0 if rejection else 8.0
    raw = s_stretch + wait_cost + band_pos
    final = raw * m
    state = ("SELL ZONE" if final >= 60 else
             "GETTING CLOSE" if final >= 41 else "WAIT")
    return {"score": round(final), "raw": round(raw), "momentum_gate_m": m,
            "state": state, "bars_since_extreme": bars_since,
            "layer": "HEURISTIC"}


# ── §34 Hysteresis ──────────────────────────────────────────────────────────

_ESCALATE_ORDER = ["WAIT", "GETTING CLOSE", "SELL ZONE", "STRONG SELL ZONE"]


def _apply_hysteresis(key: str, raw_state: str, score, mc_se: float,
                      cfg: dict) -> tuple[str, dict]:
    """Displayed state changes only when the score crosses its band edge by
    a margin (raised to ≥ k× the MC standard error in score points,
    Amendment B) or the raw state persists N evaluations. Exempt states
    fire immediately."""
    h = cfg.get("hysteresis") or {}
    exempt = set(h.get("exempt_states") or [])
    with _STATE_LOCK:
        mem = _HYST.setdefault(key, {"displayed": raw_state, "pending": None,
                                     "count": 0, "score": score})
        if raw_state in exempt or mem["displayed"] in exempt or score is None:
            mem.update({"displayed": raw_state, "pending": None, "count": 0,
                        "score": score})
            return raw_state, {"held": False}
        if raw_state == mem["displayed"]:
            mem.update({"pending": None, "count": 0, "score": score})
            return raw_state, {"held": False}
        margin = max(float(h.get("margin_points", 6)),
                     float(h.get("min_margin_mc_se_mult", 2.0)) * mc_se * 100.0)
        edge = _band_edge_between(mem["displayed"], raw_state, cfg)
        crossed_hard = edge is not None and abs(score - edge) >= margin
        if mem["pending"] == raw_state:
            mem["count"] += 1
        else:
            mem["pending"], mem["count"] = raw_state, 1
        if crossed_hard or mem["count"] >= int(h.get("persist_evals", 2)):
            mem.update({"displayed": raw_state, "pending": None, "count": 0,
                        "score": score})
            return raw_state, {"held": False}
        return mem["displayed"], {"held": True, "pending": raw_state,
                                  "needs": int(h.get("persist_evals", 2)) - mem["count"],
                                  "margin": round(margin, 1)}


def _band_edge_between(a: str, b: str, cfg: dict) -> float | None:
    bands = cfg.get("bands") or {}
    edges = {("WAIT", "GETTING CLOSE"): float(bands.get("wait_max", 40)) + 0.5,
             ("GETTING CLOSE", "SELL ZONE"): float(bands.get("getting_close_max", 59)) + 0.5,
             ("SELL ZONE", "STRONG SELL ZONE"): float(bands.get("sell_zone_max", 79)) + 0.5}
    for (x, y), e in list(edges.items()):
        if (a, b) in ((x, y), (y, x)):
            return e
    ia = _ESCALATE_ORDER.index(a) if a in _ESCALATE_ORDER else None
    ib = _ESCALATE_ORDER.index(b) if b in _ESCALATE_ORDER else None
    if ia is not None and ib is not None and abs(ia - ib) > 1:
        return None                                   # multi-band jump: persistence path
    return None


def _score_to_state(score: float, cfg: dict) -> str:
    bands = cfg.get("bands") or {}
    if score <= float(bands.get("wait_max", 40)):
        return "WAIT"
    if score <= float(bands.get("getting_close_max", 59)):
        return "GETTING CLOSE"
    if score <= float(bands.get("sell_zone_max", 79)):
        return "SELL ZONE"
    return "STRONG SELL ZONE"


# ── Decision log (§4/§35) ───────────────────────────────────────────────────

def _timing_dir() -> Path:
    d = (_DATA_DIR or Path(".")) / "timing"
    d.mkdir(parents=True, exist_ok=True)
    return d


_LOG_LOCK = threading.Lock()


_LOG_KEEP = ("schema_version", "decision_id", "ts_et", "symbol", "contract",
             "intent", "state", "state_raw", "reason", "score", "confidence",
             "layer", "blocked", "probabilities", "model", "_sim")


def _log_decision(state: dict) -> None:
    """Append-only JSONL by day. Every displayed decision is reproducible:
    the record keeps the full input snapshot + seed + config hash (§35) but
    drops display-only weight (scenarios, limits, heuristic prose) so a
    full Friday of once-a-minute evals stays ~1MB/contract. Files older
    than 30 days are pruned opportunistically."""
    try:
        p = _timing_dir() / "decisions"
        p.mkdir(exist_ok=True)
        day = state["ts_et"][:10]
        rec = {k: state.get(k) for k in _LOG_KEEP if k in state}
        rec["credit"] = {k: (state.get("credit") or {}).get(k)
                         for k in ("bid", "ask", "mid", "spread_pct", "quote_age_s")}
        w = state.get("wait") or {}
        rec["wait"] = {k: w.get(k) for k in ("expected_extra_credit", "wait_edge")}
        with _LOG_LOCK:
            with open(p / f"{day}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
                f.flush()
        cutoff = (_now_et().date() - timedelta(days=30)).isoformat()
        for f in p.glob("*.jsonl"):
            if f.stem < cutoff:
                try:
                    f.unlink()
                except Exception:
                    pass
    except Exception:
        pass


def _find_decision(decision_id: str) -> dict | None:
    p = _timing_dir() / "decisions"
    if not p.exists():
        return None
    for f in sorted(p.glob("*.jsonl"), reverse=True)[:14]:
        try:
            for line in f.read_text().splitlines():
                if decision_id in line:
                    rec = json.loads(line)
                    if rec.get("decision_id") == decision_id:
                        return rec
        except Exception:
            continue
    return None


def replay(decision_id: str) -> dict:
    """§35 replay endpoint: reproduce a stored decision from its stored
    inputs + seed + config hash and diff the joint outputs. The debugging
    spine and the honesty check."""
    rec = _find_decision(decision_id)
    if not rec:
        return {"error": f"decision {decision_id} not found in the last 14 days"}
    model = rec.get("model") or {}
    inputs = model.get("inputs")
    if not inputs:
        return {"error": "decision has no stored inputs (cannot replay)"}
    cfg, cfg_hash = config()
    notes = []
    if model.get("config_hash") != cfg_hash:
        notes.append(f"config changed since decision ({model.get('config_hash')} → {cfg_hash}); replaying under the STORED semantics may differ")
    sim = simulate(inputs, cfg, int(model.get("seed", 0)))
    sim.pop("_grids", None)
    stored = rec.get("_sim") or {}
    diffs = {}
    for k in ("p_touch", "p_itm", "expected_extra", "wait_edge"):
        a, b = stored.get(k), sim.get(k)
        if a is not None and b is not None and abs(float(a) - float(b)) > 1e-9:
            diffs[k] = {"stored": a, "replayed": b}
    return {"decision_id": decision_id, "match": not diffs, "diffs": diffs,
            "notes": notes, "stored_state": rec.get("state"),
            "replayed": {k: sim.get(k) for k in
                         ("p_touch", "p_itm", "expected_extra", "wait_edge",
                          "p_better", "mc_se")}}


# ── Candidates + fills (§4 entry capture) ───────────────────────────────────

def _cand_path() -> Path:
    return _timing_dir() / "candidates.json"


def list_candidates() -> list:
    try:
        p = _cand_path()
        if p.exists():
            v = json.loads(p.read_text())
            return v if isinstance(v, list) else []
    except Exception:
        pass
    return []


def _save_candidates(rows: list) -> None:
    try:
        p = _cand_path()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rows[-60:], separators=(",", ":")))
        tmp.replace(p)
    except Exception:
        pass


def contract_key(symbol: str, expiry: str, kind: str, strike: float) -> str:
    return f"{symbol.upper()}|{str(expiry)[:10]}|{kind}|{float(strike):g}"


def add_candidate(symbol: str, expiry: str, kind: str, strike: float,
                  contracts: int | None = None, intent: str | None = None) -> dict:
    kind = "put" if str(kind).lower().startswith("p") else "call"
    key = contract_key(symbol, expiry, kind, strike)
    rows = [r for r in list_candidates() if r.get("key") != key]
    rows.append({"key": key, "symbol": symbol.upper(), "expiry": str(expiry)[:10],
                 "kind": kind, "strike": float(strike),
                 "contracts": int(contracts) if contracts else None,
                 "added_at": _now_et().isoformat(timespec="seconds")})
    _save_candidates(rows)
    if intent:
        set_intent(symbol, kind, intent)
    return {"ok": True, "key": key, "count": len(rows)}


def remove_candidate(key: str) -> dict:
    rows = [r for r in list_candidates() if r.get("key") != key]
    _save_candidates(rows)
    return {"ok": True, "count": len(rows)}


def log_fill(payload: dict) -> dict:
    """§4: journal the DECISION STATE, not just the trade. Called when a
    fill is logged from the card. Captures the full current engine state +
    tranche/mode context. Missing fields store null — never blocks."""
    try:
        sym = str(payload.get("symbol") or "").upper()
        expiry = str(payload.get("expiry") or "")[:10]
        kind = "put" if str(payload.get("kind", "")).lower().startswith("p") else "call"
        strike = float(payload.get("strike") or 0)
        state = None
        try:
            state = evaluate(sym, strike, kind, expiry,
                             contracts=payload.get("contracts"))
        except Exception:
            state = None
        rec = {"ts_et": _now_et().isoformat(timespec="seconds"),
               "symbol": sym, "expiry": expiry, "kind": kind, "strike": strike,
               "key": contract_key(sym, expiry, kind, strike),
               "credit": payload.get("credit"),
               "contracts": payload.get("contracts"),
               "mode": payload.get("mode") or "chase",     # §32 resting|chase
               "fraction_of_intended": payload.get("fraction"),
               "intent": intent_for(sym, kind),
               "alert_preceded": payload.get("alert_preceded"),
               "entry_state": state, "model_version": MODEL_VERSION,
               "config_hash": config()[1]}
        p = _timing_dir() / "fills.jsonl"
        with _LOG_LOCK:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return {"ok": True, "captured_state": bool(state),
                "decision_id": (state or {}).get("decision_id")}
    except Exception as exc:
        return {"error": str(exc)}


def list_fills(day: str | None = None) -> list:
    p = _timing_dir() / "fills.jsonl"
    if not p.exists():
        return []
    out = []
    try:
        for line in p.read_text().splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if day and not str(rec.get("ts_et", "")).startswith(day):
                continue
            out.append(rec)
    except Exception:
        pass
    return out[-200:]


# ── Live inputs assembly ────────────────────────────────────────────────────

def _chain_row(symbol: str, expiry: str, kind: str, strike: float) -> tuple[dict | None, dict | None]:
    """(contract row, underlying) from one normalized chain fetch."""
    if _CHAIN_FN is None:
        return None, None
    try:
        full = _CHAIN_FN(symbol, expiry) or {}
    except Exception:
        return None, None
    chains = (full.get("chains") or {}).get(str(expiry)[:10]) or {}
    rows = chains.get("calls" if kind == "call" else "puts") or []
    best = None
    for r0 in rows:
        k = r0.get("strike")
        if k is None:
            continue
        if best is None or abs(k - strike) < abs(best.get("strike") - strike):
            best = r0
    if best is not None and abs(best.get("strike", 0) - strike) / max(strike, 1e-9) > 0.02:
        best = None
    return best, (full.get("underlying") or {})


def _vwap_context(symbol: str) -> dict | None:
    """Today's VWAP/σ/stretch + HOD/LOD + trend flags from minute bars via
    the injected intraday source (reuses intraday.vwap_series)."""
    if _INTRADAY_FN is None:
        return None
    try:
        bars = _INTRADAY_FN(symbol) or []
    except Exception:
        return None
    if len(bars) < 5:
        return None
    try:
        import intraday as _intra
        vw = _intra.vwap_series(bars)
    except Exception:
        vw = None
    highs = [b.get("high") for b in bars if b.get("high") is not None]
    lows = [b.get("low") for b in bars if b.get("low") is not None]
    out = {"bars": bars, "hod": max(highs) if highs else None,
           "lod": min(lows) if lows else None}
    if vw:
        out.update({"vwap": vw["last"], "sigma": vw["sigma_last"],
                    "stretch": vw["stretch"]})
    return out


def _trend_dir(symbol: str, kind: str) -> int:
    """+1 when the tape trends TOWARD the premium side of this contract
    (up for calls, down for puts), else 0. Phase A day-type input: reuses
    intraday.market_regime on SPY/QQQ (labeled SIMULATION PRIOR)."""
    if _INTRADAY_FN is None:
        return 0
    try:
        import intraday as _intra
        idx = {}
        for isym in ("SPY", "QQQ"):
            try:
                idx[isym] = _INTRADAY_FN(isym) or []
            except Exception:
                idx[isym] = []
        verdict = (_intra.market_regime(idx) or {}).get("verdict")
    except Exception:
        return 0
    if verdict == "trend_up":
        return 1 if kind == "call" else 0
    if verdict == "trend_down":
        return 1 if kind == "put" else 0
    return 0


# ── §17/§18 evaluate(): the decision ────────────────────────────────────────

def evaluate(symbol: str, strike: float, kind: str, expiry: str,
             contracts: int | None = None, force: bool = False) -> dict:
    """Full engine state for one contract (schema: engine_state.schema.json).
    Server-cached briefly so UI polls don't re-simulate."""
    cfg, cfg_hash = config()
    kind = "put" if str(kind).lower().startswith("p") else "call"
    key = contract_key(symbol, expiry, kind, strike)
    ttl = float((cfg.get("engine") or {}).get("state_cache_seconds", 20))
    with _STATE_LOCK:
        hit = _EVAL_CACHE.get(key)
        if hit and not force and time.time() - hit[0] < ttl:
            return hit[1]
    state = _evaluate_uncached(symbol, strike, kind, expiry, contracts, cfg, cfg_hash)
    with _STATE_LOCK:
        _EVAL_CACHE[key] = (time.time(), state)
        if len(_EVAL_CACHE) > 40:
            for k in sorted(_EVAL_CACHE, key=lambda k: _EVAL_CACHE[k][0])[:10]:
                _EVAL_CACHE.pop(k, None)
    _log_decision(state)
    return state


def _blocked_state(key, symbol, strike, kind, expiry, code, detail, cfg_hash) -> dict:
    now = _now_et()
    return {"schema_version": SCHEMA_VERSION,
            "decision_id": f"{key}|{now.isoformat(timespec='seconds')}",
            "ts_et": now.isoformat(timespec="seconds"), "symbol": symbol.upper(),
            "contract": {"strike": float(strike), "expiry": str(expiry)[:10],
                         "kind": kind, "occ": None, "contracts": None},
            "intent": intent_for(symbol, kind),
            "state": "BLOCKED", "state_raw": "BLOCKED",
            "reason": detail, "score": None, "confidence": None,
            "layer": "MEASURED", "blocked": {"code": code, "detail": detail},
            "credit": {"bid": None, "ask": None, "mid": None, "last": None,
                       "spread_pct": None, "quote_age_s": None, "session": None},
            "probabilities": {"p_itm": None, "p_touch": None,
                              "p_itm_analytic": None, "p_touch_analytic": None,
                              "p_better": {}, "mc_se": None},
            "wait": {"expected_extra_credit": None, "wait_edge": None},
            "risk": {"admissible": False},
            "model": {"version": MODEL_VERSION, "config_hash": cfg_hash,
                      "seed": 0, "paths": 0, "snapshot_ids": None, "inputs": None}}


def _evaluate_uncached(symbol, strike, kind, expiry, contracts, cfg, cfg_hash) -> dict:
    now = _now_et()
    key = contract_key(symbol, expiry, kind, strike)
    decision_id = f"{key}|{now.isoformat(timespec='seconds')}"

    def blocked(code, detail):
        st = _blocked_state(key, symbol, strike, kind, expiry, code, detail, cfg_hash)
        st["decision_id"] = decision_id
        return st

    # Hard blocks (§15): reserved for bad data, unacceptable spread, market
    # closed, expiration reached, clock drift, event block windows.
    clock_msg = _clock_blocked(cfg)
    if clock_msg:
        return blocked("clock_drift", clock_msg)
    exp_d = date.fromisoformat(str(expiry)[:10])
    mins = minutes_to_close(now, exp_d, cfg)
    if mins <= 0:
        return blocked("expired", "expiration reached — settlement layer only")
    try:
        import intraday as _intra
        is_open = _intra.market_open(now if _ET else None)
    except Exception:
        is_open = now.weekday() < 5 and _dtime(9, 30) <= now.time() < _dtime(16, 0)
    if not is_open:
        # Hard block whenever the market is closed (§15) — and say THAT,
        # not "stale quote": outside the session every quote is frozen at
        # the last close by definition, and timing a frozen market is
        # meaningless. Watching resumes automatically at the next open.
        detail = ("market closed — quotes are frozen at the last session's "
                  "close, so there is nothing live to time. This contract "
                  "stays watched; evaluation and alerts resume at the open")
        if exp_d != now.date():
            detail += f" (decision day: {exp_d.isoformat()})"
        return blocked("market_closed", detail)
    ev = _event_context(now, cfg)
    if ev["block"]:
        return blocked("event_window", f"blocked by configured event window: {ev['block']}")

    row, under = _chain_row(symbol, str(expiry)[:10], kind, strike)
    if not row:
        return blocked("no_data", "no live chain row for this contract (source down or bad strike/expiry)")
    bid, ask = row.get("bid"), row.get("ask")
    last = row.get("last")
    spot = (under or {}).get("last")
    if not spot and _QUOTE_FN is not None:
        try:
            q = _QUOTE_FN(symbol) or {}
            spot = q.get("last")
        except Exception:
            spot = None
    if not spot or bid is None or ask is None:
        return blocked("invalid_quote", "missing spot or bid/ask")
    qage = row.get("quote_age_s")
    max_age = float((cfg.get("risk") or {}).get("max_quote_age_s", 120))
    if qage is not None and qage > max_age:
        return blocked("stale_quote",
                       f"option quote is {qage:.0f}s old (limit {max_age:.0f}s) — "
                       "stale or delayed quotes can never produce a sell signal")
    mid = (float(bid) + float(ask)) / 2.0 if (bid and ask) else None
    spread_pct = ((float(ask) - float(bid)) / mid * 100.0) if (mid and mid > 0) else None
    if spread_pct is not None and spread_pct > float((cfg.get("risk") or {}).get("max_spread_pct_of_mid", 30.0)):
        return blocked("bad_spread",
                       f"spread {spread_pct:.0f}% of mid exceeds the "
                       f"{(cfg.get('risk') or {}).get('max_spread_pct_of_mid')}% limit")

    # IV: back out of the mid under the canonical clock; guard low premium
    # (§1 [V3]); fall back to the chain's quoted IV (labeled), then to a
    # usable-neighbor interpolation.
    usability = iv_usability(bid, ask, cfg)
    iv = None
    iv_source = None
    if usability == "ok":
        iv = implied_vol_intraday(mid, float(spot), float(strike), mins, kind)
        iv_source = "backed_out"
    if iv is None:
        civ = normalize_iv(row.get("iv"))
        if civ:
            iv, iv_source = civ, "chain"
    if iv is None:
        iv = _neighbor_iv(symbol, str(expiry)[:10], kind, float(strike), float(spot))
        iv_source = "chain_interp" if iv else None
    if iv is None:
        return blocked("invalid_quote",
                       "no usable IV: premium too low to back out (§1 guard) and no usable chain neighbors")

    intent = intent_for(symbol, kind)
    lim = _limits_for(intent, cfg)
    att = cfg.get("attention") or {}
    vwctx = _vwap_context(symbol)
    trend = _trend_dir(symbol, kind)

    snap = {"spot": float(spot), "strike": float(strike), "kind": kind,
            "iv": float(iv), "minutes_remaining": mins, "bid": float(bid),
            "spread_frac": (spread_pct or 6.0) / 100.0, "trend_dir": trend,
            "widen": bool(ev["widen"]), "limits": lim,
            "look_interval_min": float(att.get("look_interval_min", 30)),
            "latency_min": float(att.get("alert_latency_min", 3)),
            "min_improve_frac": float((cfg.get("risk") or {}).get("min_improvement_frac", 0.08))}
    seed = _seed_from(decision_id)
    sim = simulate(snap, cfg, seed)

    # Analytic priors stay visible (§12); a large gap vs the simulation
    # means the day-type shading is doing heavy lifting — information.
    p_touch_an = touch_probability(float(spot), float(strike), float(iv), mins, kind)
    rp_now = reprice_intraday(float(spot), float(spot), float(strike), kind,
                              float(iv), mins, mins)
    p_itm_an = rp_now["p_itm_terminal"]

    admissible_now = (sim["p_itm"] <= lim["max_p_itm"]
                      and sim["p_touch"] <= lim["max_p_touch"]
                      and abs(rp_now["delta"]) <= lim["max_delta"])

    # §17 score: transparent summary of the stopping quantities.
    w = cfg.get("score_weights") or {}
    pbnl = sim.get("p_better_next_look")
    pbnl = sim["p_better"].get("close", 0.0) if pbnl is None else pbnl
    exhaustion = 1.0 - min(sim["expected_extra"] / max(float(bid) * 0.5, 0.05), 1.0)
    risk_pressure = min(max(sim["p_touch"] / max(lim["max_p_touch"], 1e-9),
                            sim["p_itm"] / max(lim["max_p_itm"], 1e-9)), 1.0)
    score = 100.0 * (float(w.get("p_no_better_next_look", 0.45)) * (1.0 - pbnl)
                     + float(w.get("extra_credit_exhausted", 0.35)) * exhaustion
                     + float(w.get("risk_pressure", 0.20)) * risk_pressure)
    score = max(0.0, min(100.0, score))
    # Premium Edge integration: the remaining-session variance edge may
    # shade the score by a few points (never drive it) — and the
    # inadmissibility cap below still binds after the nudge.
    sess_edge = session_variance_edge((vwctx or {}).get("bars"), float(iv), mins, cfg)
    if sess_edge and sess_edge.get("score_nudge"):
        score = max(0.0, min(100.0, score + sess_edge["score_nudge"]))
    # Acceptance test 13: a huge premium with spot on/through the strike is
    # hazardous, not opportunity — while selling NOW violates the intent's
    # limits, the score can never read as a sell zone.
    inadmissible_now_cap = None
    if not admissible_now:
        inadmissible_now_cap = float((cfg.get("bands") or {}).get("wait_max", 40))
        score = min(score, inadmissible_now_cap)
    raw_state = _score_to_state(score, cfg)
    if mins <= 5 or float(bid) < float((cfg.get("risk") or {}).get("min_bid", 0.05)):
        raw_state = "TOO LATE"

    heur = heuristic_check((vwctx or {}).get("bars"), kind, float(bid),
                           sim["expected_extra"], vwctx)
    disagree_hard = bool(heur and abs((heur["score"] or 0) - score) >= 35)

    confidence = 0.65
    if iv_source != "backed_out":
        confidence -= 0.08
    if qage is not None and qage > 45:
        confidence -= 0.05
    if disagree_hard:
        confidence -= 0.15
    if sim["mc_se"] > 0.02:
        confidence -= 0.03
    confidence = round(max(0.2, min(0.9, confidence)), 2)

    displayed, hyst = _apply_hysteresis(key, raw_state, score, sim["mc_se"], cfg)

    extra_d = (sim["expected_extra"] * 100.0 * contracts) if contracts else None
    if inadmissible_now_cap is not None:
        reason = (f"Selling now violates your {intent.replace('_', ' ')} limits "
                  f"(P(ITM) {sim['p_itm']:.0%}, touch {sim['p_touch']:.0%}, "
                  f"delta {abs(rp_now['delta']):.2f}) — this premium is hazardous, "
                  "not opportunity. Stand aside or change the position's intent.")
    else:
        reason = _one_line_reason(displayed, sim, lim, float(bid), extra_d, contracts)
        if sess_edge and abs(sess_edge["session_vrp_pct"]) >= 20:
            more = sess_edge["session_vrp_pct"] > 0
            reason += (f" Session vol edge {sess_edge['session_vrp_pct']:+.0f}% — the "
                       f"premium prices {'more' if more else 'less'} movement than "
                       f"today's tape projects.")

    dec = decomposition(float(spot), float(strike), kind, float(iv), mins,
                        float(bid), snap["spread_frac"], vwctx, cfg)
    limits3 = seed_limits(float(spot), float(strike), kind, float(iv), mins,
                          snap["spread_frac"], sim, vwctx, cfg)
    tranche = _tranche_suggestion(sim, confidence, cfg)
    fh = _final_hour(float(spot), float(strike), kind, float(bid), mid, mins,
                     rp_now, sim, cfg)
    session_bench = _tape_session(key)
    what_changed = _what_changed(key, displayed, float(bid), sim)
    dist_sig = (abs(math.log(float(strike) / float(spot)))
                / (float(iv) * math.sqrt(max(_t_years(mins), 1e-12))))

    state = {
        "schema_version": SCHEMA_VERSION, "decision_id": decision_id,
        "ts_et": now.isoformat(timespec="seconds"), "symbol": symbol.upper(),
        "contract": {"occ": row.get("occ"), "strike": float(strike),
                     "expiry": str(expiry)[:10], "kind": kind,
                     "contracts": contracts},
        "intent": intent, "state": displayed, "state_raw": raw_state,
        "reason": reason, "score": round(score), "confidence": confidence,
        "layer": "SIMULATION PRIOR", "blocked": None,
        "hysteresis": hyst,
        "credit": {"bid": float(bid), "ask": float(ask), "mid": round(mid, 4),
                   "last": last, "bid_size": row.get("bid_size"),
                   "ask_size": row.get("ask_size"),
                   "spread_pct": round(spread_pct, 1) if spread_pct is not None else None,
                   "quote_age_s": qage, "session": session_bench},
        "probabilities": {"p_itm": round(sim["p_itm"], 4),
                          "p_touch": round(sim["p_touch"], 4),
                          "p_itm_analytic": p_itm_an,
                          "p_touch_analytic": round(p_touch_an, 4) if p_touch_an is not None else None,
                          "p_better": {k: round(v, 3) for k, v in sim["p_better"].items()},
                          "p_better_next_look": round(pbnl, 3),
                          "mc_se": round(sim["mc_se"], 4)},
        "wait": {"expected_extra_credit": round(sim["expected_extra"], 3),
                 "hazardous_premium": sim["hazardous_premium"],
                 "p_better_raw": round(sim["p_better_raw"], 3),
                 "expected_extra_dollars": round(extra_d, 0) if extra_d is not None else None,
                 "expected_extra_next_look": round(sim["expected_extra_next_look"], 3),
                 "expected_time_to_better_min": (round(sim["expected_time_to_better_min"], 0)
                                                 if sim["expected_time_to_better_min"] else None),
                 "wait_edge": round(sim["wait_edge"], 3),
                 "timing_regret_now": round(sim["expected_extra"], 3),
                 "decomposition": dec["decomposition"],
                 "scenarios": dec["scenarios"]},
        "risk": {"admissible": bool(admissible_now),
                 "delta": rp_now["delta"], "gamma": rp_now["gamma"],
                 "theta_per_min": rp_now["theta_per_min"], "vega": rp_now["vega"],
                 "iv": round(float(iv), 4), "iv_source": iv_source,
                 "iv_usability": usability,
                 "headroom_dollars": sim["risk_headroom_dollars"],
                 "distance": {"dollars": round(abs(float(strike) - float(spot)), 2),
                              "pct": round(abs(float(strike) - float(spot)) / float(spot) * 100.0, 2),
                              "sigma": round(dist_sig, 2)},
                 "minutes_remaining": round(mins, 1)},
        "limits": limits3, "tranche": tranche,
        "attention": {"next_look_min": float(att.get("look_interval_min", 30)),
                      "p_better_next_look": round(pbnl, 3),
                      "alert": None},
        "final_hour": fh,
        "heuristic": (dict(heur, agrees=not disagree_hard,
                           disagree_hard=disagree_hard) if heur else None),
        "volatility_edge": sess_edge,
        "what_changed": what_changed,
        "data_quality": {"source": (under or {}).get("source") or "schwab",
                         "option_quote_age_s": qage,
                         "spread_quality": ("tight" if (spread_pct or 99) < 8
                                            else "ok" if (spread_pct or 99) < 20 else "wide"),
                         "real_vs_modeled": "real quotes, modeled paths",
                         "sample_size": None,
                         "calibration": "uncalibrated (Phase A — priors)"},
        "events": ev["events"],
        "horizon_note": ("simulation models the next ~400 minutes of a "
                         "multi-day expiry — built for 0DTE, where the "
                         "horizon is the whole problem" if mins > 400 else None),
        "projected_extreme": dec["projected_extreme"],
        "model": {"version": MODEL_VERSION, "config_hash": cfg_hash,
                  "seed": seed, "paths": sim["paths"],
                  "snapshot_ids": None, "inputs": snap},
        "_sim": {k: sim[k] for k in ("p_touch", "p_itm", "expected_extra",
                                     "wait_edge")},
    }
    return state


def _one_line_reason(state: str, sim: dict, lim: dict, bid: float,
                     extra_d, contracts) -> str:
    ee = sim["expected_extra"]
    pt = sim["p_touch"]
    if state == "TOO LATE":
        return "Effectively no admissible premium left — the window has closed."
    if state in ("SELL ZONE", "STRONG SELL ZONE"):
        s = f"Only {ee:.2f} expected additional admissible credit remains"
        if extra_d is not None:
            s += f" ({extra_d:.0f} for your {contracts} contracts)"
        s += f", while touch probability is {pt:.0%}"
        if pt > lim["max_p_touch"] * 0.7:
            s += f" and rising toward your {lim['max_p_touch']:.0%} limit"
        hz = sim.get("hazardous_premium") or 0.0
        if hz >= max(ee * 2.0, 0.10):
            s += (f". A further {hz:.2f} exists only OUTSIDE your risk limits "
                  "— hazardous premium, not opportunity")
        return s + "."
    if state == "GETTING CLOSE":
        return (f"{sim['p_better'].get('close', 0):.0%} chance a better admissible premium "
                f"still prints ({ee:.2f} expected), but the edge is thinning.")
    return (f"{sim['p_better'].get('close', 0):.0%} chance of a better admissible premium ahead — "
            f"expected extra {ee:.2f} vs credit {bid:.2f} now; touch risk {pt:.0%} is inside limits.")


def _neighbor_iv(symbol, expiry, kind, strike, spot) -> float | None:
    """§11: when this contract's IV is unusable, interpolate from usable
    neighbors on the SAME chain snapshot (nearest usable strikes, inverse-
    distance weighted). Labeled chain_interp."""
    if _CHAIN_FN is None:
        return None
    try:
        full = _CHAIN_FN(symbol, expiry) or {}
    except Exception:
        return None
    rows = ((full.get("chains") or {}).get(expiry) or {}).get(
        "calls" if kind == "call" else "puts") or []
    usable = []
    for r0 in rows:
        ivn = normalize_iv(r0.get("iv"))
        if ivn and iv_usability(r0.get("bid"), r0.get("ask")) == "ok":
            usable.append((abs(r0["strike"] - strike), ivn))
    usable.sort()
    if not usable:
        return None
    near = usable[:3]
    wsum = sum(1.0 / max(d, 0.01) for d, _ in near)
    return sum(v / max(d, 0.01) for d, v in near) / wsum


def _tranche_suggestion(sim: dict, confidence: float, cfg: dict) -> dict:
    """§13 dynamic tranching: fraction now depends on the evidence a better
    admissible premium is still ahead. Cold-start fixed plan while
    confidence is low, badged heuristic."""
    tc = cfg.get("tranche") or {}
    if confidence < float(tc.get("confidence_floor", 0.60)):
        return {"suggest_frac": (tc.get("cold_start_plan") or [0.4])[0],
                "plan": tc.get("cold_start_plan"), "basis": "cold_start (fixed 40/30/30 — heuristic)"}
    pb = sim["p_better"].get("close", 0.0)
    if pb >= 0.65:
        frac = 0.0
    elif pb >= 0.45:
        frac = 0.25
    elif pb >= 0.25:
        frac = 0.50
    else:
        frac = 1.0
    frac = max(frac, 0.0)
    mn = float(tc.get("min_frac", 0.10))
    if 0 < frac < mn:
        frac = mn
    return {"suggest_frac": frac, "plan": None,
            "basis": f"dynamic: P(better ahead) {pb:.0%}, expected extra {sim['expected_extra']:.2f}"}


def _final_hour(spot, strike, kind, bid, mid, mins, rp_now, sim, cfg) -> dict | None:
    """§33: inside the window the question becomes close vs carry. Also the
    after-hours exercise watch flag near the strike."""
    fc = cfg.get("final_hour") or {}
    win = float(fc.get("window_min", 45))
    if mins > win:
        return None
    cost_to_close = round((mid if mid is not None else bid) or 0.0, 2)
    remaining = cost_to_close
    sign = 1.0 if kind == "call" else -1.0
    dist_pct = sign * (strike - spot) / spot * 100.0
    exp_loss = sim["p_itm"] * max(spot * abs(dist_pct) / 100.0, 0.0)
    risk_per_penny = (exp_loss / remaining) if remaining > 0.01 else float("inf")
    near = abs(strike - spot) / spot * 100.0 <= float(fc.get("strike_near_pct", 1.0))
    recommend = None
    if remaining <= float(fc.get("pennies_max_cost", 0.05)) and near:
        recommend = "BUY TO CLOSE FOR PENNIES — risk per remaining penny exceeds threshold"
    elif risk_per_penny > float(fc.get("risk_per_penny_threshold", 1.5)) and remaining > 0:
        recommend = "BUY TO CLOSE — expected loss per dollar of remaining premium is above your threshold"
    elif remaining <= 0.01:
        recommend = "carry — nothing meaningful left to buy back"
    watch = abs(strike - spot) <= float(fc.get("exercise_watch_dollars", 1.0))
    return {"active": True, "cost_to_close": cost_to_close,
            "remaining_credit": remaining,
            "risk_per_penny": (round(risk_per_penny, 2)
                               if risk_per_penny != float("inf") else None),
            "p_touch_before_bell": round(sim["p_touch"], 3),
            "gamma": rp_now["gamma"], "recommend": recommend,
            "exercise_watch": {"active": watch,
                               "until_et": fc.get("settlement_watch_until_et", "17:30"),
                               "note": ("finished within $1 of the strike — exercise "
                                        "decisions can follow after-hours moves; confirm "
                                        "positions Saturday") if watch else None}}


def _tape_session(key: str) -> dict | None:
    if _TAPE is None:
        return None
    try:
        return _TAPE.session_benchmarks(key)
    except Exception:
        return None


def _what_changed(key: str, state: str, bid: float, sim: dict) -> list:
    """§19: WHAT CHANGED since the last time this card was evaluated."""
    out = []
    with _STATE_LOCK:
        prev = _LAST_SEEN.get(key)
        _LAST_SEEN[key] = {"state": state, "bid": bid,
                           "p_touch": sim["p_touch"], "ts": time.time()}
    if not prev:
        return out
    if prev["state"] != state:
        out.append(f"state {prev['state']} → {state}")
    if prev.get("bid") and abs(bid - prev["bid"]) >= 0.05:
        out.append(f"executable credit {prev['bid']:.2f} → {bid:.2f}")
    if abs(sim["p_touch"] - prev.get("p_touch", 0)) >= 0.05:
        out.append(f"touch risk {prev.get('p_touch', 0):.0%} → {sim['p_touch']:.0%}")
    return out


# ── §20 Management engine (open short legs) ─────────────────────────────────

def manage_position(pos: dict) -> dict:
    """States for an OPEN short leg: HOLD / BUYBACK ATTRACTIVE / RISK RISING /
    STRIKE THREATENED / BREACHED (+ the model-free 2x tripwire, always on).
    pos: {symbol, strike, kind, expiry, credit (entry), contracts, intent?}.
    Advisory only; hands off to final-hour mode inside the window."""
    cfg, cfg_hash = config()
    sym = str(pos.get("symbol") or "").upper()
    kind = "put" if str(pos.get("kind", "")).lower().startswith("p") else "call"
    strike = float(pos.get("strike") or 0)
    expiry = str(pos.get("expiry") or "")[:10]
    entry_credit = float(pos.get("credit") or 0)
    state = evaluate(sym, strike, kind, expiry, contracts=pos.get("contracts"))
    if state.get("blocked"):
        return {"symbol": sym, "strike": strike, "kind": kind, "expiry": expiry,
                "state": "BLOCKED", "detail": state["blocked"],
                "intent": state.get("intent")}
    mc = cfg.get("management") or {}
    bid = state["credit"]["bid"]; mid = state["credit"]["mid"]
    cost_now = mid if mid is not None else bid
    p_touch = state["probabilities"]["p_touch"]
    p_itm = state["probabilities"]["p_itm"]
    dist_pct = state["risk"]["distance"]["pct"]
    intent = intent_for(sym, kind, cfg)
    lim = _limits_for(intent, cfg)
    spot_beyond = ((kind == "call" and state["model"]["inputs"]["spot"] >= strike)
                   or (kind == "put" and state["model"]["inputs"]["spot"] <= strike))
    tripwire = (entry_credit > 0 and cost_now is not None
                and cost_now >= entry_credit * float(mc.get("tripwire_credit_mult", 2.0)))
    remaining = cost_now or 0.0
    captured = max(entry_credit - remaining, 0.0)
    risk_per_dollar = ((p_itm * state["model"]["inputs"]["spot"] * abs(dist_pct) / 100.0)
                       / remaining) if remaining > 0.02 else None
    if spot_beyond:
        mstate = "BREACHED"
    elif tripwire:
        mstate = "TRIPWIRE"
    elif dist_pct <= float(mc.get("strike_threatened_pct", 1.0)):
        mstate = "STRIKE THREATENED"
    elif p_touch >= float(mc.get("risk_rising_p_touch", 0.45)) * (2.0 if intent == "wheel_acceptable" else 1.0):
        mstate = "RISK RISING"
    elif entry_credit > 0 and captured / entry_credit >= float(mc.get("buyback_attractive_capture_frac", 0.80)) \
            and remaining <= 0.15:
        mstate = "BUYBACK ATTRACTIVE"
    else:
        mstate = "HOLD"
    return {"symbol": sym, "strike": strike, "kind": kind, "expiry": expiry,
            "contracts": pos.get("contracts"), "intent": intent,
            "state": mstate, "tripwire_armed": True, "tripwire_fired": tripwire,
            "entry_credit": entry_credit, "cost_to_close": cost_now,
            "captured": round(captured, 2),
            "captured_pct": round(captured / entry_credit * 100.0, 0) if entry_credit > 0 else None,
            "remaining_credit": round(remaining, 2),
            "risk_per_remaining_dollar": (round(risk_per_dollar, 2)
                                          if risk_per_dollar is not None else None),
            "p_itm": p_itm, "p_touch": p_touch,
            "distance_pct": dist_pct, "gamma": state["risk"]["gamma"],
            "breach_limits": lim, "final_hour": state.get("final_hour"),
            "decision_id": state["decision_id"]}


# ── §30 Portfolio + correlation layer ───────────────────────────────────────

def portfolio_rollup(legs: list) -> dict:
    """Live rollup across short 0DTE legs. legs: [{symbol, strike, kind,
    expiry, credit, contracts}]. Shared shock table: the sector proxy moves
    ±1/±2% into the close and EVERY leg repriced under the shared move
    (beta-adjusted) — joint, not independent (acceptance test 20).
    Amendment D: each leg valued under the view matching its intent
    (income = option cash P&L; wheel = all-in economics), then summed."""
    cfg, _ = config()
    pc = cfg.get("portfolio") or {}
    shocks = [float(s) for s in (pc.get("shocks_pct") or [-2, -1, 1, 2])]
    betas = pc.get("beta_overrides") or {}
    default_beta = float(pc.get("default_beta", 1.0))
    now = _now_et()
    rows, total_credit, tot_delta, tot_gamma = [], 0.0, 0.0, 0.0
    strikes_near = 0
    scen_tot = {s: 0.0 for s in shocks}
    scen_opt = {s: 0.0 for s in shocks}
    for leg in legs or []:
        sym = str(leg.get("symbol") or "").upper()
        kind = "put" if str(leg.get("kind", "")).lower().startswith("p") else "call"
        strike = float(leg.get("strike") or 0)
        expiry = str(leg.get("expiry") or now.date().isoformat())[:10]
        n_ct = int(leg.get("contracts") or 1)
        credit = float(leg.get("credit") or 0)
        intent = intent_for(sym, kind, cfg)
        row, under = _chain_row(sym, expiry, kind, strike)
        spot = (under or {}).get("last")
        if not row or not spot:
            rows.append({"symbol": sym, "strike": strike, "kind": kind,
                         "error": "no live data", "intent": intent})
            continue
        mins = minutes_to_close(now, date.fromisoformat(expiry), cfg)
        bid, ask = row.get("bid"), row.get("ask")
        mid = ((bid or 0) + (ask or 0)) / 2.0 if (bid is not None and ask is not None) else None
        iv = normalize_iv(row.get("iv")) or implied_vol_intraday(
            mid or 0, float(spot), strike, mins, kind) or 0.5
        rp = reprice_intraday(float(spot), float(spot), strike, kind, iv, mins, mins)
        beta = float(betas.get(sym, default_beta))
        total_credit += credit * n_ct * 100.0
        tot_delta += -rp["delta"] * n_ct * 100.0 * beta      # short leg
        tot_gamma += -rp["gamma"] * n_ct * 100.0 * beta
        if abs(strike - float(spot)) / float(spot) * 100.0 <= float(pc.get("strike_cluster_pct", 1.0)):
            strikes_near += 1
        leg_scen = {}
        for s in shocks:
            tgt = float(spot) * (1.0 + beta * s / 100.0)
            rp_s = reprice_intraday(float(spot), tgt, strike, kind, iv, mins,
                                    max(mins * 0.25, 1.0))
            opt_pnl = (credit - rp_s["premium"]) * n_ct * 100.0
            if intent == "wheel_acceptable":
                if kind == "call":
                    stock_pnl = (tgt - float(spot)) * n_ct * 100.0
                    capped = min(stock_pnl + opt_pnl,
                                 (strike - float(spot) + credit) * n_ct * 100.0)
                    all_in = capped
                else:
                    all_in = opt_pnl if tgt > strike else \
                        (credit - max(strike - tgt, 0.0)) * n_ct * 100.0
            else:
                all_in = opt_pnl
            leg_scen[s] = {"option": round(opt_pnl, 0), "all_in": round(all_in, 0)}
            scen_tot[s] += all_in
            scen_opt[s] += opt_pnl
        rows.append({"symbol": sym, "strike": strike, "kind": kind,
                     "expiry": expiry, "contracts": n_ct, "credit": credit,
                     "intent": intent, "spot": float(spot),
                     "delta": rp["delta"], "p_itm": rp["p_itm_terminal"],
                     "distance_pct": round(abs(strike - float(spot)) / float(spot) * 100.0, 2),
                     "scenarios": leg_scen, "beta": beta})
    budget = float(pc.get("weekly_max_loss_usd", 5000.0))
    worst = min(scen_tot.values()) if scen_tot else 0.0
    used_pct = round(max(0.0, -worst) / budget * 100.0, 0) if budget > 0 else None
    return {"as_of": now.isoformat(timespec="seconds"),
            "sector_proxy": pc.get("sector_proxy", "SMH"),
            "legs": rows, "total_credit_today": round(total_credit, 0),
            "net_delta_beta_adj": round(tot_delta, 0),
            "net_gamma_beta_adj": round(tot_gamma, 1),
            "strikes_within_1pct": strikes_near,
            "shock_table": {str(s): {"all_in": round(scen_tot[s], 0),
                                     "option_only": round(scen_opt[s], 0)}
                            for s in shocks},
            "risk_budget_usd": budget, "risk_budget_used_pct": used_pct,
            "note": ("shared shock: every leg repriced under the SAME sector move "
                     "(beta-adjusted) — six short calls on one tape are one big short call")}


# ── §31 Alert policy ────────────────────────────────────────────────────────

def maybe_alert(state: dict) -> dict | None:
    """Alert only when the value of acting now decays fast; silent when
    stable. Budgeted per day, ranked by dollar impact, and every alert
    names the action it wants."""
    if _PUSH_FN is None or state.get("blocked"):
        return None
    cfg, _ = config()
    att = cfg.get("attention") or {}
    day = state["ts_et"][:10]
    with _STATE_LOCK:
        used = _ALERTS_SENT.get(day, 0)
        if used >= int(att.get("alert_budget_per_day", 10)):
            return None
    st = state["state"]
    fh = state.get("final_hour") or {}
    trigger = None
    if st in ("SELL ZONE", "STRONG SELL ZONE") and (state.get("what_changed") or []):
        trigger = f"{st} — {state['reason']}"
        action = "Sell (or place the Balanced limit)"
    elif fh.get("recommend") and "BUY TO CLOSE" in (fh.get("recommend") or ""):
        trigger = fh["recommend"]
        action = "Buy to close"
    else:
        return None
    contracts = (state.get("contract") or {}).get("contracts") or 1
    impact = (state["wait"].get("expected_extra_dollars")
              or (state["credit"]["bid"] or 0) * 100 * contracts)
    if impact is not None and impact < float(att.get("alert_min_dollar_impact", 25.0)):
        return None
    key = state["contract"]
    title = f"0DTE {state['symbol']} {key['strike']:g}{key['kind'][0].upper()} — {st}"
    try:
        _PUSH_FN(title, f"{trigger}\nAction: {action}")
        with _STATE_LOCK:
            _ALERTS_SENT[day] = used + 1
        return {"sent": True, "title": title, "action": action}
    except Exception:
        return None


# ── Status ──────────────────────────────────────────────────────────────────

def status() -> dict:
    cfg, cfg_hash = config()
    tape_stat = None
    if _TAPE is not None:
        try:
            tape_stat = _TAPE.status()
        except Exception:
            tape_stat = None
    return {"model_version": MODEL_VERSION, "schema_version": SCHEMA_VERSION,
            "config_hash": cfg_hash, "config_version": cfg.get("version"),
            "clock": dict(_CLOCK), "clock_blocked": _clock_blocked(cfg),
            "candidates": len(list_candidates()),
            "session_valid_through": (cfg.get("session") or {}).get("valid_through"),
            "tape": tape_stat,
            "data_dir": str(_timing_dir())}


# ── Mini schema validator (§35, stdlib-only) ────────────────────────────────

def validate_state(state: dict) -> list:
    """Checks required keys/enums of engine_state.schema.json. Returns a
    list of violations (empty = valid). Intentionally minimal — the schema
    file is the contract, this enforces its load-bearing parts."""
    errs = []
    schema_p = Path(__file__).resolve().parent / "engine_state.schema.json"
    try:
        schema = json.loads(schema_p.read_text())
    except Exception as exc:
        return [f"schema file unreadable: {exc}"]
    for k in schema.get("required") or []:
        if k not in state:
            errs.append(f"missing required key: {k}")
    props = schema.get("properties") or {}
    for k, spec in props.items():
        if k not in state or state[k] is None:
            continue
        enum = spec.get("enum")
        if enum and state[k] not in enum:
            errs.append(f"{k}: {state[k]!r} not in {enum}")
    con = state.get("contract")
    if isinstance(con, dict):
        for k in ("strike", "expiry", "kind"):
            if k not in con:
                errs.append(f"contract missing {k}")
        if con.get("kind") not in ("call", "put", None):
            errs.append(f"contract.kind invalid: {con.get('kind')}")
    return errs


# ── §5 Post-trade metrics (risk-admissible, both variants) ──────────────────

def post_trade_report(day: str | None = None) -> dict:
    """After expiration: per logged fill, the forward benchmarks from the
    TAPE (§2) scored by execution mode (§32: resting → Executable High,
    chase → Durable Executable High), Risk-Admissible Forward Max under
    BOTH admissibility variants (model-probability and hindsight/realized),
    capture efficiencies, and the Hazardous Premium share. Small-sample
    honesty: aggregates report n and a CI only when n allows."""
    d = day or _now_et().date().isoformat()
    cfg, _ = config()
    fills = list_fills(day=d)
    rows = []
    for f in fills:
        key = f.get("key")
        credit = f.get("credit")
        if not key or credit is None:
            rows.append({"key": key, "error": "fill missing key/credit"})
            continue
        snaps = []
        if _TAPE is not None:
            try:
                snaps = _TAPE.read_contract_day(key, d) or []
            except Exception:
                snaps = []
        after = [s for s in snaps if s.get("ts", "") > f.get("ts_et", "")]
        if not after:
            rows.append({"key": key, "credit": credit, "mode": f.get("mode"),
                         "coverage": "no tape after the fill — metrics unavailable "
                                     "(a Friday of missed tape can never be recovered)"})
            continue
        lim = _limits_for(f.get("intent") or "income_only", cfg)
        durable_s = float((cfg.get("tape") or {}).get("durable_seconds", 60))
        bench = None
        if _TAPE is not None:
            b = None
            try:
                b = _TAPE._bench_new()
                for s in after:
                    adm = _snap_admissible(s, lim)
                    _TAPE._bench_update(b, s, durable_s, adm)
            except Exception:
                b = None
            bench = {k: v for k, v in (b or {}).items() if not k.startswith("_")}
        exec_max = (bench or {}).get("exec_high")
        durable_max = (bench or {}).get("durable_high")
        raw_max = (bench or {}).get("raw_high")
        adm_model_max = (bench or {}).get("admissible_high")
        # Hindsight variant (§5 guard): admissible if spot did NOT touch in
        # the remaining session after that snapshot (realized risk).
        touch_ts = (bench or {}).get("touch_ts")
        adm_hind = [s.get("bid") for s in after
                    if s.get("bid") is not None
                    and (touch_ts is None or s.get("ts", "") < touch_ts)]
        adm_hind_max = max(adm_hind) if adm_hind else None
        mode = (f.get("mode") or "chase").lower()
        mode_bench = exec_max if mode == "resting" else durable_max
        missed = max(0.0, (mode_bench or 0.0) - credit) if mode_bench is not None else None
        rows.append({
            "key": key, "ts_et": f.get("ts_et"), "mode": mode,
            "intent": f.get("intent"), "credit": credit,
            "contracts": f.get("contracts"),
            "n_snaps_after": len(after),
            "forward_raw_max": raw_max,
            "forward_exec_max": exec_max,
            "forward_durable_max": durable_max,
            "mode_benchmark": mode_bench,
            "forward_missed_executable": round(missed, 3) if missed is not None else None,
            "raw_capture_pct": (round(credit / raw_max * 100.0, 1)
                                if raw_max else None),
            "risk_admissible_max_model": adm_model_max,
            "risk_admissible_max_hindsight": adm_hind_max,
            "risk_adjusted_capture_model": (round(credit / adm_model_max * 100.0, 1)
                                            if adm_model_max else None),
            "risk_adjusted_capture_hindsight": (round(credit / adm_hind_max * 100.0, 1)
                                                if adm_hind_max else None),
            "hazardous_premium": (round(max(0.0, (mode_bench or 0.0)
                                            - (adm_model_max or 0.0)), 3)
                                  if mode_bench is not None else None),
            "touched_after_fill": bool((bench or {}).get("touched")),
        })
    # Divergence between the two admissibility variants is a calibration
    # alarm, not a nuisance (§5).
    div = [abs((r.get("risk_admissible_max_model") or 0)
               - (r.get("risk_admissible_max_hindsight") or 0))
           for r in rows if r.get("risk_admissible_max_model") is not None
           and r.get("risk_admissible_max_hindsight") is not None]
    caps = [r["risk_adjusted_capture_model"] for r in rows
            if r.get("risk_adjusted_capture_model") is not None]
    agg = None
    if caps:
        n = len(caps)
        mean = sum(caps) / n
        agg = {"n": n, "mean_risk_adjusted_capture": round(mean, 1)}
        if n >= 3:
            var = sum((c - mean) ** 2 for c in caps) / max(n - 1, 1)
            se = (var / n) ** 0.5
            agg["ci95"] = [round(mean - 1.96 * se, 1), round(mean + 1.96 * se, 1)]
        else:
            agg["note"] = "n < 3 — inside noise; no CI reported (§5 statistical honesty)"
    return {"day": d, "fills": rows, "aggregate": agg,
            "calibration_alarm": (round(max(div), 3) if div and max(div) > 0.5 else None),
            "model_version": MODEL_VERSION}


def _snap_admissible(s: dict, lim: dict) -> bool | None:
    """Model-probability admissibility of a tape snapshot: analytic P(ITM),
    forward P(touch) and |delta| inside the intent's limits."""
    try:
        spot, k = s.get("spot"), s.get("k")
        if not spot or not k:
            return None
        kind = s.get("kind") or "call"
        iv = normalize_iv(s.get("iv")) or 0.5
        ts = s.get("ts")
        exp = s.get("exp")
        mins = None
        if ts and exp:
            now_dt = datetime.fromisoformat(ts)
            mins = minutes_to_close(now_dt, date.fromisoformat(exp))
        if mins is None or mins <= 0:
            return None
        rp = reprice_intraday(float(spot), float(spot), float(k), kind, iv, mins, mins)
        pt = touch_probability(float(spot), float(k), iv, mins, kind)
        delta = s.get("delta")
        d_ok = abs(float(delta)) <= lim["max_delta"] if delta is not None else True
        return (rp["p_itm_terminal"] <= lim["max_p_itm"]
                and (pt is None or pt <= lim["max_p_touch"]) and d_ok)
    except Exception:
        return None


# ── §26 Diagnostic day replay (underlying MEASURED, premium MODELED) ────────

REFERENCE_TRADES_2026_08_14 = [
    # The spec's reference trades, "approximate until verified" — used as
    # INPUTS (the user's own fills), never as computed outputs.
    {"symbol": "MU", "strike": 945.0, "kind": "put", "credit": 5.25},
    {"symbol": "STX", "strike": 990.0, "kind": "call", "credit": 3.00},
    {"symbol": "SNDK", "strike": 1550.0, "kind": "put", "credit": None},
    {"symbol": "MP", "strike": 61.0, "kind": "call", "credit": None},
    {"symbol": "AMD", "strike": 515.0, "kind": "call", "credit": None},
    {"symbol": "AAOI", "strike": 160.0, "kind": "call", "credit": None},
]


def replay_day(day: str, trades: list | None = None) -> dict:
    """§26 diagnostic replay for a past session. Underlying-side facts are
    MEASURED from that day's real minute bars (Schwab). Premium-side values
    are MODELED: IV is backed out of YOUR fill (a real datum) at the fill
    time and held flat — labeled on every number. Where no fill credit
    exists, the premium side reports unavailable rather than invented.
    Nothing here fabricates a quote history that was never recorded."""
    if _MINUTE_DAY_FN is None:
        return {"error": "minute-bar source not wired"}
    cfg, _ = config()
    trades = trades or ([t for t in REFERENCE_TRADES_2026_08_14]
                        if day == "2026-08-14" else [])
    if not trades:
        return {"error": "no trades supplied for this day"}
    out_trades = []
    spots_1pm = {}
    for t in trades:
        sym = str(t.get("symbol") or "").upper()
        strike = float(t.get("strike") or 0)
        kind = "put" if str(t.get("kind", "")).lower().startswith("p") else "call"
        credit = t.get("credit")
        fill_hhmm = str(t.get("fill_et") or "10:30")
        try:
            bars = _MINUTE_DAY_FN(sym, day) or []
        except Exception:
            bars = []
        if not bars:
            out_trades.append({"symbol": sym, "strike": strike, "kind": kind,
                               "error": "no minute bars for this day (source retention)"})
            continue
        closes = [(b["ts"], b.get("close")) for b in bars if b.get("close") is not None]
        highs = [b.get("high") for b in bars if b.get("high") is not None]
        lows = [b.get("low") for b in bars if b.get("low") is not None]
        close_px = closes[-1][1] if closes else None
        sign = 1.0 if kind == "call" else -1.0
        # MEASURED: touch analysis from real bars.
        touch_ts = None
        for b in bars:
            px = b.get("high") if kind == "call" else b.get("low")
            if px is not None and sign * (px - strike) >= 0:
                touch_ts = b["ts"]
                break
        # Minutes spent beyond the strike (bar closes).
        beyond_mins = sum(1 for _, c in closes if sign * (c - strike) >= 0)
        # 1:00 PM ET snapshot for the portfolio case study.
        try:
            one_pm = datetime.fromisoformat(f"{day}T13:00:00-04:00").timestamp() * 1000
            at1 = next((c for ts, c in reversed(closes) if ts <= one_pm), None)
            spots_1pm[sym] = at1
        except Exception:
            pass
        row = {"symbol": sym, "strike": strike, "kind": kind,
               "measured": {
                   "hod": max(highs) if highs else None,
                   "lod": min(lows) if lows else None,
                   "close": close_px,
                   "touched_strike": touch_ts is not None,
                   "touch_ts_ms": touch_ts,
                   "minutes_beyond_strike": beyond_mins,
                   "closing_distance": (round(sign * (strike - close_px), 2)
                                        if close_px is not None else None),
                   "layer": "MEASURED (real minute bars)"}}
        if credit:
            # MODELED premium path anchored to the user's own fill.
            try:
                fh, fm = int(fill_hhmm[:2]), int(fill_hhmm[3:5])
                fill_dt = datetime.fromisoformat(f"{day}T{fh:02d}:{fm:02d}:00-04:00")
                fill_ms = fill_dt.timestamp() * 1000
                spot_fill = next((c for ts, c in closes if ts >= fill_ms), closes[0][1])
                mins_fill = minutes_to_close(fill_dt.astimezone(_ET) if _ET else fill_dt,
                                             date.fromisoformat(day), cfg)
                iv_fill = implied_vol_intraday(credit, spot_fill, strike, mins_fill, kind)
                if iv_fill:
                    peak = {"premium": None, "ts": None}
                    for ts, c in closes:
                        if ts < fill_ms:
                            continue
                        dt_c = datetime.fromtimestamp(ts / 1000, tz=_ET) if _ET else \
                            datetime.utcfromtimestamp(ts / 1000)
                        mins_c = minutes_to_close(dt_c, date.fromisoformat(day), cfg)
                        rp = reprice_intraday(spot_fill, c, strike, kind, iv_fill,
                                              mins_fill, mins_c)
                        if peak["premium"] is None or rp["premium"] > peak["premium"]:
                            peak = {"premium": rp["premium"], "ts": ts}
                    # Admissible (model) portion: premium while analytically
                    # inside income-only limits.
                    lim = _limits_for(intent_for(sym, kind, cfg), cfg)
                    adm_peak = None
                    for ts, c in closes:
                        if ts < fill_ms:
                            continue
                        dt_c = datetime.fromtimestamp(ts / 1000, tz=_ET) if _ET else \
                            datetime.utcfromtimestamp(ts / 1000)
                        mins_c = minutes_to_close(dt_c, date.fromisoformat(day), cfg)
                        rp = reprice_intraday(spot_fill, c, strike, kind, iv_fill,
                                              mins_fill, mins_c)
                        pt = touch_probability(c, strike, iv_fill, mins_c, kind)
                        if rp["p_itm_terminal"] <= lim["max_p_itm"] and \
                                (pt is None or pt <= lim["max_p_touch"]):
                            if adm_peak is None or rp["premium"] > adm_peak:
                                adm_peak = rp["premium"]
                    row["modeled"] = {
                        "iv_from_your_fill": round(iv_fill, 3),
                        "fill_assumed_et": fill_hhmm,
                        "modeled_forward_max": (round(peak["premium"], 2)
                                                if peak["premium"] is not None else None),
                        "modeled_admissible_max": (round(adm_peak, 2)
                                                   if adm_peak is not None else None),
                        "modeled_missed": (round(max(peak["premium"] - credit, 0.0), 2)
                                           if peak["premium"] is not None else None),
                        "modeled_hazardous_share": (round(max((peak["premium"] or 0)
                                                              - (adm_peak or 0), 0.0), 2)
                                                    if peak["premium"] is not None else None),
                        "layer": ("MODELED — flat IV backed out of YOUR fill; no "
                                  "intraday option tape existed for this day. The "
                                  "tape this engine records makes future replays "
                                  "MEASURED instead.")}
                else:
                    row["modeled"] = {"error": "could not back IV out of the fill"}
            except Exception as exc:
                row["modeled"] = {"error": f"model failed: {exc}"}
        else:
            row["modeled"] = {"note": "no fill credit supplied — premium side "
                                      "unavailable (nothing is invented)"}
        # Final-hour case study (§33): the state at 3:30 PM ET.
        try:
            t330 = datetime.fromisoformat(f"{day}T15:30:00-04:00").timestamp() * 1000
            spot_330 = next((c for ts, c in reversed(closes) if ts <= t330), None)
            if spot_330 is not None:
                dist_pct = abs(strike - spot_330) / spot_330 * 100.0
                row["final_hour_330pm"] = {
                    "spot": round(spot_330, 2),
                    "distance_pct": round(dist_pct, 2),
                    "within_1pct": dist_pct <= 1.0,
                    "note": ("§33 would evaluate close-vs-carry here"
                             + (" — exercise-watch territory" if dist_pct <= 0.5 else ""))}
        except Exception:
            pass
        out_trades.append(row)
    # Portfolio case study at 1:00 PM (§26/§30): shared-tape correlation.
    port = None
    moves = []
    for sym, s1 in spots_1pm.items():
        try:
            bars = _MINUTE_DAY_FN(sym, day) or []
            opens = [b.get("open") for b in bars if b.get("open") is not None]
            if s1 and opens:
                moves.append({"symbol": sym,
                              "pct_from_open_at_1pm": round((s1 - opens[0]) / opens[0] * 100.0, 2)})
        except Exception:
            continue
    if len(moves) >= 2:
        vals = [m["pct_from_open_at_1pm"] for m in moves]
        same_dir = all(v >= 0 for v in vals) or all(v <= 0 for v in vals)
        port = {"at": "13:00 ET", "moves": moves,
                "same_direction": same_dir,
                "note": ("one tape wearing several tickers — the §30 rollup "
                         "treats these as ONE shared shock, not independent"
                         if same_dir else
                         "mixed directions at 1pm — dispersion day"),
                "layer": "MEASURED (real minute bars)"}
    result = {"day": day, "trades": out_trades, "portfolio_1pm": port,
              "model_version": MODEL_VERSION,
              "honesty": ("Underlying facts are measured from real minute bars. "
                          "Premium values are modeled (flat IV from your fill) "
                          "because no option tape existed — the spec forbids "
                          "pretending otherwise.")}
    try:
        p = _timing_dir() / f"replay_{day}.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, separators=(",", ":")))
        tmp.replace(p)
    except Exception:
        pass
    return result


def cached_replay(day: str) -> dict | None:
    try:
        p = _timing_dir() / f"replay_{day}.json"
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return None


# ── Boot scheduler: headless evaluation + alerts (§31, Amendment A) ─────────

_SCHED = {"running": False}


def start_engine_scheduler() -> None:
    """Wake every 60s. While the market is open and candidates expiring
    today exist: refresh the clock check hourly, evaluate each candidate,
    and run the §31 alert policy — so alerts reach the phone with the app
    closed (the user acts when they look; the engine watches in between)."""
    if _SCHED["running"]:
        return
    _SCHED["running"] = True

    def loop():
        while True:
            try:
                now = _now_et()
                try:
                    import intraday as _intra
                    is_open = _intra.market_open(now if _ET else None)
                except Exception:
                    is_open = now.weekday() < 5 and _dtime(9, 30) <= now.time() < _dtime(16, 0)
                if is_open:
                    check_clock()
                    today = now.date().isoformat()
                    cands = [c for c in list_candidates()
                             if c.get("expiry") == today]
                    for c in cands:
                        try:
                            st = evaluate(c["symbol"], c["strike"], c["kind"],
                                          c["expiry"], contracts=c.get("contracts"))
                            if not st.get("blocked"):
                                maybe_alert(st)
                        except Exception:
                            continue
            except Exception:
                pass
            time.sleep(60)

    threading.Thread(target=loop, name="timing-engine", daemon=True).start()
