(function () {
// tab-edge.jsx — LAZY CHUNK (v4.20), loaded on first Premium Edge open.
// The volatility workstation: where are option buyers overpaying for
// volatility, which structure/expiry/strike monetizes it, and is the
// premium juicy or dangerous.
// Endpoints: GET /api/edge (board) · /api/edge/scan · /api/edge/detail
// · /api/edge/history · /api/edge/breach · /api/edge/config
// · GET/POST /api/edge/backtest · POST /api/edge/kelly

const EDGE_INTENTS = [["premium_only", "Premium only", "Defined-risk structures (credit spreads, iron condors). I don't specifically want the shares."], ["want_stock", "Want the stock", "Cash-secured puts at strikes where premium, probabilities and acquisition price align."], ["own_stock", "Own the stock", "Covered calls — rich call premium without giving up too much upside."]];
const EDGE_SIG_TONE = {
  "STRONG SELL VOL": "up",
  "SELL VOL": "up",
  "WATCH": "warn",
  "FAIR": "mut",
  "CHEAP VOL": "down",
  "AVOID": "down",
  "INSUFFICIENT DATA": "mut"
};
const edgeIvPct = v => v == null ? "—" : `${(v * 100).toFixed(1)}%`;
const edgeNum = (v, d = 2) => v == null ? "—" : Number(v).toFixed(d);
const edgeProb = v => v == null ? "—" : `${(v * 100).toFixed(1)}%`;
const edgeSgn = (v, d = 1) => v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}`;
// Dates render as "Oct 28, 2026" — never raw ISO (house rule: Month Day, Year).
const edgeDate = s => {
  if (!s) return "—";
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  });
};
const edgeWhen = s => {
  if (!s) return "—";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return String(s);
  return `${d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  })} ` + d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit"
  });
};
function EdgeSigPill({
  signal
}) {
  const tone = EDGE_SIG_TONE[signal] || "mut";
  return /*#__PURE__*/React.createElement("span", {
    className: `edge-sig edge-sig-${tone}`
  }, signal || "—");
}
function EdgeScoreBar({
  score
}) {
  const s = Math.max(0, Math.min(100, score || 0));
  const cls = s >= 70 ? "hi" : s >= 45 ? "mid" : "lo";
  return /*#__PURE__*/React.createElement("span", {
    className: "edge-scorebar",
    title: `Premium Edge Score ${s}/100`
  }, /*#__PURE__*/React.createElement("i", {
    className: cls,
    style: {
      width: `${s}%`
    }
  }), /*#__PURE__*/React.createElement("b", null, Math.round(s)));
}

// ── charts (inline SVG, theme via CSS classes — house pd-chart pattern) ────

function EdgeVrpChart({
  obs,
  stats
}) {
  const pts = (obs || []).filter(o => o.vrp_points != null);
  if (pts.length < 2) {
    return /*#__PURE__*/React.createElement("div", {
      className: "research-empty"
    }, "VRP history accrues one observation per scan day \u2014 ", pts.length, " so far. The Z-score needs ", stats?.min_required ?? 60, ".");
  }
  const W = 760,
    H = 200,
    L = 42,
    R = 8,
    T = 10,
    B = 20;
  const vals = pts.map(o => o.vrp_points);
  const bands = [];
  if (stats && stats.mean != null && stats.std != null) {
    bands.push(["mean", stats.mean], ["+1σ", stats.mean + stats.std], ["+1.5σ", stats.mean + 1.5 * stats.std], ["+2σ", stats.mean + 2 * stats.std]);
  }
  const lo = Math.min(...vals, ...bands.map(b => b[1])) - 2;
  const hi = Math.max(...vals, ...bands.map(b => b[1])) + 2;
  const x = i => L + i / (pts.length - 1) * (W - L - R);
  const y = v => T + (1 - (v - lo) / Math.max(hi - lo, 1e-9)) * (H - T - B);
  const path = pts.map((o, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(o.vrp_points).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  return /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${W} ${H}`,
    className: "edge-chart",
    role: "img",
    "aria-label": "Historical VRP with sigma bands"
  }, bands.map(([lbl, v]) => /*#__PURE__*/React.createElement("g", {
    key: lbl
  }, /*#__PURE__*/React.createElement("line", {
    x1: L,
    x2: W - R,
    y1: y(v),
    y2: y(v),
    className: `edge-band ${lbl === "mean" ? "edge-band-mean" : ""}`
  }), /*#__PURE__*/React.createElement("text", {
    x: W - R - 2,
    y: y(v) - 2,
    className: "edge-chart-lbl",
    textAnchor: "end"
  }, lbl))), /*#__PURE__*/React.createElement("path", {
    d: path,
    className: "edge-line-main",
    fill: "none"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: x(pts.length - 1),
    cy: y(last.vrp_points),
    r: "3.5",
    className: "edge-dot"
  }), /*#__PURE__*/React.createElement("text", {
    x: L - 4,
    y: y(lo + 2),
    className: "edge-chart-lbl",
    textAnchor: "end"
  }, lo.toFixed(0)), /*#__PURE__*/React.createElement("text", {
    x: L - 4,
    y: y(hi - 2) + 4,
    className: "edge-chart-lbl",
    textAnchor: "end"
  }, hi.toFixed(0)), /*#__PURE__*/React.createElement("text", {
    x: L,
    y: H - 4,
    className: "edge-chart-lbl"
  }, edgeDate(pts[0].date)), /*#__PURE__*/React.createElement("text", {
    x: W - R,
    y: H - 4,
    className: "edge-chart-lbl",
    textAnchor: "end"
  }, edgeDate(last.date)));
}
function EdgeIvErvChart({
  obs
}) {
  const pts = (obs || []).filter(o => o.iv30 != null && o.erv30 != null);
  if (pts.length < 2) return null;
  const W = 760,
    H = 180,
    L = 42,
    R = 8,
    T = 10,
    B = 20;
  const all = pts.flatMap(o => [o.iv30, o.erv30]);
  const lo = Math.min(...all) * 0.9,
    hi = Math.max(...all) * 1.05;
  const x = i => L + i / (pts.length - 1) * (W - L - R);
  const y = v => T + (1 - (v - lo) / Math.max(hi - lo, 1e-9)) * (H - T - B);
  const line = k => pts.map((o, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(o[k]).toFixed(1)}`).join(" ");
  return /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${W} ${H}`,
    className: "edge-chart",
    role: "img",
    "aria-label": "IV30 vs Expected RV30 history"
  }, /*#__PURE__*/React.createElement("path", {
    d: line("iv30"),
    className: "edge-line-iv",
    fill: "none"
  }), /*#__PURE__*/React.createElement("path", {
    d: line("erv30"),
    className: "edge-line-erv",
    fill: "none"
  }), /*#__PURE__*/React.createElement("text", {
    x: L + 4,
    y: T + 10,
    className: "edge-chart-lbl edge-lbl-iv"
  }, "IV30"), /*#__PURE__*/React.createElement("text", {
    x: L + 44,
    y: T + 10,
    className: "edge-chart-lbl edge-lbl-erv"
  }, "Expected RV30"), /*#__PURE__*/React.createElement("text", {
    x: L - 4,
    y: y(lo),
    className: "edge-chart-lbl",
    textAnchor: "end"
  }, edgeIvPct(lo)), /*#__PURE__*/React.createElement("text", {
    x: L - 4,
    y: y(hi) + 8,
    className: "edge-chart-lbl",
    textAnchor: "end"
  }, edgeIvPct(hi)));
}
function EdgeTermChart({
  term
}) {
  const rows = term && term.rows || [];
  if (rows.length < 2) return null;
  const W = 760,
    H = 170,
    L = 42,
    R = 8,
    T = 12,
    B = 22;
  const lo = Math.min(...rows.map(r => r.iv)) * 0.94;
  const hi = Math.max(...rows.map(r => r.iv)) * 1.05;
  const maxD = Math.max(...rows.map(r => r.dte));
  const x = d => L + d / maxD * (W - L - R);
  const y = v => T + (1 - (v - lo) / Math.max(hi - lo, 1e-9)) * (H - T - B);
  const path = rows.map((r, i) => `${i ? "L" : "M"}${x(r.dte).toFixed(1)},${y(r.iv).toFixed(1)}`).join(" ");
  return /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${W} ${H}`,
    className: "edge-chart",
    role: "img",
    "aria-label": "IV term structure"
  }, /*#__PURE__*/React.createElement("path", {
    d: path,
    className: "edge-line-main",
    fill: "none"
  }), rows.map(r => /*#__PURE__*/React.createElement("g", {
    key: r.exp
  }, /*#__PURE__*/React.createElement("circle", {
    cx: x(r.dte),
    cy: y(r.iv),
    r: "3",
    className: r.covers_earnings ? "edge-dot-earn" : "edge-dot"
  }, /*#__PURE__*/React.createElement("title", null, `${edgeDate(r.exp)} · ${r.dte}d · IV ${edgeIvPct(r.iv)}${r.covers_earnings ? " · covers earnings" : ""}`)), /*#__PURE__*/React.createElement("text", {
    x: x(r.dte),
    y: H - 6,
    className: "edge-chart-lbl",
    textAnchor: "middle"
  }, Math.round(r.dte), "d"))), /*#__PURE__*/React.createElement("text", {
    x: L - 4,
    y: y(lo),
    className: "edge-chart-lbl",
    textAnchor: "end"
  }, edgeIvPct(lo)), /*#__PURE__*/React.createElement("text", {
    x: L - 4,
    y: y(hi) + 8,
    className: "edge-chart-lbl",
    textAnchor: "end"
  }, edgeIvPct(hi)));
}
function EdgeSkewChart({
  skew
}) {
  if (!skew) return null;
  const pts = [["10Δ put", skew.put10_iv], ["25Δ put", skew.put25_iv], ["ATM", skew.atm_iv], ["25Δ call", skew.call25_iv], ["10Δ call", skew.call10_iv]].filter(p => p[1] != null);
  if (pts.length < 3) return null;
  const W = 760,
    H = 150,
    L = 42,
    R = 8,
    T = 12,
    B = 22;
  const lo = Math.min(...pts.map(p => p[1])) * 0.96;
  const hi = Math.max(...pts.map(p => p[1])) * 1.04;
  const x = i => L + i / (pts.length - 1) * (W - L - R);
  const y = v => T + (1 - (v - lo) / Math.max(hi - lo, 1e-9)) * (H - T - B);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  return /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${W} ${H}`,
    className: "edge-chart",
    role: "img",
    "aria-label": "Volatility skew curve"
  }, /*#__PURE__*/React.createElement("path", {
    d: path,
    className: "edge-line-main",
    fill: "none"
  }), pts.map((p, i) => /*#__PURE__*/React.createElement("g", {
    key: p[0]
  }, /*#__PURE__*/React.createElement("circle", {
    cx: x(i),
    cy: y(p[1]),
    r: "3",
    className: "edge-dot"
  }, /*#__PURE__*/React.createElement("title", null, `${p[0]}: ${edgeIvPct(p[1])}`)), /*#__PURE__*/React.createElement("text", {
    x: x(i),
    y: H - 6,
    className: "edge-chart-lbl",
    textAnchor: "middle"
  }, p[0]))));
}

