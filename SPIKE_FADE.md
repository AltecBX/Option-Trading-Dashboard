# Sold into strength — today's runs, priced (v4.82)

A stock has run hard today. Its same-day calls are briefly rich. Is a strike
above the level it has already reached worth selling?

`spike_evidence.py` · `spike_scan.py` · `tab-spike.jsx` · `GET /api/spike*` ·
top of the Trade tab · `fixtures/spike_universe.json`

---

## 1. What was measured before anything was built

830,059 sessions across 371 names, 2015–2026, from daily prints. Two facts
came out of it, and the trade lives between them.

**A stock that runs hard almost never finishes at its high.**

| Reached | Finishes within a whisker of its high | Typical giveback |
|---|---|---|
| 1.0 sigma | 13.1% | 0.47 sigma |
| 2.5 sigma | 10.6% | 0.62 sigma |
| 4.0 sigma | 6.8% | 1.01 sigma |
| 5.0 sigma | 5.1% | 1.25 sigma |

The bigger the run, the less often it holds. Gap-ups are cleaner still: after
a two-sigma gap up, 5.0% finish at the high and **54% close below the open**.

**But the level on the screen is often not the high yet.** After a four-sigma
run, a strike a quarter-sigma above is *touched* 83% of the time and *closed
above* 44.6%.

So the seller is not paid for the stock falling back — most of them do. The
seller is paid for **the high already being in**, which is a function of how
much session is left.

## 2. Sigma, not percent

A 3.6% day on a name running at 81% annualised volatility is an ordinary
session (0.69 sigma). A 16.4% day on a 51%-vol name is a 4.7-sigma event.
Ranked on percentage distance those two come out backwards, and the strike
that *looks* further away is the closer one in the only units that matter.

The normalisation was tested before it was trusted. Across volatility
quartiles (under 21% to over 49% annualised) and price bands (under $20 to
over $100), the probability of closing a sigma beyond a three-sigma move sits
between **20.6% and 22.1%**. One table serves every name, so a ticker with a
handful of its own runs can safely borrow the universe's — and the payload
says what share it borrowed.

## 3. What decides the trade

**Credit minus measured settlement.** Not a probability, which cannot price a
sale; not a score, which cannot be checked against a fill.

The settlement is what the call has historically been worth at the bell after
a comparable run, in the stock's own sigma, converted to dollars. No
volatility model appears in the verdict. The credit is the **bid** — the only
price a resting sell order is promised.

## 4. The session is the trade

The same strike is a different trade at different times of day. Your IOT
example, at the real $1.15 bid on the 45.50 call:

| Sold at | Settles for | Edge per contract |
|---|---|---|
| 10:00 | $1.27 | −$12 |
| 12:00 | $1.03 | +$12 |
| 14:00 | $0.73 | **+$42** |
| 15:15 | $0.45 | **+$70** |

The measured tables credit the stock with the *whole* remaining session, so
they are an upper bound on the risk. `remaining_session` scales that down:
**MEASURED** when an intraday variance profile is available (risk clusters at
the open and the close, not evenly), **MODELED** when it falls back to the
clock. That fallback is the largest approximation in the feature and the card
says so on screen.

## 5. The funnel

| Stage | Cost | What it does |
|---|---|---|
| 1 | free | Ranks the board by today's move **in the stock's own sigma** |
| 2 | one bounded chain call per name | Same-day expiries only, every call above the current price |
| 3 | free | Prices each strike against the measured record and ranks by edge |

The worker starts on the first read and dies when the market shuts or nobody
is looking, so an eager board costs nothing idle. It refreshes every two
minutes while open — same-day premium decays by the minute.

## 5a. What stage 1 is allowed to spend

The watchlist is 1,289 names and several hundred of them are green on an
ordinary day. Judging a move in sigma needs the stock's volatility, and the
first version fetched daily bars for every green name to get it — hundreds
of broker calls against a shared 110-a-minute budget, every morning, before
knowing whether any of them was a candidate. It would have worked and
starved the rest of the app doing it.

Volatility moves slowly, so it is cached per symbol for four days and
persisted to disk across restarts. A pass answers every name it already
knows for free, then spends bars on at most `cold_fetches_per_pass` (40) of
the ones it does not — biggest movers first, so the cache warms on the names
most likely to matter. The payload reports how many are still warming.

## 6. Refusals

- **Takeover and merger spikes are never listed.** That is the one move that
  does not come back.
- No real bid, spread past the limit, or thin open interest.
- An underlying too thin to manage the position in.
- A credit that does not clear the measured settlement by the configured
  floor — which is most strikes, most days.

Every refusal keeps its reason and is shown on the card.

## 7. Limitations, stated plainly

- **Time of day is estimated, not known**, unless the intraday profile is
  available. It is the single biggest factor in the verdict.
- **The measured record is drawn from names that still exist**, which omits
  the spikes that were acquired and never came back. That is precisely the
  tail that hurts a call seller, and it is why takeover headlines are refused
  outright rather than priced.
- **The pooled tail is not transferable to a specific name** — a 60-sigma
  outcome somewhere in the universe is not a dollar figure for your stock, so
  the card does not quote one.
- Daily bars cannot see whether the stock re-tested its high before fading;
  only where it closed, which is what the option settles on.
- Nothing here is calibrated against your actual fills yet. The forward-test
  grader that does that for the Best Sales board is the natural next step.

## 8. Endpoints

`GET /api/spike?top=` · `/api/spike/detail?symbol=` · `/api/spike/status` ·
`/api/spike/config`. Every floor lives in `thresholds.json` under `spike`.

## 9. Tests

`test_spike_evidence.py` (36) · `test_spike_scan.py` (24) ·
`test_spike_ui.js` (67 source guards) · HTTP smoke (+5 routes). Invariants:
sigma is point-in-time, a further strike is never more likely nor more
expensive, touch is never rarer than close, pooled names are graded pooled,
the clock only ever reduces the settlement, and off the measured grid it
clamps rather than extrapolating.
