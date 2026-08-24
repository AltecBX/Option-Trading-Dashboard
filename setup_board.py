"""setup_board.py — which names are worth selling today, and which are not.

Pure functions only: no network, no disk, no clock. `options_dashboard`
gathers the rows (from `edge_scan`, which already computes implied
volatility, expected realized volatility and the premium's richness) and
calls in here to decide what survives and in what order.

═══════════════════════════════════════════════════════════════════════════
WHY THIS IS NOT THE PREMIUM EDGE SCAN
═══════════════════════════════════════════════════════════════════════════

The Premium Edge scan ranks its stage-1 candidates by realized-volatility
rank PLUS a bonus for earnings inside the next week — a deliberate choice,
because an earnings report is where the richest premium lives and a trader
who closes before the print can harvest it.

This board is for the opposite discipline: selling and HOLDING TO EXPIRY.
A seller who sits through the report does not harvest that premium, they
underwrite it. So earnings inside the option's life is an EXCLUSION here,
not a bonus. Same data, opposite sign, because it is a different trade.

═══════════════════════════════════════════════════════════════════════════
WHAT THIS BOARD CLAIMS, AND WHAT IT DOES NOT
═══════════════════════════════════════════════════════════════════════════

It does NOT claim to find a better strike. Measurement on ten years of
large-cap history says the 15-22 delta band already sits at roughly the
85% win rate it targets, so there is no gap between "the distance the
evidence supports" and "the strike the market quotes" to harvest. More
premium, at that horizon, means a lower win rate — which is a decision
about risk appetite, not an edge.

What it does claim is narrower and better supported: on any given day some
names pay far more than their own history says that risk is worth, and most
pay about fair. Selling the rich ones at the SAME delta, and skipping the
rest, raises income without touching the win rate. That is a selection
claim, not a pricing claim, and it does not require the market to be wrong
about anything this app cannot measure.

═══════════════════════════════════════════════════════════════════════════
THE TWO STAGES, AND WHY
═══════════════════════════════════════════════════════════════════════════

Stage 1 is free: it reads the watchlist board, which is already computed,
and ranks the WHOLE universe on realized-volatility rank. That is only a
proxy for "premium is probably rich" — the real measurement needs an option
chain, and a chain costs a network round trip per symbol. Stage 1 exists to
decide which handful of symbols are worth spending those round trips on.

Stage 2 is the real measurement, on the few that survive.

The board therefore ranks every name it can see, but only MEASURES the top
few. It says so in the payload rather than implying it looked at everything.
"""

from __future__ import annotations

SETUP_BOARD_VERSION = "setup-board-1.0.0"

# ── stage 1: the free screen ──────────────────────────────────────────────
MIN_PRICE = 20.0             # penny-ish names have unsellable option chains
MIN_MARKET_CAP = 5e9
MIN_AVG_VOLUME = 1_000_000
STAGE1_LIMIT = 25            # how many symbols earn a chain fetch

# ── stage 2: the gates ────────────────────────────────────────────────────
# The option must pay at least this much more than the stock actually
# realizes. At 1.0 the premium is merely fair, and a fair price is not a
# reason to take assignment risk.
MIN_VRP_RATIO = 1.05
# Below this many past observations, "today is in the 90th percentile of
# this stock's own premium" is a coincidence with a decimal point.
MIN_HIST_N = 30
# An earnings report this many days beyond expiry still counts as inside:
# the date drifts, and an unconfirmed date drifts more.
EARNINGS_BUFFER_DAYS = 2


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


# ══════════════════════════════════════════════════════════════════════════
# STAGE 1 — THE FREE SCREEN OVER THE WHOLE UNIVERSE
# ══════════════════════════════════════════════════════════════════════════

