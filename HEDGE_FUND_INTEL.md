# Hedge Fund Intelligence — audit and design

*Audit performed September 6, 2026. Every source below was tested from the
build container or read from the live deployment that day; nothing here is
assumed to exist because a website says it does. Where a source failed, the
failure is recorded rather than smoothed over.*

The objective, in Jerry's words: *the closest reliable picture possible of
what the world's important hedge funds are doing now, what has changed
recently, and where institutional money appears to be moving before quarterly
filings reveal the full picture.*

The honest framing that shapes everything that follows: **there is no data
feed of what hedge funds did this week.** The weekly numbers that Goldman
Sachs, Morgan Stanley and JPMorgan prime brokerage publish are client research.
They reach the public only as quotes in news stories, days later, in
fragments. What *does* exist, and can be verified every week, is a set of
regulatory and market-structure sources that each see one slice of hedge fund
behaviour. The feature is designed around those slices, labels each one by
what it can and cannot prove, and treats the prime-broker quotes as what they
are: attributed, secondhand, valuable, and unverifiable.

---

## 1. What the dashboard already has

| Already in the app | Where | What it gives this feature |
|---|---|---|
| **CFTC Traders in Financial Futures**, Leveraged Funds category, with 3-year percentile history and a throttle fallback | `treasury.py` §14 | The only *weekly, official, hedge-fund-specific* positioning data that exists. Currently pulled for Treasury futures only; the same API serves S&P 500, Nasdaq-100, Russell 2000, VIX, DJIA and seven S&P sector futures (verified, §2). |
| **SEC EDGAR** submissions API, filing reader, Form 4 and SC 13D/G parsing, per-company | `sec_filings.py`, `filing_reader.py`, `filing_tables.py` | Throttling, User-Agent handling and the acceptance-time-to-ET logic are already solved. Company-centric today; this feature turns it around to be *filer*-centric. |
| **Unusual Whales** — configured and connected on Railway (87 calls used of 40,000/day on the audit date) | `unusual_whales_client.py`, `/api/uw/*` | Already ingests every 13F including amendments, with **both** the report date and the filing date on each row, put/call flagged, `is_hedge_fund` and activist tags, four quarters of unit history per position. Also sector options tide, ETF creation/redemption flows (`in_out_flow` in `/api/uw/sector_flow`), and intraday borrow fee and shares available per ticker. |
| **Finviz Elite** news feed — configured live | `finviz_news.py` | The freshest press-wire capture in the app; where Reuters "HEDGE FLOW" headlines will land first. |
| **Google News RSS** | `FREE_DATA_SOURCES.md` §3 | Verified to return the Reuters HEDGE FLOW series with publication dates. |
| **X API v2** recent search (`X_BEARER_TOKEN`) | `ewhispers.py` | Optional channel for manager statements. Whether the token is set on Railway was not verifiable from outside. |
| Sector metadata per ticker (SIC → sector, peers) | `fundamentals.py`, `peers.py` | Lets 13F positions and short-interest rows roll up to sectors. |
| Snapshot-store patterns, lazy workers, forward-test grader | `invest_scan.py`, `sp_forward.py`, `juice.py` | The weekly-report history store follows the same shape. |
| **Market calendar** (v4.84) | `market_calendar.py` | Weekly jobs land on sessions, not holidays. |

---

## 2. What was verified, and how often it moves

Every row was fetched on September 6, 2026. "Lag" is how stale the data is
on the day it becomes public.

### 2a. Regulatory positioning — hedge-fund-specific, aggregate

| Source | Cadence | Lag | What it proves | Verified |
|---|---|---|---|---|
| **CFTC TFF, Leveraged Funds** — `publicreporting.cftc.gov/resource/gpe5-46if.json` | Weekly. Positions as of Tuesday, published Friday 3:30 PM ET | 3 days | Hedge funds' long, short and spread contracts in S&P 500 (consolidated, e-mini, micro), Nasdaq-100, Russell 2000, VIX, DJIA, S&P 400, and sector futures for Staples, Energy, Financials, Health Care, Industrials, Utilities, Communication. Trader counts too. | ✅ Latest report September 1, 2026. S&P 500 consolidated: leveraged funds long 150,561 / short 484,997 contracts. |
| **OFR Hedge Fund Monitor** — `data.financialresearch.gov/hf/v1`, no key | Form PF aggregates quarterly; TFF summary and FICC sponsored repo monthly; Fed SCOOS quarterly | Form PF ~5 months (latest point March 31, 2026) | Industry **leverage** from Form PF: average leverage ratio by fund size, prime-broker borrowing, repo borrowing, top-10 share of borrowing. 329 series. This is the only official answer to "are hedge funds increasing leverage?" | ✅ `FPF-ALLQHF_GAVN10_LEVERAGERATIO_AVERAGE`, `FPF-BORROW_PRIMEBROKER_SUM`, etc. |

