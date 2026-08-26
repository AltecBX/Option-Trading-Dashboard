"""setup_scan.py — the half of the Best Setup feature with a clock, a
network and a cache.

`setup_engine.py` is the decision layer and knows nothing about the world.
This module gathers what it decides on, from the modules that already own
each number:

    bars, chain, spot        schwab_client, through the same getter
                             every other tab uses
    ExpectedRV, IV30,        edge_scan.forecast_for + premium_edge —
    term, skew, VRP          the validated per-ticker model choice
    contract economics       premium_edge.contract_economics, over a WIDER
                             delta band than the Premium Edge tab scans,
                             because the ceiling can open past its fixed
                             [0.15, 0.35]
    streak                   watchlist_table._streak_metrics
    swing maturity           swing_projection.project directly — NOT
                             swings.analyze, which downloads SPY, QQQ
                             and earnings dates for features this card
                             never renders
    weekly range location    computed here from the same daily bars
    gamma exposure           gex_engine, through the injected chain getter

Nothing is re-implemented. Where a module already answers a question, it is
called; where two modules could answer it, the one that owns it wins.

═══════════════════════════════════════════════════════════════════════════
THE CONDITIONING RULE, FIXED IN ADVANCE
═══════════════════════════════════════════════════════════════════════════

The engine's whole claim rests on a measured rate for bars "in this state",
so what counts as the state cannot be chosen after seeing which answer is
better. That would be picking the winner and calling it evidence.

The rule is therefore fixed, in priority order, and reported in the payload:

  1. A RUN. If the stock is on a run of `min_streak` days or more, the
     state is every past bar that ended a run of at least that length in
     the same direction.
  2. AN OVERSIZED SWING. Otherwise, if the active swing is beyond the size
     this stock's swings normally reach, the state is every past bar whose
     swing had also gone beyond that size.
  3. NOTHING. Otherwise there is no unusual state to measure, the engine
     gets no evidence, and the default delta band stands.

Rule 3 is the common case and that is correct: if nothing about today is
unusual, there is no reason to think the market's delta is wrong.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import date, datetime, timedelta

import setup_engine as SE

SETUP_SCAN_VERSION = "setup-scan-1.0.0"

try:
    import premium_edge as pe
except Exception as _exc:  # noqa: BLE001
    print(f"[setup_scan] premium_edge unavailable: {_exc}", file=sys.stderr)
    pe = None  # type: ignore
try:
    import edge_scan as _edge
except Exception as _exc:  # noqa: BLE001
    print(f"[setup_scan] edge_scan unavailable: {_exc}", file=sys.stderr)
    _edge = None  # type: ignore
try:
    import gex_engine as _gex
except Exception as _exc:  # noqa: BLE001
    print(f"[setup_scan] gex_engine unavailable: {_exc}", file=sys.stderr)
    _gex = None  # type: ignore
try:
    import swing_projection as _sproj
    from swings import _zigzag
except Exception as _exc:  # noqa: BLE001
    print(f"[setup_scan] swing projection unavailable: {_exc}", file=sys.stderr)
    _sproj = None  # type: ignore
    _zigzag = None  # type: ignore
try:
    from watchlist_table import _streak_metrics as _streaks
except Exception as _exc:  # noqa: BLE001
    print(f"[setup_scan] streak metrics unavailable: {_exc}", file=sys.stderr)
    _streaks = None  # type: ignore

# ── policy ────────────────────────────────────────────────────────────────
# The distance ladder the touch curve is measured on, in percent. Percent
# rather than dollars so a $6 stock and a $600 stock are asked the same
# question; the engine interpolates between rungs.
DISTANCES = (1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0)
MIN_STREAK = 3                 # shorter than this is not a run
# The hard floor for producing ANY recommendation. It is not the floor for
# the measured widening — that has its own, much higher bar (MIN_MEASURED_N)
# and refuses on its own. Gating the whole card at 400 turned a recently
# listed name like SNDK, with 383 bars and a perfectly good conservative
# trade available, into a blank error.
MIN_BARS = 260
INDEX_STRIKES = 10             # cheap call, just to list expirations
WINDOW_STRIKES = 40            # enough ladder around the money to choose from
WINDOW_MAX_EXPIRATIONS = 4     # bounds the payload; _pick_expiry ranks these
SIDE_DELTA_SCAN = (0.05, 0.50)  # wider than Premium Edge's fixed band
CACHE_TTL = 90.0

_SCHWAB_GETTER = None
_CHAIN_GETTER = None            # (symbol) -> (chain, source, fetched_at, avail, sel)
_EARNINGS_FN = None             # (symbol) -> {"next": "YYYY-MM-DD"}
_LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, dict]] = {}


def configure(schwab_getter=None, chain_getter=None, earnings_fn=None) -> None:
    global _SCHWAB_GETTER, _CHAIN_GETTER, _EARNINGS_FN
    if schwab_getter is not None:
        _SCHWAB_GETTER = schwab_getter
    if chain_getter is not None:
        _CHAIN_GETTER = chain_getter
    if earnings_fn is not None:
        _EARNINGS_FN = earnings_fn


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


# ══════════════════════════════════════════════════════════════════════════
# MEASUREMENT — the touch curve
# ══════════════════════════════════════════════════════════════════════════

def forward_extremes(highs, lows, days: int):
    """For every bar, the highest high and lowest low of the NEXT `days`
    bars. Computed once so the touch curve costs one pass per distance
    instead of one pass per distance per bar.

    Returns (max_high, min_low), each with None where the full window does
    not exist — an incomplete window is not a miss, and counting it as one
    would flatter every rate at the recent end of the history.
    """
    n = len(highs)
    mx: list = [None] * n
    mn: list = [None] * n
    d = max(1, int(days))
    for i in range(n):
        j0, j1 = i + 1, i + 1 + d
        if j1 > n:
            break
        hi = lo = None
        for j in range(j0, j1):
            h, l = highs[j], lows[j]
            hi = h if hi is None or h > hi else hi
            lo = l if lo is None or l < lo else lo
        mx[i], mn[i] = hi, lo
    return mx, mn


def touch_curve(closes, mx, mn, idx, distances, up: bool) -> dict:
    """Share of the given bars from which price travelled each distance
    within the horizon, measured from the bar's CLOSE.

    `up=True` asks about a rise (the risk to a short call); `up=False` about
    a fall (the risk to a short put). The close on both sides of the
    comparison, so a rate measured from an intraday extreme cannot flatter
    itself against a baseline measured from a close.
    """
    out = {}
    for d in distances:
        hits = tries = 0
        for i in idx:
            if i >= len(closes):
                continue
            ext = mx[i] if up else mn[i]
            if ext is None:
                continue
            c0 = closes[i]
            if not c0 or c0 <= 0:
                continue
            tries += 1
            level = c0 * (1.0 + d / 100.0) if up else c0 * (1.0 - d / 100.0)
            if (ext >= level) if up else (ext <= level):
                hits += 1
        if tries:
            out[d] = {"rate": round(hits / tries * 100.0, 2), "n": tries}
    return out


def _runs(closes):
    """(direction, length) of the run each bar sits at the end of."""
    n = len(closes)
    rdir = [0] * n
    rlen = [0] * n
    for i in range(1, n):
        s = (1 if closes[i] > closes[i - 1]
             else -1 if closes[i] < closes[i - 1] else 0)
        if s == 0:
            continue
        if rdir[i - 1] == s:
            rdir[i], rlen[i] = s, rlen[i - 1] + 1
        else:
            rdir[i], rlen[i] = s, 1
    return rdir, rlen


def conditioning(closes, streak_block: dict | None,
                 swing_block: dict | None) -> dict:
    """Which bars count as "in this state", by the rule fixed above.

    Returns {"kind", "label", "idx", "note"} — `idx` empty when nothing
    about today is unusual, which is the common and correct case.
    """
    sb = streak_block or {}
    sdir = SE.streak_sign(sb.get("streak_dir"))
    scount = int(SE._num(sb.get("streak_count")) or 0)   # noqa: SLF001
    if sdir in (1, -1) and scount >= MIN_STREAK:
        rdir, rlen = _runs(closes)
        idx = [i for i in range(len(closes))
               if rdir[i] == sdir and rlen[i] >= scount]
        return {
            "kind": "streak", "idx": idx,
            "label": (f"{scount} {'up' if sdir == 1 else 'down'} days in a row"),
            "note": (f"Measured across every past bar that ended a run of "
                     f"{scount} or more {'up' if sdir == 1 else 'down'} days."),
        }
    wb = swing_block or {}
    mat = (wb.get("maturity") or {})
    if mat.get("code") == "BEYOND ITS NORMAL SIZE":
        # The reversal engine already decides what "beyond normal" means and
        # already holds the cohort that met it. Re-deriving the rule here
        # would let the two drift apart. `cross_bar_index` is the bar at
        # which each past swing had come as far as this one has now, which
        # is the bar to measure forward from.
        idx = [int(i) for i in ((wb.get("cohort") or {}).get("cross_bar_index") or [])
               if i is not None]
        if idx:
            return {"kind": "swing", "idx": idx,
                    "label": "the swing is beyond its normal size",
                    "note": ("Measured across every past swing of this stock that "
                             "also went beyond its normal size.")}
    return {"kind": None, "idx": [], "label": None,
            "note": ("Nothing about today is unusual for this stock — no run and "
                     "no oversized swing — so there is no special state to "
                     "measure and the default delta band stands.")}


# ══════════════════════════════════════════════════════════════════════════
# WEEKLY RANGE LOCATION
# ══════════════════════════════════════════════════════════════════════════

def weekly_range(dates, highs, lows, closes, weeks: int = 52) -> dict:
    """Where this week's move sits between the worst and best week this
    stock has managed, from the same daily bars everything else uses.

    Grouped Monday to Friday against each week's own opening baseline —
    the same shape the Weekly Option Selling panel uses, computed here so
    the recommendation does not depend on a separate scanner having run.
    """
    if not dates or len(dates) < 30:
        return {}
    buckets: dict = {}
    order = []
    for i, d in enumerate(dates):
        try:
            y, m, dd = str(d)[:10].split("-")
            iso = date(int(y), int(m), int(dd)).isocalendar()
            key = f"{iso[0]:04d}-W{iso[1]:02d}"
        except Exception:
            continue
        if key not in buckets:
            buckets[key] = {"first": i, "last": i, "hi": highs[i], "lo": lows[i]}
            order.append(key)
        else:
            b = buckets[key]
            b["last"] = i
            b["hi"] = max(b["hi"], highs[i])
            b["lo"] = min(b["lo"], lows[i])
    order = order[-max(8, weeks):]
    rets = []
    for key in order:
        b = buckets[key]
        base = closes[b["first"] - 1] if b["first"] > 0 else closes[b["first"]]
        if not base or base <= 0:
            continue
        rets.append({"key": key,
                     "hi_pct": (b["hi"] / base - 1.0) * 100.0,
                     "lo_pct": (b["lo"] / base - 1.0) * 100.0})
    if len(rets) < 8:
        return {}
    cur = rets[-1]
    past = rets[:-1]
    best = max(r["hi_pct"] for r in past)
    worst = min(r["lo_pct"] for r in past)
    span = best - worst
    now_pct = (closes[-1] / (closes[buckets[order[-1]]["first"] - 1]
                             if buckets[order[-1]]["first"] > 0 else closes[-1]) - 1.0) * 100.0
    pos = None if span <= 0 else max(0.0, min(100.0, (now_pct - worst) / span * 100.0))
    return {"pos": round(pos, 1) if pos is not None else None,
            "weeks": len(past), "this_week_pct": round(now_pct, 2),
            "best_week_pct": round(best, 2), "worst_week_pct": round(worst, 2)}


# ══════════════════════════════════════════════════════════════════════════
# THE PAYLOAD
# ══════════════════════════════════════════════════════════════════════════

def _swing_read(dates, highs, lows, closes) -> dict:
    """The active swing's direction, maturity and cohort — from the
    projection engine directly, not through `swings.analyze`.

    `swings.analyze` is the Patterns tab's entry point and does more than
    this needs: even when handed bars it still downloads SPY and QQQ for
    relative strength and pulls earnings dates from yfinance. Those are two
    network round-trips this card never renders, on the one path that is
    supposed to answer instantly. Calling `project` directly uses the same
    engine on the same bars with no I/O at all.
    """
    if _sproj is None or _zigzag is None or len(closes) < 60:
        return {}
    try:
        zz = _sproj.resolve_zigzag_pct(closes, cfg=None, sensitivity="standard")
        pct = float(zz.get("pct") or 0.12)
        pivots = _zigzag(highs, lows, pct)
        out = _sproj.project(pivots, dates, highs, lows, closes,
                             zigzag_pct=pct * 100.0) or {}
        return out if out.get("ok") else {}
    except Exception:  # noqa: BLE001
        return {}


def _ladder(chain, exp, side, spot, t_years, erv, cfg, rate):
    rows = ((chain.get("chains") or {}).get(exp) or {}).get(
        "calls" if side == "call" else "puts") or []
    out = []
    lo, hi = SIDE_DELTA_SCAN
    for r in rows:
        m = pe.contract_economics(r, spot, side, t_years, erv, cfg, rate)
        if m is None:
            continue
        d = _num(m.get("delta"))
        if d is None or not (lo <= abs(d) <= hi):
            continue
        ok, why = pe.liquidity_gate(m, cfg)
        m["liquidity_ok"], m["liquidity_notes"] = ok, why
        out.append(m)
    return out


def _window_dtes(cfg) -> tuple[float, float]:
    """The seller's expiration window, from the same config premium_edge
    picks with. Kept in one place so the fetch and the pick cannot disagree
    about which days are eligible."""
    st = ((cfg or {}).get("select") or {})
    return float(st.get("min_dte", 7)), float(st.get("max_dte", 60))


def _window_expiries(chain, now: date, lo: float, hi: float) -> list:
    """Expirations in this chain that a seller could actually use."""
    out = []
    for exp in ((chain or {}).get("chains") or {}):
        d = pe._expiry_dte(exp, now)                          # noqa: SLF001
        if lo <= d <= hi:
            out.append(exp)
    return sorted(out)


def _broker_note(sc) -> str | None:
    """Why the broker is not answering, in Jerry's words, when it is not.

    A failed chain fetch and a symbol with no options look identical from
    here, and the difference matters enormously: one is fixed by
    re-authorizing, the other by picking a different stock. Schwab refresh
    tokens expire after seven days, so "it worked yesterday" is the normal
    case rather than a surprise. Say which it is.
    """
    try:
        st = sc.status() if hasattr(sc, "status") else None
    except Exception:  # noqa: BLE001
        return None
    if not st:
        return None
    if not st.get("configured"):
        return {
            "missing_credentials": ("The broker credentials are not set, so "
                                    "no option chain can be fetched. Add them "
                                    "under Manage."),
            "no_token_file": ("The broker has never been authorized on this "
                              "deployment. Connect it under Manage."),
            "module_unavailable": "The broker integration did not load.",
        }.get(st.get("reason"),
              "The broker is not connected. Connect it under Manage.")
    if st.get("needs_reauth") or st.get("auth_error"):
        return ("The broker rejected the last request and needs "
                "re-authorizing under Manage — this is what an expired "
                "sign-in looks like, not a problem with this symbol.")
    days = st.get("refresh_remaining_days")
    if days is not None and days <= 0:
        return ("The broker sign-in has expired — they last seven days — so "
                "re-authorize under Manage. Nothing is wrong with this "
                "symbol.")
    if days is not None and days < 1:
        return (f"The broker sign-in expires in about "
                f"{days * 24:.0f} hours; re-authorize under Manage before it "
                f"does.")
    # Signed in and still not answering. This is the state the sidebar badge
    # is already reporting as a fallback data source, and it is the one that
    # used to come out as "no option chain came back for CIEN" — a sentence
    # about Ciena, when nothing about Ciena was involved. Nothing to fix and
    # nothing to re-authorize: the backup price source carries no option
    # chains, so the cards that need one have to wait.
    if st.get("serving") is False:
        n = st.get("consecutive_failures") or 0
        return ("Your broker is signed in but is not answering right now"
                + (f" ({n} requests in a row went unanswered)" if n > 1 else "")
                + ". The app has fallen back to its backup price source, "
                "which carries no option chains. This usually clears on its "
                "own — nothing is wrong with this symbol or your sign-in.")
    return None


def _list_expirations(sc, symbol: str) -> list:
    """Every expiration the underlying lists, from one narrow-ladder call.

    Ten strikes keeps this cheap no matter how deep the listing is — the
    same trick the Gamma Exposure tab uses, for the same reason.
    """
    try:
        idx = sc.get_option_chain(symbol, strike_count=INDEX_STRIKES)
    except Exception:  # noqa: BLE001
        return []
    if not idx:
        return []
    return list(idx.get("expirations") or sorted((idx.get("chains") or {}).keys()))


def _window_chain(sc, symbol: str, now: date, lo: float, hi: float):
    """(chain, every expiration the symbol lists, why-it-failed) for the
    selling window.

    The third value exists because the three ways this returns nothing are
    NOT the same fact, and collapsing them is how "TSLA lists 22
    expirations but none between 7 and 60 days out" ended up on screen
    while the list underneath it plainly showed one at 8 days. That was a
    throttled fetch wearing the wrong message.


    Two steps, because one is not enough. Asking Schwab for a 55-day RANGE
    at sixty strikes is roughly fourteen hundred contracts on a name like
    AAPL, which runs past the client's fifteen-second timeout and comes
    back as None — indistinguishable, from the caller, from "this symbol
    has no options". So: list the expirations cheaply, choose a handful
    inside the window, then fetch only those.
    """
    avail = _list_expirations(sc, symbol)
    if not avail:
        return None, [], "no_listing"
    wanted = [e for e in sorted(avail)
              if lo <= pe._expiry_dte(e, now) <= hi]                # noqa: SLF001
    if not wanted:
        return None, avail, "no_window"
    # CONTIGUOUS, not spread. The fetch is a date RANGE, and Schwab fills a
    # range with every expiration between the ends — so picking four dates
    # spread across the window still asks for the whole window and times out
    # exactly like the request this replaced. A contiguous block from the
    # near end of the window is bounded by construction, and the nearest
    # expirations inside it are the liquid ones anyway.
    sel = wanted[:WINDOW_MAX_EXPIRATIONS]
    try:
        chain = sc.get_option_chain(symbol, expiration=sel[0], to_date=sel[-1],
                                    strike_count=WINDOW_STRIKES)
    except Exception:  # noqa: BLE001
        return None, avail, "fetch_failed"
    if not chain or not (chain.get("chains") or {}):
        return None, avail, "fetch_failed"
    # A date range can return expirations between the ends that were not
    # asked for; keep only the selection so the payload stays bounded.
    keep = set(sel)
    chain["chains"] = {e: v for e, v in (chain.get("chains") or {}).items()
                       if e in keep}
    chain["expirations"] = sorted(chain["chains"].keys())
    if not chain["chains"]:
        return None, avail, "fetch_failed"
    return chain, avail, None


def _no_trade_reason(sides: dict) -> str:
    """Why nothing was recommended, in the sides' own words."""
    parts = []
    for side in ("call", "put"):
        d = sides.get(side) or {}
        r = (d.get("reason") or "").strip()
        if r:
            parts.append(f"{'Calls' if side == 'call' else 'Puts'}: {r}")
    if not parts:
        return "Neither side produced a contract the evidence supports."
    return " ".join(parts)


