# nl_engine.py — "Ask AI" natural-language layer (v4.00).
#
# One rule language, three consumers. This module puts an LLM (the user's
# OpenAI key) IN FRONT of the app's existing strategy rule schema — the same
# JSON rules the Backtest Lab's deterministic grammar (backtest.parse_strategy)
# has emitted since v3.43 — and adds a universal scanner that evaluates those
# same rules on the LATEST daily bar using the Lab engine's own condition
# checker (backtest._Ctx.check). Scan and backtest can never disagree about
# what a condition means because they run the identical code.
#
# Design rules (the contract with the rest of the app):
#   - The AI ONLY translates text → rules. It never invents numbers the app
#     then trusts blindly: everything passes through _validate_ai(), which
#     whitelists condition types and fields, clamps every numeric range, and
#     moves anything unrecognized into an "unsupported" list shown to the
#     user. The plain-English restate shown in the UI is rebuilt HERE from
#     the validated rules — not echoed from the model — so what the user
#     reads is what will actually run.
#   - No key / no network / API failure → backtest.parse_strategy fallback.
#     The feature degrades to the strict grammar, never to an error page.
#   - Nothing here places orders. Intents are: scan, backtest, alert, chart,
#     help. There is no "trade" execution path by design.
#   - Only the user's text, their watchlist tag names, and a symbol count are
#     sent to OpenAI. No positions, no journal, no account data.

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import backtest as _bt

# ── module wiring (options_dashboard.configure fills these) ─────────────────
_data_dir: Path | None = None
_universe_fn = lambda: {"starred": [], "all": []}
_tags_fn = lambda: {}                 # () -> {tag: [SYMBOLS]}
_bars_fn = lambda sym, days: []       # load_daily rows (date/open/high/low/close/volume)
_push_fn = None                       # (title, message) -> None
_et_tz = None                         # zoneinfo for US/Eastern (alert scheduling)


def configure(data_dir, universe_fn, tags_fn, bars_fn, push_fn=None, et_tz=None):
    global _data_dir, _universe_fn, _tags_fn, _bars_fn, _push_fn, _et_tz
    _data_dir = Path(data_dir) if data_dir else None
    _universe_fn = universe_fn
    _tags_fn = tags_fn
    _bars_fn = bars_fn
    _push_fn = push_fn
    _et_tz = et_tz


def _no_net() -> bool:
    return os.environ.get("JERRY_NO_NET") == "1"


def _api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


def _base_url() -> str:
    return (os.environ.get("OPENAI_BASE_URL", "").strip()
            or "https://api.openai.com/v1").rstrip("/")


# Model fallback chain: the configured model first, then progressively
# older/cheaper families so any paid OpenAI key works out of the box.
def _model_chain() -> list[str]:
    first = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-5-mini"
    chain = [first]
    for m in ("gpt-4.1-mini", "gpt-4o-mini"):
        if m not in chain:
            chain.append(m)
    return chain


def ai_available() -> bool:
    return bool(_api_key()) and not _no_net()


# ════════════════════════════════════════════════════════════════════════════
# 1) VOCABULARY — the schema the model must fill in (and the validator's law)
# ════════════════════════════════════════════════════════════════════════════
# Field spec: name -> (min, max) for numbers, tuple of choices for enums,
# None for free/handled specially. Everything not listed is stripped.

_COND_FIELDS: dict[str, dict] = {
    "gap_pct":            {"op": ("<=", ">="), "value": (-50.0, 50.0)},
    "day_change_pct":     {"op": ("<=", ">="), "value": (-95.0, 500.0)},
    "move_pct":           {"op": ("<=", ">="), "value": (-95.0, 1000.0), "days": (1, 252)},
    "rel_volume":         {"mult": (0.1, 50.0), "lookback": (2, 200)},
    "drawdown_from_high": {"pct": (1.0, 99.0), "lookback": (20, 756)},
    "rsi":                {"op": ("<=", ">="), "value": (1.0, 99.0), "period": (2, 50)},
    "sma_cross":          {"fast": (2, 100), "slow": (5, 400), "direction": ("up", "down")},
    "price_vs_sma":       {"op": ("<=", ">="), "period": (2, 400)},
    "new_high":           {"lookback": (2, 756)},
    "new_low":            {"lookback": (2, 756)},
    "consec_down":        {"n": (2, 15)},
    "consec_up":          {"n": (2, 15)},
    "price_abs":          {"op": ("<=", ">="), "value": (0.01, 100000.0)},
    "market_regime":      {"regime": ("uptrend", "downtrend", "chop")},
    "cross_above_open":   {},
    "cross_below_open":   {},
}
_COND_DEFAULTS: dict[str, dict] = {
    "move_pct": {"days": 5}, "rel_volume": {"mult": 2.0, "lookback": 20},
    "drawdown_from_high": {"lookback": 252}, "rsi": {"period": 14},
    "new_high": {"lookback": 20}, "new_low": {"lookback": 20},
    "consec_down": {"n": 3}, "consec_up": {"n": 3},
}
_EXIT_FIELDS: dict[str, dict] = {
    "profit_pct":        {"value": (0.1, 1000.0)},
    "stop_pct":          {"value": (0.1, 99.0)},
    "trailing_stop_pct": {"value": (0.5, 99.0)},
    "time_days":         {"value": (1, 252)},
    "same_day_close":    {},
    "hold_to_expiry":    {},
}
_STRUCTURES = ("short_put", "covered_call", "short_strangle", "iron_condor",
               "iron_fly", "put_credit_spread", "call_credit_spread", "wheel")
_INTENTS = ("scan", "backtest", "alert", "chart", "help")

# One-line semantics per condition — used verbatim in the model prompt AND
# as the tooltip source of truth. Keep in sync with backtest._Ctx.check.
_COND_DOC = {
    "gap_pct": "today's OPEN vs yesterday's close, in % (op/value; negative = gap down)",
    "day_change_pct": "close-to-close change today in % (op/value)",
    "move_pct": "total % move over the trailing `days` trading days (op/value)",
    "rel_volume": "today's volume >= `mult` x the `lookback`-day average volume",
    "drawdown_from_high": "price is at least `pct`% below its highest close of the last `lookback` trading days",
    "rsi": "RSI(`period`) compared to `value` (op)",
    "sma_cross": "the `fast`-day SMA crossed the `slow`-day SMA today, in `direction`",
    "price_vs_sma": "close is above (>=) or below (<=) the `period`-day SMA",
    "new_high": "close is a new `lookback`-day closing high",
    "new_low": "close is a new `lookback`-day closing low",
    "consec_down": "`n` consecutive down closes",
    "consec_up": "`n` consecutive up closes",
    "price_abs": "share price filter in dollars (op/value)",
    "market_regime": "SPY 50/200-SMA regime equals `regime`",
    "cross_above_open": "opened lower then crossed back ABOVE the opening price (intraday)",
    "cross_below_open": "crossed back BELOW the opening price (intraday)",
}


