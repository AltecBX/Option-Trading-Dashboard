# Option-Trading-Dashboard — Complete Technical Handoff & Audit

**Prepared for:** an expert reviewer who will design an implementation prompt.
**Scope:** how the app works *today*, evidence-based, with real file/line references.
**No code was changed to produce this document.**

Repository snapshot: `main` @ `e62c1cb` (classic **v3.63**, "Earnings Opportunities tab").
Size: **~25.8k lines Python** across ~30 modules, **~28.7k lines JSX** (7 source files, precompiled to plain JS), **10.7k-line `styles.css`**, **90+ HTTP API routes**.

---

## 1. Product overview

**What it is.** A single-user, self-hosted **options trading cockpit** for one active retail trader ("JerryTrade", deployed at dashboard.jerrytrade.com on Railway). It is a personal terminal, not a multi-tenant SaaS: there is no user database, no login/sessions, one shared watchlist, one Schwab brokerage connection (see §6 auth). It runs as a Python stdlib HTTP server that both serves the static frontend and answers a large private JSON API.

**Who it's for.** A premium-selling / swing options trader who wants, in one place: live quotes and option chains, weekly return history, expected-move vs historical-move analysis, IV/HV context, a suggested strike + strategy menu, several proprietary scanners (reversal radar, 0DTE juice, range-location, movers, trend, analyst board, patterns, earnings), a rates/macro terminal, and account/position management. The design language is dense and terminal-like (OKLCH dark theme, JetBrains Mono numerics).

**Primary workflows** (detailed in §3): pick a ticker → read weekly range + expected move + IV context → choose an expiration/strike (by delta or buffer) → compare strategies → find premium-selling or reversal opportunities via scanners → review earnings and macro risk → journal a pick → manage positions. Most "decisions" the app supports are: *is this a good premium sale here, which strike/expiry, is the name extended, is there an earnings/macro catalyst, and which watchlist names are set up right now.*

**Major state of maturity.** The app began life as a **mock-data prototype** (`data.js` = `window.MockData`, a seeded generator "for AAPL covered-call dashboard"). Live data was retrofitted by **monkey-patching the MockData builders at runtime** (`data.js:271-301`) to read a live payload cache, falling back to synthetic data when live is absent. That origin still shapes the architecture (§4, §9).

---

## 2. Complete feature inventory

The frontend is **20 top-level tabs** (`app-lib.jsx:99-120`, `const TABS`), rendered as `<TabPanel>` blocks inside one giant `App()` component (`app.jsx:132-7954`). Order and status below. "Status" = fully functional / partial / demo-fallback / duplicated / vestigial, based on code inspection.

