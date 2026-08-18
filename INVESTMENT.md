# Investment tab — Phase 1

The dashboard's other tabs ask what a stock will do this week. This one asks
whether the business behind the ticker is worth owning at all, and at what
price. Four questions, in this order:

1. Is this a strong, profitable business?
2. Are revenue and earnings per share growing?
3. Is it cheap compared with its own fundamentals?
4. At what price would it be worth owning?

Phase 1 answers those four and stops. There is no cash-secured-put optimizer,
no LEAPS optimizer, no full fair-value engine, no structure comparator, no
reverse DCF and no peer engine — those are later phases, and this document
ends with what is ready for them.

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

## The verdict

Five words: **ATTRACTIVE · WATCH · WAIT · AVOID · INSUFFICIENT DATA**.

There is deliberately **no 0-100 investment score**. The distance between a
61 and a 68 would not mean anything, and a number invites a precision the
inputs cannot support. Every verdict is reproducible by hand from figures
printed on the same screen, and every WAIT or AVOID states what would have to
change:

> **WAIT** — At $300.00 a share the analyst forward earnings estimate of $9.00
> is an earnings yield of 3.0%, against a 10-year Treasury yield of 4.20%.
> That is −1.2 percentage points of compensation for owning a business instead
> of a government bond.
>
> Reconsider below $145.16 a share, or if the analyst forward earnings estimate
> rises to $18.60. Either one puts the earnings yield at 6.2% — the 10-year
> Treasury yield plus the 2.0 point cushion this dashboard asks for.

### Why yields, not multiples

Internally the tab thinks in yields: earnings yield (EPS ÷ price) and free
cash flow yield (free cash flow ÷ market value). A yield can be set beside a
Treasury yield; a multiple cannot. A yield stays finite when earnings are
small and stays meaningful when they are negative, where a P/E goes to
infinity and then flips sign. Trailing and forward price/earnings are still
**displayed**, because that is the number everyone reads — but a negative P/E
is never printed, because "−14×" reads like a cheap stock and is an
arithmetic artifact.

**Price to sales is displayed and gates nothing.** A company can sell a great
deal and never earn anything, and the ratio cannot tell the two apart.

**There is no historical forward-P/E series and no ten-year average P/E.**
Both would require an archive of past consensus that does not exist for free.

### Thresholds

All in `thresholds.json` under `investment`, hashed into every snapshot:

| Key | Default | Meaning |
|---|---|---|
| `attractive_spread_pp` | 2.0 | Percentage points of earnings yield above the 10-year Treasury required for ATTRACTIVE |
| `watch_spread_pp` | 0.0 | At or above the Treasury but below the cushion reads WATCH |
| `min_revenue_growth_pct` | 0.0 | Below this counts as shrinking |
| `min_fcf_yield_pct` | 0.0 | Below this, the business consumed cash |
| `estimate_cut_pct` | −5.0 | Revision breadth below this counts as analysts cutting |
| `fallback_treasury_pct` | 4.0 | Used only when the live curve is unreachable — and the verdict says so |

**This is a demanding bar and that is on purpose.** At a 4.7% 10-year yield,
`attractive_spread_pp: 2.0` asks for a 6.7% earnings yield — a trailing
price/earnings ratio near 15. Most large, high-quality companies read WAIT
against it. Lower it to accept growth at a higher multiple; raise it to be
stricter. Nothing in the code cares which you choose.

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
