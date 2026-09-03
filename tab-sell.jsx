// tab-sell.jsx — LAZY CHUNK (v4.80). BEST SALES TODAY.
//
// The market-wide answer to the one question a premium seller has: which
// option has the strongest evidence of eventually expiring worthless while
// still paying enough to justify the risk — in the selling mode the reader
// chose, across every name the app scanned, with the defence of #1, the
// risk pathway, and the list of everything that was refused and why.
//
// NO TRADE is a valid answer and renders as one. Every number on this card
// carries a tooltip saying what it is and where it came from; every date is
// spelled out; every probability says whether it is the model, this stock's
// measured history, or the conservative bound.
//
// Endpoints: GET /api/sell?mode=&strategy=&top=
//            GET /api/sell/detail?symbol=&mode=
//            GET /api/sell/calibration      GET /api/sell/scan

const SL_MODES = [
  ["conservative", "Conservative", "Highest chance of expiring worthless first. Objective: the conservative P0 (the lower of the model and the Wilson lower bound of this stock's own history), subject to positive expected value after costs. Tightest tail and liquidity gates."],
  ["balanced", "Balanced", "Compensation per unit of tail risk. Objective: expected value divided by the average loss in the worst 5% of outcomes, subject to the P0 floor. The default."],
  ["income", "Income", "Most premium per dollar of capital. Objective: annualized return on capital, subject to a lower P0 floor and a wider tail allowance. Fewer refusals, more risk — the mode says so."],
  ["event", "Event premium", "EVENT PREMIUM mode: only contracts with a known earnings date inside their life, judged on the measured earnings-move history, not on ordinary volatility. Normal modes refuse these outright; here they are the point."],
];

const SL_STRATS = [
  ["", "All structures"],
  ["cash_secured_put", "Cash-secured put"],
  ["covered_call", "Covered call"],
  ["put_credit_spread", "Put credit spread"],
  ["call_credit_spread", "Call credit spread"],
  ["iron_condor", "Iron condor"],
];
const SL_STRAT_LABEL = Object.fromEntries(SL_STRATS.map(([k, v]) => [k, v]));

