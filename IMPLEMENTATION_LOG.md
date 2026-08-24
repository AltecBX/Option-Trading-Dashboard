# Implementation Log — production upgrade

Working baseline: `main` @ 989b51d (classic v3.63 + HANDOFF_AUDIT.md).

## Phase 1 — Verified baseline (before any change)

- **Python tests:** all 9 suites pass (`test_earnings_scan`, `test_strategy_math`,
  `test_reprice`, `test_iv_history`, `test_v115_storage`, `test_v116_push`,
  `test_v117_broker`, `test_failure_modes`, `test_http_smoke`). Two of them
  require pandas/numpy (installed into the work env; production has them via
  yfinance).
- **JS tests:** `test_recommendation` 30/30, `test_journal` 26/26, `test_weather` 24/24.
- **verify_frontend.js:** Layer 1 + Layer 2 PASS.
- **Artifact sync:** rebuild (`build_frontend.js` + `build_next.js`) produced a
  0-file git diff → committed `.js` matches `.jsx` sources.
- **Asset sizes (bytes, uncompressed):** app-cards.js 1,013,587 · styles.css
  430,907 · app.js 423,340 · charts.js 101,049 · strategies.js 69,743 ·
  next-app.js 34,897 · tweaks-panel.js 19,342 · tooltips.js 15,096 · data.js
  13,604 · app-lib.js 11,056 · recommendation.js 9,897 · journal.js 4,184 ·
  weather.js 3,410 · config.js 902. **Total JS+CSS 2,116,117 B (~2.0 MB).**
- **Initial requests from index.html:** 16 `<script>`/stylesheet tags
  (12 local scripts + styles.css + React, ReactDOM, lightweight-charts from unpkg)
  plus Google Fonts.
- **Static cache headers (measured):** `Cache-Control: no-store, no-cache,
  must-revalidate` + `Pragma: no-cache` on every static asset; **no gzip** on
  static files (only JSON endpoints gzip). Repeat visits re-download ~2 MB.
- **Static exposure (measured):** `/options_dashboard.py`, `/storage.py`,
  `/Procfile` all served HTTP 200 — whole CWD is exposed.
- **/api/ticker size:** not measurable in this sandbox (upstream providers
  blocked → HTTP 500 fallback path exercised instead). Endpoint already
  gzips >1 KB bodies and carries ETag; last-good cache serves stale on error.
- **Console/runtime:** verify harness reports single mount, no errors; prior
  Playwright runs this session showed no JS errors on Trade/Treasuries/EarnOps.
- **Production frontend:** classic (`index.html`). Evidence: Procfile boots the
  classic server root; every release v3.47→v3.63 targets classic; `/next`
  frozen at 4.0.5-next with no commits since; deployed domain serves classic.

## Phase 2 — Static asset pipeline + deployment security (v3.64 groundwork)

- **Build (`build_frontend.js` v2.0):** three stages — (1) JSX→readable .js as
  before (verify harness unchanged); (2) esbuild-minify all 11 served scripts +
  styles.css into `dist/*.min.*` with pre-compressed `.gz` siblings; (3) read
  APP_VERSION from app.jsx and stamp every `?v=` in index.html + point local
  tags at dist. **Version bumps are now: edit APP_VERSION, run build.** Node
  toolchain declared in new `package.json` (dev-only; deploy machines never
  run node — dist/ is committed).
- **Sizes:** raw 2,046K → minified 1,305K → **gzip transfer 313K** (app-cards
  985K→630K→167K; app 413K→225K→55K; styles 412K→316K→49K).
