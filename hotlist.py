"""
hotlist.py — the Options Hotlist (v5.36).

Two market-wide scans from Jerry's Unusual Whales plan:

  HOTTEST CONTRACTS  UW's contract screener ordered by premium: where the
                     biggest money traded in options today, and whether it
                     printed at the ask (bought) or the bid (sold).
  VOLATILITY ODDITIES  UW's volatility-anomaly screen in both directions:
                     `short_vol` names, whose options are unusually rich
                     (a seller's shortlist), and `long_vol` names, whose
                     options are unusually cheap (a poor place to sell).

UW's spec publishes the screener's row fields but describes the anomaly
rows only as "the full component fields", so those rows are read
defensively: a symbol and whatever score and volatility fields are
there, never a guess at a field that is not.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Iterable, Optional

from money_map import parse_occ

HOT_N = 40
ODD_N = 30


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _rows(v) -> list:
    if isinstance(v, list):
        return [r for r in v if isinstance(r, dict)]
    if isinstance(v, dict):
        for k in ("data", "rows", "results"):
            if isinstance(v.get(k), list):
                return [r for r in v[k] if isinstance(r, dict)]
    return []


def _money(v: Optional[float]) -> str:
    a = abs(v or 0)
    return (f"${a / 1e9:.1f}B" if a >= 1e9 else f"${a / 1e6:.1f}M" if a >= 1e6
            else f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:.0f}")


def _day(iso) -> str:
    try:
        d = datetime.strptime(str(iso)[:10], "%Y-%m-%d").date()
        return f"{d.strftime('%b')} {d.day}"
    except ValueError:
        return str(iso or "")


def hottest(rows, watch: set) -> list:
    out = []
    for r in _rows(rows):
        occ = parse_occ(r.get("option_symbol"))
        vol = _num(r.get("volume")) or 0
        if not occ or vol <= 0:
            continue
        right = r.get("option_type") or occ["right"]
        ask, bid = _num(r.get("ask_side_volume")) or 0, _num(r.get("bid_side_volume")) or 0
        ask_p, bid_p = ask / vol, bid / vol
        if ask_p >= 0.6:
            how = "bought"
            lean = "bullish" if right == "call" else "bearish"
        elif bid_p >= 0.6:
            how = "sold"
            lean = "bearish" if right == "call" else "bullish"
        else:
            how, lean = "mixed", None
        prem = _num(r.get("premium"))
        oi = _num(r.get("open_interest")) or 0
        sweep = (_num(r.get("sweep_volume")) or 0) / vol
        multi = (_num(r.get("multileg_volume")) or 0) / vol
        strike = _num(r.get("strike")) or occ["strike"]
        exp = r.get("expiry") or occ["expiry"]
        what = {"bought": f"{ask_p * 100:.0f}% at the ask, mostly BOUGHT",
                "sold": f"{bid_p * 100:.0f}% at the bid, mostly SOLD",
                "mixed": "bought and sold about evenly"}[how]
        extra = []
        if oi and vol > oi:
            extra.append(f"volume {vol / oi:.1f}x the open interest (new positions)")
        if sweep >= 0.3:
            extra.append(f"{sweep * 100:.0f}% sweeps (urgent orders)")
        if multi >= 0.5:
            extra.append("mostly part of spreads")
        sym = occ["root"]
        out.append({
            "symbol": sym, "option_symbol": r.get("option_symbol"), "right": right, "strike": strike,
            "expiry": exp, "volume": int(vol), "open_interest": int(oi), "premium": prem,
            "ask_pct": round(ask_p * 100, 1), "bid_pct": round(bid_p * 100, 1), "how": how, "lean": lean,
            "stock_price": _num(r.get("stock_price")), "sector": r.get("sector"),
            "next_earnings": r.get("next_earnings_date"), "watchlist": sym in watch,
            "text": (f"{sym} {_day(exp)} ${strike:g} {right}: {_money(prem)} traded on {int(vol):,} contracts, "
                     f"{what}" + (f"; {', '.join(extra)}" if extra else "") + "."),
        })
    out.sort(key=lambda x: -(x["premium"] or 0))
    return out[:HOT_N]


_SYM_KEYS = ("ticker", "symbol", "underlying_symbol")
_SCORE_KEYS = ("score", "anomaly_score", "composite_score", "anomaly", "z_score", "zscore", "value")
_IV_KEYS = ("iv", "implied_volatility", "iv30", "atm_iv", "iv_30d")
_RV_KEYS = ("rv", "realized_volatility", "rv30", "hv", "rv_20d", "realized_vol")
_RANK_KEYS = ("iv_rank", "rank", "percentile", "iv_percentile")


def _first(r: dict, keys) -> Optional[float]:
    for k in keys:
        v = _num(r.get(k))
        if v is not None:
            return v
    return None


def _pct(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return round(v * 100, 1) if abs(v) <= 3 else round(v, 1)


def oddities(rows, direction: str, watch: set) -> list:
    out = []
    rich = direction == "short_vol"
    for r in _rows(rows):
        sym = next((str(r[k]).upper() for k in _SYM_KEYS if r.get(k)), None)
        if not sym:
            continue
        score = _first(r, _SCORE_KEYS)
        iv, rv, rank = _pct(_first(r, _IV_KEYS)), _pct(_first(r, _RV_KEYS)), _first(r, _RANK_KEYS)
        bits = []
        if iv is not None and rv is not None:
            bits.append(f"options price a {iv:.0f}% move against {rv:.0f}% realized")
        elif iv is not None:
            bits.append(f"implied volatility {iv:.0f}%")
        if score is not None:
            bits.append(f"anomaly score {score:.2f}")
        head = ("Options unusually EXPENSIVE: a seller's candidate" if rich
                else "Options unusually CHEAP: a poor place to sell premium")
        out.append({"symbol": sym, "score": score, "iv": iv, "rv": rv, "rank": rank,
                    "watchlist": sym in watch, "direction": direction,
                    "text": head + (f" ({'; '.join(bits)})." if bits else ".")})
    if any(x["score"] is not None for x in out):
        out.sort(key=lambda x: -(abs(x["score"]) if x["score"] is not None else -1))
    return out[:ODD_N]


def build(uw, watchlist: Iterable[str] = ()) -> dict:
    """All three lists. Never raises; a list UW could not answer is named."""
    watch = {str(s).upper().strip() for s in (watchlist or []) if s}
    missing = []

    def get(name, *a, **k):
        fn = getattr(uw, name, None)
        try:
            v = fn(*a, **k) if fn else None
        except Exception:  # noqa: BLE001
            v = None
        if v is None:
            missing.append(k.get("direction", name) if name == "vol_anomaly_top" else name)
        return v

    hot = get("hottest_chains", limit=100, order="premium")
    rich = get("vol_anomaly_top", direction="short_vol", limit=50)
    cheap = get("vol_anomaly_top", direction="long_vol", limit=50)
    return {"date": date.today().isoformat(),
            "hottest": hottest(hot, watch),
            "rich": oddities(rich, "short_vol", watch),
            "cheap": oddities(cheap, "long_vol", watch),
            "missing": missing}
