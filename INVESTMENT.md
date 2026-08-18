# Investment tab — Phases 1 and 2

The dashboard's other tabs ask what a stock will do this week. This one asks
whether the business behind the ticker is worth owning at all, and at what
price. Four questions, in this order:

1. Is this a strong, profitable business?
2. Are revenue and earnings per share growing?
3. Is it cheap compared with its own fundamentals?
4. At what price would it be worth owning?

Phase 2 adds two more:

5. Are analyst expectations improving or deteriorating?
6. Is the apparent cheapness actually a value trap?

There is still no cash-secured-put optimizer, no LEAPS optimizer, no full
fair-value engine, no structure comparator and no reverse DCF — those are
Phase 3, and this document ends with what is ready for them.

---

## Where the numbers come from

| What | Source | Module |
|---|---|---|
| Revenue, net income, earnings per share, share count, cash flow, capital spending | **SEC EDGAR Company Facts** (XBRL) — what the company reported and signed | `fundamentals.py` |
| Shares outstanding | SEC filing cover page (`dei:EntityCommonStockSharesOutstanding`) | `fundamentals.py` |
| Business description, moat tags | **Item 1, Business** of the latest 10-K, read once and cached by accession | `fundamentals.py` |
| Share price | **Schwab first**, then the existing Yahoo chart fallback — the same path the rest of the app uses | `options_dashboard._invest_quote` |
| Forward earnings estimates | the app's existing analyst client | `analyst_client.get_eps_estimates` |
| 10-year Treasury yield | the official daily par yield curve, same as the Treasuries tab | `treasury.ten_year()` |
| Daily price history for the chart | `load_daily` (Schwab, then fallbacks) | `options_dashboard._gap_daily` |

No new Treasury client, no new options or volatility code, no second
Black-Scholes, no second CIK map, no second SEC transport. `fundamentals.py`
imports `sec_filings.py` for the ticker→CIK lookup, the request throttle and
the SEC-mandated User-Agent, so the app still has exactly one SEC client.

---

## Architecture

```
fundamentals.py    SEC Company Facts -> normalized reported fundamentals
                   (network + disk cache; no opinions)
invest_engine.py   pure maths: yields, growth, EPS decomposition, verdict
                   (no network, no disk, no clock — fully unit-testable)
invest_scan.py     providers, normalized snapshot, daily store, payloads
                   (everything with a clock, a network or a disk)
tab-invest.jsx     reads /api/invest and renders it. Talks to no provider.
```

`invest_scan.configure()` injects every provider as a function. Replacing the
analyst-estimate provider with a different vendor later is a change to one
lambda in `options_dashboard.py`: no field name, no payload shape and no line
of the tab changes.

### Endpoints

- `GET /api/invest?symbol=X&years=3|5&force=0|1` — the whole payload
- `GET /api/invest/history?symbol=X&years=3|5` — just the chart series
- `GET /api/invest/config` — the effective thresholds and their hash

### The store

`<data>/invest/snapshots/SYM.jsonl` — one row per calendar day, appended
atomically (temp file, then rename), last write of a day winning. Each row
carries date, ticker, price, market value, trailing revenue and earnings,
both growth rates, the forward estimates, forward and trailing price/earnings,
both yields, the estimate trend, the 10-year yield and the source of every
field.

`<data>/invest/latest/SYM.json` — the full last snapshot, which is also what
the staleness fallback reads.

`<data>/invest/facts/SYM.json` — the raw Company Facts payload, cached 12
hours. `<data>/invest/profiles/SYM.json` — the business description, cached
forever, because a filing never changes.

Snapshots accumulate two ways: opening the tab for a ticker records that
ticker, and a daily thread records the **starred** watchlist after 5pm.
Deliberately not all 1,289 names — that would be 1,289 SEC downloads a day
for a store nothing has asked for yet.

### Staleness