const SL_TIP = {
  card: "Which option has the strongest EVIDENCE of expiring worthless while still paying enough for the risk — across every name the Premium Edge scan fetched a chain for, in the mode you chose. Delta is what the market charges; the probabilities here are what the stock's own forecast and measured history say.",
  mode: "The selling mode. Each mode has its own gates (P0 floor, tail allowance, liquidity limits) and its own ranking objective, and each is backtested — the results are in SHORT_PREMIUM.md.",
  strategy: "Limit the board to one structure. No naked calls are ever listed: calls appear only as covered calls or as defined-risk spreads.",
  refresh: "Re-read the board from the last completed scan. Does not fetch chains.",
  scan: "Start the Premium Edge chain pass now. One bounded chain per name feeds both the Premium Edge and this board — no second fetch, no second budget. Takes a few minutes; the board refreshes itself when it finishes.",
  as_of: "When the last symbol was evaluated. Older than a session and every price on this board is from then, not now.",
  source: "Where the quotes came from. SCHWAB is the live broker feed; anything else is delayed and the data gate says so on every row.",
  scanning: "A chain pass is running right now; rows will change as symbols finish.",
  objective: "What #1 is #1 by in this mode. The other columns are shown so you can see what the objective traded away.",
  engine: "The engine version and the hash of the thresholds it ran with. Every recommendation is recorded with both, so a later calibration can tell which rules produced which results.",
  no_trade: "Nothing cleared every gate in this mode. That is a finding, not a gap: the list below names every reason a candidate was refused. A board that always has something on it is not measuring anything.",
  rank: "Position by this mode's objective, ties broken by Sell Quality.",
  symbol: "The underlying. Click to load it in the cards below.",
  strategy_col: "The structure: cash-secured put, covered call, put or call credit spread, iron condor.",
  side: "Which way the risk points: PUT means you lose if the stock falls through the strike, CALL if it rises through it, BOTH for a condor.",
  expiration: "The expiration date of the contract.",
  dte: "Days to expiration and the bucket it is judged in (0, 1–7, 8–21, 22–45, 46–60). Probabilities and evidence are computed at THIS horizon, never at a fixed 30 days.",
  short_strike: "The strike you sell. For a condor, the short put strike; the short call strike is in the next column.",
  long_strike: "The protective wing you buy (spreads and condors), and the width between the two. Single-leg structures have none.",
  dist: "How far the short strike sits from the current price, in percent.",
  moves: "The same distance in EXPECTED MOVES: forecast standard deviations at this horizon. A strike 2 expected moves away has roughly a 2.3% chance of being finished through under a normal model — the measured history tells you what it actually was.",
  delta: "The provider's delta (or the app's computed one when the provider has none). Delta is the RISK-NEUTRAL probability the market charges, not the real-world chance — the P0 columns are the real-world estimates.",
  credit: "The credit at the BID (the wing, if any, bought at the ASK). The bid is the only price a resting sell order is actually promised.",
  net_credit: "The credit after commissions, regulatory fees and the slippage allowance per share.",
  spread_pct: "The bid-ask spread of the short option as a percentage of the mid. Wide markets are expensive to enter and worse to exit; the liquidity gate refuses them.",
  oi: "Open interest on the short option — how many contracts exist. Thin interest means a hard exit.",
  volume: "Today's traded contracts on the short option.",
  p0_model: "P0 — the probability the option expires WORTHLESS on the model: a driftless lognormal at the forecast volatility for this horizon, with the measured tail correction applied (the model under-predicts beyond 1.5 expected moves; the correction table was built from ten years of 100 names).",
  p0_conservative: "P0 on the CONSERVATIVE bound: the lower of the model and the Wilson lower bound of this stock's own measured history at this distance and horizon (when 20+ independent windows exist; otherwise the model less five points). This is what the sizing and the Conservative mode use.",
  p0_measured: "P0 from this stock's OWN history at this distance and horizon, shrunk toward its sector peers and the universe when the sample is thin. Overlapping windows are not independent trials — the sample size shown is the independent count.",
  p_touch: "The probability the stock TOUCHES the short strike at some point before expiration (model, reflection principle) — it can touch and still expire worthless. This is what you would feel; P0 is what you would keep.",
  p_touch_measured: "How often this stock actually touched a strike this far out within this horizon, from its measured history.",
  p_profit: "The probability of a profit after costs: that the option finishes worth less than the net credit.",
  p_hit_50: "In modeled price paths (2,000 paths at the entry implied volatility), how often half the credit could be bought back before expiration, and the typical day it happens. MODELED — the forward test grades whether taking it beat holding.",
  ev: "Expected value per CONTRACT after commissions: what you are paid minus what the option is worth at the forecast volatility. Positive means the market overpays for this risk.",
  es95: "Expected shortfall: in the worst 5% of outcomes, the AVERAGE loss per share. Not the worst case — the average of the bad cases.",
  max_loss: "The most you can lose per share. Defined for spreads and condors; for a cash-secured put it is the strike less the credit.",
  capital: "The capital the position ties up per contract: the collateral for a cash-secured put, the width for a spread, 100 shares for a covered call.",
  roc: "The net credit as a percentage of that capital — what the money actually earns for this trade.",
  ann: "That return annualized by this contract's days to expiration. A comparison figure, not a forecast.",
  ev_tail: "Expected value per unit of tail loss (expected value per share divided by the worst-5% average loss). The Balanced mode's objective.",
  iv: "The implied volatility of the short option next to the forecast realized volatility at this horizon. The gap is the premium being sold.",
  vrp: "Implied volatility divided by the forecast realized volatility for this name — how many times over the market pays what the stock is forecast to do. Above 1 favors sellers; the edge gate requires it.",
  earnings: "The next known earnings date and whether it falls inside the life of the option. Normal modes refuse any contract with earnings inside; Event premium mode requires it.",
  sq: "SELL QUALITY SCORE, 0–100: a weighted blend of Safety, Edge, Income efficiency, Liquidity, Tail, Event, Data confidence and Calibration. Expand the row to see the breakdown. It is a summary, not the ranking objective.",
  confidence: "How well supported the probability is: MEASURED when 20+ independent windows of this stock's own history stand behind it, MODEL when it rests on the pooled universe.",
  data: "The quote source, the greeks' provenance (provider or computed) and how old the quote was when evaluated.",
  why1: "WHY IS NUMBER 1 NUMBER 1 — the plain-English defence of the top row, in the order a seller would ask: why this stock, this expiration, this strike, this side; the evidence; what the market is overpaying for; comparable breaches; what could make it wrong; the worst comparable outcome; what would reject it; and why #2 sits below.",
  pathway: "The RISK PATHWAY for the top rows: how to enter, how to manage, what danger looks like, when to exit, when a roll is worth it, what assignment means here, and how to size it.",
  failed: "WHY OTHER STOCKS FAILED — every refusal, grouped by the gate and the reason, with the count and the symbols. The gates run in order: data integrity, liquidity, event risk, positive edge after costs, tail risk, probability.",
  portfolio: "Are the top picks one bet wearing several names? Sector, expiration and side concentration of the top five.",
  funnel: "How many names the scan evaluated, and how many candidates each produced and qualified in this mode.",
  calibration: "What the app claimed against what happened: every recommendation this board showed is recorded with its probabilities and graded after expiry from daily bars. Finish and touch are MEASURED; profit is MODELED (intrinsic value at expiry — there is no option price history); early-profit targets are UNAVAILABLE. A slice under 30 graded recommendations is ACCRUING, not a verdict.",
  brier: "Brier score: the mean squared gap between the claimed probability and the outcome (0 is perfect; 0.25 is a coin flip). Lower is better.",
  ece: "Expected calibration error: the average gap between what was claimed and what happened, weighted by how many claims sat in each bucket.",
  claimed: "The average probability the app claimed, next to the rate that actually happened and the Wilson 95% interval of that rate. The claim should sit inside the interval.",
  learning: "The controlled learning loop: would a recalibration fitted on the FIRST half of the graded history have improved the SECOND half? Reported only; never applied by the app on its own.",
  components: "The eight Sell Quality components, each 0–100 with the weight it carries and the one-line reason for its score.",
  gates: "Each gate's verdict for this contract, in the order they run.",
  paths: "Profit-target intelligence from 2,000 modeled price paths at the entry implied volatility: the chance each target is reachable before expiry and the typical day. Residual reward versus residual risk decides whether to take it.",
  ex_div: "Ex-dividend risk on the short call: a dividend inside the life of the option raises early-assignment risk. No ex-dividend source is wired yet, so calls are flagged rather than cleared.",
  macro: "Scheduled macro events (FOMC, CPI, jobs) inside the life of the option, counted from the app's macro calendar.",
  stale: "This board is older than a trading session. Prices, premiums and probabilities are from when the scan last completed. Run the scan or check the broker connection.",
};

