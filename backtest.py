# Natural-language backtesting lab (app v3.43).
#
# Three parts, dependency-injected from options_dashboard.py like intraday.py:
#
#   1. PARSER  — a deterministic trading grammar (regex clause classifier),
#      NOT an LLM: it converts plain English ("buy stocks that open down 2%,
#      reverse above the open, volume 2x the 20 day average; exit at +5%,
#      -2% stop, or the close") into an explicit JSON rule set. Every clause
#      it cannot interpret is surfaced as a warning so the user can edit the
#      rules by hand before running — nothing is silently guessed.
#
#   2. ENGINE  — event-driven simulator over Schwab daily bars (and 1-minute
#      bars for intraday rules). Look-ahead safety: signals are computed from
#      data up to bar t only and filled at the NEXT bar's open. Costs are
#      modeled (spread, slippage, commission) and thin names are skipped as
#      unfillable rather than filled at fantasy prices.
#
#   3. JOBS    — backtests run on a background thread with progress the UI
#      polls; the last completed result is persisted to the stable data dir.
#
# Honesty rules (the user asked for loud warnings, so every one of these is
# reported in result["warnings"]):
#   - No historical option quotes exist in our data: option strategies are
#     MODEL-PRICED (Black-Scholes on realized vol) and labeled as estimates.
#   - No historical news feed, no historical IV/Greeks: those conditions are
#     rejected with a warning instead of pretend-tested.
#   - Minute data only reaches back ~6 months: intraday tests are clipped
#     and say so.

from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# ── module wiring (options_dashboard.configure() fills these) ───────────────
_schwab_getter = lambda: None
_minute_day_fn = lambda sym, d: None
_universe_fn = lambda: {"starred": [], "all": []}
_data_dir: Path | None = None

def configure(schwab_getter, minute_day_fn, universe_fn, data_dir):
    global _schwab_getter, _minute_day_fn, _universe_fn, _data_dir
    _schwab_getter = schwab_getter
    _minute_day_fn = minute_day_fn
    _universe_fn = universe_fn
    _data_dir = Path(data_dir) if data_dir else None


# ════════════════════════════════════════════════════════════════════════════
# 1) PARSER — plain English → JSON rules
# ════════════════════════════════════════════════════════════════════════════

_NUM = r"(\d+(?:\.\d+)?)"

def _f(m, i=1):
    try:
        return float(m.group(i))
    except Exception:
        return None

# Each pattern: (compiled regex, builder(match) -> (section, rule) | None)
# section is "entry", "exit", "sizing", "universe", "period", "options",
# "direction", or "warn".

