// tab-backtest.jsx — LAZY CHUNK (v3.64), split from app-cards.jsx.
// Natural-language Backtest Lab; loaded on first Backtest-tab open.

// ── Natural-language Backtest Lab (v3.43) ───────────────────────────────────
// Describe a strategy in plain English → the backend's deterministic trading
// grammar converts it to explicit JSON rules → review/edit every rule here →
// run. The engine fills at the NEXT bar's open (no look-ahead), models
// spread/slippage/commission, skips illiquid fills, and reports loud
// warnings whenever the data can't support the idea (options are
// model-priced; no historical news/IV). Results persist across reloads.
const BT_COND_TYPES = {
  gap_pct: { label: "Gap at open vs prior close (%)", make: () => ({ type: "gap_pct", op: "<=", value: -2 }) },
  cross_above_open: { label: "Crosses back ABOVE the open (intraday)", make: () => ({ type: "cross_above_open" }) },
  cross_below_open: { label: "Crosses back BELOW the open (intraday)", make: () => ({ type: "cross_below_open" }) },
  rel_volume: { label: "Volume ≥ N × average", make: () => ({ type: "rel_volume", mult: 2, lookback: 20 }) },
  drawdown_from_high: { label: "Drawdown from high (%)", make: () => ({ type: "drawdown_from_high", pct: 30, lookback: 252 }) },
  rsi: { label: "RSI", make: () => ({ type: "rsi", period: 14, op: "<=", value: 30 }) },
  sma_cross: { label: "MA cross", make: () => ({ type: "sma_cross", fast: 20, slow: 50, direction: "up" }) },
  price_vs_sma: { label: "Price vs moving average", make: () => ({ type: "price_vs_sma", op: ">=", period: 200 }) },
  new_high: { label: "New N-day high", make: () => ({ type: "new_high", lookback: 20 }) },
  new_low: { label: "New N-day low", make: () => ({ type: "new_low", lookback: 20 }) },
  consec_down: { label: "N consecutive down days", make: () => ({ type: "consec_down", n: 3 }) },
  consec_up: { label: "N consecutive up days", make: () => ({ type: "consec_up", n: 3 }) },
  day_change_pct: { label: "Change on the day (%)", make: () => ({ type: "day_change_pct", op: "<=", value: -3 }) },
  move_pct: { label: "Move over trailing N days (%)", make: () => ({ type: "move_pct", days: 5, op: ">=", value: 10 }) },
  price_abs: { label: "Price filter ($)", make: () => ({ type: "price_abs", op: ">=", value: 20 }) },
  market_regime: { label: "SPY regime filter", make: () => ({ type: "market_regime", regime: "uptrend" }) },
};
const BT_EXIT_TYPES = {
  profit_pct: { label: "Profit target (%)", make: () => ({ type: "profit_pct", value: 5 }) },
  stop_pct: { label: "Stop loss (%)", make: () => ({ type: "stop_pct", value: 2 }) },
  trailing_stop_pct: { label: "Trailing stop (%)", make: () => ({ type: "trailing_stop_pct", value: 8 }) },
  time_days: { label: "Time exit (trading days)", make: () => ({ type: "time_days", value: 10 }) },
  same_day_close: { label: "Exit by the close (same day)", make: () => ({ type: "same_day_close" }) },
  hold_to_expiry: { label: "Hold option to expiry", make: () => ({ type: "hold_to_expiry" }) },
};
const BT_EXAMPLES = [
  // Premium-selling lifecycle presets (v2 engine: management, assignment, BP)
  "Sell a 30 delta put 45 dte, take profit at 50% of credit, stop at 2x credit, exit at 21 dte, skip earnings week.",
  "Sell strangles at 16 delta, 45 dte, take profit at 50%, exit at 21 dte, skip earnings week.",
  "Sell an iron condor at 20 delta, 45 dte, wings at 5 delta, take profit at 50%, exit at 21 dte.",
  "Wheel on KO, 30 dte, 30 delta, take profit at 60%.",
  "Sell covered calls at 25 delta, 30 dte, roll at 7 dte.",
  // Stock / long-option strategies (v1 engine)
  "Buy stocks that open down at least 2%, reverse above the opening price, and have volume at least twice the 20 day average. Exit at a 5% profit, a 2% stop loss, or before the market closes.",
  "Buy stock after a 30% drawdown from a recent high. Hold for 15 days with a 10% trailing stop. $5,000 per trade on AAPL, MSFT and NVDA.",
  "Buy 30 dte calls at the money when RSI 14 below 30 and price above the 200 day moving average, only when SPY is in an uptrend. 50% profit target, 25% stop loss.",
];

function BTParamInputs({ cond, onChange }) {
  // Generic param editor: every non-label key becomes a small typed input,
  // so any rule the parser (or the user via JSON) produces stays editable.
  const keys = Object.keys(cond).filter(k => k !== "type" && k !== "label");
  return (
    <span className="bt-params">
      {keys.map(k => (
        <label key={k} className="bt-param" title={`Edit the '${k}' parameter of this rule.`}>
          <span>{k}</span>
          {k === "op" ? (
            <select value={cond[k]} onChange={e => onChange({ ...cond, [k]: e.target.value })}>
              <option value="<=">≤</option><option value=">=">≥</option>
            </select>
          ) : k === "direction" || k === "regime" ? (
            <select value={cond[k]} onChange={e => onChange({ ...cond, [k]: e.target.value })}>
              {k === "direction"
                ? [<option key="u" value="up">up</option>, <option key="d" value="down">down</option>]
                : [<option key="u" value="uptrend">uptrend</option>, <option key="d" value="downtrend">downtrend</option>, <option key="c" value="chop">chop</option>]}
            </select>
          ) : (
            <input type="number" step="any" value={cond[k] ?? ""}
                   onChange={e => onChange({ ...cond, [k]: e.target.value === "" ? null : +e.target.value })} />
          )}
        </label>
      ))}
    </span>
  );
}

