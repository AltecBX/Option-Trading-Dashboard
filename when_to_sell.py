"""
when_to_sell.py — the "When to sell" card on the ticker page (v5.37).

`trigger_sell.decide` answers WHEN to sell this week's call: wait for the
trigger, sell now, sell the tap, or skip. This module feeds it what the
ticker page already has (a year of daily bars, the live price, the chain,
the earnings dates) plus two things kept on disk:

  data/conditional_triggers.json   Jerry's regime table (last week's move
                                   picks the trigger). Read from the repo,
                                   re-read when the file changes.
  <data_dir>/trigger_sell/history.json
                                   one `history_record` per ticker per week,
                                   so "theta won two weeks running" can be
                                   told (`decide`'s theta_history).

On a weekend the card shows the coming week as seen on Monday morning: the
trigger is set from Friday's close, and the Monday rule applies. Never
raises: a failure returns {"ok": False, "reason": ...}.
"""

from __future__ import annotations

import json
import math
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import trigger_sell as ts

TABLE_PATH = Path(__file__).resolve().parent / "data" / "conditional_triggers.json"
HISTORY_KEEP = 12            # weeks of records kept per ticker
_LOCK = threading.Lock()
_TABLE_CACHE: dict = {}


def load_table(path: Path | str | None = None) -> Optional[dict]:
    """The regime table, cached until the file changes. None without one."""
    p = Path(path) if path else TABLE_PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    hit = _TABLE_CACHE.get(str(p))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        table = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    _TABLE_CACHE[str(p)] = (mtime, table)
    return table


def table_symbols(table: Any = "default") -> list:
    """The stocks the regime table covers: the Live Scanner's When to sell
    list. A {"tickers": {...}} or {SYM: entry} map, or a list of entries."""
    tbl = load_table() if table == "default" else table
    if isinstance(tbl, dict):
        inner = tbl.get("tickers") if isinstance(tbl.get("tickers"), dict) else tbl
        return sorted(str(k).upper() for k, v in inner.items() if isinstance(v, dict))
    if isinstance(tbl, list):
        return sorted({str(e.get("ticker") or e.get("symbol")).upper() for e in tbl
                       if isinstance(e, dict) and (e.get("ticker") or e.get("symbol"))})
    return []


def decision_day(today: date) -> date:
    """The day the card decides for: today on a weekday, the coming Monday
    on a weekend."""
    return today + timedelta(days=(7 - today.weekday()) % 7) if today.weekday() >= 5 else today


def week_expiry(day: date) -> date:
    """This week's weekly expiry: Friday, or Thursday when Friday is a holiday."""
    return ts._last_session(ts._monday(day))


def atm_iv(calls: Sequence[dict], puts: Sequence[dict], spot: float) -> Optional[float]:
    """Mean implied volatility of the call and put nearest the money, as a
    decimal (0.62), or None."""
    ivs = []
    for side in (calls or [], puts or []):
        rows = [r for r in side if isinstance(r, dict) and r.get("strike") is not None]
        if not rows or not spot:
            continue
        near = min(rows, key=lambda r: abs(float(r["strike"]) - spot))
        iv = ts._sigma(near.get("iv"))
        if iv:
            ivs.append(iv)
    return sum(ivs) / len(ivs) if ivs else None


# ── the weekly records ────────────────────────────────────────────────────
def _history_path(data_dir) -> Optional[Path]:
    return Path(data_dir) / "trigger_sell" / "history.json" if data_dir else None


def load_history(data_dir) -> dict:
    p = _history_path(data_dir)
    if p is None or not p.exists():
        return {}
    try:
        v = json.loads(p.read_text())
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def prior_records(data_dir, ticker: str, week: str) -> list:
    """This ticker's records from weeks BEFORE `week`, oldest first: what
    `decide` reads as theta_history. This week's own record is left out, so
    loading the page twice in a week is not two weeks running."""
    recs = load_history(data_dir).get(ticker.upper()) or []
    return sorted([r for r in recs if isinstance(r, dict) and str(r.get("week")) < week],
                  key=lambda r: str(r.get("week")))


