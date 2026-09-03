"""sp_forward.py — the forward-test audit trail for Best Sales Today (v4.80).

Every recommendation the sell board shows is recorded by sell_scan with the
probabilities it claimed. This module grades each one after its expiration
from DAILY BARS and turns the graded set into calibration tables.

What can be graded, and how it is labeled:

  MEASURED   — did the stock finish beyond the short strike (P0 / P(ITM)),
               did it touch the strike on any session (P(touch)), how far it
               went toward the strike in forecast-sigma units (max
               excursion). Daily high/low/close are real prints.
  MODELED    — the profit/loss per share, from intrinsic value at expiry
               against the credit taken. There is no option price history
               in the app (chain_store snapshots are opportunistic), so the
               path of the option's value between entry and expiry — and
               therefore whether a 50%/75%/90% profit target was hit early —
               cannot be measured. Those fields stay UNAVAILABLE rather
               than invented.
  ACCRUING   — a calibration slice with fewer than `min_n` graded
               recommendations. Shown with its count, never as a verdict.
  UNAVAILABLE— bars for the symbol could not be fetched.

Calibration (Brier, log loss, expected calibration error, reliability
buckets with Wilson intervals, Brier decomposition) is computed on the
whole graded set and on slices — side, DTE bucket, delta bucket, mode,
strategy — so a bias in one corner cannot hide inside a good average.

The learning loop is CONTROLLED: this module only *reports* whether a
Platt-style adjustment fitted on the first half of the graded history
improves the second half out of sample. It never rewrites the engine's
probabilities on its own; the report is what a human (and the docs) act on.
"""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

SP_FORWARD_VERSION = "sp-forward-1.0.0"

_DATA_DIR: Path | None = None
_BARS_FN = None
_MARKET_OPEN_FN = None
_LOCK = threading.RLock()
_STATE: dict = {"last_grade_ts": None, "last_grade": None, "calibration_ts": None,
                "calibration": None, "error": None, "graded_keys": None}
_SCHED_STARTED = False

MIN_N_DEFAULT = 30
DELTA_BUCKETS = (("≤0.10", 0.0, 0.10), ("0.10–0.20", 0.10, 0.20), ("0.20–0.30", 0.20, 0.30), (">0.30", 0.30, 9.0))
PROB_FIELDS = (
    # (prediction field, outcome field, plain-English name)
    ("p0_model", "expired_worthless", "P0 model (expires worthless)"),
    ("p0_conservative", "expired_worthless", "P0 conservative bound"),
    ("p0_measured", "expired_worthless", "P0 measured history"),
    ("p_touch", "touched", "P(touch) model"),
    ("p_touch_measured", "touched", "P(touch) measured history"),
    ("p_profit", "profitable", "P(profit after costs) [outcome MODELED]"),
)


def configure(data_dir=None, bars_fn=None, market_open_fn=None) -> None:
    global _DATA_DIR, _BARS_FN, _MARKET_OPEN_FN
    _DATA_DIR = Path(data_dir) if data_dir else None
    _BARS_FN, _MARKET_OPEN_FN = bars_fn, market_open_fn
    if _DATA_DIR is not None:
        try:
            (_DATA_DIR / "sell" / "grades").mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass


# ── statistics ───────────────────────────────────────────────────────────────
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


def _clip(p: float, eps: float = 1e-4) -> float:
    return min(1 - eps, max(eps, float(p)))


