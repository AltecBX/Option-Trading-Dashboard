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
