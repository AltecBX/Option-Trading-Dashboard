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



# Phase 6 — the right engine, and better data to feed it

Phase 5 built an insurer model and a broker model and sent companies to them
by SEC industry code. Two things were wrong with that, and both showed up as
refusals that read like limitations of the company rather than of the app.

The first was the document reader. Five insurers were refused because their
business chapter could not be found, and not one of those failures was the
filer's fault: three of them file a 10-K/A amendment after the 10-K, and an
amendment carries only the items it amends. The second was the routing.
Industry code 6211 holds Charles Schwab, Goldman Sachs and BlackRock; 6200
holds LPL Financial and the CME; 6282 holds Evercore and T. Rowe Price.
Sending all of them to a broker model meant the model correctly refused most
of them, and the refusal was then read as "this company cannot be valued"
when what it meant was "this company is not a broker".

Phase 6 does four things: it reads the document properly, it routes on
economics rather than on a code, it handles a company that is genuinely two
financial businesses, and it reads a small, named set of operating measures
out of the filing tables that XBRL does not carry.

## Reading the business chapter

`filing_reader.py` tries four methods in a fixed order, best evidence first,
and reports which one worked:

| Method | What it uses | Highest confidence |
|---|---|---|
| Table of contents anchor | the document's own link to Item 1 | HIGH |
| Heading element | an element whose whole text IS the heading | HIGH |
| Heading text boundary | Item 1 through Item 1A in the flattened text | MODERATE |
| Longest candidate | the Phase 5 heuristic, last resort | LOW |

Four things had to change for that to work on real filings.

**Filings are tried newest first, and an amendment that has no Item 1 is
passed over.** Ameriprise, Equitable and Interactive Brokers all file a
10-K/A after the 10-K; the amendment carries Part III and an exhibit index
and nothing else. The reader gets nothing usable from it and moves on to the
10-K underneath, which costs one extra fetch and answers the question.

**Every heading pattern tolerates whitespace between any two letters.**
Berkshire Hathaway styles its chapter heading letter by letter, so the
flattened text reads `Item 1. Busines s Description`; Cincinnati Financial
writes `I TEM 1.` and MarketAxess `I tem 1.`. `sec_filings._plain`
deliberately does not close up a close-then-open tag pair — joining
`authorized</span><span>the` would be far worse than the blemish — so the
tolerance lives in the heading patterns, where it is safe because the
pattern is short and anchored.

**The read limit went from 4MB to 20MB.** Thirty of fifty-six documents
measured exceeded four megabytes. Filings are immutable and cached by
accession, so the document is fetched once, ever.

**A chapter's own contents list is cut off the front, bounded by the first
sentence.** MetLife, Prudential, Brighthouse and Apollo open Item 1 with an
index of the chapter. An unbounded version of that rule sliced Visa, Simon
Property and seven others off mid-sentence, because ordinary prose is full
of label-then-number shapes: "as of December 31, 2025", "Note 4". A contents
list contains no sentences, and that is the whole safety of the rule.

Rejections are explicit. A contents entry, a cross-reference, a
single-paragraph pointer and a block of Risk Factors text that ran on past a
missing boundary each produce a named refusal rather than a body.

**Confidence gates classification, not display.** A LOW extraction is still
shown to a reader as a description — a person can judge prose for
themselves — and is not allowed to decide which valuation model runs. The
subtype is read from a fixed budget of forty thousand characters, which is
what Phase 5 classified on: Palomar Holdings comes out a property-casualty
insurer on forty thousand characters and a reinsurer on sixty thousand,
purely because its reinsurance section sits at the end, and what a company
is must not depend on how much of its chapter the reader managed to keep.

### Coverage, before and after

| Universe | Chapter read before | after | Classified before | after |
|---|---|---|---|---|
| 42 insurers | 38 | 41 | 37 | 40 |
| 27 exchanges, managers and brokers | 23 | 27 | — | — |

The three insurers that gained a classification are Cincinnati Financial
(multiline), Equitable Holdings (life) and Berkshire Hathaway (multiline,
and then overridden — see below). Of the four filers in the second group
that gained a readable chapter, three are the amendment cases and the fourth
is Morgan Stanley, whose chapter heading is the single word "Business".

Two insurers are still refused, and both refusals are correct:

* **Alleghany** files no annual report at all. It was taken over in 2022 and
  stopped filing. The reason says so.