def brier(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return -sum((math.log(_clip(p)) if y else math.log(1 - _clip(p))) for p, y in pairs) / len(pairs)


def reliability(pairs: list[tuple[float, int]], edges=(0.0, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0001)) -> list[dict]:
    """Reliability buckets over the ranges a seller actually lives in
    (most claims sit above 70%), each with the Wilson interval of the
    observed rate and whether the claim falls inside it."""
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        rows = [(p, y) for p, y in pairs if lo <= p < hi]
        if not rows:
            continue
        n = len(rows)
        k = sum(y for _, y in rows)
        pbar = sum(p for p, _ in rows) / n
        lo_ci, hi_ci = wilson(k, n)
        out.append({"bucket": f"{lo:.0%}–{min(hi, 1.0):.0%}", "n": n, "claimed": round(pbar, 4),
                    "observed": round(k / n, 4), "ci_low": round(lo_ci, 4), "ci_high": round(hi_ci, 4),
                    "inside_ci": lo_ci <= pbar <= hi_ci})
    return out


def ece(buckets: list[dict]) -> float | None:
    n = sum(b["n"] for b in buckets)
    if not n:
        return None
    return sum(b["n"] * abs(b["claimed"] - b["observed"]) for b in buckets) / n


def brier_decomposition(pairs: list[tuple[float, int]], buckets: list[dict]) -> dict | None:
    """Murphy (1973): Brier = reliability − resolution + uncertainty."""
    n = len(pairs)
    if not n:
        return None
    ybar = sum(y for _, y in pairs) / n
    rel = sum(b["n"] * (b["claimed"] - b["observed"]) ** 2 for b in buckets) / n
    res = sum(b["n"] * (b["observed"] - ybar) ** 2 for b in buckets) / n
    unc = ybar * (1 - ybar)
    return {"reliability": round(rel, 5), "resolution": round(res, 5), "uncertainty": round(unc, 5),
            "base_rate": round(ybar, 4)}


def platt_fit(pairs: list[tuple[float, int]], iters: int = 200, lr: float = 0.5) -> tuple[float, float]:
    """Logistic recalibration on the logit: q = σ(a·logit(p) + b), fitted
    by plain gradient descent (tiny n, no dependency)."""
    a, b = 1.0, 0.0
    xs = [(math.log(_clip(p) / (1 - _clip(p))), y) for p, y in pairs]
    for _ in range(iters):
        ga = gb = 0.0
        for x, y in xs:
            q = 1 / (1 + math.exp(-(a * x + b)))
            ga += (q - y) * x
            gb += (q - y)
        a -= lr * ga / len(xs)
        b -= lr * gb / len(xs)
    return a, b


def platt_apply(p: float, a: float, b: float) -> float:
    x = math.log(_clip(p) / (1 - _clip(p)))
    return 1 / (1 + math.exp(-(a * x + b)))


def learning_check(pairs: list[tuple[float, int]], min_n: int = 100) -> dict:
    """Would a Platt adjustment fitted on the FIRST half (chronological)
    have beaten the raw probabilities on the SECOND half? Reported, never
    applied — the controlled learning loop."""
    n = len(pairs)
    if n < min_n:
        return {"status": "ACCRUING", "n": n, "min_n": min_n, "recommended": False,
                "note": f"{n} graded recommendations; {min_n} needed before any recalibration is judged."}
    half = n // 2
    train, test = pairs[:half], pairs[half:]
    a, b = platt_fit(train)
    raw = brier(test)
    adj = brier([(platt_apply(p, a, b), y) for p, y in test])
    better = adj is not None and raw is not None and adj < raw * 0.98
    return {"status": "MEASURED", "n": n, "a": round(a, 4), "b": round(b, 4),
            "brier_raw_oos": round(raw, 5), "brier_adjusted_oos": round(adj, 5),
            "recommended": bool(better),
            "note": ("A Platt adjustment fitted on the first half improves the second half out of sample; "
                     "apply only by hand after reading the slices." if better else
                     "No out-of-sample improvement from recalibration — the raw model stays.")}


# ── grading ─────────────────────────────────────────────────────────────────
def _grades_path(day: str) -> Path | None:
    return None if _DATA_DIR is None else _DATA_DIR / "sell" / "grades" / f"{day}.jsonl"


def load_grades(days: int = 400) -> list[dict]:
    if _DATA_DIR is None:
        return []
    folder = _DATA_DIR / "sell" / "grades"
    if not folder.exists():
        return []
    d0 = date.today() - timedelta(days=int(days))
    out = []
    for p in sorted(folder.glob("*.jsonl")):
        try:
            if date.fromisoformat(p.stem) < d0:
                continue
            for line in p.read_text().splitlines():
                if line.strip():
                    out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


def _graded_keys() -> set:
    with _LOCK:
        if _STATE["graded_keys"] is None:
            _STATE["graded_keys"] = {f"{g.get('day')}|{g.get('key')}" for g in load_grades(800)}
        return _STATE["graded_keys"]


def grade_record(rec: dict, bars: list[dict], today: date | None = None) -> dict | None:
    """Grade one recorded recommendation from daily bars. Returns None while
    the expiration is still ahead or the bars do not reach it."""
    today = today or date.today()
    try:
        entry_day = date.fromisoformat(str(rec["day"])[:10])
        exp = date.fromisoformat(str(rec["expiration"])[:10])
    except Exception:  # noqa: BLE001
        return None
    if exp >= today:
        return None
    window = [b for b in bars if entry_day < date.fromisoformat(str(b.get("date"))[:10]) <= exp]
    if not window:
        return None
    last = date.fromisoformat(str(window[-1]["date"])[:10])
    if (exp - last).days > 4:      # bars stop short of expiration — not gradable yet
        return None
    side = rec.get("side")
    ks = float(rec["short_strike"])
    kl = rec.get("long_strike")
    kc = rec.get("short_call")
    kcl = rec.get("long_call")
    spot0 = float(rec.get("spot") or window[0]["open"] or 0) or 1.0
    sigma_h = float(rec.get("sigma_h") or 0.0)
    dte = float(rec.get("dte") or 1.0)
    sig_move = spot0 * sigma_h * math.sqrt(max(dte, 0.25) / 365.0) if sigma_h > 0 else None
    close = float(window[-1]["close"])
    lows = [float(b["low"]) for b in window if b.get("low") is not None]
    highs = [float(b["high"]) for b in window if b.get("high") is not None]

    def put_leg(k):
        return {"itm": close < k, "touched": bool(lows) and min(lows) <= k,
                "excursion": ((spot0 - min(lows)) / sig_move) if (lows and sig_move) else None,
                "intrinsic": max(k - close, 0.0)}

    def call_leg(k):
        return {"itm": close > k, "touched": bool(highs) and max(highs) >= k,
                "excursion": ((max(highs) - spot0) / sig_move) if (highs and sig_move) else None,
                "intrinsic": max(close - k, 0.0)}

    if side == "put":
        leg = put_leg(ks)
        loss = leg["intrinsic"] - (max(float(kl) - close, 0.0) if kl else 0.0)
        legs = {"put": leg}
    elif side == "call":
        leg = call_leg(ks)
        loss = leg["intrinsic"] - (max(close - float(kl), 0.0) if kl else 0.0)
        legs = {"call": leg}
    else:  # condor
        pl, cl = put_leg(ks), call_leg(float(kc)) if kc is not None else None
        loss = pl["intrinsic"] - (max(float(kl) - close, 0.0) if kl else 0.0)
        if cl:
            loss += cl["intrinsic"] - (max(close - float(kcl), 0.0) if kcl else 0.0)
        legs = {"put": pl, "call": cl}
        leg = {"itm": pl["itm"] or bool(cl and cl["itm"]), "touched": pl["touched"] or bool(cl and cl["touched"]),
               "excursion": max([x["excursion"] for x in (pl, cl) if x and x["excursion"] is not None] or [None])}
    credit = float(rec.get("net_credit") if rec.get("net_credit") is not None else (rec.get("credit") or 0.0))
    pnl = credit - loss
    return {
        "day": rec.get("day"), "key": rec.get("key"), "symbol": rec.get("symbol"), "mode": rec.get("mode"),
        "strategy": rec.get("strategy"), "side": side, "expiration": rec.get("expiration"),
        "dte": dte, "dte_bucket": rec.get("dte_bucket"), "delta": rec.get("delta"), "rank": rec.get("rank"),
        "graded_at": datetime.now().replace(microsecond=0).isoformat(), "bars_through": last.isoformat(),
        "sessions": len(window), "close_at_expiry": round(close, 4),
        "expired_worthless": int(not leg["itm"]), "touched": int(bool(leg["touched"])),
        "max_excursion_sigma": (round(leg["excursion"], 3) if leg.get("excursion") is not None else None),
        "pnl_per_share": round(pnl, 4), "profitable": int(pnl > 0),
        "loss_fraction_of_max": (round(max(loss, 0.0) / float(rec["max_loss_per_share"]), 4)
                                 if rec.get("max_loss_per_share") else None),
        "basis": {"finish": "MEASURED", "touch": "MEASURED", "excursion": "MEASURED",
                  "pnl": "MODELED (intrinsic at expiry vs credit; no option price path)",
                  "early_profit_targets": "UNAVAILABLE (no option price history)"},
        # the claims, carried so calibration never needs the prediction file again
        "claims": {f: rec.get(f) for f, _o, _n in PROB_FIELDS},
        "engine": rec.get("engine"), "config_hash": rec.get("config_hash"),
    }


def grade_pending(max_symbols: int = 60) -> dict:
    """Grade every recorded recommendation whose expiration has passed and
    that has not been graded yet. One bars fetch per symbol (cached by the
    provider layer)."""
    try:
        import sell_scan as ss
        preds = ss.predictions(days=400)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"predictions unavailable: {exc}"}
    today = date.today()
    done = _graded_keys()
    pending = [r for r in preds if f"{r.get('day')}|{r.get('key')}" not in done
               and str(r.get("expiration", "9999"))[:10] < today.isoformat()]
    by_sym: dict = {}
    for r in pending:
        by_sym.setdefault(r.get("symbol"), []).append(r)
    graded, unavailable, still = 0, 0, 0
    for sym in list(by_sym)[:max_symbols]:
        bars = None
        if _BARS_FN:
            try:
                bars = _BARS_FN(sym)
            except Exception:  # noqa: BLE001
                bars = None
        if not bars:
            unavailable += len(by_sym[sym])
            continue
        lines: dict = {}
        for r in by_sym[sym]:
            g = grade_record(r, bars, today)
            if g is None:
                still += 1
                continue
            lines.setdefault(str(today), []).append(g)
            done.add(f"{r.get('day')}|{r.get('key')}")
            graded += 1
        for day, rows in lines.items():
            p = _grades_path(day)
            if p is None:
                continue
            try:
                with open(p, "a") as fh:
                    fh.write("\n".join(json.dumps(x, separators=(",", ":"), default=str) for x in rows) + "\n")
            except Exception as exc:  # noqa: BLE001
                print(f"sp_forward: grade append failed: {exc}")
    out = {"ok": True, "graded_now": graded, "unavailable": unavailable, "not_yet_gradable": still,
           "pending_before": len(pending), "total_graded": len(done), "as_of": datetime.now().isoformat()}
    with _LOCK:
        _STATE["last_grade_ts"] = time.time()
        _STATE["last_grade"] = out
    return out


