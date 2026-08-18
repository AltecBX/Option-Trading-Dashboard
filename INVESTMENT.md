# Investment tab — Phases 1, 2 and 3

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

---

# Phase 3

Phase 1 asked *what does this company report*. Phase 2 asked *is it attractive
against its own history and its peers*. Phase 3 asks five questions the first
two deliberately left alone:

1. What is a defensible Bear / Base / Bull value?
2. What return does today's price imply?
3. What growth is the market already pricing in?
4. At what price do I actually want to own it?
5. If I want exposure, which structure is economically best?

New modules: `fair_value.py` (pure valuation arithmetic), `structures.py`
(pure payoff maths for the comparator), `invest_options.py` (the chain, the
two optimizers, the entry verdict, the long-dated observation store).

## Fair value — three methods, never averaged

| Method | What it prices | At what multiple | Basis |
|---|---|---|---|
| A. Its own valuation history | GAAP trailing earnings per share | percentiles of this company's own point-in-time earnings-yield history | GAAP trailing, both sides |
| B. Comparable companies | GAAP trailing earnings per share | the peer group's aggregate multiple, with the 25th and 75th percentile of member multiples for the range | GAAP trailing, both sides |
| B-forward | adjusted analyst forward earnings | peer FORWARD multiples | analyst adjusted, both sides — **unavailable**, see below |
| C. Normalized free cash flow | the median of the last five trailing-twelve-month readings | percentiles of this company's own free-cash-flow-yield history | reported cash flow, both sides |

The percentiles run on YIELDS, where a HIGH yield is a CHEAP price. That is
why the pessimistic value sits at the 85th percentile of the yield
distribution and the optimistic one at the 15th. Getting that inversion
backwards would produce a bear case above the bull case, which is why the
config keys are named rather than written as bare numbers in three places.

**Methods are not averaged.** Averaging a good method with a bad one produces
a number worse than the good method alone and hides which was which. Instead:

* **Base** is the single highest-confidence valid method's value.
* **Bear** is the most pessimistic value any valid method produced.
* **Bull** is the most optimistic.

So methods that disagree widen the range rather than being blended into a
false middle, and the disagreement itself sets the confidence.

**Basis never mixes.** B-forward is offered and then refused, every time,
because no free source publishes analyst estimates for a whole industry.
Building it from trailing peer multiples would multiply one company's adjusted
forecast by another company's audited past. That combination is not offered at
all rather than offered with a footnote.

## Confidence, and why the obvious formula is backwards

Disagreement is `(highest base − lowest base) ÷ lowest`, and the bands are
25% / 50% / 100% for HIGH / MODERATE / LOW / UNRELIABLE. A single method
standing alone is capped at MODERATE: nothing disagreeing with it is not the
same as something agreeing with it.

The tempting formula is

```
Adjusted margin of safety = Raw margin of safety × Confidence
```

and it runs the wrong way — it SHRINKS the discount demanded exactly when the
valuation is least trustworthy. What Phase 3 does instead is let the
confidence decide how far up from the pessimistic case the valuation is
credited:

```
Credited fair value = Bear + Confidence × (Base − Bear)
Buy zone            = Credited × (1 − minimum margin of safety)
```

with `Confidence` mapping HIGH → 1.00, MODERATE → 0.65, LOW → 0.35,
UNRELIABLE → 0.00. On the specification's own example (Base 300, Bear 220)
that gives 300 at HIGH and 220 at UNRELIABLE. Lower confidence lowers the
price we will pay, which is the direction that keeps money.

## Expected return bridge

Three scenarios over a configurable horizon (three years by default):

```
Future EPS      = today's EPS × (1 + scenario growth)^years
Future price    = Future EPS × the scenario exit multiple
Terminal wealth = Future price + the future value of dividends received
Total CAGR      = (Terminal wealth ÷ today's price)^(1/years) − 1
```

Three refusals live in that arithmetic.

* **Buyback yield is not added.** A buyback shrinks the diluted share count,
  which raises earnings per share, which is already the first contribution.
  Adding it again counts the same cash twice.
* **Dividend yield is not added to the price return.** A dividend is cash on a
  date. It is modelled quarterly, grown with earnings, and compounded to the
  horizon at the matching Treasury yield — `treasury.rate_for_years()`, the
  same official curve the Treasuries tab draws, read at the matching point.
  Quarterly rather than annual matters for the option horizons: a forty-five
  day contract crosses about one dividend, and an annual model would either
  invent it or lose it.
* **The exit multiple is on the same basis as today's.** A bridge that starts
  on trailing GAAP and lands on a forward adjusted multiple has manufactured
  most of its own answer.

The attribution is exact, because `E₁·M₁ = P₁` and `E₀·M₀ = P₀`:

```
ln(TW/P₀) = ln(E₁/E₀) + ln(M₁/M₀) + ln(TW/P₁)
```

annualized, so the three bars add up to the total shown beside them. The
reconciliation is asserted in `test_fair_value.py` and re-checked in the
browser against the rendered numbers.

### Growth rates are measured over the horizon being projected

This was learned the hard way. Percentiles of ONE-year earnings growth are not
percentiles of three-year growth: Apple's 75th-percentile single year is +39%,
its 75th-percentile three-year run is +13.5%, and compounding the first over
three years produced a bull case of seven hundred dollars a share. So the
distribution is built from overlapping compound windows the same length as the
projection, falling back to one-year rates — labelled — when the comparable
history is too short to hold six of them.