- **Server static layer (options_dashboard.py):** replaced the
  SimpleHTTPRequestHandler whole-directory fallthrough with an explicit
  allowlist (`_serve_static`/`_send_file`): HTML no-cache; config.js no-cache;
  `dist/*` + `assets/*` → `public, max-age=31536000, immutable`, served
  pre-gzipped; everything else 404 (all .py/.jsx/.md/json state/Procfile/.git
  denied). `do_HEAD` routed through the same allowlist (the inherited handler
  leaked any file's existence/size). Blanket `end_headers` no-store removed;
  responses without an explicit policy still default to no-store.
- **CORS:** default is now **no ACAO header** (same-origin only). ALLOWED_ORIGIN
  env enables a specific origin. `*` on a public bind is refused at startup.
- **Startup gate (`check_deploy_security`):** non-loopback bind without API_KEY
  exits with a clear error (override: DANGEROUSLY_DISABLE_AUTH=1). Loopback dev
  unaffected.
- **Tests:** new `test_security.py` — 20 tests: allowlist serving, immutable+gzip
  headers, python/state/git/traversal/HEAD denial, auth on/off, CORS default
  and configured, startup gate matrix. All pass. `test_http_smoke` OK,
  verify_frontend PASS.
- **Measured after:** `/dist/app.min.js` → 200, immutable, gzip 56,494 B
  (was 423,340 B no-store); `/` → no-cache; `/options_dashboard.py` → 404
  (was 200).

## Phase 3 — Options-math contract (v3.64)

- **One canonical Black-Scholes (`metrics.py` v2.0):** documented contract
  (T = days/365, sigma decimal, theta $/calendar-day, vega $/vol-point,
  explicit `r` + continuous dividend `q`, exact erf CDF). `option_reprice.py`
  and `backtest.py` now delegate — the audit's 4 divergent copies are 1
  implementation + 1 fixture-matched JS mirror.
- **Live risk-free rate:** `metrics.risk_free_rate()` → (rate, source);
  wired to `treasury.risk_free_3m_cached()` (peek-only — pricing never
  blocks on network). Unavailable → labeled `fallback constant 4.00%
  (live curve unavailable)`, never a silent constant. backtest's default
  rate resolves through it.
- **Expected-move vocabulary:** `/api/expected_move` now returns `method`
  ("atm_straddle" | "one_sigma_iv"), `method_label`, and BOTH measures
  (`straddle_dollars/pct`, `one_sigma_dollars/pct`). ExpectedMoveCard
  displays the method actually used plus a "Straddle vs 1σ" comparison
  row; no more silent substitution.
- **Iron-fly POP fixed (`juice.py`):** `2·Φ(credit/x)−1` now divides by the
  true 1σ (S·σ·√T) instead of the ATM straddle (≈1.25σ) — the old formula
  understated POP ~8–10 pts. Every juice strategy POP carries `pop_basis`
  ("delta" | "one_sigma") and the UI labels each ("POP (delta est.)" /
  "POP (1σ est.)") with P(ITM)-vs-P(touch) tooltips. Juice `iv_rank` now
  carries `iv_rank_src` ("iv_history" | "hv_proxy").
- **One shared rank:** `metrics.rank_and_percentile` replaces three
  divergent copies (storage `_iv_history_compute_rank`, ivrank
  `_vol_metrics`, options_dashboard inline HV rank) + the inline
  hv-percentile in build_expected_move. Payloads carry sample size
  (`volRankN`, `rank_n`, `iv_rank_days`).
- **HV-proxy labeling:** scanner tab/card renamed "HV Rank (IV rank proxy)",
  sidebar pill "HV rank" with n and a proxy-explainer tooltip; true IV rank
  (stored IV30 history) keeps the name "IV rank" and now shows its n.
- **Modeled backtests:** structured `result.modeled` block (assumptions
  incl. BS-on-HV20 premiums, spread/commission model, next-bar fills) +
  prominent MODELED badge on the results header.
- **Beta verified:** Treasuries UI labels Pearson correlation as
  "Correlation vs Δ10y" and the rate-sense "β per +10bp" is a genuine
  regression slope (cov/var, `treasury.py:1230`) with n + t-stat +
  confidence — audit flag #5 was fixed by the v3.60–62 rebuild; verified,
  no change needed.
- **Cross-language fixtures:** `fixtures/options_math.json` (435 cases,
  generated from metrics.py by `fixtures/generate_math_fixtures.py`);
  `test_math_contract.py` (21 tests: fixtures, cross-module identity,
  parity, conventions via finite differences, normalize_iv/rank/1σ edges,
  rate plumbing, juice POP); `test_strategy_fixtures.js` (17 checks: JS
  engine matches the same fixtures, worst |Δprice| 1.3e-4 on a $1025
  underlying, payoff identities, break-even dedupe fix).
- **Tests:** all suites green — 11 Python suites OK (test_failure_modes
  8/9: the 1 failure is `/api/ticker?symbol=AAPL` 500 because Yahoo is
  unreachable from this sandbox — reproduced as a curl TLS reset inside
  yfinance, not a code path), JS 97/97, verify_frontend Layers 1+2 PASS,
  rebuilt dist stamped v3.64.

## Phase 4 — Frontend performance (v3.64)

- **Lazy tab chunks:** Treasuries (1,359 lines), Earnings Ops, Pattern
  Discovery and Backtest Lab split out of app-cards.jsx into
  `tab-*.jsx` → `dist/tab-*.min.js`, loaded by a new `loadChunk`/`LazyTab`
  layer (app-lib.jsx) on first tab activation, cached for the session,
  version-locked to the app bundle's `?v=`, skeleton loading state +
  retry-able failure card, existing CardErrorBoundaries preserved.
  **Measured (Chromium):** initial load fetches 0 chunks; each chunk
  fetched exactly once on first open, no refetch on revisit; no JS
  errors. app-cards.min.js 632K→496K (gz 168K→133K); 38K gz now
  on-demand. Build + verify harness extended to compile/lint/load the
  chunks in their real (post-load) order.
- **Sidebar sliders:** new memoized `SliderTuner` (app.jsx) — weeks /
  target-delta / buffer sliders keep value LOCAL during drag and commit
  on release (+250ms debounce fallback). Dragging no longer re-renders
  the whole App per tick.
- **/api/ticker:** now always fetched at weeks=52; the weeks slider
  slices client-side (rows are newest-first; data.js buildWeekly already
  sliced). **Measured (Chromium):** slider dragged through 4 values →
  0 additional /api/ticker requests (was 1 full ~payload refetch per
  commit). Bonus: all weeks settings share one browser-LRU and one
  server-TTL cache entry per symbol. Existing AbortController stale-
  ticker cancellation + ETag/gzip verified in place.
- **sharedJson v2:** stale-while-revalidate (serve ≤4×TTL-old data
  instantly, refresh in background), refresh skipped while the tab is
  hidden, 300-entry LRU bound. `/api/watchlist_table` confirmed to flow
  through one shared source (6 consumers, network-coalesced).
- **Bounded boards:** shared `useBoundedList` (top-150 + "show more/all"
  + honest shown-count) applied to the three ~600-row scanner boards
  (Analyst, HV Rank, Trend). Watchlist table already had 120-row
  incremental windowing (kept). Range scan already capped (kept).
- **Tests:** JS 97/97, verify Layers 1+2 PASS, security+math 41/41 OK.

## Phase 5 — Duplication removal + fork decision (v3.64)

- **Rails 4 → 1:** LeftRail52W / LeftRailDailyHigh / RightRail52WLow /
  RightRailDailyLow (four ~110-line near-clones) replaced by ONE
  parameterized `ExtremeRail` + a `RAIL_CFG` table. All DOM classes
  unchanged (existing CSS untouched); shared logic (owned-highlight,
  live-quote overlay, viewport measure, seamless scroll) exists once.
- **Honest strategy economics (`economicBounds`):** `pnlBounds` is now
  documented as VISUAL (chart-axis) only; new `OptionStrats.economicBounds`
  derives the true tail behavior — net call+stock slope > 0 → max profit
  unbounded; < 0 → max loss unbounded; downside always finite (scanned to
  S=0, so a CSP shows its real strike−credit worst case).
  **Real bugs fixed:** (1) the P/L card's "Max profit" could show a
  window-edge dollar figure for unlimited-upside strategies (pnlBounds
  over a finite window is never Infinity — the "unlimited" branch was
  dead); (2) the Position-sizing card sized undefined-risk structures to
  the window-edge loss AND multiplied the per-contract loss by 100 again
  (legs carry qty=±100, so pnlAt is already per-contract) — contracts
  were ~100× understated; (3) "/sh" labels on per-contract values → now
  "/ contract" everywhere in the P/L + custom-builder cards.
  12 new fixture tests (long call, CSP, short strangle, covered call,
  iron condor) in test_strategy_fixtures.js (now 27 checks).
- **`/next` fork removed:** next-app.jsx, next-app.js, next.html,
  next.css, build_next.js deleted (production is classic — P1 evidence;
  the server route was already gone in P2 and test_security proves
  non-allowlisted files 404). No references remained.
- **Breakpoint ladder:** 22 scattered media-query values normalized to a
  documented 6-step scale (1100 / 900-mobile-mode / 760 / 620 / 520 /
  380) via EXPANSION-only merges (640/680/700/720→760, 800→900,
  560/600→620, 480→520) + documented specials (1080/1081 pair, wide-
  desktop 1200-1400, ultra-wide min-1601/2080, 950px landscape,
  reduced-motion). **Measured (Chromium):** zero horizontal overflow at
  1200/900/760/750/700/620/520/390px, mobile chrome engages ≤900,
  no JS errors.
- **Tests:** JS 107/107 (30+26+24+27), verify PASS, security+math 41 OK,
  http smoke 50/50.

## Phases 6–9 — Cohesion, mobile, CI, schedules (v3.64)

- **Opportunity ribbon (P6):** one compact line under the context bar —
  the strongest current setup from each ALREADY-CACHED scanner board
  (watchlist flow edge · HV rank · earnings ops · range location). Pure
  reads only: juice/radar deliberately excluded because their GET
  endpoints start background scan workers. Each chip shows the SOURCE
  scanner's own number (no synthetic blend), the tooltip spells out the
  inputs, boards >20 min old are labeled "Nm old", demo earnings data
  never qualifies, and an explicit "no current setups / scanners idle"
  state renders when nothing clears threshold. Chip click → the source
  tab. Verified in Chromium: chips + stale label + thresholds + tab
  navigation, no JS errors; horizontally scrollable on phones.
- **Earnings cross-links (P6):** Market Calendar and the Earnings-vol-
  crush card now link straight to the Earnings Ops scanner ("Earnings
  Ops →"); Earnings Ops rows already open Analyze — the earnings surfaces
  now navigate into each other instead of dead-ending.
- **Mobile (P7):** verified at 390/520/620/700/750/760/900px — zero
  horizontal overflow, fixed bottom bar, drawer nav, ribbon scrolls
  horizontally; initial JS on phones reduced by the P4 lazy chunks
  (4 heavy tabs load only when opened).
- **CI (P8):** `.github/workflows/ci.yml` — py_compile all modules;
  rebuild + `git diff --exit-code` artifact-sync gate (deploys never run
  node, so drift = broken deploy); verify harness; JS tests (incl. the
  cross-language BS fixture match); the full deterministic Python battery
  with `JERRY_NO_NET=1` (skips the single test needing live Yahoo). No
  paid keys anywhere.
- **Maintained schedules (P9):** CPI/FOMC tables moved into
  `treasury.MACRO_SCHEDULE` with explicit `valid_through`/`updated` and a
  `schedule_status()` gate. When expired: `_events()` nulls the dates and
  flags `needs_update` (never recycles stale dates), the Fed card gets
  `schedule_note`, and the UI (events card, overview minis, fed chip)
  shows "Schedule requires update". New `test_schedules.py` (7 tests)
  FAILS when the schedule is expired or within 21 days of expiry — the
  repo nags before the UI ever degrades — plus structure/coverage checks
  and frozen-clock expiry-behavior tests.
- **Tests:** unittest 48 OK (math/security/schedules) · py runners
  123/123 · JS 107/107 · verify PASS.

# Backtest v2 (BACKTEST_UPGRADE.md)

## B1 — Options lifecycle engine

- **`bt_options.py`** — the single leg-based simulator: 9 structures
  (short_put/CC/strangle/IC/credit spreads/iron fly/long legs; wheel and
  rolls as linked chains), strike-by-delta solver, management rules
  (profit-take % of credit, stop ×credit, DTE exit, roll-at-DTE),
  assignment model (American intrinsic floor — found+fixed the European
  deep-ITM negative-extrinsic artifact; deep-ITM early exercise; ex-div
  call assignment; expiry settlement + pin-risk flag; trader exits
  checked before overnight assignment), per-leg spread f(mid, DTE,
  moneyness) + commissions/fees, portfolio simulator with broker-formula
  buying power, one-position-per-symbol continuous entry, and a DAILY
  mark-to-model equity curve (open-position pain included — v1 counted
  only realized P/L).
- **Grammar v2** (backtest.py): structure names, "take profit at 50% of
  credit", "stop at 2x credit", "exit/roll at 21 dte", "wings at 5
  delta", "skip/only earnings week" (real per-symbol earnings dates via
  load_earnings_history — replaces the old "can't test earnings"
  rejection). Span-based number-ownership so "exit at 21 dte" can't
  overwrite the structure's 45 DTE (found+fixed), and "short strangle"
  no longer trips the legacy long-puts conversion.
- **Routing**: structure strategies → `_run_structure_portfolio`
  (liquidity gate, earnings filter with loud-skip when dates missing,
  IV series = HV20×1.1 floored at 8% — degenerate-vol delta-targeting
  guard added after a fixture caught 0-IV selling ITM puts as "30Δ"),
  daily curve swapped into the result, new metrics (assignments, avg
  return on BP), structured modeled-assumptions block.
- **UI**: premium-selling presets first in the examples row; trade table
  renders structure trades (credit, legs tooltip, ×contracts); Avg-ret-
  on-BP and Assignments tiles.
- **Tests**: test_bt_options.py — 28 tests (builders, every management
  path, assignment incl. ex-div, defined-risk bounds, roll/wheel chains,
  BP + max-position gating, mark-to-model curve identity, grammar → end-
  to-end runs on a stubbed client, earnings filter, legacy path intact).
  Full battery green: 69 unittest + runners + JS 107 + smoke 50/50.

## B2 — IV realism + self-building real-quote layer

- **`bt_iv.py`** — layered, LABELED IV model replacing flat HV20×1.1:
  0.6·HV20+0.4·HV60 base × per-symbol IV/HV ratio (CALIBRATED from the
  app's own stored daily IV30 snapshots when ≥20 matched obs — clamped
  0.8–2.0 — else 1.10 "assumed") × VIX-percentile regime scaler
  (0.85–1.20, flat 1.0 + labeled when Yahoo is out) × earnings ramp
  (+35% into known report dates over the final 7 days, crush after),
  floored 8%. Result carries per-symbol calibration provenance.
- **Premium-sensitivity harness** (unique vs every named platform): each
  options run repeats at 0.85×/1.15× IV; result carries the P/L band +
  a verdict ("edge survives the full band" / "edge FLIPS SIGN at the
  pessimistic assumption — that's a premium assumption, not an edge").
- **`chain_store.py`** — EOD chain snapshots: every chain the app fetches
  (juice/ticker/EM — recorder hooked at the schwab_client choke point,
  once per symbol per day, ≤75 DTE, ±30% strikes, 500-day retention,
  atomic). Engine precedence: entry fills use REAL bid/ask when a
  same-day snapshot exists (leg snapped to the real listed strike,
  collision-guarded); trades labeled real_quote/mixed/modeled; result
  reports real-fill %. Accuracy compounds with normal app use.
- **Wiring**: providers injected (iv_history_fn, vix_fn cached 6h,
  earnings_fn); modeled-assumptions block now states the layered model +
  provenance. UI: PREMIUM SENSITIVITY verdict band + real-fill line.
- **Tests**: test_bt_iv (11 — calibration recovery/clamping, VIX
  percentile mapping, ramp shape + crush, floor, warm-up honesty),
  test_chain_store (8 — round-trip, throttle, DTE/strike lookup, wrong-
  day rejection, engine precedence real/model/collision). CI extended.
  Battery: 88 unittest OK + JS 107 + smoke 50/50.

## B3 — Validation suite

- **`bt_validate.py`** (pure, seeded, no network):
  · `monte_carlo` — 10k-path trade-order bootstrap → maxDD/final-equity
    P5/P50/P95 + risk-of-ruin at the tested sizing (deterministic seed).
  · `walk_forward` — rolling IS/OOS folds over an injected runner, WFE +
    per-fold table + verdicts ("consistent" / "weaker" / "does NOT hold
    out of sample — likely curve-fit").
  · `psr`/`deflated_sharpe` — Bailey & López de Prado: observed Sharpe
    vs the expected best-of-N-trials hurdle. Tests PIN the honest
    behavior: a 3.0 Sharpe over 3 years clears a best-of-50 hurdle, the
    SAME Sharpe over 1 year does not (estimator noise).
  · `plateau_score` — parameter-grid 3×3-neighborhood robustness;
    recommends the ROBUST cell, not the lucky peak.
  · `regime_matrix` + `vol_terciles` — trend × VIX-tercile cells with a
    ">70% of the edge in one regime" concentration warning.
  · `sharpe_from_curve` — Sharpe/Sortino/CAGR + skew/kurtosis off the
    daily mark-to-model curve.
- **Routing integration** — every structure run now automatically gets:
  Sharpe/Sortino/CAGR in metrics, Monte Carlo, 4-fold walk-forward
  (re-running the real portfolio per fold, real-quote precedence
  preserved), regime matrix (when VIX data present), benchmarks (SPY
  buy-and-hold same window, T-bill carry at the labeled live/fallback
  rate, vs strategy return), and Deflated Sharpe (n_trials=1 baseline;
  when the opt-in `rules["optimize"]` grid runs — delta × DTE × PT,
  capped 48 combos — DSR uses the true trial count and trial-Sharpe
  variance, plus plateau scoring of the grid).
- **Tests**: test_bt_validate (17) incl. WF consistent-vs-curve-fit,
  seeded-MC determinism + ruin detection, DSR trial penalty, plateau vs
  lone-peak. End-to-end smoke: full payload (MC/WF/DSR/optimizer/
  benchmarks/sensitivity) on the stub client. CI extended.
  Battery: 112 unittest OK + JS 107 + smoke 50/50.

## B4 — Tear sheet, scorecard, trade replay, A/B (tab-backtest chunk)

- **Validation scorecard** — chip row of REAL statistics (no blended
  score): sample size, walk-forward WFE + OOS-positive folds, Monte
  Carlo P95 drawdown + risk-of-ruin, Deflated Sharpe, premium-band
  sign-survival, regime concentration, real-fill share. Tooltips carry
  the full meaning of each number.
- **Tear sheet** — drawdown underlay beneath the equity curve (mark-to-
  model, open pain included), monthly-returns heatmap, per-trade P/L
  histogram with zero-line, MAE/MFE scatter from each trade's daily
  marks (labeled daily-resolution), breakdown tables (by exit reason,
  by symbol, trend×vol regime cells with the concentration warning,
  walk-forward fold table, benchmarks strip, optimizer grid with
  ★robust vs ·peak rows).
- **Trade replay** — click any structure trade → its full lifecycle:
  daily open-P/L path (labeled MODELED marks) with event dots
  (open/profit-take/stop/roll/assigned/expired), legs with real-fill •
  markers, BP, prev/next stepping.
- **A/B comparison** — pin a run's headline metrics, run a variation,
  get a per-metric delta table (direction-aware coloring).
- **Verified in Chromium** against a REAL engine payload (37-trade CSP
  run with full validation fields): 5 scorecard chips, 17 monthly
  cells, 12 histogram bars, 37 MAE/MFE dots, 4 breakdown tables,
  replay open/step, A/B pin — zero JS errors. All inside the lazy
  tab-backtest chunk (no initial-load cost).

## B5 — Research → live trading plan + adherence loop

- **`bt_plans.py`** — plan objects persisted to /data: the tested rules
  as a human ENTRY CHECKLIST (grammar conditions → plain English),
  Monte-Carlo-derived sizing guidance (scale so MC P95 drawdown ≤15% of
  the account), the validation evidence snapshot (WF/DSR/sensitivity
  verdicts, n, win rate, DD), and a PERMANENT not-automation statement.
  Adherence: journal trades carrying `plan_id` are split from off-plan
  trades with per-plan n/win-rate/P&L (and the honest note when
  off-plan beats plan).
- **Endpoints**: GET /api/plans (plans + adherence), POST /api/plans
  (create from a completed result), POST /api/plans/status
  (archive/reactivate). Journal tagging rides the existing
  /api/trade_journal (extra `plan_id` field).
- **UI (tab-backtest chunk)**: "deploy as plan →" on any completed
  result; LIVE TRADING PLANS panel — checklist, evidence chips,
  suggested allocation, LIVE adherence line, log-trade-against-plan
  form, archive/reactivate; the not-automation line rendered on every
  plan card.
- **Verified**: full API round-trip on the real handler (create →
  checklist derived → journal trade tagged → adherence 220.0 computed →
  archive) + Chromium render (checklist, chips, adherence, log form,
  zero JS errors). test_bt_plans (4 tests: checklist derivation, MC
  sizing, persistence round-trip, adherence math). CI extended.
- **Backtest v2 final battery: 116 unittest + smoke 50/50 + JS 107 +
  verify both layers — all green.**

# v3.65 — Credit Risk monitor (the honest no-Bloomberg CDS view)

- **Reality stated up front**: single-name CDS quotes are OTC dealer data
  (S&P Global/Bloomberg/ICE) — no free source exists; nothing is faked.
- **`credit_risk.py`**: Merton structural model — bisection solver for
  (asset value, asset vol) from market cap + rolling HV60 + KMV default
  point (short + ½·long-term debt; 75% of total when split unavailable);
  5Y model spread in bps, distance-to-default, risk-neutral PD; DAILY
  series over the past year; crash-put skew gauge (25Δ risk reversal +
  annualized ~20%-OTM put cost) from live chains; honest interpretations
  incl. the negligible-leverage case (NVDA: model ≈ 0 → watch skew +
  sector indices instead).
- **Endpoint** `/api/credit_risk?symbol=` — Merton series (Schwab bars +
  yfinance balance sheet, hourly cache), live skew, and REAL traded ICE
  BofA IG/BBB/HY OAS via FRED (verified live from the sandbox: HY 284
  bps +10/1m, IG 81, BBB 100 as of 2026-07-28). Every component labels
  itself unavailable rather than estimating.
- **CreditRiskCard** (Analyze tab): big model-spread number + 1y trend
  chart + DD/PD/leverage + LIVE CREDIT FEAR (options) + REAL TRADED
  CREDIT strip with 1-month deltas + permanent model-not-quote note.
  Browser-verified, zero JS errors.
- **Tests**: test_credit_risk (14) — solver roundtrip against the Merton
  call formula, realistic-regime monotonicity (with the extreme-leverage
  vol-compensation effect documented), joint price-drop+vol-spike
  widening, NVDA-style negligible-debt → ~0 bps, KMV default point,
  widening series direction, skew-gauge math. CI extended. APP_VERSION
  3.65. Battery: 83 unittest slice + smoke 50/50 + JS 107 + verify PASS.

# v3.66 — Options Playbook (best/worst performers × premium richness)

- **The ask, encoded literally**: "buy calls and buy puts, or sell calls
  and sell puts — it just depends where the premiums are." One board now
  answers it per name: strong + cheap premium → BUY CALLS · weak + cheap
  → BUY PUTS · strong + rich → SELL PUTS · weak + rich → SELL CALLS
  (defined-risk call credit spread first — naked calls flagged as
  unlimited risk).
- **`trend.py`**: rows now carry realized returns over 1w/1m/3m/6m/~1y
  (`_returns`, strict windows — a short history yields None, never a
  shrunk window relabeled; ~1y uses the full downloaded year when ≥240
  bars). This makes "best/worst performing stocks" a direct sort.
- **`playbook.py`** (new): PURE JOIN of three already-cached boards —
  Trend (direction/strength/returns) × HV-Rank (premium rich vs cheap,
  the documented realized-vol proxy for IV rank) × watchlist table
  (sector, market cap, earnings dates). Never fetches, never starts a
  worker, never fabricates: names missing from either scanner are
  excluded AND counted (`excluded`), enrichment fields stay None when
  absent. Quadrant at HV-rank 50; conviction = 60% trend strength + 40%
  premium edge (|rank−50|×2) — transparent, both inputs shown per row.
  Context flags: earnings ≤9d (event premium/vol crush), vol
  expanding/contracting worded per SIDE (tailwind for buyers vs "early
  for sellers"), overbought/oversold, 52wk high/low.
- **Endpoints**: `/api/playbook` (pure read, ETag incl. scan progress) +
  `/api/playbook/scan` (explicit trigger for BOTH source scans — they
  serialize on HEAVY_SCAN_LOCK).
- **PlaybookCard** — new first sub-tab Discover › Playbook: best/worst
  performer strips over a selectable window (1w/1m/3m/6m/~1y), four
  quadrant boxes with conviction chips, filters (play, min conviction,
  earnings ≤9d hide/only, search), full table with play pills + flag
  chips, row click → Trade tab for the real chain/IV/premiums. Honest
  proxy note pinned on the card. Opportunity ribbon gained a PLAYBOOK
  chip (top conviction ≥70, earnings-soon excluded) — still pure-read.
- **Tests**: test_playbook (12) — the 4-cell quadrant matrix, rank-50
  boundary, conviction bounds/monotonicity, join exclusion counts,
  watchlist enrichment + earnings flag, side-dependent vol-trend
  wording, empty-source guidance, sources passthrough, malformed-row
  skipping, strict return-window math. CI + smoke extended
  (`/api/playbook` → 51/51). APP_VERSION 3.66.
- **Browser-verified** (real Chromium, real bundles, payload generated
  through the real `assemble()`): desktop + 390px — 4 quadrant boxes,
  2 perf strips, 16 rows, 16 play pills, 7 earnings flags, ribbon chip
  "PLAYBOOK UNH SELL CALLS 77", zero JS errors, zero horizontal
  overflow; phone table stacks into cards.
- **Battery: 142 unittest + smoke 51/51 + JS 107 + verify both layers —
  all green.**

# v3.67 — Priced for Perfection (pre-earnings module)

- **The question**: how much future success is already in the price, and
  can the stock fall even on strong results? NOT a company-quality score.
- **`perfection.py`** (pure, versioned MODEL v1.0): 7 weighted components
  (hurdle 25 / valuation 20 / expectations 15 / reactions 15 / momentum
  10 / crowding 10 / conversion 5), bands 0-24/25-49/50-69/70-84/85-100,
  reverse DCF (bisection, roundtrip-tested) for implied revenue CAGR +
  required FCF margin with configurable horizon/discount/terminal/margin,
  winsorized own-history percentiles, documented anchors where no
  distribution exists, missing components renormalize (contributions
  reconcile exactly; sub-40%-coverage components excluded, not shown
  thin), confidence High/Medium/Low/Insufficient from weighted coverage −
  freshness penalties (<50% → NO composite shown), whisper pathway with
  source-confidence weighting (full/half/excluded) — no legitimate free
  whisper source exists so the slot ships EMPTY and a separately-labeled
  MARKET-IMPLIED HURDLE (reverse-valuation derivation) is shown instead,
  "Consensus is not the hurdle" warning (≥3 conditions), 6-scenario
  matrix from the stock's own event analogs (ranges only with ≥4 samples,
  median±1.5·MAD), explanation layer (top-3 up / top-2 down from real
  signals), Good-News Saturation from stored beat/fade data, deep
  sanitize pass (json allow_nan=False contract).
- **`perfection_data.py`**: adapters over EXISTING providers only —
  yfinance (info, quarterly/annual statements with aligned-TTM windows
  that skip the provider's partial newest column, earnings_dates with
  report-clock AMC/BMO detection, eps_trend/eps_revisions/estimates,
  get_shares_full, recommendations/upgrades) + Schwab (live quote, chain:
  earnings-expiry straddle implied move, 25Δ skew, OI walls, 5%-OTM put
  cost) + stored IV history (percentile) + watchlist board (sector
  peers). Per-group TTL caches; every fetch guarded → data_issues, never
  fabricated. Trailing EV/S & P/E percentile series rebuilt from daily
  price × actual share count ÷ quarterly filings (labeled approximation).
  Point-in-time: append-only pre-earnings snapshots
  (data/perfection/SYM.jsonl, one/day); per-event vs-implied comparisons
  ONLY from own stored snapshots (accumulate forward, never backfilled).
  Reactions use session-correct cutoffs (BMO=same day, AMC=next trading
  day) on split-safe auto-adjusted closes, relative to SPY + sector ETF.
- **`/api/perfection?symbol=&horizon=&discount=&terminal=&margin_target=`**
  (assumptions clamped server-side). JERRY_NO_NET guard.
- **PerfectionCard** (Analyze tab, above Credit Risk): score badge +
  classification + confidence + coverage + Unprotected-Long-Risk label +
  countdown + AMC/BMO + as-of + snapshot dot; one-sentence summary; "Why
  this score?" expander (top factors + formula + reconciliation ✓);
  warning/saturation banners; 7 expandable component rows (score bar,
  effective vs base weight, contribution) with full detail grids,
  signals, sources+timestamps; execution-hurdle detail has the
  assumptions editor (re-solve server-side) + sensitivity + What-Must-Go-
  Right checklist; whisper box states "No reliable whisper estimate
  available" + market-implied hurdle; reaction detail shows the 10-event
  table with fade/miss highlighting; options & expected-move panel
  (explicitly OUTSIDE the score); scenario matrix; limitations expander;
  disclaimer. Mobile: single-column KV grids, compact component rows.
- **Tests**: test_perfection (34) — composite/reconciliation/renorm,
  confidence bands incl. Insufficient→no score, freshness penalties,
  reverse-DCF roundtrips + monotonicity + guards, percentiles/winsorize,
  whisper full/half/excluded + multi-source + never-fabricated, reaction
  classification/saturation/<8 events/scenario analogs+range gating,
  warning conditions, NaN/inf/zero-div hygiene (allow_nan=False), BMO/AMC
  reaction-day rule, snapshot future-leak protection, options panel
  outside the score. CI + smoke extended (52/52).
- **Live validation (sandbox, real data)**: `validate_perfection.py AMD
  SNDK` — AMD 53.9 Elevated (High conf, 100% coverage; EV/S 71st pctile,
  +132%/90d expansion, revisions +6.2%/30d, 5 of 9 beats faded →
  saturation; price implies 57.2%/yr vs consensus 58.8% blend — hurdle
  moderate); SNDK 37.6 Moderate (High conf; price implies LESS than the
  memory-boom consensus). Values are data-determined, nothing hardcoded;
  structural checks (reconciliation, weight sums, no-fabricated-whisper,
  sources present) all pass. Bugs found & fixed by live validation: tz
  clash in share-history alignment, single-year consensus growth →
  FY0/FY1 geometric blend, provider's partial newest quarterly column →
  aligned TTM windows, FCF row fallback for spin-offs.
- **Browser-verified** (real Chromium, real bundles, real AMD payload):
  desktop + 390px — header/meta complete, why-box, saturation banner, 7
  components, assumptions editor, whisper honesty box + market-implied
  hurdle, 10-event reaction table (5 fades highlighted), options panel,
  6-row scenario matrix, zero JS errors, zero horizontal overflow.
- **Battery: 176 unittest + 8 runner suites + smoke 52/52 + JS 107 +
  verify both layers — all green.** APP_VERSION 3.67.

# v3.68 — Whisper/expectation source adapters (Priced for Perfection)

- **Extends** the v3.67 whisper slot with real source adapters. The model,
  scoring and UI were NOT rebuilt — the confidence-weighted whisper input
  the model already accepted is now fed by live providers.
- **`whisper_sources.py`** (new): adapters + aggregation.
  - **Earnings Whispers** — the public, unauthenticated JSON its own pages
    call, discovered by reading the site's `cal.js`/`stocks.js` bundles:
    `/api/getstocksdata/{SYM}` (pre-event whisper, consensus EPS,
    consensus revenue, next earnings date, release-time slot→BMO/DMT/AMC,
    confirm flag) and `/api/epsdetails/{SYM}` (last reported event: actual
    vs estimate vs the whisper that stood, high/low estimate range) — the
    latter is POST-EVENT history only, never offered as an upcoming
    whisper. 6h cache, ≥2s/domain rate limit, 2 retries with backoff on
    timeouts/5xx only (4xx never retries), strict ticker/shape validation,
    whisper-vs-provider-consensus sanity check (garbage discarded WITH a
    visible validation note), HTML-instead-of-JSON → `layout_changed`.
  - **WhisperNumber** — TLS does not verify from this deploy and the data
    sits behind registration. We do NOT disable verification and do NOT
    automate a logged-in session (credentialed scraping of subscription
    data). Adapter attempts a plain public read, reports
    `unreachable`/`no_public_data`; quick link + manual entry is the path.
  - **Seeking Alpha** — public endpoints answer with a PerimeterX
    challenge (403). No bot-detection evasion is attempted; reports
    `blocked`. Its consensus/calendar content is already covered by the
    app's yfinance estimates. A `consensus_only` parse path exists and is
    tested should access ever become legitimate.
- **Aggregation**: every value labeled by KIND — `provider_whisper` /
  `community_estimate` / `consensus_only` / `user_supplied`. Multiple
  sources → median + range + count (never a silent pick). Confidence
  ladder: fresh provider whisper → high; stale provider or user entry
  WITH a source URL → medium; unattributed user entry → low; dispersion
  >10% caps at medium, >20% → low. Conflict handling: when a provider's
  consensus diverges >15% from the app's published consensus, the
  divergence is surfaced AND the headline gap switches to same-provider
  pairs so numbers are never mixed across vintages (`gap_basis` shipped).
- **Point-in-time**: snapshots appended once per source per UTC day to
  `data/whisper/snaps/{SYM}.jsonl`; `whisper_for_event` only accepts
  snapshots dated STRICTLY BEFORE the event and collected FOR it — a
  post-earnings revision can never become "what was expected". Manual
  entries bind to the upcoming event at entry time and are excluded from
  events that already happened.
- **Endpoints**: `GET /api/whisper?symbol=` (per-source values, statuses,
  quick links, manual entries, conflicts) and `POST /api/whisper/manual`
  (source attribution + at least one value required). Both JERRY_NO_NET-safe.
- **UI**: `PfWhisperSources` inside the expectations component —
  per-source table (kind badge, whisper/consensus EPS, consensus revenue,
  earnings date+session+confirmed, as-of, clickable source URL), live
  per-provider status chips with plain-English tooltips, conflict banner
  with the gap basis, quick-open links for all three, and the
  source-attributed manual entry form (posts + refetches with a
  cache-busted key).
- **BUG FOUND BY LIVE DATA & FIXED**: composite bands are declared with
  INTEGER bounds (0-24, 25-49, 50-69, …) but scores are fractional — CSCO
  scored 69.4 and fell between "50-69" and "70-84", displaying as **Low**
  when it was Elevated. `_band_of` now treats each band as [lo, hi+1);
  regression tests sweep all 1001 tenths for both the composite and ULR
  ladders and assert label monotonicity.
- **Tests**: `test_whisper_sources` (29) on SAVED FIXTURES captured from
  the real provider (`fixtures/ew_*.json`) — whisper present, whisper
  absent but consensus present, session mapping, 204/HTML/wrong-ticker
  layout changes, sanity-check rejection, 5xx retry vs 4xx no-retry,
  cache hit, last-event history, SA blocked + consensus-only path, WN
  unreachable/no-public-data/public-parse, manual roundtrip + validation +
  event binding, post-event exclusion, snapshot leakage guard, once-per-
  day snapshots, collect() confidence ladder, conflict basis switch,
  dispersion capping, no-net guard, JSON safety, and an end-to-end model
  integration asserting the collected whisper gets full weight and fires
  the warning. CI + smoke extended (54/54).
- **Live verification**: CSCO (reports in 4d) — Earnings Whispers $1.20 vs
  consensus $1.17 = **+2.7% gap, high confidence**, "Consensus is NOT the
  hurdle" FIRED citing it; AMD (87d out) — no whisper published yet, panel
  says so honestly while still collecting consensus + calendar. Browser-
  verified desktop + 390px: source table, kind badges, three status chips,
  three quick links, working manual form, zero JS errors, zero overflow.
- **Battery: 207 unittest + 8 runner suites + smoke 54/54 + JS 107 +
  verify both layers — all green.** APP_VERSION 3.68.

# v3.69 — Priced for Perfection: larger, more readable type

- **Why**: the card was hard to read on the dark theme — most of its type
  sat at 9-12.5px, well below the app's body size.
- **Every one of the 56 `font-size` declarations** in the card's CSS
  (v3.67 model card + v3.68 whisper panel) raised by exactly 2px. Score
  badge 30→32, summary 13.5→15.5, component rows 12.5→14.5, KV grids
  12→14, whisper/source table 11.5→13.5, kind badges 8.5→10.5, status
  chips 10→12, scenario table 12→14, sources/disclaimer 10.5→12.5, mobile
  score 24→26.
- **Layout adjusted to fit the larger type** (no clipping): component-row
  fixed columns widened at all three breakpoints (42/56/52/20 → 50/64/62/24
  desktop, and proportionally on tablet/phone), assumption inputs 58→66px,
  KV grid min column 230→260px.
- **Card header**: `.kicker` 13px, `.card-title` 20px (18px on phones).
  These required `!important` ONLY to clear the app-wide mobile clamps
  (`.kicker`/`.card-title` at 10px/16px !important) — scoped to `.pf-card`
  so no other card in the app changed. Documented inline.
- **Verified in DARK theme** (the reported context), desktop 1700px and
  phone 390px: every computed size confirmed at its new value, no element
  spills the card, no page-level horizontal overflow, zero JS errors.
- **Pre-existing issue observed, NOT introduced and NOT fixed here**: at
  ~1500px viewport widths the profit-calculator drawer (`.pcalc-panel`,
  outside this card) sits off-canvas and creates page horizontal overflow —
  measured 460px on the v3.68 build vs 443px on this one, so this change
  slightly reduced it. Flagged for a separate fix.
- Battery: 207 unittest + smoke 54/54 + JS 107 + verify both layers — all
  green. APP_VERSION 3.69.

# v3.70 — column tooltips, Earnings Calendar load feedback, Simply Wall St link

## 1. Column tooltips (Priced for Perfection)
- Every column header in the card's three tables now explains itself:
  events table (Date, Session, EPS est→act, Surprise, **1d, 5d, vs SPY**),
  expectation-sources table (Source, Type, Whisper EPS, Consensus EPS,
  Consensus rev, Earnings, As of), scenario matrix (Scenario, Bar cleared,
  Risk, Compression, Historical analog). Each says what the number IS and
  how to read it (e.g. 1d = last close BEFORE the announcement → close of
  the first session that could trade on it; vs SPY = that move minus the
  index's same-day move).
- **App-wide sweep NOT done and deliberately not faked**: 189 bare `<th>`
  remain across app.jsx (37), app-cards.jsx (48), tab-treasuries (90),
  tab-backtest (12), tab-earnops (2) — 107 distinct labels. A shared
  label→text glossary was considered and REJECTED: identical labels mean
  different things per table ("1D" is a stock reaction here, a yield change
  in bps in Treasuries), so a global map would ship confidently wrong
  tooltips. Correct coverage needs per-table context; flagged for a
  dedicated pass, tab by tab.

## 2. Earnings Calendar populate time
- **Real bug fixed**: `_bulk_earnings_map` docstring claimed "cached 15
  min" but NO `@_ttl_memoize` decorator was ever applied — every cache miss
  re-paginated the whole upstream feed (up to 40 sequential requests, 100
  names each). Now `@_ttl_memoize(30 * 60)`; scheduled earnings dates
  barely move intraday, and the caller keeps its own 10-min cache for the
  fresher enrichment layer.
- **Honest first-load UI**: while the first fetch is in flight the card
  showed empty rails reading "No watchlist names report today." / "No
  earnings this week." and a grid of "—" — i.e. it looked broken. Now a
  spinner banner explains what is being fetched, the rails say "Loading…",
  and the day columns show shimmer skeletons. A manual ↻ refresh keeps the
  existing rows on screen instead of blanking back to skeletons
  (`firstLoad = loadingE && !earn`). Reduced-motion safe.

## 3. Simply Wall St in the Sites row
- **`site_links.py`** (new) builds the deep link. Their URLs are
  `/stocks/{country}/{sector}/{exchange}-{ticker}/{name}` and only the
  `{exchange}-{ticker}` segment is derivable from a ticker, so: country +
  exchange from an EXCHANGE_MAP over the yfinance exchange code; sector
  from SWS_SECTOR keyed on **industry first** (NVDA's segment is
  "semiconductors" = its industry, while SNDK's is "tech" = closer to its
  sector), then sector, then "tech"; company slug from the name with legal
  suffixes and dangling connectors stripped ("JPMorgan Chase & Co." →
  jpmorgan-chase, "Nestlé S.A." → nestle via dotted-initialism collapsing).
- `GET /api/site_link?site=simplywallst&symbol=` — resolves from the 12h
  cached profile. When the exchange or company name is missing it returns
  NO url plus the reason, rather than guessing a company slug.
- UI: a "Simply Wall St ↗" entry on the Sites row (desktop) and in the
  mobile sections sheet, following the global ticker. **External, not
  embedded** — simplywall.st refuses third-party framing and non-browser
  traffic, so an iframe panel would render a blank box; the tooltip says so.
- **Limitation stated in-module**: simplywall.st blocks this deployment
  entirely (403 to requests, connection reset even from real Chromium), so
  the slug scheme could not be verified end-to-end — only reproduced from
  the two known-good URLs. `verified: false` ships in the payload.
- **Live validation**: driving the resolver with REAL yfinance profiles
  reproduces both reference URLs EXACTLY (NVDA → /us/semiconductors/
  nasdaq-nvda/nvidia, SNDK → /us/tech/nasdaq-sndk/sandisk).
- Tests: `test_site_links` (19) pins both reference URLs as exact matches,
  plus slugify/sector/exchange/missing-data/URL-shape cases.

- Battery: 226 unittest + smoke 55/55 + JS 107 + verify both layers — all
  green. Browser-verified in dark theme. APP_VERSION 3.70.

# v3.71 — app-wide column tooltip sweep

- Completes the sweep deferred in v3.70. **169 column tooltips added**
  across every remaining table in the app, written per table rather than
  from a shared glossary (the v3.70 note stands: "1D" means a stock
  reaction in one table and a basis-point yield change in another, so a
  global label→text map would have shipped confidently wrong text).
- Coverage by file: app.jsx 34/37 titled, app-cards.jsx 109/121,
  tab-treasuries.jsx 85/90, tab-backtest.jsx 12/12, tab-earnops.jsx 4/4.
- Tables covered: earnings implied-vs-realized ladder; weekly cycle
  backtest; the full option chain (bid/ask/IV/delta/theta/vol/OI on both
  sides, plus the CALLS/STRIKE/PUTS group headers and the leg +/− buttons);
  swing projected targets; most-similar past moves; swing history up and
  down legs; Options Playbook; weekly range-location scan; breadth stock
  lists; Treasury yield curve, curve spreads, inflation expectations, CPI
  release reactions (all 13 columns incl. each ETF proxy), Treasury ETFs,
  auctions (bid-to-cover, indirect/direct/dealer takedown, read), Fed
  implied path, COT positioning, rate-sensitivity scan, and all five
  overview strips; backtest regime + trade blotter; earnings-ops board.
- **20 headers deliberately left untitled and verified as intentional**:
  12 empty spacer cells (nothing to explain), 5 already covered by the
  existing `<Term>` glossary component (adding `title` too would fire two
  tooltips at once), and 3 dynamic headers that already build their own
  title. Two of those dynamic ones were UPGRADED from placeholder text —
  the breadth list's `title="Sort"` now describes each column, and the
  earnings-ops Ticker/Price/MCap headers gained real descriptions instead
  of the "Sort by X" fallback.
- Applied by a verifying script (scratchpad `apply_tips.py`): every
  insertion is matched to its expected header label first and the run
  aborts on any mismatch, so no tooltip can land on the wrong column.
- Verified: build + both verify layers pass; rendered audit shows Earnings
  Ops 16/16 and Trade 19/22 titled (the 3 being the option chain's empty
  spacers); tooltip strings confirmed present in the minified
  dist/app.min.js, dist/tab-treasuries.min.js and dist/tab-backtest.min.js.
  The Treasuries tab could not be rendered in this sandbox (its data
  endpoints use yfinance's curl_cffi TLS path, which this environment
  blocks) — its coverage is confirmed by static analysis plus the
  artifact check.
- Battery: 226 unittest + 8 runner suites + smoke 55/55 + JS 107 + verify
  both layers — all green. APP_VERSION 3.71.

# v3.72 — fix: Simply Wall St link permanently greyed out

- **Root cause**: the resolver depended on yfinance `.info`, which calls
  Yahoo's `quoteSummary` endpoint. That endpoint now answers **401
  "Invalid Crumb"** without a session crumb, so `.info` returned nothing,
  the company name and listing exchange were both missing, and the
  endpoint correctly refused to guess a URL — leaving the chip disabled
  forever. Reproduced exactly against the real server:
  `{"url": null, "reason": "missing listing exchange and company name"}`.
- **Fix — layered light sources**, `_symbol_listing()` (12h cached):
  1. Yahoo **chart** `meta` → company + fullExchangeName (cheap, reliable)
  2. Yahoo **search** → sector + industry + company + exchange — the only
     light endpoint that carries sector/industry; verified 200 while
     quoteSummary 401s
  3. `.info` last, to fill any remaining gap
  4. the watchlist board row (no network) for sector/industry
- **`site_links.merge_profile()`** (new, pure + tested) combines those
  sources FIELD BY FIELD, taking the first non-empty value for each, so a
  single dead upstream can never blank the whole link. It records which
  source supplied each field and ships that as `derived.field_sources`.
- **UI: never a dead chip.** The disabled state is gone; when a lookup
  comes back empty the chip stays clickable, shows a ⟳ mark, and CLICKING
  RETRIES the resolution (cache-busted) and opens the page on success.
  Tooltip states the reason and says "click to retry".
- **Verified live**: NVDA → /us/semiconductors/nasdaq-nvda/nvidia (exact
  reference match restored), SNDK → /us/tech/nasdaq-sndk/sandisk (exact),
  plus JPM → banks, XOM → energy, AMD → semiconductors, CSCO → tech.
  Rendered check: chip at full opacity, correct href, ↗ mark, no
  "unresolved" class, zero JS errors.
- Tests: +4 in test_site_links (23 total) covering field-level fallback,
  the exact regression (an all-None source must not win any field), URL
  build from merged sources, and junk/None source handling. Also moved the
  `__main__` guard to the end of the file so a direct
  `python3 test_site_links.py` run executes every class.
- Battery: 230 unittest + smoke 55/55 + JS 107 + verify both layers — all
  green. APP_VERSION 3.72.

# v3.73 — Simply Wall St as a real in-app Sites panel (no more new tab)

## Investigation first — two independent blockers, both measured
- Direct iframe: simplywall.st app pages send `X-Frame-Options: SAMEORIGIN`
  (the homepage does not; `/stocks` and `/markets/us` do).
- Server-side reverse proxy: every `/stocks/*` path answers **403 with a
  Cloudflare managed JS challenge** ("Just a moment…", `__cf_chl`,
  `challenges.cloudflare.com`) — 5/5 consecutive attempts, with full browser
  headers, a warmed session carrying `__cf_bm`, and across every variant
  tried: `/health`, `/past`, `/amp-v2`, bare `{exchange}-{ticker}`,
  `/quote/…`, `/stock-quotes/…`, category pages, `api.simplywall.st`, and
  the sitemap targets. `robots.txt` also disallows `/api/*`. So no
  server-side retrieval, HTML rewriting or asset proxying can work without
  auto-solving the challenge — bot-detection circumvention, not built.

## The solution the architecture already had
The app ships a **Site Helper extension** whose `declarativeNetRequest`
rules strip `x-frame-options`/`content-security-policy` on SUB-FRAME
responses — that is how TradingView and Unusual Whales already embed.
Applied to simplywall.st this solves both blockers at once: the header
rule removes the frame block, and the Cloudflare challenge is irrelevant
because the frame is fetched by the USER'S OWN BROWSER with their normal
cookies, exactly as a normal tab.

- **`finviz-helper` → v2.8**: rules.json rule 4 (simplywall.st sub-frames),
  host_permissions, `sws-sync.js` content script, third-party-cookie
  exception so the site session + Cloudflare clearance cookie survive in
  the frame, and simplywall.st added to the watched-cookie list.
  Deliberately NOT added to REWRITE_DOMAINS (the SameSite rewrite is the
  path that once broke TradingView logins). README + zip rebuilt.
- **`sws-sync.js`**: reads the ticker from
  `/stocks/{country}/{sector}/{exchange}-{ticker}/{name}` and posts
  `jth-sws-ticker` up to the dashboard — the same two-way sync contract as
  uw-sync.js. Regex verified against 8 real URL shapes.
- **`SWSTPanel`**: same card, toolbar, sizing, Follow toggle, ↺/Reload,
  quick links and hint line as the UW panel. `swst` joins the `EXT` map so
  it renders in the Sites row and the mobile sheet next to the other three.
- **External link REMOVED**: `SwsLink`/`SwsSheetLink` deleted; zero
  `target="_blank"` links to simplywall.st remain (asserted in the browser).

## Robustness fixes found by testing
- **Stale-frame guard**: a failed re-resolve used to leave the PREVIOUS
  company on screen while the header showed a different ticker. The panel
  now tracks `srcSym`, dims the frame (`.sws-stale`), says "Showing X —
  still switching to Y", and surfaces resolve errors even when an old frame
  exists. One automatic retry on transient failure.
- **Resolver latency**: the endpoint no longer calls the slow, rate-limited
  `.info` when the light Yahoo endpoints already supplied company +
  exchange + sector. Resolution went from timing out to ~1s cold and ~1ms
  cached (12h server cache).
- **ETFs handled**: SPY is detected as an ETF and returns no deep link with
  a clear reason (Simply Wall St's `/stocks/` space is listed companies),
  and the panel offers Retry / Browse stocks instead.

## Verification
- Resolver, all 10 requested tickers: NVDA, SNDK, AAPL, TSLA, AMD, MSFT,
  META, AMZN, PLTR all resolve to their correct company page; SPY returns
  the ETF notice.
- Browser, desktop 1700px AND mobile 390px: **9/10 correct on both**
  (SPY = the ETF notice, by design), clicking through all ten tickers in
  sequence; zero `target="_blank"` simplywall links; zero JS errors.
- A first pass showed the frame lagging; instrumenting proved the
  `/api/site_link` requests were issued but got no response — head-of-line
  blocking on the browser's 6-connection pool, saturated by this sandbox's
  crippled yfinance endpoints (curl on the same server answered in 1ms).
  With the other endpoints stubbed the panel tracked every ticker
  correctly, confirming the panel logic — and the stale-guard above means
  even a slow resolve can never show the wrong company as if it were right.
- Battery: 230 unittest + smoke 55/55 + JS 107 + verify both layers.
  APP_VERSION 3.73.

## What the user must do once
Install/refresh the Site Helper (v2.8) — the same one-time step Finviz,
TradingView and Unusual Whales already require. Without it the frame is
blocked by simplywall.st's X-Frame-Options, exactly as it would be for the
other three.

## v3.73a — helper download link on the Simply Wall St panel
The panel told users it needed Site Helper v2.8 but offered no way to get
it (Finviz/TV/UW panels all carry the link). Added a gate on the panel:
when the stamped helper version is < 2.8 it explains why the frame is
blocked, shows the detected version, and links `/finviz-helper.zip`
(served by the dashboard, 200 / 17,645 bytes / application/zip) with the
unzip-and-reload instruction. It clears itself the moment the extension
announces v2.8 — no page reload.

First attempt called `FINVIZ.helperVersion()`, which does not exist
(`helperVersion` lives on TVIEW) — the CardErrorBoundary caught it and the
tab rendered "FINVIZ.helperVersion is not a function". Rather than couple
Simply Wall St to TradingView's config, SWST got its own `helperVersion()`.
Verified desktop 1500px + mobile 390px: no crash, link present and
downloadable, notice clears on v2.8, zero JS errors. Also corrected the
stale "Site Helper v2.7" chip tooltip to v2.8.

## v3.73b — the helper download was unreachable for anyone already set up
Reported: "I don't see Download helper anywhere on this app." Not a deploy
lag — a real pre-existing gap. Every link to /finviz-helper.zip was gated:
- the Finviz setup card renders ONLY in the `else` branch of `if (helper)`,
  i.e. only when no helper is detected at all;
- the TradingView block is inside its helper-missing/outdated branch;
- the per-panel "update helper" chips fire only below 2.1 / 1.4 / 2.3.
So a user already running v2.7 passed every gate and saw NO download link
anywhere in the UI — there was no way to obtain a newer helper from the app.

Added `HelperDownloadChip`: always present in the Sites row (and the mobile
tab sheet), never version-gated. Shows the installed version, turns warning-
coloured with "→ 2.8" when behind, and carries the unzip + ↻-reload
instructions in its tooltip. Verified desktop 1500px and mobile 390px at
three states — no helper ("⤓ Helper — → 2.8", stale), v2.7 ("⤓ Helper v2.7
→ 2.8", stale) and v2.8 ("⤓ Helper v2.8", not stale) — exactly one visible
chip per viewport, zero JS errors.

(A first test run reported the version always as "—" and a JS error; that
was the harness setting `document.documentElement.dataset` in an init script
before the document element existed, not the app.)

## v3.73c — AMZN 404: stop guessing, start learning
Reported: AMZN showed Simply Wall St's "Something went wrong / page was not
found". The embed itself was fine — the derived URL was wrong.

Only the `{exchange}-{ticker}` segment of a Simply Wall St URL is derivable
from a symbol. The country, sector and company-name segments are THEIR
slugs, and `simplywallst_url()` has always returned `verified: False`
because their pages cannot be fetched server-side to check a guess
(Cloudflare). AMZN is where the guess broke.

Two changes:
1. `slugify()` now collapses a dot INSIDE a word instead of treating it as a
   separator: "Amazon.com, Inc." → `amazoncom` (was `amazon-com`). Also
   fixes Booking.com, Salesforce.com. Still a derivation, not a verification.
2. The real fix — the panel LEARNS. `sws-sync.js` (helper v2.9) now reports
   `location.pathname` alongside the symbol, so whatever page you actually
   land on becomes the remembered address for that ticker
   (`jerry_sws_paths_v1` in localStorage, bounded to 400 entries, company
   root only — tab suffixes like /health are stripped). A learned path beats
   the derived one and needs no server call at all. This is the only channel
   that can see the truth: the frame runs in the user's browser, which
   Cloudflare lets through.
   - "Find {TICKER}" chip opens Simply Wall St's browse page so a wrong guess
     is one click from recovery — and the frame then teaches the app.
   - "✓ learned" chip shows when a path was learned, with the stored path in
     its tooltip; clicking forgets it and reverts to the derived link.

Verified: frame reports real path → stored + "✓ learned" appears; on reload
the learned path is used with ZERO /api/site_link calls; clicking "✓ learned"
clears the store and falls back to the derived URL. Zero JS errors.
23 site-link tests + 64 unittest + 27 JS + both verify layers.

NOTE: I cannot confirm AMZN's true slug from here — Cloudflare blocks every
server-side fetch of their stock pages. `amazoncom` is a better derivation,
not a verified fact. The learning mechanism is what makes it not matter.

## v3.73d / helper v3.0 — the framed Simply Wall St login didn't stick
Reported: AMZN now resolves correctly, but signing in with Google inside the
frame works and then the page asks to log in again on scroll.

Cause, and it was my omission. The helper has THREE cookie mechanisms and I
only gave Simply Wall St the first:
  1. third-party cookie exception (contentSettings)  — SWS had it
  2. SameSite=None rewrite                            — SWS did NOT
  3. cookie-header fallback for browsers that strip
     frame cookies entirely (Comet/Brave)             — SWS did NOT
Their session cookie is SameSite-restricted, so the browser refused to send
it from inside the frame: the login succeeded, and every subsequent request
arrived anonymous. I had left SWS out of (2) out of caution after the
TradingView incident, but TV is the special case (surgical allow-list); the
blanket rewrite is exactly what makes finviz and unusualwhales logins work.

helper v3.0: simplywall.st joins REWRITE_DOMAINS, gets cookie rule id 9004,
and is added to the webRequest watch + reload channel. Its Cloudflare cookies
are explicitly NOT rewritten — SKIP_COOKIE already excludes cf_clearance /
__cf* / _cfuvid, which are what let the page load at all.

VERSION TRAP: helper versions are compared with parseFloat, so "2.10" reads
as 2.1 — BELOW 2.9. 2.9 is therefore followed by 3.0, not 2.10.

New permanent test `test_helper_cookies.js` (wired into npm test) runs the
REAL background.js in a vm against a stub extension API and asserts both
directions for every embedded site: session cookies ARE rewritten, anti-abuse
and Cloudflare cookies are NOT, partitioned and already-cross-site copies are
skipped, TradingView keeps its allow-list and stays out of REWRITE_DOMAINS,
every site has both mechanisms, and rule ids are unique. 25 assertions.

HONEST LIMIT: I cannot verify the end-to-end Google login myself — it needs a
Simply Wall St account in a real browser with the extension loaded, and
Cloudflare blocks server-side access. The cookie rules are unit-tested; the
login flow needs your confirmation.

## v3.74 — the real reason none of v3.73a/b/c/d ever reached the browser
Reported three times: "I don't see the Helper chip." It was merged, CI-green
and deployed each time. The cause was cache busting, not deployment.

`build_frontend.js` stamped every asset URL with APP_VERSION:
`dist/app-cards.min.js?v=3.73`. v3.73, v3.73a, v3.73b, v3.73c and v3.73d ALL
carried APP_VERSION "3.73", so the URL never changed and every browser kept
serving the bundle it cached on the FIRST v3.73 deploy. The user got the
Simply Wall St tab (shipped in v3.73) and none of the follow-ups — and no
amount of merging or redeploying could ever have reached them.

Fixed at the root: the `?v=` marker is now an 8-char sha1 of each built
file's own bytes. Change a file → only that file's URL changes; change
nothing → URLs are stable, so the build stays idempotent and warm caches are
not needlessly thrown away. A release no longer depends on remembering to
bump a version string. Verified: hashes differ per file, a real source edit
busts only that file, a comment-only edit correctly busts nothing (identical
minified bytes), and two consecutive builds produce byte-identical HTML.

Also fixed, found while testing: the helper chip's LABEL hard-coded "→ 2.8"
while `LATEST` was a separate constant, so every version bump changed the
tooltip and left the visible text stale — the exact same class of bug as the
cache-buster (two sources of truth for one value). LATEST is now a single
string used by both, and a string rather than a number because `3.0` as a
Number renders as "3".

Verified the chip renders alongside the earnings chip — both use
`margin-left: auto` in the same flex row, which I had never tested together:
no overlap at 1980px or 1180px, correct order, same row. APP_VERSION -> 3.74.

## v3.74a — the panel advertised the wrong helper version
With v3.74 deployed the chip finally appeared, and it exposed that the
panel's banner still read "needs Site Helper v2.8+ ... Download helper v2.8"
while the zip it links to is v3.0. A user on v2.7 was told to install 2.8 —
which renders the frame but does NOT fix the login, the thing they were
actually trying to do.

- Two named thresholds instead of one: SWS_NEED_VER (2.8, renders at all) and
  SWS_LOGIN_VER (3.0, login persists). The banner now shows the honest state
  for each: below 2.8 "needed to render here AND to keep you logged in";
  between 2.8 and 3.0 an explicit "the page renders, but a login here will
  NOT stick — their session cookie is SameSite-restricted and the browser
  won't send it from inside a frame until v3.0". It clears at 3.0.
- The download link and label read from one SWS_LATEST string.
- Version LABELS now use SWST.helperVersionLabel() — the raw dataset string —
  because helperVersion() parses to a Number for comparison and "3.0" as a
  Number renders as "3" (the chip read "⤓ Helper v3").

Verified at v2.7 / v2.9 / v3.0: correct notice for each, link reads
"Download helper v3.0", chip reads "⤓ Helper v2.7 → 3.0" and "⤓ Helper v3.0",
notice absent at 3.0, zero JS errors.

## v3.75 / helper v3.1 — Storage Access, and a diagnosis you can click
v3.0 shipped and the login still did not stick. Then I asked the user to
hand-run a console snippet in a specific DevTools frame context, which was an
unreasonable thing to ask — diagnosing my own feature is not their job.

Why v3.0 was not enough: SameSite=None makes a cookie ELIGIBLE to be sent
cross-site, but Chrome blocks third-party cookies by default, so the frame is
handed a partitioned jar and the cookie is dropped anyway. The helper tries to
grant an exception via chrome.contentSettings.cookies, which is deprecated in
current Chrome — it then logs "relying on SameSite upgrade only" and does
nothing. That is the exact state a modern Chrome user lands in.

1. STORAGE ACCESS (the fix candidate). sws-sync.js now calls
   document.requestStorageAccess() on the first click/keydown in the frame —
   the standards-blessed way for embedded content to get its own first-party
   cookies. v2.5 shipped this app-wide and v2.7 removed it because it reloaded
   the frame on EVERY click, losing unsaved TradingView work. This cannot
   repeat that: simplywall.st only, `asked` set BEFORE the async call so rapid
   clicks can't double-fire, at most one reload ever (sessionStorage guard),
   and no reload at all when access was already granted.
2. DIAGNOSE LOGIN (the data). A button in the panel asks the frame what it can
   actually see and renders the answer with a Copy button. Key and cookie
   NAMES only — never values, never page content — and the frame only answers
   a request from a dashboard origin. It distinguishes the two candidate
   causes: no cookies + no keys = partitioned/blocked storage; storage access
   granted but login still dropping = the token lives somewhere the grant
   doesn't cover (partitioned localStorage), which no extension can fix.
   A helper older than 3.1 cannot answer, and the panel says exactly that
   rather than showing an empty result.

Verified end-to-end in a browser against the REAL sws-sync.js served into the
frame: the diagnostic round-trips and renders a verdict + JSON + Copy; with no
script in the frame the panel shows the "helper too old" guidance. Zero JS
errors on both paths. 27 JS + 25 helper-cookie + 64 unittest + verify layers.

STILL UNVERIFIED BY ME: whether the login now persists. That needs a real
browser, the extension loaded, and a Simply Wall St account — Cloudflare
blocks server-side access. The diagnostic exists so the next round is driven
by data from the user's browser instead of another guess.

## v3.76 / helper v3.2 — compare the frame against a real tab
The v3.1 diagnostic came back with real data from the user's browser:
  storageAccess: true
  cookies:  _hjSessionUser_44113, _hjSession_44113, IR_PI, IR_40071
  localStorage: unleash:repository:sessionId, REACT_QUERY_OFFLINE_CACHE,
                snowplowOutQueue_..., portfolios, unleash:repository:repo,
                _gcl_ls, portfolio
Readings: the Storage Access grant WORKED, so that is no longer the blocker.
Every visible cookie is a third-party tracker (Hotjar, Impact Radius) — no
Simply Wall St session cookie, though an httpOnly one would be invisible here
so that is not conclusive. localStorage holds Simply Wall St's own app state
but no auth key, which is consistent with a partitioned, anonymous session.

Consistent with — not proof of. The frame cannot know what it is MISSING
without something to compare against, so v3.2 supplies it: a normal
simplywall.st tab records the NAMES of its keys and cookies into extension
storage (names only, local, never transmitted), and the diagnostic reply now
carries that snapshot plus a computed diff. The panel turns the diff into one
of four plain-English verdicts:
  - no snapshot yet      -> tells the user to open a normal signed-in tab first
  - localStorage missing -> partitioned storage; NO extension can fix it
  - cookies missing      -> cookie delivery; the helper CAN act on it
  - nothing missing      -> not isolation; likely an httpOnly cookie refused
                            on the request itself
Verified all four in a browser using the user's real key names: each renders
its own distinct verdict, zero JS errors. 27 JS + 25 helper-cookie + 64
unittest + both verify layers.

This is deliberately a diagnostic release, not a seventh blind fix. Six
attempts have each been a plausible theory that turned out incomplete; the
next change should be driven by which of the four verdicts comes back.

## v3.77 / helper v3.3 — the comparison never got a baseline
Second diagnostic came back with `topTab: null` and `missingVsTopTab: null`,
so no verdict could be computed. The reply DID carry those two fields, which
only exist in v3.2's script — so the helper was current and the snapshot
simply never got written.

v3.2 snapshotted only on `load` + a 6s timer. That misses the common cases: a
simplywall.st tab already open before the update, or a sign-in minutes after
load. It could report "no snapshot yet" indefinitely.

- The snapshot now also fires on focus, visibilitychange, pagehide and every
  30s while visible. Re-snapshotting costs a few key names and always
  overwrites with the latest view.
- When there is still no snapshot, the panel now says so plainly AND offers
  an "Open a normal tab to compare" button, instead of only instructing.

New permanent test `test_helper_swssync.js` (in npm test), running the REAL
content script under a fake DOM — 13 assertions covering the parts with the
widest blast radius, since this script also runs in the user's ordinary
simplywall.st tabs:
  - top-level tabs write a snapshot of cookie/localStorage/sessionStorage
    NAMES, and re-snapshot on focus and on visibilitychange
  - NO cookie value and NO localStorage value ever reaches storage (the
    privacy claim this feature rests on, asserted directly)
  - a top-level tab never posts to a parent
  - a framed tab answers a dashboard request with the snapshot AND a correct
    diff (the verdict is only as good as this diff)
  - a request from a foreign origin is ignored

## v3.78 / helper v3.4 — audit the real cookie jar; stop staging comparisons
Third diagnostic again returned topTab: null. Two lessons:

1. I could not tell WHICH helper version produced any of these replies — the
   payload never carried it. Every conclusion about "you must be on vX" was
   therefore unfounded. The reply now includes `helperVersion` from
   chrome.runtime.getManifest().
2. The whole comparison approach was the hard way round. document.cookie
   inside the frame can only see NON-httpOnly cookies, and a session cookie is
   almost always httpOnly — so every in-frame check so far was structurally
   blind to the one cookie that decides this, and asking the user to stage a
   normal-tab comparison was working around my own blind spot.

The background worker holds the "cookies" permission and can enumerate the
real jar for simplywall.st, httpOnly included, with no tab choreography at
all. v3.4 adds a `jth-sws-cookie-audit` message returning METADATA ONLY —
name, sameSite, secure/httpOnly/session/partitioned flags, and an authish
heuristic — never a single value. The panel reads it first and gives a direct
verdict:
  - no auth-ish cookie exists      -> the session lives in localStorage;
                                      partitioned per top-level site; NOT
                                      fixable by any extension
  - auth cookie, none SameSite=None-> withheld from the frame; sign in again
                                      so the helper's rewrite applies
  - auth cookie partitioned        -> the frame holds an isolated copy
  - auth cookie cross-site-ready   -> not storage isolation; the app is
                                      rejecting the session for another reason
Verified all four render distinctly in a browser, zero JS errors. The
normal-tab comparison is kept as a fallback but is no longer the primary path.

## v3.79 / helper v3.5 — root cause found: Simply Wall St has no login cookie
The v3.4 cookie audit answered it. The user's browser holds exactly THREE
cookies for .simplywall.st while they are demonstrably signed in in a normal
Chrome tab (screenshot: account menu, "Jerry Pena"):
  _hjSessionUser_44113   Hotjar analytics
  _hjSession_44113       Hotjar analytics
  __cf_bm                Cloudflare bot management (httpOnly)
There is NO Simply Wall St session cookie anywhere in the jar. Their session
is kept in localStorage, and Chrome partitions localStorage per top-level
site, so the copy inside the frame is a different, empty one.

THAT is why Finviz, TradingView and Unusual Whales stay signed in and this
does not: all three authenticate with COOKIES, which the helper rewrites to
SameSite=None. Simply Wall St has no cookie to rewrite, and an extension
cannot un-partition localStorage. Every fix from v3.0 onward was aimed at a
cookie that does not exist.

BUG FOUND IN MY OWN VERDICT LOGIC: the authish heuristic matched
"_hjSessionUser_44113" on sess/user, so crossSiteReady was 2 and the panel
would have reported "the login cookie looks correctly set for framing" — the
exact opposite of the truth, for the one user whose data proved otherwise.
Added a NOT_AUTH deny-list (_hj, _ga, _gcl, IR_, __cf, datadome, snowplow,
sentry, ...) that runs before the auth match, and locked BOTH patterns down
with 13 cases in test_helper_cookies.js using the user's real cookie names.
Under the fixed rule their jar yields zero auth cookies and the correct
verdict renders.

The panel now states the cause plainly, says explicitly why the other three
sites behave differently, and gives the only lever that exists — it is a
BROWSER setting, not an app one:
  chrome://flags/#third-party-storage-partitioning -> Disabled -> relaunch
  (or the ThirdPartyStoragePartitioningBlockedForOrigins managed policy)
with the honest caveats that it is a global privacy trade-off and that newer
Chrome builds may have removed the flag.

40 helper-cookie + 13 sws-sync + 27 JS + 64 unittest + both verify layers.

## v3.80 / helper v3.6 — mirror the session; the login works
I had called this unfixable. That was wrong, and the user was right to push.

The reasoning I skipped: Chrome partitions localStorage between the top-level
simplywall.st tab and the framed one — but the EXTENSION has content scripts in
both, and both are the SAME ORIGIN. Partitioning stops the page from reaching
across; it does not stop an extension that is already inside both. The session
can be read in the normal tab and written into the frame. The precedent was
already in this codebase: v2.6 mirrors cookies for Comet/Brave.

Implementation:
  - Normal tabs export localStorage entries to chrome.storage.local.swsSession
    (values), alongside the existing names-only swsTopSnapshot (diagnostics).
  - The framed script applies them at document_start, so on a normal load the
    app boots already signed in — no reload, no logged-out flash. If the keys
    land after the app read storage, it reloads ONCE (sessionStorage-guarded,
    cannot loop).
  - Refreshed every 20s and on focus, so a token refresh or a later sign-in
    propagates without another reload.
  - Sign-out propagates: keys the mirror wrote that the source no longer has
    are removed; keys the frame created itself are never touched.
  - Never mirrored: analytics/telemetry/caches (REACT_QUERY_OFFLINE_CACHE,
    snowplow*, _hj*, _ga*, IR_*, sentry, ...), any value >256 KB, >2 MB total.

PRIVACY BOUNDARY, now explicit and tested. Two stores with different
contracts: swsTopSnapshot is names-only and is what gets reported to the page;
swsSession holds values and must never leave extension storage. The diagnostic
reply carries the mirror's SHAPE (key names + timestamp) and never a value —
asserted directly with a token named SUPERSECRET that must not appear in the
postMessage payload.

test_helper_swssync.js now 29 assertions: what is and is not mirrored, size
caps, adoption into the frame, logout propagation, frame-owned keys left
alone, no-mirror-means-no-writes, foreign origins ignored, and the two privacy
assertions above. 40 helper-cookie + 27 JS + 64 unittest + verify layers.

The user's normal tab is the SOURCE and must be signed in — stated in the
panel and the README.

## v3.81 / helper v3.7 — the version number was a lie for nine releases
User reported the login still failing, and the screenshot showed the banner
saying "(you have v2.7)" while their own earlier diagnostic had returned
helperVersion 3.4 straight from the manifest. Both cannot be true.

announce.js hard-coded `const VERSION = "2.7"` and was never updated when the
manifest went 2.8 -> 3.6. That value is what sets
document.documentElement.dataset.finvizHelperVersion, which drives the helper
chip AND the Simply Wall St banner. So from v2.8 onward BOTH always claimed
v2.7, regardless of what was installed. Every "you have vX, update to vY"
message I sent was reasoning from a number that was never read from the
extension — and I told the user their helper was current based on it.

This is the FIFTH instance of the same defect class in this one feature:
APP_VERSION not bumped (cache-buster), the chip label hard-coding "2.8", the
banner text hard-coding "v2.8", the authish heuristic hard-coding assumptions
about names, and now this. Every one was a literal drifting from its source of
truth. announce.js now reads chrome.runtime.getManifest().version, and
test_helper_swssync.js fails if a version literal reappears there or if the
panel's LATEST / SWS_LATEST drift from the shipped manifest.

Also fixed, and likely why the v3.6 mirror did nothing: it sourced the session
from the normal tab's CONTENT SCRIPT, and Chrome does not inject content
scripts into tabs that were already open when an extension updates. A user who
updates the helper with their simplywall.st tab already open gets an empty
mirror and no indication why — which also explains three rounds of
`topTab: null`. v3.7 pulls the session on demand via chrome.scripting from any
open simplywall.st tab, so injection timing is irrelevant. Adds "scripting"
and "tabs" permissions, used only to read localStorage from the user's own
simplywall.st tab.

The diagnostic now also lists IndexedDB database names in the frame — if the
session is neither a cookie nor a localStorage key, that is where it is, and
the mirror would need extending to reach it.

33 sws-sync + 40 helper-cookie + 27 JS + 64 unittest + verify layers.

## v3.82 — the panel now names the remaining gap itself
User confirmed helper 3.7 installed (chrome://extensions shows 3.7) while the
dashboard still read "you have v2.7". That is expected and I should have said
it upfront: announce.js is a content script that runs at PAGE LOAD. Reloading
the extension orphans the running copy; the corrected one does not execute
until the dashboard tab is refreshed. The banner now says so explicitly.

Substantively: one fact is still missing after all these rounds — whether the
user's NORMAL tab holds a session key in localStorage at all. Every diagnostic
so far read the FRAME (signed out, so of course no auth key) and topTab was
always null. v3.7's on-demand pull finally reads the normal tab, so the panel
can now answer it without another guess from me:
  - pulled keys include an auth-looking name -> mirrored; if still signed out
    the app read storage before the key landed, so Reload
  - pulled keys exist but NONE is auth-looking -> the session is in IndexedDB,
    not localStorage, and mirroring localStorage cannot reach it. That is a
    different mechanism, and the panel says so rather than implying a tweak
    would do it.
The auth-name test deliberately excludes the app's own non-auth keys
(unleash, portfolios, react-query, analytics) so `portfolios` cannot be
mistaken for a session.

Deliberately NOT building IndexedDB mirroring speculatively: it is a large
piece of work (enumerate DBs, read stores, write into the frame's partition
via scripting frameIds) and would be wasted if the session is a localStorage
key the pull now reaches. One Diagnose click decides it. Building on an
unverified assumption is exactly what cost the previous rounds.

## v3.83 / helper v3.8 — the session cookie was INVISIBLE, not absent
The v3.7 diagnostic finally compared the frame against the normal tab, and
`missingVsTopTab` settled it:
  cookies missing in the frame: auth, PHPSESSID, _sws_uid, _sws_suid,
    _sws_ses.1740, _sws_id.1740, _sws_imsessid, _sws_flagsessid, ...
  localStorage missing: []   <- the v3.6/3.7 mirror worked perfectly
So Simply Wall St DOES use cookie auth, the localStorage mirror was solving a
problem that did not exist, and my "no session cookie exists" conclusion was
wrong.

WHY THE AUDIT LIED. chrome.cookies only surfaces cookies the extension holds
host permission for, and a cookie WITHOUT the Secure flag belongs to the
http:// origin. host_permissions listed only https://simplywall.st/*, so
cookies.getAll returned 8 of ~30 cookies — and every one of those 8 had
secure:true, which was the tell sitting in the data the whole time. The
session cookies are not Secure, so they were invisible to:
  - the SameSite=None rewrite (never applied -> browser never sent them to
    the frame -> frame permanently signed out), and
  - the cookie audit built on the same call, which then reported "this site
    has no login cookie" and sent me down the localStorage/IndexedDB path for
    four releases.

FIX: host_permissions += http://simplywall.st/* (+ subdomains), and a new
getAllCookiesFor() that queries by URL over BOTH schemes as well as by domain
and merges, used by the sweep and the audit. The existing rewrite then covers
auth/PHPSESSID/_sws_* and upgrades them to Secure, which SameSite=None
requires anyway.

Tests: manifest must cover both schemes for embedded sites; the lookup must
exist, query http, and be used by both the sweep and the audit. 46
helper-cookie + 33 sws-sync + 27 JS + 64 unittest + verify layers.

The localStorage mirror is kept — it is harmless, it demonstrably works, and
it covers any future non-cookie state — but it was never the fix.

## v3.84 / helper v3.9 — remove the localStorage mirror
Confirmed working by the user after v3.8. The v3.6/3.7 localStorage mirror was
built on the wrong diagnosis and is now dead weight: the login is a cookie, and
`missingVsTopTab.localStorage` was empty, i.e. the mirror was already copying
everything correctly and it never mattered.

Removed at the user's request:
  - normal tabs no longer export localStorage VALUES (swsSession is gone)
  - the frame no longer adopts anything into the site's storage
  - the adopt-then-reload path and its sessionStorage guard are gone
  - the panel's mirror status UI is gone
Kept: the names-only snapshot, the cookie audit, and the frame-vs-tab
comparison — that comparison is what identified the missing auth/PHPSESSID/
_sws_* cookies, so it earns its place.

CLEANUP FOR EXISTING USERS: anyone who ran 3.6/3.7 has mirrored values sitting
in the embedded frame's storage. On first load the frame now removes exactly
the keys the mirror recorded writing (leaving keys the frame created itself),
drops the marker, and the background worker deletes the stored copy from
extension storage. Removing a feature that wrote data is not finished until
that data is gone.

Tests inverted rather than deleted — they now assert the opposite invariant:
no cookie value and no storage value is ever written to disk, no swsSession
store is created, the frame adopts nothing even if an old store exists, and
the cleanup removes mirrored keys and the marker while leaving frame-owned
keys. The diagnostic reply must contain no mirror section and must still carry
missingVsTopTab.

"scripting"/"tabs" are kept but narrowed to NAMES only. Reading the tab
directly is what made the comparison reliable — content scripts are not
injected into tabs already open at update time, which is what produced three
rounds of topTab: null.

29 sws-sync + 46 helper-cookie + 27 JS + 64 unittest + verify layers.

## Fix: test_whisper_sources expired on 2026-08-12 and blocked CI
The v3.9 PR went red on a test that has nothing to do with it:
  test_user_entry_merges_and_dispersion_caps_confidence
  AssertionError: 1 != 2   (source_count)
It failed locally too, and had passed on every PR up to #265 — because it only
became false today.

The provider fixtures are REAL captures, so their event dates are fixed: the
SE fixture reports on 2026-08-11. The test files a manual whisper entry
against that date, and whisper_sources.py:425 correctly refuses an entry whose
`asof` is LATER than the event it claims to describe — a whisper filed after
the earnings release is not a whisper for that release. Once the wall clock
passed 2026-08-11, the entry was dropped and source_count fell from 2 to 1.

Production logic is right; the test was standing in the wrong time. Fixed by
pinning whisper_sources' clock (Base.pin_clock, default 2026-08-01) in the
tests that file manual entries, rather than by editing an authentic captured
fixture or loosening the assertion.

Verified date-proof, not just green today: re-ran the module with the clock
forced to 2027-12-31 — long past every fixture date — and all 29 pass. The
comment in the test records why, so the next person does not "fix" it by
relaxing the assertion.

# v3.85 — reliability sweep: kill the bug classes this codebase has proven it has

Instead of speculative "improvements", this release hunts the defect classes
with a track record here and builds guards so they cannot recur.

## 1. Time bombs (the class that fired on 2026-08-12)
A grep found ~60 hard-coded future dates across the test suite; any of them
can become false purely by time passing, failing on an innocent PR months
later. Rather than audit them by eye, `test_time_travel.py` re-runs the WHOLE
suite with the clock shifted +400 days (freezegun). First run found:
  - test_manual_roundtrip_and_event_binding — second whisper time bomb
    (would have fired 2026-11-03). Pinned like the first.
  - test_schedules' expiry alarm — correctly excluded: that one is a DATA
    FRESHNESS DEADLINE whose whole purpose is to fail when the calendar runs
    out; the normal suite enforces it against the real date.
CI now runs the time-travel check on every push, so a test that only passes
because of what today is fails ON THE PR THAT INTRODUCES IT.

## 2. The macro schedule (the alarm the time-travel run surfaced)
  - Added the eight 2027 FOMC decision days from the Federal Reserve's own
    calendar. The parser was validated by reproducing the 2026 list already
    in treasury.py byte-for-byte from the same page before being trusted for
    2027. BLS blocks automated access (403), so 2027 CPI dates are NOT added
    — inventing CPI dates in a trading app is worse than the alarm firing.
  - schedule_status() now reports per-series coverage ("cpi ends 2026-12-10,
    fomc ends 2027-12-08"), which series need refreshing, and
    declared_beyond_data — the hand-maintained valid_through literal claiming
    coverage the real dates don't back (the same literal-drift class again).
  - test_schedules now: names the exhausted series in its failure message,
    fails if valid_through outruns the data, and checks every series is
    chronological, duplicate-free and parseable.

## 3. Version-literal drift (five prior instances)
The frontend now has exactly ONE helper-version constant, HELPER_LATEST in
app-lib.jsx. The ⤓ Helper chip, the SWS panel and the cookie-setup chip all
read it. The cookie-setup chip alone had THREE different versions in one
block (gate 2.7, tooltip "v2.8", label "v2.7" — telling the user to update to
a version nine releases old). test_helper_swssync asserts HELPER_LATEST
equals the shipped manifest AND that no local version literal reappears in
app-cards.jsx.

## Checked and deliberately left alone
  - setInterval leaks in useEffect blocks: none found (every effect clears
    what it starts).
  - 322 `except Exception` blocks in options_dashboard.py: predominantly
    deliberate per-card resilience (one provider failing must not take down
    the dashboard). Mass-rewriting them would be high-risk churn with no
    per-line test coverage — exactly how bugs get introduced. Not touched.

Honesty note: "bug free" is not a promise anyone can make. What this adds is
that two entire defect classes with a track record here — expiring tests and
drifting version literals — are now caught mechanically by CI rather than by
a user hitting them.

## v3.86 — options-math accuracy + slow-provider latency
Two requested follow-ups. Both started by MEASURING rather than assuming.

### 1. The greeks were fed library defaults, not the app's own data
`_bs_*` supported a dividend yield `q` and a rate `r`, but NO production call
site supplied either — only the fixture generator did. So every option was
priced as if the stock paid no dividend, at a hardcoded 4.5% rate, even though
the app already knows the live 3-month Treasury rate AND computes each symbol's
dividend yield for the sidebar.

Measured, at 90 DTE / 30% IV, on the strike this app labels "0.20 delta":
    AAPL 0.5% yield -> 0.195   (-2.4%)
    JPM  2.1%       -> 0.187   (-6.4%)
    KO   3.1%       -> 0.182   (-8.8%)
    O    5.5%       -> 0.171  (-14.5%)
    VZ   6.2%       -> 0.168  (-16.1%)
Precisely the income names a covered-call / cash-secured-put book is built
from. Fixed at all 12 greek call sites plus `_strike_for_delta` itself (which
took `r` but had no `q` at all) and the backtest leg builder, so a backtested
"0.20 delta" strike is the strike the app would really pick.

`_dividend_yield_cached()` reads ONLY the warm .info cache and returns 0.0 on a
miss — never fetching, because it runs inside the option-chain hot path and
.info is the slowest upstream call there is. It also bounds the yield at 50%:
the same yfinance format flip that once read AAPL's 0.35% as 35% must never
reach the pricer as a 3500% yield.

### 2. Nothing validated the math against outside ground truth
test_math_contract is thorough about self-consistency — but its fixtures are
generated by the code under test, so a shared wrong formula passes everything.
New test_math_reference.py pins the numbers three independent ways:
  - Hull's published example (S=42 K=40 r=10% sigma=20% T=0.5 -> 4.76 / 0.81)
  - Monte Carlo, a completely different algorithm, which independently
    confirms the dividend term
  - Finite differences, so delta/gamma/vega/theta are checked against the
    DEFINITION of the derivative rather than the hand-derived formula
All pass. The formulas were already right; what was wrong was the inputs — and
a source-level guard now fails the build if any greek call site goes back to
the defaults. That guard immediately caught one site I had missed.

### 3. One slow provider could stall the whole page
The server speaks HTTP/1.1 keep-alive and a browser opens ~6 connections per
host, so slow requests stall everything behind them — the mechanism measured
during the Simply Wall St work, where /api/site_link requests were issued and
never answered. `_ticker_info` released its lock BEFORE the network call, so N
concurrent cold requests for one symbol made N identical slow calls (verified:
8 concurrent callers -> 8 upstream calls). It is now coalesced to one shared
fetch with a 4s deadline; on timeout the caller gets {} (every caller already
handled that) while the fetch completes in the background, so the next request
is instant instead of repeating the stall.

### 4. CI could silently skip a whole test file
The Python step listed test modules BY HAND, so a new suite could sit in the
repo, green locally, never executed in CI — exactly the class of bug this
session keeps finding. Replaced with `unittest discover`. That alone took CI
from 230 to 280 executed tests.

Also fixed in my own new tests, both found by the time-travel runner: shared
state between latency tests (a background fetch from one test could warm the
next test's cache) and durations measured with time.time() instead of
time.monotonic(). test_provider_latency is exempt from the time-travel run —
its 12h TTL arithmetic straddles the faked-clock boundary, which tests
freezegun rather than the app; the reason is recorded in SKIP_MODULES.

## v3.87 — Earnings Whispers weekly calendar, live from @eWhispers on X
The Earnings Ops tab now opens with the weekly Earnings Whispers calendar —
the "#earnings for the week of …" image post — detected automatically each
week, no URL editing ever.

### Detection is multi-signal, and selection is BY WEEK, never by recency
A broad X API search (`from:eWhispers earnings has:images -is:retweet
-is:reply`) feeds a scorer: weekly-calendar phrase, a parsed "week of" date,
match against the current trading week, cashtag count, and a penalty for
daily-reporters wording. Accepted posts are stored under the week they
ANNOUNCE, so the daily posts and charts the account also publishes — always
newer — can never displace the current week's calendar, and next week's
calendar takes over exactly when the weekend rolls the relevant week
forward (Sat/Sun → coming Monday). Verified against the REAL example post
(2085726194914242793), whose text and publish date were captured live
through X's official oEmbed endpoint into fixtures.

### Every layer has a fallback, so the card never breaks
current verified post → last verified post from the on-disk cache (labeled
"previous week") → a manually pasted post URL (validated to @eWhispers only,
hydrated credential-free via official oEmbed: text, week, tickers) → a clean
labeled empty state. Display mirrors the same ladder: native <img> from the
API's media metadata (pbs.twimg.com only — anything else is dropped), else
the official X embed (created by widgets.js's createTweet — no third-party
HTML ever injected), else a plain link. Broken-image onError falls through
automatically.

### The terminal look, not a social-media card
The calendar image gets the card's full width at its real aspect ratio with
a click-to-enlarge lightbox (Esc closes). Cashtags from the post text become
ticker chips wired to the app's existing global ticker (switchTicker →
Analyze) — structured data only, no OCR. Week navigation appears only when
history exists (small window, pruned to 8 weeks).

### Server-side cost discipline
`/api/ewhispers/weekly` is a cache-only read; a stale cache kicks ONE
coalesced background check (≥4h apart — browsers never fan out to X), and
credentials stay server-side (`X_BEARER_TOKEN`; responses carry a boolean).
Without credentials the card says so and the manual path works end to end.
JERRY_NO_NET=1 disables all network, matching the suite convention.

Tests: test_ewhispers.py (38) — week math incl. weekend rollover and the
spec's own Wednesday example, wording variants, year inference across
Dec/Jan, scorer vs the real fixture + decoys, rate-limit/garbage-payload
survival, URL validation (rejects non-eWhispers, http, lookalike hosts),
oEmbed hydration from the real capture, persistence across restart, token
never in a response. Suite: 318 Python + JS + time-travel green; Playwright
QA (desktop 1440px + mobile 390px): 25/25 — including "a newer unrelated
post does not replace the weekly calendar" and chip → global ticker. Found
and fixed in QA: `.card-head > div` forces flex-column, which stacked the
card's header buttons vertically (scoped row override).

## v3.88 — the full-size calendar image, with NO X API key
Jerry: "I would never have an X API key… I would like to see the Image take
up the space, not the post." Both fixed.

The manual-URL path now hydrates through cdn.syndication.twimg.com/
tweet-result — the public, unauthenticated JSON feed the OFFICIAL X embed
widget itself renders from (verified live against the real post: it returns
the full text, the publish time, and the direct pbs.twimg.com media URL at
3840×2160). So pasting the weekly post link now produces the same large
native image the credentialed path shows — the 550px tweet card is only the
last-resort fallback if that feed ever changes shape (tombstones, foreign
authors and off-CDN media are all rejected; oEmbed remains the text
fallback). A post saved by v3.87 upgrades itself in the background on the
next card load — no re-saving.

Also: the card image area widened to 1280px, and the lightbox now loads the
4096px original variant — the point of enlarging a calendar is reading the
small print. DEPLOY.md now says out loud that the key is optional and not
worth paying for.

Tests: 43 in test_ewhispers.py (five new: real syndication capture →
image/dimensions/week, fallback to oEmbed when the feed is down, foreign
author rejected, deleted-post tombstone, off-CDN media dropped, and the
v3.87 self-upgrade). Full suite 323 + JS + time-travel green.

## v3.89 — the calendar image is served by the app itself
Jerry still saw the tweet card after v3.88. Two ways that can happen, both
now closed:

1. The v3.87→v3.88 self-upgrade runs in the background, and if the image
   feed hiccups it used to wait 24h to retry — with no way to force it,
   because Refresh needed an API key to do anything. Now: Refresh without
   credentials force-re-hydrates the manual post (one click fixes a stuck
   embed), the automatic retry window is 2h, and when the embed fallback IS
   shown the card says WHY ("calendar image unavailable (x image feed
   unreachable)") instead of failing silently.
2. Ad-blockers commonly kill pbs.twimg.com in the browser, which tripped
   the img onError → embed fallback even when the server had the image.
   The card now loads the image from THIS app: /api/ewhispers/image
   downloads it from X's CDN once, keeps it on disk, and serves it from
   then on (magic-byte sniffed, 12MB cap, atomic write, single-flight; the
   frontend fetches it as a blob through apiFetch so the API-key gate
   holds). The browser never talks to X at all — nothing to block, and the
   same big image is never pulled from X twice. Direct URL and embed remain
   as fallbacks two and three.

Tests: 50 in test_ewhispers.py (+7: proxy paths in the payload, download-
once-then-disk incl. offline reads, separate full-size cache, path-traversal
and bad-size refusals, failed-upstream-not-cached-then-recovers, forced
credential-free re-hydrate, unforced still declines). Full suite green.

### v3.89 addendum — the bug the browser QA caught
apiFetch's GET dedupe reads every response body as TEXT and rebuilds a
Response from the string — fine for the JSON it was built for, fatal for
binary: the proxied JPEG came out mangled, the <img> failed to decode, and
the card fell back to the embed. The image fetch now passes noCache (raw
fetch path; the endpoint's Cache-Control keeps repeat loads cached). The
final QA run served the REAL calendar (288KB, fetched once through the
app's cache) end to end: 25/25 desktop + mobile.

## v3.90 — mobile sweep: every tab driven at phone size
Jerry: "make sure this and everything else is mobile friendly." Method, not
vibes: Playwright drove all 21 tabs at 390×844 (iPhone UA, touch), measured
page-level horizontal scroll and per-element clipping at three scroll stops
per tab, and screenshotted everything for eyeballing.

Result: the shell (bottom bar, tab strip, drawers), the boards, the
calendars and the new Earnings Whispers card were already clean — zero
page-level horizontal scroll anywhere, no JS errors. TWO real defects
found, both fixed:

1. The Weekly Option Selling Setup card was 528px wide on a 390px phone —
   title, NOW marker and right-hand numbers clipped. Root cause: the phone
   reset for `.wos-dayctx { white-space: normal }` at the 1100px breakpoint
   was OUT-CASCADED by an unconditional nowrap declared later in the file
   (same specificity — media queries add none), and a bare `fr` grid track
   inflates to min-content, dragging every zone of the card with it. Fixed
   by making nowrap a min-width rule and flooring the card's grid tracks
   with minmax(0, …) so no future child can widen the card again.
2. The calendar lightbox fit the image to screen width — unreadable on a
   phone. Tap now magnifies (260vw, pan both axes; desktop: natural pixel
   size), tap again fits, × / Esc / backdrop close. A failed full-res load
   falls back to the already-shown image (onError guard) instead of
   blanking — caught in QA when the full variant wasn't cached yet.

Verified: magnify/pan/restore/close on the phone viewport with the REAL
calendar; the fixed setup card re-probed (zero elements wider than the
viewport, was 12); full EW card QA still 25/25; suite 330 green.

## v3.91 — Prior High Recovery scanner (backtested on 10y × 1,283 stocks)
Jerry's spec: find stocks that made a significant high, corrected, and are
now turning back up — BEFORE they reach the prior high — modeled, scored,
ranked and backtested; "let the historical evidence determine the final
rules instead of hardcoding my initial assumptions."

New: `recovery.py` (detector + scan board + probability serving),
`recovery_fit.py` (the offline historical study), `recovery_model.json`
(the fitted artifact, versioned in the repo), `tab-recovery.jsx` (Recovery
tab: scanner table with presets/filters, expansion detail, research view),
chart levels in `TVPriceChart` (prior high / correction low / higher low /
bounce high / invalidation lifted onto the Trade chart), `test_recovery.py`
(38 tests incl. the lookahead-invariance proof).

Design decisions worth remembering:
- ONE detection implementation (`_eval_at` against causal precomputed
  series). `detect_setup(bars)` describes the last bar; the backtest replays
  history through `iter_days`, and a test asserts both paths are
  byte-identical on every bar — that IS the no-lookahead guarantee.
- The probability model is fit OFFLINE on real data (Yahoo v8 chart API,
  10y daily, 1,283 watchlist symbols → 41,923 spaced signals) and shipped
  as a static artifact. The app never re-crunches years of history; it
  looks up empirical decile tables with sample sizes attached. No model /
  small bucket / out-of-population stage → "insufficient historical data",
  never an invented number.
- Chronological protocol: train ≤2021, validation 2022–23 (hyperparams +
  feature pruning), test 2024+ touched once: AUC 0.775, Brier 0.161 vs
  0.194 base rate. Deciles 1→10 monotone 1%→59%. Top-3-decile hit rate
  beats each year's base rate in all 10 years including 2022.
- Feature pruning DROPPED break10, raw RSI, MACD-improving, relative
  volume, SPY-regime and pre-high trend — no out-of-sample value once
  structure (distance, recovery ratio, ATR, bounce-break) is accounted for.
- Survivorship disclosed everywhere: universe = today's watchlist.

## v3.92 — sector tags on the Recovery board
Jerry: "put the symbol tag so I can quickly see if it's a group in a
sector that's moving to the upside." Each row now carries a compact sector
tag (Tech ▲ / Energy ▼ — arrow = that sector's ETF ±1%+ over 20 sessions),
and a sector strip above the table shows every sector's setup count + ETF
momentum (e.g. "Tech 146 +4.0%"), biggest cluster first; clicking a chip
filters the board to that sector. ETF trends are computed during the scan
from the sector ETF closes the worker already fetched (no new downloads)
and persist with the board. Sector column is sortable so clusters group
visually. 370 tests green (3 new); browser QA 30/30 desktop + mobile.

## v3.93 — YOUR tags, not sectors, on the Recovery board
Jerry: "I wanted the Tag. Sector is too broad." The group column and the
chip strip now use the watchlist tag each stock carries (from the Manage
tab's CSV import — Jerry's own narrow groups). A tag group's momentum has
no ETF, so it's measured from the group itself: median 5/20-session move
across ALL scanned members of the tag (≥2 members required so one stock
can't be a "group"), computed from bars the scan already downloads.
Untagged stocks show a muted sector abbreviation as fallback; a watchlist
with no tags at all falls back to the v3.92 sector strip with a hint that
tags come from the CSV. Persisted with the board. 372 tests green
(2 new); QA 30/30 with a simulated-tag board (chip filter → exact rows).

## v3.94 — Jerry's examples validated + an evidence-tested new signal
Jerry restated the goal and named live examples (DELL, TEVA, NVDA, STLD,
LITE, AAOI, COHR). Engine read on real bars: DELL = breakout (trip done,
correctly dropped), TEVA/NVDA = approaching with 1.6%/5.3% left (recovery
real, upside gone — the discriminator working), STLD/LITE/COHR = confirmed
in the sweet spot. AAOI excluded by the 60% max-depth gate — and the data
backs the gate: 3,211 historical 60-80% collapses reclaimed their high
only 9.5% of the time in 60 days (median best bounce +22% though — that's
a bounce trade, not this trade).

Signal audit: 11 candidates tested one at a time on validation AUC
(up/down volume, higher-low count, sector RS, VIX, MACD/RSI crosses, gap,
recovery speed, correction velocity, ATR expansion, distance-in-ATRs).
ONE adopted: distance-in-ATRs (how many normal days of range to the
target). Refit: test AUC 0.7753 → 0.7794; pruning now also drops
days-since-low + significance (absorbed). Engine additionally computes
updown_vol + hl_count — shown in the expansion for context, deliberately
NOT in the model (no measurable lift). 373 tests green; QA 30/30.

## v3.95 — Recovery table headers centered on their columns
Jerry (from the live app): TAG's header sat all the way right, STAGE's all
the way left — headers were inheriting the numeric right-align. Every
column (header + cells) is now centered except Ticker, which stays
left-aligned as the row anchor; the expansion detail row is pinned back to
left so the explanation text doesn't center. Verified with computed-style
probes (th/td both "center", detail td "left") + screenshot.

## v3.96 — swing chart mobile cleanup + Down on by default
Jerry (from his phone): the Patterns swing chart was messy on mobile and
he wants Down swings visible on open. Three changes:
1. The OHLC readout + now/median/aggr/inval legend floated OVER the
   candles and covered a third of a phone plot — on ≤760px they now sit in
   normal flow above the chart, full-width, smaller; chart height 360px.
2. Down toggle defaults ON everywhere; % labels default OFF on phones
   (they collide at that width once both directions draw — the Labels
   toggle re-enables).
3. Explicit touch config on the swing chart AND the main TVPriceChart:
   pinch zooms, horizontal drag pans, but vertTouchDrag:false leaves
   vertical swipes to the PAGE — previously the chart trapped the scroll.
Verified at 390×844 with real BE swing data (fixture from cached bars):
Down on, labels off, overlay static, 360px chart, no page h-scroll, no JS
errors.

## v3.97 — native-feeling pinch zoom on the charts
Jerry: LWC's built-in pinch "automatically zooms in all the way, and then
it's hard to zoom back out". Root cause: the library's pinch is coarse —
not proportional to finger spread. Replaced it: built-in pinch disabled,
new attachTouchZoom (charts.jsx, exported on window) drives the visible
logical range directly — zoom tracks finger spread exactly 1:1 (span ×
d0/d), anchored at the pinch midpoint, span clamped to [7 bars, 1.5×
data] so it can never slam shut. Double-tap returns to the home view
(6-month view on the swing chart, fit-content on the price chart).
Wired into SwingChart + TVPriceChart. One-finger: horizontal drag pans,
vertical swipe scrolls the PAGE (untouched by the handler — verified via
defaultPrevented probe). Proved with synthesized TouchEvents in Chromium:
span 100 → 66.7 → 50 → 125 exactly proportional, floor holds at 7 after
repeated extreme pinches, double-tap fires, page scroll free.

## v3.98 — five workflow asks in one release
1. SWING CHART: 2 years of data (520 bars) instead of 1 — zoom out for more
   patterns. Same Schwab-first cached path (the 2y gate was hard-coded to
   1y; without extending it, period=2y would have fallen into a fresh
   yfinance download — slower, not deeper). Bonus fix found on the way:
   load_daily rows never carried volume, so the swing chart's volume
   histogram and rel-vol chips were silently zero on the fast path.
2. MARKET CALENDAR: server-side stale-while-revalidate with a disk mirror
   (<data>/market_calendar.json). The tab now renders instantly from the
   last built calendar — even right after a redeploy — and a background
   thread rebuilds when it's older than 15 min (earnings) / 30 min
   (economic). A day-old calendar is fine; it looks 4-5 weeks out.
3. FLOW: Market flow dashboard expanded by default. localStorage key bumped
   .v1→.v2 because the old key auto-wrote "0" on first mount — every
   existing device would have stayed collapsed forever.
4+5. SCAN ALL: /api/scan_all — server-side sequential orchestrator over the
   board scanners (movers → weekly range → trend → HV rank → analyst),
   one at a time, each waiting for the previous, with per-scan freshness
   TTLs (movers 10m, price scans 6h, analyst 20h) so fresh boards are
   SKIPPED — strictly fewer provider calls than clicking each button.
   Discover gets a "Scan all" button in the screener subnav; Scanners gets
   a "Scan everything" strip that also chains the per-symbol scanners
   (EMA pullback → open-to-low → momentum → richness → best-setup →
   implied-move; UW ones skipped when disconnected) one at a time — never
   more than one request in flight. Single-ticker tools (earnings ladder,
   walk-forward) stay manual. Board cards now resume polling on mount when
   a scan is in flight, so Scan-all progress shows live.
   Bugs caught by the new tests: a self-deadlock in the busy guard
   (scanall_status() under the non-reentrant lock) and a response key
   collision ("started" boolean vs timestamp — now started_at).
   382 tests green (9 new in test_scanall.py).

## v4.50 — The Patterns tab answers WHERE it reverses, WHEN, and WHAT FOLLOWS

The swing card's question changes from "how far does this stock normally
run?" to "given the swing in progress has ALREADY travelled X%, where did
swings that reached X% actually end, how many more days did they take from
this exact depth, and what did the opposite swing afterward look like?"

`swing_projection.py` (pure, stdlib + the house Wilson interval) computes
that from the zigzag pivots swings.py already finds. The comparison set is
a SURVIVAL population — every completed same-direction swing whose extreme
reached at least the current one's — deliberately not a "similar size"
window, which would exclude the swings that kept going and understate
remaining risk exactly when the knife is still falling. Walk-forward
validation on 14 symbols / ~800 events before wiring: 28% lower median
reversal-size error than the unconditional rhythm (9.37 → 6.79 points),
p25–p75 band coverage 47.8% against the ~50% ideal. Trend regime, 20-day
range position and velocity were each tested as additional cohort filters
and each REJECTED (no error improvement, worse coverage); they render as
context only. A circularity caught on real data: at 12% zigzag sensitivity
every confirmed counter-swing is ≥12% by definition, so touch levels at or
under the sensitivity are dropped and the floor is disclosed rather than
shown as impressive 100%s.

The card now leads with a deterministic plain-English summary and three
blocks — Current swing / Historical bottom-or-top (the conditional zone in
dollars, remaining move and days from per-swing remainders at the same
depth, the bottomed-within-another-X% ladder) / After the reversal (the
next swing's size, dollar targets anchored at the median zone, touch rates
with Wilson bounds and random-day baselines, 20-day reclaim) — plus a
what-if race entered the day each historical swing first reached the
current depth, same-bar ambiguity counted against the trade. The chart
gains a Zones toggle (band edges, median, follow-on target in the legend);
the score-derived "three paths" card is retired — it manufactured path
probabilities from continuation/exhaustion scores, the exact confusion the
empirical block replaces. The swing card moves above Pattern Discovery;
history deepens to 10 years (card) and 5 years (watchlist scan), and every
scanned row carries rz_* zone fields feeding a sortable bounce/pullback
Reversal scan section. Tunables live in thresholds.json under
swing_projection with the validation evidence in the doc keys.

Also fixed: the invest-scan chain-capture tests inherited the runner's
calendar and went red on a real Saturday (capture correctly refuses
non-trading days; the tests now pin a trading Wednesday) — the same
failure class the time-travel CI step exists for, from the other
direction. 2,452 Python tests green (47 new), 176 node guards (the
swing-paths guard replaced by a reversal-block guard), 123/123 HTTP smoke,
46/46 browser checks at 1400px and 390px, suite green 400 days forward.

## v4.51 — The Patterns tab corrections: the display filter stops defining the population

An independent review of v4.50 found four statistical faults and a product
one. All five are fixed, each measured over 16 symbols × 10 years of daily
bars (~5,000 walk-forward events) rather than argued.

**The 15% floor is out of the cohort.** `min_move_pct` hides small swings
from the TABLES; it was also gating the survival population, so a 13%
decline was excluded from the comparison set of a 12% decline. That deleted
precisely the swings that ENDED shallow and pushed every projected zone
deeper. At 12% sensitivity it was throwing away 19% of all completed legs
(the 12–15% band); at 8% sensitivity, 51%. Removing it cut early-stage
reversal-size error 13% at Standard and 43% at Sensitive, and moved p25–p75
band coverage from 36% / 22% to about 46% against the 50% ideal. It is
inert once a swing passes 15%, which is why the fault was invisible on deep
swings. The remaining floor is structural — a zigzag only turns after a
counter-move of the sensitivity setting, so no completed leg is smaller
than it — and the payload says so in words.

**Status comes from the running extreme, against the UNCONDITIONAL band.**
APPROACHING / IN ZONE / BOUNCING (FADING) OFF ZONE / BEYOND TYPICAL ZONE /
BEYOND HISTORY, decided by three yes/no questions with no invented
confirmation threshold — the band is the yardstick. The band had to be the
unconditional one: every member of the survival cohort ended at or beyond
the current depth, so its shallow quartile sits at or past the extreme BY
CONSTRUCTION and "in the zone" could never have fired — the same dead-state
trap as the beyond-the-median flag removed in v4.50. Both bands now render,
labelled for what they are.

**Probabilities are fixed-horizon or they are not comparable.** The
lifetime touch ladder is retired (a confirmed swing exceeds the sensitivity
by definition, so every level under it scored 100%). In its place: X% within
N trading days, measured from the reversal bar's CLOSE, beside the identical
question asked of every ordinary bar. Pooled: after confirmed bottoms
82.9% / 71.9% / 59.5% for +5%/5d, +10%/10d, +15%/20d against baselines of
27.6% / 18.1% / 17.5% (n=772). Conditional on the turn having happened, and
labelled that way — the what-if is the version that starts where a trade
would actually have to be entered.

**Targets are paired, not multiplied.** Each cohort episode contributes its
own (final depth, follow-on move) pair projected from today's swing origin;
the target band is the distribution of those projections. Honest finding:
the rank correlation between depth and next move is about zero (−0.02), so
the median target moves ~1% — what the change fixes is the BAND, which was
mixing a fixed depth with a variable move.

**Historical entries stop inventing fills.** Where the market gapped through
the crossing level overnight — roughly a third of crossings — the entry is
the session open, in both the remaining-move measurement and the what-if
race. Episodes whose follow-on leg carries a split or an incredible print
are dropped rather than raced through prices the engine does not trust.

**Earnings are tagged in three places, not one:** between the crossing and
the final extreme (new — the case that teaches a zone that an extra 25% gap
was ordinary), at the reversal, and inside the follow-on swing. Depth comes
from SEC 10-Q/10-K filing dates for everything older than the ~4 years the
earnings calendar covers; a filing date is a window, not a report date, and
is treated and labelled as one.

**Stage labels with evidence behind them.** EARLY IN THE MOVE / AT ITS
NORMAL SIZE / BEYOND ITS NORMAL SIZE, at 100% and 125% of the stock's own
median swing. From the staged run (conditional vs unconditional MAE): 25%
of median 6.11 vs 5.98, 50% 5.87 vs 5.83, 75% 5.59 vs 4.58, 100% 6.40 vs
8.08, 125% 8.70 vs 13.76. Conditioning is worth nothing before a move
reaches its normal size and worth a fifth to a third of the error after it.
Descriptive only — nothing on the panel is scaled by them.

**The tab is reordered around how it is used:** projection, chart, what-if,
historical swing tables — with the decision banner, scores, odds, target
ladder and trade plan moved behind a collapsed "More details" (kept, not
deleted, including the rejected range-position and regime readings). The
scan is now organised by reversal status — already reacting, then in the
zone, then approaching — with adequate samples ahead of thin ones and
distance breaking the tie, ranked by a comparator chain over named fields
rather than any composite score.

Nothing new was added that testing did not support: the regime, range-
position and velocity filters stay rejected and were not retested until
they passed; no indicator, similarity engine or model was introduced.
2,498 Python tests green (93 in the projection suite), 176 node guards
(69 in the reversal-UI guard), 43/43 browser checks at 1500px and 390px.

## v4.52 — What "already reacting" can and cannot mean

Jerry noticed that the bounce scan's page one was full of names that had
barely ticked up off a shallow decline, while a deep, well-sampled name sat
a hundred rows down. He was right about the mechanism, and measuring it
changed what the fix should be.

**The asymmetry is structural.** BOUNCING (FADING) OFF ZONE requires price
to travel back OUT of the typical band. From an extreme that merely grazed
the near edge that is a 1% move; from one sitting deep inside the band it is
a very large one. So the reaction states can realistically only fire near
the shallow edge: across 1,197 scanned watchlist symbols the median reacting
row had penetrated 14% of its band against 46% for a row still IN ZONE, and
43% of reacting rows were under 10% penetration.

**The obvious fix makes the list worse.** Four alternative orderings were
measured over the 434 active down-swings against the what-if edge
(target-first minus stop-first from today's depth — the only outcome-like
criterion the ordering itself does not use): shipped +1pp; deepest-first
within status −2pp; reacting and in-zone merged then deepest-first −4pp;
penetration-banded 0pp with the median cohort collapsing from 24 names to
10. Deeper penetration selects the swings that simply kept falling.
Promoting by stage or by sample size was flat to worse too. **The default
order is therefore unchanged** — status, then adequate sample, then distance
to the median, exactly as specified.

**What does work is a filter, not a sort.** `band_penetration_pct` (0% at
the near edge, 100% at the far one, like the 20-day range position) is now a
visible, sortable "Into zone" column, and the scan gains a stage filter that
hides swings the walk-forward run says the conditional projection cannot yet
read. It uses the already-validated 100%-of-median boundary rather than a
new threshold, and it restricts the list instead of silently promoting rows:
removing the 286 early-stage down-swings moves a deep, well-sampled name
from about #106 to #19 — because 286 rows the engine cannot speak to are
gone, not because it now claims to predict better. Off by default; the scan
never hides a row unless asked.

99 Python tests in the projection suite (6 new), 78 node guards (9 new),
17/17 browser checks at 1600px and 390px.

## v4.53 — A scan button where the scan is read, and a legend off the candles

**The reversal scan gets its own `Scan now`.** It reads the shared watchlist
board, so it had no way to refill itself — the only trigger lived on another
tab. The button fires the same `/api/watchlist_table/scan`, polls until the
server reports the scan finished, disables itself with live progress while
it runs, and prints when the board was last filled plus the 9 AM / 6 PM ET
auto-refresh. Its tooltip says plainly that this is the SAME full watchlist
scan (minutes, every tracked symbol) and that it refreshes the six other
cards reading that board — a per-tab refresh button that quietly costs
minutes and moves other screens would be worse than no button.

**The chart legend moved out of the crosshair overlay.** It had grown to
eleven entries and two lines, and floating that over the plot put an opaque
band across the price action it describes. It now renders as a block above
the canvas; the one-line OHLC readout stays floating, where it belongs next
to the cursor. This also revived the legend's own tooltips, which had been
unreachable inside a `pointer-events: none` layer since they were added.

2,504 Python tests green, 176 node guards (87 in the reversal-UI guard,
9 new), 21/21 browser checks at 1600px and 390px.

## v4.54 — A swing means something different on Coca-Cola than on Coinbase

Jerry looked at a Mastercard chart and said it had no down line, and that
every chart should get its own algorithm because not every stock falls 30%
and rallies 25%. He was right, and measuring it made the size of the problem
plain.

**One threshold could not mean one thing.** At the fixed 12% setting the
watchlist segmented into anything from 2.6 swings a year (KO) to 38 (COIN).
Quiet names got single "swings" spanning more than a year — MA's longest leg
was 531 days, PG's 452 — and their comparison sets starved: KO's live
projection stood on ONE comparable episode, JNJ's on four. Worse, the
threshold rather than the stock was setting the answer, because every leg is
at least the threshold by construction: at 12% the "typical swing" came out
17–22% for a utility and for a rocket alike.

**The threshold now scales to the stock's own travel** — k × the median
absolute 20-day move, clamped, with the three sensitivity settings as
multipliers on both k and the clamps. k = 2.5 arrives from two independent
directions: solving per symbol for a comparable number of swings a year
gives a threshold/travel ratio with median 2.48 and correlation **0.987**
across 22 symbols, and the walk-forward sweep over 32 symbols preferred 2.5
on days-coverage (50.2% against the 50% ideal). MA resolves to 10.2%, KO to
6.7%, COIN to a capped 18.0%.

**The display filter was half the bug.** Fixed at 15% it hid 28 of KO's 33
completed declines, which is literally why a quiet chart could look as
though it had no down legs. It now scales at 1.25× the threshold — the ratio
the app's own 12%/15% defaults already implied. MA's six-month view goes
from one long red line to two.

**What this does NOT do, measured rather than assumed:** it does not make
the projection more accurate. Band coverage 45.2% fixed versus 44.9%
adaptive; error relative to the swing being projected 27.0% versus 26.9%;
days-coverage 49.9% versus 50.2%. Absolute MAE in percentage points *looks*
much better (5.88 → 4.30) and that number is worthless — a smaller threshold
makes smaller swings and shrinks the error for free, which is why only
scale-free measures are quoted. What changes is whether there is a sample to
stand on: the median live cohort goes 17 → 24, and symbols projecting from
fewer than six episodes go from 5 in 32 to none.

The card now states which threshold it used, the travel it was scaled to,
whether the cap bound, and what the tables are hiding. An explicit `pct=`
still wins and is labelled as explicit; too little history falls back to the
legacy fixed setting and says so.

Also fixed, found by the browser harness rather than by reading: changing
ticker and sensitivity within a moment of each other left two requests in
flight, and the older one could land last and paint the wrong symbol's
swings. Only the newest request may write now.

2,504 Python tests green (115 in the projection suite, 17 new), 176 node
guards (96 in the reversal-UI guard, 9 new), 14/14 browser checks across a
quiet, a normal and a wild stock at 1600px and 390px.

## v4.55 — The zigzag draws every leg, so the line never breaks

Jerry wanted the chart's line to always alternate up, down, up — and said
not to do it if it disturbed the algorithm. It does not, and the reason is
worth writing down: the connector was being drawn from the swing TABLES,
which hide legs under min_move_pct so they stay readable. Wherever a run of
small legs sat under that filter the line simply stopped, and a high
connected to nothing.

The payload now carries `zigzag_legs`: every leg the zigzag actually found,
including the unfinished one, each flagged `major` (big enough for the
tables) or not. The chart draws its connectors from that list — solid for
major legs, dotted for the small ones that keep the shape continuous, dashed
for the leg still in progress. Markers and labels still come from the
tables, so a table-row click still highlights its own leg and the chart does
not fill with labels.

It is presentation only, and there is a test that says so rather than a
comment: changing min_move_pct moves the `major` flags and nothing else —
same leg count, same dates, same percentages — and the projection's zone is
byte-identical across the change. The engine module never mentions the list
at all, which is also asserted.

2,526 Python tests green (121 in the projection suite, 6 new), 176 node
guards (102 in the reversal-UI guard, 6 new).

## v4.56 — Candle states: Sectors, Market Context, Gamma Exposure

Three new dashboards, built on two new pure engines and one live layer.
Nothing here introduces a second architecture: the states are computed
inside the watchlist scan that already downloads the bars, the sector list
comes from the board column that already carries it, the option chain comes
through the Schwab client that already normalizes gamma and open interest,
and all three tabs ship as one lazy chunk.

**What a state is.** Every bar classified against the bar before it on the
same timeframe, from two highs and two lows: inside (1), directional up
(2U), directional down (2D), outside (3). Nothing in the code says a 2U is
bullish, because whether it is depends on the timeframe above it and the
engine cannot see context it was not given.

Two conventions are pinned by test rather than by comment. Taking out an
extreme means EXCEEDING it, so every comparison is strict and a bar that
matches the prior high exactly is inside — the alternative puts a 3 on every
flat, thin day. And weekly through yearly candles are CALENDAR buckets, not
rolling windows; a rolling five-day high would make the weekly state churn
daily. Weeks are ISO weeks, which is the only numbering that does not split
the week containing January 1st into two and invent a one-day weekly bar
every January.

**Stored extremes, not stored states.** The scan writes each symbol's
current and prior high/low on all five timeframes — about twenty numbers —
and the live layer re-reads them against a batched quote. A stored STATE is
wrong the moment price makes a new high; stored extremes never are. The
merge is max/min, so it is idempotent. `get_board()` keeps them off the wire
by default: roughly a megabyte of JSON that only `market_state` can use.

**Live means the regular session, and only once it has begun.** Schwab's
quote carries the regular-session high and low, which are stale or zero
pre-market, and a hundred shares through yesterday's high at 7 AM is not a
2U. Before the open the states are the settled ones from the last close and
every payload says which it is showing.

Two things the tests caught that reading did not. The calendar date is the
wrong session key twice a week: bucketing by it on a Sunday opens a daily
candle in a Sunday bucket, finds it empty, and blanks the whole dashboard
all weekend. And the first version of the rollover blanked a state whenever
a new period began without a live quote — which is every weekend, every
pre-market, and any morning the scan is a day behind. It now rolls only when
there is a quote to open the new candle with, and otherwise shows the last
settled candle labelled with its own date.

**Gamma exposure states its model on screen.** Dealer positioning is not
published by anyone, so every GEX figure is open interest times gamma times
an assumption about who is on which side. Calls positive, puts negative —
the standard convention, and the one the existing SPY gamma read already
used, so the two agree by construction. Figures are dollars of dealer delta
per 1% move, which is what makes a $6 stock and a $600 stock comparable. The
convention string travels with the payload and is rendered, so a screenshot
cannot lose it.

The flip level re-computes each contract's Black-Scholes gamma across a grid
of hypothetical spot prices rather than cumulatively summing today's
per-strike figures — the cheap version holds gamma fixed at the one thing
that moves. Contracts without an implied volatility or a usable expiration
cannot be re-priced, so they leave the profile (not the per-strike totals)
and the covered share of open interest is reported beside the answer. No
crossing inside the grid is reported as no crossing, not extrapolated.

Development fixtures for the chain are OFF unless `GEX_DEV_FIXTURES=1`. This
dashboard places real trades, and a synthetic chain that appears whenever
the broker token happens to be expired is a trap no badge fully defuses.

**The market map** is a squarified treemap — the naive ordering produces
slivers whose relative size nobody can read, which defeats the only thing a
treemap is for. Forty names per sector are drawn and what was dropped is
counted on screen.

Three defects the browser harness found that no static check could: the
constituent list destructured `useBoundedList` as an object when it returns
a pair, which threw at render; a segmented control dropped straight into a
card head rendered as a vertical strip, because `.card-head > div` stacks
its children; and at 390px the five-option sort control was clipped with its
last option unreachable. All three now have guards.

2,647 Python tests green (121 new across three suites), 288 node guards (112
in the new candle-state guard), 85/85 browser checks at 1600px and 390px
against the real engines.

## v4.56a — The Korea tempdir flake: a thread that started on import

An intermittent failure that had surfaced twice, in two different tests,
under two different names:

    OSError: [Errno 39] Directory not empty: 'korea'
    OSError: [Errno 39] Directory not empty: 'bars'

Both times it landed during a `TemporaryDirectory` teardown in a test that
had nothing to do with whatever was writing. Twice I looked at it, guessed
at the mechanism, and left it alone because it was not reproducible on
demand. The guesses were wrong. What settled it was measuring instead:
running the suite under a hook that dumps `threading.enumerate()` at exit.
One thread was still alive — `korea-capture`.

The chain, once seen, is short. `options_dashboard.py` called
`korea_capture.start()` at MODULE SCOPE, so importing that module started a
daemon thread that fetched Korean market data and wrote bar caches under
`korea_lead._DATA_DIR`. Any test that imports the dashboard — the HTTP
smoke tests, the security tests — started it, and it then ran for the rest
of the process. Meanwhile every Korea test points `_DATA_DIR` at its own
`TemporaryDirectory`. The thread wrote a bar file into one while `rmtree`
was deleting it.

Two things were wrong, and both are fixed.

The loop now starts in `serve()`, beside every other background worker.
The original comment said it started at import "rather than lazily on first
request because the whole point is to record mornings nobody was watching"
— that intent is untouched, because the server is what runs around the
clock. Only importers stop getting a thread. Verified both ways: importing
the module now spawns nothing, and `/api/korea_forward/status` still reports
`running: true` under `--serve`.

And `korea_lead.configure()` with no arguments now resets `_DATA_DIR` too.
Every Korea test registers a bare `configure()` as its cleanup, right beside
the tempdir cleanup and correctly ordered to run first — but it reset every
provider and every memo and left the path pointing at the directory about to
be deleted. Resetting a provider but not the path it writes to is a
half-reset, and a half-reset only ever shows up as an intermittent failure
somewhere else. Every reader of `_DATA_DIR` already guards on falsy and
`None` is the module's own initial value, so this returns it to a state the
code was already written for.

Five guards, and the thing that makes them worth having is that all five
FAIL against the old code — including one that reproduces the actual bug
deterministically: reset the module, write a bar, and assert the old
directory is still empty. It was not; it contained `_KS11.json`.

2,652 Python tests green across four consecutive full runs, and no thread
survives the suite.

## v4.57 — Three fixes from the first real look at v4.56

**SPY and QQQ returned no gamma at all.** Asking Schwab for a chain with no
date filter returns EVERY listed expiration. On a single-name stock that is a
handful; on SPY it is sixty-odd expirations of dailies, weeklies, monthlies
and LEAPS, and at the wide strike count gamma needs that is tens of thousands
of contracts. `SchwabClient._get` allows fifteen seconds and then returns
None, so the tab reported — accurately and uselessly — that no chain was
available, for the two symbols anyone opens it for first. MU worked, which is
what made it look like a symbol problem rather than a size problem.

The chain is now fetched in two bounded steps: one narrow-ladder call to
enumerate the expirations, then one call for only the expirations being
measured, bounded by a date range Schwab filters server-side. Neither grows
with how many expirations the underlying lists. The multi-expiration option
is capped at the nearest eight and the dropdown says so rather than promising
"all", because summing every SPY expiration is the unbounded call again under
another name.

The same unbounded fetch is why the header's SPY gamma read had been showing
a dash. Fixed the same way.

**Gamma Exposure now follows the app's ticker.** It was taking the ticker as
initial state only, so changing the symbol anywhere else left the tab behind.

**The market map was purple and teal.** The colour is built by mixing --up or
--down toward a neutral by the size of the move, and the mix target was
--bg-3 — the panel behind the map. --bg-3 is a navy at hue ~228 with real
chroma, so in oklch a green at 152 interpolated toward it came out teal and a
red at 25 came out purple.

The first fix was wrong too, and the browser caught it: mixing toward
oklch(L 0 0) still rotated the hue, because a hue of 0 is not a powerless
hue — it is the red direction. Measured on screen, a +3.6% gainer rendered at
125 degrees, a +1.7% gainer at 81, and a +0.3% gainer at 30. Green, then
yellow-green, then orange.

It now mixes in sRGB toward an equal-channel grey, which cannot rotate a hue
by construction: mixing (r,g,b) with (k,k,k) scales every channel difference
by the same factor, and hue is a function of those differences alone. Measured
after: 148 degrees for every gainer and 1 degree for every loser, identical in
both themes and holding on sub-1% moves.

**And most of the map was blank.** Forty names across eleven sectors in a
520-pixel-tall map averages about 34 pixels square, which is under every label
threshold — so the map rendered as a field of unlabelled blocks and read as
missing data rather than as small holdings. The height is now derived from how
many rectangles have to fit, labels are sized to their rectangle instead of
being switched off below a fixed width, and how many names per sector is the
reader's choice. 230 of 242 rectangles carry their ticker now.

2,652 Python tests, 129 UI guards (13 new), 82/82 browser checks, and a new
12-check colour harness that measures rendered hue in both themes.

## v4.58 — Best Setup

One card at the top of the Trade tab that combines every layer already in the
app — weekly range, streaks, swing maturity, premium, implied volatility,
probability, liquidity, gamma exposure — into a single recommendation: side,
expiration, strike, delta, credit, the reasoning and the specific risks.

The delta is not predetermined. It is solved for.

**The circularity that had to go first.** The first version mapped a measured
keep rate to an implied delta of (1 − keep). That returns (1 − target) no
matter what the data says — it looks like analysis and computes nothing. The
engine now solves for a DISTANCE instead: the measurement says price stayed
inside 8.1% of spot 85% of the time over 181 windows, and the market
independently quotes whatever delta it likes at 8.1% out. The gap between
those two numbers is the only honest edge here, and it is only an edge
because they come from different places.

Everything else is a refusal rule. Widening needs 30+ windows and a 5-point
edge over the unconditional baseline; sizing is on the Wilson lower bound,
never the point estimate; a driftless lognormal at ExpectedRV has to agree
about the same distance before the band opens; the hard cap is 0.45 on any
evidence. Gamma exposure can shrink a trade or veto it, never open one.

Four defects surfaced only by running the thing against real data. Every one
of them passed the unit tests first.

**Model probabilities were off by a factor of a hundred.** premium_edge
returns p_itm_model as a fraction — 0.0627 means 6.27%. The engine read it as
a percentage, so a 6% chance of assignment rendered as a 99.9% keep rate and
the card said "finishes in the money about 0% of the time". The unit tests
missed it because the fixture wrote percentages too: a fixture speaking a
different dialect from the producer it stands in for tests nothing. Fixed at
the boundary, fixture corrected, and the unit contract pinned.

**Both conditioning branches were dead.** watchlist_table emits the strings
"up" and "down"; the engine compared them against 1 and -1, which is silently
false. The swing branch read a key called cohort_bar_index that no producer
has ever written. The symptom was not a crash — it was 44 of 44 symbols
reporting "nothing about today is unusual", which reads like a calm market.
After the fix, 22 of 44. swing_projection now exports cross_bar_index, the
bar at which each past swing had come as far as this one has, so there is one
definition of "this deep" rather than two.

**Gathering evidence could make the answer worse.** A measured floor of 8.1%
that the market happened to quote at 14.8 delta rejected every strike on the
ladder — including the 18-delta one the default rule would have taken without
complaint. The reward for having 181 windows of history was no trade at all.
Evidence may now only ADD candidates: a contract the default band accepts
stays eligible whatever the measurement found.

**An opposing gamma reading collapsed the band to a sliver.** Scaling only
the ceiling of 0.15–0.22 leaves 0.15–0.176, a window 2.6 delta points wide
that a real chain steps straight over, so the card returned "no contract"
instead of a safer one. Both edges move now — an opposing reading means sell
further out, not sell nothing.

And one thing the browser found that no test would have: the card was
recommending trades with negative expected value. Sell a put, MODERATE
CONFIDENCE, expected value −$20. A high probability of keeping the credit is
not the same thing as a profitable trade, and letting the confidence badge
carry that news still puts a recommendation on screen. Those are refused by
name now, with the contract that came closest and by how much it fell short.
A no-trade day is a finding and renders as one.

2,740 Python tests (88 for this feature), 54 UI guards, 26 browser checks
against the real engine on real Nasdaq history, and Layer 1 + Layer 2 of
verify_frontend green.

## v4.59 — Worth selling today

A board on the Trade tab that ranks the watchlist by how rich each option
is against what that stock itself realizes, and says which names to skip
and why.

This is what came out of asking whether the Best Setup card actually makes
money. It does not, in the way it was designed to.

**The measured-distance rule is inert, and the arithmetic says it must be.**
Backtested point-in-time over 597 entries it fired zero times in the useful
direction. The rule demands 85% at the Wilson LOWER bound; with the ~45-70
windows conditioning actually produces, clearing that bound needs a 95%
point estimate, and a 95%-keep distance sits further out than a 15-delta
strike. Lowering the bar does not help: at a 65% bar it still pointed
further out 41 times out of 50.

The deeper reason is that an 85% win rate over 30 days IS the 15-22 delta
band — measured realized keep at 18.5 delta is 83-85%. The rule and the
default agree with each other, so there was never a gap between them to
harvest. More premium at that horizon means a lower win rate, which is a
risk-appetite decision and needs no measurement to make.

**And the earlier "there is edge at every delta" reading was mostly an
artefact.** Strikes were placed using a modelled IV at 1.10x trailing
realized. At 1.00x the edge at 15 delta is -0.1 points; at 1.30x it is
+4.8. The constant was doing the work. The model also has no skew at all,
which cuts against the same conclusion. That analysis cannot settle the
delta question, and the app's own recorded IV30 is the only thing that
will — the Backtest tab reports `modeled` until ~60 days accumulate.

So the board makes the narrower claim the data supports: pick the names and
the days, not the strike. Earnings inside the option's life EXCLUDES here,
which is the opposite sign to the Premium Edge scan's +30 bonus, because a
seller who holds to expiry underwrites the report rather than harvesting
it. Ranking prefers where today's premium sits in that stock's own history
and falls back to the raw ratio — labelled RATIO, not PCTL — when too few
readings exist for a percentile.

It measures nothing itself: the rows come from the Premium Edge scan that
already computes them, because re-deriving would cost a chain fetch per
symbol to reach the same numbers.

2,782 Python tests (37 new), 67 UI guards (13 new), 19 browser checks
against the real module, and Layer 1 + Layer 2 of verify_frontend green.
