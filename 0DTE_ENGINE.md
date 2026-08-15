# Friday 0DTE Premium Timing Engine (v4.10)

Built to MASTER SPEC V3.0 + AMENDMENTS V3.1. An optimal-stopping,
risk-constrained premium timing system: for a short 0DTE call/put under
consideration, decide **sell now or wait** — maximizing executable credit
while P(ITM), P(touch), liquidity and assignment risk stay inside configured
limits. Phase A ships the truth layer + simulation core; learning layers
(Phase A2/B) gate on data that must first be recorded.

## Step 0 audit — what was reused (no duplication)

| Capability | Reused from | Key signatures |
|---|---|---|
| Black-Scholes (canonical) | `metrics.py` | `_bs_price/_bs_delta/_bs_gamma/_bs_theta/_bs_vega(spot, strike, T, sigma, side, r)` — T in years = days/365, theta $/calendar-day |
| IV backout / level reprice | `option_reprice.py` | untouched — daily path intact (acceptance 4-6); the intraday path is new and separate |
| VWAP + σ bands, reversal evidence, regime | `intraday.py` | `vwap_series(bars)`, `reversal_evidence(reg_bars, side)`, `market_regime(index_bars)`, `market_open(now)` |
| Minute bars (today + past days) | `schwab_client.py` | `get_intraday(sym, extended)`, `get_intraday_day(sym, date_iso)`; daily via `get_price_history` |
| Chains (atomic option+underlying snapshot) | `schwab_client.py` | `get_option_chain(sym, expiration=...)` — one call returns every contract's bid/ask/greeks/OI **plus the underlying quote in the same payload** (§3 pairing for free) |
| EOD chain snapshots | `chain_store.py` | untouched (acceptance 4) |
| Push | `options_dashboard._push_notify(title, msg, priority)` | priority=1 for timing alerts |
| Scheduled events | `treasury.MACRO_SCHEDULE` | CPI 8:30 / FOMC 14:00 ET, maintained through 2027; jobs = first Friday |
| Spread quality, atomic writes | `earnings_scan.spread_quality`, `storage._atomic_write_json` idiom | |

**Data sources & entitlements (resolved from code):** Schwab Market Data is
primary and real-time for stocks *and* options (per-contract
`quoteTimeInLong` now rides along as `quote_age_s`); the client self-caps at
**110 req/min** (`_rate_check`, sliding 60s window; new public
`rate_usage()` reads it). yfinance is delayed → under §15 it is never a live
decision input; it remains the daily-bars fallback only.
**Minute-history depth honesty:** `get_intraday_day` serves recent past
sessions; Schwab does not serve *years* of 1-minute bars, so Phase A2's
"250 sessions per ticker" pretraining is **not satisfiable from wired
sources** — the day-type/drift shading ships as a labeled SIMULATION PRIOR
(regime + gap features) instead, and the trained upgrade waits on data that
actually exists. **Persistence:** Railway volume at `/data` → `_STABLE_DIR`
(`storage.py:29`); everything the engine writes lives under
`<data>/timing/` and survives redeploys. **Clock (Step 0.5):**
`check_clock()` measures drift from HTTPS Date headers at boot + hourly;
drift beyond `clock.max_drift_seconds` (40s) hard-blocks decision states.

## The one-clock ruling (§1 vs §12)

Everything — IV backout, pricing, P(touch), decay, simulation steps — runs
on ONE clock: `T_years = seconds to session close / (365·86400)` (the
app-wide metrics.py convention; early closes honored via the maintained
calendar in `thresholds.json → session`). §12's `252×390` trading-clock tau
is normalized to this same clock: price and touch probability consume only
σ√T, which is invariant to the clock **as long as backout and consumption
share one** — mixing clocks is the silent misscaling the spec forbids, and
`test_clock_convention_invariance` proves the equivalence.

## What shipped (files)

- **`timing_engine.py`** — §1 math (`time_to_expiry_years`,
  `reprice_intraday`, `implied_vol_intraday`, `iv_usability`,
  `touch_probability`), §9 seeded Monte Carlo (`simulate` — every quantity
  from ONE path set; extrinsic anchored to the live bid; drift/range
  shading; event widening), §12 analytic priors alongside, §10
  decomposition + scenarios, §14 three resting limits with fill stats off
  the same paths, §13 tranching, §17/§18 score→states with one-line
  reasons, §34 hysteresis (margins ≥ 2× MC standard error, Amendment B),
  §29 intents (admissibility + breach cost only — Amendment C), §31
  attention-constrained capture + alert policy (budgeted, dollar-ranked,
  names the action), §33 final-hour mode + after-hours exercise watch, §30
  portfolio rollup (shared shock, intent-matched views — Amendment D), §20
  management states + always-on 2× tripwire, §4 fill logging with full
  decision-state capture, §5 post-trade metrics (both admissibility
  variants + divergence alarm), §26 `replay_day` diagnostic, §35 decision
  log (model version + config hash + inputs + seed) with `replay()` diff,
  `validate_state()` against `engine_state.schema.json`.
