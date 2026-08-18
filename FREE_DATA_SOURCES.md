# Free Data Sources — the JerryTrade map

Every external data source this dashboard uses that costs **nothing** — no
Schwab, no Unusual Whales, no paid API. Each entry lists what you get, the
exact access pattern, auth requirements, the gotchas we actually hit in
production, and the cache TTL JerryTrade uses (a sane default for any app).

Written as a feeding guide for a sibling app. Sections marked **[PAID]** at
the bottom are what JerryTrade gets from paid integrations, so you know what
a free-only app gives up.

---

## 1. Analyst price targets, ratings, upgrades & downgrades

### Finnhub (free tier) — used by `analyst_client.py`
- **Get a free key** at https://finnhub.io (no card). Env: `FINNHUB_API_KEY`.
- `GET https://finnhub.io/api/v1/stock/price-target?symbol=AAPL&token=KEY`
  → consensus target: `targetMean / targetHigh / targetLow / numberAnalysts / lastUpdated`
- `GET https://finnhub.io/api/v1/stock/recommendation?symbol=AAPL&token=KEY`
  → monthly analyst breakdown: `strongBuy / buy / hold / sell / strongSell` (last ~4 months)
- **Limits:** 60 calls/min on free tier. Cache 30 min+ (targets change a few
  times a week at most; polling faster just burns quota).

### yfinance (no key) — the workhorse
- `yf.Ticker(sym).upgrades_downgrades` → per-firm rating changes:
  date, firm, action (`up`/`down`/`init`/`main`), fromGrade, toGrade.
  **This is where upgrades/downgrades come from.**
- `yf.Ticker(sym).analyst_price_targets` → `current / low / high / mean / median`
  (used as the fallback when Finnhub is unconfigured).
- `yf.Ticker(sym).recommendations` → recent broker actions table.

## 2. Prices, quotes, history — yfinance (Yahoo, no key, ~15-min delayed)

The free backbone. Everything below is one `pip install yfinance`:

| Data | Call | Notes |
|---|---|---|
| Daily OHLCV, batched | `yf.download("AAPL MSFT …", period="1y", interval="1d", auto_adjust=False, group_by="ticker", threads=False)` | Batch up to ~60 symbols per call; JerryTrade chunks at 60 with a 0.3 s sleep between chunks |
| Single-name history | `yf.Ticker(sym).history(period=…, interval=…)` | Intraday intervals limited to recent windows |
| Company info | `yf.Ticker(sym).info` | name, sector, P/E, forward P/E, dividendRate, market cap — **slow call, cache 12 h** |
| Option chains | `yf.Ticker(sym).option_chain(expiry)` | strikes, bid/ask, last, volume, OI, IV. **No greeks** — compute Black–Scholes from the chain IV yourself (JerryTrade does exactly this as its Schwab fallback) |
| Earnings dates | `yf.Ticker(sym).earnings_dates` / `.get_earnings_dates(limit=…)` | next + historical report dates |
| News | `yf.Ticker(sym).news` | headlines w/ links, lags press wires |
| Economic calendar | `yfinance` calendars module → `get_economic_events_calendar(start, end)` | Reuters-sourced US economic events (CPI, FOMC, jobs…) |
| Indices / futures / crypto | `^TNX ^IRX ^MOVE ^VIX`, `ZT=F ZF=F ZN=F ZB=F UB=F ZQ<mth><yr>.CBT`, `BTC-USD`, `CL=F`, `DX-Y.NYB` | ^MOVE = Treasury vol; ZQ = fed-funds futures → implied policy path = 100 − price |

**Gotchas (all hit in production):**
- Unofficial API — Yahoo rate-limits by IP (429s) and resets connections.
  Batch, cache, back off, and always have a "data unavailable" path.
- `yf.download` silently returns empty frames per symbol on failure —
  check `.dropna()` results, never assume.
- Internal retries can hang for minutes when the host is blocked; wrap in
  timeouts/threads if latency matters.

## 3. News

- **Google News RSS** (no key, no limit worth noting):
  `https://news.google.com/rss/search?q=<query>&hl=en-US&gl=US&ceid=US:en`
  → per-ticker or topic headlines. Parse as RSS/XML.