* **AIG's** chapter is now read at HIGH confidence and still does not say
  clearly enough what kind of insurance it writes: five property-casualty
  signals against a minimum of eight, with its chapter organised by segment
  names rather than by lines of business. Loosening the classifier to admit
  it would admit a great deal else, and claims over premiums is a loss ratio
  for one kind of insurer and not a ratio at all for another.

Equitable Holdings is now classified as a life insurer and is still refused
by the insurer model, for a different and honest reason: its shareholders'
equity is negative, so there is no book value to value it on.

## Routing on economics

`business_routing.py` answers "which model does this company get" from three
kinds of filed evidence and one kind of prose.

**What is on the balance sheet**, as a share of total assets: customer
receivables plus segregated cash, deposits or loans, policy reserves.
**What the revenue is made of**, as a share of revenue: premiums, advisory
fees, commissions and dealer trading, market data and clearing fees.
**Whether the accounts behave like a corporation's at all** — capital
expenditure reported, and operating cash flow reported, positive, and
between 2% and 150% of revenue. **And what the chapter says**, reduced to
family names, and only when the reader is confident it found the chapter.

Every threshold was measured:

| Measure | Level | Why that level |
|---|---|---|
| Customer money as a share of assets | 3% | Every genuine broker clears it; Stifel is thinnest at 3.4%. MarketAxess sits at 2.0% and Ameriprise at 0.5% |
| Deposits or loans as a share of assets | 20% | Stifel 72%, Raymond James 67% |
| Commissions, dealer trading and underwriting as a share of revenue | 30% | Virtu is a market maker with barely any customer balances and 70% trading revenue; MarketAxess earns 5.9% from principal trading |
| Policy reserves as a share of assets | 10% | Apollo 70%, KKR 27%, Ameriprise 20%, Berkshire 6.6% |
| Market data and clearing fees as a share of revenue | 5% | Decisive when present |
| Kinds of market-infrastructure language in the chapter | 2 | Catches Nasdaq, Tradeweb, Coinbase, MarketAxess |
| Kinds of asset-management language in the chapter | 2 | Catches BlackRock, T. Rowe Price, Invesco, Franklin, Blackstone, KKR, Apollo, Ares |

**Evidence has to be current.** Every one of these measures is refused when
the concept behind it is more than 400 days behind the filer's own newest
fact, and that rule does most of the work. Evercore tags investment banking
revenue whose series stopped in 2018. So do the market-data and clearing-fee
concepts at the CME, Intercontinental Exchange and Cboe, and the advisory-fee
concepts at every asset manager measured: the revenue standard changed
between 2013 and 2018 and everyone folded the detail into a single
revenue-from-contracts total. Written without the freshness rule, this
module read Evercore as a dealer on a seven-year-old figure and T. Rowe
Price as earning 59% of today's revenue from a 2018 fee line. Both were
wrong, and both were caught by measuring rather than by reasoning.

The consequence is that an exchange and an asset manager are usually
recognised from their business chapters rather than their revenue lines, and
the module says so rather than implying a precision it does not have.

**Materiality is what separates the hard cases.** Intercontinental Exchange
holds customer margin worth 4.6% of its assets, because it runs clearing
houses. Morgan Stanley holds customer balances worth 4.9% of its assets,
because it is a broker. No threshold can tell those apart, and nothing here
pretends one can: what settles ICE is that its chapter describes a market
venue, a clearing house, an electronic marketplace, listing services and a
regulated contract market, and its accounts are an operating company's.
Where a chapter uses both kinds of language — Franklin Resources' regulation
section names clearing houses and national securities exchanges — the kind
it says MORE about wins, and a tie goes to the venue, because a manager
describes exchanges it uses while a venue does not describe money it does
not manage.

Every routing decision is inspectable, behind an expander: the class, the
model, the confidence, the sentences of reasoning in the order they were
weighed, every material exposure with the measurement and the level it had
to clear, whether the accounts behave like an operating company's, and which
filing the description came from.

### Where each kind goes

| Class | Model | Note |
|---|---|---|
| Standard operating company | STANDARD | unchanged |
| Bank or lender | BANK | unchanged, by industry code |
| Insurer | INSURANCE | unchanged, by industry code |
| Real estate investment trust | REIT | unchanged, by industry code |
| Broker or dealer | BROKER | now from customer money and trading revenue |
| Exchange or market infrastructure | **STANDARD** | new |
| Asset manager | **STANDARD** | new |
| More than one financial business | the dominant one, or none | new |

