"""trigger_sell.py — WHEN to sell this week's call: the wick-trigger system.

`weekly_sell.py` answers WHERE (which strike), and `stretch_evidence.py`
measures what a stock does after it reaches its usual high. This module is
the timing decision in between, and it encodes one trader's system exactly:

  1. NOT ON MONDAY. A flat 16-20 delta call sold at Monday's open kept going
     in the money the same day. Selling into the week's open is selling
     before the week has shown where it wants to go.

  2. EVERY STOCK HAS ITS OWN WEEKLY WICK. Measured from the PRIOR FRIDAY'S
     CLOSE, each stock's weekly high lands in its own band: LITE runs about
     7.4% in a typical week, AAPL about 2.5%. The trigger is set near the
     top of that band, the 70th percentile of past weekly highs. A
     percentile and not an average, because one melt-up week drags an
     average up and the trigger with it.

  3. SELL THE TAP. When price reaches the trigger mid-week, the call is sold
     there. The ideal stock spikes mid-week and closes the week flat or
     down: a big top wick on the weekly candle. `wick_score` measures
     exactly that and `rank_tickers` sorts a watchlist by it.

  4. THE DELTA ADAPTS. The more extended the stock is against its recent
     weeks when the trigger hits, the higher the delta sold, up to an
     in-the-money call deep in a wick when a retrace into Friday is
     expected. That aggressiveness must be EARNED: the deep-extension
     bucket of past trigger weeks has to show a harder retrace than the
     shallow one, or the delta is capped at the money.

  5. WAITING COSTS THETA. `decide` prices both choices and splits the
     difference into three legs: the higher strike the trigger unlocks,
     the higher delta it allows, and the premium lost to fewer sessions.
     It recommends waiting only when the wait is worth clearly more. If
     theta dominates two weeks running, the trigger is set too far out for
     that stock.

What this deliberately does NOT claim. Daily bars cannot see the time of
day a trigger was tapped, so the tap day's whole remaining range is
charged to the seller. Option prices here are Black-Scholes at today's
implied volatility, not historical quotes; the live strike and fill come
from `weekly_sell.build_plan`, re-run at the trigger with
`retime_at_trigger`. Nothing here says a stock "must" come back. A weekly
high is the high of one week, not a ceiling.

Pure stdlib plus this repo's `weekly_sell` and `metrics`. No network, no
pandas: every number below can be re-derived from its inputs.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

import metrics
import weekly_sell as ws

SCHEMA = "trigger_sell/v1"

# ── Knobs. Every one of these is a stated policy, not a magic constant. ────
TRIGGER_PCTILE = 0.70      # the trigger sits at the 70th percentile of this
                           # stock's past weekly highs from Friday's close:
                           # near the top of its usual wick, reached in about
                           # three weeks of ten. An average would be dragged
                           # up by the melt-up weeks that never come back.
MIN_WEEKS = 12             # fewer clean weeks than this and the percentile
                           # is a guess with a decimal point: no trigger.
DELTA_NOW = 0.20           # the benchmark it has to beat: an ordinary
                           # 20-delta call sold right now. Also the Thursday
                           # fallback when the trigger never comes.
WAIT_MARGIN = 0.10         # waiting must be worth 10% more than selling now
                           # (EV(wait) > EV(now) x 1.10). A model edge thinner
                           # than that is noise, and selling now is certain.
IV_SCALE_LO = 0.70         # the trigger scales with the volatility regime,
IV_SCALE_HI = 1.50         # sqrt(iv_now / iv_median), clamped to this range:
                           # a calm tape pulls it in, a hot one pushes it out,
                           # but one quote can never move it more than this.

# The adaptive delta, by extension (how far the trigger sits above the
# stock's recent closes, in units of its usual wick spread p90 - p50).
DELTA_FLOOR = 0.30         # extension <= 0: the trigger is not stretched
DELTA_MID = 0.50           # extension ~ 1: one wick-spread above recent closes
DELTA_DEEP = 0.70          # extension 1.5: deep in the wick, at or in the money
DELTA_MAX = 0.85           # never further in than this; past it the "call" is
                           # mostly a short stock position
EXT_DEEP = 1.5             # where DELTA_DEEP begins
MIN_SPREAD = 0.005         # floor on p90 - p50 (as a fraction of anchor), so a
                           # stock whose weeks are nearly identical cannot
                           # divide by almost zero and read as infinitely stretched
PRIOR_CLOSES = 8           # "recent" = the last 8 weekly closes
MIN_PRIOR_CLOSES = 4       # ...and at least this many to measure extension at all
MIN_TERCILE = 3            # each extension bucket needs this many trigger weeks
                           # before it can prove anything
NO_MONDAY_SALE = True      # the rule this system starts from: a flat 20-delta
                           # sold on Monday kept going in the money the same
                           # day, so a Monday decision never says "sell now".
                           # A TAP of the trigger is the system working, so a
                           # Monday tap still sells.
FALLBACK_SESSIONS = 2      # no tap by Thursday: sell the 20-delta then, two
                           # sessions of risk (Thursday and Friday)

SESSIONS_PER_YEAR = ws.SESSIONS_PER_YEAR
_EPS = 1e-9


# ── small helpers ─────────────────────────────────────────────────────────
def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _sigma(iv: Any) -> float | None:
    return metrics.normalize_iv(iv)


def _years(sessions: float) -> float:
    return max(0.0, float(sessions)) / SESSIONS_PER_YEAR


def _rows(bars: Sequence[dict]) -> list[dict]:
    """Daily bars cleaned and sorted: date, close, high, low."""
    out = []
    for b in bars or []:
        d = ws._as_date(b.get("date"))
        c, h, lo = ws._f(b.get("close")), ws._f(b.get("high")), ws._f(b.get("low"))
        if d is None or c is None or h is None or lo is None or c <= 0:
            continue
        out.append({"date": d, "close": c, "high": max(h, c), "low": min(lo, c)})
    out.sort(key=lambda r: r["date"])
    return out


def _last_session(monday: date) -> date:
    """The week's final trading day: Friday, or earlier when it is a holiday."""
    for i in range(4, -1, -1):
        d = monday + timedelta(days=i)
        if ws._is_trading_day(d):
            return d
    return monday + timedelta(days=4)


