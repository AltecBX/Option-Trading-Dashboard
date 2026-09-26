"""
money_map.py — the Big Money Map (v5.33).

Jerry upgraded Unusual Whales to API Basic: "figure out how to use my App
for these features I may not have coded". The app already read flow,
tide, net premium, sector flow and 13Fs. What it never read was where
the big players are *positioned*:

  - the gamma levels dealers hedge around (call wall, put wall, flip,
    magnet), and max pain for the nearest expiry;
  - the prices where dark pools traded the most shares;
  - which contracts gained the most open interest overnight, and whether
    the prior session's volume printed at the ask or the bid;
  - whether options are priced above or below what the stock really
    moves (implied vs realized volatility, the seller's edge);
  - insider buys and sells, and congressional trades.

This module turns those answers into one dict of plain sentences, one
per section, for a premium seller reading at a glance. It is pure: the
UW client is passed in, every call is guarded, and a section that cannot
be answered is listed in `missing` instead of raising. Nothing here
decides a trade; it says where the levels are and what they usually mean.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

INSIDER_DAYS = 90
CONGRESS_DAYS = 180
DARK_LEVELS = 3
OPENED_N = 4
# Implied minus realized, in volatility points, that counts as rich/thin.
RICH_PTS = 3.0
THIN_PTS = -2.0

LEVEL_TEXT = {
    "call_wall": ("Call wall", "Resistance: dealers sell into rallies here."),
    "put_wall": ("Put wall", "Support: dealers buy dips here."),
    "gamma_magnet": ("Magnet", "Price tends to get pulled to this strike and pinned."),
    "gamma_flip": ("Gamma flip", "Above it moves get calmer; below it they get bigger."),
    "max_pain": ("Max pain", "Where the most options expire worthless; price often drifts here into expiry."),
    "dark_pool": ("Dark pool", "Big buyers and sellers traded the most shares here, off the exchange."),
}

_OCC = re.compile(r"^([A-Z][A-Z0-9.]*?)(\d{6})([CP])(\d{8})$")


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _pct(level: Optional[float], spot: Optional[float]) -> Optional[float]:
    if level is None or not spot:
        return None
    return round((level / spot - 1.0) * 100.0, 2)


def _date(v) -> Optional[date]:
    if not v:
        return None
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"${a / 1e9:.1f}B"
    if a >= 1e6:
        return f"${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"${a / 1e3:.0f}K"
    return f"${a:.0f}"


def _px(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"${v:,.2f}".replace(".00", "")


def _short_day(d: Optional[date]) -> str:
    return f"{d.strftime('%b')} {d.day}" if d else ""


def _rows(v) -> list:
    """UW peels `data`; some answers are a list, some a dict holding one."""
    if isinstance(v, list):
        return [r for r in v if isinstance(r, dict)]
    if isinstance(v, dict):
        inner = v.get("data")
        if isinstance(inner, list):
            return [r for r in inner if isinstance(r, dict)]
        return [v]
    return []


def parse_occ(sym) -> Optional[dict]:
    """MSFT240315C00350000 -> {root, expiry, right, strike}."""
    m = _OCC.match(str(sym or "").strip().upper())
    if not m:
        return None
    root, ymd, cp, strike = m.groups()
    try:
        exp = datetime.strptime(ymd, "%y%m%d").date()
    except ValueError:
        return None
    return {"root": root, "expiry": exp.isoformat(),
            "right": "call" if cp == "C" else "put", "strike": int(strike) / 1000.0}


def _call(fn: Callable, *a, **k):
    try:
        return fn(*a, **k), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:120]


# ── sections ───────────────────────────────────────────────────────────────

def levels_section(gex, max_pain, dark, spot: Optional[float], today: date) -> dict:
    levels: list[dict] = []
    g = gex if isinstance(gex, dict) else (_rows(gex)[0] if _rows(gex) else {})
    for key in ("call_wall", "gamma_magnet", "gamma_flip", "put_wall"):
        v = _num(g.get(key))
        if v is not None:
            levels.append({"kind": key, "price": v})
    # Max pain for the nearest expiry that has not passed.
    mp_rows = sorted(((d, _num(r.get("max_pain"))) for r in _rows(max_pain)
                      for d in [_date(r.get("expiry"))] if d and d >= today),
                     key=lambda t: t[0])
    mp = next(((d, v) for d, v in mp_rows if v is not None), None)
    if mp:
        levels.append({"kind": "max_pain", "price": mp[1], "expiry": mp[0].isoformat()})
    # The price buckets with the most dark pool shares.
    dp = [(p, int(_num(r.get("dark_pool_volume")) or 0))
          for r in _rows(dark) for p in [_num(r.get("price"))] if p is not None]
    dp = [t for t in dp if t[1] > 0]
    dp.sort(key=lambda t: t[1], reverse=True)
    for p, vol in dp[:DARK_LEVELS]:
        levels.append({"kind": "dark_pool", "price": p, "shares": vol})
    for lv in levels:
        name, meaning = LEVEL_TEXT[lv["kind"]]
        if lv["kind"] == "max_pain":
            name = f"Max pain ({_short_day(_date(lv['expiry']))})"
        if lv["kind"] == "dark_pool":
            meaning = f"{lv['shares']:,} shares traded off the exchange here: a price big money cared about."
        lv["label"] = name
        lv["meaning"] = meaning
        lv["pct"] = _pct(lv["price"], spot)
        lv["side"] = None if spot is None else ("above" if lv["price"] > spot else "below" if lv["price"] < spot else "at")
    levels.sort(key=lambda lv: lv["price"], reverse=True)

    flip = _num(g.get("gamma_flip"))
    regime = None
    if spot is not None and flip is not None:
        if spot >= flip:
            regime = {"state": "calm", "flip": flip,
                      "text": f"Above the gamma flip ({_px(flip)}): dealers sell rallies and buy dips, "
                              "so moves get damped. A calmer tape suits selling premium."}
        else:
            regime = {"state": "wild", "flip": flip,
                      "text": f"Below the gamma flip ({_px(flip)}): dealers chase the move, "
                              "so swings get bigger. Give short strikes extra room."}

    seller = []
    pw, cw = _num(g.get("put_wall")), _num(g.get("call_wall"))
    if spot is not None and pw is not None and pw < spot:
        seller.append({"side": "put", "level": pw, "pct": _pct(pw, spot),
                       "text": f"Selling puts: the put wall at {_px(pw)} is {abs(_pct(pw, spot)):.1f}% below. "
                               f"A short put at or under {_px(pw)} has dealer support in front of it."})
    if spot is not None and cw is not None and cw > spot:
        seller.append({"side": "call", "level": cw, "pct": _pct(cw, spot),
                       "text": f"Selling calls: the call wall at {_px(cw)} is {_pct(cw, spot):.1f}% above. "
                               f"A short call at or over {_px(cw)} has dealer selling in front of it."})
    return {"levels": levels, "regime": regime, "seller": seller,
            "as_of": g.get("time") or g.get("date")}


def premium_section(vrp) -> Optional[dict]:
    rows = [r for r in _rows(vrp) if _num(r.get("implied_volatility")) is not None
            and _num(r.get("realized_volatility")) is not None]
    if not rows:
        return None
    r = max(rows, key=lambda x: str(x.get("date") or ""))
    iv, rv = _num(r["implied_volatility"]), _num(r["realized_volatility"])
    # UW quotes both as fractions (0.26); a percent would already be > 3.
    scale = 100.0 if max(iv, rv) <= 3 else 1.0
    iv_p, rv_p = iv * scale, rv * scale
    gap = round(iv_p - rv_p, 1)
    ivd = r.get("implied_volatility_days") or 30
    rvd = r.get("realized_volatility_days") or 21
    if gap >= RICH_PTS:
        state, text = "rich", (f"Options price a {iv_p:.0f}% yearly move; the stock has actually moved "
                               f"{rv_p:.0f}% (last {rvd} trading days). Premium is RICH: sellers are being overpaid "
                               f"by {gap:.1f} points.")
    elif gap <= THIN_PTS:
        state, text = "thin", (f"Options price a {iv_p:.0f}% move but the stock has really moved {rv_p:.0f}%. "
                               f"Premium is THIN: selling here pays less than the risk.")
    else:
        state, text = "fair", (f"Options price a {iv_p:.0f}% move; the stock has moved {rv_p:.0f}%. "
                               "Premium is FAIR: no extra edge for sellers.")
    rank = _num(r.get("rank"))
    return {"state": state, "text": text, "iv": round(iv_p, 1), "rv": round(rv_p, 1), "gap": gap,
            "iv_days": ivd, "rv_days": rvd, "rank": rank, "date": r.get("date")}


def opened_section(oi_rows, symbol: str) -> list:
    out = []
    for r in _rows(oi_rows):
        diff = _num(r.get("oi_diff_plain"))
        occ = parse_occ(r.get("option_symbol"))
        if not diff or diff <= 0 or not occ:
            continue
        ask, bid = _num(r.get("prev_ask_volume")) or 0, _num(r.get("prev_bid_volume")) or 0
        vol = _num(r.get("volume")) or (ask + bid) or 0
        multi = _num(r.get("prev_multi_leg_volume")) or 0
        if ask > 1.5 * bid and ask > 0:
            how, lean = "bought", ("bullish" if occ["right"] == "call" else "bearish")
        elif bid > 1.5 * ask and bid > 0:
            how, lean = "sold", ("bearish" if occ["right"] == "call" else "bullish")
        else:
            how, lean = "mixed", None
        exp = _date(occ["expiry"])
        prem = _num(r.get("prev_total_premium"))
        spreads = vol > 0 and multi / vol >= 0.5
        what = {"bought": "mostly bought at the ask", "sold": "mostly sold at the bid",
                "mixed": "bought and sold about evenly"}[how]
        text = (f"+{int(diff):,} contracts of the {_short_day(exp)} {_px(occ['strike'])} {occ['right']} opened, "
                f"{what}" + (f" ({_money(prem)})" if prem else "") +
                (". Mostly part of spreads." if spreads else "."))
        out.append({"symbol": r.get("option_symbol"), "right": occ["right"], "strike": occ["strike"],
                    "expiry": occ["expiry"], "opened": int(diff), "how": how, "lean": lean,
                    "premium": prem, "spreads": spreads, "text": text})
    out.sort(key=lambda x: x["opened"], reverse=True)
    return out[:OPENED_N]


def insiders_section(rows, today: date) -> Optional[dict]:
    if rows is None:
        return None
    since = today - timedelta(days=INSIDER_DAYS)
    buys, sells = [], []
    for r in _rows(rows):
        code = str(r.get("transaction_code") or "").upper()
        if str(r.get("formtype") or "").startswith("144"):
            continue                           # a notice of intent, not a trade
        d = _date(r.get("transaction_date") or r.get("filing_date"))
        if d is None or d < since:
            continue
        shares = _num(r.get("amount")) or 0
        price = _num(r.get("price")) or _num(r.get("stock_price")) or 0
        value = abs(shares) * price
        row = {"name": str(r.get("owner_name") or "").title(),
               "title": r.get("officer_title") or ("Director" if r.get("is_director") else ""),
               "date": d.isoformat(), "value": round(value, 2),
               "planned": bool(r.get("is_10b5_1"))}
        if code == "P":
            buys.append(row)
        elif code == "S":
            sells.append(row)
    bv, sv = sum(b["value"] for b in buys), sum(s["value"] for s in sells)
    nb = len({b["name"] for b in buys})
    ns = len({s["name"] for s in sells})
    planned = sum(1 for s in sells if s["planned"])
    top = max(buys, key=lambda b: b["value"]) if buys else None
    if buys:
        who = f"{top['title'] + ' ' if top['title'] else ''}{top['name']}".strip()
        state = "buying"
        text = (f"Insiders are BUYING: {nb} bought {_money(bv)} in {INSIDER_DAYS} days "
                f"(biggest: {who}, {_money(top['value'])} on {_short_day(_date(top['date']))}).")
        if sells:
            text += f" {ns} sold {_money(sv)}."
    elif sells:
        state = "selling"
        text = f"Only selling: {ns} insider{'s' if ns != 1 else ''} sold {_money(sv)} in {INSIDER_DAYS} days"
        text += (f", {planned} of {len(sells)} pre-planned (10b5-1), which is usually routine." if planned
                 else ", none of it pre-planned.")
    else:
        state, text = "quiet", f"No insider buys or sells in the last {INSIDER_DAYS} days."
    recent = sorted(buys + [dict(s, sell=True) for s in sells], key=lambda x: x["date"], reverse=True)[:4]
    for x in recent:
        x.setdefault("sell", False)
    return {"state": state, "text": text, "buy_value": round(bv, 2), "sell_value": round(sv, 2),
            "buyers": nb, "sellers": ns, "planned_sells": planned, "recent": recent}


def congress_section(rows, today: date) -> Optional[dict]:
    if rows is None:
        return None
    since = today - timedelta(days=CONGRESS_DAYS)
    trades = []
    for r in _rows(rows):
        d = _date(r.get("transaction_date"))
        if d is None or d < since:
            continue
        kind = str(r.get("txn_type") or "").lower()
        side = "buy" if "buy" in kind or "purchase" in kind else "sell" if "sell" in kind or "sale" in kind else kind
        trades.append({"name": r.get("name") or r.get("reporter") or "", "side": side,
                       "amount": r.get("amounts") or "", "date": d.isoformat(),
                       "filed": r.get("filed_at_date"), "chamber": r.get("member_type")})
    trades.sort(key=lambda t: t["date"], reverse=True)
    nb = sum(1 for t in trades if t["side"] == "buy")
    ns = sum(1 for t in trades if t["side"] == "sell")
    if not trades:
        text = "No congressional trades in the last 6 months."
    else:
        t = trades[0]
        text = (f"{nb} buy{'s' if nb != 1 else ''} and {ns} sale{'s' if ns != 1 else ''} by members of "
                f"Congress in 6 months. Latest: {t['name']} {'bought' if t['side'] == 'buy' else 'sold'} "
                f"{t['amount']} on {_short_day(_date(t['date']))}.")
    return {"text": text, "buys": nb, "sells": ns, "recent": trades[:4]}


def build(uw, symbol: str, spot: Optional[float] = None, today: Optional[date] = None) -> dict:
    """Every section for one ticker. Never raises."""
    symbol = str(symbol or "").upper().strip()
    today = today or date.today()
    spot = _num(spot) if spot else None
    missing: list[str] = []

    def get(name, *a, **k):
        fn = getattr(uw, name, None)
        if fn is None:
            missing.append(name)
            return None
        v, err = _call(fn, *a, **k)
        if v is None:
            missing.append(name)
        return v

    gex = get("gex_levels", symbol)
    mp = get("max_pain", symbol)
    dark = get("darkpool_levels", symbol)
    vrp = get("variance_risk_premium", symbol)
    oi = get("oi_change", symbol, limit=25)
    ins = get("insider_transactions", symbol,
              start_date=(today - timedelta(days=INSIDER_DAYS)).isoformat(), limit=200)
    cong = get("congress_trades", symbol, limit=50)

    lv = levels_section(gex, mp, dark, spot, today)
    return {
        "symbol": symbol, "spot": spot, "date": today.isoformat(),
        "levels": lv["levels"], "regime": lv["regime"], "seller": lv["seller"], "levels_as_of": lv["as_of"],
        "premium": premium_section(vrp),
        "opened": opened_section(oi, symbol),
        "insiders": insiders_section(ins, today),
        "congress": congress_section(cong, today),
        "missing": missing,
    }
