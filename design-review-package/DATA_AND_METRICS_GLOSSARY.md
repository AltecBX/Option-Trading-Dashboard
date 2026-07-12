# DATA_AND_METRICS_GLOSSARY

Format per item: **meaning · calculation/source · interpretation · where · decision
supported · limitations.** Data sources: Charles Schwab API (quotes, option chains,
daily bars ≈10y, minute bars ≈6mo, self-capped 110 requests/min), Unusual Whales API
(flow), Finviz (news scrape-free embed only), locally accumulated histories on disk.

## Price & chart

- **Daily bars / 1-Min bars** — OHLCV from Schwab; adjusted daily to ~10y, minute
  bars current session + per-day history ~6 months. Charts on Trade tab. Basis for
  everything else. *Limit: minute history is shallow; pre/post-market excluded on
  the day-history endpoint.*
- **VWAP** — volume-weighted average price computed from minute bars, session-
  anchored. Intraday chart + radar tickets (T1) + intraday sequence tokens.
  Interpreted as intraday fair value; reversal targets revert to it.
- **Day levels** — open, prior close, day high/low, and **OI walls** (strikes with
  outsized open interest, 30-min cached) drawn on the 1-min chart. Support/
  resistance for entries/stops. *OI walls are a static snapshot, not live.*
- **Stale tag** — quote older than a freshness threshold (from Schwab trade time).
  Sidebar price + boards. Tells the user not to trust the print.
- **Weeks of history** — sidebar slider setting the daily chart lookback (1–52w).

## Expected Move (EM)

- **EM$ / EM%** — the options-implied move to the chosen expiration, derived from
  the ATM straddle/strangle pricing of the selected chain (with fallback method
  noted on the card). Upper/lower bound = spot ± EM. Trade tab card + chart band +
  juice context + pattern options-ideas. Decision: is there room to target; is
  premium rich vs realized behavior. *Limit: EM is a market forecast, not a
  guarantee; last-good values are kept (marked) when the chain call fails.*
- **ATM IV / IV percentile / IV rank** — implied volatility at the money;
  percentile/rank against the symbol's own locally-accumulated IV history
  (`iv_history` on disk). High rank = expensive options (favor selling), low =
  cheap (favor buying). *Limit: history accumulates only while the app runs.*
- **EM comparisons** — EM vs average actual move over similar windows, vs previous
  EM, vs post-earnings average move, vs day high/low, vs support/resistance, with
  verdict lines ("options price a bigger move than the stock usually makes").

## Reversal Radar

- **Radar score (0–100)** — weighted sum of five groups, each shown as a chip:
  **stretch** (distance from VWAP in intraday σ AND range/RVOL-scaled move),
  **exhaustion** (momentum fading: contracting bars/volume into the extreme),
  **location** (position inside the day range / near day extreme),
  **confirmation** (reclaim/failure bars, first-pullback behavior),
  **context** (market regime alignment, EM room, flow bonus).
  Interpretation: ≥ 85 push-worthy, ≥ 80 toast, ≥ 70 auto-logged, else watch.
- **Trend-day guard** — on strong trend days counter-trend scores are capped at 60.
  Prevents fighting runaway tape.
- **Flow bonus** — Unusual Whales flow-score agreement adds points (budgeted to 8
  fresh lookups/cycle, 5-min TTL).
- **Ticket** — entry ≈ current price, stop = day extreme ± 0.25 × ATR(5m), target 1
  = VWAP. Where: radar row expansion. *Limit: heuristic, not order routing.*
- **Radar report / self-tuning** — resolved signals (first-touch via minute-day
  replay) produce hit rates by time-of-day; after ≥ 20 resolved signals the
  time-of-day weights adapt. *Limit: needs live accumulation.*
- **$5B floor** — stocks under $5B market cap are excluded everywhere (user rule).

## Premium Juice (0–3 DTE)

- **Juice Score** — composite premium-richness rank built from: straddle premium as
  % of spot vs EM, IV level/rank, spread quality (bid/ask tightness), volume/OI,
  earnings-inside-window boost (+75), price/liquidity filters. Higher = richer
  premium per unit of implied risk. Decision: which names to sell premium on.
- **DEFINED / UNDEFINED risk labels** — every suggested structure (strangle, iron
  condor, fly, credit spreads, CSP, CC) is labeled; defined-risk structures are
  ordered first when earnings fall inside the window or spot > $400. *Rule: the
  app never auto-recommends undefined risk merely because premium is high.*
- **Stale row marking** — when a scan cycle is starved by the 110/min cap, the last
  good rows are kept and marked, with an amber "rate-limited" note.

## Pattern Discovery

- **Event / setup** — a detected condition on a bar: percentile-adaptive N-day
  surges/plunges, gaps, one-day shocks on ≥2× volume, new 60/252-day highs/lows,
  3–4 down/up streaks, drawdown-threshold crossings, and **discovered 5-day shapes**
  (unsupervised clustering of vol-normalized return windows).