Every field arrives with `{value, source, as_of, basis, stale, age_hours}`.
When a provider fails there are exactly three outcomes:

1. Provider answers → the live value.
2. Provider fails, a recent stored value exists → that value, flagged
   **STALE** with its age.
3. Provider fails and the stored value is past its shelf life → **N/A** with
   the reason.

The shelf lives are in `thresholds.json`: 24 hours for a price, 7 days for
the Treasury yield, 30 days for an analyst estimate. A share price from three
weeks ago is not stale, it is wrong, and a small grey "stale" tag does not fix
that.

---

## What the filings actually look like

Everything below was **measured on live EDGAR across seventeen tickers before
a line of `fundamentals.py` was written**, and each one changed the code.

**Concepts are not consistent between filers.** Robinhood's revenue lives in
`Revenues` (72 datapoints). Its
`RevenueFromContractWithCustomerExcludingAssessedTax` has 4 datapoints and
stops in 2021. A fixed priority list picks the dead one, so concepts are
chosen by **coverage and recency**, with the preference order breaking ties
only.

**Cash-flow statements are cumulative.** Income-statement facts are discrete
quarters; cash-flow facts are year-to-date — Q1 is 3 months, Q2 is 6, Q3 is 9.
Filtering for ~90-day periods finds only Q1, and free cash flow comes out
empty for almost every company. Each quarter is recovered by subtracting the
previous cumulative figure. The same subtraction recovers a fourth quarter
that only appears inside the annual total.

**Weighted-average share counts are not additive.** Adding Microsoft's four
quarterly counts claims it has 30 billion shares outstanding instead of 7.4
billion. Flows are summed; share counts are averaged, and are never
differenced.

**Retail fiscal calendars are not 91 days.** Costco's quarters are 12 weeks,
its fourth is 16, and its third-quarter year-to-date is 252 days, not 273. A
fixed window rejects that and the entire company returns nothing. Quarter
classification is proportional with a tolerance.

**The same period is reported many times.** Later filings restate it, and a
stock split re-expresses every prior per-share figure. The **latest** filing
wins the value, which puts the whole per-share history on today's share basis
— the same basis split-adjusted prices use, so a split creates no step in
either line of the chart. The **earliest** filing date is kept separately as
`first_filed`, because that is when the market actually learned the number.

---

## What this refuses to answer, and why

Each refusal was verified against live EDGAR. None of them shows a zero.

| Case | Example | What the tab shows |
|---|---|---|
| IFRS foreign private issuers | TSMC files in TWD, Novo Nordisk in DKK | N/A — "the ADR-to-ordinary ratio and the exchange rate are not in the filings, so per-share figures cannot be lined up against a US dollar share price" |
| Annual-only filers | Alibaba files a 20-F, no 10-Qs | N/A — "reports that figure only once a year, so there are no quarterly numbers to build a trailing-twelve-month figure from" |
| Thin or broken history | Exxon's Company Facts currently holds six period-ends, no annual report, and is missing 2025-09-30 | N/A — "the four most recent quarters do not form a continuous year" |
| Pre-revenue companies | Cingulate has no revenue concept at all | N/A, not $0 |
| Banks and some property companies | JPMorgan reports no comparable capital-expenditure line | Free cash flow N/A — treating the missing half as zero would print operating cash flow as if it were free cash flow |
| Multi-class share structures | Robinhood and Shopify report the cover-page count per share class, which Company Facts drops | Falls back to the average diluted count and **says so in the basis line** |
| Non-filers | ETFs, indices, most foreign tickers | N/A — "funds, ETFs and most foreign tickers do not file with EDGAR" |

**Forward earnings have no free authoritative source.** The SEC has what a
company *reported*, never what analysts *expect*. When the estimate provider
is unreachable, the forward fields stay N/A and the verdict falls back to the
GAAP trailing basis **and says which basis it used**. Trailing GAAP earnings
and adjusted analyst forward earnings are never combined inside one ratio.

---