def _expected_sessions(monday: date) -> int:
    return sum(1 for i in range(5) if ws._is_trading_day(monday + timedelta(days=i)))


# ── 1. the weeks ──────────────────────────────────────────────────────────
def friday_weeks(bars: Sequence[dict]) -> list[dict]:
    """Group daily bars into Monday-Friday weeks, each measured from the
    PRIOR week's last close (Friday's, or Thursday's when Friday was a
    holiday).

    Per week:
      anchor        prior Friday's close
      close         this Friday's close
      high_pct      the week's highest high, as a fraction of anchor
      term_pct      this Friday's close, as a fraction of anchor
      hit_idx       weekday the weekly high first printed (0=Mon .. 4=Fri)
      sessions_left_after_hit   sessions in the week after that bar
      days          [{idx, high_pct, close_pct}] per session, for asking
                    when ANY level was first reached
      prior_median  median of the last PRIOR_CLOSES weekly closes up to and
                    including the anchor (None with fewer than
                    MIN_PRIOR_CLOSES): what "recently" means for extension

    A week with fewer bars than the calendar has sessions is partial (the
    current week, a gap in the data) and is dropped, as is the first week,
    which has no anchor. A bar on a holiday weekday is tolerated.
    """
    rows = _rows(bars)
    if not rows:
        return []
    groups: list[list[dict]] = []
    for r in rows:
        if groups and _monday(groups[-1][0]["date"]) == _monday(r["date"]):
            groups[-1].append(r)
        else:
            groups.append([r])
    out: list[dict] = []
    closes: list[float] = []          # weekly closes, complete weeks or not
    for i, g in enumerate(groups):
        prev_close = closes[-1] if closes else None
        # The anchor must be the prior week's FINAL session (Friday, or
        # Thursday when Friday was a holiday). A gap that cut the prior
        # week short would otherwise anchor this week to, say, Wednesday's
        # close and quietly distort every trigger built on it (Codex, #424).
        prev_ok = (i > 0
                   and (_monday(g[0]["date"]) - _monday(groups[i - 1][0]["date"])).days == 7
                   and groups[i - 1][-1]["date"] >= _last_session(_monday(groups[i - 1][0]["date"])))
        complete = len(g) >= _expected_sessions(_monday(g[0]["date"]))
        if prev_close is not None and prev_ok and complete:
            anchor = prev_close
            hi = max(r["high"] for r in g)
            hit = next(j for j, r in enumerate(g) if r["high"] >= hi - _EPS)
            recent = closes[-PRIOR_CLOSES:]
            out.append({
                "start": _monday(g[0]["date"]).isoformat(),
                "end": g[-1]["date"].isoformat(),
                "anchor": anchor,
                "close": g[-1]["close"],
                "high_pct": hi / anchor - 1.0,
                "term_pct": g[-1]["close"] / anchor - 1.0,
                "hit_idx": g[hit]["date"].weekday(),
                "sessions_left_after_hit": len(g) - 1 - hit,
                "days": [{"idx": r["date"].weekday(),
                          "high_pct": r["high"] / anchor - 1.0,
                          "close_pct": r["close"] / anchor - 1.0} for r in g],
                "prior_median": median(recent) if len(recent) >= MIN_PRIOR_CLOSES else None,
            })
        closes.append(g[-1]["close"])
    return out


