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