"Comparable history" is doing real work there. See the share-basis note below.

### The multiple does not snap to its target

The same scenario machinery has to price a three-year share thesis and a
forty-five-day put. Letting a six-week contract re-rate the whole way would
have the fundamental scenarios doing all the work at exactly the horizon where
they explain the least. The multiple travels geometrically:

```
M(T) = M₀ × (M_target / M₀)^min(1, T / reversion years)
```

which covers a third of the LOG distance at a third of the horizon and leaves
the attribution above reconciling exactly.

## Implied expectations — the reverse discounted cash flow

An EXPECTATIONS instrument, not a valuation. A forward discounted cash flow
answers "what is it worth" with whatever growth rate somebody typed in; this
runs the model backwards and solves for ONE unknown — the five-year
free-cash-flow growth today's enterprise value is already paying for.

Solved by **bisection on a bracketed interval**, not Newton-Raphson. The
present value rises monotonically with the growth rate for positive cash flow,
so bisection cannot diverge, cannot need a derivative and cannot wander onto a
second root — none of which is true of a Newton step started at a bad guess on
a function this convex. Outside the searched range the answer is named
("bounded above" / "bounded below") rather than clamped.

The discount rate is the 10-year Treasury plus a stated equity risk premium,
displayed and varied across a 3 × 3 grid. It is deliberately **not** a
per-company weighted average cost of capital: a beta estimated from five years
of daily returns moves by whole points depending on the window chosen, and
that false precision would propagate into every cell of the grid.

Five-year free-cash-flow **consensus is never printed**. No free source
publishes it. The field exists, says so, and stays empty.

## The structure comparator

```
C = 100 × the current share price
```

One round lot, which is what one contract controls. Every structure is marked
at the SAME expiration, on the SAME three scenario prices, against the SAME
account, and whatever a structure does not spend earns the matching Treasury
yield to expiration.

Nothing is ever ranked on return on premium, return on broker margin or return
on buying-power reduction. Those denominators reward a structure for the money
it did NOT put to work, which is what makes leverage look like skill.

| Structure | Terminal wealth of the whole account |
|---|---|
| SHARES | `100·S_T + 100·D + unused·G` |
| PORTFOLIO SECURED PUT | `(C + 100·P)·G − 100·max(K − S_T, 0)` |
| LEAPS | `(C − 100·A)·G + 100·max(S_T − K, 0)` |
| BUY-WRITE | `100·S_T − 100·max(S_T − K_c, 0) + 100·P_c·G + 100·D` |
| BULL CALL SPREAD | `(C − debit)·G + 100·(max(S_T−K₁,0) − max(S_T−K₂,0))` |

`G = (1+r)^T` at the matched Treasury yield; `D` is dividends carried to the
horizon, per scenario. A put is eligible only if its FULL strike notional
`K × 100` fits inside the comparison capital — never the buying-power
reduction, because if the stock goes to zero the obligation is the full strike
whatever the margin clerk asked for. A call receives no dividend, and the
buy-write flags early-assignment risk when its remaining extrinsic value is
smaller than the dividends due before expiration.

The buy-write is ONE expiration. It is deliberately not a model of selling a
call every week: that path depends on where the stock went between rolls, and
pretending one contract's economics describe a year of rolling would be the
most flattering possible assumption dressed as a calculation.

### TOSS UP

Scenario weights (25 / 50 / 25 by default) are assumptions. The ranking is
re-run with the bear and the bull weight moved five points either way. If the
winner changes — or if the top two are within half a point a year — the answer
is **TOSS UP — PROBABILITY SENSITIVE** rather than a preference with two
decimal places.

Option-implied probabilities are shown separately as market context and are
never relabelled as real-world probabilities.

## Two horizons, deliberately not mixed

The comparator runs at ONE long-dated expiration. The short-dated put
optimizer runs on its own, in the chain coverage the rest of the app already
uses. Comparing a three-year view of a business against a forty-five-day
option would be arithmetic rather than comparison.

That separation creates an honesty problem the tab solves rather than hides:
over a shorter horizon the fundamental scenarios barely separate, so a
structure that loses badly on a real fall looks safe by construction. Beside
the comparison the tab therefore prints what the same horizon looks like as a
DISTRIBUTION — a lognormal at this stock's own realized volatility over
windows of that length — and says in writing that the two answer different
questions.

## The put optimizer

The fundamental acquisition price comes FIRST: a strike above the buy zone is
never considered, however rich the premium. A put sold above the price this
analysis says the shares are worth is not a way of buying the business
cheaply; it is a bet with the business attached.

Nothing is decided by a delta band, an implied-volatility rank or a preferred
number of days to expiration. Those are displayed because they are useful to
read. The choice is made on what the whole comparison account is worth, and
the qualifying premium is then compared against the matching Treasury yield
plus a stated cushion — what the same cash earns doing nothing. Below that:

```
WAIT — INSUFFICIENT PREMIUM AT DESIRED OWNERSHIP PRICE
```

and the bid that WOULD qualify is solved rather than searched for, by
inverting the secured-put wealth equation.

Each candidate carries the Premium Edge block the rest of the app already
computes: true IV30, term structure, VRP, ExpectedRV30, model P(ITM), P(touch),
expected shortfall, model EV, the PURE / EVENT / MIXED classification, quote
quality and the earnings event.