## Earnings drivers

Earnings per share is an identity:

```
EPS = Revenue × Net margin ÷ Diluted shares
```

**When everything is positive in both periods**, taking logarithms turns that
product into a sum:

```
Δln(EPS) = Δln(Revenue) + Δln(Net margin) − Δln(Diluted shares)
```

The three contributions add up to the total *exactly*, because this is an
identity rather than an approximation. That is asserted in the test suite, not
trusted.

**When earnings or margins are negative or cross zero**, the logarithm does
not exist and no percentage is invented. Instead each driver gets its
**Shapley value**: its effect on earnings per share averaged over every order
in which the three drivers could have moved. Averaging over the orderings is
what removes the arbitrary choice of which driver gets credit for the overlap
— a single sequential walk-down gives a different answer depending on which
factor you move first. The Shapley values also sum exactly to the change, and
the panel is titled **Dollar EPS Bridge**. A company with no revenue has no
margin to split out and falls back to a two-driver bridge (earnings, share
count) rather than showing nothing.

**The identity check.** The breakdown always describes net income ÷ diluted
shares. Measured across fourteen of this dashboard's tickers, nine reproduce
reported EPS within 1.5% (Microsoft 0.02%, Costco 0.05%, JPMorgan 0.15%, Apple
1.46%). The ones that do not are exactly the cases worth flagging: Realty
Income at 5.3%, where preferred dividends and minority interests sit between
net income and what common shareholders earn, and loss-makers like Plug Power
(8.5%) and Cingulate (60%), where the share count dividing a loss is the
undiluted one. Past a 3% tolerance the panel is **still drawn** — it is
useful — but it carries a warning naming both numbers, rather than quietly
bridging to a different EPS than the one printed above it.

---

## Price against earnings

Both lines are indexed to 100 at the start of the window, so their shapes can
be compared regardless of scale. Three refusals live in this chart:

1. **Reported earnings are plotted on the day they were FILED**, not the day
   the quarter ended. A quarter that ended in March was not public until May,
   and putting it in March shows information nobody had. Because every annual
   report repeats two years of comparatives, the *value* comes from the latest
   restatement and the *date* from the first filing that stated it — verified:
   Apple's September 2021 twelve-month figure now plots on October 29, 2021,
   not on the November 2023 annual report that repeated it.
2. **Forward earnings only appear from the first day this dashboard recorded
   one.** There is no free archive of past analyst consensus, and back-filling
   today's estimate across last year's chart would manufacture a history that
   never happened. The chart says so in words rather than drawing a flat line.
3. **A negative starting value is not indexed.** 100 × (−3 ÷ −5) is not an
   index; the line is omitted and a note points at the dollar figures instead.

---

## Volatility terminology (preserved for later phases)

Phase 1 makes no options recommendation, but the distinction the rest of the
app maintains is not blurred here:

- **HV Rank** — a ranking of *realized* volatility. A proxy.
- **True IV Rank** — a rank built from accumulated *actual option IV*
  observations (`premium_edge.record_observation`).
- **Current IV / IV30** — today's option-implied volatility, interpolated on
  total variance between the expirations bracketing 30 days.

These are three different quantities and later phases must not average them.

---

## What is ready for Phase 2

- **A normalized snapshot with provenance on every field**, so a fair-value
  engine can consume it without touching a provider.
- **A daily store already accumulating**, which is the only way a valuation
  history, a real forward-P/E series or an "is it cheap versus its own past"
  measure can ever exist. Nothing can back-fill it; it only grows forward.
- **A swappable provider interface.** A paid estimates vendor drops in at one
  lambda.
- **Quarterly and trailing series per metric**, filing-dated, restatement- and
  split-consistent — the input a reverse DCF or a normalized-earnings model
  needs.
- **A configuration block with a hash stamped on every snapshot**, so a later
  backtest of the verdict rule can tell which thresholds produced which call.
