# SCREEN_AND_COMPONENT_MAP

Single-page application: one persistent **sidebar** (left) + one **two-row tab bar**
(top) + one **main column** whose content is the active tab. Tabs stay mounted after
first visit (instant switching; embedded sites never reload). File paths are given
only as reference pointers.

```
┌───────────────────────────────────────────────────────────────────────┐
│ TAB ROW 1: Trade Discover Analyze Patterns News Flow Scanners         │
│            0DTE-Juice Backtest Breadth Journal Watchlist Streaks      │
│            Market-Calendar Manage            [TICKER EARNINGS chip]   │
│ TAB ROW 2: Sites -  Finviz  TradingView  Unusual Whales               │
├──────────────┬────────────────────────────────────────────────────────┤
│ SIDEBAR      │ MAIN COLUMN (active tab's cards, vertically stacked)   │
│ (persistent) │  … optional left/right mini-rails on wide screens …    │
└──────────────┴────────────────────────────────────────────────────────┘
```

## Sidebar (`app.jsx` ~line 2772+)

Top → bottom:
1. Version pill (v3.45, top-left) · Weather pill (top-right corner, 13px, tap = location toggle)
2. Brand block: Jerry logo, live status line ("Live. date | time"), SCHWAB LIVE badge, UW rate badge
3. Icon row: theme/tweaks/reference toggles (opens **Tweaks panel** — theme dark/light, accent presets emerald/indigo/amber/rose/teal, behavior tweaks; `tweaks-panel.jsx`)
4. **TICKER** section: symbol input (compact), company logo (60px, capped width), name, div yield, price + day change + stale tag, P/E · Fwd P/E
5. **WATCHLIST** section: ☆ star button · Manage (n) · FV · TV · UW buttons (brand-colored); starred-symbol chip row (active = filled)
6. **PRESETS** section: pinned symbol chips + Edit
7. **WEEKS OF HISTORY** slider (daily chart range)
8. **EXPIRATION** selector · **STRIKE PICKER** · **RETURN BASELINE** (options context controls)

## Overlays / global

- **Command palette** (keyboard): fuzzy tab/action jumps (`app.jsx`)
- **Radar toasts** (`RadarAlerts`, bottom overlay): score ≥ 80 signals
- **Watchlist Manager** modal (opened from sidebar Manage or Manage tab): add/remove/tag/star; the only removal surface
- **Root error screen** and per-card error cards with Retry

## Tab: Trade (`app.jsx` TabPanels ~3391, 3401, 4370, 6562)

- Chart header: ticker, price, Daily ↔ **1-Min** toggle
- **PriceChart** (daily, EM band overlay, weeks slider) / **IntradayChart** (1-min bars, VWAP, day levels, OI walls) — `charts.jsx`
- **ExpectedMoveCard** (`app-cards.jsx`) with expiry switcher and comparison strip
- **RecommendationPair** (CSP + CC) → **StrategyCard** → **TradeBuilderCard**
- **ThetaPanel**, **VolSkewCard**, **LevelRepriceCard**, **RollManagerCard**
- **WatchlistAlertsCard**, **NewsTicker**, P/L chart (`PLChart`)

## Tab: Discover (~3190)

- **ScreenersHub** with sub-tab bar: **Analyst calls / Movers / Trend / Vol Rank**
  (each a sortable board; rows jump the global ticker) — `app-cards.jsx:3309`

## Tab: Analyze (~4207, 4360, 4406)

- **AnalystCard** (targets/ratings/catalysts) · company profile kicker
- **ValuationCard** · **BasingCard** · **PullbackProfileCard** ("Open behavior · pullback profile")
- **ReturnsChart**, **DayBarChart**, macro kicker

## Tab: Patterns (~3198)

- **PatternDiscoveryCard**: header (ticker + range + ↺ analyze) → **Current Setup**
  panel (accent-bordered; top-3 ranked with score/similarity/expected/target/stop/
  invalidation/typical-days) → **Ask box** (input + Ask →) → filter chips + "N
  candidates searched" → **pattern rows** (actionability badge, reliability label,
  kind chips, sentence; expand = stats grid ~15 tiles, first-touch line, context
  note, context buckets, occurrence chart with bands, occurrence dates, options
  idea, action buttons incl. inline **⌕ scan** results) → **Intraday sequences**
  section (mine button, progress bar, sequence sentences with labels) → methodology
  notes → **Watched patterns** list (TRIGGERED badges, ✕ remove)
- **SwingPatternCard** below (legacy preset patterns)