**A caution the card must carry:** leveraged funds are *structurally* net short
S&P 500 futures — they hedge long stock books and run basis trades. On
September 1 they were net short 334,436 contracts. That level is not "hedge
funds are bearish." **The change and the percentile are the signal; the level
is not.** The existing `treasury.py` percentile logic is the right tool.

### 2b. Named funds — verified, attributable

| Source | Cadence | Lag | What it proves | Verified |
|---|---|---|---|---|
| **13F-HR** information table XML, per filing, from EDGAR Archives | Quarterly, due 45 days after quarter end | 45 days on the day it lands; up to 135 days by the next one | Every long equity and listed-option position (put/call flagged) over $200k, as of quarter end. | ✅ Citadel Advisors' Q2 table: 16,127 positions, 8.1 MB, 7,627 rows carry a put/call flag. |
| **13F-HR/A** amendments | Any time | — | `amendmentType` is RESTATEMENT (replaces the table) or NEW HOLDINGS (adds to it). | ✅ Citadel filed a RESTATEMENT on September 2, 2026 (16,122 rows). |
| **13F-NT** notices | With the 13F | — | "My positions are reported by someone else." The trail to a successor filer. | ✅ Pershing Square Capital Management filed 13F-NT for Q2; the holdings are now under **Pershing Square Inc.** (CIK 2026053). |
| **SCHEDULE 13D / 13D/A** — structured XML since December 2024 | Within 5 business days of crossing 5% with intent, amendments on material change | Days | Activist stakes as data: `percentOfClass`, `aggregateAmountOwned`, `reportingPersonName`, `transactionPurpose`, event `date`. | ✅ Parsed a live 13D/A; the EDGAR full-text index returned 52 13D filings in one week. |
| **SCHEDULE 13G / 13G/A** | Quarterly (45 days after quarter end) for passive >5% holders, faster for some triggers | 45 days | Passive large stakes and their changes. | ✅ 178 filed on September 4, 2026 alone. |
| **EDGAR daily form index** — `Archives/edgar/daily-index/…/form.YYYYMMDD.idx` | Daily | Same day | Every filing of every form type in one 776 KB file. The cheap way to catch every 13D, 13G and 13F amendment across all filers. | ✅ September 4: 8 × 13F-HR, 3 × 13F-HR/A, 4 × 13F-NT, 30 × SCHEDULE 13D, 178 × SCHEDULE 13G. |
| **EDGAR full-text search** — `efts.sec.gov/LATEST/search-index` | Live | Minutes | Search by form type, date range and text; returns filer CIKs and subject CIKs together. | ✅ |
| **Unusual Whales institutions** | Follows EDGAR within days | Same as 13F | Already-parsed 13F holdings with `filing_date` and `report_date`, put/call, unit history, `buy_value`/`sell_value`, `is_hedge_fund`, tags. Cross-check against EDGAR, not a replacement for it. | ✅ Citadel: report date June 30, filing date September 2 (the restatement). |
| **Form N-PX** | Annual, by August 31 | Up to 14 months | Proxy votes cast — proof a fund held a name on a record date. Low value; kept for completeness. | ✅ Every named fund filed one in late August 2026. |
| **Public statements** — Substack RSS, fund letters, X | Irregular | Hours | What a manager *says*. Not a position. | ✅ Michael Burry's *Cassandra Unchained* feed: posts on September 3 and 4, 2026. |

**Findings about the named funds that change the design:**

- **Scion Asset Management no longer files anything.** Last 13F-HR: filed
  November 3, 2025, for September 30, 2025. Nothing since. Burry closed the
  fund in November 2025 and now publishes on Substack. For him, public
  statements are the *only* source, and they are claims, not positions.
