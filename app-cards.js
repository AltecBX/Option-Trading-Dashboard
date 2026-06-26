(function () {
// All card and panel components — split out of the app.jsx monolith (v1.40).
// Loads before app.js; every binding is published to window so later
// files resolve bare references exactly as they did in one file.

function TickerLogo({
  ticker
}) {
  // Fallback chain — try several free logo CDNs in order, fall back to
  // text if all fail. We track loaded/error state explicitly because
  // mobile Safari sometimes renders the broken-image placeholder briefly
  // before firing onError, which produces an ugly "ticker?" box flash.
  // Until the image confirms it loaded, we render the text fallback so
  // users never see the broken-image glyph.
  const sources = React.useMemo(() => [`https://logo.synthfinance.com/ticker/${ticker}`, `https://financialmodelingprep.com/image-stock/${ticker}.png`, `https://assets.parqet.com/logos/symbol/${ticker}`], [ticker]);
  const [idx, setIdx] = React.useState(0);
  const [loaded, setLoaded] = React.useState(false);
  // Reset on ticker change
  React.useEffect(() => {
    setIdx(0);
    setLoaded(false);
  }, [ticker]);
  // Hard timeout per source — if the image hasn't loaded in 4s, advance
  // to the next source. Mobile Safari sometimes never fires onError on
  // slow/blocked images, leaving the broken-image placeholder visible.
  React.useEffect(() => {
    if (loaded) return undefined;
    if (idx >= sources.length) return undefined;
    const timer = setTimeout(() => {
      setIdx(i => i + 1);
    }, 4000);
    return () => clearTimeout(timer);
  }, [idx, loaded, sources.length]);
  // All sources exhausted: show text fallback
  if (idx >= sources.length) {
    return /*#__PURE__*/React.createElement("div", {
      className: "sb-ticker-symbol-fallback"
    }, ticker);
  }
  // While loading: show text underneath, but render an invisible img to
  // probe the URL. Once it loads, swap to the image. This prevents the
  // broken-image glyph from ever flashing.
  return /*#__PURE__*/React.createElement(React.Fragment, null, !loaded && /*#__PURE__*/React.createElement("div", {
    className: "sb-ticker-symbol-fallback"
  }, ticker), /*#__PURE__*/React.createElement("img", {
    key: `${ticker}-${idx}`,
    src: sources[idx],
    alt: "",
    "aria-hidden": "true",
    className: "sb-ticker-logo",
    loading: "eager",
    decoding: "async",
    referrerPolicy: "no-referrer",
    style: loaded ? undefined : {
      display: "none"
    },
    onLoad: () => setLoaded(true),
    onError: () => {
      setLoaded(false);
      setIdx(i => i + 1);
    }
  }));
}
function VolSkewCard({
  calls,
  puts,
  currentPrice,
  ticker,
  sugCall,
  sugPut,
  activeExpDate,
  chartColors
}) {
  const svgRef = useRef(null);
  const [hover, setHover] = useState(null); // {strike, callIv, putIv, x, y}

  // Reset hover whenever the underlying data changes so a stale hover
  // never points to a strike no longer in scope. This MUST run before any
  // of the early `return null` guards below — otherwise the hook count
  // changes between renders (sparse data returns early and skips it),
  // which throws React error #300 and trips the card's error boundary
  // until the user clicks Retry.
  useEffect(() => {
    setHover(null);
  }, [ticker, calls.length, puts.length]);

  // Filter to plausible IVs. Anything > 500% is almost certainly a stale
  // quote on a deep-ITM/OTM strike with no real bid; exclude it.
  const callsWithIv = calls.filter(c => c.iv && c.iv > 0 && c.iv < 5);
  const putsWithIv = puts.filter(p => p.iv && p.iv > 0 && p.iv < 5);
  if (callsWithIv.length < 4 && putsWithIv.length < 4) return null;
  const allStrikesK = Array.from(new Set([...callsWithIv.map(c => c.strike), ...putsWithIv.map(p => p.strike)])).sort((a, b) => a - b);
  const lo = currentPrice * 0.75,
    hi = currentPrice * 1.25;
  const ks = allStrikesK.filter(k => k >= lo && k <= hi);
  if (ks.length < 4) return null;
  const W = 1200,
    H = 240,
    pL = 56,
    pR = 16,
    pT = 24,
    pB = 32;
  const innerW = W - pL - pR,
    innerH = H - pT - pB;
  const xMin = ks[0],
    xMax = ks[ks.length - 1];
  const xScale = v => pL + (v - xMin) / (xMax - xMin) * innerW;

  // Y-scale ONLY uses IVs from the visible (±25% of spot) strike range,
  // and trims the top 2% percentile so a single freak quote doesn't
  // dictate the whole y-axis. This was the bug Jerry hit — y-axis went
  // to 450% even though no visible point came near that.
  const visCallIvs = callsWithIv.filter(c => c.strike >= lo && c.strike <= hi).map(c => c.iv);
  const visPutIvs = putsWithIv.filter(p => p.strike >= lo && p.strike <= hi).map(p => p.iv);
  const visIvs = [...visCallIvs, ...visPutIvs].sort((a, b) => a - b);
  if (!visIvs.length) return null;
  const trimIdx = Math.max(0, Math.floor(visIvs.length * 0.98) - 1);
  const ivCeiling = visIvs[trimIdx];
  const iMin = Math.max(0, visIvs[0] * 0.92);
  const iMax = ivCeiling * 1.06;
  // Clamp values at iMax so any clipped freak quote sits on the top
  // edge instead of disappearing.
  const yScale = v => pT + (1 - (Math.min(v, iMax) - iMin) / (iMax - iMin)) * innerH;
  const buildPath = rows => {
    const sorted = [...rows].sort((a, b) => a.strike - b.strike).filter(r => r.strike >= lo && r.strike <= hi);
    if (!sorted.length) return "";
    return sorted.map((r, i) => `${i === 0 ? "M" : "L"} ${xScale(r.strike)} ${yScale(r.iv)}`).join(" ");
  };
  const callPath = buildPath(callsWithIv);
  const putPath = buildPath(putsWithIv);
  const nearestK = ks.reduce((a, b) => Math.abs(a - currentPrice) < Math.abs(b - currentPrice) ? a : b);
  const nearestCall = callsWithIv.find(c => c.strike === nearestK);
  const nearestPut = putsWithIv.find(p => p.strike === nearestK);
  const atmIv = nearestCall && nearestPut ? (nearestCall.iv + nearestPut.iv) / 2 : nearestCall ? nearestCall.iv : nearestPut ? nearestPut.iv : 0;
  const lookupIv = (rows, target) => {
    if (!rows.length) return null;
    const r = rows.reduce((a, b) => Math.abs(a.strike - target) < Math.abs(b.strike - target) ? a : b);
    return r.iv;
  };
  const otmPutIv = lookupIv(putsWithIv, currentPrice * 0.95);
  const otmCallIv = lookupIv(callsWithIv, currentPrice * 1.05);
  const skew25 = otmPutIv && otmCallIv ? (otmPutIv - otmCallIv) * 100 : null;

  // Persist daily snapshot for the trend sparkline (unchanged from v17).
  const SKEW_KEY = "weeklyOptionsTimer.skewHistory.v1";
  let skewHistory = [];
  try {
    const raw = localStorage.getItem(SKEW_KEY);
    const all = raw ? JSON.parse(raw) : {};
    const todayStr = new Date().toISOString().slice(0, 10);
    const list = all[ticker] || [];
    const last = list[list.length - 1];
    const sample = {
      d: todayStr,
      atm: atmIv,
      sk: skew25
    };
    let updated;
    if (last && last.d === todayStr) {
      updated = [...list.slice(0, -1), sample];
    } else {
      updated = [...list, sample];
    }
    updated = updated.slice(-90);
    all[ticker] = updated;
    localStorage.setItem(SKEW_KEY, JSON.stringify(all));
    skewHistory = updated;
  } catch {}
  const xTicks = (() => {
    const ticks = [];
    const stepBase = Math.max(1, Math.round((xMax - xMin) / 8));
    const step = stepBase >= 50 ? 50 : stepBase >= 20 ? 20 : stepBase >= 10 ? 10 : 5;
    for (let v = Math.ceil(xMin / step) * step; v <= xMax; v += step) ticks.push(v);
    return ticks;
  })();
  const yTicks = [];
  {
    const range = iMax - iMin;
    const step = range > 1 ? 0.25 : range > 0.5 ? 0.1 : range > 0.2 ? 0.05 : 0.02;
    for (let v = Math.ceil(iMin / step) * step; v <= iMax; v += step) yTicks.push(v);
  }

  // Hover handler — converts mouse X back to a strike, then finds the
  // nearest call+put rows. Uses native pixel→viewBox math because the
  // SVG uses preserveAspectRatio and its pixel size != its viewBox.
  const onMove = e => {
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const vbX = (e.clientX - rect.left) / rect.width * W;
    if (vbX < pL || vbX > W - pR) {
      setHover(null);
      return;
    }
    const targetK = xMin + (vbX - pL) / innerW * (xMax - xMin);
    const nearestVisK = ks.reduce((a, b) => Math.abs(a - targetK) < Math.abs(b - targetK) ? a : b);
    const c = callsWithIv.find(x => x.strike === nearestVisK);
    const p = putsWithIv.find(x => x.strike === nearestVisK);
    if (!c && !p) {
      setHover(null);
      return;
    }
    setHover({
      strike: nearestVisK,
      callRow: c,
      putRow: p
    });
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      marginBottom: "var(--row-gap)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "IV by strike · ", activeExpDate.toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric"
  })), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Volatility skew")), /*#__PURE__*/React.createElement("div", {
    className: "vs-stats"
  }, /*#__PURE__*/React.createElement("div", {
    className: "vs-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "vs-stat-lbl"
  }, "ATM IV"), /*#__PURE__*/React.createElement("div", {
    className: "vs-stat-val"
  }, (atmIv * 100).toFixed(1), "%")), skew25 != null && /*#__PURE__*/React.createElement("div", {
    className: "vs-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "vs-stat-lbl"
  }, "Skew (95% put · 105% call)"), /*#__PURE__*/React.createElement("div", {
    className: `vs-stat-val ${skew25 >= 0 ? "down" : "up"}`
  }, skew25 >= 0 ? "+" : "", skew25.toFixed(1), " pts")), skewHistory.length >= 2 && (() => {
    const sw = 110,
      sh = 36,
      sp = 3;
    const vals = skewHistory.map(h => h.sk).filter(v => v != null);
    if (vals.length < 2) return null;
    const vMin = Math.min(...vals),
      vMax = Math.max(...vals);
    const range = vMax - vMin || 1;
    const xS = i => sp + i / Math.max(1, skewHistory.length - 1) * (sw - 2 * sp);
    const yS = v => sp + (1 - (v - vMin) / range) * (sh - 2 * sp);
    const path = skewHistory.map((h, i) => h.sk == null ? null : `${i === 0 ? "M" : "L"} ${xS(i)} ${yS(h.sk)}`).filter(Boolean).join(" ");
    const last = skewHistory[skewHistory.length - 1];
    const first = skewHistory[0];
    const change = last && first && last.sk != null && first.sk != null ? last.sk - first.sk : 0;
    return /*#__PURE__*/React.createElement("div", {
      className: "vs-stat"
    }, /*#__PURE__*/React.createElement("div", {
      className: "vs-stat-lbl"
    }, "Skew · ", skewHistory.length, "d trend"), /*#__PURE__*/React.createElement("svg", {
      width: sw,
      height: sh,
      style: {
        display: "block"
      }
    }, /*#__PURE__*/React.createElement("line", {
      x1: sp,
      x2: sw - sp,
      y1: yS(0),
      y2: yS(0),
      stroke: chartColors.fg3,
      strokeWidth: "1",
      strokeDasharray: "2 3",
      opacity: "0.4"
    }), /*#__PURE__*/React.createElement("path", {
      d: path,
      fill: "none",
      stroke: chartColors.accent,
      strokeWidth: "1.6"
    }), skewHistory.length > 0 && /*#__PURE__*/React.createElement("circle", {
      cx: xS(skewHistory.length - 1),
      cy: yS(last.sk || 0),
      r: "2.5",
      fill: chartColors.accent
    })), /*#__PURE__*/React.createElement("div", {
      style: {
        fontSize: 10,
        color: change >= 0 ? "var(--down)" : "var(--up)",
        fontFamily: "var(--font-mono)"
      }
    }, change >= 0 ? "▲" : "▼", " ", Math.abs(change).toFixed(1), " pts since first sample"));
  })())), /*#__PURE__*/React.createElement("div", {
    className: "vs-svg-wrap",
    style: {
      position: "relative"
    }
  }, /*#__PURE__*/React.createElement("svg", {
    ref: svgRef,
    viewBox: `0 0 ${W} ${H}`,
    className: "vs-svg",
    onMouseMove: onMove,
    onMouseLeave: () => setHover(null)
  }, /*#__PURE__*/React.createElement("rect", {
    x: "0",
    y: "0",
    width: W,
    height: H,
    fill: "transparent"
  }), yTicks.map(t => /*#__PURE__*/React.createElement("g", {
    key: `yg${t}`
  }, /*#__PURE__*/React.createElement("line", {
    x1: pL,
    x2: W - pR,
    y1: yScale(t),
    y2: yScale(t),
    stroke: "currentColor",
    opacity: "0.06"
  }), /*#__PURE__*/React.createElement("text", {
    x: pL - 8,
    y: yScale(t) + 3,
    fontSize: "10",
    textAnchor: "end",
    fill: chartColors.fg3,
    fontFamily: "ui-monospace, monospace"
  }, (t * 100).toFixed(0), "%"))), xTicks.map(t => /*#__PURE__*/React.createElement("g", {
    key: `xg${t}`
  }, /*#__PURE__*/React.createElement("line", {
    x1: xScale(t),
    x2: xScale(t),
    y1: pT,
    y2: H - pB,
    stroke: "currentColor",
    opacity: "0.04"
  }), /*#__PURE__*/React.createElement("text", {
    x: xScale(t),
    y: H - pB + 14,
    fontSize: "10",
    textAnchor: "middle",
    fill: chartColors.fg3,
    fontFamily: "ui-monospace, monospace"
  }, "$", t))), /*#__PURE__*/React.createElement("line", {
    x1: xScale(currentPrice),
    x2: xScale(currentPrice),
    y1: pT,
    y2: H - pB,
    stroke: chartColors.fg2,
    strokeWidth: "1",
    strokeDasharray: "2 3",
    opacity: "0.6"
  }), /*#__PURE__*/React.createElement("text", {
    x: xScale(currentPrice),
    y: pT - 6,
    fontSize: "10",
    textAnchor: "middle",
    fill: chartColors.fg2,
    fontFamily: "ui-monospace, monospace"
  }, "spot"), sugCall > 0 && sugCall >= xMin && sugCall <= xMax && /*#__PURE__*/React.createElement("line", {
    x1: xScale(sugCall),
    x2: xScale(sugCall),
    y1: pT,
    y2: H - pB,
    stroke: chartColors.up,
    strokeWidth: "1",
    strokeDasharray: "3 3",
    opacity: "0.7"
  }), sugPut > 0 && sugPut >= xMin && sugPut <= xMax && /*#__PURE__*/React.createElement("line", {
    x1: xScale(sugPut),
    x2: xScale(sugPut),
    y1: pT,
    y2: H - pB,
    stroke: chartColors.down,
    strokeWidth: "1",
    strokeDasharray: "3 3",
    opacity: "0.7"
  }), callPath && /*#__PURE__*/React.createElement("path", {
    d: callPath,
    fill: "none",
    stroke: chartColors.up,
    strokeWidth: "1.8"
  }), putPath && /*#__PURE__*/React.createElement("path", {
    d: putPath,
    fill: "none",
    stroke: chartColors.down,
    strokeWidth: "1.8"
  }), callsWithIv.filter(c => c.strike >= lo && c.strike <= hi).map(c => /*#__PURE__*/React.createElement("circle", {
    key: `vc${c.strike}`,
    cx: xScale(c.strike),
    cy: yScale(c.iv),
    r: "2.5",
    fill: chartColors.up,
    opacity: "0.85"
  })), putsWithIv.filter(p => p.strike >= lo && p.strike <= hi).map(p => /*#__PURE__*/React.createElement("circle", {
    key: `vp${p.strike}`,
    cx: xScale(p.strike),
    cy: yScale(p.iv),
    r: "2.5",
    fill: chartColors.down,
    opacity: "0.85"
  })), hover && /*#__PURE__*/React.createElement("g", {
    pointerEvents: "none"
  }, /*#__PURE__*/React.createElement("line", {
    x1: xScale(hover.strike),
    x2: xScale(hover.strike),
    y1: pT,
    y2: H - pB,
    stroke: chartColors.fg2,
    strokeWidth: "1",
    opacity: "0.55"
  }), hover.callRow && /*#__PURE__*/React.createElement("circle", {
    cx: xScale(hover.strike),
    cy: yScale(hover.callRow.iv),
    r: "5",
    fill: chartColors.up,
    stroke: "white",
    strokeWidth: "1.5"
  }), hover.putRow && /*#__PURE__*/React.createElement("circle", {
    cx: xScale(hover.strike),
    cy: yScale(hover.putRow.iv),
    r: "5",
    fill: chartColors.down,
    stroke: "white",
    strokeWidth: "1.5"
  }))), hover && /*#__PURE__*/React.createElement("div", {
    className: "vs-tooltip",
    style: {
      left: `${xScale(hover.strike) / W * 100}%`,
      top: 8
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "vs-tt-head"
  }, "$", hover.strike.toFixed(2)), hover.callRow && /*#__PURE__*/React.createElement("div", {
    className: "vs-tt-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "vs-tt-lbl up"
  }, "Call IV"), /*#__PURE__*/React.createElement("span", {
    className: "vs-tt-val"
  }, (hover.callRow.iv * 100).toFixed(1), "%")), hover.putRow && /*#__PURE__*/React.createElement("div", {
    className: "vs-tt-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "vs-tt-lbl down"
  }, "Put IV"), /*#__PURE__*/React.createElement("span", {
    className: "vs-tt-val"
  }, (hover.putRow.iv * 100).toFixed(1), "%")), hover.callRow && hover.putRow && /*#__PURE__*/React.createElement("div", {
    className: "vs-tt-row vs-tt-spread"
  }, /*#__PURE__*/React.createElement("span", {
    className: "vs-tt-lbl"
  }, "P − C"), /*#__PURE__*/React.createElement("span", {
    className: `vs-tt-val ${hover.putRow.iv - hover.callRow.iv >= 0 ? "down" : "up"}`
  }, ((hover.putRow.iv - hover.callRow.iv) * 100).toFixed(1), " pts")))), /*#__PURE__*/React.createElement("div", {
    className: "legend",
    style: {
      marginTop: 8
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "item"
  }, /*#__PURE__*/React.createElement("span", {
    className: "swatch",
    style: {
      background: chartColors.up,
      height: 2
    }
  }), "Call IV"), /*#__PURE__*/React.createElement("span", {
    className: "item"
  }, /*#__PURE__*/React.createElement("span", {
    className: "swatch",
    style: {
      background: chartColors.down,
      height: 2
    }
  }), "Put IV"), /*#__PURE__*/React.createElement("span", {
    className: "item"
  }, /*#__PURE__*/React.createElement("span", {
    className: "swatch dashed",
    style: {
      borderColor: chartColors.fg2
    }
  }), "Spot"), skew25 != null && skew25 > 0.5 && /*#__PURE__*/React.createElement("span", {
    className: "item",
    style: {
      color: "var(--down)"
    }
  }, "Put skew — downside is more expensive than upside"), skew25 != null && skew25 < -0.5 && /*#__PURE__*/React.createElement("span", {
    className: "item",
    style: {
      color: "var(--up)"
    }
  }, "Call skew — upside is more expensive than downside")));
}
function AnalystBoardCard({
  apiFetch,
  onSwitchTicker
}) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [days, setDays] = useState(2);
  const [fType, setFType] = useState("all");
  const [fDir, setFDir] = useState("all");
  const [fSector, setFSector] = useState("all");
  const [fCap, setFCap] = useState("all");
  const [fHigh, setFHigh] = useState(false);
  const [fToday, setFToday] = useState(false);
  const [q, setQ] = useState("");
  const pollRef = useRef(null);
  const load = async () => {
    try {
      const r = await apiFetch("/api/analyst_board");
      const d = await r.json();
      setBoard(d);
      return d;
    } catch (e) {
      setErr(String(e));
      return null;
    }
  };
  useEffect(() => {
    load();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);
  const startScan = async () => {
    setErr(null);
    try {
      await apiFetch(`/api/analyst_board/scan?days=${days}&force=1`);
    } catch (e) {
      setErr(String(e));
      return;
    }
    await load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 4000);
  };
  const status = board && board.status || {};
  const actions = board && board.actions || [];
  const summary = board && board.summary || {};
  const scanning = !!status.scanning;
  const sectors = useMemo(() => Array.from(new Set(actions.map(a => a.sector).filter(Boolean))).sort(), [actions]);
  const capBucket = mc => {
    if (!mc) return "unknown";
    const b = mc / 1e9;
    if (b >= 200) return "mega";
    if (b >= 50) return "large";
    if (b >= 10) return "mid";
    return "small";
  };
  const filtered = useMemo(() => actions.filter(a => {
    if (fType !== "all" && a.action_class !== fType) return false;
    if (fDir !== "all" && a.direction !== fDir) return false;
    if (fSector !== "all" && a.sector !== fSector) return false;
    if (fCap !== "all" && capBucket(a.market_cap) !== fCap) return false;
    if (fHigh && a.importance !== "high") return false;
    if (fToday) {
      const dt = new Date(String(a.date || "").slice(0, 10) + "T00:00:00");
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      if (isNaN(dt) || Math.round((today - dt) / 86400000) !== 0) return false;
    }
    if (q) {
      const s = q.toLowerCase();
      if (!String(a.ticker || "").toLowerCase().includes(s) && !String(a.firm || "").toLowerCase().includes(s)) return false;
    }
    return true;
  }), [actions, fType, fDir, fSector, fCap, fHigh, fToday, q]);
  const fmtPct = v => v == null ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(2) + "%";
  const fmtCap = fmtMktCap;
  const fmt$ = v => fmtUsd(v);
  const fmtDate = d => {
    if (!d) return "";
    const s = String(d).slice(0, 10);
    const dt = new Date(s + "T00:00:00");
    if (isNaN(dt)) return s;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const days = Math.round((today - dt) / 86400000);
    const rel = days <= 0 ? "today" : days === 1 ? "yesterday" : `${days}d ago`;
    const md = dt.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric"
    });
    return `${md} · ${rel}`;
  };
  const actLabel = {
    upgrade: "Upgrade",
    downgrade: "Downgrade",
    initiate: "Initiation",
    reiterate: "Reiterate",
    target_change: "PT change"
  };
  const Chips = ({
    rows,
    withScore
  }) => /*#__PURE__*/React.createElement("div", {
    className: "ab-chips"
  }, (rows || []).length === 0 && /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 12
    }
  }, "—"), (rows || []).map((a, i) => /*#__PURE__*/React.createElement("button", {
    key: a.ticker + i,
    className: `ab-chip ab-${a.direction || "neutral"}`,
    onClick: () => onSwitchTicker(a.ticker),
    title: (a.reasons || []).join(" · ")
  }, a.ticker, a.multi_count > 1 ? ` ·${a.multi_count}` : "", withScore && /*#__PURE__*/React.createElement("b", null, Math.round(a.score)))));
  const SummaryBox = ({
    title,
    children,
    tone
  }) => /*#__PURE__*/React.createElement("div", {
    className: `ab-sumbox ${tone || ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-sumbox-title"
  }, title), children);
  return /*#__PURE__*/React.createElement("div", {
    className: "card ab-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Pre-market game plan"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Analyst actions that matter")), /*#__PURE__*/React.createElement("div", {
    className: "ab-controls"
  }, /*#__PURE__*/React.createElement("select", {
    className: "sb-select ab-days",
    value: days,
    onChange: e => setDays(+e.target.value),
    title: "How far back to look for fresh actions"
  }, /*#__PURE__*/React.createElement("option", {
    value: 1
  }, "Today"), /*#__PURE__*/React.createElement("option", {
    value: 2
  }, "2 days"), /*#__PURE__*/React.createElement("option", {
    value: 3
  }, "3 days"), /*#__PURE__*/React.createElement("option", {
    value: 7
  }, "1 week")), /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: startScan,
    disabled: scanning
  }, scanning ? "Scanning…" : "Scan now"))), /*#__PURE__*/React.createElement("div", {
    className: "ab-status"
  }, status.last_scan ? /*#__PURE__*/React.createElement("span", null, "Last scan ", new Date(status.last_scan).toLocaleString(), " · ", status.universe_size || 0, " names · ", actions.length, " actions") : /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "No scan yet — click ", /*#__PURE__*/React.createElement("b", null, "Scan now"), " (a full ~600-name scan takes a few minutes)."), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " · Auto-scans weekdays 8:00 AM ET"), err && /*#__PURE__*/React.createElement("span", {
    className: "ab-err"
  }, " · ", err)), scanning && /*#__PURE__*/React.createElement("div", {
    className: "ab-progress"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-progress-bar",
    style: {
      width: `${status.total ? status.scanned / status.total * 100 : 0}%`
    }
  }), /*#__PURE__*/React.createElement("span", {
    className: "ab-progress-txt"
  }, status.scanned || 0, " / ", status.total || 0)), actions.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-summary"
  }, /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Top bullish",
    tone: "up"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.top_bullish,
    withScore: true
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Top bearish",
    tone: "down"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.top_bearish,
    withScore: true
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Multiple firms"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.multi_action
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Biggest pre-market"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.biggest_premarket
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Looks meaningful",
    tone: "up"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.meaningful,
    withScore: true
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Weak / suspicious",
    tone: "warn"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.suspicious
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Sectors — bullish"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-sectors"
  }, (summary.sectors_positive || []).map(s => /*#__PURE__*/React.createElement("span", {
    key: s.sector,
    className: "ab-sectchip up"
  }, s.sector, /*#__PURE__*/React.createElement("b", null, s.count))))), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Sectors — bearish"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-sectors"
  }, (summary.sectors_negative || []).map(s => /*#__PURE__*/React.createElement("span", {
    key: s.sector,
    className: "ab-sectchip down"
  }, s.sector, /*#__PURE__*/React.createElement("b", null, s.count))))), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Watch after open"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.watch_after_open,
    withScore: true
  }))), actions.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-filters"
  }, /*#__PURE__*/React.createElement("input", {
    className: "sb-select ab-search",
    placeholder: "Ticker or firm…",
    value: q,
    onChange: e => setQ(e.target.value)
  }), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fType,
    onChange: e => setFType(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "All actions"), /*#__PURE__*/React.createElement("option", {
    value: "upgrade"
  }, "Upgrades"), /*#__PURE__*/React.createElement("option", {
    value: "downgrade"
  }, "Downgrades"), /*#__PURE__*/React.createElement("option", {
    value: "initiate"
  }, "Initiations"), /*#__PURE__*/React.createElement("option", {
    value: "target_change"
  }, "PT changes"), /*#__PURE__*/React.createElement("option", {
    value: "reiterate"
  }, "Reiterations")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fDir,
    onChange: e => setFDir(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Bull & bear"), /*#__PURE__*/React.createElement("option", {
    value: "bull"
  }, "Bullish"), /*#__PURE__*/React.createElement("option", {
    value: "bear"
  }, "Bearish")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fCap,
    onChange: e => setFCap(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any cap"), /*#__PURE__*/React.createElement("option", {
    value: "mega"
  }, "Mega (≥$200B)"), /*#__PURE__*/React.createElement("option", {
    value: "large"
  }, "Large ($50–200B)"), /*#__PURE__*/React.createElement("option", {
    value: "mid"
  }, "Mid ($10–50B)"), /*#__PURE__*/React.createElement("option", {
    value: "small"
  }, "Small (<$10B)")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fSector,
    onChange: e => setFSector(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "All sectors"), sectors.map(s => /*#__PURE__*/React.createElement("option", {
    key: s,
    value: s
  }, s))), /*#__PURE__*/React.createElement("label", {
    className: "ab-toggle"
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: fToday,
    onChange: e => setFToday(e.target.checked)
  }), " Today only"), /*#__PURE__*/React.createElement("label", {
    className: "ab-toggle"
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: fHigh,
    onChange: e => setFHigh(e.target.checked)
  }), " High impact only")), /*#__PURE__*/React.createElement("div", {
    className: "ab-board"
  }, actions.length === 0 && !scanning && /*#__PURE__*/React.createElement("div", {
    className: "ab-empty"
  }, "No analyst actions yet. Run a scan to build this morning's board."), filtered.map((a, i) => /*#__PURE__*/React.createElement("div", {
    key: a.ticker + a.firm + i,
    className: "ab-row",
    onClick: () => onSwitchTicker(a.ticker),
    title: "Open this ticker on the Trade tab"
  }, /*#__PURE__*/React.createElement("div", {
    className: `ab-scorebadge imp-${a.importance}`
  }, Math.round(a.score)), /*#__PURE__*/React.createElement("div", {
    className: "ab-rowmain"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-rowtop"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ab-tk"
  }, a.ticker), /*#__PURE__*/React.createElement("span", {
    className: `ab-pill ab-${a.direction}`
  }, actLabel[a.action_class] || a.action_class), a.multi_count > 1 && /*#__PURE__*/React.createElement("span", {
    className: "ab-pill ab-multi"
  }, a.multi_count, " firms"), a.suspicious && /*#__PURE__*/React.createElement("span", {
    className: "ab-pill ab-warn"
  }, "weak move"), a.date && /*#__PURE__*/React.createElement("span", {
    className: "ab-datepill",
    title: `Analyst action dated ${a.date}`
  }, fmtDate(a.date)), a.company && /*#__PURE__*/React.createElement("span", {
    className: "ab-company"
  }, a.company), /*#__PURE__*/React.createElement("span", {
    className: "ab-sector"
  }, a.sector)), /*#__PURE__*/React.createElement("div", {
    className: "ab-rowsub"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ab-firm"
  }, a.firm || "—"), (a.prior_grade || a.new_grade) && /*#__PURE__*/React.createElement("span", null, a.prior_grade || "—", " → ", /*#__PURE__*/React.createElement("b", null, a.new_grade || "—")), (a.prior_target || a.new_target) && /*#__PURE__*/React.createElement("span", null, "PT ", fmt$(a.prior_target), " → ", /*#__PURE__*/React.createElement("b", null, fmt$(a.new_target)), a.target_change_pct != null ? ` (${fmtPct(a.target_change_pct)})` : ""), /*#__PURE__*/React.createElement("span", {
    className: `ab-pm ${(a.premarket_pct || 0) >= 0 ? "up" : "down"}`
  }, fmtPct(a.premarket_pct), " pre"), /*#__PURE__*/React.createElement("span", {
    className: "ab-cap"
  }, fmtCap(a.market_cap))), a.reasons && a.reasons.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-reasons"
  }, a.reasons.join(" · ")))))));
}

// Shared money formatters for the Discover boards. Comma thousands
// separators everywhere; market caps roll up to T / B / M.
function fmtUsd(v, dp) {
  if (v == null || isNaN(v)) return "—";
  const d = dp == null ? 2 : dp;
  return "$" + Number(v).toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d
  });
}
function fmtMktCap(v) {
  if (!v) return "—";
  if (v >= 1e12) return "$" + (v / 1e12).toLocaleString(undefined, {
    maximumFractionDigits: 2
  }) + "T";
  if (v >= 1e9) return "$" + (v / 1e9).toLocaleString(undefined, {
    maximumFractionDigits: 1
  }) + "B";
  if (v >= 1e6) return "$" + (v / 1e6).toLocaleString(undefined, {
    maximumFractionDigits: 0
  }) + "M";
  return "$" + Number(v).toLocaleString();
}

// Finviz-style market-cap buckets for the watchlist screener. Each is
// [value, label, predicate(marketCapInDollars)].
const MCAP_BUCKETS = [["all", "All caps", () => true], ["mega", "Mega ($200B+)", mc => mc >= 200e9], ["large", "Large ($10–200B)", mc => mc >= 10e9 && mc < 200e9], ["mid", "Mid ($2–10B)", mc => mc >= 2e9 && mc < 10e9], ["small", "Small ($300M–2B)", mc => mc >= 300e6 && mc < 2e9], ["micro", "Micro ($50–300M)", mc => mc >= 50e6 && mc < 300e6], ["nano", "Nano (<$50M)", mc => mc > 0 && mc < 50e6], ["plarge", "+Large (>$10B)", mc => mc >= 10e9], ["pmid", "+Mid (>$2B)", mc => mc >= 2e9], ["psmall", "+Small (>$300M)", mc => mc >= 300e6], ["pmicro", "+Micro (>$50M)", mc => mc >= 50e6], ["nlarge", "-Large (<$200B)", mc => mc > 0 && mc < 200e9], ["nmid", "-Mid (<$10B)", mc => mc > 0 && mc < 10e9], ["nsmall", "-Small (<$2B)", mc => mc > 0 && mc < 2e9], ["nmicro", "-Micro (<$300M)", mc => mc > 0 && mc < 300e6]];
const MCAP_PRED = Object.fromEntries(MCAP_BUCKETS.map(([v,, fn]) => [v, fn]));

// Watchlist Edge model. Collapses the per-stock options-flow fields into a
// single signed conviction score in [-100, +100]: positive = long candidate,
// negative = short. All from data already loaded — no extra UW cost.
//
// Quant principles applied:
//  - Normalize before comparing: premium is judged size-free (lean ratios)
//    and by cross-sectional rank of net premium / market cap, so a small-cap
//    whale bet outranks mega-cap background noise.
//  - Confluence: direction blends flow $ lean, ask-side aggression, sweeps,
//    UW flow score, the stock's SECTOR tilt, and price-trend structure.
//  - Direction x conviction: a clean stacked setup scores high; a muddy one
//    scores low even if it leans the same way (quality, premium rank, rel-vol,
//    alert count, and price agreement drive conviction).
//  - Risk gate: earnings within 7 days halves the score and flags the row.
function computeWatchlistEdges(rows) {
  if (!rows || !rows.length) return rows || [];
  const clip = (x, a, b) => Math.max(a, Math.min(b, x));

  // Sector tilt: net-premium lean per sector, size-free, in [-1, +1].
  const secAgg = new Map();
  rows.forEach(r => {
    if (!r.flow_available) return;
    const k = r.sector || "—";
    const s = secAgg.get(k) || {
      bull: 0,
      bear: 0
    };
    s.bull += r.call_prem || 0;
    s.bear += r.put_prem || 0;
    secAgg.set(k, s);
  });
  const sectorTilt = new Map();
  secAgg.forEach((v, k) => {
    const tot = v.bull + v.bear;
    sectorTilt.set(k, tot > 0 ? (v.bull - v.bear) / tot : 0);
  });

  // Cross-sectional rank of premium intensity (|net prem| / market cap).
  const intens = rows.filter(r => r.flow_available).map(r => r.market_cap > 0 ? Math.abs(r.net_prem || 0) / r.market_cap : 0).sort((a, b) => a - b);
  const pctRank = x => {
    if (!intens.length) return 0;
    let lo = 0,
      hi = intens.length;
    while (lo < hi) {
      const m = lo + hi >> 1;
      if (intens[m] <= x) lo = m + 1;else hi = m;
    }
    return lo / intens.length;
  };
  return rows.map(r => {
    if (!r.flow_available) return {
      ...r,
      edge: null,
      setup: null,
      prem_sell: null,
      edge_er: false,
      edge_tip: "No flow data — run a scan"
    };
    const cp = r.call_prem || 0,
      pp = r.put_prem || 0;
    const ac = r.ask_call_prem || 0,
      ap = r.ask_put_prem || 0;
    const cs = r.call_sweeps || 0,
      ps = r.put_sweeps || 0;

    // Direction: size-free leans, positive = bullish.
    const premTilt = (cp - pp) / (cp + pp + 1);
    const askTilt = (ac - ap) / (ac + ap + 1);
    const sweepTilt = (cs - ps) / (cs + ps + 1);
    const flowTilt = clip((r.flow_net || 0) / 60, -1, 1);
    const secTilt = sectorTilt.get(r.sector || "—") || 0;
    const trendTilt = clip((r.from_ma50 != null ? r.from_ma50 : 0) / 15, -1, 1);
    const D = 0.28 * premTilt + 0.20 * askTilt + 0.16 * flowTilt + 0.08 * sweepTilt + 0.16 * secTilt + 0.12 * trendTilt;

    // Price-trend confirmation shrinks (never flips) conviction on divergence.
    const agreeMult = r.flow_agree === "agrees" ? 1.0 : r.flow_agree === "disagrees" ? 0.55 : 0.8;

    // Conviction (cleanliness of the signal), ~0.15..1.0.
    const intensityPct = pctRank(r.market_cap > 0 ? Math.abs(r.net_prem || 0) / r.market_cap : 0);
    const quality = clip((r.flow_quality || 0) / 100, 0, 1);
    const relvol = clip((r.rel_vol || 0) / 2, 0, 1);
    const alerts = clip((r.flow_alerts || 0) / 15, 0, 1);
    const K = 0.15 + 0.25 * quality + 0.25 * intensityPct + 0.20 * relvol + 0.15 * alerts;
    let edge = 100 * D * agreeMult * (0.4 + 0.6 * K);
    const er = r.days_to_earnings != null && r.days_to_earnings >= 0 && r.days_to_earnings <= 7;
    if (er) edge *= 0.5; // earnings: flag + dampen
    edge = Math.round(clip(edge, -100, 100));
    const dir = edge >= 15 ? "long" : edge <= -15 ? "short" : "mixed";
    const strength = Math.abs(edge) >= 50 ? "strong" : Math.abs(edge) >= 25 ? "building" : "weak";
    let setup = dir === "long" ? "Long" : dir === "short" ? "Short" : "Mixed";
    if (dir !== "mixed") setup += " · " + strength;

    // Premium-selling lens (both lenses): sell puts under bullish flow, sell
    // calls under bearish — flag squeeze risk when CC-Risk is high.
    let prem_sell = "—";
    if (dir === "long") prem_sell = "Sell puts";else if (dir === "short") prem_sell = r.flow_cc_risk != null && r.flow_cc_risk >= 60 ? "Sell calls ⚠" : "Sell calls";

    // Driver breakdown for the hover tooltip.
    const parts = [];
    const tag = (label, v) => {
      if (Math.abs(v) >= 0.08) parts.push((v > 0 ? "+" : "−") + label);
    };
    tag("flow$", premTilt);
    tag("ask-side", askTilt);
    tag("sweeps", sweepTilt);
    tag("flow-score", flowTilt);
    tag("sector", secTilt);
    tag("trend", trendTilt);
    let tip = `Edge ${edge > 0 ? "+" : ""}${edge} (${setup}). Drivers: ${parts.join(", ") || "balanced"}.`;
    tip += ` Conviction: quality ${Math.round(quality * 100)}, premium-rank ${Math.round(intensityPct * 100)}, ${r.rel_vol || 0}× vol`;
    tip += r.flow_agree === "agrees" ? ", price confirms" : r.flow_agree === "disagrees" ? ", price diverges" : "";
    if (er) tip += `. ⚠ Earnings in ${r.days_to_earnings}d — score halved`;
    return {
      ...r,
      edge,
      setup,
      prem_sell,
      edge_er: er,
      edge_dir: dir,
      edge_tip: tip
    };
  });
}

// MM-DD-YYYY (e.g. 6-19-2026) from an ISO YYYY-MM-DD string.
function fmtSwingDate(s) {
  if (!s) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s));
  if (!m) return String(s);
  return `${+m[2]}-${+m[3]}-${m[1]}`;
}
function NewsCard({
  apiFetch,
  ticker,
  companyName
}) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [src, setSrc] = useState("all");
  const load = async sym => {
    if (!sym) return;
    setLoading(true);
    setErr(null);
    try {
      const nm = companyName ? `&name=${encodeURIComponent(companyName)}` : "";
      const r = await apiFetch(`/api/news?symbol=${encodeURIComponent(sym)}${nm}`);
      const d = await r.json();
      if (d.error && !(d.items || []).length) setErr(d.error);else setData(d);
    } catch (e) {
      setErr(String(e));
    }
    setLoading(false);
  };
  useEffect(() => {
    setSrc("all");
    load(ticker); /* eslint-disable-next-line */
  }, [ticker, companyName]);
  const items = data && data.items || [];
  const sources = data && data.sources || [];
  const shown = items.filter(i => src === "all" || i.source === src);
  return /*#__PURE__*/React.createElement("div", {
    className: "card ab-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "News · ", ticker), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Latest headlines")), /*#__PURE__*/React.createElement("div", {
    className: "ab-controls"
  }, /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: () => load(ticker),
    disabled: loading
  }, loading ? "Loading…" : "Refresh"))), err && /*#__PURE__*/React.createElement("div", {
    className: "ab-status"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ab-err"
  }, err)), loading && !data && /*#__PURE__*/React.createElement("div", {
    className: "skel-list"
  }, [0, 1, 2, 3, 4].map(i => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "skel-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "skel skel-when"
  }), /*#__PURE__*/React.createElement("span", {
    className: "skel skel-line"
  }), /*#__PURE__*/React.createElement("span", {
    className: "skel skel-tag"
  })))), data && /*#__PURE__*/React.createElement("div", {
    className: "ab-status"
  }, items.length, " headlines from ", sources.length, " source", sources.length === 1 ? "" : "s", " ", "· aggregated from Yahoo Finance & Finnhub (free)"), sources.length > 1 && /*#__PURE__*/React.createElement("div", {
    className: "news-srcnav"
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: src === "all" ? "active" : "",
    onClick: () => setSrc("all")
  }, "All (", items.length, ")"), sources.map(s => /*#__PURE__*/React.createElement("button", {
    key: s,
    type: "button",
    className: src === s ? "active" : "",
    onClick: () => setSrc(s)
  }, s, " (", items.filter(i => i.source === s).length, ")"))), shown.length > 0 ? /*#__PURE__*/React.createElement("div", {
    className: "news-list"
  }, shown.map((it, i) => /*#__PURE__*/React.createElement("a", {
    key: i,
    className: "news-row",
    href: it.url || "#",
    target: "_blank",
    rel: "noopener noreferrer",
    title: it.summary || it.title
  }, /*#__PURE__*/React.createElement("span", {
    className: "news-when"
  }, /*#__PURE__*/React.createElement("span", {
    className: "news-abs"
  }, it.date_label || "—"), /*#__PURE__*/React.createElement("span", {
    className: "news-age"
  }, it.time_label || it.age || "")), /*#__PURE__*/React.createElement("span", {
    className: "news-body"
  }, /*#__PURE__*/React.createElement("span", {
    className: "news-title"
  }, it.title, it.day_change != null && /*#__PURE__*/React.createElement("span", {
    className: `news-chg ${it.day_change >= 0 ? "up" : "down"}`
  }, it.day_change >= 0 ? "+" : "", it.day_change, "%")), it.summary && /*#__PURE__*/React.createElement("span", {
    className: "news-summary"
  }, it.summary)), /*#__PURE__*/React.createElement("span", {
    className: "news-src"
  }, it.source)))) : !err && !loading && /*#__PURE__*/React.createElement("div", {
    className: "ab-empty"
  }, "No recent headlines for ", ticker, "."));
}

// ── TradingView Charting Library integration ────────────────────────────
// Activates ONLY when the licensed library files are present at
// /charting_library/charting_library.standalone.js (committed by the owner
// after TradingView grants access). Until then, callers fall back to the
// open-source Lightweight Charts SwingChart below. Untested until the real
// library files are in the repo — will be tuned once they are.
const _dms = d => Date.parse(String(d) + "T00:00:00Z");
function makeSwingDatafeed(apiFetch) {
  return {
    onReady: cb => setTimeout(() => cb({
      supported_resolutions: ["1D"],
      supports_time: true,
      supports_marks: false,
      supports_timescale_marks: false
    }), 0),
    searchSymbols: (_u, _e, _t, onResult) => onResult([]),
    resolveSymbol: (name, onResolve) => setTimeout(() => onResolve({
      name,
      ticker: name,
      description: name,
      type: "stock",
      session: "0930-1600",
      timezone: "America/New_York",
      exchange: "",
      minmov: 1,
      pricescale: 100,
      has_intraday: false,
      supported_resolutions: ["1D"],
      volume_precision: 0,
      data_status: "streaming"
    }), 0),
    getBars: (symbolInfo, _res, periodParams, onResult, onError) => {
      apiFetch(`/api/swings?symbol=${encodeURIComponent(symbolInfo.name)}`).then(r => r.json()).then(d => {
        const bars = (d.bars || []).map(b => ({
          time: _dms(b.t),
          open: b.o,
          high: b.h,
          low: b.l,
          close: b.c,
          volume: b.v
        })).filter(x => x.time / 1000 >= periodParams.from && x.time / 1000 <= periodParams.to).sort((a, b) => a.time - b.time);
        onResult(bars, {
          noData: bars.length === 0
        });
      }).catch(e => onError(String(e)));
    },
    subscribeBars: () => {},
    unsubscribeBars: () => {}
  };
}
function TVAdvancedChart({
  apiFetch,
  ticker,
  data,
  fallback
}) {
  const ref = useRef(null);
  const widgetRef = useRef(null);
  const [mode, setMode] = useState("loading"); // loading | tv | fallback
  const [collapsed, setCollapsed] = useState(() => typeof window !== "undefined" && window.innerWidth <= 900);

  // Detect whether the licensed library is available (load the script once).
  useEffect(() => {
    let cancelled = false;
    const done = ok => {
      if (!cancelled) setMode(ok ? "tv" : "fallback");
    };
    if (window.TradingView && window.TradingView.widget) {
      done(true);
      return;
    }
    // Only attempt to load the licensed Charting Library when explicitly
    // enabled (set window.__CHARTING_LIBRARY = true in config.js once the
    // files are committed). Otherwise go straight to the Lightweight chart —
    // no wasted 404 request or loading flash for everyone else.
    const enabled = window.__CHARTING_LIBRARY === true || window.__APP_CONFIG && window.__APP_CONFIG.chartingLibrary === true;
    if (!enabled) {
      done(false);
      return;
    }
    const timer = setTimeout(() => done(false), 5000);
    const finish = ok => {
      clearTimeout(timer);
      done(ok);
    };
    const existing = document.getElementById("tv-charting-lib");
    if (existing) {
      if (existing.dataset.loaded === "1") {
        finish(true);
        return () => {
          cancelled = true;
          clearTimeout(timer);
        };
      }
      existing.addEventListener("load", () => finish(true));
      existing.addEventListener("error", () => finish(false));
      return () => {
        cancelled = true;
        clearTimeout(timer);
      };
    }
    const s = document.createElement("script");
    s.id = "tv-charting-lib";
    s.src = "/charting_library/charting_library.standalone.js";
    s.onload = () => {
      s.dataset.loaded = "1";
      finish(true);
    };
    s.onerror = () => finish(false);
    document.head.appendChild(s);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  // Create the TradingView widget + draw the swing overlays.
  useEffect(() => {
    if (mode !== "tv" || collapsed || !ref.current || !window.TradingView) return;
    let widget;
    try {
      widget = new window.TradingView.widget({
        container: ref.current,
        library_path: "/charting_library/",
        datafeed: makeSwingDatafeed(apiFetch),
        symbol: ticker,
        interval: "1D",
        theme: "dark",
        autosize: true,
        timezone: "America/New_York",
        disabled_features: ["use_localstorage_for_settings", "header_symbol_search", "header_compare"]
      });
      widgetRef.current = widget;
      widget.onChartReady(() => {
        try {
          const chart = widget.activeChart();
          const lastT = data && data.bars && data.bars.length ? _dms(data.bars[data.bars.length - 1].t) / 1000 : null;
          const drawSwing = (s, color) => chart.createMultipointShape([{
            time: _dms(s.low_date) / 1000,
            price: s.low_price
          }, {
            time: _dms(s.high_date) / 1000,
            price: s.high_price
          }], {
            shape: "trend_line",
            lock: true,
            disableSave: true,
            disableSelection: true,
            overrides: {
              linecolor: color,
              linewidth: 2,
              linestyle: 0
            }
          });
          (data.swings || []).forEach(s => drawSwing(s, "#22c55e"));
          (data.down_swings || []).forEach(s => drawSwing(s, "#ef4444"));
          const a = data && data.analysis;
          if (a && a.status === "ok" && lastT) {
            const hline = (price, color, txt) => {
              if (price == null) return;
              chart.createShape({
                time: lastT,
                price
              }, {
                shape: "horizontal_line",
                lock: true,
                disableSelection: true,
                overrides: {
                  linecolor: color,
                  linestyle: 2,
                  showLabel: true,
                  text: txt
                }
              });
            };
            if (a.targets) {
              hline(a.targets[1] && a.targets[1].price, "#22c55e", "median");
              hline(a.targets[2] && a.targets[2].price, "#15803d", "aggr");
            }
            if (a.trade_plan) hline(a.trade_plan.invalidation, "#ef4444", "invalidation");
            hline(a.current_price, "rgba(255,255,255,0.6)", "now");
          }
        } catch (e) {
          console.warn("[swing-tv] overlay draw failed:", e);
        }
      });
    } catch (e) {
      console.warn("[swing-tv] widget init failed, falling back:", e);
      setMode("fallback");
    }
    return () => {
      try {
        if (widgetRef.current) widgetRef.current.remove();
      } catch (e) {}
      widgetRef.current = null;
    };
    /* eslint-disable-next-line */
  }, [mode, ticker, collapsed]);
  if (mode === "fallback") return fallback;
  return /*#__PURE__*/React.createElement("div", {
    className: "swing-chart-block"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-chart-head"
  }, /*#__PURE__*/React.createElement("button", {
    className: "swing-chart-toggle",
    onClick: () => setCollapsed(c => !c)
  }, collapsed ? "▸" : "▾", " Swing chart ", /*#__PURE__*/React.createElement("span", {
    className: "swing-tv-badge"
  }, "TradingView"))), !collapsed && mode === "loading" && /*#__PURE__*/React.createElement("div", {
    className: "ab-status muted"
  }, "Loading TradingView charting library…"), !collapsed && mode === "tv" && /*#__PURE__*/React.createElement("div", {
    className: "swing-chart swing-chart-tv",
    ref: ref
  }));
}
function SwingChart({
  data,
  focusKey,
  onPickSwing,
  onClearFocus
}) {
  const LC = window.LightweightCharts;
  const wrapRef = useRef(null);
  const chartRef = useRef(null);
  const candleRef = useRef(null);
  const volRef = useRef(null);
  const overlayRef = useRef({
    lines: [],
    priceLines: []
  });
  const [show, setShow] = useState({
    markers: true,
    lines: true,
    up: true,
    down: false,
    current: true,
    targets: true,
    labels: true
  });
  const [ohlc, setOhlc] = useState(null); // crosshair hover readout (O/H/L/C/Chg/Vol)
  const [collapsed, setCollapsed] = useState(() => typeof window !== "undefined" && window.innerWidth <= 900);
  const bars = data && data.bars || [];
  const upSw = data && data.swings || [];
  const downSw = data && data.down_swings || [];
  const a = data && data.analysis;
  const UPC = "#22c55e",
    DNC = "#ef4444";

  // Default "home" view = last ~6 months (126 trading days), not the full year.
  const applyHome = () => {
    const n = bars.length;
    if (!n || !chartRef.current) return;
    try {
      chartRef.current.timeScale().setVisibleRange({
        from: bars[Math.max(0, n - 126)].t,
        to: bars[n - 1].t
      });
    } catch (e) {
      try {
        chartRef.current.timeScale().fitContent();
      } catch (e2) {}
    }
  };

  // Create the chart once (re-create when uncollapsed so the container exists).
  useEffect(() => {
    if (!LC || !wrapRef.current || collapsed) return;
    const el = wrapRef.current;
    const chart = LC.createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight || 420,
      layout: {
        background: {
          type: "solid",
          color: "transparent"
        },
        textColor: "#9aa4b2",
        fontFamily: "JetBrains Mono, ui-monospace, monospace"
      },
      grid: {
        vertLines: {
          color: "rgba(255,255,255,0.04)"
        },
        horzLines: {
          color: "rgba(255,255,255,0.06)"
        }
      },
      rightPriceScale: {
        borderColor: "rgba(255,255,255,0.1)"
      },
      timeScale: {
        borderColor: "rgba(255,255,255,0.1)",
        rightOffset: 14,
        fixLeftEdge: true
      },
      crosshair: {
        mode: LC.CrosshairMode.Normal
      }
    });
    const candle = chart.addCandlestickSeries({
      upColor: UPC,
      downColor: DNC,
      borderUpColor: UPC,
      borderDownColor: DNC,
      wickUpColor: UPC,
      wickDownColor: DNC,
      // We draw our own "now" price line, so hide the candle's built-in
      // last-value label + price line (they duplicated/overlapped the
      // now/median/aggr/inval labels and made the right edge unreadable).
      lastValueVisible: false,
      priceLineVisible: false
    });
    const vol = chart.addHistogramSeries({
      priceFormat: {
        type: "volume"
      },
      priceScaleId: "vol",
      lastValueVisible: false,
      priceLineVisible: false
    });
    chart.priceScale("vol").applyOptions({
      scaleMargins: {
        top: 0.84,
        bottom: 0
      }
    });
    chartRef.current = chart;
    candleRef.current = candle;
    volRef.current = vol;
    if (onPickSwing) chart.subscribeClick(p => {
      if (p && p.time) onPickSwing(p.time);
    });
    // Crosshair readout: surface the hovered bar's OHLC / change% / volume.
    chart.subscribeCrosshairMove(p => {
      if (!p || !p.time || !p.seriesData) {
        setOhlc(null);
        return;
      }
      const c = p.seriesData.get(candle);
      if (!c) {
        setOhlc(null);
        return;
      }
      const vd = p.seriesData.get(vol);
      setOhlc({
        time: p.time,
        o: c.open,
        h: c.high,
        l: c.low,
        c: c.close,
        v: vd ? vd.value : null
      });
    });
    const ro = new window.ResizeObserver(() => {
      if (wrapRef.current) chart.applyOptions({
        width: wrapRef.current.clientWidth
      });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      try {
        chart.remove();
      } catch (e) {}
      chartRef.current = null;
      candleRef.current = null;
      volRef.current = null;
    };
    /* eslint-disable-next-line */
  }, [LC, collapsed]);

  // Candles + volume whenever bars change.
  useEffect(() => {
    if (!candleRef.current || !bars.length) return;
    candleRef.current.setData(bars.map(b => ({
      time: b.t,
      open: b.o,
      high: b.h,
      low: b.l,
      close: b.c
    })));
    volRef.current.setData(bars.map(b => ({
      time: b.t,
      value: b.v,
      color: b.c >= b.o ? "rgba(34,197,94,0.30)" : "rgba(239,68,68,0.30)"
    })));
    applyHome();
    /* eslint-disable-next-line */
  }, [data, collapsed]);

  // Swing overlay: markers + connector lines + current-swing price lines.
  useEffect(() => {
    const chart = chartRef.current,
      candle = candleRef.current;
    if (!chart || !candle || !bars.length) return;
    overlayRef.current.lines.forEach(ls => {
      try {
        chart.removeSeries(ls);
      } catch (e) {}
    });
    overlayRef.current.priceLines.forEach(pl => {
      try {
        candle.removePriceLine(pl);
      } catch (e) {}
    });
    overlayRef.current = {
      lines: [],
      priceLines: []
    };
    const fStart = focusKey && focusKey.start,
      fEnd = focusKey && focusKey.end;
    const DIMUP = "rgba(34,197,94,0.22)",
      DIMDN = "rgba(239,68,68,0.22)";
    const markers = [];
    const addSwing = (s, dir) => {
      const lo = s.low_date < s.high_date ? s.low_date : s.high_date;
      const hi = s.low_date < s.high_date ? s.high_date : s.low_date;
      const focused = fStart && lo === fStart && hi === fEnd;
      const dim = fStart && !focused; // something selected, not this
      const c = dir === "up" ? UPC : DNC;
      if (show.markers && !dim) {
        const lbl = show.labels ? `${s.pct_change > 0 ? "+" : ""}${Math.round(s.pct_change)}%` : "";
        markers.push({
          time: s.low_date,
          position: "belowBar",
          color: c,
          shape: "arrowUp",
          text: dir === "down" ? lbl : ""
        });
        markers.push({
          time: s.high_date,
          position: "aboveBar",
          color: c,
          shape: "arrowDown",
          text: dir === "up" ? lbl : ""
        });
      }
      if (show.lines) {
        const lineColor = dim ? dir === "up" ? DIMUP : DIMDN : c;
        const ls = chart.addLineSeries({
          color: lineColor,
          lineWidth: focused ? 3 : 1.5,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false
        });
        const pts = [{
          time: s.low_date,
          value: s.low_price
        }, {
          time: s.high_date,
          value: s.high_price
        }].sort((x, y) => x.time < y.time ? -1 : 1);
        ls.setData(pts);
        overlayRef.current.lines.push(ls);
      }
    };
    if (show.up) upSw.forEach(s => addSwing(s, "up"));
    if (show.down) downSw.forEach(s => addSwing(s, "down"));
    markers.sort((x, y) => x.time < y.time ? -1 : x.time > y.time ? 1 : 0);
    candle.setMarkers(markers);
    if (a && a.status === "ok") {
      // Draw the level lines only — no on-chart/axis labels (they piled up
      // on the right edge over the candles). The values live in the HTML
      // legend rendered over the top-left of the chart instead.
      const mk = (price, color, style) => {
        if (price == null) return;
        overlayRef.current.priceLines.push(candle.createPriceLine({
          price,
          color,
          lineWidth: 1,
          lineStyle: style,
          axisLabelVisible: false
        }));
      };
      if (show.current) mk(a.current_price, "rgba(255,255,255,0.55)", LC.LineStyle.Solid);
      if (show.targets && a.targets) {
        mk(a.targets[1] && a.targets[1].price, UPC, LC.LineStyle.Dashed);
        mk(a.targets[2] && a.targets[2].price, "#15803d", LC.LineStyle.Dotted);
      }
      if (show.current && a.trade_plan) mk(a.trade_plan.invalidation, DNC, LC.LineStyle.Dashed);
    }
    /* eslint-disable-next-line */
  }, [data, show, collapsed, focusKey]);

  // Focus the chart on a selected swing (from a table-row click), or zoom
  // back out to the home view when the selection is cleared.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (focusKey && focusKey.start && focusKey.end) {
      try {
        chart.timeScale().setVisibleRange({
          from: focusKey.start,
          to: focusKey.end
        });
      } catch (e) {}
    } else {
      applyHome();
    }
    /* eslint-disable-next-line */
  }, [focusKey, collapsed]);
  const TOGGLES = [["markers", "Markers"], ["labels", "Labels"], ["lines", "Lines"], ["up", "Up"], ["down", "Down"], ["current", "Current"], ["targets", "Targets"]];

  // Crosshair OHLC readout — hovered bar, falling back to the latest bar.
  const lastBar = bars.length ? bars[bars.length - 1] : null;
  const ro = ohlc || (lastBar ? {
    time: lastBar.t,
    o: lastBar.o,
    h: lastBar.h,
    l: lastBar.l,
    c: lastBar.c,
    v: lastBar.v
  } : null);
  const fmtVol = v => v == null ? "—" : v >= 1e9 ? (v / 1e9).toFixed(2) + "B" : v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(0) + "K" : String(Math.round(v));
  const fmtBarDate = t => typeof t === "string" ? fmtSwingDate(t) : t && t.year ? `${t.month}-${t.day}-${t.year}` : String(t);
  const roChg = ro && ro.o ? (ro.c - ro.o) / ro.o * 100 : null;

  // Level legend (rendered as HTML over the chart so the now/median/aggr/
  // inval prices don't overlap the candles on the right axis).
  const legend = [];
  if (a && a.status === "ok") {
    if (show.current && a.current_price != null) legend.push({
      name: "now",
      price: a.current_price,
      color: "#cbd5e1"
    });
    if (show.targets && a.targets) {
      if (a.targets[1] && a.targets[1].price != null) legend.push({
        name: "median",
        price: a.targets[1].price,
        color: UPC
      });
      if (a.targets[2] && a.targets[2].price != null) legend.push({
        name: "aggr",
        price: a.targets[2].price,
        color: "#15803d"
      });
    }
    if (show.current && a.trade_plan && a.trade_plan.invalidation != null) legend.push({
      name: "inval",
      price: a.trade_plan.invalidation,
      color: DNC
    });
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "swing-chart-block"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-chart-head"
  }, /*#__PURE__*/React.createElement("button", {
    className: "swing-chart-toggle",
    onClick: () => setCollapsed(c => !c)
  }, collapsed ? "▸" : "▾", " Swing chart"), !collapsed && LC && /*#__PURE__*/React.createElement("div", {
    className: "swing-chart-toggles"
  }, TOGGLES.map(([k, lbl]) => /*#__PURE__*/React.createElement("button", {
    key: k,
    className: show[k] ? "on" : "",
    onClick: () => setShow(s => ({
      ...s,
      [k]: !s[k]
    }))
  }, lbl)), /*#__PURE__*/React.createElement("button", {
    onClick: () => {
      applyHome();
      if (onClearFocus) onClearFocus();
    }
  }, "Reset"))), !collapsed && !LC && /*#__PURE__*/React.createElement("div", {
    className: "ab-status muted"
  }, "Chart library didn't load (offline?). The swing table above has the full data."), !collapsed && LC && /*#__PURE__*/React.createElement("div", {
    className: "swing-chart-wrap"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-chart-overlay"
  }, ro && /*#__PURE__*/React.createElement("div", {
    className: "swing-chart-ohlc"
  }, /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, fmtBarDate(ro.time)), /*#__PURE__*/React.createElement("span", null, "O ", /*#__PURE__*/React.createElement("b", null, ro.o.toFixed(2))), /*#__PURE__*/React.createElement("span", null, "H ", /*#__PURE__*/React.createElement("b", null, ro.h.toFixed(2))), /*#__PURE__*/React.createElement("span", null, "L ", /*#__PURE__*/React.createElement("b", null, ro.l.toFixed(2))), /*#__PURE__*/React.createElement("span", null, "C ", /*#__PURE__*/React.createElement("b", {
    className: ro.c >= ro.o ? "up" : "down"
  }, ro.c.toFixed(2))), roChg != null && /*#__PURE__*/React.createElement("span", {
    className: roChg >= 0 ? "up" : "down"
  }, roChg >= 0 ? "+" : "", roChg.toFixed(2), "%"), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "Vol ", fmtVol(ro.v))), legend.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "swing-chart-legend"
  }, legend.map(l => /*#__PURE__*/React.createElement("span", {
    key: l.name,
    className: "swing-legend-item"
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      background: l.color
    }
  }), l.name, " ", /*#__PURE__*/React.createElement("b", null, fmtUsd(l.price, 2)))))), /*#__PURE__*/React.createElement("div", {
    className: "swing-chart",
    ref: wrapRef
  })), !collapsed && LC && /*#__PURE__*/React.createElement("div", {
    className: "swing-chart-hint"
  }, "Tap a candle near a swing to open its row · tap a table row to highlight + zoom to that move · Reset = 6-month view"));
}

// Forward-looking swing projection, derived entirely from the analysis the
// backend already returns (targets, rhythm percentiles, continuation/
// exhaustion scores, structural levels) plus the completed swing list — no
// hardcoded predictions. Adds the pieces not already on the card: Fibonacci
// pullback/bounce zones, the 3 most-similar past moves + what followed, and
// three probability-weighted forward paths.
function computeSwingPrediction(data) {
  const a = data && data.analysis;
  if (!a || a.status !== "ok" || a.current_price == null) return null;
  const up = a.direction === "up";
  const fromP = a.from_price,
    extP = a.extreme_price;
  const vh = a.vs_history || {};
  const levels = a.key_levels || {};
  const tp = a.trade_plan || {};
  const inval = tp.invalidation != null ? tp.invalidation : null;
  const completed = up ? data.swings || [] : data.down_swings || [];
  const opp = up ? data.down_swings || [] : data.swings || [];
  const nSw = Math.max(1, completed.length);
  const r2 = x => Math.round(x * 100) / 100;
  const curAbs = Math.abs(a.current_move_pct || 0);
  const days = a.days_active || 0;
  const median = arr => {
    const v = arr.filter(x => x != null).slice().sort((x, y) => x - y);
    if (!v.length) return 0;
    const m = Math.floor(v.length / 2);
    return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
  };

  // 1 — current move read
  const moveRead = {
    dir: up ? "Up" : "Down",
    fromLabel: a.from_label,
    fromDate: a.from_date,
    pct: a.current_move_pct,
    days,
    perDay: r2(days ? curAbs / days : 0),
    typicalPct: vh.median_pct,
    typicalDays: vh.median_days,
    maturity: a.maturity,
    pctOfMedian: vh.pct_of_median_move,
    signal: a.signal_note
  };

  // 2 — projected targets (probability = share of past swings that ran this far)
  const tgts = (a.targets || []).map(t => ({
    label: t.label,
    price: t.price,
    fromPct: t.from_here_pct,
    reached: t.reached,
    eta: t.eta_date,
    prob: Math.min(100, Math.round((t.matched || 0) / nSw * 100)),
    conf: t.confidence
  }));

  // 3 — pullback / bounce zones, calibrated to THIS stock's OWN retracement
  // history (how far it actually pulls back), so the ranges are tight and
  // realistic instead of a generic Fibonacci grid. opp = the opposite-
  // direction swings = the actual pullbacks/bounces this name has made.
  const span = Math.abs(extP - fromP);
  const depths = opp.map(s => Math.abs(s.pct_change)).filter(x => x > 0).sort((x, y) => x - y);
  const pctile = (arr, q) => {
    if (!arr.length) return null;
    const i = (arr.length - 1) * q;
    const lo = Math.floor(i),
      hi = Math.ceil(i);
    return arr[lo] + (arr[hi] - arr[lo]) * (i - lo);
  };
  const fibBand = (lo, hi) => {
    const x = up ? [extP - span * hi, extP - span * lo] : [extP + span * lo, extP + span * hi];
    return [r2(Math.min(x[0], x[1])), r2(Math.max(x[0], x[1]))];
  };
  let pullback = null;
  if (depths.length >= 3) {
    const iqr = pctile(depths, 0.75) - pctile(depths, 0.25) || 0;
    const bw = Math.max(0.75, Math.min(3, iqr * 0.15)); // tight ± band (percent)
    const zone = d => {
      const a0 = extP * (1 + (up ? -1 : 1) * (d + bw) / 100);
      const b0 = extP * (1 + (up ? -1 : 1) * (d - bw) / 100);
      return [r2(Math.min(a0, b0)), r2(Math.max(a0, b0))];
    };
    pullback = {
      shallow: zone(pctile(depths, 0.25)),
      normal: zone(pctile(depths, 0.5)),
      deep: zone(pctile(depths, 0.75)),
      invalidation: inval,
      basis: "history",
      n: depths.length,
      medDepth: r2(pctile(depths, 0.5))
    };
  } else if (span > 0) {
    pullback = {
      shallow: fibBand(0.30, 0.40),
      normal: fibBand(0.44, 0.54),
      deep: fibBand(0.60, 0.70),
      invalidation: inval,
      basis: "fib",
      n: depths.length
    };
  }
  // Exact structural levels market makers defend (prior pivots) — the tightest
  // reference of all: real prices where this stock has turned before.
  const struct = up ? levels.supports || [] : levels.resistances || [];
  const keyLevels = struct.slice(0, 3).map(l => ({
    price: l.price,
    pctAway: l.pct_away
  }));

  // 4 — continuation / exhaustion scores + reasons
  const scores = {
    continuation: a.continuation_score,
    contFactors: (a.continuation_factors || []).slice(0, 4),
    exhaustion: a.exhaustion_score,
    exhFactors: (a.exhaustion_factors || []).slice(0, 4)
  };

  // 5 — the 3 most-similar completed moves + what happened next
  const medPct = median(completed.map(s => Math.abs(s.pct_change))) || 1;
  const medDays = median(completed.map(s => s.trading_days)) || 1;
  const dist = s => Math.abs(Math.abs(s.pct_change) - curAbs) / medPct + Math.abs(s.trading_days - days) / medDays;
  const activeKey = a.from_date;
  const similar = completed.filter(s => (up ? s.low_date : s.high_date) !== activeKey).slice().sort((x, y) => dist(x) - dist(y)).slice(0, 3).map(s => {
    const sAbs = Math.abs(s.pct_change),
      sDays = s.trading_days;
    let outcome = null;
    if (up) {
      const d = opp.find(o => o.high_date === s.high_date);
      if (d) outcome = {
        kind: "fell",
        pct: r2(Math.abs(d.pct_change)),
        days: d.trading_days
      };
    } else {
      const u = opp.find(o => o.low_date === s.low_date);
      if (u) outcome = {
        kind: "rose",
        pct: r2(Math.abs(u.pct_change)),
        days: u.trading_days
      };
    }
    return {
      lowDate: s.low_date,
      highDate: s.high_date,
      pct: r2(sAbs),
      days: sDays,
      perDay: r2(sDays ? sAbs / sDays : 0),
      outcome
    };
  });

  // 6 — decision (from the backend), 7 — three probability-weighted paths
  const decision = {
    action: a.decision && a.decision.action || "—",
    drivers: a.decision && a.decision.drivers || [],
    note: a.signal_note
  };
  const contS = a.continuation_score != null ? a.continuation_score : 50;
  const exhS = a.exhaustion_score != null ? a.exhaustion_score : 50;
  const tot = contS + exhS + ((contS + exhS) / 2 + 10) || 1;
  const contProb = Math.round(contS / tot * 100);
  const revProb = Math.round(exhS / tot * 100);
  const pullProb = 100 - contProb - revProb;
  const next = levels.next;
  const find = l => tgts.find(t => t.label === l) || {};
  const agg = find("aggressive"),
    ext = find("extreme");
  const zone = z => z ? `$${z[0]}–$${z[1]}` : "—";
  const w = (lo, hi) => `${lo || "?"}–${hi || "?"} days`;
  const md = vh.median_days || 6;
  const paths = up ? [{
    name: "Bullish continuation",
    prob: contProb,
    trigger: next ? `Holds support, breaks $${next.price}` : `Breaks aggressive $${agg.price || "—"}`,
    target: ext.price ? `$${ext.price}` : agg.price ? `$${agg.price}` : "—",
    days: w(vh.p25_days, vh.p75_days),
    inval: pullback ? `loses $${pullback.normal[1]}` : inval ? `loses $${inval}` : "—"
  }, {
    name: "Normal pullback",
    prob: pullProb,
    trigger: next ? `Stalls near $${next.price}` : `Fails near $${agg.price || "—"}`,
    target: pullback ? zone(pullback.normal) : "—",
    days: w(Math.max(1, Math.round(md / 3)), Math.max(2, Math.round(md / 1.5))),
    inval: inval ? `loses $${inval}` : "—"
  }, {
    name: "Bearish reversal",
    prob: revProb,
    trigger: inval ? `Closes below $${inval} on volume` : "Breaks the swing low on volume",
    target: pullback ? zone(pullback.deep) : "—",
    days: w(vh.median_days, vh.p75_days),
    inval: "reclaims the highs"
  }] : [{
    name: "Bearish continuation",
    prob: contProb,
    trigger: next ? `Stays weak, breaks $${next.price}` : `Breaks aggressive $${agg.price || "—"}`,
    target: ext.price ? `$${ext.price}` : agg.price ? `$${agg.price}` : "—",
    days: w(vh.p25_days, vh.p75_days),
    inval: pullback ? `reclaims $${pullback.normal[0]}` : inval ? `reclaims $${inval}` : "—"
  }, {
    name: "Normal bounce",
    prob: pullProb,
    trigger: next ? `Holds near $${next.price}` : `Stalls near $${agg.price || "—"}`,
    target: pullback ? zone(pullback.normal) : "—",
    days: w(Math.max(1, Math.round(md / 3)), Math.max(2, Math.round(md / 1.5))),
    inval: inval ? `reclaims $${inval}` : "—"
  }, {
    name: "Bullish reversal",
    prob: revProb,
    trigger: inval ? `Closes above $${inval} on volume` : "Breaks the swing high on volume",
    target: pullback ? zone(pullback.deep) : "—",
    days: w(vh.median_days, vh.p75_days),
    inval: "loses the lows"
  }];
  return {
    up,
    moveRead,
    tgts,
    pullback,
    keyLevels,
    scores,
    similar,
    decision,
    paths,
    sampleSize: completed.length,
    symbol: data.symbol
  };
}
const SWING_DECISION_TONE = {
  "Add on breakout": "go",
  "Add on pullback": "go",
  "Hold": "go",
  "Short trigger active": "short",
  "Short watch": "watch",
  "Reversal watch": "watch",
  "Take partial": "warn",
  "Trim": "warn",
  "Trail stop": "warn",
  "Cover partial": "warn",
  "Do not chase": "warn",
  "Wait": "muted",
  "No trade": "muted"
};
function SwingPrediction({
  data
}) {
  const p = computeSwingPrediction(data);
  if (!p) return null;
  const {
    up,
    moveRead: m,
    tgts,
    pullback,
    keyLevels,
    scores,
    similar,
    decision,
    paths
  } = p;
  const dirCls = up ? "up" : "down";
  const sgn = v => v == null ? "—" : `${v >= 0 ? "+" : ""}${v}%`;
  const matTone = {
    early: "up",
    developing: "up",
    mature: "",
    extended: "warn",
    exhausted: "down"
  }[m.maturity] || "";
  // Entry-timing read — the whole point: be at the START of the move, never chase.
  const early = ["early", "developing"].includes(m.maturity);
  const late = ["extended", "exhausted"].includes(m.maturity);
  const entryRead = early ? {
    cls: "up",
    txt: up ? "Early — good spot to be long; you're near the start" : "Early — good spot to be short; you're near the start"
  } : late ? {
    cls: "down",
    txt: up ? "Late — don't chase; wait for the pullback zone to go long" : "Late — don't chase; wait for the bounce zone to short"
  } : {
    cls: "",
    txt: "Mid-move — enter on a pullback, not here"
  };
  const sym = p.symbol || "this stock";
  return /*#__PURE__*/React.createElement("div", {
    className: "swing-pred"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-title"
  }, "Swing Prediction", /*#__PURE__*/React.createElement("span", {
    className: "swing-pred-sub"
  }, "based on this stock's ", p.sampleSize, " past ", up ? "up" : "down", "-swings — most likely path, not a guarantee")), /*#__PURE__*/React.createElement("div", {
    className: `swing-pred-decision tone-${SWING_DECISION_TONE[decision.action] || "muted"}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-decision-action"
  }, decision.action), decision.drivers.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-decision-why"
  }, decision.drivers.join(" · ")), /*#__PURE__*/React.createElement("div", {
    className: `swing-pred-timing ${entryRead.cls}`
  }, "Entry timing: ", entryRead.txt), decision.note && /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-decision-note"
  }, decision.note)), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-grid"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-box"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-h"
  }, "1 · Current move read"), /*#__PURE__*/React.createElement("ul", {
    className: "swing-pred-list"
  }, /*#__PURE__*/React.createElement("li", null, "Move: ", /*#__PURE__*/React.createElement("b", {
    className: dirCls
  }, m.dir, " from ", fmtSwingDate(m.fromDate), " ", m.fromLabel)), /*#__PURE__*/React.createElement("li", null, "So far: ", /*#__PURE__*/React.createElement("b", {
    className: dirCls
  }, sgn(m.pct)), " over ", /*#__PURE__*/React.createElement("b", null, m.days, "d"), " (", sgn(m.perDay), "/day)"), /*#__PURE__*/React.createElement("li", null, "Typical ", up ? "up" : "down", "-swing: ", /*#__PURE__*/React.createElement("b", null, up ? "+" : "−", m.typicalPct, "%"), " over ", /*#__PURE__*/React.createElement("b", null, m.typicalDays, "d")), m.pctOfMedian != null && /*#__PURE__*/React.createElement("li", null, "This move = ", /*#__PURE__*/React.createElement("b", null, m.pctOfMedian, "%"), " of the median"), /*#__PURE__*/React.createElement("li", null, "Status: ", /*#__PURE__*/React.createElement("b", {
    className: matTone
  }, m.maturity)))), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-box"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-h"
  }, "2 · Projected next targets"), /*#__PURE__*/React.createElement("table", {
    className: "swing-pred-tbl"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Target"), /*#__PURE__*/React.createElement("th", null, "Price"), /*#__PURE__*/React.createElement("th", null, "From here"), /*#__PURE__*/React.createElement("th", null, "By"), /*#__PURE__*/React.createElement("th", null, "Hit rate"), /*#__PURE__*/React.createElement("th", null, "Conf"))), /*#__PURE__*/React.createElement("tbody", null, tgts.map(t => /*#__PURE__*/React.createElement("tr", {
    key: t.label
  }, /*#__PURE__*/React.createElement("td", {
    className: "cap"
  }, t.label), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, fmtUsd(t.price, 2)), /*#__PURE__*/React.createElement("td", {
    className: `num ${t.reached ? "muted" : dirCls}`
  }, t.reached ? "reached" : sgn(t.fromPct)), /*#__PURE__*/React.createElement("td", {
    className: "num muted"
  }, t.reached ? "—" : fmtSwingDate(t.eta)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, t.prob, "%"), /*#__PURE__*/React.createElement("td", {
    className: "cap muted"
  }, t.conf)))))), pullback && /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-box"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-h"
  }, "3 · Expected ", up ? "pullback" : "bounce", " zone"), /*#__PURE__*/React.createElement("ul", {
    className: "swing-pred-list"
  }, /*#__PURE__*/React.createElement("li", null, "Shallow", up ? " (best re-entry)" : " (best re-short)", ": ", /*#__PURE__*/React.createElement("b", {
    className: dirCls
  }, "$", pullback.shallow[0], " – $", pullback.shallow[1])), /*#__PURE__*/React.createElement("li", null, "Normal: ", /*#__PURE__*/React.createElement("b", null, "$", pullback.normal[0], " – $", pullback.normal[1])), /*#__PURE__*/React.createElement("li", null, "Deep: ", /*#__PURE__*/React.createElement("b", null, "$", pullback.deep[0], " – $", pullback.deep[1])), pullback.invalidation != null && /*#__PURE__*/React.createElement("li", {
    className: "muted"
  }, "Invalidation: ", up ? "below" : "above", " ", /*#__PURE__*/React.createElement("b", {
    className: "down"
  }, "$", pullback.invalidation))), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-factors"
  }, pullback.basis === "history" ? `Tuned to ${sym}'s own history — it usually ${up ? "pulls back" : "bounces"} ~${pullback.medDepth}% (median of ${pullback.n} past ${up ? "pullbacks" : "bounces"}).` : "Few past pullbacks to learn from — using a tight retracement of the current move."), keyLevels.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-levels"
  }, /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, up ? "Support MMs defend" : "Resistance MMs defend", ":"), keyLevels.map((k, i) => /*#__PURE__*/React.createElement("span", {
    key: i,
    className: "swing-pred-lvl"
  }, "$", k.price, /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " ", k.pctAway > 0 ? "+" : "", k.pctAway, "%"))))), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-box"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-h"
  }, "4 · Continuation vs exhaustion"), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-score"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-score-row"
  }, /*#__PURE__*/React.createElement("span", null, "Continuation"), /*#__PURE__*/React.createElement("b", {
    className: "up"
  }, scores.continuation, "/100")), /*#__PURE__*/React.createElement("div", {
    className: "swing-bar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-bar-fill up",
    style: {
      width: `${Math.max(0, Math.min(100, scores.continuation || 0))}%`
    }
  })), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-factors"
  }, scores.contFactors.join(" · ") || "—")), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-score"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-score-row"
  }, /*#__PURE__*/React.createElement("span", null, "Exhaustion"), /*#__PURE__*/React.createElement("b", {
    className: "down"
  }, scores.exhaustion, "/100")), /*#__PURE__*/React.createElement("div", {
    className: "swing-bar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-bar-fill down",
    style: {
      width: `${Math.max(0, Math.min(100, scores.exhaustion || 0))}%`
    }
  })), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-factors"
  }, scores.exhFactors.join(" · ") || "—"))), similar.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-box"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-h"
  }, "5 · Most-similar past moves"), /*#__PURE__*/React.createElement("table", {
    className: "swing-pred-tbl"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Move"), /*#__PURE__*/React.createElement("th", null, "Size"), /*#__PURE__*/React.createElement("th", null, "Days"), /*#__PURE__*/React.createElement("th", null, "/day"), /*#__PURE__*/React.createElement("th", null, "What followed"))), /*#__PURE__*/React.createElement("tbody", null, similar.map((s, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", {
    className: "muted"
  }, fmtSwingDate(up ? s.lowDate : s.highDate)), /*#__PURE__*/React.createElement("td", {
    className: `num ${dirCls}`
  }, up ? "+" : "−", s.pct, "%"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, s.days), /*#__PURE__*/React.createElement("td", {
    className: "num muted"
  }, s.perDay, "%"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, s.outcome ? /*#__PURE__*/React.createElement("span", {
    className: s.outcome.kind === "fell" ? "down" : "up"
  }, s.outcome.kind, " ", s.outcome.pct, "% / ", s.outcome.days, "d") : /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "extended further"))))))), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-box swing-pred-wide"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-h"
  }, "6 · Three possible paths next"), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-paths"
  }, paths.map((pt, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: `swing-pred-path ${i === 0 ? up ? "up" : "down" : i === 2 ? up ? "down" : "up" : ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-path-head"
  }, /*#__PURE__*/React.createElement("span", null, pt.name), /*#__PURE__*/React.createElement("b", null, pt.prob, "%")), /*#__PURE__*/React.createElement("div", {
    className: "swing-bar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-bar-fill",
    style: {
      width: `${pt.prob}%`
    }
  })), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-path-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "Trigger"), " ", pt.trigger), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-path-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "Target"), " ", /*#__PURE__*/React.createElement("b", null, pt.target)), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-path-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "Time"), " ", pt.days), /*#__PURE__*/React.createElement("div", {
    className: "swing-pred-path-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "Invalid if"), " ", pt.inval)))))));
}
function SwingPatternCard({
  apiFetch,
  ticker
}) {
  const Term = window.Term || (({
    children
  }) => /*#__PURE__*/React.createElement("span", null, children));
  const cardRef = useRef(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [sens, setSens] = useState("0.12"); // zig-zag % threshold
  const [tab, setTab] = useState("up"); // history table: up | down
  const [fMove, setFMove] = useState("all"); // size filter
  const [fDur, setFDur] = useState("all"); // duration filter
  const [fVol, setFVol] = useState("all"); // volume filter
  const [fCat, setFCat] = useState("all"); // catalyst filter
  const [fStruct, setFStruct] = useState("all"); // structure filter
  const [openRow, setOpenRow] = useState(null); // expanded history row key
  const [focusKey, setFocusKey] = useState(null); // chart focus range {start,end}

  const load = async (sym, pct) => {
    if (!sym) return;
    setLoading(true);
    setErr(null);
    try {
      const r = await apiFetch(`/api/swings?symbol=${encodeURIComponent(sym)}&pct=${pct}`);
      const d = await r.json();
      if (d.error) setErr(d.error);else setData(d);
    } catch (e) {
      setErr(String(e));
    }
    setLoading(false);
  };
  // Clear the previous symbol's swings the instant the ticker changes so the
  // card shows its loading skeleton instead of stale data from another symbol
  // (which could be misread). Not cleared on a sensitivity tweak.
  useEffect(() => {
    setData(null);
    setErr(null);
  }, [ticker]);
  useEffect(() => {
    load(ticker, sens); /* eslint-disable-next-line */
  }, [ticker, sens]);

  // Resizable columns (desktop only). Adds a drag handle to each header cell
  // of the swing tables. Idempotent + re-runs when the tables change.
  useEffect(() => {
    if (typeof window === "undefined" || window.innerWidth <= 900) return;
    const root = cardRef.current;
    if (!root) return;
    const cleanups = [];
    root.querySelectorAll("table.swing-table thead").forEach(thead => {
      const ths = Array.from(thead.querySelectorAll("th"));
      ths.forEach((th, i) => {
        if (i === ths.length - 1 || th.querySelector(".col-resize-handle")) return;
        th.style.position = "relative";
        const h = document.createElement("span");
        h.className = "col-resize-handle";
        const onDown = e => {
          e.preventDefault();
          e.stopPropagation();
          const startX = e.clientX,
            startW = th.offsetWidth;
          document.body.style.userSelect = "none";
          const move = ev => {
            th.style.width = Math.max(44, startW + ev.clientX - startX) + "px";
          };
          const up = () => {
            window.removeEventListener("mousemove", move);
            window.removeEventListener("mouseup", up);
            document.body.style.userSelect = "";
          };
          window.addEventListener("mousemove", move);
          window.addEventListener("mouseup", up);
        };
        h.addEventListener("mousedown", onDown);
        th.appendChild(h);
        cleanups.push(() => {
          h.removeEventListener("mousedown", onDown);
          h.remove();
        });
      });
    });
    return () => cleanups.forEach(fn => fn());
  }, [data, tab, sens]);
  const fmtUsd2 = v => v == null ? "—" : "$" + Number(v).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
  const sgn = v => v == null ? "" : v >= 0 ? "+" : "";
  const a = data && data.analysis;
  const ind = data && data.indicators;
  const upRhythm = data && data.rhythm;
  const downRhythm = data && data.down_rhythm;
  const upSwings = data && data.swings || [];
  const downSwings = data && data.down_swings || [];
  const isUp = a && a.direction === "up";
  const dirTone = a ? isUp ? "up" : "down" : "";
  const matTone = m => ({
    early: "up",
    developing: "up",
    mature: "",
    extended: "warn",
    exhausted: "down"
  })[m] || "";
  const confTone = c => ({
    high: "up",
    medium: "",
    low: "warn"
  })[c] || "";
  const DECISION_TONE = {
    "Add on breakout": "go",
    "Add on pullback": "go",
    "Hold": "go",
    "Short trigger active": "short",
    "Short watch": "watch",
    "Take partial": "warn",
    "Cover partial": "warn",
    "Trail only": "warn",
    "Cover fully": "down",
    "No new trade": "muted"
  };
  // How each ladder target is derived (shown as a tooltip on the label).
  const TARGET_BASIS = {
    conservative: "25th percentile of this stock's past moves from a swing — most moves clear this.",
    median: "The typical (median) past move projected off the swing price.",
    aggressive: "75th percentile — only the stronger past moves reached this far.",
    extreme: "The single LARGEST prior move in the lookback, projected off the swing. An outlier ceiling — rarely repeated, hence the low confidence and 0 matches. Not a base case."
  };
  const ScoreBar = ({
    label,
    k,
    score,
    tone,
    factors
  }) => /*#__PURE__*/React.createElement("div", {
    className: "swing-score"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-score-row"
  }, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: k
  }, label)), /*#__PURE__*/React.createElement("b", {
    className: tone
  }, score == null ? "—" : Math.round(score), /*#__PURE__*/React.createElement("small", null, " / 100"))), /*#__PURE__*/React.createElement("div", {
    className: "swing-bar"
  }, /*#__PURE__*/React.createElement("div", {
    className: `swing-bar-fill ${tone}`,
    style: {
      width: `${Math.max(0, Math.min(100, score || 0))}%`
    }
  })), factors && factors.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "swing-factors"
  }, factors.slice(0, 3).join(" · ")));
  const histRhythm = tab === "up" ? upRhythm : downRhythm;
  const allHistSwings = tab === "up" ? upSwings : downSwings;
  const histSwings = useMemo(() => allHistSwings.filter(s => {
    const mag = Math.abs(s.pct_change || 0);
    if (fMove === "10" && mag < 10) return false;
    if (fMove === "20" && mag < 20) return false;
    const d = s.trading_days || 0;
    if (fDur === "short" && !(d >= 1 && d <= 3)) return false;
    if (fDur === "mid" && !(d >= 4 && d <= 8)) return false;
    if (fDur === "long" && d < 9) return false;
    if (fVol === "high" && !s.above_avg_vol) return false;
    if (fCat === "earnings" && !s.after_earnings) return false;
    if (fStruct === "broke" && !s.broke_resistance) return false;
    if (fStruct === "failed" && !s.failed_breakout) return false;
    return true;
  }), [allHistSwings, fMove, fDur, fVol, fCat, fStruct]);
  const filtersOn = fMove !== "all" || fDur !== "all" || fVol !== "all" || fCat !== "all" || fStruct !== "all";

  // Zoom the chart to a swing (earliest→latest date, padded a touch).
  const focusSwingOnChart = s => {
    const lo = s.low_date,
      hi = s.high_date;
    const start = lo < hi ? lo : hi,
      end = lo < hi ? hi : lo;
    setFocusKey({
      start,
      end,
      k: start + end + Date.now()
    });
  };
  // Chart click → find the swing whose span contains that date, open its row.
  const pickSwingByTime = t => {
    const inSpan = s => {
      const a0 = s.low_date < s.high_date ? s.low_date : s.high_date;
      const b0 = s.low_date < s.high_date ? s.high_date : s.low_date;
      return t >= a0 && t <= b0;
    };
    let hit = upSwings.findIndex(inSpan);
    if (hit >= 0) {
      setTab("up");
      setOpenRow(`up-${upSwings.length - 1 - hit}`);
      focusSwingOnChart(upSwings[hit]);
      return;
    }
    hit = downSwings.findIndex(inSpan);
    if (hit >= 0) {
      setTab("down");
      setOpenRow(`down-${downSwings.length - 1 - hit}`);
      focusSwingOnChart(downSwings[hit]);
    }
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "card ab-card",
    ref: cardRef
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Pattern recognition · ", ticker), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Swing decision — where am I in this move?")), /*#__PURE__*/React.createElement("div", {
    className: "ab-controls"
  }, /*#__PURE__*/React.createElement("select", {
    className: "sb-select ab-days",
    value: sens,
    onChange: e => setSens(e.target.value),
    title: "How big a reversal counts as a new swing"
  }, /*#__PURE__*/React.createElement("option", {
    value: "0.15"
  }, "Major swings"), /*#__PURE__*/React.createElement("option", {
    value: "0.12"
  }, "Standard"), /*#__PURE__*/React.createElement("option", {
    value: "0.08"
  }, "Sensitive")), /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: () => load(ticker, sens),
    disabled: loading
  }, loading ? "Loading…" : "Refresh"))), err && /*#__PURE__*/React.createElement("div", {
    className: "ab-status"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ab-err"
  }, err)), loading && !data && /*#__PURE__*/React.createElement("div", {
    className: "skel-block"
  }, /*#__PURE__*/React.createElement("div", {
    className: "skel skel-banner"
  }), /*#__PURE__*/React.createElement("div", {
    className: "skel-grid"
  }, [0, 1, 2, 3, 4, 5].map(i => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "skel skel-cell"
  }))), /*#__PURE__*/React.createElement("div", {
    className: "skel skel-bar"
  }), /*#__PURE__*/React.createElement("div", {
    className: "skel skel-bar"
  })), a && a.decision && /*#__PURE__*/React.createElement("div", {
    className: `swing-decision tone-${DECISION_TONE[a.decision.action] || "muted"}`
  }, /*#__PURE__*/React.createElement("span", {
    className: "swing-decision-action"
  }, a.decision.action), (a.decision.drivers || []).length > 0 && /*#__PURE__*/React.createElement("span", {
    className: "swing-decision-because"
  }, "because ", a.decision.drivers.join(" · "))), a && (a.status === "ok" || a.status === "no_rhythm") && /*#__PURE__*/React.createElement("div", {
    className: `swing-live swing-${dirTone}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-live-head"
  }, /*#__PURE__*/React.createElement("span", {
    className: `swing-badge ${dirTone}`
  }, isUp ? "LONG setup ▲" : "SHORT setup ▼"), a.trend_state && /*#__PURE__*/React.createElement("span", {
    className: "swing-state",
    title: "Plain-English read of the move"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "trend_state"
  }, a.trend_state)), a.maturity && /*#__PURE__*/React.createElement("span", {
    className: `swing-maturity ${matTone(a.maturity)}`,
    title: "Where this move sits in the stock's history"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "maturity"
  }, a.maturity)), a.status === "no_rhythm" && /*#__PURE__*/React.createElement("span", {
    className: "swing-maturity"
  }, "live move"), a.do_not_sell_yet && /*#__PURE__*/React.createElement("span", {
    className: "swing-flag up"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "do_not_sell_yet"
  }, "Don't sell yet")), a.cover_too_early_risk && /*#__PURE__*/React.createElement("span", {
    className: "swing-flag down"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "cover_too_early"
  }, "Don't cover yet"))), /*#__PURE__*/React.createElement("div", {
    className: "swing-live-grid"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: isUp ? "swing_low" : "swing_high"
  }, "From ", a.from_label)), /*#__PURE__*/React.createElement("b", null, fmtUsd2(a.from_price), " ", /*#__PURE__*/React.createElement("small", null, "· ", fmtSwingDate(a.from_date)))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Current price"), /*#__PURE__*/React.createElement("b", null, fmtUsd2(a.current_price))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "current_move"
  }, "Move so far")), /*#__PURE__*/React.createElement("b", {
    className: dirTone
  }, sgn(a.current_move_pct), a.current_move_pct, "% ", /*#__PURE__*/React.createElement("small", null, "· ", a.days_active, a.days_active === 1 ? "day" : "days"))), a.vs_history && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "vs typical move"), /*#__PURE__*/React.createElement("b", null, a.vs_history.pct_of_median_move, "% of median"), /*#__PURE__*/React.createElement("small", {
    className: "swing-sub"
  }, "med ", a.vs_history.median_pct, "% / ", a.vs_history.median_days, "d")), a.targets && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Median target"), /*#__PURE__*/React.createElement("b", {
    className: dirTone
  }, fmtUsd2(a.targets[1].price), " ", /*#__PURE__*/React.createElement("small", null, sgn(a.targets[1].from_here_pct), a.targets[1].from_here_pct, "% away"))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "RSI · rel-vol"), /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement(Term, {
    k: "rsi14"
  }, ind && ind.rsi14 != null ? ind.rsi14 : "—"), " · ", /*#__PURE__*/React.createElement(Term, {
    k: "rel_vol"
  }, ind && ind.rel_vol != null ? ind.rel_vol + "x" : "—"))), a.relative_strength && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "relative_strength"
  }, "vs market (SPY)")), /*#__PURE__*/React.createElement("b", {
    className: a.relative_strength.leading ? "up" : a.relative_strength.lagging ? "down" : ""
  }, sgn(a.relative_strength.vs_spy), a.relative_strength.vs_spy, "% ", /*#__PURE__*/React.createElement("small", null, a.relative_strength.leading ? "leading" : a.relative_strength.lagging ? "lagging" : "tracking"))), a.flow && a.flow.data_available && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "swing_flow"
  }, "Options flow")), /*#__PURE__*/React.createElement("b", {
    className: a.flow.label === "bullish" ? "up" : a.flow.label === "bearish" ? "down" : ""
  }, a.flow.label, " ", /*#__PURE__*/React.createElement("small", null, "quality ", a.flow.quality))), a.key_levels && a.key_levels.next && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "key_levels"
  }, "Next ", a.key_levels.next.kind)), /*#__PURE__*/React.createElement("b", {
    className: "warn"
  }, fmtUsd2(a.key_levels.next.price), " ", /*#__PURE__*/React.createElement("small", null, sgn(a.key_levels.next.pct_away), a.key_levels.next.pct_away, "% · ", fmtSwingDate(a.key_levels.next.date))))), a.key_levels && a.key_levels.note && /*#__PURE__*/React.createElement("div", {
    className: "swing-levelnote"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "key_levels"
  }, "⊟ Level read:"), " ", a.key_levels.note), (a.broke_resistance || a.after_earnings) && /*#__PURE__*/React.createElement("div", {
    className: "swing-tags"
  }, a.broke_resistance && /*#__PURE__*/React.createElement("span", {
    className: "swing-tag up"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "broke_resistance"
  }, "⤴ Broke ", isUp ? "resistance" : "support")), a.after_earnings && /*#__PURE__*/React.createElement("span", {
    className: "swing-tag"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "after_earnings"
  }, "⚡ Post-earnings move"))), a.flow && a.flow.data_available && /*#__PURE__*/React.createElement("div", {
    className: `swing-flowagree agree-${a.flow.agrees_with_price}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-flowagree-head"
  }, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "swing_flow"
  }, "Options flow agreement")), /*#__PURE__*/React.createElement("b", {
    className: a.flow.agrees_with_price === "agrees" ? isUp ? "up" : "down" : a.flow.agrees_with_price === "disagrees" ? "warn" : ""
  }, a.flow.label, " · flow ", a.flow.agrees_with_price === "agrees" ? "agrees with price" : a.flow.agrees_with_price === "disagrees" ? "disagrees with price" : "neutral vs price")), /*#__PURE__*/React.createElement("div", {
    className: "swing-flowagree-grid"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Bullish premium"), /*#__PURE__*/React.createElement("b", {
    className: "up"
  }, fmtUsd(a.flow.bull_premium, 1))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Bearish premium"), /*#__PURE__*/React.createElement("b", {
    className: "down"
  }, fmtUsd(a.flow.bear_premium, 1))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Call sweep pressure"), /*#__PURE__*/React.createElement("b", null, a.flow.call_sweep_pressure, " ", /*#__PURE__*/React.createElement("small", null, "(", a.flow.call_sweeps, ")"))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Put hedge pressure"), /*#__PURE__*/React.createElement("b", null, a.flow.put_hedge_pressure, " ", /*#__PURE__*/React.createElement("small", null, "(", a.flow.put_sweeps, ")"))))), a.signal_note && /*#__PURE__*/React.createElement("div", {
    className: "swing-signal"
  }, a.signal_note), a.status === "no_rhythm" && a.note && /*#__PURE__*/React.createElement("div", {
    className: "swing-signal"
  }, a.note), a.continuation_score != null && /*#__PURE__*/React.createElement("div", {
    className: "swing-scores"
  }, /*#__PURE__*/React.createElement(ScoreBar, {
    label: "Continuation",
    k: "continuation_score",
    score: a.continuation_score,
    tone: isUp ? "up" : "down",
    factors: a.continuation_factors
  }), /*#__PURE__*/React.createElement(ScoreBar, {
    label: "Exhaustion",
    k: "exhaustion_score",
    score: a.exhaustion_score,
    tone: "warn",
    factors: a.exhaustion_factors
  }))), a && a.status === "ok" && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 12
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-subtitle"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "target_ladder"
  }, "Projected target ladder"), " — from ", a.from_label, " ", fmtUsd2(a.from_price)), a.key_levels && ((a.key_levels.supports || []).length > 0 || (a.key_levels.resistances || []).length > 0) && /*#__PURE__*/React.createElement("div", {
    className: "swing-levels"
  }, /*#__PURE__*/React.createElement("span", {
    className: "swing-levels-lbl"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "key_levels"
  }, "Key levels")), (a.key_levels.resistances || []).slice().reverse().map((l, i) => /*#__PURE__*/React.createElement("span", {
    key: "r" + i,
    className: "swing-lvl res",
    title: `Resistance · prior swing high ${fmtSwingDate(l.date)}`
  }, fmtUsd2(l.price), " ", /*#__PURE__*/React.createElement("small", null, "+", l.pct_away, "%"))), /*#__PURE__*/React.createElement("span", {
    className: "swing-lvl now"
  }, fmtUsd2(a.current_price), " now"), (a.key_levels.supports || []).map((l, i) => /*#__PURE__*/React.createElement("span", {
    key: "s" + i,
    className: "swing-lvl sup",
    title: `Support · prior swing low ${fmtSwingDate(l.date)}`
  }, fmtUsd2(l.price), " ", /*#__PURE__*/React.createElement("small", null, l.pct_away, "%")))), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table swing-table mtable"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Target"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, isUp ? "Upside" : "Downside", " %"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "Price"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "From here"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "By (est.)"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "confidence_rating"
  }, "Confidence")))), /*#__PURE__*/React.createElement("tbody", null, a.targets.map((t, i) => /*#__PURE__*/React.createElement("tr", {
    key: i,
    className: "scan-row"
  }, /*#__PURE__*/React.createElement("td", {
    "data-label": "Target",
    style: {
      textTransform: "capitalize"
    },
    title: TARGET_BASIS[t.label] || ""
  }, t.label, t.reached ? " ✓" : ""), /*#__PURE__*/React.createElement("td", {
    "data-label": isUp ? "Upside %" : "Downside %",
    className: "scan-num"
  }, sgn(isUp ? t.pct_move : -t.pct_move), isUp ? t.pct_move : -t.pct_move, "%"), /*#__PURE__*/React.createElement("td", {
    "data-label": "Price",
    className: "scan-num"
  }, fmtUsd2(t.price)), /*#__PURE__*/React.createElement("td", {
    "data-label": "From here",
    className: `scan-num ${t.reached ? "muted" : dirTone}`
  }, t.reached ? "reached" : `${sgn(t.from_here_pct)}${t.from_here_pct}%`), /*#__PURE__*/React.createElement("td", {
    "data-label": "By (est.)",
    className: "scan-num"
  }, fmtSwingDate(t.eta_date)), /*#__PURE__*/React.createElement("td", {
    "data-label": "Confidence",
    className: `scan-num ${confTone(t.confidence)}`,
    title: `Matched ${t.matched} past move${t.matched === 1 ? "" : "s"} of this size or bigger`
  }, t.confidence)))))), a.confidence && /*#__PURE__*/React.createElement("div", {
    className: `swing-confwhy conf-${a.confidence.level}`
  }, /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement(Term, {
    k: "confidence_rating"
  }, "Confidence: ", a.confidence.level)), " ", /*#__PURE__*/React.createElement("span", null, "because ", (a.confidence.reasons || []).join(", "), "."))), a && a.status === "ok" && a.trade_plan && /*#__PURE__*/React.createElement("div", {
    className: "swing-plan"
  }, /*#__PURE__*/React.createElement("div", {
    className: "swing-subtitle"
  }, a.trade_plan.side === "long" ? "Long" : "Short", " trade plan"), /*#__PURE__*/React.createElement("div", {
    className: "swing-plan-grid"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "trade_entry_zone"
  }, "Entry zone")), /*#__PURE__*/React.createElement("b", null, fmtUsd2(a.trade_plan.entry_zone[0]), " – ", fmtUsd2(a.trade_plan.entry_zone[1]))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "trade_invalidation"
  }, "Invalidation")), /*#__PURE__*/React.createElement("b", {
    className: "down"
  }, fmtUsd2(a.trade_plan.invalidation))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "trade_t1"
  }, "Target 1 (median)")), /*#__PURE__*/React.createElement("b", {
    className: dirTone
  }, fmtUsd2(a.trade_plan.t1))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "trade_t2"
  }, "Target 2 (stretch)")), /*#__PURE__*/React.createElement("b", {
    className: dirTone
  }, fmtUsd2(a.trade_plan.t2))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "trade_extreme"
  }, "Extreme")), /*#__PURE__*/React.createElement("b", {
    className: dirTone
  }, fmtUsd2(a.trade_plan.stretch))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(Term, {
    k: "trade_holding"
  }, "Holding window")), (() => {
    const hw = a.trade_plan.holding_window || "";
    const m = /^(.*?)\s*\(through\s*(.+)\)\s*$/.exec(hw);
    return m ? /*#__PURE__*/React.createElement("b", null, m[1], /*#__PURE__*/React.createElement("small", {
      className: "swing-sub"
    }, "through ", m[2])) : /*#__PURE__*/React.createElement("b", null, hw);
  })())), /*#__PURE__*/React.createElement("div", {
    className: "swing-plan-note"
  }, a.trade_plan.entry_note), /*#__PURE__*/React.createElement("div", {
    className: "swing-plan-note muted"
  }, a.trade_plan.invalidation_note), /*#__PURE__*/React.createElement("div", {
    className: "swing-plan-cols"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "swing-plan-h up"
  }, "Reasons to stay"), /*#__PURE__*/React.createElement("ul", null, a.trade_plan.reason_to_stay.map((r, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, r)))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "swing-plan-h warn"
  }, "Exit warnings"), /*#__PURE__*/React.createElement("ul", null, a.trade_plan.exit_warnings.map((r, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, r))))), a.similar_move && /*#__PURE__*/React.createElement("div", {
    className: "swing-plan-note"
  }, /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement(Term, {
    k: "similar_move"
  }, "Similar past move:")), " ", a.similar_move.note)), /*#__PURE__*/React.createElement("div", {
    className: "swing-histnav",
    style: {
      marginTop: 14
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: tab === "up" ? "active" : "",
    onClick: () => setTab("up")
  }, "Up-swings (", upSwings.length, ")"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: tab === "down" ? "active" : "",
    onClick: () => setTab("down")
  }, "Down-swings (", downSwings.length, ")")), (upSwings.length > 0 || downSwings.length > 0) && /*#__PURE__*/React.createElement("div", {
    className: "swing-filters",
    title: "Narrow the history to setups like the one happening now"
  }, /*#__PURE__*/React.createElement("span", {
    className: "swing-filters-label"
  }, /*#__PURE__*/React.createElement(Term, {
    k: "swing_filters"
  }, "Filter")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fMove,
    onChange: e => setFMove(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any size"), /*#__PURE__*/React.createElement("option", {
    value: "10"
  }, "≥ 10%"), /*#__PURE__*/React.createElement("option", {
    value: "20"
  }, "≥ 20%")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fDur,
    onChange: e => setFDur(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any length"), /*#__PURE__*/React.createElement("option", {
    value: "short"
  }, "1–3 days"), /*#__PURE__*/React.createElement("option", {
    value: "mid"
  }, "4–8 days"), /*#__PURE__*/React.createElement("option", {
    value: "long"
  }, "9+ days")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fVol,
    onChange: e => setFVol(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any volume"), /*#__PURE__*/React.createElement("option", {
    value: "high"
  }, "Above-avg vol")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fCat,
    onChange: e => setFCat(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any catalyst"), /*#__PURE__*/React.createElement("option", {
    value: "earnings"
  }, "After earnings")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fStruct,
    onChange: e => setFStruct(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any structure"), /*#__PURE__*/React.createElement("option", {
    value: "broke"
  }, "Broke ", tab === "up" ? "resistance" : "support"), /*#__PURE__*/React.createElement("option", {
    value: "failed"
  }, "Failed breakout")), filtersOn && /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "swing-filters-clear",
    onClick: () => {
      setFMove("all");
      setFDur("all");
      setFVol("all");
      setFCat("all");
      setFStruct("all");
    }
  }, "Clear")), histRhythm && /*#__PURE__*/React.createElement("div", {
    className: "ab-status"
  }, /*#__PURE__*/React.createElement("b", null, histRhythm.count), " ", tab === "up" ? "up" : "down", "-swings · usually ", /*#__PURE__*/React.createElement("b", null, histRhythm.days_p25, "–", histRhythm.days_p75, " trading days"), " ", "· ", /*#__PURE__*/React.createElement("b", {
    className: tab === "up" ? "up" : "down"
  }, tab === "up" ? "+" : "−", histRhythm.pct_p25, "% to ", tab === "up" ? "+" : "−", histRhythm.pct_p75, "%"), " ", "(median ", /*#__PURE__*/React.createElement("b", null, tab === "up" ? "+" : "−", histRhythm.pct_median, "%"), ", ~", histRhythm.days_median, "d)", " ", "· full range ", histRhythm.days_min, "–", histRhythm.days_max, "d / ", histRhythm.pct_min, "–", histRhythm.pct_max, "%"), histSwings.length > 0 ? /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap",
    style: {
      marginTop: 8
    }
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table swing-table mtable mtable-hist"
  }, /*#__PURE__*/React.createElement("thead", null, tab === "up" ? /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, /*#__PURE__*/React.createElement(Term, {
    k: "swing_low"
  }, "Swing low")), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "Low $"), /*#__PURE__*/React.createElement("th", null, /*#__PURE__*/React.createElement(Term, {
    k: "swing_high"
  }, "Swing high")), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "High $"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "Days"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "$ chg"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "% chg"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "Avg/day"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "Rhythm"), /*#__PURE__*/React.createElement("th", null, "Flags")) : /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, /*#__PURE__*/React.createElement(Term, {
    k: "swing_high"
  }, "Swing high")), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "High $"), /*#__PURE__*/React.createElement("th", null, /*#__PURE__*/React.createElement(Term, {
    k: "swing_low"
  }, "Swing low")), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "Low $"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "Days"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "$ chg"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "% drop"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "Avg/day"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "Rhythm"), /*#__PURE__*/React.createElement("th", null, "Flags"))), /*#__PURE__*/React.createElement("tbody", null, histSwings.slice().reverse().map((s, i) => {
    const rk = `${tab}-${i}`;
    const open = openRow === rk;
    const det = s.detail || {};
    return /*#__PURE__*/React.createElement(React.Fragment, {
      key: rk
    }, /*#__PURE__*/React.createElement("tr", {
      className: `scan-row swing-exrow${open ? " open" : ""}`,
      onClick: () => {
        if (open) {
          setOpenRow(null);
          setFocusKey(null);
        } // click again = collapse + zoom back out
        else {
          setOpenRow(rk);
          focusSwingOnChart(s);
        }
      },
      title: "Click to expand & zoom to this move · click again to zoom back out"
    }, tab === "up" ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("td", {
      "data-label": "Swing low"
    }, /*#__PURE__*/React.createElement("span", {
      className: "swing-caret"
    }, open ? "▾" : "▸"), " ", fmtSwingDate(s.low_date)), /*#__PURE__*/React.createElement("td", {
      "data-label": "Low $",
      className: "scan-num"
    }, fmtUsd2(s.low_price)), /*#__PURE__*/React.createElement("td", {
      "data-label": "Swing high"
    }, fmtSwingDate(s.high_date)), /*#__PURE__*/React.createElement("td", {
      "data-label": "High $",
      className: "scan-num"
    }, fmtUsd2(s.high_price))) : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("td", {
      "data-label": "Swing high"
    }, /*#__PURE__*/React.createElement("span", {
      className: "swing-caret"
    }, open ? "▾" : "▸"), " ", fmtSwingDate(s.high_date)), /*#__PURE__*/React.createElement("td", {
      "data-label": "High $",
      className: "scan-num"
    }, fmtUsd2(s.high_price)), /*#__PURE__*/React.createElement("td", {
      "data-label": "Swing low"
    }, fmtSwingDate(s.low_date)), /*#__PURE__*/React.createElement("td", {
      "data-label": "Low $",
      className: "scan-num"
    }, fmtUsd2(s.low_price))), /*#__PURE__*/React.createElement("td", {
      "data-label": "Days",
      className: "scan-num"
    }, s.trading_days), /*#__PURE__*/React.createElement("td", {
      "data-label": "$ chg",
      className: `scan-num ${tab === "up" ? "" : "down"}`
    }, fmtUsd2(s.dollar_change)), /*#__PURE__*/React.createElement("td", {
      "data-label": tab === "up" ? "% chg" : "% drop",
      className: `scan-num ${tab === "up" ? "up" : "down"}`
    }, s.pct_change, "%"), /*#__PURE__*/React.createElement("td", {
      "data-label": "Avg/day",
      className: "scan-num"
    }, s.avg_daily_pct, "%"), /*#__PURE__*/React.createElement("td", {
      "data-label": "Rhythm",
      className: "scan-num"
    }, s.matches_rhythm ? "✓" : "·"), /*#__PURE__*/React.createElement("td", {
      "data-label": "Flags",
      className: "swing-flagcell"
    }, s.above_avg_vol && /*#__PURE__*/React.createElement("span", {
      title: `Above-average volume${s.vol_ratio ? ` (${s.vol_ratio}x)` : ""}`
    }, "🔥"), s.broke_resistance && /*#__PURE__*/React.createElement("span", {
      title: `Broke prior ${tab === "up" ? "resistance" : "support"}`
    }, "⤴"), s.failed_breakout && /*#__PURE__*/React.createElement("span", {
      title: "Failed breakout — level didn't hold"
    }, "⚠"), s.after_earnings && /*#__PURE__*/React.createElement("span", {
      title: "Launched after an earnings report"
    }, "⚡"))), open && /*#__PURE__*/React.createElement("tr", {
      className: "swing-detailrow"
    }, /*#__PURE__*/React.createElement("td", {
      colSpan: 10,
      className: "mtable-full"
    }, /*#__PURE__*/React.createElement("div", {
      className: "swing-detailgrid"
    }, det.before && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Before the move"), /*#__PURE__*/React.createElement("b", null, det.before)), det.beyond_median && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Past the median target"), /*#__PURE__*/React.createElement("b", null, det.beyond_median)), det.after && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "After the ", tab === "up" ? "high" : "low"), /*#__PURE__*/React.createElement("b", null, det.after)), det.hold_vs_target && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Sell at target vs hold"), /*#__PURE__*/React.createElement("b", null, det.hold_vs_target)), !det.before && !det.after && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Detail"), /*#__PURE__*/React.createElement("b", null, "Not enough surrounding history for this swing."))))));
  })))) : !err && !loading && /*#__PURE__*/React.createElement("div", {
    className: "ab-empty"
  }, filtersOn && allHistSwings.length > 0 ? `No ${tab === "up" ? "up" : "down"}-swings match these filters — adjust or clear them.` : `No major ${tab === "up" ? "up" : "down"}-swings found for ${ticker} in this window.`), data && (data.bars || []).length > 0 && /*#__PURE__*/React.createElement(TVAdvancedChart, {
    apiFetch: apiFetch,
    ticker: ticker,
    data: data,
    fallback: /*#__PURE__*/React.createElement(SwingChart, {
      data: data,
      focusKey: focusKey,
      onPickSwing: pickSwingByTime,
      onClearFocus: () => {
        setFocusKey(null);
        setOpenRow(null);
      }
    })
  }), data && data.analysis && data.analysis.status === "ok" && /*#__PURE__*/React.createElement(SwingPrediction, {
    data: data
  }));
}

// Trade ticket: turn a swing read into a sized, EV-ranked order. Stop = swing
// origin (where the thesis dies), target = origin + the stock's typical move,
// size = risk-budget / per-share risk. EV is the expected R-multiple. All from
// fields already on the row — no extra cost.
function computeTicket(r, acct, riskPct) {
  if (!r || !r.swing_dir || r.swing_from == null || r.swing_med_pct == null || r.last == null) return {};
  const r2 = x => Math.round(x * 100) / 100;
  const long = r.swing_dir === "long";
  const stop = r.swing_from;
  const target = long ? stop * (1 + r.swing_med_pct / 100) : stop * (1 - r.swing_med_pct / 100);
  const price = r.last;
  const risk = Math.abs(price - stop);
  const reward = long ? target - price : price - target;
  const base = {
    tk_target: r2(target),
    tk_stop: r2(stop)
  };
  if (!(risk > 0) || !(reward > 0)) return {
    ...base,
    tk_rr: null,
    tk_ev: null,
    tk_size: null
  }; // exhausted / no edge
  const rr = reward / risk;
  const wr = r.swing_winrate != null ? r.swing_winrate : 0.5;
  const ev = wr * rr - (1 - wr);
  const size = Math.floor((acct || 0) * (riskPct || 0) / 100 / risk);
  return {
    ...base,
    tk_rr: r2(rr),
    tk_ev: r2(ev),
    tk_size: size > 0 ? size : null,
    tk_riskUsd: size > 0 ? Math.round(size * risk) : null,
    tk_rewardUsd: size > 0 ? Math.round(size * reward) : null,
    tk_wr: Math.round(wr * 100)
  };
}

// Global cooldown so the watchlist board's auto-reconcile scan can't thrash
// across tab switches / remounts.
let _wlLastAutoScan = 0;
function WatchlistTableCard({
  apiFetch,
  onSwitchTicker,
  market,
  onRemoveSymbol,
  watchlistSymbols
}) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [sort, setSort] = useState({
    key: "edge",
    dir: "desc"
  });
  const [gsort, setGsort] = useState({
    key: "net",
    dir: "desc"
  });
  const [view, setView] = useState("stocks"); // stocks | sectors | industries
  const [fSector, setFSector] = useState("all");
  const [fIndustry, setFIndustry] = useState("all");
  const [fTag, setFTag] = useState("all");
  const [q, setQ] = useState("");
  const [fMcap, setFMcap] = useState("all");
  const [primeOnly, setPrimeOnly] = useState(false); // confluence shortlist
  const [acct, setAcct] = useState(() => {
    try {
      return Number(localStorage.getItem("jerry_acct")) || 100000;
    } catch {
      return 100000;
    }
  });
  const [riskPct, setRiskPct] = useState(() => {
    try {
      return Number(localStorage.getItem("jerry_riskpct")) || 1;
    } catch {
      return 1;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("jerry_acct", String(acct));
    } catch {}
  }, [acct]);
  useEffect(() => {
    try {
      localStorage.setItem("jerry_riskpct", String(riskPct));
    } catch {}
  }, [riskPct]);
  const [removed, setRemoved] = useState(() => new Set()); // optimistic hide after delete
  const [ctx, setCtx] = useState(null); // right-click menu: { x, y, symbol }
  const [analystBy, setAnalystBy] = useState({}); // symbol -> fresh-action summary (badge)
  const pollRef = useRef(null);

  // Fresh-analyst-action map for the in-table badge (cheap, server-cached).
  useEffect(() => {
    let stop = false;
    const grab = async () => {
      try {
        const r = await apiFetch("/api/watchlist_analyst");
        const d = await r.json();
        if (!stop) setAnalystBy(d && d.by_symbol || {});
      } catch (_) {/* badge is best-effort */}
    };
    grab();
    const t = setInterval(grab, 5 * 60 * 1000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);
  const load = async () => {
    try {
      const r = await apiFetch("/api/watchlist_table");
      const d = await r.json();
      setBoard(d);
      setErr(null);
      return d;
    } catch (e) {
      setErr(String(e));
      return null;
    }
  };
  useEffect(() => {
    load();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);
  const startScan = async () => {
    setErr(null);
    try {
      await apiFetch("/api/watchlist_table/scan?force=1");
    } catch (e) {
      setErr(String(e));
      return;
    }
    await load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 4000);
  };
  const status = board && board.status || {};
  const allRows = useMemo(() => computeWatchlistEdges(board && board.rows || []), [board]);
  // Live watchlist set: the scan cache can lag behind the current watchlist
  // (a deleted symbol stays in the cache until the next scan). Filtering to
  // the live watchlist makes the table reflect reality immediately and keeps
  // right-click deletes from "coming back" on refresh.
  const wlSet = useMemo(() => watchlistSymbols && watchlistSymbols.length ? new Set(watchlistSymbols.map(s => String(s).toUpperCase())) : null, [watchlistSymbols]);
  const rows = useMemo(() => allRows.filter(r => !removed.has(r.symbol) && (!wlSet || wlSet.has(String(r.symbol).toUpperCase()))).map(r => ({
    ...r,
    ...computeTicket(r, acct, riskPct)
  })), [allRows, removed, wlSet, acct, riskPct]);
  const mcapPass = mc => (MCAP_PRED[fMcap] || MCAP_PRED.all)(mc || 0);
  // Prime setup = the two independent lenses agree AND the move is early:
  // options-flow Edge direction == price-swing direction, swing just starting.
  // That's the highest-conviction "beginning of the move" trade.
  const isPrime = r => {
    if (r.swing_stage !== "early" || !r.swing_dir) return false;
    return r.swing_dir === "long" && r.edge_dir === "long" || r.swing_dir === "short" && r.edge_dir === "short";
  };
  const primeCount = useMemo(() => rows.filter(isPrime).length, [rows]);
  // Crowding check: if the Prime setups pile into one sector, that's really
  // one bet, not many — pros net correlated risk.
  const primeCrowd = useMemo(() => {
    const ps = rows.filter(isPrime);
    if (ps.length < 3) return null;
    const by = {};
    ps.forEach(r => {
      const s = r.sector || "—";
      by[s] = (by[s] || 0) + 1;
    });
    let top = null,
      n = 0;
    Object.entries(by).forEach(([s, c]) => {
      if (c > n) {
        n = c;
        top = s;
      }
    });
    return top && n >= 3 && n / ps.length >= 0.5 ? {
      sector: top,
      n,
      total: ps.length
    } : null;
  }, [rows]);
  const scanning = !!status.scanning;
  const sectors = board && board.sectors || [];
  const industries = board && board.industries || [];
  const tagOpts = board && board.tags || [];
  const COLS = [{
    k: "symbol",
    label: "Symbol"
  }, {
    k: "company",
    label: "Company"
  }, {
    k: "tag",
    label: "Tag"
  }, {
    k: "weekly",
    label: "Weekly"
  }, {
    k: "edge",
    label: "Edge",
    num: true
  }, {
    k: "setup",
    label: "Setup"
  }, {
    k: "prem_sell",
    label: "Premium"
  }, {
    k: "swing_dir",
    label: "Swing"
  }, {
    k: "swing_stage",
    label: "Timing"
  }, {
    k: "tk_ev",
    label: "EV",
    num: true
  }, {
    k: "tk_size",
    label: "Size",
    num: true
  }, {
    k: "rvol_rank",
    label: "Vol",
    num: true
  }, {
    k: "last",
    label: "Price",
    num: true
  }, {
    k: "market_cap",
    label: "Mkt Cap",
    num: true
  }, {
    k: "pe",
    label: "P/E",
    num: true
  }, {
    k: "forward_pe",
    label: "Fwd P/E",
    num: true
  }, {
    k: "industry",
    label: "Industry"
  }, {
    k: "sector",
    label: "Sector"
  }, {
    k: "rsi",
    label: "RSI",
    num: true
  }, {
    k: "rel_vol",
    label: "Rel Vol",
    num: true
  }, {
    k: "flow_net",
    label: "Flow",
    num: true
  }, {
    k: "flow_agree",
    label: "Agree"
  }, {
    k: "flow_bull",
    label: "Bull",
    num: true
  }, {
    k: "flow_bear",
    label: "Bear",
    num: true
  }, {
    k: "call_prem",
    label: "Bull $",
    num: true
  }, {
    k: "put_prem",
    label: "Bear $",
    num: true
  }, {
    k: "net_prem",
    label: "Net $",
    num: true
  }, {
    k: "pc_ratio",
    label: "P/C",
    num: true
  }, {
    k: "ask_call_prem",
    label: "Ask C$",
    num: true
  }, {
    k: "ask_put_prem",
    label: "Ask P$",
    num: true
  }, {
    k: "call_sweeps",
    label: "C Swp",
    num: true
  }, {
    k: "put_sweeps",
    label: "P Swp",
    num: true
  }, {
    k: "flow_alerts",
    label: "Alerts",
    num: true
  }, {
    k: "flow_quality",
    label: "Conv",
    num: true
  }, {
    k: "flow_cc_risk",
    label: "CC Risk",
    num: true
  }, {
    k: "flow_verdict",
    label: "Verdict"
  }, {
    k: "next_earnings",
    label: "Earnings",
    num: true
  }, {
    k: "change",
    label: "Chg%",
    num: true
  }, {
    k: "from_open",
    label: "% From Open",
    num: true
  }, {
    k: "wtd",
    label: "WTD%",
    num: true
  }, {
    k: "mtd",
    label: "MTD%",
    num: true
  }, {
    k: "qtd",
    label: "QTD%",
    num: true
  }, {
    k: "ytd",
    label: "YTD%",
    num: true
  }, {
    k: "from_ma20",
    label: "%20DMA",
    num: true
  }, {
    k: "from_ma50",
    label: "%50DMA",
    num: true
  }, {
    k: "from_ma200",
    label: "%200DMA",
    num: true
  }];
  const STR = new Set(["symbol", "company", "tag", "weekly", "industry", "sector", "flow_agree", "flow_verdict", "setup", "prem_sell", "swing_dir", "swing_stage"]);
  const setSortKey = k => setSort(s => s.key === k ? {
    key: k,
    dir: s.dir === "asc" ? "desc" : "asc"
  } : {
    key: k,
    dir: STR.has(k) ? "asc" : "desc"
  });

  // Per-column header tooltips.
  const COL_TIPS = {
    symbol: "Ticker symbol (★ = Prime setup). Click a row to open it.",
    company: "Company name",
    tag: "Your category from the imported CSV — use the Tag filter to group your list.",
    weekly: "Whether the stock has weekly options (from your CSV). Yes / No / blank.",
    edge: "Edge — signed options-flow conviction (+long / −short), size-normalized. Sort to rank morning buys vs sells.",
    setup: "Plain-English read of the edge drivers",
    prem_sell: "Suggested premium-selling side",
    swing_dir: "Active price-swing direction (long/short)",
    swing_stage: "Where in the swing the move is (early/mid/late)",
    tk_ev: "Expected value per trade in R (win-rate × reward:risk − loss odds)",
    tk_size: "Risk-based position size (account × risk% ÷ stop distance)",
    rvol_rank: "Realized-vol rank 0-100 vs the stock's own year (↑ rich → sell premium, ↓ cheap → buy)",
    last: "Last price (live during market hours)",
    market_cap: "Market capitalization",
    pe: "Trailing P/E",
    forward_pe: "Forward P/E",
    industry: "Industry",
    sector: "Sector",
    rsi: "RSI(14)",
    rel_vol: "Relative volume vs 20-day average",
    flow_net: "Net options-flow direction/score",
    flow_agree: "Does flow agree with the price move?",
    flow_bull: "Bullish flow sub-score",
    flow_bear: "Bearish flow sub-score",
    call_prem: "Total call premium today",
    put_prem: "Total put premium today",
    net_prem: "Net (call − put) premium",
    pc_ratio: "Put/Call premium ratio",
    ask_call_prem: "Ask-side (aggressive) call premium",
    ask_put_prem: "Ask-side (aggressive) put premium",
    call_sweeps: "Call sweep count",
    put_sweeps: "Put sweep count",
    flow_alerts: "Unusual-flow alert count",
    flow_quality: "Flow conviction 0-100 (0 noise, 100 high-conviction)",
    flow_cc_risk: "Covered-call risk 0-100 (high = avoid selling calls)",
    flow_verdict: "Decision-engine verdict",
    next_earnings: "Next earnings date (days away)",
    change: "Change % today (live)",
    from_open: "% from today's open — (live price − open) / open. Sort to rank intraday gainers from the open (live).",
    wtd: "Week-to-date % (live)",
    mtd: "Month-to-date % (live)",
    qtd: "Quarter-to-date % (live)",
    ytd: "Year-to-date % (live)",
    from_ma20: "% from the 20-day moving average (live)",
    from_ma50: "% from the 50-day MA (live)",
    from_ma200: "% from the 200-day MA (live)"
  };
  // Movable columns — drag a header to reorder; order persists per device.
  const COL_ORDER_KEY = "jerry_wl_colorder_v1";
  const _defaultOrder = COLS.map(c => c.k);
  const [colOrder, setColOrder] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(COL_ORDER_KEY) || "null");
      if (Array.isArray(saved)) {
        const known = new Set(_defaultOrder);
        const kept = saved.filter(k => known.has(k));
        const added = _defaultOrder.filter(k => !kept.includes(k)); // surface new columns
        return [...kept, ...added];
      }
    } catch (_) {}
    return _defaultOrder;
  });
  useEffect(() => {
    try {
      localStorage.setItem(COL_ORDER_KEY, JSON.stringify(colOrder));
    } catch (_) {}
  }, [colOrder]);
  const _colByKey = {};
  COLS.forEach(c => {
    _colByKey[c.k] = c;
  });
  const orderedCols = colOrder.map(k => _colByKey[k]).filter(Boolean);
  const dragColKey = useRef(null);
  const onColDrop = targetK => {
    const from = dragColKey.current;
    dragColKey.current = null;
    if (!from || from === targetK) return;
    setColOrder(prev => {
      const arr = prev.filter(k => k !== from);
      const idx = arr.indexOf(targetK);
      arr.splice(idx < 0 ? arr.length : idx, 0, from);
      return arr;
    });
  };

  // Industry dropdown is scoped to the selected sector so it only lists
  // industries that actually live in that sector.
  const industryOpts = useMemo(() => {
    if (fSector === "all") return industries;
    const set = new Set();
    rows.forEach(r => {
      if (r.sector === fSector && r.industry) set.add(r.industry);
    });
    return Array.from(set).sort();
  }, [rows, industries, fSector]);
  // If the chosen industry isn't in the (newly) selected sector, reset it.
  useEffect(() => {
    if (fIndustry !== "all" && !industryOpts.includes(fIndustry)) setFIndustry("all");
  }, [industryOpts, fIndustry]);

  // Live-price overlay state + helpers. The batch poll that fills liveQ is
  // further down (it needs `shown`); the state lives here so `filtered` can
  // sort by the live "% From Open".
  const [liveQ, setLiveQ] = useState({}); // symbol -> live last price
  const liveLast = r => {
    const q = liveQ[r.symbol];
    return q && q.last != null ? q.last : r.last;
  };
  const reb = (r, oldPct) => {
    const q = liveQ[r.symbol];
    const live = q && q.last != null ? q.last : null;
    if (live == null || !r.last || oldPct == null) return oldPct;
    return (live / r.last * (1 + oldPct / 100) - 1) * 100;
  };
  // % from today's open. Prefer the live quote's open (always today's), fall
  // back to the scan's open; rebase against the live price (open is fixed
  // intraday). Works as soon as quotes arrive — no re-scan needed.
  const foVal = r => {
    const q = liveQ[r.symbol];
    const open = q && q.open != null ? q.open : r.open;
    const last = liveLast(r);
    return open && last != null ? (last - open) / open * 100 : null;
  };
  // Daily change %. Use the live quote's own change (always measured vs the
  // PRIOR SESSION's close — Friday on a Monday) rather than rebasing the scan's
  // `change`, whose base is the scan's previous daily bar — which is Thursday
  // when the scan ran pre-open Monday, making CHG% wrongly include Friday.
  const chgVal = r => {
    const q = liveQ[r.symbol];
    return q && q.chg != null ? q.chg : reb(r, r.change);
  };
  // Columns whose displayed value is the LIVE-rebased % (not the raw scan
  // field). Sorting must use the same live value or the order won't match what
  // Every column that DISPLAYS a live value must SORT on that same live value,
  // or the order won't match what's on screen:
  //   last       -> liveLast (live price)
  //   change     -> chgVal   (live quote's daily change)
  //   from_open  -> foVal    (live, vs today's open)
  //   wtd..%DMAs -> reb      (live, rebased to the live price)
  // Everything else sorts on the scanned field.
  const REB_KEYS = new Set(["wtd", "mtd", "qtd", "ytd", "from_ma20", "from_ma50", "from_ma200"]);
  const sortValOf = (r, key) => key === "from_open" ? foVal(r) : key === "change" ? chgVal(r) : key === "last" ? liveLast(r) : REB_KEYS.has(key) ? reb(r, r[key]) : r[key];
  const filtered = useMemo(() => {
    let out = rows.filter(r => {
      if (primeOnly && !isPrime(r)) return false;
      if (fSector !== "all" && r.sector !== fSector) return false;
      if (fIndustry !== "all" && r.industry !== fIndustry) return false;
      if (fTag !== "all" && (r.tag || "") !== fTag) return false;
      if (fMcap !== "all" && !mcapPass(r.market_cap)) return false;
      if (q && !`${r.symbol} ${r.company || ""}`.toLowerCase().includes(q.toLowerCase())) return false;
      return true;
    });
    const {
        key,
        dir
      } = sort,
      mul = dir === "asc" ? 1 : -1;
    out = out.slice().sort((a, b) => {
      // Live %-columns (Chg/WTD/MTD/QTD/YTD/%DMAs/% From Open) sort on the LIVE
      // value so the order matches the live numbers shown; others sort the field.
      let av = sortValOf(a, key);
      let bv = sortValOf(b, key);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * mul;
      return (av - bv) * mul;
    });
    return out;
  }, [rows, fSector, fIndustry, fTag, fMcap, q, sort, primeOnly, liveQ]);

  // Progressive rendering: the stocks table can hold ~550 rows × 40+ columns.
  // Painting them all (and re-painting on every sort/filter) is the table's
  // biggest cost. Render a chunk and append more as you scroll — after a sort
  // you look at the top anyway, so this caps the expensive re-render at one
  // chunk while keeping auto column widths stable (the set only grows). True
  // row-windowing was avoided on purpose: this table is auto-layout with a
  // sticky first column, so windowing would make columns jitter horizontally.
  const WL_CHUNK = 120;
  const [visN, setVisN] = useState(WL_CHUNK);
  // Reset to the top when the result set genuinely changes (sort / filter /
  // search / new scan) — NOT on live-price re-sorts, which would otherwise
  // yank the scroll back to the top every poll.
  useEffect(() => {
    setVisN(WL_CHUNK);
  }, [sort, q, fSector, fIndustry, fTag, fMcap, primeOnly, rows]);
  const wlScrollRef = useRef(null);
  const onWlScroll = e => {
    if (visN >= filtered.length) return;
    const el = e.currentTarget;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 600) {
      setVisN(n => Math.min(n + WL_CHUNK, filtered.length));
    }
  };
  const shown = view === "stocks" ? filtered.slice(0, visN) : filtered;

  // ── Live price overlay: batch-poll /api/quote for the on-screen rows ────
  // CHG%/WTD/MTD/QTD/YTD, the %DMAs and % From Open are all (price − base)/base
  // with a base that's fixed intraday, so we rebase against the live price —
  // no re-scan. 25 symbols/call, Schwab-cached; pauses when hidden.
  const shownSymsKey = view === "stocks" ? shown.map(r => r.symbol).join(",") : "";
  useEffect(() => {
    if (view !== "stocks" || !shownSymsKey) return;
    const syms = shownSymsKey.split(",");
    let stop = false;
    const poll = async () => {
      if (document.hidden) return;
      for (let i = 0; i < syms.length && !stop; i += 25) {
        const batch = syms.slice(i, i + 25);
        try {
          const r = await apiFetch(`/api/quote?tickers=${encodeURIComponent(batch.join(","))}`);
          if (!r.ok) continue;
          const j = await r.json();
          if (stop) return;
          const res = j.results || {};
          setLiveQ(prev => {
            const next = {
              ...prev
            };
            for (const s of batch) {
              const q = res[s];
              if (q && q.last) next[s] = {
                last: q.last,
                open: q.open != null ? q.open : null,
                chg: q.change_pct != null ? q.change_pct : null
              };
            }
            return next;
          });
        } catch (_) {}
      }
    };
    poll();
    const id = setInterval(poll, 20000); // 20s; quote cache is 25s
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [shownSymsKey, view]);

  // How many of the user's tracked symbols aren't in the last scan's board —
  // either added since the scan, or skipped because the data source returned
  // no price for them. Surfaced so the count gap never looks like missing data.
  const notScanned = useMemo(() => {
    if (!watchlistSymbols || !watchlistSymbols.length) return 0;
    const boardSet = new Set(allRows.map(r => String(r.symbol).toUpperCase()));
    return watchlistSymbols.filter(s => !boardSet.has(String(s).toUpperCase())).length;
  }, [allRows, watchlistSymbols]);

  // Auto-reconcile: if the board is badly out of sync with the watchlist (lots
  // of tracked symbols missing — e.g. after a restore or bulk add), kick a
  // scan automatically so the table fills itself in, instead of making the
  // user find "Scan now". Bounded by a 10-minute global cooldown so it can't
  // thrash, and only fires once the board has actually loaded.
  useEffect(() => {
    if (scanning || !board) return;
    if (notScanned < 25) return;
    const now = Date.now();
    if (now - _wlLastAutoScan < 10 * 60 * 1000) return;
    _wlLastAutoScan = now;
    startScan();
  }, [notScanned, scanning, board]);

  // Sector / industry rollup. Sums the per-stock premium fields (all from
  // the flow_alerts call already made — no extra UW cost) so you can see
  // where money is flowing in and out at the group level. Respects the
  // sector/industry dropdowns so you can drill the industry rollup into a
  // single sector. Stocks with no flow data are counted but contribute $0.
  const groupKey = view === "sectors" ? "sector" : "industry";
  const groups = useMemo(() => {
    if (view === "stocks") return [];
    const base = rows.filter(r => {
      if (fSector !== "all" && r.sector !== fSector) return false;
      if (fIndustry !== "all" && r.industry !== fIndustry) return false;
      if (fTag !== "all" && (r.tag || "") !== fTag) return false;
      if (fMcap !== "all" && !mcapPass(r.market_cap)) return false;
      return true;
    });
    const map = new Map();
    base.forEach(r => {
      const name = r[groupKey] || "—";
      let g = map.get(name);
      if (!g) {
        g = {
          name,
          stocks: 0,
          withFlow: 0,
          mcap: 0,
          nBull: 0,
          nBear: 0,
          bull: 0,
          bear: 0,
          net: 0,
          askC: 0,
          askP: 0,
          cSwp: 0,
          pSwp: 0,
          alerts: 0
        };
        map.set(name, g);
      }
      g.stocks++;
      g.mcap += r.market_cap || 0;
      if (r.flow_available) {
        g.withFlow++;
        g.bull += r.call_prem || 0;
        g.bear += r.put_prem || 0;
        g.net += r.net_prem || 0;
        g.askC += r.ask_call_prem || 0;
        g.askP += r.ask_put_prem || 0;
        g.cSwp += r.call_sweeps || 0;
        g.pSwp += r.put_sweeps || 0;
        g.alerts += r.flow_alerts || 0;
        if (r.net_prem > 0) g.nBull++;else if (r.net_prem < 0) g.nBear++;
      }
    });
    let arr = Array.from(map.values());
    arr.forEach(g => {
      g.pc = g.bull > 0 ? Math.round(g.bear / g.bull * 100) / 100 : null;
    });
    const {
        key,
        dir
      } = gsort,
      mul = dir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      let av = a[key],
        bv = b[key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * mul;
      return (av - bv) * mul;
    });
    return arr;
  }, [rows, view, groupKey, fSector, fIndustry, fTag, fMcap, gsort]);
  const GCOLS = [{
    k: "name",
    label: view === "sectors" ? "Sector" : "Industry"
  }, {
    k: "stocks",
    label: "Stocks",
    num: true
  }, {
    k: "mcap",
    label: "Mkt Cap",
    num: true
  }, {
    k: "nBull",
    label: "Bull #",
    num: true
  }, {
    k: "nBear",
    label: "Bear #",
    num: true
  }, {
    k: "bull",
    label: "Bull $",
    num: true
  }, {
    k: "bear",
    label: "Bear $",
    num: true
  }, {
    k: "net",
    label: "Net $",
    num: true
  }, {
    k: "pc",
    label: "P/C",
    num: true
  }, {
    k: "askC",
    label: "Ask C$",
    num: true
  }, {
    k: "askP",
    label: "Ask P$",
    num: true
  }, {
    k: "cSwp",
    label: "C Swp",
    num: true
  }, {
    k: "pSwp",
    label: "P Swp",
    num: true
  }, {
    k: "alerts",
    label: "Alerts",
    num: true
  }];
  const setGsortKey = k => setGsort(s => s.key === k ? {
    key: k,
    dir: s.dir === "asc" ? "desc" : "asc"
  } : {
    key: k,
    dir: k === "name" ? "asc" : "desc"
  });
  // Drill a group row down into the per-stock view, pre-filtered.
  const drillGroup = name => {
    if (view === "sectors") setFSector(name === "—" ? "all" : name);else setFIndustry(name === "—" ? "all" : name);
    setView("stocks");
  };
  // Right-click delete: hide the row immediately and remove it from the
  // (server-synced) watchlist via the parent handler.
  const doRemove = sym => {
    setRemoved(prev => {
      const n = new Set(prev);
      n.add(sym);
      return n;
    });
    if (onRemoveSymbol) onRemoveSymbol(sym);
    setCtx(null);
  };
  useEffect(() => {
    if (!ctx) return undefined;
    const close = () => setCtx(null);
    const onKey = e => {
      if (e.key === "Escape") setCtx(null);
    };
    document.addEventListener("click", close);
    document.addEventListener("scroll", close, true);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("scroll", close, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [ctx]);
  const pctCls = v => v == null ? "" : v >= 0 ? "up" : "down";
  const pct = v => v == null ? "—" : `${v >= 0 ? "+" : ""}${Math.round(v * 100) / 100}%`;
  const flowCell = r => {
    if (!r.flow_available || r.flow_net == null) return /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "—");
    const d = r.flow_dir;
    const cls = d === "bull" ? "up" : d === "bear" ? "down" : "muted";
    const lbl = d === "bull" ? "Bull" : d === "bear" ? "Bear" : "Mixed";
    return /*#__PURE__*/React.createElement("span", {
      className: cls,
      title: "Net options-flow direction (bullish − bearish premium share)"
    }, lbl, " ", r.flow_net >= 0 ? "+" : "", r.flow_net);
  };
  const agreeCell = r => {
    if (!r.flow_available || !r.flow_agree) return /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "—");
    if (r.flow_agree === "agrees") return /*#__PURE__*/React.createElement("span", {
      className: "up",
      title: "Options flow agrees with the recent price trend"
    }, "✓ agrees");
    if (r.flow_agree === "disagrees") return /*#__PURE__*/React.createElement("span", {
      className: "down",
      title: "Options flow disagrees with the recent price trend"
    }, "✗ against");
    return /*#__PURE__*/React.createElement("span", {
      className: "muted",
      title: "Mixed / neutral flow"
    }, "~ neutral");
  };
  // Compact signed $ for premium columns (e.g. $1.2M, -$540K). Blank/0 → —
  const prem$ = v => v == null ? "—" : v === 0 ? "—" : window.fmt$M(v);
  const numOr = v => v == null ? "—" : v;
  const edgeCell = r => {
    if (r.edge == null) return /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "—");
    const cls = r.edge >= 15 ? "up" : r.edge <= -15 ? "down" : "muted";
    return /*#__PURE__*/React.createElement("b", {
      className: cls
    }, r.edge > 0 ? "+" : "", r.edge);
  };
  const setupCell = r => {
    if (!r.setup) return /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "—");
    const cls = r.edge_dir === "long" ? "up" : r.edge_dir === "short" ? "down" : "muted";
    return /*#__PURE__*/React.createElement("span", {
      className: cls
    }, r.edge_er ? "⚠ " : "", r.setup);
  };
  // Price-swing direction (long/short bias) + how far along the move is.
  const swingCell = r => {
    if (!r.swing_dir) return /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "—");
    const long = r.swing_dir === "long";
    const tip = r.swing_pct != null ? `${long ? "Up" : "Down"} move ${r.swing_pct}% over ${r.swing_days}d` : "";
    return /*#__PURE__*/React.createElement("span", {
      className: long ? "up" : "down",
      title: tip
    }, long ? "Long" : "Short");
  };
  const timingCell = r => {
    if (!r.swing_stage) return /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "—");
    const cls = r.swing_stage === "early" ? "up" : r.swing_stage === "late" ? "down" : "muted";
    const tip = r.swing_stage === "early" ? "Near the start of the move — best entry" : r.swing_stage === "late" ? "Extended — don't chase; wait for a pullback" : "Mid-move — enter on a pullback";
    return /*#__PURE__*/React.createElement("span", {
      className: cls,
      title: tip
    }, r.swing_stage);
  };
  // Expected R-multiple — the desk-style ranker. >0 = positive expectancy.
  const evCell = r => {
    if (r.tk_ev == null) return /*#__PURE__*/React.createElement("span", {
      className: "muted",
      title: r.tk_target ? "Target already reached — no edge left here" : "—"
    }, "—");
    const cls = r.tk_ev >= 0.2 ? "up" : r.tk_ev < 0 ? "down" : "muted";
    return /*#__PURE__*/React.createElement("b", {
      className: cls,
      title: `Expected value per trade. R:R ${r.tk_rr}, win-rate ${r.tk_wr}% → ${r.tk_ev >= 0 ? "+" : ""}${r.tk_ev}R expected`
    }, r.tk_ev >= 0 ? "+" : "", r.tk_ev, "R");
  };
  // Realized-volatility regime → buy-vs-sell-premium read.
  const volCell = r => {
    if (r.rvol_rank == null) return /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "—");
    const hot = r.rvol_rank >= 70,
      cold = r.rvol_rank <= 30;
    const cls = hot ? "down" : cold ? "up" : "muted";
    const tip = `Realized-vol rank ${r.rvol_rank} (20d vol ${r.rvol}% vs its own year). ` + (hot ? "Elevated — premium likely rich → favor SELLING premium (credit spreads / CSPs)." : cold ? "Calm/cheap — favor BUYING premium (long calls/puts) or directional shares." : "Mid — no strong premium edge either way.");
    return /*#__PURE__*/React.createElement("span", {
      className: cls,
      title: tip
    }, r.rvol_rank, hot ? " ↑" : cold ? " ↓" : "");
  };
  // Risk-based position size for the current account / risk-per-trade.
  const sizeCell = r => {
    if (r.tk_size == null) return /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "—");
    const dir = r.swing_dir === "long" ? "Buy" : "Short";
    const tip = `${dir} ${r.tk_size} sh · risk $${r.tk_riskUsd?.toLocaleString()} → reward $${r.tk_rewardUsd?.toLocaleString()} · R:R ${r.tk_rr} · target $${r.tk_target} / stop $${r.tk_stop}`;
    return /*#__PURE__*/React.createElement("span", {
      title: tip
    }, r.tk_size.toLocaleString(), /*#__PURE__*/React.createElement("small", {
      className: "muted"
    }, " sh"));
  };

  // One <td> for a (column, row) pair — data-driven so columns can be reordered.
  const renderCell = (c, r) => {
    const k = c.k;
    switch (k) {
      case "symbol":
        {
          const an = analystBy[r.symbol];
          const fresh = an && an.fresh_today;
          // Streak badge when the current run is near the stock's own record.
          const sdir = r.streak_dir,
            scount = r.streak_count || 0;
          const srec = sdir === "up" ? r.longest_up || 0 : sdir === "down" ? r.longest_down || 0 : 0;
          const sNear = (sdir === "up" || sdir === "down") && srec >= 4 && scount >= srec - 1;
          const sBadge = sNear ? /*#__PURE__*/React.createElement("span", {
            className: `wl-streak-badge ${sdir === "up" ? "up" : "down"}`,
            title: `${scount}-day ${sdir} streak — near its 2y record of ${srec}${sdir === "down" ? " (possible exhaustion / mean-reversion watch)" : ""}`
          }, sdir === "up" ? "▲" : "▼", scount) : null;
          return /*#__PURE__*/React.createElement("td", {
            key: k,
            className: "wl-sym"
          }, isPrime(r) && /*#__PURE__*/React.createElement("span", {
            className: "wl-prime-star",
            title: "Prime setup — flow + swing agree, move is early"
          }, "★ "), r.symbol, fresh && /*#__PURE__*/React.createElement("span", {
            className: `wl-analyst-badge wl-an-${an.direction || "neutral"}`,
            title: `Fresh analyst action today: ${an.action_type || "action"}${an.count > 1 ? ` (${an.count} firms)` : ""} · impact ${Math.round(an.score || 0)}`
          }, "⚡"), sBadge);
        }
      case "company":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "wl-co"
        }, r.company || "—");
      case "tag":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "wl-txt"
        }, r.tag ? /*#__PURE__*/React.createElement("span", {
          className: "wl-tag-chip",
          title: "Your category: " + r.tag
        }, r.tag) : "—");
      case "weekly":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "wl-txt",
          title: r.weekly === true ? "Has weekly options" : r.weekly === false ? "No weekly options" : "Unknown"
        }, r.weekly === true ? "Yes" : r.weekly === false ? "No" : "—");
      case "edge":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num",
          title: r.edge_tip || ""
        }, edgeCell(r));
      case "setup":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "wl-txt",
          title: r.edge_tip || ""
        }, setupCell(r));
      case "prem_sell":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "wl-txt"
        }, r.prem_sell || "—");
      case "swing_dir":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "wl-txt"
        }, swingCell(r));
      case "swing_stage":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "wl-txt"
        }, timingCell(r));
      case "tk_ev":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, evCell(r));
      case "tk_size":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, sizeCell(r));
      case "rvol_rank":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, volCell(r));
      case "last":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num",
          title: liveQ[r.symbol] != null ? "Live" : "Last scan"
        }, fmtUsd(liveLast(r), 2));
      case "market_cap":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, fmtMktCap(r.market_cap));
      case "pe":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, r.pe != null ? r.pe : "—");
      case "forward_pe":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, r.forward_pe != null ? r.forward_pe : "—");
      case "industry":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "wl-txt"
        }, r.industry || "—");
      case "sector":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "wl-txt"
        }, r.sector || "—");
      case "rsi":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, r.rsi != null ? r.rsi : "—");
      case "rel_vol":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, r.rel_vol != null ? r.rel_vol + "x" : "—");
      case "flow_net":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, flowCell(r));
      case "flow_agree":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "wl-txt"
        }, agreeCell(r));
      case "flow_bull":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num up"
        }, numOr(r.flow_bull));
      case "flow_bear":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num down"
        }, numOr(r.flow_bear));
      case "call_prem":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num up"
        }, prem$(r.call_prem));
      case "put_prem":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num down"
        }, prem$(r.put_prem));
      case "net_prem":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: `scan-num ${pctCls(r.net_prem)}`
        }, prem$(r.net_prem));
      case "pc_ratio":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, r.pc_ratio != null ? r.pc_ratio : "—");
      case "ask_call_prem":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, prem$(r.ask_call_prem));
      case "ask_put_prem":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, prem$(r.ask_put_prem));
      case "call_sweeps":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, numOr(r.call_sweeps));
      case "put_sweeps":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, numOr(r.put_sweeps));
      case "flow_alerts":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, numOr(r.flow_alerts));
      case "flow_quality":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, numOr(r.flow_quality));
      case "flow_cc_risk":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: `scan-num ${r.flow_cc_risk != null && r.flow_cc_risk >= 60 ? "down" : ""}`
        }, numOr(r.flow_cc_risk));
      case "flow_verdict":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "wl-txt",
          title: r.flow_verdict || ""
        }, r.flow_verdict || "—");
      case "next_earnings":
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, r.next_earnings ? fmtUSDate(r.next_earnings) : "—", r.days_to_earnings != null ? /*#__PURE__*/React.createElement("span", {
          className: "muted"
        }, " (", r.days_to_earnings, "d)") : "");
      case "from_open":
        {
          const v = foVal(r);
          return /*#__PURE__*/React.createElement("td", {
            key: k,
            className: `scan-num ${pctCls(v)}`,
            title: r.open != null ? `Open ${fmtUsd(r.open, 2)}` : "Open n/a"
          }, pct(v));
        }
      case "change":
        {
          const v = chgVal(r);
          return /*#__PURE__*/React.createElement("td", {
            key: k,
            className: `scan-num ${pctCls(v)}`
          }, pct(v));
        }
      case "wtd":
      case "mtd":
      case "qtd":
      case "ytd":
      case "from_ma20":
      case "from_ma50":
      case "from_ma200":
        {
          const v = reb(r, r[k]);
          return /*#__PURE__*/React.createElement("td", {
            key: k,
            className: `scan-num ${pctCls(v)}`
          }, pct(v));
        }
      default:
        return /*#__PURE__*/React.createElement("td", {
          key: k,
          className: "scan-num"
        }, "—");
    }
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "card ab-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Watchlist"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Tracked stocks — full metrics")), /*#__PURE__*/React.createElement("div", {
    className: "ab-controls"
  }, /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: startScan,
    disabled: scanning
  }, scanning ? "Scanning…" : "Scan now"))), /*#__PURE__*/React.createElement("div", {
    className: "ab-status"
  }, status.last_scan ? /*#__PURE__*/React.createElement("span", null, "Last scan ", new Date(status.last_scan).toLocaleString(), " · ", rows.length, " stocks") : /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "No scan yet — Scan now pulls valuation, momentum, volume, earnings & moving-average metrics for your tracked stocks (a few minutes for large lists)."), notScanned > 0 && status.last_scan && !scanning && /*#__PURE__*/React.createElement("span", {
    className: "wl-newhint",
    title: "These are in your watchlist but not in the last scan — added since the scan, or the data source returned no price. Re-scan to include them."
  }, " ", "· ", notScanned, " not in last scan — ", /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "wl-rescan-link",
    onClick: startScan
  }, "Scan now"), " to include"), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " · ", /*#__PURE__*/React.createElement("b", null, "Edge"), " = signed flow conviction (+long / −short), size-normalized; sort it to rank morning buys vs sells · hover a row for the driver breakdown · Auto-refreshes 9 AM & 6 PM ET · cached server-side"), status.error && /*#__PURE__*/React.createElement("span", {
    className: "ab-err"
  }, " · ", status.error), err && /*#__PURE__*/React.createElement("span", {
    className: "ab-err"
  }, " · ", err)), (() => {
    // Market-wide flow read (one UW call, whole market — not per row).
    const tide = market && market.tide;
    if (!tide) return null;
    const row = Array.isArray(tide) ? tide[tide.length - 1] : tide;
    if (!row) return null;
    const cp = row.net_call_premium ?? row.call_premium ?? null;
    const pp = row.net_put_premium ?? row.put_premium ?? null;
    if (cp == null && pp == null) return null;
    const net = (cp || 0) - (pp || 0);
    const tot = Math.abs(cp || 0) + Math.abs(pp || 0);
    const tilt = tot ? net / tot : 0; // -1..+1 regime strength
    const regime = net > 0 ? "Bullish" : net < 0 ? "Bearish" : "Neutral";
    const cls = net > 0 ? "up" : net < 0 ? "down" : "muted";
    // Regime gate: don't fight the tape. Strong one-sided tape → favor that side.
    const gate = tilt > 0.15 ? {
      txt: "Risk-on — favor longs, go easy on shorts",
      cls: "up"
    } : tilt < -0.15 ? {
      txt: "Risk-off — favor shorts/cash, go easy on longs",
      cls: "down"
    } : {
      txt: "Mixed tape — be selective, trade only the cleanest setups",
      cls: "muted"
    };
    return /*#__PURE__*/React.createElement("div", {
      className: "wl-market",
      title: "Whole-market options flow (net call − put premium today). One UW call, same for every row."
    }, /*#__PURE__*/React.createElement("span", {
      className: "wl-market-tag"
    }, "Market flow"), /*#__PURE__*/React.createElement("b", {
      className: cls
    }, regime), /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "net call − put"), /*#__PURE__*/React.createElement("b", {
      className: cls
    }, window.fmt$M(net)), /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "· calls ", window.fmt$M(cp), " / puts ", window.fmt$M(pp)), /*#__PURE__*/React.createElement("span", {
      className: `wl-regime ${gate.cls}`
    }, "· ", gate.txt));
  })(), scanning && /*#__PURE__*/React.createElement("div", {
    className: "ab-progress"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-progress-bar",
    style: {
      width: `${status.total ? status.scanned / status.total * 100 : 0}%`
    }
  }), /*#__PURE__*/React.createElement("span", {
    className: "ab-progress-txt"
  }, status.scanned || 0, " / ", status.total || 0)), rows.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-filters"
  }, /*#__PURE__*/React.createElement("div", {
    className: "wl-viewtabs",
    role: "tablist",
    "aria-label": "Watchlist view"
  }, [["stocks", "Stocks"], ["sectors", "Sectors"], ["industries", "Industries"]].map(([v, lbl]) => /*#__PURE__*/React.createElement("button", {
    key: v,
    type: "button",
    role: "tab",
    "aria-selected": view === v,
    className: view === v ? "active" : "",
    onClick: () => setView(v),
    title: v === "stocks" ? "Per-stock metrics" : `Premiums aggregated by ${v === "sectors" ? "sector" : "industry"} — see where money flows in and out`
  }, lbl))), view === "stocks" && /*#__PURE__*/React.createElement("input", {
    className: "sb-select ab-search",
    placeholder: "Symbol / company…",
    value: q,
    onChange: e => setQ(e.target.value)
  }), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fSector,
    onChange: e => setFSector(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "All sectors"), sectors.map(s => /*#__PURE__*/React.createElement("option", {
    key: s,
    value: s
  }, s))), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fIndustry,
    onChange: e => {
      const ind = e.target.value;
      setFIndustry(ind);
      // Auto-select the parent sector so the Sector filter reflects the
      // industry you picked (an industry lives in exactly one sector).
      if (ind !== "all") {
        const row = rows.find(r => r.industry === ind && r.sector);
        if (row) setFSector(row.sector);
      }
    }
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "All industries"), industryOpts.map(s => /*#__PURE__*/React.createElement("option", {
    key: s,
    value: s
  }, s))), tagOpts.length > 0 && /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fTag,
    onChange: e => setFTag(e.target.value),
    title: "Filter by your Tag (category from CSV import)"
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "All tags"), tagOpts.map(t => /*#__PURE__*/React.createElement("option", {
    key: t,
    value: t
  }, t))), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fMcap,
    onChange: e => setFMcap(e.target.value),
    title: "Filter by market cap (Finviz-style buckets)"
  }, MCAP_BUCKETS.map(([v, label]) => /*#__PURE__*/React.createElement("option", {
    key: v,
    value: v
  }, label))), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: `wl-prime-btn${primeOnly ? " on" : ""}`,
    onClick: () => setPrimeOnly(v => !v),
    title: "Prime setups: options flow and price-swing agree on direction AND the move is just starting — your highest-conviction, beginning-of-move trades."
  }, "★ Prime", primeCount ? ` (${primeCount})` : ""), /*#__PURE__*/React.createElement("label", {
    className: "wl-acct-wrap",
    title: "Account size — used to size each trade by risk"
  }, "$", /*#__PURE__*/React.createElement("input", {
    className: "wl-acct",
    type: "number",
    inputMode: "numeric",
    enterKeyHint: "done",
    min: "0",
    step: "1000",
    value: acct,
    onChange: e => setAcct(Number(e.target.value) || 0)
  })), /*#__PURE__*/React.createElement("label", {
    className: "wl-acct-wrap",
    title: "Risk per trade (% of account). Position size = this ÷ stop distance."
  }, "risk", /*#__PURE__*/React.createElement("input", {
    className: "wl-risk",
    type: "number",
    inputMode: "decimal",
    enterKeyHint: "done",
    min: "0",
    step: "0.1",
    value: riskPct,
    onChange: e => setRiskPct(Number(e.target.value) || 0)
  }), "%"), /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 12
    }
  }, view === "stocks" ? `${filtered.length} shown` : `${groups.length} ${view}`)), primeCrowd && /*#__PURE__*/React.createElement("div", {
    className: "wl-crowd",
    title: "Correlated names move together — sizing 4 trades in one sector is really one position's worth of risk."
  }, "⚠ Crowding: ", primeCrowd.n, " of ", primeCrowd.total, " Prime setups are ", /*#__PURE__*/React.createElement("b", null, primeCrowd.sector), " — that's really one bet. Spread risk across sectors or size each smaller."), view !== "stocks" ? groups.length > 0 ? /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap wl-scroll",
    style: {
      marginTop: 10
    }
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table wl-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, GCOLS.map(c => /*#__PURE__*/React.createElement("th", {
    key: c.k,
    className: `${c.num ? "scan-th-num" : ""} wl-th${gsort.key === c.k ? " active" : ""}`,
    onClick: () => setGsortKey(c.k),
    title: "Click to sort"
  }, c.label, gsort.key === c.k ? gsort.dir === "asc" ? " ▲" : " ▼" : "")))), /*#__PURE__*/React.createElement("tbody", null, groups.map(g => /*#__PURE__*/React.createElement("tr", {
    key: g.name,
    className: "scan-row wl-row",
    onClick: () => drillGroup(g.name),
    title: `Show ${g.name} stocks`
  }, /*#__PURE__*/React.createElement("td", {
    className: "wl-co"
  }, g.name), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.stocks, g.withFlow < g.stocks ? /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " (", g.withFlow, ")") : ""), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, fmtMktCap(g.mcap)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num up"
  }, g.nBull || "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num down"
  }, g.nBear || "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num up"
  }, prem$(g.bull)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num down"
  }, prem$(g.bear)), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${pctCls(g.net)}`
  }, /*#__PURE__*/React.createElement("b", null, prem$(g.net))), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.pc != null ? g.pc : "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, prem$(g.askC)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, prem$(g.askP)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.cSwp || "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.pSwp || "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.alerts || "—")))))) : !scanning && status.last_scan && /*#__PURE__*/React.createElement("div", {
    className: "ab-empty"
  }, "No flow data to aggregate yet — run a scan.") : filtered.length > 0 ? /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap wl-scroll",
    style: {
      marginTop: 10
    },
    ref: wlScrollRef,
    onScroll: onWlScroll
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table wl-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, orderedCols.map(c => /*#__PURE__*/React.createElement("th", {
    key: c.k,
    draggable: true,
    onDragStart: e => {
      dragColKey.current = c.k;
      e.dataTransfer.effectAllowed = "move";
    },
    onDragOver: e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
    },
    onDrop: e => {
      e.preventDefault();
      onColDrop(c.k);
    },
    className: `${c.num ? "scan-th-num" : ""} wl-th${sort.key === c.k ? " active" : ""}`,
    onClick: () => setSortKey(c.k),
    title: `${COL_TIPS[c.k] || c.label} · click to sort · drag to reorder`
  }, c.label, sort.key === c.k ? sort.dir === "asc" ? " ▲" : " ▼" : "")))), /*#__PURE__*/React.createElement("tbody", null, shown.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.symbol,
    className: "scan-row wl-row",
    onClick: () => onSwitchTicker && onSwitchTicker(r.symbol),
    onContextMenu: e => {
      e.preventDefault();
      setCtx({
        x: e.clientX,
        y: e.clientY,
        symbol: r.symbol
      });
    },
    title: `Open ${r.symbol} · right-click to remove`
  }, orderedCols.map(c => renderCell(c, r)))))), visN < filtered.length && /*#__PURE__*/React.createElement("div", {
    className: "wl-more",
    onClick: () => setVisN(n => Math.min(n + WL_CHUNK, filtered.length))
  }, "Showing ", visN, " of ", filtered.length, " — scroll or click for more")) : !scanning && status.last_scan && /*#__PURE__*/React.createElement("div", {
    className: "ab-empty"
  }, "No stocks match these filters."), ctx && /*#__PURE__*/React.createElement("div", {
    className: "wl-ctx",
    onClick: e => e.stopPropagation(),
    style: {
      left: Math.min(ctx.x, (typeof window !== "undefined" ? window.innerWidth : 9999) - 240),
      top: ctx.y
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "wl-ctx-head"
  }, ctx.symbol), /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: () => {
      if (onSwitchTicker) onSwitchTicker(ctx.symbol);
      setCtx(null);
    }
  }, "Open ", ctx.symbol), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "wl-ctx-danger",
    onClick: () => doRemove(ctx.symbol)
  }, "Remove from watchlist")));
}
function ScreenersHub({
  apiFetch,
  onSwitchTicker
}) {
  const KEY = "jerry_screener_sub_v1";
  const [sub, setSub] = useState(() => {
    try {
      return localStorage.getItem(KEY) || "analyst";
    } catch {
      return "analyst";
    }
  });
  const pick = id => {
    setSub(id);
    try {
      localStorage.setItem(KEY, id);
    } catch {}
  };
  const SUBS = [{
    id: "analyst",
    label: "Analyst calls"
  }, {
    id: "movers",
    label: "Movers"
  }, {
    id: "trend",
    label: "Trend"
  }, {
    id: "ivrank",
    label: "Vol Rank"
  }];
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "screener-subnav",
    role: "tablist",
    "aria-label": "Discovery screeners"
  }, SUBS.map(s => /*#__PURE__*/React.createElement("button", {
    key: s.id,
    type: "button",
    role: "tab",
    "aria-selected": sub === s.id,
    className: sub === s.id ? "active" : "",
    onClick: () => pick(s.id)
  }, s.label))), sub === "analyst" && /*#__PURE__*/React.createElement(AnalystBoardCard, {
    apiFetch: apiFetch,
    onSwitchTicker: onSwitchTicker
  }), sub === "movers" && /*#__PURE__*/React.createElement(MoversCard, {
    apiFetch: apiFetch,
    onSwitchTicker: onSwitchTicker
  }), sub === "trend" && /*#__PURE__*/React.createElement(TrendCard, {
    apiFetch: apiFetch,
    onSwitchTicker: onSwitchTicker
  }), sub === "ivrank" && /*#__PURE__*/React.createElement(IVRankCard, {
    apiFetch: apiFetch,
    onSwitchTicker: onSwitchTicker
  }));
}
function IVRankCard({
  apiFetch,
  onSwitchTicker
}) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [fReg, setFReg] = useState("all");
  const [fVolTrend, setFVolTrend] = useState("all");
  const [q, setQ] = useState("");
  const pollRef = useRef(null);
  const load = async () => {
    try {
      const r = await apiFetch("/api/ivrank");
      const d = await r.json();
      setBoard(d);
      return d;
    } catch (e) {
      setErr(String(e));
      return null;
    }
  };
  useEffect(() => {
    load();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);
  const startScan = async () => {
    setErr(null);
    try {
      await apiFetch("/api/ivrank/scan?force=1");
    } catch (e) {
      setErr(String(e));
      return;
    }
    await load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 4000);
  };
  const status = board && board.status || {};
  const rows = board && board.rows || [];
  const summary = board && board.summary || {};
  const scanning = !!status.scanning;
  const filtered = useMemo(() => rows.filter(r => {
    if (fReg !== "all" && r.regime !== fReg) return false;
    if (fVolTrend === "expanding" && !r.expanding) return false;
    if (fVolTrend === "contracting" && !r.contracting) return false;
    if (q && !String(r.ticker || "").toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  }), [rows, fReg, fVolTrend, q]);
  const regimeTone = rg => rg === "rich" ? "bull" : rg === "cheap" ? "bear" : "neutral";
  const Chips = ({
    rows
  }) => /*#__PURE__*/React.createElement("div", {
    className: "ab-chips"
  }, (rows || []).length === 0 && /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 12
    }
  }, "—"), (rows || []).map((r, i) => /*#__PURE__*/React.createElement("button", {
    key: r.ticker + i,
    className: `ab-chip ab-${regimeTone(r.regime)}`,
    onClick: () => onSwitchTicker(r.ticker),
    title: (r.reasons || []).join(" · ")
  }, r.ticker, " ", /*#__PURE__*/React.createElement("b", null, Math.round(r.rank)))));
  const SummaryBox = ({
    title,
    tone,
    children
  }) => /*#__PURE__*/React.createElement("div", {
    className: `ab-sumbox ${tone || ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-sumbox-title"
  }, title), children);
  return /*#__PURE__*/React.createElement("div", {
    className: "card ab-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Premium selling"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Volatility rank")), /*#__PURE__*/React.createElement("div", {
    className: "ab-controls"
  }, /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: startScan,
    disabled: scanning
  }, scanning ? "Scanning…" : "Scan now"))), /*#__PURE__*/React.createElement("div", {
    className: "ab-status"
  }, status.last_scan ? /*#__PURE__*/React.createElement("span", null, "Last scan ", new Date(status.last_scan).toLocaleString(), " · ", status.universe_size || 0, " names · ", rows.length, " ranked") : /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "No scan yet — ranks ~600 names by where their volatility sits in its 1-year range (rich = good for selling premium)."), status.error && /*#__PURE__*/React.createElement("span", {
    className: "ab-err"
  }, " · ", status.error), err && /*#__PURE__*/React.createElement("span", {
    className: "ab-err"
  }, " · ", err)), /*#__PURE__*/React.createElement("div", {
    className: "ab-status muted",
    style: {
      marginTop: -6
    }
  }, "Free realized-vol proxy for IV rank — exact option IV shows on the Trade tab per name."), scanning && /*#__PURE__*/React.createElement("div", {
    className: "ab-progress"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-progress-bar",
    style: {
      width: `${status.total ? status.scanned / status.total * 100 : 0}%`
    }
  }), /*#__PURE__*/React.createElement("span", {
    className: "ab-progress-txt"
  }, status.scanned || 0, " / ", status.total || 0)), rows.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-summary"
  }, /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Richest premium (sell)",
    tone: "up"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.richest
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Cheapest vol (buy)",
    tone: "down"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.cheapest
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Vol expanding",
    tone: "warn"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.expanding
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Vol contracting"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.contracting
  }))), rows.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-filters"
  }, /*#__PURE__*/React.createElement("input", {
    className: "sb-select ab-search",
    placeholder: "Ticker…",
    value: q,
    onChange: e => setQ(e.target.value)
  }), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fReg,
    onChange: e => setFReg(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any regime"), /*#__PURE__*/React.createElement("option", {
    value: "rich"
  }, "Rich (rank ≥70)"), /*#__PURE__*/React.createElement("option", {
    value: "elevated"
  }, "Elevated"), /*#__PURE__*/React.createElement("option", {
    value: "normal"
  }, "Normal"), /*#__PURE__*/React.createElement("option", {
    value: "cheap"
  }, "Cheap (rank <30)")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fVolTrend,
    onChange: e => setFVolTrend(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any vol trend"), /*#__PURE__*/React.createElement("option", {
    value: "expanding"
  }, "Vol expanding"), /*#__PURE__*/React.createElement("option", {
    value: "contracting"
  }, "Vol contracting"))), /*#__PURE__*/React.createElement("div", {
    className: "ab-board"
  }, rows.length === 0 && !scanning && /*#__PURE__*/React.createElement("div", {
    className: "ab-empty"
  }, "No vol data yet. Run a scan to rank the universe by volatility."), filtered.map((r, i) => /*#__PURE__*/React.createElement("div", {
    key: r.ticker + i,
    className: "ab-row",
    onClick: () => onSwitchTicker(r.ticker),
    title: "Open this ticker on the Trade tab"
  }, /*#__PURE__*/React.createElement("div", {
    className: `ab-scorebadge imp-${r.importance}`
  }, Math.round(r.rank)), /*#__PURE__*/React.createElement("div", {
    className: "ab-rowmain"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-rowtop"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ab-tk"
  }, r.ticker), /*#__PURE__*/React.createElement("span", {
    className: `ab-pill ab-${regimeTone(r.regime)}`
  }, r.regime), r.expanding && /*#__PURE__*/React.createElement("span", {
    className: "ab-pill ab-warn"
  }, "vol ↑"), r.contracting && /*#__PURE__*/React.createElement("span", {
    className: "ab-pill ab-multi"
  }, "vol ↓"), /*#__PURE__*/React.createElement("span", {
    className: "ab-sector"
  }, fmtUsd(r.last))), /*#__PURE__*/React.createElement("div", {
    className: "ab-rowsub"
  }, /*#__PURE__*/React.createElement("span", null, "HV ", /*#__PURE__*/React.createElement("b", null, r.hv, "%")), /*#__PURE__*/React.createElement("span", null, "1y range ", /*#__PURE__*/React.createElement("b", null, r.hv_low, "–", r.hv_high, "%")), /*#__PURE__*/React.createElement("span", null, "Vol rank ", /*#__PURE__*/React.createElement("b", null, Math.round(r.rank))), /*#__PURE__*/React.createElement("span", null, "Pctile ", /*#__PURE__*/React.createElement("b", null, Math.round(r.percentile)))), r.reasons && r.reasons.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-reasons"
  }, r.reasons.join(" · ")))))));
}
function TrendCard({
  apiFetch,
  onSwitchTicker
}) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [fDir, setFDir] = useState("all");
  const [fRsi, setFRsi] = useState("all"); // overbought / oversold
  const [fExt, setFExt] = useState("all"); // new_high / new_low
  const [minStr, setMinStr] = useState(0);
  const [q, setQ] = useState("");
  const pollRef = useRef(null);
  const load = async () => {
    try {
      const r = await apiFetch("/api/trend");
      const d = await r.json();
      setBoard(d);
      return d;
    } catch (e) {
      setErr(String(e));
      return null;
    }
  };
  useEffect(() => {
    load();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);
  const startScan = async () => {
    setErr(null);
    try {
      await apiFetch("/api/trend/scan?force=1");
    } catch (e) {
      setErr(String(e));
      return;
    }
    await load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 4000);
  };
  const status = board && board.status || {};
  const rows = board && board.rows || [];
  const summary = board && board.summary || {};
  const scanning = !!status.scanning;
  const filtered = useMemo(() => rows.filter(r => {
    if (fDir !== "all" && r.direction !== fDir) return false;
    if (fRsi === "overbought" && !r.overbought) return false;
    if (fRsi === "oversold" && !r.oversold) return false;
    if (fExt === "new_high" && !r.new_high) return false;
    if (fExt === "new_low" && !r.new_low) return false;
    if (minStr && r.score < minStr) return false;
    if (q && !String(r.ticker || "").toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  }), [rows, fDir, fRsi, fExt, minStr, q]);
  const Chips = ({
    rows
  }) => /*#__PURE__*/React.createElement("div", {
    className: "ab-chips"
  }, (rows || []).length === 0 && /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 12
    }
  }, "—"), (rows || []).map((r, i) => /*#__PURE__*/React.createElement("button", {
    key: r.ticker + i,
    className: `ab-chip ab-${r.direction === "up" ? "bull" : "bear"}`,
    onClick: () => onSwitchTicker(r.ticker),
    title: (r.reasons || []).join(" · ")
  }, r.ticker, " ", /*#__PURE__*/React.createElement("b", null, Math.round(r.score)))));
  const SummaryBox = ({
    title,
    tone,
    children
  }) => /*#__PURE__*/React.createElement("div", {
    className: `ab-sumbox ${tone || ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-sumbox-title"
  }, title), children);
  return /*#__PURE__*/React.createElement("div", {
    className: "card ab-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Trend & momentum"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "What's still trending")), /*#__PURE__*/React.createElement("div", {
    className: "ab-controls"
  }, /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: startScan,
    disabled: scanning
  }, scanning ? "Scanning…" : "Scan now"))), /*#__PURE__*/React.createElement("div", {
    className: "ab-status"
  }, status.last_scan ? /*#__PURE__*/React.createElement("span", null, "Last scan ", new Date(status.last_scan).toLocaleString(), " · ", status.universe_size || 0, " names · ", rows.length, " ranked") : /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "No scan yet — click ", /*#__PURE__*/React.createElement("b", null, "Scan now"), " (pulls ~1y of daily data for ~600 names; takes a few minutes)."), status.error && /*#__PURE__*/React.createElement("span", {
    className: "ab-err"
  }, " · ", status.error), err && /*#__PURE__*/React.createElement("span", {
    className: "ab-err"
  }, " · ", err)), scanning && /*#__PURE__*/React.createElement("div", {
    className: "ab-progress"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-progress-bar",
    style: {
      width: `${status.total ? status.scanned / status.total * 100 : 0}%`
    }
  }), /*#__PURE__*/React.createElement("span", {
    className: "ab-progress-txt"
  }, status.scanned || 0, " / ", status.total || 0)), rows.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-summary"
  }, /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Strongest uptrends",
    tone: "up"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.strongest_up
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Strongest downtrends",
    tone: "down"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.strongest_down
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "New 52wk highs",
    tone: "up"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.new_highs
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "New 52wk lows",
    tone: "down"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.new_lows
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Overbought (RSI≥70)",
    tone: "warn"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.overbought
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Oversold (RSI≤30)",
    tone: "warn"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.oversold
  }))), rows.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-filters"
  }, /*#__PURE__*/React.createElement("input", {
    className: "sb-select ab-search",
    placeholder: "Ticker…",
    value: q,
    onChange: e => setQ(e.target.value)
  }), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fDir,
    onChange: e => setFDir(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Up & down"), /*#__PURE__*/React.createElement("option", {
    value: "up"
  }, "Uptrends"), /*#__PURE__*/React.createElement("option", {
    value: "down"
  }, "Downtrends")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fRsi,
    onChange: e => setFRsi(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any RSI"), /*#__PURE__*/React.createElement("option", {
    value: "overbought"
  }, "Overbought"), /*#__PURE__*/React.createElement("option", {
    value: "oversold"
  }, "Oversold")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fExt,
    onChange: e => setFExt(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any level"), /*#__PURE__*/React.createElement("option", {
    value: "new_high"
  }, "Near 52wk high"), /*#__PURE__*/React.createElement("option", {
    value: "new_low"
  }, "Near 52wk low")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: minStr,
    onChange: e => setMinStr(+e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: 0
  }, "Any strength"), /*#__PURE__*/React.createElement("option", {
    value: 40
  }, "≥ 40"), /*#__PURE__*/React.createElement("option", {
    value: 55
  }, "≥ 55 (strong)"))), /*#__PURE__*/React.createElement("div", {
    className: "ab-board"
  }, rows.length === 0 && !scanning && /*#__PURE__*/React.createElement("div", {
    className: "ab-empty"
  }, "No trend data yet. Run a scan to rank the universe by trend strength."), filtered.map((r, i) => /*#__PURE__*/React.createElement("div", {
    key: r.ticker + i,
    className: "ab-row",
    onClick: () => onSwitchTicker(r.ticker),
    title: "Open this ticker on the Trade tab"
  }, /*#__PURE__*/React.createElement("div", {
    className: `ab-scorebadge imp-${r.importance}`
  }, Math.round(r.score)), /*#__PURE__*/React.createElement("div", {
    className: "ab-rowmain"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-rowtop"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ab-tk"
  }, r.ticker), /*#__PURE__*/React.createElement("span", {
    className: `ab-pill ab-${r.direction === "up" ? "bull" : "bear"}`
  }, r.direction === "up" ? "Uptrend" : "Downtrend"), r.new_high && /*#__PURE__*/React.createElement("span", {
    className: "ab-pill ab-multi"
  }, "52wk high"), r.new_low && /*#__PURE__*/React.createElement("span", {
    className: "ab-pill ab-warn"
  }, "52wk low"), r.overbought && /*#__PURE__*/React.createElement("span", {
    className: "ab-pill ab-warn"
  }, "overbought"), r.oversold && /*#__PURE__*/React.createElement("span", {
    className: "ab-pill ab-multi"
  }, "oversold"), /*#__PURE__*/React.createElement("span", {
    className: "ab-sector"
  }, fmtUsd(r.last))), /*#__PURE__*/React.createElement("div", {
    className: "ab-rowsub"
  }, r.rsi != null && /*#__PURE__*/React.createElement("span", null, "RSI ", /*#__PURE__*/React.createElement("b", null, r.rsi)), r.from_high != null && /*#__PURE__*/React.createElement("span", null, "From 52wk hi ", /*#__PURE__*/React.createElement("b", null, r.from_high, "%")), r.streak ? /*#__PURE__*/React.createElement("span", null, "Streak ", /*#__PURE__*/React.createElement("b", null, r.streak > 0 ? `+${r.streak}` : r.streak, "d")) : null, /*#__PURE__*/React.createElement("span", null, "200-DMA ", /*#__PURE__*/React.createElement("b", null, r.above_ma200 ? "above" : "below"))), r.reasons && r.reasons.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-reasons"
  }, r.reasons.join(" · ")))))));
}
function MoversCard({
  apiFetch,
  onSwitchTicker
}) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [fDir, setFDir] = useState("all");
  const [fCap, setFCap] = useState("all");
  const [minGap, setMinGap] = useState(0);
  const [fCat, setFCat] = useState(false);
  const [q, setQ] = useState("");
  const pollRef = useRef(null);
  const load = async () => {
    try {
      const r = await apiFetch("/api/movers");
      const d = await r.json();
      setBoard(d);
      return d;
    } catch (e) {
      setErr(String(e));
      return null;
    }
  };
  useEffect(() => {
    load();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);
  const startScan = async () => {
    setErr(null);
    try {
      await apiFetch("/api/movers/scan?force=1");
    } catch (e) {
      setErr(String(e));
      return;
    }
    await load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 4000);
  };
  const status = board && board.status || {};
  const movers = board && board.movers || [];
  const summary = board && board.summary || {};
  const scanning = !!status.scanning;
  const capBucket = mc => {
    if (!mc) return "unknown";
    const b = mc / 1e9;
    if (b >= 200) return "mega";
    if (b >= 50) return "large";
    if (b >= 10) return "mid";
    return "small";
  };
  const filtered = useMemo(() => movers.filter(m => {
    if (fDir !== "all" && m.direction !== fDir) return false;
    if (fCap !== "all" && capBucket(m.market_cap) !== fCap) return false;
    if (minGap && Math.abs(m.gap_pct || 0) < minGap) return false;
    if (fCat && !m.has_analyst) return false;
    if (q && !String(m.ticker || "").toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  }), [movers, fDir, fCap, minGap, fCat, q]);
  const fmtPct = v => v == null ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(2) + "%";
  const fmtCap = fmtMktCap;
  const fmtVol = v => {
    if (!v) return "—";
    if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
    return String(v);
  };
  const Chips = ({
    rows
  }) => /*#__PURE__*/React.createElement("div", {
    className: "ab-chips"
  }, (rows || []).length === 0 && /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 12
    }
  }, "—"), (rows || []).map((m, i) => /*#__PURE__*/React.createElement("button", {
    key: m.ticker + i,
    className: `ab-chip ab-${m.direction === "up" ? "bull" : "bear"}`,
    onClick: () => onSwitchTicker(m.ticker),
    title: (m.reasons || []).join(" · ")
  }, m.ticker, " ", /*#__PURE__*/React.createElement("b", null, fmtPct(m.gap_pct)))));
  const SummaryBox = ({
    title,
    tone,
    children
  }) => /*#__PURE__*/React.createElement("div", {
    className: `ab-sumbox ${tone || ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-sumbox-title"
  }, title), children);
  return /*#__PURE__*/React.createElement("div", {
    className: "card ab-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Pre-market game plan"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "What's moving today")), /*#__PURE__*/React.createElement("div", {
    className: "ab-controls"
  }, /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: startScan,
    disabled: scanning
  }, scanning ? "Scanning…" : "Scan now"))), /*#__PURE__*/React.createElement("div", {
    className: "ab-status"
  }, status.last_scan ? /*#__PURE__*/React.createElement("span", null, "Last scan ", new Date(status.last_scan).toLocaleString(), " · ", status.universe_size || 0, " names · ", movers.length, " movers") : /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "No scan yet — click ", /*#__PURE__*/React.createElement("b", null, "Scan now"), " (needs Schwab; most useful during pre-market hours)."), status.error && /*#__PURE__*/React.createElement("span", {
    className: "ab-err"
  }, " · ", status.error), err && /*#__PURE__*/React.createElement("span", {
    className: "ab-err"
  }, " · ", err)), scanning && /*#__PURE__*/React.createElement("div", {
    className: "ab-progress"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-progress-bar",
    style: {
      width: `${status.total ? status.scanned / status.total * 100 : 0}%`
    }
  }), /*#__PURE__*/React.createElement("span", {
    className: "ab-progress-txt"
  }, status.scanned || 0, " / ", status.total || 0)), movers.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-summary"
  }, /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Top gainers",
    tone: "up"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.top_gainers
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Top losers",
    tone: "down"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.top_losers
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Heaviest volume"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.high_relvol
  })), /*#__PURE__*/React.createElement(SummaryBox, {
    title: "Moving + analyst call",
    tone: "up"
  }, /*#__PURE__*/React.createElement(Chips, {
    rows: summary.with_catalyst
  }))), movers.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-filters"
  }, /*#__PURE__*/React.createElement("input", {
    className: "sb-select ab-search",
    placeholder: "Ticker…",
    value: q,
    onChange: e => setQ(e.target.value)
  }), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fDir,
    onChange: e => setFDir(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Up & down"), /*#__PURE__*/React.createElement("option", {
    value: "up"
  }, "Gainers"), /*#__PURE__*/React.createElement("option", {
    value: "down"
  }, "Losers")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: fCap,
    onChange: e => setFCap(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any cap"), /*#__PURE__*/React.createElement("option", {
    value: "mega"
  }, "Mega (≥$200B)"), /*#__PURE__*/React.createElement("option", {
    value: "large"
  }, "Large ($50–200B)"), /*#__PURE__*/React.createElement("option", {
    value: "mid"
  }, "Mid ($10–50B)"), /*#__PURE__*/React.createElement("option", {
    value: "small"
  }, "Small (<$10B)")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: minGap,
    onChange: e => setMinGap(+e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: 0
  }, "Any move"), /*#__PURE__*/React.createElement("option", {
    value: 2
  }, "≥ 2%"), /*#__PURE__*/React.createElement("option", {
    value: 5
  }, "≥ 5%"), /*#__PURE__*/React.createElement("option", {
    value: 10
  }, "≥ 10%")), /*#__PURE__*/React.createElement("label", {
    className: "ab-toggle"
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: fCat,
    onChange: e => setFCat(e.target.checked)
  }), " Has analyst call")), /*#__PURE__*/React.createElement("div", {
    className: "ab-board"
  }, movers.length === 0 && !scanning && /*#__PURE__*/React.createElement("div", {
    className: "ab-empty"
  }, "No movers yet. Run a scan (best during pre-market hours, with Schwab connected)."), filtered.map((m, i) => /*#__PURE__*/React.createElement("div", {
    key: m.ticker + i,
    className: "ab-row",
    onClick: () => onSwitchTicker(m.ticker),
    title: "Open this ticker on the Trade tab"
  }, /*#__PURE__*/React.createElement("div", {
    className: `ab-scorebadge imp-${m.importance}`
  }, Math.round(m.score)), /*#__PURE__*/React.createElement("div", {
    className: "ab-rowmain"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ab-rowtop"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ab-tk"
  }, m.ticker), /*#__PURE__*/React.createElement("span", {
    className: `ab-pill ab-${m.direction === "up" ? "bull" : "bear"}`
  }, fmtPct(m.gap_pct)), m.has_analyst && /*#__PURE__*/React.createElement("span", {
    className: "ab-pill ab-multi"
  }, "analyst call"), m.company && /*#__PURE__*/React.createElement("span", {
    className: "ab-company"
  }, m.company), /*#__PURE__*/React.createElement("span", {
    className: "ab-sector"
  }, m.sector)), /*#__PURE__*/React.createElement("div", {
    className: "ab-rowsub"
  }, /*#__PURE__*/React.createElement("span", null, "Last ", /*#__PURE__*/React.createElement("b", null, fmtUsd(m.last))), /*#__PURE__*/React.createElement("span", null, "Pre-mkt vol ", /*#__PURE__*/React.createElement("b", null, fmtVol(m.premarket_vol))), m.rel_vol != null && /*#__PURE__*/React.createElement("span", null, "Rel vol ", /*#__PURE__*/React.createElement("b", null, m.rel_vol, "x")), /*#__PURE__*/React.createElement("span", {
    className: "ab-cap"
  }, fmtCap(m.market_cap))), m.reasons && m.reasons.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ab-reasons"
  }, m.reasons.join(" · ")))))));
}
function WatchlistAlertsCard({
  apiFetch,
  onSwitchTicker
}) {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [collapsed, setCollapsed] = useState(false);
  const [lastFetched, setLastFetched] = useState(null);
  const fetchAlerts = async () => {
    if (!apiFetch) return;
    setLoading(true);
    setError(null);
    try {
      const r = await apiFetch("/api/watchlist_alerts?lookback=7");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setAlerts(Array.isArray(j.alerts) ? j.alerts : []);
      setLastFetched(new Date());
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    fetchAlerts();
    // 5 min poll. Cheap on the analyst client which caches per-ticker.
    const id = setInterval(skipWhenHidden(fetchAlerts), 5 * 60 * 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const dismiss = async alertId => {
    // Optimistic remove. Backend write is best-effort.
    setAlerts(prev => prev.filter(a => a.id !== alertId));
    try {
      await apiFetch("/api/watchlist_alerts/dismiss", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          id: alertId
        })
      });
    } catch (e) {
      // If write fails, the alert will reappear on next poll. Acceptable.
      console.warn("dismiss failed", e);
    }
  };
  if (alerts.length === 0 && !loading && !error) return null;
  const kindLabel = k => ({
    upgrade: "Upgrade",
    downgrade: "Downgrade",
    target_raise: "Target raised",
    target_cut: "Target cut"
  })[k] || k;
  const kindClass = k => ({
    upgrade: "wa-up",
    target_raise: "wa-up",
    downgrade: "wa-down",
    target_cut: "wa-down"
  })[k] || "";
  return /*#__PURE__*/React.createElement("div", {
    className: "card watchlist-alerts-card",
    style: {
      marginBottom: "var(--row-gap)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker",
    title: "Fresh analyst signals on tickers in your watchlist within the last 7 days. Polled every 5 minutes. Dismissed alerts do not reappear."
  }, "Watchlist · last 7 days · ", alerts.length, " fresh signal", alerts.length === 1 ? "" : "s"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Analyst alerts")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 8,
      alignItems: "center"
    }
  }, lastFetched && /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 11
    },
    title: "Time of last poll. Auto-refreshes every 5 minutes."
  }, "Updated ", lastFetched.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit"
  })), /*#__PURE__*/React.createElement("button", {
    className: "wa-collapse-btn",
    onClick: () => setCollapsed(v => !v),
    title: collapsed ? "Expand the alerts list." : "Collapse the alerts list."
  }, collapsed ? "Expand" : "Collapse"))), error && /*#__PURE__*/React.createElement("div", {
    className: "wa-error"
  }, "Error loading alerts: ", error), !collapsed && /*#__PURE__*/React.createElement("div", {
    className: "wa-list"
  }, alerts.map(a => /*#__PURE__*/React.createElement("div", {
    key: a.id,
    className: `wa-row ${kindClass(a.kind)}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "wa-row-left"
  }, /*#__PURE__*/React.createElement("span", {
    className: "wa-symbol",
    title: `${a.symbol}. Click Switch to load it on the dashboard.`
  }, a.symbol), /*#__PURE__*/React.createElement("span", {
    className: `wa-kind ${kindClass(a.kind)}`,
    title: `${kindLabel(a.kind)} from ${a.firm} on ${a.date}.`
  }, kindLabel(a.kind)), /*#__PURE__*/React.createElement("span", {
    className: "wa-firm",
    title: `Originating firm: ${a.firm}`
  }, a.firm), a.from_grade && a.to_grade && /*#__PURE__*/React.createElement("span", {
    className: "wa-grades",
    title: `Rating change: ${a.from_grade} → ${a.to_grade}`
  }, a.from_grade, " → ", a.to_grade), /*#__PURE__*/React.createElement("span", {
    className: "wa-date",
    title: "Date the signal was issued."
  }, fmtUSDate(a.date))), /*#__PURE__*/React.createElement("div", {
    className: "wa-row-right"
  }, /*#__PURE__*/React.createElement("button", {
    className: "wa-switch",
    onClick: () => onSwitchTicker && onSwitchTicker(a.symbol),
    title: `Switch the dashboard to ${a.symbol} so you can review the chart, chain, and rec verdicts.`
  }, "Switch"), /*#__PURE__*/React.createElement("button", {
    className: "wa-dismiss",
    onClick: () => dismiss(a.id),
    title: "Dismiss this alert. It will not reappear on subsequent polls."
  }, "✕"))))));
}
function TabBar({
  active,
  onChange,
  ticker,
  earnDate,
  earnDays,
  tabs,
  onReorder
}) {
  const hasEarn = earnDate != null;
  const soon = earnDays != null && earnDays >= 0 && earnDays <= 7;
  const list = tabs && tabs.length ? tabs : TABS;
  const [dragId, setDragId] = useState(null);
  const [overId, setOverId] = useState(null);
  const drop = targetId => {
    if (!onReorder || !dragId || dragId === targetId) {
      setDragId(null);
      setOverId(null);
      return;
    }
    const ids = list.map(t => t.id);
    const from = ids.indexOf(dragId),
      to = ids.indexOf(targetId);
    if (from < 0 || to < 0) {
      setDragId(null);
      setOverId(null);
      return;
    }
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    onReorder(ids);
    setDragId(null);
    setOverId(null);
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "tab-bar",
    role: "tablist",
    "aria-label": "Dashboard sections",
    title: "Switch sections. Drag a tab to reorder; the order is saved to all your devices. Cards stay live in the background, so switching is instant and nothing reloads."
  }, list.map(t => /*#__PURE__*/React.createElement("button", {
    key: t.id,
    type: "button",
    role: "tab",
    "aria-selected": active === t.id,
    className: `tab-btn ${active === t.id ? "active" : ""}${dragId === t.id ? " dragging" : ""}${overId === t.id && dragId && overId !== dragId ? " drop-target" : ""}`,
    onClick: () => onChange(t.id),
    draggable: !!onReorder,
    onDragStart: e => {
      setDragId(t.id);
      try {
        e.dataTransfer.effectAllowed = "move";
      } catch (_) {}
    },
    onDragOver: e => {
      if (dragId) {
        e.preventDefault();
        setOverId(t.id);
      }
    },
    onDrop: e => {
      e.preventDefault();
      drop(t.id);
    },
    onDragEnd: () => {
      setDragId(null);
      setOverId(null);
    },
    title: `Show the ${t.label} section. Drag to reorder.`
  }, t.label)), hasEarn && /*#__PURE__*/React.createElement("div", {
    className: `tab-earn ${soon ? "soon" : ""}`,
    title: `Next earnings report for ${ticker}${earnDays != null ? ` — in ${earnDays} day${earnDays === 1 ? "" : "s"}` : ""}.`
  }, /*#__PURE__*/React.createElement("span", {
    className: "tab-earn-lbl"
  }, ticker, " earnings"), /*#__PURE__*/React.createElement("b", null, fmtSwingDate(earnDate)), earnDays != null && /*#__PURE__*/React.createElement("span", {
    className: "tab-earn-days"
  }, earnDays === 0 ? "today" : earnDays > 0 ? `in ${earnDays}d` : `${-earnDays}d ago`)));
}
function TabPanel({
  tab,
  active,
  children
}) {
  // Lazy-mount: render children only after the tab is first activated, then
  // keep them mounted (hidden) so they stay live. Avoids paying the mount /
  // fetch cost for sections you never open — faster initial load on mobile.
  const seen = useRef(active === tab);
  if (active === tab) seen.current = true;
  return /*#__PURE__*/React.createElement("div", {
    className: "tab-panel",
    role: "tabpanel",
    "data-tab": tab,
    style: active === tab ? undefined : {
      display: "none"
    }
  }, seen.current ? children : null);
}
function WeatherBadge() {
  const WX_KEY = "jerry_weather_v1";
  const persisted = (() => {
    try {
      return JSON.parse(localStorage.getItem(WX_KEY)) || {};
    } catch {
      return {};
    }
  })();
  const [useGeo, setUseGeo] = useState(persisted.useGeo === true);
  const [wx, setWx] = useState(null); // { temp, code, time } or null
  const [place, setPlace] = useState(persisted.useGeo ? "your location" : WeatherUtil.DEFAULT_COORDS.label);
  const [err, setErr] = useState(false);
  const load = React.useCallback((coords, label) => {
    const url = WeatherUtil.buildForecastUrl(coords.lat, coords.lon, "fahrenheit");
    fetch(url).then(r => r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status))).then(j => {
      const cur = WeatherUtil.parseCurrent(j);
      if (!cur) throw new Error("bad shape");
      setWx(cur);
      setErr(false);
      if (label) setPlace(label);
    }).catch(() => setErr(true));
  }, []);

  // Device geolocation when opted in, else the Yonkers default. A denied
  // or failed geolocation falls back to Yonkers rather than going blank.
  const resolveAndLoad = React.useCallback(() => {
    if (useGeo && typeof navigator !== "undefined" && navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(pos => load({
        lat: pos.coords.latitude,
        lon: pos.coords.longitude
      }, "your location"), () => load(WeatherUtil.DEFAULT_COORDS, WeatherUtil.DEFAULT_COORDS.label), {
        timeout: 8000,
        maximumAge: 600000
      });
    } else {
      load(WeatherUtil.DEFAULT_COORDS, WeatherUtil.DEFAULT_COORDS.label);
    }
  }, [useGeo, load]);
  useEffect(() => {
    resolveAndLoad();
    const id = setInterval(skipWhenHidden(resolveAndLoad), 15 * 60 * 1000);
    return () => clearInterval(id);
  }, [resolveAndLoad]);
  const toggleGeo = () => {
    const next = !useGeo;
    setUseGeo(next);
    try {
      localStorage.setItem(WX_KEY, JSON.stringify({
        useGeo: next
      }));
    } catch {}
  };
  const meta = wx ? WeatherUtil.wxFromCode(wx.code) : null;
  const temp = wx ? WeatherUtil.formatTemp(wx.temp) : "—";
  const title = err ? "Weather unavailable. Open-Meteo did not respond. Tap to retry." : `${meta ? meta.label : "Loading"}, ${temp} at ${place}. Source Open-Meteo. Tap to ${useGeo ? "switch to Yonkers" : "use your location"}.`;
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: `sb-weather-pill${err ? " wx-err" : ""}`,
    onClick: toggleGeo,
    title: title
  }, /*#__PURE__*/React.createElement("span", {
    className: "wx-icon"
  }, err ? "⚠️" : meta ? meta.icon : "🌡️"), /*#__PURE__*/React.createElement("span", {
    className: "wx-temp"
  }, err ? "wx" : temp));
}
function LevelRepriceCard({
  ticker,
  currentPrice,
  calls,
  puts,
  sugCall,
  sugPut,
  expectedMove,
  weeklyRows,
  activeExpDate,
  frontDte,
  apiFetch,
  strategyMode,
  livePrice
}) {
  const [mode, setMode] = useState("gap");
  const [kind, setKind] = useState(strategyMode === "csp" ? "put" : "call");
  useEffect(() => {
    setKind(strategyMode === "csp" ? "put" : "call");
  }, [strategyMode]);

  // This week's expirations with their own chains, so names like AAPL
  // and the index ETFs can pick Mon/Wed/Fri or 0DTE. Falls back to the
  // front-weekly chain passed in props if the fetch fails or is empty.
  const [weekChains, setWeekChains] = useState(null); // {expirations:[{date,dte}], chains:{date:{calls,puts}}}
  const [expDate, setExpDate] = useState(null); // selected expiration ISO
  const expISOprop = activeExpDate ? activeExpDate.toISOString().slice(0, 10) : undefined;
  useEffect(() => {
    let alive = true;
    if (!ticker || !apiFetch) return;
    (async () => {
      try {
        const r = await apiFetch(`/api/reprice/chain?symbol=${encodeURIComponent(ticker)}`);
        const d = await r.json();
        if (!alive) return;
        if (r.ok && d && Array.isArray(d.expirations) && d.expirations.length) {
          setWeekChains(d);
          // Default to the dashboard's front weekly if present, else the nearest.
          const have = d.expirations.map(e => e.date);
          setExpDate(have.includes(expISOprop) ? expISOprop : have[0]);
        } else {
          setWeekChains(null); // fall back to props chain
        }
      } catch (e) {
        if (alive) setWeekChains(null);
      }
    })();
    return () => {
      alive = false;
    };
  }, [ticker]); // eslint-disable-line

  const expList = weekChains?.expirations || (activeExpDate ? [{
    date: expISOprop,
    dte: frontDte || 7
  }] : []);
  const selExp = expDate || expISOprop || expList[0] && expList[0].date || null;
  // Legs for the selected expiration and side. Use the fetched week chain
  // when available, else the front-weekly props.
  const legs = React.useMemo(() => {
    if (weekChains && selExp && weekChains.chains[selExp]) {
      const side = weekChains.chains[selExp][kind === "put" ? "puts" : "calls"] || [];
      if (side.length) return side;
    }
    return kind === "put" ? puts || [] : calls || [];
  }, [weekChains, selExp, kind, calls, puts]);
  const strikes = React.useMemo(() => legs.map(l => l.strike).filter(s => s != null).sort((a, b) => a - b), [legs]);
  // Default the strike to the expected-move level: the implied move up
  // for a call, down for a put, snapped to the nearest listed strike.
  // This is usually where Jerry wants to sell, so it saves retyping per
  // stock. Falls back to the suggested 0.20 delta strike, then spot.
  const expMoveStrike = expectedMove && currentPrice ? kind === "put" ? currentPrice - expectedMove : currentPrice + expectedMove : null;
  const suggested = kind === "put" ? sugPut : sugCall;
  const [strike, setStrike] = useState(null);
  // Hold a manual strike pick for 20 seconds before the expected-move
  // default reapplies, so a live price tick (which recomputes the
  // default) does not snap your temp strike back while you are checking
  // it. Resets immediately on ticker, side, or expiry change.
  const strikeEdited = React.useRef(false);
  const strikeTimer = React.useRef(null);
  const expMoveStrikeRef = React.useRef(null);
  const strikesRef = React.useRef([]);
  useEffect(() => {
    expMoveStrikeRef.current = expMoveStrike;
    strikesRef.current = strikes;
  });
  const snapDefaultStrike = React.useCallback(() => {
    const ss = strikesRef.current;
    if (!ss.length) return;
    const want = expMoveStrikeRef.current || suggested || currentPrice || ss[0];
    const near = ss.reduce((a, b) => Math.abs(b - want) < Math.abs(a - want) ? b : a, ss[0]);
    setStrike(near);
  }, [currentPrice, suggested]);
  useEffect(() => {
    // reset the hold when side/expiry/ticker changes
    strikeEdited.current = false;
    clearTimeout(strikeTimer.current);
  }, [kind, selExp, ticker]);
  useEffect(() => {
    if (strikeEdited.current) return; // do not clobber a held manual pick
    snapDefaultStrike();
  }, [kind, selExp, strikes.length, expMoveStrike, snapDefaultStrike]);
  const onStrikePick = v => {
    setStrike(Number(v));
    strikeEdited.current = true;
    clearTimeout(strikeTimer.current);
    strikeTimer.current = setTimeout(() => {
      strikeEdited.current = false;
      snapDefaultStrike();
    }, 20000);
  };
  const leg = React.useMemo(() => legs.find(l => l.strike === Number(strike)) || null, [legs, strike]);
  const legMid = leg ? leg.bid > 0 && leg.ask > 0 ? (leg.bid + leg.ask) / 2 : leg.last || 0 : 0;

  // Days to exp derived from the SELECTED expiration, not a manual field.
  const selExpMeta = expList.find(e => e.date === selExp);
  const dte = selExpMeta ? selExpMeta.dte : frontDte || 7;
  const expLabel = selExp ? new Date(selExp + "T00:00:00").toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric"
  }) : "front weekly";
  const multiExp = expList.length > 1;
  const [rate, setRate] = useState("4.00");
  const [spotNow, setSpotNow] = useState(currentPrice ? currentPrice.toFixed(2) : "");
  const [target, setTarget] = useState("");
  const [gapHours, setGapHours] = useState(17);
  useEffect(() => {
    if (currentPrice) setSpotNow(currentPrice.toFixed(2));
  }, [currentPrice]);

  // Jerry's default target: average Monday high percent plus average
  // Tuesday high percent across the weekly history, applied to the
  // current price. day_breakdown[day].high is the day's high versus its
  // prior close, in percent. This is the same data the Day of week card
  // reads. The target pre-fills per ticker and stays editable, so it no
  // longer carries the last stock's number over.
  const monTueTarget = React.useMemo(() => {
    if (!Array.isArray(weeklyRows) || !weeklyRows.length || !currentPrice) return null;
    const avgHigh = day => {
      const v = [];
      for (const r of weeklyRows) {
        const db = r.day_breakdown && r.day_breakdown[day];
        if (db && db.high != null && isFinite(db.high)) v.push(db.high);
      }
      return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
    };
    const mon = avgHigh("Mon"),
      tue = avgHigh("Tue");
    if (mon == null && tue == null) return null;
    const pct = (mon || 0) + (tue || 0);
    return +(currentPrice * (1 + pct / 100)).toFixed(2);
  }, [weeklyRows, currentPrice]);
  // Pre-fill the target when the ticker changes, so it starts from the
  // Mon+Tue high rather than the last stock's value.
  // Hold a manual target edit for 20 seconds before the Mon+Tue default
  // reapplies, so a live price tick does not snap your temp target back
  // while you check it. Resets immediately on ticker change.
  const targetEdited = React.useRef(false);
  const targetTimer = React.useRef(null);
  const monTueRef = React.useRef(null);
  useEffect(() => {
    monTueRef.current = monTueTarget;
  });
  const applyDefaultTarget = React.useCallback(() => {
    const d = monTueRef.current;
    setTarget(d != null ? String(d) : "");
  }, []);
  useEffect(() => {
    // reset the hold on ticker change
    targetEdited.current = false;
    clearTimeout(targetTimer.current);
  }, [ticker]);
  useEffect(() => {
    if (targetEdited.current) return; // do not clobber a held manual edit
    applyDefaultTarget();
  }, [ticker, monTueTarget, applyDefaultTarget]);
  const onTargetChange = v => {
    setTarget(v);
    targetEdited.current = true;
    clearTimeout(targetTimer.current);
    targetTimer.current = setTimeout(() => {
      targetEdited.current = false;
      applyDefaultTarget();
    }, 20000);
  };

  // Fade inputs
  const [contracts, setContracts] = useState(1);
  const [hoursHeld, setHoursHeld] = useState(2);
  const [pctMode, setPctMode] = useState(false);
  const [sell, setSell] = useState("");
  const [cover, setCover] = useState("");
  const [stop, setStop] = useState("");
  const [out, setOut] = useState(null);
  const [fade, setFade] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const fmt = (n, d = 2) => n == null || isNaN(n) ? "—" : Number(n).toFixed(d);
  const base = () => ({
    ticker,
    kind,
    strike: Number(strike),
    days_to_exp: dte,
    r: parseFloat(rate) / 100,
    expiration: selExp || expISOprop,
    current_price: legMid > 0 ? +legMid.toFixed(4) : undefined
  });
  const toSpot = (v, ref) => {
    const n = parseFloat(v);
    if (isNaN(n)) return null;
    return pctMode ? +(ref * (1 + n / 100)).toFixed(2) : n;
  };
  const runGap = async () => {
    setErr(null);
    setBusy(true);
    setOut(null);
    try {
      if (!leg || legMid <= 0) throw new Error("no quote on the selected strike; pick another strike");
      const sNow = parseFloat(spotNow),
        tgt = parseFloat(target);
      if (isNaN(sNow) || isNaN(tgt)) throw new Error("enter stock now and a target price");
      const hrs = parseFloat(gapHours) || 0;
      const levels = [{
        label: "now",
        target_spot: sNow,
        hours_from_now: 0,
        iv_shift: 0
      }, {
        label: "flat",
        target_spot: tgt,
        hours_from_now: hrs,
        iv_shift: 0
      }, {
        label: "ivup",
        target_spot: tgt,
        hours_from_now: hrs,
        iv_shift: 0.05
      }, {
        label: "ivdn5",
        target_spot: tgt,
        hours_from_now: hrs,
        iv_shift: -0.05
      }, {
        label: "ivdn10",
        target_spot: tgt,
        hours_from_now: hrs,
        iv_shift: -0.10
      }];
      const r = await apiFetch("/api/reprice", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          ...base(),
          spot_now: sNow,
          levels
        })
      });
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || "reprice failed");
      const rows = {};
      d.levels.forEach(x => {
        rows[x.label] = x;
      });
      const q = d.current_price_used != null ? d.current_price_used : legMid;
      const sd = rows.now ? rows.now.delta : leg.delta ?? null;
      const deltaEst = q != null && sd != null ? q + sd * (tgt - sNow) : null;
      setOut({
        iv: d.implied_vol_now,
        q,
        startDelta: sd,
        deltaEst,
        flat: rows.flat ? rows.flat.price : null,
        sweep: [{
          lbl: "IV +5 pts (vol expands)",
          v: rows.ivup ? rows.ivup.price : null
        }, {
          lbl: "IV flat",
          v: rows.flat ? rows.flat.price : null
        }, {
          lbl: "IV -5 pts (crush)",
          v: rows.ivdn5 ? rows.ivdn5.price : null
        }, {
          lbl: "IV -10 pts (hard crush)",
          v: rows.ivdn10 ? rows.ivdn10.price : null
        }],
        tgt,
        sNow
      });
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  const runFade = async () => {
    setErr(null);
    setBusy(true);
    setFade(null);
    setSaved(false);
    try {
      if (!leg || legMid <= 0) throw new Error("no quote on the selected strike; pick another strike");
      const o = parseFloat(spotNow);
      if (isNaN(o)) throw new Error("enter the open price");
      const sellS = toSpot(sell, o),
        coverS = toSpot(cover, o),
        stopS = toSpot(stop, o);
      if (sellS == null || coverS == null) throw new Error("enter sell and cover levels");
      const r = await apiFetch("/api/fade", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          ...base(),
          spot_now: o,
          sell_spot: sellS,
          cover_spot: coverS,
          stop_spot: stopS,
          hours_held: parseFloat(hoursHeld) || 0,
          contracts: parseInt(contracts, 10) || 1
        })
      });
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || "fade failed");
      d._sellS = sellS;
      d._coverS = coverS;
      d._stopS = stopS;
      setFade(d);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  const saveFade = async () => {
    try {
      const r = await apiFetch("/api/fade/save", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          ticker,
          kind,
          strike: Number(strike),
          days_to_exp: dte,
          sell_spot: fade._sellS,
          cover_spot: fade._coverS,
          stop_spot: fade._stopS,
          contracts: parseInt(contracts, 10) || 1,
          fade
        })
      });
      if (r.ok) setSaved(true);
    } catch (e) {/* non-fatal */}
  };
  const live = livePrice ?? currentPrice;
  let status = "Waiting",
    statusCls = "lr-wait";
  if (mode === "fade" && fade && fade._sellS != null && live) {
    const dist = Math.abs(live - fade._sellS) / fade._sellS;
    if (kind === "call" && live >= fade._sellS || kind === "put" && live <= fade._sellS) {
      status = "Tagged";
      statusCls = "lr-tagged";
    } else if (dist <= 0.005) {
      status = "Approaching";
      statusCls = "lr-approach";
    }
  }
  const noChain = !strikes.length;
  return /*#__PURE__*/React.createElement("div", {
    className: "card level-reprice-card",
    style: {
      marginBottom: "var(--row-gap)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker",
    title: "Pick a strike from the live chain, the quote and expiration come with it. Gap shows what the contract should be worth at the open or at a target price you set. Level fade stages a sell at a high and a cover at a settle. IV is backed out of the live mid so every number is self-consistent."
  }, "Where the premium goes · ", kind === "call" ? "call" : "put"), /*#__PURE__*/React.createElement("div", {
    className: "card-title",
    title: "Reprice this contract at a target stock level using Black Scholes, not the delta shortcut."
  }, "Level Reprice")), mode === "fade" && fade && /*#__PURE__*/React.createElement("div", {
    className: `lr-status ${statusCls}`,
    title: "Live trigger. Waiting until the underlying nears your sell level, Approaching within 0.50 percent, Tagged once reached."
  }, status)), /*#__PURE__*/React.createElement("div", {
    className: "lr-modebar"
  }, /*#__PURE__*/React.createElement("button", {
    className: mode === "gap" ? "active" : "",
    onClick: () => setMode("gap"),
    title: "What is the contract worth at the open or at a target price."
  }, "Gap"), /*#__PURE__*/React.createElement("button", {
    className: mode === "fade" ? "active" : "",
    onClick: () => setMode("fade"),
    title: "Stage an intraday fade, sell at a high and cover at a settle."
  }, "Level fade")), noChain ? /*#__PURE__*/React.createElement("div", {
    className: "lr-err",
    title: "The option chain has not loaded for this ticker yet."
  }, "Chain not loaded yet for ", ticker, ". Give it a moment, then try again.") : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "lr-stage"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Call or put. Defaults from the strategy toggle."
  }, "Kind"), /*#__PURE__*/React.createElement("div", {
    className: "lr-seg"
  }, /*#__PURE__*/React.createElement("button", {
    className: kind === "call" ? "active" : "",
    onClick: () => setKind("call"),
    title: "Price a call."
  }, "Call"), /*#__PURE__*/React.createElement("button", {
    className: kind === "put" ? "active" : "",
    onClick: () => setKind("put"),
    title: "Price a put."
  }, "Put"))), /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Strike from the live option chain, pre-set to the expected-move level. Selecting one pulls its quote automatically."
  }, "Strike"), /*#__PURE__*/React.createElement("select", {
    value: strike ?? "",
    onChange: e => onStrikePick(e.target.value),
    title: "Valid strikes from the current chain. Defaults to the expected-move level; a manual pick is held for 20 seconds before the default reapplies."
  }, strikes.map(s => /*#__PURE__*/React.createElement("option", {
    key: s,
    value: s
  }, s.toFixed(2)))), expMoveStrike != null && /*#__PURE__*/React.createElement("div", {
    className: "lr-hint",
    title: `Expected move ${kind === "put" ? "down" : "up"} from the ATM straddle, snapped to the nearest listed strike.`
  }, "Exp move ", kind === "put" ? "↓" : "↑", " $", expMoveStrike.toFixed(2))), /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Expiration this week. For names with Monday, Wednesday, Friday, or 0DTE options, pick which one. Single-expiry weeks show the front weekly."
  }, "Expiry"), multiExp ? /*#__PURE__*/React.createElement("select", {
    value: selExp || "",
    onChange: e => setExpDate(e.target.value),
    title: "Pick which expiration this week to price against. Days to expiration and the chain update with it."
  }, expList.map(e => {
    const lbl = new Date(e.date + "T00:00:00").toLocaleDateString("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric"
    });
    return /*#__PURE__*/React.createElement("option", {
      key: e.date,
      value: e.date
    }, lbl, " · ", e.dte, "d", e.dte === 0 ? " (0DTE)" : "");
  })) : /*#__PURE__*/React.createElement("div", {
    className: "lr-readout",
    title: `Expiring ${expLabel}, ${dte} days out.`
  }, expLabel, " · ", dte, "d")), /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Live option mid for the selected strike, pulled from the chain. Read-only."
  }, "Live quote"), /*#__PURE__*/React.createElement("div", {
    className: "lr-readout num",
    title: "Current mid of the selected contract from the live chain."
  }, legMid > 0 ? "$" + fmt(legMid) : "no quote")), /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Risk free rate, annualized percent."
  }, "Rate %"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "0.01",
    value: rate,
    onChange: e => setRate(e.target.value),
    title: "Annualized risk free rate in percent."
  }))), mode === "gap" ? /*#__PURE__*/React.createElement("div", {
    className: "lr-levels"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lr-stage"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "The stock price the live quote reflects, usually the prior close. Auto-filled, editable."
  }, "Stock now"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "0.01",
    value: spotNow,
    onChange: e => setSpotNow(e.target.value),
    title: "Spot the current quote reflects."
  })), /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Pre-filled with the Monday plus Tuesday average high target from your weekly history, applied to the current price. This is usually where you sell. Editable for any other premarket open or target."
  }, "If stock reaches"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "0.01",
    value: target,
    placeholder: "target price",
    onChange: e => onTargetChange(e.target.value),
    title: "Target stock price to reprice the contract at. Pre-filled from the Mon plus Tue high; a manual edit is held for 20 seconds before the default reapplies."
  }), monTueTarget != null && /*#__PURE__*/React.createElement("div", {
    className: "lr-hint",
    title: "Average Monday high percent plus average Tuesday high percent, applied to the current price."
  }, "Mon+Tue high $", monTueTarget.toFixed(2))), /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Hours of decay between now and the move. Overnight to the open is about 17, an intraday target is 1 to 3."
  }, "Hours to move"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "0.5",
    value: gapHours,
    onChange: e => setGapHours(e.target.value),
    title: "Hours of time decay before the move."
  }))), /*#__PURE__*/React.createElement("button", {
    className: "lr-run",
    onClick: runGap,
    disabled: busy,
    title: "Back out IV from the live mid and reprice the contract at the target."
  }, busy ? "Working…" : "Reprice at target")) : /*#__PURE__*/React.createElement("div", {
    className: "lr-levels"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lr-levels-head"
  }, /*#__PURE__*/React.createElement("span", {
    title: "Enter each level as a price, or as a percent move from the open."
  }, "Levels"), /*#__PURE__*/React.createElement("div", {
    className: "lr-seg lr-seg-sm"
  }, /*#__PURE__*/React.createElement("button", {
    className: !pctMode ? "active" : "",
    onClick: () => setPctMode(false),
    title: "Enter levels as absolute prices."
  }, "Price"), /*#__PURE__*/React.createElement("button", {
    className: pctMode ? "active" : "",
    onClick: () => setPctMode(true),
    title: "Enter levels as percent move from the open."
  }, "% from open"))), /*#__PURE__*/React.createElement("div", {
    className: "lr-stage"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "The day's open price, the reference for percent levels and the spot the quote reflects."
  }, "Open"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "0.01",
    value: spotNow,
    onChange: e => setSpotNow(e.target.value),
    title: "Opening stock price."
  })), /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Where you sell to open, typically near the expected high."
  }, "Sell ", pctMode ? "%" : "$"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "0.01",
    value: sell,
    onChange: e => setSell(e.target.value),
    title: "Stock level where you sell to open."
  })), /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Where you buy to close, typically near the expected settle."
  }, "Cover ", pctMode ? "%" : "$"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "0.01",
    value: cover,
    onChange: e => setCover(e.target.value),
    title: "Stock level where you buy to close."
  })), /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "The adverse level that defines max risk. Optional."
  }, "Stop ", pctMode ? "%" : "$"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "0.01",
    value: stop,
    onChange: e => setStop(e.target.value),
    title: "Adverse level used to price max risk."
  })), /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Hours between the sell and the cover."
  }, "Hours held"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "0.5",
    value: hoursHeld,
    onChange: e => setHoursHeld(e.target.value),
    title: "Hours between selling and covering."
  })), /*#__PURE__*/React.createElement("div", {
    className: "lr-field"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Number of contracts, scales the totals."
  }, "Contracts"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    value: contracts,
    onChange: e => setContracts(e.target.value),
    title: "Contract count for totals."
  }))), /*#__PURE__*/React.createElement("button", {
    className: "lr-run",
    onClick: runFade,
    disabled: busy,
    title: "Back out IV from the live mid and price the sell, cover, and stop."
  }, busy ? "Working…" : "Price the fade"))), err && /*#__PURE__*/React.createElement("div", {
    className: "lr-err",
    title: "The pricer could not produce a result, often when the quote is at or below intrinsic."
  }, err), mode === "gap" && out && /*#__PURE__*/React.createElement("div", {
    className: "lr-results"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lr-iv",
    title: "Implied vol backed out of the live mid at the stock-now price, used for every projection below."
  }, "Backed out IV ", out.iv != null ? (out.iv * 100).toFixed(2) + "%" : "—", " · start delta ", fmt(out.startDelta, 3), " · live mid $", fmt(out.q)), /*#__PURE__*/React.createElement("div", {
    className: "lr-compare"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lr-cmp lr-cmp-old",
    title: "Your delta shortcut: quote plus starting delta times the move. It ignores gamma, so it undershoots on big moves."
  }, /*#__PURE__*/React.createElement("span", null, "Delta shortcut"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, "$", fmt(out.deltaEst))), /*#__PURE__*/React.createElement("div", {
    className: "lr-cmp-arrow",
    "aria-hidden": "true"
  }, "→"), /*#__PURE__*/React.createElement("div", {
    className: "lr-cmp lr-cmp-true",
    title: "Full Black Scholes reprice at the target. Captures delta, gamma, and time decay exactly. This is where to set your sell."
  }, /*#__PURE__*/React.createElement("span", null, "Contract at ", fmt(out.tgt)), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, "$", fmt(out.flat)))), /*#__PURE__*/React.createElement("table", {
    className: "lr-table lr-sweep"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "IV assumption at the target, in vol points off the backed out IV."
  }, "IV scenario"), /*#__PURE__*/React.createElement("th", {
    title: "Repriced contract value at the target under this IV assumption."
  }, "Contract at ", fmt(out.tgt)))), /*#__PURE__*/React.createElement("tbody", null, out.sweep.map((s, i) => /*#__PURE__*/React.createElement("tr", {
    key: i,
    className: s.lbl === "IV flat" ? "lr-row-hot" : ""
  }, /*#__PURE__*/React.createElement("td", null, s.lbl), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, "$", fmt(s.v))))))), mode === "fade" && fade && /*#__PURE__*/React.createElement("div", {
    className: "lr-fade"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lr-iv",
    title: "Implied vol backed out of the live mid, used for the sell, cover, and stop."
  }, "Backed out IV ", fade.implied_vol_now != null ? (fade.implied_vol_now * 100).toFixed(2) + "%" : "—"), /*#__PURE__*/React.createElement("div", {
    className: "lr-fade-cap",
    title: "Net premium captured per contract, sell price minus cover price times 100."
  }, /*#__PURE__*/React.createElement("span", {
    className: "lr-fade-cap-num"
  }, "$", fmt(fade.capture_per_contract)), /*#__PURE__*/React.createElement("span", {
    className: "lr-fade-cap-lbl"
  }, "capture per contract", fade.capture_total != null ? " · $" + fmt(fade.capture_total) + " total" : "")), /*#__PURE__*/React.createElement("div", {
    className: "lr-fade-grid"
  }, /*#__PURE__*/React.createElement("div", {
    title: "Model option price at the sell level. You sell to open here."
  }, /*#__PURE__*/React.createElement("span", null, "Sell at"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, "$", fmt(fade.sell_price))), /*#__PURE__*/React.createElement("div", {
    title: "Model option price at the cover level. You buy to close here."
  }, /*#__PURE__*/React.createElement("span", null, "Cover at"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, "$", fmt(fade.cover_price))), /*#__PURE__*/React.createElement("div", {
    title: "Cost to cover at the stop minus the sell price, per contract. Defined max risk."
  }, /*#__PURE__*/React.createElement("span", null, "Max risk"), /*#__PURE__*/React.createElement("b", {
    className: "num down"
  }, fade.max_risk_per_contract != null ? "$" + fmt(fade.max_risk_per_contract) : "—")), /*#__PURE__*/React.createElement("div", {
    title: "Capture divided by max risk."
  }, /*#__PURE__*/React.createElement("span", null, "Risk reward"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, fade.risk_reward != null ? fade.risk_reward.toFixed(2) : "—"))), status === "Tagged" && fade.live_quote && fade.live_quote.mid && /*#__PURE__*/React.createElement("div", {
    className: "lr-trigger",
    title: "The underlying reached your sell level. Suggested limit is the live mid; the cover target is locked to the model cover price."
  }, "Tagged. Suggested sell limit $", fmt(fade.live_quote.mid), " · lock cover target $", fmt(fade.cover_price)), fade.iv_sweep && fade.iv_sweep.length > 0 && /*#__PURE__*/React.createElement("table", {
    className: "lr-table lr-sweep"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "IV change in vol points applied at the sell."
  }, "IV shift"), /*#__PURE__*/React.createElement("th", {
    title: "Net capture per contract at this IV shift."
  }, "Capture / contract"), /*#__PURE__*/React.createElement("th", {
    title: "Net capture across all contracts at this IV shift."
  }, "Capture total"))), /*#__PURE__*/React.createElement("tbody", null, fade.iv_sweep.map((s, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, s.iv_shift >= 0 ? "+" : "", s.iv_shift.toFixed(2)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, "$", fmt(s.capture_per_contract)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, "$", fmt(s.capture_total)))))), /*#__PURE__*/React.createElement("button", {
    className: "lr-save",
    onClick: saveFade,
    title: "Save this staged fade to disk so it persists across reloads."
  }, saved ? "Saved" : "Save fade")));
}
function WinRateCard({
  apiFetch
}) {
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const fetchJournal = async () => {
    if (!apiFetch) return;
    setLoading(true);
    setError(null);
    try {
      const r = await apiFetch("/api/trade_journal");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setTrades(Array.isArray(j.trades) ? j.trades : []);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    fetchJournal();
    // Listen for position-closed events from PositionsCard so the
    // tracker refreshes immediately instead of waiting for the next
    // page load. Custom DOM event keeps the components decoupled.
    const handler = () => fetchJournal();
    window.addEventListener("jerry:position-closed", handler);
    return () => window.removeEventListener("jerry:position-closed", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Compute stats. All client-side so the math is auditable.
  const stats = React.useMemo(() => {
    if (!trades || trades.length === 0) return null;
    const opt = trades.filter(t => t.type === "call" || t.type === "put");
    if (opt.length === 0) return null;
    let wins = 0,
      losses = 0,
      breakeven = 0;
    let totalPnl = 0,
      totalPremiumCollected = 0;
    let best = null,
      worst = null;
    let deltaSum = 0,
      deltaCount = 0;
    for (const t of opt) {
      // Shared formula (v1.21): JournalUtil.tradePnl / premiumCollected
      // so the tiles, CSV export, and P/L chart never diverge.
      const pnl = JournalUtil.tradePnl(t);
      const premCollected = JournalUtil.premiumCollected(t);
      totalPnl += pnl;
      totalPremiumCollected += premCollected;
      if (pnl > 0.01) wins++;else if (pnl < -0.01) losses++;else breakeven++;
      if (best == null || pnl > best.pnl) best = {
        ...t,
        pnl
      };
      if (worst == null || pnl < worst.pnl) worst = {
        ...t,
        pnl
      };
      if (t.entry_delta != null) {
        deltaSum += Math.abs(t.entry_delta);
        deltaCount++;
      }
    }
    const total = wins + losses + breakeven;
    const winRate = total > 0 ? wins / total * 100 : 0;
    const avgDelta = deltaCount > 0 ? deltaSum / deltaCount : null;
    const avgPnl = total > 0 ? totalPnl / total : 0;
    return {
      wins,
      losses,
      breakeven,
      total,
      totalPnl,
      totalPremiumCollected,
      winRate,
      best,
      worst,
      avgDelta,
      avgPnl
    };
  }, [trades]);

  // Cumulative realized P/L over time for the chart (v1.21). Same
  // per-trade formula as the tiles, via JournalUtil, so they agree.
  const series = React.useMemo(() => JournalUtil.buildCumulativePnlSeries(trades), [trades]);

  // CSV export for tax prep (v1.21). Built client-side from the journal
  // already in memory, so there is no new backend endpoint. The export
  // includes every closed row, not just options. Downloads via a Blob.
  const exportCsv = () => {
    try {
      const csv = JournalUtil.buildJournalCsv(trades);
      const blob = new Blob([csv], {
        type: "text/csv;charset=utf-8"
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `trade_journal_${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      console.error("CSV export failed", e);
    }
  };
  if (!trades || trades.length === 0) {
    return /*#__PURE__*/React.createElement("div", {
      className: "card win-rate-card",
      style: {
        marginBottom: "var(--row-gap)"
      }
    }, /*#__PURE__*/React.createElement("div", {
      className: "card-head"
    }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
      className: "kicker",
      title: "Realized performance on closed positions. Populates as you close trades in the position tracker."
    }, "Realized performance"), /*#__PURE__*/React.createElement("div", {
      className: "card-title"
    }, "Win rate"))), /*#__PURE__*/React.createElement("div", {
      className: "muted",
      style: {
        padding: "12px 0",
        fontSize: 13
      }
    }, loading ? "Loading…" : error ? `Error: ${error}` : "No closed trades yet. The win rate populates as you close positions in the tracker below."));
  }
  const sgn = v => v >= 0 ? "+" : "";
  return /*#__PURE__*/React.createElement("div", {
    className: "card win-rate-card",
    style: {
      marginBottom: "var(--row-gap)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker",
    title: `Realized performance from ${stats.total} closed option trade${stats.total === 1 ? "" : "s"}. Stock positions excluded so the metric reflects premium-selling skill specifically.`
  }, "Closed trades · ", stats.total), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Win rate")), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "wr-export-btn",
    onClick: exportCsv,
    title: "Download the full closed-trade journal as a CSV for tax prep. One row per closed trade with realized P/L and premium collected computed per contract. Includes stock rows; P/L is filled for option rows only."
  }, "Export CSV")), /*#__PURE__*/React.createElement("div", {
    className: "wr-grid"
  }, /*#__PURE__*/React.createElement("div", {
    className: "wr-tile",
    title: "Percentage of closed option trades that finished profitable. A trade is a 'win' if realized P/L exceeded $0.01. Breakevens excluded from the win count but included in the total."
  }, /*#__PURE__*/React.createElement("div", {
    className: "wr-tile-lbl"
  }, "Win rate"), /*#__PURE__*/React.createElement("div", {
    className: `wr-tile-val ${stats.winRate >= 70 ? "up" : stats.winRate >= 50 ? "" : "down"}`
  }, stats.winRate.toFixed(1), "%"), /*#__PURE__*/React.createElement("div", {
    className: "wr-tile-sub"
  }, stats.wins, "W · ", stats.losses, "L", stats.breakeven > 0 ? ` · ${stats.breakeven}BE` : "")), /*#__PURE__*/React.createElement("div", {
    className: "wr-tile",
    title: "Total realized P/L across all closed option trades. Per-contract P/L = (entry premium − exit premium) × 100 × contracts for short positions."
  }, /*#__PURE__*/React.createElement("div", {
    className: "wr-tile-lbl"
  }, "Total P/L"), /*#__PURE__*/React.createElement("div", {
    className: `wr-tile-val ${stats.totalPnl >= 0 ? "up" : "down"}`
  }, sgn(stats.totalPnl), "$", stats.totalPnl.toFixed(0)), /*#__PURE__*/React.createElement("div", {
    className: "wr-tile-sub"
  }, "avg ", sgn(stats.avgPnl), "$", stats.avgPnl.toFixed(0), "/trade")), /*#__PURE__*/React.createElement("div", {
    className: "wr-tile",
    title: "Total premium collected on short-option entries. Independent of P/L since some of this gets returned at close. Useful for tracking gross income before assignment costs and roll debits."
  }, /*#__PURE__*/React.createElement("div", {
    className: "wr-tile-lbl"
  }, "Premium collected"), /*#__PURE__*/React.createElement("div", {
    className: "wr-tile-val"
  }, "$", stats.totalPremiumCollected.toFixed(0))), stats.avgDelta != null && /*#__PURE__*/React.createElement("div", {
    className: "wr-tile",
    title: "Average absolute delta at entry across closed trades. Drift away from the 0.20 target indicates strike picking is creeping more or less aggressive over time."
  }, /*#__PURE__*/React.createElement("div", {
    className: "wr-tile-lbl"
  }, "Avg entry Δ"), /*#__PURE__*/React.createElement("div", {
    className: `wr-tile-val ${Math.abs(stats.avgDelta - 0.20) <= 0.04 ? "up" : "warn"}`
  }, stats.avgDelta.toFixed(2)), /*#__PURE__*/React.createElement("div", {
    className: "wr-tile-sub"
  }, "target 0.20"))), /*#__PURE__*/React.createElement("div", {
    className: "wr-extremes"
  }, stats.best && stats.best.pnl > 0 && /*#__PURE__*/React.createElement("div", {
    className: "wr-extreme wr-extreme-best",
    title: `Best closed trade: ${stats.best.ticker} ${stats.best.type} ${stats.best.strike != null ? "$" + stats.best.strike : ""} ${stats.best.expiration || ""}, opened ${stats.best.opened_at}, closed ${stats.best.closed_at}.`
  }, /*#__PURE__*/React.createElement("span", {
    className: "wr-extreme-lbl"
  }, "Best"), /*#__PURE__*/React.createElement("span", {
    className: "wr-extreme-sym"
  }, stats.best.ticker), /*#__PURE__*/React.createElement("span", {
    className: "wr-extreme-val up"
  }, "+$", stats.best.pnl.toFixed(0))), stats.worst && stats.worst.pnl < 0 && /*#__PURE__*/React.createElement("div", {
    className: "wr-extreme wr-extreme-worst",
    title: `Worst closed trade: ${stats.worst.ticker} ${stats.worst.type} ${stats.worst.strike != null ? "$" + stats.worst.strike : ""} ${stats.worst.expiration || ""}, opened ${stats.worst.opened_at}, closed ${stats.worst.closed_at}.`
  }, /*#__PURE__*/React.createElement("span", {
    className: "wr-extreme-lbl"
  }, "Worst"), /*#__PURE__*/React.createElement("span", {
    className: "wr-extreme-sym"
  }, stats.worst.ticker), /*#__PURE__*/React.createElement("span", {
    className: "wr-extreme-val down"
  }, "$", stats.worst.pnl.toFixed(0)))), series.length >= 2 && (() => {
    const VW = 320,
      VH = 90,
      padX = 6,
      padY = 10;
    const cums = series.map(p => p.cum);
    const lo = Math.min(0, ...cums);
    const hi = Math.max(0, ...cums);
    const span = hi - lo || 1;
    const n = series.length;
    const px = i => padX + (n === 1 ? 0 : i * (VW - 2 * padX) / (n - 1));
    const py = v => padY + (hi - v) / span * (VH - 2 * padY);
    const pts = series.map((p, i) => [px(i), py(p.cum)]);
    const line = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const area = line + " L" + px(n - 1).toFixed(1) + " " + py(0).toFixed(1) + " L" + px(0).toFixed(1) + " " + py(0).toFixed(1) + " Z";
    const last = series[n - 1].cum;
    const up = last >= 0;
    const zeroY = py(0).toFixed(1);
    return /*#__PURE__*/React.createElement("div", {
      className: "wr-chart",
      title: "Cumulative realized P/L across closed option trades, ordered by close date. Each step is one closed trade. The dashed line is breakeven. Realized only; open positions are excluded."
    }, /*#__PURE__*/React.createElement("div", {
      className: "wr-chart-head"
    }, /*#__PURE__*/React.createElement("span", {
      className: "wr-chart-lbl"
    }, "Cumulative P/L · ", n, " trades"), /*#__PURE__*/React.createElement("span", {
      className: `wr-chart-now ${up ? "up" : "down"}`
    }, up ? "+" : "", "$", last.toFixed(0))), /*#__PURE__*/React.createElement("svg", {
      className: "wr-chart-svg",
      viewBox: `0 0 ${VW} ${VH}`,
      preserveAspectRatio: "none",
      role: "img",
      "aria-label": "Cumulative realized P/L over time"
    }, /*#__PURE__*/React.createElement("path", {
      d: area,
      className: `wr-area ${up ? "up" : "down"}`
    }), /*#__PURE__*/React.createElement("line", {
      x1: padX,
      x2: VW - padX,
      y1: zeroY,
      y2: zeroY,
      className: "wr-zero"
    }), /*#__PURE__*/React.createElement("path", {
      d: line,
      className: `wr-line ${up ? "up" : "down"}`
    }), /*#__PURE__*/React.createElement("circle", {
      cx: px(n - 1).toFixed(1),
      cy: py(last).toFixed(1),
      r: "3.5",
      className: `wr-dot ${up ? "up" : "down"}`
    })));
  })());
}
function EarningsCrushCard({
  apiFetch,
  onSwitchTicker
}) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const fetchCrush = async () => {
    if (!apiFetch) return;
    setLoading(true);
    setError(null);
    try {
      const r = await apiFetch("/api/earnings_iv_crush?horizon=14&events=6");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setRows(Array.isArray(j.rows) ? j.rows : []);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    fetchCrush();
    // Re-fetch every hour. Earnings dates do not change often.
    const id = setInterval(skipWhenHidden(fetchCrush), 60 * 60 * 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (rows.length === 0 && !loading && !error) return null;
  return /*#__PURE__*/React.createElement("div", {
    className: "card earnings-crush-card",
    style: {
      marginBottom: "var(--row-gap)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker",
    title: "Watchlist tickers with earnings inside 14 days, ranked by proximity. The crush figure is HEURISTIC: it uses pre vs post realized vol around past earnings as a proxy for implied vol crush since historical IV is paid data. Treat as directional not exact."
  }, "Watchlist · next 14 days · heuristic"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Earnings vol crush")), /*#__PURE__*/React.createElement("button", {
    className: "ec-refresh-btn",
    disabled: loading,
    onClick: fetchCrush,
    title: "Re-fetch earnings dates and recompute crush samples. Slower than the rest of the dashboard since it pulls daily history per ticker."
  }, loading ? "Loading…" : "Refresh")), error && /*#__PURE__*/React.createElement("div", {
    className: "ec-error"
  }, "Error: ", error), rows.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "ec-table"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ec-head"
  }, /*#__PURE__*/React.createElement("span", {
    title: "Ticker symbol. Click any row to load it on the dashboard."
  }, "Ticker"), /*#__PURE__*/React.createElement("span", {
    title: "Next earnings date."
  }, "Earnings"), /*#__PURE__*/React.createElement("span", {
    title: "Days until the next earnings event."
  }, "In"), /*#__PURE__*/React.createElement("span", {
    title: "Median post-earnings IV crush across past prints. Calculated as 1 minus the ratio of post-earnings 5-day realized vol over pre-earnings 5-day realized vol. Higher = more typical crush, which means short premium going into earnings tends to work but you are giving back vega the day after."
  }, "Median crush"), /*#__PURE__*/React.createElement("span", {
    title: "Average post-earnings crush across past prints. Compared to median this shows whether one outlier earnings move skewed the average."
  }, "Avg crush"), /*#__PURE__*/React.createElement("span", {
    title: "Number of past earnings events sampled for the crush calculation. More = more reliable."
  }, "Samples")), rows.map(r => /*#__PURE__*/React.createElement("div", {
    key: r.symbol,
    className: "ec-row",
    onClick: () => onSwitchTicker && onSwitchTicker(r.symbol),
    title: `Click to switch the dashboard to ${r.symbol}. Past samples: ${r.samples.map(s => s.toFixed(0) + "%").join(", ")}`
  }, /*#__PURE__*/React.createElement("span", {
    className: "ec-sym"
  }, r.symbol), /*#__PURE__*/React.createElement("span", null, fmtUSDate(r.next_earnings)), /*#__PURE__*/React.createElement("span", {
    className: r.days_to_earnings <= 3 ? "warn" : ""
  }, r.days_to_earnings, "d"), /*#__PURE__*/React.createElement("span", {
    className: r.median_crush_pct >= 30 ? "up" : r.median_crush_pct < 10 ? "warn" : ""
  }, r.median_crush_pct >= 0 ? "" : "+", r.median_crush_pct.toFixed(1), "%"), /*#__PURE__*/React.createElement("span", null, r.avg_crush_pct.toFixed(1), "%"), /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, r.sample_count)))));
}
function PushSettingsCard({
  apiFetch
}) {
  const [status, setStatus] = useState(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [collapsed, setCollapsed] = useState(true);
  useEffect(() => {
    if (!apiFetch) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await apiFetch("/api/push/status");
        if (!r.ok) return;
        const j = await r.json();
        if (!cancelled) setStatus(j);
      } catch {}
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const sendTest = async () => {
    if (!apiFetch) return;
    setTesting(true);
    setTestResult(null);
    try {
      const r = await apiFetch("/api/push/test", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          message: "Test push from your dashboard. If you see this, Pushover is wired correctly."
        })
      });
      const j = await r.json();
      setTestResult(j);
    } catch (e) {
      setTestResult({
        ok: false,
        error: String(e.message || e)
      });
    } finally {
      setTesting(false);
    }
  };
  if (!status) return null;
  const configured = status.configured;
  return /*#__PURE__*/React.createElement("div", {
    className: "card push-settings-card",
    style: {
      marginBottom: "var(--row-gap)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker",
    title: configured ? "Pushover env vars detected. Roll flag alerts will fire to your phone with 12-hour dedupe per position." : "Pushover env vars missing. Configure PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY via 'jerry env set' to enable phone alerts."
  }, "Phone alerts · Pushover · ", configured ? "configured" : "not configured"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Push notifications")), /*#__PURE__*/React.createElement("button", {
    className: "ps-collapse-btn",
    onClick: () => setCollapsed(v => !v),
    title: collapsed ? "Show setup details and test button." : "Hide setup details."
  }, collapsed ? "Details" : "Hide")), !collapsed && /*#__PURE__*/React.createElement("div", {
    className: "ps-body"
  }, configured ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "ps-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ps-row-lbl",
    title: "Pushover application token. Set via 'jerry env set PUSHOVER_APP_TOKEN xxx'."
  }, "App token"), /*#__PURE__*/React.createElement("span", {
    className: "ps-row-val ps-ok"
  }, "set")), /*#__PURE__*/React.createElement("div", {
    className: "ps-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ps-row-lbl",
    title: "Pushover user key. Set via 'jerry env set PUSHOVER_USER_KEY xxx'."
  }, "User key"), /*#__PURE__*/React.createElement("span", {
    className: "ps-row-val ps-ok"
  }, "set")), /*#__PURE__*/React.createElement("div", {
    className: "ps-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "ps-test-btn",
    disabled: testing,
    onClick: sendTest,
    title: "Send a test push to your phone right now to confirm Pushover is wired correctly."
  }, testing ? "Sending…" : "Send test push"), testResult && /*#__PURE__*/React.createElement("span", {
    className: `ps-test-result ${testResult.ok ? "ps-ok" : "ps-err"}`,
    title: testResult.ok ? "Pushover accepted the request. Check your phone." : `Pushover rejected: ${testResult.error || testResult.response}`
  }, testResult.ok ? "✓ sent · check phone" : `✕ ${testResult.error || "failed"}`)), /*#__PURE__*/React.createElement("div", {
    className: "ps-help"
  }, "Roll flag alerts fire when an open short option position has DTE ≤ 7 and |delta| ≥ 0.40. Dedupe window is 12 hours per position so you get reminded once per day, not every poll.")) : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "ps-help"
  }, "To enable phone alerts on roll-flag triggers, install the Pushover app, then set the two env vars from terminal."), /*#__PURE__*/React.createElement("pre", {
    className: "ps-code"
  }, `jerry env set PUSHOVER_APP_TOKEN <token-from-pushover-dashboard>
jerry env set PUSHOVER_USER_KEY <user-key-from-pushover-account>
jerry restart`), /*#__PURE__*/React.createElement("div", {
    className: "ps-help"
  }, "Pushover app is a one-time $5 purchase. Once configured, this card flips to show a \"Send test push\" button."))));
}
function BrokerImportCard({
  apiFetch,
  positions,
  setPositions
}) {
  const [accountsState, setAccountsState] = useState(null);
  const [selectedHash, setSelectedHash] = useState(null);
  const [brokerPositions, setBrokerPositions] = useState([]);
  const [loadingAccounts, setLoadingAccounts] = useState(false);
  const [loadingPositions, setLoadingPositions] = useState(false);
  const [error, setError] = useState(null);
  const [collapsed, setCollapsed] = useState(true);
  const [lastFetched, setLastFetched] = useState(null);
  const fetchAccounts = async () => {
    if (!apiFetch) return;
    setLoadingAccounts(true);
    setError(null);
    try {
      const r = await apiFetch("/api/broker/accounts");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setAccountsState(j);
      // Auto-select the first account when only one exists.
      if (j.accounts && j.accounts.length === 1) {
        setSelectedHash(j.accounts[0].hash);
      }
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoadingAccounts(false);
    }
  };
  const fetchPositions = async hash => {
    if (!apiFetch || !hash) return;
    setLoadingPositions(true);
    setError(null);
    try {
      const r = await apiFetch(`/api/broker/positions?account_hash=${encodeURIComponent(hash)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      setBrokerPositions(Array.isArray(j.positions) ? j.positions : []);
      setLastFetched(new Date());
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoadingPositions(false);
    }
  };
  useEffect(() => {
    fetchAccounts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (selectedHash) fetchPositions(selectedHash);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedHash]);

  // Match a broker position to an existing local one. Composite key
  // covers ticker + type + strike + expiration so a long stock at
  // 100 shares does not match a covered call at the same ticker.
  const isAlreadyTracked = bp => {
    return positions.some(p => {
      if ((p.ticker || "").toUpperCase() !== (bp.ticker || "").toUpperCase()) return false;
      if (p.type !== bp.type) return false;
      if (p.type === "stock") return true;
      if (Math.abs((p.strike || 0) - (bp.strike || 0)) > 0.01) return false;
      if ((p.expiration || "") !== (bp.expiration || "")) return false;
      return true;
    });
  };
  const importPosition = bp => {
    const id = "schwab_" + (bp.schwab_id || Date.now().toString(36));
    const local = {
      id,
      ticker: bp.ticker,
      type: bp.type,
      strike: bp.strike,
      expiration: bp.expiration,
      qty: bp.qty,
      contracts: bp.contracts,
      entryPrice: bp.entryPrice,
      entryPremium: bp.entryPrice,
      openedAt: new Date().toISOString(),
      entryDate: new Date().toISOString().slice(0, 10),
      closed: false,
      status: "open",
      source: "schwab",
      schwab_id: bp.schwab_id,
      notes: "Imported from Schwab"
    };
    setPositions(prev => {
      // Defensive: skip if already in the list (race condition on
      // double-click).
      if (prev.some(p => p.id === id)) return prev;
      return [local, ...prev];
    });
  };
  const importAll = () => {
    const toImport = brokerPositions.filter(bp => !isAlreadyTracked(bp));
    if (toImport.length === 0) return;
    if (!confirm(`Import ${toImport.length} position${toImport.length === 1 ? "" : "s"} from Schwab into the tracker?`)) return;
    for (const bp of toImport) {
      importPosition(bp);
    }
  };
  const configured = accountsState?.configured;
  const accounts = accountsState?.accounts || [];
  return /*#__PURE__*/React.createElement("div", {
    className: "card broker-import-card",
    style: {
      marginBottom: "var(--row-gap)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker",
    title: "Phase 1 of broker import: read-only fetch from Schwab. You review and click Add on positions you want tracked. Phase 2 will add auto-reconciliation on fills and rolls."
  }, "Schwab · phase 1 · manual import"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Broker import")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 8,
      alignItems: "center"
    }
  }, lastFetched && /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 11
    },
    title: "Time of last fetch from Schwab."
  }, "Updated ", lastFetched.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit"
  })), /*#__PURE__*/React.createElement("button", {
    className: "bi-collapse-btn",
    onClick: () => setCollapsed(v => !v),
    title: collapsed ? "Expand the broker import panel." : "Collapse the panel."
  }, collapsed ? "Details" : "Hide"))), !collapsed && /*#__PURE__*/React.createElement("div", {
    className: "bi-body"
  }, accountsState === null && /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12,
      padding: "8px 0"
    }
  }, loadingAccounts ? "Loading accounts…" : "Initializing…"), accountsState && !configured && /*#__PURE__*/React.createElement("div", {
    className: "bi-help"
  }, "Schwab is not configured. Run ", /*#__PURE__*/React.createElement("code", null, "jerry auth"), " from terminal to authenticate, then click Refresh below.", /*#__PURE__*/React.createElement("button", {
    className: "bi-refresh-btn",
    style: {
      marginTop: 8
    },
    onClick: fetchAccounts,
    title: "Re-check Schwab configuration."
  }, "Refresh status")), accountsState && configured && accounts.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12,
      padding: "8px 0"
    }
  }, "No accounts returned by Schwab. Verify your OAuth scope includes account read."), accountsState && configured && accounts.length > 0 && /*#__PURE__*/React.createElement(React.Fragment, null, accounts.length > 1 && /*#__PURE__*/React.createElement("div", {
    className: "bi-account-picker"
  }, /*#__PURE__*/React.createElement("span", {
    className: "bi-row-lbl",
    title: "Schwab returns one or more linked accounts. Select which one to import positions from."
  }, "Account"), /*#__PURE__*/React.createElement("select", {
    className: "bi-account-select",
    value: selectedHash || "",
    onChange: e => setSelectedHash(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "Select account…"), accounts.map(a => /*#__PURE__*/React.createElement("option", {
    key: a.hash,
    value: a.hash
  }, "Account ending ", a.masked)))), accounts.length === 1 && /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 11,
      marginBottom: 8
    }
  }, "Account ending ", accounts[0].masked, " (auto-selected, only one linked)"), /*#__PURE__*/React.createElement("div", {
    className: "bi-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "bi-refresh-btn",
    disabled: loadingPositions || !selectedHash,
    onClick: () => fetchPositions(selectedHash),
    title: "Re-fetch positions from Schwab. Cached server-side for 60 seconds so back-to-back clicks return the same data."
  }, loadingPositions ? "Loading…" : "Refresh from broker"), brokerPositions.length > 0 && /*#__PURE__*/React.createElement("button", {
    className: "bi-import-all-btn",
    onClick: importAll,
    title: "Add all broker positions to the local tracker that are not already in it. Existing positions are skipped (no duplicates)."
  }, "Import all new (", brokerPositions.filter(bp => !isAlreadyTracked(bp)).length, ")")), error && /*#__PURE__*/React.createElement("div", {
    className: "bi-error"
  }, "Error: ", error), brokerPositions.length === 0 && !loadingPositions && lastFetched && /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12,
      padding: "8px 0"
    }
  }, "Schwab returned 0 positions for this account. If you have open positions, this may indicate the position is in a non-equity, non-option asset class that the dashboard does not yet handle."), brokerPositions.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "bi-table"
  }, /*#__PURE__*/React.createElement("div", {
    className: "bi-head"
  }, /*#__PURE__*/React.createElement("span", {
    title: "Underlying ticker symbol."
  }, "Ticker"), /*#__PURE__*/React.createElement("span", {
    title: "Position type. stock = shares, call/put = single-leg option."
  }, "Type"), /*#__PURE__*/React.createElement("span", {
    title: "Strike for options. Empty for stock."
  }, "Strike"), /*#__PURE__*/React.createElement("span", {
    title: "Expiration for options. Empty for stock."
  }, "Exp"), /*#__PURE__*/React.createElement("span", {
    title: "Quantity. Negative = short."
  }, "Qty"), /*#__PURE__*/React.createElement("span", {
    title: "Average entry price per share."
  }, "Avg cost"), /*#__PURE__*/React.createElement("span", {
    title: "Status vs local tracker."
  }, "Status")), brokerPositions.map((bp, i) => {
    const tracked = isAlreadyTracked(bp);
    return /*#__PURE__*/React.createElement("div", {
      key: `${bp.ticker}-${bp.type}-${bp.strike || "x"}-${bp.expiration || "x"}-${i}`,
      className: `bi-row ${tracked ? "bi-row-tracked" : ""}`,
      title: tracked ? "This position is already in the local tracker. Skipped on import all." : "Click Add to import this position into the local tracker."
    }, /*#__PURE__*/React.createElement("span", {
      className: "bi-sym"
    }, bp.ticker), /*#__PURE__*/React.createElement("span", null, bp.type), /*#__PURE__*/React.createElement("span", null, bp.strike != null ? "$" + bp.strike.toFixed(2) : "—"), /*#__PURE__*/React.createElement("span", null, bp.expiration || "—"), /*#__PURE__*/React.createElement("span", {
      className: bp.qty < 0 ? "down" : "up"
    }, bp.qty), /*#__PURE__*/React.createElement("span", null, "$", (bp.entryPrice || 0).toFixed(2)), /*#__PURE__*/React.createElement("span", null, tracked ? /*#__PURE__*/React.createElement("span", {
      className: "bi-status-tracked"
    }, "tracked") : /*#__PURE__*/React.createElement("button", {
      className: "bi-add-btn",
      onClick: () => importPosition(bp),
      title: "Add this position to the local tracker."
    }, "Add")));
  })), /*#__PURE__*/React.createElement("div", {
    className: "bi-help"
  }, "Phase 1 is read-only manual import. Imported positions show ", /*#__PURE__*/React.createElement("code", null, "source: schwab"), " in their notes. Phase 2 will add auto-reconciliation on fills and rolls."))));
}
function StrategyReferenceCard() {
  const strategies = window.OptionStrats?.STRATEGIES || [];
  const docs = window.OptionStrats?.STRATEGY_DOCS || {};
  const [query, setQuery] = useState("");
  const [openKey, setOpenKey] = useState(null);
  const [filter, setFilter] = useState("all"); // all | income | speculation | volatility | synthetic | system

  // Only show strategies that have docs (sanity check)
  const items = strategies.filter(s => docs[s.key]);
  const familyOf = key => {
    const f = docs[key]?.family || "";
    if (/^income/i.test(f)) return "income";
    if (/^speculation/i.test(f)) return "speculation";
    if (/^volatility/i.test(f)) return "volatility";
    if (/^synthetic/i.test(f)) return "synthetic";
    if (/^system/i.test(f)) return "system";
    return "other";
  };
  const filtered = items.filter(s => {
    const d = docs[s.key];
    const q = query.trim().toLowerCase();
    if (q) {
      const hay = `${s.name} ${s.tag || ""} ${d.family || ""} ${d.summary || ""} ${d.market_view || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (filter !== "all" && familyOf(s.key) !== filter) return false;
    return true;
  });
  const families = [["all", "All"], ["income", "Income"], ["speculation", "Direction"], ["volatility", "Volatility"], ["synthetic", "Synthetic"], ["system", "Systems"]];
  return /*#__PURE__*/React.createElement("div", {
    className: "sref-modal-body"
  }, /*#__PURE__*/React.createElement("div", {
    className: "sref-toolbar"
  }, /*#__PURE__*/React.createElement("input", {
    className: "sref-search",
    type: "text",
    placeholder: "Search by name, view, family.",
    value: query,
    onChange: e => setQuery(e.target.value)
  }), /*#__PURE__*/React.createElement("div", {
    className: "sref-filter"
  }, families.map(([k, l]) => /*#__PURE__*/React.createElement("button", {
    key: k,
    className: filter === k ? "active" : "",
    onClick: () => setFilter(k)
  }, l)))), filtered.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "sref-empty"
  }, "No strategies match."), /*#__PURE__*/React.createElement("div", {
    className: "sref-grid"
  }, filtered.map(s => {
    const d = docs[s.key];
    const isOpen = openKey === s.key;
    return /*#__PURE__*/React.createElement("div", {
      key: s.key,
      className: `sref-tile ${isOpen ? "open" : ""}`,
      onClick: () => setOpenKey(prev => prev === s.key ? null : s.key)
    }, /*#__PURE__*/React.createElement("div", {
      className: "sref-tile-head"
    }, /*#__PURE__*/React.createElement("div", {
      className: "sref-tile-name"
    }, s.name), /*#__PURE__*/React.createElement("div", {
      className: "sref-tile-fam"
    }, d.family)), /*#__PURE__*/React.createElement("div", {
      className: "sref-tile-summary"
    }, d.summary), isOpen && /*#__PURE__*/React.createElement("div", {
      className: "sref-tile-detail",
      onClick: e => e.stopPropagation()
    }, /*#__PURE__*/React.createElement("div", {
      className: "sref-row"
    }, /*#__PURE__*/React.createElement("span", {
      className: "sref-lbl"
    }, "Market view"), /*#__PURE__*/React.createElement("span", {
      className: "sref-val"
    }, d.market_view)), /*#__PURE__*/React.createElement("div", {
      className: "sref-row"
    }, /*#__PURE__*/React.createElement("span", {
      className: "sref-lbl"
    }, "When to use"), /*#__PURE__*/React.createElement("span", {
      className: "sref-val"
    }, d.when_to_use)), /*#__PURE__*/React.createElement("div", {
      className: "sref-row"
    }, /*#__PURE__*/React.createElement("span", {
      className: "sref-lbl"
    }, "Max profit"), /*#__PURE__*/React.createElement("span", {
      className: "sref-val"
    }, d.max_profit)), /*#__PURE__*/React.createElement("div", {
      className: "sref-row"
    }, /*#__PURE__*/React.createElement("span", {
      className: "sref-lbl"
    }, "Max loss"), /*#__PURE__*/React.createElement("span", {
      className: "sref-val"
    }, d.max_loss)), /*#__PURE__*/React.createElement("div", {
      className: "sref-row"
    }, /*#__PURE__*/React.createElement("span", {
      className: "sref-lbl"
    }, "Break-even"), /*#__PURE__*/React.createElement("span", {
      className: "sref-val"
    }, d.breakeven)), /*#__PURE__*/React.createElement("div", {
      className: "sref-row"
    }, /*#__PURE__*/React.createElement("span", {
      className: "sref-lbl"
    }, "Ideal IV"), /*#__PURE__*/React.createElement("span", {
      className: "sref-val"
    }, d.ideal_iv)), /*#__PURE__*/React.createElement("div", {
      className: "sref-row"
    }, /*#__PURE__*/React.createElement("span", {
      className: "sref-lbl"
    }, "Time decay"), /*#__PURE__*/React.createElement("span", {
      className: "sref-val"
    }, d.time_decay)), /*#__PURE__*/React.createElement("div", {
      className: "sref-row"
    }, /*#__PURE__*/React.createElement("span", {
      className: "sref-lbl"
    }, "Assignment"), /*#__PURE__*/React.createElement("span", {
      className: "sref-val"
    }, d.assignment)), /*#__PURE__*/React.createElement("div", {
      className: "sref-row sref-row-risk"
    }, /*#__PURE__*/React.createElement("span", {
      className: "sref-lbl"
    }, "Risks"), /*#__PURE__*/React.createElement("span", {
      className: "sref-val"
    }, d.risks))), !isOpen && /*#__PURE__*/React.createElement("div", {
      className: "sref-tile-foot"
    }, "Tap to read full breakdown"));
  })));
}

// ── CSV helpers for the Manage Stocks importer ─────────────────────
// Required columns (case-insensitive, any order): Symbol, Tag, Industry,
// Sector, Weekly. Symbol is mandatory per row; Tag/Industry/Sector may be
// blank; Weekly must be Yes/No (anything else is flagged before import).
const WLM_CSV_COLUMNS = ["Symbol", "Tag", "Industry", "Sector", "Weekly"];

// Minimal RFC-4180-ish CSV parser: handles quoted fields, escaped quotes
// (""), and commas/newlines inside quotes. Returns an array of string rows.
function parseCsv(text) {
  const rows = [];
  let row = [],
    field = "",
    inQuotes = false;
  const s = (text || "").replace(/^﻿/, ""); // strip BOM
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (inQuotes) {
      if (c === '"') {
        if (s[i + 1] === '"') {
          field += '"';
          i++;
        } else inQuotes = false;
      } else field += c;
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && s[i + 1] === "\n") i++;
      row.push(field);
      field = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else field += c;
  }
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

// Normalize a Weekly cell to true / false / null(unknown) + a flag for
// "present but not Yes/No" so we can warn before importing.
function normalizeWeekly(raw) {
  const v = (raw || "").trim().toLowerCase();
  if (v === "") return {
    weekly: null,
    bad: false
  };
  if (["yes", "y", "true", "1"].includes(v)) return {
    weekly: true,
    bad: false
  };
  if (["no", "n", "false", "0"].includes(v)) return {
    weekly: false,
    bad: false
  };
  return {
    weekly: null,
    bad: true
  };
}
function csvEscape(v) {
  const s = String(v == null ? "" : v);
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

// Build the export CSV (same column format) from the current watchlist.
function watchlistToCsv(symbols) {
  const lines = [WLM_CSV_COLUMNS.join(",")];
  for (const s of symbols) {
    lines.push([csvEscape(s.symbol), csvEscape(s.tag || ""), csvEscape(s.industry || ""), csvEscape(s.sector || ""), csvEscape(s.weekly === true ? "Yes" : s.weekly === false ? "No" : "")].join(","));
  }
  return lines.join("\r\n");
}
function CsvImportPanel({
  data,
  onImportCsv,
  onClose
}) {
  const [stage, setStage] = useState("pick"); // pick | preview
  const [fileName, setFileName] = useState("");
  const [parsed, setParsed] = useState(null); // {rows, missing, badWeekly, dupes, mode}
  const [mode, setMode] = useState("update"); // update | replace
  const [error, setError] = useState("");
  const fileRef = React.useRef(null);
  const handleFile = file => {
    if (!file) return;
    setError("");
    setFileName(file.name);
    const reader = new FileReader();
    reader.onload = e => {
      try {
        const grid = parseCsv(String(e.target.result || ""));
        if (!grid.length) {
          setError("The file appears to be empty.");
          return;
        }
        const header = grid[0].map(h => h.trim().toLowerCase());
        const idx = {};
        for (const col of WLM_CSV_COLUMNS) idx[col] = header.indexOf(col.toLowerCase());
        const missing = WLM_CSV_COLUMNS.filter(c => idx[c] === -1);
        if (missing.length) {
          setError("Missing required column" + (missing.length > 1 ? "s" : "") + ": " + missing.join(", ") + ". Found: " + grid[0].map(h => h.trim()).filter(Boolean).join(", "));
          return;
        }
        const seen = new Set();
        const rows = [],
          dupes = [];
        let badWeekly = 0,
          skippedNoSymbol = 0;
        for (let r = 1; r < grid.length; r++) {
          const cells = grid[r];
          const symbol = (cells[idx.Symbol] || "").trim().toUpperCase();
          if (!symbol) {
            skippedNoSymbol++;
            continue;
          }
          const wk = normalizeWeekly(cells[idx.Weekly]);
          if (wk.bad) badWeekly++;
          if (seen.has(symbol)) {
            dupes.push(symbol);
            continue;
          }
          seen.add(symbol);
          rows.push({
            symbol,
            tag: (cells[idx.Tag] || "").trim(),
            industry: (cells[idx.Industry] || "").trim(),
            sector: (cells[idx.Sector] || "").trim(),
            weekly: wk.weekly,
            weeklyRaw: (cells[idx.Weekly] || "").trim(),
            weeklyBad: wk.bad
          });
        }
        if (!rows.length) {
          setError("No valid rows with a Symbol were found.");
          return;
        }
        const existing = new Set(data.symbols.map(s => s.symbol));
        const fileSet = new Set(rows.map(r => r.symbol));
        // Exact symbol lists so we can tell the user precisely what changes.
        const addedSyms = rows.filter(r => !existing.has(r.symbol)).map(r => r.symbol);
        const updatedSyms = rows.filter(r => existing.has(r.symbol)).map(r => r.symbol);
        const removedSyms = data.symbols.map(s => s.symbol).filter(s => !fileSet.has(s));
        setParsed({
          rows,
          dupes,
          badWeekly,
          skippedNoSymbol,
          newCount: addedSyms.length,
          updateCount: updatedSyms.length,
          addedSyms,
          updatedSyms,
          removedSyms
        });
        setStage("preview");
      } catch (err) {
        setError("Could not read the CSV: " + (err && err.message || err));
      }
    };
    reader.onerror = () => setError("Could not read the file.");
    reader.readAsText(file);
  };
  const doImport = () => {
    const n = onImportCsv(parsed.rows, mode);
    onClose(n);
  };
  // Compact symbol list: show all up to `max`, then "+N more".
  const symList = (arr, max = 40) => arr.length <= max ? arr.join(", ") : arr.slice(0, max).join(", ") + ` +${arr.length - max} more`;
  return /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-panel"
  }, stage === "pick" && /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-pick"
  }, /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-help"
  }, "Import a CSV with columns ", /*#__PURE__*/React.createElement("b", null, "Symbol, Tag, Industry, Sector, Weekly"), ". Symbols are cleaned (trimmed + uppercased) and de-duplicated. Weekly must be ", /*#__PURE__*/React.createElement("b", null, "Yes"), " or ", /*#__PURE__*/React.createElement("b", null, "No"), ". Industry & Sector from the file become the source of truth across the app."), /*#__PURE__*/React.createElement("input", {
    ref: fileRef,
    type: "file",
    accept: ".csv,text/csv",
    className: "wlm-csv-file",
    onChange: e => handleFile(e.target.files && e.target.files[0])
  }), /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-pick-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "wlm-csv-btn",
    title: "Choose a .csv file from your device",
    onClick: () => fileRef.current && fileRef.current.click()
  }, "Choose CSV file."), /*#__PURE__*/React.createElement("button", {
    className: "wlm-csv-cancel",
    onClick: () => onClose(0)
  }, "Cancel")), fileName && /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-fname"
  }, fileName), error && /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-error"
  }, error)), stage === "preview" && parsed && /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-preview"
  }, /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-summary"
  }, /*#__PURE__*/React.createElement("b", null, parsed.rows.length), " valid symbol", parsed.rows.length === 1 ? "" : "s", " in", /*#__PURE__*/React.createElement("span", {
    className: "wlm-csv-fname"
  }, " ", fileName), " — ", parsed.newCount, " new, ", parsed.updateCount, " already on your list."), /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-changes"
  }, parsed.addedSyms.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-change add"
  }, /*#__PURE__*/React.createElement("b", null, "+ ", parsed.addedSyms.length, " added:"), " ", symList(parsed.addedSyms)), parsed.updatedSyms.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-change upd"
  }, /*#__PURE__*/React.createElement("b", null, "↻ ", parsed.updatedSyms.length, " updated:"), " ", symList(parsed.updatedSyms)), mode === "replace" && parsed.removedSyms.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-change rem"
  }, /*#__PURE__*/React.createElement("b", null, "− ", parsed.removedSyms.length, " removed:"), " ", symList(parsed.removedSyms)), mode === "update" && parsed.removedSyms.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-change keep"
  }, /*#__PURE__*/React.createElement("b", null, parsed.removedSyms.length, " kept"), " (not in file, left untouched):", " ", symList(parsed.removedSyms))), (parsed.badWeekly > 0 || parsed.dupes.length > 0 || parsed.skippedNoSymbol > 0) && /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-warn"
  }, parsed.badWeekly > 0 && /*#__PURE__*/React.createElement("div", null, "⚠ ", parsed.badWeekly, " row", parsed.badWeekly === 1 ? "" : "s", " have a Weekly value that isn't Yes/No — those will be imported as blank (unknown)."), parsed.dupes.length > 0 && /*#__PURE__*/React.createElement("div", null, "⚠ ", parsed.dupes.length, " duplicate symbol", parsed.dupes.length === 1 ? "" : "s", " in the file were collapsed: ", Array.from(new Set(parsed.dupes)).slice(0, 10).join(", "), parsed.dupes.length > 10 ? "…" : "", "."), parsed.skippedNoSymbol > 0 && /*#__PURE__*/React.createElement("div", null, "⚠ ", parsed.skippedNoSymbol, " row", parsed.skippedNoSymbol === 1 ? "" : "s", " had no Symbol and were skipped.")), /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "wlm-csv-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Ticker symbol (cleaned: trimmed + uppercased)"
  }, "Symbol"), /*#__PURE__*/React.createElement("th", {
    title: "Your custom category for grouping this stock"
  }, "Tag"), /*#__PURE__*/React.createElement("th", {
    title: "Industry (becomes source of truth)"
  }, "Industry"), /*#__PURE__*/React.createElement("th", {
    title: "Sector (becomes source of truth)"
  }, "Sector"), /*#__PURE__*/React.createElement("th", {
    title: "Whether weekly options exist (Yes/No)"
  }, "Weekly"))), /*#__PURE__*/React.createElement("tbody", null, parsed.rows.slice(0, 200).map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: r.symbol + i
  }, /*#__PURE__*/React.createElement("td", {
    className: "wlm-csv-sym"
  }, r.symbol), /*#__PURE__*/React.createElement("td", null, r.tag || /*#__PURE__*/React.createElement("span", {
    className: "wlm-csv-blank"
  }, "—")), /*#__PURE__*/React.createElement("td", null, r.industry || /*#__PURE__*/React.createElement("span", {
    className: "wlm-csv-blank"
  }, "—")), /*#__PURE__*/React.createElement("td", null, r.sector || /*#__PURE__*/React.createElement("span", {
    className: "wlm-csv-blank"
  }, "—")), /*#__PURE__*/React.createElement("td", {
    className: r.weeklyBad ? "wlm-csv-badwk" : ""
  }, r.weekly === true ? "Yes" : r.weekly === false ? "No" : r.weeklyBad ? r.weeklyRaw + " ⚠" : /*#__PURE__*/React.createElement("span", {
    className: "wlm-csv-blank"
  }, "—")))))), parsed.rows.length > 200 && /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-more"
  }, "…and ", parsed.rows.length - 200, " more (all will be imported).")), /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-mode"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Refresh matching symbols and add new ones; keep symbols not in the file"
  }, /*#__PURE__*/React.createElement("input", {
    type: "radio",
    name: "wlm-csv-mode",
    checked: mode === "update",
    onChange: () => setMode("update")
  }), "Update & add ", /*#__PURE__*/React.createElement("span", {
    className: "wlm-csv-mode-note"
  }, "(keep symbols not in the file)")), /*#__PURE__*/React.createElement("label", {
    title: "Make the watchlist exactly the imported list; symbols not in the file are removed"
  }, /*#__PURE__*/React.createElement("input", {
    type: "radio",
    name: "wlm-csv-mode",
    checked: mode === "replace",
    onChange: () => setMode("replace")
  }), "Replace all ", /*#__PURE__*/React.createElement("span", {
    className: "wlm-csv-mode-note"
  }, "(remove symbols not in the file)"))), mode === "replace" && parsed.removedSyms.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-warn"
  }, "⚠ Replace will remove ", parsed.removedSyms.length, " symbol", parsed.removedSyms.length === 1 ? "" : "s", " currently on your watchlist that aren't in this file: ", /*#__PURE__*/React.createElement("b", null, symList(parsed.removedSyms))), /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "wlm-csv-btn wlm-csv-confirm",
    onClick: doImport,
    title: mode === "replace" ? "Replace your watchlist with this file" : "Merge this file into your watchlist"
  }, mode === "replace" ? "Replace watchlist" : "Import", " (", parsed.rows.length, ")"), /*#__PURE__*/React.createElement("button", {
    className: "wlm-csv-cancel",
    onClick: () => {
      setStage("pick");
      setParsed(null);
    }
  }, "Back"), /*#__PURE__*/React.createElement("button", {
    className: "wlm-csv-cancel",
    onClick: () => onClose(0)
  }, "Cancel"))));
}
function WatchlistManager({
  data,
  onAdd,
  onRemove,
  onToggleStar,
  onUpdate,
  onBulkAdd,
  onImportCsv,
  onSwitchTicker
}) {
  const [search, setSearch] = useState("");
  const [tagFilter, setTagFilter] = useState(null);
  const [bulkText, setBulkText] = useState("");
  const [bulkOpen, setBulkOpen] = useState(false);
  const [editing, setEditing] = useState(null); // symbol being edited
  const [sortBy, setSortBy] = useState("starred"); // starred | symbol | added
  const [csvOpen, setCsvOpen] = useState(false);
  const [importMsg, setImportMsg] = useState("");
  const exportCsv = () => {
    const csv = watchlistToCsv([...data.symbols].sort((a, b) => a.symbol.localeCompare(b.symbol)));
    const blob = new Blob([csv], {
      type: "text/csv;charset=utf-8"
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "watchlist.csv";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const [catFilter, setCatFilter] = useState(""); // CSV "Tag" category filter
  // Derived: all unique free-form tags with counts
  const allTags = useMemo(() => {
    const counts = {};
    for (const s of data.symbols) {
      for (const t of s.tags || []) counts[t] = (counts[t] || 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [data.symbols]);
  // Derived: all unique CSV categories (the first-class "Tag" field)
  const allCats = useMemo(() => {
    const set = new Set();
    for (const s of data.symbols) if (s.tag) set.add(s.tag);
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [data.symbols]);
  // Filtered + sorted view
  const visible = useMemo(() => {
    const q = search.trim().toUpperCase();
    let rows = data.symbols.filter(s => {
      if (q && !s.symbol.includes(q) && !(s.notes || "").toUpperCase().includes(q) && !(s.tag || "").toUpperCase().includes(q) && !(s.sector || "").toUpperCase().includes(q) && !(s.industry || "").toUpperCase().includes(q) && !(s.tags || []).some(t => t.toUpperCase().includes(q))) {
        return false;
      }
      if (tagFilter && !(s.tags || []).includes(tagFilter)) return false;
      if (catFilter && (s.tag || "") !== catFilter) return false;
      return true;
    });
    if (sortBy === "starred") {
      rows.sort((a, b) => (b.starred ? 1 : 0) - (a.starred ? 1 : 0) || a.symbol.localeCompare(b.symbol));
    } else if (sortBy === "symbol") {
      rows.sort((a, b) => a.symbol.localeCompare(b.symbol));
    } else if (sortBy === "added") {
      rows.sort((a, b) => (b.added_at || 0) - (a.added_at || 0));
    } else if (sortBy === "tag") {
      rows.sort((a, b) => (a.tag || "~").localeCompare(b.tag || "~") || a.symbol.localeCompare(b.symbol));
    }
    return rows;
  }, [data.symbols, search, tagFilter, catFilter, sortBy]);
  return /*#__PURE__*/React.createElement("div", {
    className: "wlm-body"
  }, /*#__PURE__*/React.createElement("div", {
    className: "wlm-toolbar"
  }, /*#__PURE__*/React.createElement("input", {
    className: "wlm-search",
    type: "text",
    placeholder: "Search symbol, tag, or note.",
    value: search,
    onChange: e => setSearch(e.target.value)
  }), /*#__PURE__*/React.createElement("select", {
    className: "wlm-sort",
    value: sortBy,
    onChange: e => setSortBy(e.target.value),
    title: "Sort"
  }, /*#__PURE__*/React.createElement("option", {
    value: "starred"
  }, "★ Starred first"), /*#__PURE__*/React.createElement("option", {
    value: "symbol"
  }, "A-Z"), /*#__PURE__*/React.createElement("option", {
    value: "added"
  }, "Recently added"), /*#__PURE__*/React.createElement("option", {
    value: "tag"
  }, "By tag")), allCats.length > 0 && /*#__PURE__*/React.createElement("select", {
    className: "wlm-sort",
    value: catFilter,
    onChange: e => setCatFilter(e.target.value),
    title: "Filter by your Tag (category)"
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All tags"), allCats.map(c => /*#__PURE__*/React.createElement("option", {
    key: c,
    value: c
  }, c))), /*#__PURE__*/React.createElement("button", {
    className: `wlm-bulk-toggle${bulkOpen ? " active" : ""}`,
    onClick: () => setBulkOpen(o => !o),
    title: "Paste many tickers at once"
  }, bulkOpen ? "Close bulk add" : "+ Bulk add"), /*#__PURE__*/React.createElement("button", {
    className: `wlm-bulk-toggle${csvOpen ? " active" : ""}`,
    onClick: () => {
      setCsvOpen(o => !o);
      setImportMsg("");
    },
    title: "Import a stock list from a CSV (Symbol, Tag, Industry, Sector, Weekly)"
  }, csvOpen ? "Close import" : "⇪ Import CSV"), /*#__PURE__*/React.createElement("button", {
    className: "wlm-bulk-toggle",
    onClick: exportCsv,
    title: "Download your current list as a CSV you can edit and re-import"
  }, "⇩ Export CSV")), csvOpen && /*#__PURE__*/React.createElement(CsvImportPanel, {
    data: data,
    onImportCsv: onImportCsv,
    onClose: n => {
      setCsvOpen(false);
      if (n > 0) setImportMsg(`Imported ${n} symbol${n === 1 ? "" : "s"} from CSV.`);
    }
  }), importMsg && /*#__PURE__*/React.createElement("div", {
    className: "wlm-csv-done"
  }, importMsg), allTags.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "wlm-tags-row"
  }, /*#__PURE__*/React.createElement("button", {
    className: `wlm-tag-chip${!tagFilter ? " active" : ""}`,
    onClick: () => setTagFilter(null)
  }, "All (", data.symbols.length, ")"), allTags.map(([t, n]) => /*#__PURE__*/React.createElement("button", {
    key: t,
    className: `wlm-tag-chip${tagFilter === t ? " active" : ""}`,
    onClick: () => setTagFilter(tagFilter === t ? null : t)
  }, t, " (", n, ")"))), bulkOpen && /*#__PURE__*/React.createElement("div", {
    className: "wlm-bulk-panel"
  }, /*#__PURE__*/React.createElement("textarea", {
    className: "wlm-bulk-input",
    rows: 4,
    placeholder: "Paste tickers separated by commas, spaces, or new lines.\nExample: AAPL, NVDA, MSFT, AMD\nOr one per line:\nTSLA\nMETA",
    value: bulkText,
    onChange: e => setBulkText(e.target.value)
  }), /*#__PURE__*/React.createElement("div", {
    className: "wlm-bulk-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "wlm-bulk-add",
    onClick: () => {
      const n = onBulkAdd(bulkText);
      if (n > 0) {
        setBulkText("");
        setBulkOpen(false);
      }
    }
  }, "Add to watchlist"))), /*#__PURE__*/React.createElement(QuickAddRow, {
    onAdd: s => onAdd(s)
  }), /*#__PURE__*/React.createElement("div", {
    className: "wlm-list"
  }, visible.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "wlm-empty"
  }, data.symbols.length === 0 ? "Watchlist is empty. Add symbols above." : "No matches for current filters."), visible.map(s => /*#__PURE__*/React.createElement(WatchlistRow, {
    key: s.symbol,
    entry: s,
    isEditing: editing === s.symbol,
    onSwitchTicker: onSwitchTicker,
    onToggleStar: () => onToggleStar(s.symbol),
    onRemove: () => onRemove(s.symbol),
    onEdit: () => setEditing(s.symbol),
    onCloseEdit: () => setEditing(null),
    onUpdate: patch => onUpdate(s.symbol, patch)
  }))));
}
function QuickAddRow({
  onAdd
}) {
  const [val, setVal] = useState("");
  const submit = () => {
    if (!val.trim()) return;
    onAdd(val);
    setVal("");
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "wlm-quick-add"
  }, /*#__PURE__*/React.createElement("input", {
    type: "text",
    className: "wlm-quick-input",
    placeholder: "Add a symbol.",
    value: val,
    onChange: e => setVal(e.target.value),
    onKeyDown: e => {
      if (e.key === "Enter") submit();
    }
  }), /*#__PURE__*/React.createElement("button", {
    className: "wlm-quick-btn",
    onClick: submit
  }, "Add"));
}
function WatchlistRow({
  entry,
  isEditing,
  onSwitchTicker,
  onToggleStar,
  onRemove,
  onEdit,
  onCloseEdit,
  onUpdate
}) {
  const [tagsInput, setTagsInput] = useState((entry.tags || []).join(", "));
  const [notesInput, setNotesInput] = useState(entry.notes || "");
  const [strategyInput, setStrategyInput] = useState(entry.preferred_strategy || "");
  const [tagInput, setTagInput] = useState(entry.tag || "");
  const [sectorInput, setSectorInput] = useState(entry.sector || "");
  const [industryInput, setIndustryInput] = useState(entry.industry || "");
  const [weeklyInput, setWeeklyInput] = useState(entry.weekly === true ? "Yes" : entry.weekly === false ? "No" : "");
  // Reset local state when entering edit mode
  useEffect(() => {
    if (isEditing) {
      setTagsInput((entry.tags || []).join(", "));
      setNotesInput(entry.notes || "");
      setStrategyInput(entry.preferred_strategy || "");
      setTagInput(entry.tag || "");
      setSectorInput(entry.sector || "");
      setIndustryInput(entry.industry || "");
      setWeeklyInput(entry.weekly === true ? "Yes" : entry.weekly === false ? "No" : "");
    }
  }, [isEditing, entry.symbol]);
  const saveEdits = () => {
    const tags = tagsInput.split(/[,;\n]/).map(t => t.trim().toLowerCase()).filter(t => t && t.length <= 32);
    onUpdate({
      tags: Array.from(new Set(tags)),
      notes: notesInput.slice(0, 500),
      preferred_strategy: strategyInput.trim() || null,
      tag: tagInput.trim().slice(0, 40),
      sector: sectorInput.trim().slice(0, 80),
      industry: industryInput.trim().slice(0, 80),
      weekly: weeklyInput === "Yes" ? true : weeklyInput === "No" ? false : null
    });
    onCloseEdit();
  };
  const STRATEGY_OPTIONS = [{
    value: "",
    label: "(none)"
  }, {
    value: "covered_call",
    label: "Covered Call"
  }, {
    value: "cash_secured_put",
    label: "Cash-Secured Put"
  }, {
    value: "short_strangle",
    label: "Short Strangle"
  }, {
    value: "iron_condor",
    label: "Iron Condor"
  }, {
    value: "bull_put_spread",
    label: "Bull Put Spread"
  }, {
    value: "jade_lizard",
    label: "Jade Lizard"
  }, {
    value: "wheel",
    label: "Wheel"
  }];
  return /*#__PURE__*/React.createElement("div", {
    className: `wlm-row${entry.starred ? " starred" : ""}${isEditing ? " editing" : ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "wlm-row-main"
  }, /*#__PURE__*/React.createElement("button", {
    className: `wlm-star-btn${entry.starred ? " on" : ""}`,
    onClick: onToggleStar,
    title: entry.starred ? "Unstar" : "Star (pin to sidebar)"
  }, entry.starred ? "★" : "☆"), /*#__PURE__*/React.createElement("button", {
    className: "wlm-sym-btn",
    onClick: () => onSwitchTicker(entry.symbol),
    title: "Switch dashboard to this ticker"
  }, entry.symbol), /*#__PURE__*/React.createElement("div", {
    className: "wlm-row-meta"
  }, entry.tag && /*#__PURE__*/React.createElement("span", {
    className: "wlm-cat-pill",
    title: "Your category (from CSV import)"
  }, entry.tag), entry.weekly === true && /*#__PURE__*/React.createElement("span", {
    className: "wlm-wk-pill",
    title: "Has weekly options"
  }, "Wk"), entry.sector && /*#__PURE__*/React.createElement("span", {
    className: "wlm-sec-pill",
    title: "Sector: " + entry.sector
  }, entry.sector), entry.industry && /*#__PURE__*/React.createElement("span", {
    className: "wlm-ind-pill",
    title: "Industry: " + entry.industry
  }, entry.industry), (entry.tags || []).map(t => /*#__PURE__*/React.createElement("span", {
    key: t,
    className: "wlm-tag-pill"
  }, t)), entry.preferred_strategy && /*#__PURE__*/React.createElement("span", {
    className: "wlm-strategy-pill"
  }, entry.preferred_strategy.replace(/_/g, " ")), entry.notes && /*#__PURE__*/React.createElement("span", {
    className: "wlm-note-snip",
    title: entry.notes
  }, entry.notes.length > 40 ? entry.notes.slice(0, 40) + "." : entry.notes)), /*#__PURE__*/React.createElement("div", {
    className: "wlm-row-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "wlm-edit-btn",
    onClick: isEditing ? onCloseEdit : onEdit
  }, isEditing ? "Cancel" : "Edit"), /*#__PURE__*/React.createElement("button", {
    className: "wlm-del-btn",
    onClick: onRemove,
    title: "Remove from watchlist"
  }, "×"))), isEditing && /*#__PURE__*/React.createElement("div", {
    className: "wlm-edit-panel"
  }, /*#__PURE__*/React.createElement("div", {
    className: "wlm-edit-row"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Your custom category for grouping this stock"
  }, "Tag"), /*#__PURE__*/React.createElement("input", {
    type: "text",
    value: tagInput,
    maxLength: 40,
    placeholder: "Your category, e.g. Core, Swing, AI",
    onChange: e => setTagInput(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "wlm-edit-row"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Whether weekly options exist (source of truth)"
  }, "Weekly"), /*#__PURE__*/React.createElement("select", {
    value: weeklyInput,
    onChange: e => setWeeklyInput(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "(unknown)"), /*#__PURE__*/React.createElement("option", {
    value: "Yes"
  }, "Yes"), /*#__PURE__*/React.createElement("option", {
    value: "No"
  }, "No"))), /*#__PURE__*/React.createElement("div", {
    className: "wlm-edit-row"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Sector (overrides external data across the app)"
  }, "Sector"), /*#__PURE__*/React.createElement("input", {
    type: "text",
    value: sectorInput,
    maxLength: 80,
    placeholder: "e.g. Technology",
    onChange: e => setSectorInput(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "wlm-edit-row"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Industry (overrides external data across the app)"
  }, "Industry"), /*#__PURE__*/React.createElement("input", {
    type: "text",
    value: industryInput,
    maxLength: 80,
    placeholder: "e.g. Semiconductors",
    onChange: e => setIndustryInput(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "wlm-edit-row"
  }, /*#__PURE__*/React.createElement("label", {
    title: "Free-form labels for filtering (comma-separated)"
  }, "Tags"), /*#__PURE__*/React.createElement("input", {
    type: "text",
    value: tagsInput,
    placeholder: "comma-separated. e.g. semis, mega-cap, earnings-soon",
    onChange: e => setTagsInput(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "wlm-edit-row"
  }, /*#__PURE__*/React.createElement("label", null, "Strategy"), /*#__PURE__*/React.createElement("select", {
    value: strategyInput,
    onChange: e => setStrategyInput(e.target.value)
  }, STRATEGY_OPTIONS.map(o => /*#__PURE__*/React.createElement("option", {
    key: o.value,
    value: o.value
  }, o.label)))), /*#__PURE__*/React.createElement("div", {
    className: "wlm-edit-row"
  }, /*#__PURE__*/React.createElement("label", null, "Notes"), /*#__PURE__*/React.createElement("textarea", {
    value: notesInput,
    rows: 2,
    maxLength: 500,
    placeholder: "Personal notes, conviction context, recent observations.",
    onChange: e => setNotesInput(e.target.value)
  })), /*#__PURE__*/React.createElement("div", {
    className: "wlm-edit-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "wlm-save-btn",
    onClick: saveEdits
  }, "Save"))));
}
function FlashOnChange({
  value,
  className = "",
  children
}) {
  const [flash, setFlash] = React.useState(null);
  const prev = React.useRef(value);
  React.useEffect(() => {
    if (prev.current !== value && prev.current != null) {
      setFlash(value > prev.current ? "up" : "down");
      const t = setTimeout(() => setFlash(null), 600);
      prev.current = value;
      return () => clearTimeout(t);
    }
    prev.current = value;
  }, [value]);
  return /*#__PURE__*/React.createElement("span", {
    className: `${className}${flash ? ` price-flash-${flash}` : ""}`
  }, children);
}
function SortableTh({
  label,
  sortKey,
  current,
  onSort,
  className = ""
}) {
  const isActive = current && current.key === sortKey;
  const arrow = !isActive ? "" : current.dir === "desc" ? " ▾" : " ▴";
  return /*#__PURE__*/React.createElement("th", {
    className: `${className} sortable-th${isActive ? " active" : ""}`,
    onClick: () => onSort(sortKey)
  }, /*#__PURE__*/React.createElement("span", null, label, arrow));
}
function PercentCalc({
  activeTicker,
  livePrice,
  accentColor
}) {
  const STORAGE_KEY = "weeklyOptionsTimer.calc.v1";
  const persisted = (() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  })();
  const [open, setOpen] = useState(persisted?.open ?? false);
  const [fromOverride, setFromOverride] = useState(""); // empty = auto from livePrice
  // Mode: "p2p" = price-to-percent (enter target price → see % move).
  //       "pct2p" = percent-to-price (enter % → see target price).
  // Both modes share the FROM input. Persisted so the user's preferred
  // mode survives reloads.
  const [mode, setMode] = useState(persisted?.mode || "p2p");
  const [rows, setRows] = useState(persisted?.rows ?? [{
    id: 1,
    value: ""
  }]);
  const [pctRows, setPctRows] = useState(persisted?.pctRows ?? [{
    id: 1,
    value: ""
  }]);
  const nextIdRef = useRef(persisted?.rows?.length ? Math.max(...persisted.rows.map(r => r.id)) + 1 : 2);
  const nextPctIdRef = useRef(persisted?.pctRows?.length ? Math.max(...persisted.pctRows.map(r => r.id)) + 1 : 2);

  // Persist
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        open,
        mode,
        rows,
        pctRows
      }));
    } catch {}
  }, [open, mode, rows, pctRows]);

  // The "from" price: explicit user input wins, else live price
  const fromNum = (() => {
    if (fromOverride.trim() !== "") {
      const n = parseFloat(fromOverride);
      return isFinite(n) ? n : null;
    }
    return livePrice ?? null;
  })();
  const addRow = () => {
    setRows(prev => [...prev, {
      id: nextIdRef.current++,
      value: ""
    }]);
  };
  const updateRow = (id, value) => {
    setRows(prev => prev.map(r => r.id === id ? {
      ...r,
      value
    } : r));
  };
  const removeRow = id => {
    setRows(prev => prev.length > 1 ? prev.filter(r => r.id !== id) : prev);
  };
  const addPctRow = () => {
    setPctRows(prev => [...prev, {
      id: nextPctIdRef.current++,
      value: ""
    }]);
  };
  const updatePctRow = (id, value) => {
    setPctRows(prev => prev.map(r => r.id === id ? {
      ...r,
      value
    } : r));
  };
  const removePctRow = id => {
    setPctRows(prev => prev.length > 1 ? prev.filter(r => r.id !== id) : prev);
  };

  // p2p: target price → % move + $ diff
  const calc = toStr => {
    const to = parseFloat(toStr);
    if (!isFinite(to) || fromNum == null || fromNum <= 0) return null;
    const diff = to - fromNum;
    const pct = diff / fromNum * 100;
    return {
      diff,
      pct
    };
  };

  // pct2p: % move → target price + $ diff
  const calcPct = pctStr => {
    const pct = parseFloat(pctStr);
    if (!isFinite(pct) || fromNum == null || fromNum <= 0) return null;
    const diff = fromNum * (pct / 100);
    const to = fromNum + diff;
    return {
      diff,
      to
    };
  };
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("button", {
    className: `pcalc-tab${open ? " pcalc-tab-open" : ""}`,
    onClick: () => setOpen(o => !o),
    title: open ? "Hide % calculator" : "Show % calculator"
  }, open ? "✕" : "%"), /*#__PURE__*/React.createElement("aside", {
    className: `pcalc-panel${open ? " pcalc-panel-open" : ""}`,
    "aria-hidden": !open
  }, /*#__PURE__*/React.createElement("div", {
    className: "pcalc-head"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pcalc-title"
  }, "Percent calculator"), /*#__PURE__*/React.createElement("div", {
    className: "pcalc-sub"
  }, mode === "p2p" ? "Price → percent" : "Percent → price"), /*#__PURE__*/React.createElement("div", {
    className: "pcalc-mode-toggle",
    title: "Switch direction"
  }, /*#__PURE__*/React.createElement("button", {
    className: mode === "p2p" ? "active" : "",
    onClick: () => setMode("p2p"),
    title: "Enter a target price, see the percent move from FROM"
  }, "$ → %"), /*#__PURE__*/React.createElement("button", {
    className: mode === "pct2p" ? "active" : "",
    onClick: () => setMode("pct2p"),
    title: "Enter a percent, see the target price"
  }, "% → $"))), /*#__PURE__*/React.createElement("div", {
    className: "pcalc-body"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pcalc-from-row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pcalc-label"
  }, "FROM"), /*#__PURE__*/React.createElement("div", {
    className: "pcalc-from-input-wrap"
  }, /*#__PURE__*/React.createElement("span", {
    className: "pcalc-currency"
  }, "$"), /*#__PURE__*/React.createElement("input", {
    type: "text",
    inputMode: "decimal",
    className: "pcalc-from-input",
    value: fromOverride,
    placeholder: livePrice != null ? livePrice.toFixed(2) : "—",
    onChange: e => setFromOverride(e.target.value)
  }), fromOverride !== "" && /*#__PURE__*/React.createElement("button", {
    className: "pcalc-clear-btn",
    onClick: () => setFromOverride(""),
    title: "Reset to live price"
  }, "↺")), /*#__PURE__*/React.createElement("div", {
    className: "pcalc-from-meta"
  }, fromOverride === "" && livePrice != null ? `live · ${activeTicker || "—"}` : fromOverride !== "" ? "manual" : "no live price")), /*#__PURE__*/React.createElement("div", {
    className: "pcalc-divider"
  }), mode === "p2p" ? /*#__PURE__*/React.createElement("div", {
    className: "pcalc-to-section"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pcalc-label"
  }, "TO ($)"), rows.map(row => {
    const result = calc(row.value);
    return /*#__PURE__*/React.createElement("div", {
      key: row.id,
      className: "pcalc-to-row"
    }, /*#__PURE__*/React.createElement("div", {
      className: "pcalc-to-input-wrap"
    }, /*#__PURE__*/React.createElement("span", {
      className: "pcalc-currency"
    }, "$"), /*#__PURE__*/React.createElement("input", {
      type: "text",
      inputMode: "decimal",
      className: "pcalc-to-input",
      value: row.value,
      placeholder: "0.00",
      onChange: e => updateRow(row.id, e.target.value)
    })), /*#__PURE__*/React.createElement("div", {
      className: "pcalc-result"
    }, result ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      className: `pcalc-pct ${result.pct >= 0 ? "up" : "down"}`
    }, result.pct >= 0 ? "+" : "", result.pct.toFixed(2), "%"), /*#__PURE__*/React.createElement("div", {
      className: `pcalc-dollar ${result.diff >= 0 ? "up" : "down"}`
    }, result.diff >= 0 ? "+" : "", "$", result.diff.toFixed(2))) : /*#__PURE__*/React.createElement("div", {
      className: "pcalc-empty-result"
    }, "—")), rows.length > 1 && /*#__PURE__*/React.createElement("button", {
      className: "pcalc-remove-btn",
      onClick: () => removeRow(row.id),
      title: "Remove row"
    }, "×"));
  }), /*#__PURE__*/React.createElement("button", {
    className: "pcalc-add-btn",
    onClick: addRow
  }, "+ Add row")) : /*#__PURE__*/React.createElement("div", {
    className: "pcalc-to-section"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pcalc-label"
  }, "TO (%)"), pctRows.map(row => {
    const result = calcPct(row.value);
    return /*#__PURE__*/React.createElement("div", {
      key: row.id,
      className: "pcalc-to-row"
    }, /*#__PURE__*/React.createElement("div", {
      className: "pcalc-to-input-wrap"
    }, /*#__PURE__*/React.createElement("input", {
      type: "text",
      inputMode: "decimal",
      className: "pcalc-to-input",
      value: row.value,
      placeholder: "0.00",
      onChange: e => updatePctRow(row.id, e.target.value)
    }), /*#__PURE__*/React.createElement("span", {
      className: "pcalc-currency pcalc-pct-suffix"
    }, "%")), /*#__PURE__*/React.createElement("div", {
      className: "pcalc-result"
    }, result ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      className: `pcalc-pct ${result.to >= fromNum ? "up" : "down"}`
    }, "$", result.to.toFixed(2)), /*#__PURE__*/React.createElement("div", {
      className: `pcalc-dollar ${result.diff >= 0 ? "up" : "down"}`
    }, result.diff >= 0 ? "+" : "", "$", result.diff.toFixed(2))) : /*#__PURE__*/React.createElement("div", {
      className: "pcalc-empty-result"
    }, "—")), pctRows.length > 1 && /*#__PURE__*/React.createElement("button", {
      className: "pcalc-remove-btn",
      onClick: () => removePctRow(row.id),
      title: "Remove row"
    }, "×"));
  }), /*#__PURE__*/React.createElement("button", {
    className: "pcalc-add-btn",
    onClick: addPctRow
  }, "+ Add row")))));
}
function RollManagerCard({
  ticker,
  positions,
  currentPrice,
  livePrice,
  apiFetch,
  uwHealth
}) {
  const [quotes, setQuotes] = useState({}); // key: "exp|strike" -> {mid, delta, ...}
  const [loading, setLoading] = useState(false);
  // UW flow context — used to color roll suggestions with current flow read.
  const [flowScore, setFlowScore] = useState(null);
  // Clear stale flow score the moment ticker changes — the fetch
  // below will repopulate. Without this, the previous ticker's
  // flow read briefly bleeds into the new ticker's view.
  useEffect(() => {
    setFlowScore(null);
  }, [ticker]);
  // Latest price via ref so the fetch URL stays current WITHOUT making
  // currentPrice an effect dependency (which refetched on every 5s quote tick).
  const flowPriceRef = React.useRef(currentPrice);
  flowPriceRef.current = currentPrice;
  useEffect(() => {
    if (!ticker || !uwHealth?.connected) {
      setFlowScore(null);
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const r = await apiFetch(`/api/uw/flow_score?symbol=${encodeURIComponent(ticker)}&price=${flowPriceRef.current || 0}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (!cancelled) setFlowScore(j);
      } catch {}
    };
    load();
    const id = setInterval(() => {
      if (!document.hidden) load();
    }, 60000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [ticker, uwHealth?.connected]);

  // Active short calls on the displayed ticker
  const shortCalls = (positions || []).filter(p => p.status === "open" && p.ticker === ticker).flatMap(p => (p.legs || []).filter(l => l.type === "call" && l.qty < 0).map(l => ({
    ...l,
    positionId: p.id,
    entryDate: p.entryDate || p.openedAt || null
  })));

  // Fetch current quote for each short call + roll candidates.
  useEffect(() => {
    if (!shortCalls.length) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      const next = {};
      for (const sc of shortCalls) {
        // Current short call value
        const k = `${sc.expiration}|${sc.strike}`;
        try {
          const r = await apiFetch(`/api/option_quote?symbol=${encodeURIComponent(ticker)}&exp=${sc.expiration}&strike=${sc.strike}&type=call`);
          if (r?.found) next[k] = r;
        } catch {}
        // Roll candidates: compute next-week date and fetch a few strikes
        const nextWeek = (() => {
          const d = new Date(sc.expiration + "T12:00:00");
          d.setDate(d.getDate() + 7);
          return d.toISOString().slice(0, 10);
        })();
        // Same strike +1wk, $5 higher +1wk, $10 higher +1wk
        for (const sk of [sc.strike, sc.strike + 5, sc.strike + 10]) {
          const k2 = `${nextWeek}|${sk}`;
          if (next[k2]) continue;
          try {
            const r = await apiFetch(`/api/option_quote?symbol=${encodeURIComponent(ticker)}&exp=${nextWeek}&strike=${sk}&type=call`);
            if (r?.found) next[k2] = r;
          } catch {}
        }
        // v1.16: 4-week roll-out at same strike for the P/L modeling card.
        const fourWeek = (() => {
          const d = new Date(sc.expiration + "T12:00:00");
          d.setDate(d.getDate() + 28);
          return d.toISOString().slice(0, 10);
        })();
        const k4 = `${fourWeek}|${sc.strike}`;
        if (!next[k4]) {
          try {
            const r = await apiFetch(`/api/option_quote?symbol=${encodeURIComponent(ticker)}&exp=${fourWeek}&strike=${sc.strike}&type=call`);
            if (r?.found) next[k4] = r;
          } catch {}
        }
      }
      if (!cancelled) {
        setQuotes(next);
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, JSON.stringify(shortCalls.map(s => `${s.expiration}|${s.strike}`))]);
  if (!shortCalls.length) return null;
  const live = livePrice ?? currentPrice;
  return /*#__PURE__*/React.createElement("div", {
    className: "card roll-manager"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Active short calls · roll choices"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Roll Manager", loading && /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 12,
      marginLeft: 8
    }
  }, "fetching quotes…")))), uwHealth?.connected && flowScore?.data_available && (() => {
    // Decide what flow says about rolling. The most dangerous case
    // is bullish flow targeting strikes ABOVE the short — that
    // means rolling same strike (or only slightly higher) is
    // walking into the targeted zone.
    const cls = flowScore.cc_risk >= 70 ? "verdict-avoid" : flowScore.cc_risk >= 50 ? "verdict-partial" : flowScore.bearish >= 60 ? "verdict-partial" : "verdict-sell";
    let line = "";
    if (flowScore.cc_risk >= 70 && flowScore.bullish >= 70) {
      line = `Bullish flow is targeting strikes above current. Don't roll same strike — the next-week strike has heavy call buying. Consider rolling further OTM or letting assignment play out.`;
    } else if (flowScore.cc_risk >= 60) {
      line = `Some bullish flow above current strike. If rolling, push further OTM than usual.`;
    } else if (flowScore.bearish >= 70) {
      line = `Heavy put flow. Same-strike roll may collect rich premium, but watch downside.`;
    } else if (flowScore.bullish < 40 && flowScore.bearish < 40) {
      line = `Flow is quiet. Standard roll logic applies.`;
    } else if (flowScore.overall < 45) {
      line = `Flow leaning bearish. Same-strike roll likely safer than usual.`;
    } else {
      line = flowScore.reason;
    }
    return /*#__PURE__*/React.createElement("div", {
      className: `roll-flow-context flow-verdict ${cls}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "flow-verdict-label",
      title: "Unusual Whales flow read for the active ticker. Drives roll-decision context."
    }, "UW FLOW · ", flowScore.verdict), /*#__PURE__*/React.createElement("div", {
      className: "flow-verdict-reason"
    }, line));
  })(), /*#__PURE__*/React.createElement("div", {
    className: "roll-list"
  }, shortCalls.map((sc, i) => {
    const k = `${sc.expiration}|${sc.strike}`;
    const q = quotes[k];
    const currentMid = q?.mid ?? null;
    const entryCredit = sc.premium || 0;
    const currentPL = currentMid != null ? (entryCredit - currentMid) * Math.abs(sc.qty) * 100 : null;
    const intrinsic = live > sc.strike ? Math.max(0, live - sc.strike) : 0;
    const extrinsic = currentMid != null ? Math.max(0, currentMid - intrinsic) : null;
    const dte = (() => {
      try {
        const d = new Date(sc.expiration + "T16:00:00");
        return Math.max(0, Math.ceil((d - new Date()) / 86400000));
      } catch {
        return null;
      }
    })();
    const itm = live > sc.strike;

    // Roll choices
    const nextWeek = (() => {
      const d = new Date(sc.expiration + "T12:00:00");
      d.setDate(d.getDate() + 7);
      return d.toISOString().slice(0, 10);
    })();
    const nextWeekLabel = (() => {
      try {
        const d = new Date(nextWeek + "T12:00:00");
        return d.toLocaleDateString("en-US", {
          month: "short",
          day: "numeric"
        });
      } catch {
        return nextWeek;
      }
    })();
    const buildChoice = (label, strike) => {
      const rk = `${nextWeek}|${strike}`;
      const r = quotes[rk];
      if (!r || currentMid == null) {
        return {
          label,
          strike,
          exp: nextWeekLabel,
          netCredit: null,
          available: false
        };
      }
      // Roll = buy back current short + sell new short
      // Net credit = new mid - current mid (positive = credit, negative = debit)
      const netCredit = r.mid - currentMid;
      return {
        label,
        strike,
        exp: nextWeekLabel,
        netCredit: netCredit * Math.abs(sc.qty) * 100,
        netCreditPerShare: netCredit,
        newDelta: r.delta,
        available: true
      };
    };
    const choices = [buildChoice("Same strike", sc.strike), buildChoice("+$5 strike", sc.strike + 5), buildChoice("+$10 strike", sc.strike + 10)];
    const buyback = currentMid != null ? -currentMid * Math.abs(sc.qty) * 100 : null;
    return /*#__PURE__*/React.createElement("div", {
      className: "roll-item",
      key: `${sc.positionId}-${i}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "roll-head"
    }, /*#__PURE__*/React.createElement("div", {
      className: "roll-strike"
    }, /*#__PURE__*/React.createElement("span", {
      className: "roll-qty"
    }, Math.abs(sc.qty), "x"), /*#__PURE__*/React.createElement("span", {
      className: itm ? "roll-strike-itm" : ""
    }, "$", sc.strike.toFixed(2), " call"), /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, " · ", sc.expiration), dte != null && /*#__PURE__*/React.createElement("span", {
      className: "roll-dte"
    }, dte, "d"), itm && /*#__PURE__*/React.createElement("span", {
      className: "roll-itm-badge"
    }, "ITM")), /*#__PURE__*/React.createElement("div", {
      className: "roll-pl"
    }, currentPL != null && /*#__PURE__*/React.createElement("span", {
      className: currentPL >= 0 ? "up" : "down"
    }, currentPL >= 0 ? "+" : "", "$", currentPL.toFixed(0)))), /*#__PURE__*/React.createElement("div", {
      className: "roll-stats"
    }, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "Entry"), " ", /*#__PURE__*/React.createElement("b", null, "$", entryCredit.toFixed(2))), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "Now"), " ", /*#__PURE__*/React.createElement("b", null, currentMid != null ? "$" + currentMid.toFixed(2) : "—")), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "Intrinsic"), " ", /*#__PURE__*/React.createElement("b", null, "$", intrinsic.toFixed(2))), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "Extrinsic"), " ", /*#__PURE__*/React.createElement("b", null, extrinsic != null ? "$" + extrinsic.toFixed(2) : "—")), q?.delta != null && /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "Δ"), " ", /*#__PURE__*/React.createElement("b", null, q.delta.toFixed(2)))), /*#__PURE__*/React.createElement("div", {
      className: "roll-choices"
    }, choices.map((c, j) => /*#__PURE__*/React.createElement("div", {
      key: j,
      className: `roll-choice${c.available ? "" : " unavailable"}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "roll-choice-label"
    }, "Roll ", c.label, " → ", c.exp), /*#__PURE__*/React.createElement("div", {
      className: "roll-choice-strike"
    }, "$", c.strike.toFixed(2)), c.available ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      className: `roll-choice-credit ${c.netCredit >= 0 ? "up" : "down"}`
    }, c.netCredit >= 0 ? "+" : "", "$", c.netCredit.toFixed(0)), /*#__PURE__*/React.createElement("div", {
      className: "muted",
      style: {
        fontSize: 10.5
      }
    }, c.netCredit >= 0 ? "credit" : "debit", " · Δ ", c.newDelta != null ? c.newDelta.toFixed(2) : "—")) : /*#__PURE__*/React.createElement("div", {
      className: "muted",
      style: {
        fontSize: 11
      }
    }, "quote unavailable"))), /*#__PURE__*/React.createElement("div", {
      className: "roll-choice"
    }, /*#__PURE__*/React.createElement("div", {
      className: "roll-choice-label"
    }, "Buy back · close"), /*#__PURE__*/React.createElement("div", {
      className: "roll-choice-strike"
    }, "—"), buyback != null ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      className: `roll-choice-credit ${buyback >= 0 ? "up" : "down"}`
    }, buyback >= 0 ? "+" : "", "$", buyback.toFixed(0)), /*#__PURE__*/React.createElement("div", {
      className: "muted",
      style: {
        fontSize: 10.5
      }
    }, "realize P/L")) : /*#__PURE__*/React.createElement("div", {
      className: "muted",
      style: {
        fontSize: 11
      }
    }, "—"))), (() => {
      const fourWeek = (() => {
        const d = new Date(sc.expiration + "T12:00:00");
        d.setDate(d.getDate() + 28);
        return d.toISOString().slice(0, 10);
      })();
      const fourWeekLabel = (() => {
        try {
          const d = new Date(fourWeek + "T12:00:00");
          return d.toLocaleDateString("en-US", {
            month: "short",
            day: "numeric"
          });
        } catch {
          return fourWeek;
        }
      })();
      const fwQuote = quotes[`${fourWeek}|${sc.strike}`];
      const oneWeekRoll = choices[0]; // same strike +1wk
      const qtyAbs = Math.abs(sc.qty);
      // Scenario 1: roll out 1 week same strike
      const sc1 = oneWeekRoll.available ? {
        label: "Roll +1 week",
        detail: `Roll same strike to ${oneWeekRoll.exp}. Buy back current, sell new. Net ${oneWeekRoll.netCredit >= 0 ? "credit" : "debit"} ${oneWeekRoll.netCredit >= 0 ? "+" : ""}$${oneWeekRoll.netCredit.toFixed(0)}.`,
        pnl: oneWeekRoll.netCredit,
        positive: oneWeekRoll.netCredit >= 0,
        available: true,
        reasoning: itm ? "ITM. Same-strike roll typically only works when next-week's premium exceeds the current intrinsic. Watch the credit closely." : "OTM. Standard 1-week roll. Adds another week of theta to the position."
      } : {
        label: "Roll +1 week",
        detail: "Quote unavailable",
        available: false
      };
      // Scenario 2: roll out 4 weeks same strike
      const sc2 = fwQuote && currentMid != null ? (() => {
        const netCreditPerShare = fwQuote.mid - currentMid;
        const netCredit = netCreditPerShare * qtyAbs * 100;
        return {
          label: "Roll +4 weeks",
          detail: `Roll same strike to ${fourWeekLabel}. Net ${netCredit >= 0 ? "credit" : "debit"} ${netCredit >= 0 ? "+" : ""}$${netCredit.toFixed(0)}. New delta ${fwQuote.delta != null ? fwQuote.delta.toFixed(2) : "—"}.`,
          pnl: netCredit,
          positive: netCredit >= 0,
          available: true,
          reasoning: "Longer DTE means more theta but also more time for price to keep moving against you. Consider only if you have conviction the stock pulls back."
        };
      })() : {
        label: "Roll +4 weeks",
        detail: "Quote unavailable",
        available: false
      };
      // Scenario 3: accept assignment at expiration
      // Per-share P/L if assigned: (strike - current price) * 100 + (entry credit) * 100
      // For a covered call, assignment means selling 100 shares at strike. The
      // "P/L" here is on the option itself (entry credit kept since you're called away),
      // ignoring stock cost basis since the user controls that elsewhere.
      const assignmentPL = (entryCredit + Math.max(0, sc.strike - live)) * qtyAbs * 100;
      // Wait: for a SHORT call, if the stock is at $X above strike at expiry,
      // you are called away at strike. You keep the entire entry credit. The
      // "lost upside" is (current price - strike) per share, but that is
      // your stock leg, not the option. The OPTION P/L on the short is
      // simply +entry_credit (you sold for entryCredit, expires worthless to you).
      const optionAssignmentPL = entryCredit * qtyAbs * 100;
      const lostUpside = itm ? (live - sc.strike) * qtyAbs * 100 : 0;
      const sc3 = {
        label: "Accept assignment",
        detail: itm ? `Stock called away at $${sc.strike.toFixed(2)} on ${sc.expiration}. Option P/L: +$${optionAssignmentPL.toFixed(0)} (full credit kept). Lost upside on shares: $${lostUpside.toFixed(0)} vs current price.` : `Currently OTM. If price stays below $${sc.strike.toFixed(2)} at expiration, the option expires worthless and you keep the full $${optionAssignmentPL.toFixed(0)} credit.`,
        pnl: optionAssignmentPL,
        positive: true,
        available: true,
        reasoning: itm ? "Acceptable if you wanted to exit the stock at this price anyway. Otherwise the lost upside cost may make rolling more attractive." : "Often the best outcome for OTM short calls. No additional action required."
      };
      // Scenario 4: close at current debit (buyback realized P/L)
      const sc4 = currentMid != null ? {
        label: "Close now",
        detail: `Buy back at $${currentMid.toFixed(2)} mid. Realized P/L $${buyback >= 0 ? "+" : ""}${buyback.toFixed(0)}.`,
        pnl: buyback,
        positive: buyback >= 0,
        available: true,
        reasoning: buyback >= 0 ? "Locks in profit. Frees the short for a fresh setup at a different strike or expiration." : "Locks in a loss. Only worth it when the trade thesis has clearly broken and rolling would compound the risk."
      } : {
        label: "Close now",
        detail: "Quote unavailable",
        available: false
      };
      const scenarios = [sc1, sc2, sc3, sc4];
      return /*#__PURE__*/React.createElement("div", {
        className: "roll-pl-section",
        title: "Side-by-side P/L modeling for four scenarios. Helps choose between roll, assignment, and close. P/L figures are per the underlying short call only — your stock leg P/L is separate."
      }, /*#__PURE__*/React.createElement("div", {
        className: "roll-pl-head"
      }, /*#__PURE__*/React.createElement("span", {
        className: "roll-pl-kicker"
      }, "Decision support · per contract option P/L")), /*#__PURE__*/React.createElement("div", {
        className: "roll-pl-grid"
      }, scenarios.map((s, idx) => /*#__PURE__*/React.createElement("div", {
        key: idx,
        className: `roll-pl-card ${!s.available ? "unavailable" : ""}`,
        title: s.reasoning || ""
      }, /*#__PURE__*/React.createElement("div", {
        className: "roll-pl-label"
      }, s.label), s.available ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
        className: `roll-pl-pnl ${s.positive ? "up" : "down"}`
      }, s.pnl >= 0 ? "+" : "", "$", s.pnl.toFixed(0)), /*#__PURE__*/React.createElement("div", {
        className: "roll-pl-detail"
      }, s.detail)) : /*#__PURE__*/React.createElement("div", {
        className: "muted",
        style: {
          fontSize: 11
        }
      }, s.detail)))));
    })());
  })));
}
function FlowScoreCard({
  ticker,
  currentPrice,
  apiFetch,
  uwHealth
}) {
  const [score, setScore] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  // Expand/collapse for the trade-by-trade flow list
  const [showFlow, setShowFlow] = useState(false);
  const [flowTrades, setFlowTrades] = useState(null);
  const [flowTradesLoading, setFlowTradesLoading] = useState(false);

  // Market-hours check — same logic the rest of the app uses informally.
  // 9:30am-4:00pm ET, Mon-Fri.
  const isMarketHours = () => {
    try {
      const nowET = new Date(new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false
      }).format(new Date()).replace(",", ""));
      const day = nowET.getDay(); // 0=Sun
      if (day === 0 || day === 6) return false;
      const h = nowET.getHours(),
        m = nowET.getMinutes();
      const minutes = h * 60 + m;
      return minutes >= 9 * 60 + 30 && minutes < 16 * 60;
    } catch {
      return false;
    }
  };

  // Latest price via ref so polling uses the current price without making
  // currentPrice a dependency (which double-fetched on every 5s quote tick).
  const scorePriceRef = React.useRef(currentPrice);
  scorePriceRef.current = currentPrice;
  useEffect(() => {
    if (!ticker || !uwHealth?.connected) return;
    let cancelled = false;
    const poll = async () => {
      try {
        setLoading(true);
        const url = `/api/uw/flow_score?symbol=${encodeURIComponent(ticker)}` + (scorePriceRef.current ? `&price=${scorePriceRef.current}` : "");
        const r = await apiFetch(url);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (cancelled) return;
        if (j.error) {
          setError(j.error);
        } else {
          setScore(j);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    poll();
    const intervalMs = isMarketHours() ? 10000 : 60000;
    const id = setInterval(skipWhenHidden(poll), intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [ticker, uwHealth?.connected]);

  // CRITICAL: when ticker changes, the cached flowTrades and score
  // belong to the OLD ticker. Clear them so the user never sees stale
  // data from a previous symbol. The score effect above will refetch;
  // flowTrades will refetch on next expand.
  useEffect(() => {
    setScore(null);
    setFlowTrades(null);
    setShowFlow(false);
    setError(null);
  }, [ticker]);

  // Hide entirely if UW not configured. No clutter for non-UW users.
  if (!uwHealth?.configured) return null;
  if (!uwHealth?.connected) {
    return /*#__PURE__*/React.createElement("div", {
      className: "card flow-score-card"
    }, /*#__PURE__*/React.createElement("div", {
      className: "card-head"
    }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
      className: "kicker"
    }, "Unusual Whales · real-time options flow"), /*#__PURE__*/React.createElement("div", {
      className: "card-title"
    }, "Flow Score"))), /*#__PURE__*/React.createElement("div", {
      className: "muted",
      style: {
        padding: "16px 0"
      }
    }, "UW connection error: ", uwHealth?.error || "unknown"));
  }
  if (!score && loading) {
    return /*#__PURE__*/React.createElement("div", {
      className: "card flow-score-card"
    }, /*#__PURE__*/React.createElement("div", {
      className: "card-head"
    }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
      className: "kicker"
    }, "Unusual Whales · real-time options flow"), /*#__PURE__*/React.createElement("div", {
      className: "card-title"
    }, "Flow Score"))), /*#__PURE__*/React.createElement("div", {
      className: "muted",
      style: {
        padding: "16px 0"
      }
    }, "Loading flow data."));
  }
  if (!score) return null;
  if (!score.data_available) {
    return /*#__PURE__*/React.createElement("div", {
      className: "card flow-score-card"
    }, /*#__PURE__*/React.createElement("div", {
      className: "card-head"
    }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
      className: "kicker"
    }, "Unusual Whales · real-time options flow"), /*#__PURE__*/React.createElement("div", {
      className: "card-title"
    }, "Flow Score · ", ticker))), /*#__PURE__*/React.createElement("div", {
      className: "muted",
      style: {
        padding: "16px 0"
      }
    }, score.reason || "No unusual flow detected for this ticker today."));
  }
  const overallCls = score.overall >= 65 ? "up" : score.overall <= 35 ? "down" : "";

  // Sub-score bar component
  const SubBar = ({
    label,
    value,
    tone,
    tip
  }) => {
    const cls = tone === "good" ? "sub-good" : tone === "bad" ? "sub-bad" : tone === "neutral" ? "sub-neutral" : "sub-default";
    return /*#__PURE__*/React.createElement("div", {
      className: "flow-sub",
      title: tip
    }, /*#__PURE__*/React.createElement("div", {
      className: "flow-sub-head"
    }, /*#__PURE__*/React.createElement("span", {
      className: "flow-sub-lbl"
    }, label), /*#__PURE__*/React.createElement("span", {
      className: "flow-sub-val"
    }, value)), /*#__PURE__*/React.createElement("div", {
      className: "flow-sub-bar"
    }, /*#__PURE__*/React.createElement("div", {
      className: `flow-sub-fill ${cls}`,
      style: {
        width: value + "%"
      }
    })));
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "card flow-score-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Unusual Whales · real-time options flow"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Flow Score · ", ticker)), /*#__PURE__*/React.createElement("div", {
    className: "kicker",
    title: `${score.stats.alert_count} unusual flow alerts in today's session`
  }, score.stats.alert_count, " alerts today")), /*#__PURE__*/React.createElement("div", {
    className: `flow-verdict ${score.verdict_class}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "flow-verdict-label",
    title: "UW decision-engine verdict for selling covered calls right now. Overrides standard verdict when bullish flow ≥ 70 AND CC Risk ≥ 70."
  }, "UW VERDICT"), /*#__PURE__*/React.createElement("div", {
    className: "flow-verdict-text"
  }, score.verdict), /*#__PURE__*/React.createElement("div", {
    className: "flow-verdict-reason"
  }, score.reason)), /*#__PURE__*/React.createElement("div", {
    className: "flow-overall"
  }, /*#__PURE__*/React.createElement("div", {
    className: "flow-overall-circle",
    title: "Overall flow score from 0 to 100. 50 = neutral. Above 50 = bullish flow lean. Below 50 = bearish flow lean. Quality-weighted: noisy flow tilts back toward 50."
  }, /*#__PURE__*/React.createElement("div", {
    className: `flow-overall-num ${overallCls}`
  }, score.overall), /*#__PURE__*/React.createElement("div", {
    className: "flow-overall-cap"
  }, "OVERALL")), /*#__PURE__*/React.createElement("div", {
    className: "flow-overall-stats"
  }, /*#__PURE__*/React.createElement("div", {
    className: "flow-stat-row",
    title: "Total call premium traded today across all unusual flow alerts"
  }, /*#__PURE__*/React.createElement("span", {
    className: "flow-stat-lbl"
  }, "Call premium"), /*#__PURE__*/React.createElement("span", {
    className: "flow-stat-val up"
  }, fmt$M(score.stats.total_call_premium))), /*#__PURE__*/React.createElement("div", {
    className: "flow-stat-row",
    title: "Total put premium traded today across all unusual flow alerts"
  }, /*#__PURE__*/React.createElement("span", {
    className: "flow-stat-lbl"
  }, "Put premium"), /*#__PURE__*/React.createElement("span", {
    className: "flow-stat-val down"
  }, fmt$M(score.stats.total_put_premium))), /*#__PURE__*/React.createElement("div", {
    className: "flow-stat-row",
    title: "Ask-side call premium specifically targeting strikes at or above current price — the dangerous zone for covered-call writers"
  }, /*#__PURE__*/React.createElement("span", {
    className: "flow-stat-lbl"
  }, "Above strike (calls)"), /*#__PURE__*/React.createElement("span", {
    className: "flow-stat-val"
  }, fmt$M(score.stats.call_above_strike_premium))), /*#__PURE__*/React.createElement("div", {
    className: "flow-stat-row",
    title: "Number of sweep orders detected. Sweeps are aggressive, multi-exchange ask-side fills — typically institutional."
  }, /*#__PURE__*/React.createElement("span", {
    className: "flow-stat-lbl"
  }, "Sweeps (call/put)"), /*#__PURE__*/React.createElement("span", {
    className: "flow-stat-val"
  }, score.stats.call_sweeps, "/", score.stats.put_sweeps)))), /*#__PURE__*/React.createElement("div", {
    className: "flow-subs"
  }, /*#__PURE__*/React.createElement(SubBar, {
    label: "Bullish flow",
    value: score.bullish,
    tone: score.bullish >= 70 ? "good" : score.bullish >= 50 ? "neutral" : "default",
    tip: "0-100. Driven by ask-side call premium share, call sweep concentration, and total bullish premium magnitude. Higher = more aggressive bullish flow."
  }), /*#__PURE__*/React.createElement(SubBar, {
    label: "Bearish flow",
    value: score.bearish,
    tone: score.bearish >= 70 ? "bad" : score.bearish >= 50 ? "neutral" : "default",
    tip: "0-100. Mirror of bullish. Driven by ask-side put premium share, put sweeps, and total bearish premium. Higher = more aggressive downside positioning."
  }), /*#__PURE__*/React.createElement(SubBar, {
    label: "Flow quality",
    value: score.quality,
    tone: score.quality >= 70 ? "good" : score.quality >= 40 ? "neutral" : "default",
    tip: "0-100. Conviction of the flow. Total premium magnitude, sweep prevalence, and number of distinct alerts. Low quality = noise that should be ignored."
  }), /*#__PURE__*/React.createElement(SubBar, {
    label: "CC risk",
    value: score.cc_risk,
    tone: score.cc_risk >= 70 ? "bad" : score.cc_risk >= 50 ? "neutral" : "default",
    tip: "0-100. Risk that selling covered calls right now leads to fast assignment. Driven by ask-side call premium concentrated AT or ABOVE current price. ≥70 means aggressive bullish flow is targeting your potential strike zone."
  })), /*#__PURE__*/React.createElement("div", {
    className: "flow-trades-section"
  }, /*#__PURE__*/React.createElement("button", {
    className: "flow-trades-toggle",
    onClick: async () => {
      const next = !showFlow;
      setShowFlow(next);
      if (next && !flowTrades) {
        try {
          setFlowTradesLoading(true);
          const r = await apiFetch(`/api/uw/flow_trades?symbol=${encodeURIComponent(ticker)}&limit=50`);
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          const j = await r.json();
          setFlowTrades(j.data || []);
        } catch (_) {
          setFlowTrades([]);
        } finally {
          setFlowTradesLoading(false);
        }
      }
    },
    title: "Show or hide the trade-by-trade flow list. Each row is one unusual options trade detected by Unusual Whales today."
  }, showFlow ? "▾" : "▸", " ", showFlow ? "Hide" : "Show", " flow trades (", score.stats.alert_count, ")"), showFlow && /*#__PURE__*/React.createElement("div", {
    className: "flow-trades-list"
  }, flowTradesLoading && (!flowTrades || flowTrades.length === 0) && /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      padding: "10px 0"
    }
  }, "Loading trades."), flowTrades && flowTrades.length === 0 && !flowTradesLoading && /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      padding: "10px 0"
    }
  }, "No trades returned."), flowTrades && flowTrades.length > 0 && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "flow-trades-head",
    title: "Each row is one unusual options trade today. Sort is most-recent-first (UW default)."
  }, /*#__PURE__*/React.createElement("span", {
    title: "Time of execution"
  }, "Time"), /*#__PURE__*/React.createElement("span", {
    title: "Call or put"
  }, "Side"), /*#__PURE__*/React.createElement("span", {
    title: "Strike price"
  }, "Strike"), /*#__PURE__*/React.createElement("span", {
    title: "Expiration date"
  }, "Exp"), /*#__PURE__*/React.createElement("span", {
    title: "Trade size in contracts"
  }, "Size"), /*#__PURE__*/React.createElement("span", {
    title: "Total premium paid (size × price × 100)"
  }, "Premium"), /*#__PURE__*/React.createElement("span", {
    title: "IV at the contract"
  }, "IV"), /*#__PURE__*/React.createElement("span", {
    title: "Where the trade printed: ask = aggressive buyer, bid = aggressive seller, mid = uncertain"
  }, "Side fill"), /*#__PURE__*/React.createElement("span", {
    title: "Sentiment: bullish = ask-side calls or bid-side puts; bearish = ask-side puts or bid-side calls"
  }, "Bias"), /*#__PURE__*/React.createElement("span", {
    title: "S = sweep (multi-exchange aggressive fill, usually institutional)"
  }, "Flag")), flowTrades.map((t, i) => {
    const fmtTs = ts => {
      if (!ts) return "—";
      try {
        const d = new Date(ts);
        return new Intl.DateTimeFormat("en-US", {
          timeZone: "America/New_York",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false
        }).format(d);
      } catch {
        return "—";
      }
    };
    const sideCls = t.side === "call" ? "side-call" : t.side === "put" ? "side-put" : "";
    const fillCls = t.side_label === "ask" ? "fill-ask" : t.side_label === "bid" ? "fill-bid" : "fill-mid";
    const biasCls = t.sentiment === "bullish" ? "bias-bull" : t.sentiment === "bearish" ? "bias-bear" : "bias-neutral";
    return /*#__PURE__*/React.createElement("div", {
      key: i,
      className: "flow-trade-row"
    }, /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, fmtTs(t.ts)), /*#__PURE__*/React.createElement("span", {
      className: sideCls
    }, t.side?.toUpperCase()), /*#__PURE__*/React.createElement("span", null, t.strike != null ? "$" + t.strike.toFixed(2) : "—"), /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, t.expiry || "—"), /*#__PURE__*/React.createElement("span", null, t.size != null ? t.size.toLocaleString() : "—"), /*#__PURE__*/React.createElement("span", {
      className: "num-strong"
    }, fmt$M(t.premium)), /*#__PURE__*/React.createElement("span", null, t.iv != null ? (t.iv * 100).toFixed(0) + "%" : "—"), /*#__PURE__*/React.createElement("span", {
      className: fillCls
    }, t.side_label), /*#__PURE__*/React.createElement("span", {
      className: biasCls
    }, t.sentiment), /*#__PURE__*/React.createElement("span", null, t.is_sweep ? /*#__PURE__*/React.createElement("span", {
      className: "sweep-flag",
      title: "Sweep — aggressive multi-exchange fill, typically institutional"
    }, "S") : ""));
  })))));
}
function PullbackBacktest({
  ticker,
  direction,
  defaultTarget,
  apiFetch
}) {
  const isShort = direction === "short";
  const PB_BACKTEST_KEY = "weeklyOptionsTimer.pullbackBacktest.v1";
  const persisted = (() => {
    try {
      const raw = localStorage.getItem(PB_BACKTEST_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  })();
  // Inputs persist so they don't reset every time the user switches tickers
  const [targetStr, setTargetStr] = useState(() => {
    if (persisted?.target != null) return String(persisted.target);
    if (defaultTarget != null) return defaultTarget.toFixed(2);
    return "1.00";
  });
  const [minGapStr, setMinGapStr] = useState(() => persisted?.minGap != null ? String(persisted.minGap) : "0");
  const [days, setDays] = useState(() => persisted?.days || 180);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    try {
      localStorage.setItem(PB_BACKTEST_KEY, JSON.stringify({
        target: parseFloat(targetStr) || null,
        minGap: parseFloat(minGapStr) || 0,
        days: days
      }));
    } catch {}
  }, [targetStr, minGapStr, days]);
  const runBacktest = async () => {
    const tgt = parseFloat(targetStr);
    if (!isFinite(tgt) || tgt <= 0) {
      setError("Enter a valid target percentage greater than 0.");
      return;
    }
    const gap = parseFloat(minGapStr) || 0;
    setLoading(true);
    setError(null);
    try {
      const url = `/api/pullback_backtest?symbol=${encodeURIComponent(ticker)}` + `&direction=${direction}&target=${tgt}&min_gap=${gap}&days=${days}`;
      const r = await apiFetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      if (j.error) {
        setError(j.error);
        setResult(null);
      } else {
        setResult(j);
        setError(null);
      }
    } catch (e) {
      setError(String(e));
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  // Re-run when direction changes — different math, different sample
  useEffect(() => {
    setResult(null);
  }, [direction, ticker]);
  const hitRateColor = result?.hit_rate != null ? result.hit_rate >= 70 ? "up" : result.hit_rate >= 50 ? "" : "down" : "";
  return /*#__PURE__*/React.createElement("div", {
    className: "pullback-backtest"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-backtest-title",
    title: "Run a custom hit-rate test against the historical bars. Asks: how often did the stock pull back (or pop) at least your target percent from the open?"
  }, "Custom backtest"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-backtest-controls"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pbb-field"
  }, /*#__PURE__*/React.createElement("label", {
    className: "pbb-label",
    title: isShort ? "Pullback target as a percentage. E.g. 1.50 means count days where the stock dropped at least 1.50% below the open at some point." : "Pop target as a percentage. E.g. 1.50 means count days where the stock rose at least 1.50% above the open at some point."
  }, isShort ? "Pullback target %" : "Pop target %"), /*#__PURE__*/React.createElement("input", {
    className: "pbb-input",
    type: "text",
    inputMode: "decimal",
    value: targetStr,
    onChange: e => setTargetStr(e.target.value),
    placeholder: "1.00"
  })), /*#__PURE__*/React.createElement("div", {
    className: "pbb-field"
  }, /*#__PURE__*/React.createElement("label", {
    className: "pbb-label",
    title: isShort ? "Only count days where today's open gapped UP at least this percent from prior close. 0 = include all days." : "Only count days where today's open gapped at least this percent (up or down) from prior close. 0 = include all days."
  }, "Min gap %"), /*#__PURE__*/React.createElement("input", {
    className: "pbb-input",
    type: "text",
    inputMode: "decimal",
    value: minGapStr,
    onChange: e => setMinGapStr(e.target.value),
    placeholder: "0"
  })), /*#__PURE__*/React.createElement("div", {
    className: "pbb-field"
  }, /*#__PURE__*/React.createElement("label", {
    className: "pbb-label",
    title: "How many trading days back to test against. Default 180."
  }, "Days history"), /*#__PURE__*/React.createElement("input", {
    className: "pbb-input",
    type: "number",
    min: "5",
    max: "500",
    value: days,
    onChange: e => setDays(parseInt(e.target.value || "180", 10)),
    placeholder: "180"
  })), /*#__PURE__*/React.createElement("button", {
    className: "pbb-run",
    onClick: runBacktest,
    disabled: loading,
    title: "Run the backtest with the values above"
  }, loading ? "Running." : "Run")), error && /*#__PURE__*/React.createElement("div", {
    className: "research-error",
    style: {
      marginTop: 8
    }
  }, "Error: ", error), result && !error && /*#__PURE__*/React.createElement("div", {
    className: "pullback-backtest-result"
  }, result.qualified_days === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      padding: "8px 0"
    }
  }, "No qualifying days in the lookback. Try lowering the min gap filter.") : /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "pbb-meta",
    title: "The lookback length the server actually used and how many days passed your filters"
  }, /*#__PURE__*/React.createElement("span", null, "Tested ", /*#__PURE__*/React.createElement("b", null, result.samples), " bars over the last ", result.lookback_days, " days. ", /*#__PURE__*/React.createElement("b", null, result.qualified_days), " met your filters."), result.qualified_days < 20 && /*#__PURE__*/React.createElement("span", {
    className: "pbb-warning",
    title: "Small sample. Hit rate is highly sensitive to one or two outlier days."
  }, "Small sample. Treat as directional, not statistical.")), /*#__PURE__*/React.createElement("div", {
    className: "pbb-headline"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pbb-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pbb-stat-label",
    title: "Percent of qualifying days where the target was reached intraday"
  }, "Hit rate"), /*#__PURE__*/React.createElement("div", {
    className: `pbb-stat-val ${hitRateColor}`
  }, result.hit_rate, "%")), /*#__PURE__*/React.createElement("div", {
    className: "pbb-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pbb-stat-label",
    title: "Days that met the gap filter and were tested"
  }, "Qualified days"), /*#__PURE__*/React.createElement("div", {
    className: "pbb-stat-val"
  }, result.qualified_days)), /*#__PURE__*/React.createElement("div", {
    className: "pbb-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pbb-stat-label",
    title: "Days where the target was reached intraday"
  }, "Hits"), /*#__PURE__*/React.createElement("div", {
    className: "pbb-stat-val up"
  }, result.hits)), /*#__PURE__*/React.createElement("div", {
    className: "pbb-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pbb-stat-label",
    title: "Days where the target was NOT reached"
  }, "Misses"), /*#__PURE__*/React.createElement("div", {
    className: "pbb-stat-val down"
  }, result.misses))), /*#__PURE__*/React.createElement("div", {
    className: "pbb-secondary"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pbb-sec-row"
  }, /*#__PURE__*/React.createElement("span", {
    title: isShort ? "Average pullback size on hit days. Tells you whether hit days typically stretched well past the target or barely tagged it." : "Average pop size on hit days. Tells you whether hit days typically stretched well past the target or barely tagged it."
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--fg-3)"
    }
  }, "Avg hit size"), " ", /*#__PURE__*/React.createElement("b", null, result.avg_win_size != null ? result.avg_win_size.toFixed(2) + "%" : "—")), /*#__PURE__*/React.createElement("span", {
    title: isShort ? "Largest single-day pullback in the hit set" : "Largest single-day pop in the hit set"
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--fg-3)"
    }
  }, "Max hit"), " ", /*#__PURE__*/React.createElement("b", null, result.max_win_size != null ? result.max_win_size.toFixed(2) + "%" : "—")), /*#__PURE__*/React.createElement("span", {
    title: isShort ? "Average pullback size on miss days. If close to the target, raising stop tolerance helps. If far, the target is unrealistic." : "Average pop size on miss days. If close to the target, raising stop tolerance helps. If far, the target is unrealistic."
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--fg-3)"
    }
  }, "Avg miss"), " ", /*#__PURE__*/React.createElement("b", null, result.avg_miss_size != null ? result.avg_miss_size.toFixed(2) + "%" : "—")), /*#__PURE__*/React.createElement("span", {
    title: isShort ? "How close the closest miss got to the pullback target" : "How close the closest miss got to the pop target"
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--fg-3)"
    }
  }, "Closest miss"), " ", /*#__PURE__*/React.createElement("b", null, result.max_miss_size != null ? result.max_miss_size.toFixed(2) + "%" : "—")))), result.recent && result.recent.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "pbb-timeline",
    title: "Most recent qualifying days. Green = target hit, red = target missed. Each cell shows the actual move that day."
  }, /*#__PURE__*/React.createElement("div", {
    className: "pbb-timeline-label"
  }, "Recent ", result.recent.length, " qualifying days (oldest → newest)"), /*#__PURE__*/React.createElement("div", {
    className: "pbb-timeline-bar"
  }, result.recent.map((d, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: `pbb-day ${d.hit ? "hit" : "miss"}`,
    title: `${d.date} · gap ${d.gap_pct >= 0 ? "+" : ""}${d.gap_pct}% · ${isShort ? "pullback" : "pop"} ${d.move_pct.toFixed(2)}% · ${d.hit ? "HIT" : "miss"}`
  })))), result.weekday_breakdown && result.weekday_breakdown.some(w => w.n > 0) && (() => {
    // Best weekday call-out for the headline. Need ≥3 samples
    // to even consider it; otherwise the rate is meaningless.
    const eligible = result.weekday_breakdown.filter(w => w.n >= 3);
    let best = null;
    if (eligible.length > 0) {
      best = eligible.reduce((a, b) => b.hit_rate > (a?.hit_rate ?? -1) ? b : a, null);
    }
    const overallRate = result.hit_rate;
    return /*#__PURE__*/React.createElement("div", {
      className: "pbb-weekday",
      title: "Same backtest split by day of the week. Helps spot day-of-week patterns. Hover any cell for detail."
    }, /*#__PURE__*/React.createElement("div", {
      className: "pbb-weekday-title"
    }, "Day-of-week breakdown", best && best.hit_rate > overallRate && /*#__PURE__*/React.createElement("span", {
      className: "pbb-weekday-callout",
      title: `${best.weekday}s have a higher hit rate than the overall sample. ${best.hits}/${best.n} = ${best.hit_rate}%`
    }, " · ", /*#__PURE__*/React.createElement("b", null, best.weekday, "s"), " lead at ", /*#__PURE__*/React.createElement("b", null, best.hit_rate, "%"))), /*#__PURE__*/React.createElement("div", {
      className: "pbb-weekday-grid"
    }, result.weekday_breakdown.map(w => {
      const isLowSample = w.n > 0 && w.n < 5;
      const isBest = best && best.weekday === w.weekday && best.hit_rate > overallRate;
      const cls = w.n === 0 ? "empty" : w.hit_rate >= 70 ? "good" : w.hit_rate >= 50 ? "ok" : "weak";
      return /*#__PURE__*/React.createElement("div", {
        key: w.weekday,
        className: `pbb-wd ${cls}${isBest ? " is-best" : ""}`,
        title: w.n === 0 ? `${w.weekday}: no qualifying days in this lookback` : `${w.weekday}: ${w.hits} hits / ${w.n} samples = ${w.hit_rate}%. Avg ${isShort ? "pullback" : "pop"} ${w.avg_move}%${isLowSample ? ". Low sample, treat as directional." : ""}`
      }, /*#__PURE__*/React.createElement("div", {
        className: "pbb-wd-day"
      }, w.weekday), /*#__PURE__*/React.createElement("div", {
        className: "pbb-wd-rate"
      }, w.n === 0 ? "—" : w.hit_rate + "%"), /*#__PURE__*/React.createElement("div", {
        className: "pbb-wd-sub"
      }, w.n === 0 ? "no data" : `${w.hits}/${w.n}${isLowSample ? " · small" : ""}`));
    })));
  })())));
}
function TradeBuilderCard({
  ticker,
  currentPrice,
  callAtSug,
  putAtSug,
  FRONT_DTE,
  activeExpDate,
  expHigh,
  expLow,
  analystData,
  rec,
  callSafePct,
  putSafePct,
  apiFetch,
  strategyMode
}) {
  // Cross-expiration data state. Lazy-fetched only when the user
  // clicks "Compare across expirations" — multi-expiration chain
  // loading takes 3-10 seconds on a fresh ticker so we don't want
  // it firing automatically on every ticker switch.
  const [multiExp, setMultiExp] = useState(null);
  const [multiExpLoading, setMultiExpLoading] = useState(false);
  const [multiExpError, setMultiExpError] = useState(null);
  const [multiExpExpanded, setMultiExpExpanded] = useState(false);

  // Reset the cross-exp state when ticker changes — same pattern as
  // the analyst card. Avoids showing AAPL data after switching to NVDA.
  useEffect(() => {
    setMultiExp(null);
    setMultiExpError(null);
    setMultiExpExpanded(false);
  }, [ticker]);
  const fetchMultiExp = async () => {
    if (!apiFetch || multiExpLoading) return;
    setMultiExpLoading(true);
    setMultiExpError(null);
    try {
      const r = await apiFetch(`/api/trade_builder/multi_exp?symbol=${encodeURIComponent(ticker)}&max_weeks=8`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      if (j.error) {
        setMultiExpError(j.error);
        setMultiExp(null);
      } else {
        setMultiExp(j);
      }
    } catch (e) {
      setMultiExpError(String(e));
    } finally {
      setMultiExpLoading(false);
    }
  };
  // Mid prices — same logic as the rest of the dashboard
  const mid = q => q && q.bid > 0 ? q.bid : q && (q.bid + q.ask) / 2 || q && q.last || 0;
  const callMid = mid(callAtSug);
  const putMid = mid(putAtSug);

  // Math for the call-side trade (selling covered calls)
  const callStrike = callAtSug?.strike || 0;
  const callDelta = Math.abs(callAtSug?.delta ?? 0.20);
  const callBreakeven = callStrike + callMid; // assignment-adjusted breakeven
  const callPctOfStock = currentPrice > 0 ? callMid / currentPrice * 100 : 0;
  const callAnnualizedPct = FRONT_DTE > 0 && currentPrice > 0 ? callMid / currentPrice * (365 / FRONT_DTE) * 100 : 0;
  // Probability of profit: stock stays below strike at expiration.
  // Approximation: 1 - delta. (Standard short-options PoP heuristic.)
  const callPoP = (1 - callDelta) * 100;
  // Max profit if stock stays below strike: just the premium collected
  const callMaxProfit = callMid * 100; // per contract
  // Max upside if assigned: gain to strike + premium
  const callMaxUpsideIfAssigned = (callStrike - currentPrice + callMid) * 100;

  // Math for the put-side trade (selling cash-secured puts)
  const putStrike = putAtSug?.strike || 0;
  const putDelta = Math.abs(putAtSug?.delta ?? 0.20);
  const putBreakeven = putStrike - putMid; // effective cost basis if assigned
  const putPctOfStock = currentPrice > 0 ? putMid / currentPrice * 100 : 0;
  const putAnnualizedPct = FRONT_DTE > 0 && putStrike > 0 ? putMid / putStrike * (365 / FRONT_DTE) * 100 : 0;
  const putPoP = (1 - putDelta) * 100;
  const putMaxProfit = putMid * 100;
  const putCapitalRequired = putStrike * 100; // 1 contract = 100 shares
  const putBreakevenDiscount = currentPrice > 0 ? (currentPrice - putBreakeven) / currentPrice * 100 : 0;

  // Earnings proximity flag — earnings within FRONT_DTE means the
  // expiration straddles an earnings event, which spikes IV and risk.
  // We don't have earnings date in scope here, but the recommendation
  // engine already factored it in via rec.kind. We surface a simple
  // proxy: if rec.kind is "danger" for analyst reasons OR FRONT_DTE > 35
  // (long-dated traditional weekly), flag accordingly.

  // Score each strategy
  const aVerdict = analystData?.verdict || {};
  const aTargets = analystData?.targets || {};
  let callScore = 0;
  const callReasons = [];
  if (rec?.kind === "success") {
    callScore += 30;
    callReasons.push("Favorable timing per recommendation");
  } else if (rec?.kind === "danger") {
    callScore -= 50;
    callReasons.push("Recommendation flagged as caution");
  }
  if (aVerdict.fresh_downgrade) {
    callScore += 20;
    callReasons.push("Fresh downgrade: bearish backdrop favors short calls");
  }
  if (aVerdict.fresh_upgrade) {
    callScore -= 30;
    callReasons.push("Fresh upgrade: re-rating risk for short calls");
  }
  if (aTargets.upside_pct != null && aTargets.upside_pct < 0) {
    callScore += 15;
    callReasons.push("Trading above avg target: upside priced in");
  }
  if (aTargets.upside_to_high_pct != null && aTargets.upside_to_high_pct < -5) {
    callScore += 15;
    callReasons.push("Above highest target: mean-reversion likely");
  }
  if (callPoP > 70) {
    callScore += 20;
    callReasons.push(`PoP ${callPoP.toFixed(0)}%`);
  }
  if (callAnnualizedPct > 25) {
    callScore += 10;
    callReasons.push(`Annualized ${callAnnualizedPct.toFixed(0)}%`);
  }
  if (callPctOfStock > 1.0) {
    callScore += 10;
    callReasons.push(`Premium ${callPctOfStock.toFixed(2)}% of stock`);
  }
  let putScore = 0;
  const putReasons = [];
  if (rec?.kind === "info") {
    putScore += 30;
    putReasons.push("Recommendation suggests waiting on calls — put side may be live");
  } else if (rec?.kind === "success") {
    putScore += 10;
    putReasons.push("Favorable timing");
  }
  if (aVerdict.fresh_upgrade) {
    putScore += 25;
    putReasons.push("Fresh upgrade: bullish catalyst supports put strike");
  }
  if (aVerdict.fresh_downgrade) {
    putScore -= 50;
    putReasons.push("Fresh downgrade: dropping price = high assignment risk");
  }
  if (aTargets.upside_pct != null && aTargets.upside_pct > 15) {
    putScore += 20;
    putReasons.push("Significant analyst upside: bullish backdrop");
  }
  if (aTargets.upside_to_high_pct != null && aTargets.upside_to_high_pct < -5) {
    putScore -= 25;
    putReasons.push("Above highest target: drop into put strike risk");
  }
  if (putPoP > 70) {
    putScore += 20;
    putReasons.push(`PoP ${putPoP.toFixed(0)}%`);
  }
  if (putAnnualizedPct > 25) {
    putScore += 10;
    putReasons.push(`Annualized ${putAnnualizedPct.toFixed(0)}%`);
  }
  if (putPctOfStock > 1.0) {
    putScore += 10;
    putReasons.push(`Premium ${putPctOfStock.toFixed(2)}% of strike`);
  }

  // Front-runner pick — Phase C follow-up (v1.13). Honors strategyMode:
  //   "cc"   → only the call is eligible. Put score ignored even if higher.
  //   "csp"  → only the put is eligible. Call score ignored even if higher.
  //   "both" → existing logic, picks the higher of the two if both pass.
  const _mode = strategyMode || "both";
  const _ccEligible = _mode === "both" || _mode === "cc";
  const _cspEligible = _mode === "both" || _mode === "csp";
  let frontRunner = null;
  if (_ccEligible && callScore >= 30 && (!_cspEligible || callScore > putScore)) {
    frontRunner = {
      side: "call",
      score: callScore,
      reasons: callReasons,
      label: "Sell the covered call",
      detail: `Sell the $${callStrike.toFixed(2)} call expiring ${activeExpDate.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric"
      })} for $${callMid.toFixed(2)} (delta ${callDelta.toFixed(2)}, PoP ${callPoP.toFixed(0)}%, $${callMaxProfit.toFixed(0)} per contract).`
    };
  } else if (_cspEligible && putScore >= 30 && (!_ccEligible || putScore > callScore)) {
    frontRunner = {
      side: "put",
      score: putScore,
      reasons: putReasons,
      label: "Sell the cash-secured put",
      detail: `Sell the $${putStrike.toFixed(2)} put expiring ${activeExpDate.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric"
      })} for $${putMid.toFixed(2)} (delta ${putDelta.toFixed(2)}, PoP ${putPoP.toFixed(0)}%, $${putMaxProfit.toFixed(0)} per contract). Effective cost basis if assigned: $${putBreakeven.toFixed(2)}.`
    };
  } else if (_ccEligible && _cspEligible && callScore >= 30 && putScore >= 30) {
    // Both pass threshold and tied on score — pick whichever has higher annualized.
    if (callAnnualizedPct > putAnnualizedPct) {
      frontRunner = {
        side: "call",
        score: callScore,
        reasons: callReasons,
        label: "Sell the covered call",
        detail: `Both strategies scored, call has higher annualized return. Sell the $${callStrike.toFixed(2)} call for $${callMid.toFixed(2)}.`
      };
    } else {
      frontRunner = {
        side: "put",
        score: putScore,
        reasons: putReasons,
        label: "Sell the cash-secured put",
        detail: `Both strategies scored, put has higher annualized return. Sell the $${putStrike.toFixed(2)} put for $${putMid.toFixed(2)}.`
      };
    }
  }

  // No-trade verdict if both score below threshold
  const noTrade = !frontRunner;
  return /*#__PURE__*/React.createElement(CardErrorBoundary, {
    label: "Trade Builder"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card trade-builder-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Decision engine · 0.20 delta strikes · ", activeExpDate.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric"
  }), " (", FRONT_DTE, "d)"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Trade Builder")), /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 11,
      textAlign: "right"
    }
  }, "$", currentPrice.toFixed(2), " live", /*#__PURE__*/React.createElement("br", null), "Score basis: rec + analyst + PoP + return")), frontRunner && /*#__PURE__*/React.createElement("div", {
    className: `trade-front-runner trade-front-runner-${frontRunner.side}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "tfr-header"
  }, /*#__PURE__*/React.createElement("span", {
    className: "tfr-label"
  }, "Front-runner"), /*#__PURE__*/React.createElement("span", {
    className: "tfr-score",
    title: "Composite score from recommendation, analyst signals, PoP, and annualized return."
  }, "Score: ", frontRunner.score)), /*#__PURE__*/React.createElement("div", {
    className: "tfr-action"
  }, frontRunner.label), /*#__PURE__*/React.createElement("div", {
    className: "tfr-detail"
  }, frontRunner.detail), frontRunner.reasons.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "tfr-reasons"
  }, frontRunner.reasons.map((r, i) => /*#__PURE__*/React.createElement("span", {
    key: i,
    className: "tfr-reason-pill"
  }, r)))), noTrade && /*#__PURE__*/React.createElement("div", {
    className: "trade-no-trade"
  }, /*#__PURE__*/React.createElement("div", {
    className: "tnt-label"
  }, "No clear trade today"), /*#__PURE__*/React.createElement("div", {
    className: "tnt-detail"
  }, _mode === "cc" && `The call side did not score above threshold for ${ticker} at the current price and expiration. Premium, probability, and analyst backdrop are not combining into a strong CC signal here. Wait for a better setup or look at a different expiration.`, _mode === "csp" && `The put side did not score above threshold for ${ticker} at the current price and expiration. Premium, probability, and analyst backdrop are not combining into a strong CSP signal here. Wait for a better setup or look at a different expiration.`, _mode === "both" && `Neither strategy scored above threshold for ${ticker} at the current price and expiration. The premium, probability, and analyst backdrop do not combine into a strong signal. Wait for a better setup or look at a different expiration.`), /*#__PURE__*/React.createElement("div", {
    className: "tnt-scores"
  }, _ccEligible && /*#__PURE__*/React.createElement("span", {
    title: "Composite score for selling covered calls. Below 30 = no signal."
  }, "Call: ", callScore), _cspEligible && /*#__PURE__*/React.createElement("span", {
    title: "Composite score for selling cash-secured puts. Below 30 = no signal."
  }, "Put: ", putScore))), /*#__PURE__*/React.createElement("div", {
    className: `trade-builder-compare${_mode !== "both" ? " single" : ""}`
  }, _ccEligible && /*#__PURE__*/React.createElement("div", {
    className: `trade-side trade-side-call${frontRunner?.side === "call" ? " is-front" : ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "trade-side-head"
  }, /*#__PURE__*/React.createElement("div", {
    className: "trade-side-title"
  }, "Sell covered call"), /*#__PURE__*/React.createElement("div", {
    className: "trade-side-score",
    title: "Composite score: rec timing + analyst overlay + PoP + annualized return + premium."
  }, callScore)), /*#__PURE__*/React.createElement("div", {
    className: "trade-side-strike",
    title: "Strike picked by the dashboard's 0.20-delta target. The call you'd sell."
  }, "$", callStrike.toFixed(2), " ", /*#__PURE__*/React.createElement("span", {
    className: "trade-side-strike-meta"
  }, ((callStrike / currentPrice - 1) * 100).toFixed(1), "% OTM · ", callDelta.toFixed(2), "Δ")), /*#__PURE__*/React.createElement("div", {
    className: "trade-side-rows"
  }, /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Premium you collect per share. Multiply by 100 for per-contract dollar amount."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "Premium"), /*#__PURE__*/React.createElement("span", {
    className: "trade-val"
  }, "$", callMid.toFixed(2), " ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "/ $", callMaxProfit.toFixed(0), " per contract"))), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Premium as percentage of current stock price. Quick read on richness for this strike."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "% of stock"), /*#__PURE__*/React.createElement("span", {
    className: "trade-val"
  }, callPctOfStock.toFixed(2), "%")), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Annualized return assuming you collect this premium over the holding period and roll continuously. NOT a guarantee — assumes no early assignment, no IV expansion."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "Annualized"), /*#__PURE__*/React.createElement("span", {
    className: `trade-val ${callAnnualizedPct > 25 ? "up" : ""}`
  }, callAnnualizedPct.toFixed(1), "%")), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Probability the option expires worthless (you keep all premium). Approximation: 1 - |delta|. Real-world PoP also depends on IV and time decay."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "PoP"), /*#__PURE__*/React.createElement("span", {
    className: `trade-val ${callPoP > 70 ? "up" : ""}`
  }, callPoP.toFixed(0), "%")), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Stock price at which the trade breaks even on assignment. = strike + premium collected. Above this, you start losing on the underlying."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "Breakeven"), /*#__PURE__*/React.createElement("span", {
    className: "trade-val"
  }, "$", callBreakeven.toFixed(2))), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Maximum upside if the stock rises to your strike and gets called away. = (strike - current price) × 100 + premium. Past the strike, you give up further upside."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "Max if assigned"), /*#__PURE__*/React.createElement("span", {
    className: "trade-val"
  }, "$", callMaxUpsideIfAssigned.toFixed(0))), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Historical probability that the weekly high stayed below this strike, measured against the same baseline as the rest of the dashboard. Independent confirmation of the delta-based PoP."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "Historical safe"), /*#__PURE__*/React.createElement("span", {
    className: "trade-val"
  }, callSafePct.toFixed(0), "%")))), _cspEligible && /*#__PURE__*/React.createElement("div", {
    className: `trade-side trade-side-put${frontRunner?.side === "put" ? " is-front" : ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "trade-side-head"
  }, /*#__PURE__*/React.createElement("div", {
    className: "trade-side-title"
  }, "Sell cash-secured put"), /*#__PURE__*/React.createElement("div", {
    className: "trade-side-score",
    title: "Composite score: rec timing + analyst overlay + PoP + annualized return + premium."
  }, putScore)), /*#__PURE__*/React.createElement("div", {
    className: "trade-side-strike",
    title: "Strike picked by the dashboard's 0.20-delta target. The put you'd sell."
  }, "$", putStrike.toFixed(2), " ", /*#__PURE__*/React.createElement("span", {
    className: "trade-side-strike-meta"
  }, ((1 - putStrike / currentPrice) * 100).toFixed(1), "% OTM · ", putDelta.toFixed(2), "Δ")), /*#__PURE__*/React.createElement("div", {
    className: "trade-side-rows"
  }, /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Premium you collect per share. Multiply by 100 for per-contract dollar amount."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "Premium"), /*#__PURE__*/React.createElement("span", {
    className: "trade-val"
  }, "$", putMid.toFixed(2), " ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "/ $", putMaxProfit.toFixed(0), " per contract"))), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Premium as percentage of strike (the capital you'd commit). Standard CSP yield metric."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "% of strike"), /*#__PURE__*/React.createElement("span", {
    className: "trade-val"
  }, putPctOfStock.toFixed(2), "%")), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Annualized return on capital committed if you collect this premium over the holding period and roll continuously. Assumes no assignment."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "Annualized"), /*#__PURE__*/React.createElement("span", {
    className: `trade-val ${putAnnualizedPct > 25 ? "up" : ""}`
  }, putAnnualizedPct.toFixed(1), "%")), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Probability the option expires worthless (you keep all premium and don't get assigned the stock). Approximation: 1 - |delta|."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "PoP"), /*#__PURE__*/React.createElement("span", {
    className: `trade-val ${putPoP > 70 ? "up" : ""}`
  }, putPoP.toFixed(0), "%")), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Effective cost basis if assigned: strike - premium. The price per share you'd own the stock at if put to you."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "If assigned at"), /*#__PURE__*/React.createElement("span", {
    className: "trade-val"
  }, "$", putBreakeven.toFixed(2))), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Discount vs current price if assigned. Higher = more cushion below current price before assignment hurts."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "Discount"), /*#__PURE__*/React.createElement("span", {
    className: `trade-val ${putBreakevenDiscount > 5 ? "up" : ""}`
  }, putBreakevenDiscount.toFixed(1), "%")), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Capital required to secure 1 contract = strike × 100. The cash you'd need parked to back this put."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "Capital"), /*#__PURE__*/React.createElement("span", {
    className: "trade-val"
  }, "$", putCapitalRequired.toFixed(0))), /*#__PURE__*/React.createElement("div", {
    className: "trade-row",
    title: "Historical probability that the weekly low stayed above this strike, measured against the same baseline as the rest of the dashboard. Independent confirmation of the delta-based PoP."
  }, /*#__PURE__*/React.createElement("span", {
    className: "trade-lbl"
  }, "Historical safe"), /*#__PURE__*/React.createElement("span", {
    className: "trade-val"
  }, putSafePct.toFixed(0), "%"))))), /*#__PURE__*/React.createElement("div", {
    className: "trade-multi-exp-section"
  }, !multiExpExpanded && !multiExp && /*#__PURE__*/React.createElement("button", {
    className: "trade-multi-exp-toggle",
    onClick: () => {
      setMultiExpExpanded(true);
      fetchMultiExp();
    },
    title: "Load and compare 0.20-delta strike scoring across the next 8 weekly expirations. Fetches all chains from the broker — typically 3-10 seconds."
  }, "Compare across expirations →"), multiExpExpanded && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "trade-multi-exp-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Cross-expiration · 0.20 delta · 8 weeks out"), /*#__PURE__*/React.createElement("div", {
    className: "trade-multi-exp-title"
  }, "Compare across expirations")), /*#__PURE__*/React.createElement("button", {
    className: "trade-multi-exp-refresh",
    disabled: multiExpLoading,
    onClick: fetchMultiExp,
    title: "Re-fetch all chains. Use after market open or when prices have moved meaningfully."
  }, multiExpLoading ? "Loading…" : "Refresh")), multiExpError && /*#__PURE__*/React.createElement("div", {
    className: "trade-multi-exp-error"
  }, "Error: ", multiExpError), multiExpLoading && !multiExp && /*#__PURE__*/React.createElement("div", {
    className: "trade-multi-exp-loading"
  }, "Loading ", ticker, " chains across expirations… (3-10 seconds)"), multiExp && multiExp.rows && multiExp.rows.length > 0 && (() => {
    // Find the best annualized for each side to highlight
    const bestCallAnn = Math.max(...multiExp.rows.filter(r => r.call?.annualized_pct != null).map(r => r.call.annualized_pct));
    const bestPutAnn = Math.max(...multiExp.rows.filter(r => r.put?.annualized_pct != null).map(r => r.put.annualized_pct));
    return /*#__PURE__*/React.createElement("div", {
      className: "trade-multi-exp-table"
    }, /*#__PURE__*/React.createElement("div", {
      className: `trade-multi-head trade-multi-mode-${_mode}`
    }, /*#__PURE__*/React.createElement("span", {
      title: "Expiration date for this row."
    }, "Exp"), /*#__PURE__*/React.createElement("span", {
      title: "Days to expiration."
    }, "DTE"), _ccEligible && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
      title: "Call strike at the 0.20 delta target for this expiration."
    }, "Call $"), /*#__PURE__*/React.createElement("span", {
      title: "Call premium (mid price). Multiply by 100 for per-contract."
    }, "C Prem"), /*#__PURE__*/React.createElement("span", {
      title: "Annualized return on the call premium. Higher is better, but front-week numbers are inflated by low DTE."
    }, "C Ann%"), /*#__PURE__*/React.createElement("span", {
      title: "Probability of profit for the call. Approximation: 1 - |delta|."
    }, "C PoP")), _cspEligible && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
      title: "Put strike at the 0.20 delta target for this expiration."
    }, "Put $"), /*#__PURE__*/React.createElement("span", {
      title: "Put premium (mid price). Multiply by 100 for per-contract."
    }, "P Prem"), /*#__PURE__*/React.createElement("span", {
      title: "Annualized return on capital required for the put. Higher is better."
    }, "P Ann%"), /*#__PURE__*/React.createElement("span", {
      title: "Probability of profit for the put. Approximation: 1 - |delta|."
    }, "P PoP"))), multiExp.rows.map((r, i) => {
      const c = r.call || {};
      const p = r.put || {};
      const isCallBest = c.annualized_pct === bestCallAnn;
      const isPutBest = p.annualized_pct === bestPutAnn;
      const expShort = r.expiration ? new Date(r.expiration + "T16:00:00").toLocaleDateString("en-US", {
        month: "short",
        day: "numeric"
      }) : "—";
      return /*#__PURE__*/React.createElement("div", {
        key: i,
        className: `trade-multi-row trade-multi-mode-${_mode}`
      }, /*#__PURE__*/React.createElement("span", {
        className: "trade-multi-exp"
      }, expShort), /*#__PURE__*/React.createElement("span", {
        className: "muted"
      }, r.dte, "d"), _ccEligible && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", null, c.strike != null ? "$" + c.strike.toFixed(0) : "—"), /*#__PURE__*/React.createElement("span", null, c.mid != null ? "$" + c.mid.toFixed(2) : "—"), /*#__PURE__*/React.createElement("span", {
        className: isCallBest ? "trade-multi-best" : "",
        title: isCallBest ? "Best call annualized return across expirations." : ""
      }, c.annualized_pct != null ? c.annualized_pct.toFixed(1) + "%" : "—"), /*#__PURE__*/React.createElement("span", null, c.pop_pct != null ? c.pop_pct.toFixed(0) + "%" : "—")), _cspEligible && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", null, p.strike != null ? "$" + p.strike.toFixed(0) : "—"), /*#__PURE__*/React.createElement("span", null, p.mid != null ? "$" + p.mid.toFixed(2) : "—"), /*#__PURE__*/React.createElement("span", {
        className: isPutBest ? "trade-multi-best" : "",
        title: isPutBest ? "Best put annualized return across expirations." : ""
      }, p.annualized_pct != null ? p.annualized_pct.toFixed(1) + "%" : "—"), /*#__PURE__*/React.createElement("span", null, p.pop_pct != null ? p.pop_pct.toFixed(0) + "%" : "—")));
    }));
  })(), multiExp && (!multiExp.rows || multiExp.rows.length === 0) && !multiExpLoading && /*#__PURE__*/React.createElement("div", {
    className: "trade-multi-exp-empty"
  }, "No usable strikes found for ", ticker, " in the next 8 weeks."))), /*#__PURE__*/React.createElement("div", {
    className: "trade-builder-disclaimer",
    title: "The score combines several heuristics and isn't backtested. Treat it as a structured second opinion, not a signal to blindly follow."
  }, "Heuristic score, not backtested. Decisions remain yours.")));
}
function AnalystCard({
  ticker,
  currentPrice,
  apiFetch,
  onData,
  strategyMode
}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  // Reset cached data when ticker changes — same pattern as the v85 cache fix
  useEffect(() => {
    setData(null);
    setError(null);
    if (onData) onData(null);
  }, [ticker]);

  // Latest price via ref — analyst ratings barely move with intraday price,
  // so we don't want to refetch on every 5s quote tick (deps below).
  const analystPriceRef = React.useRef(currentPrice);
  analystPriceRef.current = currentPrice;
  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    const fetchData = async () => {
      try {
        setLoading(true);
        const url = `/api/analyst?symbol=${encodeURIComponent(ticker)}` + (analystPriceRef.current ? `&price=${analystPriceRef.current}` : "") + (refreshKey > 0 ? `&force=1` : "");
        const r = await apiFetch(url);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (cancelled) return;
        if (j.error) {
          setError(j.error);
          setData(null);
          if (onData) onData(null);
        } else {
          setData(j);
          setError(null);
          if (onData) onData(j);
        }
      } catch (e) {
        if (!cancelled) {
          setError(String(e));
          if (onData) onData(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchData();
    return () => {
      cancelled = true;
    };
  }, [ticker, refreshKey]);

  // Color-coded action pills
  const actionClass = action => ({
    upgrade: "analyst-action-upgrade",
    downgrade: "analyst-action-downgrade",
    initiate: "analyst-action-initiate",
    target_change: "analyst-action-target",
    reiterate: "analyst-action-reiterate",
    unknown: "analyst-action-unknown"
  })[action] || "analyst-action-unknown";
  const actionLabel = action => ({
    upgrade: "Upgrade",
    downgrade: "Downgrade",
    initiate: "Initiate",
    target_change: "Target",
    reiterate: "Reiterate",
    unknown: "—"
  })[action] || "—";
  const gradeClass = g => {
    if (!g) return "";
    if (["Strong Buy", "Buy", "Outperform", "Overweight"].includes(g)) return "grade-bull";
    if (["Strong Sell", "Sell", "Underperform", "Underweight"].includes(g)) return "grade-bear";
    return "grade-neutral";
  };

  // Verdict pill class — color the tag based on its sentiment
  const tagClass = tag => {
    const t = tag.toLowerCase();
    if (t.includes("bullish") || t.includes("upgrade") || t.includes("more bullish") || t.includes("upside continuation")) return "verdict-pill-bull";
    if (t.includes("bearish") || t.includes("downgrade") || t.includes("above average") || t.includes("overextension") || t.includes("far above")) return "verdict-pill-bear";
    if (t.includes("no recent")) return "verdict-pill-neutral";
    return "verdict-pill-info";
  };
  return /*#__PURE__*/React.createElement(CardErrorBoundary, {
    label: "Analyst price targets"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card analyst-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Analyst price targets · ratings · catalysts"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Analyst price targets")), /*#__PURE__*/React.createElement("div", {
    className: "research-controls"
  }, /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    disabled: loading,
    onClick: () => setRefreshKey(k => k + 1),
    title: "Force-refresh analyst data (bypasses 30 min cache)."
  }, loading ? "Loading…" : "Refresh"))), error && /*#__PURE__*/React.createElement("div", {
    className: "research-error"
  }, "Error: ", error), !data && !loading && !error && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, "Loading analyst data for ", ticker, "…"), data && !data.data_available && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, "No analyst data available for ", ticker, ". This is normal for very small caps, recent IPOs, or international tickers."), data && data.data_available && (() => {
    const t = data.targets || {};
    const c = data.consensus || {};
    const v = data.verdict || {};
    const upside = t.upside_pct;
    const upsideCls = upside == null ? "" : upside > 5 ? "up" : upside < -5 ? "down" : "";
    const consensusCls = !c.label ? "" : ["Strong Buy", "Buy"].includes(c.label) ? "up" : ["Strong Sell", "Sell"].includes(c.label) ? "down" : "";
    return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      className: "analyst-stats-grid"
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat",
      title: "Current stock price the dashboard is using."
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-lbl"
    }, "Price"), /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-val"
    }, data.current_price != null ? "$" + Number(data.current_price).toFixed(2) : "—")), /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat",
      title: "Average analyst price target across all covering firms."
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-lbl"
    }, "Avg target"), /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-val"
    }, t.mean != null ? "$" + Number(t.mean).toFixed(2) : "—")), /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat",
      title: "Highest individual analyst target."
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-lbl"
    }, "High"), /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-val up"
    }, t.high != null ? "$" + Number(t.high).toFixed(2) : "—")), /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat",
      title: "Lowest individual analyst target."
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-lbl"
    }, "Low"), /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-val down"
    }, t.low != null ? "$" + Number(t.low).toFixed(2) : "—")), /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat",
      title: "Percentage from current price to average target. Positive = upside expected by analysts. Negative = trading above average target."
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-lbl"
    }, "Upside"), /*#__PURE__*/React.createElement("div", {
      className: `analyst-stat-val ${upsideCls}`
    }, upside != null ? (upside >= 0 ? "+" : "") + upside.toFixed(1) + "%" : "—")), /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat",
      title: "Aggregate consensus rating from the most recent month's recommendation breakdown. Requires FINNHUB_API_KEY in .env to populate."
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-lbl"
    }, "Consensus"), /*#__PURE__*/React.createElement("div", {
      className: `analyst-consensus ${consensusCls}`
    }, c.label && c.label !== "—" ? /*#__PURE__*/React.createElement("span", {
      className: "analyst-stat-val"
    }, c.label, c.score != null && /*#__PURE__*/React.createElement("span", {
      className: "analyst-score-num"
    }, " ", c.score)) : /*#__PURE__*/React.createElement("span", {
      className: "analyst-finnhub-hint",
      title: "Set FINNHUB_API_KEY in .env to enable consensus, analyst count, and trend."
    }, "—", /*#__PURE__*/React.createElement("span", {
      className: "analyst-needs-finnhub"
    }, "needs Finnhub")))), /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat",
      title: "Number of analysts contributing to the price target consensus. Comes from Finnhub."
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-lbl"
    }, "Analysts"), /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-val"
    }, t.num_analysts != null ? t.num_analysts : /*#__PURE__*/React.createElement("span", {
      className: "analyst-needs-finnhub"
    }, "needs Finnhub"))), /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat",
      title: "Whether sentiment has shifted bullish, bearish, or stayed stable across the last 3 months. Requires Finnhub."
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-stat-lbl"
    }, "Trend"), /*#__PURE__*/React.createElement("div", {
      className: `analyst-stat-val analyst-trend-${c.trend || "none"}`
    }, c.trend === "more_bullish" ? "↑ Bullish" : c.trend === "more_bearish" ? "↓ Bearish" : c.trend === "stable" ? "→ Stable" : /*#__PURE__*/React.createElement("span", {
      className: "analyst-needs-finnhub"
    }, "needs Finnhub")))), c.breakdown && c.breakdown.total > 0 && (() => {
      const bd = c.breakdown;
      const pct = n => bd.total > 0 ? n / bd.total * 100 : 0;
      return /*#__PURE__*/React.createElement("div", {
        className: "analyst-consensus-bar",
        title: `Latest consensus: ${bd.strong_buy} Strong Buy, ${bd.buy} Buy, ${bd.hold} Hold, ${bd.sell} Sell, ${bd.strong_sell} Strong Sell`
      }, /*#__PURE__*/React.createElement("div", {
        className: "analyst-bar-segment analyst-bar-strong-buy",
        style: {
          width: pct(bd.strong_buy) + "%"
        },
        title: `Strong Buy: ${bd.strong_buy}`
      }), /*#__PURE__*/React.createElement("div", {
        className: "analyst-bar-segment analyst-bar-buy",
        style: {
          width: pct(bd.buy) + "%"
        },
        title: `Buy: ${bd.buy}`
      }), /*#__PURE__*/React.createElement("div", {
        className: "analyst-bar-segment analyst-bar-hold",
        style: {
          width: pct(bd.hold) + "%"
        },
        title: `Hold: ${bd.hold}`
      }), /*#__PURE__*/React.createElement("div", {
        className: "analyst-bar-segment analyst-bar-sell",
        style: {
          width: pct(bd.sell) + "%"
        },
        title: `Sell: ${bd.sell}`
      }), /*#__PURE__*/React.createElement("div", {
        className: "analyst-bar-segment analyst-bar-strong-sell",
        style: {
          width: pct(bd.strong_sell) + "%"
        },
        title: `Strong Sell: ${bd.strong_sell}`
      }));
    })(), v.tags && v.tags.length > 0 && /*#__PURE__*/React.createElement("div", {
      className: "analyst-verdict-row"
    }, v.tags.map((tag, i) => /*#__PURE__*/React.createElement("span", {
      key: i,
      className: `verdict-pill ${tagClass(tag)}`
    }, tag))), (v.call_warnings && v.call_warnings.length > 0 && (strategyMode === "both" || strategyMode === "cc" || !strategyMode) || v.put_warnings && v.put_warnings.length > 0 && (strategyMode === "both" || strategyMode === "csp" || !strategyMode)) && /*#__PURE__*/React.createElement("div", {
      className: "analyst-warnings-row"
    }, v.call_warnings && v.call_warnings.length > 0 && (strategyMode === "both" || strategyMode === "cc" || !strategyMode) && /*#__PURE__*/React.createElement("div", {
      className: "analyst-warnings analyst-warnings-cc"
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-warnings-title"
    }, "Selling covered calls"), /*#__PURE__*/React.createElement("ul", null, v.call_warnings.map((w, i) => /*#__PURE__*/React.createElement("li", {
      key: i
    }, w)))), v.put_warnings && v.put_warnings.length > 0 && (strategyMode === "both" || strategyMode === "csp" || !strategyMode) && /*#__PURE__*/React.createElement("div", {
      className: "analyst-warnings analyst-warnings-csp"
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-warnings-title"
    }, "Selling cash-secured puts"), /*#__PURE__*/React.createElement("ul", null, v.put_warnings.map((w, i) => /*#__PURE__*/React.createElement("li", {
      key: i
    }, w))))), v.intraday_signals && v.intraday_signals.length > 0 && /*#__PURE__*/React.createElement("div", {
      className: "analyst-warnings analyst-warnings-intraday"
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-warnings-title"
    }, "Intraday catalyst signals"), /*#__PURE__*/React.createElement("ul", null, v.intraday_signals.map((w, i) => /*#__PURE__*/React.createElement("li", {
      key: i
    }, w)))), data.history && data.history.length > 0 && /*#__PURE__*/React.createElement("div", {
      className: "analyst-history"
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-history-title"
    }, "Recent analyst updates (", data.history.length, ")", /*#__PURE__*/React.createElement("span", {
      className: "analyst-source-tag",
      title: `Data source: ${data.source}`
    }, " · ", data.source)), /*#__PURE__*/React.createElement("div", {
      className: "analyst-history-table"
    }, /*#__PURE__*/React.createElement("div", {
      className: "analyst-history-head"
    }, /*#__PURE__*/React.createElement("span", {
      title: "Date the analyst published this rating change or price target update. Most recent first."
    }, "Date"), /*#__PURE__*/React.createElement("span", {
      title: "Investment bank or research firm that issued the call (e.g. Morgan Stanley, JP Morgan, Wedbush)."
    }, "Firm"), /*#__PURE__*/React.createElement("span", {
      title: "Type of update. Upgrade = rating raised. Downgrade = rating lowered. Initiate = first time covering. Target = price target changed but rating unchanged. Reiterate = no change to either."
    }, "Action"), /*#__PURE__*/React.createElement("span", {
      title: "Rating change. Shows prior rating → new rating when the rating moved. Bullish ratings (Buy, Outperform, Overweight) in green. Bearish (Sell, Underperform) in red. Hold/Neutral in gray."
    }, "Rating"), /*#__PURE__*/React.createElement("span", {
      title: "Price target. Shows prior target → new target when changed. Single value when only the rating moved or this is an initiation."
    }, "Target"), /*#__PURE__*/React.createElement("span", {
      title: "Percentage change in the price target from prior to new. Positive (green) = target raised. Negative (red) = target lowered. Blank = no prior target available (initiations or rating-only changes)."
    }, "Δ")), data.history.slice(0, 30).map((h, i) => {
      const tcCls = h.target_change_pct == null ? "" : h.target_change_pct > 0 ? "up" : h.target_change_pct < 0 ? "down" : "";
      return /*#__PURE__*/React.createElement("div", {
        key: i,
        className: "analyst-history-row"
      }, /*#__PURE__*/React.createElement("span", {
        className: "muted"
      }, h.date || "—"), /*#__PURE__*/React.createElement("span", {
        className: "analyst-firm"
      }, h.firm || "—"), /*#__PURE__*/React.createElement("span", {
        className: `analyst-action-pill ${actionClass(h.action_class)}`
      }, actionLabel(h.action_class)), /*#__PURE__*/React.createElement("span", {
        className: "analyst-grade-cell"
      }, h.prior_grade && h.new_grade && h.prior_grade !== h.new_grade ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
        className: `grade-pill ${gradeClass(h.prior_grade)}`
      }, h.prior_grade), /*#__PURE__*/React.createElement("span", {
        className: "grade-arrow"
      }, " → "), /*#__PURE__*/React.createElement("span", {
        className: `grade-pill ${gradeClass(h.new_grade)}`
      }, h.new_grade)) : h.new_grade ? /*#__PURE__*/React.createElement("span", {
        className: `grade-pill ${gradeClass(h.new_grade)}`
      }, h.new_grade) : "—"), /*#__PURE__*/React.createElement("span", {
        className: "analyst-target-cell"
      }, h.prior_target && h.new_target && h.prior_target !== h.new_target ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("span", {
        className: "muted"
      }, "$", h.prior_target.toFixed(0)), /*#__PURE__*/React.createElement("span", {
        className: "muted"
      }, " → "), /*#__PURE__*/React.createElement("span", null, "$", h.new_target.toFixed(0))) : h.new_target ? /*#__PURE__*/React.createElement("span", null, "$", h.new_target.toFixed(0)) : "—"), /*#__PURE__*/React.createElement("span", {
        className: `analyst-target-change ${tcCls}`
      }, h.target_change_pct != null ? (h.target_change_pct > 0 ? "+" : "") + h.target_change_pct.toFixed(1) + "%" : "—"));
    }))));
  })()));
}
function PullbackProfileCard({
  ticker,
  currentPrice,
  livePrice,
  apiFetch
}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [basingData, setBasingData] = useState(null);
  // Direction toggle — "short" = sell at open, cover lower (open→low pullback);
  // "long" = buy at open, sell higher (open→high pop). Persisted in localStorage.
  const PB_DIR_KEY = "weeklyOptionsTimer.pullback.direction.v1";
  const [direction, setDirection] = useState(() => {
    try {
      return localStorage.getItem(PB_DIR_KEY) || "short";
    } catch {
      return "short";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(PB_DIR_KEY, direction);
    } catch {}
  }, [direction]);

  // Historical pullback profile — fetch once per ticker
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const r = await apiFetch(`/api/pullback_profile?symbol=${encodeURIComponent(ticker)}&days=180`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (cancelled) return;
        if (j.error) {
          setError(j.error);
          setData(null);
        } else {
          setData(j);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  // Today's session OHL — same endpoint as BasingCard, polled every 30s
  // during market hours. Schwab cache makes this cheap.
  const isMarketOpen = () => {
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false
    });
    const parts = fmt.formatToParts(new Date());
    const wd = parts.find(p => p.type === "weekday")?.value;
    const hh = parseInt(parts.find(p => p.type === "hour")?.value || "0", 10);
    const mm = parseInt(parts.find(p => p.type === "minute")?.value || "0", 10);
    if (wd === "Sat" || wd === "Sun") return false;
    const minutes = hh * 60 + mm;
    return minutes >= 570 && minutes < 960;
  };
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await apiFetch(`/api/basing?symbol=${encodeURIComponent(ticker)}&weeks=4`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (!cancelled && !j.error) setBasingData(j);
      } catch {}
    };
    tick();
    let timer = null;
    if (isMarketOpen()) {
      timer = setInterval(() => {
        if (document.hidden) return;
        if (!isMarketOpen()) return;
        tick();
      }, 30000);
    }
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [ticker]);
  if (loading && !data) {
    return /*#__PURE__*/React.createElement("div", {
      className: "card pullback-card"
    }, /*#__PURE__*/React.createElement("div", {
      className: "card-head"
    }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
      className: "kicker"
    }, "Open behavior · pullback profile"), /*#__PURE__*/React.createElement("div", {
      className: "card-title"
    }, "Open-to-low / open-to-high"))), /*#__PURE__*/React.createElement("div", {
      className: "muted",
      style: {
        padding: "16px 0"
      }
    }, "Loading historical pullback stats."));
  }
  if (error || !data || data.samples === 0) {
    return null; // hide if not enough data
  }
  const fmt$ = v => v == null ? "—" : "$" + v.toFixed(2);
  const isShort = direction === "short";

  // Today's setup
  const live = livePrice ?? currentPrice;
  const sessionOpen = basingData?.session_open;
  const sessionLow = basingData?.session_low;
  const sessionHigh = basingData?.session_high;
  const prevClose = basingData?.prev_close;
  const todayGapPct = sessionOpen && prevClose ? (sessionOpen - prevClose) / prevClose * 100 : null;
  const todayPullbackSoFar = sessionOpen && sessionLow ? Math.max(0, (sessionOpen - sessionLow) / sessionOpen * 100) : null;
  const todayPopSoFar = sessionOpen && sessionHigh ? Math.max(0, (sessionHigh - sessionOpen) / sessionOpen * 100) : null;
  const fromOpenNow = sessionOpen && live ? (live - sessionOpen) / sessionOpen * 100 : null;

  // Pick the relevant subset for the current direction.
  // Short: gap-up days are most relevant (strong open → fade).
  // Long: gap-down days OR overall (weak open → recovery), but
  // if today gapped up, overall is the appropriate sample.
  let primaryGroup, primaryLabel;
  if (isShort) {
    const useGap = todayGapPct != null && todayGapPct >= 1.0 && data.gap_up?.n >= 15;
    primaryGroup = useGap ? data.gap_up : data.overall;
    primaryLabel = useGap ? `Gap-up days (≥1%, n=${data.gap_up.n})` : `All days (n=${data.overall.n})`;
  } else {
    // Long side: if today gapped DOWN ≥ 1%, prefer the gap-down sample
    // (recovery from a weak open). Otherwise use overall.
    const useGapDown = todayGapPct != null && todayGapPct <= -1.0 && data.gap_down?.n >= 15;
    primaryGroup = useGapDown ? data.gap_down : data.overall;
    primaryLabel = useGapDown ? `Gap-down days (≤-1%, n=${data.gap_down.n})` : `All days (n=${data.overall.n})`;
  }
  // Pick stats block by direction
  const primary = isShort ? primaryGroup.short : primaryGroup.long;

  // Suggested levels — flipped per direction
  const targetPct = primary.median;
  const stretchPct = primary.p75;
  const targetPrice = sessionOpen ? isShort ? sessionOpen * (1 - targetPct / 100) : sessionOpen * (1 + targetPct / 100) : null;
  const stretchPrice = sessionOpen ? isShort ? sessionOpen * (1 - stretchPct / 100) : sessionOpen * (1 + stretchPct / 100) : null;
  const stopPrice = sessionOpen ? isShort ? sessionOpen * (1 + primary.median * 0.5 / 100) : sessionOpen * (1 - primary.median * 0.5 / 100) : null;

  // Verdict — direction-specific reasoning
  let verdict = null,
    verdictReason = null,
    verdictCls = null;
  const goAwayPct = isShort ? primaryGroup.gap_and_go_pct ?? primary.open_eq_low_pct // % chance of running away (open=low)
  : primary.open_eq_high_pct; // % chance open=high (no buy opportunity)
  const todaySoFar = isShort ? todayPullbackSoFar : todayPopSoFar;
  if (todayGapPct == null || sessionOpen == null) {
    verdict = "No setup yet";
    verdictReason = "Waiting for today's open price.";
    verdictCls = "verdict-wait";
  } else if (isShort && todayGapPct < 0.3) {
    verdict = "No setup";
    verdictReason = "Stock did not gap up. Short-the-open thesis requires a strong open.";
    verdictCls = "verdict-wait";
  } else if (!isShort && todayGapPct > -0.3 && Math.abs(todayGapPct) < 0.3) {
    verdict = "Mixed setup";
    verdictReason = "No clear gap. Buy-the-open works best on a meaningful gap up or down.";
    verdictCls = "verdict-wait";
  } else if (goAwayPct != null && goAwayPct >= 35) {
    verdict = isShort ? "Avoid short" : "Avoid long";
    verdictReason = isShort ? `${goAwayPct.toFixed(0)}% of similar days were gap-and-go (open = low). Risk of running away.` : `${goAwayPct.toFixed(0)}% of similar days had open = high (no pop). Risk of fading immediately.`;
    verdictCls = "verdict-avoid";
  } else if (todaySoFar != null && todaySoFar >= primary.median * 0.8) {
    verdict = isShort ? "Already pulled back" : "Already popped";
    verdictReason = isShort ? `Already ${todaySoFar.toFixed(2)}% below open. Most of the typical pullback (${primary.median.toFixed(2)}% median) is already done.` : `Already ${todaySoFar.toFixed(2)}% above open. Most of the typical pop (${primary.median.toFixed(2)}% median) is already done.`;
    verdictCls = "verdict-wait";
  } else if (isShort && fromOpenNow != null && fromOpenNow > 0.5) {
    verdict = "Wait for fade";
    verdictReason = `Still ${fromOpenNow >= 0 ? "+" : ""}${fromOpenNow.toFixed(2)}% above open. Better entry near open or above.`;
    verdictCls = "verdict-partial";
  } else if (!isShort && fromOpenNow != null && fromOpenNow < -0.5) {
    verdict = "Wait for bounce";
    verdictReason = `Still ${fromOpenNow.toFixed(2)}% below open. Better entry near open or below.`;
    verdictCls = "verdict-partial";
  } else {
    verdict = isShort ? "Tradable short" : "Tradable long";
    verdictReason = isShort ? `Typical pullback ${primary.median.toFixed(2)}% (median) on ${primaryLabel.toLowerCase()}. Open-eq-low rate ${goAwayPct.toFixed(0)}%.` : `Typical pop ${primary.median.toFixed(2)}% (median) on ${primaryLabel.toLowerCase()}. Open-eq-high rate ${goAwayPct.toFixed(0)}%.`;
    verdictCls = "verdict-sell";
  }
  const thresholds = primary.thresholds || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "card pullback-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, isShort ? "Short the open · pullback profile" : "Buy the open · pop profile"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, isShort ? "Open-to-low pullback" : "Open-to-high pop", " · ", data.lookback_days, "d history")), /*#__PURE__*/React.createElement("div", {
    className: "pullback-card-tools"
  }, /*#__PURE__*/React.createElement("div", {
    className: "kicker",
    title: "Sample size for the primary distribution"
  }, primaryLabel), /*#__PURE__*/React.createElement("div", {
    className: "basing-toggle",
    title: "Switch between short-the-open (pullback) and buy-the-open (pop) views"
  }, /*#__PURE__*/React.createElement("button", {
    className: isShort ? "active" : "",
    onClick: () => setDirection("short"),
    title: "Short the open. Sell at open, cover at the typical intraday low."
  }, "Short"), /*#__PURE__*/React.createElement("button", {
    className: !isShort ? "active" : "",
    onClick: () => setDirection("long"),
    title: "Buy the open. Buy at open, sell at the typical intraday high."
  }, "Long")))), /*#__PURE__*/React.createElement("div", {
    className: `basing-verdict ${verdictCls}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "basing-verdict-label"
  }, verdict), /*#__PURE__*/React.createElement("div", {
    className: "basing-verdict-reason"
  }, verdictReason)), /*#__PURE__*/React.createElement("div", {
    className: "pullback-grid"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-stat-label",
    title: isShort ? "Median percentage drop from open to intraday low" : "Median percentage rise from open to intraday high"
  }, isShort ? "Median pullback" : "Median pop"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-stat-val"
  }, fmtPct(primary.median))), /*#__PURE__*/React.createElement("div", {
    className: "pullback-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-stat-label",
    title: isShort ? "75th percentile — pullback exceeded on 25% of days" : "75th percentile — pop exceeded on 25% of days"
  }, "75th %ile"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-stat-val"
  }, fmtPct(primary.p75))), /*#__PURE__*/React.createElement("div", {
    className: "pullback-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-stat-label",
    title: isShort ? "90th percentile — pullback exceeded on 10% of days" : "90th percentile — pop exceeded on 10% of days"
  }, "90th %ile"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-stat-val"
  }, fmtPct(primary.p90))), /*#__PURE__*/React.createElement("div", {
    className: "pullback-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-stat-label",
    title: isShort ? "Frequency of days where the open was the intraday low (gap-and-go rate). Higher = more risk for shorts." : "Frequency of days where the open was the intraday high (no pop). Higher = more risk for longs."
  }, isShort ? "Open = low" : "Open = high"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-stat-val"
  }, fmtPct(goAwayPct)))), /*#__PURE__*/React.createElement("div", {
    className: "pullback-thresholds"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-thresholds-title",
    title: isShort ? "How often the stock pulled back at least X% from the open over the lookback period" : "How often the stock popped at least X% above the open over the lookback period"
  }, isShort ? "Pullback frequency" : "Pop frequency"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-thresholds-grid"
  }, [0.25, 0.50, 0.75, 1.00, 1.50, 2.00].map(t => {
    const v = thresholds[t.toString()];
    return /*#__PURE__*/React.createElement("div", {
      key: t,
      className: "pullback-thresh"
    }, /*#__PURE__*/React.createElement("div", {
      className: "pullback-thresh-label",
      title: isShort ? `Frequency of days where the open-to-low pullback exceeded ${t.toFixed(2)}%` : `Frequency of days where the open-to-high pop exceeded ${t.toFixed(2)}%`
    }, "≥ ", t.toFixed(2), "%"), /*#__PURE__*/React.createElement("div", {
      className: "pullback-thresh-val"
    }, v ? v.pct.toFixed(0) + "%" : "—"));
  }))), /*#__PURE__*/React.createElement("div", {
    className: "pullback-today"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-today-title",
    title: "Live values for today's session vs the historical pullback profile"
  }, "Today"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-today-grid"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-today-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-today-label",
    title: "Today's opening print at 9:30am ET"
  }, "Open"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-today-val"
  }, fmt$(sessionOpen))), /*#__PURE__*/React.createElement("div", {
    className: "pullback-today-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-today-label",
    title: "Percent gap from yesterday's close to today's open"
  }, "Gap"), /*#__PURE__*/React.createElement("div", {
    className: `pullback-today-val ${todayGapPct != null && todayGapPct >= 0 ? "up" : "down"}`
  }, fmtPct(todayGapPct))), /*#__PURE__*/React.createElement("div", {
    className: "pullback-today-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-today-label",
    title: "Current price as a percentage of today's open. Negative = pulled back below open"
  }, "Now"), /*#__PURE__*/React.createElement("div", {
    className: `pullback-today-val ${fromOpenNow != null && fromOpenNow >= 0 ? "up" : "down"}`
  }, fromOpenNow != null ? (fromOpenNow >= 0 ? "+" : "") + fromOpenNow.toFixed(2) + "%" : "—")), /*#__PURE__*/React.createElement("div", {
    className: "pullback-today-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-today-label",
    title: isShort ? "Open-to-low pullback so far today. Compare to historical median to see if a typical pullback has already happened" : "Open-to-high pop so far today. Compare to historical median to see if a typical pop has already happened"
  }, isShort ? "LoD pullback" : "HoD pop"), /*#__PURE__*/React.createElement("div", {
    className: `pullback-today-val ${isShort ? "down" : "up"}`
  }, (() => {
    const v = isShort ? todayPullbackSoFar : todayPopSoFar;
    if (v == null) return "—";
    return (isShort ? "-" : "+") + v.toFixed(2) + "%";
  })())))), (verdictCls === "verdict-sell" || verdictCls === "verdict-partial") && sessionOpen && /*#__PURE__*/React.createElement("div", {
    className: "pullback-levels"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-levels-title",
    title: isShort ? "Price levels for entering and managing a short-the-open trade based on historical pullback statistics" : "Price levels for entering and managing a buy-the-open trade based on historical pop statistics"
  }, isShort ? "Suggested short levels" : "Suggested long levels"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-levels-grid"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-level"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-level-label",
    title: isShort ? "Best price area to enter a short. At or above the open captures the largest typical pullback" : "Best price area to enter a long. At or below the open captures the largest typical pop"
  }, "Entry zone"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-level-val"
  }, isShort ? "≥" : "≤", " ", fmt$(sessionOpen)), /*#__PURE__*/React.createElement("div", {
    className: "pullback-level-sub"
  }, isShort ? "at or above open" : "at or below open")), /*#__PURE__*/React.createElement("div", {
    className: "pullback-level"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-level-label",
    title: isShort ? "Conservative cover price — the median historical open-to-low pullback. Half of historical days reach this level" : "Conservative profit target — the median historical open-to-high pop. Half of historical days reach this level"
  }, isShort ? "Cover target" : "Profit target"), /*#__PURE__*/React.createElement("div", {
    className: `pullback-level-val ${isShort ? "down" : "up"}`
  }, fmt$(targetPrice)), /*#__PURE__*/React.createElement("div", {
    className: "pullback-level-sub"
  }, isShort ? "median pullback" : "median pop")), /*#__PURE__*/React.createElement("div", {
    className: "pullback-level"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-level-label",
    title: isShort ? "Aggressive cover price — the 75th percentile pullback. Only 25% of historical days reach this level" : "Aggressive profit target — the 75th percentile pop. Only 25% of historical days reach this level"
  }, "Stretch target"), /*#__PURE__*/React.createElement("div", {
    className: `pullback-level-val ${isShort ? "down" : "up"}`
  }, fmt$(stretchPrice)), /*#__PURE__*/React.createElement("div", {
    className: "pullback-level-sub"
  }, "75th %ile")), /*#__PURE__*/React.createElement("div", {
    className: "pullback-level"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-level-label",
    title: isShort ? "If price pushes this far above open with no pullback yet, the short-the-open thesis has failed. Stop loss level" : "If price drops this far below open with no pop yet, the buy-the-open thesis has failed. Stop loss level"
  }, "Stop"), /*#__PURE__*/React.createElement("div", {
    className: `pullback-level-val ${isShort ? "up" : "down"}`
  }, fmt$(stopPrice)), /*#__PURE__*/React.createElement("div", {
    className: "pullback-level-sub"
  }, isShort ? "half median above" : "half median below"))), live && targetPrice && stopPrice && (() => {
    // R:R = reward / risk. For short: reward = live - target (cover lower), risk = stop - live (stop above).
    //                     For long: reward = target - live (sell higher), risk = live - stop (stop below).
    const reward = isShort ? live - targetPrice : targetPrice - live;
    const risk = isShort ? stopPrice - live : live - stopPrice;
    const rr = risk > 0 ? reward / risk : null;
    return /*#__PURE__*/React.createElement("div", {
      className: "pullback-rr"
    }, "R:R from current ", fmt$(live), " = ", /*#__PURE__*/React.createElement("b", null, rr != null ? rr.toFixed(2) : "—"), rr != null && rr >= 2 && /*#__PURE__*/React.createElement("span", {
      className: "rr-good"
    }, " · good"), rr != null && rr < 1 && /*#__PURE__*/React.createElement("span", {
      className: "rr-bad"
    }, " · poor"));
  })()), (data.strong_gap || data.high_rvol) && (() => {
    const sg = data.strong_gap ? isShort ? data.strong_gap.short : data.strong_gap.long : null;
    const hr = data.high_rvol ? isShort ? data.high_rvol.short : data.high_rvol.long : null;
    const eqKey = isShort ? "open_eq_low_pct" : "open_eq_high_pct";
    const eqLabel = isShort ? "Open=low" : "Open=high";
    return /*#__PURE__*/React.createElement("div", {
      className: "pullback-conditions"
    }, sg && /*#__PURE__*/React.createElement("div", {
      className: "pullback-cond"
    }, /*#__PURE__*/React.createElement("div", {
      className: "pullback-cond-label",
      title: "Days where the open gapped up at least 3% from prior close. Strong gaps tend to behave differently than normal gap-ups"
    }, "Strong gap (≥3%, n=", data.strong_gap.n, ")"), /*#__PURE__*/React.createElement("div", {
      className: "pullback-cond-vals"
    }, /*#__PURE__*/React.createElement("span", {
      title: isShort ? "Median open-to-low pullback on strong-gap days" : "Median open-to-high pop on strong-gap days"
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        color: "var(--fg-3)"
      }
    }, "Median"), " ", /*#__PURE__*/React.createElement("b", null, fmtPct(sg.median))), /*#__PURE__*/React.createElement("span", {
      title: isShort ? "75th percentile pullback on strong-gap days" : "75th percentile pop on strong-gap days"
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        color: "var(--fg-3)"
      }
    }, "p75"), " ", /*#__PURE__*/React.createElement("b", null, fmtPct(sg.p75))), /*#__PURE__*/React.createElement("span", {
      title: isShort ? "Frequency the open was the day's low on strong-gap days" : "Frequency the open was the day's high on strong-gap days"
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        color: "var(--fg-3)"
      }
    }, eqLabel), " ", /*#__PURE__*/React.createElement("b", null, fmtPct(sg[eqKey]))))), hr && /*#__PURE__*/React.createElement("div", {
      className: "pullback-cond"
    }, /*#__PURE__*/React.createElement("div", {
      className: "pullback-cond-label",
      title: "Days where today's volume was in the top 25% of recent volume distribution. High relative volume often signals a catalyst is driving the stock"
    }, "High rel. volume (top 25%, n=", data.high_rvol.n, ")"), /*#__PURE__*/React.createElement("div", {
      className: "pullback-cond-vals"
    }, /*#__PURE__*/React.createElement("span", {
      title: isShort ? "Median open-to-low pullback on high relative volume days" : "Median open-to-high pop on high relative volume days"
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        color: "var(--fg-3)"
      }
    }, "Median"), " ", /*#__PURE__*/React.createElement("b", null, fmtPct(hr.median))), /*#__PURE__*/React.createElement("span", {
      title: isShort ? "75th percentile pullback on high relative volume days" : "75th percentile pop on high relative volume days"
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        color: "var(--fg-3)"
      }
    }, "p75"), " ", /*#__PURE__*/React.createElement("b", null, fmtPct(hr.p75))), /*#__PURE__*/React.createElement("span", {
      title: isShort ? "Frequency the open was the day's low on high volume days" : "Frequency the open was the day's high on high volume days"
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        color: "var(--fg-3)"
      }
    }, eqLabel), " ", /*#__PURE__*/React.createElement("b", null, fmtPct(hr[eqKey]))))));
  })(), /*#__PURE__*/React.createElement(PullbackBacktest, {
    ticker: ticker,
    direction: direction,
    defaultTarget: primary.median,
    apiFetch: apiFetch
  }), data.gap_up && data.gap_up.n >= 10 && data.gap_up.gap_and_go_pct != null && /*#__PURE__*/React.createElement("div", {
    className: "pullback-split"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-split-title",
    title: "How gap-up days resolved historically: ran straight up (gap-and-go), pulled back then closed near or above open (normal pullback), or faded all day (gap fade)"
  }, "Gap-up day breakdown"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-split-bar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pullback-split-seg seg-gandgo",
    style: {
      width: `${data.gap_up.gap_and_go_pct}%`
    },
    title: `Gap-and-go: ${data.gap_up.gap_and_go_pct.toFixed(0)}% — open was the low`
  }, data.gap_up.gap_and_go_pct >= 10 && data.gap_up.gap_and_go_pct.toFixed(0) + "%"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-split-seg seg-normal",
    style: {
      width: `${data.gap_up.normal_pullback_pct}%`
    },
    title: `Normal pullback: ${data.gap_up.normal_pullback_pct.toFixed(0)}% — pulled back then closed near or above open`
  }, data.gap_up.normal_pullback_pct >= 10 && data.gap_up.normal_pullback_pct.toFixed(0) + "%"), /*#__PURE__*/React.createElement("div", {
    className: "pullback-split-seg seg-fade",
    style: {
      width: `${data.gap_up.gap_fade_pct}%`
    },
    title: `Gap fade: ${data.gap_up.gap_fade_pct.toFixed(0)}% — closed below open by ≥0.5%`
  }, data.gap_up.gap_fade_pct >= 10 && data.gap_up.gap_fade_pct.toFixed(0) + "%")), /*#__PURE__*/React.createElement("div", {
    className: "pullback-split-legend"
  }, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("i", {
    className: "legend-sw seg-gandgo"
  }), "Gap & go"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("i", {
    className: "legend-sw seg-normal"
  }), "Normal pullback"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("i", {
    className: "legend-sw seg-fade"
  }), "Gap fade"))));
}
function BasingCard({
  ticker,
  weeks,
  apiFetch,
  livePrice
}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  // Toggle: "time" shows minutes-at-price heatmap; "volume" shows shares-at-price.
  // Persisted so the user's last view sticks across reloads.
  const BASING_PREFS_KEY = "weeklyOptionsTimer.basing.prefs.v1";
  const _basingPrefs = (() => {
    try {
      const raw = localStorage.getItem(BASING_PREFS_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  })();
  const [viewMode, setViewMode] = useState(_basingPrefs?.viewMode ?? "time");
  // Overlay: when true, draw the OTHER mode's heatmap as a horizontal bar
  // beneath each price row so user can scan extreme volume / time levels.
  const [showOverlay, setShowOverlay] = useState(_basingPrefs?.showOverlay ?? false);
  // Persist any pref change
  useEffect(() => {
    try {
      localStorage.setItem(BASING_PREFS_KEY, JSON.stringify({
        viewMode,
        showOverlay
      }));
    } catch {}
  }, [viewMode, showOverlay]);
  const isMarketOpen = () => {
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false
    });
    const parts = fmt.formatToParts(new Date());
    const wd = parts.find(p => p.type === "weekday")?.value;
    const hh = parseInt(parts.find(p => p.type === "hour")?.value || "0", 10);
    const mm = parseInt(parts.find(p => p.type === "minute")?.value || "0", 10);
    if (wd === "Sat" || wd === "Sun") return false;
    const minutes = hh * 60 + mm;
    return minutes >= 570 && minutes < 960;
  };
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      // Outside market hours: still fetch once on mount so we can show
      // a snapshot, but don't keep polling.
      try {
        setLoading(true);
        const r = await apiFetch(`/api/basing?symbol=${ticker}&weeks=${weeks}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (cancelled) return;
        if (j.error) {
          setError(j.error);
          setData(null);
        } else {
          setData(j);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    tick();
    let timer = null;
    if (isMarketOpen()) {
      timer = setInterval(() => {
        if (document.hidden) return;
        if (!isMarketOpen()) return;
        tick();
      }, 30000);
    }
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [ticker, weeks]);
  if (error) {
    return /*#__PURE__*/React.createElement("div", {
      className: "card basing-card"
    }, /*#__PURE__*/React.createElement("div", {
      className: "card-head"
    }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
      className: "kicker"
    }, "Mean reversion · today's basing"), /*#__PURE__*/React.createElement("div", {
      className: "card-title"
    }, "Intraday basing levels"))), /*#__PURE__*/React.createElement("div", {
      className: "muted",
      style: {
        padding: "16px 0"
      }
    }, "Couldn't load profile: ", error));
  }
  if (!data) {
    return /*#__PURE__*/React.createElement("div", {
      className: "card basing-card"
    }, /*#__PURE__*/React.createElement("div", {
      className: "card-head"
    }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
      className: "kicker"
    }, "Mean reversion · today's basing"), /*#__PURE__*/React.createElement("div", {
      className: "card-title"
    }, "Intraday basing levels"))), /*#__PURE__*/React.createElement("div", {
      className: "muted",
      style: {
        padding: "16px 0"
      }
    }, loading ? "Loading." : "No data."));
  }
  const fmt$ = v => v == null ? "—" : "$" + v.toFixed(2);

  // Live today % — recomputed every 5s as livePrice updates.
  // Falls back to server value if prev_close or livePrice missing.
  const livePct = livePrice != null && data.prev_close ? (livePrice - data.prev_close) / data.prev_close * 100 : data.today_pct;

  // Histogram bin scaling
  const bins = data.bins || [];
  const maxTime = Math.max(1, ...bins.map(b => b.time_min));
  const maxVol = Math.max(1, ...bins.map(b => b.volume));

  // Show in price-descending order so highest price is at the top
  const binsTopDown = [...bins].reverse();
  return /*#__PURE__*/React.createElement("div", {
    className: "card basing-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Mean reversion · today's basing"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Intraday basing levels ", data.bounce_signal && /*#__PURE__*/React.createElement("span", {
    className: "basing-signal"
  }, "Possible bounce setup")))), data.verdict && (() => {
    const cls = data.verdict === "Sell now" ? "verdict-sell" : data.verdict === "Sell partial" ? "verdict-partial" : data.verdict === "Avoid" ? "verdict-avoid" : "verdict-wait";
    return /*#__PURE__*/React.createElement("div", {
      className: `basing-verdict ${cls}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "basing-verdict-label"
    }, data.verdict), /*#__PURE__*/React.createElement("div", {
      className: "basing-verdict-reason"
    }, data.verdict_reason));
  })(), /*#__PURE__*/React.createElement("div", {
    className: "basing-row1"
  }, /*#__PURE__*/React.createElement("div", {
    className: "basing-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "basing-stat-label",
    title: "Today's percent change from yesterday's close"
  }, "Today"), /*#__PURE__*/React.createElement("div", {
    className: `basing-stat-val ${livePct >= 0 ? "up" : "down"}`
  }, fmtPct(livePct))), /*#__PURE__*/React.createElement("div", {
    className: "basing-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "basing-stat-label",
    title: `Median close-to-prior-close % move on ${data.today_dow}s across the lookback window`
  }, "Typical ", data.today_dow, " close"), /*#__PURE__*/React.createElement("div", {
    className: "basing-stat-val"
  }, fmtPct(data.typical_dow.median)), /*#__PURE__*/React.createElement("div", {
    className: "basing-stat-sub",
    title: `10th to 90th percentile range of ${data.today_dow} closes`
  }, "range ", fmtPct(data.typical_dow.p10), " to ", fmtPct(data.typical_dow.p90), " · ", "n=", data.typical_dow.samples)), /*#__PURE__*/React.createElement("div", {
    className: "basing-stat"
  }, /*#__PURE__*/React.createElement("div", {
    className: "basing-stat-label",
    title: "Whether today is moving more than 1.5x the typical range for this weekday"
  }, "Status"), /*#__PURE__*/React.createElement("div", {
    className: `basing-stat-val ${data.stretched ? "down" : ""}`,
    title: data.stretched ? "Today's move is 1.5x larger than the typical range for this weekday — a potential mean-reversion candidate" : "Today's move is within the typical range for this weekday"
  }, data.stretched ? "Stretched" : "Normal"), /*#__PURE__*/React.createElement("div", {
    className: "basing-stat-sub",
    title: data.holding_base ? "The last 30 minutes have stayed within 0.5% of the Point of Control — a bounce or breakdown setup may be forming" : "Price is not yet consolidating near a high-volume level"
  }, data.holding_base ? "holding base near POC" : "not basing yet")), /*#__PURE__*/React.createElement("div", {
    className: "basing-stat basing-ohlv"
  }, /*#__PURE__*/React.createElement("div", {
    className: "basing-ohlv-line",
    title: "Today's open price"
  }, /*#__PURE__*/React.createElement("span", {
    className: "basing-ohlv-key"
  }, "Open:"), /*#__PURE__*/React.createElement("span", {
    className: "basing-ohlv-val"
  }, fmt$(data.session_open))), /*#__PURE__*/React.createElement("div", {
    className: "basing-ohlv-line",
    title: "Today's intraday high so far"
  }, /*#__PURE__*/React.createElement("span", {
    className: "basing-ohlv-key"
  }, "High:"), /*#__PURE__*/React.createElement("span", {
    className: "basing-ohlv-val up"
  }, fmt$(data.session_high), data.prev_close && data.session_high ? /*#__PURE__*/React.createElement("span", {
    className: "basing-ohlv-pct"
  }, " (", ((data.session_high - data.prev_close) / data.prev_close * 100).toFixed(2), "%)") : null)), /*#__PURE__*/React.createElement("div", {
    className: "basing-ohlv-line",
    title: "Today's intraday low so far"
  }, /*#__PURE__*/React.createElement("span", {
    className: "basing-ohlv-key"
  }, "Low:"), /*#__PURE__*/React.createElement("span", {
    className: "basing-ohlv-val down"
  }, fmt$(data.session_low), data.prev_close && data.session_low ? /*#__PURE__*/React.createElement("span", {
    className: "basing-ohlv-pct"
  }, " (", ((data.session_low - data.prev_close) / data.prev_close * 100).toFixed(2), "%)") : null)), /*#__PURE__*/React.createElement("div", {
    className: "basing-ohlv-line",
    title: "Total shares traded so far today"
  }, /*#__PURE__*/React.createElement("span", {
    className: "basing-ohlv-key"
  }, "Volume:"), /*#__PURE__*/React.createElement("span", {
    className: "basing-ohlv-val"
  }, (data.session_volume || 0).toLocaleString())), /*#__PURE__*/React.createElement("div", {
    className: "basing-ohlv-line",
    title: "Today's percent change from yesterday's close"
  }, /*#__PURE__*/React.createElement("span", {
    className: "basing-ohlv-key"
  }, "Change:"), /*#__PURE__*/React.createElement("span", {
    className: `basing-ohlv-val ${livePct >= 0 ? "up" : "down"}`
  }, fmtPct(livePct))))), bins.length > 0 ? /*#__PURE__*/React.createElement("div", {
    className: "basing-profile"
  }, /*#__PURE__*/React.createElement("div", {
    className: "basing-profile-header"
  }, /*#__PURE__*/React.createElement("div", {
    className: "basing-profile-title"
  }, viewMode === "time" ? "Time at price" : "Volume at price", showOverlay && viewMode === "time" ? " · with volume overlay" : "", " · today (15-min cells across session)"), /*#__PURE__*/React.createElement("div", {
    className: "basing-toolbar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "basing-toggle"
  }, /*#__PURE__*/React.createElement("button", {
    className: viewMode === "time" ? "active" : "",
    onClick: () => setViewMode("time"),
    title: "Show minutes spent at each price"
  }, "Time"), /*#__PURE__*/React.createElement("button", {
    className: viewMode === "volume" ? "active" : "",
    onClick: () => setViewMode("volume"),
    title: "Show shares traded at each price"
  }, "Volume")), /*#__PURE__*/React.createElement("button", {
    className: `basing-overlay-switch${showOverlay ? " on" : ""}`,
    onClick: () => setShowOverlay(o => !o),
    title: viewMode === "time" ? "Overlay total volume per price level" : "Overlay total time per price level"
  }, /*#__PURE__*/React.createElement("span", {
    className: "basing-overlay-switch-label"
  }, "Overlay"), /*#__PURE__*/React.createElement("span", {
    className: "basing-overlay-switch-track"
  }, /*#__PURE__*/React.createElement("span", {
    className: "basing-overlay-switch-knob"
  }))))), /*#__PURE__*/React.createElement("div", {
    className: "basing-heatmap-timeaxis"
  }, /*#__PURE__*/React.createElement("div", {
    className: "basing-heatmap-time-spacer"
  }), /*#__PURE__*/React.createElement("div", {
    className: "basing-heatmap-time-track"
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      gridColumn: "1"
    }
  }, "9:30"), /*#__PURE__*/React.createElement("span", {
    style: {
      gridColumn: "3"
    }
  }, "10:00"), /*#__PURE__*/React.createElement("span", {
    style: {
      gridColumn: "7"
    }
  }, "11:00"), /*#__PURE__*/React.createElement("span", {
    style: {
      gridColumn: "11"
    }
  }, "12:00"), /*#__PURE__*/React.createElement("span", {
    style: {
      gridColumn: "15"
    }
  }, "1:00"), /*#__PURE__*/React.createElement("span", {
    style: {
      gridColumn: "19"
    }
  }, "2:00"), /*#__PURE__*/React.createElement("span", {
    style: {
      gridColumn: "23"
    }
  }, "3:00"), /*#__PURE__*/React.createElement("span", {
    style: {
      gridColumn: "26",
      justifySelf: "end"
    }
  }, "4:00")), /*#__PURE__*/React.createElement("div", {
    className: "basing-heatmap-marker-spacer"
  })), /*#__PURE__*/React.createElement("div", {
    className: "basing-profile-rows"
  }, (() => {
    // Pick the heat array based on view mode. Compute max for scaling.
    const heatField = viewMode === "volume" ? "vol_heat" : "heat";
    const overlayField = viewMode === "volume" ? "heat" : "vol_heat";
    let maxHeat = 0;
    for (const b of bins) {
      const arr = b[heatField];
      if (arr) {
        for (const v of arr) {
          if (v > maxHeat) maxHeat = v;
        }
      }
    }
    if (maxHeat <= 0) maxHeat = 1;
    // For overlay: precompute per-row totals (sum across time cells)
    // and the max total so each row's overlay bar scales correctly.
    const overlayTotals = bins.map(b => {
      const arr = b[overlayField] || [];
      let s = 0;
      for (const v of arr) s += v;
      return s;
    });
    const maxOverlayTotal = Math.max(1, ...overlayTotals);
    // Identify the SINGLE row closest to live price for the
    // dashed current-price marker. Previously used a fixed $0.50
    // tolerance which caused 5+ rows to flag for low-priced
    // stocks where bin width was much smaller than $0.50.
    const livePriceForBand = livePrice ?? data.last_price;
    let closestIdx = -1;
    if (livePriceForBand && bins.length > 0) {
      let bestDist = Infinity;
      for (let k = 0; k < bins.length; k++) {
        const d = Math.abs(bins[k].price - livePriceForBand);
        if (d < bestDist) {
          bestDist = d;
          closestIdx = k;
        }
      }
    }
    return binsTopDown.map((b, i) => {
      const isPOC = data.poc_price && Math.abs(b.price - data.poc_price) < 0.0001;
      const isTPO = data.tpo_price && Math.abs(b.price - data.tpo_price) < 0.0001 && !isPOC;
      const inVA = data.value_area_low != null && data.value_area_high != null && b.price >= data.value_area_low && b.price <= data.value_area_high;
      // Convert top-down index back to original bins index for comparison
      const origIdx = bins.length - 1 - i;
      const isCurrent = origIdx === closestIdx;
      const heat = b[heatField] || [];
      // Per-row total of the overlay metric
      let rowOverlayTotal = 0;
      if (showOverlay) {
        const oarr = b[overlayField] || [];
        for (const v of oarr) rowOverlayTotal += v;
      }
      const overlayPct = showOverlay ? rowOverlayTotal / maxOverlayTotal * 100 : 0;
      const overlayLabel = viewMode === "volume" ? `${rowOverlayTotal.toFixed(1)} min total` : `${Math.round(rowOverlayTotal).toLocaleString()} shares total`;
      return /*#__PURE__*/React.createElement("div", {
        key: i,
        className: `basing-profile-row${inVA ? " in-va" : ""}${isPOC ? " is-poc" : ""}${isTPO ? " is-tpo" : ""}${isCurrent ? " is-current" : ""}`
      }, /*#__PURE__*/React.createElement("div", {
        className: "basing-profile-price"
      }, fmt$(b.price)), /*#__PURE__*/React.createElement("div", {
        className: "basing-heatmap-cells"
      }, showOverlay && rowOverlayTotal > 0 && /*#__PURE__*/React.createElement("div", {
        className: "basing-row-overlay",
        style: {
          width: `${Math.max(1, overlayPct)}%`
        },
        title: `${fmt$(b.price)} · ${overlayLabel}`
      }), heat.map((v, j) => {
        const ratio = v / maxHeat;
        const opacity = ratio === 0 ? 0 : Math.max(0.08, ratio);
        const totalMins = 9 * 60 + 30 + j * 15;
        const hh = Math.floor(totalMins / 60);
        const mm = totalMins % 60;
        const clock = `${hh > 12 ? hh - 12 : hh}:${mm.toString().padStart(2, "0")} ${hh >= 12 ? "PM" : "AM"}`;
        const primaryStr = viewMode === "volume" ? `${v.toLocaleString()} shares` : `${v.toFixed(1)} min`;
        const tip = v > 0 ? `${clock} · ${primaryStr} at ${fmt$(b.price)}` : `${clock} · no activity`;
        return /*#__PURE__*/React.createElement("div", {
          key: j,
          className: "basing-heatmap-cell",
          style: {
            backgroundColor: opacity > 0 ? `rgba(29, 158, 117, ${opacity})` : "transparent"
          },
          title: tip
        });
      })), /*#__PURE__*/React.createElement("div", {
        className: "basing-profile-marker"
      }, isPOC && /*#__PURE__*/React.createElement("span", {
        className: "basing-tag tag-poc",
        title: "Point of Control — price level with the most volume traded today"
      }, "POC"), isTPO && /*#__PURE__*/React.createElement("span", {
        className: "basing-tag tag-tpo",
        title: "Time Price Opportunity — price level where price spent the most time today"
      }, "TPO"), isCurrent && /*#__PURE__*/React.createElement("span", {
        className: "basing-tag tag-now",
        title: "Current price"
      }, "●")));
    });
  })()), /*#__PURE__*/React.createElement("div", {
    className: "basing-legend"
  }, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("i", {
    className: "legend-sw legend-heat-light"
  }), "brief ", viewMode === "volume" ? "trading" : "visit"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("i", {
    className: "legend-sw legend-heat-mid"
  }), "some ", viewMode === "volume" ? "volume" : "time"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("i", {
    className: "legend-sw legend-heat-dark"
  }), "most ", viewMode === "volume" ? "volume" : "time"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("i", {
    className: "legend-sw legend-va"
  }), "70% value area"), showOverlay && /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("i", {
    className: "legend-sw legend-overlay"
  }), viewMode === "volume" ? "time" : "volume", " overlay"))) : /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      padding: "16px 0"
    }
  }, "No intraday data yet."), /*#__PURE__*/React.createElement("div", {
    className: "basing-levels"
  }, /*#__PURE__*/React.createElement("div", {
    title: "Point of Control — price level with the most volume traded today"
  }, /*#__PURE__*/React.createElement("span", {
    className: "basing-levels-label"
  }, "POC"), /*#__PURE__*/React.createElement("span", {
    className: "basing-levels-val"
  }, fmt$(data.poc_price))), /*#__PURE__*/React.createElement("div", {
    title: "Time Price Opportunity — price level where price spent the most time today"
  }, /*#__PURE__*/React.createElement("span", {
    className: "basing-levels-label"
  }, "TPO"), /*#__PURE__*/React.createElement("span", {
    className: "basing-levels-val"
  }, fmt$(data.tpo_price))), /*#__PURE__*/React.createElement("div", {
    title: "Value Area High — top of the 70% volume zone"
  }, /*#__PURE__*/React.createElement("span", {
    className: "basing-levels-label"
  }, "VAH"), /*#__PURE__*/React.createElement("span", {
    className: "basing-levels-val"
  }, fmt$(data.value_area_high))), /*#__PURE__*/React.createElement("div", {
    title: "Value Area Low — bottom of the 70% volume zone"
  }, /*#__PURE__*/React.createElement("span", {
    className: "basing-levels-label"
  }, "VAL"), /*#__PURE__*/React.createElement("span", {
    className: "basing-levels-val"
  }, fmt$(data.value_area_low))), /*#__PURE__*/React.createElement("div", {
    title: "Current live price"
  }, /*#__PURE__*/React.createElement("span", {
    className: "basing-levels-label"
  }, "Now"), /*#__PURE__*/React.createElement("span", {
    className: "basing-levels-val"
  }, fmt$(livePrice ?? data.last_price)))));
}
function Recommendation({
  rec
}) {
  const icons = {
    success: "✓",
    warn: "!",
    info: "i",
    danger: "⚠"
  };
  return /*#__PURE__*/React.createElement("div", {
    className: `rec ${rec.kind}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "icon"
  }, icons[rec.kind]), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "title"
  }, rec.title), /*#__PURE__*/React.createElement("div", {
    className: "body"
  }, rec.body)));
}
function RecommendationPair({
  rec,
  strategyMode
}) {
  const mode = strategyMode || "both";
  const showCC = mode === "both" || mode === "cc";
  const showCSP = mode === "both" || mode === "csp";
  const icons = {
    success: "✓",
    warn: "!",
    info: "i",
    danger: "⚠"
  };
  const cc = rec && rec.cc ? rec.cc : {
    kind: rec?.kind || "info",
    title: rec?.title || "",
    body: rec?.body || ""
  };
  const csp = rec && rec.csp ? rec.csp : null;
  return /*#__PURE__*/React.createElement("div", {
    className: "rec-pair"
  }, showCC && /*#__PURE__*/React.createElement("div", {
    className: `rec rec-with-kicker ${cc.kind}`,
    title: "Timing verdict for selling covered calls. Combines weekly price-vs-median historicals with the analyst overlay (fresh upgrades, target proximity, trend)."
  }, /*#__PURE__*/React.createElement("div", {
    className: "rec-kicker"
  }, "For covered calls"), /*#__PURE__*/React.createElement("div", {
    className: "rec-row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "icon"
  }, icons[cc.kind]), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "title"
  }, cc.title), /*#__PURE__*/React.createElement("div", {
    className: "body"
  }, cc.body)))), showCSP && csp && /*#__PURE__*/React.createElement("div", {
    className: `rec rec-with-kicker ${csp.kind}`,
    title: "Timing verdict for selling cash-secured puts. Mirrors the CC engine with inverted directional bias: weakness favors short puts (rich premium, bounce bias), strength means wait. Analyst overlay also flipped: fresh upgrade reduces danger, fresh downgrade escalates it."
  }, /*#__PURE__*/React.createElement("div", {
    className: "rec-kicker"
  }, "For cash-secured puts"), /*#__PURE__*/React.createElement("div", {
    className: "rec-row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "icon"
  }, icons[csp.kind]), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "title"
  }, csp.title), /*#__PURE__*/React.createElement("div", {
    className: "body"
  }, csp.body)))));
}
function StrategyCard({
  rank,
  score,
  reason,
  tag,
  name,
  termKey,
  structure,
  stats,
  note,
  tone,
  legs,
  frontExpLabel,
  backExpLabel,
  frontDte,
  selected,
  onSelect,
  Term
}) {
  const toneColor = tone === "up" ? "var(--up)" : tone === "down" ? "var(--down)" : tone === "warn" ? "var(--warn)" : "var(--accent)";
  const isTop3 = rank <= 3;
  // Format each leg into a concrete trade ticket line.
  const tradeLines = (legs || []).map(L => {
    const action = L.qty < 0 ? "SELL" : "BUY";
    const qty = Math.abs(L.qty / 100); // contracts (or 100-share blocks for stock)
    const side = (L.type || "").toUpperCase();
    const isStock = side === "STOCK";
    // Front vs back: front_dte ± a few days = front, otherwise back.
    const isFront = !frontDte || Math.abs((L.dte || 0) - frontDte) <= 7;
    const expLabel = isStock ? "" : isFront ? frontExpLabel : backExpLabel;
    const dteText = isStock ? "" : L.dte != null ? `${L.dte}d` : "";
    return {
      action,
      qty,
      side,
      strike: L.strike,
      expLabel,
      dteText,
      premium: L.premium
    };
  });
  return /*#__PURE__*/React.createElement("div", {
    className: `strat-card ${selected ? "selected" : ""} ${isTop3 ? `top-${rank}` : ""}`,
    style: {
      borderTop: `2px solid ${toneColor}`
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "strat-head"
  }, /*#__PURE__*/React.createElement("span", {
    className: `strat-rank ${isTop3 ? "top" : ""}`
  }, "#", rank), /*#__PURE__*/React.createElement("span", {
    className: "strat-fit",
    title: `fit score ${score} of 100`
  }, /*#__PURE__*/React.createElement("span", {
    className: "strat-fit-bar"
  }, /*#__PURE__*/React.createElement("span", {
    className: "strat-fit-bar-fill",
    style: {
      width: `${score}%`,
      background: toneColor
    }
  })), /*#__PURE__*/React.createElement("span", {
    className: "strat-fit-num"
  }, score)), /*#__PURE__*/React.createElement("span", {
    className: "strat-tag",
    style: {
      color: toneColor,
      borderColor: `color-mix(in oklch, ${toneColor}, transparent 70%)`
    }
  }, tag)), /*#__PURE__*/React.createElement("div", {
    className: "strat-name"
  }, Term && termKey ? /*#__PURE__*/React.createElement(Term, {
    k: termKey
  }, name) : name), reason && /*#__PURE__*/React.createElement("div", {
    className: "strat-why"
  }, reason), tradeLines.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "strat-legs"
  }, tradeLines.map((L, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: `strat-leg ${L.action === "SELL" ? "sell" : "buy"}`
  }, /*#__PURE__*/React.createElement("span", {
    className: "leg-action"
  }, L.action), L.qty !== 1 && /*#__PURE__*/React.createElement("span", {
    className: "leg-qty"
  }, L.qty, "×"), /*#__PURE__*/React.createElement("span", {
    className: `leg-side ${L.side === "CALL" ? "call" : L.side === "PUT" ? "put" : "stock"}`
  }, L.side), /*#__PURE__*/React.createElement("span", {
    className: "leg-strike"
  }, L.side === "STOCK" ? "" : `$${L.strike.toFixed(2)}`), /*#__PURE__*/React.createElement("span", {
    className: "leg-exp"
  }, L.expLabel, L.dteText ? ` · ${L.dteText}` : ""), L.premium != null && L.premium > 0 && /*#__PURE__*/React.createElement("span", {
    className: "leg-prem"
  }, "$", L.premium.toFixed(2))))), /*#__PURE__*/React.createElement("div", {
    className: "strat-stats"
  }, stats.map(([k, v]) => /*#__PURE__*/React.createElement("div", {
    key: k,
    className: "strat-stat"
  }, /*#__PURE__*/React.createElement("span", null, k), /*#__PURE__*/React.createElement("b", null, v)))), /*#__PURE__*/React.createElement("div", {
    className: "strat-note"
  }, note), /*#__PURE__*/React.createElement("button", {
    className: "strat-pl-btn",
    onClick: onSelect
  }, selected ? "● Showing P/L" : "Show P/L →"));
}
function PositionsCard({
  positions,
  setPositions,
  showAdd,
  setShowAdd,
  filter,
  setFilter,
  ticker,
  currentPrice,
  calls,
  puts,
  activeExpDate,
  sugCall,
  sugPut,
  callAtSug,
  putAtSug,
  FRONT_DTE,
  Term,
  fmt$,
  apiFetch
}) {
  const bsPrice = window.OptionStrats && window.OptionStrats.bsPrice;
  const skey = s => (Math.round(s * 100) / 100).toFixed(2);
  const callMap = React.useMemo(() => Object.fromEntries(calls.map(c => [skey(c.strike), c])), [calls]);
  const putMap = React.useMemo(() => Object.fromEntries(puts.map(p => [skey(p.strike), p])), [puts]);

  // Compute live state for a position. Returns { currentPremium, pnl,
  // pnlPct, dte, status }. Status is "live" if we can value via chain,
  // "estimate" if we used Black-Scholes, "stale" if we have no data.
  function valuate(p) {
    if (p.closed) {
      const pnl = p.qty * ((p.closedPremium ?? 0) - (p.entryPremium ?? 0));
      return {
        currentPremium: p.closedPremium,
        pnl,
        pnlPct: pnl / Math.max(0.01, Math.abs(p.qty * p.entryPremium)) * 100,
        dte: 0,
        status: "closed"
      };
    }
    const today = new Date();
    let dte = 0;
    if (p.expiration) {
      const exp = new Date(p.expiration + "T16:00:00");
      dte = Math.max(0, Math.round((exp - today) / 86400000));
    }
    const onActiveTicker = (p.ticker || "").toUpperCase() === ticker.toUpperCase();
    let currentPremium = null;
    let status = "stale";
    if (p.type === "stock") {
      if (onActiveTicker) {
        currentPremium = currentPrice;
        status = "live";
      }
    } else if (onActiveTicker) {
      // Try chain match first (same expiration)
      const expMatches = p.expiration === activeExpDate.toISOString().slice(0, 10);
      const map = p.type === "call" ? callMap : putMap;
      const row = map[skey(p.strike)];
      if (expMatches && row) {
        const mid = row.bid > 0 ? (row.bid + row.ask) / 2 : row.last || row.ask || 0;
        currentPremium = mid;
        status = "live";
      } else if (bsPrice && p.iv && dte > 0) {
        const T = dte / 365.0;
        currentPremium = bsPrice(currentPrice, p.strike, T, p.iv, p.type === "call");
        status = "estimate";
      }
    }
    let pnl = 0,
      pnlPct = 0;
    if (currentPremium != null) {
      pnl = p.qty * (currentPremium - (p.entryPremium ?? 0));
      const cost = Math.abs(p.qty * (p.entryPremium ?? 0));
      pnlPct = cost > 0 ? pnl / cost * 100 : 0;
    }
    // Pull live delta from chain row when available — used for roll
    // alerts. Short option delta absolute value > 0.40 with DTE < 7
    // means the position is close to in-the-money near expiration —
    // classic roll trigger to avoid assignment.
    let currentDelta = null;
    if (p.type !== "stock" && (p.ticker || "").toUpperCase() === ticker.toUpperCase()) {
      const map = p.type === "call" ? callMap : putMap;
      const row = map[skey(p.strike)];
      if (row && row.delta != null) currentDelta = row.delta;
    }
    // Roll flag: short option, < 7 DTE, |delta| > 0.40, not closed
    let rollFlag = null;
    if (!p.closed && p.type !== "stock" && p.qty < 0 && currentDelta != null && dte > 0 && dte <= 7 && Math.abs(currentDelta) >= 0.40) {
      rollFlag = `Approaching assignment: |Δ|=${Math.abs(currentDelta).toFixed(2)} with ${dte}d left. Consider rolling out.`;
    }
    return {
      currentPremium,
      pnl,
      pnlPct,
      dte,
      status,
      currentDelta,
      rollFlag
    };
  }

  // Filter
  const visible = React.useMemo(() => {
    let v = positions;
    if (filter === "open") v = v.filter(p => !p.closed);
    if (filter === "this") v = v.filter(p => (p.ticker || "").toUpperCase() === ticker.toUpperCase());
    // Most recent first
    return [...v].sort((a, b) => (b.entryDate || "").localeCompare(a.entryDate || ""));
  }, [positions, filter, ticker]);

  // ── Push alerts on roll flag (v1.16) ──────────────────────────
  // Watches positions for newly-flagged rolls and POSTs to the
  // backend, which dedupes via _SENT_ALERTS_PATH so we do not blast
  // the phone every poll. Only fires for positions on the active
  // ticker (where we have live delta from the chain). Best effort:
  // any error is logged and ignored.
  const pushedKeysRef = React.useRef(new Set());
  React.useEffect(() => {
    if (!apiFetch) return;
    for (const p of positions) {
      if (p.closed || p.type === "stock" || (p.qty || 0) >= 0) continue;
      if ((p.ticker || "").toUpperCase() !== ticker.toUpperCase()) continue;
      const v = valuate(p);
      if (!v.rollFlag) continue;
      const key = `${p.id}|${p.expiration}|${p.strike}`;
      if (pushedKeysRef.current.has(key)) continue;
      pushedKeysRef.current.add(key);
      apiFetch("/api/push/roll_flag", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          ticker: p.ticker,
          position_id: p.id,
          strike: p.strike,
          expiration: p.expiration,
          dte: v.dte,
          delta: v.currentDelta != null ? Math.abs(v.currentDelta).toFixed(2) : null,
          type: p.type
        })
      }).catch(e => console.warn("push roll_flag failed", e));
    }
    // Re-run when positions or ticker change. Live delta lives on
    // chain rows (callMap/putMap) but those are derived from positions
    // + ticker so this dependency set is sufficient.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positions, ticker, callMap, putMap]);

  // Aggregate P&L for visible open positions (skip closed and stale)
  const aggPnl = visible.reduce((acc, p) => {
    if (p.closed) return acc;
    const v = valuate(p);
    if (v.status === "live" || v.status === "estimate") acc.total += v.pnl;
    return acc;
  }, {
    total: 0
  });
  function addPosition(p) {
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    setPositions(list => [...list, {
      ...p,
      id,
      closed: false,
      entryDate: p.entryDate || new Date().toISOString().slice(0, 10)
    }]);
    setShowAdd(false);
  }
  function deletePosition(id) {
    if (!confirm("Delete this position permanently?")) return;
    setPositions(list => list.filter(p => p.id !== id));
  }
  function closePosition(p) {
    const v = valuate(p);
    const suggested = v.currentPremium != null ? v.currentPremium.toFixed(2) : "";
    const promptMsg = `Closing ${p.ticker} ${p.type} ${p.strike ? "$" + p.strike : ""}\n\nClose price ($/share or $/contract):`;
    const px = window.prompt(promptMsg, suggested);
    if (px == null) return;
    const closedPremium = parseFloat(px);
    if (isNaN(closedPremium)) {
      alert("Invalid number.");
      return;
    }
    setPositions(list => list.map(x => x.id === p.id ? {
      ...x,
      closed: true,
      closedPremium,
      closedDate: new Date().toISOString().slice(0, 10)
    } : x));
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "card",
    style: {
      marginBottom: "var(--row-gap)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "My positions"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, visible.length === 0 ? "No positions logged" : `${visible.filter(p => !p.closed).length} open · live P/L `, visible.length > 0 && /*#__PURE__*/React.createElement("span", {
    className: aggPnl.total >= 0 ? "up" : "down",
    style: {
      fontFamily: "var(--font-mono)"
    }
  }, aggPnl.total >= 0 ? "+" : "", "$", aggPnl.total.toFixed(2)))), /*#__PURE__*/React.createElement("div", {
    className: "pos-toolbar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "seg"
  }, /*#__PURE__*/React.createElement("button", {
    className: filter === "open" ? "active" : "",
    onClick: () => setFilter("open")
  }, "Open"), /*#__PURE__*/React.createElement("button", {
    className: filter === "this" ? "active" : "",
    onClick: () => setFilter("this")
  }, ticker), /*#__PURE__*/React.createElement("button", {
    className: filter === "all" ? "active" : "",
    onClick: () => setFilter("all")
  }, "All")), /*#__PURE__*/React.createElement("button", {
    className: "pos-add-btn",
    onClick: () => setShowAdd(s => !s)
  }, showAdd ? "× Cancel" : "+ Add position"))), showAdd && /*#__PURE__*/React.createElement(AddPositionForm, {
    ticker: ticker,
    activeExpDate: activeExpDate,
    sugCall: sugCall,
    sugPut: sugPut,
    callAtSug: callAtSug,
    putAtSug: putAtSug,
    FRONT_DTE: FRONT_DTE,
    onAdd: addPosition,
    onCancel: () => setShowAdd(false)
  }), visible.length === 0 && !showAdd && /*#__PURE__*/React.createElement("div", {
    className: "pos-empty"
  }, /*#__PURE__*/React.createElement("div", null, "Log a position to track live P&L, days to expiration, and net Greeks."), /*#__PURE__*/React.createElement("button", {
    className: "pos-add-btn",
    onClick: () => setShowAdd(true)
  }, "+ Add your first position")), visible.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "pos-list"
  }, visible.map(p => {
    const v = valuate(p);
    const sideLabel = p.qty < 0 ? "SHORT" : "LONG";
    const typeLabel = (p.type || "").toUpperCase();
    const expLabel = p.expiration ? new Date(p.expiration + "T16:00:00").toLocaleDateString("en-US", {
      month: "short",
      day: "numeric"
    }) : "";
    return /*#__PURE__*/React.createElement("div", {
      key: p.id,
      className: `pos-row ${p.closed ? "closed" : ""}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "pos-row-main"
    }, /*#__PURE__*/React.createElement("div", {
      className: "pos-line1"
    }, /*#__PURE__*/React.createElement("span", {
      className: "pos-tk"
    }, p.ticker), /*#__PURE__*/React.createElement("span", {
      className: `pos-side ${p.qty < 0 ? "short" : "long"}`
    }, sideLabel), /*#__PURE__*/React.createElement("span", {
      className: `pos-type ${typeLabel === "CALL" ? "call" : typeLabel === "PUT" ? "put" : "stock"}`
    }, typeLabel), p.strike != null && /*#__PURE__*/React.createElement("span", {
      className: "pos-k"
    }, "$", p.strike.toFixed(2)), expLabel && /*#__PURE__*/React.createElement("span", {
      className: "pos-exp"
    }, expLabel), !p.closed && v.dte != null && p.expiration && /*#__PURE__*/React.createElement("span", {
      className: "pos-dte"
    }, v.dte, "d"), /*#__PURE__*/React.createElement("span", {
      className: "pos-qty"
    }, Math.abs(p.qty / (p.type === "stock" ? 1 : 100)), "× ", p.type === "stock" ? "shares" : "ctr")), /*#__PURE__*/React.createElement("div", {
      className: "pos-line2"
    }, /*#__PURE__*/React.createElement("span", null, "Entry ", /*#__PURE__*/React.createElement("b", null, "$", (p.entryPremium ?? 0).toFixed(2))), v.currentPremium != null && /*#__PURE__*/React.createElement("span", null, "Now ", /*#__PURE__*/React.createElement("b", null, "$", v.currentPremium.toFixed(2))), v.currentDelta != null && /*#__PURE__*/React.createElement("span", {
      title: "Live |delta| of the position. For short OTM options 0.20-0.30 is the entry zone; > 0.40 with low DTE is a roll trigger."
    }, "|Δ| ", /*#__PURE__*/React.createElement("b", null, Math.abs(v.currentDelta).toFixed(2))), /*#__PURE__*/React.createElement("span", {
      className: "pos-status"
    }, v.status === "live" ? "● live" : v.status === "estimate" ? "○ estimate" : v.status === "closed" ? "✓ closed" : "load " + p.ticker)), v.rollFlag && /*#__PURE__*/React.createElement("div", {
      className: "pos-roll-flag",
      title: "Position is approaching in-the-money near expiration. Common short-options heuristic: roll out (and possibly down for puts, up for calls) when DTE < 7 and |Δ| > 0.40 to defer assignment and collect more premium."
    }, "⚠ ", v.rollFlag)), /*#__PURE__*/React.createElement("div", {
      className: "pos-row-pnl"
    }, v.currentPremium != null ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      className: `pos-pnl ${v.pnl >= 0 ? "up" : "down"}`
    }, v.pnl >= 0 ? "+" : "", "$", v.pnl.toFixed(2)), /*#__PURE__*/React.createElement("div", {
      className: `pos-pnl-pct ${v.pnl >= 0 ? "up" : "down"}`
    }, v.pnl >= 0 ? "+" : "", v.pnlPct.toFixed(1), "%")) : /*#__PURE__*/React.createElement("div", {
      className: "pos-pnl-na"
    }, "—")), /*#__PURE__*/React.createElement("div", {
      className: "pos-row-actions"
    }, !p.closed && /*#__PURE__*/React.createElement("button", {
      className: "pos-action",
      onClick: () => closePosition(p)
    }, "Close"), /*#__PURE__*/React.createElement("button", {
      className: "pos-action danger",
      onClick: () => deletePosition(p.id)
    }, "×")));
  })));
}
function AddPositionForm({
  ticker,
  activeExpDate,
  sugCall,
  sugPut,
  callAtSug,
  putAtSug,
  FRONT_DTE,
  onAdd,
  onCancel
}) {
  const [form, setForm] = React.useState({
    ticker: ticker,
    type: "call",
    side: "short",
    // long | short
    qty: 1,
    // number of contracts (or 100-share blocks for stock)
    strike: "",
    entryPremium: "",
    expiration: activeExpDate.toISOString().slice(0, 10),
    iv: ""
  });
  function setField(k, v) {
    setForm(f => ({
      ...f,
      [k]: v
    }));
  }
  function fillFromSuggestion(side) {
    if (side === "call") {
      setForm(f => ({
        ...f,
        type: "call",
        side: "short",
        qty: 1,
        strike: sugCall.toFixed(2),
        entryPremium: ((callAtSug.bid + callAtSug.ask) / 2 || callAtSug.last || 0).toFixed(2),
        expiration: activeExpDate.toISOString().slice(0, 10),
        iv: callAtSug.iv ? callAtSug.iv.toFixed(3) : ""
      }));
    } else {
      setForm(f => ({
        ...f,
        type: "put",
        side: "short",
        qty: 1,
        strike: sugPut.toFixed(2),
        entryPremium: ((putAtSug.bid + putAtSug.ask) / 2 || putAtSug.last || 0).toFixed(2),
        expiration: activeExpDate.toISOString().slice(0, 10),
        iv: putAtSug.iv ? putAtSug.iv.toFixed(3) : ""
      }));
    }
  }
  function submit() {
    const tk = (form.ticker || "").toUpperCase().trim();
    if (!tk) return alert("Ticker required.");
    const qtyNum = parseFloat(form.qty) || 0;
    if (qtyNum <= 0) return alert("Quantity must be positive.");
    const entryNum = parseFloat(form.entryPremium);
    if (isNaN(entryNum)) return alert("Entry premium required.");
    const sharesPerContract = form.type === "stock" ? 1 : 100;
    const qty = (form.side === "short" ? -1 : 1) * qtyNum * sharesPerContract;
    const strike = form.type === "stock" ? null : parseFloat(form.strike);
    if (form.type !== "stock" && (isNaN(strike) || strike <= 0)) return alert("Strike required for options.");
    onAdd({
      ticker: tk,
      type: form.type,
      qty,
      strike,
      entryPremium: entryNum,
      expiration: form.type === "stock" ? null : form.expiration,
      iv: form.iv ? parseFloat(form.iv) : null
    });
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "pos-add-form"
  }, /*#__PURE__*/React.createElement("div", {
    className: "pos-form-quick"
  }, "Quick fill from current setup:", /*#__PURE__*/React.createElement("button", {
    className: "pos-quick-btn",
    onClick: () => fillFromSuggestion("call")
  }, "Suggested call ($", sugCall.toFixed(2), ")"), /*#__PURE__*/React.createElement("button", {
    className: "pos-quick-btn",
    onClick: () => fillFromSuggestion("put")
  }, "Suggested put ($", sugPut.toFixed(2), ")")), /*#__PURE__*/React.createElement("div", {
    className: "pos-form-grid"
  }, /*#__PURE__*/React.createElement("label", null, /*#__PURE__*/React.createElement("span", null, "Ticker"), /*#__PURE__*/React.createElement("input", {
    value: form.ticker,
    onChange: e => setField("ticker", e.target.value.toUpperCase())
  })), /*#__PURE__*/React.createElement("label", null, /*#__PURE__*/React.createElement("span", null, "Type"), /*#__PURE__*/React.createElement("select", {
    value: form.type,
    onChange: e => setField("type", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "call"
  }, "Call"), /*#__PURE__*/React.createElement("option", {
    value: "put"
  }, "Put"), /*#__PURE__*/React.createElement("option", {
    value: "stock"
  }, "Stock"))), /*#__PURE__*/React.createElement("label", null, /*#__PURE__*/React.createElement("span", null, "Side"), /*#__PURE__*/React.createElement("select", {
    value: form.side,
    onChange: e => setField("side", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "short"
  }, "Short / Sold"), /*#__PURE__*/React.createElement("option", {
    value: "long"
  }, "Long / Bought"))), /*#__PURE__*/React.createElement("label", null, /*#__PURE__*/React.createElement("span", null, "Qty (", form.type === "stock" ? "shares × 100" : "contracts", ")"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    min: "1",
    step: "1",
    value: form.qty,
    onChange: e => setField("qty", e.target.value)
  })), form.type !== "stock" && /*#__PURE__*/React.createElement("label", null, /*#__PURE__*/React.createElement("span", null, "Strike"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "0.5",
    value: form.strike,
    onChange: e => setField("strike", e.target.value)
  })), /*#__PURE__*/React.createElement("label", null, /*#__PURE__*/React.createElement("span", null, "Entry premium ", form.type === "stock" ? "(share price)" : "($/share)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "0.01",
    value: form.entryPremium,
    onChange: e => setField("entryPremium", e.target.value)
  })), form.type !== "stock" && /*#__PURE__*/React.createElement("label", null, /*#__PURE__*/React.createElement("span", null, "Expiration"), /*#__PURE__*/React.createElement("input", {
    type: "date",
    value: form.expiration,
    onChange: e => setField("expiration", e.target.value)
  })), form.type !== "stock" && /*#__PURE__*/React.createElement("label", null, /*#__PURE__*/React.createElement("span", null, "IV at entry (optional, e.g. 0.30)"), /*#__PURE__*/React.createElement("input", {
    type: "number",
    step: "0.01",
    min: "0",
    max: "5",
    value: form.iv,
    onChange: e => setField("iv", e.target.value),
    placeholder: "for BS pricing"
  }))), /*#__PURE__*/React.createElement("div", {
    className: "pos-form-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "pos-add-btn primary",
    onClick: submit
  }, "Add position"), /*#__PURE__*/React.createElement("button", {
    className: "pos-add-btn",
    onClick: onCancel
  }, "Cancel")));
}

// ──────────────────────────────────────────────────────────────────────
// Market Calendar — watchlist earnings calendar (weekly grid of stock
// cards) + economic calendar. A scannable "market command center" for the
// week ahead. Backed by /api/market_calendar/{earnings,economic} and a
// lazy /api/market_calendar/earnings_extra for the heavy per-symbol moves.
// ──────────────────────────────────────────────────────────────────────

// ── shared little formatters ──────────────────────────────────────────
function mcEtToday() {
  // The trading day in US/Eastern, as YYYY-MM-DD, regardless of the
  // browser's own timezone.
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York"
    }).format(new Date());
  } catch (_) {
    return new Date().toISOString().slice(0, 10);
  }
}
function mcDateObj(s) {
  return new Date(String(s).slice(0, 10) + "T12:00:00");
}
function mcMondayOf(d) {
  const x = new Date(d.getTime());
  const wd = (x.getDay() + 6) % 7; // 0 = Monday
  x.setDate(x.getDate() - wd);
  x.setHours(12, 0, 0, 0);
  return x;
}
function mcIso(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function mcWeekday(s) {
  return mcDateObj(s).toLocaleDateString(undefined, {
    weekday: "short"
  });
}
function mcDayLabel(s) {
  return mcDateObj(s).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric"
  });
}
function mcPct(v, d = 2) {
  return v == null || isNaN(v) ? "—" : `${v >= 0 ? "+" : ""}${(Math.round(v * 100) / 100).toFixed(d)}%`;
}
function mcEps(v) {
  return v == null || isNaN(v) ? "—" : `${v < 0 ? "-$" : "$"}${Math.abs(v).toFixed(2)}`;
}
function mcBigUSD(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${Math.round(v).toLocaleString()}`;
}
function mcInt(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return String(Math.round(v));
}
// Report-time chip styling.
function mcTimeMeta(t, reported) {
  if (reported) return {
    cls: "mc-rt-reported",
    label: "Reported",
    dot: "●"
  };
  if (t === "BMO") return {
    cls: "mc-rt-bmo",
    label: "Before Open",
    dot: "☀"
  };
  if (t === "AMC") return {
    cls: "mc-rt-amc",
    label: "After Close",
    dot: "🌙"
  };
  return {
    cls: "mc-rt-tas",
    label: "Time TBD",
    dot: "•"
  };
}

// One stock card inside a weekday column.
function MarketEarningsCard({
  e,
  expanded,
  extra,
  live,
  compact,
  onToggle,
  onSwitchTicker
}) {
  const liveLast = live && live.last != null ? live.last : e.last;
  const liveOpen = live && live.open != null ? live.open : e.open;
  const liveChg = live && live.chg != null ? live.chg : e.change;
  const fromOpen = liveOpen && liveLast != null ? (liveLast - liveOpen) / liveOpen * 100 : null;
  const rt = mcTimeMeta(e.report_time, e.reported);
  const big = (e.market_cap || 0) >= 10e9; // index-mover flag
  const mega = (e.market_cap || 0) >= 200e9;
  const open = expanded;
  return /*#__PURE__*/React.createElement("div", {
    className: `mc-ecard ${rt.cls} ${open ? "open" : ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "mc-ecard-top",
    onClick: onToggle,
    title: "Click to expand earnings detail"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mc-ecard-id"
  }, /*#__PURE__*/React.createElement("button", {
    className: "mc-sym",
    onClick: ev => {
      ev.stopPropagation();
      onSwitchTicker && onSwitchTicker(e.symbol);
    },
    title: `Open ${e.symbol} on the Trade tab`
  }, e.symbol), mega ? /*#__PURE__*/React.createElement("span", {
    className: "mc-star",
    title: "Mega-cap — high-importance print"
  }, "★") : big ? /*#__PURE__*/React.createElement("span", {
    className: "mc-star dim",
    title: "Large-cap — notable print"
  }, "★") : null), /*#__PURE__*/React.createElement("span", {
    className: `mc-rt-badge ${rt.cls}`,
    title: rt.label
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-rt-dot"
  }, rt.dot), rt.label)), !compact && e.company ? /*#__PURE__*/React.createElement("div", {
    className: "mc-co",
    title: e.company
  }, e.company) : null, /*#__PURE__*/React.createElement("div", {
    className: "mc-ecard-quick"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-cap",
    title: "Market cap"
  }, fmtMktCap(e.market_cap)), /*#__PURE__*/React.createElement("span", {
    className: `mc-fo ${fromOpen == null ? "" : fromOpen >= 0 ? "up" : "down"}`,
    title: "% from today's open"
  }, mcPct(fromOpen))), !compact ? /*#__PURE__*/React.createElement("div", {
    className: "mc-badges"
  }, e.sector ? /*#__PURE__*/React.createElement("span", {
    className: "mc-tag mc-tag-sector",
    title: `Sector: ${e.sector}`
  }, e.sector) : null, e.industry ? /*#__PURE__*/React.createElement("span", {
    className: "mc-tag mc-tag-ind",
    title: `Industry: ${e.industry}`
  }, e.industry) : null) : null, open ? /*#__PURE__*/React.createElement("div", {
    className: "mc-ecard-detail"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mc-stat-grid"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "EPS est."), /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-v"
  }, mcEps(e.eps_estimate))), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "EPS act."), /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-v"
  }, mcEps(e.eps_actual))), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "EPS surp."), /*#__PURE__*/React.createElement("span", {
    className: `mc-stat-v ${e.eps_surprise == null ? "" : e.eps_surprise >= 0 ? "up" : "down"}`
  }, mcPct(e.eps_surprise))), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "Rev est."), /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-v"
  }, mcBigUSD(e.revenue_estimate))), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "Rev act."), /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-v"
  }, mcBigUSD(e.revenue_actual))), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "Rev surp."), /*#__PURE__*/React.createElement("span", {
    className: `mc-stat-v ${e.revenue_surprise == null ? "" : e.revenue_surprise >= 0 ? "up" : "down"}`
  }, mcPct(e.revenue_surprise))), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "Price"), /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-v"
  }, liveLast == null ? "—" : `$${Number(liveLast).toFixed(2)}`)), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "Change"), /*#__PURE__*/React.createElement("span", {
    className: `mc-stat-v ${liveChg == null ? "" : liveChg >= 0 ? "up" : "down"}`
  }, mcPct(liveChg))), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "% from open"), /*#__PURE__*/React.createElement("span", {
    className: `mc-stat-v ${fromOpen == null ? "" : fromOpen >= 0 ? "up" : "down"}`
  }, mcPct(fromOpen))), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "WTD"), /*#__PURE__*/React.createElement("span", {
    className: `mc-stat-v ${e.wtd == null ? "" : e.wtd >= 0 ? "up" : "down"}`
  }, mcPct(e.wtd))), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "MTD"), /*#__PURE__*/React.createElement("span", {
    className: `mc-stat-v ${e.mtd == null ? "" : e.mtd >= 0 ? "up" : "down"}`
  }, mcPct(e.mtd))), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "YTD"), /*#__PURE__*/React.createElement("span", {
    className: `mc-stat-v ${e.ytd == null ? "" : e.ytd >= 0 ? "up" : "down"}`
  }, mcPct(e.ytd)))), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat-grid mc-stat-extra"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "Implied move"), /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-v accent"
  }, extra ? extra.implied_move_pct == null ? "—" : `±${extra.implied_move_pct}%` : "…")), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "Avg post-ER"), /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-v"
  }, extra ? extra.avg_post_earnings_move_pct == null ? "—" : `${extra.avg_post_earnings_move_pct}%` : "…")), /*#__PURE__*/React.createElement("div", {
    className: "mc-stat"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-k"
  }, "Options vol"), /*#__PURE__*/React.createElement("span", {
    className: "mc-stat-v"
  }, extra ? mcInt(extra.options_volume) : "…")))) : null);
}
function MarketCalendarCard({
  apiFetch,
  onSwitchTicker
}) {
  // ── data ────────────────────────────────────────────────────────────
  const [earn, setEarn] = useState(null);
  const [econ, setEcon] = useState(null);
  const [loadingE, setLoadingE] = useState(false);
  const [loadingM, setLoadingM] = useState(false);
  const [err, setErr] = useState(null);
  const [extras, setExtras] = useState({}); // symbol -> {implied_move_pct, ...}
  const [liveQ, setLiveQ] = useState({}); // symbol -> {last, open, chg}
  const [expanded, setExpanded] = useState({}); // symbol -> bool

  // ── earnings controls ───────────────────────────────────────────────
  const [weekOff, setWeekOff] = useState(0); // 0 = current week, 1 = next, ...
  const [view, setView] = useState("expanded"); // "compact" | "expanded"
  const [fSector, setFSector] = useState("all");
  const [fIndustry, setFIndustry] = useState("all");
  const [fMcap, setFMcap] = useState("all");
  const [sortKey, setSortKey] = useState("mcap"); // mcap | move | optvol | fromopen
  // ── economic controls ───────────────────────────────────────────────
  const [econImp, setEconImp] = useState("med"); // all | med | high

  const loadEarn = () => {
    setLoadingE(true);
    setErr(null);
    apiFetch("/api/market_calendar/earnings?days=35").then(r => r.json()).then(d => {
      setEarn(d);
    }).catch(e => setErr(String(e))).finally(() => setLoadingE(false));
  };
  const loadEcon = () => {
    setLoadingM(true);
    apiFetch("/api/market_calendar/economic?days=28").then(r => r.json()).then(d => {
      setEcon(d);
    }).catch(() => {}).finally(() => setLoadingM(false));
  };
  useEffect(() => {
    loadEarn();
    loadEcon();
  }, []);
  const entries = earn && Array.isArray(earn.entries) ? earn.entries : [];
  const today = mcEtToday();

  // Week window — Mon..Fri for the selected offset.
  const weekDays = useMemo(() => {
    const base = mcMondayOf(new Date(today + "T12:00:00"));
    base.setDate(base.getDate() + weekOff * 7);
    const out = [];
    for (let i = 0; i < 5; i++) {
      const d = new Date(base.getTime());
      d.setDate(d.getDate() + i);
      out.push(mcIso(d));
    }
    return out;
  }, [today, weekOff]);
  const weekSet = useMemo(() => new Set(weekDays), [weekDays]);

  // Entries inside the selected week, after filters.
  const weekEntries = useMemo(() => {
    return entries.filter(e => weekSet.has(e.earnings_date) && (fSector === "all" || e.sector === fSector) && (fIndustry === "all" || e.industry === fIndustry) && (fMcap === "all" || (MCAP_PRED[fMcap] || MCAP_PRED.all)(e.market_cap || 0)));
  }, [entries, weekSet, fSector, fIndustry, fMcap]);

  // Sorting comparator shared by the grid columns and highlight rails.
  const sortVal = e => {
    if (sortKey === "move") {
      const x = extras[e.symbol];
      return x && x.implied_move_pct != null ? x.implied_move_pct : -1;
    }
    if (sortKey === "optvol") {
      const x = extras[e.symbol];
      return x && x.options_volume != null ? x.options_volume : -1;
    }
    if (sortKey === "fromopen") {
      const q = liveQ[e.symbol];
      const op = q && q.open != null ? q.open : e.open;
      const la = q && q.last != null ? q.last : e.last;
      return op && la != null ? Math.abs((la - op) / op) : -1;
    }
    return e.market_cap || 0;
  };
  const sortEntries = arr => arr.slice().sort((a, b) => sortVal(b) - sortVal(a));

  // Background fill of the heavy per-symbol extras for the visible week,
  // throttled so we never hammer the option-chain endpoint.
  useEffect(() => {
    let cancelled = false;
    const need = weekEntries.map(e => e.symbol).filter(s => !(s in extras));
    if (!need.length) return;
    let i = 0;
    const CONC = 3;
    const runOne = async () => {
      while (!cancelled && i < need.length) {
        const sym = need[i++];
        try {
          const r = await apiFetch(`/api/market_calendar/earnings_extra?symbol=${encodeURIComponent(sym)}`);
          const d = await r.json();
          if (!cancelled) setExtras(prev => ({
            ...prev,
            [sym]: d || {}
          }));
        } catch (_) {
          if (!cancelled) setExtras(prev => ({
            ...prev,
            [sym]: {}
          }));
        }
      }
    };
    const ps = [];
    for (let k = 0; k < CONC; k++) ps.push(runOne());
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line
  }, [weekEntries]);

  // Live quote overlay for the visible week (current price + % from open).
  useEffect(() => {
    let stop = false,
      timer = null;
    const syms = Array.from(new Set(weekEntries.map(e => e.symbol)));
    if (!syms.length) return;
    const tick = async () => {
      try {
        const next = {};
        for (let i = 0; i < syms.length; i += 25) {
          const batch = syms.slice(i, i + 25);
          const r = await apiFetch(`/api/quote?tickers=${batch.join(",")}`);
          const d = await r.json();
          const res = d && d.results || {};
          for (const s of batch) {
            const q = res[s];
            if (q) next[s] = {
              last: q.last,
              open: q.open != null ? q.open : null,
              chg: q.change_pct != null ? q.change_pct : null
            };
          }
        }
        if (!stop) setLiveQ(next);
      } catch (_) {}
      if (!stop) timer = setTimeout(tick, 30000);
    };
    tick();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [weekEntries]);
  const toggle = sym => setExpanded(p => ({
    ...p,
    [sym]: !p[sym]
  }));

  // Highlight rails (operate on the filtered week).
  const todayEntries = sortEntries(weekEntries.filter(e => e.earnings_date === today));
  const importantEntries = weekEntries.slice().sort((a, b) => (b.market_cap || 0) - (a.market_cap || 0)).slice(0, 6);
  const moveEntries = weekEntries.filter(e => extras[e.symbol] && extras[e.symbol].implied_move_pct != null).sort((a, b) => extras[b.symbol].implied_move_pct - extras[a.symbol].implied_move_pct).slice(0, 6);
  const sectors = earn && earn.sectors || [];
  const industries = earn && earn.industries || [];
  const weekLabel = weekOff === 0 ? "This week" : weekOff === 1 ? "Next week" : `+${weekOff} weeks`;
  const scanning = earn && earn.board_status && earn.board_status.scanning;

  // ── economic calendar grouped by date ───────────────────────────────
  const econEvents = econ && Array.isArray(econ.events) ? econ.events : [];
  const econFiltered = econEvents.filter(ev => econImp === "all" ? true : econImp === "high" ? ev.importance === "high" : ev.importance !== "low");
  const econByDate = useMemo(() => {
    const m = new Map();
    for (const ev of econFiltered) {
      if (!m.has(ev.date)) m.set(ev.date, []);
      m.get(ev.date).push(ev);
    }
    return Array.from(m.entries());
  }, [econFiltered]);
  const impMeta = imp => imp === "high" ? {
    cls: "mc-imp-high",
    label: "High"
  } : imp === "medium" ? {
    cls: "mc-imp-med",
    label: "Med"
  } : {
    cls: "mc-imp-low",
    label: "Low"
  };
  const miniCard = e => {
    const live = liveQ[e.symbol];
    const liveLast = live && live.last != null ? live.last : e.last;
    const liveOpen = live && live.open != null ? live.open : e.open;
    const fo = liveOpen && liveLast != null ? (liveLast - liveOpen) / liveOpen * 100 : null;
    const x = extras[e.symbol];
    const rt = mcTimeMeta(e.report_time, e.reported);
    return /*#__PURE__*/React.createElement("button", {
      key: e.symbol,
      className: `mc-mini ${rt.cls}`,
      onClick: () => onSwitchTicker && onSwitchTicker(e.symbol),
      title: `${e.company || e.symbol} — ${mcDayLabel(e.earnings_date)} ${rt.label}`
    }, /*#__PURE__*/React.createElement("span", {
      className: "mc-mini-sym"
    }, e.symbol), /*#__PURE__*/React.createElement("span", {
      className: "mc-mini-meta"
    }, fmtMktCap(e.market_cap)), x && x.implied_move_pct != null ? /*#__PURE__*/React.createElement("span", {
      className: "mc-mini-move"
    }, "±", x.implied_move_pct, "%") : /*#__PURE__*/React.createElement("span", {
      className: `mc-mini-move ${fo == null ? "" : fo >= 0 ? "up" : "down"}`
    }, mcPct(fo)));
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "mc-wrap"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card mc-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head mc-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Watchlist"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Earnings Calendar")), /*#__PURE__*/React.createElement("div", {
    className: "mc-head-controls"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mc-weeknav"
  }, /*#__PURE__*/React.createElement("button", {
    onClick: () => setWeekOff(w => Math.max(0, w - 1)),
    disabled: weekOff === 0,
    title: "Previous week"
  }, "‹"), /*#__PURE__*/React.createElement("span", {
    className: "mc-week-label"
  }, weekLabel), /*#__PURE__*/React.createElement("button", {
    onClick: () => setWeekOff(w => Math.min(3, w + 1)),
    disabled: weekOff >= 3,
    title: "Next week"
  }, "›")), /*#__PURE__*/React.createElement("div", {
    className: "seg"
  }, /*#__PURE__*/React.createElement("button", {
    className: view === "compact" ? "active" : "",
    onClick: () => setView("compact")
  }, "Compact"), /*#__PURE__*/React.createElement("button", {
    className: view === "expanded" ? "active" : "",
    onClick: () => setView("expanded")
  }, "Expanded")), /*#__PURE__*/React.createElement("button", {
    className: "mc-refresh",
    onClick: loadEarn,
    disabled: loadingE,
    title: "Reload earnings"
  }, loadingE ? "…" : "↻"))), err ? /*#__PURE__*/React.createElement("div", {
    className: "mc-error"
  }, "Couldn't load earnings: ", err) : null, scanning ? /*#__PURE__*/React.createElement("div", {
    className: "mc-hint"
  }, "Watchlist board is still scanning — more names will appear as data fills in.") : null, /*#__PURE__*/React.createElement("div", {
    className: "mc-filters"
  }, /*#__PURE__*/React.createElement("select", {
    value: fSector,
    onChange: e => setFSector(e.target.value),
    title: "Filter by sector"
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "All sectors"), sectors.map(s => /*#__PURE__*/React.createElement("option", {
    key: s,
    value: s
  }, s))), /*#__PURE__*/React.createElement("select", {
    value: fIndustry,
    onChange: e => setFIndustry(e.target.value),
    title: "Filter by industry"
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "All industries"), industries.map(s => /*#__PURE__*/React.createElement("option", {
    key: s,
    value: s
  }, s))), /*#__PURE__*/React.createElement("select", {
    value: fMcap,
    onChange: e => setFMcap(e.target.value),
    title: "Filter by market cap"
  }, MCAP_BUCKETS.map(b => /*#__PURE__*/React.createElement("option", {
    key: b[0],
    value: b[0]
  }, b[1]))), /*#__PURE__*/React.createElement("select", {
    value: sortKey,
    onChange: e => setSortKey(e.target.value),
    title: "Sort cards within each day"
  }, /*#__PURE__*/React.createElement("option", {
    value: "mcap"
  }, "Sort: Market cap"), /*#__PURE__*/React.createElement("option", {
    value: "move"
  }, "Sort: Expected move"), /*#__PURE__*/React.createElement("option", {
    value: "optvol"
  }, "Sort: Options volume"), /*#__PURE__*/React.createElement("option", {
    value: "fromopen"
  }, "Sort: % from open"))), /*#__PURE__*/React.createElement("div", {
    className: "mc-rails"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mc-rail"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mc-rail-title"
  }, "📅 Watchlist Earnings Today"), /*#__PURE__*/React.createElement("div", {
    className: "mc-rail-body"
  }, todayEntries.length ? todayEntries.map(miniCard) : /*#__PURE__*/React.createElement("span", {
    className: "mc-empty"
  }, "No watchlist names report today."))), /*#__PURE__*/React.createElement("div", {
    className: "mc-rail"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mc-rail-title"
  }, "⭐ Most Important This Week"), /*#__PURE__*/React.createElement("div", {
    className: "mc-rail-body"
  }, importantEntries.length ? importantEntries.map(miniCard) : /*#__PURE__*/React.createElement("span", {
    className: "mc-empty"
  }, "No earnings this week."))), /*#__PURE__*/React.createElement("div", {
    className: "mc-rail"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mc-rail-title"
  }, "🚀 Biggest Expected Move"), /*#__PURE__*/React.createElement("div", {
    className: "mc-rail-body"
  }, moveEntries.length ? moveEntries.map(miniCard) : /*#__PURE__*/React.createElement("span", {
    className: "mc-empty"
  }, "Loading expected moves…")))), /*#__PURE__*/React.createElement("div", {
    className: "mc-grid"
  }, weekDays.map(day => {
    const dayEntries = sortEntries(weekEntries.filter(e => e.earnings_date === day));
    const isToday = day === today;
    return /*#__PURE__*/React.createElement("div", {
      key: day,
      className: `mc-col ${isToday ? "mc-col-today" : ""}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "mc-col-head"
    }, /*#__PURE__*/React.createElement("span", {
      className: "mc-col-wd"
    }, mcWeekday(day)), /*#__PURE__*/React.createElement("span", {
      className: "mc-col-date"
    }, mcDayLabel(day)), /*#__PURE__*/React.createElement("span", {
      className: "mc-col-count"
    }, dayEntries.length || "")), /*#__PURE__*/React.createElement("div", {
      className: "mc-col-body"
    }, dayEntries.length ? dayEntries.map(e => /*#__PURE__*/React.createElement(MarketEarningsCard, {
      key: e.symbol,
      e: e,
      expanded: !!expanded[e.symbol],
      extra: extras[e.symbol],
      live: liveQ[e.symbol],
      compact: view === "compact",
      onToggle: () => toggle(e.symbol),
      onSwitchTicker: onSwitchTicker
    })) : /*#__PURE__*/React.createElement("div", {
      className: "mc-col-empty"
    }, "—")));
  })), !loadingE && entries.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "mc-empty-all"
  }, "No watchlist earnings found in the next 4 weeks.") : null), /*#__PURE__*/React.createElement("div", {
    className: "card mc-card mc-econ"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head mc-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Macro"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Economic Calendar")), /*#__PURE__*/React.createElement("div", {
    className: "mc-head-controls"
  }, /*#__PURE__*/React.createElement("div", {
    className: "seg"
  }, /*#__PURE__*/React.createElement("button", {
    className: econImp === "high" ? "active" : "",
    onClick: () => setEconImp("high")
  }, "High"), /*#__PURE__*/React.createElement("button", {
    className: econImp === "med" ? "active" : "",
    onClick: () => setEconImp("med")
  }, "Med+"), /*#__PURE__*/React.createElement("button", {
    className: econImp === "all" ? "active" : "",
    onClick: () => setEconImp("all")
  }, "All")), /*#__PURE__*/React.createElement("button", {
    className: "mc-refresh",
    onClick: loadEcon,
    disabled: loadingM,
    title: "Reload events"
  }, loadingM ? "…" : "↻"))), econ && econ.error ? /*#__PURE__*/React.createElement("div", {
    className: "mc-error"
  }, "Economic data unavailable: ", econ.error) : null, econByDate.length === 0 && !loadingM ? /*#__PURE__*/React.createElement("div", {
    className: "mc-empty-all"
  }, "No events at this importance level.") : null, /*#__PURE__*/React.createElement("div", {
    className: "mc-econ-list"
  }, econByDate.map(([date, evs]) => /*#__PURE__*/React.createElement("div", {
    key: date,
    className: `mc-econ-day ${date === today ? "mc-econ-today" : ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "mc-econ-dayhead"
  }, /*#__PURE__*/React.createElement("span", {
    className: "mc-econ-wd"
  }, mcWeekday(date)), /*#__PURE__*/React.createElement("span", {
    className: "mc-econ-date"
  }, mcDayLabel(date)), date === today ? /*#__PURE__*/React.createElement("span", {
    className: "mc-econ-todaytag"
  }, "Today") : null), /*#__PURE__*/React.createElement("div", {
    className: "mc-econ-rows"
  }, evs.map((ev, i) => {
    const im = impMeta(ev.importance);
    return /*#__PURE__*/React.createElement("div", {
      key: i,
      className: `mc-econ-row ${im.cls}`,
      title: ev.note || ""
    }, /*#__PURE__*/React.createElement("span", {
      className: "mc-econ-time"
    }, ev.time), /*#__PURE__*/React.createElement("span", {
      className: `mc-imp-dot ${im.cls}`,
      title: `${im.label} importance`
    }), /*#__PURE__*/React.createElement("span", {
      className: "mc-econ-name"
    }, ev.event, ev.period ? /*#__PURE__*/React.createElement("span", {
      className: "mc-econ-for"
    }, " (", ev.period, ")") : null, /*#__PURE__*/React.createElement("span", {
      className: "mc-econ-ctry"
    }, ev.country)), /*#__PURE__*/React.createElement("span", {
      className: "mc-econ-vals"
    }, /*#__PURE__*/React.createElement("span", {
      className: "mc-econ-val",
      title: "Actual — the released figure (blank until reported)"
    }, /*#__PURE__*/React.createElement("b", null, "A"), ev.actual == null ? "—" : ev.actual), /*#__PURE__*/React.createElement("span", {
      className: "mc-econ-val",
      title: "Forecast — consensus estimate"
    }, /*#__PURE__*/React.createElement("b", null, "F"), ev.forecast == null ? "—" : ev.forecast), /*#__PURE__*/React.createElement("span", {
      className: "mc-econ-val",
      title: "Previous — last period's figure (r = revised)"
    }, /*#__PURE__*/React.createElement("b", null, "P"), ev.previous == null ? "—" : ev.previous, ev.revised != null ? /*#__PURE__*/React.createElement("span", {
      className: "mc-econ-rev"
    }, " (r ", ev.revised, ")") : null)), ev.note ? /*#__PURE__*/React.createElement("span", {
      className: "mc-econ-note"
    }, ev.note) : null);
  })))))));
}

// Watchlist Analyst Actions — fresh upgrades/downgrades/PT changes/initiations
// for watchlist names, drawn from the morning analyst-board scan. Today's
// actions are highlighted so the morning read is instant.
function WatchlistAnalystCard({
  apiFetch,
  onSwitchTicker
}) {
  const [data, setData] = useState(null);
  const [scope, setScope] = useState("today"); // today | recent
  const [type, setType] = useState("all"); // all|upgrade|downgrade|pt_up|pt_cut|initiate|high|multi
  const [sortKey, setSortKey] = useState("impact"); // impact|upside|date|symbol
  const [busy, setBusy] = useState(false);
  const pollRef = useRef(null);
  const load = async () => {
    try {
      const r = await apiFetch("/api/watchlist_analyst");
      const d = await r.json();
      setData(d);
      return d;
    } catch (_) {
      return null;
    }
  };
  useEffect(() => {
    load();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);
  const startScan = async () => {
    setBusy(true);
    try {
      await apiFetch("/api/analyst_board/scan?days=2&force=1");
    } catch (_) {}
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.scanning) {
        clearInterval(pollRef.current);
        pollRef.current = null;
        setBusy(false);
      }
    }, 4000);
  };
  const actions = data && data.actions || [];
  const isScanning = busy || data && data.scanning;
  const detected = data && data.detected_at ? new Date(data.detected_at).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }) : null;
  const typePass = a => {
    switch (type) {
      case "upgrade":
        return a.action_type === "upgrade";
      case "downgrade":
        return a.action_type === "downgrade";
      case "pt_up":
        return a.target_change_pct != null && a.target_change_pct > 0;
      case "pt_cut":
        return a.target_change_pct != null && a.target_change_pct < 0;
      case "initiate":
        return a.action_type === "initiate";
      case "high":
        return a.importance === "high";
      case "multi":
        return (a.multi_count || 1) > 1;
      default:
        return true;
    }
  };
  const filtered = actions.filter(a => (scope === "today" ? a.fresh_today : true) && typePass(a));
  const sorted = filtered.slice().sort((x, y) => {
    if (sortKey === "upside") return (y.upside_pct == null ? -1e9 : y.upside_pct) - (x.upside_pct == null ? -1e9 : x.upside_pct);
    if (sortKey === "date") return String(y.action_date).localeCompare(String(x.action_date));
    if (sortKey === "symbol") return String(x.symbol).localeCompare(String(y.symbol));
    return (y.impact_score || 0) - (x.impact_score || 0);
  });
  const freshCount = actions.filter(a => a.fresh_today).length;
  const AT = {
    upgrade: "Upgrade",
    downgrade: "Downgrade",
    initiate: "Initiation",
    reiterate: "Reiteration",
    target_change: "PT change"
  };
  const ptf = v => v == null ? "—" : "$" + Number(v).toFixed(2);
  const usDate = s => {
    if (!s) return "—";
    const p = String(s).slice(0, 10).split("-"); // YYYY-MM-DD -> M-D-YYYY
    return p.length === 3 ? `${+p[1]}-${+p[2]}-${p[0]}` : s;
  };
  const pctf = v => v == null ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(1) + "%";
  const FILTERS = [["all", "All"], ["upgrade", "Upgrades"], ["downgrade", "Downgrades"], ["pt_up", "PT raised"], ["pt_cut", "PT cut"], ["initiate", "New coverage"], ["high", "High impact"], ["multi", "Multi-firm"]];
  return /*#__PURE__*/React.createElement("div", {
    className: "card waa-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head waa-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Watchlist · ", freshCount, " fresh today", detected ? ` · scanned ${detected}` : ""), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Analyst Actions")), /*#__PURE__*/React.createElement("div", {
    className: "waa-head-controls"
  }, /*#__PURE__*/React.createElement("div", {
    className: "seg"
  }, /*#__PURE__*/React.createElement("button", {
    className: scope === "today" ? "active" : "",
    onClick: () => setScope("today")
  }, "Today"), /*#__PURE__*/React.createElement("button", {
    className: scope === "recent" ? "active" : "",
    onClick: () => setScope("recent")
  }, "Recent")), /*#__PURE__*/React.createElement("select", {
    value: sortKey,
    onChange: e => setSortKey(e.target.value),
    title: "Sort actions"
  }, /*#__PURE__*/React.createElement("option", {
    value: "impact"
  }, "Sort: Impact"), /*#__PURE__*/React.createElement("option", {
    value: "upside"
  }, "Sort: Upside"), /*#__PURE__*/React.createElement("option", {
    value: "date"
  }, "Sort: Action date"), /*#__PURE__*/React.createElement("option", {
    value: "symbol"
  }, "Sort: Symbol")), /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: startScan,
    disabled: isScanning
  }, isScanning ? "Scanning…" : "Scan now"))), /*#__PURE__*/React.createElement("div", {
    className: "waa-filters"
  }, FILTERS.map(([k, lbl]) => /*#__PURE__*/React.createElement("button", {
    key: k,
    className: `preset-pill ${type === k ? "active" : ""}`,
    onClick: () => setType(k)
  }, lbl))), sorted.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "waa-empty"
  }, isScanning ? "Scanning for analyst actions…" : actions.length === 0 ? /*#__PURE__*/React.createElement(React.Fragment, null, "No analyst actions cached yet — ", /*#__PURE__*/React.createElement("button", {
    className: "wl-rescan-link",
    onClick: startScan
  }, "Scan now"), " to build today's board.") : scope === "today" && type === "all" ? /*#__PURE__*/React.createElement(React.Fragment, null, "No analyst actions dated today yet — ", actions.length, " recent ", actions.length === 1 ? "action" : "actions", " on your watchlist. ", /*#__PURE__*/React.createElement("button", {
    className: "wl-rescan-link",
    onClick: () => setScope("recent")
  }, "Show recent")) : "No actions match this filter.") : /*#__PURE__*/React.createElement("div", {
    className: "waa-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "waa-table"
  }, /*#__PURE__*/React.createElement("colgroup", null, /*#__PURE__*/React.createElement("col", {
    style: {
      width: "7%"
    }
  }), /*#__PURE__*/React.createElement("col", {
    style: {
      width: "16%"
    }
  }), /*#__PURE__*/React.createElement("col", {
    style: {
      width: "8%"
    }
  }), /*#__PURE__*/React.createElement("col", {
    style: {
      width: "13%"
    }
  }), /*#__PURE__*/React.createElement("col", {
    style: {
      width: "9%"
    }
  }), /*#__PURE__*/React.createElement("col", {
    style: {
      width: "8%"
    }
  }), /*#__PURE__*/React.createElement("col", {
    style: {
      width: "9%"
    }
  }), /*#__PURE__*/React.createElement("col", {
    style: {
      width: "7%"
    }
  }), /*#__PURE__*/React.createElement("col", {
    style: {
      width: "7%"
    }
  }), /*#__PURE__*/React.createElement("col", {
    style: {
      width: "7%"
    }
  }), /*#__PURE__*/React.createElement("col", {
    style: {
      width: "5%"
    }
  }), /*#__PURE__*/React.createElement("col", {
    style: {
      width: "8%"
    }
  })), /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Ticker — click a row to open it on the Trade tab"
  }, "Symbol"), /*#__PURE__*/React.createElement("th", {
    title: "Company name"
  }, "Company"), /*#__PURE__*/React.createElement("th", {
    title: "Date of the analyst action"
  }, "Date"), /*#__PURE__*/React.createElement("th", {
    title: "Brokerage / research firm"
  }, "Firm"), /*#__PURE__*/React.createElement("th", {
    title: "Action type — upgrade, downgrade, initiation, reiteration, or price-target change"
  }, "Type"), /*#__PURE__*/React.createElement("th", {
    title: "Prior analyst rating"
  }, "From"), /*#__PURE__*/React.createElement("th", {
    title: "New analyst rating"
  }, "To"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Previous price target"
  }, "Prev PT"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "New price target"
  }, "New PT"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "% upside/downside from the current price to the new target"
  }, "Upside"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Impact score (0–100) — firm tier, market cap, PT move size, multi-firm agreement"
  }, "Impact"), /*#__PURE__*/React.createElement("th", {
    title: "Data source"
  }, "Source"))), /*#__PURE__*/React.createElement("tbody", null, sorted.map((a, i) => /*#__PURE__*/React.createElement("tr", {
    key: a.symbol + i,
    className: `waa-row ${a.fresh_today ? "waa-fresh" : ""} waa-${a.direction || "neutral"}`,
    onClick: () => onSwitchTicker && onSwitchTicker(a.symbol),
    title: (a.reasons || []).join(" · ")
  }, /*#__PURE__*/React.createElement("td", {
    className: "waa-sym"
  }, a.fresh_today && /*#__PURE__*/React.createElement("span", {
    className: "waa-bolt",
    title: "Fresh today"
  }, "⚡"), a.symbol, (a.multi_count || 1) > 1 && /*#__PURE__*/React.createElement("span", {
    className: "waa-multi",
    title: `${a.multi_count} firms acted`
  }, "×", a.multi_count)), /*#__PURE__*/React.createElement("td", {
    className: "waa-co",
    title: a.company || ""
  }, a.company || "—"), /*#__PURE__*/React.createElement("td", {
    className: "waa-date"
  }, usDate(a.action_date)), /*#__PURE__*/React.createElement("td", {
    className: "waa-firm"
  }, a.firm), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
    className: `waa-type waa-type-${a.direction || "neutral"}`
  }, AT[a.action_type] || a.action_type || "—")), /*#__PURE__*/React.createElement("td", {
    className: "waa-grade"
  }, a.rating_from || "—"), /*#__PURE__*/React.createElement("td", {
    className: "waa-grade"
  }, a.rating_to || "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, ptf(a.prev_target)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, ptf(a.new_target)), /*#__PURE__*/React.createElement("td", {
    className: `num ${a.upside_pct == null ? "" : a.upside_pct >= 0 ? "up" : "down"}`
  }, pctf(a.upside_pct)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("span", {
    className: `waa-score waa-imp-${a.importance || "low"}`
  }, a.impact_score != null ? Math.round(a.impact_score) : "—")), /*#__PURE__*/React.createElement("td", {
    className: "waa-src"
  }, a.source)))))));
}

// Company profile (Yahoo "Profile" page) — shown inside the News tab so it
// doesn't add a top-row tab. Description, sector/industry, HQ, website, execs.
function StockProfileCard({
  apiFetch,
  ticker,
  alwaysShow
}) {
  const [p, setP] = useState(null);
  const [open, setOpen] = useState(true); // description expanded by default
  useEffect(() => {
    let stop = false;
    setP(null);
    setOpen(true);
    if (!ticker) return;
    (async () => {
      try {
        const r = await apiFetch(`/api/profile?symbol=${encodeURIComponent(ticker)}`);
        const d = await r.json();
        if (!stop) setP(d);
      } catch (_) {}
    })();
    return () => {
      stop = true;
    };
  }, [ticker]);
  const empty = !p || !p.summary && !p.sector && !p.industry;
  if (empty) {
    if (!alwaysShow) return null;
    return /*#__PURE__*/React.createElement("div", {
      className: "card prof-card"
    }, /*#__PURE__*/React.createElement("div", {
      className: "prof-summary"
    }, p ? "No profile available for this symbol." : "Loading profile…"));
  }
  const fmtEmp = n => n == null ? null : Number(n).toLocaleString();
  const fmtPay = n => n == null ? null : n >= 1e6 ? "$" + (n / 1e6).toFixed(1) + "M" : "$" + Number(n).toLocaleString();
  const site = p.website ? p.website.startsWith("http") ? p.website : `https://${p.website}` : null;
  const summary = p.summary || "";
  const clamped = summary.length > 340 ? summary.slice(0, 340).trimEnd() + "…" : summary;
  return /*#__PURE__*/React.createElement("div", {
    className: "card prof-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Company profile"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, p.name || p.symbol)), site && /*#__PURE__*/React.createElement("a", {
    className: "prof-site",
    href: site,
    target: "_blank",
    rel: "noopener noreferrer"
  }, "Website ↗")), /*#__PURE__*/React.createElement("div", {
    className: "prof-tags"
  }, p.sector && /*#__PURE__*/React.createElement("span", {
    className: "prof-tag"
  }, p.sector), p.industry && /*#__PURE__*/React.createElement("span", {
    className: "prof-tag prof-tag-ind"
  }, p.industry), p.exchange && /*#__PURE__*/React.createElement("span", {
    className: "prof-tag prof-muted"
  }, p.exchange)), /*#__PURE__*/React.createElement("div", {
    className: "prof-meta"
  }, fmtEmp(p.employees) && /*#__PURE__*/React.createElement("div", {
    className: "prof-m"
  }, /*#__PURE__*/React.createElement("span", null, "Employees"), /*#__PURE__*/React.createElement("b", null, fmtEmp(p.employees))), p.address && /*#__PURE__*/React.createElement("div", {
    className: "prof-m"
  }, /*#__PURE__*/React.createElement("span", null, "Headquarters"), /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement("a", {
    className: "prof-link",
    href: `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(p.address)}`,
    target: "_blank",
    rel: "noopener noreferrer"
  }, p.address))), p.phone && /*#__PURE__*/React.createElement("div", {
    className: "prof-m"
  }, /*#__PURE__*/React.createElement("span", null, "Phone"), /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement("a", {
    className: "prof-link",
    href: `tel:${String(p.phone).replace(/[^0-9+]/g, "")}`
  }, p.phone)))), summary && /*#__PURE__*/React.createElement("div", {
    className: "prof-summary"
  }, open ? summary : clamped, summary.length > 340 && /*#__PURE__*/React.createElement("button", {
    className: "prof-more",
    onClick: () => setOpen(o => !o)
  }, open ? " Show less" : " Show more")), p.officers && p.officers.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "prof-execs"
  }, /*#__PURE__*/React.createElement("div", {
    className: "prof-execs-title"
  }, "Key executives"), /*#__PURE__*/React.createElement("div", {
    className: "prof-exec prof-exec-head"
  }, /*#__PURE__*/React.createElement("span", null, "Name"), /*#__PURE__*/React.createElement("span", null, "Title"), /*#__PURE__*/React.createElement("span", {
    className: "prof-exec-pay"
  }, "Pay")), p.officers.map((o, i) => /*#__PURE__*/React.createElement("div", {
    className: "prof-exec",
    key: i
  }, /*#__PURE__*/React.createElement("span", {
    className: "prof-exec-name"
  }, o.name), /*#__PURE__*/React.createElement("span", {
    className: "prof-exec-title"
  }, o.title || "—"), /*#__PURE__*/React.createElement("span", {
    className: "prof-exec-pay"
  }, fmtPay(o.pay) || "—")))));
}

// Streaks scanner — consecutive up/down day runs for every watchlist name,
// judged against each stock's OWN history (not a fixed 5/6/8), to surface
// names that may be near exhaustion / due for mean reversion.
function WatchlistStreaksCard({
  apiFetch,
  onSwitchTicker
}) {
  const [board, setBoard] = useState(null);
  const [liveQ, setLiveQ] = useState({});
  const [dir, setDir] = useState("all"); // all | up | down
  const [fSector, setFSector] = useState("all");
  const [minCount, setMinCount] = useState(3);
  const [flagOnly, setFlagOnly] = useState(false);
  const [sortKey, setSortKey] = useState("extremity");
  const pollRef = useRef(null);
  const load = async () => {
    try {
      const r = await apiFetch("/api/watchlist_table");
      const d = await r.json();
      setBoard(d);
      return d;
    } catch (_) {
      return null;
    }
  };
  useEffect(() => {
    load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(load, 5 * 60 * 1000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);
  const rows = board && board.rows || [];
  const status = board && board.status || {};

  // Flag/extremity logic — relative to each stock's own record.
  const decorate = r => {
    const longestSame = r.streak_dir === "up" ? r.longest_up || 0 : r.longest_down || 0;
    const ext = longestSame > 0 ? r.streak_count / longestSame : 0;
    const nearRecord = longestSame >= 4 && r.streak_count >= longestSame - 1;
    const atRecord = longestSame >= 4 && r.streak_count >= longestSame;
    const rare = r.streak_times_before != null && r.streak_times_before <= 3 && r.streak_count >= 4;
    const flags = [];
    if (r.streak_dir === "down" && nearRecord) {
      flags.push(atRecord ? "Record Down Streak" : "Near Record Down Streak");
      flags.push("Possible Exhaustion Setup");
    }
    if (r.streak_dir === "up" && nearRecord) flags.push(atRecord ? "Record Up Streak" : "Near Record Up Streak");
    if (rare) flags.push("Rare Streak");
    if (r.streak_dir === "down" && (nearRecord || rare)) flags.push("Mean Reversion Watch");
    return {
      ...r,
      ext,
      nearRecord,
      rare,
      flags
    };
  };
  const liveVal = r => {
    const q = liveQ[r.symbol] || {};
    const last = q.last != null ? q.last : r.last;
    const open = q.open != null ? q.open : r.open;
    const chg = q.change_pct != null ? q.change_pct : r.change;
    const fromOpen = open && last != null ? (last - open) / open * 100 : null;
    return {
      last,
      chg,
      fromOpen
    };
  };
  const sectors = useMemo(() => Array.from(new Set(rows.map(r => r.sector).filter(Boolean))).sort(), [rows]);
  const view = useMemo(() => {
    let v = rows.filter(r => r.streak_dir && r.streak_dir !== "flat" && (r.streak_count || 0) >= minCount).filter(r => dir === "all" || r.streak_dir === dir).filter(r => fSector === "all" || r.sector === fSector).map(decorate);
    if (flagOnly) v = v.filter(r => r.flags.length > 0);
    const sv = r => {
      switch (sortKey) {
        case "count":
          return r.streak_count || 0;
        case "winrate":
          return r.streak_winrate == null ? -1 : r.streak_winrate;
        case "fwd5":
          return r.streak_fwd5 == null ? -1e9 : r.streak_fwd5;
        case "rsi":
          return r.rsi == null ? -1 : r.rsi;
        case "rare":
          return r.streak_times_before == null ? 1e9 : r.streak_times_before;
        // fewest first
        default:
          return r.ext;
        // extremity
      }
    };
    const asc = sortKey === "rare";
    return v.sort((a, b) => asc ? sv(a) - sv(b) : sv(b) - sv(a));
  }, [rows, dir, fSector, minCount, flagOnly, sortKey, liveQ]);

  // Live overlay for the visible names (price + % from open + day change).
  useEffect(() => {
    let stop = false,
      timer = null;
    const syms = Array.from(new Set(view.slice(0, 50).map(r => r.symbol)));
    if (!syms.length) return;
    const tick = async () => {
      try {
        const next = {};
        for (let i = 0; i < syms.length; i += 25) {
          const r = await apiFetch(`/api/quote?tickers=${syms.slice(i, i + 25).join(",")}`);
          const d = await r.json();
          const res = d && d.results || {};
          for (const s of syms.slice(i, i + 25)) if (res[s]) next[s] = res[s];
        }
        if (!stop) setLiveQ(next);
      } catch (_) {}
      if (!stop) timer = setTimeout(tick, 30000);
    };
    tick();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line
  }, [view.length]);
  const pct = (v, d = 2) => v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
  const fmtV = v => v == null ? "—" : v >= 1e9 ? (v / 1e9).toFixed(2) + "B" : v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(0) + "K" : String(Math.round(v));
  const FLAG_CLS = {
    "Possible Exhaustion Setup": "warn",
    "Mean Reversion Watch": "warn",
    "Rare Streak": "rare",
    "Near Record Down Streak": "bear",
    "Record Down Streak": "bear",
    "Near Record Up Streak": "bull",
    "Record Up Streak": "bull"
  };
  const scanning = !!status.scanning;
  const nearCount = view.filter(r => r.flags.length).length;
  return /*#__PURE__*/React.createElement("div", {
    className: "card wstk-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head wstk-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Watchlist · ", view.length, " streaks · ", nearCount, " flagged"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Streak Exhaustion Scanner")), /*#__PURE__*/React.createElement("div", {
    className: "wstk-controls"
  }, /*#__PURE__*/React.createElement("div", {
    className: "seg"
  }, /*#__PURE__*/React.createElement("button", {
    className: dir === "all" ? "active" : "",
    onClick: () => setDir("all")
  }, "All"), /*#__PURE__*/React.createElement("button", {
    className: dir === "up" ? "active" : "",
    onClick: () => setDir("up")
  }, "Up"), /*#__PURE__*/React.createElement("button", {
    className: dir === "down" ? "active" : "",
    onClick: () => setDir("down")
  }, "Down")), /*#__PURE__*/React.createElement("select", {
    value: fSector,
    onChange: e => setFSector(e.target.value),
    title: "Filter by sector"
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "All sectors"), sectors.map(s => /*#__PURE__*/React.createElement("option", {
    key: s,
    value: s
  }, s))), /*#__PURE__*/React.createElement("label", {
    className: "wstk-min",
    title: "Minimum consecutive days"
  }, "≥ ", /*#__PURE__*/React.createElement("input", {
    type: "number",
    min: "1",
    max: "20",
    value: minCount,
    onChange: e => setMinCount(Math.max(1, Math.min(20, +e.target.value || 1)))
  }), " days"), /*#__PURE__*/React.createElement("button", {
    className: `preset-pill ${flagOnly ? "active" : ""}`,
    onClick: () => setFlagOnly(f => !f),
    title: "Only stocks flagged near a historical extreme"
  }, "Flagged only"), /*#__PURE__*/React.createElement("select", {
    value: sortKey,
    onChange: e => setSortKey(e.target.value),
    title: "Sort"
  }, /*#__PURE__*/React.createElement("option", {
    value: "extremity"
  }, "Sort: Extremity"), /*#__PURE__*/React.createElement("option", {
    value: "count"
  }, "Sort: Streak length"), /*#__PURE__*/React.createElement("option", {
    value: "rare"
  }, "Sort: Rarest"), /*#__PURE__*/React.createElement("option", {
    value: "winrate"
  }, "Sort: Win rate"), /*#__PURE__*/React.createElement("option", {
    value: "fwd5"
  }, "Sort: Next-5d avg"), /*#__PURE__*/React.createElement("option", {
    value: "rsi"
  }, "Sort: RSI")))), scanning ? /*#__PURE__*/React.createElement("div", {
    className: "wstk-hint"
  }, "Watchlist board is scanning — streaks fill in as data lands.") : null, !scanning && rows.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "wstk-empty"
  }, "No board data yet. Open the Watchlist tab and run a scan to build it.") : null, view.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "wstk-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "wstk-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Ticker — click a row to open it on the Trade tab"
  }, "Symbol"), /*#__PURE__*/React.createElement("th", {
    title: "Company name"
  }, "Company"), /*#__PURE__*/React.createElement("th", {
    title: "Current consecutive up/down day streak (direction + number of days)"
  }, "Streak"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Longest up streak / longest down streak ever seen in the last 2 years"
  }, "Rec ↑/↓"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "How many times this stock previously reached a streak this long in the same direction (★ = rare, ≤3 times)"
  }, "Seen"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Average next-1-day return after similar past streaks"
  }, "Nx1"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Average next-3-day return after similar past streaks"
  }, "Nx3"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Average next-5-day return after similar past streaks"
  }, "Nx5"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Win rate — % of similar past streaks that were higher 5 days later"
  }, "Win5"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Current price (live)"
  }, "Price"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "% change from today's open (live)"
  }, "%Open"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Daily % change (live)"
  }, "Day"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Latest daily volume"
  }, "Vol"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Relative volume — today's volume vs its 20-day average"
  }, "RVol"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "RSI(14)"
  }, "RSI"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Distance from the 20-day moving average"
  }, "20DMA"), /*#__PURE__*/React.createElement("th", {
    className: "num",
    title: "Distance from the 50-day moving average"
  }, "50DMA"), /*#__PURE__*/React.createElement("th", {
    title: "Sector (hover a cell for industry)"
  }, "Sector"), /*#__PURE__*/React.createElement("th", {
    title: "Exhaustion / mean-reversion flags vs this stock's own record"
  }, "Flags"))), /*#__PURE__*/React.createElement("tbody", null, view.map((r, i) => {
    const lv = liveVal(r);
    const dirCls = r.streak_dir === "up" ? "up" : "down";
    return /*#__PURE__*/React.createElement("tr", {
      key: r.symbol + i,
      className: `wstk-row ${r.flags.length ? "wstk-flagged" : ""}`,
      onClick: () => onSwitchTicker && onSwitchTicker(r.symbol)
    }, /*#__PURE__*/React.createElement("td", {
      className: "wstk-sym"
    }, r.symbol), /*#__PURE__*/React.createElement("td", {
      className: "wstk-co",
      title: r.company || ""
    }, r.company || "—"), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
      className: `wstk-streak ${dirCls}`
    }, r.streak_dir === "up" ? "▲" : "▼", " ", r.streak_count, "d")), /*#__PURE__*/React.createElement("td", {
      className: "num wstk-rec"
    }, r.longest_up || "—", "/", r.longest_down || "—"), /*#__PURE__*/React.createElement("td", {
      className: "num"
    }, r.streak_times_before == null ? "—" : r.streak_times_before, r.rare ? "★" : ""), /*#__PURE__*/React.createElement("td", {
      className: `num ${r.streak_fwd1 == null ? "" : r.streak_fwd1 >= 0 ? "up" : "down"}`
    }, pct(r.streak_fwd1, 1)), /*#__PURE__*/React.createElement("td", {
      className: `num ${r.streak_fwd3 == null ? "" : r.streak_fwd3 >= 0 ? "up" : "down"}`
    }, pct(r.streak_fwd3, 1)), /*#__PURE__*/React.createElement("td", {
      className: `num ${r.streak_fwd5 == null ? "" : r.streak_fwd5 >= 0 ? "up" : "down"}`
    }, pct(r.streak_fwd5, 1)), /*#__PURE__*/React.createElement("td", {
      className: "num"
    }, r.streak_winrate == null ? "—" : r.streak_winrate + "%"), /*#__PURE__*/React.createElement("td", {
      className: "num"
    }, lv.last == null ? "—" : "$" + Number(lv.last).toFixed(2)), /*#__PURE__*/React.createElement("td", {
      className: `num ${lv.fromOpen == null ? "" : lv.fromOpen >= 0 ? "up" : "down"}`
    }, pct(lv.fromOpen, 1)), /*#__PURE__*/React.createElement("td", {
      className: `num ${lv.chg == null ? "" : lv.chg >= 0 ? "up" : "down"}`
    }, pct(lv.chg, 1)), /*#__PURE__*/React.createElement("td", {
      className: "num"
    }, fmtV(r.volume)), /*#__PURE__*/React.createElement("td", {
      className: "num"
    }, r.rel_vol == null ? "—" : r.rel_vol + "x"), /*#__PURE__*/React.createElement("td", {
      className: "num"
    }, r.rsi == null ? "—" : Math.round(r.rsi)), /*#__PURE__*/React.createElement("td", {
      className: `num ${r.from_ma20 == null ? "" : r.from_ma20 >= 0 ? "up" : "down"}`
    }, pct(r.from_ma20, 1)), /*#__PURE__*/React.createElement("td", {
      className: `num ${r.from_ma50 == null ? "" : r.from_ma50 >= 0 ? "up" : "down"}`
    }, pct(r.from_ma50, 1)), /*#__PURE__*/React.createElement("td", {
      className: "wstk-sec",
      title: r.industry || ""
    }, r.sector || "—"), /*#__PURE__*/React.createElement("td", {
      className: "wstk-flags"
    }, r.flags.map((f, j) => /*#__PURE__*/React.createElement("span", {
      key: j,
      className: `wstk-flag wstk-f-${FLAG_CLS[f] || "warn"}`
    }, f))));
  })))), !scanning && rows.length > 0 && view.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "wstk-empty"
  }, "No streaks match these filters."));
}

// Schwab in-app reconnect. Schwab refresh tokens die every 7 days; this turns
// the re-auth into a ~20s in-browser action: open login, paste the redirect
// URL back, done — no terminal, no Railway edits. Renders as a top banner
// only when reconnect is needed; as a full panel in the Manage tab always.
function SchwabReconnect({
  apiFetch,
  placement
}) {
  const [st, setSt] = useState(null); // data_source.schwab
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null); // {ok, text}
  const load = async () => {
    try {
      const r = await apiFetch("/api/data_source");
      const d = await r.json();
      setSt(d && d.schwab || null);
    } catch (_) {}
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);
  const needs = !!(st && st.needs_reauth);
  if (placement === "banner" && !needs) return null; // banner only when broken

  const openLogin = async () => {
    setMsg(null);
    try {
      const r = await apiFetch("/api/broker/schwab/authorize_url");
      const d = await r.json();
      if (d.url) window.open(d.url, "_blank", "noopener");else setMsg({
        ok: false,
        text: d.error || "Could not start Schwab login"
      });
    } catch (e) {
      setMsg({
        ok: false,
        text: String(e)
      });
    }
  };
  const complete = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiFetch("/api/broker/schwab/exchange", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          redirect_url: url
        })
      });
      const d = await r.json();
      if (d.ok) {
        setMsg({
          ok: true,
          text: "Schwab reconnected ✓"
        });
        setUrl("");
        load();
      } else setMsg({
        ok: false,
        text: d.error || "Reconnect failed"
      });
    } catch (e) {
      setMsg({
        ok: false,
        text: String(e)
      });
    }
    setBusy(false);
  };
  const connected = !!(st && st.configured && !needs);
  const cls = placement === "banner" ? "schwab-reauth schwab-banner" : "card schwab-reauth";
  return /*#__PURE__*/React.createElement("div", {
    className: cls
  }, /*#__PURE__*/React.createElement("div", {
    className: "schwab-reauth-head"
  }, /*#__PURE__*/React.createElement("span", {
    className: "schwab-reauth-title"
  }, "Schwab connection"), /*#__PURE__*/React.createElement("span", {
    className: `schwab-dot ${connected ? "ok" : needs ? "bad" : "warn"}`
  }, connected ? "Connected" : needs ? "Disconnected — re-authorize" : "Checking…")), /*#__PURE__*/React.createElement("ol", {
    className: "schwab-steps"
  }, /*#__PURE__*/React.createElement("li", null, /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: openLogin
  }, "Open Schwab login"), /*#__PURE__*/React.createElement("span", {
    className: "schwab-hint"
  }, " log in & approve. Your browser lands on a ", /*#__PURE__*/React.createElement("b", null, "127.0.0.1"), " page that won't load — that's expected.")), /*#__PURE__*/React.createElement("li", null, "Copy that full URL and paste it here:", /*#__PURE__*/React.createElement("div", {
    className: "schwab-paste"
  }, /*#__PURE__*/React.createElement("input", {
    value: url,
    onChange: e => setUrl(e.target.value),
    placeholder: "https://127.0.0.1:8182/?code=…"
  }), /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: complete,
    disabled: busy || !url
  }, busy ? "…" : "Complete")))), msg && /*#__PURE__*/React.createElement("div", {
    className: `schwab-msg ${msg.ok ? "ok" : "bad"}`
  }, msg.text), connected && st && st.refresh_remaining_days != null && /*#__PURE__*/React.createElement("div", {
    className: "schwab-note"
  }, "Re-authorization will be needed again within ~7 days."));
}

// News tab shell: headlines by default (so the News tab opens on the news),
// with the company profile tucked behind a toggle so it has its own view.
function NewsHub({
  apiFetch,
  ticker,
  companyName
}) {
  const [view, setView] = useState("news"); // news | profile
  return /*#__PURE__*/React.createElement("div", {
    className: "newshub"
  }, /*#__PURE__*/React.createElement("div", {
    className: "seg newshub-seg"
  }, /*#__PURE__*/React.createElement("button", {
    className: view === "news" ? "active" : "",
    onClick: () => setView("news")
  }, "Headlines"), /*#__PURE__*/React.createElement("button", {
    className: view === "profile" ? "active" : "",
    onClick: () => setView("profile")
  }, "Profile")), view === "news" ? /*#__PURE__*/React.createElement(NewsCard, {
    apiFetch: apiFetch,
    ticker: ticker,
    companyName: companyName
  }) : /*#__PURE__*/React.createElement(StockProfileCard, {
    apiFetch: apiFetch,
    ticker: ticker,
    alwaysShow: true
  }));
}

// Top-of-app news ticker tape — the user's Finviz Elite feed. Hides itself
// entirely until FINVIZ_AUTH_TOKEN is configured and headlines arrive, so it
// never shows an empty strip. Headlines scroll right-to-left; hover pauses.
function NewsTicker({
  apiFetch,
  onSwitchTicker
}) {
  const [items, setItems] = useState([]);
  const [quotes, setQuotes] = useState({}); // SYM -> {last, chg}
  const stackRef = useRef(null);
  useEffect(() => {
    let stop = false,
      timer = null;
    const tick = async () => {
      try {
        const r = await apiFetch("/api/finviz_news?limit=60");
        const d = await r.json();
        if (!stop) setItems(Array.isArray(d && d.items) ? d.items : []);
      } catch (_) {/* keep last items */}
      if (!stop) timer = setTimeout(tick, 60000);
    };
    tick();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Tickers mentioned across the headlines (Finviz tags them, comma-joined).
  const symbols = useMemo(() => {
    const seen = new Set(),
      out = [];
    for (const it of items) {
      for (const raw of String(it.ticker || "").split(/[,\s]+/)) {
        const s = raw.toUpperCase().trim();
        if (s && /^[A-Z][A-Z.\-]{0,5}$/.test(s) && !seen.has(s)) {
          seen.add(s);
          out.push(s);
        }
      }
      if (out.length >= 40) break;
    }
    return out;
  }, [items]);

  // Live quotes for the mentioned tickers — Bloomberg/CNBC-style tape below.
  useEffect(() => {
    let stop = false,
      timer = null;
    if (!symbols.length) {
      setQuotes({});
      return;
    }
    const tick = async () => {
      try {
        const next = {};
        for (let i = 0; i < symbols.length; i += 25) {
          const batch = symbols.slice(i, i + 25);
          const r = await apiFetch(`/api/quote?tickers=${batch.join(",")}`);
          const d = await r.json();
          const res = d && d.results || {};
          for (const s of batch) if (res[s]) next[s] = {
            last: res[s].last,
            chg: res[s].change_pct
          };
        }
        if (!stop) setQuotes(next);
      } catch (_) {}
      if (!stop) timer = setTimeout(tick, 30000);
    };
    tick();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [symbols]);

  // Keep the tab bar parked just below however tall the (sticky) stack is —
  // 0 when nothing renders. Measured each render so it's always in sync.
  useEffect(() => {
    const el = stackRef.current;
    const h = el ? el.offsetHeight + 8 : 0;
    document.documentElement.style.setProperty("--mn-h", h ? `${h}px` : "0px");
    return () => {
      document.documentElement.style.setProperty("--mn-h", "0px");
    };
  });
  if (!items.length) return null; // unconfigured / empty → no strip

  const dur = Math.max(55, items.length * 6.5);
  const Seq = ({
    hidden
  }) => /*#__PURE__*/React.createElement("div", {
    className: "nt-seq",
    "aria-hidden": hidden || undefined
  }, items.map((it, i) => /*#__PURE__*/React.createElement("a", {
    key: i,
    className: "nt-item",
    href: it.url,
    target: "_blank",
    rel: "noopener noreferrer",
    title: `${it.source || ""}${it.date ? " · " + it.date : ""}`
  }, it.ticker ? /*#__PURE__*/React.createElement("span", {
    className: "nt-tkr"
  }, it.ticker) : null, it.source ? /*#__PURE__*/React.createElement("span", {
    className: "nt-src"
  }, it.source) : null, /*#__PURE__*/React.createElement("span", {
    className: "nt-ttl"
  }, it.title), /*#__PURE__*/React.createElement("span", {
    className: "nt-sep"
  }, "●"))));
  const qsyms = symbols.filter(s => quotes[s] && quotes[s].last != null);
  const qdur = Math.max(32, qsyms.length * 3.1); // a touch faster than the news tape
  const QSeq = ({
    hidden
  }) => /*#__PURE__*/React.createElement("div", {
    className: "nt-seq",
    "aria-hidden": hidden || undefined
  }, qsyms.map((s, i) => {
    const q = quotes[s],
      up = (q.chg || 0) >= 0;
    return /*#__PURE__*/React.createElement("button", {
      key: s + i,
      className: "mnq-item",
      onClick: () => onSwitchTicker && onSwitchTicker(s),
      title: `Open ${s}`
    }, /*#__PURE__*/React.createElement("b", {
      className: "mnq-sym"
    }, s), /*#__PURE__*/React.createElement("span", {
      className: "mnq-px"
    }, "$", Number(q.last).toFixed(2)), /*#__PURE__*/React.createElement("span", {
      className: `mnq-chg ${up ? "up" : "down"}`
    }, q.chg == null ? "" : `${up ? "▲" : "▼"} ${Math.abs(q.chg).toFixed(2)}%`));
  }));
  return /*#__PURE__*/React.createElement("div", {
    className: "mn-stack",
    ref: stackRef,
    "aria-label": "Market news and ticker tape"
  }, /*#__PURE__*/React.createElement("div", {
    className: "newsticker",
    "aria-label": "Market news feed"
  }, /*#__PURE__*/React.createElement("div", {
    className: "nt-badge",
    title: "Live market news feed"
  }, /*#__PURE__*/React.createElement("span", null, "Market"), /*#__PURE__*/React.createElement("span", null, "News")), /*#__PURE__*/React.createElement("div", {
    className: "nt-viewport"
  }, /*#__PURE__*/React.createElement("div", {
    className: "nt-track",
    style: {
      animationDuration: `${dur}s`
    }
  }, /*#__PURE__*/React.createElement(Seq, null), /*#__PURE__*/React.createElement(Seq, {
    hidden: true
  })))), qsyms.length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "newsticker mnq-bar",
    "aria-label": "Mentioned tickers"
  }, /*#__PURE__*/React.createElement("div", {
    className: "nt-badge mnq-badge",
    title: "Live quotes for tickers in the news"
  }, /*#__PURE__*/React.createElement("span", null, "Tickers")), /*#__PURE__*/React.createElement("div", {
    className: "nt-viewport"
  }, /*#__PURE__*/React.createElement("div", {
    className: "nt-track",
    style: {
      animationDuration: `${qdur}s`
    }
  }, /*#__PURE__*/React.createElement(QSeq, null), /*#__PURE__*/React.createElement(QSeq, {
    hidden: true
  })))));
}

// Left-margin vertical ticker (wide screens only): watchlist names closest to
// their 52-week high, scrolling top→bottom. Ticker · price · change · %-from-52WH.
function LeftRail52W({
  apiFetch,
  onSwitchTicker
}) {
  const [scanRows, setScanRows] = useState([]);
  const [liveQ, setLiveQ] = useState({}); // symbol -> {last, chg}
  const [owned, setOwned] = useState(() => new Set()); // Schwab-held symbols
  const [vpH, setVpH] = useState(0);
  const vpRef = useRef(null);
  // Pull the scan board (cheap, cached) for 52W-high context: high_52w plus a
  // candidate set near the high. We display LIVE price/change (below) so a
  // stale or corrupt scan `last` never shows a price the stock never traded.
  useEffect(() => {
    let stop = false,
      t = null;
    const load = async () => {
      try {
        const r = await apiFetch("/api/watchlist_table");
        const d = await r.json();
        const all = d && d.rows || [];
        // Candidates: anything the scan thinks is within ~6% of its high (a
        // touch wider than the display threshold so a live intraday push to a
        // new high still qualifies once live prices arrive).
        const near = all.filter(x => x.from_52wh != null && x.from_52wh >= -6 && x.high_52w != null).sort((a, b) => b.from_52wh - a.from_52wh).slice(0, 60);
        if (!stop) setScanRows(near);
      } catch (_) {}
      if (!stop) t = setTimeout(load, 60000);
    };
    load();
    return () => {
      stop = true;
      if (t) clearTimeout(t);
    };
  }, []);

  // Owned symbols from the Schwab portfolio (cached server-side ~5 min).
  useEffect(() => {
    let stop = false,
      t = null;
    const grab = async () => {
      try {
        const r = await apiFetch("/api/broker/owned");
        const d = await r.json();
        if (!stop && d && Array.isArray(d.symbols)) {
          setOwned(new Set(d.symbols.map(s => String(s).toUpperCase())));
        }
      } catch (_) {/* highlight is best-effort */}
      if (!stop) t = setTimeout(grab, 5 * 60 * 1000);
    };
    grab();
    return () => {
      stop = true;
      if (t) clearTimeout(t);
    };
  }, []);

  // Live-quote overlay for the candidate set (batched, pauses when hidden).
  const candKey = scanRows.map(r => r.symbol).join(",");
  useEffect(() => {
    if (!candKey) return;
    const syms = candKey.split(",");
    let stop = false,
      t = null;
    const poll = async () => {
      if (!document.hidden) {
        for (let i = 0; i < syms.length && !stop; i += 25) {
          const batch = syms.slice(i, i + 25);
          try {
            const r = await apiFetch(`/api/quote?tickers=${encodeURIComponent(batch.join(","))}`);
            if (!r.ok) continue;
            const j = await r.json();
            if (stop) return;
            const res = j.results || {};
            setLiveQ(prev => {
              const next = {
                ...prev
              };
              for (const s of batch) {
                const q = res[s];
                if (q && q.last != null) next[s] = {
                  last: q.last,
                  chg: q.change_pct != null ? q.change_pct : null
                };
              }
              return next;
            });
          } catch (_) {}
        }
      }
      if (!stop) t = setTimeout(poll, 30000);
    };
    poll();
    return () => {
      stop = true;
      if (t) clearTimeout(t);
    };
  }, [candKey]);

  // Build display rows: prefer the live price/change; recompute "% from 52W
  // high" against the live price so the rail reflects reality intraday and a
  // bad scan bar can't keep a stock pinned at a high it isn't near. Then apply
  // the real display threshold (within 3% of the high) and sort by closeness.
  const rows = useMemo(() => {
    const out = [];
    for (const r of scanRows) {
      const q = liveQ[r.symbol];
      const last = q && q.last != null ? q.last : r.last;
      if (last == null) continue;
      const chg = q && q.chg != null ? q.chg : r.change;
      const hi = r.high_52w;
      const from = hi ? Math.round((last / hi - 1) * 1000) / 10 : r.from_52wh;
      if (from == null || from < -3) continue;
      out.push({
        ...r,
        _last: last,
        _chg: chg,
        _from: from
      });
    }
    out.sort((a, b) => b._from - a._from);
    return out.slice(0, 40);
  }, [scanRows, liveQ]);

  // Measure the (full-height) viewport. Each list copy is forced to AT LEAST
  // this height (min-height + space-evenly), so the rail always fills top to
  // bottom AND the same symbol never shows twice (one copy = one viewport).
  useEffect(() => {
    const measure = () => {
      if (vpRef.current) setVpH(vpRef.current.offsetHeight);
    };
    measure();
    window.addEventListener("resize", measure);
    const id = setTimeout(measure, 80);
    return () => {
      window.removeEventListener("resize", measure);
      clearTimeout(id);
    };
  }, [rows]);
  if (!rows.length) return null;
  const colH = Math.max(vpH || 0, rows.length * 62);
  const dur = Math.max(16, Math.round(colH / 35)); // ~35 px/s (a hair slower)
  const Col = ({
    hidden
  }) => /*#__PURE__*/React.createElement("div", {
    className: "lr-col",
    "aria-hidden": hidden || undefined,
    style: vpH ? {
      minHeight: `${vpH}px`
    } : undefined
  }, rows.map((r, i) => {
    const isOwned = owned.has(String(r.symbol).toUpperCase());
    return /*#__PURE__*/React.createElement("button", {
      key: i,
      className: `lr-item${isOwned ? " owned" : ""}`,
      onClick: () => onSwitchTicker && onSwitchTicker(r.symbol),
      title: `${r.company || r.symbol} — ${r._from >= 0 ? "at" : Math.abs(r._from) + "% below"} 52-week high ($${r.high_52w != null ? r.high_52w : "?"})${isOwned ? " · you own this (Schwab)" : ""}`
    }, /*#__PURE__*/React.createElement("span", {
      className: "lr-line1"
    }, /*#__PURE__*/React.createElement("span", {
      className: "lr-sym"
    }, r.symbol), /*#__PURE__*/React.createElement("span", {
      className: "lr-px"
    }, "$", Number(r._last).toFixed(2))), /*#__PURE__*/React.createElement("span", {
      className: "lr-line2"
    }, /*#__PURE__*/React.createElement("span", {
      className: `lr-chg ${(r._chg || 0) >= 0 ? "up" : "down"}`
    }, r._chg == null ? "—" : `${r._chg >= 0 ? "+" : ""}${Number(r._chg).toFixed(2)}%`), /*#__PURE__*/React.createElement("span", {
      className: "lr-52",
      title: "% from 52-week high"
    }, r._from >= 0 ? "HIGH" : `${r._from}%`)), /*#__PURE__*/React.createElement("span", {
      className: "lr-line3",
      title: r.tag ? `Tag: ${r.tag}` : "No tag"
    }, r.tag || "—"));
  }));
  return /*#__PURE__*/React.createElement("div", {
    className: "lrail",
    "aria-label": "Watchlist names near 52-week high"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lrail-title",
    title: "Watchlist stocks within 3% of their 52-week high"
  }, "NEAR 52W HIGH"), /*#__PURE__*/React.createElement("div", {
    className: "lrail-vp",
    ref: vpRef
  }, /*#__PURE__*/React.createElement("div", {
    className: "lrail-track",
    style: {
      animationDuration: `${dur}s`
    }
  }, /*#__PURE__*/React.createElement(Col, {
    inner: true
  }), /*#__PURE__*/React.createElement(Col, {
    hidden: true
  }))));
}

// Twin of LeftRail52W, but for stocks AT or near TODAY'S session high. The
// server (/api/daily_highs) does the heavy lifting: it batches live quotes for
// the whole watchlist, computes "% from today's high", filters + ranks, and
// merges in each symbol's Tag. Everything else (owned-yellow highlight, the
// 3-line layout, the seamless scroll) mirrors the 52W rail exactly.
function LeftRailDailyHigh({
  apiFetch,
  onSwitchTicker
}) {
  const [rows, setRows] = useState([]);
  const [owned, setOwned] = useState(() => new Set());
  const [vpH, setVpH] = useState(0);
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  const vpRef = useRef(null);
  // Tick once every 5s so the "time since it hit the high" ages live without
  // re-fetching (CSS scroll animation keeps running — only text changes).
  useEffect(() => {
    const id = setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 5000);
    return () => clearInterval(id);
  }, []);
  // Compact "time since last at the high": 35s, 5m, 2h.
  const ageStr = ts => {
    if (!ts) return "";
    const s = Math.max(0, nowSec - Math.floor(ts));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    return `${Math.floor(s / 3600)}h`;
  };
  useEffect(() => {
    let stop = false,
      t = null;
    const load = async () => {
      try {
        const r = await apiFetch("/api/daily_highs");
        const d = await r.json();
        if (!stop) setRows(d && d.rows || []);
      } catch (_) {}
      if (!stop) t = setTimeout(load, 30000);
    };
    load();
    return () => {
      stop = true;
      if (t) clearTimeout(t);
    };
  }, []);
  useEffect(() => {
    let stop = false,
      t = null;
    const grab = async () => {
      try {
        const r = await apiFetch("/api/broker/owned");
        const d = await r.json();
        if (!stop && d && Array.isArray(d.symbols)) {
          setOwned(new Set(d.symbols.map(s => String(s).toUpperCase())));
        }
      } catch (_) {}
      if (!stop) t = setTimeout(grab, 5 * 60 * 1000);
    };
    grab();
    return () => {
      stop = true;
      if (t) clearTimeout(t);
    };
  }, []);
  useEffect(() => {
    const measure = () => {
      if (vpRef.current) setVpH(vpRef.current.offsetHeight);
    };
    measure();
    window.addEventListener("resize", measure);
    const id = setTimeout(measure, 80);
    return () => {
      window.removeEventListener("resize", measure);
      clearTimeout(id);
    };
  }, [rows]);
  if (!rows.length) return null;
  const colH = Math.max(vpH || 0, rows.length * 62);
  const dur = Math.max(16, Math.round(colH / 35));
  const Col = ({
    hidden
  }) => /*#__PURE__*/React.createElement("div", {
    className: "lr-col",
    "aria-hidden": hidden || undefined,
    style: vpH ? {
      minHeight: `${vpH}px`
    } : undefined
  }, rows.map((r, i) => {
    const isOwned = owned.has(String(r.symbol).toUpperCase());
    const from = r.from_high;
    return /*#__PURE__*/React.createElement("button", {
      key: i,
      className: `lr-item${isOwned ? " owned" : ""}`,
      onClick: () => onSwitchTicker && onSwitchTicker(r.symbol),
      title: `${r.company || r.symbol} — ${from >= 0 ? "at" : Math.abs(from) + "% below"} today's high ($${r.day_high != null ? Number(r.day_high).toFixed(2) : "?"})${isOwned ? " · you own this (Schwab)" : ""}`
    }, /*#__PURE__*/React.createElement("span", {
      className: "lr-line1"
    }, /*#__PURE__*/React.createElement("span", {
      className: "lr-sym"
    }, r.symbol), /*#__PURE__*/React.createElement("span", {
      className: "lr-dash"
    }, "-"), /*#__PURE__*/React.createElement("span", {
      className: "lr-px"
    }, "$", Number(r.last).toFixed(2)), /*#__PURE__*/React.createElement("span", {
      className: "lr-age",
      title: "Time since it last touched today's high"
    }, ageStr(r.hit_ts))), /*#__PURE__*/React.createElement("span", {
      className: "lr-line2"
    }, /*#__PURE__*/React.createElement("span", {
      className: `lr-chg ${(r.change || 0) >= 0 ? "up" : "down"}`
    }, r.change == null ? "—" : `${r.change >= 0 ? "+" : ""}${Number(r.change).toFixed(2)}%`), /*#__PURE__*/React.createElement("span", {
      className: "lr-age",
      title: "Time since it last touched today's high"
    }, ageStr(r.hit_ts))), /*#__PURE__*/React.createElement("span", {
      className: "lr-line3",
      title: r.tag ? `Tag: ${r.tag}` : "No tag"
    }, r.tag || "—"));
  }));
  return /*#__PURE__*/React.createElement("div", {
    className: "lrail lrail--daily",
    "aria-label": "Watchlist names at today's daily high"
  }, /*#__PURE__*/React.createElement("div", {
    className: "lrail-title lrail-title--daily",
    title: "Watchlist stocks at or within 1% of today's session high"
  }, "DAILY HIGH"), /*#__PURE__*/React.createElement("div", {
    className: "lrail-vp",
    ref: vpRef
  }, /*#__PURE__*/React.createElement("div", {
    className: "lrail-track",
    style: {
      animationDuration: `${dur}s`
    }
  }, /*#__PURE__*/React.createElement(Col, {
    inner: true
  }), /*#__PURE__*/React.createElement(Col, {
    hidden: true
  }))));
}

// Memoize the heavy, self-contained ticker cards so unrelated App state
// changes (hovers, sidebar, other tabs) don't re-render them. Their props
// (apiFetch, switchTicker, ticker) are stable identities from App.
const SwingPatternCardM = React.memo(SwingPatternCard);
const NewsCardM = React.memo(NewsCard);
const ScreenersHubM = React.memo(ScreenersHub);
Object.assign(window, {
  SwingPatternCard: SwingPatternCardM,
  NewsCard: NewsCardM,
  ScreenersHub: ScreenersHubM
});
// Heavy, prop-driven cards are wrapped in React.memo so they don't re-render
// every time the App re-renders for unrelated state (settings toggles, the 30s
// staleness tick, sibling-card updates). memo only ever SKIPS a render when
// props are shallow-equal, so it's safe for these pure components; small
// helpers/rows are left unwrapped.
const _memo = React.memo;
Object.assign(window, {
  TickerLogo,
  VolSkewCard: _memo(VolSkewCard),
  WatchlistTableCard: _memo(WatchlistTableCard),
  AnalystBoardCard: _memo(AnalystBoardCard),
  MoversCard: _memo(MoversCard),
  TrendCard: _memo(TrendCard),
  IVRankCard: _memo(IVRankCard),
  WatchlistAlertsCard: _memo(WatchlistAlertsCard),
  TabBar,
  TabPanel,
  WeatherBadge,
  LevelRepriceCard: _memo(LevelRepriceCard),
  WinRateCard: _memo(WinRateCard),
  EarningsCrushCard: _memo(EarningsCrushCard),
  PushSettingsCard,
  BrokerImportCard,
  StrategyReferenceCard,
  WatchlistManager,
  QuickAddRow,
  WatchlistRow,
  FlashOnChange,
  SortableTh,
  PercentCalc,
  RollManagerCard: _memo(RollManagerCard),
  FlowScoreCard: _memo(FlowScoreCard),
  PullbackBacktest,
  TradeBuilderCard: _memo(TradeBuilderCard),
  AnalystCard: _memo(AnalystCard),
  PullbackProfileCard: _memo(PullbackProfileCard),
  BasingCard: _memo(BasingCard),
  Recommendation,
  RecommendationPair,
  StrategyCard: _memo(StrategyCard),
  PositionsCard: _memo(PositionsCard),
  AddPositionForm,
  MarketCalendarCard: _memo(MarketCalendarCard),
  NewsTicker: _memo(NewsTicker),
  WatchlistAnalystCard: _memo(WatchlistAnalystCard),
  StockProfileCard: _memo(StockProfileCard),
  NewsHub: _memo(NewsHub),
  SchwabReconnect: _memo(SchwabReconnect),
  WatchlistStreaksCard: _memo(WatchlistStreaksCard),
  LeftRail52W: _memo(LeftRail52W),
  LeftRailDailyHigh: _memo(LeftRailDailyHigh)
});
})();
