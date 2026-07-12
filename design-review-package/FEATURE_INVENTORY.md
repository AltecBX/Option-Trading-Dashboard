# FEATURE_INVENTORY

Classification: **P** = primary (daily loop) · **S** = supporting · **X** =
experimental/new · **U** = unfinished/partial. "Where" names the tab or region.
Every feature listed carries hover tooltips (a product-wide rule).

## Global shell

| Feature | Class | Where | What the user sees / does | Supports |
|---|---|---|---|---|
| Global ticker | P | Sidebar input + everywhere | Type/select a symbol; nearly every card follows it; two-way sync with embedded sites | Everything per-stock |
| Two-row tab bar | P | Top | Row 1: 15 app tabs, draggable order (saved); row 2 "Sites -": Finviz, TradingView, Unusual Whales (brand-colored) | Navigation |
| Earnings chip | S | Tab bar right | "TICKER EARNINGS date in Nd", amber when soon | Event risk |
| Sidebar brand block | S | Sidebar top | Logo, live clock/date, SCHWAB LIVE badge, UW rate badge, version pill (v3.45), weather pill (top-right, tap toggles location) | Status/trust |
| Quick toggles | S | Sidebar | Theme toggle, reference/book icon, ~5k tweaks icon (opens Tweaks panel) | Preferences |
| Ticker identity | P | Sidebar | Company logo, name, price, day change %, stale marker, P/E, Fwd P/E, dividend yield | Identity/health |
| Watchlist chips | P | Sidebar | Starred symbols as chips (active highlighted); ☆ star button; Manage (n) button; FV/TV/UW brand-colored jump buttons | Fast switching |
| Presets row | P | Sidebar | Editable pinned symbols (Edit button) | Fast switching |
| Weeks of history slider | S | Sidebar | 1–52 weeks for the daily chart | Chart range |
| Expiration selector | P | Sidebar | Choose option expiration used by strategy/EM cards | Options context |
| Strike picker | P | Sidebar | Strike selection method for recommendations | Options context |
| Return baseline | S | Sidebar | Baseline for return calculations | Analytics |
| Command palette | S | Keyboard overlay | Fuzzy actions: jump to tabs incl. Finviz/TV/UW | Speed |
| Radar toasts | P | Overlay | Score ≥ 80 signals pop as toasts (per-day dedupe) | Live alerting |
| Push notifications | P | Phone | Radar ≥ 85, pattern watches, roll flags, test push | Away-from-desk |
| Root/Card error boundaries | S | Everywhere | Card-level "failed to render → Retry"; app-level crash screen with reload | Resilience |
| Tooltips | P | Everywhere | Every metric/control explains itself on hover | Learnability |

## Trade tab

| Feature | Class | What / actions | Supports |
|---|---|---|---|
| Price chart (Daily ↔ 1-Min) | P | Toggle timeframe; EM band overlay; intraday shows VWAP/day levels; weeks slider drives range | Entry/exit timing |
| Expected Move card | P | Spot, expiry switcher (weekly/next/monthly/earnings), DTE, ATM IV, IV pct/rank, EM$/%, bounds, method, comparisons (avg actual move, prior EM, post-earnings avg, day H/L, S/R), verdicts | Range expectations, premium rich/cheap |
| CSP / CC recommendation pair | P | Cash-secured-put and covered-call candidate strikes with rationale | Premium selling |
| Strategy card | P | Suggested structures for the ticker with defined/undefined risk labels | Structure choice |
| Trade Builder card | P | Compose contracts (legs, strikes, expiry) and see pricing/Greeks | Order construction |
| Theta panel | S | Decay schedule visualization | Premium selling |
| Vol / Skew card | S | IV term/skew view | Strike choice |
| Level reprice card | S | Reprices options at user-chosen price levels ("if it goes to X") | Scenario planning |
| Roll manager | S | Active short calls & roll choices | Position management |
| Watchlist alerts card | S | Background watchlist scan findings (dismissible) | Monitoring |
| News ticker | S | Scrolling headlines | Context |
| P/L & returns charts | S | Strategy P/L visualization | Risk shape |

## Discover tab

| Feature | Class | What | Supports |
|---|---|---|---|
| Screeners hub | P | Sub-tabs: **Analyst calls**, **Movers**, **Trend**, **Vol Rank**; each a ranked board with research-jump actions | Idea generation |

## Analyze tab

| Feature | Class | What | Supports |
|---|---|---|---|
| Analyst card | S | Price targets, ratings, catalysts (with company fallback chain) | Conviction |
| Company profile / valuation card | S | Fundamentals & valuation summary | Context |
| Basing card | S | Base-building/consolidation detection | Swing entries |
| Pullback profile card | S | Open behavior / pullback tendencies of the stock | Entry style |
| Returns / day-bar charts | S | Historical return visuals, macro kicker | Context |

## Patterns tab

