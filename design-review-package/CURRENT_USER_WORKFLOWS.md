# CURRENT_USER_WORKFLOWS

These are the workflows **as they exist today**, documented without redesign.
"Sidebar" = the persistent left column; "tab bar" = the two-row switcher at the top
(row 1: app sections, row 2: "Sites -" Finviz / TradingView / Unusual Whales).
Selecting a ticker anywhere (sidebar input, watchlist chip, preset chip, scanner row,
click inside an embedded site) updates the **global ticker** that almost every card
follows.

---

## 1. Finding potential long opportunities (intraday reversal buys)

**Starts:** Scanners tab, usually shortly after the open.
1. Tab bar → **Scanners**. The **Reversal Radar** card shows two ranked lists —
   *Long* (near day's bottom) and *Short* (near day's top) — built from a two-stage
   scan of the $5B+ watchlist (quote screen → minute-bar analysis of the top
   candidates per side).
2. Each row: symbol, score (0–100), score-group chips (stretch / exhaustion /
   location / confirmation / context), price, VWAP distance, day-range position,
   RVOL, flow bonus, and a ready **ticket** (entry, stop = extreme ± 0.25×ATR5m,
   target 1 = VWAP).
3. Row actions: **Chart →** (jumps to Trade tab in 1-Min mode), **Finviz →**
   (embedded Finviz on that symbol), expandable detail row.
4. Alerts arrive without watching: toast at score ≥ 80, push at ≥ 85; the
   **Open reclaim** card below catches the specific gap-down-then-reclaim-open long.
5. Cross-checks the user typically performs: Expected Move card (is there room to
   T1?), Flow tab (is flow agreeing?), Patterns tab Current Setup (does this stock
   statistically bounce?).

**Information needed per stage:** score and why (group chips), distance from VWAP,
ticket levels, liquidity, market regime. **Friction observed:** confirming a radar
row means visiting up to three other tabs (Trade, Flow, Patterns); the ticket is on
the radar row but the EM band is only on the Trade chart.

## 2. Finding potential short opportunities

Identical to Workflow 1 using the radar's *Short* list (stocks stretched above VWAP
into exhaustion). The trend-day guard caps counter-trend scores at 60, so on strong
trend days the short list is intentionally muted. Same actions, same friction.

## 3. Reviewing an individual stock

**Starts:** sidebar ticker input (or any click that changes the global ticker).
1. Sidebar shows identity: company logo, price, day change, stale marker, P/E,
   forward P/E, dividend yield; earnings chip appears in the tab bar
   ("BE EARNINGS 7-28-2026 in 17d").
2. **Trade** tab: price chart (Daily ↔ 1-Min toggle; EM band overlay; weeks-of-
   history slider in sidebar), Expected Move card, CSP/CC recommendation pair,
   strategy card, trade builder, theta panel, vol/skew card, level reprice card,
   roll manager (if short calls), watchlist alerts card, news ticker.
3. **Analyze** tab: analyst targets/ratings/catalysts, company profile, valuation
   card, basing card (base-building detection), pullback profile (open behavior),
   returns chart, macro context.