| # | Tab (id) | What it does | Data / endpoints | Status |
|---|----------|--------------|------------------|--------|
| 1 | **Trade** (`trade`) | The core cockpit: sidebar ticker + weekly returns chart, day-of-week chart, expected-move card, suggested call/put strikes, **Weekly Option Selling Setup** panel, strategy menu, watchlist alerts, market posture. Content is split across **5 separate `<TabPanel tab="trade">` blocks** (app.jsx:3460, 3470, 4452, 6652, 7409). | `/api/ticker` (payload → `window.__LIVE` + MockData), `/api/expected_move`, `/api/weekly_range` | Functional (primary surface) |
| 2 | **Discover** (`discover`) | Screeners hub: analyst calls, movers, trend, vol rank sub-tabs (`ScreenersHub`, app-cards.jsx). | `/api/analyst_board`, `/api/movers`, `/api/trend`, `/api/ivrank` (+ `/scan`) | Functional |
| 3 | **Analyze** (`analyze`) | Deep single-name: price chart (MA/EMA/RSI/prob-cone), options chain, valuation, strategy reference, earnings ladder/IV-crush, expected-move detail. Split across ≥4 TabPanel blocks (4276, 4442, 4488, …). | `/api/ticker`, `/api/valuation`, `/api/earnings_ladder`, `/api/earnings_iv_crush`, `/api/reprice` | Functional |
| 4 | **Patterns** (`patterns`) | Per-stock behavioral pattern discovery with statistical validation (walk-forward, bootstrap, BH-FDR), NL "ask", convert-to-scanner. | `/api/patterns`, `/api/patterns/scan`, `/api/patterns/ask`, `/api/patterns/watch(es)` | Functional (heavy compute) |
| 5 | **News** (`news`) | Aggregated headlines (Google News RSS + Finviz + yfinance), streaming ticker. | `/api/news`, `/api/finviz_news` | Functional |
| 6 | **Finviz** (`finviz`) | **Iframe embed** of finviz.com (needs the user's "Finviz Helper" browser extension to bypass frame-busting). | n/a (embed) | Functional w/ extension |
| 7 | **TradingView** (`tview`) | Iframe embed of TradingView Supercharts. | n/a (embed) | Functional w/ extension |
| 8 | **Unusual Whales** (`whales`) | Iframe embed of unusualwhales.com. | n/a (embed) | Functional w/ extension |
| 9 | **Flow** (`flow`) | UW options-flow dashboard: market tide, sector flow, GEX, net premium, strike flow, richness. Also hosts `EarningsCrushCard`. | `/api/uw/*` (14 routes) | Functional **only if `UW_API_KEY` set** (paid) |
| 10 | **Scanners** (`scanners`) | Reversal radar, open-reclaim reversal, market-wide UW scanner, **Range-location scan** (RangeEdgeScanCard). | `/api/radar`, `/api/scan/open_reversal`, `/api/range_scan`, `/api/uw/market_scan_*` | Functional (UW parts need key) |
| 11 | **0DTE Juice** (`juice`) | Premium-richness scanner scoring near-dated straddle premium vs HV + liquidity. | `/api/juice` | Functional |
| 12 | **Backtest** (`backtest`) | Natural-language backtest lab (parse → editable rules → run) for stock & synthetic-option strategies. | `/api/backtest/parse`, `/run`, `/status`, `/last` | Functional (**option premiums are modeled, not historical** — §6) |
| 13 | **Breadth** (`breadth`) | Grouped HOD/LOD market-breadth scanner over the watchlist. | `/api/market_breadth` | Functional |
| 14 | **Journal** (`journal`) | Picks journal + trade journal (win-rate tracking). | `/api/pick_journal`, `/api/trade_journal` | Functional |
| 15 | **Watchlist** (`watchlist`) | The big watchlist board: per-symbol price, 52w, earnings, sector; tag filtering; CSV import. | `/api/watchlist_table` (+`/scan`), `/api/watchlist` | Functional |
| 16 | **Streaks** (`streaks`) | Consecutive up/down streak scanner over the watchlist. | `/api/watchlist_table` (derived) | Functional |
| 17 | **Market Calendar** (`calendar`) | Watchlist earnings calendar + US economic events. | `/api/market_calendar/earnings(_extra)`, `/economic` | Functional |
| 18 | **US Treasuries** (`treasuries`) | Rates terminal: yield curve, spreads, CPI, breakevens, MOVE, auctions, COT, Fed, correlations, rate-sensitivity. Dense overview grid + collapsible depth. | `/api/treasury/{core,inflation,markets,auctions,fed,cot,sense}` | Functional (Treasury/FRED/CFTC live; MOVE/futures/ETF via delayed Yahoo) |
| 19 | **Earnings Ops** (`earnops`) | MC-style earnings opportunity scanner: score 0-100, setup/status/best-action, trade plan or NO TRADE. | `/api/earnings_scan` (+`/scan`) | Functional; **demo-fallback rows when providers down** (labeled) |
| 20 | **Manage** (`manage`) | Schwab reconnect, positions, roll manager, trade builder, push settings, broker import. Split across 3 TabPanel blocks (4220, 7214, …). | `/api/broker/*`, `/api/push/*`, `/api/trade_builder/multi_exp` | Functional (needs Schwab) |

**Sidebar controls** (persist to localStorage `STORAGE_KEY`, `app.jsx:212`): ticker search (`sb-ticker-input`, autocomplete via `/api/search`), watchlist quick-add + FV/TV/UW brand buttons, presets row, **Weeks of history** slider (4-52, `app.jsx:3168`), **Strike picker** By-delta / By-buffer toggle (`app.jsx:3175`) with a Target-delta slider (0.05-0.45) or Buffer% slider (0-10), **Return baseline** Monday/Friday select (`app.jsx:3202`), Expiration select, version pill, weather badge, market-charts strip.

**Cross-cutting components** (app-cards.jsx, ~55 exported `_memo` cards): MarketOverview, MarketPosture, MarketBreadthCard, MarketContextBar, WeeklySellSetupCard, ExpectedMoveCard, ValuationCard, VolSkewCard, IVRankCard, TrendCard, ReversalRadar family, WatchlistTable family, AnalystBoard/AnalystCard, MoversCard, RangeEdgeScanCard, FlowScoreCard, BasingCard, PullbackProfileCard, LevelRepriceCard, WinRateCard, EarningsCrushCard, PositionsCard, RollManagerCard, TradeBuilderCard, StrategyReference/StrategyCard, PercentCalc, MarketCalendarCard, NewsTicker/NewsHub, StockProfileCard, PicksJournalCard, TickerLogo, and the four side-rail extremes cards.

**Charts** (charts.jsx): `PriceChart` (SVG, main daily w/ MA/EMA/RSI/prob-cone/EM bands/pan), `ReturnsChart` (SVG weekly), `DayBarChart` (SVG day-of-week), `PLChart` (SVG strategy payoff), `ThetaPanel` (SVG theta decay), plus `TVPriceChart` and `IntradayChart` (both backed by **lightweight-charts 4.2.3**).

**Duplicated / overlapping features (noted for §9):**
- Four near-identical side-rail cards (`LeftRail52W`/`LeftRailDailyHigh`/`RightRail52WLow`/`RightRailDailyLow`, app-cards.jsx:10456/10618/10735/10876) differ only in which extreme they filter.
- Earnings appears in **three** places: the Analyze `EarningsCrushCard`/`earnings_ladder` (pre-existing), the Market Calendar earnings, and the new Earnings Ops tab.
- Two full frontends: the classic app and the **`/next` parallel app** (`next-app.jsx`, `NEXT_VERSION 4.0.5-next`) that iframes the classic one.

---

## 3. User workflow map

Primary journeys, with the exact steps and where each becomes slow/repetitive/disconnected.

**Research a stock.** Sidebar → type ticker (autocomplete `/api/search`) → App fetches `/api/ticker`, installs payload into `window.__LIVE` + monkey-patched `MockData` (`data.js:276-303`), Trade tab renders weekly returns chart, day-of-week chart, expected-move card, suggested strikes, selling-setup panel. *Friction:* the whole payload is one big `/api/ticker` blob; switching tickers re-fetches everything even to glance at price. A `_TICKER_LRU` (8 entries, 90s) softens re-selection.

**Review an options opportunity.** Trade → the suggested call/put (from strike picker) + Weekly Option Selling Setup panel (range location, breach rates, greeks, EM). Drill into Analyze for the chain and PLChart. *Friction:* strike selection lives in the sidebar (global) while the analysis is in the main pane; comparing two expiries means changing the global Expiration select and re-reading.

**Evaluate implied volatility.** IVRankCard / VolSkewCard / ExpectedMoveCard show ATM IV, IV rank (true when ≥20 days of IV history stored, else the HV-rank proxy — §6), HV. *Friction:* "vol rank" surfaces the HV proxy (`ivrank.py`) next to true IV rank without a clear on-screen distinction between the two.

**Review expected move.** ExpectedMoveCard + the chart's EM bands + the selling-setup panel. *Friction:* EM is computed as ATM straddle in some places and IV·√t in others and they fall back to each other (§6 flag #1) — the band's meaning silently shifts.

**Select an expiration and strike.** Sidebar Expiration select (from `window.__LIVE.expirations`) + Strike picker (delta target or buffer%). The suggested strikes flow to the strategy menu and panels. *Friction:* global selection; no side-by-side expiry compare; the `TradeBuilderCard` multi-exp path is a separate Manage-tab flow.

**Compare strategies.** Strategy menu (Trade) ranks 26 strategies (`strategies.jsx`) from the suggested call/put; `StrategyCard` + `PLChart` show payoff/BE/max-loss/R:R. *Friction:* two independent strategy engines exist (JS `strategies.jsx` for the menu, Python `juice.py:build_strategies` for the juice scanner) that don't share formulas.

**Find premium-selling opportunities.** 0DTE Juice tab (`/api/juice`, richness score) and Range-location scan (Scanners, near-lows = sell puts). *Friction:* two different "sell premium here" scanners with different scoring; the range scan is a location measure explicitly *not* a probability.

**Review earnings risk.** Analyze earnings ladder/IV-crush **or** Market Calendar **or** the new Earnings Ops tab. *Friction:* three earnings surfaces; no single source of truth.

**Use scanners.** Each scanner tab triggers a background worker (`/…/scan`) then polls the board. *Friction:* every scanner is a separate manual "Scan" button + poll loop; results aren't cross-linked (a name hot in juice isn't flagged in range scan).

**Create or review a trade.** No order routing. "Trade creation" = journaling a pick (`/api/pick_journal`) or a trade (`/api/trade_journal`); positions are imported/read from Schwab (`/api/broker/positions`, `/owned`). Roll manager + trade builder assist structuring. *Friction:* journaling is manual and disconnected from the analysis that produced the idea.

**Use watchlists.** Watchlist tab board (`/api/watchlist_table`), tag filter, CSV import; tags/sector power the scanners' universe. *Friction:* the board endpoint is polled by ~8 different cards (the four rails, streaks, breadth, range scan, watchlist) — `sharedJson` coalesces the network call but each card keeps its own timer.

**Use the app on mobile.** Compact `.mobile-header`, fixed `.mobile-bottombar` (Tabs / Search / status / Top), a full-screen `.tabsheet` grid for section switching, drawer sidebar, and the `mtable`/`data-label` pattern to stack wide tables into label/value cards. *Friction:* the densest tables (chain, watchlist, treasury overview, earnings ops) are horizontal-scroll on phones; the single giant App re-renders on every state change (mitigated by memoized cards but still heavy on low-end phones).

---

## 4. Technical architecture

**Framework & versions.** No SPA framework, no bundler. **React 18.3.1 + ReactDOM UMD** from unpkg (SRI-pinned, `index.html:29-30`), **lightweight-charts 4.2.3** UMD (`index.html:32`). JSX is Babel-compiled with `@babel/preset-react { runtime: "classic" }` to sibling `.js` files (`build_frontend.js`), each wrapped in an IIFE; cross-file symbols travel through `window` via `Object.assign(window, {...})`. Fonts: Inter / JetBrains Mono / Space Grotesk / Source Serif 4 (Google Fonts). Backend: **CPython, standard library only** for the server; `yfinance`+`lxml` the only declared deps (`requirements.txt`); `pandas`/`numpy`/`requests` arrive transitively via yfinance.

