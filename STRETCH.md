# At the line — reached its usual high or low, priced (v5.16)

The workflow, as it is done by eye today: open Analyze, see where the
stock's weekly highs usually land above Friday's close, wait for the price
to get there, then sell a call above it — because the week usually closes
below its high. The mirror image with puts after a drop. It works on a
handful of followed names because the watching is manual.

`stretch_evidence.py` · `stretch_scan.py` · `tab-stretch.jsx` ·
`GET /api/stretch*` · top of the Trade tab, and a companion card on Analyze

---

## 1. What was measured before anything was built

Three years of daily bars for 43 liquid names (AAPL, NVDA, META, MSFT,
AMZN, GOOGL, TSLA, AMD, NFLX, AVGO, COIN, PLTR, MU, SMCI, MSTR, SHOP, UBER,
CRM, ORCL, BA, JPM, XOM, COST, WMT, DIS, PYPL, SOFI, MARA, RIVN, ARM, DELL,
HOOD, CRWD, SNOW, ABNB, INTC, QCOM, GME, AMC, SPY, QQQ, IWM, TQQQ), September
2023 – September 2026, about 150 weeks each.

The **line** is the stock's median weekly high (or low) from the prior
week's last close — the dashed line the Analyze chart already draws. Half of
weeks reach it, by construction. The question the chart cannot answer is
what happens *next*. So for every week and every level on a small sigma
grid, the first bar that reached the level was taken as the entry, and only
what followed was measured.

**Reaching the usual high is not, by itself, a sell signal.**

| After crossing the median line… | Calls | Puts |
|---|---|---|
| …the week closed back inside it | 47% | 51% |
| …the line was reached by (typical day) | Tue/Wed | Tue |

The belief the workflow rests on — "the week usually closes below its
high" — is true of the *high*, and near a coin flip at the *line*. Half the
weeks that reach the usual high go on to a higher one.

**How much further it goes depends on how much week is left**, pooled
across the 43 names, in each stock's own weekly sigma:

| Level crossed | Sessions left | Crossings | Closed back inside | Further travel (median) | Nine in ten within |
|---|---|---|---|---|---|
| 1.0σ | 4 | 286 | 45% | +0.90σ | +2.34σ |
| 1.0σ | 3 | 466 | 44% | +0.72σ | +2.07σ |
| 1.0σ | 2 | 527 | 43% | +0.54σ | +1.64σ |
| 1.0σ | 1 | 422 | 45% | +0.37σ | +1.35σ |
| 1.0σ | 0 (Friday) | 330 | 48% | +0.19σ | +0.90σ |
| 2.0σ | 2 | 139 | 42% | +0.75σ | +1.97σ |
| 2.0σ | 0 | 149 | 40% | +0.29σ | +1.22σ |

Puts read the same shape on their own lows (closed back 40–62% depending on
the cell). The further travel after a crossing is a little *more* than a
random walk over the remaining sessions — there is no reversal to be paid
for. That is why the engine measures each strike from the crossing bar on
rather than assuming the high is in.

**The like-for-like check of the workflow itself.** The same 20-delta rule
(a strike 0.84 sigma out over the time remaining), two entries, on the
bars alone:

| | Sold Monday from last week's close | Sold after the line is reached |
|---|---|---|
| Weeks that got the trade | 100% | 50% |
| Strike from last week's close (median, calls) | +5.1% | **+7.7%** |
| Finished through (calls) | 21.4% | 24.5% |
| Touched (calls) | 39.2% | 43.0% |
| Strike from last week's close (median, puts) | −4.8% | **−6.7%** |
| Finished through (puts) | 16.9% | 22.3% |

Waiting for the line does not buy a *safer* 20-delta strike — it finishes
through slightly *more* often, because a stock that has just moved is a
stock that is moving. What it buys is a strike **half again further from
where the week started** at the same delta, in half the weeks. For a covered
call that is the upside kept if assigned; for a put it is the entry price if
assigned. Premium is not on the bars and is not invented here: the credit on
the further strike is what the board reads off the live chain.

