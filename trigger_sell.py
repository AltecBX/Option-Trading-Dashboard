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

  6. THE TRIGGER DEPENDS ON LAST WEEK. A static trigger goes dead after
     a big up week (COHR's 12.5% hit only 16% of the time the week after a
     +11.6% week, whose true wick top was 9.25%) and sells too early after
     a crash (LITE off a -8.6% week typically ran to a 21.5% wick top). A
     REGIME TABLE (one entry per ticker: `unconditional_trigger`,
     `prior_week_edges`, and `conditional[]` with `trigger70` /
     `trigger50` / `prior_lo` / `prior_hi` per quintile of last week's
     Friday-to-Friday close) picks the trigger from last week's move
     (`regime_trigger`). `calibrate` and `decide` accept it; given one,
     `decide` takes the trigger price, the extension metric, the
     sessions-left lookup and the odds of a tap from the SAME quintile's
     weeks, and the earned-delta guardrail asks for the pullback evidence
     inside that quintile only. `optimize_percentile` lets the percentile
     itself depend on the regime.

THE THETA HISTORY THE APP KEEPS. `decide` returns `history_record`, which
the app persists once a week per ticker and passes back, most recent last,
as `theta_history`:

    {"week": "2026-09-28",        # Monday of the decision week (ISO)
     "ticker": "LITE",            # or None
     "quintile": 5,               # regime quintile used, None without a table
     "trigger_pct": 0.145,        # the trigger used, fraction of Friday's close
     "theta_dominant": true,      # theta loss > strike + delta uplift that week
     "theta_decay": -0.66,        # per share, as priced
     "uplift": 0.41}              # strike + delta uplift, per share

`trigger_too_far` compares like for like: it fires when this week's theta
dominates AND the most recent earlier record in the SAME quintile did too.
A quiet week after a crash week says nothing about a trigger set for
breakout weeks. Plain booleans (the v1 contract) are still accepted and
compared as before.

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

# ── the regime layer (last week's move picks the trigger) ──
QUINTILES = 5
MIN_REGIME_WEEKS = 6       # below this many clean weeks in the quintile, the
                           # tap odds, sessions left and wick spread come from
                           # all weeks (and say so). The earned-delta guardrail
                           # never falls back: too few weeks is no proof.
PCTILE_GRID = tuple(round(0.30 + 0.05 * i, 2) for i in range(13))   # 0.30 .. 0.90
                           # the percentiles `optimize_percentile` searches

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


def _week_end(last: date) -> bool:
    """Is `last` its week's final session (Friday, or Thursday before a
    holiday Friday)?"""
    return last >= _last_session(_monday(last))