// ── detail view ─────────────────────────────────────────────────────────────

function EdgeStructRow({
  s
}) {
  const strikes = s.kind === "iron_condor" ? `${s.long_put}/${s.short_put}p · ${s.short_call}/${s.long_call}c` : s.short_strike != null ? `${s.short_strike}/${s.long_strike}` : `${s.strike}`;
  return /*#__PURE__*/React.createElement("tr", {
    className: "scan-row"
  }, /*#__PURE__*/React.createElement("td", null, (s.kind || "").replace(/_/g, " ")), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, strikes), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeNum(s.credit ?? s.credit_exec)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, s.max_loss != null ? edgeNum(s.max_loss) : "uncapped*"), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${(s.ev_per_share ?? 0) > 0 ? "up" : "down"}`
  }, edgeSgn(s.ev_per_share, 3)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeNum(s.es5_per_share, 2)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeProb(s.p_itm_model)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeProb(s.p_touch_model)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, s.prem_pct_collateral != null ? `${edgeNum(s.prem_pct_collateral, 1)}%` : "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, s.annualized_pct != null ? `${edgeNum(s.annualized_pct, 0)}%` : "—"), /*#__PURE__*/React.createElement("td", null, s.liquidity_ok ? /*#__PURE__*/React.createElement("span", {
    className: "edge-ok"
  }, "ok") : /*#__PURE__*/React.createElement("span", {
    className: "edge-bad",
    title: (s.liquidity_notes || []).join("; ")
  }, "thin")));
}
function EdgeBacktestPanel({
  apiFetch,
  sym
}) {
  const [job, setJob] = useState(null);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const pollRef = useRef(null);
  useEffect(() => () => pollRef.current && clearInterval(pollRef.current), []);
  const run = async () => {
    setErr(null);
    setRes(null);
    try {
      const r = await apiFetch("/api/edge/backtest", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          symbols: [sym]
        })
      });
      const d = await r.json();
      if (!d.job) {
        setErr(d.error || "could not start");
        return;
      }
      setJob(d.job);
      pollRef.current = setInterval(async () => {
        try {
          const jr = await apiFetch(`/api/edge/backtest?job=${d.job}`, {
            noCache: true
          });
          const jd = await jr.json();
          if (jd.status && jd.status !== "running") {
            clearInterval(pollRef.current);
            setJob(null);
            if (jd.result && !jd.result.error) setRes(jd.result);else setErr(jd.result && jd.result.error || "backtest failed");
          }
        } catch (e) {/* keep polling */}
      }, 5000);
    } catch (e) {
      setErr(String(e));
    }
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "edge-bt"
  }, /*#__PURE__*/React.createElement("div", {
    className: "edge-sechead"
  }, "VRP threshold backtest", /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: run,
    disabled: !!job,
    title: "Sweeps IV/ExpectedRV entry-ratio thresholds through the app's options backtester (real cost, slippage and assignment model) on ~2 years of this ticker's bars. Judge by expectancy and worst-tail outcomes, never win rate."
  }, job ? "Running…" : "Run backtest")), err && /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, err), res && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "edge-note"
  }, res.iv_basis && res.iv_basis[sym], " \xB7 structure ", res.structure, res.robust_threshold != null && /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 robust threshold ", /*#__PURE__*/React.createElement("b", null, "\u2265", res.robust_threshold, "\xD7"), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " (best expectancy whose neighbors also work \u2014 single-point winners are curve-fit)"))), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table edge-bt-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Enter when IV30 / ExpectedRV30 is at least this ratio."
  }, "Threshold"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Trades taken over the test period."
  }, "Trades"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Average P/L per trade after costs \u2014 the number that matters most."
  }, "Expectancy"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Percent of trades that made money. A high win rate with terrible tails is NOT a good strategy."
  }, "Win %"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Gross profit / gross loss."
  }, "PF"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Single trade at the 5th percentile \u2014 the bad-day benchmark."
  }, "Worst 5%"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Average of the worst 5% of trades (expected shortfall)."
  }, "ES 5%"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Peak-to-trough equity drawdown across the period."
  }, "Max DD"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Trades that ended in assignment."
  }, "Assigned"))), /*#__PURE__*/React.createElement("tbody", null, res.grid.map(g => /*#__PURE__*/React.createElement("tr", {
    key: g.threshold,
    className: g.threshold === res.robust_threshold ? "edge-row-open" : ""
  }, /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, "\u2265", g.threshold, "\xD7"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.n_trades), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${(g.expectancy ?? 0) > 0 ? "up" : "down"}`
  }, g.expectancy != null ? `$${g.expectancy}` : "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.win_rate != null ? `${g.win_rate}%` : "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.profit_factor ?? "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num down"
  }, g.worst_5pct != null ? `$${g.worst_5pct}` : "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num down"
  }, g.es_5pct != null ? `$${g.es_5pct}` : "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.max_drawdown_pct != null ? `${edgeNum(g.max_drawdown_pct, 1)}%` : "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.assignments ?? "—"))))))));
}
function EdgeDetail({
  apiFetch,
  sym,
  onClose,
  onOpenTicker
}) {
  const [intent, setIntent] = useState(() => {
    try {
      return localStorage.getItem("jerry_edge_intent") || "premium_only";
    } catch (e) {
      return "premium_only";
    }
  });
  const [d, setD] = useState(null);
  const [hist, setHist] = useState(null);
  const [breach, setBreach] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let stop = false;
    setD(null);
    setErr(null);
    apiFetch(`/api/edge/detail?symbol=${sym}&intent=${intent}`, {
      noCache: true
    }).then(r => r.json()).then(x => {
      if (!stop) {
        x.error && !x.row ? setErr(x.error) : setD(x);
      }
    }).catch(e => {
      if (!stop) setErr(String(e));
    });
    return () => {
      stop = true;
    };
  }, [sym, intent]);
  useEffect(() => {
    let stop = false;
    sharedJson(apiFetch, `/api/edge/history?symbol=${sym}`, 5 * 60 * 1000).then(x => {
      if (!stop) setHist(x);
    }).catch(() => {});
    sharedJson(apiFetch, `/api/edge/breach?symbol=${sym}`, 30 * 60 * 1000).then(x => {
      if (!stop) setBreach(x);
    }).catch(() => {});
    return () => {
      stop = true;
    };
  }, [sym]);
  const pickIntent = k => {
    setIntent(k);
    try {
      localStorage.setItem("jerry_edge_intent", k);
    } catch (e) {/* private mode */}
  };
  const r = d && d.row;
  const fcErrs = hist && hist.forecast && hist.forecast.errors;
  const fcMae = fcErrs && fcErrs.length >= 5 ? fcErrs.reduce((a, e) => a + Math.abs(e.err_volpts), 0) / fcErrs.length : null;
  return /*#__PURE__*/React.createElement("div", {
    className: "card edge-detail"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, sym, " \xB7 volatility detail", /*#__PURE__*/React.createElement("button", {
    className: "rr-btn edge-back",
    onClick: onClose
  }, "\u2190 board"), /*#__PURE__*/React.createElement("button", {
    className: "rr-btn",
    onClick: () => onOpenTicker && onOpenTicker(sym),
    title: "Open this ticker on the Trade tab."
  }, "Trade tab \u2192")), r && /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "as of ", edgeWhen(r.as_of), " \xB7 engine ", d.engine && d.engine.version, " \xB7 spot $", r.spot))), err && /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, err), !d && !err && /*#__PURE__*/React.createElement("div", {
    className: "card-loading"
  }, "Analyzing ", sym, "\u2026"), r && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "edge-hero"
  }, /*#__PURE__*/React.createElement("div", {
    className: "edge-hero-main"
  }, /*#__PURE__*/React.createElement(EdgeSigPill, {
    signal: r.signal
  }), /*#__PURE__*/React.createElement(EdgeScoreBar, {
    score: r.score
  }), /*#__PURE__*/React.createElement("span", {
    className: `edge-danger edge-danger-${(r.danger || "").toLowerCase()}`,
    title: (d.danger.reasons || []).join(" · ") || "no danger flags"
  }, r.danger), /*#__PURE__*/React.createElement("span", {
    className: "edge-class",
    title: `Premium classification. Event share of the IV-vs-forecast gap: ${r.event_share != null ? Math.round(r.event_share * 100) + "%" : "n/a"}`
  }, r.premium_class)), /*#__PURE__*/React.createElement("div", {
    className: "edge-hero-nums"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "iv30"
  }, "IV30")), /*#__PURE__*/React.createElement("b", null, edgeIvPct(r.iv30)), /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, r.iv30_method === "variance_interpolation" ? "interpolated" : "nearest exp")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "expected_rv"
  }, "Expected RV30")), /*#__PURE__*/React.createElement("b", null, edgeIvPct(r.erv30)), /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, d.erv.method)), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "vrp"
  }, "VRP")), /*#__PURE__*/React.createElement("b", {
    className: r.vrp_points > 0 ? "up" : "down"
  }, edgeSgn(r.vrp_points), " pts"), /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, edgeNum(r.vrp_ratio, 2), "\xD7 ratio")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "vrp_z"
  }, "VRP Z")), /*#__PURE__*/React.createElement("b", null, r.vrp_z != null ? edgeSgn(r.vrp_z, 2) : "—"), /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, r.hist_status === "ok" ? `${r.vrp_percentile}th pctl, n=${r.hist_n}` : `accruing ${r.hist_n}/${d.hist && d.hist.min_required || 60} days`))), d.erv.event_adj && /*#__PURE__*/React.createElement("div", {
    className: "edge-note"
  }, "Earnings adjustment: +", d.erv.event_adj.added_volpts, " vol pts from", " ", d.erv.event_adj.basis, " \u2192 event-adjusted RV ", edgeIvPct(r.erv30_event), ". Premium net of the event: ", edgeSgn(d.vrp.vrp_points_ex_event), " pts."), r.main_risk && /*#__PURE__*/React.createElement("div", {
    className: "edge-mainrisk"
  }, "Main risk: ", r.main_risk)), /*#__PURE__*/React.createElement("div", {
    className: "edge-sechead"
  }, "Why this score"), /*#__PURE__*/React.createElement("ul", {
    className: "edge-why"
  }, (d.score_breakdown || []).map((b, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, /*#__PURE__*/React.createElement("b", {
    className: b.pts >= 0 ? "up" : "down"
  }, edgeSgn(b.pts)), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " / ", b.max), " \xB7 ", b.factor.replace(/_/g, " "), " \u2014 ", b.note))), /*#__PURE__*/React.createElement("div", {
    className: "edge-sechead"
  }, "Structures for your intent"), /*#__PURE__*/React.createElement("div", {
    className: "edge-intents"
  }, EDGE_INTENTS.map(([k, lbl, tip]) => /*#__PURE__*/React.createElement("button", {
    key: k,
    className: `tsy-serbtn ${intent === k ? "on" : ""}`,
    title: tip,
    onClick: () => pickIntent(k)
  }, lbl))), d.structures ? /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "edge-note"
  }, "Expiry ", /*#__PURE__*/React.createElement("b", null, edgeDate(d.structures.expiry)), " (", d.structures.dte, "d)", " ", "\u2014 chosen as the richest tenor inside the seller window, not a fixed 30 DTE.", " ", "Probabilities are ", /*#__PURE__*/React.createElement("b", null, "model"), " (driftless lognormal at Expected RV) \u2014 the breach table below shows this ticker's MEASURED history."), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table edge-struct-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Structure type for the selected intent. Premium-only intents get defined risk only."
  }, "Structure"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Short strike (short/long for spreads; put side \xB7 call side for condors)."
  }, "Strikes"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Executable credit \u2014 the BID for singles (a resting seller's floor), net for spreads."
  }, "Credit"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Defined maximum loss per share. Cash-secured puts show uncapped* (collateral = strike)."
  }, "Max loss"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Expected value per share = credit \u2212 fair value at Expected RV \u2212 costs. This is the VRP in dollars for this exact strike."
  }, "EV"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Expected shortfall: average loss in the worst 5% of modeled outcomes."
  }, "ES 5%"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Model probability the short strike finishes in the money."
  }, "P(ITM)"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Model probability price touches the short strike before expiry \u2014 always \u2265 P(ITM)."
  }, "P(touch)"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Credit as % of collateral."
  }, "RoC"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Annualized return on collateral if repeated. Only meaningful for comparing candidates."
  }, "Ann."), /*#__PURE__*/React.createElement("th", {
    title: "Liquidity gates: open interest, volume, spread width."
  }, "Liq"))), /*#__PURE__*/React.createElement("tbody", null, d.structures.structures.map((s, i) => /*#__PURE__*/React.createElement(EdgeStructRow, {
    key: i,
    s: s
  })))))) : /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, "No structures pass the quality gates for this intent right now."), /*#__PURE__*/React.createElement("div", {
    className: "edge-grid2"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "edge-sechead"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "vrp"
  }, "VRP"), " history", d.hist && d.hist.status === "ok" && /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 mean ", d.hist.mean, " \xB7 \u03C3 ", d.hist.std, " \xB7 p95 ", d.hist.p95)), /*#__PURE__*/React.createElement(EdgeVrpChart, {
    obs: hist && hist.observations,
    stats: d.hist
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "edge-sechead"
  }, "IV30 vs Expected RV30"), /*#__PURE__*/React.createElement(EdgeIvErvChart, {
    obs: hist && hist.observations
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "edge-sechead"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "term_structure"
  }, "Term structure"), d.term && /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 ", d.term.shape, d.term.humps && d.term.humps.length ? " · hump (earnings)" : "")), /*#__PURE__*/React.createElement(EdgeTermChart, {
    term: d.term
  })), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "edge-sechead"
  }, "Skew", d.skew && d.skew.rr25_volpts != null && /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 RR25 ", edgeSgn(d.skew.rr25_volpts), " vol pts (", d.skew.rr25_volpts > 0 ? "puts richer" : "calls richer", ")")), /*#__PURE__*/React.createElement(EdgeSkewChart, {
    skew: d.skew
  }))), breach && breach.em_calibration && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "edge-sechead"
  }, "Expected-move calibration ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 ", breach.em_calibration.em_basis)), /*#__PURE__*/React.createElement("div", {
    className: "edge-emcal"
  }, [["inside 1×EM", breach.em_calibration.inside_1x_pct, 68.3], ["inside 1.25×", breach.em_calibration.inside_125x_pct, null], ["inside 1.5×", breach.em_calibration.inside_15x_pct, null]].map(([lbl, v, theory]) => /*#__PURE__*/React.createElement("div", {
    key: lbl,
    className: "edge-embar",
    title: theory ? `Lognormal theory: ${theory}%` : undefined
  }, /*#__PURE__*/React.createElement("span", null, lbl), /*#__PURE__*/React.createElement("div", {
    className: "edge-embar-track"
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      width: `${v}%`
    }
  }), theory && /*#__PURE__*/React.createElement("em", {
    style: {
      left: `${theory}%`
    },
    title: `theory ${theory}%`
  })), /*#__PURE__*/React.createElement("b", null, v, "%"))), /*#__PURE__*/React.createElement("div", {
    className: "edge-note"
  }, "breaches: ", breach.em_calibration.upside_breach_pct, "% up \xB7", " ", breach.em_calibration.downside_breach_pct, "% down \xB7 n=", breach.em_calibration.n, " windows"))), breach && breach.breach && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "edge-sechead"
  }, "Strike breach history ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 MEASURED from this ticker's own bars")), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table edge-breach-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Holding horizon in trading days."
  }, "Horizon"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Strike distance in trailing-\u03C320 units at entry."
  }, "k\xB7\u03C3"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "How often price actually touched a put strike placed k\xB7\u03C3 below, over all historical windows."
  }, "Put touch"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "How often price finished below that put strike."
  }, "Put ITM"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Same distance above: touch frequency for calls."
  }, "Call touch"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Finished above the call strike."
  }, "Call ITM"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "What the driftless lognormal model predicts for touch at this distance \u2014 the gap vs measured is this ticker's fat-tail correction."
  }, "Model touch"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Model finish-ITM at this distance."
  }, "Model ITM"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Historical windows measured."
  }, "n"))), /*#__PURE__*/React.createElement("tbody", null, breach.breach.rows.map((b, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, b.horizon_td, "d"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, b.k_sigma), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeProb(b.put_touch_emp)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeProb(b.put_itm_emp)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeProb(b.call_touch_emp)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeProb(b.call_itm_emp)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, edgeProb(b.touch_model)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, edgeProb(b.itm_model)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num muted"
  }, b.n))))))), /*#__PURE__*/React.createElement("div", {
    className: "edge-sechead"
  }, "Forecast model ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 ", d.erv.method)), /*#__PURE__*/React.createElement("div", {
    className: "edge-note"
  }, "components: ", Object.entries(d.erv.components || {}).map(([k, v]) => `${k} ${edgeIvPct(v)}`).join(" · "), d.erv.anchor != null && /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 long-run anchor ", edgeIvPct(d.erv.anchor)), fcMae != null && /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 live forecast error (this ticker): ", /*#__PURE__*/React.createElement("b", null, fcMae.toFixed(1), " vol pts"), " mean abs over ", fcErrs.length, " checks"), d.erv.quality !== "ok" && /*#__PURE__*/React.createElement(React.Fragment, null, " \xB7 ", /*#__PURE__*/React.createElement("b", {
    className: "down"
  }, d.erv.quality))), /*#__PURE__*/React.createElement(EdgeBacktestPanel, {
    apiFetch: apiFetch,
    sym: sym
  })));
}