- **Pershing Square moved filers** in 2026: the old entity (CIK 1336528)
  filed a 13F-NT for Q2; the book is reported by Pershing Square Inc.
  (CIK 2026053; Q2 filed August 14, 2026). A watchlist keyed by one CIK
  would have shown Ackman as "stopped filing." **Funds change EDGAR
  identities**, and four of the twenty-six candidate managers checked had
  done so (Greenlight → DME Capital Management, JANA → JANA Partners
  Management, BlackRock → BlackRock, Inc., ExodusPoint's remembered CIK was
  a different company altogether). The registry needs successor links, and
  a "stopped filing" flag needs a "look for a successor" step first.
- **Multi-strategy and quant books are not readable from a 13F.** Citadel's
  top five reported positions by value are SPY and QQQ calls and puts and
  a Micron put. 16,000 lines of hedged, market-neutral, high-turnover
  positions cannot be summarised as "Citadel is long technology." The same
  holds for Millennium, Two Sigma, Renaissance, D. E. Shaw, Point72 and
  Balyasny. For these, the 13F shows what was *hedged* on one day 45+ days
  ago. The feature will say so on every one of their cards rather than
  invent a view. For concentrated managers — Pershing Square (14 positions),
  Appaloosa, Baupost, Duquesne, Elliott, Third Point — a 13F is a real
  picture and quarter-on-quarter changes mean something.

### 2c. Institutional flow proxies — market-wide, not fund-specific

| Source | Cadence | Lag | What it proves | Verified |
|---|---|---|---|---|
| **FINRA consolidated short interest** — `api.finra.org`, no key | Twice monthly: settlement on the 15th (or prior business day) and the last business day; published about 8 business days later | 8–10 days | Shares short per security, prior period, days to cover, % change. Roll-up by sector answers "are shorts being added or covered?" | ✅ Settlement dates June 15, June 30, July 15, July 31, August 14 all served; August 31 not yet published on September 6. |
| **FINRA Reg SHO daily short volume** — `cdn.finra.org/equity/regsho/daily/` | Daily, evening | Same day | Short volume as a share of total volume per symbol. Not positions — pressure. | ✅ September 4 file: 538 KB. |
| **UW sector options tide** | Intraday | Minutes | Net call/put premium by GICS sector. Institutional-sized flow, anonymous. | ✅ |
| **UW ETF in/out flow** (`/api/uw/sector_flow`) | Daily | 1 day | Creation/redemption units for SPY and the sector SPDRs — the money moving into or out of each sector's ETF wrapper. | ✅ Live on the deployment. |
| **UW short borrow data** | Intraday | Minutes | Borrow fee and shares available per ticker — the price of shorting, which rises when shorts crowd. | ✅ AAPL fee 0.34%, 10M shares available, September 4. |

### 2d. Prime-broker aggregate data — secondhand only

| Source | Cadence | Lag | What it proves | Verified |
|---|---|---|---|---|
| **Reuters "HEDGE FLOW"** series quoting Goldman Sachs prime brokerage; Bloomberg and Investing.com coverage of the same notes; occasional Morgan Stanley and JPMorgan positioning quotes | Roughly weekly, usually Monday–Tuesday about the prior week | 3–7 days | A quoted claim such as *"hedge funds net-sold information technology for a fourth consecutive week."* Attributable to the outlet, the bank and the date. **Not verifiable, not complete, not numeric beyond what the article prints.** | ✅ Google News RSS returns the series with dates; the July 6, 2026 Reuters piece on four straight weeks of tech selling is exactly the pattern in the brief. |

There is no API, file or subscription available to this app for the
underlying prime-broker data. Every number that reaches the feature from
this channel will be shown as a quotation with its source and date.

### 2e. Checked and not available

| Source | Status on September 6, 2026 |
|---|---|
| **FINRA SLATE** securities lending (SEC Rule 10c-1a) | Public dissemination **postponed to September 28, 2028**. Nothing to read. Securities-lending signal comes from UW borrow fee and availability instead. |
| **SEC Form SHO** aggregated gross short positions (Rule 13f-2) | Managers have been filing since February 17, 2026. The SEC's aggregated monthly data set could not be located in the SEC data library (both pages checked). Treated as "watch for it," not as a source. |
| **ICI weekly fund flows** | 403 from the container — bot-blocked. |
| **SSGA sector SPDR daily holdings file** | Reachable (22 KB xlsx) but the app has no xlsx reader (`openpyxl` is not installed). Optional fallback for ETF flows; UW already covers it. |
| **SEC Form 13F bulk data sets** | Available, but the newest set covers filings through May 2026; the August filings are not in it. Per-filing XML is the fresher path and is what the design uses. |
| **YouTube source video** | Identified (*"The 24-Year-Old 'Genius' Who Lost $35 Billion"*, FINAiUS) but the page is bot-blocked; the 19:00–19:35 segment could not be viewed. The feature is designed from the brief and the verified sources, not from the video. |