def _build_patterns():
    P = []
    def add(rx, fn):
        P.append((re.compile(rx, re.I), fn))

    # ── unsupported data first (so they don't half-match something else) ──
    add(r"\b(news|headline|announcement)\b",
        lambda m: ("warn", "News-based conditions can't be tested: no historical news feed is available in this app's data."))
    add(r"\b(earnings)\b",
        lambda m: ("warn", "Earnings-date conditions can't be tested yet: no reliable historical earnings calendar is available in this app's data."))
    add(r"\b(iv|implied vol\w*)\b.*?\b(above|below|over|under|rank|percentile)\b",
        lambda m: ("warn", "Historical implied volatility isn't available. IV conditions are skipped; option premiums are model-priced from realized volatility instead."))
    add(r"\b(delta|gamma|theta|vega)\b\s*(above|below|over|under|of|at)?\s*" + _NUM,
        lambda m: ("options_delta", _f(m, 3)) if m.group(1).lower() == "delta"
        else ("warn", f"Historical {m.group(1).lower()} isn't available — Greek-based conditions other than strike-by-delta are skipped."))

    # ── direction / instrument ──
    add(r"\b(short|fade|sell short)\b", lambda m: ("direction", "short"))
    add(r"\b(call)s?\b", lambda m: ("options_right", "call"))
    add(r"\b(put)s?\b", lambda m: ("options_right", "put"))
    add(_NUM + r"\s*(?:day|dte)s?\s*(?:to\s*)?(?:expir\w*|dte|out)?\b(?=.*\b(option|call|put|dte)\b)?",
        lambda m: None)  # placeholder: dte handled below with explicit keyword
    add(_NUM + r"\s*dte\b", lambda m: ("options_dte", _f(m)))
    add(_NUM + r"\s*days?\s*to\s*expir\w*", lambda m: ("options_dte", _f(m)))
    add(r"\bweekly options?\b", lambda m: ("options_dte", 5.0))
    add(r"\bmonthly options?\b", lambda m: ("options_dte", 30.0))
    add(r"\b(at.the.money|atm)\b", lambda m: ("options_strike", {"mode": "atm"}))
    add(_NUM + r"\s*%\s*(otm|out of the money)", lambda m: ("options_strike", {"mode": "otm_pct", "value": _f(m)}))
    add(_NUM + r"\s*%\s*(itm|in the money)", lambda m: ("options_strike", {"mode": "itm_pct", "value": _f(m)}))
    add(_NUM + r"\s*delta\b", lambda m: ("options_strike", {"mode": "delta", "value": _f(m)}))

    # ── exits ──
    add(_NUM + r"\s*%\s*(?:profit|target|gain|take.?profit)|(?:profit|target|gain)\s*(?:of|at)?\s*" + _NUM + r"\s*%",
        lambda m: ("exit", {"type": "profit_pct", "value": _f(m) or _f(m, 2)}))
    add(r"(?:trail\w*)\s*(?:stop)?\s*(?:of|at)?\s*" + _NUM + r"\s*%|" + _NUM + r"\s*%\s*trail\w*\s*stop",
        lambda m: ("exit", {"type": "trailing_stop_pct", "value": _f(m) or _f(m, 2)}))
    add(_NUM + r"\s*%\s*stop(?:.?loss)?|stop(?:.?loss)?\s*(?:of|at)?\s*" + _NUM + r"\s*%",
        lambda m: ("exit", {"type": "stop_pct", "value": _f(m) or _f(m, 2)}))
    add(r"\b(?:before|by|at)\s+(?:the\s+)?(?:market\s+)?clos(?:e|es|ing)\b|\bend of (?:the )?day\b|\bsame day\b|\bintraday only\b",
        lambda m: ("exit", {"type": "same_day_close"}))
    add(r"\b(?:hold|exit|sell)\w*\s*(?:for|after|in)?\s*" + _NUM + r"\s*(?:trading\s*)?days?\b",
        lambda m: ("exit", {"type": "time_days", "value": _f(m)}))
    add(r"\bhold\w*\s*(?:for|until)?\s*expir\w*", lambda m: ("exit", {"type": "hold_to_expiry"})),

    # ── sizing ──
    add(r"\brisk\s*" + _NUM + r"\s*%", lambda m: ("sizing", {"mode": "risk_pct", "value": _f(m)}))
    add(r"\$\s*([\d,]+)\s*(?:per|a|each)\s*(?:trade|position)",
        lambda m: ("sizing", {"mode": "fixed_dollar", "value": float(m.group(1).replace(",", ""))}))
    add(_NUM + r"\s*%\s*of\s*(?:my\s*)?(?:equity|account|capital|portfolio)",
        lambda m: ("sizing", {"mode": "pct_equity", "value": _f(m)}))
    add(r"\b(?:max|at most|up to)\s*" + _NUM + r"\s*(?:open\s*)?positions?",
        lambda m: ("sizing_max", _f(m)))

    # ── period ──
    add(r"\b(?:last|past|previous)\s*" + _NUM + r"\s*year", lambda m: ("period_days", _f(m) * 365))
    add(r"\b(?:last|past|previous)\s*" + _NUM + r"\s*month", lambda m: ("period_days", _f(m) * 30))
    add(r"\b(?:last|past|previous)\s*" + _NUM + r"\s*(?:trading\s*)?days?", lambda m: ("period_days", _f(m)))
    add(r"\bsince\s+(20\d\d)", lambda m: ("period_since", m.group(1)))

    # ── entry conditions ──
    add(r"\b(?:ris\w*|rall\w*|gain\w*|climb\w*|surg\w*)\s*(?:by\s*)?(?:more than\s*|at least\s*|over\s*)?" + _NUM + r"\s*%\s*(?:or more\s*)?(?:with)?in\s*" + _NUM + r"\s*(?:trading\s*)?days?",
        lambda m: ("entry", {"type": "move_pct", "days": int(_f(m, 2)), "op": ">=", "value": _f(m)}))
    add(r"\b(?:fall\w*|drop\w*|declin\w*|los\w*|plung\w*)\s*(?:by\s*)?(?:more than\s*|at least\s*|over\s*)?" + _NUM + r"\s*%\s*(?:or more\s*)?(?:with)?in\s*" + _NUM + r"\s*(?:trading\s*)?days?",
        lambda m: ("entry", {"type": "move_pct", "days": int(_f(m, 2)), "op": "<=", "value": -_f(m)}))
    add(r"\b(?:open\w*|gap\w*)\s*(?:down|lower)\s*(?:by\s*)?(?:at least\s*)?" + _NUM + r"\s*%",
        lambda m: ("entry", {"type": "gap_pct", "op": "<=", "value": -abs(_f(m))}))
    add(r"\b(?:open\w*|gap\w*)\s*(?:up|higher)\s*(?:by\s*)?(?:at least\s*)?" + _NUM + r"\s*%",
        lambda m: ("entry", {"type": "gap_pct", "op": ">=", "value": abs(_f(m))}))
    add(r"\b(?:revers\w*|recover\w*|cross\w*|reclaim\w*|trade\w*\s*back)\s*(?:up\s*)?(?:above|over)\s*(?:the\s*)?open",
        lambda m: ("entry", {"type": "cross_above_open"}))
    add(r"\b(?:break\w*|cross\w*|fall\w*|drop\w*)\s*(?:back\s*)?(?:below|under)\s*(?:the\s*)?open",
        lambda m: ("entry", {"type": "cross_below_open"}))
    add(r"\bvolume\s*(?:is|of)?\s*(?:at least\s*)?" + _NUM + r"\s*(?:x|times)\s*(?:the\s*)?(?:" + _NUM + r"\s*.?day\s*)?(?:average|avg)",
        lambda m: ("entry", {"type": "rel_volume", "mult": _f(m), "lookback": int(_f(m, 2) or 20)}))
    _WORD_MULT = {"twice": 2.0, "double": 2.0, "2x": 2.0, "two times": 2.0,
                  "triple": 3.0, "three times": 3.0, "3x": 3.0}
    add(r"\bvolume\s*(?:is|of)?\s*(?:at least\s*)?(twice|double|2x|two times|triple|three times|3x)\s*(?:the\s*)?(?:(\d+)\s*.?day\s*)?(?:average|avg)",
        lambda m: ("entry", {"type": "rel_volume", "mult": _WORD_MULT[m.group(1).lower()],
                             "lookback": int(m.group(2) or 20)}))
    add(r"\b(?:down|fall\w*|drop\w*|decline\w*|is)\s*(?:at least\s*)?" + _NUM + r"\s*%\s*(?:or more\s*)?(?:down\s*)?"
        r"(?:drawdown\s*)?(?:from|off|below)\s*(?:its|a|the)?\s*(?:recent\s+|52.?week\s+|all.?time\s+)?high",
        lambda m: ("entry", {"type": "drawdown_from_high", "pct": abs(_f(m)), "lookback": 252}))
    add(_NUM + r"\s*%\s*(?:down\s*)?drawdown", lambda m: ("entry", {"type": "drawdown_from_high", "pct": abs(_f(m)), "lookback": 252}))
    add(r"\brsi\s*(?:\(?\s*" + _NUM + r"\s*\)?\s*)?(?:is\s*)?(below|under|above|over)\s*" + _NUM,
        lambda m: ("entry", {"type": "rsi", "period": int(_f(m) or 14),
                             "op": "<=" if m.group(2).lower() in ("below", "under") else ">=",
                             "value": _f(m, 3)}))
    add(_NUM + r"\s*.?day\s*(?:simple\s*|exponential\s*)?(?:moving\s*)?average.*?cross\w*\s*(above|over|below|under).*?" + _NUM + r"\s*.?day",
        lambda m: ("entry", {"type": "sma_cross", "fast": int(_f(m)), "slow": int(_f(m, 3)),
                             "direction": "up" if m.group(2).lower() in ("above", "over") else "down"}))
    add(r"\b(?:price|stock|close\w*|trad\w*)\s*(?:is\s*)?(above|over|below|under)\s*(?:its|the)?\s*" + _NUM + r"\s*.?day\s*(?:simple\s*|exponential\s*)?(?:moving\s*)?average",
        lambda m: ("entry", {"type": "price_vs_sma", "op": ">=" if m.group(1).lower() in ("above", "over") else "<=",
                             "period": int(_f(m, 2))}))
    add(r"\bnew\s*" + _NUM + r"\s*.?day\s*(high|low)",
        lambda m: ("entry", {"type": "new_high" if m.group(2).lower() == "high" else "new_low", "lookback": int(_f(m))}))
    add(r"\bnew\s*52.?week\s*(high|low)",
        lambda m: ("entry", {"type": "new_high" if m.group(1).lower() == "high" else "new_low", "lookback": 252}))
    add(r"\b(down|up)\s*" + _NUM + r"\s*days?\s*in a row|\b" + _NUM + r"\s*consecutive\s*(down|up)\s*days",
        lambda m: ("entry", {"type": "consec_down" if (m.group(1) or m.group(4) or "").lower() == "down" else "consec_up",
                             "n": int(_f(m, 2) or _f(m, 3) or 3)}))
    add(r"\b(?:down|fell|drops?|dropped|losing)\s*(?:at least\s*)?" + _NUM + r"\s*%\s*(?:today|on the day|intraday)?(?!\s*(?:from|off|below|drawdown))",
        lambda m: ("entry", {"type": "day_change_pct", "op": "<=", "value": -abs(_f(m))}))
    add(r"\b(?:up|gains?|gained|rall\w*)\s*(?:at least\s*)?" + _NUM + r"\s*%\s*(?:today|on the day|intraday)?(?!\s*(?:from|off|below|drawdown))",
        lambda m: ("entry", {"type": "day_change_pct", "op": ">=", "value": abs(_f(m))}))
    add(r"\bprice\s*(?:is\s*)?(above|over|below|under)\s*\$?\s*" + _NUM + r"(?!\s*%)",
        lambda m: ("entry", {"type": "price_abs", "op": ">=" if m.group(1).lower() in ("above", "over") else "<=",
                             "value": _f(m, 2)}))
    add(r"\b(?:market|spy)\s*(?:is\s*)?(?:in\s*)?(?:an?\s*)?(uptrend|downtrend|bull\w*|bear\w*)",
        lambda m: ("entry", {"type": "market_regime",
                             "regime": "uptrend" if m.group(1).lower().startswith(("up", "bull")) else "downtrend"}))
    add(r"\bspy\s*(above|over|below|under)\s*(?:its|the)?\s*" + _NUM + r"\s*.?day",
        lambda m: ("entry", {"type": "market_regime",
                             "regime": "uptrend" if m.group(1).lower() in ("above", "over") else "downtrend"}))
    return P