// ── main tab ────────────────────────────────────────────────────────────────

function EdgeTab({
  apiFetch,
  onOpenTicker
}) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [sortK, setSortK] = useState("score");
  const [sortD, setSortD] = useState(-1);
  const [sigFilter, setSigFilter] = useState("");
  const [edgeSym, setEdgeSym] = useState(null);
  const pollRef = useRef(null);
  const load = async () => {
    try {
      const r = await apiFetch("/api/edge");
      const d = await r.json();
      setBoard(d);
      return d;
    } catch (e) {
      setErr(String(e));
      return null;
    }
  };
  const watchScan = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (d && !d.scanning) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 4000);
  };
  useEffect(() => {
    load().then(d => {
      if (!d) return;
      if (d.scanning) {
        watchScan();
        return;
      }
      const age = d.as_of ? Date.now() - new Date(d.as_of).getTime() : Infinity;
      if (!d.rows || !d.rows.length || age > 45 * 60000) {
        apiFetch("/api/edge/scan").catch(() => {});
        watchScan();
      }
    });
    const iv = setInterval(skipWhenHidden(load), 60 * 1000);
    return () => {
      clearInterval(iv);
      pollRef.current && clearInterval(pollRef.current);
    };
  }, []);
  const rows = board && board.rows || [];
  const ok = rows.filter(r => r.data_ok);
  const summary = useMemo(() => {
    const by = (fn, pool) => (pool || ok).slice().sort(fn)[0];
    return {
      best: by((a, b) => b.score - a.score),
      richest: by((a, b) => (b.vrp_points ?? -99) - (a.vrp_points ?? -99)),
      csp: by((a, b) => b.score - a.score, ok.filter(r => (r.rr25_volpts ?? 0) > 0.5)),
      cc: by((a, b) => b.score - a.score, ok.filter(r => (r.rr25_volpts ?? 0) < -0.5)),
      defined: by((a, b) => b.score - a.score, ok.filter(r => /spread|condor/.test(r.best_kind || ""))),
      danger: by((a, b) => (b.iv30 ?? 0) - (a.iv30 ?? 0), ok.filter(r => r.danger === "DANGEROUS"))
    };
  }, [board]);
  const filtered = useMemo(() => rows.filter(r => !sigFilter || r.signal === sigFilter), [rows, sigFilter]);
  const sorted = useMemo(() => {
    const key = r => {
      switch (sortK) {
        case "symbol":
          return r.symbol || "";
        case "signal":
          return r.signal || "";
        case "score":
          return r.score ?? -1;
        case "iv30":
          return r.iv30 ?? -1;
        case "erv30":
          return r.erv30 ?? -1;
        case "vrp":
          return r.vrp_points ?? -99;
        case "ratio":
          return r.vrp_ratio ?? -1;
        case "z":
          return r.vrp_z ?? -99;
        case "pctl":
          return r.vrp_percentile ?? -1;
        case "skew":
          return r.rr25_volpts ?? -99;
        case "pitm":
          return r.best_p_itm ?? 2;
        case "ptouch":
          return r.best_p_touch ?? 2;
        case "roc":
          return r.best_roc_pct ?? -1;
        case "earn":
          return r.earnings_date || "9999";
        default:
          return r.score ?? -1;
      }
    };
    return filtered.slice().sort((a, b) => {
      const ka = key(a),
        kb = key(b);
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * sortD;
    });
  }, [filtered, sortK, sortD]);
  const [shown, moreControls] = useBoundedList(sorted, 60, 120);
  const th = (label, k, tip) => /*#__PURE__*/React.createElement("th", {
    className: k === "symbol" || k === "signal" ? "" : "scan-th-num",
    title: tip,
    onClick: () => {
      if (sortK === k) setSortD(x => -x);else {
        setSortK(k);
        setSortD(k === "symbol" || k === "signal" || k === "earn" || k === "pitm" || k === "ptouch" ? 1 : -1);
      }
    },
    style: {
      cursor: "pointer"
    }
  }, label, sortK === k ? sortD < 0 ? " ↓" : " ↑" : "");
  const SumBox = ({
    title,
    tone,
    r,
    note
  }) => /*#__PURE__*/React.createElement("div", {
    className: `ab-sumbox ${tone || ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-sumbox-title"
  }, title), r ? /*#__PURE__*/React.createElement("div", {
    className: "edge-sum-body"
  }, /*#__PURE__*/React.createElement("button", {
    className: "ab-chip",
    onClick: () => setEdgeSym(r.symbol),
    title: `open ${r.symbol} volatility detail`
  }, r.symbol, " ", /*#__PURE__*/React.createElement("b", null, r.score)), /*#__PURE__*/React.createElement("span", {
    className: "edge-sum-note"
  }, note(r))) : /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\u2014"));
  if (edgeSym) {
    return /*#__PURE__*/React.createElement(EdgeDetail, {
      apiFetch: apiFetch,
      sym: edgeSym,
      onClose: () => setEdgeSym(null),
      onOpenTicker: onOpenTicker
    });
  }
  const scanning = board && board.scanning;
  return /*#__PURE__*/React.createElement("div", {
    className: "card edge-tab"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Premium Edge"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "Where option buyers are overpaying for volatility \u2014 IV30 vs a walk-forward forecast of what this stock actually realizes. Probabilities labeled", /*#__PURE__*/React.createElement("b", null, " model"), " are driftless-lognormal estimates, not promises.")), /*#__PURE__*/React.createElement("div", {
    className: "edge-ctrl"
  }, /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    disabled: !!scanning,
    title: "Re-scan the candidate universe now (Stage 1 free screen \u2192 budgeted chain calls \u2192 deep contract analysis).",
    onClick: () => {
      apiFetch("/api/edge/scan?force=1").catch(() => {});
      watchScan();
    }
  }, scanning ? `Scanning ${board.scanned}/${board.universe}…` : "Scan now"))), err && /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, err), board && board.error && /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, "last scan failed: ", board.error), !board && !err && /*#__PURE__*/React.createElement("div", {
    className: "card-loading"
  }, "Loading board\u2026"), board && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "ab-summary"
  }, /*#__PURE__*/React.createElement(SumBox, {
    title: "Best opportunity now",
    tone: "up",
    r: summary.best,
    note: r => `${r.signal} · VRP ${edgeSgn(r.vrp_points)} pts`
  }), /*#__PURE__*/React.createElement(SumBox, {
    title: "Richest VRP",
    r: summary.richest,
    note: r => `IV ${edgeIvPct(r.iv30)} vs RV ${edgeIvPct(r.erv30)}`
  }), /*#__PURE__*/React.createElement(SumBox, {
    title: "Best cash-secured put",
    r: summary.csp,
    note: r => `puts richer ${edgeSgn(r.rr25_volpts)} pts`
  }), /*#__PURE__*/React.createElement(SumBox, {
    title: "Best covered call",
    r: summary.cc,
    note: r => `calls richer ${edgeSgn(-(r.rr25_volpts ?? 0))} pts`
  }), /*#__PURE__*/React.createElement(SumBox, {
    title: "Best defined risk",
    r: summary.defined,
    note: r => `${(r.best_kind || "").replace(/_/g, " ")} · RoC ${edgeNum(r.best_roc_pct, 1)}%`
  }), /*#__PURE__*/React.createElement(SumBox, {
    title: "Most dangerous premium",
    tone: "down",
    r: summary.danger,
    note: r => r.main_risk || "danger flags"
  })), /*#__PURE__*/React.createElement("div", {
    className: "edge-filters"
  }, ["", "STRONG SELL VOL", "SELL VOL", "WATCH", "CHEAP VOL", "AVOID"].map(s => /*#__PURE__*/React.createElement("button", {
    key: s || "all",
    className: `tsy-serbtn ${sigFilter === s ? "on" : ""}`,
    onClick: () => setSigFilter(s)
  }, s || "All", " ", /*#__PURE__*/React.createElement("b", null, s ? rows.filter(r => r.signal === s).length : rows.length))), /*#__PURE__*/React.createElement("span", {
    className: "muted edge-asof"
  }, "as of ", edgeWhen(board.as_of), board.market_open === false ? " · market closed (quotes = last session)" : "")), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table edge-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, th("Ticker", "symbol", "Underlying symbol. Click a row for the full volatility detail view."), th("Signal", "signal", "Decision state. SELL VOL requires the options to be attractive AFTER liquidity, event, tail-risk and execution gates — never just high IV."), th("Score", "score", "Premium Edge Score 0-100 with an itemized breakdown in the detail view. Weights live in thresholds.json and are validated by the backtest, not asserted."), th("IV30", "iv30", "True 30-calendar-day implied vol, interpolated on total variance between the bracketing expirations from liquid near-ATM call+put quotes."), th("ExpRV", "erv30", "Walk-forward-validated forecast of the realized vol this stock is likely to deliver over the next ~30 days. NOT trailing HV."), th("VRP", "vrp", "Volatility risk premium in vol points: IV30 − Expected RV30. Positive = option buyers paying more than the stock is expected to move."), th("Ratio", "ratio", "IV30 / Expected RV30. 1.33 means options price a third more volatility than forecast."), th("Z", "z", "Current VRP vs this ticker's OWN historical VRP distribution, in standard deviations. Blank until ~60 daily observations accrue — the store is filling daily."), th("Pctl", "pctl", "Current VRP's percentile in the ticker's own history (needs the same history as Z)."), th("Class", "class", "PURE = ordinary vol premium · EVENT = mostly known-event pricing (earnings) · MIXED = both. Event premium is not free premium."), th("Skew", "skew", "25Δ risk reversal in vol points, put minus call. Positive = downside puts richer (cash-secured put territory); negative = upside calls richer (covered calls)."), th("Earnings", "earn", "Next earnings date. Bold when it falls inside the trade horizon."), th("Best structure", "score", "Highest EV-per-tail-risk structure at the richest expiry (defined-risk scan). Click through for all candidates and other intents."), th("P(ITM)", "pitm", "Model probability the best structure's short strike finishes in the money (driftless lognormal at Expected RV)."), th("P(touch)", "ptouch", "Model probability of touching the short strike before expiry — roughly double P(ITM)."), th("RoC", "roc", "Credit as % of collateral for the best structure."), th("Danger", "signal", "JUICY = premium without a live reason to expect the move · DANGEROUS = the market may be pricing something real. Max premium is not max edge."))), /*#__PURE__*/React.createElement("tbody", null, shown.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.symbol,
    className: "scan-row edge-row",
    onClick: () => r.data_ok !== false && setEdgeSym(r.symbol),
    title: r.error || r.main_risk || ""
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, r.symbol)), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(EdgeSigPill, {
    signal: r.signal
  })), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, /*#__PURE__*/React.createElement(EdgeScoreBar, {
    score: r.score
  })), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeIvPct(r.iv30)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeIvPct(r.erv30)), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${(r.vrp_points ?? 0) > 0 ? "rich" : "cheap"}`
  }, edgeSgn(r.vrp_points)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeNum(r.vrp_ratio, 2)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, r.vrp_z != null ? edgeSgn(r.vrp_z, 1) : /*#__PURE__*/React.createElement("span", {
    className: "muted",
    title: `accruing: ${r.hist_n ?? 0} days stored`
  }, r.hist_n ?? 0, "d")), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, r.vrp_percentile != null ? Math.round(r.vrp_percentile) : "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, r.premium_class || "—"), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${(r.rr25_volpts ?? 0) > 0 ? "up" : "down"}`
  }, edgeSgn(r.rr25_volpts)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, r.earnings_inside ? /*#__PURE__*/React.createElement("b", null, edgeDate(r.earnings_date)) : r.earnings_date ? edgeDate(r.earnings_date) : "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num edge-struct-cell"
  }, r.best_kind ? `${(r.best_kind || "").replace(/_/g, " ")} ${r.best_strike ?? ""} ${r.best_expiry ? edgeDate(r.best_expiry) : ""} $${edgeNum(r.best_credit)}` : "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeProb(r.best_p_itm)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, edgeProb(r.best_p_touch)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, r.best_roc_pct != null ? `${edgeNum(r.best_roc_pct, 1)}%` : "—"), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${r.danger === "DANGEROUS" ? "down" : r.danger === "JUICY" ? "up" : ""}`
  }, r.danger || "—")))))), moreControls, !rows.length && !scanning && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, board.as_of ? "The last scan returned no rows — “Scan now” retries with the watchlist fallback. If this keeps happening, the reason is shown above." : "No scan yet — hit “Scan now”. The board fills from the watchlist universe: free screen first, then budgeted chain calls for the top names.")));
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, {
  EdgeTab: React.memo(EdgeTab)
});
})();