An exchange and an asset manager go to the standard engine because both have
ordinary revenue, ordinary margins and ordinary free cash flow, and neither
is valued on book value. No exchange multiple and no asset-manager multiple
is declared anywhere: they are valued the ordinary way, against their own
history and their own peers.

One industry code changed hands. 6199, "finance services", is the code the
SEC gives a filer that fits nowhere else, and it used to sit at the top of
the bank range; Coinbase files under it. It is now answered from evidence
alongside the broker codes, and a genuine lender filing under it still
reaches the bank model through the deposits-or-loans measure.

## More than one business at once

A company can be two financial businesses, and each of them is valued
differently. Ameriprise is a wealth manager and an annuity writer; Apollo
and KKR are alternative asset managers that each bought an annuity writer;
Berkshire files under an insurance code and describes a railway, a utility
group and a collection of manufacturers in its first paragraph.

There are three answers and no fourth:

* **ONE MODEL RUNS** — only one of the businesses has a model that can be
  built from the filings. That one is used and the others are disclosed.
* **MODELS AGREE** — both can be valued and their base values fall inside
  the same agreement band the fair-value engine already uses. The normal
  confidence machinery resolves it and both ranges are shown.
* **MODELS DISAGREE** — both can be valued and they do not agree. The answer
  is HYBRID — VALUATION UNRELIABLE, which names the businesses that
  disagree and how far apart they are, and shows no single fair value.
  Apollo's two models are 194% apart.

**No sum of the parts is attempted anywhere.** Segment revenue and segment
income are not in SEC Company Facts at all — the flattened facts carry
consolidated values only — so a segment-weighted valuation would be a guess
with a decimal point on it.

Berkshire is a special case handled by the conglomerate marker: its own
business chapter says it is a holding company owning subsidiaries engaged in
numerous diverse business activities, and that overrides its insurance
industry code. Its policy reserves are 6.6% of its assets, below the level
at which an insurance model could carry the whole company, and its accounts
are an operating company's, so it is valued the ordinary way with the
insurance business disclosed. That is the outcome Phase 6 was asked for:
correct refusal to force it into a pure insurance model.

## Reading the filing tables

`filing_tables.py` reads a small, named set of measures out of SEC filing
documents. This is not web scraping: every document comes from the app's own
SEC transport, by accession number, out of the EDGAR archive. Nothing is
fetched from a company website, nothing is read out of a PDF, and no
language model is asked what a row means.

Ten metrics, each with an explicit list of row labels:

| Group | Metrics |
|---|---|
| Broker and wealth | client assets, assets under administration, advisory assets, net new assets |
| Asset manager | assets under management, net flows |
| Insurance | published combined ratio, published loss ratio, published expense ratio |
| Property trust | published funds from operations |

The rules are all refusals:

* **Exact label matching after normalisation.** Parentheses always come off —
  filers put three different things in them, and none belongs in the label:
  a footnote marker, an abbreviation (`Assets under administration ("AUA")`)
  and the scale (`Customer Equity (in billions)`). "Assets" alone is not
  client assets.
* **A period on every number.** A figure whose column names no period is
  refused. A period after the filing date does not exist. A reporting period
  ends on the last day of a month, so a heading naming 15 July is naming the
  day the release was issued and does not outrank the quarter end printed
  beside it.
* **Units read from the table, most local statement winning.** The row's own
  words beat the table's heading, which beats the sentence before the table.
  Interactive Brokers prints "Customer Equity (in billions)" inside a table
  captioned in thousands, and Robinhood prints platform assets in billions
  inside a table captioned in millions.
* **A hedged caption over a decimal balance is refused.** "(in millions,
  except per-share data)" does not say which rows the exception covers.
  T. Rowe Price prints ending assets under management as "$1,893.4" inside
  such a table, meaning 1,893.4 BILLION; reading it on the caption's scale
  turns a $1.9 trillion book of other people's money into $1.9 billion.
  BlackRock's "15,344,624" in the same kind of table is genuinely millions
  and is used. Filers print exact millions as whole numbers and rounded
  billions with one decimal, and that is the distinction the rule rests on.
* **A percentage is never a money figure.** Schwab prints two per-cent-change
  columns before the dollars; reading the first number in the row without
  that check turns thirteen trillion dollars of client assets into eleven.