_PATTERNS = _build_patterns()

_TICKER_LIST = re.compile(r"\bon\s+((?:[A-Z]{1,5})(?:\s*,\s*[A-Z]{1,5})*(?:\s*(?:,|and)\s*[A-Z]{1,5})?)\b")

# Clause splitter: sentences, then commas / "and" / "or" boundaries. Numbers
# like "2%" never contain these, so splitting is safe for this grammar.
_CLAUSE_SPLIT = re.compile(r"[.;\n]+|,| and | or | then ", re.I)

_COND_LABELS = {
    "gap_pct": "Gap at the open vs prior close",
    "cross_above_open": "Price crosses back ABOVE the opening price (intraday)",
    "cross_below_open": "Price crosses back BELOW the opening price (intraday)",
    "rel_volume": "Volume vs N-day average",
    "drawdown_from_high": "Drawdown from recent high",
    "rsi": "RSI",
    "sma_cross": "Moving-average cross",
    "price_vs_sma": "Price vs moving average",
    "new_high": "New N-day high",
    "new_low": "New N-day low",
    "consec_down": "Consecutive down days",
    "consec_up": "Consecutive up days",
    "day_change_pct": "Change on the day",
    "move_pct": "Move over trailing N days",
    "price_abs": "Absolute price filter",
    "market_regime": "Market (SPY) regime filter",
}

_INTRADAY_TYPES = {"cross_above_open", "cross_below_open"}


_WORDNUM = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}

def parse_strategy(text: str) -> dict:
    """Deterministic English → rules. Returns {rules, warnings, unparsed}."""
    text = (text or "").strip()
    # Normalize spoken forms so the grammar sees canonical tokens:
    # "10 percent" → "10%", "three days" → "3 days".
    text = re.sub(r"\bper\s?cent\b", "%", text, flags=re.I)
    text = re.sub(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\b",
                  lambda m: _WORDNUM[m.group(1).lower()], text, flags=re.I)
    rules = {
        "instrument": "stock",
        "direction": "long",
        "universe": {"source": "starred", "symbols": []},
        "entry": [],
        "exit": [],
        "sizing": {"mode": "fixed_dollar", "value": 10000, "max_positions": 5},
        "costs": {"commission": 0.0, "slippage_bps": 5, "spread_model": "auto",
                  "min_dollar_vol_mult": 20},
        "options": None,
        "period_days": 365,
    }
    warnings: list[str] = []
    unparsed: list[str] = []
    if not text:
        return {"rules": rules, "warnings": ["Empty strategy text."], "unparsed": []}

    opt: dict = {}
    matched_spans: list[tuple[int, int]] = []
    cond_spans: list[tuple[dict, tuple[int, int]]] = []

    m = _TICKER_LIST.search(text)
    if m:
        syms = re.split(r"\s*(?:,|and)\s*", m.group(1))
        syms = [s.strip().upper() for s in syms if s.strip()]
        if syms:
            rules["universe"] = {"source": "symbols", "symbols": syms}
            matched_spans.append(m.span())
    for rx, fn in _PATTERNS:
        for mm in rx.finditer(text):
            try:
                res = fn(mm)
            except Exception:
                res = None
            if not res:
                continue
            matched_spans.append(mm.span())
            kind, val = res
            if kind == "warn":
                if val not in warnings:
                    warnings.append(val)
            elif kind == "entry":
                if val not in rules["entry"]:
                    rules["entry"].append(val)
                    cond_spans.append((val, mm.span()))
            elif kind == "exit":
                if val not in rules["exit"]:
                    rules["exit"].append(val)
            elif kind == "direction":
                rules["direction"] = val
            elif kind == "sizing":
                rules["sizing"].update(val)
            elif kind == "sizing_max":
                rules["sizing"]["max_positions"] = int(val)
            elif kind == "period_days":
                rules["period_days"] = int(val)
            elif kind == "period_since":
                days = (datetime.now() - datetime(int(val), 1, 1)).days
                rules["period_days"] = max(30, days)
            elif kind == "options_right":
                opt.setdefault("right", val)
            elif kind == "options_dte":
                opt["dte"] = int(val)
            elif kind == "options_strike":
                opt["strike"] = val
            elif kind == "options_delta":
                opt["strike"] = {"mode": "delta", "value": val}

    # De-dupe overlap: "opens down 2%" matches BOTH gap_pct and the generic
    # day_change_pct — when their text spans overlap, the gap wins.
    gap_spans = [sp for cond, sp in cond_spans if cond.get("type") == "gap_pct"]
    if gap_spans:
        rules["entry"] = [
            cond for cond in rules["entry"]
            if not (cond.get("type") == "day_change_pct" and any(
                not (sp[1] <= a or sp[0] >= b)
                for cond2, sp in cond_spans if cond2 is cond
                for a, b in gap_spans))
        ]

    if opt:
        rules["instrument"] = "option"
        opt.setdefault("right", "put" if rules["direction"] == "short" else "call")
        opt.setdefault("dte", 30)
        opt.setdefault("strike", {"mode": "atm"})
        rules["options"] = opt
        warnings.append(
            "Option prices are MODELED (Black-Scholes on 20-day realized volatility), "
            "not historical quotes — no historical option data source is available. "
            "Treat option results as estimates.")

    # Surface clauses no pattern touched.
    for clause in _CLAUSE_SPLIT.split(text):
        c = clause.strip()
        if len(c) < 4:
            continue
        s = text.find(c)
        span = (s, s + len(c))
        covered = any(not (span[1] <= a or span[0] >= b) for a, b in matched_spans)
        if not covered:
            unparsed.append(c)

    if not rules["entry"]:
        warnings.append("No entry condition was recognized — add one below before running.")
    if not any(x["type"] in ("profit_pct", "stop_pct", "trailing_stop_pct",
                             "same_day_close", "time_days", "hold_to_expiry")
               for x in rules["exit"]):
        rules["exit"].append({"type": "time_days", "value": 10})
        warnings.append("No exit was recognized — a default 10-trading-day time exit was added; edit it below.")

    # Annotate for the editor UI.
    for c in rules["entry"]:
        c["label"] = _COND_LABELS.get(c["type"], c["type"])
    return {"rules": rules, "warnings": warnings, "unparsed": unparsed}


