"""bt_plans.py — research → LIVE TRADING PLAN (Backtest v2, B5).

Turns a VALIDATED backtest result into an actionable plan — and closes the
loop by measuring whether real (journaled) trades actually followed it.

A plan is a CHECKLIST + sizing guidance + the validation evidence it was
built on. It is NEVER automation: nothing here places, routes, or
schedules orders, and the object carries that statement permanently.

Storage: <data_dir>/trade_plans.json (atomic writes, same conventions as
every other store in the app).
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

_DIR: Path | None = None
_LOCK = threading.Lock()

NOT_AUTOMATION = ("This is a plan, not automation — no orders are placed, "
                  "routed, or scheduled by this app.")


def configure(data_dir) -> None:
    global _DIR
    _DIR = Path(data_dir) if data_dir else None


def _path() -> Path | None:
    return (_DIR / "trade_plans.json") if _DIR else None


def _load() -> list:
    p = _path()
    if p is None or not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(plans: list) -> None:
    p = _path()
    if p is None:
        return
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(plans, separators=(",", ":")))
    tmp.replace(p)


# ── Plan creation from a backtest result ────────────────────────────────────
_COND_TEXT = {
    "gap_pct": lambda c: f"Stock gaps {'down' if c['value'] < 0 else 'up'} ≥ {abs(c['value'])}% at the open",
    "day_change_pct": lambda c: f"Stock is {'down' if c['value'] < 0 else 'up'} ≥ {abs(c['value'])}% on the day",
    "rsi": lambda c: f"RSI({c.get('period', 14)}) is {'≤' if c['op'] == '<=' else '≥'} {c['value']}",
    "price_vs_sma": lambda c: f"Price is {'above' if c['op'] == '>=' else 'below'} the {c['period']}-day average",
    "drawdown_from_high": lambda c: f"Stock is ≥ {c['pct']}% below its recent high",
    "rel_volume": lambda c: f"Volume ≥ {c['mult']}× the {c.get('lookback', 20)}-day average",
    "market_regime": lambda c: f"SPY is in a {c['regime']}",
    "new_high": lambda c: f"New {c.get('lookback', 20)}-day high",
    "new_low": lambda c: f"New {c.get('lookback', 20)}-day low",
    "consec_down": lambda c: f"{c.get('n', 3)} consecutive down days",
    "consec_up": lambda c: f"{c.get('n', 3)} consecutive up days",
    "move_pct": lambda c: f"Moved {'≤' if c['op'] == '<=' else '≥'} {abs(c['value'])}% over {c.get('days', 5)} days",
    "price_abs": lambda c: f"Price {'above' if c['op'] == '>=' else 'below'} ${c['value']}",
}


def build_checklist(rules: dict) -> list:
    """The tested rules as a human ENTRY CHECKLIST — the exact conditions
    the backtest required, so live trades can be held to the same bar."""
    out = []
    opts = rules.get("options") or {}
    struct = (opts.get("structure") or rules.get("instrument") or "stock").replace("_", " ")
    dte = opts.get("dte")
    delta = opts.get("target_delta")
    line = f"Structure: {struct}"
    if delta:
        line += f" at ~{int(round(delta * 100))}Δ"
    if dte:
        line += f", ~{dte} DTE"
    out.append(line)
    for c in (rules.get("entry") or []):
        fn = _COND_TEXT.get(c.get("type"))
        if fn:
            try:
                out.append(fn(c))
            except Exception:
                out.append(c.get("label") or c.get("type"))
    ef = rules.get("earnings_filter")
    if ef:
        out.append("No earnings inside the window" if ef.get("mode") == "skip"
                   else "ONLY during earnings week")
    mgmt = opts.get("management") or {}
    if mgmt.get("profit_take_pct"):
        out.append(f"Take profit at {mgmt['profit_take_pct']}% of the credit")
    if mgmt.get("stop_x_credit"):
        out.append(f"Stop: buy back at {mgmt['stop_x_credit']}× the credit")
    if mgmt.get("exit_dte"):
        out.append(f"Exit by {mgmt['exit_dte']} DTE regardless")
    if mgmt.get("roll_dte"):
        out.append(f"Roll at {mgmt['roll_dte']} DTE")
    if mgmt.get("hold_to_expiry"):
        out.append("Hold to expiration (no management — as tested)")
    return out


def sizing_guidance(result: dict, account_size: float | None = None) -> dict:
    """Risk guidance DERIVED from the validation run: the Monte-Carlo P95
    drawdown says what this strategy can plausibly draw down at the tested
    sizing — scale so that the P95 drawdown stays within a 15% account
    drawdown. Conservative by construction; labeled."""
    mc = result.get("monte_carlo") or {}
    m = result.get("metrics") or {}
    p95 = ((mc.get("max_dd_pct") or {}).get("p95"))
    start = m.get("start_equity") or 100_000
    out = {"tested_start_equity": start,
           "tested_mc_p95_drawdown_pct": p95,
           "basis": "Scale so the Monte-Carlo P95 drawdown stays ≤15% of the account.",
           }
    if p95 and p95 > 0:
        out["suggested_capital_fraction"] = round(min(1.0, 15.0 / p95), 2)
        if account_size:
            out["suggested_allocation"] = round(account_size * out["suggested_capital_fraction"], 0)
    else:
        out["suggested_capital_fraction"] = None
        out["note"] = "Not enough Monte-Carlo data to size — treat as untested."
    return out


def create_plan(result: dict, rules_text: str = "",
                account_size: float | None = None) -> dict:
    """Plan object from a completed backtest result (with validation)."""
    m = result.get("metrics") or {}
    wf = result.get("walk_forward") or {}
    ds = result.get("deflated_sharpe") or {}
    sens = result.get("sensitivity") or {}
    plan = {
        "id": uuid.uuid4().hex[:10],
        "created": datetime.now().isoformat(timespec="seconds"),
        "status": "active",
        "label": f"{(result.get('structure') or 'strategy').replace('_', ' ')} · "
                 f"{m.get('win_rate', '—')}% win · {m.get('n_trades', 0)} trades tested",
        "structure": result.get("structure"),
        "rules_text": rules_text or "",
        "rules": result.get("rules"),
        "checklist": build_checklist(result.get("rules") or {}),
        "sizing": sizing_guidance(result, account_size),
        "evidence": {
            "n_trades": m.get("n_trades"),
            "win_rate": m.get("win_rate"),
            "total_pnl": m.get("total_pnl"),
            "avg_return_on_bp_pct": m.get("avg_return_on_bp_pct"),
            "max_drawdown_pct": m.get("max_drawdown_pct"),
            "sharpe": m.get("sharpe"),
            "wf_efficiency": wf.get("wf_efficiency"),
            "wf_verdict": wf.get("verdict"),
            "dsr": ds.get("dsr"),
            "dsr_verdict": ds.get("verdict"),
            "sensitivity_verdict": sens.get("verdict"),
            "modeled": True,
        },
        "not_automation": NOT_AUTOMATION,
    }
    with _LOCK:
        plans = _load()
        plans.insert(0, plan)
        _save(plans[:50])
    return plan


def list_plans() -> list:
    with _LOCK:
        return _load()


def set_status(plan_id: str, status: str) -> bool:
    if status not in ("active", "archived"):
        return False
    with _LOCK:
        plans = _load()
        hit = False
        for p in plans:
            if p.get("id") == plan_id:
                p["status"] = status
                hit = True
        if hit:
            _save(plans)
        return hit


# ── Adherence: did real journaled trades follow the plan? ───────────────────
def adherence(journal_rows: list) -> dict:
    """Split CLOSED journal trades into plan-tagged vs off-plan and compare
    outcomes. A trade counts toward a plan when the journal entry carries
    plan_id (stamped when the user logs a trade from the plan card)."""
    plans = {p["id"]: p for p in _load()}
    by_plan: dict = {}
    off = {"n": 0, "pnl": 0.0, "wins": 0}

    def pnl_of(t):
        ep, cp, q = t.get("entry_premium"), t.get("closed_premium"), t.get("qty") or 0
        if ep is None or cp is None or not q:
            return None
        return (ep - cp) * 100 * abs(q) if q < 0 else (cp - ep) * 100 * abs(q)

    for t in journal_rows or []:
        p = pnl_of(t)
        if p is None:
            continue
        pid = t.get("plan_id")
        if pid and pid in plans:
            d = by_plan.setdefault(pid, {"plan_id": pid,
                                         "label": plans[pid].get("label"),
                                         "n": 0, "pnl": 0.0, "wins": 0})
        else:
            d = off
        d["n"] += 1
        d["pnl"] = round(d["pnl"] + p, 2)
        d["wins"] += 1 if p > 0 else 0
    for d in list(by_plan.values()) + [off]:
        d["win_rate"] = round(d["wins"] / d["n"] * 100.0, 1) if d["n"] else None
    return {"plans": sorted(by_plan.values(), key=lambda d: -d["n"]),
            "off_plan": off,
            "note": ("Plan rows count only journal trades logged AGAINST a plan; "
                     "everything else is off-plan. If off-plan P/L beats plan P/L, "
                     "the journal is telling you something.")}