* **Ambiguity is refused, not resolved.** Travelers prints a combined ratio
  for every segment; five rows share the label and disagree, so the answer
  is `N/A — AMBIGUOUS TABLE MATCH`. Two tables printing the same figure in
  millions and in thousands agree once converted and are not ambiguous.
* **Continuity is checked.** A balance that moved by a factor of a thousand
  is a unit error, not news. A book of client money that tripled in a
  quarter is held back for checking.

Every reading carries the accession, the document, the table, the row label,
the column heading, the raw text, the parsed value, the scale and where the
scale came from, the period and its precision, and the window. Readings are
cached by accession and document and written once: a filing never changes,
so a reading of one never changes, and a later filing writes a new entry
rather than editing an old one.

### What that yields today

| Company | Measure | Value | As of |
|---|---|---|---|
| Charles Schwab | Client assets | $13.08tn | June 30, 2026 |
| Charles Schwab | Net new assets | $118.7bn | June 30, 2026 |
| Raymond James | Assets under administration | $1.86tn | June 30, 2026 |
| LPL Financial | Advisory assets, net new assets | $1.55tn, $23.6bn | June 30, 2026 |
| Interactive Brokers | Client assets | $930bn | June 30, 2026 |
| Robinhood | Client assets | $279bn | June 30, 2026 |
| BlackRock | Assets under management | $15.34tn | June 30, 2026 |

For a retail or diversified broker, the Phase 5 fair-value confidence cap
that existed **only** because client assets were unknown lifts on its own
when a reading like these is available: the cap reads the same field the
table fills.

## Rebuilt against published

Where a measure has to be rebuilt from XBRL and the company also prints it
in a table, the two are shown side by side. Agreement corroborates the
reconstruction; a difference of more than 5% is flagged as RECONSTRUCTION
MISMATCH and holds the fair-value confidence down to LOW until it is
explained. The nicer of the two numbers is never quietly chosen, and the
rebuilt figure is not replaced by the published one.

Two honest limits on this today. Windows have to match: Realty Income heads
one table "Three months ended June 30, | Six months ended June 30," and
prints both, and a heading that names two windows at once is AMBIGUOUS and
is not compared with anything — an earlier version of this check reported a
300% mismatch that was a quarter measured against a year. And Simon Property
publishes "Real Estate Funds from Operations of the Operating Partnership",
which is a different measure from funds from operations attributable to
common shareholders; it is not used, because a cross-check against a
different measure is worse than no cross-check.

## Bugs found in Phases 1 to 5

Four, all real and all now fixed:

1. **The newest annual filing is not always an annual report.** Taking
   `filings()[0]` among 10-K and 10-K/A gave Ameriprise, Equitable and
   Interactive Brokers an amendment with no Item 1 in it, and the reader
   reported "no business chapter" for three companies whose business
   chapters are perfectly ordinary.
2. **The 4MB read limit truncated thirty of fifty-six annual reports.** It
   was not the cause of the classification failures, but it cut Item 1A
   boundaries in half the universe, which is why so many extractions ran to
   exactly the forty-thousand-character cap.
3. **Routing evidence was read without a freshness check.** Writing this
   phase surfaced it: the concepts behind advisory fees, market data and
   clearing fees all stopped being tagged between 2013 and 2018, and reading
   them as current made Evercore a dealer and T. Rowe Price a company
   earning 59% of today's revenue from a 2018 line. The Phase 5 broker gate
   already applied a 400-day rule; routing now applies the same one.
4. **The subtype moved with the amount of text.** Reading more of Palomar
   Holdings' chapter changed it from a property-casualty insurer to a
   reinsurer, because its reinsurance section sits at the end. Classification
   now reads a fixed budget.

## What is still not built

* **AIG.** Its chapter is read and still does not say clearly enough what
  kind of insurance it writes. The fix is a better subtype classifier, not a
  looser one.
* **Alleghany and companies like it.** A filer that has stopped filing has
  no annual report to read. This is permanent and correctly refused.
* **Equitable Holdings' book value.** Negative shareholders' equity leaves
  the insurer model with nothing to value against. That is a real feature of
  the company, not a data gap.
* **Segment economics.** Not in Company Facts, so no sum-of-the-parts
  valuation exists and none is attempted.
* **Assets under management for most managers.** The tables that carry it are
  captioned in millions with the row printed in billions, and the hedged-
  caption rule refuses them. BlackRock's is readable; T. Rowe Price's,
  Franklin's and Invesco's are not.