# ════════════════════════════════════════════════════════════════════════════
# 2) ENGINE
# ════════════════════════════════════════════════════════════════════════════

def _sma(vals, i, n):
    if i + 1 < n:
        return None
    w = vals[i + 1 - n:i + 1]
    return sum(w) / n

def _rsi(closes, i, period=14):
    if i < period:
        return None
    gains = losses = 0.0
    for k in range(i - period + 1, i + 1):
        d = closes[k] - closes[k - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)

def _realized_vol(closes, i, n=20):
    if i < n:
        return None
    rets = []
    for k in range(i - n + 1, i + 1):
        if closes[k - 1] > 0:
            rets.append(math.log(closes[k] / closes[k - 1]))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * 252)

from metrics import _norm_cdf, _bs_price as _metrics_bs_price, \
    _bs_delta as _metrics_bs_delta, risk_free_rate as _risk_free_rate

def _bs_price(spot, strike, t_years, vol, right, r=None):
    """MODELED price — no historical option quotes exist; every option
    result carries a modeled-prices warning. Delegates to the canonical
    metrics._bs_price; rate defaults to the app risk-free rate."""
    if vol is None or (t_years > 0 and vol <= 0) or spot <= 0 or strike <= 0:
        if t_years <= 0 and spot > 0 and strike > 0:
            return max(0.0, spot - strike) if right == "call" else max(0.0, strike - spot)
        return None
    if r is None:
        r = _risk_free_rate()[0]
    return _metrics_bs_price(spot, strike, t_years, vol, right, r=r)

def _bs_delta(spot, strike, t_years, vol, right, r=None):
    if t_years <= 0 or not vol or vol <= 0:
        return None
    if r is None:
        r = _risk_free_rate()[0]
    return _metrics_bs_delta(spot, strike, t_years, vol, right, r=r)

def _strike_for(spot, vol, dte, strike_cfg, right):
    mode = (strike_cfg or {}).get("mode", "atm")
    v = (strike_cfg or {}).get("value")
    if mode == "otm_pct" and v:
        raw = spot * (1 + v / 100.0) if right == "call" else spot * (1 - v / 100.0)
    elif mode == "itm_pct" and v:
        raw = spot * (1 - v / 100.0) if right == "call" else spot * (1 + v / 100.0)
    elif mode == "delta" and v and vol:
        # Solve strike whose |delta| ≈ v/100 by bisection over a wide band.
        target = abs(v) / 100.0
        lo, hi = spot * 0.5, spot * 2.0
        raw = spot
        for _ in range(40):
            mid = (lo + hi) / 2
            d = _bs_delta(spot, mid, dte / 365.0, vol, right)
            if d is None:
                break
            if (abs(d) > target and right == "call") or (abs(d) < target and right == "put"):
                lo = mid
            else:
                hi = mid
            raw = mid
    else:
        raw = spot
    # Snap to a plausible listed increment.
    inc = 0.5 if raw < 25 else (1.0 if raw < 100 else (5.0 if raw < 500 else 10.0))
    return round(raw / inc) * inc

def _est_spread_pct(price):
    """Realistic half-spread estimate by price bucket (stocks)."""
    if price is None or price <= 0:
        return 0.001
    if price < 5:
        return 0.004
    if price < 20:
        return 0.0015
    if price < 100:
        return 0.0006
    return 0.0003

def _op(op, a, b):
    return a <= b if op == "<=" else a >= b