## The LEAPS optimizer

Scanned across the configured long-dated window with no preferred delta, no
implied-volatility percentile gate and no implied-to-realized ceiling — the
economics on identical capital decide.

**ExpectedRV30 never appears in this panel.** It is a thirty-day volatility
forecast, and holding a two-year contract's implied volatility up against it
and calling the difference an edge compares a two-year price with a one-month
forecast. What a long contract can honestly be judged against is what this
stock's volatility has actually been over windows of the same length, so the
panel shows realized volatility measured over the contract's own tenor plus
one-year and three-year context.

A tenor-matched IMPLIED volatility history does not exist yet, and the panel
says so. `chain_store.py` deliberately keeps only short expirations because
that is what the option backtester prices against; Phase 3 adds a separate
small store under `<data>/invest/leaps/` that records the near-the-money
long-dated contracts once a day. Nothing is back-filled — there is no free
archive of what a December-2027 call cost last March.

## Quote sanity — an arbitrage that is really a stale quote

A long call quoted BELOW its own intrinsic value is free money, which means it
is a bad quote rather than an opportunity — and on identical capital it wins
the comparison outright, because the comparison is doing exactly what it was
built to do with a price that is not real. Apple's December-2027 fifteen-dollar
call came back offered at $254 against $290.93 of intrinsic value and topped
the ranking at 17.7% a year until this check was added. Crossed quotes and put
bids above the strike are refused the same way.

Liquidity (open interest, bid-ask width) is separate: a thin contract is still
PRICEABLE and is listed with its reason, because "no candidate found" and
"every candidate was untradeable" are different answers.

## The entry verdict

```
BUY SHARES · SELL PORTFOLIO SECURED PUT · BUY LEAPS · BUY-WRITE ·
BULL CALL SPREAD · WAIT · AVOID · TOSS UP ·
SPECIALIZED MODEL REQUIRED · INSUFFICIENT DATA
```

Gates, in order, each of which blocks every bullish answer:

1. Bank, insurer, broker or property trust → SPECIALIZED MODEL REQUIRED
2. No price → INSUFFICIENT DATA
3. Value trap HIGH RISK → AVOID
4. The Phase 2 business verdict is AVOID → AVOID
5. Not profitable → AVOID
6. No fair value, or fair value confidence UNRELIABLE → WAIT

Then: inside the buy zone the comparator decides; above it, a put at or below
the buy zone with adequate premium is the way in, and otherwise WAIT.

Every WAIT, AVOID and TOSS UP names the exact thing that would change it — the
price, the base fair value at the current confidence, or the bid on the strike
that would qualify. Where the required base value would exceed the optimistic
case, the sentence says so instead of quoting a number nothing could reach.

## Management plan

Written before the position is opened, never executed: no orders are placed
anywhere in this dashboard. Four common exits (thesis invalidation, fair value
reached, revisions deteriorating, the value-trap check flipping) plus
per-structure conditions — full notional and the rolling rule for a secured
put, time-to-expiration and "do not hold mechanically" for a LEAPS, the upside
cap and early-assignment risk for a buy-write.

If a put is assigned the shares go into the covered-call workflow this
dashboard already has. There is deliberately no second call-selling engine.

## Two bugs found in Phase 1, and what they cost

### Per-share history sat on more than one share basis

`latest_filed` kept the newest filing's version of each period, which handles a
split whenever the company restates that period afterwards. Apple's fiscal-2018
twelve-month earnings per share was never restated again: it stands at 11.91 in
the filing that reported it and 2.97 in the one after the 2020 four-for-one
split. Differencing a fourth quarter out of a post-split annual figure and a
pre-split nine-month one produced **−6.01 a share for a quarter Apple earned
money in**, and every cross-period comparison over that boundary was measuring
the split.

The repair uses the filings against each other. Consecutive filings repeat each
other's periods as comparatives, so the ratio between the two versions of the
same period IS the split — and a rescale is applied only when that ratio lands
on a clean split factor. Apple's 2010 retrospective revenue-recognition change
moved earnings per share by a factor of 1.2649 between two filings; nothing in
the split table is within reach of it, so it survives exactly as filed.

Verified after the fix: fiscal 2018 = $2.98, 2019 = $2.97, 2021 = $5.61,
2025 = $7.46 — all correct on today's basis — and the −6.01 quarter is now
$0.73.

Where even that is unrecoverable — Apple's June 2014 seven-for-one split sits
between two quarterly filings that share no period at all — `basis_breaks()`
detects the discontinuity on the diluted share count and the growth history
starts after it. A jump that does NOT land on a split ratio is left alone: a
company that really did double its share count has not changed basis, it has
diluted, and the history should keep that.

This never affected the five-year windows Phases 1 and 2 read; it surfaced
only when Phase 3 reached back across the whole reported history.

### The LEAPS candidate cap started at the lowest strike

`call_candidates` took the first N rows sorted by strike. A long-dated chain
can list ninety strikes from $175 to $800 on a $480 stock, so the cap spent its
whole budget on deep in-the-money contracts and never reached the ones anybody
trades. Candidates are now ordered by distance from the money.

## Snapshots

