# Premarket Gap Fade & Rebound Scanner (v4.30)
### with KOREA LEAD — overnight context above the scanner (v4.49)
### V2 adds the research layer that tests whether it deserves trust

One screen, premarket: which gap ups historically fade, which gap downs
historically rebound — with the measured evidence one click away. The board
answers "should I care about this mover" in seconds; the detail view shows
why, with a sample size and a conservative range on every probability.

## What it is

- **`gap_engine.py`** — pure math (stdlib, no I/O, no clock): gap
  calculations, event extraction, corporate-action exclusion, minute-path
  outcomes (target-before-stop, MFE/MAE, timing), premarket point-in-time
  features, Wilson-gated aggregation, cohort selection, signals, hysteresis.
- **`gap_scan.py`** — the stateful side: the two-stage premarket funnel,
  the per-symbol event store, budgeted minute-history enrichment, the
  post-close recording pass, decision journal, board persistence, the
  premarket scheduler (07:00–09:40 ET, every 5 min), and the walk-forward
  target/stop grid.
- **`tab-gap.jsx`** — the Gap Scan tab: compact board (10 columns, 5 on
  mobile), evidence detail view (§27 layout: setup / probabilities / risk /
  timing / evidence), analog inspection table, walk-forward grid.
- **Config**: `thresholds.json → gap_fade` (deep-merged data-dir overlay,
  hashed into every journal record). All signal gates are configurable
  starting rules, not hardcoded truths.

## Core honesty rules (enforced by tests)

- **No n, no probability.** Every rate carries its sample size; hovers show
  the Wilson interval; signal gates read the LOWER bound, so 7-of-8 can
  never impersonate a reliable 87%.
- **No lookahead.** A historical day only qualifies for a time-matched
  query after the moment it actually crossed the gap threshold
  (`pm_first_cross`); premarket features at time t are computed from bars
  ≤ t (proven by future-mutation tests).
- **Ordering is measured or refused.** Target-before-stop comes only from
  real minute paths; same-minute target+stop resolves AGAINST the trade
  (`INTRABAR MODELED`); daily-only history shows `UNKNOWN / DAILY ONLY`.
- **Earnings never contaminate.** Earnings-gap and ordinary-gap statistics
  are separate populations; an earnings-day mover with no earnings-gap
  history shows NO DATA rather than borrowing the ordinary base rate.
- **Corporate actions can't fake gaps.** Declared splits/dividends (when
  available) plus a heuristic detector (near-round price ratio AND the
  reciprocal volume signature) exclude days as `EXCLUDE_SPLIT` /
  `EXCLUDE_DIVIDEND` / `EXCLUDE_UNRELIABLE`, with reasons persisted.
- **Signals are stable.** Displayed state changes only after 2 consecutive
  evaluations agree (raw + displayed both recorded); risk escalations
  (NO DATA, stale data, fresh earnings tag, continuation risk) bypass
  hysteresis immediately.

## Reused (not duplicated)

| Need | Reused |
|---|---|
| Premarket quotes w/ true extended gap % | `schwab_client.get_quotes` (movers.py pattern) |
| Historical minute bars incl. premarket | `get_intraday_day(..., extended=True)` — a parameter added to the existing method |
| Live premarket tape | `get_intraday(extended=True)` |
| Daily bars (adjusted, Schwab-first) | `load_daily` + source label |
| Session split | `intraday.split_premarket` (made replay-safe: optional cut + 04:00 floor) |
| Board/scan/persist/restore contract | `recovery.py` skeleton, `movers.py` front end |
| Earnings calendar + history | `_bulk_earnings_map` (BMO/AMC), `load_earnings_history` |
| Analyst actions / macro days | `analyst_board`, `treasury.MACRO_SCHEDULE` |
| Ticker → SEC CIK | `load_ticker_index` (the SEC file the app already downloads for autocomplete) |
| Sector mapping + context ETFs | `recovery._SECTOR_ETF`, SPY/QQQ/sector quotes in the same sweep |
| Hysteresis discipline | timing-engine pattern (raw vs displayed, escalation bypass) |
| Journal/replay | timing-engine JSONL discipline (`<data>/gap/decisions/`) |
| Stats | `metrics._norm_cdf`; NEW shared `metrics.wilson_interval` + `metrics.percentile` |
| Test fixtures | seeded synthetic bars, frozen dates, injected deps (house style) |

## Measured vs modeled

- **MEASURED**: official gaps, favorable/adverse excursions, gap fill,
  continuation, minute-path ordering, MFE/MAE, times to target, premarket
  paths (checkpoints archived permanently), earnings separation.
- **MODELED (labeled)**: execution costs (per-side slippage floor, spread
  fraction, premarket multiplier, stop fill-through) inside EV and the
  walk-forward grid; same-minute ordering ties (conservative).
- **DAILY ONLY (labeled)**: events older than minute retention keep
  favorable/adverse rates but never ordering claims.