---

## 3. The cadence answer

| Cadence | What genuinely updates |
|---|---|
| **Daily** | FINRA short volume; EDGAR daily index (new 13D, 13G, 13F amendments, 13F-NT); UW sector tide, ETF flows, borrow data; press capture (Reuters HEDGE FLOW, Finviz, manager RSS). |
| **Weekly** | CFTC Leveraged Funds (Friday for Tuesday). The prime-broker headlines arrive on roughly this rhythm. This is the natural cadence of the **Pulse** and the **Weekly Report**. |
| **Twice a month** | FINRA short interest (settlements on the 15th and month-end, public ~8 business days later). |
| **Monthly** | OFR TFF summary, FICC sponsored repo. Form SHO aggregates, if and when the SEC publishes them. |
| **Quarterly** | 13F holdings (45-day lag), 13G amendments, OFR Form PF leverage (about a five-month lag), Fed SCOOS. |
| **Irregular, fast** | SCHEDULE 13D (days), manager statements (hours). |

So: **weekly is the honest cadence** for "what are hedge funds doing", built
from CFTC plus the daily proxies rolled up; **quarterly** is the honest
cadence for "what does Citadel own", with 13D and statements as the only
faster named-fund channels.

---

## 4. Evidence classification

Every fact stored by the feature carries one of Jerry's five classes, plus
two dates — `as_of` (what date the fact describes) and `public_on` (when it
became public) — and a `source` string naming the file, endpoint or article.

| Class | What qualifies | Examples |
|---|---|---|
| **VERIFIED FUND ACTIVITY** | A regulatory filing by the named manager, read from EDGAR. Sub-type `FILING`. A public statement *by* the manager is stored under this class with sub-type `STATEMENT` and is rendered as a claim ("Burry wrote on September 4…"), never as a position. | Pershing Square Inc. 13F-HR for June 30, 2026, public August 14, 2026. Elliott SCHEDULE 13D on a subject company. |
| **PRIME BROKER AGGREGATE DATA** | A quoted figure attributed to a named bank's prime brokerage, with the outlet and date. Always secondhand. | "Hedge funds net-sold tech for a fourth week — Goldman Sachs via Reuters, July 6, 2026." |
| **REGULATORY POSITIONING DATA** | CFTC TFF Leveraged Funds; OFR Form PF aggregates; FINRA short interest; Form SHO aggregates if published. | Leveraged funds' net S&P 500 position and its change; industry leverage ratio. |
| **INSTITUTIONAL FLOW PROXY** | Anonymous, market-structure signals: options premium by sector, ETF creations, short volume, borrow fees. | "Technology sector net put premium ran negative four of five days." |
| **MODEL INFERENCE** | Anything the feature computes: streaks, percentiles, crowding scores, persistence, the combined verdicts, confidence. | "Technology positioning: 4th consecutive week of reduction (CFTC + short interest + ETF outflow agree)." |

Two rules are enforced in code, not by convention:

1. **No fact of class PRIME BROKER, REGULATORY, or FLOW PROXY may be stored
   with a `fund` field.** Anonymous activity cannot be attributed. A test
   asserts it.
2. **No inference may be rendered without its inputs.** Every MODEL INFERENCE
   row lists the evidence rows it was computed from, so the card can show
   "why" on demand and the weekly report can list conflicts.

---

## 5. Design

Three Python modules, one lazy chunk, one report store. Same shape as the
Investment and Sold Into Strength features: pure math separated from
providers, floors in `thresholds.json`, every row carrying its provenance.

### 5a. `hf_registry.py` — who is watched, and under which identities

A configurable watchlist of managers, each with:

- `name`, `people`, `style` (activist / concentrated / macro / multi-strategy
  / quant / long-short / family office)
- `ciks`: an ordered list of EDGAR identities with `from`/`to` dates, so a
  13F-NT triggers a successor lookup instead of a "stopped filing" verdict
