# Backtest v2 — Institutional-Grade Upgrade Plan

Working doc for the Backtest transformation. Factual review first, then the
gap analysis, then the phased build spec. Each phase lands with tests and a
log entry, same discipline as the v3.64 upgrade (IMPLEMENTATION_LOG.md).

---

## 1. Current state (reviewed 2026-07-23, v3.64)

Three DISCONNECTED backtest surfaces:

### A. Backtest Lab (`backtest.py` + BacktestCard, v3.43)
- NL grammar (deterministic regex, not LLM) → JSON rules → editors → engine.
- Engine: event-driven over Schwab daily bars (+1-min for gap/open-cross
  rules, ~6-month reach). Look-ahead-safe (signal at bar t, fill at t+1
  open). Conservative stop-first intra-bar ordering. Job thread + progress.
- Instruments: **long stock, short stock, LONG single-leg options only.**
  `run_backtest` line ~590: *"Selling options short isn't modeled — the
  bearish view is expressed as LONG puts instead."* → **the user's actual
  strategies (CSP, covered call, strangle, condor, fly) cannot be tested
  in the Lab at all.**
- Option pricing: BS on HV20 × **flat 1.1** IV proxy; re-priced daily off
  underlying OHLC (put hi/lo swapped — good detail); flat spread
  max($0.02, 1.5%); commission max(user, $0.65/ct).
- No assignment, no early exercise, no dividends, no multi-leg, no rolls.
- Portfolio: max_positions gate + fixed $ per trade. No buying power, no
  margin, no marks on open positions (equity curve = realized P/L only),
  no portfolio Greeks/exposure.
- Metrics: total return, P/L, win rate, avg win/loss, PF, maxDD (realized
  curve), expectancy, SPY-regime split, best/worst, last-400 trade list.
  Missing: Sharpe/Sortino/CAGR/exposure/time-in-market, MAE/MFE, streaks,
  monthly returns, per-DTE/delta/symbol/exit-reason breakdowns, benchmark.
- Validation: **none** (no walk-forward, no Monte Carlo, no optimization,
  no OOS, no sensitivity).