class _Ctx:
    """Per-symbol daily context with indicators computed ONLY from bars ≤ i."""
    def __init__(self, bars, spy_regime_by_date):
        self.bars = bars
        self.closes = [b["close"] for b in bars]
        self.vols = [b.get("volume") or 0 for b in bars]
        self.spy = spy_regime_by_date

    def check(self, cond, i) -> bool | None:
        t = cond["type"]
        b = self.bars[i]
        if t == "gap_pct":
            if i == 0:
                return None
            prev = self.closes[i - 1]
            if not prev:
                return None
            gap = (b["open"] - prev) / prev * 100.0
            return _op(cond["op"], gap, cond["value"])
        if t == "day_change_pct":
            if i == 0:
                return None
            prev = self.closes[i - 1]
            if not prev:
                return None
            chg = (b["close"] - prev) / prev * 100.0
            return _op(cond["op"], chg, cond["value"])
        if t == "move_pct":
            w = int(cond.get("days") or 5)
            if i < w:
                return None
            c0 = self.closes[i - w]
            if not c0:
                return None
            r = (self.closes[i] - c0) / c0 * 100.0
            return _op(cond["op"], r, cond["value"])
        if t == "move_pct":
            # Return over the trailing N trading days (pattern-lab conversions).
            n = int(cond.get("days") or 5)
            if i < n or not self.closes[i - n]:
                return None
            r = (self.closes[i] - self.closes[i - n]) / self.closes[i - n] * 100.0
            return _op(cond["op"], r, cond["value"])
        if t == "rel_volume":
            n = cond.get("lookback", 20)
            if i < n:
                return None
            avg = sum(self.vols[i - n:i]) / n
            return avg > 0 and self.vols[i] >= cond["mult"] * avg
        if t == "drawdown_from_high":
            n = min(cond.get("lookback", 252), i)
            if n < 5:
                return None
            hi = max(self.closes[i - n:i + 1])
            return hi > 0 and (hi - self.closes[i]) / hi * 100.0 >= cond["pct"]
        if t == "rsi":
            r = _rsi(self.closes, i, cond.get("period", 14))
            return None if r is None else _op(cond["op"], r, cond["value"])
        if t == "sma_cross":
            f, s = cond["fast"], cond["slow"]
            a0, a1 = _sma(self.closes, i - 1, f), _sma(self.closes, i, f)
            b0, b1 = _sma(self.closes, i - 1, s), _sma(self.closes, i, s)
            if None in (a0, a1, b0, b1):
                return None
            if cond.get("direction") == "down":
                return a0 >= b0 and a1 < b1
            return a0 <= b0 and a1 > b1
        if t == "price_vs_sma":
            s = _sma(self.closes, i, cond["period"])
            return None if s is None else _op(cond["op"], self.closes[i], s)
        if t in ("new_high", "new_low"):
            n = cond.get("lookback", 20)
            if i < n:
                return None
            window = self.closes[i - n:i]
            return self.closes[i] > max(window) if t == "new_high" else self.closes[i] < min(window)
        if t in ("consec_down", "consec_up"):
            n = cond.get("n", 3)
            if i < n:
                return None
            for k in range(i - n + 1, i + 1):
                d = self.closes[k] - self.closes[k - 1]
                if (t == "consec_down" and d >= 0) or (t == "consec_up" and d <= 0):
                    return False
            return True
        if t == "price_abs":
            return _op(cond["op"], self.closes[i], cond["value"])
        if t == "market_regime":
            reg = self.spy.get(self.bars[i]["date"][:10])
            return reg == cond["regime"] if reg else None
        if t in _INTRADAY_TYPES:
            return None  # handled by the intraday path
        return None


def _spy_regimes(spy_bars):
    """date → uptrend/downtrend/chop using 50/200 SMA (past data only)."""
    closes = [b["close"] for b in spy_bars]
    out = {}
    for i, b in enumerate(spy_bars):
        s50, s200 = _sma(closes, i, 50), _sma(closes, i, 200)
        if s200 is None:
            out[b["date"][:10]] = None
        elif closes[i] > s200 and (s50 or 0) > s200:
            out[b["date"][:10]] = "uptrend"
        elif closes[i] < s200:
            out[b["date"][:10]] = "downtrend"
        else:
            out[b["date"][:10]] = "chop"
    return out


def _finish_trade(trade, exit_px, exit_date, reason, costs_per_side):
    qty = trade["qty"]
    mult = trade.get("mult", 1)
    gross = (exit_px - trade["entry_px"]) * qty * mult
    if trade["direction"] == "short":
        gross = -gross
    net = gross - costs_per_side * 2
    trade.update({
        "exit_px": round(exit_px, 4), "exit_date": exit_date, "reason": reason,
        "pnl": round(net, 2),
        "pnl_pct": round(net / max(1e-9, trade["cost_basis"]) * 100.0, 2),
    })
    return trade