function BTEquityCurve({ curve, start }) {
  if (!curve || curve.length < 2) return null;
  const W = 640, H = 150, PAD = 6;
  const vals = curve.map(p => p.equity);
  const lo = Math.min(start, ...vals), hi = Math.max(start, ...vals);
  const span = Math.max(1e-9, hi - lo);
  const x = i => PAD + (W - 2 * PAD) * (i / (curve.length - 1));
  const y = v => H - PAD - (H - 2 * PAD) * ((v - lo) / span);
  const pts = curve.map((p, i) => `${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
  const up = vals[vals.length - 1] >= start;
  return (
    <div className="bt-curve" title={`Equity curve: $${Math.round(start).toLocaleString()} starting equity, stepped at each trade exit (realized P&L). ${curve.length} closed trades from ${curve[0].date} to ${curve[curve.length - 1].date}.`}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <line x1={PAD} x2={W - PAD} y1={y(start)} y2={y(start)} className="bt-curve-base" />
        <polyline points={pts} className={`bt-curve-line ${up ? "up" : "down"}`} />
      </svg>
      <div className="bt-curve-lbls">
        <span>{curve[0].date}</span>
        <span className={up ? "up" : "down"}>${Math.round(vals[vals.length - 1]).toLocaleString()}</span>
        <span>{curve[curve.length - 1].date}</span>
      </div>
    </div>
  );
}

// ── B4: validation scorecard ────────────────────────────────────────────────
// The honest "should I trust this?" header. Every chip is a REAL statistic
// with its meaning in the tooltip — no blended black-box score.
function BTScorecard({ result }) {
  const M = result.metrics || {};
  const chips = [];
  const add = (label, value, tone, tip) => chips.push({ label, value, tone, tip });
  if (M.n_trades != null)
    add("SAMPLE", `${M.n_trades} trades`, M.n_trades >= 30 ? "ok" : "warn",
        M.n_trades >= 30 ? "30+ trades — statistics start to mean something."
          : "Under 30 trades — every number here is noisy; treat as anecdote, not evidence.");
  const wf = result.walk_forward;
  if (wf && wf.wf_efficiency != null)
    add("WALK-FWD", `${wf.wf_efficiency}× · ${wf.oos_positive_folds} OOS+`,
        wf.wf_efficiency >= 0.5 ? "ok" : wf.wf_efficiency > 0 ? "warn" : "bad",
        `Out-of-sample P&L-per-trade ÷ in-sample, across ${wf.folds.length} rolling folds. ${wf.verdict}. 1.0 = the edge fully persists on data that follows each fit window.`);
  const mc = result.monte_carlo;
  if (mc)
    add("MONTE CARLO", `P95 DD ${mc.max_dd_pct.p95}% · ruin ${mc.risk_of_ruin_pct}%`,
        mc.risk_of_ruin_pct <= 1 ? "ok" : mc.risk_of_ruin_pct <= 10 ? "warn" : "bad",
        `${mc.n_paths.toLocaleString()} seeded reorderings of your own trades: 95th-percentile max drawdown ${mc.max_dd_pct.p95}% (median ${mc.max_dd_pct.p50}%), and ${mc.risk_of_ruin_pct}% of paths breached the ${mc.ruin_threshold_dd_pct}% drawdown "ruin" line at this sizing. ${mc.note}`);
  const ds = result.deflated_sharpe;
  if (ds)
    add("DEFLATED SHARPE", `${ds.dsr}`, ds.dsr >= 0.95 ? "ok" : ds.dsr >= 0.5 ? "warn" : "bad",
        `Probability the observed Sharpe beats the best of ${ds.n_trials} tried configuration(s) by skill rather than selection luck (Bailey & López de Prado). Hurdle: ${ds.hurdle_sr_annual} annualized. ${ds.verdict}.`);
  const sens = result.sensitivity;
  if (sens)
    add("PREMIUM BAND", /survives/.test(sens.verdict) ? "sign holds" : "sign flips",
        /survives/.test(sens.verdict) ? "ok" : "bad",
        sens.verdict);
  const rm = result.regime_matrix;
  if (rm && rm.concentration_warning)
    add("REGIME", "concentrated", "warn", rm.concentration_warning);
  if (M.real_fill_pct != null && M.real_fill_pct > 0)
    add("REAL FILLS", `${M.real_fill_pct}%`, "ok",
        "Share of entry fills priced from real recorded bid/ask instead of the model.");
  if (!chips.length) return null;
  return (
    <div className="bt-score" role="group" aria-label="Validation scorecard">
      {chips.map((c, i) => (
        <span key={i} className={`bt-score-chip ${c.tone}`} title={c.tip}>
          <em>{c.label}</em><b>{c.value}</b>
        </span>
      ))}
    </div>
  );
}

// ── B4: drawdown underlay ───────────────────────────────────────────────────
function BTDrawdown({ curve }) {
  if (!curve || curve.length < 3) return null;
  const W = 640, H = 46, PAD = 6;
  let peak = -Infinity;
  const dds = curve.map(p => {
    peak = Math.max(peak, p.equity);
    return peak > 0 ? (peak - p.equity) / peak * 100 : 0;
  });
  const maxDD = Math.max(0.1, ...dds);
  const x = i => PAD + (W - 2 * PAD) * (i / (curve.length - 1));
  const y = v => PAD + (H - 2 * PAD) * (v / maxDD);
  const pts = [`${PAD},${PAD}`]
    .concat(dds.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`))
    .concat([`${W - PAD},${PAD}`]).join(" ");
  return (
    <div className="bt-dd" title={`Drawdown underlay (mark-to-model — open-position pain included). Deepest: ${maxDD.toFixed(1)}%.`}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <polygon points={pts} className="bt-dd-area" />
      </svg>
      <span className="bt-dd-lbl num">−{maxDD.toFixed(1)}%</span>
    </div>
  );
}

// ── B4: monthly returns heatmap ─────────────────────────────────────────────
function BTMonthly({ curve }) {
  if (!curve || curve.length < 25) return null;
  const byMonth = new Map();       // "YYYY-MM" -> {first, last}
  for (const p of curve) {
    const m = p.date.slice(0, 7);
    const e = byMonth.get(m);
    if (!e) byMonth.set(m, { first: p.equity, last: p.equity });
    else e.last = p.equity;
  }
  const months = [...byMonth.keys()].sort();
  if (months.length < 3) return null;
  let prev = null;
  const cells = months.map(m => {
    const e = byMonth.get(m);
    const base = prev != null ? prev : e.first;
    prev = e.last;
    const ret = base > 0 ? (e.last / base - 1) * 100 : 0;
    return { m, ret };
  });
  const amax = Math.max(0.5, ...cells.map(c => Math.abs(c.ret)));
  return (
    <div className="bt-months" title="Month-by-month return of the mark-to-model equity curve. Consistent light green beats occasional dark green next to dark red.">
      {cells.map(c => (
        <span key={c.m} className="bt-month" title={`${c.m}: ${c.ret >= 0 ? "+" : ""}${c.ret.toFixed(2)}%`}
              style={{ background: `color-mix(in oklch, var(${c.ret >= 0 ? "--up" : "--down"}), transparent ${Math.round(90 - 55 * Math.min(1, Math.abs(c.ret) / amax))}%)` }}>
          <em>{c.m.slice(2)}</em><b className="num">{c.ret >= 0 ? "+" : ""}{c.ret.toFixed(1)}</b>
        </span>
      ))}
    </div>
  );
}

// ── B4: P/L distribution + MAE/MFE ─────────────────────────────────────────
function BTHistogram({ trades }) {
  if (!trades || trades.length < 8) return null;
  const pnls = trades.map(t => t.pnl);
  const lo = Math.min(...pnls), hi = Math.max(...pnls);
  if (hi <= lo) return null;
  const NB = Math.min(21, Math.max(9, Math.floor(trades.length / 3)));
  const bins = new Array(NB).fill(0);
  for (const p of pnls) bins[Math.min(NB - 1, Math.floor((p - lo) / (hi - lo) * NB))]++;
  const mx = Math.max(...bins);
  const zeroX = lo < 0 && hi > 0 ? (0 - lo) / (hi - lo) * 100 : null;
  return (
    <div className="bt-hist" title={`Distribution of the ${trades.length} trade P&Ls. Premium selling should look like many small wins left-skewed by a few larger losses — if the LEFT tail dominates the total, the strategy is picking up pennies in front of the steamroller.`}>
      <div className="bt-hist-bars">
        {bins.map((b, i) => {
          const mid = lo + (i + 0.5) / NB * (hi - lo);
          return <span key={i} className={mid >= 0 ? "up" : "down"}
                       style={{ height: `${(b / mx) * 100}%` }}
                       title={`$${Math.round(lo + i / NB * (hi - lo)).toLocaleString()} … $${Math.round(lo + (i + 1) / NB * (hi - lo)).toLocaleString()}: ${b} trade${b === 1 ? "" : "s"}`} />;
        })}
        {zeroX != null && <i className="bt-hist-zero" style={{ left: `${zeroX}%` }} />}
      </div>
      <div className="bt-hist-lbls num"><span>${Math.round(lo).toLocaleString()}</span><span>P&L per trade</span><span>${Math.round(hi).toLocaleString()}</span></div>
    </div>
  );
}