def stage1(rows, horizon_days: float = 45.0, limit: int = STAGE1_LIMIT,
           min_price: float = MIN_PRICE, min_market_cap: float = MIN_MARKET_CAP,
           min_avg_volume: float = MIN_AVG_VOLUME) -> dict:
    """Rank the whole watchlist on what a hold-to-expiry seller wants.

    Returns {"candidates": [...], "considered": n, "dropped": {why: n},
             "basis": str}. The ranking figure is realized-volatility rank,
    which is a PROXY: it says premium is probably rich, not that it is.
    Stage 2 measures whether it actually is.
    """
    ranked, dropped = [], {}

    def drop(why):
        dropped[why] = dropped.get(why, 0) + 1

    considered = 0
    for r in (rows or []):
        sym = (r.get("ticker") or r.get("symbol") or "").upper().strip()
        if not sym:
            continue
        considered += 1
        last = _num(r.get("last"))
        if last is None or last < min_price:
            drop(f"under ${min_price:.0f} a share"); continue
        mcap = _num(r.get("market_cap"))
        if mcap is not None and mcap < min_market_cap:
            drop("too small to have a usable option chain"); continue
        avol = _num(r.get("avg_volume"))
        if avol is not None and avol < min_avg_volume:
            drop("too thinly traded"); continue
        # The inversion: a report inside the option's life is the risk a
        # hold-to-expiry seller underwrites rather than harvests.
        dte_e = _num(r.get("days_to_earnings"))
        if dte_e is not None and 0 <= dte_e <= (horizon_days + EARNINGS_BUFFER_DAYS):
            drop("earnings inside the option's life"); continue
        rank = _num(r.get("rvol_rank"))
        if rank is None:
            drop("no volatility history yet"); continue
        ranked.append({"symbol": sym, "stage1_score": round(rank, 1),
                       "rvol": _num(r.get("rvol")),
                       "rvol_rank": round(rank, 1),
                       "days_to_earnings": dte_e})
    ranked.sort(key=lambda x: -x["stage1_score"])
    return {
        "candidates": ranked[:max(0, int(limit))],
        "considered": considered,
        "ranked": len(ranked),
        "dropped": dropped,
        "limit": int(limit),
        "basis": ("Ranked on 20-day realized volatility versus this stock's own "
                  "year — a free proxy for premium being rich. Whether it "
                  "actually is rich needs the option chain, which is measured "
                  "for the top names only."),
    }


# ══════════════════════════════════════════════════════════════════════════
# STAGE 2 — THE GATES, ON REAL CHAIN DATA
# ══════════════════════════════════════════════════════════════════════════

def gate(row: dict, horizon_days: float | None = None,
         min_vrp_ratio: float = MIN_VRP_RATIO) -> dict:
    """Should this name be sold today at all?

    Every rejection carries its reason in words, because a board that
    silently drops names teaches nothing about why the list is short.
    """
    reasons = []
    if not row.get("data_ok", True):
        reasons.append("The data needed to price it did not come back.")
    if row.get("liquidity_ok") is False or row.get("liquidity_poor") is True:
        reasons.append("The bid/ask spread or open interest fails the "
                       "liquidity floor — the exit costs more than the edge.")
    dte = _num(row.get("best_dte")) if horizon_days is None else horizon_days
    if row.get("earnings_inside"):
        reasons.append("Earnings falls inside the option's life. Holding to "
                       "expiry through a report underwrites the event rather "
                       "than harvesting it.")
    ev = _num(row.get("best_ev"))
    if ev is not None and ev <= 0:
        reasons.append(f"Expected value is {ev:+.2f} per share at the "
                       f"volatility this stock actually realizes — a high "
                       f"chance of keeping the credit at a price that loses "
                       f"money over repetition.")
    ratio = _num(row.get("vrp_ratio"))
    if ratio is None:
        reasons.append("No reading on how the premium compares to what this "
                       "stock realizes.")
    elif ratio < min_vrp_ratio:
        reasons.append(f"The option pays {ratio:.2f}x what this stock "
                       f"actually realizes — under the {min_vrp_ratio:.2f}x "
                       f"floor. Fair pay is not a reason to take assignment "
                       f"risk.")
    if str(row.get("danger") or "").upper() in ("HIGH", "EXTREME"):
        reasons.append(f"The danger model rates this {row.get('danger')}.")
    return {"ok": not reasons, "reasons": reasons, "dte": dte}