def run_backtest(rules: dict, progress_cb=None) -> dict:
    """Execute a parsed/edited rule set. Synchronous; the job layer threads it."""
    warnings: list[str] = []
    t0 = time.time()
    c = _schwab_getter()
    if c is None:
        return {"error": "Schwab is not connected — historical data is unavailable."}

    entry = rules.get("entry") or []
    exits = rules.get("exit") or []
    direction = rules.get("direction", "long")
    sizing = rules.get("sizing") or {}
    costs = rules.get("costs") or {}
    options = rules.get("options")
    is_option = rules.get("instrument") == "option" and options
    if is_option and direction == "short":
        warnings.append("Selling options short isn't modeled — the bearish view is expressed as LONG puts instead.")
        rules = {**rules, "direction": "long"}
        if options.get("right") != "put":
            options = {**options, "right": "put"}
            rules["options"] = options
        direction = "long"
    intraday_mode = any(e.get("type") in _INTRADAY_TYPES for e in entry) or \
        (any(x.get("type") == "same_day_close" for x in exits)
         and any(e.get("type") == "gap_pct" for e in entry))

    # ── universe ──
    uni = rules.get("universe") or {}
    if uni.get("source") == "symbols" and uni.get("symbols"):
        symbols = [s.upper() for s in uni["symbols"]]
    else:
        u = _universe_fn() or {}
        symbols = list(u.get("starred") or []) or list(u.get("all") or [])[:30]
    cap = 15 if intraday_mode else 50
    if len(symbols) > cap:
        warnings.append(f"Universe clipped to {cap} symbols (of {len(symbols)}) to stay inside data-provider rate limits.")
        symbols = symbols[:cap]
    if not symbols:
        return {"error": "No symbols to test — star tickers on your watchlist or name symbols in the strategy (e.g. 'on AAPL, MSFT')."}

    period_days = int(rules.get("period_days") or 365)
    if intraday_mode:
        max_intra = 60
        eff_days = min(period_days, 90)
        if period_days > 90:
            warnings.append("Intraday rules need 1-minute bars, which only reach back ~6 months; the test window was clipped to the last 90 calendar days.")
    else:
        eff_days = min(period_days, 730)
        if period_days > 730:
            warnings.append("Daily history is limited to ~2 years; the test window was clipped.")

    # ── data ──
    hist_days = min(730, eff_days + 320)  # +320: indicator warm-up (252d drawdown etc.)
    spy_bars = c.get_price_history("SPY", days=hist_days) or []
    spy_reg = _spy_regimes(spy_bars)

    per_symbol_bars = {}
    total_steps = len(symbols)
    for si, sym in enumerate(symbols):
        bars = c.get_price_history(sym, days=hist_days)
        if bars and len(bars) > 30:
            per_symbol_bars[sym] = bars
        else:
            warnings.append(f"{sym}: no usable daily history — skipped.")
        if progress_cb:
            progress_cb("loading daily history", si + 1, total_steps)
    if not per_symbol_bars:
        return {"error": "No historical data could be loaded for the requested symbols.", "warnings": warnings}

    slip = (costs.get("slippage_bps") or 5) / 10000.0
    commission = float(costs.get("commission") or 0.0)
    liq_mult = float(costs.get("min_dollar_vol_mult") or 20)
    per_trade_budget = float(sizing.get("value") or 10000)
    max_pos = int(sizing.get("max_positions") or 5)
    start_equity = 100_000.0

    signals = []      # (date_idx_key, sym, i) chronological entry signals
    trades = []
    skipped_liq = 0
    skipped_full = 0

    def cutoff_ok(bars, i):
        cutoff = (datetime.now() - timedelta(days=eff_days)).date().isoformat()
        return bars[i]["date"][:10] >= cutoff

    # ── signal generation (daily) ──
    daily_entry = [e for e in entry if e.get("type") not in _INTRADAY_TYPES]
    ctxs = {sym: _Ctx(bars, spy_reg) for sym, bars in per_symbol_bars.items()}
    for sym, bars in per_symbol_bars.items():
        ctx = ctxs[sym]
        last_i = len(bars) - 1
        for i in range(1, last_i):        # need bar i+1 for the fill → no look-ahead
            if not cutoff_ok(bars, i):
                continue
            ok = True
            for cond in daily_entry:
                r = ctx.check(cond, i)
                if r is not True:
                    ok = False
                    break
            if ok and daily_entry:
                signals.append((bars[i]["date"][:10], sym, i))

    signals.sort(key=lambda s: s[0])

    if intraday_mode:
        trades, skipped_liq = _run_intraday(
            rules, symbols, per_symbol_bars, ctxs, eff_days, slip, commission,
            liq_mult, per_trade_budget, spy_reg, warnings, progress_cb)
    else:
        # ── daily portfolio walk ──
        open_pos: list[dict] = []
        for sig_date, sym, i in signals:
            bars = per_symbol_bars[sym]
            fill_bar = bars[i + 1]
            open_pos = [p for p in open_pos if p["exit_date"] < sig_date]
            if len(open_pos) >= max_pos:
                skipped_full += 1
                continue
            avg_dvol = sum((b["close"] or 0) * (b.get("volume") or 0) for b in bars[max(0, i - 20):i]) / max(1, min(20, i))
            if avg_dvol < liq_mult * per_trade_budget:
                skipped_liq += 1
                continue
            tr = _simulate_daily_trade(rules, sym, bars, ctxs[sym], i, slip, commission,
                                       per_trade_budget, is_option, options, warnings)
            if tr:
                tr["regime"] = spy_reg.get(sig_date) or "unknown"
                trades.append(tr)
                open_pos.append({"exit_date": tr["exit_date"]})

    # ── metrics ──
    result = _metrics(trades, start_equity, warnings)
    result["skipped_no_liquidity"] = skipped_liq
    result["skipped_max_positions"] = skipped_full
    result["symbols_tested"] = sorted(per_symbol_bars.keys())
    result["mode"] = "intraday (1-minute bars)" if intraday_mode else "daily bars"
    result["rules"] = rules
    result["elapsed_sec"] = round(time.time() - t0, 1)
    # Structured modeling disclosure (v3.64) — the UI renders this as a
    # prominent MODELED badge; the assumptions are the exact model inputs.
    result["modeled"] = {
        "option_premiums": bool(is_option),
        "assumptions": ([
            "Option premiums: Black-Scholes on 20-day realized vol (no historical option quotes)",
            "Option spread: max($0.02, 1.5% of premium) each way",
            "Commission: max(user setting, $0.65/contract)",
        ] if is_option else []) + [
            f"Stock slippage: {int(slip * 10000)} bps + price-bucket half-spread",
            "Fills at the NEXT bar's open (no look-ahead)",
        ],
    }
    if is_option:
        result["warnings"].insert(0,
            "OPTION RESULTS ARE MODEL-PRICED (Black-Scholes on 20-day realized vol + estimated spreads). "
            "No historical option quotes are available — treat these numbers as directional estimates, not fills you could have had.")
    return result


