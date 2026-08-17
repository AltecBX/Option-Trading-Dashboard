# Premarket Gap Fade & Rebound Scanner (v4.30)

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
| — *below here a share sale explains the morning better* — | | |
| OFFERING · DILUTION | 424B/S-3/S-1 + cover page, 8-K item 3.02 | one read |
| MERGER VOTE | DEFM14A / PREM14A | free |
| DEAL CLOSED | 8-K item 2.01 | free |
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

## Still missing, on purpose

These move stocks and have **no source wired into this app**. They are
listed so nobody mistakes an UNTAGGED day for a quiet one:

- short-seller reports (no feed; they are published on the author's site)
- index inclusion / deletion (S&P, Russell rebalances)
- guidance changes announced at conferences rather than in an 8-K
- product launches, partnerships and contract wins (item 1.01 is far too
  broad to tag without reading every one)
- short interest, borrow rates and float — deferred at spec time (§36) and
  still deferred
- foreign private issuers, which file 6-K with no item codes at all

## Walk-forward discipline

`/api/gap/backtest?symbol=` sweeps targets (1–5%) × stops (1–5%) over the
symbol's measured paths, splits events chronologically, and marks a pair
`robust` only when BOTH halves have positive expectancy — judged by EV and
worst outcomes, never win rate. The 2%/3% defaults in the scanner columns
are starting rules; the grid exists to challenge them per ticker.

## Endpoints

- `GET /api/gap` — board (status contract: scanning/scanned/total/…)
- `GET /api/gap/scan?force=1` — trigger
- `GET /api/gap/detail?symbol=` — full evidence for one symbol
- `GET /api/gap/events?symbol=` — the analog population, inspectable
- `GET /api/gap/backtest?symbol=` — walk-forward target/stop grid
- `GET /api/gap/config` — active config + hash

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
