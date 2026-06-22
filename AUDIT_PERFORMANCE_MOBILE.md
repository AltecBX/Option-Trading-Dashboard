# Performance & Mobile Audit — Option Trading Dashboard

_Audited at app version 2.20. This report reflects the **current** state of the
repo, including optimizations already shipped in v2.06–v2.20. It is the basis
for the remaining phased work; nothing in trading-calc paths is changed by the
audit itself._

## Architecture map

- **Backend:** single-process `http.server`-based handler in `options_dashboard.py`
  (~6.8k lines) plus modules: `schwab_client.py`, `unusual_whales_client.py`,
  `analyst_client.py`, `analyst_board.py`, `watchlist_table.py`, `swings.py`,
  `movers.py`, `trend.py`, `news.py`, `storage.py`. Data sources: Schwab (when
  configured), yfinance (fallback), Unusual Whales (when configured).
- **Frontend:** static React (UMD) loaded by `index.html`. JSX sources are
  precompiled by `build_frontend.js` into `*.js` (no Vite/Next). Files: `app.jsx`
  (shell/state), `app-cards.jsx` (all cards/scanner tables), `charts.jsx`
  (TradingView lightweight-charts + SVG), `strategies.jsx`, `recommendation.js`,
  plus `weather/journal/tooltips/tweaks-panel/data/config`.
- **Tabs:** Trade, Discover, Analyze, Patterns, Watchlist, News, Flow, Scanners,
  Manage. `TabPanel` lazy-mounts a tab on first activation then keeps it alive.
- **Persistence:** server-side watchlist + prefs in `JERRY_DATA_DIR` (`/data` on
  prod) with `.bak` mirror + repo seed (`watchlist_seed.json`). Client settings
  in localStorage (keys below).

### API endpoints (per-symbol unless noted)
`/api/ticker` (main payload), `/api/quote`, `/api/search`, `/api/news`,
`/api/swings`, `/api/basing`, `/api/earnings_ladder`, `/api/earnings_iv_crush`,
`/api/pullback_profile`, `/api/pullback_backtest`, `/api/pullback_scan`,
`/api/reprice/chain`, `/api/option_quote`, `/api/backtest`, `/api/analyst`,
`/api/trade_builder/multi_exp`, `/api/strategy/ema_pullback[_state]`,
`/api/fade[/save]`, `/api/trade_journal`.
**UW:** `/api/uw/{flow_score,flow_alerts,flow_trades,strike_flow,greek_exposure,
net_premium,option_chains,premium_richness,momentum,market_dashboard,market_tide,
sector_flow,market_scan_candidates,market_scan_score,health,debug}`.
**Boards/scanners (universe):** `/api/scan`, `/api/weekly_range`, `/api/movers[/scan]`,
`/api/trend[/scan]`, `/api/ivrank[/scan]`, `/api/analyst_board[/scan]`,
`/api/watchlist_table[/scan]`.
**State/infra:** `/api/watchlist[/diag]`, `/api/watchlist_alerts[/dismiss]`,
`/api/prefs`, `/api/data_source`, `/api/broker/{accounts,positions}`,
`/api/push/{status,test,roll_flag}`.

### Frontend polling loops (`setInterval`)
| Cadence | Where | Gated when hidden? |
|---|---|---|
| 1s | `LiveClock` (isolated component) | ✅ |
| 5s | live quotes (`app.jsx:1088`) | ✅ `skipWhenHidden` + market-hours |
| 30s | data_source, strike_flow (`app.jsx:189/235`) | ✅ |
| 60s | uw health, market dashboard, flow score cards | ✅ |
| 5m | watchlist alerts | ✅ |
| 15m | roll manager | ✅ |
| 60m | earnings crush | ✅ |
| board polls | watchlist_table / movers / trend / ivrank / analyst (`pollRef`) | mixed |

**Status: most intervals already pause on `document.hidden`.** Remaining gaps:
board `pollRef` loops poll while their Scanners sub-tab is mounted-but-hidden
(TabPanel keeps mounted), and they aren't gated on the active sub-tab.

### localStorage keys
`weeklyOptionsTimer.settings.v1` (STORAGE_KEY), `…tweaks.v1` (TWEAK_KEY),
`weeklyOptionsTimer.tabOrder` (TAB_KEY), `…marketDash.open.v1`,
`…oiChart.metric.v1`, `jerryDash.sizing.v1`, `jerry_acct`, `jerry_riskpct`,
`POSITIONS_KEY`, `SKEW_KEY`, `BASING_PREFS_KEY`, `EMA_PARAMS_KEY`,
`PB_DIR_KEY`, `PBSCAN_DIR_KEY`, `PB_BACKTEST_KEY`, `WX_KEY`.