# ── calibration tables ───────────────────────────────────────────────────────
def _delta_bucket(d) -> str:
    try:
        a = abs(float(d))
    except (TypeError, ValueError):
        return "unknown"
    for name, lo, hi in DELTA_BUCKETS:
        if lo <= a < hi:
            return name
    return ">0.30"


def _table(pairs: list[tuple[float, int]], min_n: int) -> dict:
    n = len(pairs)
    if n == 0:
        return {"status": "UNAVAILABLE", "n": 0}
    buckets = reliability(pairs)
    out = {"status": "MEASURED" if n >= min_n else "ACCRUING", "n": n, "min_n": min_n,
           "brier": round(brier(pairs), 5), "log_loss": round(log_loss(pairs), 5),
           "ece": round(ece(buckets), 5), "claimed_mean": round(sum(p for p, _ in pairs) / n, 4),
           "observed_rate": round(sum(y for _, y in pairs) / n, 4),
           "decomposition": brier_decomposition(pairs, buckets), "reliability": buckets}
    lo, hi = wilson(sum(y for _, y in pairs), n)
    out["observed_ci"] = [round(lo, 4), round(hi, 4)]
    out["claim_inside_ci"] = lo <= out["claimed_mean"] <= hi
    return out


def build_calibration(grades: list[dict], min_n: int = MIN_N_DEFAULT) -> dict:
    """Calibration for every probability the engine publishes, overall and
    by side / DTE bucket / delta bucket / mode / strategy."""
    fields = {}
    for pf, of, name in PROB_FIELDS:
        pairs_all = []
        slices: dict = {"side": {}, "dte_bucket": {}, "delta_bucket": {}, "mode": {}, "strategy": {}}
        for g in grades:
            p = (g.get("claims") or {}).get(pf)
            y = g.get(of)
            if p is None or y is None:
                continue
            pair = (float(p), int(y))
            pairs_all.append(pair)
            for sl, keyf in (("side", g.get("side")), ("dte_bucket", g.get("dte_bucket")),
                             ("delta_bucket", _delta_bucket(g.get("delta"))), ("mode", g.get("mode")),
                             ("strategy", g.get("strategy"))):
                slices[sl].setdefault(str(keyf), []).append(pair)
        fields[pf] = {"name": name, "outcome": of, "overall": _table(pairs_all, min_n),
                      "slices": {sl: {k: _table(v, min_n) for k, v in d.items()} for sl, d in slices.items()},
                      "learning": learning_check(pairs_all) if pf == "p0_model" else None}
    n = len(grades)
    pnl = [g["pnl_per_share"] for g in grades if g.get("pnl_per_share") is not None]
    exc = [g["max_excursion_sigma"] for g in grades if g.get("max_excursion_sigma") is not None]
    return {
        "version": SP_FORWARD_VERSION, "built_at": datetime.now().replace(microsecond=0).isoformat(),
        "n_graded": n, "status": ("UNAVAILABLE" if n == 0 else "MEASURED" if n >= min_n else "ACCRUING"),
        "min_n": min_n, "fields": fields,
        "outcomes": {
            "expired_worthless_rate": (round(sum(g.get("expired_worthless", 0) for g in grades) / n, 4) if n else None),
            "touched_rate": (round(sum(g.get("touched", 0) for g in grades) / n, 4) if n else None),
            "profitable_rate_modeled": (round(sum(g.get("profitable", 0) for g in grades) / n, 4) if n else None),
            "mean_pnl_per_share_modeled": (round(sum(pnl) / len(pnl), 4) if pnl else None),
            "worst_pnl_per_share_modeled": (round(min(pnl), 4) if pnl else None),
            "mean_max_excursion_sigma": (round(sum(exc) / len(exc), 3) if exc else None),
        },
        "labels": {"finish": "MEASURED", "touch": "MEASURED", "pnl": "MODELED",
                   "early_profit_targets": "UNAVAILABLE", "slices_under_min_n": "ACCRUING"},
    }


