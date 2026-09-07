"""hf_grade.py — did the reading precede the move?

The one thing the Hedge Fund Pulse has never been able to say is whether any
of it mattered. Every limitation section since Phase 2 has carried the line
"nothing here is calibrated against outcomes yet." This module is that
calibration, and it is deliberately modest about what it can prove.

What it does: for a stored weekly reading, look up what the matching market
did over the following 1, 2, 4 and 8 weeks, and keep the record.

What it refuses to do: call a verdict right or wrong. "Hedge funds reduced
exposure" is a fact about positioning; it implies nothing about what the S&P
does next, and a module that scored it as a forecast would be inventing a
claim the board never made. So the four weekly questions get a distribution
of forward returns and a base rate to compare it against — never a hit rate.

The one place a hit rate IS defined is crowding, because the brief asked for
it in those words: whether crowded trades preceded reversals. A crowded book
has a SIDE, so "reversal" has a meaning — the market moved against the side
the crowd was on. That is a real yes-or-no question, and it is scored with a
Wilson interval and the sample size next to it, so a run of luck cannot read
as a finding.

Three rules:

  * **Point in time or nothing.** A grade for week 30 may only use what was
    knowable in week 30. The caller reconstructs each week from a truncated
    series; this module never sees a series at all, only the verdict that
    was reached and the closes that came after it.

  * **A small sample says so.** Every figure carries its N and its interval.
    Below the floor, the answer is "not enough weeks yet" — which is a real
    answer and the honest one for a board that started keeping records in
    September 2026.

  * **The base rate is always shown.** "Crowded weeks reversed 55% of the
    time" means nothing until you know that every week reversed 52% of the
    time. The comparison is the finding; the raw number is not.

It is pure: dictionaries in, a dictionary out. No network, no files, no
clock — the caller supplies the weeks, so a grade is reproducible forever.

HEDGE_FUND_INTEL.md §7 phase 4.
"""

from __future__ import annotations

from datetime import date, timedelta

HF_GRADE_VERSION = "1.0.0"

# How far ahead to look, in weeks. Four horizons because positioning is said
# to matter over a month or two, not over a day.
HORIZONS = (1, 2, 4, 8)

MIN_N = 20          # below this many graded weeks, no conclusion is offered
Z = 1.96            # 95% Wilson interval

# The tradable proxy for each CFTC market. A futures position cannot be
# priced from the CFTC report itself, so the grade is measured on the fund
# the market's participants actually track.
GRADE_PROXY = {
    "sp500": "SPY", "nasdaq": "QQQ", "russell": "IWM", "djia": "DIA",
    "sec_staples": "XLP", "sec_energy": "XLE", "sec_financials": "XLF",
    "sec_health": "XLV", "sec_industrials": "XLI", "sec_utilities": "XLU",
    "sec_comm": "XLC",
}
# VIX futures have no honest cash proxy: the listed VIX funds roll a futures
# curve and bleed, so their return over eight weeks measures the roll, not
# the index. VIX crowding is still reported by the board; it is not graded.
NOT_GRADED = {"vix": ("No tradable proxy tracks the VIX index over weeks. The listed VIX funds "
                      "roll a futures curve, so an eight-week return measures the roll and not "
                      "the index.")}

SECTOR_PROXY = {"Materials": "XLB", "Communication Services": "XLC", "Energy": "XLE",
                "Financials": "XLF", "Industrials": "XLI", "Technology": "XLK",
                "Consumer Staples": "XLP", "Real Estate": "XLRE", "Utilities": "XLU",
                "Health Care": "XLV", "Consumer Discretionary": "XLY"}

REVERSED, HELD = "REVERSED", "HELD"


# ── weeks ───────────────────────────────────────────────────────────────────

def week_monday(week: str) -> date | None:
    """The Monday of an ISO week key like "2026-W36"."""
    try:
        year, wk = str(week).split("-W")
        return date.fromisocalendar(int(year), int(wk), 1)
    except (ValueError, AttributeError, TypeError):
        return None


def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_plus(week: str, n: int) -> str | None:
    """N weeks after this one.

    Done by date arithmetic rather than by stepping N places along a list of
    stored weeks. A list can have gaps — a week the board did not read, a
    holiday-shortened series — and stepping through it would silently call a
    twelve-week gap an eight-week horizon."""
    mon = week_monday(week)
    return None if mon is None else week_key(mon + timedelta(weeks=int(n)))