- **Finviz public quote page** (free, scrape politely with a browser UA):
  `https://finviz.com/quote.ashx?t=AAPL&p=d` → the `news-table` block is the
  **freshest free source for press-release wires** (Business Wire,
  GlobeNewswire) that Yahoo and Finnhub lag on.
- **yfinance** `.news` as a third layer.

## 4. Rates & macro (all official, all free — powers the US Treasuries tab)

### U.S. Treasury — daily par yield curve (`treasury.py`)
- `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=2026`
  → XML, one entry per trading day, all maturities 1M–30Y. One call per
  calendar year; cache current year 30 min, past years forever.
- The same rows give a yield at ANY horizon: interpolate linearly between the
  two quoted tenors either side (`treasury.rate_for_years()`). The Investment
  tab's structure comparator leaves whatever a structure does not spend in
  cash, and what that cash earns depends on how long the position runs — a
  single stand-in rate for every horizon would flatter short structures and
  penalize long ones.

### FRED (St. Louis Fed) — any series as CSV, **no API key**
- `https://fred.stlouisfed.org/graph/fredgraph.csv?id=SERIES&cosd=YYYY-MM-DD`
- **Always pass `cosd`** (start date) — full-history files are 60+ years and slow.
- Series JerryTrade uses: `DGS2/DGS10/DGS30` (yields), `T5YIE/T10YIE/T5YIFR`
  (breakevens), `DFII5/DFII10/DFII30` (TIPS real yields), `DFF` (EFFR),
  `DFEDTARU/DFEDTARL` (target range), CPI: `CPIAUCSL` (headline),
  `CPILFESL` (core), `CUSR0000SAH1` (shelter), `CUSR0000SASLE` (services),
  `CUSR0000SACL1E` (core goods), `CPIUFDSL` (food), `CPIENGSL` (energy),
  `CUSR0000SETA02` (used vehicles), `CPIMEDSL` (medical), `CUSR0000SEHC` (OER).
- **Gotcha:** behind some proxies FRED's edge stalls non-curl user agents —
  JerryTrade retries with `User-Agent: curl/8.5.0` and remembers which UA worked.

### TreasuryDirect — auction results & schedule (official JSON)
- `https://www.treasurydirect.gov/TA_WS/securities/auctioned?days=400&format=json`
  → highYield/highDiscountRate, bidToCoverRatio, indirect/direct/primary-dealer
  accepted amounts, offering size, dates. Enough to score auction strength
  (compare bid-to-cover + indirect share vs the prior 10 same-term auctions).
- `https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json`
  → upcoming auction calendar. Cache 6 h.

### CFTC — COT positioning (weekly)
- Primary: Socrata API
  `https://publicreporting.cftc.gov/resource/gpe5-46if.json?$where=contract_market_name='UST 10Y NOTE'&$order=report_date_as_yyyy_mm_dd DESC&$limit=160`
  → Traders-in-Financial-Futures: dealer / asset-manager / leveraged-fund
  long/short + weekly changes. 160 rows ≈ 3 years for percentiles.
  **Gotcha: throttles anonymous callers per IP — shared cloud egress IPs
  (Railway etc.) get blocked.** Register a free Socrata app token, or:
- Fallback: `https://www.cftc.gov/dea/newcot/FinFutWk.txt` — same data,
  latest week, plain CSV, never throttled. Column map (0-based):
  7 OI · 8/9 dealer L/S · 11/12 asset-mgr L/S · 14/15 lev L/S ·
  24+ weekly-change block in the same group order.

### Schedules (static, published annually — hardcode with a source label)
- CPI release dates: BLS calendar (8:30 AM ET). FOMC: federalreserve.gov.
  Jobs report: first Friday rule. Label them "per published schedule."

## 4b. Company fundamentals — SEC EDGAR Company Facts (`fundamentals.py`)

Everything a company has XBRL-tagged in its own filings, as JSON, free, no key:

```
GET https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json
```

Same declared User-Agent rule as every other SEC endpoint. Payloads run
0.3–3 MB, so cache them — this app keeps them 12 hours on disk. Gives you
revenue, net income, diluted EPS, share counts, operating cash flow and
capital spending per reporting period, plus the filing cover-page share count
under the `dei` taxonomy.