**Frontend structure.**
- `index.html` — loads (in order) `config.js, data.js, recommendation.js, weather.js, journal.js, strategies.js, tweaks-panel.js, tooltips.js, charts.js, app-lib.js, app-cards.js, app.js`, all `?v=3.63`.
- `app.jsx` (7,958 lines) — **one `App()` function, lines 132-7954**, mounted at `ReactDOM.createRoot(...).render(<RootErrorBoundary><App/></RootErrorBoundary>)` (`app.jsx:7956`).
- `app-cards.jsx` (16,045 lines) — ~172 top-level functions, ~55 exported memoized cards + the two big tabs (Treasuries, Earnings Ops).
- `app-lib.jsx` — `TABS`, `CardErrorBoundary`, `RootErrorBoundary`, `sharedJson`, formatters, `skipWhenHidden`, embed constants.
- `charts.jsx`, `strategies.jsx`, `tweaks-panel.jsx`, `tooltips.jsx` — supporting.
- `data.js` (hand-written) — `window.MockData` generator + live bootstrap; `recommendation.js`, `weather.js`, `journal.js`, `config.js` — hand-written non-JSX helpers.

**Backend structure.** `options_dashboard.py` (9,897 lines) = the server + `/api/ticker` payload builder + ~40 inline endpoints; ~20 feature modules imported defensively (`treasury.py`, `earnings_scan.py`, `range_scan.py`, `patterns.py`, `swings.py`, `backtest.py`, `juice.py`, `intraday.py`, `ivrank.py`, `movers.py`, `trend.py`, `analyst_board.py`, `analyst_client.py`, `watchlist_table.py`, `unusual_whales_client.py`, `news.py`, `finviz_news.py`, `metrics.py`, `option_reprice.py`, `storage.py`, `schwab_client.py`, `schwab_auth.py`).

**Routing.** Client: custom tab state (`activeTab`, `TAB_KEY`), no router library; `/next` served by a 2-line additive route. Server: a long linear sequence of `if parsed.path == "...": …; return` blocks in `_dispatch_get` (`options_dashboard.py:6505`) / `do_POST` (`:6050`) / `do_PUT` (`:5978`); unmatched `/api/*` → JSON 404; everything else → static file via `SimpleHTTPRequestHandler`.

**State management.** **All `useState`, one component, no reducer/context** — ~116 top-level `useState` hooks in `App()` (123 total in file), 49 `useEffect`, 26 `useMemo`/`useCallback`. Persistence: localStorage (`STORAGE_KEY`, `TWEAK_KEY`, `TAB_KEY`, per-feature keys) + server `/api/prefs` for tab order & presets. Globals: `window.__LIVE`, `window.MockData`, `window.__JT_EMBED`, `window.__APP_CONFIG`, `window.APP_VERSION`.

**Data fetching & caching (three client layers + many server layers).**
- Client: `apiFetch` (`app.jsx:147`) with `_API_CACHE` (4 s GET text cache + in-flight dedupe); `sharedJson` (`app-lib.jsx:155`, 15 s default TTL + inflight promise map); `_TICKER_LRU` (8 entries, 90 s). Polling is raw `setInterval` + `skipWhenHidden` (no `usePoll` in classic; `/next` has one). Intervals: 12-45 s for live boards, 60 s-6 h for slow data.
- Server: Schwab TTL cache (quote 4 s, chain 30 s, history 600 s, positions 60 s), UW per-endpoint TTL (5-60 s), `treasury._cached` (30 min-12 h), analyst 30 min, news 5 min, finviz-news 60 s, fundamentals 12 h, `_TICKER_LAST_GOOD`/`_EM_LASTGOOD` last-good stale fallbacks, `_JSON_BODY_CACHE` (ETag body cache, 8 entries), plus disk-baked scanner boards under `_STABLE_DIR`.

**Authentication.** Optional shared `X-API-Key` (`_check_api_key`, `:5958`) — **unset = fully open**. Schwab OAuth2 (token in `schwab_token.json`, chmod 0600, auto-refresh). No user accounts/sessions.

**Database.** **None.** All state is atomic-written JSON under `_STABLE_DIR` (`JERRY_DATA_DIR` → `/data/jerry` → `~/.jerry-dashboard`): `watchlist.json`(+.bak+seed), `ui_prefs.json`, `trade_journal.json`, `pick_journal.json`, `fade_stages.json`, `dismissed_alerts.json`, `sent_alerts.json`, `iv_history/{SYM}.json` (252 cap), `server.log`, `schwab_token.json`, `em_history.json`, scanner board caches.

**API integrations.** Schwab (market+broker), Unusual Whales (paid flow), Finnhub (analyst/news), yfinance (delayed), Google News RSS, Finviz (public + Elite export), Treasury.gov, FRED, TreasuryDirect, CFTC, SEC, S&P constituents CSV, ntfy/Pushover (push). Frontend-only: Open-Meteo, logo CDNs.

**Environment variables.** `API_KEY`, `ALLOWED_ORIGIN`, `HOST`/`PORT`, `JERRY_DATA_DIR`, `SCHWAB_APP_KEY`/`SCHWAB_APP_SECRET`/`SCHWAB_REDIRECT_URI`/`SCHWAB_TOKEN_JSON`, `UW_API_KEY`, `FINNHUB_API_KEY`, `FINVIZ_AUTH_TOKEN`/`FINVIZ_NEWS_V`, `WLT_SCAN_CONCURRENCY`/`WLT_FLOW_BUDGET`, `PUSHOVER_APP_TOKEN`/`PUSHOVER_USER_KEY`, `NTFY_TOPIC`/`NTFY_SERVER`. Each secret module also reads `~/.jerry-dashboard/.env`.

**Background jobs / threads.** `ThreadingHTTPServer` (thread per connection, `socket.setdefaulttimeout(15)`); one `HEAVY_SCAN_LOCK = Semaphore(1)` (`analyst_board.py:183`) serializes the heavy universe scans; daemon worker threads per scanner (`_scan_worker`); auto-refresh schedulers (analyst_board morning window; watchlist_table ET slots 9 & 18); the Reversal Radar (`intraday._radar_loop`, 55 s cycle, self-terminates after 300 s idle) and Juice loop (240 s), started lazily on first request.

**WebSocket / streaming.** **None.** All "streaming" (news ticker, market strip, radar/earnings alerts) is `setInterval` polling. No SSE/WebSocket anywhere.

**Charting libraries.** lightweight-charts 4.2.3 (TVPriceChart, IntradayChart) + hand-rolled inline SVG (PriceChart, ReturnsChart, DayBarChart, PLChart, ThetaPanel).

**Styling system.** One 10.7k-line `styles.css`, OKLCH tokens on `:root`, theming via `[data-theme]`/`[data-density]`/`[data-typeface]` attributes (Tweaks panel), ~24 distinct `@media` breakpoints.

**Build & deployment.** `Procfile`: `web: python options_dashboard.py --serve --host 0.0.0.0`. Frontend build: `node build_frontend.js` (compile) + `node verify_frontend.js` (Layer 1 free-variable lint + Layer 2 vm load-harness). Cache-bust: bump `APP_VERSION` in `app.jsx:8` and the 18 `?v=` markers in `index.html`. Compiled `.js` are **committed** alongside `.jsx`. Deployed on Railway from `main`.

