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
| **3 — Combined + Weekly Report** — *shipped in v4.87* (`hf_press.py`, `hf_report.py`, the Weekly Report panel) | The combined rule on every fund card; press capture (PRIME BROKER quotes); the report builder, store, history and compare; conflicts and confidence | Phases 1–2 |
| **4 — optional** — *shipped in v4.88* (`hf_grade.py`, the X channel, the grader panel) | X statements if the token is set; Form SHO when published; SSGA fallback (adds `openpyxl`); outcome grading | Phases 1–3 |
| **5 — the record deepens** — *shipped in v4.89* (`hf_replay.py`) | The four weekly answers recomputed for every past week the data allows, so they are graded on years rather than on the weeks stored since September 2026; the crowded single names the board never used to put a ticker on | Phase 4 |

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

### Phase 4, since shipped

Section 11 records what each of the four optional channels actually
returned when it was checked, and what was built as a result. Form SHO is
still unpublished and the SSGA fallback was declined as redundant; the X
channel is wired and dormant without a token; the outcome grader is live.

### Phase 3, since shipped

The prime-broker channel now populates the parameter `hf_pulse.build()` had
been holding open, the weekly report assembles both layers into one stored
document, and the history and compare-with-week-N views are on the card.
Section 10 records what the live headlines taught. What remains unbuilt is
Phase 4: X statements if a token is set, Form SHO when it is published, the
SSGA fallback, and outcome grading.

**Follow-up, same day.** The first production reading (week 2026-W36, CFTC
as of September 1, 2026) worked except for one source: the Unusual Whales
client unwraps the JSON envelope and returns the tide rows as a bare list,
while the raw endpoint returns `{data, date}`. The gather assumed the
second shape and raised `'list' object has no attribute 'get'`, which the
board correctly reported as the source being unavailable rather than
silently dropping it. Both shapes are now read, and guarded.

---

## 10. Phase 3 as built (v4.87)

Two new modules and a third panel. Nothing in Phase 3 measures anything: it
reads a channel that was designed for and left empty, and it assembles what
Phases 1 and 2 already computed.

### 10a. `hf_press.py` — the prime-broker channel

Written against 319 real Google News headlines captured on September 6,
2026. Sixteen of them cleared every filter. The other 303 are the reason
the module exists, and each refusal below is a real headline from that
capture:

| Refused | Because |
|---|---|
| *JPMorgan Chase & Co. Shares Purchased by Smith Group Asset Management* | The bank is the subject, not the source. A bank counts only where the sentence cites it: "Goldman says", "JPMorgan data shows", "says Morgan Stanley". |
| *Goldman says hedge funds suffered worst underperformance vs S&P 500 in July* | Returns, not positions. Any return word rejects the item — that loses a few genuine positioning headlines and is the cheaper mistake. |
| *Hedge funds cut Asia tech holdings in second-largest selloff* | A stated non-US market. True, and not evidence about the book this board measures. |
| *Hedge funds ditch tech and buy essentials, Goldman Sachs says* | A rotation. The first pass read this as BUYING with the sector Technology, which is exactly backwards. Both directions in one question now yield none. |
| *Hedge funds squeezed from short bets amid surging meme stocks* | A squeeze forces funds OUT of shorts. Counting the words "short bets" read it the wrong way round. |
| *Hedge Funds Cut Tech Holdings at Record Pace, Goldman Data Shows* — carried by Briefs Finance | Only a wire service or the bank's own publication counts. The same claim from Reuters does count. |

Two rules beyond the filters:

- **The period is never invented.** Headlines say "last week" or "for a
  fourth consecutive week". None of that is machine-readable, so `as_of`
  stays empty and only the publication date is claimed. The headline itself
  is carried so the reader sees what was actually said.
- **One note carried by five outlets is one note.** Quotes are deduplicated
  by bank, question and direction, and how widely a claim travelled is
  reported as its own number. Same lesson as the crowding fix: the same
  fact must not vote twice.

A surviving quote enters a verdict at weight 0.5 and cannot create one.

### 10b. `hf_report.py` — the weekly report

Pure: dictionaries in, a dictionary out, no clock, so any week rebuilds
identically from stored inputs. Contents follow §5e exactly. Two
distinctions the code holds:

- **"Nothing to compare against" is not "nothing changed."** Without a
  prior stored report the section says so, instead of showing empty lists
  that read as agreement.
- **Disagreement survives assembly.** Conflicts are their own section, are
  never collapsed by default, and carry the warning colour.

### 10c. The store

`hf/reports/<week>.json`. A rebuild inside the same week APPENDS a
revision rather than overwriting. The board is a measurement and the latest
read wins; a report records what was known when it was written, so an older
build stays true about its own moment. A rebuild diffs against the previous
WEEK, never against its own earlier revision — otherwise "what changed"
would report the noise between two builds an hour apart. The compare view
reuses the same diff as "what changed", so the two can never disagree.

`hf_scan` does not import `hf_watch`, just as `hf_watch` does not import
`hf_scan`. The fund payload arrives as an injected `funds_fn`, the mirror of
the `trend_fn` that carries the aggregate sentence the other way.

### 10d. Routes

`/api/hf/report` (optionally `?week=` and `?revision=`) ·
`/api/hf/report/history` · `/api/hf/report/build` · `/api/hf/report/status` ·
`/api/hf/report/compare?a=&b=` · `/api/hf/press`

### 10e. What the render check caught

Every static layer passed while the sector table drew a dash in its
confidence and streak columns. The report had flattened both to scalars and
the shared component reads them as objects. It also meant a stored report
lost the inputs behind each sector, so a week read back later would show a
verdict with nothing behind it. The rows are carried whole now. This is the
second time a browser render has caught something no static check could —
the first was Phase 1's `apiFetch` returning an unread `Response`.

### 10f. Five findings from the post-merge review

A review bot filed five findings on the Phase 3 pull request after it
merged. All five reproduced against the code and all five are fixed. Four of
them share a shape worth naming: **a value that was correct where it was
computed became wrong where it was reused.**

| Finding | What it did |
|---|---|
| The report looped on an old week | `build_report()` took whatever board was in memory. After a week rollover that was last week's reading, so the report was filed under last week — which `_report_stale` then judged stale forever, appending a revision on every look at the card and never re-reading the pulse. |
| Revision numbers collided | The next number came from counting what was on disk, but retention means only the survivors are there. Build 14 wrote a second revision 13, and asking for revision 13 returned the older of the two. |
| Compare showed the wrong week's watchlist changes | Those rows were copied from the current report's own `added`/`removed`, computed against the report that preceded it. Comparing two non-adjacent weeks missed what moved in between. |
| A bank's name was read as a place | "Bank of America" contains *America*, so a headline about Asia matched the US pattern too and became eligible evidence about a market it was not describing. |
| "Activity this week" was not this week | `FILED SINCE` persists until the next 13F, which is months. The first live report showed four managers as having acted in a week whose new-filing sweep found nothing at all. |

The fifth was visible in the very first production reading and I had read
past it: four managers "acted" while the new-filings section directly below
said nothing had been filed since August 31. The two numbers contradicted
each other on screen.

The stored shape changed with the last of these, so `HF_REPORT_VERSION` moved
to 1.1.0. Reports are kept forever; two documents both stamped 1.0.0 would
otherwise mean different things by `n_acted`.

---

## 11. Phase 4 as built (v4.88)

Phase 4 was four optional items. Each was checked before anything was
written, on September 7, 2026:

| Channel | What the check returned | What was built |
|---|---|---|
| **SEC Form SHO** aggregates | Still not published. Three data-library paths returned 404 and the markets-data page lists no short-sale data set. | Nothing. The design's "switched off until it exists" stands, now with a second dated check behind it. |
| **SSGA sector holdings** | Reachable — a real 23 KB xlsx, after a redirect to a different host. | Nothing. It needs an xlsx reader the app does not have, and Unusual Whales already serves the same ETF flows and answered healthy on the day (291 of 40,000 daily calls used). Adding a dependency for a redundant fallback is a cost with no return. |
| **X statements** | The token's presence cannot be read from outside the deployment. | Built, gated. `hf_sources.x_statements` returns an empty list whenever no bearer token is set, which is most deployments, and a manager's post joins the card as a STATEMENT — a claim, never a position. The search is composed in code from a validated handle; the watchlist cannot supply a query (§11e). `/api/hf/config` reports whether the channel is live. |
| **Outcome grading** | Unusual Whales returns five years of closes in one call and documents a weekly candle whose date is the ISO week's Monday — the same key the board files readings under. | Built. `hf_grade.py`. |