**On the delta question** ("maybe 30, 40, 45 delta so I don't leave money
on the table"): the pooled record says a strike at the nominal 20-delta
distance after a crossing finished through about 24% of the time. The board
prices every strike in the 0.08–0.50 delta range against the same crossings
and shows the measured finished-through rate beside each, so the choice is
made on what happened rather than on the delta label. The default gate
refuses anything that finished through more than 30% of comparable
crossings; it lives in `thresholds.json → stretch.select.max_itm` and is a
research choice, not a proven constant.

## 2. The evidence engine (`stretch_evidence.py`)

Two horizons, two sides, one method.

| | Anchor | The line | The trade |
|---|---|---|---|
| **week** | the prior week's last close | median weekly high / low from that close | this week's last listed expiry |
| **day** | the prior session's close | median daily high / low from that close | a same-day expiry (Mon/Wed/Fri names only) |

For every past window and every level on the grid (0.5–3.0 weekly sigma;
1.0–4.0 daily sigma), the **first bar** whose high reached the level (whose
low, for puts) is the entry. From that bar on:

- `beyond` — how much further it travelled past the level, in sigma
- `term` — where the window closed relative to the level, in sigma
  (positive = closed beyond it, against the seller)
- `left` — full sessions remaining after the crossing bar

Sigma is the 20-day daily sigma **known at the window's anchor**, times √5
for the week. Nothing after the crossing bar decides whether it is an event;
nothing before it is credited to the seller. The crossing bar's whole
remaining range is charged in full because daily bars cannot say whether its
high came before or after the level — an upper bound on the risk, stated on
the card.

**Comparable crossings** for a live one: the grid row at or below the live
move (never above — the sample is always of crossings at least as large),
then the exact sessions-left bucket when it holds 20 or more, otherwise
every crossing with at least that many sessions left (those had *more* room
to run; the basis says "upper bound"). Below 20 on the stock's own record
the **pool** answers — every *other* warmed name's crossings, in sigma,
converted into this stock's dollars — and the grade says MEASURED, MOSTLY
POOLED, POOLED or THIN. THIN is refused, not guessed. The pool is kept
under each name's own symbol (at most 80 crossings a name per cell, 300
names a cell), so a recompute replaces rather than appends, and the name
being priced is never counted twice.

The comparable set is handed to `weekly_sell.evaluate_strike` as `{high,
low, term}` fractions relative to the crossing level, so every strike on
the chain gets the same numbers the Weekly Option Selling Setup panel
prints: finished-through, touched, EV on the crossings, fill quality — and
the same gates.

## 3. The scanner (`stretch_scan.py`)

**Stage 1 is cheap.** The watchlist board supplies the universe, the
sectors, the volumes and the earnings dates — but its prices are rebuilt
only twice a day, so every pass reads **live quotes** instead, one call per
hundred names (about thirteen a pass on the full list), falling back to the
board for any name a call cannot answer and saying how many were live. Each
name needs its lines (four percentages and a sigma, computed from bars once and
cached five days on disk) and this week's anchor. The anchor costs nothing
on the first session of the week — the board's own previous close *is* last
week's close — and is remembered on disk for the rest of the week. Only a
name never seen this week costs a bars fetch, at most 40 per pass, biggest
movers first, and every such fetch also teaches the pool.

A name is a **candidate** on a side and horizon when its move from that
anchor is at or past its line; **within reach** at three quarters of the
way; nothing otherwise. Candidates are ranked by the move in sigma and
capped at 16 a pass.

