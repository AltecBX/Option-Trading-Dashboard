# Short Premium Opportunity Engine — Best Sales Today (v4.80)

The one question a premium seller has, asked of the whole market at once:

> Which option has the strongest EVIDENCE-BASED chance of eventually
> expiring worthless while still paying enough premium to justify the risk?

`sp_probability.py` · `sp_evidence.py` · `sp_engine.py` · `sell_scan.py` ·
`sp_forward.py` · `tab-sell.jsx` · `GET /api/sell*` · Trade tab, top card.

This document is the contract: what the engine claims, what it measured,
what it modeled, what it refused to invent, and what it still cannot know.

---

## 1. Why this exists and what it replaces

Before v4.80 the app had four places that answered part of the question,
each with its own probability and its own ranking:

| Surface | Probability it used | Ranked by | Horizon |
|---|---|---|---|
| Premium Edge (`premium_edge.py`) | lognormal at ExpectedRV, 30 days | richness score | fixed 30 |
| Best Setup (`setup_engine.py`) | measured touch curve + delta | evidence rules | contract |
| Sell board (`setup_board.py`) | none (richness only) | percentile of own history | 30 |
| Investment CSP optimizer | delta | yield | long |

None of them separated P(expire worthless) from P(touch) from P(profit);
none of them recorded what they claimed so it could be graded later; and
the audit found that a probability had never once been persisted next to
its outcome, so no calibration data existed anywhere in the app.

v4.80 does not add a fifth engine. It adds ONE canonical evaluator that the
existing pieces feed:

- `premium_edge.contract_economics` still prices every contract (credit at
  the BID, fair value at the forecast, closed-form tail) — reused.
- `premium_edge.liquidity_gate`, `danger_model`, the term/skew/VRP context
  and the observation store — reused.
- The Premium Edge chain fetch is the ONLY chain fetch. `sell_scan`
  registers as a chain consumer (`edge_scan.register_chain_consumer`), so
  one bounded chain per name feeds both engines inside the same Schwab
  budget. No second fetch, no second scheduler.
- `setup_engine` keeps its per-symbol job (the explained trade on the
  symbol you are looking at) and received the same Phase A defect fixes.

## 2. Success criteria (stated before the build)

1. Every probability on screen names its basis: MODEL, MEASURED (own
   history, with the independent sample size) or CONSERVATIVE (bound).
2. P0, P(ITM), P(touch), P(profit) and the early-target probabilities are
   separate numbers, never one number wearing five labels.
3. NO TRADE is a valid answer and renders as one, with every refusal listed.
4. Every recommendation shown is recorded with its probabilities, its
   config hash and its engine version, and is graded after expiry.
5. Model choices are made by walk-forward evidence and the losers are
   documented, not deleted.
6. The chain budget does not grow.

All six are met; the evidence is below.

---

## 3. The decision hierarchy (gates run in order, out loud)

| # | Gate | Refuses when | Source |
|---|---|---|---|
| 1 | Data integrity | quote older than 15 min / chain older than 30 min / bars older than 4 days / one-sided quote / provider not answering | `_gate_data` |
| 2 | Liquidity | spread > mode limit, open interest below floor, no volume and thin interest, underlying dollar volume < $5M | `premium_edge.liquidity_gate` + mode |
| 3 | Event risk | earnings inside the option's life (+2-day buffer) in every normal mode; macro event on expiry day noted; ex-dividend on a short call flagged (no source wired) | `_gate_events` |
| 4 | Positive edge after costs | EV per contract after commissions, fees and slippage below the mode floor; credit below floor; return on capital below floor; premium ratio (IV / forecast) ≤ 1 | `_gate_edge` |
| 5 | Tail risk | tail points above the mode allowance: 95th-percentile historical gap reaches the strike (+15), realized vol accelerating ≥1.6× its 20-day pace (+10), backwardation (+10), VIX ≥ 85th percentile (+10), ≤3 DTE and |Δ| ≥ 0.30 (+12), extreme skew (+6), modeled short index gamma (+8) — and ES95 more than N× the credit | `_gate_tail` |
| 6 | Probability / calibration | modeled P0 below the mode floor, or CONSERVATIVE P0 below its floor | `_gate_probability` |