**Dividends per share** are in here too, but which concept a filer uses varies
AND changes over time: Apple and Microsoft tag
`CommonStockDividendsPerShareDeclared` throughout, Coca-Cola's `Declared`
series stops in 2018 while `CommonStockDividendsPerShareCashPaid` continues to
today, and Exxon tags only two quarters of either. Select by coverage and
recency, never by a fixed priority list — a fixed list reads Coca-Cola's 2018
dividend and calls it current. Measured trailing-twelve-month figures after
doing it properly: Apple $1.05, Microsoft $3.64, Coca-Cola $2.06, Costco
$5.37, Realty Income $3.24, Exxon N/A-with-a-reason, Plug Power none.

**Gotchas, all measured on seventeen live tickers — every one of these will
bite you:**

- **Concept names are not consistent between filers.** Robinhood's revenue is
  `Revenues` (72 points); its `RevenueFromContractWithCustomerExcludingAssessedTax`
  has 4 points and stops in 2021. Pick the concept with the most recent,
  best-covered series — never a fixed priority list.
- **Cash-flow facts are cumulative year-to-date** (Q1 = 3 months, Q2 = 6,
  Q3 = 9); income-statement facts are discrete quarters. Filter for ~90-day
  periods and free cash flow comes out empty for almost every company.
  Difference consecutive cumulative periods to get each quarter.
- **Weighted-average share counts are averages, not flows.** Summing four
  quarters claims Microsoft has 30 billion shares instead of 7.4 billion.
- **Retail filers use 52/53-week calendars.** Costco's third-quarter
  year-to-date is 252 days, not 273. Classify periods proportionally.
- **The same period appears many times** as later filings restate it and
  splits re-express per-share figures. Take the value from the LATEST filing
  (restated, split-consistent) but the DATE from the earliest (when it was
  actually knowable) — otherwise a 2021 quarter plots at a 2023 filing.
- **Coverage is not guaranteed.** Exxon's payload currently holds six
  period-ends and no annual report at all.
- **Foreign private issuers file under `ifrs-full` in their own currency**
  (TSMC in TWD, Novo Nordisk in DKK). The ADR ratio and FX rate are NOT in the
  data, so per-share figures cannot be compared to a US dollar ADR price.
- **Annual-only filers (Form 20-F, e.g. Alibaba) have no quarterly facts**, so
  no trailing-twelve-month figure can be built.
- **Multi-class companies** (Robinhood, Shopify) report the cover-page share
  count per class, and Company Facts drops the per-class breakdown entirely.

**Business descriptions**: Item 1 of the latest 10-K, off the same
`data.sec.gov/submissions` feed the filing scanner already uses. Two traps —
"Item 1. Business" also appears in the table of contents (take the occurrence
with real prose after it), and inline-XBRL 10-Ks open with a hidden
`<ix:header>` block that flattens to thousands of taxonomy URLs. Microsoft
additionally sets the first letter of each heading as a drop cap, so the text
reads `ITEM 1. B USINESS`.

**Industry classification, free, on every filer.** The same submissions record
carries the SEC's own Standard Industrial Classification code:

```
GET https://data.sec.gov/submissions/CIK0000320193.json
  -> sic: "3571", sicDescription: "Electronic Computers",
     fiscalYearEnd: "0926", exchanges: ["Nasdaq"], category: "Large accelerated filer"
```

Measured: 6021 JPMorgan (bank), 6311 MetLife and 6331 Allstate (insurers),
6798 Realty Income (REIT), 6211 Robinhood and Schwab (brokers), 2911 Exxon and
Chevron, 1040 Newmont (commodity). That one field answers both "who are this
company's peers" and "which calculations would be nonsense for it" — better
than a vendor's sector string, because it is what the company itself files
under.

For **bulk** SIC lookups use EDGAR's browse endpoint instead, which returns the
same code in a tenth of the bytes:

```
GET https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193
      &type=10-K&count=1&output=atom
  -> <assigned-sic>3571</assigned-sic>          17KB, against 164KB for submissions
```