def _week_move_pct(prev_last: dict, last: dict) -> float | None:
    """Friday-to-Friday close in percent points, or None unless both bars
    end their weeks and the weeks are back to back. A missing week would
    pass off a two-week move as one, and a week cut short on Wednesday a
    Wednesday-to-Friday one (Codex, #425)."""
    if not (_week_end(prev_last["date"]) and _week_end(last["date"])
            and (_monday(last["date"]) - _monday(prev_last["date"])).days == 7
            and prev_last["close"] > 0):
        return None
    return (last["close"] / prev_last["close"] - 1.0) * 100.0


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
      prior_week_pct  LAST week's Friday-to-Friday close, in percent points
                    (+11.6 = up 11.6%): the regime this week starts in

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
                   and _week_end(groups[i - 1][-1]["date"]))
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
                "prior_week_pct": (_week_move_pct(groups[i - 2][-1], groups[i - 1][-1])
                                   if i >= 2 else None),
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
              iv_median: Any = None, regime_table: Any = None,
              prior_week_close_pct: float | None = None,
              ticker: str | None = None) -> dict:
    """This stock's trigger: the `pctile` percentile of its clean weekly
    highs from Friday's close, scaled by the volatility regime.

    With `regime_table` and `prior_week_close_pct` (percent points), the
    raw trigger comes from `regime_trigger` instead: the trigger70 of the
    quintile last week's move falls in. `regime_table` is the whole table
    (then `ticker` picks the entry) or one ticker's entry. A ticker the
    table cannot answer keeps the percentile and says so in
    `trigger_source`. The IV scaling, the earnings exclusion and the
    MIN_WEEKS refusal are the same either way.

    Returns ok=False with the reason when there are fewer than MIN_WEEKS
    clean weeks. Otherwise: trigger_pct (scaled), raw_trigger_pct, iv_scale,
    wick_score, p50 / p90 of weekly highs, the median sessions left in the
    week after its high printed, `trigger_source` ("percentile" or
    "regime" or "unconditional") and `regime` (what the table said)."""
    moves = weekly_high_moves(bars, earnings_dates)
    n = len(moves)
    if n < MIN_WEEKS:
        return {"schema": SCHEMA, "ok": False, "n_weeks": n,
                "reason": f"only {n} clean weeks of history (need {MIN_WEEKS}); "
                          "earnings weeks and partial weeks do not count",
                "moves": moves}
    highs = [w["high_pct"] for w in moves]
    raw = ws.percentile(highs, pctile)
    source, info = "percentile", None
    if regime_table is not None and prior_week_close_pct is not None:
        info = regime_info(ticker, prior_week_close_pct, regime_table)
        if info["trigger"] is not None:
            raw, source = info["trigger"], info["source"]
    scale = iv_scale(iv_now, iv_median)
    return {
        "schema": SCHEMA, "ok": True, "n_weeks": n, "reason": None,
        "pctile": pctile if source == "percentile" else None,
        "trigger_source": source, "regime": info,
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


# ── 4b. the regime: last week's move picks this week's trigger ────────────
def _table_entry(table: Any, ticker: str | None) -> dict | None:
    """One ticker's entry from the regime table. Accepts {TICKER: entry},
    {"tickers": {...}}, a list of entries carrying "ticker"/"symbol", or a
    single entry passed directly (then `ticker` is not needed)."""
    if not table:
        return None
    if isinstance(table, Mapping) and ("unconditional_trigger" in table or "conditional" in table):
        return dict(table)
    t = str(ticker or "").upper().strip()
    if not t:
        return None
    if isinstance(table, Mapping):
        if isinstance(table.get("tickers"), (Mapping, list)):
            return _table_entry(table["tickers"], t)
        for k, v in table.items():
            if str(k).upper() == t and isinstance(v, Mapping):
                return dict(v)
        return None
    if isinstance(table, list):
        for v in table:
            if isinstance(v, Mapping) and str(v.get("ticker") or v.get("symbol") or "").upper() == t:
                return dict(v)
    return None


def _entry_scale(entry: Mapping) -> float:
    """1/100 when the entry quotes triggers in percent points (12.5), 1 when
    in fractions (0.125). Read from the triggers themselves: no stock's
    weekly wick trigger is over 100%, and none worth trading is under 1%."""
    vals = [ws._f(entry.get("unconditional_trigger"))]
    for c in entry.get("conditional") or []:
        if isinstance(c, Mapping):
            vals += [ws._f(c.get("trigger70")), ws._f(c.get("trigger50"))]
    vals = [abs(v) for v in vals if v is not None]
    return 0.01 if vals and max(vals) > 1.0 else 1.0


def _quintile_rows(entry: Mapping) -> list[dict]:
    """The entry's quintiles, in order: by their `quintile` number, else
    as listed."""
    cond = [c for c in (entry.get("conditional") or []) if isinstance(c, Mapping)]
    if all(ws._f(c.get("quintile")) is not None for c in cond):
        cond = sorted(cond, key=lambda c: ws._f(c.get("quintile")))
    return cond


def _edges_pct(entry: Mapping, scale: float) -> list[float] | None:
    """The quintile cut points of last week's close, in percent points:
    `prior_week_edges`, or without them the bounds the quintiles declare
    (prior_hi of quintiles 1-4, with quintile 1's prior_lo and 5's
    prior_hi at the ends). The live week and the past weeks are then
    sorted by the same cut points (Codex, #425)."""
    k = 100.0 * scale              # fraction-unit tables are lifted to percent points
    e = [ws._f(x) for x in (entry.get("prior_week_edges") or [])]
    e = [x for x in e if x is not None]
    if len(e) >= QUINTILES - 1:
        return sorted(x * k for x in e)
    cond = _quintile_rows(entry)
    if len(cond) != QUINTILES:
        return None
    cuts = [ws._f(c.get("prior_hi")) for c in cond[:-1]]
    if any(x is None for x in cuts) or cuts != sorted(cuts):
        return None
    lo, hi = ws._f(cond[0].get("prior_lo")), ws._f(cond[-1].get("prior_hi"))
    if lo is not None and hi is not None and lo <= cuts[0] and hi >= cuts[-1]:
        cuts = [lo] + cuts + [hi]
    return [x * k for x in cuts]


def quintile_of(value_pct: float, edges_pct: Sequence[float]) -> int:
    """Which quintile last week's close falls in. With the six edges
    [min, q20, q40, q60, q80, max], value <= edges[i+1] falls in quintile
    i+1, so a value exactly on an edge lands in the LOWER quintile; below
    the first edge is quintile 1 and above the last is quintile 5. Four
    interior cut points work the same way."""
    e = list(edges_pct)
    inner = e[1:-1] if len(e) == QUINTILES + 1 else e
    for i, cut in enumerate(inner[:QUINTILES - 1]):
        if value_pct <= cut + _EPS:
            return i + 1
    return QUINTILES


def regime_info(ticker: str | None, prior_week_close_pct: float | None, table: Any) -> dict:
    """Everything the table says for this ticker and last week's move.

    trigger      the trigger to use, as a fraction of Friday's close: the
                 quintile's trigger70, or the entry's unconditional_trigger
                 when it has no quintiles (or no move was given), or None
                 when the table has no entry at all
    source       "regime", "unconditional" or "missing"
    quintile     1..5, None unless the regime answered
    trigger50 / prior_lo / prior_hi / n   from the quintile, as given
    edges_pct    the cut points in percent points (for classifying weeks)
    """
    out: dict[str, Any] = {"ticker": (str(ticker).upper() if ticker else None), "source": "missing",
                           "trigger": None, "quintile": None, "trigger50": None,
                           "prior_lo": None, "prior_hi": None, "n": None,
                           "unconditional": None, "edges_pct": None,
                           "prior_week_close_pct": prior_week_close_pct}
    entry = _table_entry(table, ticker)
    if entry is None:
        return out
    sc = _entry_scale(entry)
    unc = ws._f(entry.get("unconditional_trigger"))
    out["unconditional"] = unc * sc if unc is not None else None
    edges = _edges_pct(entry, sc)
    out["edges_pct"] = edges
    cond = _quintile_rows(entry)
    pw = ws._f(prior_week_close_pct)
    q = quintile_of(pw, edges) if pw is not None and cond and edges else None
    chosen = None
    if q is not None:
        chosen = next((c for c in cond if int(ws._f(c.get("quintile")) or 0) == q), None)
        if chosen is None and 1 <= q <= len(cond):
            chosen = cond[q - 1]
    t70 = ws._f(chosen.get("trigger70")) if chosen else None
    if t70 is not None:
        out.update({"source": "regime", "trigger": t70 * sc, "quintile": q,
                    "trigger50": (ws._f(chosen.get("trigger50")) * sc
                                  if ws._f(chosen.get("trigger50")) is not None else None),
                    "prior_lo": ws._f(chosen.get("prior_lo")), "prior_hi": ws._f(chosen.get("prior_hi")),
                    "n": chosen.get("n") or chosen.get("weeks")})
    elif out["unconditional"] is not None:
        out.update({"source": "unconditional", "trigger": out["unconditional"]})
    return out


def regime_trigger(ticker: str | None, prior_week_close_pct: float | None, table: Any) -> float | None:
    """This week's trigger (a fraction of Friday's close) from last week's
    Friday-to-Friday close in percent points: the trigger70 of its
    quintile, the ticker's unconditional_trigger when the table has no
    quintiles for it, None when the table has no entry for it at all."""
    return regime_info(ticker, prior_week_close_pct, table)["trigger"]


def empirical_edges(moves: Sequence[dict]) -> list[float] | None:
    """Quintile edges of last week's close from the weeks themselves, for
    when no table is given: [min, p20, p40, p60, p80, max] in percent points."""
    v = [w["prior_week_pct"] for w in moves if w.get("prior_week_pct") is not None]
    if len(v) < QUINTILES:
        return None
    return [ws.percentile(v, i / QUINTILES) for i in range(QUINTILES + 1)]


def regime_weeks(moves: Sequence[dict], quintile: int, edges_pct: Sequence[float] | None) -> list[dict]:
    """The weeks that STARTED in this quintile (by their prior week's close)."""
    if not edges_pct:
        return []
    return [w for w in moves if w.get("prior_week_pct") is not None
            and quintile_of(w["prior_week_pct"], edges_pct) == quintile]


def _left_after_first_tap(weeks: Sequence[dict], trigger_pct: float) -> list[int]:
    out = []
    for w in weeks:
        j = _first_cross(w, trigger_pct)
        if j is not None:
            out.append(len(w["days"]) - 1 - j)
    return out


def quintile_sessions_left(bars: Sequence[dict], quintile: int, trigger_pct: float, *,
                           edges_pct: Sequence[float] | None = None,
                           earnings_dates: Iterable[Any] = ()) -> float | None:
    """Median sessions left in the week after the FIRST tap of
    `trigger_pct`, over the clean weeks that started in `quintile` only.
    Edges from the regime table when given, else from the weeks themselves.
    None when no week in the quintile reached the trigger."""
    moves = weekly_high_moves(bars, earnings_dates)
    weeks = regime_weeks(moves, quintile, edges_pct or empirical_edges(moves))
    left = _left_after_first_tap(weeks, trigger_pct)
    return median(left) if left else None


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


# ── 6b. the percentile, by regime ─────────────────────────────────────────
def realized_vol(bars: Sequence[dict]) -> float | None:
    """Annualised standard deviation of daily log returns, the volatility
    used when no implied volatility is given."""
    c = [r["close"] for r in _rows(bars)]
    lr = [math.log(b / a) for a, b in zip(c, c[1:]) if a > 0 and b > 0]
    if len(lr) < 20:
        return None
    m = sum(lr) / len(lr)
    var = sum((x - m) ** 2 for x in lr) / (len(lr) - 1)
    return math.sqrt(var * SESSIONS_PER_YEAR) or None


def _regime_context(ticker, quintile, bars, regime_table, earnings_dates):
    moves = weekly_high_moves(bars, earnings_dates)
    entry = _table_entry(regime_table, ticker) if regime_table is not None else None
    edges = _edges_pct(entry, _entry_scale(entry)) if entry else None
    edges = edges or empirical_edges(moves)
    return moves, regime_weeks(moves, quintile, edges)


def _premium_detail(weeks: Sequence[dict], trigger_pct: float, sigma: float,
                    r: float = 0.045) -> dict | None:
    if not weeks or not sigma:
        return None
    n = len(weeks)
    hits = [w for w in weeks if w["high_pct"] >= trigger_pct - _EPS]
    p = len(hits) / n
    left = _left_after_first_tap(weeks, trigger_pct)
    if not left:
        return {"p_hit": p, "premium": 0.0, "price": 0.0, "sessions": None, "delta": None, "n_weeks": n}
    sessions = max(1, int(round(median(left) + 1)))          # the tap day counts
    spread = _spread(weeks)
    exts = [e for e in (extension(w["anchor"] * (1.0 + trigger_pct), w.get("prior_median"), spread)
                        for w in hits) if e is not None]
    raw = delta_for_extension(median(exts) if exts else None)
    proven = after_trigger_stats(weeks, trigger_pct)["deep_retrace_proven"]
    delta = raw if (raw <= DELTA_MID or proven) else DELTA_MID
    spot = 1.0 + trigger_pct                                  # anchor = 1: a fraction of Friday's close
    k = delta_strike(spot, sessions, sigma, delta, r=r)
    price = call_price(spot, k, sessions, sigma, r=r) if k else 0.0
    return {"p_hit": p, "premium": p * price, "price": price, "sessions": sessions,
            "delta": delta, "delta_capped": delta < raw, "n_weeks": n}


def expected_weekly_premium(ticker: str | None, quintile: int, trigger_pct: float,
                            bars: Sequence[dict], regime_table: Any = None, *,
                            iv: Any = None, earnings_dates: Iterable[Any] = (),
                            r: float = 0.045) -> float | None:
    """P(weekly high >= trigger | quintile) x the Black-Scholes price
    (`metrics._bs_price`) of the adaptive-delta call sold AT the trigger,
    with that quintile's median sessions left after the first tap (plus
    the tap day). Per unit of Friday's close (0.012 = 1.2% of it).

    The delta is the same adaptive map `decide` uses, from the quintile's
    median extension at this trigger, and capped at the money unless the
    quintile's own stretched weeks proved the pullback. Volatility is `iv`,
    or the bars' realized volatility. Premium only, as specified: what the
    trigger collects, not what assignment costs. That is why it leans to
    lower percentiles, and why `decide` (which does charge assignment) has
    the last word. None without enough weeks in the quintile."""
    _moves, weeks = _regime_context(ticker, quintile, bars, regime_table, earnings_dates)
    if len(weeks) < MIN_REGIME_WEEKS:
        return None
    sigma = _sigma(iv) or realized_vol(bars)
    d = _premium_detail(weeks, trigger_pct, sigma, r=r)
    return d["premium"] if d else None


def optimize_percentile(ticker: str | None, quintile: int, bars: Sequence[dict],
                        regime_table: Any = None, *, iv: Any = None,
                        earnings_dates: Iterable[Any] = (), grid: Sequence[float] = PCTILE_GRID,
                        r: float = 0.045) -> dict:
    """The trigger percentile that maximizes `expected_weekly_premium` for
    this regime: each percentile on `grid` (0.30 to 0.90) is turned into a
    trigger from the quintile's OWN weekly highs and scored. The percentile
    stops being a fixed 0.70 and becomes a property of the regime.

    Returns {ok, percentile, trigger_pct, premium, n_weeks, grid: [{pctile,
    trigger_pct, p_hit, price, sessions, delta, premium}]}; ok=False with
    the reason when the quintile has fewer than MIN_REGIME_WEEKS weeks."""
    _moves, weeks = _regime_context(ticker, quintile, bars, regime_table, earnings_dates)
    if len(weeks) < MIN_REGIME_WEEKS:
        return {"ok": False, "quintile": quintile, "n_weeks": len(weeks),
                "reason": f"only {len(weeks)} clean weeks in quintile {quintile} (need {MIN_REGIME_WEEKS})"}
    sigma = _sigma(iv) or realized_vol(bars)
    if not sigma:
        return {"ok": False, "quintile": quintile, "n_weeks": len(weeks),
                "reason": "no implied volatility and too little history for a realized one"}
    highs = [w["high_pct"] for w in weeks]
    rows = []
    for p in grid:
        t = ws.percentile(highs, p)
        d = _premium_detail(weeks, t, sigma, r=r)
        rows.append({"pctile": p, "trigger_pct": t, **(d or {"premium": 0.0})})
    best = max(rows, key=lambda x: (x["premium"], -abs(x["pctile"] - TRIGGER_PCTILE)))
    return {"ok": True, "quintile": quintile, "n_weeks": len(weeks), "sigma": sigma,
            "percentile": best["pctile"], "trigger_pct": best["trigger_pct"],
            "premium": best["premium"], "grid": rows}


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
           theta_history: Sequence[Any] = (), tapped: bool | None = None,
           ticker: str | None = None, regime_table: Any = None,
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

    REGIME. With `regime_table` (and `ticker`, unless the table is one
    ticker's entry), last week's Friday-to-Friday close is read from
    `bars`, its quintile is found, and that quintile's trigger70 is the
    trigger (IV-scaled as usual). The same quintile's weeks then supply the
    extension's wick spread, the odds of a tap and the sessions left after
    one, and the shortfall pool. Below MIN_REGIME_WEEKS those fall back to
    all weeks, and `regime.basis` says so. The earned-delta guardrail is
    evaluated inside the quintile only and never falls back. A ticker the
    table cannot answer keeps the v1 percentile trigger.
    """
    today_d, exp_d = ws._as_date(today), ws._as_date(friday_expiry)
    out: dict[str, Any] = {"schema": SCHEMA, "ok": False, "action": None, "reason": None}
    spot = ws._f(spot) or 0.0
    sigma = _sigma(iv_now)
    if spot <= 0 or today_d is None or exp_d is None or not sigma:
        out["reason"] = "needs a live price, today's date, the Friday expiry and implied volatility"
        return out
    rows = _rows(bars)
    # Last week's move: its own Friday close against the Friday before. A
    # missing or cut-short week gives no move, and so no regime (Codex, #425).
    wk = _weekly_closes(rows, today_d)
    prior_pct = last_week_move_pct(wk, today_d)
    cal = calibrate(bars, earnings_dates, iv_now=iv_now, iv_median=iv_median,
                    regime_table=regime_table,
                    prior_week_close_pct=prior_pct if regime_table is not None else None,
                    ticker=ticker)
    out["calibration"] = {k: v for k, v in cal.items() if k != "moves"}
    if not cal["ok"]:
        out["reason"] = cal["reason"]
        return out
    moves = cal["moves"]
    tp = cal["trigger_pct"]
    # ── the regime, when a table is given: one quintile for every input below
    info = cal.get("regime")
    regime_on = bool(info and info.get("source") == "regime")
    quintile = info["quintile"] if regime_on else None
    if regime_on:
        rmoves = regime_weeks(moves, quintile, info["edges_pct"])
        guard_moves = rmoves
        base = rmoves if len(rmoves) >= MIN_REGIME_WEEKS else moves
    else:
        rmoves, guard_moves, base = [], moves, moves
    out["regime"] = {"on": regime_on, "ticker": (str(ticker).upper() if ticker else None),
                     "prior_week_close_pct": prior_pct,
                     "quintile": quintile, "source": (info or {}).get("source") if info else None,
                     "table_trigger": (info or {}).get("trigger") if info else None,
                     "weeks_in_quintile": len(rmoves) if regime_on else None,
                     "basis": ("quintile" if regime_on and base is rmoves else "all weeks")}
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

    # The guardrail's evidence: this stock's own trigger weeks, and with a
    # regime only those that started in the same quintile.
    stats = after_trigger_stats(guard_moves, tp)
    out["after_trigger"] = stats
    spread = _spread(base)
    closes = [r_["close"] for r_ in wk]
    pm = median(closes[-PRIOR_CLOSES:]) if len(closes[-PRIOR_CLOSES:]) >= MIN_PRIOR_CLOSES else None
    ext = extension(trigger_price, pm, spread)
    raw_delta = delta_for_extension(ext)
    delta_w = raw_delta
    capped = False
    if raw_delta > DELTA_MID and not stats["deep_retrace_proven"]:
        delta_w, capped = DELTA_MID, True
    out["extension"] = ext
    if capped and regime_on:
        why = (f"in quintile {quintile} (weeks after a prior week like this one's {prior_pct:+.1f}%), "
               f"the stretched trigger weeks have not shown a harder pullback than the shallow ones "
               f"({len(guard_moves)} weeks in that quintile), so the delta stays at the money")
    elif capped:
        why = ("deep extension has not shown a harder retrace in this stock's "
               "own trigger weeks, so the delta stays at the money")
    else:
        why = None
    out["delta"] = {"adaptive": delta_w, "uncapped": raw_delta, "capped": capped, "why_capped": why}

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
    for w in base:
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
    pool = crossed_later or [w for w in base if _first_cross(w, tp) is not None]
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
    elif not tapped and p_hit == 0.0:
        # No past week ever reached the trigger: the tap has no price, but
        # its odds are zero, so the wait is the fallback alone (v5.37).
        ev_wait = max(0.0, ev_fb or 0.0)
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
    out["legs"] = {
        "theta_decay": theta, "strike_uplift": strike_up, "delta_uplift": delta_up,
        "weighted": weighted,
        "risk_and_miss": (diff - sum(weighted.values())) if diff is not None else None,
        "ev_diff": diff,
        "theta_dominant": theta_dominant,
        "trigger_too_far": theta_too_far(theta_dominant, theta_history, quintile),
    }
    out["history_record"] = {"week": _monday(today_d).isoformat(),
                             "ticker": (str(ticker).upper() if ticker else None),
                             "quintile": quintile, "trigger_pct": tp,
                             "theta_dominant": bool(theta_dominant),
                             "theta_decay": theta, "uplift": strike_up + delta_up}
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


def theta_too_far(theta_dominant: bool, history: Sequence[Any], quintile: int | None) -> bool:
    """Two weeks running, like for like. `history` items are the records
    `decide` returns as `history_record` (see the module docstring), most
    recent last; the most recent one in the SAME quintile must have been
    theta-dominant too. Plain booleans (the v1 contract) compare to the
    last entry, as before."""
    if not theta_dominant:
        return False
    items = list(history or [])
    if not items:
        return False
    if not isinstance(items[-1], Mapping):
        return bool(items[-1])
    same = [h for h in items if isinstance(h, Mapping) and h.get("quintile") == quintile]
    return bool(same and same[-1].get("theta_dominant"))


def last_week_move_pct(weekly_closes: list[dict], today: date) -> float | None:
    """Last week's Friday-to-Friday close in percent points, from
    `_weekly_closes`: last week's own final close against the week
    before's, or None when last week is missing or either week was cut
    short (Codex, #425)."""
    wk = weekly_closes
    if len(wk) < 2 or _monday(wk[-1]["date"]) != _monday(today) - timedelta(days=7):
        return None
    return _week_move_pct(wk[-2], wk[-1])


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