function btExcursions(t) {
  // MAE/MFE from the trade's daily marks (open P/L path, $ per position).
  if (!t.marks || !t.marks.length) return null;
  const entryCash = t.is_credit ? t.credit : -t.credit;
  const mult = (t.contracts || 1) * 100;
  let mae = 0, mfe = 0;
  for (const m of t.marks) {
    const pl = (entryCash + m.value) * mult;
    mae = Math.min(mae, pl);
    mfe = Math.max(mfe, pl);
  }
  return { mae, mfe };
}

function BTMaeMfe({ trades }) {
  const pts = (trades || []).map(t => ({ t, e: btExcursions(t) })).filter(x => x.e);
  if (pts.length < 8) return null;
  const W = 300, H = 170, PAD = 26;
  const maxX = Math.max(50, ...pts.map(p => -p.e.mae));
  const maxY = Math.max(50, ...pts.map(p => Math.max(p.e.mfe, p.t.pnl, 0)));
  const x = v => PAD + (W - PAD - 6) * (v / maxX);
  const y = v => H - PAD - (H - PAD - 6) * (v / maxY);
  return (
    <div className="bt-maemfe" title="Each dot is a trade: how far it went AGAINST you at its worst (→, Maximum Adverse Excursion, from daily marks) vs its final P&L color. A cluster of green dots far right = winners that survived deep pain — your stop may be doing nothing; red dots near the left axis = losses that never recovered — a tighter stop was free.">
      <svg viewBox={`0 0 ${W} ${H}`}>
        <line x1={PAD} y1={H - PAD} x2={W - 4} y2={H - PAD} className="bt-ax" />
        <line x1={PAD} y1={4} x2={PAD} y2={H - PAD} className="bt-ax" />
        {pts.map((p, i) => (
          <circle key={i} cx={x(-p.e.mae)} cy={y(Math.max(0, p.e.mfe))} r="3"
                  className={p.t.pnl >= 0 ? "up" : "down"}>
            <title>{`${p.t.symbol} ${p.t.entry_date}: worst ${Math.round(p.e.mae).toLocaleString()}, best +${Math.round(p.e.mfe).toLocaleString()}, final ${Math.round(p.t.pnl).toLocaleString()}`}</title>
          </circle>
        ))}
      </svg>
      <div className="bt-maemfe-lbl"><span>MAE → (worst open loss)</span><span>MFE ↑</span></div>
    </div>
  );
}

// ── B4: breakdown tables + WF folds + benchmarks + optimizer grid ──────────
function BTBreakdowns({ result }) {
  const trades = result.trades || [];
  const fmtD = v => `${v < 0 ? "−" : ""}$${Math.abs(Math.round(v)).toLocaleString()}`;
  const groupBy = (key) => {
    const g = {};
    for (const t of trades) {
      const k = key(t) || "—";
      const e = g[k] || (g[k] = { n: 0, pnl: 0, wins: 0 });
      e.n++; e.pnl += t.pnl; e.wins += t.pnl > 0 ? 1 : 0;
    }
    return Object.entries(g).sort((a, b) => b[1].pnl - a[1].pnl);
  };
  const Tbl = ({ title, rows, tip }) => (
    <div className="bt-bd" title={tip}>
      <em>{title}</em>
      <table><tbody>
        {rows.map(([k, v]) => (
          <tr key={k}><td>{k}</td><td className="num">{v.n}</td>
            <td className="num">{Math.round(v.wins / v.n * 100)}%</td>
            <td className={`num ${v.pnl >= 0 ? "up" : "down"}`}>{fmtD(v.pnl)}</td></tr>
        ))}
      </tbody></table>
    </div>
  );
  const wf = result.walk_forward;
  const bench = result.benchmarks;
  const opt = result.optimizer;
  const rm = result.regime_matrix;
  return (
    <div className="bt-bds">
      {trades.length > 3 && <Tbl title="BY EXIT" rows={groupBy(t => t.reason)}
        tip="Where the P&L actually comes from. Healthy managed premium selling earns most from 'target' (profit-takes); heavy 'stop'/'assigned' P&L means the entries are the problem." />}
      {trades.length > 3 && new Set(trades.map(t => t.symbol)).size > 1 &&
        <Tbl title="BY SYMBOL" rows={groupBy(t => t.symbol)}
          tip="Concentration check — if one name carries the whole result, this is a stock thesis wearing a strategy costume." />}
      {rm && rm.cells && rm.cells.length > 1 && (
        <div className="bt-bd" title="Trend regime (SPY 50/200) × volatility regime (VIX tercile) at entry. An edge that only exists in one cell needs that cell to persist.">
          <em>BY REGIME (trend × vol)</em>
          <table><tbody>
            {rm.cells.map(c => (
              <tr key={c.trend + c.vol}><td>{c.trend} / {c.vol} vol</td>
                <td className="num">{c.n}</td><td className="num">{c.win_rate}%</td>
                <td className={`num ${c.pnl >= 0 ? "up" : "down"}`}>{fmtD(c.pnl)}</td></tr>
            ))}
          </tbody></table>
          {rm.concentration_warning && <div className="bt-bd-warn">⚠ {rm.concentration_warning}</div>}
        </div>
      )}
      {wf && wf.folds && (
        <div className="bt-bd" title="Rolling in-sample / out-of-sample folds. The OOS column is the honest one — it's performance on data that FOLLOWS each window.">
          <em>WALK-FORWARD FOLDS {wf.wf_efficiency != null ? `· WFE ${wf.wf_efficiency}×` : ""}</em>
          <table><tbody>
            <tr><td></td><td className="num">IS P&L</td><td className="num">OOS P&L</td><td className="num">OOS n</td></tr>
            {wf.folds.map(f => (
              <tr key={f.fold}><td>fold {f.fold}</td>
                <td className={`num ${(f.is.total_pnl || 0) >= 0 ? "up" : "down"}`}>{fmtD(f.is.total_pnl || 0)}</td>
                <td className={`num ${(f.oos.total_pnl || 0) >= 0 ? "up" : "down"}`}>{fmtD(f.oos.total_pnl || 0)}</td>
                <td className="num">{f.oos.n_trades ?? "—"}</td></tr>
            ))}
          </tbody></table>
          <div className="bt-bd-note">{wf.verdict}</div>
        </div>
      )}
      {bench && (
        <div className="bt-bd" title="Context, not competition: a short-premium book targets a different risk shape than buy-and-hold. But if SPY beat you with LESS drawdown, the strategy earned nothing for its complexity.">
          <em>BENCHMARKS (same window)</em>
          <table><tbody>
            <tr><td>This strategy</td><td className={`num ${(bench.strategy_return_pct || 0) >= 0 ? "up" : "down"}`}>{bench.strategy_return_pct}%</td><td></td></tr>
            {bench.spy_buy_hold && <tr><td>SPY buy & hold</td><td className="num">{bench.spy_buy_hold.return_pct}%</td><td className="num">DD {bench.spy_buy_hold.max_drawdown_pct}%</td></tr>}
            {bench.t_bill && <tr><td title={bench.t_bill.rate_source}>T-bills (risk-free)</td><td className="num">{bench.t_bill.return_pct}%</td><td></td></tr>}
          </tbody></table>
        </div>
      )}
      {opt && opt.grid && (
        <div className="bt-bd bt-opt" title={opt.plateau ? opt.plateau.note : ""}>
          <em>OPTIMIZER GRID · {opt.n_combos} combos{opt.plateau && opt.plateau.plateau != null ? ` · plateau ${opt.plateau.plateau}` : ""}</em>
          <table><tbody>
            <tr><td>Δ / DTE / PT</td><td className="num">n</td><td className="num">P&L</td><td className="num">Sharpe</td></tr>
            {opt.grid.map((g, i) => {
              const isBest = opt.plateau && opt.plateau.best === g;
              const isRobust = opt.plateau && opt.plateau.robust === g;
              return (
                <tr key={i} className={isRobust ? "bt-opt-robust" : isBest ? "bt-opt-best" : ""}>
                  <td>{g.target_delta}Δ / {g.dte}d{g.profit_take_pct != null ? ` / ${g.profit_take_pct}%` : ""}
                    {isRobust ? " ★robust" : isBest ? " ·peak" : ""}</td>
                  <td className="num">{g.n_trades}</td>
                  <td className={`num ${g.total_pnl >= 0 ? "up" : "down"}`}>{fmtD(g.total_pnl)}</td>
                  <td className="num">{g.sharpe ?? "—"}</td>
                </tr>
              );
            })}
          </tbody></table>
        </div>
      )}
    </div>
  );
}