That difference is what makes indexing a 1,300-name watchlist practical at all.
Expect ~1.5s per lookup in practice, so warm it in the background, cache it
forever, and be ready to work with a partly-filled index — a SIC code does not
change once you have it.

**What EDGAR does NOT have: analyst estimates.** The SEC holds what a company
*reported*, never what anyone *expects*. Forward EPS, this year's and next
year's consensus, and estimate revisions have no free authoritative source —
yfinance (`get_earnings_estimate`, `get_eps_revisions`, `info.forwardEps`) is
the free option and it is unofficial and rate-limited. There is also no free
archive of *past* consensus, so a historical forward-P/E series cannot be
reconstructed; it can only be accumulated going forward.

### Per-share values sit on the basis of the filing that last stated them

The rule "keep the newest filing's version of each period" handles a stock
split whenever the company restates that period afterwards. It does not when
the company never restates it again. Apple's fiscal-2018 twelve-month earnings
per share stands at 11.91 in the filing that reported it and 2.97 in the one
after the 2020 four-for-one split — and the nine-month figure for the same year
was left on the older basis, so differencing a fourth quarter out of the two
produced **−6.01 a share for a quarter Apple earned money in**.

Recover the split from the filings themselves: consecutive filings repeat each
other's periods as comparatives, so the ratio between the two versions of one
period IS the split factor. Rescale ONLY when that ratio lands on a clean split
ratio — Apple's 2010 retrospective revenue-recognition change moved earnings
per share by a factor of 1.2649 between two filings, and undoing that would
destroy a real restatement.

Some splits are unrecoverable this way: Apple's June 2014 seven-for-one sits
between two quarterly filings that share no period at all. Detect the
discontinuity on the diluted share count and stop comparing across it, rather
than pretending the series is continuous.

## 5. Reference / universe data

- **SEC** — full symbol↔company↔CIK map:
  `https://www.sec.gov/files/company_tickers.json`
  **Requires a declared User-Agent** (`AppName contact@email`) per SEC's
  fair-access policy or you get blocked.
- **S&P 500 constituents** (community-maintained CSV):
  `https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv`

## 6. Stock logos (free CDNs, fallback chain in this order)

1. `https://logo.synthfinance.com/ticker/AAPL`
2. `https://financialmodelingprep.com/image-stock/AAPL.png`
3. `https://assets.parqet.com/logos/symbol/AAPL`

Probe with a hidden `<img>` + 4 s timeout per source; render a text fallback
until one confirms load (mobile Safari fires `onError` unreliably).

## 7. Utility APIs

- **Weather**: Open-Meteo, no key —
  `https://api.open-meteo.com/v1/forecast?latitude=…&longitude=…&current=temperature_2m,weather_code`
- **Push notifications**:
  - ntfy.sh — free, no account: `POST https://ntfy.sh/<your-topic>`
  - Pushover — $5 one-time per platform: `POST https://api.pushover.net/1/messages.json`

## 8. Derived for free (computed, not fetched — worth reusing)

- **Black–Scholes greeks** from chain IV when the chain has no greeks
  (delta/theta/gamma/vega; JerryTrade labels these `est`).
- **HV Rank** — percentile of 20-day realized vol within its 1-year range:
  a free proxy for IV Rank across a whole universe (`ivrank.py`).
- **Expected move** — ATM straddle mid from any option chain.
- **Implied Fed path** — 100 − ZQ futures price per month.
- **Curve regime / shape** — bull/bear steepener/flattener from 2y/10y
  5-day changes; inverted/flat/normal/humped from the spread signs.
- **Auction strength** — bid-to-cover + indirect % vs prior-10 average.
- **P(OTM)** ≈ 1 − |delta| (label as estimate, never a guarantee).

## 9. [PAID] What JerryTrade gets from paid sources (what a free app gives up)

| Paid source | What it provides here | Closest free substitute |
|---|---|---|
| **Schwab API** (free w/ account, but account-gated) | Real-time quotes, real option greeks, intraday/price history, positions, order data | yfinance delayed quotes + BS greeks from chain IV |
| **Unusual Whales** | Options flow, sweeps, dark pool, GEX, market tide, IV rank | none free — omit honestly |
| **Finviz Elite** (`elite.finviz.com/news_export.ashx?auth=TOKEN`) | Real-time export API, embedded Elite views | free finviz.com public pages + Google News RSS |
| CME (via brokers) | FedWatch probabilities, futures OI, when-issued yields | ZQ-implied path; omit OI/WI honestly |