const slNum = (v, d = 2) => (v == null || !isFinite(v) ? "—" : Number(v).toFixed(d));
const slPct = (v, d = 0) => (v == null || !isFinite(v) ? "—" : `${(Number(v) * 100).toFixed(d)}%`);
const slPctRaw = (v, d = 1) => (v == null || !isFinite(v) ? "—" : `${Number(v).toFixed(d)}%`);
const slMoney = (v, d = 2) => (v == null || !isFinite(v) ? "—" : `$${Number(v).toFixed(d)}`);
const slSigned = (v, d = 0) =>
  (v == null || !isFinite(v) ? "—" : `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(d)}`);
// House rule: dates read "September 18, 2026".
const slDate = (s) => {
  if (!s) return "—";
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? String(s)
    : d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
};
const slTime = (s) => {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s)
    : d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
};
const slAge = (s) => {
  if (!s) return null;
  const ms = Date.now() - new Date(s).getTime();
  if (!isFinite(ms)) return null;
  const m = Math.round(ms / 60000);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 36) return `${h} hour${h === 1 ? "" : "s"} ago`;
  return `${Math.round(h / 24)} days ago`;
};
const slRowKey = (r) => `${r.symbol}|${r.strategy}|${r.expiration}|${r.short_strike}`;
const slPathKey = (r) => `${r.symbol}|${r.strategy}|${r.expiration}|${r.short_strike}`;

async function slReadJson(r) {
  const text = await r.text();
  try { return { d: JSON.parse(text) }; }
  catch (_e) {
    return r.ok
      ? { d: null, err: "The app's sign-in page answered instead of data. Reload the page to sign back in.", retryable: false }
      : { d: null, err: "The server answered with an error page instead of data — the hosting layer having a moment.", retryable: true };
  }
}

// ── column definitions (label, key, tooltip, formatter, numeric) ────────────
const SL_COLS = [
  ["Rank", "rank", "rank", (r) => r.rank, true],
  ["Symbol", "symbol", "symbol", null, false],
  ["Structure", "strategy", "strategy_col", (r) => SL_STRAT_LABEL[r.strategy] || r.strategy, false],
  ["Side", "side", "side", (r) => String(r.side || "").toUpperCase(), false],
  ["Expiration", "expiration", "expiration", (r) => slDate(r.expiration), false],
  ["Days", "dte", "dte", (r) => `${slNum(r.dte, 0)} · ${r.dte_bucket}`, true],
  ["Short strike", "short_strike", "short_strike",
    (r) => (r.short_call != null ? `${slNum(r.short_strike, 1)} / ${slNum(r.short_call, 1)}` : slNum(r.short_strike, 1)), true],
  ["Wing · width", "width", "long_strike",
    (r) => (r.long_strike == null ? "—" : `${slNum(r.long_strike, 1)} · ${slNum(r.width, 1)}`), true],
  ["Distance", "dist_pct", "dist", (r) => slPctRaw(Math.abs(r.dist_pct || 0), 1), true],
  ["Expected moves", "k_sigma", "moves", (r) => slNum(r.k_sigma, 2), true],
  ["Delta", "delta", "delta", (r) => slNum(Math.abs(r.delta || 0), 2), true],
  ["Credit (bid)", "credit", "credit", (r) => slMoney(r.credit), true],
  ["Net credit", "net_credit", "net_credit", (r) => slMoney(r.net_credit), true],
  ["Spread", "spread_pct", "spread_pct", (r) => slPctRaw(r.spread_pct, 1), true],
  ["Open interest", "oi", "oi", (r) => (r.oi == null ? "—" : Number(r.oi).toLocaleString()), true],
  ["Volume", "volume", "volume", (r) => (r.volume == null ? "—" : Number(r.volume).toLocaleString()), true],
  ["P0 model", "p0_model", "p0_model", (r) => slPct(r.p0_model), true],
  ["P0 conservative", "p0_conservative", "p0_conservative", (r) => slPct(r.p0_conservative), true],
  ["P0 measured", "p0_measured", "p0_measured", (r) => `${slPct(r.p0_measured)}${r.n_eff ? ` (${r.n_eff})` : ""}`, true],
  ["Touch", "p_touch", "p_touch", (r) => slPct(r.p_touch), true],
  ["Touch measured", "p_touch_measured", "p_touch_measured", (r) => slPct(r.p_touch_measured), true],
  ["Profit", "p_profit", "p_profit", (r) => slPct(r.p_profit), true],
  ["Half credit by", "p_hit_50", "p_hit_50",
    (r) => (r.p_hit_50 == null ? "—" : `${slPct(r.p_hit_50)} · day ${slNum(r.days_to_50, 0)}`), true],
  ["Expected value", "ev_per_contract", "ev", (r) => slSigned(r.ev_per_contract, 0), true],
  ["Worst 5%", "es95_per_share", "es95", (r) => slMoney(r.es95_per_share), true],
  ["Max loss", "max_loss_per_share", "max_loss", (r) => slMoney(r.max_loss_per_share), true],
  ["Capital", "capital", "capital", (r) => slMoney(r.capital, 0), true],
  ["Return", "roc_pct", "roc", (r) => slPctRaw(r.roc_pct, 2), true],
  ["Annualized", "annualized_roc_pct", "ann", (r) => slPctRaw(r.annualized_roc_pct, 0), true],
  ["Value per tail", "ev_per_tail", "ev_tail", (r) => slNum(r.ev_per_tail, 3), true],
  ["Implied vs forecast", "iv", "iv",
    (r) => `${slPct(r.iv)} / ${slPct(r.sigma_h)}`, true],
  ["Premium ratio", "vrp_ratio", "vrp", (r) => slNum(r.vrp_ratio, 2), true],
  ["Earnings", "earnings_in_days", "earnings",
    (r) => (r.earnings_date ? `${slDate(r.earnings_date)}${r.earnings_in_days != null ? ` (${r.earnings_in_days}d)` : ""}` : "none known"), false],
  ["Sell Quality", "sell_quality", "sq", (r) => slNum(r.sell_quality, 1), true],
  ["Confidence", "confidence", "confidence", (r) => String(r.confidence || "").toUpperCase(), false],
  ["Data", "data_source", "data",
    (r) => `${String(r.data_source || "?").toUpperCase()}${r.greeks ? ` · ${r.greeks}` : ""}${r.quote_age_s != null ? ` · ${slNum(r.quote_age_s, 0)}s` : ""}`, false],
];
const SL_DEFAULT_ASC = new Set(["rank", "symbol", "strategy", "side", "expiration", "dte", "spread_pct",
                                "p_touch", "p_touch_measured", "es95_per_share", "max_loss_per_share", "capital", "delta"]);

