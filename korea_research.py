"""korea_research.py — the research layer behind KOREA LEAD V2.

korea_lead.py serves the panel: what Korea did this morning and what
usually followed. This module answers the questions that decide whether
that panel deserves to be trusted at all, and it is deliberately kept out
of the request path for the panel itself.

  Does Korea add anything the previous U.S. session did not already say?
  Which direction is the information actually flowing?
  Is the relationship the same one it was three years ago?
  Which Korean input best predicts which U.S. ticker?
  Does any of it survive out of sample?

Everything here reuses the V1 data path — korea_lead.korea_bars for Asian
series (disk cache included, and it accepts a raw Yahoo symbol) and
korea_lead.us_bars for U.S. targets. No second fetcher, no second cache,
no new dependency.

TAIWAN AND JAPAN ARE RESEARCH, NOT SIGNAL

Taipei closes at 13:30 and Tokyo at 15:00 local, both hours before New
York opens, so both are eligible on the same reasoning Korea is. They are
here to answer a question ABOUT Korea rather than to join it: if the
Nikkei — an index with far less semiconductor weight — predicts a U.S. chip
open as well as KOSPI does, then what is being measured is Asian risk
appetite and not anything about memory. Nikkei is a CONTROL and is never
promoted into a signal. Neither is Taiwan, in this version.

NOTHING HERE BECOMES A PRODUCTION SIGNAL BY ITSELF

Candidate models are compared out of sample and the comparison is
reported. Promotion is a decision, made once, by a person reading the
comparison — not a side effect of a model scoring well.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import korea_lead as kl
import korea_lead_engine as kle
import korea_research_engine as kre

MODULE_VERSION = "korea-research-1.0.0"

# ── the markets ─────────────────────────────────────────────────────────────
# Every symbol verified against the provider before being depended on:
# ^TWII answers as the TWSE Capitalization Weighted Stock Index in
# Asia/Taipei, 2330.TW as Taiwan Semiconductor Manufacturing Company
# Limited, and ^N225 as the Nikkei 225 in Asia/Tokyo. All three close
# before New York opens.

ASIAN_INPUTS = {
    "kospi":   {"symbol": "^KS11",     "label": "KOSPI",              "role": "signal"},
    "samsung": {"symbol": "005930.KS", "label": "Samsung Electronics", "role": "signal"},
    "hynix":   {"symbol": "000660.KS", "label": "SK Hynix",           "role": "signal"},
    "taiex":   {"symbol": "^TWII",     "label": "TAIEX (Taiwan)",     "role": "research"},
    "tsmc":    {"symbol": "2330.TW",   "label": "TSMC (Taipei)",      "role": "research"},
    "nikkei":  {"symbol": "^N225",     "label": "Nikkei 225",         "role": "control"},
}

# The U.S. side of the sensitivity matrix. Availability is checked per
# symbol at run time; a ticker with too little history is reported as such
# rather than dropped silently.
US_TARGETS = ["QQQ", "SMH", "SOXX", "MU", "NVDA", "AVGO", "AMD", "MRVL",
              "WDC", "STX", "SNDK", "LRCX", "AMAT", "KLAC", "SPY", "IGV"]

# The reference U.S. semiconductor session. It is what the Korea SURPRISE
# is measured against, and what the volatility regime is computed from.
REFERENCE_US = "SMH"

# Trailing realised volatility window for the regime split. Computed from
# closes through the PRIOR session, so it is knowable before the open it
# labels — same-day VIX would be a future value and is not used.
REGIME_WINDOW = 20

# Rolling relationship windows, in matched sessions.
ROLLING_WINDOWS = {"20d": 20, "60d": 60, "120d": 120, "1y": 252}

# Candidate models for the out-of-sample comparison. Feature lists, not
# weights: the coefficients are fitted inside each fold on that fold's
# training data alone.
CANDIDATE_MODELS = {
    "zero":                 [],
    "mean":                 [],
    "us_prev":              ["us_prev"],
    "kospi":                ["kospi"],
    "kospi_surprise":       ["kospi_surprise"],
    "kospi+us_prev":        ["kospi", "us_prev"],
    "samsung+hynix":        ["samsung", "hynix"],
    "kospi+samsung+hynix":  ["kospi", "samsung", "hynix"],
    "kospi+chips+tsmc":     ["kospi", "samsung", "hynix", "tsmc"],
}
BASELINE_MODEL = "kospi"

_LOCK = threading.Lock()
_FRAME_MEM: dict = {}       # symbol -> (identity, rows)
_REPORT_MEM: dict = {}      # key -> (built_ts, report)


def limits() -> dict:
    """The research section of thresholds.json, merged over the engine's
    own defaults.

    This exists because a settings block nothing reads is worse than no
    settings block: it changes the reported configuration hash without
    changing a single number, which is a documented contract that quietly
    is not one. Every value here is passed into the computation it names.
    """
    try:
        cfg = kl.config()[0].get("research") or {}
    except Exception:               # pragma: no cover
        cfg = {}
    out = {
        "min_regression_n": kre.MIN_REGRESSION_N,
        "min_train_n": kre.MIN_TRAIN_N,
        "walk_step": kre.WALK_STEP,
        "regime_window": REGIME_WINDOW,
        "fdr_alpha": 0.05,
        "estimate_disagreement_pct": 0.75,
    }
    for k, v in cfg.items():
        if k.startswith("_") or k not in out:
            continue
        try:
            out[k] = type(out[k])(v)
        except (TypeError, ValueError):
            continue
    return out


def configure(data_dir=None) -> None:
    """Research shares korea_lead's providers and caches; this only clears
    the derived memo so a reconfigure cannot serve a stale frame."""
    with _LOCK:
        _FRAME_MEM.clear()
        _REPORT_MEM.clear()
    if data_dir:
        try:
            (Path(data_dir) / "korea").mkdir(parents=True, exist_ok=True)
        except Exception as exc:      # pragma: no cover
            print(f"[korea-research] storage init failed: {exc}")


# ── building the observation frame ──────────────────────────────────────────

def _asian_returns(name: str) -> tuple:
    """(close-to-close by date, bar count, source) for one Asian input."""
    spec = ASIAN_INPUTS.get(name)
    if not spec:
        return {}, 0, None
    pack = kl.korea_bars(spec["symbol"])
    bars = pack.get("bars") or []
    if not bars:
        return {}, 0, pack.get("source")
    grain = kle.is_daily_series(bars)
    if not grain["daily"]:
        return {}, 0, f"REFUSED — {grain['reason']}"
    return (kle.close_to_close(bars, max_move_pct=kl._max_move()),
            len(bars), pack.get("source"))


def _trailing_vol(bars, window: int = REGIME_WINDOW) -> dict:
    """{session date: realised volatility of the `window` daily returns
    ENDING THE SESSION BEFORE}.

    The offset is the whole point. A regime label built from the same day's
    close would be a future value at 9:30, and every regime finding built
    on it would be an artefact. This is the volatility a reader could have
    computed before the open it describes.
    """
    rows = [b for b in (bars or []) if b.get("close")]
    out: dict = {}
    rets = []
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        if not prev.get("close") or prev["close"] <= 0:
            continue
        # label THIS session with the volatility of everything strictly
        # before it, then add this session's own return afterwards
        if len(rets) >= window:
            w = rets[-window:]
            m = sum(w) / len(w)
            var = sum((v - m) ** 2 for v in w) / (len(w) - 1)
            out[kle.bar_date(cur)] = math.sqrt(max(0.0, var))
        rets.append((cur["close"] / prev["close"] - 1.0) * 100.0)
    return out


def frame(symbol: str, force: bool = False) -> dict:
    """Every matched session for one U.S. target, with every research
    column attached: each Asian input, the previous U.S. session, the
    reference semiconductor session, the point-in-time volatility regime,
    and the point-in-time Korea surprise.

    Alignment is V1's and is not re-implemented: Asian session D against
    U.S. session D, nothing shifted, today excluded from its own history.
    """
    sym = (symbol or "").strip().upper()
    us = kl.us_bars(sym, force=force)
    ref = kl.us_bars(REFERENCE_US) if sym != REFERENCE_US else us
    kospi_pack = kl.korea_bars("kospi", force=force)
    ident = (sym, len(us.get("bars") or []),
             kle.bar_date((us.get("bars") or [{}])[-1]),
             len(kospi_pack.get("bars") or []),
             kle.bar_date((kospi_pack.get("bars") or [{}])[-1]))
    with _LOCK:
        hit = _FRAME_MEM.get(sym)
    if hit and hit[0] == ident and not force:
        return dict(hit[1])

    out = {"symbol": sym, "rows": [], "ok": False, "error": None,
           "sources": {}, "coverage": {}}
    if not us.get("bars"):
        out["error"] = us.get("error") or f"no daily history for {sym}"
        return out
    grain = kle.is_daily_series(us["bars"])
    if not grain["daily"]:
        out["error"] = f"The {sym} history is not usable: {grain['reason']}."
        return out

    measures = kle.us_measures(us["bars"], max_move_pct=kl._max_move())
    ref_measures = (measures if sym == REFERENCE_US
                    else kle.us_measures(ref.get("bars") or [],
                                         max_move_pct=kl._max_move()))
    through = kl._through_date(measures)
    asian: dict = {}
    for name in ASIAN_INPUTS:
        series, n_bars, source = _asian_returns(name)
        asian[name] = series
        out["sources"][name] = {"symbol": ASIAN_INPUTS[name]["symbol"],
                                "label": ASIAN_INPUTS[name]["label"],
                                "role": ASIAN_INPUTS[name]["role"],
                                "bars": n_bars, "source": source}
    out["sources"]["us"] = {"symbol": sym, "bars": len(us["bars"]),
                            "source": us.get("source"),
                            "spacing_days": grain["spacing_days"]}
    if not asian.get("kospi"):
        out["error"] = ("KOSPI history is unavailable, so no research frame "
                        "can be built.")
        return out

    lim = limits()
    vol = _trailing_vol(us["bars"], window=int(lim["regime_window"]))
    us_days = sorted(measures)
    prev_us = {us_days[i]: us_days[i - 1] for i in range(1, len(us_days))}
    ref_days = sorted(ref_measures)
    prev_ref = {ref_days[i]: ref_days[i - 1] for i in range(1, len(ref_days))}

    rows = []
    for d in sorted(set(asian["kospi"]) & set(measures)):
        if through is not None and d > through:
            continue
        m = measures[d]
        p = prev_us.get(d)
        pr = prev_ref.get(d)
        # Both naming conventions on purpose: the short keys are what the
        # research columns use, and the canonical engine names are what
        # korea_lead_engine.measure_stats reads. One row, one set of
        # numbers, so the two can never drift apart.
        row = {"date": d, "gap": m["opening_gap"], "o2c": m["open_to_close"],
               "full": m["full_day"],
               "opening_gap": m["opening_gap"],
               "open_to_close": m["open_to_close"],
               "full_day": m["full_day"],
               "korea": asian["kospi"].get(d),
               "us_prev": measures[p]["full_day"] if p in measures else None,
               "ref_prev": (ref_measures[pr]["full_day"]
                            if pr in ref_measures else None),
               "vol20": vol.get(d)}
        for name, series in asian.items():
            row[name] = series.get(d)
        rows.append(row)
    if not rows:
        out["error"] = ("Korea and this ticker have no completed trading day "
                        "in common in the available history.")
        return out

    # The Korea surprise, built without hindsight: each row's residual comes
    # from an echo model fitted only on the rows before it.
    for name, key in (("kospi", "kospi_surprise"), ("hynix", "hynix_surprise"),
                      ("samsung", "samsung_surprise")):
        kre.expanding_residual(rows, name, "ref_prev",
                               min_train=int(lim["min_train_n"]), out_key=key)

    out.update({
        "rows": rows, "ok": True, "through": through,
        "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
        "coverage": {
            name: sum(1 for r in rows if r.get(name) is not None)
            for name in list(ASIAN_INPUTS) + ["us_prev", "vol20",
                                              "kospi_surprise"]},
    })
    with _LOCK:
        _FRAME_MEM[sym] = (ident, out)
        if len(_FRAME_MEM) > 12:
            _FRAME_MEM.pop(next(iter(_FRAME_MEM)), None)
    return dict(out)


def _window_rows(rows, window: str) -> list:
    """Reuse V1's lookback definitions so a research number and a panel
    number over the same window mean the same thing."""
    return kl._window_slice(rows, window)


# ── the pair sensitivity matrix ─────────────────────────────────────────────

def pair_matrix(window: str = "max", targets=None, inputs=None,
                force: bool = False) -> dict:
    """Every Asian input against every U.S. target, corrected for the fact
    that we just ran dozens of tests.

    Do not read the raw p-values down the column and pick the small ones —
    with this many cells, some are small by luck. The q-value is the
    Benjamini-Hochberg false-discovery rate across the whole matrix, and it
    is the number that decides whether a cell means anything.
    """
    targets = list(targets or US_TARGETS)
    inputs = list(inputs or ASIAN_INPUTS)
    lim = limits()
    out = {"window": window, "cells": [], "targets": targets,
           "inputs": [{"key": k, **ASIAN_INPUTS[k]} for k in inputs],
           "skipped": [], "engine": kre.ENGINE_VERSION,
           "note": ("Raw p-values are not the deciding number here. Across "
                    "this many cells some will look significant by luck, so "
                    "the q-value carries the Benjamini-Hochberg false "
                    "discovery rate for the whole matrix.")}
    for sym in targets:
        f = frame(sym, force=force)
        if not f["ok"]:
            out["skipped"].append({"symbol": sym, "reason": f["error"]})
            continue
        rows = _window_rows(f["rows"], window)
        for name in inputs:
            pairs = [(r.get(name), r.get("gap")) for r in rows]
            pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
            if len(pairs) < int(lim["min_regression_n"]):
                out["skipped"].append(
                    {"symbol": sym, "input": name,
                     "reason": f"only {len(pairs)} matched sessions"})
                continue
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            r = kre.pearson(xs, ys)
            s = kle.spearman(xs, ys)
            directional = [p for p in pairs if p[0] != 0 and p[1] != 0]
            same = sum(1 for a, b in directional if (a > 0) == (b > 0))
            out["cells"].append({
                "input": name, "input_label": ASIAN_INPUTS[name]["label"],
                "role": ASIAN_INPUTS[name]["role"],
                "target": sym, "n": len(pairs),
                "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
                "pearson": None if r is None else round(r, 3),
                "spearman": None if s is None else round(s, 3),
                "same_direction_pct": (round(same / len(directional) * 100.0, 1)
                                       if directional else None),
                "p": kre.correlation_p(r, len(pairs)),
            })
    qs = kre.benjamini_hochberg([c["p"] for c in out["cells"]])
    for c, q in zip(out["cells"], qs):
        c["q"] = None if q is None else round(q, 5)
        c["p"] = None if c["p"] is None else round(c["p"], 6)
        c["significant"] = bool(q is not None and q < float(lim["fdr_alpha"]))
    out["cells"].sort(key=lambda c: -(abs(c["pearson"] or 0.0)))
    out["n_cells"] = len(out["cells"])
    out["n_significant"] = sum(1 for c in out["cells"] if c["significant"])
    out["fdr_alpha"] = float(lim["fdr_alpha"])
    return out


def best_input_for(symbol: str, window: str = "max") -> dict:
    """Which Asian input best predicts THIS ticker's open, measured
    directly rather than inferred through a sector proxy.

    A direct relationship is preferred wherever the ticker has enough of
    its own history. When it does not, the caller is told to fall back and
    the fallback is labelled — never presented as if it had been measured
    on the ticker itself.
    """
    m = pair_matrix(window, targets=[symbol.upper()])
    cells = [c for c in m["cells"] if c["role"] == "signal"]
    out = {"symbol": symbol.upper(), "window": window, "basis": None,
           "best": None, "ranked": cells, "reason": None}
    if not cells:
        out["basis"] = "SECTOR MODEL FALLBACK"
        out["reason"] = (f"{symbol.upper()} does not have enough of its own "
                         f"matched history to measure a direct relationship, "
                         f"so anything shown for it is the sector model and "
                         f"is labelled as such.")
        return out
    out["basis"] = "DIRECT"
    out["best"] = cells[0]
    return out


# ── the report ──────────────────────────────────────────────────────────────

def _leadlag(symbol: str, input_name: str, window: str, force=False) -> dict:
    """Which way the information is actually travelling.

    D is deliberately reported next to A even though the two are close to
    the same measurement — the U.S. session before Korean session D IS, in
    most weeks, the U.S. session that Korean session D-1 preceded. They are
    printed together so that near-equality is visible rather than sold as
    two independent findings.
    """
    f = frame(symbol, force=force)
    if not f["ok"]:
        return {"ok": False, "reason": f["error"]}
    rows = _window_rows(f["rows"], window)
    nxt = {rows[i]["date"]: rows[i + 1] for i in range(len(rows) - 1)}

    def corr(xs, ys):
        r = kre.pearson(xs, ys)
        return None if r is None else round(r, 3)

    a = corr([r.get("ref_prev") for r in rows], [r.get(input_name) for r in rows])
    b = corr([r.get(input_name) for r in rows], [r.get("gap") for r in rows])
    c = corr([r.get(input_name) for r in rows], [r.get("o2c") for r in rows])
    d = corr([r.get("full") for r in rows],
             [nxt[r["date"]].get(input_name) if r["date"] in nxt else None
              for r in rows])
    label = ASIAN_INPUTS[input_name]["label"]
    return {
        "ok": True, "input": input_name, "input_label": label,
        "symbol": symbol.upper(), "n": len(rows), "window": window,
        "legs": [
            {"key": "A", "label": f"prior {REFERENCE_US} full day → {label}",
             "r": a, "meaning": "how much Korea echoes the U.S. session before it"},
            {"key": "B", "label": f"{label} → {symbol.upper()} opening gap",
             "r": b, "meaning": "how much Korea leads the U.S. open"},
            {"key": "C", "label": f"{label} → {symbol.upper()} open-to-close",
             "r": c, "meaning": "whether anything survives past 9:30"},
            {"key": "D", "label": f"{symbol.upper()} full day → next {label}",
             "r": d, "meaning": "the U.S. feeding back into the next Korean session"},
        ],
        "note": ("A and D are close to the same measurement indexed two ways: "
                 "the U.S. session before Korean session D is usually the one "
                 "Korean session D-1 followed. They are shown together so that "
                 "is visible, not as two separate findings."),
    }


def _asia_control(rows, symbol: str) -> dict:
    """Does KOSPI still matter once Japan is in the room?

    The Nikkei is the control for the one alternative explanation that
    would make this whole feature a mirage: that what looks like Korea
    leading U.S. chips is really just overnight Asian risk appetite, which
    Korea happens to be a convenient thermometer for. If that were true,
    an index with far less semiconductor weight would do the same job, and
    KOSPI would collapse the moment both are in the same regression.

    All three are fitted on ONE identical sample — sessions where every
    input is present — because a horse race run on different rows is not a
    horse race.
    """
    lim = limits()
    usable = [r for r in rows
              if all(r.get(k) is not None for k in ("kospi", "nikkei", "tsmc", "gap"))]
    out = {"ok": False, "symbol": symbol, "n": len(usable), "models": {},
           "verdict": None, "detail": None, "reason": None}
    if len(usable) < int(lim["min_regression_n"]):
        out["reason"] = (f"needs at least {int(lim['min_regression_n'])} sessions "
                         f"carrying KOSPI, the Nikkei and TSMC together; "
                         f"have {len(usable)}")
        return out
    y = [r["gap"] for r in usable]
    fits = {
        "kospi_only": kre.ols(y, [[r["kospi"]] for r in usable], names=["kospi"]),
        "nikkei_only": kre.ols(y, [[r["nikkei"]] for r in usable], names=["nikkei"]),
        "kospi_plus_nikkei": kre.ols(
            y, [[r["kospi"], r["nikkei"]] for r in usable],
            names=["kospi", "nikkei"]),
        "kospi_nikkei_tsmc": kre.ols(
            y, [[r["kospi"], r["nikkei"], r["tsmc"]] for r in usable],
            names=["kospi", "nikkei", "tsmc"]),
    }
    if not all(fits.values()):
        out["reason"] = "one of the control regressions could not be fitted"
        return out
    for name, fit in fits.items():
        out["models"][name] = {
            "r2": round(fit["r2"], 4), "n": fit["n"],
            "params": [{"name": p["name"], "beta": round(p["beta"], 4),
                        "t": None if p["t"] is None else round(p["t"], 2),
                        "p": p["p"]}
                       for p in fit["params"] if p["name"] != "intercept"]}
    both = fits["kospi_plus_nikkei"]
    kp = next(p for p in both["params"] if p["name"] == "kospi")
    npar = next(p for p in both["params"] if p["name"] == "nikkei")
    kt, nt = kp["t"], npar["t"]
    out["ok"] = True
    if kt is None or nt is None:
        out["verdict"] = "NOT MEASURABLE"
        out["detail"] = ("One of the control coefficients had no usable "
                         "standard error, so the comparison cannot be made.")
        return out
    out["kospi_t_with_control"] = round(kt, 2)
    out["nikkei_t_with_control"] = round(nt, 2)
    # Significance and relative strength are judged on the MAGNITUDE of the
    # t-statistic; the SIGN is reported separately rather than folded into
    # the test. A KOSPI coefficient that came out strongly NEGATIVE after
    # controlling for Japan would still be independent information — an
    # important one — and a signed comparison would have filed it under
    # "explained by broad Asia", which is the opposite of what it means.
    out["kospi_sign"] = "positive" if kp["beta"] >= 0 else "negative"
    out["nikkei_sign"] = "positive" if npar["beta"] >= 0 else "negative"
    ka, na = abs(kt), abs(nt)
    sign_note = ("" if kp["beta"] >= 0 else
                 " Note the KOSPI coefficient is NEGATIVE here — independent "
                 "information, but running the opposite way to the usual "
                 "relationship, which is worth understanding before using.")
    if ka >= 2.0 and ka > na:
        out["verdict"] = "KOREA-SPECIFIC"
        out["detail"] = (
            f"With the Nikkei in the same regression on the same sessions, "
            f"KOSPI still carries t={kt:+.2f} against Japan's {nt:+.2f}. What "
            f"Korea is saying about {symbol} is not merely overnight Asian "
            f"risk appetite — though a large part of the two overlaps." + sign_note)
    elif ka >= 2.0:
        out["verdict"] = "SHARED WITH BROAD ASIA"
        out["detail"] = (
            f"KOSPI survives at t={kt:+.2f} but the Nikkei is stronger at "
            f"{nt:+.2f} on the same sessions. For {symbol} this reads mostly "
            f"as broad Asian risk appetite rather than anything specific to "
            f"Korean memory." + sign_note)
    else:
        out["verdict"] = "EXPLAINED BY BROAD ASIA"
        out["detail"] = (
            f"Once the Nikkei is in the regression KOSPI falls to t={kt:+.2f}. "
            f"For {symbol} the Korean signal does not survive the control.")
    return out


def report(symbol: str, window: str = "max", force: bool = False,
           heavy: bool = True) -> dict:
    """The full research picture for one U.S. target.

    Expensive by design — the walk-forward alone re-fits every candidate
    model once per fold — so it lives behind its own endpoint and its own
    cache, and is never on the path that renders the panel.
    """
    sym = (symbol or "").strip().upper()
    win = window if window in kl.WINDOWS else "max"
    key = "|".join((sym, win, str(bool(heavy)), MODULE_VERSION,
                    kre.ENGINE_VERSION, kl.config()[1]))
    with _LOCK:
        hit = _REPORT_MEM.get(key)
    f = frame(sym, force=force)
    if hit and not force and hit[0] == (f.get("last_date"), f.get("ok")):
        out = dict(hit[1])
        out["cached"] = True
        return out
    out = {"ok": False, "symbol": sym, "window": win, "cached": False,
           "module": MODULE_VERSION, "engine": kre.ENGINE_VERSION,
           "config_hash": kl.config()[1], "error": None}
    if not f["ok"]:
        out["error"] = f["error"]
        return out
    rows = _window_rows(f["rows"], win)
    lim = limits()
    out.update({
        "ok": True, "n": len(rows), "limits": lim,
        "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
        "through": f.get("through"), "sources": f["sources"],
        "coverage": f["coverage"],
        "adjustment": ("split- and spinoff-adjusted daily bars from the "
                       "provider; open, close and prior close all read from "
                       "the same adjusted series, never mixed with an "
                       "adjusted close"),
    })

    # 1. the three measurements, long-run
    out["measures"] = {m: kle.measure_stats(rows, m, signal="kospi")
                       for m in kle.MEASURES}

    # 2. incremental information — does Korea add anything to the prior session
    inc = {}
    for name in ("kospi", "samsung", "hynix", "tsmc", "nikkei"):
        usable = [r for r in rows
                  if r.get(name) is not None and r.get("us_prev") is not None]
        inc[name] = kre.incremental(
            [r["gap"] for r in usable],
            [[r["us_prev"]] for r in usable],
            [[r[name], r["us_prev"]] for r in usable],
            names=[name, "us_prev"], min_n=int(lim["min_regression_n"]))
        inc[name]["label"] = ASIAN_INPUTS[name]["label"]
        inc[name]["role"] = ASIAN_INPUTS[name]["role"]
    out["incremental"] = inc

    # 2b. Korea against the Asian control, on ONE identical sample.
    #     The question the Nikkei exists to answer: is this semiconductor
    #     information, or is it Asian risk appetite wearing Korea's name?
    out["asia_control"] = _asia_control(rows, sym)

    # 3. lead / lag
    out["lead_lag"] = {n: _leadlag(sym, n, win) for n in ("kospi", "hynix")}

    # 4. placebo — the alignment guard
    out["placebo"] = kre.placebo_table(rows, "kospi", "gap",
                                       min_n=int(lim["min_regression_n"]))

    # 5. rolling relationship strength — the whole series, not just today's
    #    number, so a relationship that is fading can be seen fading
    out["rolling"] = {
        lab: kre.rolling_correlation(rows, "kospi", "gap", w)
        for lab, w in ROLLING_WINDOWS.items()}
    out["rolling_by_input"] = {
        name: {lab: kre.rolling_correlation(rows, name, "gap", w)
               for lab, w in (("60d", 60), ("1y", 252))}
        for name in ("samsung", "hynix")
        if f["coverage"].get(name, 0) >= 252}

    # 6. year by year
    out["by_year"] = kre.by_year(rows, "kospi", "gap")

    # 7. volatility regime, point-in-time
    out["regime"] = kre.split_by_regime(rows, "vol20", "kospi", "gap",
                                        min_n=int(lim["min_regression_n"]))
    out["regime"]["basis"] = (
        f"{REGIME_WINDOW}-session realised volatility of {sym}, computed "
        f"through the PRIOR close. Same-day VIX is deliberately not used: at "
        f"9:30 it is a future value, and every regime finding built on it "
        f"would be an artefact of that.")

    # 8. surprise versus raw, in sample (the honest test is the walk-forward)
    sur = {}
    for raw, s_key in (("kospi", "kospi_surprise"), ("hynix", "hynix_surprise"),
                       ("samsung", "samsung_surprise")):
        usable = [r for r in rows if r.get(s_key) is not None]
        if len(usable) < int(lim["min_regression_n"]):
            sur[raw] = {"ok": False,
                        "reason": (f"only {len(usable)} sessions have a "
                                   f"point-in-time surprise value")}
            continue
        r_raw = kre.pearson([r[raw] for r in usable], [r["gap"] for r in usable])
        r_sur = kre.pearson([r[s_key] for r in usable], [r["gap"] for r in usable])
        sur[raw] = {"ok": True, "n": len(usable),
                    "label": ASIAN_INPUTS[raw]["label"],
                    "raw_pearson": None if r_raw is None else round(r_raw, 3),
                    "surprise_pearson": None if r_sur is None else round(r_sur, 3),
                    "note": ("The surprise is the residual from an echo model "
                             "fitted only on sessions before each row, so it "
                             "contains no hindsight. Whether it is actually "
                             "better is decided by the walk-forward below, "
                             "not by these two numbers.")}
    out["surprise"] = sur

    # 9. does the gap residual converge after the open?
    conv_rows = [dict(r) for r in rows]
    kre.expanding_residual(conv_rows, "gap", "kospi",
                           min_train=int(lim["min_train_n"]),
                           out_key="gap_residual")
    usable = [r for r in conv_rows if r.get("gap_residual") is not None]
    out["convergence"] = kre.convergence_test(usable, "gap_residual", "o2c",
                                              min_n=int(lim["min_regression_n"]))
    out["convergence"]["basis"] = (
        "Measured from the OFFICIAL OPEN, not from the premarket tape: this "
        "app holds no historical premarket prices for ordinary sessions, so "
        "the honest version of this question is whether a stock that OPENED "
        "further from the implied gap than usual closes the difference "
        "afterwards.")

    # 10. walk-forward model comparison
    if heavy:
        wf = kre.walk_forward(rows, CANDIDATE_MODELS, "gap",
                              min_train=int(lim["min_train_n"]),
                              step=int(lim["walk_step"]))
        out["walk_forward"] = wf
        out["model_comparison"] = kre.compare_models(wf, BASELINE_MODEL)
        out["model_comparison"]["note"] = (
            "Expanding-window folds; every prediction comes from a model "
            "fitted only on sessions strictly earlier than the one it is "
            "scoring. A model counts as beating the baseline only when it is "
            "better on BOTH the size of the error and the direction — winning "
            "one while losing the other shows nothing.")
        # 11. the primary-driver decision, re-evaluated here because this is
        # the only place the expensive walk-forward is already being paid
        # for. The panel reads the persisted answer; it never causes this.
        try:
            out["primary_driver"] = primary_driver(sym, force=force)
        except Exception as exc:      # noqa: BLE001
            out["primary_driver"] = {"ok": False, "reason": str(exc)[:200]}
    with _LOCK:
        _REPORT_MEM[key] = ((f.get("last_date"), f.get("ok")), out)
        if len(_REPORT_MEM) > 16:
            _REPORT_MEM.pop(next(iter(_REPORT_MEM)), None)
    return dict(out)


# ── the long-run validation routine (never a CI test) ───────────────────────

VALIDATION_PAIRS = [("kospi", "SMH"), ("kospi", "QQQ"), ("hynix", "MU"),
                    ("samsung", "SMH"), ("tsmc", "NVDA"), ("nikkei", "SMH")]


def validation(window: str = "max", force: bool = False) -> dict:
    """Reproduce the long-run relationships against CURRENT provider data.

    Kept separate from the unit tests on purpose. Tests must be
    deterministic, and a test whose expected value is whatever the provider
    returned this morning is not a test — it is a tripwire that fires when
    a vendor revises a price. This routine is where live numbers belong:
    run it, read it, and compare it with what was seen before.
    """
    out = {"module": MODULE_VERSION, "engine": kre.ENGINE_VERSION,
           "window": window, "pairs": [], "generated": None,
           "note": ("Live provider data, so these move as history extends and "
                    "as the provider revises. Deliberately NOT asserted by "
                    "any unit test — the tests use frozen fixtures.")}
    try:
        out["generated"] = kl._now_et().isoformat(timespec="seconds")
    except Exception:               # pragma: no cover
        pass
    for name, sym in VALIDATION_PAIRS:
        f = frame(sym, force=force)
        if not f["ok"]:
            out["pairs"].append({"input": name, "target": sym,
                                 "ok": False, "reason": f["error"]})
            continue
        rows = [r for r in _window_rows(f["rows"], window)
                if r.get(name) is not None]
        if len(rows) < int(limits()["min_regression_n"]):
            out["pairs"].append({"input": name, "target": sym, "ok": False,
                                 "reason": f"only {len(rows)} matched sessions"})
            continue
        xs = [r[name] for r in rows]
        row = {"input": name, "input_label": ASIAN_INPUTS[name]["label"],
               "role": ASIAN_INPUTS[name]["role"], "target": sym, "ok": True,
               "n": len(rows), "first_date": rows[0]["date"],
               "last_date": rows[-1]["date"],
               "source": (f["sources"].get(name) or {}).get("source"),
               "us_source": (f["sources"].get("us") or {}).get("source")}
        for key, lab in (("gap", "opening_gap"), ("o2c", "open_to_close"),
                         ("full", "full_day")):
            r = kre.pearson(xs, [x[key] for x in rows])
            row[lab] = None if r is None else round(r, 3)
        usable = [r for r in rows if r.get("us_prev") is not None]
        i = kre.incremental([r["gap"] for r in usable],
                            [[r["us_prev"]] for r in usable],
                            [[r[name], r["us_prev"]] for r in usable],
                            names=[name, "us_prev"],
                            min_n=int(limits()["min_regression_n"]))
        if i["ok"]:
            add = next((p for p in i["added"] if p["name"] == name), None)
            row["incremental"] = {
                "r2_base": round(i["r2_base"], 4),
                "r2_full": round(i["r2_full"], 4),
                "delta_r2": round(i["delta_r2"], 4),
                "beta": None if not add else round(add["beta"], 4),
                "t": None if not add or add["t"] is None else round(add["t"], 2),
                "p": None if not add else add["p"],
                "n": i["base"]["n"],
            }
        out["pairs"].append(row)
    return out


# ── what the minute store can and cannot answer ─────────────────────────────

def minute_coverage(symbols=None) -> dict:
    """Whether a Friday high-of-day / low-of-day study is possible at all.

    It is not, and this function exists to say so with numbers rather than
    to quietly produce a study from the wrong data. Three separate reasons,
    each sufficient on its own:

      The gap scanner's event store keeps minute paths only for days that
      QUALIFIED as gap events — roughly a four percent opening move. Fridays
      that behaved normally are not in it, and a timing distribution built
      from gap days only would describe gap days, not Fridays.

      What it stores from those days is the favourable/adverse path and the
      time to each target. The clock time of the session high and low is
      not among the fields, so it could not be read out even for the days
      that are covered.

      The upstream minute source retains roughly six months. Multi-year
      Friday coverage cannot be back-filled, only accumulated forward.

    The honest output is a coverage count and that explanation.
    """
    syms = list(symbols or ["SMH", "MU", "NVDA", "AVGO", "AMD"])
    out = {"ok": False, "symbols": [], "fridays_with_minute_paths": 0,
           "verdict": "NOT MEASURABLE WITH THE DATA THIS APP HOLDS",
           "reasons": [
               "The gap event store keeps minute paths only for days that "
               "qualified as gap events, so ordinary Fridays are absent and "
               "any distribution built from it would describe gap days.",
               "Stored minute outcomes carry the favourable and adverse path "
               "and the time to each target, but not the clock time of the "
               "session high or low — so the field this study needs does not "
               "exist even where the days do.",
               "The upstream minute source retains about six months, so "
               "multi-year Friday coverage cannot be back-filled, only "
               "accumulated going forward.",
           ],
           "what_would_enable_it": (
               "Recording the clock time of the session high and low for "
               "every followed ticker every day, not only on gap days. That "
               "is a forward-accumulating capture like the option chain one, "
               "and it would need roughly a year before a Friday-only split "
               "held enough sessions to say anything."),
           }
    try:
        import gap_scan as gs
    except Exception as exc:        # pragma: no cover
        out["error"] = f"gap store unavailable: {exc}"
        return out
    total = 0
    for sym in syms:
        try:
            store = gs.load_store(sym)
        except Exception:
            continue
        evs = store.get("events") or []
        with_minutes = [e for e in evs if (e.get("outcomes") or {}).get("minute")]
        fridays = 0
        for e in with_minutes:
            try:
                from datetime import date as _d
                if _d.fromisoformat(str(e.get("date"))[:10]).weekday() == 4:
                    fridays += 1
            except ValueError:
                continue
        total += fridays
        out["symbols"].append({"symbol": sym, "events": len(evs),
                               "with_minute_paths": len(with_minutes),
                               "fridays_with_minute_paths": fridays})
    out["fridays_with_minute_paths"] = total
    out["ok"] = True
    return out


# ── which Korean input is this ticker's primary driver ──────────────────────
# Reading the answer is cheap and reading it is what the panel does. WORKING
# THE ANSWER OUT is expensive — a walk-forward re-fits every candidate once
# per fold — so it happens only behind the research endpoint, and the panel
# never triggers it. That separation is the whole reason this is two
# functions rather than one.

DRIVER_CANDIDATES = {
    # The single Korean inputs, plus the two baselines the absolute quality
    # floor is measured against. Nothing multi-input is a candidate here:
    # the question is which Korean market leads this ticker, not which
    # combination of them fits best.
    "mean": [],
    "kospi": ["kospi"],
    "samsung": ["samsung"],
    "hynix": ["hynix"],
}


def _driver_path(symbol: str) -> Path | None:
    root = kl._DATA_DIR
    if not root:
        return None
    p = Path(root) / "korea" / "drivers"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:                 # pragma: no cover
        return None
    safe = "".join(c if c.isalnum() else "_" for c in symbol.upper())
    return p / f"{safe}.json"


def driver_state(symbol: str) -> dict:
    """The stored answer, read from disk. Cheap enough for the panel.

    Returns NO CLEAR PRIMARY DRIVER when nothing has been decided yet,
    which is the honest answer rather than a placeholder: until the
    out-of-sample comparison has actually been run for this ticker, nobody
    has established that any Korean input leads it.
    """
    sym = (symbol or "").strip().upper()
    p = _driver_path(sym)
    blank = {"symbol": sym, "driver": None, "verdict": kre.DRIVER_NONE,
             "basis": "NOT YET EVALUATED", "since": None, "evidence": None,
             "detail": ("No out-of-sample comparison has been run for this "
                        "ticker yet, so no Korean input has been shown to "
                        "lead it. That is a statement about what has been "
                        "measured, not about the market.")}
    if p is None or not p.exists():
        return blank
    try:
        d = json.loads(p.read_text())
    except Exception:                 # pragma: no cover
        return blank
    if not isinstance(d, dict):
        return blank
    d.setdefault("symbol", sym)
    d.setdefault("basis", "OUT OF SAMPLE WALK-FORWARD")
    return d


def primary_driver(symbol: str, force: bool = False) -> dict:
    """Work out — and persist — which Korean input leads this ticker.

    EVERY GATE IS OUT OF SAMPLE. Candidates are scored by expanding-window
    walk-forward on one shared evaluation set, then filtered by an absolute
    quality floor before any of them is compared to the incumbent. A
    difference in in-sample correlation, however large, cannot move the
    driver; that is exactly the mechanism that produces Hynix on Monday and
    Samsung on Wednesday.

    THE DECISION IS ARCHIVED, NOT JUST APPLIED. Every evaluation appends to
    a log carrying the incumbent, the challenger, both sets of evidence,
    whether the switch was accepted or refused, why, and the settings hash
    it was decided under. Without that a changing driver is unexplainable
    after the fact, and an unexplainable driver is one nobody can trust.
    """
    sym = (symbol or "").strip().upper()
    f = frame(sym, force=force)
    prior = driver_state(sym)
    if not f["ok"]:
        return dict(prior, ok=False, reason=f["error"])
    lim = limits()
    walk = kre.walk_forward(f["rows"], DRIVER_CANDIDATES, "gap",
                            min_train=int(lim["min_train_n"]),
                            step=int(lim["walk_step"]))
    if not walk.get("ok"):
        return dict(prior, ok=False, reason=walk.get("reason"),
                    walk_forward=walk)
    scores = {k: v for k, v in (walk.get("models") or {}).items()
              if k not in ("mean", "zero")}
    gates = kl._section("driver_selection")
    today = f.get("last_date")
    decision = kre.driver_decision(scores, incumbent=prior, gates=gates,
                                   today=today)
    entry = {
        "at": today, "verdict": decision["verdict"],
        "incumbent": decision["incumbent"], "challenger": decision["challenger"],
        "chosen": decision["driver"], "changed": bool(decision["changed"]),
        "detail": decision["detail"],
        "direction_gain_points": decision.get("direction_gain_points"),
        "mae_relative_gain": decision.get("mae_relative_gain"),
        "evidence": {r["model"]: {k: r[k] for k in
                                  ("n", "direction_pct", "mae_pct",
                                   "rmse_ratio", "eligible", "fails")}
                     for r in decision["eligibility"]["rows"]},
        "gates": decision["gates"],
        "config_hash": kl.config()[1],
        "engine": kre.ENGINE_VERSION,
        "walk_forward_n": walk.get("n"),
        "folds": walk.get("folds"),
    }
    log = list(prior.get("log") or [])[-49:]
    log.append(entry)
    state = {
        "symbol": sym, "ok": True,
        "driver": decision["driver"], "verdict": decision["verdict"],
        "basis": "OUT OF SAMPLE WALK-FORWARD",
        "detail": decision["detail"],
        "since": (today if decision["changed"] or not prior.get("since")
                  else prior.get("since")),
        "evidence": entry["evidence"],
        "streak": decision.get("streak"),
        "eligibility": decision["eligibility"],
        "evaluated_at": today,
        "config_hash": kl.config()[1],
        "log": log,
    }
    p = _driver_path(sym)
    if p is not None:
        try:
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, separators=(",", ":")))
            tmp.replace(p)
        except Exception as exc:      # pragma: no cover
            print(f"[korea-research] driver state write failed {sym}: {exc}")
    if decision["changed"]:
        # A state transition, and one of the few things in this feature
        # worth a log line. A driver that changes silently is the thing
        # §21 exists to prevent.
        print(f"[korea-research] primary driver for {sym}: "
              f"{decision['incumbent']} -> {decision['driver']} "
              f"({decision['verdict']})")
    return state