A candidate that fails any gate is REJECTED with the gate and reason
attached; nothing is scored around a failed gate. `rejection_summary`
groups the refusals by gate and reason SHAPE (numbers stripped) so the
"WHY OTHER STOCKS FAILED" list is a handful of lines with counts and
symbols, not a thousand near-duplicates.

**EVENT PREMIUM mode** is the one place earnings are allowed: it REQUIRES a
known earnings date inside the life of the option and evaluates the
contract on the measured earnings-move history the Premium Edge engine
already keeps. It is never mixed with the normal modes.

---

## 4. Probability engine (`sp_probability.py`)

Separated outputs per contract, all at the CONTRACT's horizon:

| Symbol | Meaning | Basis |
|---|---|---|
| P0 | expires worthless (1 − P(ITM)) | MODEL: driftless lognormal at the horizon forecast, tail-corrected |
| P0 measured | the same event from this stock's own history at this distance and horizon, shrunk toward peers/universe | MEASURED (n, n_eff shown) |
| P0 conservative | min(model, Wilson lower bound of measured) when n_eff ≥ 20, else model − 5 points | BOUND |
| P(touch) | strike reached at any point before expiry | MODEL: reflection principle 2·N(−|z|), horizon-ratio corrected |
| P(touch) measured | touched within the horizon, own history | MEASURED |
| P(profit) | finishes worth less than the net credit | MODEL |
| P(50/75/90% of credit by day d), P(near-zero) | early-target reachability and timing | MODELED (2,000 lognormal paths at entry IV held flat) |
| ES95 / ES99 | average loss in the worst 5% / 1% | MODEL (closed form, `premium_edge._tail_es_short`) |

Delta is shown as what it is: the risk-neutral probability the market
charges, never P0.

### What was tested and what won (walk-forward, 100 names × 10 years, point-in-time, scored on the second half)

| Question | Candidates | Winner | Why the others lost |
|---|---|---|---|
| Terminal distribution for P(ITM) | lognormal at forecast · Student-t(4) · empirical | **lognormal** (ECE 0.6–1.5%; at 1σ claimed 15.9% vs realized 15.5%) | t(4) under-predicts near strikes; empirical over-predicts |
| Tails | none · fixed fat-tail multiplier · measured table | **measured table** {1.25σ: 1.00, 1.5σ: 1.054, 2σ: 1.345} | lognormal under-predicts beyond 1.5σ (at 2σ: 2.3% claimed vs 3.1% realized) |
| Touch | continuous reflection · BGK discrete-monitoring shift (β = 0.5826) | **continuous** (ECE 1.2–2.6%) with horizon ratios {5: 0.93, 10: 0.96, 21: 1.01, 42: 1.04} | BGK is for close-monitored barriers; strikes are touched on highs/lows |
| Horizon-specific vol blends | per-bucket short blends · 60-day long blend · one flat blend | **flat blend** {RV20 .30, EWMA94 .35, PARK20C .35}, horizon enters only via √T | short blends lost at every horizon; the long blend won 2024–26 but lost 2018–21 including 2020 — rejected by the both-halves rule |
| Calibration layer | none · Platt · isotonic · beta | **none applied** — `sp_forward.learning_check` reports whether Platt on the first half improves the second half; a human applies it | no graded live data exists yet (see §8) |

Overlapping windows are not independent trials: `n_eff` counts starts spaced
at least one horizon apart and every Wilson interval uses it.

## 5. Evidence engine (`sp_evidence.py`)