// ── sub-components ─────────────────────────────────────────────────────────
function SlModeBar({ mode, setMode }) {
  return (
    <div className="sl-modes" role="tablist" aria-label="Selling mode">
      {SL_MODES.map(([k, label, tip]) => (
        <button key={k} role="tab" aria-selected={mode === k}
                className={`sl-mode ${mode === k ? "sl-mode-on" : ""}`}
                title={tip} onClick={() => setMode(k)}>{label}</button>
      ))}
    </div>
  );
}

function SlQuality({ detail }) {
  const sq = detail && detail.sell_quality;
  if (!sq || !sq.breakdown) return null;
  return (
    <div className="sl-block" title={SL_TIP.components}>
      <div className="sl-block-title">Sell Quality {slNum(sq.score, 1)} — the breakdown</div>
      <table className="sl-mini">
        <thead>
          <tr>
            <th title="Which of the eight components">Component</th>
            <th className="scan-num" title="Component score 0–100">Score</th>
            <th className="scan-num" title="How much of the total this component can contribute">Weight</th>
            <th className="scan-num" title="Weight × score — what it added to the total">Points</th>
            <th title="Why it scored what it scored">Why</th>
          </tr>
        </thead>
        <tbody>
          {sq.breakdown.map((b) => (
            <tr key={b.component}>
              <td title={SL_TIP.components}>{b.component.replace(/_/g, " ")}</td>
              <td className="scan-num" title="Component score 0–100">{slNum(b.score, 0)}</td>
              <td className="scan-num" title="Weight">{slNum(b.weight, 2)}</td>
              <td className="scan-num" title="Points contributed">{slNum(b.points, 1)}</td>
              <td title="Reason">{b.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SlGates({ detail }) {
  const g = detail && detail.gates;
  if (!g) return null;
  const order = ["data", "liquidity", "events", "edge", "tail", "probability"];
  return (
    <div className="sl-block" title={SL_TIP.gates}>
      <div className="sl-block-title">The gates, in order</div>
      <ul className="sl-gates">
        {order.filter((k) => g[k]).map((k) => (
          <li key={k} className={g[k].ok ? "sl-gate-ok" : "sl-gate-no"} title={SL_TIP.gates}>
            <b>{k}</b> — {g[k].ok ? "passed" : "refused"}
            {(g[k].why || []).length ? `: ${g[k].why.join("; ")}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SlPaths({ detail }) {
  const p = detail && detail.probability && detail.probability.paths;
  if (!p || !p.targets) return (
    <div className="sl-block" title={SL_TIP.paths}>
      <div className="sl-block-title">Profit targets</div>
      <p className="sl-muted">Not modeled for this row (paths run for the finalists only, and need an entry implied volatility).</p>
    </div>
  );
  return (
    <div className="sl-block" title={SL_TIP.paths}>
      <div className="sl-block-title">Profit targets — {p.basis || "MODELED"}</div>
      <table className="sl-mini">
        <thead>
          <tr>
            <th title="How much of the credit is captured at this target">Target</th>
            <th className="scan-num" title="Share of modeled paths that reach it before expiry">Reachable</th>
            <th className="scan-num" title="Typical day it is reached, when it is">Typical day</th>
            <th className="scan-num" title="Median day it is reached, when it is">Median day</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(p.targets).map(([t, v]) => (
            <tr key={t}>
              <td title={SL_TIP.paths}>{t}% of credit</td>
              <td className="scan-num" title="Reachable before expiry">{slPct(v.p_hit)}</td>
              <td className="scan-num" title="Typical day">{slNum(v.expected_days_if_hit, 0)}</td>
              <td className="scan-num" title="Median day">{slNum(v.median_days_if_hit, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="sl-muted" title={SL_TIP.paths}>
        Near-zero (under 10% of the credit left) before expiry: {slPct(p.p_near_zero)} of paths.
        Touched the strike along the way: {slPct(p.p_touch_paths)}.
      </p>
    </div>
  );
}

function SlPathway({ pw }) {
  if (!pw) return null;
  const sec = (title, tip, body) => (
    <div className="sl-pw" title={tip}>
      <div className="sl-pw-title">{title}</div>
      {body}
    </div>
  );
  const pt = (pw.management && pw.management.profit_targets) || {};
  return (
    <div className="sl-pathway" title={SL_TIP.pathway}>
      {sec("Entry", "How to place the order and where to stop chasing.",
        <p>{pw.entry && pw.entry.note} Slippage allowance: {slMoney(pw.entry && pw.entry.max_slippage_per_share)} per share.</p>)}
      {sec("Management", "Profit targets and what invalidates the thesis.",
        <React.Fragment>
          <p>{pw.management && pw.management.note}</p>
          {Object.keys(pt).length ? (
            <p className="sl-muted">
              {Object.entries(pt).map(([t, v]) => `${t}%: ${slPct(v.p_hit)} by day ${slNum(v.expected_days_if_hit, 0)}`).join(" · ")}
            </p>
          ) : null}
          <ul>{((pw.management && pw.management.invalidation) || []).map((x, i) => <li key={i}>{x}</li>)}</ul>
        </React.Fragment>)}
      {sec("Danger", "What the beginning of a loss looks like, in this contract's own numbers.",
        <ul>{Object.entries(pw.danger || {}).map(([k, v]) => <li key={k}><b>{k}</b>: {v}</li>)}</ul>)}
      {sec("Exit", "RESIDUAL REWARD versus RESIDUAL RISK: when the premium left is small against a tail that is not.",
        <p>{pw.exit && pw.exit.residual_reward_vs_risk} <i>Rule: {pw.exit && pw.exit.rule}.</i></p>)}
      {sec("Roll", "A roll must improve the position at its own horizon, or it only postpones the problem.",
        <React.Fragment>
          <p>{pw.roll && pw.roll.note}</p>
          <ul>{((pw.roll && pw.roll.check) || []).map((x, i) => <li key={i}>{x}</li>)}</ul>
        </React.Fragment>)}
      {sec("Assignment", "What being assigned means for this structure.",
        <p>{pw.assignment && pw.assignment.note}</p>)}
      {sec("Position size", "Size on the conservative probability and the tail, never the point estimate.",
        <p>{pw.position_size && pw.position_size.note} Capital: {slMoney(pw.position_size && pw.position_size.capital_required, 0)};
          worst-5% loss per contract: {slMoney(pw.position_size && pw.position_size.es95_per_contract, 0)}
          {pw.position_size && pw.position_size.max_loss_per_contract != null
            ? `; maximum loss per contract: ${slMoney(pw.position_size.max_loss_per_contract, 0)}` : ""}.</p>)}
    </div>
  );
}

function SlWhy({ why }) {
  if (!why) return null;
  const sec = (title, body) => (
    <div className="sl-why-sec">
      <div className="sl-why-title">{title}</div>
      {Array.isArray(body) ? <ul>{body.map((x, i) => <li key={i}>{x}</li>)}</ul> : <p>{body}</p>}
    </div>
  );
  return (
    <div className="sl-why" title={SL_TIP.why1}>
      {sec("Why this stock", why.why_stock)}
      {sec("Why this expiration", why.why_expiration)}
      {sec("Why this strike", why.why_strike)}
      {sec("Why this side", why.why_side)}
      {sec("The evidence", why.evidence)}
      {sec("What the market is overpaying for", why.what_market_overpays)}
      {sec("Comparable breaches", why.comparable_breaches)}
      {sec("What could make this wrong", why.what_could_make_this_wrong)}
      {sec("The worst comparable outcome", why.worst_comparable_outcome)}
      {sec("What would reject it", why.what_would_reject_it)}
      {why.why_second_is_second ? sec("Why #2 is below #1", why.why_second_is_second) : null}
    </div>
  );
}

function SlCalibration({ apiFetch }) {
  const [cal, setCal] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const r = await apiFetch("/api/sell/calibration");
        const { d, err: e } = await slReadJson(r);
        if (!live) return;
        if (!d) { setErr(e); return; }
        setCal(d);
      } catch (e) { if (live) setErr(String(e && e.message ? e.message : e)); }
    })();
    return () => { live = false; };
  }, [apiFetch]);
  if (err) return <p className="research-error">{err}</p>;
  if (!cal) return <p className="sl-muted">Loading the calibration…</p>;
  const fields = cal.fields || {};
  const learn = fields.p0_model && fields.p0_model.learning;
  return (
    <div className="sl-block" title={SL_TIP.calibration}>
      <p className="sl-muted">
        Status <b>{cal.status}</b> · {cal.n_graded || 0} graded recommendation{cal.n_graded === 1 ? "" : "s"}
        {cal.built_at ? ` · built ${slDate(cal.built_at)} at ${slTime(cal.built_at)}` : ""}.
        Finish and touch are MEASURED from daily bars; profit is MODELED (intrinsic value at expiry);
        early-profit targets are UNAVAILABLE (no option price history). Slices under {cal.min_n} are ACCRUING.
      </p>
      {cal.n_graded ? (
        <table className="sl-mini">
          <thead>
            <tr>
              <th title="Which probability the app published">Probability</th>
              <th className="scan-num" title="Graded recommendations behind this row">Graded</th>
              <th className="scan-num" title={SL_TIP.claimed}>Claimed</th>
              <th className="scan-num" title={SL_TIP.claimed}>Happened</th>
              <th className="scan-num" title="Wilson 95% interval of the observed rate">Interval</th>
              <th className="scan-num" title={SL_TIP.brier}>Brier</th>
              <th className="scan-num" title={SL_TIP.ece}>Calibration error</th>
              <th title="MEASURED, ACCRUING or UNAVAILABLE">Status</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(fields).map(([k, f]) => {
              const o = f.overall || {};
              return (
                <tr key={k} className={o.claim_inside_ci === false ? "sl-cal-off" : ""}>
                  <td title={SL_TIP.calibration}>{f.name}</td>
                  <td className="scan-num" title="Graded">{o.n || 0}</td>
                  <td className="scan-num" title={SL_TIP.claimed}>{slPct(o.claimed_mean, 1)}</td>
                  <td className="scan-num" title={SL_TIP.claimed}>{slPct(o.observed_rate, 1)}</td>
                  <td className="scan-num" title="Wilson interval">
                    {o.observed_ci ? `${slPct(o.observed_ci[0], 0)}–${slPct(o.observed_ci[1], 0)}` : "—"}
                  </td>
                  <td className="scan-num" title={SL_TIP.brier}>{slNum(o.brier, 3)}</td>
                  <td className="scan-num" title={SL_TIP.ece}>{slNum(o.ece, 3)}</td>
                  <td title="Status">{o.status}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}
      {learn ? (
        <p className="sl-muted" title={SL_TIP.learning}>
          Learning check: <b>{learn.status}</b> — {learn.note}
        </p>
      ) : null}
    </div>
  );
}

function SlDetailRow({ r, detail, pathway, colSpan, why }) {
  return (
    <tr className="sl-detail-row">
      <td colSpan={colSpan} className="mtable-full">
        <div className="sl-detail">
          {why ? (
            <div className="sl-block">
              <div className="sl-block-title" title={SL_TIP.why1}>Why is number 1 number 1?</div>
              <SlWhy why={why} />
            </div>
          ) : null}
          {detail ? (
            <React.Fragment>
              <SlQuality detail={detail} />
              <SlGates detail={detail} />
              <SlPaths detail={detail} />
            </React.Fragment>
          ) : (
            <p className="sl-muted">Loading the full evaluation…</p>
          )}
          {pathway ? (
            <div className="sl-block">
              <div className="sl-block-title" title={SL_TIP.pathway}>Risk pathway</div>
              <SlPathway pw={pathway} />
            </div>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

// ── the card ─────────────────────────────────────────────────────────────────
function SellBestCard({ apiFetch, onPickTicker }) {
  const [mode, setModeRaw] = useState(() => {
    try { return localStorage.getItem("jerrySellMode") || "balanced"; } catch { return "balanced"; }
  });
  const setMode = (m) => { setModeRaw(m); try { localStorage.setItem("jerrySellMode", m); } catch {} };
  const [strategy, setStrategy] = useState("");
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [scanNote, setScanNote] = useState(null);
  const [sortK, setSortK] = useState("rank");
  const [sortD, setSortD] = useState(1);
  const [open, setOpen] = useState(null);           // row key expanded
  const [details, setDetails] = useState({});        // symbol|mode -> detail payload
  const [showFailed, setShowFailed] = useState(false);
  const [showFunnel, setShowFunnel] = useState(false);
  const [showCal, setShowCal] = useState(false);
  const seq = useRef(0);

  const load = React.useCallback(async () => {
    const mine = ++seq.current;
    setBusy(true);
    try {
      const qs = `mode=${encodeURIComponent(mode)}${strategy ? `&strategy=${encodeURIComponent(strategy)}` : ""}`;
      const r = await apiFetch(`/api/sell?${qs}`);
      const { d, err: pageErr } = await slReadJson(r);
      if (mine !== seq.current) return;
      if (d == null) { setData(null); setErr(pageErr); return; }
      setData(d);
      setErr(d.ok === false && d.error ? d.error : null);
    } catch (e) {
      if (mine === seq.current) setErr(String(e && e.message ? e.message : e));
    } finally {
      if (mine === seq.current) setBusy(false);
    }
  }, [apiFetch, mode, strategy]);

  useEffect(() => { load(); }, [load]);
  // While a chain pass is running, re-read the board every 45 seconds; the
  // rest of the time nothing polls — the board only changes when a scan does.
  useEffect(() => {
    if (!(data && data.scanning)) return;
    const t = setInterval(load, 45000);
    return () => clearInterval(t);
  }, [data && data.scanning, load]);

  const runScan = async () => {
    setScanNote("Starting the chain pass…");
    try {
      const r = await apiFetch("/api/sell/scan?force=1");
      const { d } = await slReadJson(r);
      setScanNote(d && d.error ? d.error : "Scan started. The board refreshes itself as symbols finish.");
      setTimeout(load, 4000);
    } catch (e) {
      setScanNote(String(e && e.message ? e.message : e));
    }
  };

  const rows = (data && data.rows) || [];
  const sorted = useMemo(() => {
    const key = (r) => {
      const v = r[sortK];
      if (v == null) return sortD > 0 ? Infinity : -Infinity;
      return typeof v === "string" ? v.toLowerCase() : v;
    };
    return rows.slice().sort((a, b) => {
      const ka = key(a), kb = key(b);
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * sortD;
    });
  }, [rows, sortK, sortD]);

  const th = (label, k, tipKey, numeric) => (
    <th key={k} className={numeric ? "scan-th-num" : ""} title={SL_TIP[tipKey]}
        style={{ cursor: "pointer" }}
        onClick={() => {
          if (sortK === k) setSortD((x) => -x);
          else { setSortK(k); setSortD(SL_DEFAULT_ASC.has(k) ? 1 : -1); }
        }}>
      {label}{sortK === k ? (sortD < 0 ? " ↓" : " ↑") : ""}
    </th>
  );

  const detailFor = (r) => {
    const top = (data && data.top_detail) || [];
    const hit = top.find((c) => slRowKey(c) === slRowKey(r));
    if (hit) return hit;
    const d = details[`${r.symbol}|${mode}`];
    if (d && d.top_detail) return d.top_detail.find((c) => slRowKey(c) === slRowKey(r)) || null;
    return null;
  };

  const toggle = async (r) => {
    const k = slRowKey(r);
    if (open === k) { setOpen(null); return; }
    setOpen(k);
    if (!detailFor(r) && !details[`${r.symbol}|${mode}`]) {
      try {
        const resp = await apiFetch(`/api/sell/detail?symbol=${encodeURIComponent(r.symbol)}&mode=${encodeURIComponent(mode)}`);
        const { d } = await slReadJson(resp);
        if (d) setDetails((prev) => ({ ...prev, [`${r.symbol}|${mode}`]: d }));
      } catch (_e) { /* the row still opens with what the board carries */ }
    }
  };

  const ageMs = data && data.as_of ? Date.now() - new Date(data.as_of).getTime() : null;
  const stale = ageMs != null && isFinite(ageMs) && ageMs > 20 * 3600 * 1000;
  const sources = new Set(((data && data.per_symbol) || []).map((s) => String(s.source || "?").toUpperCase()));
  const objective = data && data.objective ? String(data.objective).replace(/^max_/, "").replace(/_/g, " ") : "";
  const failed = (data && data.why_others_failed) || [];
  const portfolio = data && data.portfolio;
  const modeLabel = (SL_MODES.find(([k]) => k === mode) || [])[1] || mode;

  return (
    <div className="card sl-card">
      <div className="card-head">
        <div>
          <span className="kicker" title={SL_TIP.card}>Best sales today</span>
          <h3 className="card-title">Which option has the strongest evidence of expiring worthless — and still pays for the risk</h3>
          <p className="card-sub">
            Every name the scan fetched a chain for, every structure it supports, ranked in the mode you
            chose after six gates: data, liquidity, events, edge after costs, tail, probability.
            Delta is what the market charges. P0 is what the forecast and this stock&rsquo;s own history say.
          </p>
        </div>
        <div className="toolbar sl-toolbar">
          <select className="sl-select" value={strategy} title={SL_TIP.strategy}
                  onChange={(e) => setStrategy(e.target.value)}>
            {SL_STRATS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <button className="research-run-btn" onClick={load} disabled={busy} title={SL_TIP.refresh}>
            {busy ? "Reading…" : "Refresh"}
          </button>
          <button className="research-run-btn sl-scan-btn" onClick={runScan} title={SL_TIP.scan}
                  disabled={!!(data && data.scanning)}>
            {data && data.scanning ? "Scanning…" : "Run scan"}
          </button>
        </div>
      </div>

      <SlModeBar mode={mode} setMode={setMode} />

      {data ? (
        <p className="sl-status">
          <span title={SL_TIP.as_of}>
            {data.as_of ? `Evaluated ${slDate(data.as_of)} at ${slTime(data.as_of)} (${slAge(data.as_of)})` : "No scan on file"}
          </span>
          {sources.size ? <span title={SL_TIP.source}> · source {[...sources].join(", ")}</span> : null}
          {data.scanning ? <span className="sl-live" title={SL_TIP.scanning}> · scanning now</span> : null}
          <span title={SL_TIP.objective}> · {modeLabel} mode ranks by {objective}</span>
          <span title={SL_TIP.funnel}> · {data.n_symbols} names · {data.n_qualified} qualified</span>
          <span title={SL_TIP.engine}> · {data.engine} · {String(data.config_hash || "").slice(0, 8)}</span>
        </p>
      ) : null}
      {scanNote ? <p className="sl-muted">{scanNote}</p> : null}

      {stale ? (
        <div className="su-refused" title={SL_TIP.stale}>
          <b>This board is from {slDate(data.as_of)} at {slTime(data.as_of)}, not now.</b>{" "}
          Prices, premiums and probabilities are from then. Run the scan, or check the broker connection in the sidebar.
        </div>
      ) : null}

      {busy && !data ? (
        <div className="st-loading" aria-busy="true">
          <div className="skel skel-line" style={{ width: "40%" }} />
          <div className="skel skel-line" style={{ width: "88%" }} />
        </div>
      ) : null}

      {err ? (
        <React.Fragment>
          <div className="research-error">{err}</div>
          <button className="card-error-btn st-retry" onClick={load}>Try again</button>
        </React.Fragment>
      ) : null}

      {data && !err && data.no_trade ? (
        <div className="su-refused sl-notrade" title={SL_TIP.no_trade}>
          <b>NO TRADE in {modeLabel} mode.</b> {data.no_trade_reason}
        </div>
      ) : null}

      {rows.length ? (
        <div className="scan-table-wrap sl-table-wrap">
          <table className="scan-table mtable sl-table">
            <thead>
              <tr>{SL_COLS.map(([label, k, tipKey, _f, numeric]) => th(label, k, tipKey, numeric))}</tr>
            </thead>
            <tbody>
              {sorted.map((r) => {
                const k = slRowKey(r);
                const isOpen = open === k;
                return (
                  <React.Fragment key={k}>
                    <tr className={`scan-row sl-row ${isOpen ? "scan-row-active" : ""} ${r.rank === 1 ? "sl-row-top" : ""}`}
                        onClick={() => toggle(r)} title="Click for the Sell Quality breakdown, the gates, the profit targets and the risk pathway">
                      {SL_COLS.map(([label, ck, tipKey, f, numeric]) => (
                        <td key={ck} className={numeric ? "scan-num" : ""} data-label={label} title={SL_TIP[tipKey]}>
                          {ck === "symbol" ? (
                            <button className="su-blink" title={SL_TIP.symbol}
                                    onClick={(e) => { e.stopPropagation(); onPickTicker && onPickTicker(r.symbol); }}>
                              {r.symbol}
                            </button>
                          ) : f(r)}
                        </td>
                      ))}
                    </tr>
                    {isOpen ? (
                      <SlDetailRow r={r} colSpan={SL_COLS.length}
                                   detail={detailFor(r)}
                                   pathway={(data.risk_pathways || {})[slPathKey(r)] || null}
                                   why={r.rank === 1 ? data.why_number_one : null} />
                    ) : null}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {portfolio && (portfolio.flags || []).length ? (
        <div className="sl-flags" title={SL_TIP.portfolio}>
          <b>Portfolio note:</b> {portfolio.flags.join(" · ")}
        </div>
      ) : null}

      {data ? (
        <div className="su-more">
          <button className="su-more-btn" aria-expanded={showFailed} title={SL_TIP.failed}
                  onClick={() => setShowFailed((v) => !v)}>
            {showFailed ? "Hide" : "Show"} why other stocks failed ({failed.reduce((a, g) => a + (g.n || 0), 0)} refusals)
          </button>
          {showFailed ? (
            failed.length ? (
              <table className="sl-mini sl-failed">
                <thead>
                  <tr>
                    <th title="The gate that refused it; gates run in this order: data, liquidity, events, edge, tail, probability">Gate</th>
                    <th title="The reason, in the gate's own words (one example of the shape; the numbers vary by contract)">Reason</th>
                    <th className="scan-num" title="How many candidates were refused for this reason">Count</th>
                    <th title="Which symbols">Symbols</th>
                  </tr>
                </thead>
                <tbody>
                  {failed.map((g, i) => (
                    <tr key={i}>
                      <td title={SL_TIP.failed}>{g.gate}</td>
                      <td title={SL_TIP.failed}>{g.reason}</td>
                      <td className="scan-num" title="Count">{g.n}</td>
                      <td title="Symbols">{(g.symbols || []).join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <p className="sl-muted">Nothing was refused — or nothing has been scanned yet.</p>
          ) : null}
          <button className="su-more-btn" aria-expanded={showFunnel} title={SL_TIP.funnel}
                  onClick={() => setShowFunnel((v) => !v)}>
            {showFunnel ? "Hide" : "Show"} the funnel ({data.n_symbols || 0} names)
          </button>
          {showFunnel ? (
            <table className="sl-mini">
              <thead>
                <tr>
                  <th title="Symbol">Symbol</th>
                  <th title="When this symbol was last evaluated">Evaluated</th>
                  <th className="scan-num" title="Spot price at evaluation">Spot</th>
                  <th className="scan-num" title="Structures built from the chain">Candidates</th>
                  <th className="scan-num" title="Structures that cleared every gate in this mode">Qualified</th>
                  <th className="scan-num" title="Candidates the data gate could not judge">Insufficient</th>
                  <th title={SL_TIP.source}>Source</th>
                </tr>
              </thead>
              <tbody>
                {(data.per_symbol || []).map((s) => (
                  <tr key={s.symbol}>
                    <td title="Symbol">{s.symbol}</td>
                    <td title="Evaluated">{slDate(s.as_of)} {slTime(s.as_of)}</td>
                    <td className="scan-num" title="Spot">{slMoney(s.spot)}</td>
                    <td className="scan-num" title="Candidates">{s.n_candidates}</td>
                    <td className="scan-num" title="Qualified">{s.n_qualified}</td>
                    <td className="scan-num" title="Insufficient">{s.insufficient}</td>
                    <td title={SL_TIP.source}>{String(s.source || "?").toUpperCase()}{s.provider_serving === false ? " · not answering" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          <button className="su-more-btn" aria-expanded={showCal} title={SL_TIP.calibration}
                  onClick={() => setShowCal((v) => !v)}>
            {showCal ? "Hide" : "Show"} what the app claimed versus what happened
          </button>
          {showCal ? <SlCalibration apiFetch={apiFetch} /> : null}
        </div>
      ) : null}
    </div>
  );
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, { SellBestCard: React.memo(SellBestCard) });