def _vocab_doc(tags: dict[str, list]) -> str:
    conds = "\n".join(
        f'  - type "{t}": fields {sorted(k for k in f)} — {_COND_DOC[t]}'
        for t, f in _COND_FIELDS.items())
    exits = ", ".join(f'"{t}"' for t in _EXIT_FIELDS)
    tag_lines = "\n".join(f'  - "{t}" ({len(s)} symbols)'
                          for t, s in sorted(tags.items())) or "  (none defined)"
    return f"""You translate a trader's plain-English request into JSON for a personal
stock/options dashboard. Output ONLY a JSON object — no prose, no markdown.

TOP-LEVEL SHAPE:
{{"intent": one of {list(_INTENTS)},
 "symbols": [TICKERS]  (chart intent, or when specific tickers are named),
 "rules": {{...see below...}},
 "assumptions": ["each default YOU chose for a fuzzy term, e.g. 'oversold -> RSI(14) <= 30'"],
 "unsupported": [{{"text": "the clause", "reason": "why it cannot run"}}]}}

INTENT: "backtest" when they want history tested ("backtest", "would have",
"test this", "how would X have done"). "alert" when they want to be notified
("alert me", "tell me when", "watch for", "notify"). "chart" when they just
want a symbol opened ("show me NVDA", "pull up the chart"). "help" when they
ask what this box can do. Otherwise, conditions to find stocks NOW = "scan".

RULES OBJECT (all keys optional except entry for scans):
{{"instrument": "stock" | "option",
 "direction": "long" | "short",
 "universe": {{"source": "starred" | "all" | "symbols" | "tag",
              "symbols": [...], "tag": "<exact tag name from the list below>"}},
 "entry": [condition objects],
 "exit": [exit objects],
 "sizing": {{"mode": "fixed_dollar"|"risk_pct"|"pct_equity", "value": n, "max_positions": n}},
 "period_days": n  (backtest lookback, 30..3650),
 "earnings_filter": {{"mode": "skip"|"only", "window": 5}},
 "options": {{"structure": one of {list(_STRUCTURES)},
             "target_delta": 0.30, "dte": 45, "wing_delta": 0.05,
             "management": {{"profit_take_pct": 50, "stop_x_credit": 2.0,
                            "exit_dte": 21, "roll_dte": 7, "hold_to_expiry": true}}}}
   or for single-leg longs: {{"right": "call"|"put", "dte": 30,
             "strike": {{"mode": "atm"|"otm_pct"|"itm_pct"|"delta", "value": n}}}}}}

ENTRY CONDITION TYPES (use ONLY these; never invent types or fields):
{conds}

EXIT TYPES: {exits} — each takes "value" where a number is meaningful.

THE USER'S WATCHLIST TAGS (universe "tag" must copy one EXACTLY):
{tag_lines}

HARD RULES:
- A condition the vocabulary cannot express (news, IV rank, analyst ratings,
  fundamentals, intraday times, option flow) goes in "unsupported" with a
  short reason. NEVER approximate it with a different condition silently.
- Keep every number the user gave. For fuzzy words use conventions —
  "oversold" RSI<=30, "overbought" RSI>=70, "beaten down" drawdown>=30%,
  "high volume" rel_volume 2x/20d, "uptrend" price above 200-day SMA — and
  list each choice in "assumptions".
- "my <word> stocks/names" refers to a watchlist tag when one matches
  (case-insensitive, partial ok — but output the exact tag string).
- Tickers are 1-5 uppercase letters. "on AAPL, MSFT" = universe symbols.
- Percentages: "down 3%" on the day = day_change_pct <= -3. "down 30% from
  highs" = drawdown_from_high pct 30. Gaps mention the open.
- Options: "30 delta" = target_delta 0.30. Selling premium implies a
  structure; buying calls/puts is single-leg with "right".
- If the request is only tickers/a company name, intent "chart".
- Do not include null/None fields. Do not add commentary keys."""


# Few-shot examples keep small models honest about the envelope shape.
_FEWSHOTS = [
    {"role": "user", "content": "my gold names down 3% today on double volume"},
    {"role": "assistant", "content": json.dumps({
        "intent": "scan",
        "rules": {"universe": {"source": "tag", "tag": "Gold-Metals"},
                  "entry": [{"type": "day_change_pct", "op": "<=", "value": -3},
                            {"type": "rel_volume", "mult": 2.0, "lookback": 20}]},
        "assumptions": ["'double volume' -> 2x the 20-day average"],
        "unsupported": []})},
    {"role": "user", "content": "backtest selling 30 delta puts 45 dte on my starred names, take profit 50%, exit 21 dte, skip earnings, last 3 years"},
    {"role": "assistant", "content": json.dumps({
        "intent": "backtest",
        "rules": {"instrument": "option", "universe": {"source": "starred"},
                  "options": {"structure": "short_put", "target_delta": 0.30,
                              "dte": 45, "management": {"profit_take_pct": 50,
                                                        "exit_dte": 21}},
                  "earnings_filter": {"mode": "skip", "window": 5},
                  "period_days": 1095},
        "assumptions": [], "unsupported": []})},
    {"role": "user", "content": "alert me when anything on my list makes a new 50 day low with rsi under 25"},
    {"role": "assistant", "content": json.dumps({
        "intent": "alert",
        "rules": {"universe": {"source": "all"},
                  "entry": [{"type": "new_low", "lookback": 50},
                            {"type": "rsi", "period": 14, "op": "<=", "value": 25}]},
        "assumptions": ["RSI period 14 (standard)"], "unsupported": []})},
]


# ════════════════════════════════════════════════════════════════════════════
# 2) OPENAI CLIENT — stdlib urllib; JSON mode; model-fallback; one repair try
# ════════════════════════════════════════════════════════════════════════════

_HTTP_TIMEOUT = 45          # per request
_TOTAL_BUDGET = 75          # seconds across the whole chain
_LAST_AI_ERROR: dict = {"msg": None, "at": None}