## 9b. What SEC XBRL supports for banks and property trusts (Phase 4)

Measured directly, ticker by ticker, before any of it was built. These are the
coverage counts that decided what the models compute and what they refuse.

**Banks (15 large and regional US lenders measured):**

| Concept | Coverage | Note |
|---|---|---|
| `InterestIncomeExpenseNet` | 14/15 | Net interest income — the core revenue line |
| `NoninterestIncome` / `NoninterestExpense` | 14/15 | Together give the efficiency ratio |
| `Goodwill` / `IntangibleAssetsNetExcludingGoodwill` | 14/15 | The tangible-book deductions |
| `PreferredStockValue` / `…LiquidationPreferenceValue` | 13/15 | **Bank of America tags neither** |
| `Deposits`, `InterestExpenseDeposits` | 14/15, 13/15 | Funding cost |
| Loans (four alternative concepts) | 14/15 | |
| Net charge-offs (three alternatives) | 13/15 | |
| Non-accrual loans | 9/15 | JPMorgan does not tag it |
| Capital ratios | 9/15 | And filers tag DIFFERENT ratios — common equity tier one, tier one, or total capital — so which one is named on screen |
| **Net interest MARGIN, or any earning-asset base** | **0/15** | Reported instead as net interest income over average TOTAL assets, under that name |

**Property trusts (20 large US trusts measured):**

| Concept | Coverage | Note |
|---|---|---|
| **`FundsFromOperations`** | **0/20** | Not one trust tags it. Reconstructed from components |
| `NetIncomeLossAvailableToCommonStockholdersBasic` | 20/20 | |
| Depreciation and amortisation | 19/20 | AvalonBay tags only `Depreciation` |
| Gains on property sales | sporadic | Realty Income every quarter, Prologis stopped 2019, Simon Property Group never |
| Impairments | 20/20 concept present, sporadic quarters | |
| **Occupancy** | **0/20** | Prose only |
| **Same-store net operating income** | **0/20** | Press-release tables only |
| **EBITDAre** | **0/20** | So net debt is shown over funds from operations, under that name |
| Dividends per share | 19/20 | But only 12/20 tag it for the CURRENT trailing year |

Two gotchas that cost real accuracy and are worth writing down:

- **Concept lists ordered by scope need strict priority.** `pick_concept`
  breaks a tie on the newest end date by COVERAGE, which is right when every
  alternative is a synonym. Citigroup tags both `NoninterestExpense` (48
  quarters, the total) and `OtherNoninterestExpense` (70 quarters, one line
  inside it), both ending the same day — and coverage alone read Citigroup's
  cost of running the bank as 7% of revenue rather than 65%. Metrics whose
  alternatives differ in SCOPE are listed in `fundamentals.STRICT_PRIORITY`.
- **A filer can stop tagging the cover page and keep filing.** Simon Property
  Group's newest `dei:EntityCommonStockSharesOutstanding` is dated September
  2009 against a June 2026 income statement — 6,117 days stale. Taking it at
  face value understated its market value by 13% and every ratio built on it.
  A cover-page count more than 200 days behind the latest reported period is
  now treated as abandoned; staleness is measured against the newest fact in
  the filer's own record, never against the clock, so the answer does not
  change with the date the app is run.

## 9c. What SEC XBRL supports for insurers and brokers (Phase 5)

Same exercise, run before either model was written.

**Insurers (36 US insurers measured across property-casualty, life, health,
reinsurance and multiline):**

