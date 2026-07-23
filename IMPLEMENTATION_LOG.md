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