def _http_post_json(url: str, headers: dict, payload: dict, timeout: float):
    """POST JSON, return (status, parsed_body_or_None, raw_text). Split out
    so tests can stub the wire without a network."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(raw), raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw), raw
        except Exception:
            return e.code, None, raw
    # URLError/timeout propagate to the caller (network-level failure)


def _chat_payload(model: str, messages: list) -> dict:
    """Build a chat.completions payload that suits the model family.
    gpt-5*/o* reasoning models: no temperature, reasoning_effort low.
    Older families: temperature 0 for deterministic parses."""
    p = {"model": model, "messages": messages,
         "response_format": {"type": "json_object"},
         "max_completion_tokens": 4000}
    fam = model.lower()
    if fam.startswith(("gpt-5", "o1", "o3", "o4")):
        p["reasoning_effort"] = "low"
    else:
        p["temperature"] = 0
    return p


def _strip_param(payload: dict, err_text: str) -> bool:
    """OpenAI 400s name the offending parameter; drop it and retry once.
    Returns True when something was removed."""
    removed = False
    for k in ("reasoning_effort", "temperature", "response_format",
              "max_completion_tokens"):
        if k in payload and k in (err_text or ""):
            payload.pop(k, None)
            if k == "max_completion_tokens":
                payload["max_tokens"] = 4000
            removed = True
    return removed


def _openai_json(messages: list) -> tuple[dict | None, str | None, dict]:
    """Run the model chain until one returns parseable JSON.
    Returns (parsed_json | None, model_used | None, meta)."""
    key = _api_key()
    url = _base_url() + "/chat/completions"
    headers = {"Authorization": f"Bearer {key}"}
    deadline = time.monotonic() + _TOTAL_BUDGET
    meta: dict = {"tokens_in": 0, "tokens_out": 0, "attempts": 0}
    last_err = "no models attempted"
    for model in _model_chain():
        if time.monotonic() > deadline:
            break
        payload = _chat_payload(model, messages)
        for attempt in (1, 2):   # attempt 2 only after a stripped param / 429
            if time.monotonic() > deadline:
                break
            meta["attempts"] += 1
            try:
                status, body, raw = _http_post_json(url, headers, payload,
                                                    _HTTP_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 — network layer
                last_err = f"network: {exc}"
                break                 # network trouble: try next model? no — bail chain
            if status == 200 and body:
                usage = body.get("usage") or {}
                meta["tokens_in"] += usage.get("prompt_tokens") or 0
                meta["tokens_out"] += usage.get("completion_tokens") or 0
                try:
                    content = body["choices"][0]["message"]["content"]
                except Exception:
                    last_err = "malformed completion envelope"
                    break
                try:
                    return json.loads(content), model, meta
                except Exception:
                    m = re.search(r"\{.*\}", content or "", re.S)
                    if m:
                        try:
                            return json.loads(m.group(0)), model, meta
                        except Exception:
                            pass
                    last_err = "model returned non-JSON content"
                    break
            err_msg = ""
            if isinstance(body, dict):
                err_msg = ((body.get("error") or {}).get("message")) or raw[:200]
            else:
                err_msg = (raw or "")[:200]
            last_err = f"HTTP {status}: {err_msg}"
            if status in (401, 403):          # bad key — no model will fix it
                _LAST_AI_ERROR.update({"msg": last_err,
                                       "at": datetime.now().isoformat(timespec="seconds")})
                return None, None, meta
            if status == 404 or "model" in (err_msg or "").lower() and status == 400 \
                    and "not" in (err_msg or "").lower() and "param" not in (err_msg or "").lower():
                break                          # unknown model → next in chain
            if status == 400 and _strip_param(payload, err_msg):
                continue                       # retry same model minus the param
            if status == 429 or status >= 500:
                if attempt == 1:
                    time.sleep(2)
                    continue
            break
    _LAST_AI_ERROR.update({"msg": last_err,
                           "at": datetime.now().isoformat(timespec="seconds")})
    return None, None, meta


# ════════════════════════════════════════════════════════════════════════════
# 3) VALIDATOR — the law. Whitelist, clamp, normalize; nothing else survives.
# ════════════════════════════════════════════════════════════════════════════

def _num(v, lo, hi, as_int=False):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    x = max(lo, min(hi, x))
    return int(round(x)) if as_int else x


def _clean_cond(c: dict, unsupported: list) -> dict | None:
    t = c.get("type")
    spec = _COND_FIELDS.get(t)
    if spec is None:
        unsupported.append({"text": json.dumps({k: v for k, v in c.items() if k != "label"})[:120],
                            "reason": f"unknown condition type '{t}'"})
        return None
    out = {"type": t}
    for field, rng in spec.items():
        v = c.get(field, _COND_DEFAULTS.get(t, {}).get(field))
        if isinstance(rng, tuple) and rng and isinstance(rng[0], str):
            out[field] = v if v in rng else rng[0]
        elif isinstance(rng, tuple):
            as_int = field in ("days", "lookback", "period", "fast", "slow", "n")
            n = _num(v, rng[0], rng[1], as_int=as_int)
            if n is None:
                dflt = _COND_DEFAULTS.get(t, {}).get(field)
                if dflt is None:
                    unsupported.append({"text": json.dumps(c)[:120],
                                        "reason": f"'{t}' needs a numeric '{field}'"})
                    return None
                n = dflt
            out[field] = n
    if t == "sma_cross" and out.get("fast", 0) >= out.get("slow", 1):
        out["fast"], out["slow"] = min(out["fast"], out["slow"] - 1), out["slow"]
        if out["fast"] < 2:
            out["fast"], out["slow"] = 20, 50
    out["label"] = _bt._COND_LABELS.get(t, t)
    return out


def _clean_exit(x: dict, unsupported: list) -> dict | None:
    t = x.get("type")
    spec = _EXIT_FIELDS.get(t)
    if spec is None:
        unsupported.append({"text": json.dumps(x)[:120],
                            "reason": f"unknown exit type '{t}'"})
        return None
    out = {"type": t}
    for field, rng in spec.items():
        n = _num(x.get(field), rng[0], rng[1],
                 as_int=(t == "time_days"))
        if n is None:
            unsupported.append({"text": json.dumps(x)[:120],
                                "reason": f"'{t}' needs a numeric '{field}'"})
            return None
        out[field] = n
    return out


def _clean_universe(u, tags: dict[str, list], warnings: list) -> dict:
    u = u if isinstance(u, dict) else {}
    src = u.get("source")
    if src == "symbols":
        syms = [str(s).upper().strip() for s in (u.get("symbols") or [])]
        syms = [s for s in syms if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,5}", s)]
        if syms:
            return {"source": "symbols", "symbols": syms[:60]}
        warnings.append("No valid ticker symbols were found — using starred watchlist instead.")
        return {"source": "starred", "symbols": []}
    if src == "tag":
        want = str(u.get("tag") or "").strip().lower()
        for t in tags:
            if t.lower() == want:
                return {"source": "tag", "tag": t, "symbols": []}
        for t in tags:
            if want and (want in t.lower() or t.lower() in want):
                return {"source": "tag", "tag": t, "symbols": []}
        warnings.append(f"No watchlist tag matches '{u.get('tag')}' — using starred watchlist instead.")
        return {"source": "starred", "symbols": []}
    if src == "all":
        return {"source": "all", "symbols": []}
    return {"source": "starred", "symbols": []}


def _clean_options(o, unsupported: list) -> dict | None:
    if not isinstance(o, dict):
        return None
    out: dict = {}
    st = o.get("structure")
    if st:
        if st not in _STRUCTURES:
            unsupported.append({"text": str(st)[:60],
                                "reason": "unknown option structure"})
            return None
        out["structure"] = st
        td = o.get("target_delta")
        td = _num(td, 0.01, 99.0)
        if td is not None:
            out["target_delta"] = td / 100.0 if td > 1 else td
            out["target_delta"] = max(0.03, min(0.70, out["target_delta"]))
        else:
            out["target_delta"] = 0.30
        wd = _num(o.get("wing_delta"), 0.01, 99.0)
        if wd is not None:
            wd = wd / 100.0 if wd > 1 else wd
            out["wing_delta"] = max(0.01, min(0.40, wd))
        out["dte"] = _num(o.get("dte"), 1, 365, as_int=True) or 45
        mg_in = o.get("management") if isinstance(o.get("management"), dict) else {}
        mg: dict = {}
        pt = _num(mg_in.get("profit_take_pct"), 1, 99)
        if pt is not None:
            mg["profit_take_pct"] = pt
        sx = _num(mg_in.get("stop_x_credit"), 0.5, 10)
        if sx is not None:
            mg["stop_x_credit"] = sx
        xd = _num(mg_in.get("exit_dte"), 0, 60, as_int=True)
        if xd is not None:
            mg["exit_dte"] = xd
        rd = _num(mg_in.get("roll_dte"), 0, 60, as_int=True)
        if rd is not None:
            mg["roll_dte"] = rd
        if mg_in.get("hold_to_expiry") or not mg:
            mg["hold_to_expiry"] = True
        out["management"] = mg
        return out
    right = o.get("right")
    if right not in ("call", "put"):
        return None
    out["right"] = right
    out["dte"] = _num(o.get("dte"), 1, 365, as_int=True) or 30
    sk = o.get("strike") if isinstance(o.get("strike"), dict) else {}
    mode = sk.get("mode")
    if mode in ("otm_pct", "itm_pct", "delta"):
        v = _num(sk.get("value"), 0.5, 99)
        out["strike"] = {"mode": mode, "value": v if v is not None else 5.0}
    else:
        out["strike"] = {"mode": "atm"}
    return out


def _grammar_defaults() -> dict:
    """The same skeleton parse_strategy starts from, so AI-validated rules
    and grammar rules are interchangeable everywhere downstream."""
    return {
        "instrument": "stock", "direction": "long",
        "universe": {"source": "starred", "symbols": []},
        "entry": [], "exit": [],
        "sizing": {"mode": "fixed_dollar", "value": 10000, "max_positions": 5},
        "costs": {"commission": 0.0, "slippage_bps": 5, "spread_model": "auto",
                  "min_dollar_vol_mult": 20},
        "options": None, "period_days": 365,
    }


def _validate_ai(out: dict, tags: dict[str, list]) -> dict:
    """Normalize a raw model response into the app's rule contract."""
    warnings: list[str] = []
    unsupported: list[dict] = []
    for u in (out.get("unsupported") or [])[:12]:
        if isinstance(u, dict) and u.get("text"):
            unsupported.append({"text": str(u["text"])[:160],
                                "reason": str(u.get("reason") or "not supported")[:160]})
        elif isinstance(u, str):
            unsupported.append({"text": u[:160], "reason": "not supported"})
    assumptions = [str(a)[:160] for a in (out.get("assumptions") or [])[:10]
                   if isinstance(a, str) and a.strip()]

    intent = out.get("intent")
    if intent not in _INTENTS:
        intent = "scan"
    symbols = [str(s).upper().strip() for s in (out.get("symbols") or [])
               if isinstance(s, (str,))]
    symbols = [s for s in symbols if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,5}", s)][:20]

    r_in = out.get("rules") if isinstance(out.get("rules"), dict) else {}
    rules = _grammar_defaults()
    if r_in.get("direction") in ("long", "short"):
        rules["direction"] = r_in["direction"]
    rules["universe"] = _clean_universe(r_in.get("universe"), tags, warnings)
    if symbols and rules["universe"]["source"] == "starred" and intent != "chart":
        rules["universe"] = {"source": "symbols", "symbols": symbols}
    for c in (r_in.get("entry") or [])[:12]:
        if isinstance(c, dict):
            cc = _clean_cond(c, unsupported)
            if cc and cc not in rules["entry"]:
                rules["entry"].append(cc)
    for x in (r_in.get("exit") or [])[:6]:
        if isinstance(x, dict):
            xx = _clean_exit(x, unsupported)
            if xx and xx not in rules["exit"]:
                rules["exit"].append(xx)
    s_in = r_in.get("sizing") if isinstance(r_in.get("sizing"), dict) else {}
    if s_in.get("mode") in ("fixed_dollar", "risk_pct", "pct_equity"):
        v = _num(s_in.get("value"),
                 100 if s_in["mode"] == "fixed_dollar" else 0.1,
                 10_000_000 if s_in["mode"] == "fixed_dollar" else 100)
        if v is not None:
            rules["sizing"]["mode"] = s_in["mode"]
            rules["sizing"]["value"] = v
    mp = _num(s_in.get("max_positions"), 1, 100, as_int=True)
    if mp is not None:
        rules["sizing"]["max_positions"] = mp
    pd = _num(r_in.get("period_days"), 30, 3650, as_int=True)
    if pd is not None:
        rules["period_days"] = pd
    ef = r_in.get("earnings_filter")
    if isinstance(ef, dict) and ef.get("mode") in ("skip", "only"):
        rules["earnings_filter"] = {"mode": ef["mode"],
                                    "window": _num(ef.get("window"), 1, 10, as_int=True) or 5}
    opts = _clean_options(r_in.get("options"), unsupported)
    if opts:
        rules["instrument"] = "option"
        rules["options"] = opts
        if opts.get("structure"):
            warnings.append(
                "Option prices are MODELED (Black-Scholes on realized volatility), "
                "not historical quotes. Treat option results as estimates.")

    if intent in ("scan", "alert") and not rules["entry"]:
        warnings.append("No scan conditions were recognized — add a condition and try again.")
    if intent == "backtest" and not rules["entry"] and not (opts or {}).get("structure"):
        warnings.append("No entry condition was recognized — the Backtest Lab will let you add one before running.")
    if intent == "backtest" and not (opts or {}).get("structure") and not any(
            x["type"] in _EXIT_FIELDS for x in rules["exit"]):
        rules["exit"].append({"type": "time_days", "value": 10})
        warnings.append("No exit was recognized — a default 10-trading-day time exit was added; edit it in the Lab.")

    return {"intent": intent, "symbols": symbols, "rules": rules,
            "warnings": warnings, "unsupported": unsupported,
            "assumptions": assumptions}