# ── 2. without the scheduled gaps ─────────────────────────────────────────
def weekly_high_moves(bars: Sequence[dict], earnings_dates: Iterable[Any] = ()) -> list[dict]:
    """The weeks that may set a trigger: every complete week EXCEPT one that
    contains an earnings date. A scheduled gap says nothing about the wick
    an ordinary week draws, and one earnings spike would lift the trigger
    for every quiet week after it."""
    eds = {d for d in (ws._as_date(e) for e in earnings_dates or ()) if d}
    out = []
    for w in friday_weeks(bars):
        s, e = date.fromisoformat(w["start"]), date.fromisoformat(w["start"]) + timedelta(days=6)
        if any(s <= d <= e for d in eds):
            continue
        out.append(w)
    return out


# ── 3. the wick ───────────────────────────────────────────────────────────
def wick_score(bars: Sequence[dict], earnings_dates: Iterable[Any] = ()) -> float | None:
    """Median over clean weeks of (weekly high - weekly close), both from
    last Friday's close. High = the stock spikes mid-week and gives it back
    by Friday: the top wick a call seller sells into. A stock that grinds
    up and closes at its high scores near zero."""
    moves = weekly_high_moves(bars, earnings_dates)
    if not moves:
        return None
    return median(w["high_pct"] - w["term_pct"] for w in moves)


def rank_tickers(universe: Mapping[str, Any]) -> list[dict]:
    """A watchlist sorted by wick score, best candidates first.

    `universe` maps symbol -> daily bars, or symbol -> {"bars": [...],
    "earnings_dates": [...]}. A symbol without enough clean weeks is kept
    at the bottom with its reason rather than dropped silently."""
    out = []
    for sym, v in (universe or {}).items():
        if isinstance(v, Mapping):
            bars, eds = v.get("bars") or [], v.get("earnings_dates") or ()
        else:
            bars, eds = v or [], ()
        moves = weekly_high_moves(bars, eds)
        score = median(w["high_pct"] - w["term_pct"] for w in moves) if moves else None
        ok = len(moves) >= MIN_WEEKS and score is not None
        out.append({"symbol": str(sym).upper(), "wick_score": score, "weeks": len(moves), "ok": ok,
                    "reason": None if ok else f"only {len(moves)} clean weeks (need {MIN_WEEKS})"})
    out.sort(key=lambda r: (not r["ok"], -(r["wick_score"] if r["wick_score"] is not None else -1e9)))
    return out


# ── 4. the trigger ────────────────────────────────────────────────────────
def iv_scale(iv_now: Any = None, iv_median: Any = None) -> float:
    """sqrt(iv_now / iv_median), clamped to [IV_SCALE_LO, IV_SCALE_HI];
    1.0 when either is missing. Both sides are normalised first, so a 42
    and a 0.42 mean the same thing."""
    a, b = _sigma(iv_now), _sigma(iv_median)
    if not a or not b:
        return 1.0
    return min(IV_SCALE_HI, max(IV_SCALE_LO, math.sqrt(a / b)))