### 11a. Why three years can be graded when one week is stored

Crowding is computed from the CFTC series **and nothing else**. Truncating
that series so week W is the newest row reproduces exactly what the board
would have said in week W, with no data that arrived later. So the record
can be reconstructed back to 2021 and priced.

The four weekly verdicts cannot be reconstructed that way — they need short
interest, ETF flows and the options tide, none of which are kept
historically — so they are graded only from readings stored since the board
began keeping them. Today that is one week, and the panel says so.

The first reconstruction produced **1,303 market-weeks across 194 weeks,
2021-W21 to 2026-W36**, of which 51 were crowded.

### 11b. What the grader refuses to do

- **It never scores a verdict right or wrong.** "Hedge funds reduced
  exposure" is a fact about positioning and implies nothing about what the
  market does next. Scoring it as a forecast would put a claim in the
  board's mouth. The four questions get a distribution of forward returns
  beside the distribution across all weeks, and no hit rate.
- **It always shows the base rate, on the same market.** A sector that fell
  all year would otherwise make crowding look predictive; with the base rate
  beside it the lift is zero, which is the truth. A test pins exactly that.
- **It does not grade a market with no honest proxy.** VIX is the case: its
  listed funds roll a futures curve, so an eight-week return measures the
  roll rather than the index.

### 11c. What a random-walk control exposed

Grading the real 1,303-week history against random prices produced an
8-week crowded reversal share whose 95% interval excluded the base rate — a
finding, on data with nothing in it. The cause is real and worth stating on
the card rather than hiding: **crowded weeks arrive in runs and their
forward windows overlap**, so the Wilson interval, which assumes independent
draws, is optimistic.

The 51 crowded weeks are only **23 episodes** — Financials alone is 15 weeks
in 4 episodes. The episode count now travels beside every share, and the
limitation says in words that the intervals are optimistic and why.

### 11d. Two bugs the first backfill run caught

- **Every reconstructed week carried the same date.** The window was built
  as `{**market, "series": series[i:]}`, which left the market's original
  `as_of` untouched, so 204 rows all landed in 2026-W36. The window's own
  newest date has to be written in.
- **The shortest series capped every market.** Taking the minimum depth
  across all twelve markets cut the S&P's 170 weeks down to the 69 the
  Communication Services contract has. Each market is now reconstructed as
  deep as its own history allows: 118 weeks for most, 17 for the newest
  contract.

### 11e. A handle cannot smuggle a query

The first build stored two fields per manager: `x_handle` and a free-form
`x_query` that went to the search verbatim. That is a hole in the one rule
this whole feature rests on. A handle of

    BillAckman OR from:someone_else

was interpolated into the query actually sent —

    from%3ABillAckman+OR+from%3Asomeone_else+-is%3Aretweet+-is%3Areply

— and the second account's posts came back filed under the first manager's
**own words**. A STATEMENT is the strongest thing the board can say about a
person, and a watchlist string could forge one.

So there is no free-form query any more. `x_handle` must match
`^[A-Za-z0-9_]{1,15}$` — X's own handle grammar, which cannot contain a
space, a colon or the word `OR` — and `hf_sources.x_query_for` composes the
search from it. The registry rejects a bad handle **and rejects an
`x_query` key outright** rather than ignoring it, so an old watchlist that
carries one fails loudly instead of quietly losing a field the author
thought was doing something. The editor applies the same rule in the
browser.

### 11f. A grade that priced nothing waits an hour

An earlier fix stopped a build that returned no closes from being saved as
a finished card — otherwise an outage froze an empty grader for a week.
But "not saved" also meant "still stale", and the next request rebuilt
immediately: a provider outage became a retry storm against the same dead
endpoint, once per page load.

A failed build now records `grades_retry_at` an hour out, and staleness
returns false until that passes. Proven both ways:

    after a failed build:  stale? False   retry_at 2026-09-07T13:00:00+00:00
    two hours later:       stale? True

The wait is the `hedge.grade.retry_hours` knob, and `/api/hf/grades/status`
reports `retry_after` so the panel can say when it will try again instead of
looking broken. An explicit rebuild still overrides it — the cooldown holds
back the automatic retry, not the person asking for one.