# ════════════════════════════════════════════════════════════════════════════
# 4) RESTATE — deterministic English rebuilt from the VALIDATED rules
# ════════════════════════════════════════════════════════════════════════════

def _fmt_cond(c: dict) -> str:
    t = c["type"]
    op = {"<=": "≤", ">=": "≥"}.get(c.get("op"), "")
    if t == "gap_pct":
        return f"gaps {op} {c['value']:+g}% at the open"
    if t == "day_change_pct":
        return f"changes {op} {c['value']:+g}% on the day"
    if t == "move_pct":
        return f"moved {op} {c['value']:+g}% over {c['days']} days"
    if t == "rel_volume":
        return f"volume ≥ {c['mult']:g}× the {c['lookback']}-day average"
    if t == "drawdown_from_high":
        return f"≥ {c['pct']:g}% below its {c['lookback']}-day high"
    if t == "rsi":
        return f"RSI({c['period']}) {op} {c['value']:g}"
    if t == "sma_cross":
        return f"{c['fast']}-day SMA crossed {'above' if c['direction'] == 'up' else 'below'} the {c['slow']}-day"
    if t == "price_vs_sma":
        return f"price {'above' if c.get('op') == '>=' else 'below'} the {c['period']}-day SMA"
    if t == "new_high":
        return f"new {c['lookback']}-day closing high"
    if t == "new_low":
        return f"new {c['lookback']}-day closing low"
    if t == "consec_down":
        return f"{c['n']} straight down days"
    if t == "consec_up":
        return f"{c['n']} straight up days"
    if t == "price_abs":
        return f"price {op} ${c['value']:g}"
    if t == "market_regime":
        return f"SPY in a {c['regime']}"
    if t == "cross_above_open":
        return "crossed back above the open (evaluated on the latest bar: close above today's open)"
    if t == "cross_below_open":
        return "crossed back below the open (latest bar: close below today's open)"
    return t


