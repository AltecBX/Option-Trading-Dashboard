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