---

## A. Top 20 performance problems (ranked by impact)

> ✅ = already fixed in v2.06–v2.20. ⬜ = open.

1. ✅ `/api/ticker` rebuilt the whole payload with **6 serial network loaders** → now parallelized (`ThreadPoolExecutor`), ~3–5s → ~1.5–2s.
2. ✅ vol-rank **re-downloaded a year of history** already held by `load_daily` → reuse (~1s saved).
3. ✅ No short-TTL cache on `/api/ticker` → 10s TTL; tab-flip back is instant.
4. ✅ `_fundamentals` (`.info` + earnings, retried/back-off) ran per symbol every scan → **12h cache**; repeat "Scan now" minutes → seconds.
5. ✅ `/api/swings`, `/api/basing`, `/api/earnings_ladder` uncached → TTL caches (90s/60s/900s).
6. ✅ `/api/swings` did its **own yfinance 1y pull** → reuses cached `load_daily` for the 1y window.
7. ✅ `apiFetch` had **no dedupe/cache** → in-flight dedupe + 4s TTL + `noCache` bypass.
8. ✅ FlowScore (×2) + Analyst cards refetched on **every 5s quote tick** (`currentPrice` dep) → price via ref, ~12/min → ~1/min.
9. ✅ 1-second clock re-rendered the **entire app** → isolated `<LiveClock>`.
10. ✅ ~17 heavy cards not memoized → `React.memo` at publish layer.
11. ✅ TradingView chart **fully rebuilt on style toggle** → series-swap only; zoom preserved.
12. ✅ 550-row watchlist rendered all rows → progressive rendering (120/chunk + scroll).
13. ⬜ **Universe scanners (`/api/scan`, `/api/weekly_range`, board scans) iterate symbols one-at-a-time on the main scan thread**, ~0.15s sleep + per-symbol upstream calls → a full 549-name watchlist scan takes minutes. Needs a bounded concurrency pool (4–8). _Highest open item._
14. ⬜ Scanner boards (`movers/trend/ivrank/analyst`) **don't progressively stream**; the board only updates at scan completion (`_STATE["rows"]` set at end), so the UI shows stale rows for the whole scan.
15. ⬜ `/api/ticker` `daily` payload returns ~260 bars × ~10 fields always; mobile charts only need ~90. Trim by request param for mobile.
16. ⬜ Other big screener tables (movers/trend/ivrank/analyst) are **not virtualized/windowed** like the watchlist (currently small, but unbounded if universes grow).
17. ⬜ `_compute_flow_score` is recomputed by several endpoints per symbol; only the underlying UW call is cached (60s in the UW client), not the assembled score.
18. ⬜ No **dev-mode timing** to quantify any of this (added in this phase).
19. ⬜ `build_payload` still fetches `yf.Ticker().info` even when Schwab is the source; `.info` is the slowest yfinance call and could be cached like `_fundamentals`.
20. ⬜ Several cards fetch on mount even when their **Scanners sub-tab isn't the selected sub-tab** (board pollRefs) — wasted upstream calls.

## B. Top 20 mobile UX problems (ranked by impact)

> The app already has a mobile header, drawer overlay, ~40 media queries,
> `viewport-fit=cover`, and Apple PWA meta. These are the **remaining** gaps.

1. ⬜ **Wide data tables** (watchlist 40+ cols, option chain, scanner boards) force horizontal scroll inside a scroll container; on phones the sticky first column + horizontal scroll is hard one-handed. Need a "card row" collapse for `<700px`.
2. ⬜ **No global horizontal-overflow guard** — any one over-wide element can cause whole-page sideways scroll on Safari.
3. ⬜ Some **touch targets < 44px** (column header sort chips, filter chips, the tiny `↻`/icon buttons, row context actions).
4. ⬜ Charts use **mousemove hover crosshair**; on touch this fights scroll and has no tap-to-inspect affordance.
5. ⬜ `maximum-scale=5` allows pinch (good for a11y) but several panels still **require** pinch because min-width content overflows the viewport.
6. ⬜ Safe-area insets applied in only 2 places — sticky header/bottom and the drawer should pad `env(safe-area-inset-*)` consistently (notch/home-bar).
7. ⬜ Top tab bar is horizontally scrollable but **active tab can scroll out of view**; no scroll-into-view on change.
8. ⬜ Large cards with dense text (analyst, flow verdict, swing history) overflow small screens; need collapsible "advanced" sections.
9. ⬜ Loading: some cards show stale/old-symbol data briefly on switch (Patterns fixed in v2.17; others remain). Skeletons inconsistent.
10. ⬜ `position: sticky` headers inside scroll areas can **jitter** on iOS Safari momentum scroll.
11. ⬜ Number inputs (Acct $, risk %, deltas) don't set `inputmode`/`enterkeyhint` → wrong mobile keyboard.
12. ⬜ Landscape phone: `@media (orientation: landscape) and (max-height:500px)` exists but only for one panel; chart/tab bar not optimized.
13. ⬜ Horizontal scroll tables lack momentum/`-webkit-overflow-scrolling: touch` in places.
14. ⬜ No explicit `touch-action` on draggable chart/option-chain handles → scroll hijack.
15. ⬜ Tap highlight / `:hover` styles stick on touch (no `@media (hover: hover)` guard) → "stuck hover" look.
16. ⬜ Dropdown `<select>`s and modals can render off-screen on small widths.
17. ⬜ Font sizes scale via `!important` overrides at breakpoints, but some monospace tables go to 10.5px → unreadable on phones.
18. ⬜ Scanner progress on mobile is a thin bar; failed tickers aren't surfaced.
19. ⬜ No bottom-nav option; top tabs require a reach to the top of the screen one-handed.
20. ⬜ The sidebar drawer holds critical controls (ticker, weeks, delta, baseline) — fine, but no quick "ticker switch" affordance from the main content on mobile besides the header.