def richness(row: dict, min_hist_n: int = MIN_HIST_N) -> dict:
    """How rich today's premium is, and on what basis.

    Preferred: where today's premium sits in this stock's OWN history of
    premiums, which is self-normalising — an 8-point premium means something
    different on a 15% stock than on a 60% one. That needs enough past
    observations to mean anything; below the floor it falls back to the raw
    ratio and says so, rather than quoting a percentile from six readings.
    """
    pct = _num(row.get("vrp_percentile"))
    n = int(_num(row.get("hist_n")) or 0)
    ratio = _num(row.get("vrp_ratio"))
    points = _num(row.get("vrp_points"))
    if pct is not None and n >= min_hist_n:
        return {"value": pct, "basis": "percentile", "n": n,
                "ratio": ratio, "points": points,
                "why": (f"Today's premium is richer than {pct:.0f}% of the "
                        f"{n} readings on file for this stock.")}
    if ratio is None:
        return {"value": None, "basis": "none", "n": n,
                "ratio": None, "points": points,
                "why": "No usable reading on the premium's richness."}
    # Map the ratio onto the same 0-100 axis so one sort order works, while
    # the basis field keeps the two kinds of number distinguishable.
    scaled = max(0.0, min(100.0, (ratio - 1.0) * 200.0))
    return {"value": round(scaled, 1), "basis": "ratio", "n": n,
            "ratio": ratio, "points": points,
            "why": (f"The option pays {ratio:.2f}x what this stock actually "
                    f"realizes"
                    + (f", {points:+.1f} volatility points."
                       if points is not None else ".")
                    + (f" Only {n} past readings on file, too few for a "
                       f"percentile." if n else
                       " No past readings on file yet for a percentile."))}


def build(rows, horizon_days: float | None = None, limit: int = 10,
          min_vrp_ratio: float = MIN_VRP_RATIO,
          min_hist_n: int = MIN_HIST_N) -> dict:
    """The board: what to sell today, in order, and what was skipped and why."""
    keep, skipped = [], []
    for r in (rows or []):
        sym = (r.get("symbol") or r.get("ticker") or "").upper().strip()
        if not sym:
            continue
        g = gate(r, horizon_days=horizon_days, min_vrp_ratio=min_vrp_ratio)
        rich = richness(r, min_hist_n=min_hist_n)
        entry = {
            "symbol": sym,
            "spot": _num(r.get("spot")),
            "richness": rich["value"], "richness_basis": rich["basis"],
            "richness_why": rich["why"],
            "vrp_ratio": rich["ratio"], "vrp_points": rich["points"],
            "hist_n": rich["n"],
            "iv30": _num(r.get("iv30")), "erv30": _num(r.get("erv30")),
            "expiration": r.get("best_expiry"), "dte": _num(r.get("best_dte")),
            "strike": _num(r.get("best_strike")),
            "delta": _num(r.get("best_delta")),
            "credit": _num(r.get("best_credit")),
            "roc_pct": _num(r.get("best_roc_pct")),
            "ev_per_share": _num(r.get("best_ev")),
            "kind": r.get("best_kind"),
            "danger": r.get("danger"),
            "earnings_date": r.get("earnings_date"),
            "premium_class": r.get("premium_class"),
        }
        if g["ok"] and rich["value"] is not None:
            keep.append(entry)
        else:
            skipped.append({**entry, "why": g["reasons"] or
                            ["No usable reading on the premium's richness."]})
    # Richest first. Ties break on return on collateral, so of two equally
    # rich names the one that pays more for the same money wins.
    keep.sort(key=lambda x: (-(x["richness"] or 0.0), -(x["roc_pct"] or 0.0)))
    return {
        "rows": keep[:max(0, int(limit))],
        "shown": min(len(keep), max(0, int(limit))),
        "qualified": len(keep),
        "skipped": skipped,
        "version": SETUP_BOARD_VERSION,
        "basis": ("Ranked by how rich today's premium is against what this "
                  "stock itself realizes — not by how much it pays in "
                  "dollars, which mostly tracks how volatile the stock is."),
    }