- Earnings: the grammar REJECTS earnings conditions ("no reliable
  historical earnings calendar") — yet the app itself has an earnings
  calendar, per-symbol earnings ladders with realized post-print moves,
  and days_to_earnings on every board. Unused here.
- IV history: the app has been snapshotting IV30 per symbol daily since
  v1.14 (`storage.iv_history`) — unused by backtests.

### B. `backtest_strategy()` (options_dashboard ~2491, WinRateCard)
- Separate weekly options-selling walk-forward: 7 structures (CC, CSP,
  short strangle, IC, bull put spread, jade lizard, wheel).
- Monday-entry → Friday-expiry ONLY, hold-to-expiry ONLY (no management),
  costs "ignored" by design, no assignment, $/share units, yfinance direct,
  HV20-as-IV (no ×1.1 here — inconsistent with the Lab).
- Duplicates leg-building/pricing that juice.py + strategies.jsx also do.

### C. Odds & ends
- PullbackBacktest (per-ticker event stats), patterns.py (event-study
  engine WITH real validation: BH-FDR multiple-testing correction, OOS
  fold checks, stability across time folds — machinery the Lab never uses).

### Data reality (constraints everything must respect)
- NO historical option quotes/IV surface (paid: ORATS/OptionMetrics…).
- Schwab: live chains (rich), daily bars (~10y), 1-min bars (~6mo).
- yfinance: daily history, dividends, ^VIX daily closes.
- Already accumulating: IV30/day per symbol, EM readings, earnings ladders.
- numpy/pandas available server-side (yfinance deps). No new heavy deps.
- HARD rules: no order routing / auto-execution; modeled numbers labeled;
  every calculation tested; no framework rewrite.

---

## 2. Gap analysis vs the named platforms

| Capability | TV / TrendSpider | NT8 / MT5 / ForexTester | QuantConnect | Tastytrade / TradeZella | **Us (target)** |
|---|---|---|---|---|---|
| Short premium + multi-leg lifecycle | ✗ | ✗ | ✓ (code, paid data) | canned research only | **✓ native, NL-driven** |
| Management rules (50% PT, 2× stop, 21-DTE, rolls) | ✗ | ✗ | DIY | canned | **✓ first-class** |
| Assignment / early exercise / dividends | ✗ | ✗ | partial | ✗ | **✓ modeled + labeled** |
| Earnings IV ramp/crush in tests | ✗ | ✗ | DIY | ✗ | **✓ (dates known + ladder history)** |
| Premium-assumption sensitivity band | ✗ | ✗ | ✗ | ✗ | **✓ unique** |
| Walk-forward | ✗ | NT8 ✓ | ✓ | ✗ | ✓ |
| Monte Carlo (DD dist., risk-of-ruin) | ✗ | partial | DIY | ✗ | ✓ |
| Overfit protection (deflated Sharpe, plateau, OOS decay) | ✗ | ✗ | ✗ (DIY) | ✗ | **✓ unique at retail** |
| Portfolio BP/margin for options | ✗ | ✗ | ✓ | ✗ | ✓ (broker formulas) |
| Trade replay on chart | replay bars | ✓ | ✗ | ✓ (journal) | ✓ |
| Research → live plan + adherence | ✗ | ✗ | live algo (≠plan) | Zella journals | **✓ closes the loop, no execution** |
| Real options data that improves itself | n/a | n/a | paid | n/a | **✓ EOD chain snapshots from day 1** |
| Honesty labeling of every assumption | ✗ | ✗ | n/a | ✗ | **✓ house style** |

Wedge: nobody at retail combines options-lifecycle realism + validation
rigor + assumption honesty + a research→plan→journal loop. That's the win
condition; feature-count parity is explicitly NOT the goal.

---

## 3. Build phases

### B1 — Options lifecycle engine (`backtest.py` v2, `bt_options.py` new)
ONE leg-based position engine both the Lab and WinRateCard eventually use.
- Structures: short_put (CSP), covered_call, short_strangle, iron_condor,
  put/call credit spread, iron_fly, wheel, long single-leg (existing),
  custom legs. Reuse strike-by-delta solver; leg schema mirrors
  juice/strategies vocabulary.
- Management rules (per structure): profit_take_pct_of_credit (e.g. 50),
  stop_loss_x_credit (e.g. 2.0), exit_at_dte (e.g. 21), roll_at_dte
  (re-open same structure at target delta, chain of trades linked),
  hold_to_expiry. Grammar: "sell a 30 delta put 45 dte, take profit at
  50%, stop at 2x credit, exit at 21 dte, skip earnings week".
- Entry filters: earnings awareness from the app's earnings history
  (skip_earnings_week / only_earnings_week / days_to_earnings ≥ N) —
  replaces the "can't test earnings" rejection for date-based rules.
- Assignment model (labeled): short ITM option assigned when extrinsic <
  $0.03; short calls assigned day before ex-div when div > extrinsic
  (yfinance dividends); assignment → stock position (wheel continues) or
  cash settle + reopen per rules; expiration settlement at intrinsic;
  pin-risk note when |spot−strike| < 0.2% at expiry.
- Costs v2: per-leg spread = f(premium, moneyness, DTE, underlying price
  bucket) with documented table; $0.65/contract + regulatory fees;
  slippage on stock legs as today.
- Portfolio simulator: concurrent positions, daily MARK-TO-MODEL equity
  curve (incl. open positions), buying power per broker formulas (from
  juice.py), BP utilization series, reject entries exceeding BP, net
  delta/theta/vega series (from metrics greeks), exposure %.
- Tests (`test_bt_options.py`): synthetic deterministic bar fixtures per
  structure; management-path cases (PT hit, stop hit, DTE exit, roll
  chain); assignment cases (deep ITM put, pre-div call); BP math; equity
  curve includes open marks; no look-ahead (signal/fill offset).

### B2 — IV & fill realism + self-building real-data layer
- IV model v2 (`bt_iv.py`): base = blend(HV20, HV60); × per-symbol IV/HV
  ratio calibrated from the app's own stored iv_history (≥20 obs, labeled
  "calibrated", else default 1.1 "assumed"); × vol-regime scaler from
  ^VIX daily percentile; earnings ramp (+X% into report, from that
  symbol's ladder implied-vs-realized history) and post-print crush;
  strike skew: put wings richer via delta-linear skew (documented consts).
- Sensitivity harness: every options run auto-repeats at IV mult 0.85 /
  1.00 / 1.15 → band on headline metrics + verdict line ("edge survives
  pessimistic premiums" / "edge is a premium assumption").
- Chain snapshot recorder (`chain_store.py`): EOD job snapshots full
  chains (strike, bid, ask, iv, oi, delta) for starred + juice-board
  names into /data (compact JSON, ~daily). Backtests use REAL snapshots
  when present for entry/exit pricing (per-trade source tag
  real_quote|modeled, counts in the report). Accuracy improves forever;
  after ~3 months the common short-DTE tests are mostly real-quote.
- Tests: IV components pinned; snapshot round-trip; real-vs-model
  precedence; sensitivity determinism.

### B3 — Validation suite (`bt_validate.py`)
- Walk-forward: rolling anchored splits (default 4 folds, 70/30), params
  frozen per fold, WF efficiency = OOS/IS return ratio, per-fold table.
- Monte Carlo (seeded, deterministic): trade-order bootstrap (10k
  resamples) → maxDD/CAGR distributions, P5/P50/P95, risk-of-ruin at
  chosen sizing; entry-jitter robustness (±1 bar) → metric stability.
- Optimizer: small grid over declared params (delta, DTE, PT%, stop×) —
  hard cap ~200 combos; report full grid heatmap, neighborhood-plateau
  score (mean of 3×3 neighborhood vs peak), pick "robust best" not "peak".
- Overfit protection: deflated Sharpe ratio (Bailey & López de Prado —
  trials = combos tested, skew/kurtosis from trade P/L), OOS decay %,
  loud verdicts. PBO-lite via combinatorial split ranking if cheap.
- Benchmarks: SPY buy-hold (same window, same start equity), T-bill carry
  (risk_free_rate), and the NAIVE version of the same structure (e.g.
  always-sell 30Δ/45DTE no filters) — "does YOUR filter beat mindless?".
- Regime matrix: trend (SPY 50/200) × vol (^VIX tercile) cells with n /
  win rate / P&L; concentration warning when >70% of edge in one cell.
- Tests: WF split boundaries (no leakage), MC determinism w/ seed, DSR
  formula fixtures, plateau math, benchmark alignment.

### B4 — Analytics, tear sheet, replay (tab-backtest.jsx v2)
- Validation scorecard header: sample size, WF eff, MC P95 DD, premium
  band, regime concentration, DSR → honest verdict chip row (house
  vocabulary, no black box).
- Tear sheet: equity + drawdown underlay + regime shading; monthly
  returns heatmap; P/L histogram; MAE/MFE scatter (daily-bar resolution,
  labeled); rolling win rate / Sharpe; streaks; breakdown tables (DTE,
  delta bucket, symbol, exit reason, regime cell, day-of-week).
- Trade replay: click trade → lightweight-charts pane (already loaded on
  the page) with underlying candles, entry/exit/management/assignment
  markers, modeled-vs-real option value path, step prev/next.
- Saved runs: keep last N results (data dir), A/B compare two runs
  side-by-side (delta of every headline metric).
- Keep the NL-first flow + JSON power view; add structure presets row
  ("CSP 30Δ 45DTE managed" etc.) that fill the grammar text.

### B5 — Research → live plan (no execution, ever)
- "Deploy as plan": validated run → plan object (rules text, structure,
  entry checklist bound to LIVE scanners — e.g. HV-rank/juice/range
  filters that mirror the tested entry, sizing from validated risk (MC
  P95 DD → suggested per-trade risk), management card, alert wiring via
  existing push system when a live scanner row matches plan criteria).
- Plans stored in /data; Journal integration: trades tagged to plan;
  adherence report (followed vs off-plan count + P/L split) on the
  Journal tab. Framing everywhere: "a plan, not automation".
- Tests: plan serialization, matcher parity (plan criteria ≡ tested
  entry), adherence math.

### Explicit non-goals (clutter guard)
- No order routing / broker execution / auto-trading.
- No tick data, no L2, no FX/futures modules, no social features.
- No genetic/ML optimizers (grid + plateau is honest and sufficient).
- No new charting library, no server framework change.
- WinRateCard folds onto engine v2 (or is labeled legacy) — no third
  parallel engine survives this upgrade.

## 4. Sequencing note
B1 is the foundation and the biggest single win (the user can finally
test what he actually trades). B2 makes numbers trustworthy; B3 makes
conclusions trustworthy; B4 makes them readable; B5 makes them actionable.
Ship order: B1 → B2 → B3 → B4 → B5, each committed with tests + log entry.