def _simulate_daily_trade(rules, sym, bars, ctx, i, slip, commission,
                          budget, is_option, options, warnings):
    """Entry signal at bar i → fill at bar i+1 open. Walk forward to exit."""
    direction = rules.get("direction", "long")
    exits = rules.get("exit") or []
    profit = next((x["value"] for x in exits if x["type"] == "profit_pct"), None)
    stop = next((x["value"] for x in exits if x["type"] == "stop_pct"), None)
    trail = next((x["value"] for x in exits if x["type"] == "trailing_stop_pct"), None)
    tdays = next((int(x.get("value") or 10) for x in exits if x["type"] == "time_days"), None)
    same_day = any(x["type"] == "same_day_close" for x in exits)
    to_expiry = any(x["type"] == "hold_to_expiry" for x in exits)

    fill = bars[i + 1]
    spread = _est_spread_pct(fill["open"])
    entry_px = fill["open"] * (1 + (slip + spread) * (1 if direction == "long" else -1))

    trade = {"symbol": sym, "entry_date": fill["date"][:10], "direction": direction}

    if is_option:
        vol = _realized_vol(ctx.closes, i, 20)
        if vol is None:
            return None
        iv = vol * 1.1  # realized→implied proxy; carries a loud warning upstream
        dte = int(options.get("dte") or 30)
        right = options.get("right", "call")
        strike = _strike_for(fill["open"], iv, dte, options.get("strike"), right)
        prem = _bs_price(fill["open"], strike, dte / 365.0, iv, right)
        if not prem or prem <= 0.02:
            return None
        opt_spread = max(0.02, prem * 0.015)
        entry_prem = prem + opt_spread  # buy at the modeled ask
        contracts = max(1, int(budget / (entry_prem * 100)))
        trade.update({"qty": contracts, "mult": 100, "entry_px": round(entry_prem, 3),
                      "cost_basis": entry_prem * contracts * 100,
                      "option": {"right": right, "strike": strike, "dte": dte}})
        commission = max(commission, 0.65 * contracts)
    else:
        qty = max(1, int(budget / max(0.01, entry_px)))
        trade.update({"qty": qty, "mult": 1, "entry_px": round(entry_px, 4),
                      "cost_basis": entry_px * qty})

    horizon = tdays if tdays else (int(options["dte"]) if (is_option and to_expiry) else 40)
    if same_day:
        horizon = 0
    best = trade["entry_px"]   # trailing-stop watermark (premium-space for options)

    for k in range(i + 1, min(len(bars), i + 2 + horizon)):
        b = bars[k]
        held_days = k - (i + 1)
        if is_option:
            # Model the option's value from the underlying's OHLC each day.
            vol = _realized_vol(ctx.closes, max(0, k - 1), 20)
            iv = (vol or 0.3) * 1.1
            dte_left = max(0, trade["option"]["dte"] - held_days)
            o = trade["option"]
            px_at = lambda spot: _bs_price(spot, o["strike"], dte_left / 365.0, iv, o["right"]) or 0.0
            hi_px, lo_px = px_at(b["high"]), px_at(b["low"])
            if o["right"] == "put":
                hi_px, lo_px = lo_px, hi_px    # put value peaks at the LOW
            close_px = px_at(b["close"])
            entry_ref = trade["entry_px"]
        else:
            hi_px, lo_px, close_px = b["high"], b["low"], b["close"]
            entry_ref = trade["entry_px"]

        # Conservative intra-bar ordering everywhere below: when both the stop
        # and the target sit inside the same bar, assume the STOP hit first.
        exit_spread = _est_spread_pct(b["close"]) if not is_option else 0
        if direction == "short" and not is_option:
            # Short stock: profit at the LOW, stopped at the HIGH.
            stop_px = entry_ref * (1 + stop / 100.0) if stop is not None else None
            tgt_px = entry_ref * (1 - profit / 100.0) if profit is not None else None
            if trail is not None:
                best = min(best, lo_px)
                tstop = best * (1 + trail / 100.0)
                stop_px = min(stop_px if stop_px is not None else float("inf"), tstop)
            if stop_px is not None and hi_px >= stop_px:
                return _finish_trade(trade, stop_px * (1 + slip + exit_spread), b["date"][:10], "stop", commission)
            if tgt_px is not None and lo_px <= tgt_px:
                return _finish_trade(trade, tgt_px * (1 + slip + exit_spread), b["date"][:10], "target", commission)
        else:
            # Long stock, or long option premium (eff highs/lows already
            # premium-space for options; puts had hi/lo swapped above).
            stop_px = entry_ref * (1 - stop / 100.0) if stop is not None else None
            tgt_px = entry_ref * (1 + profit / 100.0) if profit is not None else None
            if trail is not None:
                best = max(best, hi_px)
                tstop = best * (1 - trail / 100.0)
                stop_px = max(stop_px or 0, tstop)
            if stop_px is not None and lo_px <= stop_px:
                px = stop_px * (1 - slip - exit_spread)
                if is_option:
                    px = max(0.01, stop_px - max(0.02, stop_px * 0.015))
                return _finish_trade(trade, px, b["date"][:10], "stop", commission)
            if tgt_px is not None and hi_px >= tgt_px:
                px = tgt_px * (1 - slip - exit_spread)
                if is_option:
                    px = max(0.01, tgt_px - max(0.02, tgt_px * 0.015))
                return _finish_trade(trade, px, b["date"][:10], "target", commission)
        if held_days >= horizon:
            px = close_px * (1 - slip - exit_spread) if not is_option else max(0.01, close_px - max(0.02, close_px * 0.015))
            reason = "expiry" if (is_option and to_expiry) else ("time" if tdays else "close")
            return _finish_trade(trade, px, b["date"][:10], reason, commission)
    # Ran off the end of history with the position open → mark at last close.
    b = bars[-1]
    if is_option:
        return None  # can't fairly mark an unfinished modeled option
    px = b["close"] * (1 - slip)
    return _finish_trade(trade, px, b["date"][:10], "end_of_data", commission)


def _run_intraday(rules, symbols, per_symbol_bars, ctxs, eff_days, slip, commission,
                  liq_mult, budget, spy_reg, warnings, progress_cb):
    """Gap / open-cross strategies on 1-minute bars, one symbol-day at a time.
    Entry: condition confirmed on minute m → fill at minute m+1 open.
    Exits: target/stop intra-minute (stop first when both), forced close 15:55."""
    direction = rules.get("direction", "long")
    entry = rules.get("entry") or []
    exits = rules.get("exit") or []
    profit = next((x["value"] for x in exits if x["type"] == "profit_pct"), None)
    stop = next((x["value"] for x in exits if x["type"] == "stop_pct"), None)
    gap_conds = [e for e in entry if e["type"] == "gap_pct"]
    relvol = next((e for e in entry if e["type"] == "rel_volume"), None)
    want_cross_up = any(e["type"] == "cross_above_open" for e in entry)
    want_cross_dn = any(e["type"] == "cross_below_open" for e in entry)
    daily_side = [e for e in entry if e["type"] not in ("gap_pct", "rel_volume") and e["type"] not in _INTRADAY_TYPES]

    trades = []
    skipped_liq = 0
    # Candidate days: use DAILY bars to find days whose open gapped as asked —
    # dramatically cuts minute-data calls vs pulling every day for every symbol.
    tasks = []
    cutoff = (datetime.now() - timedelta(days=eff_days)).date().isoformat()
    for sym, bars in per_symbol_bars.items():
        ctx = ctxs[sym]
        for i in range(1, len(bars) - 1):
            d = bars[i]["date"][:10]
            if d < cutoff:
                continue
            ok = True
            for cond in gap_conds:
                if ctx.check(cond, i) is not True:
                    ok = False
                    break
            if ok:
                for cond in daily_side:
                    if ctx.check(cond, i - 1) is not True:   # info known BEFORE the open
                        ok = False
                        break
            if ok:
                tasks.append((sym, i, d))
    tasks.sort(key=lambda t: t[2])
    if len(tasks) > 400:
        warnings.append(f"Intraday candidate days clipped to the most recent 400 (of {len(tasks)}) to stay inside rate limits.")
        tasks = tasks[-400:]

    for ti, (sym, i, d) in enumerate(tasks):
        if progress_cb:
            progress_cb("fetching 1-minute bars", ti + 1, len(tasks))
        bars = per_symbol_bars[sym]
        prev_close = bars[i - 1]["close"]
        day_open = bars[i]["open"]
        minutes = _minute_day_fn(sym, d)
        if not minutes or len(minutes) < 30:
            continue
        n20 = min(20, i)
        avg_vol = sum(b.get("volume") or 0 for b in bars[i - n20:i]) / max(1, n20)
        avg_dvol = avg_vol * (prev_close or 1)
        if avg_dvol < liq_mult * budget:
            skipped_liq += 1
            continue

        entered = None
        was_beyond = False   # price traded below open (for cross-up) / above (for cross-dn)
        cum_vol = 0
        for m in range(len(minutes) - 2):
            bar = minutes[m]
            cum_vol += bar.get("volume") or 0
            px = bar["close"]
            if want_cross_up:
                if px < day_open:
                    was_beyond = True
                crossed = was_beyond and px > day_open
            elif want_cross_dn:
                if px > day_open:
                    was_beyond = True
                crossed = was_beyond and px < day_open
            else:
                crossed = True   # gap-only entry: enter once volume confirms
            if not crossed:
                continue
            if relvol:
                elapsed = (m + 1) / 390.0
                if cum_vol < relvol["mult"] * avg_vol * elapsed:
                    continue     # volume pace not met YET — keep watching
            entered = m + 1      # fill on the NEXT minute's open
            break
        if entered is None or entered >= len(minutes) - 1:
            continue

        fb = minutes[entered]
        spread = _est_spread_pct(fb["open"])
        entry_px = fb["open"] * (1 + (slip + spread) * (1 if direction == "long" else -1))
        qty = max(1, int(budget / max(0.01, entry_px)))
        trade = {"symbol": sym, "entry_date": d, "direction": direction,
                 "qty": qty, "mult": 1, "entry_px": round(entry_px, 4),
                 "cost_basis": entry_px * qty}
        stop_px = entry_px * (1 - stop / 100.0) if stop is not None else None
        tgt_px = entry_px * (1 + profit / 100.0) if profit is not None else None
        if direction == "short":
            stop_px = entry_px * (1 + stop / 100.0) if stop is not None else None
            tgt_px = entry_px * (1 - profit / 100.0) if profit is not None else None

        done = None
        for m in range(entered, len(minutes)):
            mb = minutes[m]
            lo, hi = mb["low"], mb["high"]
            if direction == "long":
                if stop_px is not None and lo <= stop_px:
                    done = _finish_trade(trade, stop_px * (1 - slip - spread), d, "stop", commission); break
                if tgt_px is not None and hi >= tgt_px:
                    done = _finish_trade(trade, tgt_px * (1 - slip - spread), d, "target", commission); break
            else:
                if stop_px is not None and hi >= stop_px:
                    done = _finish_trade(trade, stop_px * (1 + slip + spread), d, "stop", commission); break
                if tgt_px is not None and lo <= tgt_px:
                    done = _finish_trade(trade, tgt_px * (1 + slip + spread), d, "target", commission); break
            if m >= len(minutes) - 2:
                px = mb["close"] * (1 - slip - spread) if direction == "long" else mb["close"] * (1 + slip + spread)
                done = _finish_trade(trade, px, d, "close", commission); break
        if done:
            done["regime"] = spy_reg.get(d) or "unknown"
            trades.append(done)
    return trades, skipped_liq