### 11g. One week graded four times

Checking the live card turned up a third instance of the same pattern.
`n_crowded_weeks_graded` was the **sum** of the four per-horizon counts, so
a week old enough for every horizon to reach it was counted four times: the
status route read **127** where **33 distinct weeks** had been graded.

It is now the union of what the horizons actually graded, and
`n_crowded_weeks` — the crowded total, 51 — travels beside it, because "33
graded" says nothing without "out of 51 crowded, from 1,303 reconstructed".
Neither the sum nor the maximum would do: the sum double-counts, and the
shorter horizons grade recent weeks the eight-week horizon cannot reach
yet, so no single horizon is a superset of the rest. The number was never
rendered on the panel — it is a diagnostics field — but it was wrong where
it was read, which is the whole of the problem. `HF_GRADE_VERSION` moved to
1.0.1 so two cards do not mean different things by the same key.

And the reason it survived a live check: `/api/hf/grades/status` reported
the **running module's** version, not the stored card's. After the deploy it
said 1.0.1 while serving a card built under 1.0.0, whose count still meant
the old thing. Both status routes now report the version of the document
they are describing, with `code_version` beside it for the running module.
The panels were never affected — they read `/api/hf/grades` and
`/api/hf/report`, which carry the stored document's own version.

### 11h. Routes

`/api/hf/grades` · `/api/hf/grades/status` · `/api/hf/grades/build`

---

## 12. Phase 5A as built (v4.89) — the four answers, recomputed

Phase 4 shipped a grader that could price **1,303 market-weeks of crowding**
and exactly **one week** of the four weekly answers, and §11a said the four
could never be reconstructed because they need short interest, flows and the
tide, none of which the board kept.

That was too pessimistic. Checked on September 7, 2026:

| Input | Depth actually available |
|---|---|
| CFTC leveraged-fund futures | 170 weeks |
| FINRA consolidated short interest | every settlement back past 2022 |
| FINRA daily short volume | daily files back past 2022 |
| OFR Form PF leverage | 53 quarters, 2013 → 2026 |
| ETF creations | `/api/etfs/{ticker}/in-outflow` takes `start_date` and `end_date` |

### 12a. Which answers need what

Removing each optional input and counting what the verdict was actually
built from settles it — this is measured, not assumed:

| Question | Inputs with everything | Futures only | Needs |
|---|---|---|---|
| Increasing or reducing exposure | 5 | 4 | ETF creations |
| Increasing or reducing leverage | 3 | **3** | nothing else |
| Reducing longs | 3 | **3** | nothing else |
| Adding shorts, or covering | 5 | 3 | short interest **and** the short-volume share |

Form PF never enters the leverage verdict — it is carried as `context`
beside it, labelled with its own date. So **leverage and longs are decided
by the futures report alone** and replay to the full depth of the CFTC
series with no other source at all. That is why Phase 5A delivers a real
record even on a deployment where every optional history fails.

### 12b. The rule that makes a replay honest

**Input-complete or not at all.** A question is recomputed for a week only
when every input that decides it today was public that week. Rebuilt from
fewer inputs it is a different answer wearing the same name, and grading it
would say nothing about the answers the board actually publishes.

The rule is enforced in three places:

- `REQUIRES` names each question's non-futures inputs; a week missing one is
  skipped for that question, with the reason recorded.
- A **half-filled window is refused**. The flat band on the short-volume
  input is drawn from the spread of its own history, so three sessions and
  twenty do not merely differ in confidence — they hand `build_verdict` a
  different band. Same for the five-session ETF total.
- A **partial ETF universe is refused**. The live board sums every fund the
  daily endpoint returns, so the replay reads that endpoint to learn the
  universe and refuses to sum whichever funds happened to answer.

And the replay calls `hf_pulse.exposure`, `hf_pulse.leverage` and
`hf_pulse.longs_and_shorts` — the board's own functions. A reimplementation
would drift the first time either changed and would then be grading a board
that does not exist. A test asserts the verdict maths appears nowhere in
`hf_replay.py`.

### 12c. It reproduces the live board exactly

The strongest check available: replay the current week and compare it with
what the deployment is publishing right now.

```
question    replayed W36     live board  match?  inputs
leverage          RISING         RISING  True    3/3
longs             ADDING         ADDING  True    3/3
```