- **Claim** — "moves ≥ X% within F days in direction D", fitted on the FIRST 70% of
  history only (X = the 35th percentile of in-sample outcomes; ~65% in-sample hit).
- **Hit rate / in-sample / out-of-sample** — % of occurrences meeting the claim;
  IS = fit period, OOS = the untouched last 30%. OOS collapse ⇒ distrust.
- **Baseline** — how often the SAME move follows any random day. The bar to beat.
- **p-value / q-value** — one-sided binomial vs baseline; q = Benjamini-Hochberg
  corrected across ALL candidates searched (shown on the card). q > 0.10 ⇒ label
  **likely random**.
- **Bootstrap CI** — 5–95% resampled interval on the hit rate; wide ⇒ small sample.
- **Folds** — hit rate in each chronological quarter of occurrences (walk-forward);
  wide spread ⇒ **unstable**; weak last fold ⇒ **weakening**.
- **Reliability labels** — reliable / unstable / weakening / likely random /
  insufficient sample (tooltip defines each).
- **First-touch** — race between target (claimed move) and stop (~⅔ of it) using
  daily highs/lows; same-bar double-hit = "ambiguous", counted AGAINST the pattern.
  Outputs P(target first), P(stop first), P(neither), median days each way.
  Decision: realistic entries/targets/stops. *Limit: intra-bar order unknowable on
  daily data (exact on minute-based intraday sequences).*
- **MFE / MAE** — max favorable/adverse excursion within the window (daily
  extremes; approximate). Reward-vs-risk input.
- **EV (net)** — average claimed-direction move minus estimated round-trip spread
  (price-bucket table) and slippage. A high hit rate with negative net EV is
  untradeable.
- **Actionability (0–100)** — ranking score: net EV, OOS-vs-baseline, sample size,
  fold consistency, MFE/MAE ratio, speed, dollar-volume liquidity, recent fold.
  Explicitly NOT just hit rate.
- **Similarity (Current Setup)** — how today's context matches the pattern's best-
  performing context bucket (plus event freshness). Scales actionability into
  **actionability-now**.
- **Invalidation price** — level beyond ~125% of typical adverse excursion; below
  it (for longs) history says the pattern has failed.
- **Context buckets** — occurrences split by SPY trend, QQQ trend, sector-ETF trend
  (sector from watchlist board → SPDR ETF), market vol state (SPY 20d realized vol
  vs median), stock vol state, gap direction, relative volume, year; buckets < 5
  hidden; "works best in… fails in…" note when spread ≥ 15 pts.
- **Intraday sequence** — ordered tokens (gap up/down/flat, holds-above-open-30m,
  OR break up/down, pullback-to-VWAP, loses/reclaims VWAP, reclaims morning high,
  power hour up/down); outcome = move from sequence completion to close (exact).
  FDR-corrected like everything else.

## Backtest Lab

- **Rules** — instrument, direction, universe, entry conditions, exits (profit /
  stop / trailing / time / same-day / hold-to-expiry), sizing, costs, window.
- **Fill model** — signal on bar t → fill at bar t+1 open (no look-ahead); stop
  assumed first when stop+target share a bar; estimated spread by price bucket +
  slippage bps + commissions; liquidity skip when avg dollar volume < 20× position.
- **Metrics** — total return & P/L (from $100k), trades, win rate, avg gain/loss,
  profit factor, max drawdown (realized equity curve), expectancy, best/worst
  trade, skipped (liquidity / max positions), performance by SPY regime, equity
  curve. *Limit: options runs are Black-Scholes model-priced from 20-day realized
  vol ×1.1 — loudly labeled estimates.*

## Flow / market context

- **Flow score** — Unusual Whales-derived composite of options-flow aggression/
  direction for a symbol. Confirmation input to radar and the user.
- **Market tide / sector flow / net premium / Greek exposure / premium richness /
  dark pool** — UW market-wide series shown in Flow-related cards. *Limit: no
  historical archive; current-state only.*
- **SPY/QQQ/sector regime** — uptrend (price > 200-SMA and 50 > 200), downtrend
  (< 200), chop (between). Used in patterns, backtests, radar context.
- **Breadth / posture** — market-wide advance/decline style measures and a summary
  posture read on the Breadth tab.

## Watchlist & misc

- **Tags / sector / industry / weekly flag** — CSV-imported metadata (source of
  truth; overrides live lookups). Weekly = the symbol has weekly options.
- **Starred** — user-pinned subset; feeds sidebar chips and default universes.
- **Streaks** — consecutive up/down closing days per symbol.
- **Analyst board** — targets/ratings/catalyst dates with fallback chain when a
  provider misses. *Limit: third-party estimates.*
- **Earnings chip / days-to-earnings** — next earnings date from the watchlist
  board scan. *Limit: no historical earnings archive.*
- **Win rate (Manage)** — journaled-trade outcome statistics.
- **Weather pill** — Yonkers or device-location weather (novelty/status).
- **Max pain** — strike of maximum option-seller pain where shown on options cards.
- **110/min budget** — the Schwab self-cap; the origin of "stale but never blank"
  behavior across boards.
