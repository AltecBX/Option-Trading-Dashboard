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
- **Catalyst tags**: EARNINGS (real, with BMO/AMC), OFFERING / DILUTION
  (real, SEC EDGAR — see below), UPGRADE / DOWNGRADE / ANALYST ACTION
  (real), MACRO (real), sector context (real). FDA decisions, M&A and short
  interest still have NO data source in this app and are never fabricated —
  such days are UNTAGGED. Priority is earnings → offering → rating change →
  macro; only earnings splits the statistical population, everything else is
  context shown alongside it.

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