4. **Patterns** tab: Current Setup (what's active now), discovered behaviors, Ask
   box, intraday sequences.
5. **News** tab: NewsHub for the ticker + market.
6. Embedded **Finviz / TradingView / UW** tabs open the same symbol automatically
   (two-way sync — clicking a different stock inside those sites updates the app).

**Friction observed:** a full review touches 4–6 tabs; there is no single
"stock summary" screen combining the top facts of each.

## 4. Analyzing historical stock behavior

**Starts:** Patterns tab or Backtest tab.
- **Patterns** tab (primary): auto-discovery runs for the selected ticker (~10 years
  of daily bars). Ranked list of behaviors with actionability score, reliability
  label, hit rate vs baseline, first-touch stats, folds, CI, context buckets,
  occurrence chart. The **Ask box** answers free-text questions ("What does NFLX do
  after rising 10% in 3 days?") by translating them into visible rules.
- **Backtest** tab (for strategy-shaped questions): type the strategy in English →
  Interpret → review/edit explicit rules → Run → metrics, equity curve, trade log.
- Charts tab-adjacent: weeks-of-history slider + returns chart on Analyze for a
  quick visual read.

## 5. Finding repeatable stock patterns

**Starts:** Patterns tab.
1. Discovery runs automatically per ticker (cached ~6h); "↺ analyze" re-runs.
2. Filter chips: all / bullish / bearish / mean-reverting / momentum.
3. Each row expands into stats, validation, context ("works best in… fails in…"),
   the occurrence chart, and actions: **→ Backtest**, **→ Options backtest**,
   **⚑ Watch/alert**, **⌕ Scan watchlist** (same setup across starred symbols).
4. **Intraday sequences** section mines minute bars (background job with progress)
   into ordered event sentences ("gaps up, then pulls back to VWAP, then reclaims
   the morning high → closed higher 78% of the time").
5. Watched patterns appear in a "Watched patterns — live signals" list with
   TRIGGERED badges and 30-minute background checks + push.

## 6. Reviewing expected moves

**Starts:** Trade tab, Expected Move card (also the EM band on the chart).
- Card fields: spot, chosen expiration (weekly / next / monthly / earnings
  switcher), DTE, ATM IV, IV percentile/rank, EM$ and EM%, upper/lower bounds,
  method, updated time; comparisons vs average actual move, previous EM,
  post-earnings average, day high/low, and support/resistance; verdict summaries
  ("rich/cheap vs how it actually moves").
- The same EM powers: chart band, Juice score context, radar context, options-idea
  sizing in Patterns.

## 7. Reviewing implied volatility and options premium

**Starts:** Trade tab (vol/skew + theta cards) and Flow tab.
- IV rank/percentile (from locally accumulated IV history per symbol), skew card,
  theta panel (decay schedule), earnings crush card (pre/post earnings IV behavior),
  premium richness (UW), strategy card recommendations with defined-vs-undefined
  risk labeling.

## 8. Finding 0DTE / 1DTE premium opportunities

**Starts:** 0DTE Juice tab.
1. Board of the highest **Juice Score** names for 0–3 DTE (two-stage scan over the
   $5B+ watchlist; ranged chain calls today → +3d).
2. Row: symbol, price, expiry/DTE, IV, EM, straddle/strangle premium, Juice Score,
   earnings-inside-window boost, spread quality, volume/OI.
3. Expanding a row lists **strategy suggestions** (strangle, iron condor, fly,
   spreads, CSP, CC) each labeled **DEFINED** or **UNDEFINED** risk — defined-first
   ordering when earnings are inside the window or spot > $400.
4. Stale handling: on rate-limit starvation the board keeps the last scan with an
   amber "rate-limited — showing the last scan" note instead of blanking.

## 9. Selecting entries, exits, targets, invalidation

**Sources used today:**
- Radar tickets (entry / stop / T1-at-VWAP) on Scanners rows.
- Patterns → Current Setup: target price with P(touched first), stop price with
  P(stop first), invalidation price, typical days.
- Expected Move bounds as natural magnets/limits.
- Day levels (open, prior close, day high/low, OI walls) on the 1-Min chart.
- Backtest Lab for validating the exit scheme (profit/stop/time/trailing).
**Friction observed:** these levels live on four different tabs and are not merged
into one per-stock levels view.

## 10. Converting research into a trade decision

1. Signal appears (radar toast/push, juice row, pattern trigger, watchlist alert).
2. User pulls up the stock (Trade tab), checks EM room, 1-minute structure, flow.
3. For options: strategy card / juice suggestion → Trade Builder card to compose
   the actual contract(s) (strike picker + expiration selector in the sidebar).
4. Optional validation: Patterns first-touch odds; Backtest for the rule version.
5. Execution happens **outside the app** at the broker; the app records the idea in
   the Journal (picks journal / trade journal) and tracks positions imported from
   the broker (Manage → Broker import / Positions card, win-rate card).

## 11. Using imported watchlists, tags, sectors, industries

**Starts:** Watchlist tab (board) and Manage tab (maintenance).
- The CSV import is the **source of truth** for tag/sector/industry/weekly-options
  metadata (overrides live data). The Watchlist board (~1,276 rows) supports
  filtering by tags/sector/industry, sorting, and per-row research jumps.
- **Critical rule:** removing a symbol must ONLY happen in the Manage workflow —
  removal wipes its metadata. Everywhere else the star/add controls are add-only
  (a deliberate guard added after a data-loss incident).
- Starred symbols form the sidebar chip row and the default universe for the
  Backtest Lab, Pattern scans, and watches.

## 12. Using the application during the trading day

- Continuous: radar + juice background workers only run during market hours;
  cards keep last-good data (stale-marked) instead of blanking on rate limits.
- Alert surfaces: toasts (radar ≥ 80), push (radar ≥ 85, pattern watches, roll
  flags, test push from Manage), the tab-bar earnings chip, watchlist alerts card.
- Tab order is user-draggable and persists across devices; tabs stay mounted, so
  switching is instant and embedded sites don't reload.
- Command palette (keyboard) jumps between tabs/actions.
- Mobile: same SPA; the tab rows scroll horizontally; embedded sites are
  desktop-only (mobile shows an explanation and a direct link instead).

---

## Cross-cutting observations (factual, not redesign)

- **Repeated information:** ticker identity (price/change) renders in the sidebar,
  chart header, EM card, and several scanner rows simultaneously. EM values appear
  on the EM card, chart band, juice rows, and pattern options-ideas.
- **Multi-tab confirmation loops:** the long/short decision (Workflow 1/2) and the
  levels decision (Workflow 9) each span 3–4 tabs.
- **Where it can overwhelm:** Trade tab stacks 10+ cards vertically; Patterns rows
  expose ~15 statistics each; the watchlist board is 1,276 rows with many columns.
- **Where it can confuse:** the distinction between the Scanners tab (intraday
  radar), Discover tab (screeners hub), and Watchlist alerts (background scan)
  overlaps in purpose ("what should I look at?"); "0DTE Juice" vs Flow tab premium
  richness likewise overlap partially.