## Data limitations (disclosed in-product)

- **Minute retention ~6 months** at the source. Minute-path enrichment is
  archived as compact checkpoints forever, so coverage GROWS from launch;
  today the deep history is daily-basis and recent history is minute-basis.
- **PM-only qualifiers** (crossed the threshold premarket, opened small)
  are fully discoverable only from launch forward (the post-close pass
  records every scanned day); historically they're found on "hinted" days
  (daily range/gap ≥ 2%) inside the minute window. A historical day with no
  daily trace and no scan is honestly invisible.
- **Survivorship bias**: the universe is today's watchlist + analyst
  universe; delisted gappers are absent, so fade rates run hot. Stated on
  the board itself.
- **Catalyst tags**: all real, all dated, all from sources the app actually
  has — the full list is below. Short interest, index inclusion, analyst-day
  guidance, product launches and short-seller reports still have NO source
  here and are never fabricated; such days are UNTAGGED. Only earnings
  splits the statistical population, everything else is context shown
  alongside it. When two land the same morning both are named in one label
  ("… · also filed: stock offering priced"), because the approval explains
  the gap and the raise explains what happens to it next.

## Offering & dilution filings (`sec_filings.py`)

The most common reason a small cap gaps down premarket is that it is
selling stock. EDGAR is the source of record — free, authoritative, live
within minutes of acceptance — so this is read, not guessed.

| Form | Tag | Why |
| --- | --- | --- |
| 424B1 / 424B4 / 424B5 | OFFERING | a deal being priced off an effective registration |
| 8-K item 3.02 | OFFERING | unregistered equity sale (PIPE / private placement) |
| S-1 / S-3 / S-3ASR / F-1 / F-3 | DILUTION | shares registered for future sale — permission, not a sale |
| 424B2 / 424B3 / 424B7 / FWP | only with proof | debt takedowns and merger prospectuses use these too |

**Today's tag** reads the winning filing's own cover page once, so the
label says what was actually sold (`Stock offering priced — 6.8M shares`,
`At-the-market stock program — $1.0B`, `Convertible notes offering`) and a
bond deal is dropped rather than mislabeled as dilution — a utility pricing
notes dilutes nobody. Deal size is quoted only when the sentence binds the
number to the offering; a stray figure from a fee table is left out. Every
tag carries a link straight to the filing.

**Historical tags** are form-derived only — no document read — so the
ambiguous forms above are excluded from history entirely. Better a missing
tag on a 2024 gap than a wrong one.