def _fmt_exit(x: dict) -> str:
    t = x["type"]
    return {"profit_pct": f"+{x.get('value', 0):g}% target",
            "stop_pct": f"-{x.get('value', 0):g}% stop",
            "trailing_stop_pct": f"{x.get('value', 0):g}% trailing stop",
            "time_days": f"exit after {x.get('value', 0):g} days",
            "same_day_close": "exit by the close",
            "hold_to_expiry": "hold to expiry"}.get(t, t)


def _universe_desc(uni: dict, tags: dict[str, list]) -> str:
    src = uni.get("source")
    if src == "symbols":
        return ", ".join(uni.get("symbols") or []) or "named symbols"
    if src == "tag":
        t = uni.get("tag") or "?"
        n = len(tags.get(t) or [])
        return f"tag “{t}” ({n} symbols)"
    if src == "all":
        return "entire watchlist"
    return "starred watchlist"


def restate(parsed: dict, tags: dict[str, list]) -> str:
    rules = parsed.get("rules") or {}
    intent = parsed.get("intent")
    bits = []
    verb = {"scan": "Scan", "alert": "Alert on", "backtest": "Backtest",
            "chart": "Open chart:", "help": "Help"}.get(intent, "Scan")
    if intent == "chart":
        return f"Open {', '.join(parsed.get('symbols') or []) or 'chart'}"
    if intent == "help":
        return "What can I ask?"
    bits.append(f"{verb} {_universe_desc(rules.get('universe') or {}, tags)}")
    opts = rules.get("options") or {}
    if opts.get("structure"):
        st = opts["structure"].replace("_", " ")
        mg = opts.get("management") or {}
        mtxt = []
        if mg.get("profit_take_pct"):
            mtxt.append(f"TP {mg['profit_take_pct']:g}%")
        if mg.get("stop_x_credit"):
            mtxt.append(f"stop {mg['stop_x_credit']:g}× credit")
        if mg.get("exit_dte") is not None:
            mtxt.append(f"exit {mg['exit_dte']}dte")
        if mg.get("roll_dte") is not None:
            mtxt.append(f"roll {mg['roll_dte']}dte")
        bits.append(f"{st} @ {opts.get('target_delta', 0.3) * 100:.0f}Δ {opts.get('dte', 45)}dte"
                    + (f" ({', '.join(mtxt)})" if mtxt else ""))
    elif rules.get("instrument") == "option" and opts.get("right"):
        sk = opts.get("strike") or {}
        sdesc = {"atm": "ATM", "otm_pct": f"{sk.get('value', 0):g}% OTM",
                 "itm_pct": f"{sk.get('value', 0):g}% ITM",
                 "delta": f"{sk.get('value', 0):g}Δ"}.get(sk.get("mode"), "ATM")
        bits.append(f"long {opts['right']}s {sdesc} {opts.get('dte', 30)}dte")
    conds = [_fmt_cond(c) for c in rules.get("entry") or []]
    if conds:
        bits.append("where " + "; ".join(conds))
    if rules.get("earnings_filter"):
        bits.append({"skip": "skipping earnings week",
                     "only": "earnings week only"}[rules["earnings_filter"]["mode"]])
    if intent == "backtest":
        ex = [_fmt_exit(x) for x in rules.get("exit") or []]
        if ex:
            bits.append("exits: " + ", ".join(ex))
        bits.append(f"over ~{max(1, round((rules.get('period_days') or 365) / 365.0)):g}y")
    return " — ".join(bits)


# ════════════════════════════════════════════════════════════════════════════
# 5) TRANSLATE — cache → AI (with repair) → grammar fallback
# ════════════════════════════════════════════════════════════════════════════

_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 300
_RL_LOCK = threading.Lock()
_RL_STAMPS: list[float] = []
_RL_PER_MIN = 8


def _cache_path() -> Path | None:
    return (_data_dir / "nl_cache.json") if _data_dir else None


def _cache_load() -> dict:
    p = _cache_path()
    if not p or not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _cache_store(key: str, result: dict) -> None:
    p = _cache_path()
    if not p:
        return
    with _CACHE_LOCK:
        data = _cache_load()
        data[key] = {"t": time.time(), "result": result}
        if len(data) > _CACHE_MAX:
            for k in sorted(data, key=lambda k: data[k]["t"])[:len(data) - _CACHE_MAX]:
                data.pop(k, None)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(p)


def _rate_limited() -> bool:
    now = time.time()
    with _RL_LOCK:
        while _RL_STAMPS and now - _RL_STAMPS[0] > 60:
            _RL_STAMPS.pop(0)
        if len(_RL_STAMPS) >= _RL_PER_MIN:
            return True
        _RL_STAMPS.append(now)
        return False


def _grammar_result(text: str, tags: dict[str, list], note: str | None = None) -> dict:
    g = _bt.parse_strategy(text)
    parsed = {"intent": "backtest", "symbols": [],
              "rules": g["rules"], "warnings": list(g["warnings"]),
              "unsupported": [{"text": u, "reason": "the strict parser could not read this"}
                              for u in g["unparsed"]],
              "assumptions": []}
    low = text.lower()
    if re.search(r"\balert|notify|tell me when|watch for|let me know\b", low):
        parsed["intent"] = "alert"
    elif not re.search(r"\bbacktest|test|would have|history|historical\b", low):
        parsed["intent"] = "scan"
    if note:
        parsed["warnings"].insert(0, note)
    parsed["restate"] = restate(parsed, tags)
    parsed["source"] = "grammar"
    parsed["model"] = None
    return parsed