Every Phase 3 figure joins the daily row: bear/base/bull, the confidence and
its spread, the method that produced the base, the credited value, the buy
zone, the premium to it, the three scenario prices and returns, the
probability-weighted return, the implied growth and its grid range, the
expectations gap, the scenario weights, the preferred structure and every
structure's return, the chosen put, LEAPS and buy-write contracts, the entry
verdict, its first reason, its flip trigger, and the config hash.

Nothing already on disk is ever rewritten. A row written before these keys
existed simply lacks them, and a reader must treat a missing key as "not
recorded that day" rather than as a zero.

## The scanner

Every starred watchlist name, read from the stored snapshots rather than
rebuilt per request — a full build reads SEC filings, a peer group and an
option chain. Names with nothing stored say so, and a few are built in the
background per visit.

There is deliberately **no summed investment score**. A column that added
Quality to Growth to Valuation would let one strong reading carry a weak one,
and it would be sortable — which is worse, because it would become the column
everybody sorts by.

# Phase 4 — a bank on a bank's terms, a trust on a trust's, and a way to
# find out whether any of this works

Phase 3 ended with three things it deliberately would not do: value a bank,
value a property trust, and claim that any of it predicted anything. Phase 4
does the first two and starts the third.

## The bank model — `bank_model.py`

A lender's borrowing is its raw material rather than its risk, so net debt,
free cash flow and return on invested capital all measure the wrong thing.
What a bank owns is mostly financial claims carried near their value, which
makes book value the natural anchor — and tangible book, with goodwill and
other intangibles removed, the conservative one, because goodwill paid for an
acquisition cannot absorb a loan loss.

What a bank is worth ABOVE that book depends on what it earns on it:

    Justified price to tangible book = (ROTCE − g) ÷ (cost of equity − g)

A bank earning exactly its cost of equity is worth exactly its tangible book;
one earning more is worth a premium and one earning less a discount. The cost
of equity is the ten-year Treasury plus a stated equity risk premium — the
same market-wide convention the reverse discounted cash flow uses, never a
per-company number fitted to make the answer come out.

Four methods, combined by the unchanged Phase 3 machinery:

| Method | What it prices |
|---|---|
| `bank_self_ptbv` | Tangible book at this bank's own history of price to tangible book |
| `bank_self_pb` | Book value at its own history of price to book |
| `self_history` | Earnings at its own earnings-yield history — unchanged from Phase 3, because a bank's earnings ARE earnings |
| `bank_peers_ptbv` | Tangible book at what comparable banks cost, adjusted for how profitable this one is against them |
| `bank_justified` | Tangible book at the multiple its own profitability justifies |

**The cheapest price to tangible book in a group is not the cheapest bank.**
It is usually the least profitable one. Where enough comparable banks report
both a return on tangible common equity and a price to tangible book, and
where profitability actually explains enough of the spread between them, the
subject is priced off that fitted relationship rather than off the group
median. Where it does not, the median is used and the reason says so —
including the coefficient of determination that failed the test.

**One refusal matters more than the rest.** Bank of America tags no
preferred-equity concept in its machine-readable filings. Treating that as
zero would credit the common shareholder with equity belonging to the
preferred holders, so its tangible book value is left unreported rather than
overstated — and it drops out of every peer comparison for the same reason,
which is why JPMorgan's bank peer group prices off seven comparable banks
rather than eight.

**Net interest margin is not reported.** That ratio divides net interest
income by average interest-EARNING assets, and not one of fifteen banks
measured tags either the ratio or the earning-asset base. What is shown is net
interest income over average TOTAL assets, under that name.

Measured live: JPMorgan tangible book $112.63 a share, price to tangible book
3.22×, return on tangible common equity 20.5%, efficiency ratio 51.4%, capital
ratio 10.8%, deposits $2.71 trillion at a 1.7% cost, charge-off rate 0.3% and
FALLING 31.4% against the year before.

## The property-trust model — `reit_model.py`

Buildings are depreciated on a schedule that has nothing to do with whether
they are worth more or less than last year, and that charge runs straight
through the income statement. Realty Income earned $1.27 billion last year and
generated $4.0 billion of funds from operations. Valuing it on the first
number would be arithmetic performed on the wrong quantity.

    Funds from operations = net income available to common
                          + depreciation and amortisation of property
                          − gains on property sales
                          + impairments of property

**Not one of twenty large US property trusts tags a funds-from-operations
concept.** Every headline figure these trusts publish lives only in the prose
of a press release. So this module RECONSTRUCTS it from the four filed
components and says so everywhere it appears — and a trust's own headline is
usually a further-adjusted "core" number, which is different again.

**The reconstruction knows when it is incomplete.** Gains on property sales are
tagged sporadically: Realty Income tags them every quarter, Prologis stopped in
2019, Simon Property Group does not tag them at all. A quarter with no tagged
gain and a quarter where nothing was sold produce the same zero and mean
opposite things, so the tagged quarters are counted. When fewer than four of
the trailing quarters carry the adjustments, the reconstruction is called
INCOMPLETE, it is ranked below a complete one as a valuation method, and the
fair-value confidence is **held down to LOW** — which lowers the buy zone
rather than decorating the screen with a warning nobody acts on.

Two things make it honest enough to value on anyway. The completeness is
reported. And the comparison — today's price to funds from operations against
this trust's OWN history and against its peers — computes all three sides by
the same reconstruction, so a systematic bias largely cancels.

