"""
report_card.py — Worth Selling Today, graded in real dollars (v5.36).

Jerry asked for the truest report card: not "did the stock stay away from
the strike" but what each pick would actually have made, priced with the
real option prices Unusual Whales keeps for every contract.

  record()  Every pick the board shows is written down the first day it
            appears: symbol, trade, legs, expiry and the credit quoted.
  grade()   For each pick, the price of each leg is read from UW's daily
            history for that exact contract (`/api/option-contract/{id}/
            historic`), on the expiry day for a finished trade or on the
            latest day for one still open. What it cost to buy the trade
            back is subtracted from the credit: that is the profit or loss
            per contract, if it had been held to expiry.

A finished pick is graded once and kept; an open one is re-priced. A pick
whose history does not come back stays ungraded rather than guessed.
Pure apart from the JSON file it keeps; the history fetch is passed in.
"""

from __future__ import annotations

import json
import math
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

KEEP = 3000                 # picks kept on disk
FETCH_BUDGET = 40           # contract histories fetched per grading pass
_LOCK = threading.Lock()


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _date(v) -> Optional[date]:
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date() if v else None
    except ValueError:
        return None


def occ(root: str, expiry: str, right: str, strike: float) -> Optional[str]:
    """IOVA, 2026-10-16, put, 12.5 -> IOVA261016P00012500 (UW's symbol form)."""
    d = _date(expiry)
    if d is None or strike is None or right not in ("put", "call"):
        return None
    return f"{str(root).upper()}{d.strftime('%y%m%d')}{'P' if right == 'put' else 'C'}{int(round(strike * 1000)):08d}"


def _path(data_dir) -> Optional[Path]:
    return Path(data_dir) / "report_card" / "picks.json" if data_dir else None


def load(data_dir) -> list:
    p = _path(data_dir)
    if p is None or not p.exists():
        return []
    try:
        v = json.loads(p.read_text())
        return v if isinstance(v, list) else []
    except Exception:  # noqa: BLE001
        return []


def save(data_dir, picks: list) -> None:
    p = _path(data_dir)
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(picks[-KEEP:]))
    tmp.replace(p)


def _key(sym, kind, expiry, legs) -> str:
    return "|".join([sym, kind or "", expiry or ""] + [f"{l['action'][0]}{l['right'][0]}{l['strike']:g}" for l in legs])


def record(data_dir, rows, today: date) -> int:
    """Write down every board pick not already on file. Returns how many
    were new. A pick is the same pick while its symbol, trade, expiry and
    strikes are the same: seeing it again tomorrow is not a second trade."""
    new = []
    with _LOCK:
        picks = load(data_dir)
        have = {p["key"] for p in picks}
        for r in (rows or []):
            legs = [l for l in (r.get("legs") or []) if l.get("strike") is not None]
            credit = _num(r.get("credit"))
            sym = str(r.get("symbol") or "").upper()
            if not sym or not legs or credit is None or not r.get("expiration"):
                continue
            k = _key(sym, r.get("kind"), r["expiration"], legs)
            if k in have:
                continue
            have.add(k)
            new.append({"key": k, "date": today.isoformat(), "symbol": sym, "kind": r.get("kind"),
                        "trade": r.get("trade"), "expiry": r["expiration"], "credit": credit,
                        "max_loss": _num(r.get("max_loss")), "spot": _num(r.get("spot")),
                        "legs": [{"action": l["action"], "right": l["right"], "strike": float(l["strike"])}
                                 for l in legs],
                        "grade": None})
        if new:
            save(data_dir, picks + new)
    return len(new)


def _leg_price(rows, on: date) -> Optional[tuple]:
    """(price, date) of the last daily row on or before `on`: the NBBO mid
    when both sides are there, else the last trade."""
    best = None
    for r in rows or []:
        d = _date(r.get("date"))
        if d is None or d > on:
            continue
        bid, ask = _num(r.get("nbbo_bid")), _num(r.get("nbbo_ask"))
        px = (bid + ask) / 2 if (bid is not None and ask is not None and ask > 0) else _num(r.get("last_price"))
        if px is None:
            continue
        if best is None or d > best[1]:
            best = (px, d)
    return best


def _due(picks: list, today: date) -> list:
    out = []
    for p in picks:
        if (p.get("grade") or {}).get("final"):
            continue
        exp, picked = _date(p.get("expiry")), _date(p.get("date"))
        if exp is None or picked is None or today <= picked:
            continue
        out.append(p)
    return out


