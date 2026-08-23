# Best Setup — one recommendation, and what it refuses to do

The Best Setup card sits at the top of the Trade tab. It answers one question:
**what is the single strongest premium sale on this symbol right now, and
why** — using the layers the app already computes, rather than a new model.

It names a side, an expiration, a strike, a delta and a credit; it explains
the reasoning; it lists the specific risks; and every number on it can be
traced back to the tab that produced it.

---

## The one thing this feature exists to get right

**Delta is already a probability.** A 15-delta short call is the market
saying "about a 15% chance this finishes in the money". Selling a 40-delta
call instead does not keep that win rate and add premium — it lowers the win
rate to roughly 60% and pays you for the difference. That is arithmetic, and
no amount of pattern, streak or gamma evidence changes it.

So there is exactly **one honest reason** to sell closer to the money: the
market's probability is wrong for this symbol in this state. Delta is a
*risk-neutral* probability, priced off implied volatility. What actually
happens is a *real-world* probability. When a stock's implied volatility
persistently exceeds what it goes on to realize, the two differ, and that gap
is real money.

The engine therefore never reasons "the move looks extended, so sell a higher
delta". It asks a measurable question instead:

> In this state, how often did this stock actually travel far enough to reach
> that strike inside the life of the option?

### The circularity that had to be designed out

The first version of this engine mapped a measured keep rate to an "implied
delta" of `(1 − keep)`. That returns `(1 − target)` no matter what the data
says. It looks like analysis and computes nothing.

The fix is to solve for a **distance** instead. The measurement says "price
stayed inside 8.1% of spot 85% of the time, over 181 windows". The market
then independently quotes whatever delta it likes at 8.1% out. The gap
between that quoted delta and the measured risk is the edge — and it is only
an edge because the two numbers come from different places.

`test_the_delta_is_not_derived_from_the_target_it_is_solved_for` guards this.

---

## The rules, in order

1. **The default band is 0.15–0.22** and it is where the engine returns
   whenever evidence is missing. It is the rule that already works.
2. **Widening requires a real measured sample** — at least 30 windows, and
   the conditional rate must beat the unconditional baseline by at least 5
   percentage points. Below that, the engine says so in the risks and keeps
   the default.
3. **Sizing is on the Wilson lower bound, never the point estimate.** Nine
   wins from ten tries is a 90% point estimate and a 59% lower bound; the
   second number is the one that should decide how much money is at risk.
4. **A second opinion must agree.** A driftless lognormal at ExpectedRV is
   asked about the same distance the measurement solved for. If the model
   says that distance is riskier than the measurement does, the band stays at
   the default.
5. **Evidence may only ADD candidates, never remove them.** A contract the
   default rule would have accepted stays eligible whatever the measurement
   found. Without that rule, gathering 181 windows of history could turn a
   perfectly good 18-delta trade into no trade at all — a rule that punishes
   evidence is not a rule about risk.
6. **The hard cap is 0.45**, on any evidence. Past that a short option is a
   directional bet wearing a premium-selling costume.
7. **A negative expected value is refused, not ranked.** If the best contract
   the evidence allows pays less than it is worth at the volatility the stock
   actually realizes, there is no recommendation — only a note naming the
   contract that came closest and by how much it fell short. A high
   probability of keeping the credit is not the same thing as a profitable
   trade.

### What counts as "this state"

The conditioning rule is **fixed in advance**, in this priority:

1. a run of 3 or more up or down days, else
2. a swing that has gone beyond its normal size, else
3. nothing — and "nothing" is the common and correct answer.

Choosing the rule after seeing which answer it produces is picking the winner
and calling it evidence, so the order is pinned by test.

---

## What gamma exposure is allowed to do

**Never a standalone signal.** Dealer positioning is not published. Every GEX
figure in this app is open interest times gamma times an assumption about who
is short which side — a model, not a measurement, and the Gamma Exposure tab
says so on screen.

Here it is used only as a *structure modifier* on a trade the other layers
already justify:

| Verdict | Meaning | Effect |
|---|---|---|
| `supports` | a positive-gamma wall sits between spot and the strike | **nothing** — it never widens the band |
| `neutral` | no meaningful concentration in the way | nothing |
| `opposes` | the path to the strike runs through negative gamma | pulls the whole delta window further out of the money, and costs confidence |
| `veto` | spot is below the flip **and** the strike sits in nearby negative gamma | refuses the trade outright |

Two details that matter:

- **A pullback moves both edges of the band, not just the ceiling.** Scaling
  only the top of 0.15–0.22 leaves 0.15–0.176 — a window 2.6 delta points
  wide, which a real chain steps straight over. The card would then report
  "no contract" instead of recommending a safer one. An opposing reading
  means *sell further out*, not *sell nothing*.
- **The veto needs BOTH conditions, not either.** Spot below the flip alone
  is not a refusal, and neither is nearby negative gamma alone.

When gamma positioning conflicts with the patterns, streaks or premium data,
confidence falls and the disagreement is listed in the risks by name.

---

## Reading the card

- **The band notice** always says whether the strike is the conservative
  default or a measured widening. That distinction is the feature, so it is
  never implied — a widening states its distance floor, its sample size and
  the conservative lower bound it rests on.
- **The credit is the BID**, never the mid. The bid is the only price a
  resting sell order is actually promised.
- **"Keeps it"** is the model probability at ExpectedRV, not at implied
  volatility, and not one minus delta.
- **The evidence table** shows every distance measured: the rate in this
  state, the rate from any bar, the difference, the keep rate, the Wilson
  lower bound and the window count. Rows that clear the distance floor are
  marked.
- **"What could go wrong"** carries the same weight as the reasoning. An
  empty risk list would itself be a warning sign.
- **Both sides are shown.** When the other side was refused outright rather
  than merely outscored, it says which.

---

## Caching

`/api/setup?symbol=X` caches for 90 seconds. The chain and the quote move
faster than the reasoning does, but not by much. `?force=1` bypasses it, and
the Refresh button on the card uses that.

---

## Tests

- `test_setup_engine.py` — the decision layer, weighted toward the ways it
  could be **wrong**: widening without evidence, widening on a sample that
  does not beat baseline, widening when the model disagrees, sizing on the
  point estimate, gamma opening a trade, evidence removing a candidate, a
  negative-EV sale being ranked instead of refused.
- `test_setup_scan.py` — the gathering layer: forward windows (an incomplete
  window is not a miss), the touch curve, the fixed conditioning rule, and
  the vocabulary contracts with the producers it reads from.
- `test_setup_ui.js` — source-level guards on the card, weighted toward
  disclosure rather than appearance.