**Testing setup.** Python `unittest` suites: `test_earnings_scan.py` (32 tests, pass), `test_strategy_math.py`, `test_reprice.py`, `test_iv_history.py`, `test_v115_storage.py`, `test_v116_push.py`, `test_v117_broker.py`, `test_failure_modes.py`, `test_http_smoke.py`. JS: `test_recommendation.js`, `test_journal.js`, `test_weather.js`. Pandas-dependent suites guard with a "Missing dependency" skip when numpy/pandas absent. No CI config found in-repo; `verify_frontend.js` is the frontend gate but run manually.

**Key file map (most important first):**
- `options_dashboard.py` — server, routing, `/api/ticker` payload, ~40 endpoints, module wiring.
- `app.jsx` — the entire frontend App component + sidebar + tab layout + all top-level state.
- `app-cards.jsx` — every card/panel + the Treasuries & Earnings Ops tabs.
- `metrics.py` — canonical Black-Scholes greeks (imported by the server).
- `data.js` — the MockData↔live-payload seam.
- `styles.css` — the entire design system.
- `storage.py` — all persistence.
- `schwab_client.py` — the primary market/broker data provider.
- `charts.jsx` — all charts.
- `strategies.jsx` — the strategy payoff engine + 26 strategies.

---

## 5. Data sources

Live = real-time when authed; Delayed = ~15 min; Historical = past bars/dates; Calculated = derived; Demo/Hardcoded flagged explicitly.

| Source | Access | Data | Refresh / cache | Live? | Rate-limit / reliability | App parts |
|---|---|---|---|---|---|---|
| **Schwab** (`schwab_client.py`) | OAuth2 REST `api.schwabapi.com` | Quotes (regular+extended), chains **w/ real greeks**, daily & 1-min bars, account positions | quote 4 s, chain 30 s, history 600 s, positions 60 s | **Live** | Token auto-refresh; 401→refresh+retry; falls back to yfinance | Trade/Analyze core, panels, Manage, radar, juice |
| **Unusual Whales** (`unusual_whales_client.py`) | Bearer REST `api.unusualwhales.com` | Flow alerts, chains, GEX, net premium, market tide, sector flow | 5-60 s per endpoint | **Live (paid)** | Adaptive throttle from `x-uw-*` headers; blocks at ≤3 remaining | Flow tab, UW scanners, radar flow bonus |
| **Finnhub** (`analyst_client.py`, `news.py`) | REST `finnhub.io/api/v1?token=` | Price targets, recommendation counts, company news | 30 min | Live-ish | Tolerates 403 (price-target went paid) | Analyst board/cards, news |
| **yfinance / Yahoo** (many modules) | `yf.download/Ticker`, plus `query1.finance.yahoo.com/v8/finance/chart` | Delayed quotes, daily/intraday bars, chains (no greeks), info, upgrades/downgrades, earnings dates, `.news` | via each module's TTL + last-good | **Delayed ~15 min** | **429/IP throttling** — batched, cached, stale fallback; no explicit backoff | Fallback everywhere; movers/trend/ivrank/swings/treasury markets |
| **Google News RSS** (`news.py:188`) | RSS XML | Per-ticker/topic headlines | 5 min | Live | None notable | News |
| **Finviz** (`news.py:260`, `finviz_news.py:37`) | Public quote scrape + Elite export (`auth=TOKEN`) | Fresh press-wire headlines | 60 s | Live | Elite needs `FINVIZ_AUTH_TOKEN` | News |
| **Treasury.gov** (`treasury.py:185`) | XML, 1 call/year | Daily par yield curve 1M-30Y | 30 min curr yr | **EOD (official)** | UA-failover | Treasuries tab |
| **FRED** (`treasury.py:137`) | CSV `fredgraph.csv?id=&cosd=` | Yields, breakevens, TIPS reals, fed funds, CPI+9 subindices | 30 min-12 h | Historical/daily (official) | UA stalls non-curl → curl UA failover | Treasuries tab |
| **TreasuryDirect** (`treasury.py:508/933`) | JSON | Auction results + schedule | 6 h | Live (official) | None notable | Treasuries auctions |
| **CFTC** (`treasury.py:1099/1060`) | Socrata JSON + `FinFutWk.txt` fallback | COT positioning | 12 h | Weekly (official) | **Socrata throttles cloud IPs; no app token configured** → falls back, loses percentiles | Treasuries COT |
| **SEC** (`options_dashboard.py:4165`) | `company_tickers.json` | Symbol↔company↔CIK | daily | Reference | Requires declared UA | Search autocomplete |
| **S&P constituents** (`analyst_board.py:108`) | GitHub CSV | Scan universe | daily | Reference | None notable | Discover scans |
| **ntfy / Pushover** | POST | Push alerts | on-event | n/a | env-gated | Alerts |
| **Open-Meteo, logo CDNs** | frontend fetch | Weather, logos | — | Live | frontend only | Sidebar/logos |