`acceptanceDateTime` from EDGAR is **UTC**, verified rather than assumed:
Apple's 4:30pm ET earnings 8-K lands at 20:30Z, JPMorgan's 6:30am one at
10:30Z, and across ~2,700 non-Section-16 filings the next-business-day roll
begins exactly at 21:30Z (the SEC's 5:30pm ET cutoff). Reading it as
Eastern would move a 3am premarket offering into the afternoon.

Limits worth knowing: EDGAR's `recent` block covers the last ~1,000
filings, which for a busy filer can be shorter than the 900-day daily
history — older gap days then stay UNTAGGED. Foreign private issuers file
6-K/F-forms with less structure. Neither is faked.

## The catalyst taxonomy

Ranked by how much of a gap the event explains. Earnings sits above all of
it — not because it matters more, but because it is the one tag that
decides which statistical population the day belongs to.

| Tag | Source | Cost |
| --- | --- | --- |
| EARNINGS | bulk calendar + report history | free |
| BANKRUPTCY | 8-K item 1.03 | free |
| BUYOUT | SC 14D9 / SC TO-T / SC TO-C, 8-K item 5.01, or merger text naming this company as the target | free / one read |
| FDA REJECTION · FDA APPROVAL | 8-K text | one read |
| TRIAL FAILURE · TRIAL SUCCESS | 8-K text ("did not meet the primary endpoint") | one read |
| MERGER DEAL | merger agreement whose side could not be proven | one read |
| RESTATEMENT | 8-K item 4.02 | free |
| DELISTING NOTICE | 8-K item 3.01 | free |
| REVERSE SPLIT | 8-K text, with the ratio | one read |
| ACTIVIST STAKE | first SC 13D (amendments excluded) | free |
| SHORT REPORT | named short-selling firm in a fresh headline (8-K response where one exists) | news feed |
| DELISTING NOTICE | 8-K item 3.01 | free |
| LATE FILING | NT 10-K / NT 10-Q | free |
| — *below here a share sale explains the morning better* — | | |
| OFFERING · DILUTION | 424B/S-3/S-1 + cover page, 8-K item 3.02 | one read |
| GUIDANCE CUT · GUIDANCE RAISED | 8-K text, outside quarterly releases | one read |
| INDEX ADD · INDEX DROP | fresh headline naming S&P / Russell / Nasdaq-100 | news feed |
| MERGER VOTE | DEFM14A / PREM14A | free |
| DEAL CLOSED | 8-K item 2.01 | free |
| BUYBACK | 8-K text, new authorizations only | one read |
| INSIDER BUYING · INSIDER SELLING | Form 4 transaction codes, rolled up per session | one read each |
| IMPAIRMENT · RESTRUCTURING · AUDITOR CHANGE | 8-K items 2.06 / 2.05 / 4.01 | free |
| UPGRADE · DOWNGRADE · ANALYST ACTION | analyst board + per-symbol feed | free |
| LEADERSHIP CHANGE | 8-K item 5.02 | free |
| MACRO | macro schedule | free |

Most of the list costs **nothing**: 8-K item numbers and form types are
already in the submissions feed the offering tagger fetches. Only the rows
marked "one read" open a document, and those verdicts are cached forever.

**A pending deal breaks the statistics, and the UI says so.** Once a
takeover price is fixed the stock trades to that price instead of moving on
its own, so its own gap history stops describing it. BUYOUT / MERGER DEAL /
MERGER VOTE therefore carry a visible warning above the numbers — the
statistics still render (they are measured history, not a guess), but they
were measured on a stock that was free to move.

Direction is never guessed. A merger agreement is tagged BUYOUT only when
the filing's own words put this company on the receiving end ("the
acquisition of the Company by Parent", "to acquire all of the Company's
outstanding shares"); otherwise it is MERGER DEAL, and the label says read
the filing. Where the deal price appears it is quoted: `$77.00 per share`.

## FDA approvals & rejections (`sec_filings.classify_filing`)

Why not openFDA: it publishes approvals on a weekly lag, under sponsor
names that do not map cleanly to tickers (big pharma files through
subsidiaries), and it does **not publish rejections at all** — a Complete
Response Letter reaches the market only because the company discloses it.
Both halves live in the same place instead: the **8-K the company files
when it happens**. That is same-morning, authoritative, and needs no name
matching, because the filing is the company's own.

One fetch of the full submission (`<accession>.txt`) carries the item body
*and* the press-release exhibit, which is where the plain-English sentence
lives. The tag quotes that sentence verbatim — the board shows a trimmed
line, the tooltip the whole thing, and the link opens the filing.

Every biotech filing mentions the FDA constantly, so the classifier is
built to stay silent unless a decision is genuinely being announced:

| Rejected as not-an-event | Example |
| --- | --- |
| Hedged sentences | "the risk that the FDA **may** issue a Complete Response Letter" |
| Permission to run a study | "received FDA approval **to initiate** the CGUARDIANS III trial" |
| A board, not the agency | "the Board of Directors **has approved** a quarterly dividend" |
| Old news retold | "…to discuss the Complete Response Letter **issued on April 23**" |
| Quarterly releases (item 2.02) | recap the year's approvals; that day is the earnings gap anyway |

Validated against 19 real filings pulled from EDGAR while writing it —
4 approvals, 1 fresh rejection, 14 negatives including every trap above.

Which session a filing explains comes from the **acceptance clock**, not
the filing date: a 7:05am filing moves that morning, one accepted after
16:00 ET moves the next business morning. (EDGAR already rolls the filing
date forward for evening submissions, so going by that alone points a day
too far.)

Historical days are read the same way, budgeted — a handful of documents
per pass, every verdict cached to `<data>/sec_fda/SYM.json` including the
"nothing here" ones, so a symbol's history fills in across scans instead
of stalling one.

Coverage limits: US 8-K filers only; a company that discloses in a later
business update rather than a same-day 8-K is tagged on neither day (the
recap guard is deliberate — an August filing about a June rejection must
not tag an August gap); and device 510(k)s or label changes announced only
by press release never reach EDGAR at all.

## Headline-derived catalysts (`news_catalyst.py`)

Two things move stocks hard and are **never filed with anybody**:

- a **short-seller report** — published on the author's own website. The
  target sometimes files a response days later, by which time the gap has
  happened. (Checked: TransMedics' and Onterris' responses both landed
  inside quarterly 8-Ks, which are skipped as earnings gaps anyway.)
- an **index add or drop** — announced by S&P or FTSE Russell, not by the
  company, though the company usually issues a press release.

These come from the news feed the app already runs (Yahoo · Finnhub ·
Google News · Finviz) and are the **only** tags not backed by a filing, so
the UI says so: the row shows `· headline · <publisher>`, the tooltip
spells out that no filing exists for this kind of event, and the link opens
the story instead of EDGAR.

Three rules keep the weakest evidence in the system honest:

| Rule | Why | Rejected example (real) |
| --- | --- | --- |
| Named short-selling firms, or an explicit "short-seller report" | "short" appears in half of all market commentary | *"SoFi Stock Short Interest Builds"* |
| The headline must name the ticker or the company | per-symbol feeds carry adjacent stories constantly | *"Defiance Launches MUZ: The First 2X Short ETF for Micron"* — in **MU**'s feed |
| Two days old at most | a stock gaps on the morning the report lands, not through the week of commentary after | Reddit's S&P inclusion, three days stale |

The news check runs **last and only when nothing was filed** — that is what
the evidence deserves, and it also keeps four network fetches per symbol off
the common path. Cached 10 minutes.

**History cannot carry these.** The news feed reaches back days, not years,
so past gap days are tagged from filings only. A dash in the analogs means
no *filing* was found for that day.

## Guidance changes

Guidance moved **outside** a quarterly report is a preannouncement, and
those gap hard. Read from the 8-K text like the FDA and deal rules; a raise
or cut inside a 2.02 release is ignored, because that day is an earnings
gap and earnings outranks everything anyway. Verified both ways against
real filings (Trex's mid-July raise tags; AMETEK's quarterly raise does not).

## Insider trading (`sec_filings.latest_insider`)

Form 4 is filed by the insider, not the company, but it lands in the
company's own submissions feed — measured, not assumed: **5,873 of the
filings across fifteen tickers were Form 4s**, more than half of every feed.

Which is the first problem: almost none of it means anything. Across 200
consecutive Form 4s from nine tickers the transaction codes ran

| code | meaning | n |
| --- | --- | --- |
| A | stock granted | 101 |
| S | sold on the open market | 62 |
| F | shares withheld for tax on a vest | 31 |
| M | option exercised | 24 |
| **P** | **bought on the open market** | **9** |

Grants, withholding and exercises are the mechanics of being paid, and are
dropped entirely. Derivative transactions are ignored for the same reason.

The second measurement decides the whole design. Of those 345 sales, **321
— 93% — were made under a Rule 10b5-1 plan**: scheduled months ahead,
non-discretionary by law, silent on what the seller thinks today. Of the ten
purchases, **zero** were under a plan. Insiders sell for many reasons and
buy for one; here that is a measurement on this app's own tickers rather
than a maxim. So buying is reported, selling is reported **only when it was
discretionary**, and the label says so out loud.

Trades are **rolled up per session**, because insiders move together: four
CING officers and directors filed separately on the same morning and only
their total ($167K) says anything — the smallest of them was $9,808 alone.
Floors apply to the session total, so a cluster of small buys still clears
while a lone trivial one does not.

**Live-only, for a cost reason.** Buy-versus-sell cannot be read from
metadata, so tagging history would mean opening thousands of documents per
symbol. History stays on the free metadata tags.

## Corporate actions

- **REVERSE SPLIT** — the one catalyst that changes the *numbers* rather
  than the company. A 1-for-10 multiplies the quoted price by ten overnight
  with no trading involved, so the fade percentages below it were measured
  on a price scale that no longer exists. It therefore carries a visible
  warning above the numbers, exactly like a pinned takeover price. The ratio
  is quoted from the filing (`1-for-5 · …`). Common in small caps defending
  a $1 minimum listing price, which is why it often lands beside a delisting
  notice or an offering.
- **BUYBACK** — new board authorizations only. Two real traps are blocked:
  a filing that sets up a 10b5-1 plan to *execute* a program approved
  earlier, and one that reports the *balance remaining* on one. Tyler's June
  8-K says "we have remaining authorization … to repurchase up to $332.7
  million", which reads exactly like an authorization and is a balance.
- **ACTIVIST STAKE** — a *first* Schedule 13D, the form used when a 5%+
  holder intends to influence the company. Amendments are excluded: 90 of
  the 121 real 13D filings measured were amendments, which are the same
  holder adding, trimming or leaving, and the form alone cannot say which.
  Passive 13Gs never count.
- **LATE FILING** — NT 10-K / NT 10-Q. Free from the form type, and ranked
  *above* an offering: a company that cannot produce its own financials
  explains a gap better than a share sale does.

## Still missing, on purpose

These move stocks and have **no source wired into this app**. Listed so
nobody mistakes an UNTAGGED day for a quiet one:

- **product launches** — measured and rejected, not skipped. In a sample of
  160 real headlines across four tickers, every single headline matching
  launch language ("launches", "unveils", "introduces", "debuts") was about
  a *different* company than the feed it appeared in — an ETF issuer
  launching a Micron-linked product, an energy-hardware vendor in Plug
  Power's feed. A tag that wrong would attach a confident, false reason to
  a gap that happened for another one. The detail view's 3-day news list
  already shows launches for the reader to judge.
- **dividend initiations, raises and cuts** — measured and rejected. Over
  seven months of 8-Ks there were **2,268** routine "quarterly cash
  dividend" declarations against **2** findable suspensions and **0**
  matches for the several ways a cut is normally phrased. The informative
  half is not reliably findable and the routine half would flood the screen,
  so neither is tagged. (A cut is a real gap catalyst; this is a sourcing
  limit, not a judgement that it does not matter.)
- **Form 144** — notice of a *proposed* sale. Skipped: it is intent rather
  than execution, it overlaps almost entirely with the 10b5-1 plan sales
  already discarded, and Form 4 records what actually happened.
- guidance given verbally at a conference and never filed
- partnerships and contract wins (8-K item 1.01 is far too broad to tag
  without reading every one, and most are not why a stock gapped)
- short interest, borrow rates and float — deferred at spec time (§36) and
  still deferred
- foreign private issuers, which file 6-K with no item codes at all

## Walk-forward discipline

`/api/gap/backtest?symbol=` sweeps targets (1–5%) × stops (1–5%) over the
symbol's measured paths, splits events chronologically, and marks a pair
`robust` only when BOTH halves have positive expectancy — judged by EV and
worst outcomes, never win rate. The 2%/3% defaults in the scanner columns
are starting rules; the grid exists to challenge them per ticker.

## KOREA LEAD — the overnight context layer

Sits at the TOP of the Gap Scan tab, above the individual-stock scanner,
because that is the order the morning happens in. Korea finishes trading
hours before New York opens, so whatever Seoul decided about memory and
semiconductors today is a completed, published fact by the time a U.S. chip
stock has to choose an opening price. The panel measures whether that fact
has ever told us anything, and refuses to say more than the measurement
supports.

Reading order: **what Korea did → did the Korean chip names confirm it →
what has historically happened to this ticker's OPEN after a move like this
→ what the U.S. premarket is actually doing → the movers below.**

### Files

- **`korea_lead_engine.py`** — pure math (stdlib, no I/O, no clock, no
  persistence): session alignment, the four return definitions, signed
  magnitude buckets, Pearson and Spearman, same-direction rates, Wilson
  intervals via `metrics.wilson_interval`, percentiles via
  `metrics.percentile`, the implied-gap lookup, chip confirmation, the
  premarket comparison, and how strongly each edge is described.
- **`korea_lead.py`** — the stateful side: the Korean series from Yahoo's
  chart endpoint (stdlib `urllib`, no new dependency), U.S. bars through
  the loader the rest of the app already uses, Seoul session state from
  `zoneinfo`, disk cache for bars, in-memory cache for statistics, and the
  assembled payload.
- **`tab-gap.jsx`** — the `KoreaLead` panel: four boxes on desktop, one
  column on a phone, every deeper statistic behind **Details**.
- **Config**: `thresholds.json → korea_lead` (data-dir overlay, hashed into
  every payload).

### Return definitions — exact, and never mixed

| Measurement | Definition |
| --- | --- |
| Korea signal | close ÷ prior close − 1, per Korean symbol |
| **U.S. opening gap** | open ÷ prior close − 1 — **the predictive target** |
| U.S. open to close | close ÷ same-day open − 1 — diagnostic |
| U.S. full day | close ÷ prior close − 1 — diagnostic |

The gap and the full day differ by exactly the move after 9:30. Treating
any two of them as interchangeable is how a signal that only predicts the
open gets sold as a signal that predicts the day, so the engine computes
all three from the same two bars and a test asserts they compose.

### Alignment

Korean session **D** maps to U.S. session **D** — nothing is shifted,
because Korea closed first. A date becomes an observation only when BOTH
markets actually traded it, read from the dates in each market's own bars
rather than from a calendar. When Korea traded and the U.S. did not, that
Korean session is **skipped and counted**, never rolled forward onto a
later U.S. session. Today is excluded from its own history: before the open
today's U.S. bar does not exist, and after it the bar is unfinished.

The alignment is protected by construction rather than by inspection. The
test fixtures make the U.S. gap a deterministic function of ONE specific
day's Korean move; the correct pairing recovers a perfect correlation and
both mis-pairings must not. On real data the same shape holds — correct
alignment is the strongest of the three, and pairing Korea one session late
turns the relationship NEGATIVE.

### What it will not do

- **No composite score, no weights.** There is no "Korea 87/100" and no
  40/30/20/10 split across KOSPI, Samsung, SK Hynix and the currency. No
  weighting has been validated out of sample, so none is applied. The
  inputs are shown side by side and the reader does the combining.
- **No probability.** The conditional figure is a **HISTORICAL MATCH
  RATE**, in the product and in the code. Nothing here has been calibrated
  against out-of-sample outcomes.
- **USD/KRW is context only, never a statistic.** Yahoo stamps the USD/KRW
  daily bar on a LONDON day — verified: its bars carry a 3600-second offset
  and land at London midnight, so the bar for a given day closes around 7pm
  in New York, hours AFTER the 9:30 open it would be used to predict. Using
  it would be reading the future, and no intraday FX history reaches back
  years to sample it honestly instead. It is displayed, labelled `context`,
  and excluded from every statistic; a test changes the currency and
  asserts that not one number moves.
- **Two dates have to agree before anything is a signal.** KOSPI's own
  newest session must BE the current Seoul date; on a Korean holiday, a
  weekend, a provider running a day behind, or a failed refresh serving the
  stored copy, the bias is withheld and the panel names the session it
  actually has. That earlier Korean move already had its U.S. session — the
  one sharing its date — so carrying it forward here would be the same
  roll-forward the historical alignment refuses to do. And a chip name is
  only confirmation if it traded the SAME day: a Samsung reading from
  yesterday beside a KOSPI reading from today is two different days being
  compared, so it is passed through as unreadable, named, and marked
  `not counted` on the row.
- **No sign-blind buckets.** Upside and downside Korean moves are separate
  tables. On measured data they are genuinely asymmetric — for SMH over one
  year, KOSPI up 1–2% preceded a same-direction open 83% of the time while
  KOSPI down 1–2% managed 38%.
- **No made-up "percentage priced in".** The share-already-covered figure
  is refused outright when the two signs disagree, and when the expected
  gap sits near zero. A share of a move in the other direction is not a
  quantity.
- **No silent granularity swap.** Asked for its longest daily range, the
  Yahoo chart endpoint answers 200 with MONTHLY bars — about twelve a year
  instead of two hundred and forty-five — and says nothing. That range is
  never requested, and `is_daily_series()` measures the median spacing of
  BOTH series and refuses the study by name if either is not daily.
- **No implausible day.** A split- and spinoff-adjusted series should never
  print a ±50% overnight move, so a day that does is excluded and counted
  rather than explained. It is deliberately not called a split detector.

### The two edges are separate, and judged identically

**OPENING GAP BIAS** and **AFTER OPEN EDGE** are computed by the same
function, from the same kind of evidence, against the same gates — so
whatever separates them on screen is the data, not a softer standard
applied to one of them. There is no single bullish/bearish word for the
whole day. Every gate reads the CONSERVATIVE (lower Wilson) end of the
same-direction rate and requires the linear and rank correlations to agree
in sign; the bias is MIXED whenever the interval straddles a coin flip, and
reports a lean AGAINST Korea when the interval sits entirely below one.

### Independent sanity check

Measured live, SMH against KOSPI over one year: opening gap **+0.41**
Pearson / **+0.38** Spearman / **65.8%** same direction; open-to-close
**−0.04** / **−0.03** / **49.2%**; full day **+0.26** / **+0.23**. Stable
across lookbacks (60 sessions +0.47, 1 year +0.41, 3 years +0.37, all
available +0.36). That reproduces the shape of the independently-run study
this feature was specified from — materially stronger for where a stock
OPENS than for what it does afterwards — without any of those numbers
being hardcoded anywhere.

The controls do their job: over the same window IGV (technology software,
which buys no Korean memory) reads WEAK where SMH reads STRONG, and SPY
sits between them.

### V2 — the research layer

V1 answered "what usually followed a Korean session like this one". V2 adds
the questions that decide whether that answer deserves to be trusted, in
`korea_research_engine.py` (pure statistics) and `korea_research.py` (data
and report). It has its own endpoint and its own cache and is never on the
path that renders the panel.

**What it measures.** Regression with HC1 heteroskedasticity-robust standard
errors — daily returns are violently heteroskedastic and classical errors
understate the uncertainty in exactly the months that matter. A real
Student's t distribution rather than a normal approximation. Incremental R²,
which is the number that separates a signal from an echo. Benjamini-Hochberg
FDR across the pair matrix. Empirical residual bands rather than multiples
of a standard deviation, because gap residuals have fat tails and a sigma
band is too narrow precisely when being wrong is expensive.

**Walk-forward, never a random split.** Folds are expanding windows and
every prediction comes from a model fitted only on sessions strictly earlier
than the one it scores. The regression test for this is a fixture whose
relationship REVERSES halfway: a model that could see the future would look
harmless, and a model restricted to the past is actively wrong afterwards,
so its out-of-sample direction accuracy must fall BELOW a coin flip. Any
future change that leaks future data into a fold pushes that number up and
fails the test.

**Korea Surprise, built point-in-time.** Raw KOSPI is partly an echo of the
previous U.S. semiconductor session. The surprise is the residual from an
echo model fitted only on sessions before each row — computed with running
sums so the expanding re-fit is exact rather than approximated for speed.
Fitting that model once on the whole sample would have made every residual
informed by sessions that had not happened yet.

**Taiwan and Japan are research and control, never signal.** Taipei closes
at 13:30 and Tokyo at 15:00 local, both before New York opens, so both are
eligible on the same reasoning Korea is. The Nikkei is there to test the one
alternative that would make this feature a mirage — that Korea is merely a
thermometer for overnight Asian risk appetite — and it is never promoted.

**On the main card**, V2 adds only: the 60-session and 1-year relationship
with a sparkline of the recent trend, a RELATIONSHIP UNSTABLE flag when the
two disagree in sign, an evidence-based health label with all four of its
inputs printed beside it, an UNUSUAL KOREA MOVE banner driven by a
percentile against the index's own trailing year rather than a fixed
percentage, a second fitted-line gap estimate beside the bucket one with
MODEL DISAGREEMENT when they differ, and the residual judged against this
pair's own residual history. Everything else is behind Details.

**Endpoints**

- `GET /api/korea_research?symbol=&window=` — the full report for one target
- `GET /api/korea_research/matrix?window=` — every Asian input against every
  U.S. target, with q-values
- `GET /api/korea_research/validation` — the long-run reproduction against
  live provider data
- `GET /api/korea_research/coverage` — what the minute store can and cannot
  answer

**Tests use frozen fixtures only.** Not one assertion depends on what a
provider returned this morning; a test whose expected value is a live
correlation is a tripwire that fires on a vendor revision and trains whoever
sees it red to stop reading it. Live numbers live in
`korea_research.validation()`, which is a report to read rather than an
assertion to satisfy.

### What V2 found

Measured over the full common history (about ten years, ~2,360 matched
sessions), and reproducing the independently-run second research pass:

| | KOSPI → SMH | KOSPI → QQQ | SK Hynix → MU |
| --- | --- | --- | --- |
| Opening gap | **+0.34** | **+0.31** | **+0.40** |
| Open to close | −0.03 | −0.05 | +0.03 |
| Full day | +0.19 | +0.15 | +0.28 |

**Korea is not an echo.** The previous U.S. session explains essentially
nothing about the next U.S. OPENING GAP — baseline R² is 0.000 to 0.007 —
because the previous day's full-day return is already inside the prior
close, which the gap is measured from. Adding Korea takes R² to 0.13–0.19
with robust t of +8.8 to +14.6. Practically all of the gap's explainable
variance is Korea's.

**But information flows both ways, and mostly the other way.** Prior SMH →
KOSPI is +0.37, larger than KOSPI → SMH gap at +0.30. Korea echoes the
previous U.S. session more strongly than it leads the next open. Note that
legs A and D of the lead/lag map are close to the same measurement indexed
two ways, and are shown together rather than counted as two findings.

**It is Korea, not just Asia — and the evidence discriminates cleanly.**
With the Nikkei in the same regression on identical sessions, KOSPI survives
on every semiconductor target (t +4.2 to +5.5). The discriminating result is
what happens as the target changes: for MU, Korea t=+5.5 against Japan
t=+2.2; for IGV — technology software, which buys no Korean memory — Japan
t=+5.8 BEATS Korea t=+3.3. TSMC adds for broad semiconductors (SMH t=+3.6)
and nothing at all for MU (t=+0.40), which is what a logic-versus-memory
story predicts. A large part of Korea and Japan does overlap; the part that
does not tracks memory exposure.

**The relationship is structural, not one cycle.** Positive in every
calendar year from 2017 to 2026, ranging +0.17 to +0.50.

**No volatility-regime effect.** Split at the median of trailing realised
volatility computed through the prior close: calm +0.34, volatile +0.34.

**Which Korean input predicts which ticker**, measured directly rather than
inferred through a sector proxy: SK Hynix wins on the memory names — MU
(+0.40), SNDK (+0.38), WDC (+0.31) — and KOSPI wins on everything else,
including the broad ETFs and the equipment names. With samples this large
the FDR correction is not the binding constraint; the size of the
relationship is.

**Out of sample, on one shared evaluation set**, Korea Surprise is the
strongest single candidate for the SMH opening gap — 64.4% direction
against plain KOSPI's 63.6%, with lower error and a better Brier score. The
shared set matters: every candidate is scored on exactly the sessions where
EVERY candidate's inputs were present, because a model whose input is
missing on a Taiwan holiday would otherwise skip those sessions while the
baseline still scored them, and Lunar New Year is not a random sample of
anything. Restricting to the shared set removed a model that had appeared
to win by omitting the days it could not answer. Nothing was promoted to a
production signal.

### Endpoint

- `GET /api/korea_lead?symbol=MU&window=1y` — one target, one lookback,
  every statistic already decided server-side. Windows: `60d` `1y` `3y`
  `max`. Sections: `as_of` `session` `sources` `korea` `target`
  `opening_gap` `premarket_comparison` `after_open` `diagnostics`.
  Offline it answers 200 with a stated reason — the panel renders that.

Caching: Korean bars persist to `<data>/korea/bars/`, refreshed every five
minutes while Seoul is trading and hourly once it has closed; a failed
refresh serves the stored copy and marks it `stale`. Statistics cache in
memory against the target, the lookback, the signal definition, the engine
version, the hash of the active settings, and the exact span of sessions
measured — the settings hash included because editing the thresholds
overlay changes the wording of an edge without changing the span, so a key
built only from the span kept serving the previous settings' answer until
the next trading day rolled it over.

### Prepared for, not built

The engine already takes the signal series as a parameter and the
observations already carry Samsung and SK Hynix columns, so a **Korea
memory signal** or **Korea semiconductor signal** is a different argument
rather than new machinery — the Details drawer already shows both measured
separately, and on current data SK Hynix (+0.43) edges KOSPI (+0.41) for
SMH. A **Korea surprise/residual** signal (what Korea did minus what the
prior U.S. semiconductor session would have implied) needs one more column
on the same observations. Taiwan would be a sibling symbol map. None of
those is in V1, and no weighting will be applied to any of them until it
has been validated out of sample.

## V2.3 — hardening, and the first genuine forward data

V2.3 added no indicator. It closed the ways the feature could lie.

**Finality is evidence now, not a clock reading.** The panel used to call
the Korean session final at 15:30 Seoul regardless of what the market was
doing, which on the days Korea moves its trading day — most visibly the
annual College Scholastic Ability Test — would publish a still-moving
number as a settled one. No exam-day calendar was added, deliberately: a
hardcoded calendar is silently wrong the first year nobody updates it, and
wrong in the worst direction. Provider metadata does not answer it either,
and that was checked rather than assumed — the endpoint carrying these
series exposes **no market-state field at all**, and the trading-period
block it does expose reports the KRX regular session ending at 15:00,
disagreeing with the 15:30 the closing auction settles at. So the API now
carries a SCHEDULED state and a DATA state side by side, and a session is
final only once its value has been watched standing still, with a
documented conservative fallback for the case where the app was restarted
and has nothing to compare against. "Still moving" and "not watched long
enough" are separate answers.

**A stale premarket quote no longer produces a real-looking residual.**
Measured, not hypothesised: when the primary quote source is unavailable
the fallback returns yesterday's 4pm close as `last` and the close before
that as `previous`, which subtract into *yesterday's full-day return*
wearing this morning's label — SMH read −1.55% that way on a morning it
had not traded. Before 9:30 a quote past the configured age now produces
no gap, no residual, no percentile and no confirming call. Thin premarket
names (WDC, STX) are where an old print looks most like a live one.

**INCONCLUSIVE and RELATIONSHIP UNSTABLE are different answers.** The
first says the evidence cannot establish a direction; the second says the
relationship changed underneath evidence that may look decisive. A
direction is named only with enough matched sessions, an interval that
excludes a coin flip *on either side*, and a median that agrees with what
the count implies.

**The primary Korean driver is decided out of sample and moves
reluctantly.** Candidates are scored by expanding walk-forward on one
shared evaluation set, filtered by an absolute quality floor — because
ranking three worthless signals still produces a winner — and a challenger
must then beat the incumbent on *both* direction accuracy and error, by a
margin, sustained across matched sessions. Every decision is archived with
both sets of evidence. On live data it elects SK Hynix for Micron and
KOSPI for SMH and QQQ, refuses SNDK outright (flattering accuracy on too
short a history), and holds rather than flipping when KOSPI leads Hynix by
a tenth of a point.

**And the part that only time can supply.** `korea_capture.py` archives
the Korean state at 11:00, 13:00, 14:00, 15:00 Seoul and the confirmed
close, plus an immutable pre-open prediction record at 9:25 ET over a
fixed server-side universe. It exists to answer a question no amount of
daily history can — how early in the Korean session the information stops
improving — because a daily bar holds one number for the whole session.
Nothing is ever backfilled: a checkpoint the app missed is MISSED
permanently, a shut market is NO KOREA SESSION, and outcomes are separate
records scored against the archived prediction rather than against
today's model re-run over an old date. That last distinction is the whole
point: re-running the current model historically is a BACKTEST, and it is
never mixed into the forward rate.

## Endpoints

- `GET /api/gap` — board (status contract: scanning/scanned/total/…)
- `GET /api/gap/scan?force=1` — trigger
- `GET /api/gap/detail?symbol=` — full evidence for one symbol
- `GET /api/gap/events?symbol=` — the analog population, inspectable
- `GET /api/gap/backtest?symbol=` — walk-forward target/stop grid
- `GET /api/gap/config` — active config + hash
- `GET /api/korea_lead?symbol=&window=` — the overnight Korea panel
- `GET /api/korea_research…` — the V2 research layer (see below)
- `GET /api/korea_forward/coverage|scorecard|status` — the V2.3 forward record

Scheduler: weekdays 07:00–09:40 ET every 5 min (quote sweep ≈3 calls;
~20 candidates × 1 minute-history call + a small backfill budget, deferred
when the shared Schwab budget is above 70 req/min); post-close recording
pass at 16:10 ET.

## Strongest next improvement

After a few months of morning scans: the store will hold enough
minute-basis PM-qualified events to (1) switch the board's headline
probabilities from open-entry to time-matched premarket-entry stats at the
user's actual decision time, and (2) re-fit the signal gates from the
journal's raw-vs-displayed record and realized outcomes. Both are data
accumulation, not new machinery — the journal and store already record
everything needed.