- **The refusals are already enumerated and tested**, so a peer engine knows
  in advance which filers it cannot compare.

Still missing, on purpose, until someone asks for them: cash-secured put and
LEAPS optimizers, a full fair-value engine, a structure comparator, a reverse
DCF, and peer comparison.

---

# Phase 2

## What Phase 1 got wrong, and why it was removed

Phase 1 judged every company with one rule: is the earnings yield above the
10-year Treasury plus a fixed cushion. At a 4.7% 10-year that asked for a 6.7%
earnings yield — a price/earnings ratio near fifteen — and it marked **Apple
and Microsoft WAIT** for no better reason than that.

That is arithmetic, not analysis. A universal multiple threshold cannot tell
an excellent business priced fairly from a poor one priced cheaply, and it
gets the more important question backwards: a great company is *allowed* to
trade at a high multiple. What matters is whether the multiple is high **for
this company** and high **for this kind of company**.

The rule has been **deleted**, not tuned. `attractive_spread_pp` and
`watch_spread_pp` are gone from `thresholds.json`. The 10-year Treasury yield
is still displayed, now labelled as context rather than as a hurdle.

## The four-vector scorecard

Four **independent** dimensions, deliberately never blended into one number.
A single score would let strong growth hide an expensive price, which is the
exact mistake the layout exists to prevent.

| Vector | What it reads |
|---|---|
| **QUALITY** | Return on invested capital · free cash flow conversion · operating margin trend · share count trend · stock compensation as a share of revenue · net debt to operating profit |
| **GROWTH** | Revenue growth · earnings growth · forward earnings growth, plus the margin and share-count contributions **lifted from the Phase 1 earnings bridge** rather than recomputed, so no movement is counted twice |
| **VALUATION** | Cheap or expensive against **its own history** and against **comparable businesses**. 100 means cheap. |
| **REVISIONS** | Whether analysts are raising or cutting. **NOT RATED** below four covering analysts. |

Each dimension scores only the inputs it could actually build, prints its own
coverage ("5 of 6 inputs available"), and **never fills a missing input with a
neutral 50** — averaging in a 50 for an unknown would drag an 80 to 65 and
invent an opinion out of an absence.

Scoring is **peer-relative percentile** wherever a credible group of five or
more exists. Below that it falls back to coarse absolute bands and says so on
screen, because an absolute cut-off is a blunt instrument across industries.

## Quality, measured against what filers actually report

Coverage is genuinely partial and varies by filer. Measured across twenty
tickers before the code was written:

- `OperatingIncomeLoss` is **absent** for JPMorgan, Realty Income, Robinhood,
  Exxon, Chevron and Pfizer.
- Share-based compensation is **absent** for Walmart, Exxon, Chevron and AT&T.
- Microsoft tags **no combined depreciation-and-amortisation figure at all**.

So every input is independently optional with its own reason. A missing input
is a missing input — never a failing grade, never a zero.

```
ROIC = operating profit × (1 − effective tax rate) ÷ (equity + net debt)
```

Invested capital is what shareholders and lenders have actually tied up.
Returns None when that base is zero or negative, where the ratio stops meaning
anything. Apple's comes out near 100%, which is correct — enormous operating
profit on a capital base shrunk by two decades of buybacks.

## Valuation against its own history

The major Phase 2 feature. For every trading day in the window, three inputs
are combined:

- the split-adjusted close
- the trailing earnings (or free cash flow) **known on that day**
- the share count **known on that day**, for the free-cash-flow yield

Point-in-time safety comes from the Phase 1 reader: the **latest** restatement
supplies the value, so a split leaves no step and the series sits on today's
share basis; the **first** filing date supplies the timing, so nothing appears
before it was knowable.

For 3-year and 5-year windows the tab reports **current, percentile, median,
10th and 90th percentile** for earnings yield, free cash flow yield and
trailing price/earnings. Percentiles are oriented so **100 always means cheap**
whichever direction the underlying measure runs.

