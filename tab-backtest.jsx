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
                  {(result.trades || []).slice().reverse().map((t, i) => (
                    <tr key={i}>
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