def _metrics(trades, start_equity, warnings):
    trades = sorted(trades, key=lambda t: (t["exit_date"], t["entry_date"]))
    n = len(trades)
    if n == 0:
        return {"trades": [], "n_trades": 0, "warnings": warnings + [
            "No trades were generated — the conditions never lined up in the tested window, or every candidate was skipped for liquidity/positions."],
            "equity_curve": [], "metrics": {}}
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    eq = start_equity
    curve = []
    peak = eq
    max_dd = 0.0
    for t in trades:
        eq += t["pnl"]
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100.0)
        curve.append({"date": t["exit_date"], "equity": round(eq, 2)})
    win_rate = len(wins) / n * 100.0
    avg_gain = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    expectancy = (win_rate / 100.0) * avg_gain - (1 - win_rate / 100.0) * avg_loss
    by_regime = {}
    for t in trades:
        r = t.get("regime") or "unknown"
        d = by_regime.setdefault(r, {"n": 0, "pnl": 0.0, "wins": 0})
        d["n"] += 1
        d["pnl"] = round(d["pnl"] + t["pnl"], 2)
        d["wins"] += 1 if t["pnl"] > 0 else 0
    for r, d in by_regime.items():
        d["win_rate"] = round(d["wins"] / d["n"] * 100.0, 1)
    best = max(trades, key=lambda t: t["pnl"])
    worst = min(trades, key=lambda t: t["pnl"])
    return {
        "metrics": {
            "total_return_pct": round((eq - start_equity) / start_equity * 100.0, 2),
            "total_pnl": round(eq - start_equity, 2),
            "n_trades": n,
            "win_rate": round(win_rate, 1),
            "avg_gain": round(avg_gain, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "max_drawdown_pct": round(max_dd, 2),
            "expectancy": round(expectancy, 2),
            "start_equity": start_equity,
        },
        "n_trades": n,
        "best_trade": best,
        "worst_trade": worst,
        "by_regime": by_regime,
        "equity_curve": curve,
        "trades": trades[-400:],
        "warnings": warnings,
    }


# ════════════════════════════════════════════════════════════════════════════
# 3) JOBS
# ════════════════════════════════════════════════════════════════════════════

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()

def _last_path():
    return (_data_dir / "backtest_last.json") if _data_dir else None

def start_job(rules: dict) -> dict:
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "status": "running", "progress": {"phase": "starting", "done": 0, "total": 1},
           "started": datetime.now().isoformat(timespec="seconds")}
    with _JOBS_LOCK:
        # Bound memory: keep the 10 most recent jobs.
        for k in sorted(_JOBS, key=lambda k: _JOBS[k]["started"])[:-9]:
            _JOBS.pop(k, None)
        _JOBS[job_id] = job

    def _cb(phase, done, total):
        job["progress"] = {"phase": phase, "done": done, "total": total}

    def _run():
        try:
            res = run_backtest(rules, progress_cb=_cb)
            job["result"] = res
            job["status"] = "error" if res.get("error") else "done"
            p = _last_path()
            if p and not res.get("error"):
                try:
                    tmp = p.with_suffix(".tmp")
                    tmp.write_text(json.dumps(res, separators=(",", ":")))
                    tmp.replace(p)
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001
            job["status"] = "error"
            job["result"] = {"error": f"Backtest crashed: {exc}"}

    threading.Thread(target=_run, daemon=True, name=f"backtest-{job_id}").start()
    return {"job": job_id}

def job_status(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return {"error": "unknown job"}
    out = {"id": job["id"], "status": job["status"], "progress": job["progress"]}
    if job["status"] in ("done", "error"):
        out["result"] = job.get("result")
    return out

def last_result() -> dict:
    p = _last_path()
    if p and p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}