## Tab: News (~3207)
- **NewsHub**: ticker + market news lists, filter controls, headline links

## Tab: Flow (~4199, 4354, 4732)
- **FlowScoreCard** ("Unusual Whales · real-time options flow")
- **EarningsCrushCard**; market-wide UW readouts (tide, sector flow, net premium)

## Tab: Scanners (~4903)
- **ReversalRadarCard**: Long list + Short list; rows expandable (RRRow) with
  score chips, ticket, Trade tab → / 1-Min → / Finviz → buttons
- **RadarReportCard**: resolved-signal hit rates, time-of-day tuning state
- **OpenReversalCard**: open-reclaim scanner ("CRDO pattern")

## Tab: 0DTE Juice (~4891)
- **PremiumJuiceCard**: scan status line (age, stale note), sortable board,
  expandable **PJStrategy** suggestion blocks (DEFINED/UNDEFINED labels)

## Tab: Backtest (~4897)
- **BacktestCard**: strategy textarea + example chips + Interpret → · rules editor
  (Setup / Universe / Entry list / Exits list / Sizing & costs rows; add-condition
  dropdowns; "edit as JSON") · Run ▶ + progress bar · results (9 metric tiles,
  equity-curve SVG, best/worst/skips line, regime table, trades table toggle)

## Tab: Breadth (~3230)
- **MarketBreadthCard**, **MarketOverview**, **MarketPosture**

## Tab: Journal (~3235)
- **PicksJournalCard** (+ trade journal entries; win-rate feeds Manage)

## Tab: Watchlist (~3212)
- **WatchlistTableCard**: status/scan line, tag/sector/industry filters, sortable
  1,276-row table (price, change, tag, sector, industry, weekly, alerts), row →
  global ticker; rescan link

## Tab: Streaks (~3220)
- **WatchlistStreaksCard**: consecutive up/down-day streak board

## Tab: Market Calendar (~3225)
- **MarketCalendarCard**: dated macro/market events ("Pre-market game plan" kicker)

## Tab: Manage (~4151)
- **WatchlistManager** (add/remove/tag; removal warning) · **BrokerImportCard** ·
  **PositionsCard** ("My positions") · **WinRateCard** · **PushSettingsCard** ·
  **SchwabReconnect** · **StrategyReferenceCard**

## Row-2 tabs: embedded sites (~4852, 4867, 4879)

Shared anatomy (`FinvizPanel` / `TVPanel` / `UWPanel`, `app-cards.jsx`):
- Toolbar row 1: current-ticker chip · add-only watchlist star / "★ on watchlist"
  badge · radar/juice presence badges · **Trade →** · **1-Min →** · status chips
  (helper update needed / cookies compat mode)
- Toolbar row 2: **Follow ON/OFF** · site-specific controls (Finviz: Elite/Free
  segment; TV: Sign in ↗, Repair session) · ↺ticker · Reload · ⧉ open-in-tab ·
  site nav chips (Finviz: Screener/Portfolio/Map/Earnings/News/My watchlist; TV:
  Supercharts/Screener/Heatmap/Calendar/News; UW: Live Flow/Flow Alerts/Overview/
  Dark Pool/Earnings/Alerts)
- Full-height iframe (`calc(100vh - 170px)`); two-way ticker sync
- Without the helper extension: setup panel with download link, install steps,
  fine-print explanation; mobile: explanation + external link

## Screen connections (how screens link)

- Any row/chip with a symbol → sets global ticker → all per-stock tabs update.
- Scanner rows → Trade (1-Min) or Finviz tab. Pattern rows → Backtest tab
  (prefilled) or watches. Juice rows → Trade/strategy cards. Embedded-site clicks
  → global ticker. Sidebar FV/TV/UW → row-2 tabs. Earnings chip → (info only).
- Manage is the hub for account/data plumbing (Schwab OAuth, push, imports).

## Reference file map

- `app.jsx` — shell, sidebar, tab panels, global state (~6.5k lines)
- `app-cards.jsx` — nearly all cards (~13.5k lines) · `app-lib.jsx` — helpers/TABS
- `charts.jsx` — chart components · `strategies.jsx` — strategy logic
- `tweaks-panel.jsx` — settings · `tooltips.jsx` — glossary tooltips · `styles.css`
- Backend: `options_dashboard.py` (server/endpoints), `intraday.py` (radar),
  `juice.py`, `backtest.py`, `patterns.py`, `schwab_client.py`, `uw_client`/flow,
  `watchlist_table.py`, `storage.py`
- Extension: `finviz-helper/` (v2.7) + `/finviz-helper.zip`