def translate(text: str, base_rules: dict | None = None) -> dict:
    """The whole front door. Never raises; always returns a usable result."""
    t0 = time.time()
    text = (text or "").strip()[:2000]
    tags = {}
    try:
        tags = _tags_fn() or {}
    except Exception:
        pass
    if not text:
        return {"error": "Type what you want to find, test, or watch."}
    if not ai_available():
        note = ("AI translation is off (JERRY_NO_NET)." if _no_net() and _api_key()
                else "AI translation is off — add OPENAI_API_KEY to enable plain-English mode. Strict parser used.")
        out = _grammar_result(text, tags, note)
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
        return out

    key = hashlib.sha256(json.dumps(
        [text.lower(), base_rules or {}, sorted(tags)],
        sort_keys=True).encode()).hexdigest()[:32]
    hit = _cache_load().get(key)
    if hit and isinstance(hit.get("result"), dict):
        out = dict(hit["result"])
        out["source"] = "cache"
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
        return out

    if _rate_limited():
        out = _grammar_result(text, tags,
                              "Slow down a moment — too many AI requests this minute. Strict parser used.")
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
        return out

    messages = [{"role": "system", "content": _vocab_doc(tags)}]
    messages += _FEWSHOTS
    if base_rules:
        messages.append({"role": "user", "content":
                         "CURRENT RULES (apply my modification below, keep everything else "
                         "unchanged, return the COMPLETE updated JSON):\n"
                         + json.dumps(base_rules)[:4000] + "\n\nMODIFICATION: " + text})
    else:
        messages.append({"role": "user", "content": text})

    raw, model, meta = _openai_json(messages)
    if raw is None:
        out = _grammar_result(
            text, tags,
            f"AI translation failed ({_LAST_AI_ERROR.get('msg') or 'unknown'}) — strict parser used instead.")
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
        return out

    parsed = _validate_ai(raw if isinstance(raw, dict) else {}, tags)
    parsed["restate"] = restate(parsed, tags)
    parsed["source"] = "ai"
    parsed["model"] = model
    parsed["tokens"] = {"in": meta.get("tokens_in", 0), "out": meta.get("tokens_out", 0)}
    _cache_store(key, parsed)
    out = dict(parsed)
    out["elapsed_ms"] = int((time.time() - t0) * 1000)
    return out


# ════════════════════════════════════════════════════════════════════════════
# 6) UNIVERSAL SCANNER — the Lab's own condition checker on the latest bar
# ════════════════════════════════════════════════════════════════════════════

_SCAN_CAP = 250          # hard universe cap per scan
_MIN_BARS = 30


def resolve_universe(uni: dict) -> tuple[list[str], str]:
    uni = uni or {}
    tags = {}
    try:
        tags = _tags_fn() or {}
    except Exception:
        pass
    src = uni.get("source")
    if src == "symbols" and uni.get("symbols"):
        syms = [s.upper() for s in uni["symbols"]]
    elif src == "tag" and uni.get("tag"):
        syms = list(tags.get(uni["tag"]) or [])
    elif src == "all":
        u = _universe_fn() or {}
        syms = list(u.get("all") or [])
    else:
        u = _universe_fn() or {}
        syms = list(u.get("starred") or []) or list(u.get("all") or [])[:60]
    seen, out = set(), []
    for s in syms:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out[:_SCAN_CAP], _universe_desc(uni, tags)