// ── B4: trade replay ────────────────────────────────────────────────────────
// The lifecycle of ONE trade: its daily modeled value path with every
// management/assignment event marked. Step through trades with ‹ ›.
function BTReplay({ trades, idx, onStep, onClose }) {
  const t = trades[idx];
  if (!t || !t.marks || !t.marks.length) return null;
  const entryCash = t.is_credit ? t.credit : -t.credit;
  const mult = (t.contracts || 1) * 100;
  const pls = t.marks.map(m => (entryCash + m.value) * mult);
  const W = 640, H = 160, PAD = 8;
  const lo = Math.min(0, ...pls, t.pnl), hi = Math.max(0, ...pls, t.pnl);
  const span = Math.max(1, hi - lo);
  const x = i => PAD + (W - 2 * PAD) * (i / Math.max(1, t.marks.length - 1));
  const y = v => H - PAD - (H - 2 * PAD) * ((v - lo) / span);
  const evByDate = {};
  for (const e of (t.events || [])) evByDate[e.date] = e;
  return (
    <div className="bt-replay">
      <div className="bt-replay-head">
        <b>{t.symbol} · {String(t.structure || "").replace(/_/g, " ")} · {t.entry_date} → {t.exit_date}</b>
        <span className={`num ${t.pnl >= 0 ? "up" : "down"}`}>{t.pnl >= 0 ? "+" : ""}{Math.round(t.pnl).toLocaleString()}</span>
        <span className="bt-replay-nav">
          <button className="rr-btn" disabled={idx <= 0} onClick={() => onStep(-1)}>‹ prev</button>
          <span className="num">{idx + 1}/{trades.length}</span>
          <button className="rr-btn" disabled={idx >= trades.length - 1} onClick={() => onStep(1)}>next ›</button>
          <button className="rr-btn" onClick={onClose}>close</button>
        </span>
      </div>
      <div className="bt-replay-meta">
        {t.is_credit ? `credit $${t.credit}/sh` : `debit $${t.credit}/sh`} ×{t.contracts}
        {" · "}legs {(t.legs || []).map(l => `${l.qty > 0 ? "+" : "−"}${l.strike}${l.right[0].toUpperCase()}${l.fill_src === "real" ? "•" : ""}`).join(" ")}
        {" · "}BP ${Math.round(t.bp || 0).toLocaleString()} · exit: {t.reason}
        {(t.legs || []).some(l => l.fill_src === "real") && <span title="• = leg filled at REAL recorded bid/ask"> · • real fill</span>}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="bt-replay-svg"
           aria-label="Open P&L path (modeled daily marks)">
        <line x1={PAD} x2={W - PAD} y1={y(0)} y2={y(0)} className="bt-curve-base" />
        <polyline className={`bt-curve-line ${t.pnl >= 0 ? "up" : "down"}`}
                  points={t.marks.map((m, i) => `${x(i).toFixed(1)},${y(pls[i]).toFixed(1)}`).join(" ")} />
        {t.marks.map((m, i) => evByDate[m.date] ? (
          <circle key={i} cx={x(i)} cy={y(pls[i])} r="4" className="bt-replay-ev">
            <title>{`${m.date}: ${evByDate[m.date].type} — ${evByDate[m.date].detail || ""}`}</title>
          </circle>
        ) : null)}
      </svg>
      <div className="bt-replay-events">
        {(t.events || []).map((e, i) => (
          <span key={i} className={`bt-replay-echip ${e.type}`} title={e.detail || e.type}>
            {e.date.slice(5)} {e.type}
          </span>
        ))}
      </div>
      <div className="bt-bd-note">Daily MODELED marks (option value re-priced off the underlying's close) — the path between marks is unknown; events show the engine's actual decisions.</div>
    </div>
  );
}

// ── B5: live trading plans — research → checklist, NEVER automation ───────
function BTLogTradeForm({ apiFetch, planId, onDone }) {
  const [f, setF] = useState({ ticker: "", type: "put", entry_premium: "", closed_premium: "",
                               qty: -1, opened_at: "", closed_at: "" });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const submit = async () => {
    try {
      const body = { ...f, entry_premium: +f.entry_premium, closed_premium: +f.closed_premium,
                     qty: +f.qty, ticker: f.ticker.toUpperCase(), plan_id: planId };
      const r = await apiFetch("/api/trade_journal", { method: "POST", body: JSON.stringify(body) });
      const d = await r.json();
      if (d.ok) onDone(true); else onDone(false, d.error);
    } catch (e) { onDone(false, String(e)); }
  };
  return (
    <div className="btp-log" title="Log a CLOSED trade against this plan. It lands in the same trade journal as everything else, tagged with the plan — the adherence report splits plan trades from off-plan trades.">
      <input placeholder="Ticker" value={f.ticker} onChange={set("ticker")} style={{ width: 64 }} />
      <select value={f.type} onChange={set("type")}><option>put</option><option>call</option></select>
      <input placeholder="entry prem" type="number" step="0.01" value={f.entry_premium} onChange={set("entry_premium")} style={{ width: 78 }} />
      <input placeholder="closed prem" type="number" step="0.01" value={f.closed_premium} onChange={set("closed_premium")} style={{ width: 78 }} />
      <input placeholder="qty (−=short)" type="number" value={f.qty} onChange={set("qty")} style={{ width: 70 }} />
      <input placeholder="opened YYYY-MM-DD" value={f.opened_at} onChange={set("opened_at")} style={{ width: 130 }} />
      <input placeholder="closed YYYY-MM-DD" value={f.closed_at} onChange={set("closed_at")} style={{ width: 130 }} />
      <button className="rr-btn" onClick={submit}
              disabled={!f.ticker || !f.entry_premium || !f.closed_premium || !f.opened_at || !f.closed_at}>log</button>
    </div>
  );
}

function BTPlans({ apiFetch }) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
  const [logFor, setLogFor] = useState(null);
  const [msg, setMsg] = useState(null);
  const load = () => sharedJson(apiFetch, "/api/plans", 30000).then(d => setData(d)).catch(() => {});
  useEffect(() => { load(); }, []);
  if (!data || !(data.plans || []).length) return null;
  const active = data.plans.filter(p => p.status === "active");
  const adh = data.adherence || {};
  const adhFor = (id) => (adh.plans || []).find(x => x.plan_id === id);
  return (
    <div className="btp">
      <button className="btp-head" onClick={() => setOpen(!open)}
              title="Trading plans deployed from validated backtests: the tested entry checklist, evidence, Monte-Carlo-derived sizing, and how your real journaled trades measure against each plan. A plan is a checklist — this app never places or routes orders.">
        <em>LIVE TRADING PLANS</em>
        <b>{active.length} active</b>
        {adh.off_plan && adh.off_plan.n > 0 && (
          <span className="num" title={adh.note}>
            plan trades {(adh.plans || []).reduce((s, p) => s + p.n, 0)} · off-plan {adh.off_plan.n}
          </span>
        )}
        <span>{open ? "▾" : "▸"}</span>
      </button>
      {open && data.plans.map(p => {
        const a = adhFor(p.id);
        const ev = p.evidence || {};
        return (
          <div key={p.id} className={`btp-plan ${p.status}`}>
            <div className="btp-top">
              <b>{p.label}</b>
              <span className="btp-chips">
                {ev.wf_efficiency != null && <span className="bt-score-chip" title={`Walk-forward efficiency at deploy time. ${ev.wf_verdict || ""}`}><em>WF</em><b>{ev.wf_efficiency}×</b></span>}
                {ev.dsr != null && <span className="bt-score-chip" title={ev.dsr_verdict || ""}><em>DSR</em><b>{ev.dsr}</b></span>}
                {ev.sensitivity_verdict && <span className={`bt-score-chip ${/survives/.test(ev.sensitivity_verdict) ? "ok" : "bad"}`} title={ev.sensitivity_verdict}><em>BAND</em><b>{/survives/.test(ev.sensitivity_verdict) ? "holds" : "flips"}</b></span>}
              </span>
              <span className="btp-actions">
                <button className="rr-btn" onClick={() => setLogFor(logFor === p.id ? null : p.id)}>log trade</button>
                <button className="rr-btn" onClick={async () => {
                  await apiFetch("/api/plans/status", { method: "POST",
                    body: JSON.stringify({ id: p.id, status: p.status === "active" ? "archived" : "active" }) }).catch(() => {});
                  load();
                }}>{p.status === "active" ? "archive" : "reactivate"}</button>
              </span>
            </div>
            <ol className="btp-check" title="The EXACT conditions the backtest required — hold live entries to the same bar. Modeled evidence, not fills.">
              {(p.checklist || []).map((c, i) => <li key={i}>{c}</li>)}
            </ol>
            <div className="btp-meta">
              Tested: {ev.n_trades} trades · {ev.win_rate}% win · {ev.avg_return_on_bp_pct != null ? `${ev.avg_return_on_bp_pct}% avg on BP · ` : ""}max DD {ev.max_drawdown_pct}%
              {p.sizing && p.sizing.suggested_capital_fraction != null && (
                <span title={`${p.sizing.basis} Monte-Carlo P95 drawdown at tested sizing: ${p.sizing.tested_mc_p95_drawdown_pct}%.`}>
                  {" · "}suggested allocation ≤ {(p.sizing.suggested_capital_fraction * 100).toFixed(0)}% of account
                </span>
              )}
              {a && <span className="btp-adh" title="Real journaled trades logged against this plan."> · LIVE: {a.n} trades, {a.win_rate}% win, ${Math.round(a.pnl).toLocaleString()}</span>}
            </div>
            {logFor === p.id && <BTLogTradeForm apiFetch={apiFetch} planId={p.id}
              onDone={(ok, err) => { setMsg(ok ? "logged ✓" : `failed: ${err}`); if (ok) { setLogFor(null); load(); } }} />}
            <div className="btp-noauto">{p.not_automation}</div>
          </div>
        );
      })}
      {msg && <div className="bt-bd-note">{msg}</div>}
    </div>
  );
}