- `turnover_class`: **READABLE** (concentrated; quarter-on-quarter changes are
  meaningful) or **OPAQUE** (multi-strat/quant; 13F shows hedging, not views)
- `statement_feeds`: RSS/Substack/X handles, each attributable
- `status`: FILING / SUCCESSOR / CEASED (Scion)

Seed list, every CIK verified on September 6, 2026 against EDGAR with a Q2
2026 13F-HR on file unless noted:

| Manager | CIK | Style | Turnover | Note |
|---|---|---|---|---|
| Citadel Advisors | 1423053 | multi-strategy | OPAQUE | Restated Q2 on September 2, 2026 |
| Millennium Management | 1273087 | multi-strategy | OPAQUE | |
| Bridgewater Associates | 1350694 | macro | OPAQUE for stocks; CFTC/ETF-heavy | Never files 13D/G |
| Two Sigma Investments | 1179392 | quant | OPAQUE | |
| Renaissance Technologies | 1037389 | quant | OPAQUE | Filed August 13 |
| Pershing Square Inc. | 2026053 (succeeds 1336528) | activist / concentrated | READABLE | 14 positions |
| Scion Asset Management | 1649339 | — | CEASED | Burry's Substack is the only channel |
| Elliott Investment Management | 1791786 | activist | READABLE | |
| Third Point | 1040273 | activist / event | READABLE | |
| Starboard Value | 1517137 | activist | READABLE | |
| Icahn (Carl C. Icahn) | 921669 | activist | READABLE | |
| Trian Fund Management | 1345471 | activist | READABLE | Filed August 12 |
| ValueAct Holdings | 1418814 | activist | READABLE | |
| JANA Partners Management | 1998597 (succeeds 1159159) | activist | READABLE | |
| Berkshire Hathaway | 1067983 | concentrated | READABLE | Not a hedge fund; most-watched 13F |
| Baupost Group | 1061768 | value | READABLE | Filed August 13 |
| Appaloosa (Tepper) | 1656456 | concentrated | READABLE | |
| Duquesne Family Office (Druckenmiller) | 1536411 | macro / concentrated | READABLE | |
| Soros Fund Management | 1029160 | macro | READABLE | |
| Tiger Global | 1167483 | growth | READABLE | |
| Coatue | 1135730 | growth / tech | READABLE | |
| Lone Pine | 1061165 | long-short | READABLE | |
| Viking Global | 1103804 | long-short | READABLE | |
| DME Capital Management (Greenlight, Einhorn) | 1489933 (succeeds 1079114) | value | READABLE | |
| Paulson & Co. | 1035674 | event / macro | READABLE | |
| D. E. Shaw | 1009207 | quant | OPAQUE | |
| AQR Capital Management | 1167557 | quant | OPAQUE | |
| Man Group | 1637460 | quant / multi | OPAQUE | |
| Point72 | 1603466 | multi-strategy | OPAQUE | |
| Balyasny | 1218710 | multi-strategy | OPAQUE | |
| ExodusPoint | 1736225 | multi-strategy | OPAQUE | |
| ARK Investment Management | 1697748 | thematic | READABLE | Not a hedge fund; daily trade disclosures exist |

The list lives in `hf_watchlist.json` and is editable from the card.

### 5b. `hf_sources.py` — the providers

One function per source, each returning evidence rows with class, dates and
source string, each cached to the cadence in §3 so a refresh never costs more
than the data can change:

- `cftc_equity()` — extends the existing `treasury.py` TFF code to the equity
  index and sector futures list; weekly; percentiles over three years.
- `ofr_leverage()` — Form PF leverage and borrowing series; quarterly.
- `finra_short_interest(settlement)` — bulk pull per settlement date, rolled up
  by sector using the app's SIC map; twice monthly.
- `finra_short_volume(day)` — daily file; sector roll-up of short-volume share.
- `uw_sector_tide()`, `uw_etf_flows()`, `uw_borrow(symbol)` — through the
  existing client; daily.
- `edgar_daily_index(day)` — every 13D/13G/13F-family filing that day; matched
  to the registry by CIK, and to the sector map by subject-company CIK.
- `edgar_13f(cik, accession)` — the information table XML, parsed to
  positions with put/call; diffed against the prior quarter to produce NEW /
  INCREASED / REDUCED / EXITED, sector exposure, concentration (top-10 weight,
  Herfindahl), and options overlay share.