Per ticker, from daily bars, fixed rules, no lookahead: for horizons
{5, 10, 21, 31, 42} sessions and distances {0.5 … 2.0}σ (σ = trailing
20-day realized), the finish rate, touch rate, overshoot beyond the strike,
first-touch timing, re-crossings and overnight-gap sizes — overall and in
six pre-declared states (low/mid/high vol regime, run-up, run-down, after a
big move). Each cell carries n, n_eff and a Wilson interval.

Hierarchical shrinkage, weakest to strongest evidence:

    ticker-in-state → ticker → sector / vol-regime peers → universe prior

with empirical-Bayes weight `p = (x·n_eff + κ·prior) / (n_eff + κ)`,
κ = 40. Every strike reports `weight_own` — the share of its probability
that came from this stock's own history — and the UI prints it.

The universe prior (`fixtures/sp_universe_calibration.json`) is the measured
finish/touch ratio table from the 100-name validation, so a thin ticker
falls back to something measured rather than to a textbook.

---

## 6. Ranking objectives and modes — what the backtest says

`validate_rank.py` (kept under the session scratchpad; results saved in
`fixtures/sp_rank_validation.json`) ran the REAL engine — `build_candidates`
→ `evaluate` with every gate — on 50 names, decision dates every 42 sessions
from April 2018 to May 2026, plus a dense pass (every 5 sessions) from
January 22 to June 29, 2020. Premiums are MODELED (Black-Scholes at the
engine's own forecast × a fixed 1.15 variance-risk premium with a put
skew); finishes, touches and P&L paths are MEASURED from real bars. That
makes it a fair comparison OF OBJECTIVES under one premium relationship,
not a claim about live P&L.

Full sample, Balanced gates, top-1 pick per objective per name-date
(n = 2,175 picks; ROC = P&L per capital per trade):

| Objective | worthless | touched | mean ROC | ROC sd | worst-5% ROC | P&L per tail | held both halves? |
|---|---|---|---|---|---|---|---|
| max P0 conservative | 92.5% | 13.4% | +0.28% | 17.6 | −68% | 0.058 | mixed |
| **max EV per tail (Balanced)** | 92.3% | 12.9% | −0.12% | 15.9 | −59% | **0.097** | **yes** (0.049 / 0.215) |
| max annualized ROC (Income) | 81.6% | 36.1% | +4.66% | 33.1 | −101% | 0.046 | yes on ROC, no on tail |
| max Sell Quality | 92.9% | 13.6% | +1.05% | 18.0 | −70% | 0.067 | tie / lower |
| max credit (novice control) | 78.4% | 41.2% | +2.96% | 39.7 | −101% | 0.028 | — |
| random (control) | 86.0% | 27.9% | +2.48% | 24.3 | −93% | 0.051 | — |

Readings:

- **The probability engine is calibrated out of sample.** Across the safe
  objectives the model claimed P0 ≈ 93% and 92–93% expired worthless; the
  conservative bound (≈ 85%) sat below both, as designed. Income mode
  claimed 69% and delivered 71%.
- **Balanced's objective (EV per unit of tail) is the only one that beat
  every alternative on risk-adjusted P&L in BOTH halves.** It is the
  default and stays the default.
- **Income's objective pays the most and hurts the most**: +7% mean ROC per
  trade as a mode, but 54% of its picks touched the strike and the worst 5%
  lose the whole width. The mode exists because the user asked for it; the
  card says so on the mode button.
- **Far-out-of-the-money selling at a 1.15× premium is close to zero
  expected ROC.** Tiny credits against full-width losses. The edge, when
  there is one, comes from selection (which names pay far more than 1.15×)
  and refusal — exactly what the gates and the Premium Edge richness do.
- **Kelly**: with independent samples this small (n_eff typically 20–40 per
  strike) any Kelly fraction is noise; the risk pathway says "quarter-Kelly
  is the ceiling" only when n_eff ≥ 40 and otherwise prescribes a fixed
  ≤2% risk per trade. Not "optimised".

### The 2020 stress pass (dense dates)