function BacktestCard({ apiFetch }) {
  const [text, setText] = useState(() => { try { return localStorage.getItem("jerry_bt_text") || ""; } catch (e) { return ""; } });
  const [rules, setRules] = useState(null);
  const [parseWarns, setParseWarns] = useState([]);
  const [unparsed, setUnparsed] = useState([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const [showJson, setShowJson] = useState(false);
  const [replayIdx, setReplayIdx] = useState(null);   // B4: trade replay
  const [plansNonce, setPlansNonce] = useState(0);    // B5: refresh plans
  const [pinned, setPinned] = useState(null);         // B4: A/B comparison
  const [jsonDraft, setJsonDraft] = useState("");
  const [showTrades, setShowTrades] = useState(false);
  const pollRef = useRef(null);

  useEffect(() => {
    // Restore the last completed backtest so results survive reloads.
    sharedJson(apiFetch, "/api/backtest/last", 60000)
      .then(d => { if (d && d.metrics && d.metrics.n_trades != null && !result) setResult(d); })
      .catch(() => {});
    // Accept rule sets sent from Pattern Discovery ("→ Backtest"): live via
    // event when this card is mounted, via localStorage when it wasn't yet.
    const onLoad = (e) => { if (e.detail) setRulesAnd(e.detail); };
    window.addEventListener("jerry-bt-load", onLoad);
    try {
      const pre = localStorage.getItem("jerry_bt_prefill");
      if (pre) { localStorage.removeItem("jerry_bt_prefill"); setRulesAnd(JSON.parse(pre)); }
    } catch (e) { /* no-op */ }
    return () => {
      window.removeEventListener("jerry-bt-load", onLoad);
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const setRulesAnd = (r) => { setRules(r); setJsonDraft(JSON.stringify(r, null, 2)); };

  const interpret = () => {
    setErr(null); setBusy(true); setResult(result); setProgress(null);
    try { localStorage.setItem("jerry_bt_text", text); } catch (e) {}
    apiFetch("/api/backtest/parse", { method: "POST", body: JSON.stringify({ text }) })
      .then(r => r.json())
      .then(d => {
        setBusy(false);
        if (d.error) { setErr(d.error); return; }
        setRulesAnd(d.rules); setParseWarns(d.warnings || []); setUnparsed(d.unparsed || []);
      })
      .catch(e => { setBusy(false); setErr(String(e)); });
  };

  const run = () => {
    if (!rules) return;
    setErr(null); setBusy(true); setProgress({ phase: "starting", done: 0, total: 1 });
    apiFetch("/api/backtest/run", { method: "POST", body: JSON.stringify({ rules }) })
      .then(r => r.json())
      .then(d => {
        if (d.error || !d.job) { setBusy(false); setErr(d.error || "could not start"); return; }
        pollRef.current = setInterval(() => {
          apiFetch(`/api/backtest/status?job=${d.job}`).then(r => r.json()).then(s => {
            if (s.progress) setProgress(s.progress);
            if (s.status === "done" || s.status === "error") {
              clearInterval(pollRef.current); pollRef.current = null;
              setBusy(false); setProgress(null);
              if (s.status === "error" || (s.result && s.result.error)) setErr((s.result && s.result.error) || "backtest failed");
              else setResult(s.result);
            }
          }).catch(() => {});
        }, 1500);
      })
      .catch(e => { setBusy(false); setErr(String(e)); });
  };

  const mutate = (fn) => { const r = JSON.parse(JSON.stringify(rules)); fn(r); setRulesAnd(r); };
  const M = (result && result.metrics) || {};
  const fmtD = (v) => (v == null ? "—" : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`);

  return (
    <div className="card bt-card">
      <div className="card-head">
        <div>
          <div className="kicker" title="Describe a strategy in plain English. A deterministic trading grammar (running in this app — your idea never leaves the server) converts it to explicit rules you can inspect and edit before anything runs. Fills happen at the NEXT bar's open (no look-ahead), with modeled spread, slippage, commissions and liquidity checks.">Backtest Lab</div>
          <h2 title="Type an idea like the examples below, press Interpret, review the exact rules it built, then Run.">Test any idea in plain English</h2>
        </div>
      </div>

      <textarea className="bt-input" rows={3} value={text} spellCheck={false}
                placeholder='e.g. "Buy stocks that open down at least 2%, reverse above the opening price, and have volume at least twice the 20 day average. Exit at a 5% profit, a 2% stop loss, or before the market closes."'
                onChange={e => setText(e.target.value)}
                title="Your strategy, in your words. Supported vocabulary: gaps, reversals over/under the open, volume vs average, drawdowns from highs, RSI, moving averages and crosses, new highs/lows, consecutive days, price filters, SPY regime, calls/puts with DTE and strike (ATM / % OTM / delta), profit targets, stops, trailing stops, time and same-day exits, position sizing, symbol lists ('on AAPL, MSFT'), and test windows ('last 2 years')." />
      <BTPlans key={plansNonce} apiFetch={apiFetch} />
      <div className="bt-examples">
        {BT_EXAMPLES.map((ex, i) => (
          <button key={i} className="rr-btn" onClick={() => setText(ex)}
                  title={ex}>example {i + 1}</button>
        ))}
        <button className="rr-btn bt-go" disabled={busy || !text.trim()} onClick={interpret}
                title="Convert the text above into explicit, editable rules. Nothing runs yet — you review the rules first.">
          {busy && !progress ? "interpreting…" : "Interpret →"}
        </button>
      </div>

      {err && <div className="bt-warn bt-err" title="The last action failed — the message comes straight from the engine.">{err}</div>}

      {rules && (
        <div className="bt-rules">
          <div className="bt-sec-title" title="These are the EXACT rules the engine will run — edit any number, remove any rule, or add one. If a clause of your text was not understood it is listed below in amber, not silently guessed.">Rules (review & edit)</div>
          {(parseWarns.length > 0 || unparsed.length > 0) && (
            <div className="bt-warn">
              {parseWarns.map((w, i) => <div key={i} title="A limitation or assumption you should know about before trusting results.">⚠ {w}</div>)}
              {unparsed.map((u, i) => <div key={"u" + i} title="This part of your text matched no known rule pattern. Add it manually below or rephrase.">✎ not understood: “{u}”</div>)}
            </div>
          )}

          <div className="bt-row" title="Long buys first; Short sells first (stocks only — bearish option ideas become long puts). Instrument: stock shares or model-priced options.">
            <span className="bt-lbl">Setup</span>
            <select value={rules.direction} onChange={e => mutate(r => { r.direction = e.target.value; })}>
              <option value="long">Long</option><option value="short">Short</option>
            </select>
            <select value={rules.instrument} onChange={e => mutate(r => {
              r.instrument = e.target.value;
              if (e.target.value === "option" && !r.options) r.options = { right: "call", dte: 30, strike: { mode: "atm" } };
            })}>
              <option value="stock">Stock</option><option value="option">Option (modeled)</option>
            </select>
            {rules.instrument === "option" && rules.options && (
              <>
                <select value={rules.options.right} onChange={e => mutate(r => { r.options.right = e.target.value; })}
                        title="Call or put. Premiums are Black-Scholes estimates from realized volatility — no historical option quotes exist.">
                  <option value="call">calls</option><option value="put">puts</option>
                </select>
                <label className="bt-param" title="Days to expiration at entry."><span>dte</span>
                  <input type="number" value={rules.options.dte} onChange={e => mutate(r => { r.options.dte = +e.target.value || 30; })} /></label>
                <select value={rules.options.strike.mode} onChange={e => mutate(r => { r.options.strike = { mode: e.target.value, value: e.target.value === "atm" ? undefined : (r.options.strike.value || 5) }; })}
                        title="Strike selection: at-the-money, a % out/in of the money, or by delta.">
                  <option value="atm">ATM</option><option value="otm_pct">% OTM</option>
                  <option value="itm_pct">% ITM</option><option value="delta">by delta</option>
                </select>
                {rules.options.strike.mode !== "atm" && (
                  <label className="bt-param"><span>{rules.options.strike.mode === "delta" ? "delta" : "%"}</span>
                    <input type="number" step="any" value={rules.options.strike.value ?? ""}
                           onChange={e => mutate(r => { r.options.strike.value = +e.target.value; })} /></label>
                )}
              </>
            )}
          </div>

          <div className="bt-row" title="Which symbols to test. 'Starred watchlist' uses the tickers you starred in the sidebar; or list symbols explicitly. Universes are capped (50 daily / 15 intraday) to respect data rate limits — a warning tells you if clipped.">
            <span className="bt-lbl">Universe</span>
            <select value={rules.universe.source} onChange={e => mutate(r => { r.universe.source = e.target.value; })}>
              <option value="starred">Starred watchlist</option><option value="symbols">These symbols:</option>
            </select>
            {rules.universe.source === "symbols" && (
              <input className="bt-syms" value={(rules.universe.symbols || []).join(", ")}
                     onChange={e => mutate(r => { r.universe.symbols = e.target.value.split(/[\s,]+/).map(s => s.toUpperCase()).filter(Boolean); })}
                     placeholder="AAPL, MSFT, NVDA" />
            )}
            <label className="bt-param" title="Test window in calendar days back from today. Daily data reaches ~2 years; 1-minute data (intraday rules) ~6 months — the engine clips and warns.">
              <span>window (days)</span>
              <input type="number" value={rules.period_days} onChange={e => mutate(r => { r.period_days = +e.target.value || 365; })} /></label>
          </div>

          <div className="bt-cond-list">
            <div className="bt-sec-sub" title="ALL entry conditions must be true on the same bar. The position is opened at the NEXT bar's open — never on the bar that generated the signal.">Entry — all must be true</div>
            {rules.entry.map((c, i) => (
              <div key={i} className="bt-cond" title={BT_COND_TYPES[c.type] ? BT_COND_TYPES[c.type].label : c.type}>
                <span className="bt-cond-name">{(BT_COND_TYPES[c.type] || {}).label || c.type}</span>
                <BTParamInputs cond={c} onChange={nc => mutate(r => { r.entry[i] = nc; })} />
                <button className="bt-x" onClick={() => mutate(r => { r.entry.splice(i, 1); })} title="Remove this condition.">✕</button>
              </div>
            ))}
            <select className="bt-add" value="" onChange={e => { const t = e.target.value; if (t) mutate(r => { r.entry.push(BT_COND_TYPES[t].make()); }); }}
                    title="Add another entry condition.">
              <option value="">+ add entry condition…</option>
              {Object.entries(BT_COND_TYPES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </div>

          <div className="bt-cond-list">
            <div className="bt-sec-sub" title="First exit hit wins. When a stop and a target land inside the same bar, the engine assumes the STOP filled first — the conservative reading.">Exits — first hit wins (stop assumed first inside a bar)</div>
            {rules.exit.map((c, i) => (
              <div key={i} className="bt-cond">
                <span className="bt-cond-name">{(BT_EXIT_TYPES[c.type] || {}).label || c.type}</span>
                <BTParamInputs cond={c} onChange={nc => mutate(r => { r.exit[i] = nc; })} />
                <button className="bt-x" onClick={() => mutate(r => { r.exit.splice(i, 1); })} title="Remove this exit.">✕</button>
              </div>
            ))}
            <select className="bt-add" value="" onChange={e => { const t = e.target.value; if (t) mutate(r => { r.exit.push(BT_EXIT_TYPES[t].make()); }); }}
                    title="Add another exit.">
              <option value="">+ add exit…</option>
              {Object.entries(BT_EXIT_TYPES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </div>

          <div className="bt-row" title="Position sizing and realism knobs. Slippage is charged on both sides on top of an estimated bid/ask spread by price bucket; trades are SKIPPED (not filled) when the name's average dollar volume is under the liquidity multiple × position size.">
            <span className="bt-lbl">Sizing & costs</span>
            <label className="bt-param"><span>$ / trade</span>
              <input type="number" value={rules.sizing.value} onChange={e => mutate(r => { r.sizing.value = +e.target.value || 10000; })} /></label>
            <label className="bt-param"><span>max positions</span>
              <input type="number" value={rules.sizing.max_positions} onChange={e => mutate(r => { r.sizing.max_positions = +e.target.value || 5; })} /></label>
            <label className="bt-param"><span>slippage (bps)</span>
              <input type="number" value={rules.costs.slippage_bps} onChange={e => mutate(r => { r.costs.slippage_bps = +e.target.value || 0; })} /></label>
            <label className="bt-param"><span>commission $</span>
              <input type="number" step="any" value={rules.costs.commission} onChange={e => mutate(r => { r.costs.commission = +e.target.value || 0; })} /></label>
          </div>

          <div className="bt-actions">
            <button className="rr-btn bt-go" disabled={busy || rules.entry.length === 0} onClick={run}
                    title={rules.entry.length === 0 ? "Add at least one entry condition first." : "Run the backtest with exactly the rules shown above. Intraday rules fetch 1-minute bars and can take a few minutes — progress shows below."}>
              {busy && progress ? "running…" : "Run backtest ▶"}
            </button>
            <button className="rr-btn" onClick={() => setShowJson(!showJson)}
                    title="Power view: the full rule set as JSON. Edit anything and Apply — the structured editors above update to match.">{showJson ? "hide JSON" : "edit as JSON"}</button>
          </div>
          {showJson && (
            <div className="bt-json">
              <textarea rows={12} value={jsonDraft} spellCheck={false} onChange={e => setJsonDraft(e.target.value)} />
              <button className="rr-btn" onClick={() => { try { setRules(JSON.parse(jsonDraft)); setErr(null); } catch (e) { setErr("JSON error: " + e.message); } }}
                      title="Validate and apply the JSON above as the active rule set.">Apply JSON</button>
            </div>
          )}
        </div>
      )}

      {progress && (
        <div className="bt-progress" title="Backtests run on the server in the background; heavy intraday tests fetch one symbol-day of minute bars at a time inside the data provider's rate limit.">
          <div className="bt-progress-bar"><div style={{ width: `${Math.min(100, (progress.done / Math.max(1, progress.total)) * 100)}%` }} /></div>
          <span>{progress.phase} — {progress.done}/{progress.total}</span>
        </div>
      )}

      {result && result.metrics && (
        <div className="bt-results">
          <div className="bt-sec-title" title={`Mode: ${result.mode || "daily"}. Symbols: ${(result.symbols_tested || []).length}. Completed in ${result.elapsed_sec || "?"}s. Metrics are computed on realized trade P&L from $${Number(M.start_equity || 100000).toLocaleString()} starting equity.`}>
            Results
            <button className="rr-btn bt-deploy"
                    onClick={async () => {
                      try {
                        const r = await apiFetch("/api/plans", { method: "POST",
                          body: JSON.stringify({ result, rules_text: text }) });
                        const d = await r.json();
                        setErr(d.ok ? null : (d.error || "plan failed"));
                        if (d.ok) setPlansNonce(n => n + 1);
                      } catch (e) { setErr(String(e)); }
                    }}
                    title="Deploy this validated result as a LIVE TRADING PLAN: the tested entry checklist, management rules, Monte-Carlo-derived sizing guidance, and the validation evidence — then track whether your real journaled trades follow it. A plan is a checklist; this app never places or routes orders.">
              deploy as plan →
            </button>
            <button className="rr-btn bt-pin" onClick={() => setPinned(pinned ? null : { metrics: result.metrics, label: `${result.structure || "run"} · ${new Date().toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}` })}
                    title={pinned ? "Unpin the saved run." : "Pin this run's headline metrics, then run a variation — the next result shows a side-by-side delta against the pin."}>
              {pinned ? "unpin A/B" : "pin for A/B"}
            </button>
            <span className="bt-modeled-badge"
                  title={`These are SIMULATED results, not historical fills. Model assumptions:\n• ${((result.modeled && result.modeled.assumptions) || ["Fills at the next bar's open; modeled spread, slippage and commissions"]).join("\n• ")}`}>
              {result.modeled && result.modeled.option_premiums ? "MODELED — synthetic option prices" : "MODELED — simulated fills"}
            </span>
          </div>
          {(result.warnings || []).length > 0 && (
            <div className="bt-warn">
              {result.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
            </div>
          )}
          {result.sensitivity && (
            <div className={`bt-sens ${/FLIPS|EVERY/.test(result.sensitivity.verdict) ? "bad" : "ok"}`}
                 title={`The identical run repeated at 0.85× and 1.15× the modeled IV. Pessimistic P&L $${result.sensitivity.low.total_pnl.toLocaleString()} · base $${result.sensitivity.base_total_pnl.toLocaleString()} · optimistic $${result.sensitivity.high.total_pnl.toLocaleString()}. If the sign only holds at one end, the "edge" is a premium assumption.`}>
              <b>PREMIUM SENSITIVITY</b> {result.sensitivity.verdict}
              <span className="num"> · ${result.sensitivity.low.total_pnl.toLocaleString()} / ${result.sensitivity.base_total_pnl.toLocaleString()} / ${result.sensitivity.high.total_pnl.toLocaleString()}</span>
            </div>
          )}
          {M.real_fill_pct != null && (
            <div className="bt-fillsrc" title={`Entry fills priced from REAL recorded bid/ask (the app snapshots every chain it touches, once per symbol per day) vs the model. Real coverage grows automatically with normal app use. Sources: ${JSON.stringify(M.entry_fill_sources)}.`}>
              {M.real_fill_pct > 0
                ? `${M.real_fill_pct}% of entry fills priced from REAL recorded quotes`
                : "All fills model-priced — real-quote coverage builds as the app records daily chain snapshots"}
            </div>
          )}
          <BTScorecard result={result} />
          {pinned && pinned.metrics && (
            <div className="bt-bd bt-ab" title={`Side-by-side vs the pinned run (${pinned.label}). Positive delta = the current run is better on that metric.`}>
              <em>A/B vs pinned · {pinned.label}</em>
              <table><tbody>
                {["total_pnl", "win_rate", "max_drawdown_pct", "sharpe", "avg_return_on_bp_pct"].map(k => {
                  const a = pinned.metrics[k], b = M[k];
                  if (a == null && b == null) return null;
                  const d = (b != null && a != null) ? +(b - a).toFixed(2) : null;
                  const betterUp = k !== "max_drawdown_pct";
                  return (
                    <tr key={k}><td>{k.replace(/_/g, " ")}</td>
                      <td className="num">{a ?? "—"}</td>
                      <td className="num">{b ?? "—"}</td>
                      <td className={`num ${d == null ? "" : (betterUp ? d >= 0 : d <= 0) ? "up" : "down"}`}>{d == null ? "" : `${d >= 0 ? "+" : ""}${d}`}</td></tr>
                  );
                })}
              </tbody></table>
            </div>
          )}
          <div className="bt-tiles">
            <div className="bt-tile" title="Sum of all realized trade P&L as a % of starting equity ($100k), after modeled costs."><span>Total return</span><b className={M.total_return_pct >= 0 ? "up" : "down"}>{M.total_return_pct}%</b></div>
            <div className="bt-tile" title="Realized profit and loss in dollars, after spread, slippage and commissions."><span>Total P&L</span><b className={M.total_pnl >= 0 ? "up" : "down"}>{fmtD(M.total_pnl)}</b></div>
            <div className="bt-tile" title="Number of closed trades in the test. Skipped candidates (liquidity, max positions) are counted separately below."><span>Trades</span><b>{M.n_trades}</b></div>
            <div className="bt-tile" title="Share of trades that closed with a profit."><span>Win rate</span><b>{M.win_rate}%</b></div>
            <div className="bt-tile" title="Average dollar profit across winning trades."><span>Avg gain</span><b className="up">{fmtD(M.avg_gain)}</b></div>
            <div className="bt-tile" title="Average dollar loss across losing trades."><span>Avg loss</span><b className="down">{fmtD(M.avg_loss)}</b></div>
            <div className="bt-tile" title="Gross profits ÷ gross losses. Above 1.0 = the wins outweigh the losses; below 1.0 the strategy loses money overall."><span>Profit factor</span><b>{M.profit_factor == null ? "∞" : M.profit_factor}</b></div>
            <div className="bt-tile" title="Deepest peak-to-trough drop of the equity curve, as a % of the peak."><span>Max drawdown</span><b className="down">{M.max_drawdown_pct}%</b></div>
            <div className="bt-tile" title="Expected $ per trade: win-rate × avg gain − loss-rate × avg loss. Positive = the edge survives its costs."><span>Expectancy</span><b className={M.expectancy >= 0 ? "up" : "down"}>{fmtD(M.expectancy)}</b></div>
            {M.avg_return_on_bp_pct != null && (
              <div className="bt-tile" title="Average per-trade return on the buying power the position actually tied up (broker-formula BP). The honest efficiency number for premium selling."><span>Avg ret on BP</span><b className={M.avg_return_on_bp_pct >= 0 ? "up" : "down"}>{M.avg_return_on_bp_pct}%</b></div>
            )}
            {M.assignments != null && (
              <div className="bt-tile" title="Trades that ended by assignment (deep ITM or ex-div early exercise) — modeled, labeled in each trade's lifecycle log."><span>Assignments</span><b>{M.assignments}</b></div>
            )}
          </div>
          <BTEquityCurve curve={result.equity_curve} start={M.start_equity || 100000} />
          <BTDrawdown curve={result.equity_curve} />
          <div className="bt-tear">
            <BTMonthly curve={result.equity_curve} />
            <BTHistogram trades={result.trades} />
            <BTMaeMfe trades={result.trades} />
          </div>
          <BTBreakdowns result={result} />
          {replayIdx != null && (
            <BTReplay trades={result.trades || []} idx={replayIdx}
                      onStep={(d) => setReplayIdx(i => Math.max(0, Math.min((result.trades || []).length - 1, i + d)))}
                      onClose={() => setReplayIdx(null)} />
          )}
          <div className="bt-detail">
            {result.best_trade && (
              <span title={`Best single trade: ${result.best_trade.symbol} ${result.best_trade.entry_date} → ${result.best_trade.exit_date} (${result.best_trade.reason}).`}>
                best <b className="up">{result.best_trade.symbol} {fmtD(result.best_trade.pnl)}</b>
              </span>
            )}
            {result.worst_trade && (
              <span title={`Worst single trade: ${result.worst_trade.symbol} ${result.worst_trade.entry_date} → ${result.worst_trade.exit_date} (${result.worst_trade.reason}).`}>
                worst <b className="down">{result.worst_trade.symbol} {fmtD(result.worst_trade.pnl)}</b>
              </span>
            )}
            <span title="Entry candidates skipped because the stock's average daily dollar volume was too small to absorb the position realistically — an unavailable fill, not a loss.">skipped (liquidity): <b>{result.skipped_no_liquidity || 0}</b></span>
            <span title="Signals ignored because the maximum number of simultaneous open positions was already reached.">skipped (max positions): <b>{result.skipped_max_positions || 0}</b></span>
          </div>
          {result.by_regime && Object.keys(result.by_regime).length > 0 && (
            <table className="bt-regime" title="The same trades bucketed by the S&P 500's condition on entry day (SPY vs its 50/200-day averages): does the edge only exist in one type of market?">
              <thead><tr><th>Market condition</th><th>Trades</th><th>Win rate</th><th>P&L</th></tr></thead>
              <tbody>
                {Object.entries(result.by_regime).map(([r, d]) => (
                  <tr key={r}><td>{r}</td><td>{d.n}</td><td>{d.win_rate}%</td>
                    <td className={d.pnl >= 0 ? "up" : "down"}>{fmtD(d.pnl)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          <button className="rr-btn" onClick={() => setShowTrades(!showTrades)}
                  title="Every closed trade with entry/exit dates, prices, exit reason and P&L (most recent 400).">
            {showTrades ? "hide trades" : `show trades (${(result.trades || []).length})`}</button>
          {showTrades && (
            <div className="bt-trades-wrap">
              <table className="bt-trades">
                <thead><tr><th>Sym</th><th>In</th><th>Out</th><th>Entry</th><th>Exit</th><th>Why</th><th>P&L</th><th>%</th></tr></thead>
                <tbody>
                  {(result.trades || []).slice().reverse().map((t, i, arr) => (
                    <tr key={i} className={t.marks && t.marks.length ? "bt-tr-replay" : ""}
                        title={t.marks && t.marks.length ? "Click to replay this trade's daily lifecycle." : undefined}
                        onClick={() => { if (t.marks && t.marks.length) setReplayIdx((result.trades || []).indexOf(t)); }}>
                      <td title={t.structure && t.legs ? t.legs.map(l => `${l.qty > 0 ? "+" : "−"}${l.strike}${l.right[0].toUpperCase()}@${l.entry_px}`).join(" ") : undefined}>
                        {t.symbol}
                        {t.option ? ` ${t.option.strike}${t.option.right[0].toUpperCase()}` : ""}
                        {t.structure ? ` ${String(t.structure).replace(/_/g, " ")}${t.contracts > 1 ? ` ×${t.contracts}` : ""}` : ""}
                      </td>
                      <td>{t.entry_date}</td><td>{t.exit_date}</td>
                      {t.structure
                        ? <td colSpan="2" title="Net credit received (per share) for the structure; legs and fills in the row tooltip.">{t.is_credit ? "cr" : "db"} ${t.credit}</td>
                        : <><td>${t.entry_px}</td><td>${t.exit_px}</td></>}
                      <td>{t.reason}</td>
                      <td className={t.pnl >= 0 ? "up" : "down"}>{fmtD(t.pnl)}</td>
                      <td className={t.pnl >= 0 ? "up" : "down"}>{t.pnl_pct}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { BacktestCard: React.memo(BacktestCard) });
