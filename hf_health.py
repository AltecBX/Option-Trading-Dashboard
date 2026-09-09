"""hf_health.py — is each source actually working? (v4.92)

PURE. No I/O, no clock, no files. It is handed observation records and a
moment, and returns a card.

WHY THIS EXISTS

Before this there was no per-provider record of last success, last failure,
expected cadence, staleness or "no key configured". `gather()` collected
free-text failure strings and the board showed them as "sources that had
nothing this week". That reads the same whether a provider is down, has never
been configured, or genuinely had nothing new to say — and the third is a
NORMAL, frequent answer for a source that publishes twice a month.

The distinction this module draws is the one that matters:

  * A source that ANSWERED and whose data has moved on is working.
  * A source that ANSWERS but keeps handing back the same old period is
    STALE — the failure that is invisible without both dates.
  * A source that did not answer is DOWN.
  * A source with no key configured is NOT CONFIGURED, which is a decision
    rather than a fault, and must never be reported as a breakage.
  * A source with no attempt in the window is UNKNOWN. Not "fine".

Every cadence below is a published fact about the provider, not a guess, and
each carries its source in a comment. The grace period is what separates
"has not published yet" from "has stopped publishing" — a report due Friday
is not late on Friday morning.

HEDGE_FUND_INTEL.md §5h.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import hf_obs as OBS

HF_HEALTH_VERSION = "1.0.0"

HEALTHY = "WORKING"
STALE = "STALE"
DOWN = "NOT ANSWERING"
NOT_CONFIGURED = "NOT CONFIGURED"
UNKNOWN = "NOT CHECKED"

# Three separate numbers per provider, and keeping them separate is the whole
# point. Staleness is judged on the age of the newest `as_of` a source has
# given, and that age is the sum of THREE things:
#
#   every_hours  how often a NEW reading arrives (the period it covers)
#   lag_hours    how far BEHIND that period a reading already is when it
#                arrives — the publication lag
#   grace_hours  how much later than that it may be before it is worth saying
#
# The first version folded the lag into the grace and got it badly wrong for
# the one source where the lag dwarfs the period: Form PF is quarterly but
# reaches the OFR about five months later, so its newest reading is ROUTINELY
# five to eight months old. The live panel duly called it STALE on its first
# day — a false alarm on a source that was working perfectly, which is exactly
# how a health panel teaches its reader to ignore it.
#
# Every number below is the provider's own published schedule, not a guess.
EXPECTED = {
    # Positions as of Tuesday, published the following Friday at 3:30 PM ET —
    # the same three days `hf_sources.CFTC_PUBLICATION_LAG_DAYS` applies.
    "cftc.tff": {"every_hours": 168, "lag_hours": 72, "grace_hours": 72,
                 "cadence": "once a week, on Friday afternoon"},
    # Settled twice a month; published about eight BUSINESS days later, which
    # is the twelve calendar days `hf_sources.finra_public_on` walks.
    "finra.short_interest": {"every_hours": 360, "lag_hours": 288,
                             "grace_hours": 168, "cadence": "twice a month"},
    # One file per trading session, posted that evening. The grace covers a
    # long weekend, which is why it is not 24.
    "finra.short_volume": {"every_hours": 24, "lag_hours": 24, "grace_hours": 96,
                           "cadence": "every trading day"},
    # Quarterly, and about five months behind — so between one publication and
    # the next, the newest reading ages from ~150 days to ~240 days, and none
    # of that is a fault.
    "ofr.form_pf": {"every_hours": 2184, "lag_hours": 3600, "grace_hours": 720,
                    "cadence": "once a quarter, and about five months behind"},
    "uw.etf_creations": {"every_hours": 24, "lag_hours": 24, "grace_hours": 96,
                         "cadence": "every trading day"},
    "uw.sector_tide": {"every_hours": 24, "lag_hours": 24, "grace_hours": 96,
                       "cadence": "every trading day"},
    # The banks send their notes weekly and the wires quote them; a week with
    # no quoted note is ordinary, not a failure.
    "press.prime_broker": {"every_hours": 168, "lag_hours": 24, "grace_hours": 168,
                           "cadence": "weekly, when a wire quotes one"},
}

# Plain names, because "uw.sector_tide" is a key and not something to read.
LABELS = {
    "cftc.tff": "CFTC Traders in Financial Futures",
    "finra.short_interest": "FINRA consolidated short interest",
    "finra.short_volume": "FINRA daily short volume",
    "ofr.form_pf": "Form PF leverage, via the Treasury's OFR",
    "uw.etf_creations": "Sector ETF creations and redemptions",
    "uw.sector_tide": "Sector options premium tide",
    "press.prime_broker": "Prime broker notes quoted by the wires",
}


def _parse(ts):
    try:
        d = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _hours_between(later, earlier) -> float | None:
    a, b = _parse(later), _parse(earlier)
    if a is None or b is None:
        return None
    return (a - b).total_seconds() / 3600.0


def _newest(values):
    got = [v for v in values if v]
    return max(got) if got else None


def assess(source: str, records: list[dict], now: str) -> dict:
    """One provider's card, from every record it appears in.

    `records` may be all of them — this filters. Passing the whole window is
    deliberate: a caller that had to pre-group them could group them wrongly,
    and the grouping rule belongs here with the states it decides."""
    spec = EXPECTED.get(source) or {}
    mine = [r for r in records if r.get("source") == source]
    health = [r for r in mine if r.get("metric") == OBS.HEALTH]
    readings = [r for r in mine if r.get("metric") != OBS.HEALTH]

    card = {
        "source": source, "label": LABELS.get(source, source),
        "cadence": spec.get("cadence"),
        "every_hours": spec.get("every_hours"),
        "n_attempts": len(health), "n_readings": len(readings),
        "last_attempt": _newest(r.get("observed_at") for r in health),
        "last_ok": _newest(r.get("observed_at") for r in health
                           if r.get("quality") == OBS.OK),
        "newest_as_of": _newest(r.get("as_of") for r in readings),
        "newest_public_on": _newest(r.get("public_on") for r in readings),
    }

    if not health:
        card.update({"state": UNKNOWN,
                     "why": "no attempt to read this source is on record yet"})
        return card

    last = max(health, key=lambda r: r.get("observed_at") or "")
    card["last_quality"] = last.get("quality")
    card["last_note"] = last.get("note")

    if last.get("quality") == OBS.AUTH_MISSING:
        card.update({"state": NOT_CONFIGURED,
                     "why": "no key is configured for this source, so it is "
                            "not read at all — a decision, not a fault"})
        return card

    if last.get("quality") != OBS.OK:
        hrs = _hours_between(now, card["last_ok"]) if card["last_ok"] else None
        card.update({
            "state": DOWN,
            "hours_since_ok": hrs,
            "why": (last.get("note") or "it did not answer")
                   + (f"; last answered {_spell_hours(hrs)} ago" if hrs else
                      "; it has never answered"),
        })
        return card

    # It answered. The second question is whether what it answered with has
    # moved on — a provider that keeps handing back the same period is the
    # failure that is invisible without both dates.
    stale_after = (float(spec.get("every_hours") or 0)
                   + float(spec.get("lag_hours") or 0)
                   + float(spec.get("grace_hours") or 0))
    age = _age_hours(card["newest_public_on"] or card["newest_as_of"], now)
    card["hours_since_data"] = age
    if stale_after and age is not None and age > stale_after:
        card.update({
            "state": STALE,
            "why": (f"it is answering, but the newest reading it has given is "
                    f"{_spell_hours(age)} old and this source publishes "
                    f"{spec.get('cadence')}"),
        })
        return card
    card.update({"state": HEALTHY,
                 "why": f"it answered, and its newest reading is current for a "
                        f"source that publishes {spec.get('cadence')}"})
    return card


def _age_hours(day, now) -> float | None:
    """How old a DATE is, measured to the end of that day.

    A settlement dated today is nought hours old, not twelve — the reading
    describes the whole day, and treating it as a midnight instant would make
    every same-day figure look half a day stale."""
    d = _parse(str(day)[:10] + "T23:59:59+00:00") if day else None
    n = _parse(now)
    if d is None or n is None:
        return None
    return max(0.0, (n - d).total_seconds() / 3600.0)


def _spell_hours(h) -> str:
    if h is None:
        return "an unknown time"
    if h < 48:
        n = max(1, int(round(h)))
        return f"{n} hour" if n == 1 else f"{n} hours"
    n = int(round(h / 24))
    return f"{n} day" if n == 1 else f"{n} days"


def card(records: list[dict], now: str, sources=None) -> dict:
    """Every source, worst first, with a one-line summary a reader can act on."""
    names = list(sources or EXPECTED)
    rows = [assess(s, records, now) for s in names]
    order = {DOWN: 0, STALE: 1, UNKNOWN: 2, NOT_CONFIGURED: 3, HEALTHY: 4}
    rows.sort(key=lambda r: (order.get(r["state"], 5), r["label"]))
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    broken = [r for r in rows if r["state"] in (DOWN, STALE)]
    return {
        "available": True, "as_of": now, "rows": rows, "counts": counts,
        "n_sources": len(rows), "n_broken": len(broken),
        "version": HF_HEALTH_VERSION,
        "headline": _headline(rows, counts),
        # The window this was measured over is part of the answer: "everything
        # is fine" from an empty log means nothing was checked.
        "n_records": len([r for r in records if r.get("source") in set(names)]),
    }


def _headline(rows: list[dict], counts: dict) -> str:
    if not rows:
        return "Nothing has been read yet, so nothing can be said about the sources."
    down = [r["label"] for r in rows if r["state"] == DOWN]
    stale = [r["label"] for r in rows if r["state"] == STALE]
    if down:
        return ("Not answering: " + ", ".join(down)
                + (f". Stale: {', '.join(stale)}." if stale else "."))
    if stale:
        return "Answering, but out of date: " + ", ".join(stale) + "."
    unknown = counts.get(UNKNOWN, 0)
    if unknown == len(rows):
        return "No source has been checked yet."
    off = counts.get(NOT_CONFIGURED, 0)
    return (f"{counts.get(HEALTHY, 0)} of {len(rows)} sources are working"
            + (f"; {off} are not configured" if off else "")
            + (f"; {unknown} have not been checked" if unknown else "") + ".")
