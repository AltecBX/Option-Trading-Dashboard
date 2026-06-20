(function () {
// tooltips.jsx
// Hover tooltip component + glossary of options/trading terms.
// Usage: <Term k="delta">Δ</Term>  or  <Term k="median_high">Median high</Term>
//
// The tooltip is a controlled <span> that flips a popover on hover/focus.
// CSS lives in styles.css under .term / .tip.

const GLOSSARY = {
  // Price / position basics
  strike: "The price at which an option can be exercised. Calls are exercised by buying shares at the strike, puts by selling shares at the strike.",
  premium: "The price paid (long) or received (short) for one option contract, quoted per share. One contract covers 100 shares.",
  bid: "The highest price a buyer is willing to pay right now. When you sell to open, you typically get filled near the bid.",
  ask: "The lowest price a seller is willing to accept right now. When you buy to open, you typically get filled near the ask.",
  last: "The price of the most recent trade. Can be stale on illiquid contracts.",
  mid: "The midpoint between bid and ask. A reasonable estimate of fair value when the spread is tight.",
  spread: "The gap between bid and ask. Tight spreads mean liquid contracts. Wide spreads mean slippage on entry and exit.",
  // Greeks and IV
  delta: "Approximate probability the option finishes in the money, and also the dollar change in option price per $1 move in the stock. Selling at 0.20 delta means roughly an 80% probability the option expires worthless.",
  iv: "Implied volatility. The market's forward-looking estimate of how much the stock will move, annualized. Higher IV means richer premium.",
  iv_rank: "Where current IV sits within its 52-week range. High IV rank favors selling premium, low IV rank favors buying it.",
  oi: "Open interest. The total number of contracts currently held open at this strike. Higher OI generally means better liquidity.",
  volume: "Number of contracts traded today. A spike in volume vs OI can signal directional interest.",
  // Returns and history
  median_high: "The middle value of weekly highs over the lookback. Half of weeks topped out above this, half below.",
  median_low: "The middle value of weekly lows over the lookback. Half of weeks bottomed below this, half above.",
  median_close: "The middle value of weekly close-vs-baseline returns. Where Friday usually lands.",
  baseline: "The reference price used to measure weekly return. Monday open captures the full week. Previous Friday close captures the gap plus the week.",
  monday_open: "The first traded price on Monday morning. Treats the weekend gap as part of last week.",
  prev_friday_close: "The official closing print on Friday. Treats the weekend gap as part of this week.",
  typical_high_day: "The day of the week where the high has landed most often over the lookback.",
  typical_low_day: "The day of the week where the low has landed most often over the lookback.",
  // Setup and structure
  buffer: "Extra cushion added to the suggested strike beyond the historical median. Larger buffer means less premium, lower assignment risk.",
  expected_move: "The market's pricing of a one-standard-deviation move by expiration, derived from the at-the-money straddle. Roughly captures 68% of outcomes if implied volatility is right.",
  expected_range: "Baseline price adjusted by the median historical high and low. The shaded band on the price chart.",
  atm_straddle: "Buying or selling the at-the-money call and put together. Its mid price is a clean read on the market's expected move in dollars.",
  historical_range: "Sum of the absolute median high and absolute median low. How much the stock has actually swung in a typical week.",
  // Strategy outcomes
  break_even: "The stock price at expiration where total profit equals zero. Below this for a sold put, above this for a sold call.",
  max_profit: "The most you can make on the position at expiration. For credit strategies this is the premium received, capped.",
  max_loss: "The most you can lose at expiration. For defined-risk credit spreads this is the spread width minus the credit received.",
  profit_zone: "The range of stock prices where the position is profitable at expiration.",
  net_credit: "The premium collected after netting all option legs. Cash that lands in your account at entry.",
  net_debit: "The cost of opening the position after netting all option legs. Cash that leaves your account at entry.",
  spread_width: "Distance in dollars between the long and short strikes of a vertical spread. Defines max loss.",
  // Probabilities
  prob_profit: "Probability of finishing profitable at expiration. Computed from historical weekly highs and lows over the lookback, not from implied volatility.",
  prob_call_safe: "Percentage of past weeks where the high stayed below the suggested call strike. Higher means the call has a stronger record of expiring worthless.",
  prob_put_safe: "Percentage of past weeks where the low stayed above the suggested put strike. Higher means the put has a stronger record of expiring worthless.",
  prob_both: "Percentage of past weeks where both sides expired worthless. The strangle win rate.",
  // Risk management
  assignment: "Being forced to fulfill the option contract. Short calls assigned mean shares called away at the strike. Short puts assigned mean shares put to you at the strike.",
  early_exercise: "Long American-style options being exercised before expiration. Most common on deep ITM short calls before ex-dividend dates.",
  rolling: "Closing the current option and opening a new one further out in time, often at a different strike, for additional credit or to defend.",
  pin_risk: "Uncertainty about whether a short option closing right at the strike will be assigned. Manage by closing the position before the bell on expiration day.",
  // Strategy names
  covered_call: "Sell a call against shares you already own. Caps upside at the strike, collects premium each cycle. Income strategy for stock you'd be willing to part with at the strike.",
  cash_secured_put: "Sell a put backed by enough cash to buy the shares if assigned. Generates premium and gets you long stock at a discount if the put goes in the money.",
  short_strangle: "Sell an out-of-the-money call and an out-of-the-money put simultaneously. Profits if the stock stays between the strikes. Undefined risk on both sides.",
  short_straddle: "Sell the at-the-money call and put simultaneously. Maximum premium, narrowest profit zone. Bet on a quiet week.",
  iron_condor: "Short strangle with protective wings on both sides. Defined-risk version of the strangle, smaller credit, lower margin.",
  bull_put_spread: "Sell a put and buy a further-OTM put. Bullish-neutral. Defined-risk credit spread.",
  bear_call_spread: "Sell a call and buy a further-OTM call. Bearish-neutral. Defined-risk credit spread.",
  calendar_spread: "Sell a near-dated option and buy a longer-dated option at the same strike. Profits from time decay on the short leg outpacing decay on the long leg. Best when the stock pins the strike at front expiration.",
  diagonal_spread: "Calendar spread variant where the long leg uses a different strike than the short leg. Adds a directional bias to the time decay play.",
  jade_lizard: "Sell a put and sell a call spread. Structured so total credit exceeds the call spread width, eliminating upside risk. Profits if the stock stays above the put or finishes inside the call spread.",
  ratio_spread: "Buy one option and sell two at a further strike (1x2). Net credit or small debit. Profits in a moderate move toward the short strike. Risk grows past the short strikes.",
  wheel: "Repeating cycle: sell cash-secured puts until assigned, then sell covered calls on the assigned shares until called away, then back to puts. Premium-generating system on a stock you're comfortable owning.",
  // Swing pattern recognition / live decision
  swing_low: "A local price bottom confirmed by the zig-zag: price fell into it, then reversed up by more than the swing threshold. The launch point of an up-move.",
  swing_high: "A local price top confirmed by the zig-zag: price rallied into it, then reversed down by more than the swing threshold. The launch point of a down-move.",
  swing_threshold: "How big a reversal (percent) counts as a new swing. Larger = only major swings; smaller = catches more, noisier swings.",
  swing_rhythm: "This stock's historical habit: across past swings, how far it typically runs (percent) and how long it takes (trading days). The basis for every projection here.",
  current_move: "The move in progress right now, measured from the most recent swing pivot to the current price. The heart of the live read.",
  maturity: "Where the live move sits in this stock's history of moves. Early = below the 25th percentile, developing = below median, mature = below the 75th percentile, extended = below the largest ever, exhausted = beyond it.",
  target_ladder: "Projected price targets built from the from-price and this stock's historical move sizes: conservative (25th pctile), median (typical), aggressive (75th pctile), and extreme (largest prior move).",
  exhaustion_score: "0–100 read on how stretched the move is: size vs history, duration vs history, RSI extreme, distance from the 20-day average, wick rejection, and fading volume. High = late, manage risk.",
  continuation_score: "0–100 read on whether the move should keep going: close near the day's extreme, position vs the moving averages, RSI in a trending band, room left before the usual exhaustion zone, and participation (volume).",
  trend_state: "A plain-English label for the move: Accumulating, Breaking out, Continuation, Extended, Exhaustion / reversal risk, Breaking down, etc.",
  do_not_sell_yet: "Fires on a long when momentum still favors continuation, exhaustion is low, and the typical move hasn't reached its usual size yet — a nudge not to sell too early.",
  cover_too_early: "Fires on a short when the down-move still favors continuation and hasn't reached its usual depth yet — a nudge not to cover too early.",
  similar_move: "The closest past swing to this one by size and duration, with whether the current move is running hotter or cooler than that comparison's pace.",
  confidence_rating: "How many past moves actually reached a given target. High = matched 5+ similar moves, medium = 2–4, low = thin or none.",
  rsi14: "Relative Strength Index (14). Above 70 is overbought, below 30 oversold. Used here as one input to exhaustion.",
  rel_vol: "Today's volume vs the 20-day average. Above 1x means heavier-than-normal participation behind the move.",
  relative_strength: "How the current move stacks up against SPY and QQQ over the same window. 'Leading' = stronger than the market in the move's own direction (up-moves rising faster, down-moves falling faster) — confirmation the move has real relative power, not just a market tide.",
  broke_resistance: "The move pushed through the prior swing extreme (resistance for up-moves, support for down-moves) — a structural breakout / breakdown rather than a move inside the old range.",
  failed_breakout: "Price broke the prior level but then closed back through it within ~10 bars — the breakout didn't hold. A common reversal tell.",
  after_earnings: "The swing launched within ~5 trading days of an earnings report — i.e. the move was likely catalyst-driven.",
  vol_ratio: "Average volume during the swing vs the ~50 bars before it. Above ~1.2x means the move ran on heavier-than-usual participation.",
  swing_filters: "Narrow the history table to the kinds of moves you care about — size, duration, volume, catalyst, and structure — so you can study only the setups that match what's happening now.",
  swing_flow: "Today's unusual options flow from Unusual Whales, distilled to a bullish-vs-bearish read with a quality score (trade size + ask-side conviction). When the tape confirms the move's direction it adds to Continuation; when it fades the move it adds to Exhaustion. Quality gates how much it's allowed to count.",
  key_levels: "Horizontal support (prior swing lows) and resistance (prior swing highs) from this stock's own history. Unlike the statistical target ladder, these are the price levels traders actually defend — the move will negotiate the nearest one FIRST. A hold can spark a bounce (double-bottom / double-top); a decisive break on volume opens the next target. The median projection may sit beyond a level, meaning it requires a breakdown/breakout to reach.",
  trade_entry_zone: "Where to get in: from the swing pivot up to ~3% above it. Ideally enter on a higher-low or a break above the prior day's high with volume.",
  trade_invalidation: "The line in the sand. A close back through this level (under the swing low for a long, over the swing high for a short) means the setup failed — exit.",
  trade_t1: "First target: the typical (median) move this stock makes from a swing, projected off the entry. The realistic base-case exit.",
  trade_t2: "Second target: the 75th-percentile move — only the stronger historical runs reach here. A stretch, not a base case.",
  trade_extreme: "The single largest prior move in the lookback projected off the swing. An outlier ceiling, rarely repeated — low confidence by design. Don't plan around it.",
  trade_holding: "How long this stock's moves of this kind usually take (25th–75th percentile of past durations), with the date the window typically closes."
};
function Term({
  k,
  children,
  className = ""
}) {
  const {
    useState,
    useRef,
    useEffect
  } = React;
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const ref = useRef(null);
  const text = GLOSSARY[k];
  if (!text) return /*#__PURE__*/React.createElement("span", {
    className: className
  }, children);
  const show = () => {
    if (!ref.current) return;
    const r = ref.current.getBoundingClientRect();
    setPos({
      x: r.left + r.width / 2,
      y: r.bottom
    });
    setOpen(true);
  };
  const hide = () => setOpen(false);

  // Close on scroll/resize so the bubble doesn't strand off the trigger
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);
  return /*#__PURE__*/React.createElement("span", {
    ref: ref,
    className: "term " + className,
    tabIndex: 0,
    onMouseEnter: show,
    onMouseLeave: hide,
    onFocus: show,
    onBlur: hide
  }, children, open && pos && ReactDOM.createPortal(/*#__PURE__*/React.createElement("div", {
    className: "tip",
    style: {
      left: pos.x,
      top: pos.y
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "tip-arrow"
  }), /*#__PURE__*/React.createElement("div", {
    className: "tip-body"
  }, text)), document.body));
}
window.Term = Term;
window.GLOSSARY = GLOSSARY;
})();
