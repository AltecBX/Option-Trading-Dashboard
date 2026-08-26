# Premium Edge Engine (v4.20)

Finds option premium that is genuinely overpriced relative to the
volatility the underlying is **likely to realize** — then the exact
structure, expiration, and strike that best monetizes it for a given
intent, with tail risk and events priced in. Not an IV ranker: high IV
alone is never a signal here.

## What was reused (no duplication)

| Capability | Reused from |
|---|---|
| Black-Scholes, N(·), IV normalization, rank math | `metrics.py` (canonical, fixture-locked) |
| Chains + 20y daily OHLC + 110/min self-cap | `schwab_client.py` (`get_option_chain` with `to_date` gives term structure + skew + contracts in ONE call; `rate_usage()` budgets it) |
| Walk-forward harness, Sharpe/DD/Monte Carlo | `bt_validate.py` |
| Options lifecycle backtests (costs, slippage, assignment) | `bt_options.py` — the VRP threshold sweep only *generates signals*; simulation is the existing engine |
| Proxy IV series + IV/HV calibration | `bt_iv.py` (`calibrate_ratio`'s honest `calibrated (n=N)` vs `assumed` labels kept) |
| IV persistence (252-day store) | `storage._iv_history_*` — the edge scan now appends **true IV30** daily, so the legacy store fills with the real thing |
| Config pattern (repo defaults + data-dir overlay + sha256) | `timing_engine.config()` pattern, `premium_edge` section of `thresholds.json` |
| Earnings dates/board, macro schedule, VIX | `watchlist_table`, `treasury.MACRO_SCHEDULE`, `_backtest_vix_closes` |
| UI primitives | `.scan-table`, `.ab-summary`, lazy-chunk tab system, Term glossary |

New modules: `vol_forecast.py` (pure forecaster), `premium_edge.py` (pure
chain math + scoring + obs store), `edge_scan.py` (funnel/scheduler/
backtest glue), `tab-edge.jsx` (lazy chunk).

## The measurements

- **IV30** — constant-maturity 30-calendar-day IV, interpolated on **total
  variance** (iv²·T) between bracketing expirations, from liquid near-ATM
  call+put quotes (quality gates: two-sided, min bid, spread ≤35%,
  staleness only enforced while the market is open). Falls back to
  nearest-expiry, labeled. This deliberately supersedes the legacy
  `iv30_avg` (front-expiry ATM — the name lied; §27).
- **ExpectedRV30** — a *forecast*, not trailing HV: RV5/10/20/30/60,
  EWMA(0.94), Parkinson (first range-based estimator in the app), blended
  with a long-run anchor shrink. The Parkinson voice enters the blend
  **gap-calibrated** (`PARK20C`), scaled by the ticker's own trailing ratio
  of close-to-close vol to range vol. Parkinson measures only the
  high-to-low distance travelled while the market is open, so it is blind
  to overnight moves — but the target it is blended to predict, and the
  move a seller is exposed to, both include them. Across 59 names and
  26,373 out-of-sample forecasts the ratio runs at a median of **1.20**
  (p10 1.07, p90 1.39) and calibrating for it cut blend QLIKE by **6.7%**,
  on 58 of 59 names, in *both* halves of the period; forecast bias moved
  from −0.90 to +0.57 vol points (the safe side for a seller). The result
  is insensitive to the ratio window (126/252/504 all within 0.5%) and the
  clamp never binds in-sample. Uncalibrated Parkinson had read low by ~7.5
  vol points, which flattered premium. Per-ticker model choice is
  **walk-forward validated by QLIKE** (under-forecasting vol is penalized harder — the
  seller's asymmetry) and only replaces the global blend when it wins
  out-of-sample on *both halves* of the eval period by ≥5%. No lookahead —
  tests mutate future bars and prove the forecast can't change. A live
  error ledger per ticker records forecast-vs-realized every horizon.
- **VRP** — points (IV30 − ERV30), ratio, variance spread; plus the
  ex-event variants against the earnings-adjusted forecast
  (`erv30_event` adds the ticker's own MEASURED historical earnings-move
  variance). `event_share` = how much of the IV-over-forecast gap the
  earnings jump explains → **PURE / MIXED / EVENT** classification.
- **Z-score & percentiles** — against the ticker's own daily observation
  store (`<data>/edge/obs/`, one §20 record per day, 750-cap). **Below 60
  observations the Z is display-only**: its score weight shifts to the
  cross-sectional ratio and the breakdown says so in words. The store
  fills daily from the scan — the statistic matures on its own.
- **Term structure** — ATM IV per expiration + 7/14/30/45/60/90 marks,
  contango/flat/backwardation, hump detection (earnings expiry flagged).
  Structure selection targets the **richest tenor in the seller window**,
  never a hardcoded 30 DTE.
- **Skew** — ATM/25Δ/10Δ per side; **risk reversal = put − call vol pts
  (positive = puts richer)** — one documented convention (credit_risk's;
  note `perfection_data` uses the opposite sign internally).
- **Contract economics** — credit basis = BID; fair value = BS at
  ExpectedRV (r=q=0, driftless) so **EV = bid − fair − costs is the VRP in
  dollars for that strike**; closed-form 5% expected shortfall (verified
  against 200k-path Monte Carlo in tests); P(ITM)/P(touch) labeled
  `model`; premium-efficiency ratios (EV/tail, theta/collateral, credit
  per expected-move distance).
- **Structures per intent** — own_stock → covered calls; want_stock →
  cash-secured puts; premium_only → **defined risk only** (put/call credit
  spreads, iron condor). Ranked by EV per unit of tail risk, liquidity-gated.
- **Danger model** — JUICY / MIXED / DANGEROUS from measured triggers
  (earnings inside, RV acceleration, gap frequency, backwardation, big
  trend, wide spreads, thin OI, extreme skew, VIX percentile). DANGEROUS
  forces AVOID regardless of score.
- **Breach history (§17)** — MEASURED: vol-adjusted strike distances
  (k·σ20, no lookahead) checked against what actually happened per
  horizon: touch + finish-ITM frequencies vs the lognormal model — the gap
  IS the ticker's fat-tail correction.
- **EM calibration (§18)** — inside 1×/1.25×/1.5× EM rates, breach
  direction + overshoot. EM basis today is a **MODELED** σ20 proxy
  (labeled); it upgrades to MEASURED implied as stored IV accrues.

## Score, signals, honesty

Premium Edge Score 0–100 with a factor-by-factor breakdown (points,
weight, plain-English note) — weights live in `thresholds.json →
premium_edge.score_weights` and are hypotheses for the backtest, not
gospel. Signals: STRONG SELL VOL / SELL VOL / WATCH / FAIR / CHEAP VOL /
AVOID, plus **INSUFFICIENT DATA** whenever quality gates fail — the
engine prefers saying so over false precision. Every probability carries
its basis; every persisted decision carries `ENGINE_VERSION` +
config hash.

## Scanner funnel & budget

Stage 1 (free): watchlist-board screen (price/mcap/volume gates + rvol
rank + earnings proximity), split into **two slates** because one budget
of chain fetches feeds two tabs that want opposite things. The **event
slate** (`stage2_n`, 24) keeps the earnings-proximity bonus — the richest
premium lives around a report, which is Premium Edge's question. The
**seller slate** (`stage2_seller_n`, 10) holds only names with *no* report
inside `seller_horizon_days` (47 = a 45-day option plus the sell board's
2-day buffer), because an earnings report inside the option's life is an
outright exclusion for a hold-to-expiry seller. The seller slate is ranked
by each ticker's own last **MEASURED** `vrp_ratio` from the observation
store when one is ≤10 days old, and by the rvol-rank proxy only where it
is not; a stale or future-dated reading counts as no reading. Store lookups
are bounded to 3× the slate size, so a 600-name board is never 600 file
opens. Before this split the board read whatever the event slate picked,
so the earnings bonus was buying chains for names the board would then
refuse for having earnings — and reporting the result as "nothing
qualifies today". Stage 2 (budgeted): those names get ONE
chain call (95d out — IV30, term, skew, and all candidate contracts ride
in the same payload) + cached history. Stage 3 (free): deep contract
analysis. Chain calls defer while app-wide Schwab usage ≥70/min
(shared 110/min cap with juice/tape/browsing). Scheduler: every 25 min
market hours + one post-close pass that records the day's observations.
Worst case ≈ 48 requests per pass, spread out — no polling added to the
frontend beyond the tab's own 60s board refresh.

## Backtesting (§6/§21) & sizing (§22)

`POST /api/edge/backtest {symbols}` sweeps VRP-ratio entry thresholds
through `bt_options.run_portfolio` (real commissions, half-spread fills,
assignment, gaps). Per-day IV is **real stored IV30 when ≥60 matched days
exist** (labeled `measured (n=…)`), else the bt_iv proxy (labeled
`modeled`). Reported: expectancy, win rate, profit factor, worst 1%/5%
trades, ES, max DD, Sharpe, assignments, return on BP. The "robust
threshold" is the best expectancy **whose neighbors also work** —
single-point winners are treated as curve-fit. Z-score thresholds
(0.75–2.5σ, §6) activate automatically once own-history depth exists.
Sizing: `kelly_guidance` computes mean/variance Kelly **only from ≥40
real outcomes**, quarter-Kelly by default, hard-capped at 10% per
position — and refuses with the honest reason below the gate. The
sector/earnings-week exposure caps live in config for the caller.

## 0DTE integration (§19)

`timing_engine.session_variance_edge`: implied remaining variance
(iv²·T_rem on the engine's one 365-calendar clock) vs today's tape
projection (realized per-minute variance × minutes left). The edge can
nudge the timing score by at most `score_nudge_max` (6 pts) — shade,
never drive — appears in the state as `volatility_edge` (schema 1.0
additive optional field) and in the reason line when ≥20%. Disabled or
early-session → silently absent, never a block.

## Endpoints

GET `/api/edge` (board) · `/api/edge/scan` · `/api/edge/detail?symbol=&intent=`
· `/api/edge/history?symbol=` · `/api/edge/breach?symbol=` ·
`/api/edge/config` · `/api/edge/backtest?job=` — POST `/api/edge/backtest`
· `/api/edge/kelly`. All smoke-tested (96/96).

## MEASURED vs MODELED (the honest ledger)

- MEASURED: everything from underlying daily bars (all RV estimators,
  forecaster validation, breach history, EM calibration frequencies,
  earnings reaction sizes, gap stats) and live chain quotes.
- MODELED: P(ITM)/P(touch)/EV/ES (driftless lognormal at ExpectedRV —
  labeled on every row), the EM calibration's σ20 proxy basis, backtest
  IV when the store lacks coverage.
- ACCRUING: VRP Z-score/percentiles (needs ~60 daily obs; store fills
  daily; sample size shown everywhere), true-IV-history backtests, the
  0DTE session-VRP percentile.

## Limitations (data, not code)

Real IV history only accrues from chain snapshots (recording since
v3.64, 2026-07-23) and the new daily obs store — so per-ticker VRP
Z-scores, IV-percentile rank, and measured-IV backtests are honestly
thin until ~Q4 2026. The engine is built to upgrade itself as the store
fills: nothing needs re-architecting, the labels just flip.

## Strongest next improvement once option history accumulates

When ~6 months of daily IV30 + skew observations exist: (1) switch the
EM calibration and breach model columns to MEASURED implied bases, (2)
run the §6 Z-threshold sweep on real history and let it re-weight
`score_weights`, (3) train the fat-tail correction (empirical minus
model breach gap) into the P(touch) shown on contracts, per ticker.
