"""playbook.py — Options Playbook: direction × premium → the right structure (v3.66).

The decision rule this encodes is the user's own:

    "My goal is to buy calls and buy puts, or sell calls and sell puts.
     It just depends where the premiums are."

Strong stock + cheap premium  → BUY CALLS  (pay little for the upside)
Weak stock   + cheap premium  → BUY PUTS   (pay little for the downside)
Strong stock + rich premium   → SELL PUTS  (get PAID for the bullish view)
Weak stock   + rich premium   → SELL CALLS (get paid for the bearish view —
                                            defined-risk credit spread first,
                                            naked short calls are unlimited risk)

This module is a PURE JOIN of boards the app already scans and caches —
the Trend board (direction, strength, multi-window returns), the HV-Rank
board (premium rich/cheap via the realized-vol proxy) and the watchlist
table (sector, market cap, earnings dates). It never fetches, never
starts a worker, and never fabricates: a name missing from either scanner
is excluded and counted, not filled in. The HV rank is a realized-vol
PROXY for IV rank (documented in ivrank.py); the real option IV and live
premiums show on the Trade tab per name.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Premium side: rank >= 50 (elevated/rich) → selling structures;
# rank < 50 (normal/cheap) → buying structures. Conviction scales with the
# distance from 50, so a 51 barely leans short-premium while a 90 screams it.
SELL_THRESHOLD = 50.0
EARNINGS_SOON_DAYS = 9
SUMMARY_CAP = 12

QUADRANTS = {
    "buy_calls": {
        "play": "BUY CALLS",
        "structure": "Buy calls / call debit spread",
        "alt": "Bull call spread to cut the debit if IV starts climbing",
        "tone": "bull",
        "logic": "uptrend + premium cheap — pay little for the upside",
    },
    "buy_puts": {
        "play": "BUY PUTS",
        "structure": "Buy puts / put debit spread",
        "alt": "Bear put spread to cut the debit if IV starts climbing",
        "tone": "bear",
        "logic": "downtrend + premium cheap — pay little for the downside",
    },
    "sell_puts": {
        "play": "SELL PUTS",
        "structure": "Sell cash-secured puts / put credit spread",
        "alt": "Put credit spread if you don't want assignment risk",
        "tone": "bull",
        "logic": "uptrend + premium rich — get PAID for the bullish view",
    },
    "sell_calls": {
        "play": "SELL CALLS",
        "structure": "Call credit spread (defined risk)",
        "alt": "Covered calls only if you already own shares — naked calls are unlimited risk",
        "tone": "bear",
        "logic": "downtrend + premium rich — get paid for the bearish view",
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify(direction: str, rank: float) -> str:
    """Quadrant key from trend direction ('up'/'down') and HV rank (0-100)."""
    selling = rank >= SELL_THRESHOLD
    if direction == "up":
        return "sell_puts" if selling else "buy_calls"
    return "sell_calls" if selling else "buy_puts"


def conviction(trend_score: float, rank: float) -> float:
    """0-100 blend: 60% trend strength + 40% premium edge.

    Premium edge = distance of the HV rank from the 50 midline, rescaled to
    0-100 — rank 50 has NO premium edge either way; 0 or 100 is maximal.
    Transparent by design: both inputs show on the row.
    """
    ts = max(0.0, min(100.0, float(trend_score or 0.0)))
    edge = max(0.0, min(100.0, abs(float(rank) - 50.0) * 2.0))
    return round(0.6 * ts + 0.4 * edge, 1)


def _flags(t: dict, iv: dict, wl: dict | None, quad: str) -> tuple[list[str], bool | None]:
    """Human warning/context strings + the filterable earnings_soon boolean."""
    out: list[str] = []
    earnings_soon: bool | None = None
    if wl is not None:
        d = wl.get("days_to_earnings")
        if isinstance(d, (int, float)) and 0 <= d <= EARNINGS_SOON_DAYS:
            earnings_soon = True
            out.append(f"earnings in {int(d)}d — event premium; expect a vol crush after the report")
        elif isinstance(d, (int, float)):
            earnings_soon = False
    selling = quad in ("sell_puts", "sell_calls")
    if iv.get("expanding"):
        out.append("vol expanding — tailwind for bought options; early for sellers"
                   if not selling else
                   "vol still expanding — premium may get richer; selling early risks marks against you")
    elif iv.get("contracting"):
        out.append("vol contracting — theta-friendly for sellers"
                   if selling else
                   "vol contracting — bought options fight IV bleed as well as theta")
    if t.get("overbought"):
        out.append(f"RSI {t.get('rsi')} overbought — chase risk on fresh longs")
    if t.get("oversold"):
        out.append(f"RSI {t.get('rsi')} oversold — bounce risk on fresh shorts")
    if t.get("new_high"):
        out.append("at/near a 52-week high")
    if t.get("new_low"):
        out.append("at/near a 52-week low")
    return out, earnings_soon


def assemble(trend_board: dict, iv_board: dict, wl_board: dict | None = None) -> dict:
    """Join the three cached boards into the Playbook board. Pure function."""
    t_rows = {r.get("ticker"): r for r in (trend_board.get("rows") or []) if r.get("ticker")}
    iv_rows = {r.get("ticker"): r for r in (iv_board.get("rows") or []) if r.get("ticker")}
    wl_rows: dict[str, dict] = {}
    if wl_board:
        for r in (wl_board.get("rows") or []):
            sym = r.get("symbol")
            if sym:
                wl_rows[sym] = r

    both = sorted(set(t_rows) & set(iv_rows))
    rows: list[dict[str, Any]] = []
    for sym in both:
        t, iv = t_rows[sym], iv_rows[sym]
        rank = iv.get("rank")
        direction = t.get("direction")
        if rank is None or direction not in ("up", "down"):
            continue
        wl = wl_rows.get(sym)
        quad = classify(direction, float(rank))
        q = QUADRANTS[quad]
        conv = conviction(t.get("score") or 0.0, float(rank))
        flags, earnings_soon = _flags(t, iv, wl, quad)
        reasons = [
            f"{'Uptrend' if direction == 'up' else 'Downtrend'} (score {round(t.get('score') or 0)}) "
            f"+ premium {iv.get('regime', '?')} (HV rank {round(float(rank))}) → {q['logic']}",
            *[str(x) for x in (t.get("reasons") or [])[:3]],
        ]
        row: dict[str, Any] = {
            "ticker": sym,
            "last": t.get("last"),
            "quadrant": quad,
            "play": q["play"],
            "structure": q["structure"],
            "alt": q["alt"],
            "tone": q["tone"],
            "conviction": conv,
            # trend side
            "direction": direction,
            "trend_score": t.get("score"),
            "rsi": t.get("rsi"),
            "streak": t.get("streak"),
            "from_high": t.get("from_high"),
            "from_low": t.get("from_low"),
            "above_ma200": t.get("above_ma200"),
            "new_high": t.get("new_high"),
            "new_low": t.get("new_low"),
            "overbought": t.get("overbought"),
            "oversold": t.get("oversold"),
            "r1w": t.get("r1w"), "r1m": t.get("r1m"), "r3m": t.get("r3m"),
            "r6m": t.get("r6m"), "r1y": t.get("r1y"),
            # premium side
            "hv": iv.get("hv"),
            "hv_rank": rank,
            "hv_pctile": iv.get("percentile"),
            "premium_regime": iv.get("regime"),
            "expanding": iv.get("expanding"),
            "contracting": iv.get("contracting"),
            # watchlist enrichment (None-safe; absent name → fields stay None)
            "sector": (wl or {}).get("sector"),
            "market_cap": (wl or {}).get("market_cap"),
            "days_to_earnings": (wl or {}).get("days_to_earnings"),
            "next_earnings": (wl or {}).get("next_earnings"),
            "earnings_soon": earnings_soon,
            "reasons": reasons,
            "flags": flags,
        }
        rows.append(row)
    rows.sort(key=lambda r: -r["conviction"])

    summary = {}
    for quad in QUADRANTS:
        summary[quad] = [r for r in rows if r["quadrant"] == quad][:SUMMARY_CAP]

    def _src(board: dict | None) -> dict:
        st = (board or {}).get("status") or {}
        return {
            "last_scan": st.get("last_scan"),
            "scanning": bool(st.get("scanning")),
            "scanned": st.get("scanned"),
            "total": st.get("total"),
            "count": len((board or {}).get("rows") or []),
            "error": st.get("error"),
        }

    sources = {
        "trend": _src(trend_board),
        "ivrank": _src(iv_board),
        "watchlist": _src(wl_board) if wl_board else None,
    }
    missing = []
    if not t_rows:
        missing.append("Trend board is empty — run a scan (Scan both) to rank best/worst performers.")
    if not iv_rows:
        missing.append("HV-Rank board is empty — run a scan (Scan both) to rank premium rich vs cheap.")
    if t_rows and iv_rows and not rows:
        missing.append("The two boards share no tickers yet — rescan so both cover the same universe.")

    return {
        "as_of": _now_iso(),
        "count": len(rows),
        "rows": rows,
        "summary": summary,
        "sources": sources,
        "missing": missing,
        "excluded": {
            "trend_only": len(set(t_rows) - set(iv_rows)),
            "ivrank_only": len(set(iv_rows) - set(t_rows)),
        },
        "note": ("Premium side uses the HV rank — a realized-vol PROXY for IV rank "
                 "(no free source provides a year of option-IV history per name). "
                 "Real option IV, live premiums and greeks show on the Trade tab per name."),
    }
