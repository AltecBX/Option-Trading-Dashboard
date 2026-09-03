# Performance Changes

Chronological log of performance/reliability work, newest first. Every change
preserves trading-calc outputs (see `AUDIT_PERFORMANCE_MOBILE.md` §H).

## v4.75 — one fetch per thing, however many ask for it
- **Price history is cached by the year-bucket Schwab serves, not by the
  caller's day count** (`schwab_client.get_price_history`). The dashboard's
  weekly loader (394 days) and daily loader (580 days) are the same two-year
  request; a dozen scan callers ask for 10, 30, 200, 400, 730, 900, 1400 days
  of one symbol. Each was its own broker call. One fetch per bucket now serves
  all of them, a narrower ask is sliced from a wider cached bucket, and a
  request that finds an identical fetch in flight waits for it instead of
  starting another. Output is bar-for-bar identical (`full[-days:]`, as before).
- **`/api/ticker` builds once per key at a time** (`_ticker_payload`). A
  symbol switched away from and back to mid-build used to start a second
  full build concurrently with the first.
- **Earnings dates are fetched once per symbol per hour** (`_earnings_dates`).
  Both earnings loaders read yfinance's `earnings_dates`, in parallel, in every
  build. Empty answers are kept only five minutes — yfinance answers a throttle
  with an empty frame, and an empty frame must not read as "no earnings" for an
  hour. Failures are not kept.
- **The broker client's cache is bounded** (`_CACHE_MAX`): expired entries were
  never removed until read back, so a scan left every symbol resident.
- Guards: `test_history_cache.py` (21; 16 fail against the prior code).

## v2.21 (this phase) — measurement + audit + docs
- **Dev-only perf instrument** (`app.jsx`, `_PERF`). OFF in production. Enable with
  `localStorage.setItem("jerryDebug","1")` (reload) or append `?debug` to the URL.
  Logs to the console: app-eval + first-render marks, time from ticker change to
  Trade-tab usable, per-endpoint timing + payload size + cache-hit counts, and a
  `console.table` report ~3s after first load and ~1.5s after each ticker change.
  Call `window.__PERF.report()` anytime. Zero logging when disabled.
- `AUDIT_PERFORMANCE_MOBILE.md`, `MOBILE_QA_CHECKLIST.md`, `SECURITY_NOTES.md` added.
- Safe mobile micro-wins: numeric `inputmode` on Acct/Risk inputs; iOS momentum
  scrolling on the watchlist table container.

## v2.20 — watchlist board auto-reconcile
- Watchlist tab auto-kicks a scan when the board is 25+ symbols behind the list
  (10-min global cooldown), so the table self-fills instead of showing a stale
  snapshot. Removes the recurring "my stocks are gone" confusion.

## v2.19 — server-side anti-clobber guard (data integrity)
- `PUT /api/watchlist` rejects a destructive shrink (>50% of a 20+ list) with 409
  unless `?force=1`; client heals from the server on 409. Stops a stale tab from
  wiping the saved watchlist. Proven: 549→5 attempt blocked, list stood at 549.

## v2.18 — bulletproof watchlist seed + diagnostics
- Multi-path seed resolution (`/app`, cwd, module dir); `/api/watchlist/diag`
  endpoint (data dir, file counts, seed path, load branch); stronger client
  anti-shrink guard.

## v2.14–v2.17 — durability + scan/Patterns speed
- v2.17: cache `_fundamentals` (slow `.info`+earnings) 12h → fast repeat scans;
  Patterns clears stale data on symbol switch.
- v2.15: `/api/swings` reuses the app's cached daily history (no 2nd yfinance pull);
  scheduled GitHub Action mirrors the live list into `watchlist_seed.json`.
- v2.14: layered watchlist recovery (main → `.bak` → repo seed → defaults).

## v2.06–v2.13 — core perf sweep
- **Backend:** parallelized `build_payload`'s 6 loaders (ThreadPoolExecutor);
  removed duplicate year-of-history fetch in vol-rank; 10s TTL cache on
  `/api/ticker`; TTL caches on swings/basing/earnings.
- **Frontend fetch:** `apiFetch` in-flight dedupe + 4s TTL cache + `noCache`
  bypass; killed per-5s-tick refetch on FlowScore (×2) + Analyst cards.
- **Render:** isolated the 1s `<LiveClock>` (was re-rendering the whole app every
  second); `React.memo` on ~17 heavy cards; chart now swaps series on style
  toggle instead of full rebuild (keeps zoom); 550-row watchlist uses progressive
  rendering (120/chunk, scroll to load more).

## Known-open (next phases — see audit §G)
- **Phase B:** bounded-concurrency pool for universe scanners (`/api/scan`,
  `/api/weekly_range`, board scans), per-symbol result cache, progressive board
  streaming, failed-ticker reporting. _Biggest remaining backend win._
- **Phase C:** mobile table→card-row transform (<700px), chart tap-to-inspect,
  tab-active gating for board polls.
- **Phase D:** first-load P3/P4 deferral to idle; trim `/api/ticker` daily payload
  for mobile.
- **Phase E:** endpoint cache for `_compute_flow_score`; cache `build_payload`'s `.info`.

## How to read the dev numbers
1. `localStorage.setItem("jerryDebug","1")` then reload (or load with `?debug`).
2. Watch the console: `[perf] app.js evaluated`, `App mounted`, then per-API lines.
3. Switch a ticker → see `ticker → X: fetch start` and `Trade tab usable (+Nms)`.
4. `window.__PERF.report()` prints the per-endpoint table (calls, cache hits,
   avg ms, total KB).
