"""
debt_risk.py — how much a company owes, and how exposed it is to higher rates (v5.39).

Jerry: "The tech market has been selling off because interest rates are
going higher. The companies with the highest debt are selling off. The
smart money has been shorting these companies because they have tools to
see it, like the Bloomberg terminal. I'd like to do the same."

Two levels, from two sources:

  THE SCREEN (every watchlist stock, every scan). Read from the Yahoo
  profile the watchlist scan already downloads, so it costs no new
  calls: total debt, cash, EBITDA, free cash flow, market cap, and the
  shares sold short now and a month earlier. Plus the stock's measured
  RATE MOVE: its average move, in percent, for each +0.10% the 10-year
  Treasury yield rose, over the last six months of trading days.
  `assess` turns the balance sheet into a level (High / Elevated / Low)
  with the reasons in words.

  THE CARD (one stock, on demand). From the company's own SEC filings:
  when its debt comes due (next 12 months, years 2 to 5, after), the
  interest it paid over the last twelve months, how many times its
  operating profit covers that interest, and the average rate it pays.

What was measured before the thresholds were set (53 stocks, Oct 2026):
the raw rate move runs from -6% (OKLO) to +1.5% (VLO) per +0.10%, median
-2.3%: almost every stock falls when yields rise, the jumpiest most. Net
of the market's own move it is small and only loosely tied to debt
(correlation -0.18 on that list). So the rate move is shown, sortable, but
the risk LEVEL comes from the balance sheet and the shorts, where debt
actually is.

Pure apart from nothing: every input is passed in. Never raises.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Optional, Sequence

# ── knobs ────────────────────────────────────────────────────────────────────
LEV_HIGH = 4.0          # net debt / EBITDA at or above this: heavily borrowed
LEV_MID = 2.5           # ... and this: borrowed
SIZE_HIGH = 0.50        # total debt / market cap at or above this: debt is half the company
SIZE_MID = 0.25
BURN_HIGH = 0.10        # yearly cash burn of at least 10% of the debt: a heavy burn
SHORT_HIGH = 10.0       # % of the float sold short: heavily shorted
SHORT_RISE = 10.0       # shares short up at least this % on a month earlier: shorts piling in
LEVEL_HIGH = 4          # points for "High"
LEVEL_ELEVATED = 2      # points for "Elevated"
BETA_SESSIONS = 126     # six months of trading days for the rate move
BETA_MIN = 60           # fewer paired days than this and no rate move is given
DUE_SOON_SHARE = 0.25   # a quarter or more of the debt due within two years: refinancing wall
COVER_LOW = 3.0         # operating profit under 3x the interest bill: thin cover
INTEREST_STALE_DAYS = 200   # an interest figure older than this, against the balance sheet, is not current


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _r(v: Any, dp: int = 2) -> Optional[float]:
    x = _f(v)
    return round(x, dp) if x is not None else None


# ── the screen ───────────────────────────────────────────────────────────────
def screen_fields(info: Mapping[str, Any] | None) -> dict:
    """The debt and short-interest numbers from a Yahoo profile, in dollars
    and percent. Missing stays None: "not reported" is never zero debt."""
    i = info or {}
    debt, cash = _f(i.get("totalDebt")), _f(i.get("totalCash"))
    ebitda, fcf = _f(i.get("ebitda")), _f(i.get("freeCashflow"))
    mcap = _f(i.get("marketCap"))
    short_now, short_prev = _f(i.get("sharesShort")), _f(i.get("sharesShortPriorMonth"))
    spf = _f(i.get("shortPercentOfFloat"))
    # Net debt needs both: a missing cash balance is unknown, not zero, or a
    # cash-rich company with a thin profile reads as heavily borrowed
    # (Codex, #428).
    net = (debt - cash) if (debt is not None and cash is not None) else None
    # A foreign filer reports its debt in its own currency (Novo Nordisk in
    # kroner) while its market value is in dollars: debt against market
    # value would mix the two, so it is left out. Ratios inside one
    # statement (debt to EBITDA, burn to debt) stay.
    ccy = str(i.get("financialCurrency") or "USD").upper()
    same = ccy == str(i.get("currency") or "USD").upper()
    out = {
        "debt_total": debt, "debt_cash": cash, "net_debt": net,
        "ebitda": ebitda, "fcf": fcf, "debt_ccy": ccy,
        # v5.40: the tradable share count, for FINRA's short interest as a
        # share of the float (short_trend.py).
        "float_shares": _f(i.get("floatShares")),
        "net_debt_ebitda": (round(net / ebitda, 2) if (net is not None and ebitda and ebitda > 0) else None),
        "debt_to_mcap": (round(debt / mcap, 3) if (same and debt is not None and mcap and mcap > 0) else None),
        # Yahoo gives the float share as a fraction (0.19 = 19%).
        "short_pct": (round(spf * 100.0, 2) if spf is not None else None),
        "short_chg": (round((short_now / short_prev - 1.0) * 100.0, 1)
                      if (short_now is not None and short_prev and short_prev > 0) else None),
    }
    return out


def rate_beta(closes: Mapping[str, float], yields: Mapping[str, float],
              sessions: int = BETA_SESSIONS) -> Optional[dict]:
    """The stock's average move (%) for each +0.10% in the 10-year yield:
    the slope of its daily return on the yield's daily change, over the
    last `sessions` days both series traded. `closes` and `yields` map ISO
    dates to the close and to the yield in percent (5.21)."""
    days = sorted(d for d in closes if d in yields)
    xs, ys = [], []
    for a, b in zip(days, days[1:]):
        ca, cb = _f(closes[a]), _f(closes[b])
        ya, yb = _f(yields[a]), _f(yields[b])
        if not ca or not cb or ca <= 0 or ya is None or yb is None:
            continue
        xs.append((yb - ya) * 100.0)          # basis points
        ys.append((cb / ca - 1.0) * 100.0)    # percent
    xs, ys = xs[-sessions:], ys[-sessions:]
    n = len(xs)
    if n < BETA_MIN:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return {"move_per_10bp": round(sxy / sxx * 10.0, 2),
            "r": round(sxy / math.sqrt(sxx * syy), 2), "n": n}


def _money(v: Optional[float], ccy: str = "USD") -> str:
    if v is None:
        return "—"
    a = abs(v)
    cur = "$" if (ccy or "USD").upper() == "USD" else f"{ccy.upper()} "
    s = (f"{cur}{a / 1e12:.1f}T" if a >= 1e12 else f"{cur}{a / 1e9:.1f}B" if a >= 1e9
         else f"{cur}{a / 1e6:.0f}M" if a >= 1e6 else f"{cur}{a:,.0f}")
    return ("-" if v < 0 else "") + s


_money_ccy = _money


def assess(f: Mapping[str, Any]) -> dict:
    """{level, score, reasons}. Points for how much is borrowed against
    earnings and against the company's size, for burning cash while in
    debt, and, only when there is debt to worry about, for shorts piling
    in. High at LEVEL_HIGH points, Elevated at LEVEL_ELEVATED."""
    debt, net = _f(f.get("debt_total")), _f(f.get("net_debt"))
    ebitda, fcf = _f(f.get("ebitda")), _f(f.get("fcf"))
    lev, size = _f(f.get("net_debt_ebitda")), _f(f.get("debt_to_mcap"))
    ccy = str(f.get("debt_ccy") or "USD")

    def _money(v):                      # the figures are in the filer's own currency
        return _money_ccy(v, ccy)
    if debt is None:
        return {"level": None, "score": None, "reasons": ["No debt figure was reported for this stock."]}
    if net is not None and net <= 0:
        return {"level": "Low", "score": 0,
                "reasons": [f"More cash than debt: {_money(-net)} net cash."]}
    pts, why = 0, []
    if ebitda is not None and ebitda <= 0:
        pts += 2
        why.append(f"No operating profit (EBITDA {_money(ebitda)}) to pay {_money(debt)} of debt.")
    elif lev is not None and lev >= LEV_HIGH:
        pts += 2
        why.append(f"Net debt is {lev:.1f}x a year of operating profit (EBITDA).")
    elif lev is not None and lev >= LEV_MID:
        pts += 1
        why.append(f"Net debt is {lev:.1f}x a year of operating profit (EBITDA).")
    if size is not None and size >= SIZE_HIGH:
        pts += 2
        why.append(f"Debt of {_money(debt)} is {size * 100:.0f}% of the company's market value.")
    elif size is not None and size >= SIZE_MID:
        pts += 1
        why.append(f"Debt of {_money(debt)} is {size * 100:.0f}% of the company's market value.")
    if fcf is not None and fcf < 0:
        heavy = debt > 0 and -fcf >= BURN_HIGH * debt
        pts += 2 if heavy else 1
        why.append(f"Burning cash ({_money(fcf)} free cash flow a year) while in debt: "
                   "it may have to borrow more at today's higher rates.")
    if pts:
        sp, sc = _f(f.get("short_pct")), _f(f.get("short_chg"))
        if sp is not None and sp >= SHORT_HIGH:
            pts += 1
            why.append(f"{sp:.1f}% of its tradable shares are sold short.")
        if sc is not None and sc >= SHORT_RISE:
            pts += 1
            why.append(f"Shorts are piling in: {sc:+.0f}% more shares short than a month earlier.")
    level = "High" if pts >= LEVEL_HIGH else "Elevated" if pts >= LEVEL_ELEVATED else "Low"
    if not why:
        why.append("Debt is modest next to its earnings and its size.")
    return {"level": level, "score": pts, "reasons": why}


def row_fields(info: Mapping[str, Any] | None, closes: Mapping[str, float] | None,
               yields: Mapping[str, float] | None) -> dict:
    """Everything the watchlist row carries: the screen numbers, the rate
    move and the assessment."""
    out = screen_fields(info)
    rb = rate_beta(closes or {}, yields or {}) if (closes and yields) else None
    out["rate_move"] = rb["move_per_10bp"] if rb else None
    out["rate_r"] = rb["r"] if rb else None
    a = assess(out)
    out.update({"debt_risk": a["level"], "debt_score": a["score"], "debt_reasons": a["reasons"]})
    return out


def ticker_fields(ticker: Any, closes: Mapping[str, float] | None,
                  yields: Mapping[str, float] | None) -> dict:
    """row_fields for a stock off the watchlist, from a yfinance Ticker. A
    thin .info without a market value gets fast_info's before anything is
    worked out, as the watchlist scan does, so debt against market value
    and its points still count (Codex, #428)."""
    try:
        info = dict(ticker.info or {})
    except Exception:  # noqa: BLE001
        info = {}
    if not info.get("marketCap"):
        try:
            mc = getattr(ticker.fast_info, "market_cap", None)
        except Exception:  # noqa: BLE001
            mc = None
        if mc:
            info["marketCap"] = mc
    out = row_fields(info, closes, yields)
    out["market_cap"] = info.get("marketCap")
    return out


# ── the card: the company's own filings ─────────────────────────────────────
MATURITY_TAGS = (
    ("Next 12 months", "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths"),
    ("Year 2", "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo"),
    ("Year 3", "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree"),
    ("Year 4", "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour"),
    ("Year 5", "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive"),
    ("After year 5", "LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive"),
)
INTEREST_TAGS = ("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt",
                 "InterestAndDebtExpense")


def _gaap(facts: Mapping | None) -> dict:
    return ((facts or {}).get("facts") or {}).get("us-gaap") or {}


def _annual_rows(gaap: Mapping, tag: str) -> list[dict]:
    """A tag's USD values from annual reports, the latest filing of each
    period end winning."""
    rows = ((gaap.get(tag) or {}).get("units") or {}).get("USD") or []
    best: dict = {}
    for r in rows:
        if r.get("form") not in ("10-K", "10-K/A") or r.get("val") is None or not r.get("end"):
            continue
        cur = best.get(r["end"])
        if cur is None or str(r.get("filed") or "") > str(cur.get("filed") or ""):
            best[r["end"]] = r
    return [best[k] for k in sorted(best)]


def maturities(facts: Mapping | None) -> Optional[dict]:
    """When the debt comes due, from the latest annual report that states
    it. Only annual reports: in quarterly filings the same tags mean
    different windows from one company to the next. None when not reported."""
    gaap = _gaap(facts)
    first = _annual_rows(gaap, MATURITY_TAGS[0][1])
    if not first:
        return None
    end = first[-1]["end"]
    buckets = []
    for label, tag in MATURITY_TAGS:
        hit = next((r for r in reversed(_annual_rows(gaap, tag)) if r["end"] == end), None)
        buckets.append({"label": label, "value": _f(hit["val"]) if hit else None})
    vals = [b["value"] for b in buckets if b["value"] is not None]
    total = sum(vals) if vals else None
    # A year the filing does not tag is unknown, not zero: the two-year
    # figure needs both years, and its share needs every year, or a partly
    # tagged schedule reads as a refinancing wall (Codex, #428).
    complete = len(vals) == len(buckets)
    first_two = [b["value"] for b in buckets[:2]]
    due2 = sum(first_two) if all(v is not None for v in first_two) else None
    return {"as_of": end, "buckets": buckets, "total": total, "complete": complete,
            "due_2y": due2,
            "due_2y_share": (due2 / total if (complete and due2 is not None and total) else None),
            "source": "the company's annual report (SEC)"}


def interest_ttm(facts: Mapping | None, *, as_of: Optional[str] = None) -> Optional[dict]:
    """Interest paid on borrowings over the last four quarters, from the
    filings. The tag reported most recently wins, as fundamentals.py does.
    None when not reported, or when the latest figure is old against
    `as_of` (Apple stopped reporting it separately in 2023)."""
    try:
        import fundamentals as F
    except Exception:  # noqa: BLE001
        return None
    gaap = _gaap(facts)
    best = None
    for rank, tag in enumerate(INTEREST_TAGS):
        entry = gaap.get(tag)
        if not entry or "USD" not in (entry.get("units") or {}):
            continue
        try:
            qs = F.discrete_quarters(F.latest_filed(entry, "USD"), aggregate="sum")
        except Exception:  # noqa: BLE001
            continue
        if not qs:
            continue
        score = (qs[-1]["end"], len(qs), -rank)
        if best is None or score > best[0]:
            best = (score, tag, qs)
    if not best:
        return None
    _s, tag, qs = best
    t = F.ttm(qs, aggregate="sum")
    if not t or t.get("value") is None:
        return None
    if as_of:
        try:
            gap = (date.fromisoformat(as_of[:10]) - date.fromisoformat(t["period_end"][:10])).days
        except (TypeError, ValueError):
            gap = 0
        if gap > INTEREST_STALE_DAYS:
            return None
    return {"value": abs(float(t["value"])), "tag": tag, "period_end": t["period_end"]}


def ten_year_series(fred_series=None, *, today: Optional[date] = None, days: int = 400) -> list:
    """The 10-year Treasury yield (FRED DGS10), daily, for the last `days`.
    The shared reader is asked for its DEFAULT history and the slice is
    taken here: treasury caches a series by its id alone, so asking it for
    13 months would leave its three-year correlation reader short until
    the cache expired (Codex, #428)."""
    if fred_series is None:
        return []
    try:
        rows = fred_series("DGS10") or []
    except Exception:  # noqa: BLE001
        return []
    start = ((today or date.today()) - timedelta(days=days)).isoformat()
    return [(str(d)[:10], v) for d, v in rows if str(d)[:10] >= start]


def yield_context(yields: Mapping[str, float] | None) -> Optional[dict]:
    """The 10-year yield now, a month ago and a year ago."""
    if not yields:
        return None
    days = sorted(yields)
    now_d = days[-1]
    now = _f(yields[now_d])

    def back(n_days: int) -> Optional[float]:
        target = (date.fromisoformat(now_d) - timedelta(days=n_days)).isoformat()
        prior = [d for d in days if d <= target]
        return _f(yields[prior[-1]]) if prior else None

    m, y = back(30), back(365)
    return {"as_of": now_d, "now": now,
            "change_1m": _r(now - m) if (now is not None and m is not None) else None,
            "change_1y": _r(now - y) if (now is not None and y is not None) else None}


def card(symbol: str, row: Mapping[str, Any] | None, facts: Mapping | None,
         yields: Mapping[str, float] | None) -> dict:
    """The Debt & rates card for one stock: the watchlist row's screen
    numbers and assessment, plus the filings' debt schedule and interest."""
    sym = str(symbol or "").upper()
    r = dict(row or {})
    out: dict[str, Any] = {"symbol": sym, "ok": True}
    keys = ("debt_total", "debt_cash", "net_debt", "ebitda", "fcf", "debt_ccy", "net_debt_ebitda", "debt_to_mcap",
            "short_pct", "short_chg", "rate_move", "rate_r", "debt_risk", "debt_score", "debt_reasons",
            "market_cap")
    out["screen"] = {k: r.get(k) for k in keys}
    out["on_watchlist"] = bool(row)
    mat = maturities(facts)
    out["maturities"] = mat
    try:
        import fundamentals as F
        bs = F.instant(facts, "long_term_debt") if facts else {}
        bs_as_of = (bs or {}).get("as_of")
        op = F.metric(facts, "operating_income") if facts else {}
    except Exception:  # noqa: BLE001
        bs_as_of, op = None, {}
    intr = interest_ttm(facts, as_of=bs_as_of)
    out["interest"] = intr
    debt = _f(r.get("debt_total"))
    op_v = _f((op or {}).get("value"))
    out["coverage"] = (round(op_v / intr["value"], 1)
                       if (intr and intr["value"] > 0 and op_v is not None and op_v > 0) else None)
    out["avg_rate"] = (round(intr["value"] / debt * 100.0, 2) if (intr and debt and debt > 0) else None)
    out["operating_income"] = op_v
    out["rates"] = yield_context(yields)
    # The filings add their own warnings to the screen's reasons.
    extra = []
    if mat and mat.get("due_2y_share") is not None and mat["due_2y_share"] >= DUE_SOON_SHARE and mat["due_2y"] > 0:
        extra.append(f"{_money(mat['due_2y'])} ({mat['due_2y_share'] * 100:.0f}% of its scheduled debt) "
                     "comes due within two years and will have to be refinanced at today's rates.")
    if intr and op_v is not None and op_v <= 0:
        extra.append(f"It has an operating loss ({_money(op_v)} over the last year), so it pays its "
                     f"{_money(intr['value'])} interest bill out of cash or new borrowing.")
    elif out["coverage"] is not None and out["coverage"] < COVER_LOW:
        extra.append(f"Operating profit covers its {_money(intr['value'])} interest bill only "
                     f"{out['coverage']:.1f}x.")
    out["filing_flags"] = extra
    return out