| Concept | Coverage | Note |
|---|---|---|
| `PremiumsEarnedNet` | 36/36 | |
| `PolicyholderBenefitsAndClaimsIncurredNet` | 36/36 | |
| `LiabilityForClaimsAndClaimsAdjustmentExpense` | 34/36 | |
| `NetInvestmentIncome` | 33/36 | |
| `DeferredPolicyAcquisitionCostAmortizationExpense` | 31/36 | The acquisition-cost half of the expense ratio |
| **`OtherUnderwritingExpense`** | **5/36** | The other half. This is why the combined ratio is usually blank |
| Prior-year reserve development (Schedule-P style) | 16/22 property-casualty | `SupplementalInformationForPropertyCasualtyInsuranceUnderwritersPriorYearClaimsAndClaimsAdjustmentExpense` |
| `PremiumsWrittenNet` | 14/36 | |
| **Risk-based capital** | **0/36** | Filed with state regulators, not with the SEC in XBRL. Equity to total assets is reported instead, under that name |

The expense side is the whole story. There is a tempting substitute —
`BenefitsLossesAndExpenses` minus claims — and it was measured: believable for
pure property-casualty insurers (Travelers 88.6, Chubb 85.3) and nonsense
everywhere else (Cigna 716, Equitable 1,134), because the total sweeps in
interest credited, annuity costs and the cost of dispensing prescriptions. It
is not used.

**Brokers (24 filers in the broker industry codes, of which 10 are actual
broker-dealers):**

| Concept | Coverage | Note |
|---|---|---|
| `LaborAndRelatedExpense` | 20/24 | Compensation is the largest cost at every broker |
| `CashAndSecuritiesSegregatedUnderFederalAndOtherRegulations` | 9/10 brokers | Concept present; CURRENT for fewer |
| `ReceivablesFromCustomers` | 8/10 brokers | Margin lending |
| `InterestIncomeExpenseNet` | 6/24 | |
| `BrokerageCommissionsRevenue` | 5/24 | |
| **`PayablesToCustomers`** | **8/10 present, 0/10 current** | Newest reading anywhere is 2020 |
| **`AssetsUnderManagementCarryingAmount`** | **1/24** | LPL Financial, dated 2012 |

**Client assets do not exist in XBRL.** Not assets under management, not
assets under administration, not net new assets. Every figure that circulates
comes from press releases and monthly activity reports. They stay blank.

**The industry code cannot answer "is this a broker".** Code 6211 holds
Charles Schwab, Goldman Sachs AND BlackRock; 6200 holds LPL Financial and the
CME; 6282 holds Evercore and T. Rowe Price. The question is answered from the
balance sheet instead — and each piece of evidence has to be CURRENT
(Evercore's investment banking revenue series stopped in 2018,
Intercontinental Exchange's segregated cash in 2015) and MATERIAL (Ameriprise
parks $0.9bn of segregated cash against $198bn of assets).

Three more gotchas, each of which was a live bug:

- **Instant concepts need strict priority too.** `StockholdersEquity` is the
  parent company's equity and
  `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`
  adds equity belonging to somebody else. Both are usually filed on the same
  date, so the coverage tie-break picked the wrong one for 22 of 53 filers
  measured — Interactive Brokers' book value 73% too high, American Tower's
  65%, Simon Property Group's 21%. See `fundamentals.STRICT_INSTANT_PRIORITY`.
- **"No preferred stock tagged" and "no preferred stock" are different.** Bank
  of America tags no preferred balance and pays $1.5bn a year of preferred
  dividends; Chubb, Aflac and Hanover tag no balance, no dividend and no
  preferred share count. The first must be refused and the second is zero.
- **A concept can be present and years stale.** LPL Financial's
  `NetIncomeLossAvailableToCommonStockholdersBasic` stops in 2012, which put
  its return on equity at 2.8% against a real 18.6%. Freshness decides which
  of two overlapping concepts is used, not mere presence.

## 9d. Reading the annual report itself, and its tables (Phase 6)

Company Facts is XBRL, and XBRL is the financial statements. What kind of
business a company is, and how much of other people's money it holds, are
not in there. Both live in the filing documents, and both are readable
through the same SEC transport — by accession, out of the EDGAR archive —
with no scraping of anybody's website and no PDFs.

### Finding Item 1 in a real 10-K

Measured over 69 annual reports:

- **The newest annual filing is not always an annual report.** A 10-K/A
  amendment carries only the items it amends. Three filers in the universe
  file one after every 10-K, and taking the newest form gets Part III and an
  exhibit index. Try the filings newest-first and accept the first one that
  actually yields a chapter.