| Window | Balanced | Conservative | Income |
|---|---|---|---|
| Jan 22 – Feb 28, 2020 (before the crash) | claimed P0 92% → **37% worthless**, 89% touched, mean ROC −41%; NO TRADE on 6% of dates | claimed 96% → 49% worthless; **NO TRADE on 77% of dates** | claimed 69% → 36% worthless, −40% |
| Mar 2 – Jun 29, 2020 (after) | 98.8% worthless, +1.6% ROC, worst-5% −4% | 99.6% worthless, +1.5%, worst-5% −0.7% | 76% worthless, +8.4%, worst-5% −101% |

No gate saw the crash coming from February 7. The only defense the system
had was Conservative mode refusing to trade on three dates in four, which
is why NO TRADE is treated as a first-class answer and why the "what could
make this wrong" list on every #1 now carries this measured sentence.

---

## 7. Sell Quality score (summary, not the objective)

Eight components, each 0–100 with a one-line reason, weights in
`thresholds.json → short_premium.weights` (hypotheses, backtest-scored):
Safety 22 · Edge 18 · Income efficiency 12 · Liquidity 10 · Tail 14 ·
Event 8 · Data confidence 8 · Calibration 8. The board is ORDERED by the
mode's objective; the score breaks ties and explains.

## 8. Forward test and calibration (`sp_forward.py`)

Every row the board shows is appended to `<data>/sell/predictions/
YYYY-MM-DD.jsonl` once per day per contract per mode, with its quotes,
probabilities, tail, score, engine version and config hash. After
expiration the grader reads daily bars and records:

| Field | Label |
|---|---|
| expired worthless / touched / max excursion (σ) | MEASURED |
| P&L per share | MODELED (intrinsic at expiry vs credit — there is no option price history) |
| early-profit targets hit | UNAVAILABLE (never invented) |