Measured against published figures: Realty Income $4.40 a share against a
published ~$4.20, Prologis $6.71, Public Storage $17.11 against ~$16.50, Kimco
$1.78 against ~$1.70, AvalonBay $11.04 against ~$11.40. Adding the
all-asset gain concepts moved Digital Realty from $9.45 to $6.59 against a
published ~$7.00 and Boston Properties from $8.40 to $7.27 against ~$6.80.
Simon Property Group, whose gains are untagged, reconstructs at $19.37 against
a published ~$12.60 — is flagged INCOMPLETE, warned about for a +82% swing no
property portfolio produces, and held at LOW confidence.

**Adjusted funds from operations is refused.** It subtracts recurring
maintenance spending, straight-line rent and lease-intangible amortisation.
No trust measured separates recurring maintenance from development spending in
machine-readable form, and only thirteen of twenty tag a straight-line rent
adjustment at all. Estimating it would mean inventing the largest of its three
deductions.

**Property type is read from the trust's own annual report.** The SEC gives
every property trust industry code 6798, so the code cannot tell a data-centre
trust from a shopping-centre trust — and those two have never traded at the
same multiple. Measured against twenty trusts: sixteen classified correctly,
three declined to answer, one (Iron Mountain, whose Item 1 now leads with data
centres) came back as a data-centre trust. Declining is the designed outcome:
an unclassified trust is compared against all property trusts, which is weaker
than a matched comparison but never a wrong one.

**Insurers and brokers remain refused.** No model here is built for either,
and half a model reads exactly like a whole one on screen.

## The covered-call simulator — `covered_call.py`

The Phase 3 comparator prices a buy-write at ONE expiration, which is the
honest answer to "which structure should I open today". It is not the question
people ask about covered calls. That one is path dependent: a call sold in
January caps a rally in February, and the shares are then assigned away, or
bought back at a loss, or rolled into a further-out call whose credit does not
cover the loss on the one it replaced. So this walks the lifecycle day by day:

    own shares → sell a call → it expires, is assigned, is bought back or is
    rolled → sell the next one

**A roll never erases a loss.** Rolling is closing one option and opening
another, and both legs are recorded: the realized loss on the option being
closed stays in the ledger permanently and the credit on the new one is a
separate event.

**An option win rate is never presented as a result.** A ninety-five percent
win rate on calls that repeatedly cap a rising stock loses to owning the
shares. The comparison reported is terminal wealth against buy and hold on
identical starting capital, with dividends treated identically; the win rate
is one line of context inside it.

**No delta, tenor or roll rule is declared best.** Ten policies run side by
side across three tenors, four strike rules (delta target, percent above spot,
never below fair value, never below the credited fair value), four roll rules
and three assignment modes. The fair-value rules only ever RAISE a strike —
selling the business below what it is judged to be worth in exchange for a
premium is a decision to make deliberately rather than fall into by a delta
setting.

**Re-entry after assignment uses the buy zone**, not the next morning's open.
Where the proceeds no longer buy a hundred shares back, that is recorded once
as `COULD NOT BUY BACK` — a run that quietly sits in cash from then on looks
like a decision rather than the arithmetic it is.

**Historical option quotes are never invented.** Fills come from `chain_store`
where a snapshot exists for that day and from Black-Scholes against a modelled
volatility path everywhere else, each labelled, and the run reports
`REAL CHAIN BACKTEST`, `PART REAL CHAIN, PART MODEL-BASED ESTIMATE` or
`MODEL-BASED ESTIMATE`. Today every run is the last of those; it becomes a real
backtest as the chain store fills in.

The strongest check on the ledger: with no calls sold at all, terminal wealth
equals buy and hold **to the cent**.

## Forward validation — `forward_test.py`

Since Phase 1 this tab has written one snapshot per ticker per day. This reads
that store forward. Nothing is recomputed and nothing is rewritten: a
recommendation made on a Tuesday is judged exactly as it was written down that
Tuesday, against what the price then did.

Three rules, enforced rather than promised:

1. **No lookahead.** Every input comes from the stored row; every outcome from
   bars strictly after it. `lookahead_audit` recomputes each outcome against a
   price series truncated at the horizon and again with everything before the
   recommendation removed, and asserts all three agree.
2. **No incomplete horizons.** A ninety-day outcome appears ninety days after
   the recommendation, and only if the price series actually reaches it.
3. **No substitution.** A structure is scored on the EXACT contract that was
   recommended — that strike, that expiration, that credit. Picking a better
   one after seeing the outcome is the easiest way there is to manufacture a
   good result.

Reported at 30, 90, 180 and 365 days: sample size first, then median forward
return, hit rate, return against the benchmark, return against the median of
the OTHER companies in the same industry recorded the same day, median maximum
adverse excursion, how often the published Bear-to-Bull range contained the
outcome, and how often the comparison called the structures too close to call.
Broken down by verdict, preferred structure, valuation percentile, quality,
revision state, trap state and fair-value confidence.

**There is no accuracy score.** A single number blending a hit rate, a median
return and a calibration check is one nobody can act on and everybody can be
reassured by. Below the minimum sample every bucket says `INSUFFICIENT SAMPLE`
and shows nothing else. Results under materially different configuration
hashes are broken out separately rather than combined and called one strategy.

## What is unreliable or unavailable, honestly