def analyze(symbol: str, now: date | None = None) -> dict:
    """The whole Best Setup answer for one symbol."""
    symbol = (symbol or "").upper().strip()
    if pe is None or _edge is None:
        return {"ok": False, "symbol": symbol,
                "error": "The Premium Edge engine is not available."}
    sc = _SCHWAB_GETTER() if _SCHWAB_GETTER else None
    if sc is None:
        return {"ok": False, "symbol": symbol,
                "error": ("The broker is not connected, so there is no option "
                          "chain to build a recommendation from. Connect it "
                          "under Manage.")}
    now = now or datetime.now().date()
    cfg = _edge._cfg()                                        # noqa: SLF001
    bars = sc.get_price_history(symbol, days=1400)
    if not bars:
        # None and [] mean the request failed or was throttled — NOT that
        # this stock has no history. "Only 0 daily bars on file" reads as a
        # fact about TSLA and sends the reader looking in the wrong place.
        note = _broker_note(sc)
        return {"ok": False, "symbol": symbol,
                "error": (note if note else
                          f"No price history came back for {symbol}. The "
                          f"request failed or was throttled rather than the "
                          f"symbol being empty."),
                # Nothing about the symbol caused this, so asking again is
                # the whole fix — the card retries rather than parking a
                # button in front of the reader.
                "retryable": True,
                "broker_note": note}
    if len(bars) < MIN_BARS:
        return {"ok": False, "symbol": symbol,
                "error": (f"{symbol} has only {len(bars)} daily bars of "
                          f"history, fewer than the {MIN_BARS} this needs. "
                          f"That usually means a recent listing or spin-off.")}
    D = [b["date"][:10] for b in bars]
    Hh = [_num(b.get("high")) for b in bars]
    Ll = [_num(b.get("low")) for b in bars]
    Cc = [_num(b.get("close")) for b in bars]
    keep = [i for i in range(len(bars))
            if Hh[i] is not None and Ll[i] is not None and Cc[i] is not None]
    D = [D[i] for i in keep]; Hh = [Hh[i] for i in keep]
    Ll = [Ll[i] for i in keep]; Cc = [Cc[i] for i in keep]

    # The chain has to cover the SELLING WINDOW, not merely exist.
    #
    # The gamma-exposure getter returns the nearest expiration, because that
    # is where intraday dealer gamma lives. For a premium sale that is the
    # wrong chain: the nearest expiration is usually a weekly one to five
    # days out, and every one of them falls outside the 7-60 day window a
    # seller picks from. The old guard only asked whether the chain was
    # non-empty, so it accepted that chain and every symbol then failed with
    # "no expiration inside the selling window". A chain that cannot answer
    # the question is as useless as no chain at all — check for a usable
    # expiration, not for a truthy dict.
    lo_dte, hi_dte = _window_dtes(cfg)
    chain, gsource, fetched_at = None, None, None
    if _CHAIN_GETTER:
        try:
            c, gsource, fetched_at, _avail, _sel = _CHAIN_GETTER(symbol)
            if _window_expiries(c, now, lo_dte, hi_dte):
                chain = c
        except Exception:  # noqa: BLE001
            chain = None
    listed, why = [], None
    if chain is None:
        chain, listed, why = _window_chain(sc, symbol, now, lo_dte, hi_dte)
        gsource, fetched_at = "broker", None
    if not chain and why == "fetch_failed":
        # The symbol lists options in the window; the request for them
        # failed or was throttled. Say THAT — do not tell the reader the
        # window is empty when it demonstrably is not.
        note = _broker_note(sc)
        return {"ok": False, "symbol": symbol,
                "error": (note if note else
                          f"The option chain request for {symbol} failed or "
                          f"was throttled. The broker is connected and the "
                          f"symbol lists {len(listed)} expirations."),
                "retryable": True,
                "broker_note": note}
    if not chain and listed:
        # The symbol lists options, just none inside the selling window.
        # That is a different fact from "no chain came back at all", and the
        # reader has to be able to tell them apart. _window_chain already
        # knows every expiration this symbol lists, so naming them costs
        # nothing — no second fetch, and none of the unbounded requests that
        # earlier versions of this fallback made.
        near = ", ".join(f"{e[:10]} ({pe._expiry_dte(e, now):.0f} days)"   # noqa: SLF001
                         for e in sorted(listed)[:4])
        return {"ok": False, "symbol": symbol,
                "error": (f"{symbol} lists {len(listed)} expirations but none "
                          f"between {lo_dte:.0f} and {hi_dte:.0f} days out, "
                          f"which is the window a seller picks from. "
                          f"Nearest: {near}.")}
    if not chain or not (chain.get("chains") or {}):
        # Do not blame the symbol for what is usually a connection problem.
        # When the broker is answering normally, an empty listing really can
        # mean the symbol has no options — but when it is NOT answering, the
        # emptiness says nothing at all about the symbol, and offering the
        # reader "either ... or" makes them weigh a possibility we already
        # know to be false. Retry only in the case that can change.
        note = _broker_note(sc)
        return {"ok": False, "symbol": symbol,
                "error": (note if note else
                          f"No option chain came back for {symbol}. The "
                          f"broker is connected, so either this symbol has "
                          f"no listed options or the request timed out — "
                          f"try again."),
                "retryable": bool(note),
                "broker_note": note}
    spot = _num((chain.get("underlying") or {}).get("last")) or Cc[-1]

    earn_next = None
    try:
        if _EARNINGS_FN:
            earn_next = (_EARNINGS_FN(symbol) or {}).get("next")
    except Exception:  # noqa: BLE001
        earn_next = None
    earn_days = None
    if earn_next:
        try:
            y, m, d2 = str(earn_next)[:10].split("-")
            earn_days = (date(int(y), int(m), int(d2)) - now).days
        except Exception:  # noqa: BLE001
            earn_days = None

    erv_pack = _edge.forecast_for(symbol, bars, cfg, now.isoformat(),
                                  bool(earn_days is not None and 0 <= earn_days <= 30),
                                  None, 0)
    if erv_pack is None:
        return {"ok": False, "symbol": symbol,
                "error": "Not enough history to forecast the volatility this "
                         "stock actually realizes."}
    erv = _num(erv_pack.get("erv30")) or _num(erv_pack.get("erv"))
    is_open = _edge._market_open()                            # noqa: SLF001
    iv = pe.iv30(chain, now, cfg, market_open=is_open)
    term = pe.term_structure(chain, now, cfg, earnings_date=earn_next,
                             market_open=is_open)
    sk = pe.skew(chain, now, cfg, market_open=is_open)
    vrp = pe.vrp_block(iv["iv30"], erv_pack) if iv else None

    exp = pe._pick_expiry(chain, now, cfg, term)              # noqa: SLF001
    if not exp:
        # Say what the chain DID contain. "No expiration in the window" with
        # nothing else is a dead end for the reader: it looks identical
        # whether the symbol lists no medium-dated options at all or the
        # fetch simply asked for the wrong dates.
        got = sorted((chain.get("chains") or {}).keys())
        near = ", ".join(f"{e[:10]} ({pe._expiry_dte(e, now):.0f} days)"  # noqa: SLF001
                         for e in got[:4]) or "none at all"
        return {"ok": False, "symbol": symbol,
                "error": (f"No expiration between {lo_dte:.0f} and {hi_dte:.0f} "
                          f"days out on this chain. It listed {len(got)} "
                          f"expiration{'' if len(got) == 1 else 's'}: {near}"
                          + ("…" if len(got) > 4 else "") + ".")}
    dte = pe._expiry_dte(exp, now)                            # noqa: SLF001
    t_years = max(1e-6, dte / 365.0)
    rate = 0.04

    # ── the layers ───────────────────────────────────────────────────────
    streak_block = _streaks(Cc) if _streaks else {}
    swing_block = _swing_read(D, Hh, Ll, Cc)
    range_block = weekly_range(D, Hh, Ll, Cc)
    bias = SE.directional_bias(range_block, streak_block, swing_block)

    # ── the measurement ──────────────────────────────────────────────────
    cond = conditioning(Cc, streak_block, swing_block)
    horizon = max(1, int(round(dte)))
    mx, mn = forward_extremes(Hh, Ll, horizon)
    all_idx = [i for i in range(len(Cc)) if mx[i] is not None]
    measured_by_side = {}
    for side, up in (("call", True), ("put", False)):
        c_curve = touch_curve(Cc, mx, mn, cond["idx"], DISTANCES, up) if cond["idx"] else {}
        b_curve = touch_curve(Cc, mx, mn, all_idx, DISTANCES, up)
        measured_by_side[side] = SE.measured_touch(c_curve, b_curve)

    gex_block = None
    if _gex is not None and chain:
        try:
            gex_block = _gex.build(chain, [exp], now, spot=spot)
        except Exception:  # noqa: BLE001
            gex_block = None

    iv_block = {
        "iv30": (iv or {}).get("iv30"), "erv30": erv,
        "vrp_points": (vrp or {}).get("vrp_points"),
        "vrp_ratio": (vrp or {}).get("vrp_ratio"),
        "term_shape": (term or {}).get("shape"),
        "skew": (sk or {}).get("skew_points"),
    }

    # ── one recommendation per side, then the better of the two ──────────
    out_sides = {}
    for side in ("call", "put"):
        # The second opinion, asked at whatever distance the measurement
        # ends up solving for: a driftless lognormal at ExpectedRV, which is
        # the same model Premium Edge already prices every candidate with.
        # The band only widens where measurement and model agree.
        def model_touch(dist_pct, _up=(side == "call")):
            k = spot * (1 + (dist_pct if _up else -dist_pct) / 100.0)
            p = pe.touch_prob(spot, k, erv, t_years)
            return None if p is None else p * 100.0

        ladder = _ladder(chain, exp, side, spot, t_years, erv, cfg, rate)
        ceiling = SE.delta_ceiling(measured_by_side[side], model_touch_pct=model_touch)
        g_probe = SE.gex_context(gex_block, spot,
                                 (spot * (1 + (SE._num(ceiling.get("min_distance_pct")) or 5) / 100.0)
                                  if side == "call" else
                                  spot * (1 - (SE._num(ceiling.get("min_distance_pct")) or 5) / 100.0)),
                                 side)
        ceiling = SE.apply_gex_to_ceiling(ceiling, g_probe)
        if ceiling.get("vetoed"):
            out_sides[side] = {"ok": False, "symbol": symbol, "side": side,
                               "vetoed": True,
                               "reason": ceiling.get("veto_note"),
                               "ceiling": ceiling}
            continue
        out_sides[side] = SE.recommend(
            symbol, spot, side, exp, dte, ladder, bias, ceiling,
            gex_block=gex_block, iv_block=iv_block, earnings_in_days=earn_days)

    # Prefer the side the layers lean toward; when nothing leans, take the
    # better-scoring of the two and let confidence carry the uncertainty.
    lean_side = ("call" if bias.get("lean") == "fade_up"
                 else "put" if bias.get("lean") == "fade_down" else None)
    ranked = [s for s in ("call", "put") if out_sides[s].get("ok")]
    if lean_side and out_sides[lean_side].get("ok"):
        chosen = lean_side
    elif ranked:
        chosen = max(ranked,
                     key=lambda s: (out_sides[s].get("scoring") or {}).get("score", -1e9))
    else:
        chosen = None

    return {
        "ok": chosen is not None,
        "symbol": symbol, "spot": round(spot, 2) if spot else None,
        "as_of": datetime.now().replace(microsecond=0).isoformat(),
        "expiration": exp, "dte": round(dte, 1),
        "best": out_sides.get(chosen) if chosen else None,
        "chosen_side": chosen,
        "alternative": (out_sides.get("put" if chosen == "call" else "call")
                        if chosen else None),
        "sides": out_sides,
        "bias": bias,
        "conditioning": {k: v for k, v in cond.items() if k != "idx"},
        "conditioned_bars": len(cond["idx"]),
        "measured": {s: measured_by_side[s] for s in measured_by_side},
        "range": range_block, "streak": streak_block,
        "swing": {"direction": swing_block.get("direction"),
                  "maturity": swing_block.get("maturity"),
                  "cohort_n": (swing_block.get("cohort") or {}).get("n")},
        "iv": iv_block,
        "gex_source": gsource, "gex_fetched_at": fetched_at,
        "earnings_next": earn_next, "earnings_in_days": earn_days,
        "bars": len(Cc),
        # When nothing is recommended, WHY nothing is the whole answer, and
        # each side already knows its own reason. Collapsing both into
        # "neither side produced a contract" throws away the only useful
        # part of a no-trade day.
        "reason": (None if chosen else _no_trade_reason(out_sides)),
        "version": SETUP_SCAN_VERSION, "engine": SE.SETUP_VERSION,
    }


def get(symbol: str, force: bool = False) -> dict:
    """Cached `analyze`. The chain and the quote move faster than the
    reasoning does, but not by much at a 90-second TTL."""
    key = (symbol or "").upper().strip()
    now = time.time()
    if not force:
        with _LOCK:
            hit = _CACHE.get(key)
        if hit and (now - hit[0]) < CACHE_TTL:
            return {**hit[1], "cached": True}
    out = analyze(key)
    with _LOCK:
        _CACHE[key] = (now, out)
        if len(_CACHE) > 200:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)
    return out


def invalidate() -> None:
    with _LOCK:
        _CACHE.clear()
