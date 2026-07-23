(function () {
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
  gap_pct: {
    label: "Gap at open vs prior close (%)",
    make: () => ({
      type: "gap_pct",
      op: "<=",
      value: -2
    })
  },
  cross_above_open: {
    label: "Crosses back ABOVE the open (intraday)",
    make: () => ({
      type: "cross_above_open"
    })
  },
  cross_below_open: {
    label: "Crosses back BELOW the open (intraday)",
    make: () => ({
      type: "cross_below_open"
    })
  },
  rel_volume: {
    label: "Volume ≥ N × average",
    make: () => ({
      type: "rel_volume",
      mult: 2,
      lookback: 20
    })
  },
  drawdown_from_high: {
    label: "Drawdown from high (%)",
    make: () => ({
      type: "drawdown_from_high",
      pct: 30,
      lookback: 252
    })
  },
  rsi: {
    label: "RSI",
    make: () => ({
      type: "rsi",
      period: 14,
      op: "<=",
      value: 30
    })
  },
  sma_cross: {
    label: "MA cross",
    make: () => ({
      type: "sma_cross",
      fast: 20,
      slow: 50,
      direction: "up"
    })
  },
  price_vs_sma: {
    label: "Price vs moving average",
    make: () => ({
      type: "price_vs_sma",
      op: ">=",
      period: 200
    })
  },
  new_high: {
    label: "New N-day high",
    make: () => ({
      type: "new_high",
      lookback: 20
    })
  },
  new_low: {
    label: "New N-day low",
    make: () => ({
      type: "new_low",
      lookback: 20
    })
  },
  consec_down: {
    label: "N consecutive down days",
    make: () => ({
      type: "consec_down",
      n: 3
    })
  },
  consec_up: {
    label: "N consecutive up days",
    make: () => ({
      type: "consec_up",
      n: 3
    })
  },
  day_change_pct: {
    label: "Change on the day (%)",
    make: () => ({
      type: "day_change_pct",
      op: "<=",
      value: -3
    })
  },
  move_pct: {
    label: "Move over trailing N days (%)",
    make: () => ({
      type: "move_pct",
      days: 5,
      op: ">=",
      value: 10
    })
  },
  price_abs: {
    label: "Price filter ($)",
    make: () => ({
      type: "price_abs",
      op: ">=",
      value: 20
    })
  },
  market_regime: {
    label: "SPY regime filter",
    make: () => ({
      type: "market_regime",
      regime: "uptrend"
    })
  }
};
const BT_EXIT_TYPES = {
  profit_pct: {
    label: "Profit target (%)",
    make: () => ({
      type: "profit_pct",
      value: 5
    })
  },
  stop_pct: {
    label: "Stop loss (%)",
    make: () => ({
      type: "stop_pct",
      value: 2
    })
  },
  trailing_stop_pct: {
    label: "Trailing stop (%)",
    make: () => ({
      type: "trailing_stop_pct",
      value: 8
    })
  },
  time_days: {
    label: "Time exit (trading days)",
    make: () => ({
      type: "time_days",
      value: 10
    })
  },
  same_day_close: {
    label: "Exit by the close (same day)",
    make: () => ({
      type: "same_day_close"
    })
  },
  hold_to_expiry: {
    label: "Hold option to expiry",
    make: () => ({
      type: "hold_to_expiry"
    })
  }
};
const BT_EXAMPLES = ["Buy stocks that open down at least 2%, reverse above the opening price, and have volume at least twice the 20 day average. Exit at a 5% profit, a 2% stop loss, or before the market closes.", "Buy stock after a 30% drawdown from a recent high. Hold for 15 days with a 10% trailing stop. $5,000 per trade on AAPL, MSFT and NVDA.", "Buy 30 dte calls at the money when RSI 14 below 30 and price above the 200 day moving average, only when SPY is in an uptrend. 50% profit target, 25% stop loss."];
function BTParamInputs({
  cond,
  onChange
}) {
  // Generic param editor: every non-label key becomes a small typed input,
  // so any rule the parser (or the user via JSON) produces stays editable.
  const keys = Object.keys(cond).filter(k => k !== "type" && k !== "label");
  return /*#__PURE__*/React.createElement("span", {
    className: "bt-params"
  }, keys.map(k => /*#__PURE__*/React.createElement("label", {
    key: k,
    className: "bt-param",
    title: `Edit the '${k}' parameter of this rule.`
  }, /*#__PURE__*/React.createElement("span", null, k), k === "op" ? /*#__PURE__*/React.createElement("select", {
    value: cond[k],
    onChange: e => onChange({
      ...cond,
      [k]: e.target.value
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "<="
  }, "\u2264"), /*#__PURE__*/React.createElement("option", {
    value: ">="
  }, "\u2265")) : k === "direction" || k === "regime" ? /*#__PURE__*/React.createElement("select", {
    value: cond[k],
    onChange: e => onChange({
      ...cond,
      [k]: e.target.value
    })
  }, k === "direction" ? [/*#__PURE__*/React.createElement("option", {
    key: "u",
    value: "up"
  }, "up"), /*#__PURE__*/React.createElement("option", {
    key: "d",
    value: "down"
  }, "down")] : [/*#__PURE__*/React.createElement("option", {
    key: "u",
    value: "uptrend"
  }, "uptrend"), /*#__PURE__*/React.createElement("option", {
    key: "d",
    value: "downtrend"
  }, "downtrend"), /*#__PURE__*/React.createElement("option", {
    key: "c",
    value: "chop"
  }, "chop")]) : /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "any",
    value: cond[k] ?? "",
    onChange: e => onChange({
      ...cond,
      [k]: e.target.value === "" ? null : +e.target.value
    })
  }))));
}
function BTEquityCurve({
  curve,
  start
}) {
  if (!curve || curve.length < 2) return null;
  const W = 640,
    H = 150,
    PAD = 6;
  const vals = curve.map(p => p.equity);
  const lo = Math.min(start, ...vals),
    hi = Math.max(start, ...vals);
  const span = Math.max(1e-9, hi - lo);
  const x = i => PAD + (W - 2 * PAD) * (i / (curve.length - 1));
  const y = v => H - PAD - (H - 2 * PAD) * ((v - lo) / span);
  const pts = curve.map((p, i) => `${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
  const up = vals[vals.length - 1] >= start;
  return /*#__PURE__*/React.createElement("div", {
    className: "bt-curve",
    title: `Equity curve: $${Math.round(start).toLocaleString()} starting equity, stepped at each trade exit (realized P&L). ${curve.length} closed trades from ${curve[0].date} to ${curve[curve.length - 1].date}.`
  }, /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${W} ${H}`,
    preserveAspectRatio: "none"
  }, /*#__PURE__*/React.createElement("line", {
    x1: PAD,
    x2: W - PAD,
    y1: y(start),
    y2: y(start),
    className: "bt-curve-base"
  }), /*#__PURE__*/React.createElement("polyline", {
    points: pts,
    className: `bt-curve-line ${up ? "up" : "down"}`
  })), /*#__PURE__*/React.createElement("div", {
    className: "bt-curve-lbls"
  }, /*#__PURE__*/React.createElement("span", null, curve[0].date), /*#__PURE__*/React.createElement("span", {
    className: up ? "up" : "down"
  }, "$", Math.round(vals[vals.length - 1]).toLocaleString()), /*#__PURE__*/React.createElement("span", null, curve[curve.length - 1].date)));
}
function BacktestCard({
  apiFetch
}) {
  const [text, setText] = useState(() => {
    try {
      return localStorage.getItem("jerry_bt_text") || "";
    } catch (e) {
      return "";
    }
  });
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
    sharedJson(apiFetch, "/api/backtest/last", 60000).then(d => {
      if (d && d.metrics && d.metrics.n_trades != null && !result) setResult(d);
    }).catch(() => {});
    // Accept rule sets sent from Pattern Discovery ("→ Backtest"): live via
    // event when this card is mounted, via localStorage when it wasn't yet.
    const onLoad = e => {
      if (e.detail) setRulesAnd(e.detail);
    };
    window.addEventListener("jerry-bt-load", onLoad);
    try {
      const pre = localStorage.getItem("jerry_bt_prefill");
      if (pre) {
        localStorage.removeItem("jerry_bt_prefill");
        setRulesAnd(JSON.parse(pre));
      }
    } catch (e) {/* no-op */}
    return () => {
      window.removeEventListener("jerry-bt-load", onLoad);
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);
  const setRulesAnd = r => {
    setRules(r);
    setJsonDraft(JSON.stringify(r, null, 2));
  };
  const interpret = () => {
    setErr(null);
    setBusy(true);
    setResult(result);
    setProgress(null);
    try {
      localStorage.setItem("jerry_bt_text", text);
    } catch (e) {}
    apiFetch("/api/backtest/parse", {
      method: "POST",
      body: JSON.stringify({
        text
      })
    }).then(r => r.json()).then(d => {
      setBusy(false);
      if (d.error) {
        setErr(d.error);
        return;
      }
      setRulesAnd(d.rules);
      setParseWarns(d.warnings || []);
      setUnparsed(d.unparsed || []);
    }).catch(e => {
      setBusy(false);
      setErr(String(e));
    });
  };
  const run = () => {
    if (!rules) return;
    setErr(null);
    setBusy(true);
    setProgress({
      phase: "starting",
      done: 0,
      total: 1
    });
    apiFetch("/api/backtest/run", {
      method: "POST",
      body: JSON.stringify({
        rules
      })
    }).then(r => r.json()).then(d => {
      if (d.error || !d.job) {
        setBusy(false);
        setErr(d.error || "could not start");
        return;
      }
      pollRef.current = setInterval(() => {
        apiFetch(`/api/backtest/status?job=${d.job}`).then(r => r.json()).then(s => {
          if (s.progress) setProgress(s.progress);
          if (s.status === "done" || s.status === "error") {
            clearInterval(pollRef.current);
            pollRef.current = null;
            setBusy(false);
            setProgress(null);
            if (s.status === "error" || s.result && s.result.error) setErr(s.result && s.result.error || "backtest failed");else setResult(s.result);
          }
        }).catch(() => {});
      }, 1500);
    }).catch(e => {
      setBusy(false);
      setErr(String(e));
    });
  };
  const mutate = fn => {
    const r = JSON.parse(JSON.stringify(rules));
    fn(r);
    setRulesAnd(r);
  };
  const M = result && result.metrics || {};
  const fmtD = v => v == null ? "—" : `$${Number(v).toLocaleString(undefined, {
    maximumFractionDigits: 0
  })}`;
  return /*#__PURE__*/React.createElement("div", {
    className: "card bt-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker",
    title: "Describe a strategy in plain English. A deterministic trading grammar (running in this app \u2014 your idea never leaves the server) converts it to explicit rules you can inspect and edit before anything runs. Fills happen at the NEXT bar's open (no look-ahead), with modeled spread, slippage, commissions and liquidity checks."
  }, "Backtest Lab"), /*#__PURE__*/React.createElement("h2", {
    title: "Type an idea like the examples below, press Interpret, review the exact rules it built, then Run."
  }, "Test any idea in plain English"))), /*#__PURE__*/React.createElement("textarea", {
    className: "bt-input",
    rows: 3,
    value: text,
    spellCheck: false,
    placeholder: "e.g. \"Buy stocks that open down at least 2%, reverse above the opening price, and have volume at least twice the 20 day average. Exit at a 5% profit, a 2% stop loss, or before the market closes.\"",
    onChange: e => setText(e.target.value),
    title: "Your strategy, in your words. Supported vocabulary: gaps, reversals over/under the open, volume vs average, drawdowns from highs, RSI, moving averages and crosses, new highs/lows, consecutive days, price filters, SPY regime, calls/puts with DTE and strike (ATM / % OTM / delta), profit targets, stops, trailing stops, time and same-day exits, position sizing, symbol lists ('on AAPL, MSFT'), and test windows ('last 2 years')."
  }), /*#__PURE__*/React.createElement("div", {
    className: "bt-examples"
  }, BT_EXAMPLES.map((ex, i) => /*#__PURE__*/React.createElement("button", {
    key: i,
    className: "rr-btn",
    onClick: () => setText(ex),
    title: ex
  }, "example ", i + 1)), /*#__PURE__*/React.createElement("button", {
    className: "rr-btn bt-go",
    disabled: busy || !text.trim(),
    onClick: interpret,
    title: "Convert the text above into explicit, editable rules. Nothing runs yet \u2014 you review the rules first."
  }, busy && !progress ? "interpreting…" : "Interpret →")), err && /*#__PURE__*/React.createElement("div", {
    className: "bt-warn bt-err",
    title: "The last action failed \u2014 the message comes straight from the engine."
  }, err), rules && /*#__PURE__*/React.createElement("div", {
    className: "bt-rules"
  }, /*#__PURE__*/React.createElement("div", {
    className: "bt-sec-title",
    title: "These are the EXACT rules the engine will run \u2014 edit any number, remove any rule, or add one. If a clause of your text was not understood it is listed below in amber, not silently guessed."
  }, "Rules (review & edit)"), (parseWarns.length > 0 || unparsed.length > 0) && /*#__PURE__*/React.createElement("div", {
    className: "bt-warn"
  }, parseWarns.map((w, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    title: "A limitation or assumption you should know about before trusting results."
  }, "\u26A0 ", w)), unparsed.map((u, i) => /*#__PURE__*/React.createElement("div", {
    key: "u" + i,
    title: "This part of your text matched no known rule pattern. Add it manually below or rephrase."
  }, "\u270E not understood: \u201C", u, "\u201D"))), /*#__PURE__*/React.createElement("div", {
    className: "bt-row",
    title: "Long buys first; Short sells first (stocks only \u2014 bearish option ideas become long puts). Instrument: stock shares or model-priced options."
  }, /*#__PURE__*/React.createElement("span", {
    className: "bt-lbl"
  }, "Setup"), /*#__PURE__*/React.createElement("select", {
    value: rules.direction,
    onChange: e => mutate(r => {
      r.direction = e.target.value;
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "long"
  }, "Long"), /*#__PURE__*/React.createElement("option", {
    value: "short"
  }, "Short")), /*#__PURE__*/React.createElement("select", {
    value: rules.instrument,
    onChange: e => mutate(r => {
      r.instrument = e.target.value;
      if (e.target.value === "option" && !r.options) r.options = {
        right: "call",
        dte: 30,
        strike: {
          mode: "atm"
        }
      };
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "stock"
  }, "Stock"), /*#__PURE__*/React.createElement("option", {
    value: "option"
  }, "Option (modeled)")), rules.instrument === "option" && rules.options && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("select", {
    value: rules.options.right,
    onChange: e => mutate(r => {
      r.options.right = e.target.value;
    }),
    title: "Call or put. Premiums are Black-Scholes estimates from realized volatility \u2014 no historical option quotes exist."
  }, /*#__PURE__*/React.createElement("option", {
    value: "call"
  }, "calls"), /*#__PURE__*/React.createElement("option", {
    value: "put"
  }, "puts")), /*#__PURE__*/React.createElement("label", {
    className: "bt-param",
    title: "Days to expiration at entry."
  }, /*#__PURE__*/React.createElement("span", null, "dte"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    value: rules.options.dte,
    onChange: e => mutate(r => {
      r.options.dte = +e.target.value || 30;
    })
  })), /*#__PURE__*/React.createElement("select", {
    value: rules.options.strike.mode,
    onChange: e => mutate(r => {
      r.options.strike = {
        mode: e.target.value,
        value: e.target.value === "atm" ? undefined : r.options.strike.value || 5
      };
    }),
    title: "Strike selection: at-the-money, a % out/in of the money, or by delta."
  }, /*#__PURE__*/React.createElement("option", {
    value: "atm"
  }, "ATM"), /*#__PURE__*/React.createElement("option", {
    value: "otm_pct"
  }, "% OTM"), /*#__PURE__*/React.createElement("option", {
    value: "itm_pct"
  }, "% ITM"), /*#__PURE__*/React.createElement("option", {
    value: "delta"
  }, "by delta")), rules.options.strike.mode !== "atm" && /*#__PURE__*/React.createElement("label", {
    className: "bt-param"
  }, /*#__PURE__*/React.createElement("span", null, rules.options.strike.mode === "delta" ? "delta" : "%"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "any",
    value: rules.options.strike.value ?? "",
    onChange: e => mutate(r => {
      r.options.strike.value = +e.target.value;
    })
  })))), /*#__PURE__*/React.createElement("div", {
    className: "bt-row",
    title: "Which symbols to test. 'Starred watchlist' uses the tickers you starred in the sidebar; or list symbols explicitly. Universes are capped (50 daily / 15 intraday) to respect data rate limits \u2014 a warning tells you if clipped."
  }, /*#__PURE__*/React.createElement("span", {
    className: "bt-lbl"
  }, "Universe"), /*#__PURE__*/React.createElement("select", {
    value: rules.universe.source,
    onChange: e => mutate(r => {
      r.universe.source = e.target.value;
    })
  }, /*#__PURE__*/React.createElement("option", {
    value: "starred"
  }, "Starred watchlist"), /*#__PURE__*/React.createElement("option", {
    value: "symbols"
  }, "These symbols:")), rules.universe.source === "symbols" && /*#__PURE__*/React.createElement("input", {
    className: "bt-syms",
    value: (rules.universe.symbols || []).join(", "),
    onChange: e => mutate(r => {
      r.universe.symbols = e.target.value.split(/[\s,]+/).map(s => s.toUpperCase()).filter(Boolean);
    }),
    placeholder: "AAPL, MSFT, NVDA"
  }), /*#__PURE__*/React.createElement("label", {
    className: "bt-param",
    title: "Test window in calendar days back from today. Daily data reaches ~2 years; 1-minute data (intraday rules) ~6 months \u2014 the engine clips and warns."
  }, /*#__PURE__*/React.createElement("span", null, "window (days)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    value: rules.period_days,
    onChange: e => mutate(r => {
      r.period_days = +e.target.value || 365;
    })
  }))), /*#__PURE__*/React.createElement("div", {
    className: "bt-cond-list"
  }, /*#__PURE__*/React.createElement("div", {
    className: "bt-sec-sub",
    title: "ALL entry conditions must be true on the same bar. The position is opened at the NEXT bar's open \u2014 never on the bar that generated the signal."
  }, "Entry \u2014 all must be true"), rules.entry.map((c, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "bt-cond",
    title: BT_COND_TYPES[c.type] ? BT_COND_TYPES[c.type].label : c.type
  }, /*#__PURE__*/React.createElement("span", {
    className: "bt-cond-name"
  }, (BT_COND_TYPES[c.type] || {}).label || c.type), /*#__PURE__*/React.createElement(BTParamInputs, {
    cond: c,
    onChange: nc => mutate(r => {
      r.entry[i] = nc;
    })
  }), /*#__PURE__*/React.createElement("button", {
    className: "bt-x",
    onClick: () => mutate(r => {
      r.entry.splice(i, 1);
    }),
    title: "Remove this condition."
  }, "\u2715"))), /*#__PURE__*/React.createElement("select", {
    className: "bt-add",
    value: "",
    onChange: e => {
      const t = e.target.value;
      if (t) mutate(r => {
        r.entry.push(BT_COND_TYPES[t].make());
      });
    },
    title: "Add another entry condition."
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "+ add entry condition\u2026"), Object.entries(BT_COND_TYPES).map(([k, v]) => /*#__PURE__*/React.createElement("option", {
    key: k,
    value: k
  }, v.label)))), /*#__PURE__*/React.createElement("div", {
    className: "bt-cond-list"
  }, /*#__PURE__*/React.createElement("div", {
    className: "bt-sec-sub",
    title: "First exit hit wins. When a stop and a target land inside the same bar, the engine assumes the STOP filled first \u2014 the conservative reading."
  }, "Exits \u2014 first hit wins (stop assumed first inside a bar)"), rules.exit.map((c, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "bt-cond"
  }, /*#__PURE__*/React.createElement("span", {
    className: "bt-cond-name"
  }, (BT_EXIT_TYPES[c.type] || {}).label || c.type), /*#__PURE__*/React.createElement(BTParamInputs, {
    cond: c,
    onChange: nc => mutate(r => {
      r.exit[i] = nc;
    })
  }), /*#__PURE__*/React.createElement("button", {
    className: "bt-x",
    onClick: () => mutate(r => {
      r.exit.splice(i, 1);
    }),
    title: "Remove this exit."
  }, "\u2715"))), /*#__PURE__*/React.createElement("select", {
    className: "bt-add",
    value: "",
    onChange: e => {
      const t = e.target.value;
      if (t) mutate(r => {
        r.exit.push(BT_EXIT_TYPES[t].make());
      });
    },
    title: "Add another exit."
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "+ add exit\u2026"), Object.entries(BT_EXIT_TYPES).map(([k, v]) => /*#__PURE__*/React.createElement("option", {
    key: k,
    value: k
  }, v.label)))), /*#__PURE__*/React.createElement("div", {
    className: "bt-row",
    title: "Position sizing and realism knobs. Slippage is charged on both sides on top of an estimated bid/ask spread by price bucket; trades are SKIPPED (not filled) when the name's average dollar volume is under the liquidity multiple \xD7 position size."
  }, /*#__PURE__*/React.createElement("span", {
    className: "bt-lbl"
  }, "Sizing & costs"), /*#__PURE__*/React.createElement("label", {
    className: "bt-param"
  }, /*#__PURE__*/React.createElement("span", null, "$ / trade"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    value: rules.sizing.value,
    onChange: e => mutate(r => {
      r.sizing.value = +e.target.value || 10000;
    })
  })), /*#__PURE__*/React.createElement("label", {
    className: "bt-param"
  }, /*#__PURE__*/React.createElement("span", null, "max positions"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    value: rules.sizing.max_positions,
    onChange: e => mutate(r => {
      r.sizing.max_positions = +e.target.value || 5;
    })
  })), /*#__PURE__*/React.createElement("label", {
    className: "bt-param"
  }, /*#__PURE__*/React.createElement("span", null, "slippage (bps)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    value: rules.costs.slippage_bps,
    onChange: e => mutate(r => {
      r.costs.slippage_bps = +e.target.value || 0;
    })
  })), /*#__PURE__*/React.createElement("label", {
    className: "bt-param"
  }, /*#__PURE__*/React.createElement("span", null, "commission $"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "any",
    value: rules.costs.commission,
    onChange: e => mutate(r => {
      r.costs.commission = +e.target.value || 0;
    })
  }))), /*#__PURE__*/React.createElement("div", {
    className: "bt-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "rr-btn bt-go",
    disabled: busy || rules.entry.length === 0,
    onClick: run,
    title: rules.entry.length === 0 ? "Add at least one entry condition first." : "Run the backtest with exactly the rules shown above. Intraday rules fetch 1-minute bars and can take a few minutes — progress shows below."
  }, busy && progress ? "running…" : "Run backtest ▶"), /*#__PURE__*/React.createElement("button", {
    className: "rr-btn",
    onClick: () => setShowJson(!showJson),
    title: "Power view: the full rule set as JSON. Edit anything and Apply \u2014 the structured editors above update to match."
  }, showJson ? "hide JSON" : "edit as JSON")), showJson && /*#__PURE__*/React.createElement("div", {
    className: "bt-json"
  }, /*#__PURE__*/React.createElement("textarea", {
    rows: 12,
    value: jsonDraft,
    spellCheck: false,
    onChange: e => setJsonDraft(e.target.value)
  }), /*#__PURE__*/React.createElement("button", {
    className: "rr-btn",
    onClick: () => {
      try {
        setRules(JSON.parse(jsonDraft));
        setErr(null);
      } catch (e) {
        setErr("JSON error: " + e.message);
      }
    },
    title: "Validate and apply the JSON above as the active rule set."
  }, "Apply JSON"))), progress && /*#__PURE__*/React.createElement("div", {
    className: "bt-progress",
    title: "Backtests run on the server in the background; heavy intraday tests fetch one symbol-day of minute bars at a time inside the data provider's rate limit."
  }, /*#__PURE__*/React.createElement("div", {
    className: "bt-progress-bar"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: `${Math.min(100, progress.done / Math.max(1, progress.total) * 100)}%`
    }
  })), /*#__PURE__*/React.createElement("span", null, progress.phase, " \u2014 ", progress.done, "/", progress.total)), result && result.metrics && /*#__PURE__*/React.createElement("div", {
    className: "bt-results"
  }, /*#__PURE__*/React.createElement("div", {
    className: "bt-sec-title",
    title: `Mode: ${result.mode || "daily"}. Symbols: ${(result.symbols_tested || []).length}. Completed in ${result.elapsed_sec || "?"}s. Metrics are computed on realized trade P&L from $${Number(M.start_equity || 100000).toLocaleString()} starting equity.`
  }, "Results", /*#__PURE__*/React.createElement("span", {
    className: "bt-modeled-badge",
    title: `These are SIMULATED results, not historical fills. Model assumptions:\n• ${(result.modeled && result.modeled.assumptions || ["Fills at the next bar's open; modeled spread, slippage and commissions"]).join("\n• ")}`
  }, result.modeled && result.modeled.option_premiums ? "MODELED — synthetic option prices" : "MODELED — simulated fills")), (result.warnings || []).length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "bt-warn"
  }, result.warnings.map((w, i) => /*#__PURE__*/React.createElement("div", {
    key: i
  }, "\u26A0 ", w))), /*#__PURE__*/React.createElement("div", {
    className: "bt-tiles"
  }, /*#__PURE__*/React.createElement("div", {
    className: "bt-tile",
    title: "Sum of all realized trade P&L as a % of starting equity ($100k), after modeled costs."
  }, /*#__PURE__*/React.createElement("span", null, "Total return"), /*#__PURE__*/React.createElement("b", {
    className: M.total_return_pct >= 0 ? "up" : "down"
  }, M.total_return_pct, "%")), /*#__PURE__*/React.createElement("div", {
    className: "bt-tile",
    title: "Realized profit and loss in dollars, after spread, slippage and commissions."
  }, /*#__PURE__*/React.createElement("span", null, "Total P&L"), /*#__PURE__*/React.createElement("b", {
    className: M.total_pnl >= 0 ? "up" : "down"
  }, fmtD(M.total_pnl))), /*#__PURE__*/React.createElement("div", {
    className: "bt-tile",
    title: "Number of closed trades in the test. Skipped candidates (liquidity, max positions) are counted separately below."
  }, /*#__PURE__*/React.createElement("span", null, "Trades"), /*#__PURE__*/React.createElement("b", null, M.n_trades)), /*#__PURE__*/React.createElement("div", {
    className: "bt-tile",
    title: "Share of trades that closed with a profit."
  }, /*#__PURE__*/React.createElement("span", null, "Win rate"), /*#__PURE__*/React.createElement("b", null, M.win_rate, "%")), /*#__PURE__*/React.createElement("div", {
    className: "bt-tile",
    title: "Average dollar profit across winning trades."
  }, /*#__PURE__*/React.createElement("span", null, "Avg gain"), /*#__PURE__*/React.createElement("b", {
    className: "up"
  }, fmtD(M.avg_gain))), /*#__PURE__*/React.createElement("div", {
    className: "bt-tile",
    title: "Average dollar loss across losing trades."
  }, /*#__PURE__*/React.createElement("span", null, "Avg loss"), /*#__PURE__*/React.createElement("b", {
    className: "down"
  }, fmtD(M.avg_loss))), /*#__PURE__*/React.createElement("div", {
    className: "bt-tile",
    title: "Gross profits \xF7 gross losses. Above 1.0 = the wins outweigh the losses; below 1.0 the strategy loses money overall."
  }, /*#__PURE__*/React.createElement("span", null, "Profit factor"), /*#__PURE__*/React.createElement("b", null, M.profit_factor == null ? "∞" : M.profit_factor)), /*#__PURE__*/React.createElement("div", {
    className: "bt-tile",
    title: "Deepest peak-to-trough drop of the equity curve, as a % of the peak."
  }, /*#__PURE__*/React.createElement("span", null, "Max drawdown"), /*#__PURE__*/React.createElement("b", {
    className: "down"
  }, M.max_drawdown_pct, "%")), /*#__PURE__*/React.createElement("div", {
    className: "bt-tile",
    title: "Expected $ per trade: win-rate \xD7 avg gain \u2212 loss-rate \xD7 avg loss. Positive = the edge survives its costs."
  }, /*#__PURE__*/React.createElement("span", null, "Expectancy"), /*#__PURE__*/React.createElement("b", {
    className: M.expectancy >= 0 ? "up" : "down"
  }, fmtD(M.expectancy)))), /*#__PURE__*/React.createElement(BTEquityCurve, {
    curve: result.equity_curve,
    start: M.start_equity || 100000
  }), /*#__PURE__*/React.createElement("div", {
    className: "bt-detail"
  }, result.best_trade && /*#__PURE__*/React.createElement("span", {
    title: `Best single trade: ${result.best_trade.symbol} ${result.best_trade.entry_date} → ${result.best_trade.exit_date} (${result.best_trade.reason}).`
  }, "best ", /*#__PURE__*/React.createElement("b", {
    className: "up"
  }, result.best_trade.symbol, " ", fmtD(result.best_trade.pnl))), result.worst_trade && /*#__PURE__*/React.createElement("span", {
    title: `Worst single trade: ${result.worst_trade.symbol} ${result.worst_trade.entry_date} → ${result.worst_trade.exit_date} (${result.worst_trade.reason}).`
  }, "worst ", /*#__PURE__*/React.createElement("b", {
    className: "down"
  }, result.worst_trade.symbol, " ", fmtD(result.worst_trade.pnl))), /*#__PURE__*/React.createElement("span", {
    title: "Entry candidates skipped because the stock's average daily dollar volume was too small to absorb the position realistically \u2014 an unavailable fill, not a loss."
  }, "skipped (liquidity): ", /*#__PURE__*/React.createElement("b", null, result.skipped_no_liquidity || 0)), /*#__PURE__*/React.createElement("span", {
    title: "Signals ignored because the maximum number of simultaneous open positions was already reached."
  }, "skipped (max positions): ", /*#__PURE__*/React.createElement("b", null, result.skipped_max_positions || 0))), result.by_regime && Object.keys(result.by_regime).length > 0 && /*#__PURE__*/React.createElement("table", {
    className: "bt-regime",
    title: "The same trades bucketed by the S&P 500's condition on entry day (SPY vs its 50/200-day averages): does the edge only exist in one type of market?"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Market condition"), /*#__PURE__*/React.createElement("th", null, "Trades"), /*#__PURE__*/React.createElement("th", null, "Win rate"), /*#__PURE__*/React.createElement("th", null, "P&L"))), /*#__PURE__*/React.createElement("tbody", null, Object.entries(result.by_regime).map(([r, d]) => /*#__PURE__*/React.createElement("tr", {
    key: r
  }, /*#__PURE__*/React.createElement("td", null, r), /*#__PURE__*/React.createElement("td", null, d.n), /*#__PURE__*/React.createElement("td", null, d.win_rate, "%"), /*#__PURE__*/React.createElement("td", {
    className: d.pnl >= 0 ? "up" : "down"
  }, fmtD(d.pnl)))))), /*#__PURE__*/React.createElement("button", {
    className: "rr-btn",
    onClick: () => setShowTrades(!showTrades),
    title: "Every closed trade with entry/exit dates, prices, exit reason and P&L (most recent 400)."
  }, showTrades ? "hide trades" : `show trades (${(result.trades || []).length})`), showTrades && /*#__PURE__*/React.createElement("div", {
    className: "bt-trades-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "bt-trades"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Sym"), /*#__PURE__*/React.createElement("th", null, "In"), /*#__PURE__*/React.createElement("th", null, "Out"), /*#__PURE__*/React.createElement("th", null, "Entry"), /*#__PURE__*/React.createElement("th", null, "Exit"), /*#__PURE__*/React.createElement("th", null, "Why"), /*#__PURE__*/React.createElement("th", null, "P&L"), /*#__PURE__*/React.createElement("th", null, "%"))), /*#__PURE__*/React.createElement("tbody", null, (result.trades || []).slice().reverse().map((t, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, t.symbol, t.option ? ` ${t.option.strike}${t.option.right[0].toUpperCase()}` : ""), /*#__PURE__*/React.createElement("td", null, t.entry_date), /*#__PURE__*/React.createElement("td", null, t.exit_date), /*#__PURE__*/React.createElement("td", null, "$", t.entry_px), /*#__PURE__*/React.createElement("td", null, "$", t.exit_px), /*#__PURE__*/React.createElement("td", null, t.reason), /*#__PURE__*/React.createElement("td", {
    className: t.pnl >= 0 ? "up" : "down"
  }, fmtD(t.pnl)), /*#__PURE__*/React.createElement("td", {
    className: t.pnl >= 0 ? "up" : "down"
  }, t.pnl_pct, "%"))))))));
}
Object.assign(window, {
  BacktestCard: React.memo(BacktestCard)
});
})();