* **Analyst forward estimates and coverage counts** need Finnhub or yfinance.
  Where they are missing, the forward peer method stays refused, REVISIONS
  reads NOT RATED, and the estimate-driven trap signal is unmeasurable.
* **Peer FORWARD multiples** have no free source at all. Method B-forward will
  activate the day one exists; until then it prints its reason.
* **Five-year free-cash-flow consensus** likewise does not exist for free.
* **Tenor-matched long-dated implied-volatility history** starts accumulating
  from the day Phase 3 shipped and is never back-filled.
* **Dividends per share** are read from EDGAR, where the concept varies by
  filer — Apple and Microsoft tag `Declared` throughout, Coca-Cola's `Declared`
  series stops in 2018 and only `CashPaid` continues. Exxon tags only two
  quarters and therefore reports N/A with the reason rather than a wrong
  number.
* **Insurers whose kind cannot be established** are refused. Sixteen of the
  forty-two US insurers measured classify as nothing, mostly because their
  annual report carries no readable "Item 1. Business" heading at all — and a
  wrong subtype applies the wrong ratios. See the Phase 5 section below.
* **Filers in a broker's industry code that hold no customer money** are
  refused: they are exchanges or asset managers, and the broker model is
  built on a broker's balance sheet.
* **A bank that does not tag its preferred stock** has its tangible book value
  refused rather than overstated. Bank of America is that bank, and it drops
  out of every bank peer comparison for the same reason.
* **Funds from operations is reconstructed, never quoted.** No property trust
  publishes it in machine-readable form. Where the gains and impairment
  components are untagged the reconstruction reads HIGH in a year with
  disposals, is labelled INCOMPLETE, and has its confidence held at LOW.
* **Occupancy, same-store net operating income and EBITDAre** are tagged by
  none of the twenty property trusts measured. They appear only in
  press-release tables, so they are not reported.
* **Ex-dividend dates** come from the corporate-actions provider. Where none is
  available the filed trailing rate is accrued evenly across the days held and
  the screen says the total is right but the individual dates are not.
* **Every covered-call run is currently a MODEL-BASED ESTIMATE.** The chain
  store has no history for these names yet. Each run reports what share of its
  fills came from real snapshots, and becomes a real backtest as that fills in.

# Phase 5 — an insurer on an insurer's terms, a broker on a broker's, and
# option data that becomes real with time

Phase 4 left two of the four specialized business types refused. Phase 5
builds them, and strengthens the prospective capture that turns the
covered-call simulator and the forward validation from estimates into
measurements.

It does NOT lower the bar to do it. Five of the forty-two US insurers
measured still come back refused, and thirteen of the fourteen filers that
share a broker's industry code without being brokers are kept out. Both
refusals are the point.

## The insurer model — `insurance_model.py`

### The subtype comes first, because it decides which numbers exist

Divide claims by premiums and you get 66% for Progressive, which is its loss
ratio and means what it says. Do the same arithmetic for MetLife and you get
99%, for Principal Financial 129% and for Brighthouse 248%.

Those are not loss ratios. A life insurer's premiums exclude the fee and
spread income that is most of what it earns, and its benefits include
interest credited to policyholder accounts and the annual change in reserves
for policies that pay out decades from now. The numerator and the denominator
describe different businesses.

So the model classifies before it measures — property and casualty, life,
health, reinsurance, multiline — from the insurer's own Item 1 checked
against its SEC industry code, and refuses when the two cannot agree.

| Subtype     | Metric basis  | What that means                                     |
| ----------- | ------------- | --------------------------------------------------- |
| P&C         | UNDERWRITING  | Loss ratio, expense ratio, combined ratio all apply  |
| Reinsurance | UNDERWRITING  | Same measures; a reinsurer underwrites the same way  |
| Multiline   | UNDERWRITING  | Both books; the underwriting side is measured        |
| Health      | BENEFIT       | Benefit ratio only — no underwriting expense is filed |
| Life        | SPREAD        | No loss ratio at all; book value, spread and reserves |

A life insurer's panel does not draw those rows even as N/A. A row of blanks
invites the reader to go looking for a number that is not a ratio of anything
for that business, so one paragraph says why instead.

Measured across forty-two US insurers, thirty-seven classify — fifteen
property-casualty, nine life, six health, five reinsurance and two multiline.
Four of the five refusals have no readable Item 1 heading in their annual
report at all (Cincinnati Financial, Equitable, Berkshire Hathaway,
Alleghany), and the fifth is American International Group, whose report this
reader cannot find one in either. A refusal costs a screen. A wrong subtype
puts arithmetic on the screen and calls it a measurement.

### Compatibility, and the Allstate case

Every ratio against premiums is checked before it is computed: the numerator
and the denominator must cover the SAME twelve months.

Allstate tags `PremiumsEarnedNetPropertyAndCasualty`, whose series stopped in
March 2018, alongside `IncurredClaimsPropertyCasualtyAndLiability`, which
runs to today. Dividing one by the other gives a loss ratio of 123%, which
looks like a catastrophe and is a date mismatch. The check catches it and the
screen reports nothing with that reason.

### The combined ratio, and why it is usually blank

    Combined ratio = Loss ratio + Expense ratio
    Loss ratio     = Claims incurred ÷ Premiums earned
    Expense ratio  = (Acquisition-cost amortisation + Other underwriting
                      expense) ÷ Premiums earned

