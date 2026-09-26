"""
smart_buyers.py — who is putting their own money in (v5.34).

Jerry, after upgrading Unusual Whales: build the market-wide feed of
insider and Congress buying. Two lists and where they meet:

  - INSIDERS: open-market purchases (Form 4, code P) over the last 30
    days, grouped by stock. An insider can have many reasons to sell and
    one reason to buy with their own cash, so only buys count here.
    Several different insiders buying the same stock (a cluster) is the
    strongest version, so the list is ranked by how many distinct people
    bought, then by dollars.
  - CONGRESS: purchases disclosed by members of Congress over the last
    60 days, grouped by stock. Disclosures arrive up to 45 days late and
    amounts come in ranges, so this list is weaker by construction and
    says so.
  - BOTH: stocks on both lists.

Pure: the UW client is passed in and every call is guarded. A stock on
Jerry's watchlist is marked so he can filter to names he already follows.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

INSIDER_DAYS = 30
CONGRESS_DAYS = 60
CLUSTER_N = 3
TOP_N = 40
# A busy month can hold more filings than one call returns (500 insider
# rows, 200 congressional). Every page inside the window is read before
# anything is grouped, up to these caps (Codex, #421).
INSIDER_PAGE = 500
INSIDER_MAX_PAGES = 8
CONGRESS_PAGE = 200
CONGRESS_MAX_PAGES = 6
# A congressional "ticker" that is not a listed stock: mutual funds and
# notes come through as six-letter codes or with digits in them.
_STOCK_SYM = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
_CHIEF = re.compile(r"\b(CEO|CFO|COO|PRESIDENT|CHIEF|CHAIR)", re.I)


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


def _rows(v) -> list:
    if isinstance(v, list):
        return [r for r in v if isinstance(r, dict)]
    if isinstance(v, dict) and isinstance(v.get("data"), list):
        return [r for r in v["data"] if isinstance(r, dict)]
    return []


def _money(v: float) -> str:
    a = abs(v or 0)
    return (f"${a / 1e9:.1f}B" if a >= 1e9 else f"${a / 1e6:.1f}M" if a >= 1e6
            else f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:.0f}")


def _day(d: Optional[date]) -> str:
    return f"{d.strftime('%b')} {d.day}" if d else ""


def insiders(rows, today: date, watch: set) -> list:
    since = today - timedelta(days=INSIDER_DAYS)
    by: dict[str, dict] = {}
    for r in _rows(rows):
        if str(r.get("transaction_code") or "").upper() != "P":
            continue
        if not str(r.get("formtype") or "4").startswith("4"):
            continue
        d = _date(r.get("transaction_date") or r.get("filing_date"))
        sym = str(r.get("ticker") or "").upper().strip()
        if d is None or d < since or not sym:
            continue
        value = abs(_num(r.get("amount")) or 0) * (_num(r.get("price")) or 0)
        if value <= 0:
            continue
        name = str(r.get("owner_name") or "").title().strip()
        title = str(r.get("officer_title") or ("Director" if r.get("is_director") else "")).strip()
        g = by.setdefault(sym, {"symbol": sym, "people": {}, "value": 0.0, "latest": d,
                                "sector": r.get("sector"), "market_cap": _num(r.get("marketcap")),
                                "next_earnings": r.get("next_earnings_date")})
        g["value"] += value
        g["latest"] = max(g["latest"], d)
        p = g["people"].setdefault(name, {"name": name, "title": title, "value": 0.0, "date": d})
        p["value"] += value
        p["date"] = max(p["date"], d)
    out = []
    for g in by.values():
        people = sorted(g["people"].values(), key=lambda p: -p["value"])
        top = people[0]
        chief = any(_CHIEF.search(p["title"] or "") for p in people)
        n = len(people)
        who = f"{top['title'] + ' ' if top['title'] else ''}{top['name']}".strip()
        text = (f"{n} insider{'s' if n != 1 else ''} bought {_money(g['value'])} in {INSIDER_DAYS} days. "
                f"Biggest: {who}, {_money(top['value'])} on {_day(top['date'])}.")
        out.append({
            "symbol": g["symbol"], "buyers": n, "value": round(g["value"], 2),
            "latest": g["latest"].isoformat(), "cluster": n >= CLUSTER_N, "chief": chief,
            "top": {"name": top["name"], "title": top["title"], "value": round(top["value"], 2),
                    "date": top["date"].isoformat()},
            "people": [{"name": p["name"], "title": p["title"], "value": round(p["value"], 2),
                        "date": p["date"].isoformat()} for p in people[:5]],
            "sector": g["sector"], "market_cap": g["market_cap"], "next_earnings": g["next_earnings"],
            "watchlist": g["symbol"] in watch, "text": text,
        })
    out.sort(key=lambda x: (-x["buyers"], -x["value"]))
    return out[:TOP_N]


def congress(rows, today: date, watch: set) -> list:
    since = today - timedelta(days=CONGRESS_DAYS)
    by: dict[str, dict] = {}
    for r in _rows(rows):
        kind = str(r.get("txn_type") or "").lower()
        if "buy" not in kind and "purchase" not in kind:
            continue
        sym = str(r.get("ticker") or "").upper().strip()
        d = _date(r.get("transaction_date"))
        if d is None or d < since or not _STOCK_SYM.match(sym):
            continue
        name = r.get("name") or r.get("reporter") or ""
        g = by.setdefault(sym, {"symbol": sym, "members": {}, "latest": d, "trades": []})
        g["latest"] = max(g["latest"], d)
        g["members"][name] = r.get("member_type")
        g["trades"].append({"name": name, "amount": r.get("amounts") or "", "date": d.isoformat(),
                            "filed": r.get("filed_at_date"), "chamber": r.get("member_type")})
    out = []
    for g in by.values():
        trades = sorted(g["trades"], key=lambda t: t["date"], reverse=True)
        n = len(g["members"])
        t = trades[0]
        text = (f"{n} member{'s' if n != 1 else ''} of Congress bought in {CONGRESS_DAYS} days. "
                f"Latest: {t['name']}, {t['amount']} on {_day(_date(t['date']))}.")
        out.append({"symbol": g["symbol"], "members": n, "trades": trades[:5],
                    "latest": g["latest"].isoformat(), "watchlist": g["symbol"] in watch, "text": text})
    out.sort(key=lambda x: (-x["members"], _neg_date(x["latest"])))
    return out[:TOP_N]


def _neg_date(iso: str) -> int:
    d = _date(iso)
    return -(d.toordinal() if d else 0)


def fetch_insiders(uw, since: date) -> tuple:
    """(rows, truncated) over every page, or (None, False) when the first
    page failed. A later page failing keeps what arrived and says so."""
    rows: list = []
    for page in range(INSIDER_MAX_PAGES):
        try:
            got = uw.insider_buys(since.isoformat(), limit=INSIDER_PAGE, page=page)
        except Exception:  # noqa: BLE001
            got = None
        if got is None:
            return (None, False) if page == 0 else (rows, True)
        batch = _rows(got)
        rows.extend(batch)
        if len(batch) < INSIDER_PAGE:
            return rows, False
    return rows, True


def fetch_congress(uw, since: date) -> tuple:
    """(rows, truncated). UW pages congressional trades by date: each call
    asks for trades on or before the oldest one seen, and repeats are
    dropped, until the window is covered."""
    rows: list = []
    seen: set = set()
    before = None
    for i in range(CONGRESS_MAX_PAGES):
        try:
            got = uw.congress_recent(limit=CONGRESS_PAGE, date=before)
        except Exception:  # noqa: BLE001
            got = None
        if got is None:
            return (None, False) if i == 0 else (rows, True)
        batch = _rows(got)
        fresh = []
        for r in batch:
            k = (r.get("name") or r.get("reporter"), r.get("ticker"), r.get("transaction_date"),
                 r.get("amounts"), r.get("txn_type"), r.get("filed_at_date"))
            if k not in seen:
                seen.add(k)
                fresh.append(r)
        rows.extend(fresh)
        dates = [d for d in (_date(r.get("transaction_date")) for r in batch) if d]
        if len(batch) < CONGRESS_PAGE or not fresh or not dates or min(dates) < since:
            return rows, False
        before = min(dates).isoformat()
    return rows, True


def build(uw, watchlist: Iterable[str] = (), today: Optional[date] = None) -> dict:
    """Both lists and where they meet. Never raises."""
    today = today or date.today()
    watch = {str(s).upper().strip() for s in (watchlist or []) if s}
    missing = []
    ins_raw, ins_cut = fetch_insiders(uw, today - timedelta(days=INSIDER_DAYS))
    if ins_raw is None:
        missing.append("insider_buys")
    cong_raw, cong_cut = fetch_congress(uw, today - timedelta(days=CONGRESS_DAYS))
    if cong_raw is None:
        missing.append("congress_recent")
    ins = insiders(ins_raw, today, watch)
    con = congress(cong_raw, today, watch)
    cmap = {c["symbol"]: c for c in con}
    both = [{"symbol": i["symbol"], "insider": i, "congress": cmap[i["symbol"]],
             "watchlist": i["watchlist"],
             "text": f"Insiders ({i['buyers']}) and Congress ({cmap[i['symbol']]['members']}) are both buying."}
            for i in ins if i["symbol"] in cmap]
    return {"date": today.isoformat(), "insiders": ins, "congress": con, "both": both,
            "insider_days": INSIDER_DAYS, "congress_days": CONGRESS_DAYS,
            "cluster_n": CLUSTER_N, "missing": missing,
            # A cap or a failed later page: the lists are built from part
            # of the window, and the page says so.
            "partial": [k for k, cut in (("insider_buys", ins_cut), ("congress_recent", cong_cut)) if cut]}
