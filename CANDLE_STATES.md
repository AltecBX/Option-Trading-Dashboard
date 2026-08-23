# Candle States, Sectors, Market Context and Gamma Exposure

Three dashboards added in v4.56, plus the two engines and the live layer
underneath them.

| Tab | What it answers |
|---|---|
| **Sectors** | Which of the eleven sectors are moving, on which timeframe, and which names inside them are doing it |
| **Market Context** | What the whole watchlist's candles are doing right now, how that has shifted through today, and where the four index funds sit |
| **Gamma Exposure** | Where dealer hedging is concentrated by strike, and a market map of the watchlist by size and today's move |

---

## 1. What a candle state is

Every bar is classified against the bar before it, **on the same timeframe**,
using nothing but the two highs and the two lows.

| State | Name | Meaning |
|---|---|---|
| `1` | Inside | The whole range sits inside the prior bar's. Neither extreme was taken out. |
| `2U` | Directional up | The prior **high** was exceeded and the prior **low** held. |
| `2D` | Directional down | The prior **low** was broken and the prior **high** held. |
| `3` | Outside | **Both** the prior high and the prior low were taken out in the one bar. |

That is the whole vocabulary. It is deliberately a *description of what
happened*, not a prediction. Nothing in this codebase says a 2U is bullish,
because whether it is depends on the timeframe above it, and the engine
cannot see context it was not given.

### The equality rule

**Taking out an extreme means exceeding it.** A bar whose high exactly equals
the prior high has not taken it out, so it counts as held. Every comparison in
`strat_states.state_of` is strict (`>` and `<`), and a bar that exactly matches
the prior bar on both ends is an **inside** bar.

The alternative convention — touching counts — puts a `3` on every flat,
thin-volume day. This is why the rule is written down and covered by
`TestTheEqualityConvention` rather than left to whichever comparison got typed
first.

### Calendar periods, not rolling windows

Weekly, monthly, quarterly and yearly candles are built by grouping **daily
bars into calendar buckets** and taking each bucket's high and low.

| Timeframe | Bucket key | Note |
|---|---|---|
| `D` | `2026-08-21` | The session |
| `W` | `2026-W34` | **ISO** week, Monday-start |
| `M` | `2026-08` | |
| `Q` | `2026-Q3` | |
| `Y` | `2026` | |

Weeks are ISO weeks because that is the only week numbering unambiguous across
a year boundary: the week containing January 1st can belong to the previous
ISO year, and a home-rolled `(year, week-of-year)` pair splits that week in two
and invents a one-day weekly bar every January.

A rolling five-day high is a *different measurement* that happens to share
units. Using one would make the weekly state change every single day, which is
not what a weekly candle does.

### The current period is always in progress

On any timeframe above daily the newest bucket is unfinished — a monthly bar on
the 3rd of the month is three days old. Its state is real and it is what a
trader reads, but it can still change. Every payload carries `complete: false`
on it so nothing downstream can treat a three-day-old monthly candle as
settled, and `cur_days` says how far into the period it is.

---

## 2. Where the numbers come from

Nothing here downloads a bar.

```
watchlist_table._scan_worker
  └─ already downloads 5 years of daily OHLC per symbol, twice a day
     └─ _scan_one → strat_states.read(dates, highs, lows)
        └─ stores ~20 numbers per symbol: the current and prior
           high/low on each of D/W/M/Q/Y

market_state._reads()
  ├─ watchlist_table.get_board(with_strat=True)   ← the stored extremes
  ├─ SchwabClient.get_quotes(...)                 ← one batched quote pass
  └─ strat_states.live_read(...)                  ← merge, per symbol
```

**Stored extremes, not stored states.** A stored *state* is wrong the moment
price makes a new high. Stored *extremes* are re-read against a live quote and
are never stale. That is the whole reason the scan writes highs and lows.

The stored extremes are kept **off the wire by default**:
`watchlist_table.get_board()` strips them unless `with_strat=True`. They are
about a megabyte of JSON across 1,285 symbols that only `market_state` can use,
and the watchlist table the browser downloads would otherwise carry it for
nothing.

### The live merge

Two things can have changed since the scan:

1. **A new period began.** Detected by comparing the current session's period
   key to the stored one. The stored candle becomes the reference and the live
   session opens a fresh one.
2. **The same period extended.** Merged by `max`/`min`, which makes the merge
   **idempotent**: re-reading with a live high already inside the stored range
   changes nothing.

A rollover only happens when there is a live quote to open the new candle with.
Rolling without one would blank the state, and there are ordinary reasons for
no quote to arrive — a weekend, pre-market, a scan a day behind, a disconnected
broker. In all of those the last settled candle is the right thing to show,
labelled with its own date (`stale: true`, `as_of: "2026-08-21"`, and a
"scan is behind" note in the status line) rather than a dashboard of dashes.