## C. Backend bottlenecks
- **Sequential universe scans** (#13/#14) — the dominant remaining backend cost.
- `.info` calls (`build_payload`, anywhere) — slowest yfinance path; cache aggressively (`_fundamentals` already does; `build_payload` does not).
- Assembled-score recomputation (`_compute_flow_score`) not cached at endpoint level.
- All caches are **in-process TTL dicts** (correct for a single Railway dyno; documented — no Redis/DB needed yet).

## D. Frontend bottlenecks
- Mostly addressed (memo, clock, progressive render, chart swap). Remaining: board tables not windowed; some cards fetch when their sub-tab isn't active; no dev timing.

## E. Data-fetching bottlenecks
- `apiFetch` dedupe+TTL shipped. Remaining: per-endpoint TTLs are uniform 4s client-side (fine); **no AbortController on ticker change** (effects use a `cancelled` flag to drop late setState, but the HTTP request isn't aborted — low impact given dedupe/cache, listed for completeness).
- First load fetches: Trade-tab essentials + several always-on polls (quotes, data_source, uw health, market dashboard) start immediately. Could defer P3/P4 to idle.

## F. Rendering bottlenecks
- Watchlist windowed ✅. Option chain renders ~25 visible strikes (OK). Charts: SVG `PriceChart` recomputes paths on data change only (effect-gated) ✅; TradingView uses `setData` ✅. Remaining: throttle chart pointer handlers; gate hover logic behind `hover: hover`.

## G. Safe implementation phases
- **Phase A (this PR):** Audit doc + dev-only perf instrumentation + deliverable docs (`PERFORMANCE_CHANGES.md`, `MOBILE_QA_CHECKLIST.md`, `SECURITY_NOTES.md`) + safe global mobile hardening (overflow-x guard, `hover:hover` guard, larger tap targets on header controls, `inputmode` on number inputs). **No trading-calc changes.**
- **Phase B:** Bounded-concurrency pool for universe scanners (`/api/scan`, `/api/weekly_range`, board scans) — config via env `SCAN_CONCURRENCY` (default 6). Per-symbol result cache + progressive board updates + failed-ticker reporting.
- **Phase C:** Mobile table→card-row transform for watchlist/scanner/option-chain at `<700px`; chart tap-to-inspect; tab-active gating for board polls.
- **Phase D:** First-load priority/idle deferral of P3/P4 data; trim `/api/ticker` daily payload for mobile.
- **Phase E:** Endpoint-level cache for `_compute_flow_score`; cache `build_payload`'s `.info`.

## H. DO NOT CHANGE (trading-logic surfaces)
- Option strategy math, Greeks, breakevens, P/L: `strategies.jsx`, `recommendation.js`, and the strike/leg math in `app.jsx`.
- Edge / flow scoring: `computeWatchlistEdges` (`app-cards.jsx`), `_compute_flow_score` / `_flow_metrics` (`options_dashboard.py`).
- Swing detection & rhythm: `swings.py`, `_swing_read` (`watchlist_table.py`).
- Vol-rank / HV math, EV/size trade-ticket formula, premium-richness, IV-rank.
- Watchlist semantics: server-side store, `.bak`/seed recovery, the **anti-clobber shrink guard** (v2.19), and the auto-reconcile threshold (v2.20).
- Saved settings, tab order, baseline/weeks/expiration behavior.

Performance work must preserve identical **outputs** of all the above — only how
fast/when they run may change.
