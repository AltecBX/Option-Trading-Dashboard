# PRODUCT_OVERVIEW

## What this application is

**JerryTrade** (deployed at `dashboard.jerrytrade.com`, current version **v3.45**) is a
personal, single-user stock and options trading cockpit built for one active trader
("Jerry"). It is a dense, dark-themed, browser-based dashboard that combines live
brokerage market data (Charles Schwab API), real-time options-flow data (Unusual
Whales API), embedded partner research sites (Finviz, TradingView, Unusual Whales),
and a large amount of in-house analytics: scanners, expected-move math, an intraday
reversal radar, a 0–3 DTE premium scanner, a natural-language backtester, and a
per-stock statistical pattern-discovery engine.

It is NOT a multi-tenant product, has no user accounts beyond an API key, and is
operated by its owner all day during US market hours on desktop, with meaningful
mobile use for monitoring and push alerts.

## The problems it solves

1. **Fragmentation** — before this app the workflow was spread across Schwab,
   Finviz, TradingView, Unusual Whales, spreadsheets, and mental notes. The app is
   the single screen where all of it converges, including the partner sites
   themselves (embedded live, with the user's own logins, and two-way ticker sync).
2. **Timing intraday reversals** — the owner's core edge (see philosophy below)
   requires spotting stocks stretched away from fair intraday value in real time,
   with evidence they are exhausting. Doing this by eye across a 1,200+ symbol
   watchlist is impossible; the Reversal Radar automates it.
3. **Options premium selection** — deciding which 0–3 DTE premium is actually rich
   (vs merely expensive), and which structure (defined vs undefined risk) fits.
4. **Knowing what a stock "usually does"** — the Pattern Discovery engine and the
   Backtest Lab turn "I think this stock bounces after big down days" into measured,
   validated statistics with explicit warnings when an idea cannot be tested honestly.
5. **Not blowing up** — expected-move bands, invalidation levels, first-touch
   probabilities, earnings proximity chips, and market-cap floors keep risk visible.

## Who it is designed for

One experienced retail trader who:
- trades intraday reversals and short-dated options (0–3 DTE) daily;
- **never trades stocks under a $5B market cap** (hard rule encoded in the scanners);
- maintains a large imported watchlist (~1,276 symbols) enriched with tags, sectors,
  industries, and weekly-options flags from a CSV that is the source of truth;
- keeps real accounts at Finviz Elite, TradingView, and Unusual Whales and expects
  them embedded, not linked;
- is on desktop during the session (multi-hour continuous use) and on phone when away
  (push notifications via the configured push service);
- explicitly demands tooltips on **everything** — every metric, chip, and button in
  the app carries a hover explanation.

## How it is used during a daily trading workflow

- **Pre-market**: check the Market Calendar and pre-market game plan, review
  watchlist alerts and analyst moves, scan news, note earnings-proximity chips.
- **Open / morning**: the Scanners tab (Reversal Radar) surfaces stocks near an
  intraday bottom (long candidates) or top (short candidates), each with a scored
  ticket (entry, stop, first target at VWAP). Radar toasts fire at score ≥ 80 and
  push notifications at ≥ 85. The 0DTE Juice tab ranks the richest short-dated
  premium with strategy suggestions.
- **Per-stock drilldown**: selecting any ticker (sidebar input, watchlist chip,
  clicking a row in any scanner, or clicking inside embedded Finviz/TradingView/UW)
  drives the whole app: the Trade tab shows the chart (daily or 1-minute with the
  expected-move band), strategy recommendations, Greeks/theta, and the trade builder;
  Analyze shows analyst targets, valuation, base-building; Patterns shows what the
  stock statistically does next; News shows the tape.
- **Research → decision**: the Patterns tab's Current Setup section states which
  behaviors are active *now*, the expected move band, the probability the target is
  touched before the stop, and the invalidation price. One click converts any pattern
  or idea into a backtest, a scanner sweep, a live alert, or an options structure.
- **After hours**: journal entries, hit-rate reports (radar self-tuning), watchlist
  maintenance in Manage.

## The major decisions the app supports

1. *Which stock do I trade right now?* (Radar, Juice, screeners, watchlist alerts)
2. *Long or short, and is the move exhausted?* (Radar score groups, day levels, VWAP)
3. *Where do I enter, stop out, and take profit?* (tickets, EM band, first-touch stats)
4. *Which options structure, strike, and expiry?* (Juice strategies, EM, IV rank,
   skew, theta, trade builder)
5. *Is this behavior real or noise?* (Pattern Discovery validation stack, Backtest Lab)
6. *Am I fighting the market?* (Breadth, market posture, SPY/QQQ regime context)

## Core philosophy

**Find stocks near the bottom of their day to buy before they reverse higher, and
stocks near the top of their day to short before they reverse lower.** Everything
load-bearing in the app serves that: the Reversal Radar's two-stage scan and scoring
(stretch, exhaustion, location, confirmation, context), the trend-day guard that caps
counter-trend scores, VWAP-anchored targets, the open-reclaim scanner (the "CRDO
pattern"), gap-and-reclaim intraday sequences in Pattern Discovery, and the
mean-reversion classification of discovered patterns. Premium selling (0DTE Juice)
is the complementary income side of the same view: sell premium where the expected
move is overpriced.

## Essential vs secondary features

**Essential (the daily loop):**
- Sidebar global ticker + starred watchlist + presets
- Trade tab (chart with EM band, 1-minute view, strategy recommendations, trade builder)
- Scanners tab (Reversal Radar + open-reclaim) with alerts/push
- 0DTE Juice scanner
- Expected Move card
- Patterns tab (Current Setup + discovered behaviors)
- Embedded Finviz / TradingView / Unusual Whales with ticker sync
- Watchlist board with imported tags/sector/industry/weekly metadata
- Flow tab (Unusual Whales flow score, market tide)

**Secondary / supporting:**
- Breadth, Streaks, Market Calendar, News hub, Journal, Analyst boards,
  Valuation/Basing/Pullback profile cards, Earnings crush card, Roll manager,
  Theta/skew panels, Win-rate card, weather pill, accent themes, command palette.

**Experimental / newest (recently shipped, still maturing):**
- Backtest Lab (natural-language → rules → simulation), Pattern Discovery v2
  (shape clustering, intraday sequence mining, watches/alerts), radar self-tuning
  from resolved-signal hit rates.

## Development status

Actively developed, single developer + AI pair, deployed continuously to Railway
(version stamps in the UI, v3.45 at the time of writing). Data foundations are
stable (Schwab quotes/chains/history at a self-capped 110 requests/min; Unusual
Whales flow; persistent storage on a Railway volume). Recent releases added the
Backtest Lab (v3.43), Pattern Discovery (v3.44), and its v2 (v3.45). Known
data limits are surfaced in-product: ~10 years of daily bars, ~6 months of minute
bars (archived forward), no historical options quotes / IV / news / earnings-date
history — features that would need those refuse or warn rather than fake it.