### Regular session only, and only once it has started

Extended-hours prints are **excluded** from the live merge, and before 9:30 ET
the states shown are the settled ones from the last close. Two reasons:

- Schwab's quote carries the **regular** session high and low, which are stale
  or zero pre-market. Merging them at 7 AM would compare today's states against
  yesterday's range.
- A single thin pre-market print through yesterday's high would flip a symbol
  to `2U` on a hundred shares — a state nobody trading the candle would agree
  had happened.

Every payload carries `live: true|false` per symbol, and the status line on all
three tabs says which one is on screen.

### Which session the states belong to

`market_state.session_date()` returns **today once the regular session has
begun, and otherwise the most recent completed trading day**. The calendar date
is the wrong answer twice a week: bucketing by it on a Sunday opens a daily
candle in a Sunday bucket, finds it empty, and blanks every state all weekend.
Holidays come from `capture_health.is_trading_day`, which computes them
(including Good Friday via the Easter algorithm) rather than tabulating them.

---

## 3. Sector membership

Membership comes from the `sector` column the watchlist board already carries,
mapped onto the eleven SPDR sector ETFs by `market_state.sector_etf`.

**It is a classification, not an index membership list.** This is "the
technology names on your watchlist", not "the holdings of XLK". Those two are
close enough to be confused and far enough apart to matter, so the payload says
so in `membership_note` and the UI repeats it.

Names whose sector does not map to one of the eleven are **not** put in a
twelfth bucket called "Other" — that would make them look like a sector. They
are counted, reported (`unclassified`), left out of the sector cards, and still
counted in whole-market breadth.

### Leaders and laggards

Ranked by **directional share**: `2U ÷ (2U + 2D)` on the daily candle.

Inside bars are excluded because an inside bar has no direction. Outside bars
are excluded because a `3` took out *both* extremes, and calling it up or down
needs a close — a different measurement than this engine makes. A sector with
no directional names at all returns `None` and is **unranked**, not ranked
last.

The leader and laggard lists are made disjoint. With few ranked sectors, a
naive top-3 / bottom-3 labels the same sector both a leader and a laggard on
the same screen.

---

## 4. Gamma exposure

### The assumptions, stated before the first number

Gamma exposure is **not a measured quantity**. Nobody publishes dealer
inventory, so every GEX figure anywhere — this one included — is open interest
times gamma times an *assumption* about who is on which side.

1. **Sign convention — calls positive, puts negative.** This assumes dealers
   are net **long call gamma** and net **short put gamma**, the standard
   convention behind the commonly quoted "zero gamma" and "gamma flip" levels,
   and the one this dashboard's existing SPY gamma-regime read already used.

   What follows: **positive** net GEX means dealers hedge *against* the move —
   pinning, mean reversion, suppressed realised volatility. **Negative** means
   they hedge *with* it — trending, amplified moves.

   This assumption is wrong for individual names often enough to matter. A
   stock where the public is buying calls outright inverts it. Read the sign as
   "under the standard convention", never as a measurement.

2. **Contract multiplier — 100.** Standard US equity options, passed in as
   `contract_size` so an index or mini contract can override it rather than
   silently being wrong.

3. **Scaling — dollars of dealer delta per 1% move:**

   ```
   GEX = gamma × open_interest × contract_size × spot² × 0.01
   ```

   Gamma is delta per $1 of spot, so `gamma × spot² × 0.01` converts it to
   dollars of delta per 1% of spot. Quoting per 1% rather than per $1 is what
   makes a $6 stock and a $600 stock comparable.

4. **Open interest, not volume.** Open interest is yesterday's settled position
   count. It is stale by up to a session and it is still the right input:
   today's volume includes both openings and closings and cannot be netted into
   a position.

The `convention` string travels **with the payload** and is rendered on screen,
so a screenshot of that tab cannot lose it.

### The gamma flip

The cheap way to estimate the flip is to cumulatively sum the per-strike GEX in
strike order and report where the running total crosses zero. **That is not the
same quantity**, because it holds every contract's gamma fixed at the value it
has at today's spot — and gamma is precisely the thing that moves as spot moves.

`gex_engine.profile` does it properly: it re-computes each contract's
Black-Scholes gamma (via `metrics._bs_gamma`, already in the repo) at every
candidate spot across ±20% in 81 steps, using that contract's **own** implied
volatility and time to expiry, and interpolates the lowest upward crossing.

Contracts missing an IV or a usable expiration cannot be re-priced, so they are
excluded from the **profile** — they still count in the static per-strike
totals — and `covered_oi_pct` reports what share of open interest the estimate
actually covered. A flip level built from 40% of the open interest is not a
flip level, and the UI says so below 90%.

If net exposure never changes sign inside the grid, the answer is
**"None in range"** with a reason, not a number extrapolated off the end.