The first real run replayed **158 weeks, 2023-W35 → 2026-W36**, with
genuinely varied answers rather than one repeated verdict: leverage 77
RISING / 70 FALLING / 11 MIXED, longs 80 ADDING / 65 REDUCING / 13 MIXED.

### 12d. A week-key bug this uncovered

Comparing the replay against the live board exposed a real defect in the
**shipped** Phase 4 grader. The board stamps a snapshot with the calendar
week it ran in; the CFTC report it read is dated the Tuesday before and
published that Friday. Live, right now:

```
board week : 2026-W37
cftc_as_of : 2026-09-01   ->  2026-W36
```

The crowding backfill has always filed a reading under its **data** week.
The stored verdicts were filed under the **build** week. So inside a single
grade card the two layers were priced from weeks a week apart. With one
stored reading that was invisible; with 158 replayed weeks beside it, every
week would have been mis-priced. `data_week()` now keys a reading by the
data it describes, falling back to the stamp for older rows that predate
`cftc_as_of`.

A stored week always beats a replayed one for the same week: the board's own
record is what it published, the replay only what it would have.

### 12e. The daily files are read once and thrown away

Each FINRA short-volume file is several megabytes and yields exactly one
number the verdict reads. Fetching eight hundred of them on every rebuild
would move gigabytes to recompute a few hundred floats, so the numbers are
cached one per session and the files are not. The walk is **bounded per
build** — forty sessions by default — and resumes rather than restarts, so
the `shorts` answer reaches further back every week and the panel reports
the depth it has actually reached rather than one it has not.

### 12f. Routes

`/api/hf/replay` · `/api/hf/replay/status` · `/api/hf/replay/build`

The week-by-week rows are hundreds of entries that nothing renders, so the
payload carries the coverage and the sources and leaves them out.

---

## 13. Phase 5B as built (v4.89) — the names behind the crowd

The Pulse could say Financials was crowded without ever putting a ticker on
screen for the long side. The **short** side already did:
`hf_pulse.crowded_shorts` reads FINRA's short interest and lists the most
crowded single-name shorts, anonymously, because that data belongs to
nobody. This is the long side, and unlike the short side it is fully
attributable — every row is a named manager's own 13F, so the funds are
named beside it.

### 13a. Four rules that keep it from becoming fiction

- **An OPAQUE book is not a view.** A multi-strategy or quant 13F shows
  hedging, index exposure, and the long leg of trades whose short leg never
  appears in it. §6 already says the fund cards refuse to summarise those as
  conviction; counting them in a consensus would be that same fiction at
  scale. Only READABLE managers are counted, and the card names the ones
  left out. In testing, Citadel held the largest position in the sample and
  was still correctly absent from every row.
- **A put is not ownership.** A 13F lists options beside shares. A manager
  holding puts is betting against the name, and counting that row as one of
  the funds "in" the stock would report the position exactly backwards.
  Puts are listed apart, under their own heading. Calls are counted — a call
  is still a long bet — but flagged, because it is not the same as owning
  the stock.
- **It counts what it can actually see.** Only each manager's ten largest
  positions are stored, so this measures how many readable books hold a name
  **among their ten largest** — never "most owned", which would need whole
  books. A name held in eleventh place by everyone would not appear at all,
  and the card says so.
- **Different managers, different quarters.** A 13F describes one day and
  lands 45 days later, and managers do not file together, so a row can mix
  one manager's June book with another's March. Every row carries the span
  rather than a single date that would imply they were all true at once.

### 13b. The denominator travels with the count

"Eleven managers agree" means one thing out of eleven and quite another out
of thirty-two, so the card always shows both, plus how many were left out as
not readable and how many have not been read yet. A manager never read is
not silently treated as holding nothing.

### 13c. Position rows never reach the browser

`hf_watch.positions()` is deliberately separate from `snapshot()`. The
snapshot goes to the browser on every load of the tab, and thirty-two books
of position rows would be by far the largest thing on it. The consensus
layer is the only caller that needs them and asks for them separately,
through an injected `positions_fn` — `hf_scan` still never imports
`hf_watch`.

### 13d. Route

`/api/hf/names`. Nothing is stored: a 13F changes four times a year, the
watch already keeps the filings, and a second copy on disk would only be a
second thing to keep in step with the first.