- **Headings are styled letter by letter.** `Item 1. Busines s Description`
  (Berkshire), `I TEM 1.` (Cincinnati Financial), `I tem 1.` (MarketAxess).
  Any tag-stripping flattener that refuses to join a close-then-open tag pair
  — as it should, because joining `authorized</span><span>the` is worse —
  leaves these split. Heading patterns must tolerate whitespace between any
  two letters. It is safe there and nowhere else.
- **The chapter heading may not contain the item number at all.** Morgan
  Stanley's is the single word "Business", with `Item 1` only in the
  contents list. The document's own `<a href="#…">` anchor is the best
  evidence available and resolves it.
- **A 4MB read limit truncates half of them.** Thirty of fifty-six documents
  exceeded it. Filings are immutable, so fetch once and read all of it.
- **The longest candidate is the wrong one.** A cross-reference late in the
  report ("see Part I, Item 1 — Business — Regulation") runs to the next
  mention of Risk Factors and is longer than the chapter it points at. A
  document is written in order: the chapter is the FIRST candidate long
  enough to be one.

### Reading a number out of a filing table

- **The scale is stated three places and they disagree.** The row's own label
  wins over the table heading, which wins over the sentence before the table.
  Interactive Brokers prints "Customer Equity (in billions)" in a table
  captioned in thousands.
- **"(in millions, except per-share data)" does not say what the exception
  covers.** T. Rowe Price prints assets under management as "$1,893.4" in
  such a table, meaning billions. Filers write exact millions as whole
  numbers and rounded billions with one decimal; a decimal balance under a
  hedged caption is not safe to convert and should be refused.
- **Percentage columns come first.** Schwab prints two per-cent-change
  columns before the dollars. A money metric must skip any cell containing
  `%`.
- **Column indexes cannot be trusted.** Colspans and empty spacer cells mean
  the fourth heading is not above the fourth number. What can be trusted is
  that the current period is printed first, with comparatives after it —
  true of every release measured — so take the first figure and read the
  period from the heading as a whole.
- **A bare "June 30," needs the year from the row below it.** That two-row
  heading is the commonest in the corpus.
- **A reporting period ends on the last day of a month.** A heading naming
  15 July is the date the release was issued.
- **A heading can name two windows at once.** "Three months ended June 30, |
  Six months ended June 30," — comparing across that gap turns a quarter
  into a 300% error against a year. Ambiguous means do not compare.
- **Segment rows share the company-wide label.** Travelers prints a combined
  ratio for every segment. Several rows, one label, different numbers, same
  period: refuse.

### Which concepts died in 2018

A whole family of income-statement detail stopped being tagged when the
revenue standard changed, and every one of these has a series that simply
ends:

| Concept | Last tagged, typically |
|---|---|
| `InvestmentAdvisoryFees`, `AssetManagementFees` | 2013–2018 |
| `MarketDataRevenue`, `ClearingFeesRevenue` | 2018 |
| `InvestmentBankingRevenue` (some filers) | 2018 |
| `PayablesToCustomers` | 2020 |

They are still worth reading, because they are decisive when present, but
any use of them needs a freshness rule or it will describe a company as it
was seven years ago. Conversely, `PolicyholderFunds` and
`PolicyholderContractDeposits` are current and widely tagged, and they see an
annuity business inside a company whose industry code says asset manager.


## 10. House rules that made all of this reliable

1. **Cache by cadence**: daily data 15–30 min, monthly (CPI/COT) 6–12 h,
   static reference 24 h+. Cache failures briefly (~2 min) so a dead source
   isn't hammered.
2. **Every payload carries** `source`, `as_of`/update time, and an `ok` flag —
   the UI shows "Data unavailable" instead of estimating. Never manufacture
   consensus estimates, when-issued yields, or probabilities you can't source.
3. **UA discipline**: SEC requires a declared UA; FRED may stall fancy UAs
   (curl UA works); Finviz needs a browser UA; everything needs timeouts.
4. **Time zones**: use the ET calendar date for "is the week/day complete"
   decisions — `date.today()` on a UTC server rolls over at 8 PM ET and
   silently corrupts weekly logic (real bug we shipped and fixed).