The loss side is fine: all thirty-six insurers measured tag both claims
incurred and premiums earned. The expense side is not. Only five tag
`OtherUnderwritingExpense`.

There is a tempting shortcut — total benefits, losses and expenses minus
claims. Measured, it produces believable numbers for pure property-casualty
insurers (Travelers 88.6, Chubb 85.3) and nonsense everywhere else (Cigna
716, Equitable 1,134), because the total sweeps in interest credited, annuity
costs and, for the health insurers, the cost of dispensing prescriptions. It
is a ratio manufactured from unrelated concepts, so it is not used.

Where the real underwriting expense IS filed the reconstruction lands where
it should: Progressive 87.8 against a published figure near 88, Selective
97.9 against a published 98.

### Reserve development

`SupplementalInformationForPropertyCasualtyInsuranceUnderwritersPriorYear
ClaimsAndClaimsAdjustmentExpense` is tagged by sixteen of twenty-two
property-casualty insurers with long histories. Negative means reserves set
aside in earlier years proved more than enough and were released; positive —
adverse development — means they did not.

It is the single most important warning in this industry, because it says the
insurer under-estimated what it already owed, and it tends to repeat. Everest
Group's +$478m quarter in late 2025 shows up exactly as it should.

Adverse development caps the fair-value confidence at LOW, which lowers the
credited value and therefore the buy zone. It is not a warning printed beside
a number it did not change.

### What an insurer is worth

    Justified price to book = (ROE − g) ÷ (Cost of equity − g)

The same dividend-discount result the bank model uses, on book rather than
tangible book, with the same market-wide cost of equity — the ten-year
Treasury plus a stated equity risk premium. An insurer earning exactly its
cost of equity is worth exactly its book.

Five methods, never averaged: its own price to book, its own price to
tangible book, its own earnings history, comparable insurers of the same
subtype priced off the profitability relationship where one holds, and the
justified multiple above.

## The broker model — `broker_model.py`

### Is it even a broker?

The industry codes here are the widest in the SEC's list. Code 6211 holds
Charles Schwab, Goldman Sachs AND BlackRock. Code 6200 holds LPL Financial
and the CME. Code 6282, "Investment Advice", holds Evercore and T. Rowe
Price.

So the question is answered from the BALANCE SHEET. A broker-dealer holds
customer money and earns brokerage revenue, and says so in concepts an asset
manager has no reason to tag: receivables from customers, cash segregated
under the SEC's customer-protection rule, brokerage commissions, principal
transactions, investment banking revenue.

Each piece of evidence has to be CURRENT — Evercore and PJT Partners tag
investment banking revenue whose series stopped in 2018, Intercontinental
Exchange's segregated cash stopped in 2015 — and the balance-sheet ones have
to be MATERIAL, at least one percent of the firm's own assets, because every
financial company parks a little cash somewhere. Ameriprise Financial holds
nine hundred million dollars of segregated cash against a hundred and
ninety-eight billion of assets and is a wealth and insurance group.

Measured across twenty-four filers in those codes, the test admits all ten
genuine broker-dealers (Schwab, Interactive Brokers, Robinhood, LPL, Raymond
James, Stifel, Morgan Stanley, Goldman Sachs, Jefferies, Virtu) and one of
the fourteen others: MarketAxess, whose bond-trading venue operates a
registered broker-dealer holding forty-nine million dollars of segregated
customer cash. That is a real broker-dealer fact about a business that is
really a trading venue, and it is recorded here rather than papered over.

### The subtype does not gate anything

Retail, institutional or both, read from the annual report. Unlike an
insurer's subtype this does not change which numbers are valid — both kinds
are read on book value, return on equity and their own history of price to
earnings — so where the report cannot separate them the model still runs and
the mix is reported as undetermined. Refusing a filer over a label that would
not have changed a single number would be theatre.

A material deposit book is flagged separately, from filed deposits rather
than from prose: Schwab, Raymond James, Stifel, Morgan Stanley and Goldman
Sachs all fund themselves substantially that way, and it changes what their
balance sheet is doing.

### Client assets are refused

They are the numbers this industry actually runs on and they are not in the
filings. `PayablesToCustomers` is tagged by eight of the ten brokers and by
none of them since 2020; `AssetsUnderManagementCarryingAmount` appears once,
for LPL, dated 2012. Every figure that circulates comes from press releases
and monthly activity reports.

They stay blank. For a retail or diversified brokerage the missing number
caps the fair-value confidence at MODERATE, because a valuation built on book
value and earnings can look steady while customers leave.

## Real option data — `chain_store.py` and the daily capture

### What every observation now retains

    [strike, bid, ask, implied volatility, delta, open interest,
     last, volume, quality]

plus, per day: the underlying price, the capture timestamp, the quote source
and the event state (an earnings date inside the window, and how far away).
The first six are the pre-Phase-5 layout and are read unchanged; a row
written before Phase 5 has no last trade, volume or quality, and a reader
must treat that as "not recorded that day" rather than as zero.

Quality separates a two-sided market a penny wide from a one-sided quote and
from a stale one. Both are "real" and only one of them is a price anybody
could have traded at.

### The capture

Once a day after the close, for the followed names: expirations from today
out to about fifty days, and a bounded ring of strikes around the money. That
covers every tenor the covered-call simulator sells — weekly, fourteen to
twenty-one days, thirty to forty-five — with room for a fair-value-aware
strike a quarter above the price, and it is a small request rather than a
whole chain.