# ── the arithmetic ──────────────────────────────────────────────────────────

def wilson(k: int, n: int, z: float = Z) -> dict | None:
    """A share with its 95% interval. A share without one invites a reader to
    treat three out of four as a finding."""
    if not n or n <= 0:
        return None
    p = k / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return {"k": k, "n": n, "share": p,
            "low": max(0.0, centre - half), "high": min(1.0, centre + half)}


def forward_return(closes: dict, week: str, horizon: int) -> float | None:
    """The proxy's return from this week's close to the close N weeks on.

    Both weeks must have a close. A missing later week means the horizon has
    not arrived yet, which is not a zero and not a failure."""
    if not closes:
        return None
    later = week_plus(week, horizon)
    a, b = closes.get(week), closes.get(later)
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return None
    return None if a == 0 else (b / a) - 1.0


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def summarize(returns: list[float], min_n: int = MIN_N) -> dict:
    """A distribution of forward returns, with its own sample size attached.

    No verdict here: these are the returns that followed, and whether that
    number means anything is the reader's call once they can see N."""
    xs = [r for r in (returns or []) if r is not None]
    n = len(xs)
    up = sum(1 for r in xs if r > 0)
    return {"n": n, "median": _median(xs), "mean": (sum(xs) / n) if n else None,
            "share_up": wilson(up, n) if n else None,
            "best": max(xs) if xs else None, "worst": min(xs) if xs else None,
            "enough": n >= min_n, "min_n": min_n}


def reversal(side: str | None, ret: float | None) -> str | None:
    """Did the market move against the side the crowd was on?

    Only defined where there is a side. "Crowded" with no side is a large
    position with no direction, and asking whether it reversed is asking a
    question with no answer."""
    if ret is None or side not in ("long", "short"):
        return None
    return REVERSED if ((side == "long" and ret < 0) or (side == "short" and ret > 0)) else HELD


# ── grading crowding ────────────────────────────────────────────────────────

def episodes(rows: list[dict], state: str = "CROWDED") -> dict:
    """How many separate EPISODES the crowded weeks form, per market.

    This matters more than the week count. Crowded weeks arrive in runs — a
    book stays crowded for a month — and their forward windows overlap, so
    forty crowded weeks can be five independent events wearing forty hats.
    A Wilson interval assumes independent draws and is therefore optimistic
    on this data. Reporting the episode count beside the week count is what
    stops the interval being read as stronger than it is."""
    per: dict[str, list[str]] = {}
    for r in rows or []:
        if r.get("state") == state and r.get("key") and r.get("week"):
            per.setdefault(r["key"], []).append(r["week"])
    out, total = {}, 0
    for key, wks in per.items():
        ordered = sorted(set(wks))
        runs, prev = 0, None
        for w in ordered:
            mon = week_monday(w)
            if prev is None or mon is None or (mon - prev).days > 7:
                runs += 1
            prev = mon
        out[key] = {"weeks": len(ordered), "episodes": runs}
        total += runs
    return {"per_market": out, "total_episodes": total,
            "total_weeks": sum(v["weeks"] for v in out.values()),
            "note": ("An episode is a run of consecutive weeks in the same state. Forty crowded "
                     "weeks made of five episodes are five events, not forty, and every interval "
                     "on this page is computed as though they were forty.")}