- `edgar_13d(accession)` — the structured XML fields above.
- `uw_institution(cik)` — cross-check of the EDGAR diff; disagreements are
  stored as conflicts, not silently resolved.
- `press_capture()` — Reuters HEDGE FLOW and equivalent via Google News RSS
  and the Finviz feed; a small rule set extracts *bank*, *direction*,
  *sector*, *streak length* from headlines and first paragraphs; anything
  that does not parse cleanly is stored as a raw quote. Class PRIME BROKER.
- `statements(registry_entry)` — RSS/Substack/X items for a named manager,
  stored verbatim with link and timestamp. Class VERIFIED, sub-type STATEMENT.

Budget per week, worst case: CFTC ~25 calls; FINRA 2 short-interest pulls +
5 daily files; EDGAR 5 daily indexes + one submissions read per watched
manager per day (~35) + tables only when a new filing appears; UW ~120 calls
(sector tide 11, ETF flows 11, per day) — under 1% of the token limit.

### 5c. `hf_pulse.py` — pure, tested, no I/O

Given evidence rows, answers each of the brief's weekly questions with a
verdict, a streak, persistence at 2/4/8/12 weeks, an unusualness percentile,
the list of inputs, the list of conflicts, and a confidence:

| Question | Inputs (class) | Method |
|---|---|---|
| Increasing or reducing exposure? | CFTC net index futures change (REG); ETF net flows (PROXY); prime-broker quotes (PB) | Sign of the weekly change in each; agreement count → confidence |
| Increasing or reducing leverage? | OFR Form PF (REG, quarterly, stated as such); CFTC gross (long + short) change as the weekly proxy (REG); PB quotes | Level percentile + direction |
| Buying or selling stocks? | ETF flows + sector tide (PROXY); PB quotes | As above |
| Reducing longs? | CFTC long-side change (REG); PB quotes | Direction of the long leg specifically |
| Adding shorts? / Covering? | FINRA short interest change (REG); short-volume share (PROXY); borrow fee trend (PROXY); CFTC short leg | Short interest is the anchor; the daily proxies fill the two-week gap and are labelled as proxies |
| Sectors bought / sold | CFTC sector futures (7 sectors), sector ETF flows, sector tide, sector short interest | Per-sector score with each input listed |
| Crowded / de-crowding | Short interest % float and days-to-cover percentile, borrow fee percentile, CFTC one-sidedness percentile, options skew | Crowded = ≥90th percentile on two or more inputs; de-crowding = crowded last week and moving toward the median |
| What changed from last week | Diff of every verdict against the stored prior report | |
| Persistence 2/4/8/12 weeks | Sign consistency of the primary input over the window | Reported as "n of the last m weeks" |
| How unusual vs history | Percentile of the primary input over its full stored history (CFTC three years; short interest since first capture) | |

Confidence is not a probability. It is **how many independent classes
agree**, with the rule that PRIME BROKER quotes can raise confidence but
never create a verdict alone, and MODEL INFERENCE never counts as evidence
for another inference.

### 5d. `hf_watch.py` — the named funds

For each registry entry, the card shows exactly what is known and when:

```
Citadel Advisors                                          multi-strategy · OPAQUE
Current activity:        UNKNOWN
Last verified positions: June 30, 2026   (public August 14, 2026;
                         restated September 2, 2026)
What that filing shows:  16,122 positions · 42% of reported value is listed
                         options · top holdings are SPY/QQQ calls and puts
Read this as:            a hedged, high-turnover book on one day 68 days
                         ago — not a view on any sector
Since then (verified):   nothing filed
Since then (statements): none captured
Broader hedge fund trend (not Citadel): Technology — 4th consecutive week
                         of reduction (CFTC, short interest, ETF flows agree;
                         Goldman via Reuters concurs)
```

For a READABLE manager the middle block becomes the quarter-on-quarter diff
(new / increased / reduced / exited, sector exposure with the change,
concentration), any 13D events since, and statements. Every row shows
`as_of` and `public_on`.

The **combined** rule is a single function with a single test: the sentence
about the broader universe is always rendered under its own heading, in its
own evidence class, and never inside the fund's activity block. The words
"current activity: UNKNOWN" are rendered whenever no VERIFIED row exists
after the last filing's `as_of` date — even if the broader trend is strong.

### 5e. `hf_report.py` — the weekly report, stored forever