def _rows_to_bars(rows: list[dict]) -> list[dict]:
    bars = []
    for r in rows or []:
        try:
            bars.append({"date": str(r.get("date") or "")[:10],
                         "open": float(r["open"]), "high": float(r.get("high") or r["close"]),
                         "low": float(r.get("low") or r["close"]),
                         "close": float(r["close"]),
                         "volume": float(r.get("volume") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    return bars


def _cond_value(ctx: "_bt._Ctx", cond: dict, i: int) -> str | None:
    """The number behind the ✓/✗ — shown next to each condition so the user
    can sanity-check every match. None when it can't be computed."""
    t = cond["type"]
    closes = ctx.closes
    try:
        if t == "gap_pct" and i > 0 and closes[i - 1]:
            return f"{(ctx.bars[i]['open'] - closes[i - 1]) / closes[i - 1] * 100:+.1f}%"
        if t == "day_change_pct" and i > 0 and closes[i - 1]:
            return f"{(closes[i] - closes[i - 1]) / closes[i - 1] * 100:+.1f}%"
        if t == "move_pct":
            n = int(cond.get("days") or 5)
            if i >= n and closes[i - n]:
                return f"{(closes[i] - closes[i - n]) / closes[i - n] * 100:+.1f}%"
        if t == "rel_volume":
            n = int(cond.get("lookback") or 20)
            if i >= n:
                avg = sum(ctx.vols[i - n:i]) / n
                if avg > 0:
                    return f"{ctx.vols[i] / avg:.1f}×"
        if t == "drawdown_from_high":
            n = min(int(cond.get("lookback") or 252), i)
            if n >= 5:
                hi = max(closes[i - n:i + 1])
                if hi > 0:
                    return f"-{(hi - closes[i]) / hi * 100:.1f}%"
        if t == "rsi":
            r = _bt._rsi(closes, i, int(cond.get("period") or 14))
            if r is not None:
                return f"{r:.0f}"
        if t == "price_vs_sma":
            s = _bt._sma(closes, i, int(cond["period"]))
            if s:
                return f"{(closes[i] / s - 1) * 100:+.1f}% vs SMA"
        if t in ("new_high", "new_low"):
            return f"${closes[i]:.2f}"
        if t in ("consec_down", "consec_up"):
            run = 0
            for k in range(i, 0, -1):
                d = closes[k] - closes[k - 1]
                if (t == "consec_down" and d < 0) or (t == "consec_up" and d > 0):
                    run += 1
                else:
                    break
            return f"{run} days"
        if t == "price_abs":
            return f"${closes[i]:.2f}"
        if t == "market_regime":
            return ctx.spy.get(ctx.bars[i]["date"][:10]) or None
        if t in ("cross_above_open", "cross_below_open"):
            o = ctx.bars[i]["open"]
            if o:
                return f"{(closes[i] / o - 1) * 100:+.1f}% vs open"
    except Exception:
        return None
    return None


class _ScanCtx(_bt._Ctx):
    """Lab context + a live-bar reading for the two intraday cross types,
    which the Lab handles with minute data the scanner doesn't need: on the
    LATEST bar, 'crossed above the open' ≈ opened at/below yesterday's close
    territory and now trades above today's open. Labeled as approximate."""

    def check(self, cond, i):
        t = cond.get("type")
        if t == "cross_above_open":
            b = self.bars[i]
            return b["close"] > b["open"] and b["low"] < b["open"]
        if t == "cross_below_open":
            b = self.bars[i]
            return b["close"] < b["open"] and b["high"] > b["open"]
        return super().check(cond, i)


def run_scan(rules: dict, bars_cache: dict | None = None,
             progress_cb=None) -> dict:
    """Evaluate entry conditions on the latest bar for every universe symbol.
    Synchronous — the job layer threads it. Returns the board payload."""
    entry = [c for c in (rules.get("entry") or []) if isinstance(c, dict)]
    if not entry:
        return {"error": "No conditions to scan for — describe at least one."}
    symbols, uni_desc = resolve_universe(rules.get("universe") or {})
    if not symbols:
        return {"error": "No symbols in that universe — star names or import tags on the Manage tab."}
    intraday_note = any(c.get("type") in ("cross_above_open", "cross_below_open")
                        for c in entry)

    spy_regimes = {}
    if any(c.get("type") == "market_regime" for c in entry):
        try:
            spy_bars = _rows_to_bars(_bars_fn("SPY", 460))
            spy_regimes = _bt._spy_regimes(spy_bars) if spy_bars else {}
        except Exception:
            spy_regimes = {}

    matches, near, errors = [], [], 0
    for k, sym in enumerate(symbols):
        if progress_cb:
            progress_cb(k, len(symbols), sym)
        try:
            if bars_cache is not None and sym in bars_cache:
                bars = bars_cache[sym]
            else:
                bars = _rows_to_bars(_bars_fn(sym, 420))
                if bars_cache is not None:
                    bars_cache[sym] = bars
        except Exception:
            bars = []
        if len(bars) < _MIN_BARS:
            errors += 1
            continue
        ctx = _ScanCtx(bars, spy_regimes)
        i = len(bars) - 1
        checks = []
        n_true = n_false = n_na = 0
        for c in entry:
            ok = None
            try:
                ok = ctx.check(c, i)
            except Exception:
                ok = None
            if ok is True:
                n_true += 1
            elif ok is False:
                n_false += 1
            else:
                n_na += 1
            checks.append({"label": _fmt_cond(c), "ok": ok,
                           "value": _cond_value(ctx, c, i)})
        prev = ctx.closes[i - 1] if i > 0 else None
        row = {"symbol": sym, "price": round(ctx.closes[i], 2),
               "chg_pct": (round((ctx.closes[i] - prev) / prev * 100, 2)
                           if prev else None),
               "date": bars[i]["date"], "checks": checks}
        if n_true == len(entry):
            matches.append(row)
        elif n_false == 1 and n_na == 0 and len(entry) > 1:
            row["missed"] = next(c["label"] for c in checks if c["ok"] is False)
            near.append(row)
    matches.sort(key=lambda r: (r["chg_pct"] if r["chg_pct"] is not None else 0))
    near.sort(key=lambda r: (r["chg_pct"] if r["chg_pct"] is not None else 0))
    out = {"as_of": datetime.now().isoformat(timespec="seconds"),
           "universe": uni_desc, "n_scanned": len(symbols) - errors,
           "n_universe": len(symbols), "n_no_data": errors,
           "conditions": [_fmt_cond(c) for c in entry],
           "matches": matches, "near_misses": near[:25]}
    if intraday_note:
        out["note"] = ("Open-cross conditions are approximated on daily bars "
                       "(close vs today's open) — the backtest engine uses minute data.")
    return out


# ── scan job (board pattern: trigger → poll status → read board) ────────────

_SCAN_LOCK = threading.Lock()
_SCAN_STATE: dict = {"scanning": False, "done": 0, "total": 0, "symbol": None,
                     "strategy_id": None, "started_at": None, "finished_at": None,
                     "error": None}
_BOARD_LOCK = threading.Lock()


def _board_path() -> Path | None:
    return (_data_dir / "nl_board.json") if _data_dir else None


def _board_load() -> dict:
    p = _board_path()
    if not p or not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _board_store(sid: str, payload: dict) -> None:
    p = _board_path()
    if not p:
        return
    with _BOARD_LOCK:
        data = _board_load()
        data[sid] = payload
        keep = sorted(data, key=lambda k: data[k].get("as_of") or "", reverse=True)[:40]
        data = {k: data[k] for k in keep}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(p)


def get_board(strategy_id: str | None = None) -> dict:
    data = _board_load()
    sid = strategy_id or "adhoc"
    board = data.get(sid) or {}
    with _SCAN_LOCK:
        status = {k: _SCAN_STATE[k] for k in
                  ("scanning", "done", "total", "symbol", "strategy_id",
                   "started_at", "finished_at", "error")}
    return {"board": board, "status": status}


def start_scan(rules: dict, strategy_id: str | None = None) -> dict:
    if _no_net():
        return {"started": False, "reason": "offline (JERRY_NO_NET) — scanning needs market data"}
    with _SCAN_LOCK:
        if _SCAN_STATE["scanning"]:
            return {"started": False, "reason": "a scan is already running"}
        _SCAN_STATE.update({"scanning": True, "done": 0, "total": 0,
                            "symbol": None, "strategy_id": strategy_id or "adhoc",
                            "started_at": datetime.now().isoformat(timespec="seconds"),
                            "finished_at": None, "error": None})

    def work():
        err = None
        try:
            def prog(k, n, sym):
                with _SCAN_LOCK:
                    _SCAN_STATE.update({"done": k, "total": n, "symbol": sym})
            result = run_scan(rules, bars_cache={}, progress_cb=prog)
            if result.get("error"):
                err = result["error"]
            else:
                result["rules"] = rules
                _board_store(strategy_id or "adhoc", result)
                if strategy_id:
                    _strategy_record_run(strategy_id, result)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)[:300]
        finally:
            with _SCAN_LOCK:
                _SCAN_STATE.update({"scanning": False, "error": err,
                                    "finished_at": datetime.now().isoformat(timespec="seconds")})

    threading.Thread(target=work, name="nl-scan", daemon=True).start()
    return {"started": True}


# ════════════════════════════════════════════════════════════════════════════
# 7) SAVED STRATEGIES + ALERTS
# ════════════════════════════════════════════════════════════════════════════

_STRAT_LOCK = threading.Lock()
_STRAT_MAX = 100
_ALERT_DEDUPE_DAYS = 5


def _strat_path() -> Path | None:
    return (_data_dir / "nl_strategies.json") if _data_dir else None


def _strat_load() -> dict:
    p = _strat_path()
    if not p or not p.exists():
        return {"version": 1, "items": []}
    try:
        d = json.loads(p.read_text())
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            return d
    except Exception:
        pass
    return {"version": 1, "items": []}


def _strat_save(data: dict) -> None:
    p = _strat_path()
    if not p:
        return
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(p)


def list_strategies() -> dict:
    with _STRAT_LOCK:
        d = _strat_load()
    items = []
    for it in d["items"]:
        lite = dict(it)
        lr = lite.get("last_run") or {}
        lite["last_run"] = {"at": lr.get("at"), "n_matches": lr.get("n_matches"),
                            "matched": (lr.get("matched") or [])[:12]}
        items.append(lite)
    return {"items": items}


def save_strategy(name: str, text: str, rules: dict, intent: str,
                  restated: str = "", alert: bool = False) -> dict:
    name = (name or "").strip()[:80] or (restated or text or "Strategy")[:60]
    sid = hashlib.sha256(f"{name}|{time.time()}".encode()).hexdigest()[:12]
    item = {"id": sid, "name": name, "text": (text or "")[:2000],
            "rules": rules, "intent": intent if intent in _INTENTS else "scan",
            "restate": (restated or "")[:400],
            "created": datetime.now().isoformat(timespec="seconds"),
            "alert": {"enabled": bool(alert), "last_checked": None,
                      "last_matches": [], "last_pushed": {}},
            "last_run": None}
    with _STRAT_LOCK:
        d = _strat_load()
        if len(d["items"]) >= _STRAT_MAX:
            return {"error": f"Strategy library is full ({_STRAT_MAX}) — delete one first."}
        d["items"].insert(0, item)
        _strat_save(d)
    return {"ok": True, "id": sid, "item": item}


def update_strategy(sid: str, *, name: str | None = None,
                    alert_enabled: bool | None = None) -> dict:
    with _STRAT_LOCK:
        d = _strat_load()
        for it in d["items"]:
            if it["id"] == sid:
                if name is not None and name.strip():
                    it["name"] = name.strip()[:80]
                if alert_enabled is not None:
                    it.setdefault("alert", {"enabled": False, "last_checked": None,
                                            "last_matches": [], "last_pushed": {}})
                    it["alert"]["enabled"] = bool(alert_enabled)
                _strat_save(d)
                return {"ok": True, "item": it}
    return {"error": "strategy not found"}


def delete_strategy(sid: str) -> dict:
    with _STRAT_LOCK:
        d = _strat_load()
        n0 = len(d["items"])
        d["items"] = [it for it in d["items"] if it["id"] != sid]
        if len(d["items"]) < n0:
            _strat_save(d)
            return {"ok": True}
    return {"error": "strategy not found"}


def get_strategy(sid: str) -> dict | None:
    with _STRAT_LOCK:
        d = _strat_load()
    for it in d["items"]:
        if it["id"] == sid:
            return it
    return None


def _strategy_record_run(sid: str, result: dict) -> None:
    matched = [m["symbol"] for m in result.get("matches") or []]
    with _STRAT_LOCK:
        d = _strat_load()
        for it in d["items"]:
            if it["id"] == sid:
                it["last_run"] = {"at": result.get("as_of"),
                                  "n_matches": len(matched),
                                  "matched": matched[:60]}
                _strat_save(d)
                return


# ── alerts: diff new matches, push once, dedupe per symbol ──────────────────

def check_alerts(force: bool = False, bars_cache: dict | None = None) -> dict:
    """Run every alert-enabled strategy; push NEW matches. One shared bars
    cache across strategies so N alerts on the same universe fetch once."""
    if _no_net():
        return {"checked": 0, "reason": "offline"}
    with _STRAT_LOCK:
        d = _strat_load()
    enabled = [it for it in d["items"] if (it.get("alert") or {}).get("enabled")]
    if not enabled:
        return {"checked": 0, "pushed": 0}
    cache = bars_cache if bars_cache is not None else {}
    now = datetime.now()
    checked = pushed = 0
    for it in enabled:
        rules = it.get("rules") or {}
        if not rules.get("entry"):
            continue
        result = run_scan(rules, bars_cache=cache)
        if result.get("error"):
            continue
        checked += 1
        matched = [m["symbol"] for m in result.get("matches") or []]
        al = it.setdefault("alert", {})
        prev = set(al.get("last_matches") or [])
        last_pushed = al.get("last_pushed") or {}
        fresh = []
        for sym in matched:
            if sym in prev:
                continue
            lp = last_pushed.get(sym)
            if lp:
                try:
                    if now - datetime.fromisoformat(lp) < timedelta(days=_ALERT_DEDUPE_DAYS):
                        continue
                except ValueError:
                    pass
            fresh.append(sym)
        if fresh and _push_fn:
            detail = []
            for m in result["matches"]:
                if m["symbol"] in fresh[:6]:
                    vals = ", ".join(f"{c['label']} {c.get('value') or '✓'}"
                                     for c in m["checks"] if c["ok"])
                    detail.append(f"{m['symbol']} ${m['price']:g} ({vals})"[:120])
            try:
                _push_fn(f"Scanner match: {it['name'][:40]}",
                         "\n".join(detail) or ", ".join(fresh))
                pushed += len(fresh)
                for sym in fresh:
                    last_pushed[sym] = now.isoformat(timespec="seconds")
            except Exception:
                pass
        al.update({"last_checked": now.isoformat(timespec="seconds"),
                   "last_matches": matched[:200], "last_pushed": last_pushed})
        _strategy_record_run(it["id"], result)
        with _STRAT_LOCK:
            d2 = _strat_load()
            for jt in d2["items"]:
                if jt["id"] == it["id"]:
                    jt["alert"] = al
                    _strat_save(d2)
                    break
    return {"checked": checked, "pushed": pushed}


# ── daily scheduler: one alert pass after the close, only when needed ───────

_SCHED_STATE = {"last_run_date": None, "running": False}


def start_scheduler() -> None:
    """Daemon loop: after ~4:45pm ET on weekdays, run one alert pass for the
    day. Skips instantly (no data fetched) when no alerts are enabled."""
    if _no_net():
        return

    def loop():
        while True:
            time.sleep(1200)   # every 20 minutes
            try:
                now_et = datetime.now(_et_tz) if _et_tz else datetime.now()
                if now_et.weekday() >= 5:
                    continue
                if not (now_et.hour > 16 or (now_et.hour == 16 and now_et.minute >= 45)):
                    continue
                today = now_et.strftime("%Y-%m-%d")
                if _SCHED_STATE["last_run_date"] == today or _SCHED_STATE["running"]:
                    continue
                _SCHED_STATE["running"] = True
                try:
                    check_alerts()
                    _SCHED_STATE["last_run_date"] = today
                finally:
                    _SCHED_STATE["running"] = False
            except Exception:
                _SCHED_STATE["running"] = False

    threading.Thread(target=loop, name="nl-alerts", daemon=True).start()


# ════════════════════════════════════════════════════════════════════════════
# 8) STATUS
# ════════════════════════════════════════════════════════════════════════════

def status() -> dict:
    tags = {}
    try:
        tags = _tags_fn() or {}
    except Exception:
        pass
    uni = {}
    try:
        uni = _universe_fn() or {}
    except Exception:
        pass
    with _STRAT_LOCK:
        d = _strat_load()
    n_alerts = sum(1 for it in d["items"] if (it.get("alert") or {}).get("enabled"))
    return {"ai": ai_available(),
            "model": (os.environ.get("OPENAI_MODEL", "").strip() or "gpt-5-mini")
                     if ai_available() else None,
            "key_set": bool(_api_key()),
            "last_ai_error": _LAST_AI_ERROR.get("msg"),
            "tags": sorted(tags.keys()),
            "starred_count": len(uni.get("starred") or []),
            "watchlist_count": len(uni.get("all") or []),
            "strategies_count": len(d["items"]),
            "alerts_enabled": n_alerts,
            "alerts_last_run": _SCHED_STATE.get("last_run_date")}