def save_record(data_dir, record: Optional[dict]) -> None:
    """Keep this week's record for its ticker, replacing an earlier one from
    the same week (the last look of the week is the one that counts)."""
    p = _history_path(data_dir)
    if p is None or not record or not record.get("ticker") or not record.get("week"):
        return
    with _LOCK:
        hist = load_history(data_dir)
        tk = record["ticker"]
        recs = [r for r in (hist.get(tk) or []) if isinstance(r, dict) and r.get("week") != record["week"]]
        recs.append(record)
        recs.sort(key=lambda r: str(r.get("week")))
        hist[tk] = recs[-HISTORY_KEEP:]
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(hist))
        tmp.replace(p)


# ── the card ──────────────────────────────────────────────────────────────
def _r(v, dp=4):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, dp) if math.isfinite(f) else None


HEADLINES = {
    "wait": "Wait for the trigger",
    "sell_now": "Sell now",
    "sell_at_trigger": "Sell the tap now",
    "skip": "Skip this week",
}


def card(d: dict, *, weekend: bool, iv_source: str) -> dict:
    """The parts of `decide`'s answer the card shows, rounded, per share
    and per contract."""
    out: dict[str, Any] = {"ok": bool(d.get("ok")), "action": d.get("action"),
                           "headline": HEADLINES.get(d.get("action") or ""),
                           "reason": d.get("reason"), "weekend": weekend,
                           "iv_source": iv_source}
    if d.get("trigger_price") is None:
        return out
    reg = d.get("regime") or {}
    cal = d.get("calibration") or {}
    now, wait, legs = d.get("now") or {}, d.get("wait") or {}, d.get("legs") or {}
    out.update({
        "anchor": _r(d.get("anchor"), 2), "spot": _r(d.get("spot"), 2),
        "trigger_pct": _r(d.get("trigger_pct")), "trigger_price": _r(d.get("trigger_price"), 2),
        "sessions_now": d.get("sessions_now"), "iv": _r(d.get("sigma")),
        "monday_rule": bool(d.get("monday_rule")),
        "tap": d.get("tap"),
        "regime": {"on": bool(reg.get("on")), "quintile": reg.get("quintile"),
                   "prior_week_pct": _r(reg.get("prior_week_close_pct"), 2),
                   "source": cal.get("trigger_source"),
                   "weeks": reg.get("weeks_in_quintile"), "basis": reg.get("basis"),
                   "unconditional": _r(((cal.get("regime") or {}).get("unconditional")))},
        "now": {"strike": _r(now.get("strike"), 2), "credit": _r(now.get("credit"), 3),
                "ev": _r(now.get("ev"), 3), "delta": now.get("delta")},
        "wait": {"p_hit": _r(wait.get("p_hit"), 3), "sessions_at_tap": wait.get("sessions_at_tap"),
                 "sale_price": _r(wait.get("sale_price"), 2), "strike": _r(wait.get("strike"), 2),
                 "credit": _r(wait.get("credit"), 3), "delta": _r(wait.get("delta"), 2),
                 "ev": _r(wait.get("ev"), 3), "tap_weeks": wait.get("tap_weeks"),
                 "fallback": {k: _r(v, 3) for k, v in (wait.get("fallback") or {}).items()}},
        "delta": d.get("delta"),
        "legs": {k: _r(legs.get(k), 3) for k in ("theta_decay", "strike_uplift", "delta_uplift", "ev_diff")},
        "theta_dominant": bool(legs.get("theta_dominant")),
        "trigger_too_far": bool(legs.get("trigger_too_far")),
        "note": legs.get("note"),
        "weeks": cal.get("n_weeks"),
    })
    return out


def why_trigger(cal_source: Optional[str], table: Any, ticker: str, prior_pct: Any) -> str:
    """Why the card's trigger is the one it is, as a code the card words:
    regime (last week's row), no_quintiles (the table's single trigger),
    no_last_week (in the table, but last week's move could not be read),
    not_in_table, or no_table (both: the stock's own percentile)."""
    if cal_source == "regime":
        return "regime"
    if cal_source == "unconditional":
        return "no_quintiles"
    if not table:
        return "no_table"
    if ts.regime_info(ticker, None, table)["source"] == "missing":
        return "not_in_table"
    return "no_last_week" if prior_pct is None else "not_in_table"