- **`intraday_option_store.py`** — the tape (§2/§3): tiered collector (P1
  30s candidates/on-screen, P2 delta 0.10–0.50 free in the same chain
  call, discovery 300s Fridays), session benchmarks (raw/mid/executable
  highs + **Durable Executable High defined in seconds** — rolling
  max-of-window-minimums, Amendment E), budget accounting off
  `rate_usage()` with the written degradation ladder
  (discovery→P2→P1), retention (full tape for trailing 8 sessions, older
  compacted to session.json + event windows), crash-safe JSONL appends,
  ET timestamps.
- **`thresholds.json`** — every tunable (§35); data-dir copy overrides
  key-by-key; sha256 stamped on every decision; read-only in tweaks panel.
- **`engine_state.schema.json`** — versioned output contract (extend-only).
- **`timing.jsx`** — the Trade-tab card (§19): decision-first hero (state,
  score, confidence, layer badge, reason, WHAT CHANGED), credit/probability
  metrics, hazardous-premium warning, intent toggle, resting/chase fill
  logger, expandable Premium/Underlying/Risk/Decomposition sections,
  final-hour strip, portfolio shared-shock strip, diagnostics (engine+tape
  status, Aug 14 replay button), tweaks read-only thresholds.
- **`test_timing.py`** — 40 tests mapping the §27 acceptance list (see
  below). `test_http_smoke.py` covers all 16 new endpoints.
- `schwab_client.py` — additive only: `rate_usage()`; chain rows gained
  `bid_size/ask_size/occ/quote_age_s`.

## Endpoints

GET `/api/timing/status · state · contracts · config · replay?id= ·
post_trade · replay_day?day= · tape/status · fills` — POST
`/api/timing/candidates · fill · intent · portfolio · manage · replay_day ·
clock_check`. Schedulers (boot, `serve()`): tape collector keeper (60s
wake; collector runs market hours only, idles at 20 req/min projected
load), engine evaluator+alerts (60s, only when candidates expire today),
boot clock check.

## Budget math (§3 deliverable)

One chain call per tracked symbol per P1 cadence covers every tracked
contract AND the P2 delta neighborhood AND the paired underlying quote.
Worst realistic Friday (6 P1 symbols @30s + SPY/QQQ minute bars @30s
amortized + evaluator chains served from the 30s TTL cache):
**≈ 14–16 req/min** against a 30 req/min configured slice of the 110/min
self-cap — >30% headroom. Ladder: >21 req/min app-wide → discovery pauses;
>25 → P2 to 120s; >28 → P1 to 60s. Storage: ~40 delta-neighborhood rows ×
2/min × 390 min ≈ 6–8 MB/symbol/Friday raw (measured estimate surfaced in
`tape/status`); trailing 8 sessions full, then compacted — bounded.

## Acceptance tests (§27) → where proven

1 minute-accurate T `test_two_hours_not_zero_days` · 2 bid-not-last
`test_credit_is_bid_not_last` · 3 benchmark separation
`test_four_benchmarks_stay_separate` · 4-6 untouched modules (audit +
existing suites) · 7 admissible metrics `post_trade_report` (both
variants) · 8 `test_p_itm_and_p_touch_are_separate` · 9 calibration
labeled uncalibrated Phase A · 10 no training shipped in Phase A · 11 P1
cadence 30s · 12 `test_stale_quote_blocks` · 13
`test_on_strike_never_high_score` · 14 full app battery green (455 py +
node + 87/87 smoke) · 15 `test_joint_coherence` · 16
`test_replay_byte_identical` + `test_decision_logged_and_replayable` · 17
`TestHysteresis` (4 tests) · 18 ladder `test_degradation_ladder` · 19
`test_intent_changes_admissibility_and_wait_edge` · 20
`test_shared_shock_sums_per_leg` · 21
`test_final_hour_pennies_recommendation` · 22 `test_clock_drift_blocks`.

## Aug 14 replay (§26)

Run from the card (diagnostics → "Run Aug 14 replay") on the deployed app,
where Schwab still serves that day's minute bars. Underlying-side facts
(touch times, minutes beyond strike, closing distance, the AMD 3:30pm
final-hour state, the 1pm shared-tape portfolio view) are **MEASURED**.
Premium-side values are **MODELED** — IV backed out of *your fill* (a real
datum) and held flat — because no intraday option tape existed that day;
the spec forbids pretending otherwise, and every such number carries the
label. The tape this release records makes future replays MEASURED.

## What the spec asked that Phase A does not ship (stated, not hidden)

- **Phase A2 pretraining** (extreme-timing/day-type models on 250 sessions
  of minute bars): blocked on data depth (audit above). Shading ships as a
  labeled prior; the §24 promotion gate machinery (bt_validate walk-forward
  + cluster-aware errors) is the planned harness once tape accumulates.
- **Learned corrections (Phase B)**: off by design until effective sample
  gates pass; every number is badged SIMULATION PRIOR / HEURISTIC /
  MEASURED accordingly (§21).
- **§36 regret attribution**: needs weeks of fills+tape; `post_trade`
  already records the per-fill buckets it will aggregate.

## Next highest-value improvement after this ships (§28)

Wire the broker positions feed (`/api/broker/positions`) into automatic
leg discovery so the portfolio strip and management states appear with
zero manual entry — then the §36 weekly regret attribution once 3–4
Fridays of tape + fills exist.