Below 60 observations in a window, the window shows N/A with its count.

**Forward price/earnings has no history and is not given one.** No free archive
of past analyst consensus exists. It stays a current-only figure until this
dashboard's own daily snapshots accumulate enough of their own.

## Regime check

```
recent  = the last 2 years of the valuation series
earlier = the rest of the 5-year window
shift   = median(recent) − median(earlier)
bar     = max( p90(earlier) − p10(earlier),  10% of median(earlier) )
REGIME SHIFT DETECTED when |shift| > bar
```

A shift worth calling is one bigger than the earlier period's **own spread** —
a change of level, not a wiggle. The floor matters: an earlier period that
barely moved has a spread near zero, and without the floor its very stability
would make a shift impossible to detect at exactly the moment it is most
obvious. That was a real bug, caught by the module's own tests.

When a shift is detected the banner is rendered above the numbers, and the
valuation score **halves the weight on the company's own history**, because
the older half was recorded under conditions the company is no longer in.

## Peers

Grouping comes from the **Standard Industrial Classification code the SEC
assigns to every filer** and carries on its own submissions record. Free, on
every filer, and the same field the company files under.

Hierarchy, most specific first: **DIRECT PEERS** (a curated list, or the same
four-digit code) → **INDUSTRY** (three-digit) → **SECTOR** (two-digit) →
**BROAD BENCHMARK**. A group needs **five** members; below that it falls back
a level and says so. A curated override lives at
`<data>/invest/peers_curated.json` and beats the code, because automated peer
picking gets some companies wrong.

A **BROAD BENCHMARK** group is displayed for context but is deliberately **not
used to rank valuation** — ranking a bank's earnings yield against a software
company's is arithmetic, not comparison.

### The group multiple

```
Aggregate P/E = total market value of the profitable members
                ÷ their total earnings
```

Never an arithmetic average of the members' ratios. On a five-member test
group where one member earns a cent a share, the naive average is **764×** —
a number describing nobody. The aggregate is **18.5×**. The median member's
ratio is shown beside it, and loss-makers are **excluded and named**, because
"four of five were profitable" is part of the answer.

The index is built from EDGAR's atom endpoint (17KB) rather than the full
submissions record (164KB) — the difference between an index that can be
warmed across a 1,300-name watchlist and one that cannot. It fills a slice at
a time on the daily scheduler and then stays filled, because a SIC code does
not change.

## Business type

SIC ranges classify every filer into STANDARD, BANK, INSURANCE, REIT, BROKER,
CYCLICAL, UNPROFITABLE or UNSUPPORTED, and the classification is used to
**refuse** calculations rather than to perform them:

| Type | Withheld | Why |
|---|---|---|
| Bank | free cash flow, leverage, ROIC | Borrowing IS a bank's raw material |
| Insurer | free cash flow, leverage, ROIC | Float reads as either debt or spare cash; both wrong |
| Broker | free cash flow, leverage, ROIC | Customer assets and segregated cash sit on the balance sheet |
| REIT | free cash flow, leverage | Depreciation on properties that are not wearing out at that rate |

Those types return **SPECIALIZED MODEL REQUIRED** as their verdict rather than
a confident-looking number built on holes.

## Value trap

Cheapness is not evidence of value; it is the question. Eight deterioration
signals — falling estimates, deteriorating revenue growth, narrowing margins,
deteriorating free cash flow, rising leverage, rising dilution, structural
change (restatement, reverse split, late filing, read from the filing tagger
the Gap tab already uses), and a possible cyclical peak.

Each fires on a **direction of travel**, not a level — the level that counts as
bad differs by industry, and the level is already scored under Quality.
Three or more active is **HIGH RISK**, one is **MODERATE**, none is **LOW**.

A signal that could not be measured is listed as **unknown**, never counted as
fine. When almost nothing is measurable the whole check reads NOT RATED,
because silence is not evidence of health.