def grade_crowded_weeks(weeks: list[dict], closes: dict, horizons=HORIZONS,
                        min_n: int = MIN_N) -> dict:
    """Every week a market was crowded, and what followed.

    `weeks` is one row per market per week: {"week", "market", "key", "state",
    "side"}. `closes` is {proxy symbol: {week: close}}. The base rate uses
    EVERY week of the same market, crowded or not, so the comparison is
    like for like: a sector that fell all year would otherwise look as though
    crowding predicted the fall."""
    per_market, overall = {}, {}
    # Episodes are counted per horizon, from the rows that horizon actually
    # graded. Counting them once over every reconstructed row and reusing
    # the total put VIX's three episodes — never graded at all — beside a
    # sample containing none of its weeks, and did the same for episodes too
    # recent to have matured. The number exists to temper the interval, so
    # it has to describe the same population the interval does.
    graded_rows: dict[int, list] = {h: [] for h in horizons}
    for h in horizons:
        crowded_hits, crowded_n = 0, 0
        base_hits, base_n = 0, 0
        for row in weeks or []:
            key = row.get("key")
            sym = GRADE_PROXY.get(key)
            if not sym:
                continue
            ret = forward_return(closes.get(sym) or {}, row.get("week"), h)
            verdict = reversal(row.get("side"), ret)
            if verdict is None:
                continue
            slot = per_market.setdefault(key, {"market": row.get("market"), "proxy": sym,
                                               "horizons": {}})
            hz = slot["horizons"].setdefault(str(h), {"crowded_k": 0, "crowded_n": 0,
                                                      "base_k": 0, "base_n": 0,
                                                      "crowded_returns": []})
            hz["base_n"] += 1
            base_n += 1
            if verdict == REVERSED:
                hz["base_k"] += 1
                base_hits += 1
            if row.get("state") == "CROWDED":
                hz["crowded_n"] += 1
                crowded_n += 1
                hz["crowded_returns"].append(ret)
                graded_rows[h].append(row)
                if verdict == REVERSED:
                    hz["crowded_k"] += 1
                    crowded_hits += 1
        overall[str(h)] = {
            "crowded": wilson(crowded_hits, crowded_n),
            "base": wilson(base_hits, base_n),
            "enough": crowded_n >= min_n,
            "lift": ((crowded_hits / crowded_n) - (base_hits / base_n))
                    if crowded_n and base_n else None,
        }
    for slot in per_market.values():
        for h, hz in slot["horizons"].items():
            hz["crowded"] = wilson(hz.pop("crowded_k"), hz.pop("crowded_n"))
            hz["base"] = wilson(hz.pop("base_k"), hz.pop("base_n"))
            hz["returns"] = summarize(hz.pop("crowded_returns"), min_n=min_n)
    per_horizon = {str(h): episodes(graded_rows[h]) for h in horizons}
    for h in horizons:
        overall[str(h)]["episodes"] = per_horizon[str(h)]["total_episodes"]
    # The per-market row shows the longest horizon's count, which is the
    # most conservative of the four and the one a reader should carry.
    longest = str(max(horizons)) if horizons else None
    for key, slot in per_market.items():
        by_market = (per_horizon.get(longest) or {}).get("per_market") or {}
        slot["episodes"] = (by_market.get(key) or {}).get("episodes")
    eps = episodes([r for r in (weeks or [])
                    if GRADE_PROXY.get(r.get("key"))])
    eps["graded_only"] = True
    eps["per_horizon"] = {h: v["total_episodes"] for h, v in per_horizon.items()}
    return {"overall": overall, "markets": per_market, "horizons": list(horizons),
            "min_n": min_n, "episodes": eps,
            "not_graded": {k: v for k, v in NOT_GRADED.items()},
            "reversal_rule": ("A crowded book has a side. It REVERSED when the market moved "
                              "against that side over the horizon, and HELD when it did not. "
                              "The base rate is every week of the same market, so a market that "
                              "fell all year cannot make crowding look predictive."),
            "note": ("This records what followed. It is not evidence that crowding causes "
                     "reversals, and the interval on every share is wide until the record is "
                     "long.")}


# ── grading the four weekly verdicts ────────────────────────────────────────