Calibration (`/api/sell/calibration`, card panel "what the app claimed
versus what happened"): Brier, log loss, expected calibration error,
reliability buckets with Wilson intervals, Murphy decomposition — overall
and by side, DTE bucket, delta bucket, mode and strategy. A slice under 30
graded recommendations is **ACCRUING**; with none it is **UNAVAILABLE**.

The learning loop is controlled: `learning_check` fits a Platt adjustment
on the first half of the graded history and reports whether it improves
the second half out of sample. It never rewrites the engine's numbers.

As of this build the live calibration is UNAVAILABLE — nothing has been
graded because nothing had ever been recorded. It starts accruing with the
first scan.

## 9. Risk pathway (per finalist)

ENTRY (rest at the bid; do not chase past 95% of it) · MANAGEMENT (modeled
early-target odds and days; invalidation triggers) · DANGER (price,
volatility, event, market regime — in this contract's own numbers) · EXIT
(residual reward vs residual risk: under 25% of credit left with more than
five days to go) · ROLL (must improve P0, EV and tail at its own horizon) ·
ASSIGNMENT (what it means for this structure) · POSITION SIZE (on the
conservative P0 and the tail; Kelly only with n_eff ≥ 40).

Whether taking 50% early beats holding is graded by the forward test, not
assumed from broker folklore; there is no peer-reviewed evidence either
way.

## 10. Portfolio awareness

`portfolio_concentration` flags the top five when ≥60% share a sector, an
expiration, or are all short puts ("one long-market bet"). The board caps
rows per symbol (`scan.max_rows_per_symbol`, default 3) so one rich name
cannot fill the list; every contract stays reachable through
`/api/sell/detail`.

## 10a. What identifies a contract, and how fresh the board must be (v4.81)

**One identity.** `sp_engine.contract_id` — symbol, strategy, side,
expiration and all four strikes. A short strike is not an identity: three
put credit spreads can share one and differ only in the wing, and two iron
condors can share a put wing. The v4.80 board keyed rows by the short
strike, so those merged; React reconciles a keyed list by key, and a keyed
list with duplicates leaves stale rows mounted when it re-sorts — clicking
a column header appeared to add duplicate rows. Every row, the risk-pathway
map, `top_detail` and the forward-test records now carry `row_id`, and the
card keys on it.

**Freshness is a gate, not a caption.** The board persists to disk and
reloads on restart. Anything not evaluated TODAY on the exchange clock, or
older than `scan.max_board_age_hours` (default 12), is dropped and counted
in `stale_dropped` / `stale_symbols` rather than ranked — its quotes, its
spot and its days-to-expiry are all from then. In v4.80 that gap put
contracts expiring TODAY on the board labeled "1 day". A contract at or
past its expiration is never offered in any case.

**Timestamps carry their offset.** The scanner runs on the exchange clock
(`now_fn`) and every stamp is timezone-aware, so a UTC container no longer
tells a reader in New York that a board built at 6:39 AM their time was
built at 10:39, and an age can never render negative.

## 11. Endpoints

`GET /api/sell?mode=&strategy=&top=&record=` · `/api/sell/detail?symbol=&mode=`
· `/api/sell/scan?force=` (kicks the shared Premium Edge chain pass) ·
`/api/sell/status` · `/api/sell/config` · `/api/sell/predictions?days=` ·
`/api/sell/calibration?refresh=` · `/api/sell/grade`. All smoke-tested.

## 12. MEASURED / MODELED / PROXY / ACCRUING / UNAVAILABLE

- MEASURED: breach tables, touch/finish rates, gap sizes, first-touch
  timing, universe prior, the walk-forward calibration of the model, the
  2020 stress result, graded finishes and touches.
- MODELED: P0/P(ITM)/P(touch)/P(profit)/ES on the lognormal at the forecast,
  early-target paths, backtest premiums (1.15× VRP), graded P&L.
- PROXY: sector from SIC when Yahoo is quiet (`sector_map`), macro events
  from the app's calendar, ex-dividend risk on calls (flagged, no source).
- ACCRUING: live calibration slices under 30 graded rows; VRP percentiles
  per name (observation store).
- UNAVAILABLE: early-profit-target outcomes; option price paths; any
  claim about live P&L.

## 13. Limitations (data, not code)

- No option price history: early-target grading and live-P&L calibration
  cannot be measured until `chain_store` snapshots cover the recommended
  contracts. The labels flip by themselves when they do.
- Earnings dates come from the existing feed; an unscheduled event is
  invisible to gate 3 by construction.
- No ex-dividend source: calls are flagged, never cleared.
- Regime shifts: the forecast is a backward-looking blend; February 2020
  is the measured example. Conservative mode's refusal rate is the defense.
- The ranking backtest uses one modeled premium relationship for every
  name; the live edge depends on the Premium Edge richness measurement,
  which is ACCRUING per name.
- The universe prior and the tail table were built on 100 Nasdaq-listed
  large names; small caps and ETFs borrow from them until their own
  history is thick enough.

## 14. Next highest-value improvement

Persist the quoted contract's mark daily (`chain_store` already snapshots
opportunistically; make the recommended contracts a required snapshot set).
With ~6 months of that, the early-target rule (50% / 21 DTE) can be
MEASURED instead of MODELED, P&L calibration becomes real, and
`learning_check` has something to act on.

## 15. Tests

`test_sp_probability` (30) · `test_sp_evidence` (17) · `test_v477_seller_defects`
(18; 13 fail against the pre-v4.77 code) · `test_sell_scan` (14) ·
`test_sp_forward` (18) · `test_sell_ui.js` (83 source guards) ·
`test_http_smoke` (+10 routes). Invariants: P0 + P(ITM) = 1; P(touch) ≥
P(ITM); conservative ≤ model; no structure listed twice; a stale quote is
refused before anything is scored; grading is idempotent and never before
expiry; Wilson on n_eff; every UI column has a defined tooltip; every date
spelled out.