def weekly_trigger(ticker: str, bars: Sequence[dict], today: date, table: Any = "default",
                   earnings_dates: Iterable[Any] = ()) -> dict:
    """This week's trigger alone, without the option pricing: what the Live
    Scanner watches for (v5.38). The same trigger `build` shows: Friday's
    close times (1 + the table's row for last week's move), or the stock's
    own percentile when the table cannot answer. Never raises.

    {ok, symbol, week, decision_day, anchor, trigger_pct, trigger_price,
     regime: {on, quintile, prior_week_pct, why, unconditional}, reason}"""
    tk = str(ticker or "").upper()
    out: dict[str, Any] = {"ok": False, "symbol": tk}
    try:
        day = decision_day(today)
        tbl = load_table() if table == "default" else table
        rows = ts._rows(bars)
        prior = ts.last_week_move_pct(ts._weekly_closes(rows, day), day)
        cal = ts.calibrate(bars, [e for e in (earnings_dates or []) if e], regime_table=tbl,
                           prior_week_close_pct=prior if tbl is not None else None, ticker=tk)
        anchor = ts._anchor_close(rows, day)
        out.update({"week": ts._monday(day).isoformat(), "decision_day": day.isoformat()})
        if not cal.get("ok") or not anchor:
            out["reason"] = cal.get("reason") or "no Friday close to measure the trigger from"
            return out
        tp = cal["trigger_pct"]
        info = cal.get("regime") or {}
        out.update({
            "ok": True, "anchor": _r(anchor, 2), "trigger_pct": _r(tp),
            "trigger_price": _r(anchor * (1.0 + tp), 2),
            "regime": {"on": cal.get("trigger_source") == "regime",
                       "quintile": info.get("quintile") if cal.get("trigger_source") == "regime" else None,
                       "prior_week_pct": _r(prior, 2),
                       "why": why_trigger(cal.get("trigger_source"), tbl, tk, prior),
                       "unconditional": _r(info.get("unconditional"))},
        })
        return out
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"could not set the trigger: {str(exc)[:160]}"
        return out


def build(ticker: str, *, spot: Any, bars: Sequence[dict], calls: Sequence[dict] = (),
          puts: Sequence[dict] = (), earnings_dates: Iterable[Any] = (), today: date,
          data_dir=None, hv: Any = None, table: Any = "default",
          chain_expiry: Any = None, chain_fn=None) -> dict:
    """The card for one ticker. `today` is the market's date (ET).

    `calls`/`puts` are the page's chain, for `chain_expiry`. When that is not
    this week's expiry (a later one picked, or next Friday's on a Friday),
    its IV is another tenor's: this week's chain is fetched with
    `chain_fn(expiry_date) -> (calls, puts)` instead, and without one (or
    when it fails) realized volatility stands in (Codex, #426)."""
    try:
        tk = str(ticker or "").upper()
        day = decision_day(today)
        weekend = day != today
        px = float(spot) if spot else None
        exp = week_expiry(day)
        if chain_expiry and str(chain_expiry)[:10] != exp.isoformat():
            calls = puts = ()
            if chain_fn is not None:
                try:
                    got = chain_fn(exp) or ((), ())
                    calls, puts = got[0] or (), got[1] or ()
                except Exception:  # noqa: BLE001
                    calls = puts = ()
        iv = atm_iv(calls, puts, px) if px else None
        iv_source = "chain"
        if not iv and ts._sigma(hv):
            iv, iv_source = ts._sigma(hv), "realized"
        tbl = load_table() if table == "default" else table
        week = ts._monday(day).isoformat()
        d = ts.decide(px or 0.0, bars, day, exp, iv,
                      earnings_dates=[e for e in (earnings_dates or []) if e],
                      theta_history=prior_records(data_dir, tk, week),
                      ticker=tk, regime_table=tbl)
        out = card(d, weekend=weekend, iv_source=iv_source)
        if isinstance(out.get("regime"), dict):
            out["regime"]["why"] = why_trigger(out["regime"].get("source"), tbl, tk,
                                               out["regime"].get("prior_week_pct"))
        out["table"] = bool(tbl)
        out["table_as_of"] = (tbl or {}).get("as_of") if isinstance(tbl, dict) else None
        out["decision_day"] = day.isoformat()
        out["expiry"] = exp.isoformat()
        if d.get("history_record") and not weekend:
            save_record(data_dir, d["history_record"])
        return out
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"could not price the decision: {str(exc)[:160]}"}