def _fetch_all(symbols: list, history, workers: int = 6, budget_s: float = 25.0) -> dict:
    from concurrent.futures import ThreadPoolExecutor, wait
    out: dict = {}
    if not symbols:
        return out
    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(symbols))))
    try:
        futs = {pool.submit(history, s): s for s in symbols}
        done, _ = wait(futs, timeout=budget_s)
        for f in done:
            try:
                out[futs[f]] = f.result()
            except Exception:  # noqa: BLE001
                out[futs[f]] = None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def grade(picks: list, history: Callable[[str], Optional[list]], today: date,
          budget: int = FETCH_BUDGET) -> tuple:
    """Grade what can be graded. Returns (picks, fetched). A finished pick
    keeps its grade; an open one is re-priced each pass. Up to `budget`
    contract histories are fetched, in parallel. The picks tried longest
    ago go first and each pick tried is stamped, so a pass that runs out
    of budget starts where the last one stopped: without that, open picks
    kept taking the same budget and newer ones were never priced
    (Codex, #423)."""
    due = sorted(_due(picks, today), key=lambda p: (p.get("tried") or "", p["expiry"], p["date"]))
    want: list = []
    for p in due:
        syms = [occ(p["symbol"], p["expiry"], l["right"], l["strike"]) for l in p["legs"]]
        new = [s for s in syms if s and s not in want]
        if len(want) + len(new) > budget:
            break
        want.extend(new)
    cache = _fetch_all(want, history)
    fetched = len(want)
    stamp = datetime.now().isoformat(timespec="microseconds")
    for p in due:
        if all(occ(p["symbol"], p["expiry"], l["right"], l["strike"]) in want for l in p["legs"]):
            p["tried"] = stamp
    for p in due:
        g = p.get("grade") or {}
        if g.get("final"):
            continue
        exp = _date(p.get("expiry"))
        on = min(exp, today)
        final = exp < today
        cost, as_of, ok = 0.0, None, True
        for leg in p["legs"]:
            sym = occ(p["symbol"], p["expiry"], leg["right"], leg["strike"])
            if sym not in cache:
                ok = False                      # over this pass's budget: next time
                break
            got = _leg_price(cache[sym], on)
            if got is None:
                ok = False
                break
            px, d = got
            cost += px if leg["action"] == "sell" else -px
            as_of = d if as_of is None or d < as_of else as_of
        if not ok:
            continue
        pnl = round((p["credit"] - cost) * 100, 2)
        p["grade"] = {"final": final, "on": as_of.isoformat() if as_of else on.isoformat(),
                      "buyback": round(cost, 4), "pnl": pnl, "win": pnl > 0}
    return picks, fetched


def summary(picks: list) -> dict:
    closed = [p for p in picks if (p.get("grade") or {}).get("final")]
    open_ = [p for p in picks if p.get("grade") and not p["grade"].get("final")]
    ungraded = [p for p in picks if not p.get("grade")]
    wins = sum(1 for p in closed if p["grade"]["win"])
    total = round(sum(p["grade"]["pnl"] for p in closed), 2)
    open_pnl = round(sum(p["grade"]["pnl"] for p in open_), 2)
    n = len(closed)
    if n:
        text = (f"Of {n} finished pick{'s' if n != 1 else ''}, {wins} made money ({wins / n * 100:.0f}%), "
                f"{'+' if total >= 0 else '-'}${abs(total):,.0f} in total at one contract each, priced with the real option prices.")
    else:
        text = "No pick has reached its expiry yet, so there is nothing final to grade."
    if open_:
        text += (f" {len(open_)} still open: {'+' if open_pnl >= 0 else '-'}${abs(open_pnl):,.0f} if bought back today.")
    recent = sorted([p for p in picks if p.get("grade")], key=lambda p: (p["expiry"], p["date"]), reverse=True)[:12]
    return {"closed": n, "wins": wins, "win_rate": round(wins / n * 100, 1) if n else None,
            "total_pnl": total, "avg_pnl": round(total / n, 2) if n else None,
            "open": len(open_), "open_pnl": open_pnl, "ungraded": len(ungraded), "recorded": len(picks),
            "text": text,
            "recent": [{"symbol": p["symbol"], "trade": p.get("trade"), "legs": p["legs"], "expiry": p["expiry"],
                        "date": p["date"], "credit": p["credit"], **p["grade"]} for p in recent]}


def report(data_dir, history: Callable[[str], Optional[list]], today: Optional[date] = None,
           budget: int = FETCH_BUDGET) -> dict:
    """Grade what is due and return the summary. Never raises."""
    today = today or date.today()
    # The network calls happen outside the lock; the grades are merged into
    # whatever is on file by then, so a pick recorded meanwhile is kept.
    try:
        graded, fetched = grade(load(data_dir), history, today, budget=budget)
    except Exception as exc:  # noqa: BLE001
        out = summary(load(data_dir))
        out["error"] = str(exc)[:200]
        return out
    by = {p["key"]: p["grade"] for p in graded if p.get("grade")}
    tried = {p["key"]: p["tried"] for p in graded if p.get("tried")}
    with _LOCK:
        picks = load(data_dir)
        for p in picks:
            if p["key"] in by:
                p["grade"] = by[p["key"]]
            if p["key"] in tried:
                p["tried"] = tried[p["key"]]
        if by or tried:
            save(data_dir, picks)
    out = summary(picks)
    out["fetched"] = fetched
    return out