`TestTheGammaFlip.test_gamma_is_recomputed_at_each_spot_not_held_fixed` is the
guard: with gamma held fixed the profile would be a weighted sum scaled by
spot², strictly increasing in magnitude and never peaking. Re-computed gamma
decays as spot moves away from the strikes, so the profile must fall somewhere
on the way out.

### Data source and development fixtures

Schwab is the only provider wired here that carries per-contract gamma **and**
open interest, so `options_dashboard._gex_chain()` is the whole provider
interface: one function, one shape, one honest source label.

Development fixtures are **off** unless `GEX_DEV_FIXTURES=1` is set in the
environment. This dashboard is used to place real trades, and a synthetic chain
that appears whenever the broker token happens to be expired is a trap no badge
fully defuses. With the flag off an unavailable chain says so; with it on every
payload is stamped `source: "fixture"` and the view renders a full-width
warning banner instead of a price.

---

## 5. The market map

A **squarified** treemap (the Bruls/Huizing/van Wijk pass), sector-grouped,
rectangle area by market capitalisation and colour by today's percentage
change, diverging around zero and saturating at ±3%.

Squarified rather than naive because laying rectangles out in plain order
produces slivers whose relative size nobody can read, which defeats the only
thing a treemap is for.

The forty largest names per sector are drawn. A treemap with 1,285 rectangles
is a texture, not a chart — the tail renders sub-pixel and costs more to lay
out than to look at. What was dropped is reported per sector and totalled below
the map rather than trimmed silently.

Every rectangle carries a tooltip (symbol, company, price, change, market
value, daily and weekly state) and is clickable and keyboard-reachable through
to the Trade tab.

---

## 6. Files

| File | What it is |
|---|---|
| `strat_states.py` | **Pure.** State classification, calendar bucketing, the live merge, breadth tallies. No I/O, no clock. |
| `gex_engine.py` | **Pure.** Per-strike exposure, summary, the Black-Scholes gamma-flip profile. No I/O, no clock. |
| `market_state.py` | **Stateful.** Board + quotes, sector taxonomy, indices matrix, intraday breadth store, caching. |
| `tab-strat.jsx` | One lazy chunk exporting `SectorsTab`, `MarketContextTab`, `GexTab`. |
| `fixtures/gex_dev_chain.json` | Synthetic chain, development only, gated behind `GEX_DEV_FIXTURES`. |
| `test_strat_states.py` | 41 tests |
| `test_gex_engine.py` | 34 tests |
| `test_market_state.py` | 46 tests |
| `test_strat_ui.js` | 111 source-level UI guards |

Changed: `watchlist_table.py` (computes and stores the extremes; `get_board`
gains `with_strat`), `schwab_client.py` (adds `get_candles`),
`options_dashboard.py` (routes, wiring, `_threshold_section`), `app.jsx`,
`app-lib.jsx`, `styles.css`, `build_frontend.js`, `verify_frontend.js`,
`thresholds.json`.

## 7. Endpoints

| Route | Returns |
|---|---|
| `GET /api/strat/sectors` | Eleven sector cards, breadth per timeframe, leaders/laggards |
| `GET /api/strat/sector?name=XLK` | One sector's constituents and their state per timeframe |
| `GET /api/strat/context` | Whole-market breadth, the intraday series, the snapshot |
| `GET /api/strat/indices` | SPY/QQQ/IWM/DIA × 60m/4H/D/W/M/Q/Y |
| `GET /api/strat/status` | Session phase and which session the states belong to |
| `GET /api/market_map?limit=40` | Sector-grouped treemap rows |
| `GET /api/gex?symbol=SPY&expiration=…` | Per-strike exposure, summary, flip profile. `expiration` takes one date, a comma-separated list, or `all`; omit for the nearest. |

All of them are served from **one** cached read of the board plus **one**
batched quote pass, so a dashboard polling four panels costs one quote batch,
not four.

## 8. Configuration

`thresholds.json` sections `market_state` and `gex`, overridable key by key
from `<data_dir>/thresholds.json`. Every value in `market_state` is a cache
lifetime, a sampling cadence or a display bound — **none of them can change a
state, a breadth count or which side of a comparison a bar lands on.** Those
come from two highs and two lows and have nothing to tune.

`gex.sign_convention` is documented but is deliberately **not** a tuning knob:
inverting it inverts every number and every regime label on the screen.

## 9. Environment

| Variable | Effect |
|---|---|
| `GEX_DEV_FIXTURES=1` | Serve the synthetic chain when the broker has none. Off by default. |

No new credentials, no new external service. The Sectors and Market Context
tabs work with no broker connection at all — they fall back to the settled
states from the last watchlist scan and say so. Gamma Exposure needs the
Schwab connection, because nothing else wired here carries per-contract gamma.