**Stage 2 spends one bounded chain call per candidate** (today through the
week's last session, 40 strikes) and prices every out-of-the-money strike
in the delta range against the comparable crossings. Then:

- **READY** — a strike cleared the finished-through limit and the fill
  gates. The pick is the highest-scoring survivor; three alternates and the
  whole ladder ride along with each strike's gate.
- **CROSSED** — the stock is there and nothing pays. The row says why: no
  expiry in the window (the calendar; kept off the board and counted among
  the refusals), earnings inside the trade, a takeover headline, thin
  evidence, or the gate most strikes failed.

**Alerts** go through the app's own push (ntfy / Pushover), **once per
symbol, side and expiry**, remembered in `stretch_alerts.json` across
restarts, never below the credit floor. The text carries the ticker, side,
expiry, contract, credit, delta, the measured finished-through and touched
rates with their count and grade, and a link that opens Analyze on the
name. Every READY is also appended to `stretch_alerts.jsonl` with the
numbers it was READY on — the prediction log, separate from anything traded.

**The loop runs in the background** from server start, every three minutes
while the market is open, sleeping through a closed one. It does not care
whether the tab is open. `/api/stretch/status` reports the last tick, the
caches, and the alerts sent.

## 4. What was taken from the outside specification, and what was not

An external design brief (an "Adaptive Premium Selling Scanner") was
reviewed before this was built. Its statistical corrections were right and
are in: explicit anchors on every number; entry at the first crossing with
outcomes measured only after it; calls and puts measured separately;
sessions-left conditioning; delta shown as the market's price beside the
measured rate, never multiplied into it; touch kept apart from finish;
extremes as context, not ceilings; READY requiring an actual contract;
alerts deduplicated per symbol/side/expiry and persisted; monitoring
independent of the browser; a prediction log; and the like-for-like baseline
above.

Left out on purpose, because this is a one-person dashboard and not a
research platform: walk-forward training/tuning/test splits and
multiple-testing corrections; regime, sector and relative-strength
features; intraday path modelling and time-of-day claims (the crossing bar
is charged in full instead, and Sold into strength already scales same-day
calls by the session clock); a six-state machine with Invalidated;
latency instrumentation; a formal non-dominated-alternatives ranking. Each
would add code Jerry would have to read without changing what the board
says on a Wednesday morning.

## 5. Refusals and limits, stated plainly

- A takeover or merger is refused outright — through the filing-aware
  catalyst (`_gap_catalyst`: earnings, EDGAR events, offerings, analyst
  actions, then headlines), remembered ten minutes a name.
- A scheduled earnings date between today and the expiry is refused.
- Fewer than 20 comparable crossings, even with the pool, is refused.
- No bid, a spread too wide to fill, or no open interest is refused by the
  same gates the weekly panel uses.
- **The line is not the high.** The card prints the measured closed-back
  rate for every setup; it is near a coin flip.
- **The crossing bar is charged in full**, and a same-day expiry is charged
  the whole day. Both are upper bounds and both are on the card.
- **Pooled evidence is other names' behaviour** in this stock's sigma. It
  is a less specific answer and it is labelled.
- **Nothing here is calibrated against fills yet.** The alert log exists so
  that it can be.

## 6. Endpoints and configuration

`GET /api/stretch` · `/api/stretch/detail?symbol=` ·
`/api/stretch/profile?symbol=` (the Analyze card) · `/api/stretch/status` ·
`/api/stretch/config` · `/api/stretch/alerts?days=`

Every number lives in `thresholds.json` under `stretch`: `line_quantile`
(0.5 — the median), `max_itm` (0.30), `min_delta` / `max_delta`
(0.08 / 0.50), `near_fraction` (0.75), `max_candidates` (16),
`cycle_seconds` (180), `cold_fetches_per_pass` (40), `lines_ttl_days` (5),
`alerts.min_credit` (0.10). A push channel is whichever of `NTFY_TOPIC` or
`PUSHOVER_*` the server already has; `PUBLIC_BASE_URL` overrides the link
in the alert.

## 7. Tests

`test_stretch_evidence.py` (20) · `test_stretch_scan.py` (32) ·
`test_stretch_ui.js` (77 source guards) · HTTP smoke (+6 routes).
Invariants: the event is the first crossing and nothing before it counts;
a window that never reached the level is not an event; puts are measured on
lows and closes-below; sigma is point-in-time; the comparable set widens
only toward more room and says so; thin evidence is pooled and graded or
refused; the anchor is free on the first session and remembered; READY
needs a contract; one alert per key across a restart; nothing below the
credit floor; a closed market starts nothing.