Assembled every Friday after the CFTC release (the market calendar picks the
session), or on demand. Contents follow the brief's list exactly: biggest
positioning changes, sectors bought and sold, leverage, long selling, short
selling, short covering, crowded trades, de-crowding, named-fund activity
found this week, major new filings (every 13D by a watched manager; every 13F
amendment), watchlist changes, 2/4/8/12-week trends, conflicts between
sources, and per-conclusion confidence with inputs.

Stored as `hf_reports/2026-W37.json` in the stable data directory. Never
overwritten; a re-run the same week writes a new revision alongside. The card
offers the history as a list and a "compare with week N" view so positioning
can be watched evolving. The prior week's report is an input to the next
one — that is where "what changed" comes from.

### 5f. Card — `tab-hedge.jsx`, lazy chunk

Three panels, one tooltip on every figure and header:

1. **Pulse** — the weekly answers as a grid: verdict, streak, persistence
   (2/4/8/12), unusualness, confidence, inputs on hover. A sector strip
   below with bought/sold/crowded/de-crowding markers.
2. **Named Fund Watch** — one card per manager as in §5d; sortable by last
   filing date, by change, by style; a watchlist editor.
3. **Weekly Report** — the current report, a history list, and the compare
   view. Conflicts section is never collapsed by default.

Dates always Month Day, Year. Evidence class shown as a coloured tag on
every row. "UNKNOWN" is a real state with its own style, not an empty cell.

### 5g. Routes and config

`/api/hf/pulse` · `/api/hf/watch` · `/api/hf/fund?cik=` ·
`/api/hf/report` · `/api/hf/report/history` · `/api/hf/report/build` ·
`/api/hf/config` · `/api/hf/watchlist` (GET/PUT). Floors and percentile
thresholds under `hedge` in `thresholds.json`.

---

## 6. Limitations, stated plainly

- **The weekly prime-broker picture is secondhand.** The feature will show
  the Goldman/Morgan Stanley quotes with their dates and outlets, and will
  never show a number from them that the article did not print.
- **Between filings, named-fund activity is unknown**, and the card says so.
  The only faster named-fund channels are 13D (activists, days) and
  statements (claims).
- **Multi-strategy and quant 13Fs are not views.** The card refuses to
  summarise them as such.
- **Sector roll-ups depend on the sector map.** Positions or short-interest
  rows with no sector are reported as "unmapped," not dropped silently.
- **CFTC sector futures cover seven sectors**, not eleven; Technology,
  Materials, Real Estate and Consumer Discretionary have no leveraged-fund
  futures data and rely on ETF flows, tide and short interest only. The
  sector strip shows which inputs exist for each sector.
- **Form SHO and SLATE** are designed for as future inputs and switched off
  until they exist.
- **Nothing here is calibrated against outcomes yet.** The forward-grader
  pattern from Best Sales can later score whether "crowded" preceded
  reversals.

---

## 7. Phases

| Phase | Delivers | Depends on |
|---|---|---|
| **1 — Named Fund Watch** — *shipped in v4.85* (`hf_registry.py`, `hf_sources.py`, `hf_watch.py`, `tab-hedge.jsx`) | Registry with successors and turnover class; EDGAR 13F ingestion and quarter diff; 13D/13G/13F-NT capture from the daily index; UW cross-check; statements capture; the fund card with both dates and "UNKNOWN"; tests against captured filings | Nothing new — every source verified |
| **2 — Pulse** | CFTC equity + sector futures with percentiles; FINRA short interest and short volume with sector roll-up; UW sector tide, ETF flows, borrow; the pure `hf_pulse.py` with streaks, persistence, crowding; the Pulse panel | Phase 1 sector map |
| **3 — Combined + Weekly Report** | The combined rule on every fund card; press capture (PRIME BROKER quotes); the report builder, store, history and compare; conflicts and confidence | Phases 1–2 |
| **4 — optional** | X statements if the token is set; Form SHO when published; SSGA fallback (adds `openpyxl`); outcome grading | — |

Each phase ships as its own PR with tests and a browser render check, the
same way the last three features did.

---

## 8. Decisions for Jerry before Phase 1

1. **Watchlist**: keep the 32 managers in §5a as the seed, or trim? Adding
   one later is a JSON edit from the card.
2. **Statements**: store manager statements under VERIFIED FUND ACTIVITY with
   the STATEMENT sub-type (recommended, because they are attributable), or
   keep them out of the five classes entirely?