def calibrate(bars: Sequence[dict], earnings_dates: Iterable[Any] = (),
              pctile: float = TRIGGER_PCTILE, iv_now: Any = None,
              iv_median: Any = None) -> dict:
    """This stock's trigger: the `pctile` percentile of its clean weekly
    highs from Friday's close, scaled by the volatility regime.

    Returns ok=False with the reason when there are fewer than MIN_WEEKS
    clean weeks. Otherwise: trigger_pct (scaled), raw_trigger_pct, iv_scale,
    wick_score, p50 / p90 of weekly highs, and the median sessions left in
    the week after its high printed."""
    moves = weekly_high_moves(bars, earnings_dates)
    n = len(moves)
    if n < MIN_WEEKS:
        return {"schema": SCHEMA, "ok": False, "n_weeks": n,
                "reason": f"only {n} clean weeks of history (need {MIN_WEEKS}); "
                          "earnings weeks and partial weeks do not count",
                "moves": moves}
    highs = [w["high_pct"] for w in moves]
    raw = ws.percentile(highs, pctile)
    scale = iv_scale(iv_now, iv_median)
    return {
        "schema": SCHEMA, "ok": True, "n_weeks": n, "reason": None,
        "pctile": pctile,
        "raw_trigger_pct": raw,
        "iv_scale": scale,
        "trigger_pct": raw * scale,
        "wick_score": median(w["high_pct"] - w["term_pct"] for w in moves),
        "p50": ws.percentile(highs, 0.50),
        "p90": ws.percentile(highs, 0.90),
        "median_sessions_left_after_hit": median(w["sessions_left_after_hit"] for w in moves),
        "median_hit_idx": median(w["hit_idx"] for w in moves),
        "moves": moves,
    }


# ── 5. what happens after the tap ─────────────────────────────────────────
def _spread(moves: Sequence[dict]) -> float:
    highs = [w["high_pct"] for w in moves]
    p50, p90 = ws.percentile(highs, 0.50), ws.percentile(highs, 0.90)
    if p50 is None or p90 is None:
        return MIN_SPREAD
    return max(MIN_SPREAD, p90 - p50)


def extension(trigger_price: float, prior_median: float | None, spread: float) -> float | None:
    """How stretched the trigger is: (trigger price / median of the last 8
    weekly closes - 1), in units of the stock's wick spread p90 - p50 (a
    fraction of anchor, floored at MIN_SPREAD). 0 = the trigger sits at the
    recent closes; 1 = one wick-spread above them."""
    if not prior_median or prior_median <= 0 or not trigger_price:
        return None
    return (trigger_price / prior_median - 1.0) / max(MIN_SPREAD, spread)


def delta_for_extension(ext: float | None) -> float:
    """The adaptive delta. Piecewise linear through the policy points:
    ext <= 0 -> 0.30, ext = 1 -> 0.50, ext = 1.5 -> 0.70, then +0.10 per unit
    of extension, capped at DELTA_MAX. Unknown extension sells the floor."""
    if ext is None or ext <= 0:
        return DELTA_FLOOR
    if ext <= 1.0:
        return DELTA_FLOOR + (DELTA_MID - DELTA_FLOOR) * ext
    if ext <= EXT_DEEP:
        return DELTA_MID + (DELTA_DEEP - DELTA_MID) * (ext - 1.0) / (EXT_DEEP - 1.0)
    return min(DELTA_MAX, DELTA_DEEP + 0.10 * (ext - EXT_DEEP))


def _first_cross(w: dict, trigger_pct: float) -> int | None:
    """Position (0-based, within the week's bars) of the first session whose
    high reached the trigger, or None if the week never reached it."""
    for j, d in enumerate(w["days"]):
        if d["high_pct"] >= trigger_pct - _EPS:
            return j
    return None