**Mock / placeholder / hardcoded (features that look live but aren't):**
1. **`window.MockData`** (`data.js`, `options_dashboard.py:3743`) — seeded synthetic weekly/daily/chain generator; the live bootstrap monkey-patches it, but any symbol without a live payload renders synthetic data through the same UI.
2. **Earnings Ops demo rows** (`earnings_scan.py:766-841`) — labeled `demo:True` when all providers fail; a client ignoring the flag would show fake setups as real.
3. **Treasury hardcoded schedules** — `CPI_SCHEDULE` (`treasury.py:453`) and `FOMC_2026` (`:462`) are **static literals through 2026**; they present as a live calendar and will silently go stale in 2027+.
4. **Backtest option premiums are fully synthetic** — `iv = realized_vol*1.1` (`backtest.py:742`); patterns' options idea is "MODELED" (`patterns.py:816`). No historical option quotes exist anywhere in the app.
5. **Curated hardcoded lists** — `analyst_board.UNIVERSE` (`:100`) and firm-tier reputation sets (`FIRM_TIER_1/2`, `:166-177`) affect scan scoring.
6. **`recommendation.js`, `strategies.jsx` default IV = 0.3** when a leg's IV is missing (`strategies.jsx:58`) — silent 30% assumption.

---

## 6. Options calculations and trading logic

**⚠ Cross-cutting finding: Black-Scholes is implemented four times with inconsistent risk-free rate and CDF, none using the live curve the app itself fetches.**

| File | Functions | `r` | Normal CDF | `T` |
|---|---|---|---|---|
| `metrics.py:89-161` (**canonical**, re-imported by server `options_dashboard.py:1665-1674`) | `_bs_delta/theta/gamma/vega/price` | **0.045** | Abramowitz-Stegun 7.1.26 | dte/365 |
| `option_reprice.py:13-63` | `bs_price/greeks/implied_vol` | **0.04** | `math.erf` | days/365 |
| `backtest.py:381-402` | `_bs_price/_bs_delta` | **0.04** | `math.erf` | dte/365 |
| `strategies.jsx:24-44` (JS) | `bsPrice/normCdf` | **0.045** | Abramowitz-Stegun | days/365 |

All use `T = calendar_days/365` (calendar, not trading time).

**Expected / implied move** — computed ≥4 ways, used interchangeably:
- ATM straddle mid: `em_d = atm_call_mid + atm_put_mid`; band `spot ± em_d` (`options_dashboard.py:5525-5537`, `3372-3383`; `earnings_scan.py:78-90`). **This is ~1.25σ, not 1σ**, yet labeled "expected move."
- IV·√t fallback: `spot * atm_iv * sqrt(max(dte,0.5)/365)` (`options_dashboard.py:5529-5533`, `3378-3383`, `2873-2875`) — a true 1σ.
- **Flag #1:** the two fall back to each other inside the same function (`5527` vs `5532`), so band width silently changes meaning depending on whether quotes are present.
- Prob-cone (only true log-normal): `charts.jsx:252-255` — `dailySigma = ivAnnualized/√252`, ±2σ = `price*exp(±2·σ·√dte)`.

**Implied volatility.** Source Schwab/yfinance; ATM IV = mean of ATM call+put IV (`:5523`, `3362`, `juice.py:319`). Stored as annualized decimal. **Inconsistent sanity bounds**: keep `0<iv<5` (`app-cards.jsx:551`), `iv*100 if iv<5 else iv` (`earnings_scan.py:609`), reject `iv>10` (`storage.py:141`), IV search `[1e-4,5]` (`option_reprice.py:38`). No universal normalizer.

**IV rank / percentile / HV rank — three implementations:**
- **True IV rank** (`storage.py:181-203`): needs ≥20 stored daily IVs; `rank = (iv−min)/(max−min)·100`, `pctile = count(v<iv)/n·100`. This is a real rolling IV history (like the earnings-EM store), populated over time via `_iv_history_append`.
- **HV-rank proxy** (`ivrank.py:53-82`): `HV20 = std(logret,20)·√252·100`; rank over 252-day window; the module's "score" **is** the rank (`:118`). Candidly documented as a proxy but surfaced in the UI as "vol rank."
- **Inline HV pctile** (`options_dashboard.py:5560-5570`) duplicates the ivrank logic in the EM panel.

**Probability (all approximations, never a real integral):**
- Delta-as-P(ITM): `pop = (1−|Δ|)·100` pervasively (`juice.py:152/190/215/250/259`).
- EM-based normal CDF iron fly: `pop = (2·Φ(credit/em)−1)·100` (`juice.py:229`) — **but `em` is the straddle (~1.25σ) while the comment asserts "EM as 1σ," so it understates POP (flag #2)**.
- Prob cone (`charts.jsx:1298-1308`): symmetric Gaussian bell, `sigma = max(EM, spot·0.06)` — visual only, not log-normal.
- Range-scan "proximity" is **explicitly not probability** (`range_scan.py:16-18`, disclaimer `app-cards.jsx:3432`).

**Greeks** (`metrics.py:89-140`, r=0.045): standard `d1=(ln(S/K)+(r+σ²/2)T)/(σ√T)`; **theta ÷365** → per-calendar-day dollars (`:99-116`, understates per-active-day decay vs ÷252); **vega ÷100** per vol point; delta fallback ±0.5. Greeks computed **only as a fallback** when the chain lacks them (`options_dashboard.py:2136-2143`, flagged `delta_est=True`).

**DTE / theta decay.** DTE = calendar days; only `juice.py:65-75` decays intraday (0-DTE `frac = max(hours_to_close/6.5, 0.05)`).

**Premium / spread / liquidity.** Mid = `(bid+ask)/2` else last (multiple copies). Spread% = `(ask−bid)/mid·100`; labels good ≤5 / ok ≤12 / poor (`earnings_scan.py:168-174`). Liquidity score `juice.py:376-380`: OI·10 + vol·7 + spread·8 caps. Estimated spread by price tier (`patterns.py:161-170`).

**Strategy payoff / P&L / BE / R:R** (`strategies.jsx`): leg valuation via `bsPrice` (default IV 0.3 if missing, `:58`); P/L = `Σ qty·(legValue − premium)` at nearest-DTE expiry; breakevens by linear interpolation of a 240-pt curve; **max profit/loss = min/max of the sampled window only** — undefined-risk legs report a finite off-screen bound (flag). 26 hard-coded strategies with per-structure credit/max-loss formulas; wings at fixed % offsets (condor 5%, fly 2.5%, iron fly 4%). **Parallel Python engine** in `juice.py:130-279` with its own formulas (BP approximation `max(0.20·spot−OTM, 0.10·strike)+credit`).

**Support/resistance.** Zig-zag swing pivots (`swings.py:34-58`, 12% reversal); level clustering within 1.2% (`:497-524`); hold/break stats band ±1.5% (`:859-912`). No classic floor pivots.

**Historical moves.** Weekly returns `(week_hi/lo ÷ baseline − 1)·100` (`range_scan.py:121`, `load_weekly_data`); earnings reactions `(close_after/close_before−1)·100`, BMO/AMC-aware (`earnings_scan.py:532-572`); IV-crush ladder synthetic straddle vs realized (`options_dashboard.py:2420-2460`).

**Proprietary scores (exact weights, file:line):**
- **Juice Score** (`juice.py:366-403`, 0-100): richness `min(prem_per_day/2.5,1)·25 + max(0,min((iv_vs_hv−0.9)/0.5,1))·15`; liquidity 25 (OI 10 + vol 7 + spread 8); activity 10; structure 15 (support>put-BE +7, resistance<call-BE +8); context ~13 (DTE + no-earnings + iv_rank≥60). Filters price≥$20, cap≥$5B, DTE≤3.
- **Reversal Radar** (`intraday.py:524-634`, 0-100): stretch 25 + exhaustion 25 + location 20 + confirmation 20 + context (−5..+10); penalties −15 repricing, counter-trend cap 60; +5 flow bonus. Log≥70, push≥85.
- **Pattern confidence** (`patterns.py:641-645`) & **actionability** (`:734-748`): sample/consistency/significance/effect and EV/OOS/stability/RR/speed/liquidity/recency, each ×25 or weighted; BH-FDR q>0.10 caps at 40.
- **Range-scan edge** (`range_scan.py:146-151`): `edge = max(bottom_prox, top_prox)`; location, not probability.
- **Earnings opportunity score** (`earnings_scan.py:337-415`, weights sum 100): liquidity 10, rel_vol 10, options_liq 15, weekly 5, iv_edge 15, move_vs_expected 10, confirmation 15, spread 5, market_align 5, R:R 10; ×0.6 if extended; capped 25 if no-trade.
- **Rate sensitivity "beta"** (`treasury.py:877-923`): **actually Pearson correlation**, not a regression slope (flag #5).
- **Auction strength** (`treasury.py:958-978`): bid-to-cover & indirect vs prior-10 average.
- **Curve regime** (`treasury.py:314-323`): bull/bear × steepener/flattener from 5-day 2y/10y bp.

**Inaccurate / oversimplified / misleading (ranked):** (1) EM semantic inconsistency; (2) iron-fly POP understated; (3) 4 un-reconciled BS copies, r 0.04 vs 0.045, none live; (4) HV proxy surfaced as "vol rank" next to true IV rank; (5) "beta" is correlation; (6) delta-as-POP throughout; (7) backtest premiums fully synthetic; (8) theta ÷365 (calendar); (9) undefined-risk max P/L capped to sample window; (10) default 30% IV when a leg's IV missing.

---

## 7. UI and design system

**Layout.** Sidebar (`--sidebar-w:304px`) + main content grid of cards; tab bar spans full width; card grid uses `--gap-grid:16px`/`--row-gap:16px`. Content for several tabs (Trade, Analyze, Flow, Manage) is split across multiple `<TabPanel>` blocks in the App render.

**Navigation.** Desktop: two-row tab bar (app sections + a "Sites -" row for FV/TV/UW) with drag-reorder (saved to `/api/prefs`) and an earnings chip. Mobile: `.mobile-bottombar` (Tabs/Search/status/Top) + full-screen `.tabsheet` grid + drawer sidebar.

**Typography.** Inter (display/body), JetBrains Mono (numerics), swappable to Space Grotesk / Source Serif 4 / mono via `[data-typeface]`.

**Color.** OKLCH tokens (`styles.css:2-42`): accent from HCL parts (`--accent-h:152`), semantic `--up`(green)/`--down`(red)/`--warn`(amber), four text tiers `--fg…--fg-4`, surfaces `--bg/-2/-3`, `--line/-2`. Light default (`--bg:#fafaf7`) + `[data-theme="dark"]` (`--bg:#0e1014`).

**Spacing/density.** `[data-density="compact|full"]` rescales padding/gaps; radii `--radius-sm/--radius/--radius-lg`; shadows `--shadow-card/-hover`.

**Components.** ~55 memoized cards; consistent `.card`/`.card-head`/`.kicker`/`.card-title` shell; pills, chips, segmented toggles, sliders, sticky-header sortable tables.

**Charts.** Mixed SVG + lightweight-charts (§4) — a real inconsistency (two rendering paradigms).

**Tooltips.** Native `title=` attributes pervasively + a `Term`/tooltips.jsx glossary system for defined terms.

**Forms/filters.** Native `<select>`/`<input type=range>`/chip toggles; filter chips reused across scanners (`tsy-serbtn`, `eop-sec`).

**Loading/empty/error.** `.skel` shimmer skeletons (reduced-motion aware); "Data unavailable" text (27× in app-cards); `CardErrorBoundary` wrapped **93×** in App + a full-page `RootErrorBoundary`.

**Responsive.** ~24 distinct `@media` widths (900 heaviest at 30 rules; then 720/760/1100/700/620/…); `mtable`/`data-label` table-to-card collapse; `hover:none` + 10 `prefers-reduced-motion` blocks.

**Accessibility.** `role="tab"/"tablist"/"dialog"`, `aria-selected`, `aria-live` on toasts, reduced-motion respected; but heavy reliance on `title=` (not keyboard/screen-reader-friendly for tooltips), color-only up/down encoding in places, and tiny mono numerics.

**Dark mode.** Attribute-driven full token override; both themes maintained.

**Inconsistent / dated / crowded areas:** two chart paradigms; ~24 ad-hoc breakpoints (no tier tokens); dense mono tables can feel crowded on desktop and require horizontal scroll on mobile; the classic vs `/next` look diverge; some older cards (rails, strategy reference) predate the current token discipline.

---

## 8. Performance audit

**Bundle size (largest problem).** `app-cards.js` **1.0 MB**, `app.js` **423 KB**, `styles.css` **421 KB**, `charts.js` 101 KB, `strategies.js` 70 KB — all **unminified, committed, and loaded synchronously** as classic scripts with `?v=` cache-busting. No code-splitting, no tree-shaking, no minification. Every tab's code ships on first paint even though most tabs are never opened in a session. React/lightweight-charts add ~180 KB from unpkg (cached cross-site, SRI-pinned).

**Initial load.** 12 synchronous `<script>` tags + Google Fonts + React/LC from unpkg, then one large `/api/ticker` fetch. Static assets are served `Cache-Control: no-store` (`options_dashboard.py:9728`) — **defeats browser caching of the 1.5 MB JS/CSS on every visit** (the `?v=` scheme would allow long-cache immutable instead).

**Re-renders (structural).** The entire UI is one `App()` with ~116 `useState`; any state change re-renders App. Mitigated only by ~55 `_memo` child cards + `useCallback`-stabilized `apiFetch`/`switchTicker`. Sidebar controls (weeks slider, strike sliders) live in App, so dragging a slider re-runs App's whole render + its 26 `useMemo`s.

**Expensive calculations client-side.** Strategy payoff sampling (240 pts × 26 strategies), prob-cone, SVG chart path building — all in render/useMemo. `strategies.jsx` recomputes on every strike/expiry change.

**Repeated API requests.** `/api/watchlist_table` is consumed by ~8 cards (4 rails + streaks + breadth + range scan + watchlist); `sharedJson` coalesces the network call but each card keeps its own `setInterval`, so timers proliferate. `/api/ticker` re-fetches the full payload on every symbol switch (LRU softens re-selection only).

**Missing caching.** Static-asset `no-store` (above). Client `_API_CACHE` is only 4 s. No service worker.

**Oversized responses.** `/api/ticker` returns rows + daily + full chain + current + earnings history in one blob; scanner boards can be large (watchlist board, treasury overview). ETag + gzip mitigate re-serialization but not first payload.

**Large tables.** Watchlist board, option chain, treasury overview, earnings ops — all render every row (earnings ops caps at 150 with "show all"; range scan caps at 150; others don't). No virtualization anywhere.

**Chart rendering.** SVG charts rebuild full path strings each render; lightweight-charts instances created/destroyed on mount. Two paradigms = two perf profiles.

**Mobile.** The single-App re-render + 1.5 MB bundle + dense tables are the mobile bottleneck; horizontal-scroll tables and the tabsheet mitigate layout but not JS cost.

**Network / console.** yfinance 429s under load (cached/stale-guarded, no backoff); CFTC Socrata throttling on cloud IPs → fallback; no other systematic console errors observed. `verify_frontend.js` Layer 2 asserts a single mount.

**Layout shifts.** Skeletons reduce CLS on cards; the market strip and rails can shift as async data lands.

**Likely-highest-impact perf items:** (1) minify + long-cache the committed JS/CSS and flip static `no-store`→immutable; (2) lazy-load per-tab code (esp. Treasuries/Earnings Ops/Flow); (3) lift sidebar slider state so App doesn't re-render on drag; (4) virtualize the big tables; (5) unify on one chart library.

---

## 9. Code quality and complexity

**Monolithic App (top risk).** `App()` = ~7,800 lines, ~116 `useState`, 49 `useEffect`, no reducer/context. Scan features each carry a 5-6-field state quintet (rows/running/error/progress/at/sort). Any refactor is high-friction; any state change re-renders everything.

**Duplicate components.** Four near-identical rail cards (`app-cards.jsx:10456/10618/10735/10876`) → one parameterized component. Three earnings surfaces. Two strategy-payoff engines (`strategies.jsx` JS + `juice.py` Python). Two full frontends (classic + `/next`) with duplicated TABS, apiFetch, X-API-Key, usePoll, version.

**Duplicate logic.** Four Black-Scholes copies (§6). Three IV/HV-rank implementations. `X-API-Key` header block written twice (`app.jsx:152`, `1045`). `setLiveQuotes` merge repeated (1234/1276/1694). Three client cache layers (`_API_CACHE`, `sharedJson`, `_TICKER_LRU`).

**Repeated styling.** ~24 ad-hoc breakpoints; per-feature CSS blocks (`tsy-*`, `eop-*`, `wos-*`, `rgs-*`) with overlapping patterns.

**Unused / vestigial.** `window.MockData` demo paths remain wired as fallback throughout App though the app is live-driven. `tweaks-panel.jsx` still ships its generic "prototype host protocol" (`__activate_edit_mode` postMessage) from its origin as a reusable editor shell.

**Overly large files.** `options_dashboard.py` (9.9k), `app-cards.jsx` (16k), `app.jsx` (8k), `styles.css` (10.7k) — all beyond comfortable review size.

**Fragile logic.** Monkey-patching `MockData.buildWeekly/...` at runtime (`data.js:276-303`) is clever but non-obvious coupling; the payload shape is an implicit contract between `build_payload` and every consumer. Hardcoded 2026 macro schedules go stale.

**Inconsistent naming / data models.** Bar field names differ (`h/l/c` vs `high/low/close` — a real bug fixed in earnings_scan this session); IV bounds differ per module; "beta" means correlation; "expected move" means two things.

**Error handling.** Backend is strong (every endpoint → JSON 500, defensive module imports, last-good fallbacks, NaN-safe JSON). Frontend is strong on boundaries (93 `CardErrorBoundary`). Weak spots: silent `except Exception: pass` in several fetchers hides root causes.

**Technical debt.** Committed compiled artifacts (easy to desync; the only guard is manually running verify). No CI. Compiled `.js` ~1.5 MB in git.

**Security (from backend audit).** No committed secrets; `.gitignore` correct; tokens chmod 0600. **But:** `API_KEY` unset = fully open API incl. `/api/broker/*` position data; `ALLOWED_ORIGIN` defaults `"*"`; whole working directory served statically with no auth (all `.py` readable — keep secrets out of CWD); no Socrata app token; single shared static key, no rate-limiting of failed attempts.

**Where less code = same/better result:** collapse the four rails; unify the two strategy engines and the four BS copies; one chart library; one client cache; delete `/next` or promote it (not both); tokenize breakpoints.

**Architecture verdict:** the monolith is painful but **incrementally improvable** — a full rewrite is not warranted. The backend module boundaries are actually clean; the frontend needs decomposition, not replacement.

---

## 10. Current strengths (preserve these)

- **Depth of genuinely useful, trader-specific analytics** — the Weekly Option Selling Setup panel, expected-move-vs-historical, IV-crush ladder, reversal radar, range-location scan, 0DTE juice, the rates terminal, and now earnings ops are real, differentiated tools, not generic widgets.
- **Intellectual honesty in the data layer** — pervasive "Data unavailable" over fabrication, `delta_est`/`demo` flags, last-good stale serving, explicit "location not probability" and "correlation not causation" disclaimers. This is rare and valuable.
- **Statistical rigor in patterns** — walk-forward, bootstrap CIs, BH-FDR multiple-testing correction (`patterns.py`) is more disciplined than most retail tools.
- **Resilient backend** — defensive module imports (one broken module never blocks startup), every endpoint degrades to JSON 500, `HEAVY_SCAN_LOCK` prevents OOM, NaN-safe JSON, ETag+gzip body cache, last-good/stale caches, 15 s socket timeout.
- **Real greeks when available** — Schwab greeks preferred, BS only as a labeled fallback; a rolling IV-history store that yields *true* IV rank over time.
- **Cost-conscious free-data architecture** — Treasury/FRED/TreasuryDirect/CFTC/SEC/Google-News/Finviz with UA-failover and throttle fallbacks; documented in `FREE_DATA_SOURCES.md`.
- **Verify harness** — `verify_frontend.js` Layer 1 (free-variable lint proving cross-file globals resolve in load order) + Layer 2 (vm load harness asserting exports + single mount) is a clever safety net for a bundler-less app.
- **Dense, coherent visual language** — OKLCH tokens, attribute-driven theming/density/typeface, JetBrains Mono numerics; the recent tabs (Treasuries, Earnings Ops) hit real terminal density.
- **Mobile navigation** — the tabsheet + bottombar + mtable collapse make a genuinely dense app usable on a phone.

---

## 11. Highest-impact opportunities (ranked)

For each: problem · why it matters · user impact · perf impact · complexity · risk · files · simplifies-or-expands · approach.

1. **Minify + long-cache the committed JS/CSS; flip static `no-store`→immutable.** Problem: 1.5 MB unminified assets re-downloaded every visit (`options_dashboard.py:9728`). Matters: first-paint on every load. User: much faster load, esp. mobile. Perf: large. Complexity: low. Risk: low. Files: `build_frontend.js`, `options_dashboard.py` static headers, `index.html`. Simplifies. Approach: add a terser pass in build; serve `/assets/*.js?v=` with `Cache-Control: immutable, max-age=1y`; keep HTML `no-store`.
2. **Lazy-load per-tab code.** Problem: every tab's JS ships on first paint. Matters: bundle + parse cost. User: faster start. Perf: large. Complexity: medium (no bundler). Risk: medium. Files: `index.html`, `build_frontend.js`, `app.jsx` TabPanels. Simplifies net. Approach: split `app-cards.js` into per-tab chunks loaded on first tab activation (dynamic `<script>` inject keyed off `changeTab`).
3. **Unify the four Black-Scholes copies and the "expected move" definition.** Problem: r 0.04 vs 0.045, straddle-vs-IV·√t confusion (§6). Matters: correctness of every greek/POP/EM. User: trustworthy numbers. Perf: none. Complexity: medium. Risk: medium (numbers shift). Files: `metrics.py`, `option_reprice.py`, `backtest.py`, `strategies.jsx`. Simplifies. Approach: make `metrics.py` the single source (optionally feed the live 3m yield from `treasury.py`), delete the copies, standardize "EM" = 1σ IV·√t with the straddle labeled separately.
4. **Lift sidebar slider/selection state out of App** (or memo-isolate). Problem: dragging weeks/delta re-renders the 7,800-line App. Matters: input lag. User: smooth controls. Perf: medium. Complexity: medium. Risk: low. Files: `app.jsx`. Simplifies. Approach: move the sidebar into a memoized subtree with local state, publish committed values via callback.
5. **Collapse the four rail cards into one parameterized component.** Problem: 4× duplicate (`app-cards.jsx:10456+`). Matters: maintenance. User: none. Perf: minor. Complexity: low. Risk: low. Files: `app-cards.jsx`. Simplifies (−~600 lines). Approach: `<ExtremesRail side= metric= />`.
6. **Fix the iron-fly POP and label delta-as-POP as an estimate.** Problem: `juice.py:229` understates POP; delta≠P(ITM). Matters: premium-selling decisions. User: honest odds. Perf: none. Complexity: low. Risk: low. Files: `juice.py`. Simplifies. Approach: pass a true-1σ EM to `Φ(credit/em)`; annotate delta-POP.
7. **Enforce `API_KEY` + tighten CORS in deploy; never serve secrets from CWD.** Problem: unset key = open broker API; `ALLOWED_ORIGIN="*"`. Matters: security of position data. User: safety. Perf: none. Complexity: low. Risk: low. Files: `options_dashboard.py`, deploy env. Simplifies. Approach: refuse to bind non-loopback without `API_KEY`; default-deny CORS; assert no `.env` in served dir at startup.
8. **Decide the classic-vs-`/next` fork.** Problem: two full frontends, doubled TABS/apiFetch/version. Matters: every change done twice. User: consistency. Perf: medium. Complexity: high. Risk: high. Files: `next-app.jsx`+`next.*`, classic. Simplifies (delete one). Approach: pick the survivor; migrate the winning ideas; delete the other.
9. **Virtualize the big tables** (watchlist, chain, treasury overview). Problem: full-row render. Matters: large-list scroll. User: smoother. Perf: medium. Complexity: medium. Risk: low. Files: `app-cards.jsx`. Expands slightly. Approach: windowed rendering (visible-rows only).
10. **Consolidate IV/HV-rank into one implementation and label proxy clearly.** Problem: 3 implementations; HV proxy shown as "vol rank." Matters: vol reads. User: clarity. Perf: none. Complexity: low. Risk: low. Files: `storage.py`, `ivrank.py`, `options_dashboard.py`. Simplifies. Approach: one rank fn; UI badges "HV proxy" vs "IV rank (n days)."
11. **Split `app.jsx` App into a shell + per-tab components.** Problem: 7,800-line function. Matters: maintainability, review, re-render scope. User: indirectly (fewer bugs). Perf: medium. Complexity: high. Risk: medium. Files: `app.jsx`. Simplifies long-term. Approach: extract each TabPanel's body into its own memoized component receiving explicit props; keep the monkey-patch seam intact.
12. **Add a lightweight CI** (run `verify_frontend.js` + the python unittests + a "compiled `.js` matches `.jsx`" check). Problem: no CI; committed artifacts can desync. Matters: prevents broken deploys. User: reliability. Perf: none. Complexity: low. Risk: low. Files: `.github/workflows/*`. Expands (small). Approach: GitHub Action on PR.
13. **Consolidate the two strategy-payoff engines.** Problem: JS `strategies.jsx` + Python `juice.py` diverge. Matters: consistent P/L/BE/POP everywhere. User: trust. Perf: none. Complexity: medium. Risk: medium. Files: both. Simplifies. Approach: pick one authority (server), have the client render its output.
14. **Cross-link scanner results.** Problem: juice/range/radar/earnings are siloed. Matters: the core value is "which names matter now." User: high (fewer tabs to check). Perf: none. Complexity: medium. Risk: low. Files: scanner cards, a small aggregation endpoint. Expands modestly. Approach: a unified "opportunities" ribbon that surfaces a name appearing in ≥2 scanners.
15. **Refresh the hardcoded 2026 macro schedules to a data source or a maintained file with an explicit expiry warning.** Problem: `CPI_SCHEDULE`/`FOMC_2026` go stale in 2027. Matters: the calendar silently lies. User: correctness. Perf: none. Complexity: low. Risk: low. Files: `treasury.py`. Simplifies. Approach: load from a small dated JSON; if past the last entry, render "schedule needs update" instead of wrong dates.

---

## 12. Questions and unknowns (not determinable from the repo)

- **Live production behavior of paid sources** — Schwab greeks/quotes and UW flow can't be exercised here (no keys, sandboxed network). Whether real-time greeks fully populate every panel in prod is unverified.
- **Actual bundle/parse timing on the user's devices** — no Lighthouse/RUM data in-repo; §8 figures are byte sizes and structural inference, not measured load times.
- **Whether the `/next` app is actively used** or abandoned — both exist; usage isn't in the repo.
- **Railway config specifics** — volume mount path (`/data`), env values, scaling, and whether `API_KEY`/`ALLOWED_ORIGIN` are actually set in prod are outside the repo.
- **IV-history maturity** — true IV rank needs ≥20 stored days per symbol; how many symbols have accumulated enough isn't visible (state lives in `~/.jerry-dashboard`, not the repo).
- **Real accuracy of the scores vs realized outcomes** — no backtest of the proprietary scores (juice/radar/earnings) against forward returns exists; their predictive value is unmeasured.
- **Browser/device support matrix** — OKLCH + color-mix + `100dvh` assume modern browsers; the actual support target is unstated.
- **Whether the committed `.js` always matches `.jsx`** at any given commit — currently in sync, but no automated guarantee.

---

## 13. Final handoff package

**Architecture summary.** A single-process Python stdlib `ThreadingHTTPServer` (`options_dashboard.py`) serves a bundler-less, precompiled classic-React frontend and a 90+-route private JSON API. ~20 clean backend feature modules (each defensively imported, TTL-cached, with last-good fallbacks) wrap Schwab (live, paid-by-account), Unusual Whales (paid), Finnhub (free), yfinance (delayed), and official free rates/macro sources. State is flat JSON files under `~/.jerry-dashboard` (no database). The frontend is one ~7,800-line `App()` with ~116 `useState` and ~55 memoized cards, data flowing through a runtime-monkey-patched `MockData`↔live-payload seam. Auth is an optional shared `X-API-Key`.

**Product summary.** A dense, honest, single-user options-trading terminal for a premium-selling/swing trader: pick a ticker, read expected-move-vs-historical + IV context, choose a strike/expiry, compare 26 strategies, and hunt opportunities across a suite of proprietary scanners (juice, radar, range-location, patterns, earnings) plus a full rates/macro terminal — answering "is this a good sale here, and which watchlist names are set up right now."

**Five strongest parts.** (1) Trader-specific analytical depth (selling-setup panel, EM-vs-historical, IV-crush, radar, rates terminal, earnings ops). (2) Data-honesty discipline (Data-unavailable > fabrication, est/demo flags, disclaimers). (3) Statistical rigor in `patterns.py` (walk-forward/bootstrap/BH-FDR). (4) Resilient, defensively-structured backend. (5) The verify harness + coherent OKLCH design system.

**Five weakest parts.** (1) 7,800-line monolithic `App()`. (2) Four un-reconciled Black-Scholes copies + inconsistent "expected move." (3) 1.5 MB unminified, `no-store`-served, non-lazy bundle. (4) Duplicate everything (rails, strategy engines, IV-rank, classic-vs-`/next`). (5) Security defaults (open API when `API_KEY` unset, `ALLOWED_ORIGIN="*"`, whole-dir static serving).

**Five biggest performance bottlenecks.** (1) Unminified 1.5 MB assets with `no-store`. (2) No per-tab lazy loading. (3) Single-App re-render on every state change (incl. slider drag). (4) Non-virtualized large tables. (5) Full `/api/ticker` blob per symbol switch.

**Five biggest UX problems.** (1) Global sidebar strike/expiry state makes side-by-side compare awkward. (2) Three earnings surfaces + siloed scanners → "which name matters now" takes many tabs. (3) HV proxy shown as "vol rank" next to true IV rank. (4) "Expected move" band silently changes meaning. (5) Dense mono tables require horizontal scroll on mobile.

**Five biggest performance/UX-adjacent correctness risks.** (1) Iron-fly POP understated. (2) Delta-as-POP unlabeled. (3) Backtest premiums fully synthetic. (4) "Beta" is correlation. (5) Hardcoded 2026 macro schedules going stale.

**Five highest-value opportunities.** (1) Minify + long-cache + lazy-load the frontend. (2) Unify BS/EM math (feed the live curve). (3) Lift sidebar state / begin decomposing App. (4) Enforce `API_KEY`/CORS. (5) Cross-link scanners into one "opportunities" view.

**Five files to inspect first.** (1) `options_dashboard.py` (server, routing, `/api/ticker` payload, module wiring). (2) `app.jsx` (the entire frontend + state + tab layout). (3) `metrics.py` (canonical greeks — the math to unify around). (4) `data.js` (the MockData↔live seam every consumer implicitly depends on). (5) `app-cards.jsx` (all cards + the two newest tabs; where duplication lives).

**Recommended order of operations.**
1. **Safety & speed first (low risk, high return):** enforce `API_KEY`/CORS in deploy; minify + long-cache + flip static `no-store`; add CI (verify + unittests + artifact-sync check).
2. **Correctness:** unify the four BS copies and the EM definition in `metrics.py` (optionally feed the live 3m yield); fix iron-fly POP; label delta-POP and the HV proxy.
3. **Perf structure:** lazy-load per-tab code; lift sidebar state; virtualize the big tables.
4. **De-duplication:** collapse the rails; consolidate the two strategy engines and the three IV-rank implementations; decide classic-vs-`/next`.
5. **Decomposition:** extract each TabPanel body from `App()` into memoized per-tab components (keep the MockData seam).
6. **Product cohesion:** cross-link scanners into a unified opportunities ribbon; consolidate the three earnings surfaces; refresh the hardcoded macro schedules.

*All references are to the working tree at `main`/v3.63. No files were modified in producing this audit.*