3. **Report day**: Friday after the 3:30 PM CFTC release (recommended), or
   Monday morning so the weekend's press coverage is in it?

---

## 9. Phase 2 as built — the Hedge Fund Pulse

*Shipped in v4.86: `hf_pulse.py` (pure), `hf_scan.py` (stateful), the Pulse
panel in `tab-hedge.jsx`, and four new providers in `hf_sources.py`.*

### What the live data changed about the design

Six things only appeared once the real sources were running, and each one
changed the code:

1. **FINRA caps days-to-cover at 1000, and 4,133 of the 22,482 rows in the
   August 14, 2026 report sit at that cap.** They are OTC foreign ordinaries
   trading a few hundred shares a day, where dividing by a near-zero
   denominator produces a number that means nothing. Unfiltered, the
   "most crowded shorts" list was entirely untradeable tickers. With a
   floor of a million shares a day and the cap excluded, the list is
   NFE at 28.6 days, NTST at 27.9, SVRA at 27.1, OCGN at 23.9 — real names.

2. **Net position and one-sidedness are the same fact when gross is
   steady.** The first crowding rule counted net, gross and one-sidedness as
   three independent measures and called any two of them a crowd. But
   one-sidedness is |net| ÷ gross, so a high net drags it up automatically
   and one fact voted twice; every quiet market with a single notable
   reading came out crowded. Crowding is now LEAN (one-sidedness) **and**
   SIZE (gross), which are genuinely two different things.

3. **A perfectly flat series returns the 100th percentile** if ties count,
   which would flag every quiet market as extreme. A series with no spread
   now has no percentile at all.

4. **The sector contracts differ in size by more than tenfold.** Ranking
   "most bought" by raw contracts put Financials on top regardless of what
   happened. Sectors are now ranked by the change divided by that
   contract's own typical weekly move — Health Care sold at 1.7× its
   typical week, Utilities bought at 1.6×.

5. **The short share of volume moves in tenths of a point.** A 0.19-point
   move read as a direction until the input was given a noise band drawn
   from its own history.

6. **The OFR gzips its JSON whatever Accept-Encoding asks for**, and urllib
   does not decompress. Sniffing the magic bytes is the only reliable test.

### The rules the Pulse keeps

- **The change is the signal; the level is not.** Leveraged funds are
  structurally net short index futures — on September 1, 2026 they were
  short 317,564 E-mini contracts, which is an ordinary Tuesday. Every
  verdict reads the weekly change; the level appears only as a percentile
  of its own three-year history.
- **Leverage reads GROSS, not net.** A book that doubles both legs has
  taken more risk and its net does not move. Form PF is the official
  measure and is about five months behind, so it is context with its own
  date, never the weekly picture.
- **The two legs are answered separately.** "Reducing longs?" and "adding
  shorts?" are different questions and a net figure hides both.
- **Confidence counts independent evidence classes**, not a probability.
  One source is one source (LOW); two classes agreeing is the first point
  the answer is not an artefact of one provider (MODERATE); three with no
  dissent is HIGH. A prime-broker quote can raise confidence in what the
  data already says and can never create a verdict alone — the Phase 3
  channel is wired and tested for exactly that.
- **Disagreement is a finding.** Inputs pointing opposite ways make the
  verdict MIXED and both sides are listed.
- **Missing is not zero.** Only seven of the eleven sectors have a
  leveraged-fund futures contract; the other four are read from flows and
  the card says so. Sources that did not answer are named.

### The record

Each reading is stored under its ISO week and kept forever, so "what
changed from last week" is a comparison against a stored answer rather than
a claim. A re-run inside the same week replaces that week's entry and still
compares against the previous one.

### Where the two layers meet

In exactly one place: `hf_scan.headline()` produces one sentence, classed
MODEL INFERENCE, and `options_dashboard.py` injects it into `hf_watch` as
`trend_fn`. `hf_watch` does not import `hf_scan`. The sentence is rendered
under its own heading with its own date, and no fund's record is computed
from it.

### Still Phase 3

The prime-broker channel (Reuters "HEDGE FLOW" and equivalents quoting
Goldman, Morgan Stanley, JPMorgan) is wired into `hf_pulse.build()` and
tested, but nothing populates it yet. The weekly report assembly, its
history view and the compare-with-week-N view are Phase 3.