def grade_verdicts(readings: list[dict], closes: dict, horizons=HORIZONS,
                   min_n: int = MIN_N, proxy: str = "SPY") -> dict:
    """What the broad market did after each verdict value.

    Deliberately NOT scored as right or wrong. "Hedge funds reduced exposure"
    says nothing about what the S&P should do next, and turning it into a
    forecast would be putting a claim in the board's mouth. What is shown is
    the distribution of returns that followed each verdict, beside the
    distribution across all weeks."""
    out, all_returns = {}, {h: [] for h in horizons}
    px = closes.get(proxy) or {}
    for key in ("exposure", "leverage", "longs", "shorts"):
        by_verdict: dict[str, dict] = {}
        for r in readings or []:
            verdict = (r.get("verdicts") or {}).get(key)
            wk = r.get("week")
            if not verdict or not wk:
                continue
            for h in horizons:
                ret = forward_return(px, wk, h)
                if ret is None:
                    continue
                by_verdict.setdefault(verdict, {str(x): [] for x in horizons})[str(h)].append(ret)
        out[key] = {v: {h: summarize(rs, min_n=min_n) for h, rs in hs.items()}
                    for v, hs in by_verdict.items()}
    for r in readings or []:
        for h in horizons:
            ret = forward_return(px, r.get("week"), h)
            if ret is not None:
                all_returns[h].append(ret)
    return {"by_question": out, "proxy": proxy,
            "base": {str(h): summarize(rs, min_n=min_n) for h, rs in all_returns.items()},
            "horizons": list(horizons), "min_n": min_n,
            "note": ("A verdict about positioning is not a forecast of returns, so nothing here "
                     "is scored as right or wrong. These are the returns that followed each "
                     "answer, beside the returns across every week.")}


# ── the whole card ──────────────────────────────────────────────────────────

def build(crowded_weeks: list[dict] | None = None, readings: list[dict] | None = None,
          closes: dict | None = None, *, horizons=HORIZONS, min_n: int = MIN_N,
          source: str | None = None, as_of: str | None = None) -> dict:
    """Everything the record can say so far."""
    closes = closes or {}
    crowd = grade_crowded_weeks(crowded_weeks or [], closes, horizons, min_n)
    verdicts = grade_verdicts(readings or [], closes, horizons, min_n)
    graded = sum((h.get("crowded") or {}).get("n") or 0 for h in crowd["overall"].values())
    return {"version": HF_GRADE_VERSION, "crowding": crowd, "verdicts": verdicts,
            "as_of": as_of, "source": source, "horizons": list(horizons), "min_n": min_n,
            "n_crowded_weeks_graded": graded,
            "n_readings": len(readings or []),
            "n_market_weeks": len(crowded_weeks or []),
            "limitations": [
                "This records what followed a reading. It is not evidence that the reading "
                "caused what followed, and the board does not claim positioning predicts "
                "returns.",
                "Crowding is reconstructed from the CFTC series as it stood in each week, so a "
                "grade never sees data that arrived later. The four weekly verdicts cannot be "
                "reconstructed that way — they need short interest and flows that are not kept "
                "historically — so they are graded only from readings stored since the board "
                "started keeping them.",
                "A futures market is graded on the fund its participants track, not on the "
                "contract itself. VIX is not graded at all: its listed funds roll a curve, so "
                "their return measures the roll.",
                f"Below {min_n} graded weeks no share is offered as a finding, and every share "
                f"shown carries a 95% interval that stays wide for a long time.",
                "Those intervals are OPTIMISTIC, and knowingly so. Crowded weeks arrive in runs "
                "and their forward windows overlap, so forty crowded weeks can be five "
                "independent events. Every share is computed as though each week were a separate "
                "draw; the episode count beside it is the number that should temper it.",
            ]}


def headline(card: dict) -> dict:
    """The one line the panel leads with, or an honest refusal."""
    crowd = (card or {}).get("crowding") or {}
    rows = []
    for h in (card or {}).get("horizons") or []:
        o = (crowd.get("overall") or {}).get(str(h)) or {}
        c, b = o.get("crowded"), o.get("base")
        if not c or not b or not o.get("enough"):
            continue
        rows.append({"weeks": h, "crowded": c["share"], "base": b["share"],
                     "lift": o.get("lift"), "n": c["n"],
                     "low": c["low"], "high": c["high"]})
    if not rows:
        return {"available": False,
                "text": ("Not enough graded weeks yet to say whether crowded trades preceded "
                         "reversals more often than ordinary weeks did.")}
    best = max(rows, key=lambda r: abs(r["lift"] or 0))
    direction = "more often than" if (best["lift"] or 0) > 0 else "no more often than"
    return {"available": True, "row": best,
            "text": (f"Over {best['weeks']} week(s), crowded trades reversed "
                     f"{best['crowded'] * 100:.0f}% of the time against a base rate of "
                     f"{best['base'] * 100:.0f}% — {direction} an ordinary week, on "
                     f"{best['n']} graded weeks.")}