| Feature | Class | What | Supports |
|---|---|---|---|
| Pattern Discovery card | X/P | Auto-discovers per-stock behaviors (10y daily + shape clustering); ranked by actionability; reliability labels; full stats on expand | "What does this stock do?" |
| Current Setup section | X/P | Active-now patterns ranked, top 3 with expected band, target/stop probabilities, invalidation price, typical days, earnings flag | Live decision |
| Ask box (NL research) | X | Free-text question → visible rules → measured answer | Ad-hoc research |
| First-touch stats | X | P(target before stop), medians | Realistic targets/stops |
| Occurrence chart | X | All occurrences + avg/median paths + p25–p75 band | Visual proof |
| Pattern actions | X | → Backtest, → Options backtest, ⚑ Watch/alert, ⌕ Scan watchlist | Research→trade |
| Intraday sequences | X | Minute-bar sequence mining job; ordered-event sentences with exact outcomes; disk archive grows | Intraday behavior |
| Watched patterns list | X | Live TRIGGERED badges, 30-min checks, push | Alerts |
| Swing pattern card | S | The earlier preset swing-pattern view (kept) | Swing setups |

## News tab
| NewsHub | S | Per-ticker + market news with filters | Catalysts |

## Flow tab

| Feature | Class | What | Supports |
|---|---|---|---|
| Flow score card | P | Unusual Whales real-time options-flow score for the ticker | Direction confirmation |
| Earnings crush card | S | IV crush behavior around earnings | Event options |
| UW market endpoints | S | Market tide, sector flow, net premium, Greek exposure, strike flow, premium richness, momentum, dark pool (surfaced within Flow/related cards) | Market context |

## Scanners tab

| Feature | Class | What | Supports |
|---|---|---|---|
| Reversal Radar | P | Two-stage $5B+ scan; Long/Short ranked lists; 0–100 score with group chips; tickets (entry/stop/T1=VWAP); flow bonus; trend-day guard; expandable rows; Chart→ / Finviz→ | The core edge |
| Radar report | S | Resolved-signal hit-rate report; self-tuning time-of-day weights (≥ 20 resolved signals) | Trust/tuning |
| Open reclaim scanner | P | Gap-down-then-reclaim-the-open longs ("CRDO pattern") | Specific setup |

## 0DTE Juice tab
| Premium Juice board | P | Ranked 0–3 DTE premium richness (Juice Score); strategy suggestions labeled DEFINED/UNDEFINED; defined-first when earnings inside window or spot > $400; stale-keep on rate limits | Premium selling |

## Backtest tab
| Backtest Lab | X/P | English → explicit rules (editable, JSON view) → background run with progress → metrics, equity curve, regime breakdown, trade log; accepts prefills from Patterns | Idea validation |

## Breadth tab
| Market breadth card / overview / posture | S | Breadth metrics, market overview, posture read | Regime awareness |

## Journal tab
| Picks journal + trade journal | S | Record ideas/trades, review outcomes | Discipline |

## Watchlist tab
| Watchlist board | P | ~1,276-row table: price, change, tag, sector, industry, weekly-options flag, alerts; filter/sort; CSV-imported metadata is source of truth | Universe management |
| Watchlist analyst card | S | Analyst-move alerts across the watchlist (background scan; company-name fallback chain) | Catalyst monitoring |
| Watchlist streaks (Streaks tab) | S | Consecutive up/down day streaks across the watchlist | Mean-reversion ideas |

## Additional global widgets
| Percent calculator | S | Sidebar-adjacent quick % calculator seeded with the live price of the active ticker | Sizing/level math |
| Max-pain pill | S | Max-pain strike shown on options cards (Trade tab) | Pinning awareness |
| Wide-screen mini-rails | S | On very wide desktops: left/right rails listing 52-week and daily high/low proximity across the watchlist | Peripheral scanning |

## Market Calendar tab
| Market calendar card | S | Macro events / market schedule | Event risk |

## Manage tab

| Feature | Class | What | Supports |
|---|---|---|---|
| Watchlist manager | P | The ONLY place to remove symbols (protects metadata); add, tag, star | Data integrity |
| Broker import | S | Import Schwab account positions | Position tracking |
| Positions card | S | Current positions view | Monitoring |
| Win-rate card | S | Outcome statistics | Feedback |
| Push settings | S | Configure/test push notifications | Alerting |
| Schwab reconnect | S | OAuth reconnect flow (paste redirect URL) | Data lifeline |
| Strategy reference | S | Options strategy reference/education card | Learning |

## Embedded sites (row-2 tabs)

| Feature | Class | What | Supports |
|---|---|---|---|
| Finviz panel | P | Live finviz.com/elite in-app (helper extension); toolbar row 1: ticker chip, add-only watchlist star, radar/juice badges, Trade→/1-Min→, cookie chip, helper-update chip; row 2: Follow toggle, Elite/Free, ↺ticker, Reload, ⧉, nav chips (Screener/Portfolio/Map/Earnings/News/My-watchlist) | Research |
| TradingView panel | P | Live tradingview.com with real account; Sign in ↗ popup flow; Repair session; chips: Supercharts/Screener/Heatmap/Calendar/News | Charting |
| Unusual Whales panel | P | Live unusualwhales.com; chips: Live Flow/Flow Alerts/Overview/Dark Pool/Earnings/Alerts | Flow research |
| Two-way ticker sync | P | Clicking a stock inside any embedded site updates the app; app ticker changes navigate the frames (Follow toggle per site) | Cohesion |
| Site Helper extension | P | User-installed MV3 extension (v2.7) enabling embedding + login persistence (incl. Comet compat mode); zip download + status chips in-app | Enabler |

## Unfinished / partial (U)

- Radar self-tuning has shipped but needs ~2 weeks of resolved signals to activate.
- Intraday sequence archive starts thin (~6 months max) and grows with use.
- Options backtests are model-priced (documented limitation, not a bug).
- Helper diagnostics are labeled "temporary" in the extension.