def after_trigger_stats(moves: Sequence[dict], trigger_pct: float) -> dict:
    """Conditional on the trigger being reached: how often, and where the
    week then FINISHED relative to it.

      hit_rate          share of weeks that reached the trigger
      p_finish_over     of those, the share that closed Friday above it
                        (what assigns a call struck at the trigger)
      finish_median / finish_p90   Friday close minus trigger, fraction of anchor
      terciles          the trigger weeks split by extension (low / mid /
                        high): n, extension range, p_finish_over, and the
                        median retrace from the trigger into Friday
      deep_retrace_proven   True only when the high-extension tercile
                        retraced harder than the low one, with at least
                        MIN_TERCILE weeks in each. A deep, high-delta sale
                        needs that evidence; without it `decide` caps the
                        delta at the money.
    """
    moves = list(moves or [])
    n = len(moves)
    hits = [w for w in moves if w["high_pct"] >= trigger_pct - _EPS]
    out: dict[str, Any] = {"n_weeks": n, "n_hit": len(hits),
                           "hit_rate": (len(hits) / n) if n else None,
                           "p_finish_over": None, "finish_median": None, "finish_p90": None,
                           "terciles": [], "deep_retrace_proven": False}
    if not hits:
        return out
    fin = [w["term_pct"] - trigger_pct for w in hits]
    out["p_finish_over"] = sum(1 for f in fin if f > 0) / len(hits)
    out["finish_median"] = median(fin)
    out["finish_p90"] = ws.percentile(fin, 0.90)

    spread = _spread(moves)
    tagged = []
    for w in hits:
        ext = extension(w["anchor"] * (1.0 + trigger_pct), w.get("prior_median"), spread)
        if ext is not None:
            tagged.append((ext, w))
    tagged.sort(key=lambda t: t[0])
    k = len(tagged)
    if k:
        cuts = [0, k // 3, (2 * k) // 3, k]
        for name, a, b in zip(("low", "mid", "high"), cuts[:-1], cuts[1:]):
            part = tagged[a:b]
            if not part:
                out["terciles"].append({"bucket": name, "n": 0})
                continue
            f = [w["term_pct"] - trigger_pct for _, w in part]
            out["terciles"].append({
                "bucket": name, "n": len(part),
                "ext_lo": part[0][0], "ext_hi": part[-1][0],
                "p_finish_over": sum(1 for x in f if x > 0) / len(part),
                "finish_median": median(f),
                # how far below the trigger Friday closed: positive = retrace
                "retrace_median": -median(f),
            })
        lo, hi = out["terciles"][0], out["terciles"][-1]
        out["deep_retrace_proven"] = bool(
            lo.get("n", 0) >= MIN_TERCILE and hi.get("n", 0) >= MIN_TERCILE
            and hi["retrace_median"] > lo["retrace_median"] + _EPS)
    return out


# ── 6. strikes and expected value ─────────────────────────────────────────
def delta_strike(spot: float, sessions: float, sigma: Any, target_delta: float,
                 r: float = 0.045) -> float | None:
    """The call strike whose Black-Scholes delta is `target_delta`, found by
    bisection on `metrics._bs_delta` (call delta falls as the strike rises).
    `sessions` of risk, at SESSIONS_PER_YEAR. A target above 0.5 returns an
    in-the-money strike."""
    s = _sigma(sigma)
    if not spot or spot <= 0 or not s or sessions is None or sessions <= 0:
        return None
    if not (0.0 < target_delta < 1.0):
        return None
    T = _years(sessions)
    lo, hi = spot * 0.2, spot * 5.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if metrics._bs_delta(spot, mid, T, s, "call", r=r) > target_delta:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def call_price(spot: float, strike: float, sessions: float, sigma: Any, r: float = 0.045) -> float:
    s = _sigma(sigma) or 0.0
    return metrics._bs_price(spot, strike, _years(sessions), s, "call", r=r)


def ev_short_call(spot: float, strike: float, credit: float,
                  windows: Sequence[dict]) -> float | None:
    """Credit minus the mean Friday shortfall past the strike, over
    `weekly_sell.forward_windows`: the same arithmetic as
    `weekly_sell.evaluate_strike` for the call side, per share."""
    if not windows or not spot or spot <= 0 or strike is None:
        return None
    total = 0.0
    for w in windows:
        term = spot * (1.0 + w["term"])
        total += credit - max(0.0, term - strike)
    return total / len(windows)


# ── 7. wait or sell ───────────────────────────────────────────────────────
def _anchor_close(rows: list[dict], today: date) -> float | None:
    """The close the trigger is measured from: the last bar before this
    week's Monday."""
    mon = _monday(today)
    prior = [r for r in rows if r["date"] < mon]
    return prior[-1]["close"] if prior else None


def decide(spot: float, bars: Sequence[dict], today: Any, friday_expiry: Any,
           iv_now: Any, *, iv_median: Any = None, earnings_dates: Iterable[Any] = (),
           anchor_close: float | None = None,
           theta_history: Sequence[bool] = (), tapped: bool | None = None,
           r: float = 0.045) -> dict:
    """Wait for the trigger, or sell now? Both priced, per share.

    EV(now)   a DELTA_NOW call priced today with `metrics._bs_price`, minus
              the mean shortfall over this stock's forward windows of the
              sessions left to expiry.
    EV(wait)  p_hit x EV(adaptive-delta call sold AT the trigger price with
              the typical sessions left after a tap, shortfall measured on
              the Friday closes of past trigger weeks re-anchored to that
              price) + (1 - p_hit) x max(0, EV of the Thursday fallback: a
              DELTA_NOW call with FALLBACK_SESSIONS).
              p_hit only counts weeks that first reached the trigger on or
              after today's weekday: a Wednesday decision cannot be credited
              with Monday taps.

    EV(wait) - EV(now) is split into legs that add up exactly:
      theta_decay    the same DELTA_NOW strike priced with the sessions
                     left at a typical tap instead of today (negative)
      strike_uplift  a DELTA_NOW call at the trigger price instead of that
      delta_uplift   the adaptive delta instead of DELTA_NOW
      risk_and_miss  the rest: assignment shortfall and the weeks the
                     trigger never comes.
    The three premium legs are reported per share as priced, and also
    weighted by p_hit so that all four sum to EV(wait) - EV(now).

    Theta DOMINATES when its loss exceeds the two uplifts together.
    `theta_history` holds the caller's prior weekly flags (most recent
    last); two weeks running sets `trigger_too_far`.

    Recommends "wait" only when EV(wait) > EV(now) + WAIT_MARGIN x |EV(now)|
    (that is EV(now) x 1.10 when EV(now) is positive); "skip" when both
    are <= 0; otherwise "sell_now". When the trigger has already been
    tapped (spot >= trigger price, or any of this week's highs in `bars`
    already reached it, or `tapped=True`) the answer is "sell_at_trigger",
    with the adaptive strike priced at the current spot on the sessions
    actually left: a tap that has since pulled back is not a reason to
    wait for a second one.

    On Monday, with NO_MONDAY_SALE, "sell_now" becomes "wait" with the
    numbers kept: that is the rule the system starts from. The Thursday
    fallback shrinks to the sessions that will actually be left after
    today and vanishes on Friday.
    """
    today_d, exp_d = ws._as_date(today), ws._as_date(friday_expiry)
    out: dict[str, Any] = {"schema": SCHEMA, "ok": False, "action": None, "reason": None}
    spot = ws._f(spot) or 0.0
    sigma = _sigma(iv_now)
    if spot <= 0 or today_d is None or exp_d is None or not sigma:
        out["reason"] = "needs a live price, today's date, the Friday expiry and implied volatility"
        return out
    cal = calibrate(bars, earnings_dates, iv_now=iv_now, iv_median=iv_median)
    out["calibration"] = {k: v for k, v in cal.items() if k != "moves"}
    if not cal["ok"]:
        out["reason"] = cal["reason"]
        return out
    moves = cal["moves"]
    tp = cal["trigger_pct"]
    rows = _rows(bars)
    anchor = ws._f(anchor_close) or _anchor_close(rows, today_d)
    if not anchor:
        out["reason"] = "no prior Friday close to measure the trigger from"
        return out
    trigger_price = anchor * (1.0 + tp)
    s_now = ws.sessions_until(exp_d, today_d) or 0
    if s_now < 1:
        out["reason"] = "this expiry has no sessions left"
        return out
    out.update({"anchor": anchor, "trigger_pct": tp, "trigger_price": trigger_price,
                "spot": spot, "sessions_now": s_now, "sigma": sigma})

    stats = after_trigger_stats(moves, tp)
    out["after_trigger"] = stats
    spread = _spread(moves)
    closes = [r_["close"] for r_ in _weekly_closes(rows, today_d)]
    pm = median(closes[-PRIOR_CLOSES:]) if len(closes[-PRIOR_CLOSES:]) >= MIN_PRIOR_CLOSES else None
    ext = extension(trigger_price, pm, spread)
    raw_delta = delta_for_extension(ext)
    delta_w = raw_delta
    capped = False
    if raw_delta > DELTA_MID and not stats["deep_retrace_proven"]:
        delta_w, capped = DELTA_MID, True
    out["extension"] = ext
    out["delta"] = {"adaptive": delta_w, "uncapped": raw_delta, "capped": capped,
                    "why_capped": ("deep extension has not shown a harder retrace in this stock's "
                                   "own trigger weeks, so the delta stays at the money") if capped else None}

    # ── EV(now)
    windows_now = ws.forward_windows(bars, s_now, drop_last_dated=today_d)
    k_now = delta_strike(spot, s_now, sigma, DELTA_NOW, r=r)
    c_now = call_price(spot, k_now, s_now, sigma, r=r)
    ev_now = ev_short_call(spot, k_now, c_now, windows_now)
    out["now"] = {"strike": k_now, "credit": c_now, "ev": ev_now, "delta": DELTA_NOW,
                  "sessions": s_now, "windows": len(windows_now)}

    mon = _monday(today_d)
    week_hi = [r_ for r_ in rows if mon <= r_["date"] <= today_d and r_["high"] >= trigger_price - _EPS]
    tap_seen = (week_hi[0]["date"].isoformat() if week_hi else None)
    if tapped is None:
        tapped = spot >= trigger_price - _EPS or bool(week_hi)
    out["tap"] = {"tapped": bool(tapped), "first_seen": tap_seen,
                  "at_spot": spot >= trigger_price - _EPS}
    # ── p_hit and the sessions left after a tap, from today's weekday on
    idx_today = today_d.weekday()
    still_open, crossed_later, left_after = 0, [], []
    for w in moves:
        j = _first_cross(w, tp)
        if j is not None and w["days"][j]["idx"] < idx_today:
            continue                      # already tapped before today: not this decision
        still_open += 1
        if j is not None:
            crossed_later.append(w)
            left_after.append(len(w["days"]) - 1 - j)
    p_hit = (len(crossed_later) / still_open) if still_open else 0.0
    s_hit = (median(left_after) + 1) if left_after else (cal["median_sessions_left_after_hit"] + 1)
    s_hit = max(1, min(s_now, int(round(s_hit))))
    if tapped:
        p_hit, s_hit = 1.0, s_now

    # ── EV(wait): the adaptive sale at the trigger
    sale_price = spot if tapped else trigger_price
    k_w = delta_strike(sale_price, s_hit, sigma, delta_w, r=r)
    c_w = call_price(sale_price, k_w, s_hit, sigma, r=r)
    pool = crossed_later or [w for w in moves if _first_cross(w, tp) is not None]
    if pool:
        short = [max(0.0, sale_price * ((1.0 + w["term_pct"]) / (1.0 + tp)) - k_w) for w in pool]
        ev_hit = c_w - sum(short) / len(short)
    else:
        ev_hit = None
    # ── the Thursday fallback
    # It is a LATER sale, so it can only use sessions left after today:
    # two from Monday-Wednesday, one on Thursday (Friday's), none on
    # Friday, when "waiting" for a fallback is selling nothing (Codex, #424).
    s_fb = min(FALLBACK_SESSIONS, s_now - 1)
    if s_fb >= 1:
        win_fb = ws.forward_windows(bars, s_fb, drop_last_dated=today_d)
        k_fb = delta_strike(spot, s_fb, sigma, DELTA_NOW, r=r)
        c_fb = call_price(spot, k_fb, s_fb, sigma, r=r)
        ev_fb = ev_short_call(spot, k_fb, c_fb, win_fb)
    else:
        k_fb = c_fb = None
        ev_fb = 0.0
    ev_wait = None
    if ev_hit is not None:
        ev_wait = p_hit * ev_hit + (1.0 - p_hit) * max(0.0, ev_fb or 0.0)
    out["wait"] = {"p_hit": p_hit, "sessions_at_tap": s_hit, "sale_price": sale_price,
                   "strike": k_w, "credit": c_w, "delta": delta_w, "ev_at_tap": ev_hit,
                   "itm_at_sale": bool(k_w is not None and k_w < sale_price),
                   "fallback": {"strike": k_fb, "credit": c_fb, "ev": ev_fb, "sessions": max(0, s_fb)},
                   "ev": ev_wait, "tap_weeks": len(pool)}

    # ── the legs: A -> B (theta) -> C (strike) -> D (delta)
    a = c_now
    b = call_price(spot, k_now, s_hit, sigma, r=r)
    k_c = delta_strike(sale_price, s_hit, sigma, DELTA_NOW, r=r)
    c = call_price(sale_price, k_c, s_hit, sigma, r=r)
    d = c_w
    theta, strike_up, delta_up = b - a, c - b, d - c
    diff = (ev_wait - ev_now) if (ev_wait is not None and ev_now is not None) else None
    weighted = {"theta_decay": p_hit * theta, "strike_uplift": p_hit * strike_up,
                "delta_uplift": p_hit * delta_up}
    theta_dominant = (-theta) > (strike_up + delta_up)
    history = list(theta_history or [])
    out["legs"] = {
        "theta_decay": theta, "strike_uplift": strike_up, "delta_uplift": delta_up,
        "weighted": weighted,
        "risk_and_miss": (diff - sum(weighted.values())) if diff is not None else None,
        "ev_diff": diff,
        "theta_dominant": theta_dominant,
        "trigger_too_far": bool(theta_dominant and history and history[-1]),
    }
    if out["legs"]["trigger_too_far"]:
        out["legs"]["note"] = ("Theta has outweighed the uplift two weeks running: the trigger "
                               "is set too far out for this stock.")

    # ── the recommendation
    out["ok"] = ev_now is not None and ev_wait is not None
    if not out["ok"]:
        out["reason"] = "not enough forward windows to price both choices"
        return out
    if tapped:
        where = (f"price {spot:.2f} is at the trigger {trigger_price:.2f}" if out["tap"]["at_spot"]
                 else f"the trigger {trigger_price:.2f} was tapped {tap_seen or 'earlier this week'} "
                      f"and price is now {spot:.2f}")
        if ev_hit is not None and ev_hit > 0:
            out["action"] = "sell_at_trigger"
            out["reason"] = (f"{where}: sell the {delta_w:.2f}-delta call now with "
                             f"{s_hit} session(s) left")
        else:
            out["action"] = "skip"
            out["reason"] = (f"{where}, but a {delta_w:.2f}-delta call sold at the trigger has lost "
                             f"money on this stock's own trigger weeks: do not sell this one")
    elif ev_wait <= 0 and ev_now <= 0:
        out["action"] = "skip"
        out["reason"] = "neither selling now nor waiting for the trigger has paid on this stock's history"
    elif ev_wait > ev_now + WAIT_MARGIN * abs(ev_now):
        out["action"] = "wait"
        out["reason"] = (f"waiting for {trigger_price:.2f} (+{tp * 100:.1f}% from Friday) is worth "
                         f"{ev_wait:.2f} a share against {ev_now:.2f} for a {DELTA_NOW:.2f}-delta "
                         f"call now; the trigger came on or after today in {p_hit * 100:.0f}% of weeks")
    else:
        out["action"] = "sell_now"
        out["reason"] = (f"waiting is worth {ev_wait:.2f} a share, not clearly more than "
                         f"{ev_now:.2f} for selling the {DELTA_NOW:.2f}-delta call now")
        if NO_MONDAY_SALE and idx_today == 0:
            # The rule this system exists for (Codex, #424). The numbers stay
            # on the answer; the action does not become a Monday sale.
            out["action"] = "wait"
            out["monday_rule"] = True
            out["reason"] = (f"no Monday sale: a {DELTA_NOW:.2f}-delta call now would be worth "
                             f"{ev_now:.2f} a share against {ev_wait:.2f} for waiting, so re-check "
                             f"Tuesday or sell the tap at {trigger_price:.2f}")
    return out


def _weekly_closes(rows: list[dict], today: date) -> list[dict]:
    """The last bar of every calendar week before today's week."""
    mon = _monday(today)
    out: list[dict] = []
    for r_ in rows:
        if r_["date"] >= mon:
            break
        if out and _monday(out[-1]["date"]) == _monday(r_["date"]):
            out[-1] = r_
        else:
            out.append(r_)
    return out


# ── 8. hand the tap back to the strike engine ─────────────────────────────
def retime_at_trigger(anchor_close: float, trigger_pct: float, today: Any,
                      friday_expiry: Any) -> dict:
    """The inputs for re-running `weekly_sell.build_plan` at the moment of
    the tap: spot at the trigger price, and the sessions actually left
    between `today` and the Friday expiry."""
    a = ws._f(anchor_close)
    spot = a * (1.0 + trigger_pct) if a else None
    return {"spot": spot, "sessions": ws.sessions_until(friday_expiry, today)}