def _calib_path() -> Path | None:
    return None if _DATA_DIR is None else _DATA_DIR / "sell" / "calibration.json"


def calibration(refresh: bool = False, min_n: int = MIN_N_DEFAULT) -> dict:
    with _LOCK:
        cached = _STATE["calibration"]
    if cached and not refresh:
        return cached
    p = _calib_path()
    if not refresh and p is not None and p.exists():
        try:
            data = json.loads(p.read_text())
            with _LOCK:
                _STATE["calibration"] = data
            return data
        except Exception:  # noqa: BLE001
            pass
    data = build_calibration(load_grades(), min_n=min_n)
    with _LOCK:
        _STATE["calibration"] = data
        _STATE["calibration_ts"] = time.time()
    if p is not None:
        try:
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")))
            tmp.replace(p)
        except Exception:  # noqa: BLE001
            pass
    return data


def status() -> dict:
    with _LOCK:
        return {"version": SP_FORWARD_VERSION, "last_grade": _STATE["last_grade"],
                "last_grade_ts": _STATE["last_grade_ts"], "calibration_ts": _STATE["calibration_ts"],
                "n_graded": len(_STATE["graded_keys"] or ()) if _STATE["graded_keys"] is not None else None,
                "error": _STATE["error"]}


def run_once() -> dict:
    out = grade_pending()
    if out.get("ok") and out.get("graded_now", 0) > 0 or _STATE["calibration"] is None:
        calibration(refresh=True)
    return out


def start_scheduler(interval_s: int = 6 * 3600, initial_delay_s: int = 120) -> None:
    """Grade + rebuild calibration a couple of minutes after boot, then every
    `interval_s`. Nothing here touches the provider budget beyond one cached
    bars fetch per symbol with something to grade."""
    global _SCHED_STARTED
    if _SCHED_STARTED:
        return
    _SCHED_STARTED = True

    def loop():
        time.sleep(initial_delay_s)
        while True:
            try:
                run_once()
            except Exception as exc:  # noqa: BLE001
                with _LOCK:
                    _STATE["error"] = str(exc)
            time.sleep(interval_s)

    threading.Thread(target=loop, name="sp-forward", daemon=True).start()