**Nothing is ever back-filled.** There is no source of historical option
chains this app can reach. A day that goes uncaptured is gone. A day already
on disk is never replaced, so a second capture cannot quietly rewrite what a
backtest was built on.

### Readiness

Every covered-call run now carries a readiness block: how many days of real
chains exist, when they start, what share of the run's days they cover, and
one of three modes — REAL CHAIN BACKTEST, PART REAL / PART MODELED, or
MODEL-BASED ESTIMATE. Real fills and model fills are counted separately and
never blended into a single accuracy figure, because they are different kinds
of number rather than the same number known to different precisions.

## Is today's recording good enough for tomorrow's scoring?

The forward-validation panel now audits its own inputs. Eight things must be
in every daily row for a future scoring pass to settle up exactly:

    the share price · the config hash · the recommendation · the preferred
    structure · the exact contract AND the quote it carried · the benchmark
    it will be measured against · the fair value · the buy zone

Two of those were not being recorded before Phase 5. The exact contract now
goes in with its bid, ask, mid, spread, open interest, volume, delta,
implied volatility and quote source; the sector benchmark and its close go in
on the day the recommendation is made, because choosing a benchmark after
seeing which one flatters the result is the same lookahead everything else
here refuses.

The audit looks FORWARD. Rows already on disk are never rewritten, so a row
written before a field existed will always lack it and that is correct. What
matters is whether what is being written today is complete, because nothing
can be filled in after the fact.

## Bugs found in earlier phases

Five, all real and all now fixed:

1. **Book value was overstated for twenty-two of fifty-three filers.**
   `StockholdersEquity` is the parent company's equity;
   `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`
   adds equity belonging to somebody else. Both are usually filed on the same
   date, so the coverage tie-break picked whichever had been tagged longer.
   Interactive Brokers' book value came out 73% too high, American Tower's
   65%, Simon Property Group's 21%. The same fault picked a component over
   its total for short-term borrowings (Goldman Sachs, 28× too small) and for
   intangibles. Fixed with `STRICT_INSTANT_PRIORITY`.
2. **Bank of America's refusal was over-broad.** Refusing tangible book for
   any filer that does not tag preferred stock also refused Chubb, Aflac,
   Hanover, American Financial and RLI — none of which HAS any preferred.
   `fundamentals.preferred_equity` now tells "we could not find it" from
   "there is none": no preferred balance AND no preferred dividend AND no
   preferred share count is the filings saying there is none. Bank of
   America, which pays $1.5bn a year of preferred dividends, is still
   refused.
3. **LPL Financial's return on equity read 2.8% against a real 18.6%.** Its
   `NetIncomeLossAvailableToCommonStockholdersBasic` series stopped in 2012
   and the models took it because it was present rather than because it was
   current. `fundamentals.net_income_to_common` now takes whichever series is
   CURRENT and prefers the common figure only on a tie — which is also what
   Charles Schwab needs, where the total picks up a series reading $1.3bn
   against a real $9.7bn.
4. **Travelers' business description was a paragraph about holding-company
   liquidity.** `_item1_body` took the LONGEST candidate, and a cross-
   reference late in the report ("see Part I, Item 1 — Business —
   Regulation") ran to the next mention of Risk Factors and beat the chapter
   it pointed at. It now takes the FIRST candidate with a chapter's worth of
   prose behind it, because a document is written in order. This also fixed
   CME Group.
5. **The refusal told the reader a model existed that already did.** The
   four-dimension scorecard refuses every specialized business — that part is
   right, because the scorecard itself is the generic one — but it went on to
   say "that is not built yet", which stopped being true for lenders and
   property trusts in Phase 4 and for insurers and brokers here. Progressive
   showed a full insurer panel above a sentence saying no insurer model had
   been written. It now names the panel that holds the answer, and keeps the
   old sentence only for a company whose specialized model genuinely could
   not run — an insurer whose kind cannot be established, a filer in a
   broker's industry code that is not a broker.

## What is still not built

* **A verdict for an insurer whose annual report cannot be read.** Sixteen of
  forty-two. The fix is a better Item 1 extractor, not a looser model.
* **Exchanges and asset managers** are still SPECIALIZED MODEL REQUIRED. They
  do not need a specialized model — a derivatives exchange has real free cash
  flow and real margins, and the standard model would serve it — but routing
  them there is a business-type reclassification rather than one of Phase 5's
  three jobs. MarketAxess is the one venue the broker test wrongly admits.
* **Ameriprise Financial and the other hybrids.** A firm that is a large
  retail brokerage AND a large annuity writer is read as neither. The
  business-type gate allows one answer per filer.
* **Client assets, net new assets and assets under administration.** Not in
  the filings. A cached filing-table reader could reach the press-release
  tables; nothing here scrapes prose.
* **Statutory capital.** Insurers file risk-based capital with state
  regulators, not with the SEC in XBRL. Equity to total assets is reported
  instead, under its own name.
* **Real-chain covered-call backtests.** The capture starts from the day this
  ships. Every run today is still a MODEL-BASED ESTIMATE and says so.
* **Forward validation with a sample in it.** Every horizon still reports
  INSUFFICIENT SAMPLE, which remains the correct answer.