* **A published combined ratio for most insurers.** Travelers and Progressive
  print one per segment and the rows disagree, so the answer is ambiguous.
  Chubb's is readable.
* **Real-chain covered-call backtests and forward validation with a sample
  in them.** Unchanged from Phase 5: the capture is prospective, nothing is
  back-filled, and INSUFFICIENT SAMPLE remains the correct answer.


# Phase 7 — the weak inputs made production-grade

Phase 7 adds no valuation engine. It takes the four data inputs the earlier
phases left thinnest and makes them good enough to run on: an annual report
organised by segment rather than by adjective, the scale a filing table
prints its numbers on, the comparison between a rebuilt figure and a
published one, and whether the prospective capture is actually happening.

## Insurers whose report is organised by segment

The Phase 5 classifier counts what an insurer SAYS it writes. Across
forty-seven US insurers it answers forty-three of them, and the ones it
cannot answer are the ones that describe themselves by SEGMENT instead.
American International Group's Item 1 is read at high confidence and
mentions "property and casualty" five times in forty-three thousand
characters, because it never needs to. It opens:

> We report the results of our businesses through three segments and Other
> Operations. The three segments are North America Commercial, International
> Commercial and Global Personal.

and then heads two chapters COMMERCIAL LINES PRODUCTS and PERSONAL INSURANCE
PRODUCTS. That is the answer, written the way a filer with reportable
segments writes it.

So a second path reads the segment names. It runs ONLY when the first path
has refused — nothing above it is loosened, and an insurer the keyword rules
can classify is still classified by them. Three channels of evidence, each
worth a different amount:

| Where a family name appears | Worth |
|---|---|
| Inside the sentence declaring the reporting structure, or the 800 characters after it | 4 |
| Inside an all-capitals heading, which is how filers style chapter titles and how the plain text keeps them | 3 |
| Anywhere else in the chapter | 1 |

A family needs 12 points to count at all and three times the runner-up to be
called dominant. Property-casualty and life both material and neither
dominant is MULTILINE, which is what such a company is. Anything else is
UNRESOLVED — a wrong subtype applies the wrong ratios, and a wrong ratio is
worse than a blank.

Measured: AIG scores 40 on property-casualty against 3 on health and 0 on
life. Essent Group, a mortgage insurer whose chapter never uses the words
"property and casualty", scores 139 against 1. Both are now classified;
neither is hard-coded.

Coverage went from 43 of 47 insurers to 45. The two that remain — NMI
Holdings and MGIC — are refused at the EXTRACTION gate, not by the
classifier: their business chapters are read at LOW confidence and are not
allowed to decide anything.

## A company-wide ratio needs a company-wide basis

A multiline insurer files one premium line and one claims line, and each
covers whatever the company chose to put in it. Where the company is
genuinely two businesses, the claims include benefits paid by a life book
whose earnings the premiums leave out, and dividing one by the other
produces a number that looks like a loss ratio and is a blend.

Two tests, and both have to pass:

1. **The concepts have to describe the same business.** `PremiumsEarnedNet`
   is company-wide; `PremiumsEarnedNetPropertyAndCasualty` is not.
   `IncurredClaimsPropertyCasualtyAndLiability` over `PremiumsEarnedNet` is
   one business's claims over another's premiums.
2. **For a multiline, the life side has to be immaterial.** Measured as what
   the company owes policyholders on long-dated contracts, as a share of
   total assets, from a fact no more than 400 days old.

Measured across today's multiline insurers: Chubb 3.2%, Kemper 4.5%,
Cincinnati Financial 6.9%, Berkshire Hathaway 1.4% — and Horace Mann at
32.3%, which is a life company with a car insurer attached. The line is at
10%, and only Horace Mann is above it.

Where the test fails the answer is `UNDERWRITING METRIC N/A — MIXED BUSINESS
BASIS`. Book value, returns, reserves, investments, the capital ratio and
the valuation itself are untouched: it is only the underwriting ratios that
have no honest basis.

## What scale a number is printed on — `filing_units.py`

A filer captions a table "(in millions, except per-share data)" and then,
eighteen rows down, prints assets under management in billions under a
heading of its own. T. Rowe Price does exactly that: **$1,893.4** means $1.9
TRILLION. BlackRock prints **$15,344,624** in the same kind of table and
means millions. Both are correct, and the caption is not the whole story.

So the scale comes from the most specific statement that covers the number,
and the reading records which one that was:

| Precedence | Example | Confidence |
|---|---|---|
| 1. The row's own label | `AUM (at period end, in billions)` | HIGH |
| 2. A section heading above it | `Assets under management (in billions) (4)` | HIGH |
| 3. The column it sits under | a head cell reading `(in billions)` | HIGH |
| 4. The table's heading rows | one scale, stated once | MODERATE |
| 5. The caption before the table | `(in millions, except per share data)` | LOW |
| 6. Nothing says | UNKNOWN, and the figure is refused | — |

Two rules keep it honest. **No magnitude guessing** — "this looks too big to
be millions" is not evidence. **A conflict at the same level is ambiguous** —
two head cells that disagree about a column do not get ranked by luck.

The caption is held to a stricter form than the row and the column are,
because the text before a table is prose. Invesco's reads "market returns
which decreased AUM by $59 billion", which is a sentence about a change, not
a statement about the table.

Every reading now stores the raw number, the raw unit, the resolved unit,
the normalised value, the unit source and the unit confidence — and refuses
an impossible unit for its metric: a percentage is not a quantity of
dollars, and a per-share figure is not a company total.

Continuity against the previous filing changed too. A thousandfold jump is
still refused, because that IS a unit error. A large but possible move is now
FLAGGED and still shown, because continuity may raise a hand and may never
quietly rewrite a number or its unit.

## The number comes from the column its period heads

Reading the first figure in a row is right when the current period is printed
first and wrong when it is printed last. Affiliated Managers prints:

```
(in billions, except as noted) | 2025    | 2026    | % Change
Assets under management        | $771.0  | $942.4  | 22 %
```

so the first figure is a year out of date. Columns are now lined up with the
heading row that has as many cells as the data row, or one fewer — Schwab's
heading row is `['2026', '2025', '2026', '2025']` with no stub cell, and
reading it the other way took Schwab's client assets from the June 2025
column and reported $10.8 trillion as this quarter's figure.

Three more shapes now read:

* **Day-month-year headings.** Franklin Resources heads every column
  `30-Jun-26`, and without it every one of its columns named no period.
* **Curly-apostrophe quarters.** Blackstone writes `2Q’26`.
* **A bare quarter over a bare year.** BlackRock heads one column `Q2` and
  the one below it `2026`; neither cell is a period and the pair is.

And one shape is now refused outright. Filers lay two independent tables side
by side inside one HTML table, so BlackRock's net-flows row reads
`['Total net flows', '$191,700', '$67,737', 'EMEA', '55', '68']` — two rows
glued together, whose columns cannot be lined up with any heading. A word
sitting where a number should be is the tell.

## Segment figures are not company figures

Travelers prints a combined ratio five times in one filing: once for the
company and once for each of Business Insurance, Bond & Specialty Insurance,
Personal Insurance and Personal Automobile. All five rows carry the same
label. What tells them apart is what the filer wrote above the table —
"CONSOLIDATED OVERVIEW … Consolidated Results of Operations" against "Segment
Income by Major Component and Combined Ratio — Business Insurance" — so that
is what is read. A table the filer heads as one segment is never read for a
company-wide measure, and Travelers' consolidated 83.6% is now used where
Phase 6 refused all five as ambiguous.

## Funds from operations, five ways

Simon Property Group prints, in ONE table:

| Row | Amount |
|---|---|
| FFO of the Operating Partnership | $1,184,945 |
| FFO allocable to limited partners | $174,687 |
| Dilutive FFO allocable to common stockholders | $1,010,258 |
| Real Estate FFO | $1,248,564 |

Only the third is what a share of Simon is entitled to, and comparing either
of the others against a reconstruction reports a mismatch that is really a
definition difference. So the funds-from-operations label is parsed by an
explicit grammar — operating partnership, core, normalised, adjusted, per
share, attributable to common shareholders — and only the common-shareholder
basis is used. This is not a loosening of the exact-label rule; it is a
stricter rule for a label that names five different measures.

## Published against reconstructed — `cross_check.py`

The Phase 6 check compared whatever it found. That is how a published quarter
gets compared with a trailing twelve months and reports a 300% disagreement
about the units of time. The reusable layer has five states and makes a
comparison only when the basis, the period and the window ALL match:

| State | What it means |
|---|---|
| MATCH | The two are the same number. |
| MINOR DIFFERENCE | They differ by less than the tolerance. |
| MATERIAL MISMATCH | They do not agree. The reconstruction's confidence is lowered and both are shown. |
| INCOMPATIBLE BASIS | There is a published figure, on a different basis, period or window. |
| PUBLISHED UNAVAILABLE | The company does not print it in a readable table. |

In practice that means an ANNUAL published figure against the reconstruction
**rebuilt as of that same year end** — the model is re-run at the published
figure's date so the two cover the same twelve months. Neither number ever
replaces the other.

Alongside it, every asset and flow measure the filings supplied is listed
with its own period, scope and unit. Client assets, assets under
administration, advisory assets, assets under management, net new assets and
net flows are six different things: a custodian's assets under
administration include money it merely holds, an adviser's advisory assets
are the part it is paid to advise on, and assets under management are the
part it actually runs. None of them stands in for another and none is added
to another.

## Is the capture actually happening? — `capture_health.py`

The biggest risk left is not another formula. It is silently missing
prospective data, and finding out six months later during a backtest.

Five things are expected once per followed ticker per TRADING day:

| Kind | Recoverable if missed? |
|---|---|
| Investment snapshot | Yes, from filings |
| End-of-day option chain | **No. Gone for good.** |
| Long-dated contract observation | No |
| Sector benchmark close | Partly |
| Recommended contract quote | No |

Every attempt writes down whether it was expected, attempted, successful,
when, from which source, how many records and why it failed. The next
morning the day before is COMPLETE, PARTIAL, MISSED or NOT EXPECTED, and the
system as a whole is HEALTHY, PARTIAL or CAPTURE FAILURE. Where the app
already has a push configured, an incomplete day uses it — no new
notification subsystem was built.

**The calendar is computed, not tabulated.** Weekends, and the ten US market
holidays including Good Friday from the Gregorian computus and the
observance rules for fixed dates. A hard-coded table of dates is a thing
that stops being true on a date nobody remembers; these rules have not
changed in decades, and Juneteenth — the only recent addition — has a start
year. A Saturday is NOT EXPECTED, never MISSED.

**Nothing is ever back-filled.** A missed option chain stays missed. This
module reports the hole; it never fills it.

## Bugs found in Phases 1 to 6

1. **The scheduler read the container's clock as though it were New York's.**
   `RECORD_AFTER_ET_HOUR = 17` against a naive `datetime.now()` on a UTC
   container fires at one in the afternoon in New York — so the "end of day"
   chain was captured mid-session and the daily snapshot was taken before the
   close. Both now read an exchange clock.
2. **Weekends and market holidays were captured.** With no trading-day gate,
   a Saturday capture stored Friday's stale quotes under Saturday's date, and
   a backtest would later fill a Saturday from it.
3. **The first figure in a row is not always the current one.** Affiliated
   Managers prints 2025 before 2026, so its assets under management were read
   a year out of date.
4. **A heading row without a stub cell was read off by one.** Schwab's client
   assets came from the June 2025 column: $10.8 trillion reported as this
   quarter's figure.
5. **Company-wide net flows were read out of a segment column.** T. Rowe
   Price's rollforward puts the Equity column first and the Total column
   last.
6. **A published quarter was compared against a trailing twelve months** and
   reported as agreement or mismatch depending on luck.
7. **A REIT's operating-partnership FFO was eligible for the check.** The
   difference between it and the common-shareholder measure is the limited
   partners, not an error.

## What is still not built

* **A published combined ratio for Progressive.** It prints five in one
  table, one per line of business plus a total, and which row is which
  cannot be told from the table. Ambiguous, and refused.
* **Assets under management for Blackstone, KKR and Ares.** Blackstone
  prints a company total and two segment totals under labels that normalise
  the same, so all three are ambiguous. Ares prints its AUM inside a chart
  label rather than a table row. KKR prints none this reader can find.
* **American Tower's published funds from operations.** Its table states no
  scale anywhere a reader can attach to the row, and the figure is refused
  rather than guessed at.
* **Prologis' published funds from operations.** Its filings carry the
  definition and not the figure.
* **NMI Holdings and MGIC.** Their business chapters are read at LOW
  confidence, so nothing is allowed to be classified from them.
* **Real-chain covered-call backtests and forward validation with a sample
  in them.** Unchanged since Phase 5, and unchangeable by writing code: the
  capture is prospective, nothing is back-filled, and INSUFFICIENT SAMPLE
  remains the correct answer until the horizons complete.