**A stock that is cheap with HIGH RISK cannot receive ATTRACTIVE.** It receives
AVOID, and the reasons say why: "It is cheap against its own history, and that
is exactly the pattern a value trap makes."

## Revision underreaction — EXPERIMENTAL

Recorded, not believed, and **kept out of the verdict entirely**.

```
revision intensity = (consensus EPS now − consensus EPS 90 days ago) ÷ price
price reaction     = stock 90-day return − sector 90-day return
underreaction      = z(revision intensity) − z(price reaction)
```

Both halves are standardised across the peer group before being differenced.
Intensity is scaled by **price**, not by the old estimate: dividing by a
near-zero prior estimate produces a number in the hundreds of percent that
says more about the denominator than about the revision.

This dashboard has no evidence that it predicts anything. The prospective
snapshot store is what will eventually test it.

## The Phase 2 verdict

**ATTRACTIVE · WATCH · WAIT · AVOID · INSUFFICIENT DATA · SPECIALIZED MODEL
REQUIRED.** Still no weighted composite: the rules are conditions on named
dimensions, so any answer can be re-derived by hand from the same screen.

- Good quality + acceptable growth + cheap + revisions not weak + trap low → **ATTRACTIVE**
- Good quality + strong growth + expensive → **WAIT**, with the price named
- Cheap + HIGH trap risk → **AVOID (value trap)**
- Losing money with no profitable forward estimate → **AVOID**
- Bank / insurer / broker / REIT → **SPECIALIZED MODEL REQUIRED**

Every WAIT and AVOID states what would have to change, and the price comes
from **this company's own median valuation** rather than a universal multiple:

> **WAIT** — Today's earnings yield is at the 18th percentile of its own
> history. Reaching its own median valuation — an earnings yield of 4.0% —
> means about $125.00 a share at today's earnings, or trailing earnings of
> $4.00 a share at today's price.

## Snapshots

The daily row now also carries: each dimension's score and label, both
valuation percentiles, the regime flag, the value-trap level and its active
signal keys, business type, earnings-cycle state, peer level and aggregate
multiple, the verdict, the experimental underreaction score, and the
configuration hash.

**History is never rewritten.** A row written before these fields existed
simply lacks the keys, and a reader must treat a missing key as "not recorded
that day" rather than as a zero. Within a day the last write wins, so the
enriched payload replaces the base snapshot for the same date.

## Earnings cycle

PRE-EARNINGS (reports within 14 days) · POST-EARNINGS FRESH (reported within
21) · NORMAL · STALE (last report over 100 days ago) · UNKNOWN. Visible now
and feeding the confidence language around stale estimates; a later phase uses
it for option entry timing.

## Drawdown history

Worst peak-to-trough fall over the window, the worst of the last year, and the
2020 and 2022 stress periods where the history reaches them. **No Beta, on
purpose**: it compresses a whole distribution into one number that says nothing
about what this stock actually did when things went wrong — which is precisely
what these rows show.

## What is ready for Phase 3

- **Four independent dimensions plus a trap state**, each with its inputs and
  its coverage, which is what a fair-value engine needs to know how much to
  trust its own inputs.
- **A point-in-time valuation history** with distributions — the input a
  reverse DCF or a normalised-earnings model works from.
- **A peer engine with aggregate group valuation**, ready to supply the
  comparable multiple a bear/base/bull range needs.
- **Business-type gating already enforced**, so a specialised model can be
  slotted in per type without unpicking the generic one.
- **An earnings-cycle state** for option entry timing.
- **A snapshot store recording the full Phase 2 state daily**, with a
  configuration hash, so a forward test can tell which thresholds produced
  which call — and the experimental underreaction signal is already
  accumulating against that day.

Still not built, on purpose: bear/base/bull fair value, reverse DCF, expected
return bridge, cash-secured put and LEAPS optimizers, structure comparator,
options recommendation, scanner.
