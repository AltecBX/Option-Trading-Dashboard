(function () {
// tab-treasuries.jsx — LAZY CHUNK (v3.64), split from app-cards.jsx.
// US Treasuries rates terminal; loaded on first Treasuries-tab open.

// ═══════════════════════════════════════════════════════════════════════════
// US TREASURIES TAB (v3.59) — rates terminal for a stock & options trader.
// Data: /api/treasury/* (Treasury.gov, FRED, TreasuryDirect, CFTC official;
// Yahoo delayed for MOVE/futures/ETFs). Anything a source can't provide
// renders "Data unavailable" — nothing is estimated in its place.
// ═══════════════════════════════════════════════════════════════════════════

const TSY_TENORS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"];
const TSY_KEY4 = {
  "2Y": 1,
  "5Y": 1,
  "10Y": 1,
  "30Y": 1
};
function TsyNA({
  why
}) {
  return /*#__PURE__*/React.createElement("span", {
    className: "tsy-na",
    title: why || "This field's source has no reliable value right now — nothing is estimated in its place."
  }, "Data unavailable");
}
// Yield-move coloring: red = yields RISING (bond prices falling), green =
// yields FALLING (bond prices rising). Every use carries the inverse-price tooltip.
function tsyBpCls(v) {
  return v == null ? "" : v > 0.05 ? "cd" : v < -0.05 ? "cu" : "";
}
const TSY_INV = "Yields and bond PRICES move in opposite directions: red = yield up = Treasury prices down.";
function TsyBp({
  v,
  d = 1
}) {
  if (v == null) return /*#__PURE__*/React.createElement("span", {
    className: "tsy-na"
  }, "\u2014");
  return /*#__PURE__*/React.createElement("span", {
    className: `num ${tsyBpCls(v)}`,
    title: TSY_INV
  }, v >= 0 ? "+" : "", v.toFixed(d), " bp");
}
function TsyFoot({
  src,
  at,
  delayed
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "tsy-foot"
  }, "Source: ", src, at ? ` · updated ${at}` : "", delayed ? " · delayed" : "");
}
function useTsy(apiFetch, section, ttl) {
  const [st, setSt] = useState({
    d: null,
    err: null,
    loading: true
  });
  const load = () => {
    sharedJson(apiFetch, `/api/treasury/${section}`, ttl).then(d => setSt({
      d,
      err: d && d.error && !d.ok ? d.error : null,
      loading: false
    })).catch(e => setSt({
      d: null,
      err: String(e),
      loading: false
    }));
  };
  useEffect(() => {
    load();
  }, []);
  return {
    ...st,
    retry: load
  };
}
function TsyLoading() {
  return /*#__PURE__*/React.createElement("div", {
    className: "tsy-loading"
  }, /*#__PURE__*/React.createElement("span", {
    className: "skel skel-line",
    style: {
      width: "60%"
    }
  }), /*#__PURE__*/React.createElement("span", {
    className: "skel skel-line",
    style: {
      width: "85%"
    }
  }), /*#__PURE__*/React.createElement("span", {
    className: "skel skel-line",
    style: {
      width: "40%"
    }
  }));
}
function TsyErr({
  err,
  retry
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "tsy-err"
  }, "Failed to load \u2014 ", String(err).slice(0, 120), " ", /*#__PURE__*/React.createElement("button", {
    type: "button",
    onClick: retry
  }, "Retry"));
}
// Collapsed-by-default heavy section: children mount (and fetch) on expand.
function TsyFold({
  kicker,
  title,
  hint,
  children,
  defaultOpen
}) {
  const [open, setOpen] = useState(!!defaultOpen);
  return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "tsy-foldhead",
    onClick: () => setOpen(o => !o),
    "aria-expanded": open
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, kicker), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, title)), /*#__PURE__*/React.createElement("span", {
    className: "tsy-foldarrow"
  }, open ? "▾" : "▸", !open && hint ? /*#__PURE__*/React.createElement("em", null, hint) : null)), open && children);
}

/* ── 1. Maturity cards ─────────────────────────────────────────────────── */
function TsyYieldCards({
  core
}) {
  if (core.loading) return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement(TsyLoading, null));
  if (!core.d || !core.d.ok) return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Treasury market summary"), /*#__PURE__*/React.createElement(TsyErr, {
    err: core.err || "no data",
    retry: core.retry
  }));
  const cards = core.d.yields || [];
  return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Treasury market summary \xB7 official EOD curve"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Yields by maturity")), /*#__PURE__*/React.createElement("span", {
    className: "tsy-datechip num",
    title: "U.S. Treasury publishes the daily par yield curve after each trading day."
  }, core.d.curve_date)), /*#__PURE__*/React.createElement("div", {
    className: "tsy-cards"
  }, cards.map(c => {
    const span = c.hi52w - c.lo52w;
    const pos = span > 0 ? Math.max(0, Math.min(100, (c.yield - c.lo52w) / span * 100)) : 50;
    return /*#__PURE__*/React.createElement("div", {
      key: c.tenor,
      className: `tsy-ycard ${TSY_KEY4[c.tenor] ? "key" : ""}`,
      title: `${c.tenor} Treasury par yield ${c.yield.toFixed(2)}% (as of ${core.d.curve_date}).\n52-week range ${c.lo52w.toFixed(2)}–${c.hi52w.toFixed(2)}%, currently the ${c.pct52w != null ? c.pct52w.toFixed(0) + "th percentile" : "—"}.\n${c.key ? "Why it matters: " + c.key + ".\n" : ""}${TSY_INV}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "tsy-ycard-t"
    }, c.tenor, c.key && /*#__PURE__*/React.createElement("i", {
      title: c.key
    }, "\u2605")), /*#__PURE__*/React.createElement("div", {
      className: "tsy-ycard-y num"
    }, c.yield.toFixed(2), "%"), /*#__PURE__*/React.createElement("div", {
      className: "tsy-ycard-chg"
    }, /*#__PURE__*/React.createElement("span", {
      className: `num ${tsyBpCls(c.bp1d)}`
    }, c.bp1d != null ? `${c.bp1d >= 0 ? "+" : ""}${c.bp1d.toFixed(0)}` : "—", /*#__PURE__*/React.createElement("em", null, "1d")), /*#__PURE__*/React.createElement("span", {
      className: `num ${tsyBpCls(c.bp5d)}`
    }, c.bp5d != null ? `${c.bp5d >= 0 ? "+" : ""}${c.bp5d.toFixed(0)}` : "—", /*#__PURE__*/React.createElement("em", null, "5d")), /*#__PURE__*/React.createElement("span", {
      className: `num ${tsyBpCls(c.bp21d)}`
    }, c.bp21d != null ? `${c.bp21d >= 0 ? "+" : ""}${c.bp21d.toFixed(0)}` : "—", /*#__PURE__*/React.createElement("em", null, "1m"))), /*#__PURE__*/React.createElement("div", {
      className: "tsy-52bar"
    }, /*#__PURE__*/React.createElement("i", {
      style: {
        left: `${pos}%`
      }
    })), /*#__PURE__*/React.createElement("div", {
      className: "tsy-52lbl num"
    }, /*#__PURE__*/React.createElement("span", null, c.lo52w.toFixed(2)), /*#__PURE__*/React.createElement("span", null, c.pct52w != null ? `${c.pct52w.toFixed(0)}%ile` : "—"), /*#__PURE__*/React.createElement("span", null, c.hi52w.toFixed(2))));
  })), /*#__PURE__*/React.createElement(TsyFoot, {
    src: core.d.source,
    at: core.d.curve_date
  }));
}

/* ── 2. Yield curve chart ──────────────────────────────────────────────── */
function TsyCurveSvg({
  snaps,
  cmp
}) {
  const W = 820,
    H = 280,
    L = 46,
    R = 12,
    T = 14,
    B = 28;
  const cur = snaps.current && snaps.current.points;
  const old = cmp !== "none" && snaps[cmp] ? snaps[cmp].points : null;
  if (!cur) return null;
  const ts = TSY_TENORS.filter(t => cur[t] != null);
  let vals = ts.map(t => cur[t]);
  if (old) vals = vals.concat(ts.map(t => old[t]).filter(v => v != null));
  const lo = Math.floor((Math.min(...vals) - 0.08) * 10) / 10;
  const hi = Math.ceil((Math.max(...vals) + 0.08) * 10) / 10;
  const x = i => L + i / Math.max(1, ts.length - 1) * (W - L - R);
  const y = v => T + (1 - (v - lo) / Math.max(0.01, hi - lo)) * (H - T - B);
  const path = pts => ts.map((t, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(pts[t]).toFixed(1)}`).join("");
  const ticks = [];
  for (let v = lo; v <= hi + 1e-9; v += Math.max(0.1, Math.round((hi - lo) / 5 * 10) / 10)) ticks.push(Math.round(v * 100) / 100);
  return /*#__PURE__*/React.createElement("svg", {
    className: "tsy-curvesvg",
    viewBox: `0 0 ${W} ${H}`,
    role: "img",
    "aria-label": "Treasury yield curve"
  }, ticks.map(v => /*#__PURE__*/React.createElement("g", {
    key: v
  }, /*#__PURE__*/React.createElement("line", {
    x1: L,
    x2: W - R,
    y1: y(v),
    y2: y(v),
    className: "tsy-grid"
  }), /*#__PURE__*/React.createElement("text", {
    x: L - 6,
    y: y(v) + 3.5,
    className: "tsy-axis",
    textAnchor: "end"
  }, v.toFixed(2)))), ts.map((t, i) => /*#__PURE__*/React.createElement("text", {
    key: t,
    x: x(i),
    y: H - 8,
    className: "tsy-axis",
    textAnchor: "middle"
  }, t)), old && /*#__PURE__*/React.createElement("path", {
    d: path(old),
    className: "tsy-line-old"
  }), /*#__PURE__*/React.createElement("path", {
    d: path(cur),
    className: "tsy-line-cur"
  }), ts.map((t, i) => /*#__PURE__*/React.createElement("circle", {
    key: t,
    cx: x(i),
    cy: y(cur[t]),
    r: "4",
    className: "tsy-dot"
  }, /*#__PURE__*/React.createElement("title", null, `${t}: ${cur[t].toFixed(2)}%${old && old[t] != null ? `\n${cmp} ago: ${old[t].toFixed(2)}% → ${((cur[t] - old[t]) * 100).toFixed(0)} bp change` : ""}`))));
}
function TsyCurveCard({
  core
}) {
  const [cmp, setCmp] = useState("1m");
  const [view, setView] = useState("chart");
  if (core.loading) return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement(TsyLoading, null));
  if (!core.d || !core.d.ok) return null;
  const snaps = core.d.snapshots || {};
  const reg = core.d.regime,
    mv = core.d.curve_moves;
  const cmps = [["1d", "1 day"], ["1w", "1 week"], ["1m", "1 month"], ["3m", "3 months"], ["1y", "1 year"]];
  return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Yield curve \xB7 all maturities"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Treasury yield curve")), /*#__PURE__*/React.createElement("div", {
    className: "tsy-ctrl"
  }, /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: cmp,
    onChange: e => setCmp(e.target.value),
    title: "Overlay the curve as of this long ago (dashed)."
  }, /*#__PURE__*/React.createElement("option", {
    value: "none"
  }, "No compare"), cmps.map(([k, l]) => snaps[k] ? /*#__PURE__*/React.createElement("option", {
    key: k,
    value: k
  }, "vs ", l, " ago") : null)), /*#__PURE__*/React.createElement("div", {
    className: "tsy-toggle"
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: view === "chart" ? "on" : "",
    onClick: () => setView("chart")
  }, "Chart"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: view === "table" ? "on" : "",
    onClick: () => setView("table")
  }, "Table")))), reg && /*#__PURE__*/React.createElement("div", {
    className: "tsy-regime",
    title: `Classified from the 5-day change: 2y ${reg.d2y_bp >= 0 ? "+" : ""}${reg.d2y_bp} bp, 10y ${reg.d10y_bp >= 0 ? "+" : ""}${reg.d10y_bp} bp → slope ${reg.slope_chg_bp >= 0 ? "+" : ""}${reg.slope_chg_bp} bp. "Bull" = yields falling (prices rallying), "bear" = yields rising. Steepener = long end rising vs short end.`
  }, core.d.curve_shape && /*#__PURE__*/React.createElement("span", {
    title: `Curve shape from today's official curve: ${core.d.curve_shape.detail}.`
  }, "SHAPE ", /*#__PURE__*/React.createElement("b", {
    className: core.d.curve_shape.label.startsWith("inverted") || core.d.curve_shape.label.startsWith("partially") ? "cd" : "cu"
  }, core.d.curve_shape.label.toUpperCase()), " \xB7"), /*#__PURE__*/React.createElement("b", {
    className: reg.label.startsWith("bull") ? "cu" : reg.label.startsWith("bear") ? "cd" : ""
  }, reg.label.toUpperCase()), /*#__PURE__*/React.createElement("span", null, "2y ", /*#__PURE__*/React.createElement(TsyBp, {
    v: reg.d2y_bp
  }), " \xB7 10y ", /*#__PURE__*/React.createElement(TsyBp, {
    v: reg.d10y_bp
  }), " over ", reg.window), mv && mv.biggest && /*#__PURE__*/React.createElement("span", null, "\xB7 biggest mover ", /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, mv.biggest.tenor), " ", /*#__PURE__*/React.createElement(TsyBp, {
    v: mv.biggest.bp5d
  })), mv && /*#__PURE__*/React.createElement("span", null, "\xB7 front end ", /*#__PURE__*/React.createElement(TsyBp, {
    v: mv.front_avg_bp5d
  }), " / long end ", /*#__PURE__*/React.createElement(TsyBp, {
    v: mv.long_avg_bp5d
  }))), view === "chart" ? /*#__PURE__*/React.createElement(TsyCurveSvg, {
    snaps: snaps,
    cmp: cmp
  }) : /*#__PURE__*/React.createElement("div", {
    className: "tsy-tablewrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tsy-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Maturity"), /*#__PURE__*/React.createElement("th", null, "Now"), cmps.map(([k, l]) => snaps[k] ? /*#__PURE__*/React.createElement("th", {
    key: k
  }, l, " ago") : null), /*#__PURE__*/React.createElement("th", null, "\u0394 vs ", cmp !== "none" ? cmp : "—"))), /*#__PURE__*/React.createElement("tbody", null, TSY_TENORS.filter(t => snaps.current && snaps.current.points[t] != null).map(t => {
    const cur = snaps.current.points[t];
    const oldv = cmp !== "none" && snaps[cmp] ? snaps[cmp].points[t] : null;
    return /*#__PURE__*/React.createElement("tr", {
      key: t
    }, /*#__PURE__*/React.createElement("td", {
      className: "num"
    }, t), /*#__PURE__*/React.createElement("td", {
      className: "num"
    }, /*#__PURE__*/React.createElement("b", null, cur.toFixed(2), "%")), cmps.map(([k]) => snaps[k] ? /*#__PURE__*/React.createElement("td", {
      key: k,
      className: "num"
    }, snaps[k].points[t] != null ? snaps[k].points[t].toFixed(2) : "—") : null), /*#__PURE__*/React.createElement("td", null, oldv != null ? /*#__PURE__*/React.createElement(TsyBp, {
      v: (cur - oldv) * 100,
      d: 0
    }) : "—"));
  })))), /*#__PURE__*/React.createElement(TsyFoot, {
    src: core.d.source,
    at: core.d.curve_date
  }));
}

/* ── 3. Spreads ────────────────────────────────────────────────────────── */
function TsySpreadsCard({
  core
}) {
  if (core.loading || !core.d || !core.d.ok) return null;
  const sp = core.d.spreads || [];
  return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Curve spreads \xB7 positive = normal slope, negative = inverted"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Important Treasury spreads"))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-tablewrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tsy-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Spread"), /*#__PURE__*/React.createElement("th", null, "Now"), /*#__PURE__*/React.createElement("th", null, "1d"), /*#__PURE__*/React.createElement("th", null, "1w"), /*#__PURE__*/React.createElement("th", null, "1m"), /*#__PURE__*/React.createElement("th", null, "%ile (3y)"), /*#__PURE__*/React.createElement("th", null, "State"), /*#__PURE__*/React.createElement("th", null, "Direction"))), /*#__PURE__*/React.createElement("tbody", null, sp.map(s => /*#__PURE__*/React.createElement("tr", {
    key: s.key,
    title: s.note || `${s.label}. Percentile over ~3 years of daily history. Direction from the 1-week change.`
  }, /*#__PURE__*/React.createElement("td", null, s.label), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("b", {
    className: s.inverted ? "cd" : ""
  }, s.bp >= 0 ? "+" : "", s.bp.toFixed(0), " bp")), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(TsyBp, {
    v: s.d1,
    d: 0
  })), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(TsyBp, {
    v: s.d5,
    d: 0
  })), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(TsyBp, {
    v: s.d21,
    d: 0
  })), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, s.pctile != null ? s.pctile.toFixed(0) : "—"), /*#__PURE__*/React.createElement("td", null, s.inverted ? /*#__PURE__*/React.createElement("span", {
    className: "tsy-pill down"
  }, "INVERTED") : /*#__PURE__*/React.createElement("span", {
    className: "tsy-pill up"
  }, "POSITIVE")), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, s.trend ? s.trend === "steepening" ? "↗ steepening" : s.trend === "flattening" ? "↘ flattening" : "→ flat" : "—")))))), /*#__PURE__*/React.createElement(TsyFoot, {
    src: "U.S. Treasury daily curve; EFFR from FRED",
    at: core.d.curve_date
  }));
}

/* ── 4. Trader interpretation ──────────────────────────────────────────── */
function TsySignalsCard({
  core
}) {
  if (core.loading || !core.d || !core.d.ok) return null;
  const sig = core.d.signals || [];
  return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Rules-based read \xB7 every signal cites the numbers that fired it"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "What rates imply for your trading"))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-sigs"
  }, sig.map((s, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "tsy-sig"
  }, /*#__PURE__*/React.createElement("span", {
    className: `tsy-sigdot ${s.tone}`
  }), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "tsy-sigl"
  }, s.label, " ", /*#__PURE__*/React.createElement("b", {
    className: `tsy-pill ${s.tone === "up" ? "up" : s.tone === "down" ? "down" : "mut"}`
  }, s.level)), /*#__PURE__*/React.createElement("div", {
    className: "tsy-sigd"
  }, s.detail))))), /*#__PURE__*/React.createElement(TsyFoot, {
    src: "Derived from the displayed Treasury/FRED data \u2014 fixed rules, no AI summarization",
    at: core.d.curve_date
  }));
}

/* ── 7. Inflation expectations + decomposition ─────────────────────────── */
function TsyExpectationsCard({
  core
}) {
  if (core.loading || !core.d || !core.d.ok) return null;
  const e = core.d.expectations || {};
  const dec = core.d.decomposition;
  const rows = [["be5", "5y breakeven"], ["be10", "10y breakeven"], ["f5y5y", "5y5y forward"], ["real5", "5y TIPS real"], ["real10", "10y TIPS real"], ["real30", "30y TIPS real"]];
  return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Breakevens & TIPS real yields (FRED, daily)"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Inflation expectations"))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-tablewrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tsy-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Series"), /*#__PURE__*/React.createElement("th", null, "Now"), /*#__PURE__*/React.createElement("th", null, "1d"), /*#__PURE__*/React.createElement("th", null, "1w"), /*#__PURE__*/React.createElement("th", null, "1m"), /*#__PURE__*/React.createElement("th", null, "52w %ile"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(([k, l]) => {
    const s = e[k];
    return /*#__PURE__*/React.createElement("tr", {
      key: k
    }, /*#__PURE__*/React.createElement("td", null, l), s ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("td", {
      className: "num"
    }, /*#__PURE__*/React.createElement("b", null, s.value.toFixed(2), "%")), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(TsyBp, {
      v: s.d1 != null ? s.d1 * 100 : null,
      d: 0
    })), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(TsyBp, {
      v: s.d5 != null ? s.d5 * 100 : null,
      d: 0
    })), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(TsyBp, {
      v: s.d21 != null ? s.d21 * 100 : null,
      d: 0
    })), /*#__PURE__*/React.createElement("td", {
      className: "num"
    }, s.pct52w != null ? s.pct52w.toFixed(0) : "—")) : /*#__PURE__*/React.createElement("td", {
      colSpan: "5"
    }, /*#__PURE__*/React.createElement(TsyNA, null)));
  })))), dec && /*#__PURE__*/React.createElement("div", {
    className: "tsy-decomp",
    title: "\u039410y nominal = \u039410y TIPS real + \u039410y breakeven (identity, FRED daily closes)."
  }, "Nominal 10y ", dec.nominal_bp >= 0 ? "+" : "", dec.nominal_bp, " bp over ", dec.window, " = real ", dec.real_bp >= 0 ? "+" : "", dec.real_bp, " bp + breakeven ", dec.breakeven_bp >= 0 ? "+" : "", dec.breakeven_bp, " bp \u2192 ", /*#__PURE__*/React.createElement("b", null, "driven by ", dec.verdict)), /*#__PURE__*/React.createElement(TsyFoot, {
    src: "FRED T5YIE / T10YIE / T5YIFR / DFII5 / DFII10 / DFII30"
  }));
}

/* ── 8. CPI countdown & event risk ─────────────────────────────────────── */
function TsyEventsCard({
  core
}) {
  if (core.loading || !core.d || !core.d.ok) return null;
  const ev = core.d.events || {};
  const cpi = ev.next_cpi,
    fomc = ev.next_fomc,
    jobs = ev.next_jobs;
  return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Event risk \xB7 scheduled macro catalysts"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "CPI countdown & upcoming events"))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-events"
  }, ev.schedule && ev.schedule.note && /*#__PURE__*/React.createElement("div", {
    className: "tsy-sched-warn",
    title: "The maintained CPI/FOMC schedule tables have run out. Old dates are never recycled \u2014 update MACRO_SCHEDULE in treasury.py from the official BLS / Federal Reserve calendars."
  }, "\u26A0 ", ev.schedule.note), cpi && cpi.date && /*#__PURE__*/React.createElement("div", {
    className: "tsy-cd",
    title: `Next CPI release per the BLS schedule: ${cpi.date} at ${cpi.time_et}. Consensus estimates need a paid feed — never estimated here.`
  }, /*#__PURE__*/React.createElement("em", null, "NEXT CPI \xB7 ", cpi.date, " \xB7 ", cpi.time_et), cpi.countdown ? /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, cpi.countdown.days, "d ", cpi.countdown.hours, "h ", cpi.countdown.minutes, "m") : /*#__PURE__*/React.createElement("b", null, "\u2014"), /*#__PURE__*/React.createElement("span", null, "Consensus: ", /*#__PURE__*/React.createElement(TsyNA, {
    why: "No free reliable consensus feed \u2014 not estimated."
  }))), cpi && !cpi.date && /*#__PURE__*/React.createElement("div", {
    className: "tsy-cd"
  }, /*#__PURE__*/React.createElement("em", null, "NEXT CPI"), /*#__PURE__*/React.createElement("b", null, "\u2014"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement(TsyNA, {
    why: "Schedule unavailable \u2014 requires update (MACRO_SCHEDULE in treasury.py)."
  }), " Schedule requires update")), /*#__PURE__*/React.createElement("div", {
    className: "tsy-evrows"
  }, fomc && fomc.date && /*#__PURE__*/React.createElement("div", {
    className: "tsy-evrow"
  }, /*#__PURE__*/React.createElement("em", null, "FOMC decision"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, fomc.date), /*#__PURE__*/React.createElement("span", null, fomc.days, " days \xB7 ", fomc.source)), fomc && !fomc.date && /*#__PURE__*/React.createElement("div", {
    className: "tsy-evrow"
  }, /*#__PURE__*/React.createElement("em", null, "FOMC decision"), /*#__PURE__*/React.createElement("b", null, "\u2014"), /*#__PURE__*/React.createElement("span", null, "Schedule requires update")), jobs && /*#__PURE__*/React.createElement("div", {
    className: "tsy-evrow"
  }, /*#__PURE__*/React.createElement("em", null, "Employment report"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, jobs.date), /*#__PURE__*/React.createElement("span", null, jobs.source)), /*#__PURE__*/React.createElement("div", {
    className: "tsy-evrow"
  }, /*#__PURE__*/React.createElement("em", null, "PPI / PCE"), /*#__PURE__*/React.createElement("b", null, "\u2014"), /*#__PURE__*/React.createElement("span", null, ev.note_ppi_pce))), (ev.upcoming_auctions || []).length > 0 && /*#__PURE__*/React.createElement("div", {
    className: "tsy-upauc"
  }, /*#__PURE__*/React.createElement("em", null, "UPCOMING TREASURY AUCTIONS"), (ev.upcoming_auctions || []).slice(0, 8).map((a, i) => /*#__PURE__*/React.createElement("span", {
    key: i,
    className: "tsy-aucchip num",
    title: `${a.term} ${a.type} auction ${a.auction_date}${a.offering ? `, offering $${(a.offering / 1e9).toFixed(0)}B` : ""}`
  }, a.auction_date && a.auction_date.slice(5), " ", a.term, " ", a.type)))), /*#__PURE__*/React.createElement(TsyFoot, {
    src: "BLS / Federal Reserve schedules \xB7 auctions from TreasuryDirect (official)"
  }));
}

/* ── 11. MOVE ──────────────────────────────────────────────────────────── */
function TsyMoveCard({
  core
}) {
  if (core.loading || !core.d || !core.d.ok) return null;
  const m = core.d.move;
  return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Treasury market volatility \u2014 NOT the stock-market VIX"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "MOVE index")), m && /*#__PURE__*/React.createElement("span", {
    className: `tsy-pill ${m.regime === "low" || m.regime === "normal" ? "up" : m.regime === "elevated" ? "mut" : "down"}`
  }, m.regime.toUpperCase())), m ? /*#__PURE__*/React.createElement("div", {
    className: "tsy-move"
  }, /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, m.value), /*#__PURE__*/React.createElement("div", {
    className: "tsy-move-chg"
  }, /*#__PURE__*/React.createElement("span", null, "1d ", /*#__PURE__*/React.createElement(TsyBp, {
    v: m.d1
  })), /*#__PURE__*/React.createElement("span", null, "5d ", /*#__PURE__*/React.createElement(TsyBp, {
    v: m.d5
  })), /*#__PURE__*/React.createElement("span", null, "1m ", /*#__PURE__*/React.createElement(TsyBp, {
    v: m.d21
  })), /*#__PURE__*/React.createElement("span", null, "52w %ile ", /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, m.pct52w != null ? m.pct52w.toFixed(0) : "—"))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-sigd"
  }, "MOVE measures implied volatility of Treasury options \u2014 rates uncertainty, not equity volatility. Bands: ", m.bands, "."), /*#__PURE__*/React.createElement(TsyFoot, {
    src: m.source,
    at: m.date,
    delayed: true
  })) : /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "8px 0"
    }
  }, /*#__PURE__*/React.createElement(TsyNA, {
    why: "^MOVE quote source unreachable \u2014 not estimated."
  })));
}

/* ── 5. CPI dashboard + trend chart ────────────────────────────────────── */
function TsySeriesSvg({
  series,
  period
}) {
  const W = 820,
    H = 260,
    L = 46,
    R = 10,
    T = 12,
    B = 24;
  const cut = period === "max" ? 0 : {
    "1y": 12,
    "2y": 24,
    "5y": 60,
    "10y": 120
  }[period] || 24;
  const shown = series.map(s => ({
    ...s,
    pts: cut ? s.pts.slice(-cut) : s.pts
  })).filter(s => s.pts.length > 1);
  if (!shown.length) return null;
  const all = shown.flatMap(s => s.pts.map(p => p.v));
  const lo = Math.min(...all),
    hi = Math.max(...all);
  const pad = Math.max(0.2, (hi - lo) * 0.06);
  const y = v => T + (1 - (v - (lo - pad)) / Math.max(0.01, hi + pad - (lo - pad))) * (H - T - B);
  const n = Math.max(...shown.map(s => s.pts.length));
  const x = (i, len) => L + (i + (n - len)) / Math.max(1, n - 1) * (W - L - R);
  const step = Math.max(0.5, Math.round((hi - lo + 2 * pad) / 5 * 2) / 2);
  const ticks = [];
  for (let v = Math.ceil((lo - pad) / step) * step; v <= hi + pad; v += step) ticks.push(v);
  const xs = shown[0].pts;
  const xevery = Math.max(1, Math.floor(xs.length / 6));
  return /*#__PURE__*/React.createElement("svg", {
    className: "tsy-curvesvg",
    viewBox: `0 0 ${W} ${H}`,
    role: "img",
    "aria-label": "CPI trend"
  }, ticks.map(v => /*#__PURE__*/React.createElement("g", {
    key: v
  }, /*#__PURE__*/React.createElement("line", {
    x1: L,
    x2: W - R,
    y1: y(v),
    y2: y(v),
    className: "tsy-grid"
  }), /*#__PURE__*/React.createElement("text", {
    x: L - 6,
    y: y(v) + 3.5,
    className: "tsy-axis",
    textAnchor: "end"
  }, v.toFixed(1)))), xs.map((p, i) => i % xevery === 0 ? /*#__PURE__*/React.createElement("text", {
    key: p.d,
    x: x(i, xs.length),
    y: H - 6,
    className: "tsy-axis",
    textAnchor: "middle"
  }, p.d) : null), /*#__PURE__*/React.createElement("line", {
    x1: L,
    x2: W - R,
    y1: y(2),
    y2: y(2),
    className: "tsy-target"
  }), shown.map(s => /*#__PURE__*/React.createElement("path", {
    key: s.key,
    d: s.pts.map((p, i) => `${i ? "L" : "M"}${x(i, s.pts.length).toFixed(1)},${y(p.v).toFixed(1)}`).join(""),
    className: "tsy-seriesline",
    style: {
      stroke: s.color
    }
  }, /*#__PURE__*/React.createElement("title", null, s.label))));
}
// Fixed distinct colors — theme accent is green, which collided with the
// green "up" color when both series were shown (user report).
const TSY_CPI_SERIES = [["headline_yoy", "Headline YoY", "#4E9CF5"], ["core_yoy", "Core YoY", "#E8A33D"], ["headline_mom", "Headline MoM", "#8b5cf6"], ["core_mom", "Core MoM", "#06b6d4"], ["core_3m_ann", "Core 3m ann.", "#3BD996"], ["core_6m_ann", "Core 6m ann.", "#F56D77"]];
function TsyCpiCard({
  apiFetch
}) {
  const inf = useTsy(apiFetch, "inflation", 3600000);
  const [period, setPeriod] = useState("2y");
  const [on, setOn] = useState({
    headline_yoy: true,
    core_yoy: true,
    core_3m_ann: true
  });
  if (inf.loading) return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "CPI & inflation"), /*#__PURE__*/React.createElement(TsyLoading, null));
  if (!inf.d || !inf.d.ok) return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "CPI & inflation"), /*#__PURE__*/React.createElement(TsyErr, {
    err: inf.err || "no data",
    retry: inf.retry
  }));
  const rows = (inf.d.rows || []).filter(r => r.ok);
  const core = rows.find(r => r.key === "core");
  const charts = inf.d.charts || {};
  const series = TSY_CPI_SERIES.filter(([k]) => on[k] && charts[k]).map(([k, label, color]) => ({
    key: k,
    label,
    color,
    pts: charts[k]
  }));
  return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "CPI & inflation \xB7 BLS data via FRED (seasonally adjusted)"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Inflation dashboard")), core && /*#__PURE__*/React.createElement("span", {
    className: "tsy-datechip num",
    title: "Most recent CPI data month."
  }, core.month)), /*#__PURE__*/React.createElement("div", {
    className: "tsy-cpigrid"
  }, rows.map(r => /*#__PURE__*/React.createElement("div", {
    key: r.key,
    className: `tsy-cpicell ${r.key === "headline" || r.key === "core" ? "big" : ""}`,
    title: `${r.label} — data month ${r.month}.\nMoM ${r.mom != null ? r.mom + "%" : "—"} (prev ${r.mom_prev != null ? r.mom_prev + "%" : "—"})\nYoY ${r.yoy != null ? r.yoy + "%" : "—"} (prev ${r.yoy_prev != null ? r.yoy_prev + "%" : "—"})\nYoY sits at the ${r.yoy_pctile_10y != null ? r.yoy_pctile_10y.toFixed(0) + "th percentile of the last 10 years" : "—"}.\nConsensus: no free reliable feed — not estimated.`
  }, /*#__PURE__*/React.createElement("em", null, r.label), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, r.yoy != null ? `${r.yoy.toFixed(2)}%` : "—", /*#__PURE__*/React.createElement("i", null, "YoY")), /*#__PURE__*/React.createElement("span", {
    className: "num"
  }, "MoM ", r.mom != null ? `${r.mom >= 0 ? "+" : ""}${r.mom.toFixed(2)}%` : "—", /*#__PURE__*/React.createElement("i", {
    className: r.yoy != null && r.yoy_prev != null ? r.yoy < r.yoy_prev ? "cu" : r.yoy > r.yoy_prev ? "cd" : "" : ""
  }, r.yoy != null && r.yoy_prev != null ? r.yoy < r.yoy_prev ? "▼ cooling" : r.yoy > r.yoy_prev ? "▲ heating" : "flat" : "")), r.key === "core" && /*#__PURE__*/React.createElement("span", {
    className: "num tsy-annrow"
  }, "3m ann ", /*#__PURE__*/React.createElement("b", null, r.ann3m != null ? r.ann3m.toFixed(2) + "%" : "—"), " \xB7 6m ann ", /*#__PURE__*/React.createElement("b", null, r.ann6m != null ? r.ann6m.toFixed(2) + "%" : "—")))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-cpicell",
    title: inf.d.supercore && inf.d.supercore.note
  }, /*#__PURE__*/React.createElement("em", null, "Supercore (svcs ex-shelter)"), /*#__PURE__*/React.createElement(TsyNA, {
    why: inf.d.supercore && inf.d.supercore.note
  }))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-ctrl tsy-chartctrl"
  }, TSY_CPI_SERIES.map(([k, label, color]) => charts[k] ? /*#__PURE__*/React.createElement("button", {
    key: k,
    type: "button",
    className: `tsy-serbtn ${on[k] ? "on" : ""}`,
    style: on[k] ? {
      borderColor: color,
      color
    } : null,
    onClick: () => setOn(o => ({
      ...o,
      [k]: !o[k]
    }))
  }, label) : null), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: period,
    onChange: e => setPeriod(e.target.value)
  }, [["1y", "1 year"], ["2y", "2 years"], ["5y", "5 years"], ["10y", "10 years"], ["max", "Max"]].map(([k, l]) => /*#__PURE__*/React.createElement("option", {
    key: k,
    value: k
  }, l)))), /*#__PURE__*/React.createElement(TsySeriesSvg, {
    series: series,
    period: period
  }), /*#__PURE__*/React.createElement("div", {
    className: "tsy-sigd"
  }, "Dashed line = 2% (Fed target, on PCE \u2014 CPI shown here typically runs a bit above PCE). Consensus estimates: no free reliable source \u2014 differences vs consensus are not shown rather than guessed."), /*#__PURE__*/React.createElement(TsyFoot, {
    src: inf.d.source,
    at: core ? core.month : null
  }));
}

/* ── 6. CPI releases & market reaction ─────────────────────────────────── */
function TsyCpiReactions({
  apiFetch
}) {
  const inf = useTsy(apiFetch, "inflation", 3600000);
  const [flt, setFlt] = useState("all");
  if (inf.loading) return /*#__PURE__*/React.createElement(TsyLoading, null);
  const rx = inf.d && inf.d.reactions;
  if (!rx || !rx.ok) return /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "8px 0"
    }
  }, /*#__PURE__*/React.createElement(TsyNA, {
    why: "No reaction history available."
  }));
  const rows = (rx.rows || []).filter(r => flt === "all" || r.class === flt);
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "tsy-ctrl",
    style: {
      marginBottom: 8
    }
  }, /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: flt,
    onChange: e => setFlt(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "All releases"), /*#__PURE__*/React.createElement("option", {
    value: "hot"
  }, "Hot core CPI"), /*#__PURE__*/React.createElement("option", {
    value: "cool"
  }, "Cool core CPI"), /*#__PURE__*/React.createElement("option", {
    value: "inline"
  }, "In-line core CPI")), /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 11.5
    }
  }, rows.length, " releases")), /*#__PURE__*/React.createElement("div", {
    className: "tsy-tablewrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tsy-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Release"), /*#__PURE__*/React.createElement("th", null, "Data mo."), /*#__PURE__*/React.createElement("th", null, "Head MoM"), /*#__PURE__*/React.createElement("th", null, "Core MoM"), /*#__PURE__*/React.createElement("th", null, "vs trend"), /*#__PURE__*/React.createElement("th", null, "2y"), /*#__PURE__*/React.createElement("th", null, "10y"), /*#__PURE__*/React.createElement("th", null, "SPY"), /*#__PURE__*/React.createElement("th", null, "QQQ"), /*#__PURE__*/React.createElement("th", null, "IWM"), /*#__PURE__*/React.createElement("th", null, "TLT"), /*#__PURE__*/React.createElement("th", null, "GLD"), /*#__PURE__*/React.createElement("th", null, "UUP"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.date
  }, /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.date), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.data_month), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.headline_mom != null ? `${r.headline_mom >= 0 ? "+" : ""}${r.headline_mom.toFixed(2)}%` : "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.core_mom != null ? `${r.core_mom >= 0 ? "+" : ""}${r.core_mom.toFixed(2)}%` : "—"), /*#__PURE__*/React.createElement("td", null, r.class ? /*#__PURE__*/React.createElement("span", {
    className: `tsy-pill ${r.class === "hot" ? "down" : r.class === "cool" ? "up" : "mut"}`
  }, r.class.toUpperCase()) : "—"), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(TsyBp, {
    v: r.y2_bp,
    d: 0
  })), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(TsyBp, {
    v: r.y10_bp,
    d: 0
  })), ["spy", "qqq", "iwm", "tlt", "gld", "uup"].map(k => /*#__PURE__*/React.createElement("td", {
    key: k,
    className: `num ${r[k] != null ? r[k] >= 0 ? "cu" : "cd" : ""}`
  }, r[k] != null ? `${r[k] >= 0 ? "+" : ""}${r[k].toFixed(2)}%` : "—"))))))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-sigd"
  }, rx.note, " ", rx.intraday), /*#__PURE__*/React.createElement(TsyFoot, {
    src: "Release dates: BLS schedule \xB7 CPI values: FRED \xB7 market closes: Yahoo (delayed)"
  }));
}

/* ── 9/10. Futures + ETF proxies ───────────────────────────────────────── */
function TsyMarketsCards({
  apiFetch,
  onOpenTicker
}) {
  const mk = useTsy(apiFetch, "markets", 900000);
  if (mk.loading) return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Treasury futures & ETFs"), /*#__PURE__*/React.createElement(TsyLoading, null));
  if (!mk.d || !mk.d.ok) return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Treasury futures & ETFs"), /*#__PURE__*/React.createElement(TsyErr, {
    err: mk.err || mk.d && mk.d.error || "no data",
    retry: mk.retry
  }));
  const futs = mk.d.futures || [];
  const etfs = mk.d.etfs || [];
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Bond ETF proxies \xB7 click a row to open it in the Analyze workflow"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Treasury ETFs"))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-tablewrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tsy-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "ETF"), /*#__PURE__*/React.createElement("th", null, "Price"), /*#__PURE__*/React.createElement("th", null, "1d"), /*#__PURE__*/React.createElement("th", null, "5d"), /*#__PURE__*/React.createElement("th", null, "1m"), /*#__PURE__*/React.createElement("th", null, "Duration\u2248"), /*#__PURE__*/React.createElement("th", null, "Vol"), /*#__PURE__*/React.createElement("th", null, "RelVol"), /*#__PURE__*/React.createElement("th", null, "vs 20d"), /*#__PURE__*/React.createElement("th", null, "vs 50d"), /*#__PURE__*/React.createElement("th", null, "vs 200d"))), /*#__PURE__*/React.createElement("tbody", null, etfs.map(t => /*#__PURE__*/React.createElement("tr", {
    key: t.sym,
    className: "tsy-rowlink",
    onClick: () => t.ok && onOpenTicker && onOpenTicker(t.sym),
    title: `Open ${t.sym} on the Analyze tab. Duration ≈ ${t.duration} yrs: a +10bp yield move ≈ ${t.duration != null ? (-t.duration * 0.1).toFixed(1) : "—"}% price move.`
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, t.sym)), t.ok ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, fmt$(t.last, 2)), ["d1", "d5", "d21"].map(k => /*#__PURE__*/React.createElement("td", {
    key: k,
    className: `num ${t[k] != null ? t[k] >= 0 ? "cu" : "cd" : ""}`
  }, t[k] != null ? `${t[k] >= 0 ? "+" : ""}${t[k]}%` : "—")), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, t.duration, "y"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, t.volume != null ? (t.volume / 1e6).toFixed(1) + "M" : "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, t.rel_volume != null ? t.rel_volume + "×" : "—"), ["dma20", "dma50", "dma200"].map(k => /*#__PURE__*/React.createElement("td", {
    key: k,
    className: `num ${t[k] != null ? t[k] >= 0 ? "cu" : "cd" : ""}`
  }, t[k] != null ? `${t[k] >= 0 ? "+" : ""}${t[k]}%` : "—"))) : /*#__PURE__*/React.createElement("td", {
    colSpan: "10"
  }, /*#__PURE__*/React.createElement(TsyNA, null))))))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-sigd"
  }, mk.d.etf_note, " Distribution yields: ", /*#__PURE__*/React.createElement(TsyNA, {
    why: "No reliable free source for current distribution yields \u2014 not estimated."
  })), /*#__PURE__*/React.createElement(TsyFoot, {
    src: mk.d.source,
    delayed: true
  })));
}

/* ── 15. Cross-asset correlations ──────────────────────────────────────── */
function TsyCorrTable({
  apiFetch
}) {
  const mk = useTsy(apiFetch, "markets", 900000);
  const [w, setW] = useState(60);
  if (mk.loading) return /*#__PURE__*/React.createElement(TsyLoading, null);
  const c = mk.d && mk.d.correlations;
  if (!c || !c.ok) return /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "8px 0"
    }
  }, /*#__PURE__*/React.createElement(TsyNA, {
    why: "Correlation inputs unavailable."
  }));
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "tsy-ctrl",
    style: {
      marginBottom: 8
    }
  }, c.windows.map(win => /*#__PURE__*/React.createElement("button", {
    key: win,
    type: "button",
    className: `tsy-serbtn ${w === win ? "on" : ""}`,
    onClick: () => setW(win)
  }, win, "d"))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-corrbars"
  }, c.rows.map(r => {
    const v = r[`w${w}`];
    return /*#__PURE__*/React.createElement("div", {
      key: r.sym,
      className: "tsy-corrrow",
      title: `${r.label}: ${v != null ? v : "—"} correlation of daily returns vs daily CHANGE in the 10y yield over the last ${w} trading days. Positive = tends to rise when yields rise. Correlation ≠ causation.`
    }, /*#__PURE__*/React.createElement("em", null, r.label), /*#__PURE__*/React.createElement("div", {
      className: "tsy-corrbar"
    }, /*#__PURE__*/React.createElement("i", {
      className: v != null && v >= 0 ? "pos" : "neg",
      style: {
        width: `${Math.abs(v || 0) * 50}%`,
        [v != null && v >= 0 ? "left" : "right"]: "50%"
      }
    })), /*#__PURE__*/React.createElement("b", {
      className: `num ${v != null ? v >= 0 ? "cu" : "cd" : ""}`
    }, v != null ? v.toFixed(2) : "—"));
  })), /*#__PURE__*/React.createElement("div", {
    className: "tsy-sigd"
  }, c.note), /*#__PURE__*/React.createElement(TsyFoot, {
    src: "FRED DGS10 + Yahoo closes (delayed)"
  }));
}

/* ── 12. Auctions ──────────────────────────────────────────────────────── */
function TsyAuctions({
  apiFetch
}) {
  const au = useTsy(apiFetch, "auctions", 3600000);
  if (au.loading) return /*#__PURE__*/React.createElement(TsyLoading, null);
  if (!au.d || !au.d.ok) return /*#__PURE__*/React.createElement(TsyErr, {
    err: au.err || "TreasuryDirect unavailable",
    retry: au.retry
  });
  const strengthPill = a => a.strength ? /*#__PURE__*/React.createElement("span", {
    className: `tsy-pill ${a.strength === "strong" ? "up" : a.strength === "weak" ? "down" : "mut"}`,
    title: a.vs_prior ? `Rule: bid-to-cover ${a.btc} vs ${a.vs_prior.btc_avg10} avg of prior ${a.vs_prior.n}; indirect ${a.indirect_pct}% vs ${a.vs_prior.indirect_avg10}% avg. Strong = both above; weak = both below.` : ""
  }, a.strength.toUpperCase()) : /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\u2014");
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "tsy-tablewrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tsy-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Auction"), /*#__PURE__*/React.createElement("th", null, "Date"), /*#__PURE__*/React.createElement("th", null, "Settle"), /*#__PURE__*/React.createElement("th", null, "Size"), /*#__PURE__*/React.createElement("th", null, "High yield"), /*#__PURE__*/React.createElement("th", null, "Bid-to-cover"), /*#__PURE__*/React.createElement("th", null, "Indirect"), /*#__PURE__*/React.createElement("th", null, "Direct"), /*#__PURE__*/React.createElement("th", null, "Dealers"), /*#__PURE__*/React.createElement("th", null, "Read"))), /*#__PURE__*/React.createElement("tbody", null, (au.d.recent_coupons || []).map((a, i) => /*#__PURE__*/React.createElement("tr", {
    key: i
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, a.term), " ", a.type), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, a.date), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, a.settle), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, a.offering ? `$${(a.offering / 1e9).toFixed(0)}B` : "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, a.high_yield != null ? a.high_yield.toFixed(3) + "%" : "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, a.btc != null ? a.btc.toFixed(2) : "—", a.vs_prior ? /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " /", a.vs_prior.btc_avg10) : null), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, a.indirect_pct != null ? a.indirect_pct + "%" : "—", a.vs_prior && a.vs_prior.indirect_avg10 != null ? /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " /", a.vs_prior.indirect_avg10, "%") : null), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, a.direct_pct != null ? a.direct_pct + "%" : "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, a.dealer_pct != null ? a.dealer_pct + "%" : "—"), /*#__PURE__*/React.createElement("td", null, strengthPill(a))))))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-sigd"
  }, au.d.note, " Small figures after \"/\" = average of the prior 10 auctions of the same security. Tail / when-issued comparison: ", /*#__PURE__*/React.createElement(TsyNA, {
    why: "When-issued yields need dealer quotes (no free source) \u2014 not estimated."
  })), /*#__PURE__*/React.createElement(TsyFoot, {
    src: au.d.source
  }));
}

/* ── 13. Fed expectations ──────────────────────────────────────────────── */
function TsyFedCard({
  apiFetch
}) {
  const fd = useTsy(apiFetch, "fed", 1800000);
  if (fd.loading) return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Fed rate expectations"), /*#__PURE__*/React.createElement(TsyLoading, null));
  if (!fd.d || !fd.d.ok) return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Fed rate expectations"), /*#__PURE__*/React.createElement(TsyErr, {
    err: fd.err || "no data",
    retry: fd.retry
  }));
  const t = fd.d.target,
    nm = fd.d.next_meeting,
    path = fd.d.implied_path || [];
  return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Policy rate \xB7 market-implied path"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Fed rate expectations")), nm && /*#__PURE__*/React.createElement("span", {
    className: "tsy-datechip num",
    title: "Next scheduled FOMC decision."
  }, nm.date, " \xB7 ", nm.days, "d"), !nm && fd.d.schedule_note && /*#__PURE__*/React.createElement("span", {
    className: "tsy-datechip",
    title: fd.d.schedule_note
  }, "\u26A0 schedule update needed")), /*#__PURE__*/React.createElement("div", {
    className: "tsy-fed"
  }, t && /*#__PURE__*/React.createElement("div", {
    className: "tsy-cd"
  }, /*#__PURE__*/React.createElement("em", null, "CURRENT TARGET RANGE"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, t.lower.toFixed(2), "\u2013", t.upper.toFixed(2), "%"), /*#__PURE__*/React.createElement("span", null, t.source, " \xB7 as of ", t.date)), fd.d.yearend && /*#__PURE__*/React.createElement("div", {
    className: "tsy-cd",
    title: "Implied avg fed funds for December from CME 30-day FF futures (100 \u2212 price), vs the current target midpoint."
  }, /*#__PURE__*/React.createElement("em", null, "MARKET-IMPLIED BY ", fd.d.yearend.month), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, fd.d.yearend.implied_rate.toFixed(2), "%"), /*#__PURE__*/React.createElement("span", null, "\u2248 ", Math.abs(fd.d.yearend.cuts_25bp).toFixed(1), " \xD7 25bp ", fd.d.yearend.cuts_25bp >= 0 ? "of cuts" : "of hikes", " priced")), path.length > 0 ? /*#__PURE__*/React.createElement("div", {
    className: "tsy-tablewrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tsy-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Month"), /*#__PURE__*/React.createElement("th", null, "Implied avg rate"), /*#__PURE__*/React.createElement("th", null, "1d \u0394"))), /*#__PURE__*/React.createElement("tbody", null, path.map(p => /*#__PURE__*/React.createElement("tr", {
    key: p.month
  }, /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, p.month), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, /*#__PURE__*/React.createElement("b", null, p.implied_rate.toFixed(2), "%")), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(TsyBp, {
    v: p.d1_bp,
    d: 0
  }))))))) : /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "6px 0"
    }
  }, "Implied path: ", /*#__PURE__*/React.createElement(TsyNA, {
    why: "Fed funds futures quotes unreachable \u2014 not estimated."
  })), /*#__PURE__*/React.createElement("div", {
    className: "tsy-sigd"
  }, fd.d.implied_note, " Per-meeting probabilities: ", /*#__PURE__*/React.createElement(TsyNA, {
    why: "Requires CME FedWatch data \u2014 not estimated."
  }))), /*#__PURE__*/React.createElement(TsyFoot, {
    src: "FRED (target range, official) \xB7 CME ZQ futures via Yahoo (path, delayed)"
  }));
}

/* ── 14. COT positioning ───────────────────────────────────────────────── */
function TsyCot({
  apiFetch
}) {
  const ct = useTsy(apiFetch, "cot", 3600000);
  if (ct.loading) return /*#__PURE__*/React.createElement(TsyLoading, null);
  if (!ct.d || !ct.d.ok) return /*#__PURE__*/React.createElement(TsyErr, {
    err: ct.err || "CFTC unavailable",
    retry: ct.retry
  });
  const g = grp => grp ? /*#__PURE__*/React.createElement("span", {
    className: "num"
  }, (grp.net >= 0 ? "+" : "") + grp.net.toLocaleString(), /*#__PURE__*/React.createElement("em", {
    className: "muted"
  }, " wk ", grp.wk_chg != null ? (grp.wk_chg >= 0 ? "+" : "") + grp.wk_chg.toLocaleString() : "—", " \xB7 ", grp.pctile != null ? grp.pctile.toFixed(0) + "%ile" : "—"), grp.crowded && /*#__PURE__*/React.createElement("b", {
    className: `tsy-pill ${grp.crowded === "long" ? "up" : "down"}`,
    title: "Net position at a 3-year extreme (\u226590th or \u226410th percentile). Context, not a signal by itself \u2014 crowded positioning can persist or unwind violently."
  }, "CROWDED ", grp.crowded.toUpperCase())) : /*#__PURE__*/React.createElement(TsyNA, null);
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "tsy-tablewrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tsy-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Futures"), /*#__PURE__*/React.createElement("th", null, "Report"), /*#__PURE__*/React.createElement("th", null, "Asset managers"), /*#__PURE__*/React.createElement("th", null, "Leveraged funds"), /*#__PURE__*/React.createElement("th", null, "Dealers"), /*#__PURE__*/React.createElement("th", null, "Non-comm. (AM+Lev)"))), /*#__PURE__*/React.createElement("tbody", null, (ct.d.rows || []).map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.code
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, r.code)), r.ok ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.date), /*#__PURE__*/React.createElement("td", null, g(r.asset_mgr)), /*#__PURE__*/React.createElement("td", null, g(r.lev_funds)), /*#__PURE__*/React.createElement("td", null, g(r.dealer)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.noncommercial ? `${r.noncommercial.net >= 0 ? "+" : ""}${r.noncommercial.net.toLocaleString()} (${r.noncommercial.pctile != null ? r.noncommercial.pctile.toFixed(0) + "%ile" : "—"})` : "—")) : /*#__PURE__*/React.createElement("td", {
    colSpan: "5"
  }, /*#__PURE__*/React.createElement(TsyNA, null))))))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-sigd"
  }, ct.d.note), /*#__PURE__*/React.createElement(TsyFoot, {
    src: ct.d.source
  }));
}

/* ── 16. Rate sensitivity of the watchlist ─────────────────────────────── */
const TSY_FACTORS = [["y10", "10y yield"], ["y2", "2y yield"], ["y30", "30y yield"], ["curve", "2s10s steepening"], ["real10", "10y real yield"]];
function TsySense({
  apiFetch,
  onOpenTicker
}) {
  const [board, setBoard] = useState(null);
  const [factor, setFactor] = useState("y10");
  const [dir, setDir] = useState("neg");
  const pollRef = useRef(null);
  const load = async () => {
    try {
      const r = await apiFetch("/api/treasury/sense");
      const d = await r.json();
      setBoard(d);
      return d;
    } catch (e) {
      return null;
    }
  };
  useEffect(() => {
    load();
    return () => pollRef.current && clearInterval(pollRef.current);
  }, []);
  const scan = async () => {
    try {
      await apiFetch("/api/treasury/sense/scan?force=1");
    } catch (e) {
      return;
    }
    await load();
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 5000);
  };
  const st = board && board.status || {};
  const rows = (board && board.rows || []).map(r => ({
    ticker: r.ticker,
    f: r[factor]
  })).filter(r => r.f && r.f.ok).sort((a, b) => dir === "neg" ? a.f.beta10bp - b.f.beta10bp : b.f.beta10bp - a.f.beta10bp).slice(0, 25);
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "tsy-ctrl",
    style: {
      marginBottom: 8
    }
  }, /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: factor,
    onChange: e => setFactor(e.target.value)
  }, TSY_FACTORS.map(([k, l]) => /*#__PURE__*/React.createElement("option", {
    key: k,
    value: k
  }, l))), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: dir,
    onChange: e => setDir(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "neg"
  }, "Most hurt by rising factor"), /*#__PURE__*/React.createElement("option", {
    value: "pos"
  }, "Most helped by rising factor")), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "scan-run-btn",
    onClick: scan,
    disabled: !!st.scanning
  }, st.scanning ? `Scanning… ${st.scanned || 0}/${st.total || 0}` : rows.length ? "Rescan watchlist" : "Scan watchlist"), st.last_scan && /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 11.5
    }
  }, "last scan ", new Date(st.last_scan).toLocaleString())), rows.length > 0 ? /*#__PURE__*/React.createElement("div", {
    className: "tsy-tablewrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tsy-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Ticker"), /*#__PURE__*/React.createElement("th", null, "\u03B2 per +10bp"), /*#__PURE__*/React.createElement("th", null, "Corr"), /*#__PURE__*/React.createElement("th", null, "n"), /*#__PURE__*/React.createElement("th", null, "Confidence"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.ticker,
    className: "tsy-rowlink",
    onClick: () => onOpenTicker && onOpenTicker(r.ticker),
    title: `${r.ticker}: moves ${r.f.beta10bp >= 0 ? "+" : ""}${r.f.beta10bp}% on average when the ${(TSY_FACTORS.find(f => f[0] === factor) || [])[1]} rises 10bp (last ${r.f.n} sessions, t=${r.f.t}). Click to open in Analyze.`
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, r.ticker)), /*#__PURE__*/React.createElement("td", {
    className: `num ${r.f.beta10bp >= 0 ? "cu" : "cd"}`
  }, /*#__PURE__*/React.createElement("b", null, r.f.beta10bp >= 0 ? "+" : "", r.f.beta10bp, "%")), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.f.corr), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.f.n), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
    className: `tsy-pill ${r.f.conf === "high" ? "up" : "mut"}`
  }, r.f.conf.toUpperCase()))))))) : /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "6px 0"
    },
    className: "muted"
  }, st.scanning ? "Scanning your watchlist…" : "No scan yet — click Scan watchlist. Names without a statistically meaningful relationship (|t| < 2) are excluded rather than shown with a fake conclusion."), board && /*#__PURE__*/React.createElement("div", {
    className: "tsy-sigd"
  }, board.note), /*#__PURE__*/React.createElement(TsyFoot, {
    src: "FRED daily yield changes \xD7 your watchlist's daily returns (Yahoo, delayed)"
  }));
}

/* ── 17. Alerts (client-side rules on the displayed data) ──────────────── */
const TSY_ALERT_DEFS = [["y2_abs1d", "2y daily move ≥ (bp)", 8], ["y10_above", "10y yield crosses above (%)", 4.75], ["y10_below", "10y yield crosses below (%)", 4.25], ["y30_above", "30y yield crosses above (%)", 5.25], ["s2s10_uninvert", "2s10s uninverts (no value needed)", 0], ["s2s10_chg21", "2s10s 1-month change ≥ (bp, abs)", 15], ["move_above", "MOVE crosses above", 130]];
function TsyAlertsCard({
  core
}) {
  const KEY = "tsy_alerts_v1";
  const [rules, setRules] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(KEY)) || [];
    } catch (e) {
      return [];
    }
  });
  const [sel, setSel] = useState(TSY_ALERT_DEFS[0][0]);
  const [val, setVal] = useState(String(TSY_ALERT_DEFS[0][2]));
  const [fired, setFired] = useState([]);
  const save = rs => {
    setRules(rs);
    try {
      localStorage.setItem(KEY, JSON.stringify(rs));
    } catch (e) {}
  };
  const d = core.d;
  useEffect(() => {
    if (!d || !d.ok || !rules.length) return;
    const y = {};
    (d.yields || []).forEach(c => {
      y[c.tenor] = c;
    });
    const s210 = (d.spreads || []).find(s => s.key === "2s10s");
    const mv = d.move;
    const hits = [];
    for (const r of rules) {
      let hit = false,
        why = "";
      if (r.k === "y2_abs1d" && y["2Y"] && y["2Y"].bp1d != null && Math.abs(y["2Y"].bp1d) >= r.v) {
        hit = true;
        why = `2y moved ${y["2Y"].bp1d} bp today`;
      }
      if (r.k === "y10_above" && y["10Y"] && y["10Y"].yield >= r.v) {
        hit = true;
        why = `10y at ${y["10Y"].yield.toFixed(2)}% ≥ ${r.v}%`;
      }
      if (r.k === "y10_below" && y["10Y"] && y["10Y"].yield <= r.v) {
        hit = true;
        why = `10y at ${y["10Y"].yield.toFixed(2)}% ≤ ${r.v}%`;
      }
      if (r.k === "y30_above" && y["30Y"] && y["30Y"].yield >= r.v) {
        hit = true;
        why = `30y at ${y["30Y"].yield.toFixed(2)}% ≥ ${r.v}%`;
      }
      if (r.k === "s2s10_uninvert" && s210 && !s210.inverted && s210.d21 != null && s210.bp - s210.d21 < 0) {
        hit = true;
        why = `2s10s now ${s210.bp >= 0 ? "+" : ""}${s210.bp} bp (was inverted a month ago)`;
      }
      if (r.k === "s2s10_chg21" && s210 && s210.d21 != null && Math.abs(s210.d21) >= r.v) {
        hit = true;
        why = `2s10s ${s210.d21 >= 0 ? "steepened" : "flattened"} ${Math.abs(s210.d21)} bp over 1 month`;
      }
      if (r.k === "move_above" && mv && mv.value >= r.v) {
        hit = true;
        why = `MOVE at ${mv.value} ≥ ${r.v}`;
      }
      if (hit) hits.push({
        id: r.id,
        label: (TSY_ALERT_DEFS.find(x => x[0] === r.k) || [])[1],
        why
      });
    }
    setFired(hits);
  }, [d, rules]);
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "tsy-ctrl",
    style: {
      marginBottom: 8,
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: sel,
    onChange: e => {
      setSel(e.target.value);
      const def = TSY_ALERT_DEFS.find(x => x[0] === e.target.value);
      if (def) setVal(String(def[2]));
    }
  }, TSY_ALERT_DEFS.map(([k, l]) => /*#__PURE__*/React.createElement("option", {
    key: k,
    value: k
  }, l))), /*#__PURE__*/React.createElement("input", {
    className: "sb-select",
    style: {
      width: 90
    },
    value: val,
    onChange: e => setVal(e.target.value),
    inputMode: "decimal"
  }), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "scan-run-btn",
    onClick: () => {
      const v = parseFloat(val);
      if (sel !== "s2s10_uninvert" && !(v === v)) return;
      save([...rules, {
        id: Date.now(),
        k: sel,
        v: v || 0
      }]);
    }
  }, "Add alert")), rules.length === 0 && /*#__PURE__*/React.createElement("div", {
    className: "muted",
    style: {
      fontSize: 12.5
    }
  }, "No alerts yet. Rules are checked against the official EOD curve each time this tab refreshes (and shown below when triggered). CPI-surprise, auction-strength and Fed-probability alerts need consensus/CME feeds that have no free source \u2014 those trigger types are intentionally absent rather than faked."), rules.map(r => {
    const def = TSY_ALERT_DEFS.find(x => x[0] === r.k) || [r.k, r.k];
    const hit = fired.find(f => f.id === r.id);
    return /*#__PURE__*/React.createElement("div", {
      key: r.id,
      className: `tsy-alertrow ${hit ? "hit" : ""}`
    }, /*#__PURE__*/React.createElement("span", null, def[1], r.k !== "s2s10_uninvert" ? /*#__PURE__*/React.createElement("b", {
      className: "num"
    }, " ", r.v) : null), hit ? /*#__PURE__*/React.createElement("b", {
      className: "tsy-pill down",
      title: hit.why
    }, "TRIGGERED \xB7 ", hit.why) : /*#__PURE__*/React.createElement("span", {
      className: "muted"
    }, "armed"), /*#__PURE__*/React.createElement("button", {
      type: "button",
      className: "tsy-x",
      onClick: () => save(rules.filter(x => x.id !== r.id)),
      "aria-label": "Remove"
    }, "\u2715"));
  }));
}

/* ── Overview grid (v3.62) — the glance terminal, mockup density ──────────
   Every mini carries the numbers on-screen (percentiles, changes, status),
   not just in tooltips. Detail sections remain below. */
function TsyMini({
  kicker,
  title,
  children,
  right
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-mini"
  }, /*#__PURE__*/React.createElement("div", {
    className: "tsy-mini-h"
  }, /*#__PURE__*/React.createElement("em", null, kicker), right), title && /*#__PURE__*/React.createElement("div", {
    className: "tsy-mini-t"
  }, title), children);
}
function TsySpark({
  pts,
  w = 150,
  h = 34,
  tone
}) {
  if (!pts || pts.length < 5) return null;
  const lo = Math.min(...pts),
    hi = Math.max(...pts);
  const x = i => i / (pts.length - 1) * w;
  const y = v => 3 + (1 - (v - lo) / Math.max(1e-9, hi - lo)) * (h - 6);
  const up = pts[pts.length - 1] >= pts[0];
  const col = tone || (up ? "var(--down)" : "var(--up)"); // yields rising = red
  return /*#__PURE__*/React.createElement("svg", {
    className: "tsy-spark",
    viewBox: `0 0 ${w} ${h}`,
    preserveAspectRatio: "none",
    "aria-hidden": "true"
  }, /*#__PURE__*/React.createElement("path", {
    d: pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(""),
    fill: "none",
    style: {
      stroke: col
    },
    strokeWidth: "1.6"
  }));
}
function TsyOvYields({
  core
}) {
  const cards = core.d && core.d.yields || [];
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "Key Treasury yields \xB7 official EOD curve",
    right: /*#__PURE__*/React.createElement("span", {
      className: "tsy-datechip num"
    }, core.d && core.d.curve_date)
  }, /*#__PURE__*/React.createElement("div", {
    className: "tsy-ystrip"
  }, cards.map(c => /*#__PURE__*/React.createElement("div", {
    key: c.tenor,
    className: `tsy-ycell ${TSY_KEY4[c.tenor] ? "key" : ""}`,
    title: `${c.tenor}: ${c.yield.toFixed(2)}% · 1d ${c.bp1d != null ? c.bp1d + " bp" : "—"} · 5d ${c.bp5d != null ? c.bp5d + " bp" : "—"} · 1m ${c.bp21d != null ? c.bp21d + " bp" : "—"} · YTD ${c.bp_ytd != null ? c.bp_ytd + " bp" : "—"}\n52w range ${c.lo52w.toFixed(2)}–${c.hi52w.toFixed(2)}%${c.key ? "\n" + c.key : ""}\n${TSY_INV}`
  }, /*#__PURE__*/React.createElement("em", null, c.tenor), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, c.yield.toFixed(2), "%"), /*#__PURE__*/React.createElement("span", {
    className: `num ${tsyBpCls(c.bp1d)}`
  }, c.bp1d != null ? `${c.bp1d >= 0 ? "+" : ""}${c.bp1d.toFixed(0)}${c.bp1d > 0 ? " ▲" : c.bp1d < 0 ? " ▼" : ""}` : "—"), /*#__PURE__*/React.createElement("i", {
    className: "num"
  }, c.pct52w != null ? `${c.pct52w.toFixed(0)}% 52w` : "")))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-mini-note",
    style: {
      marginTop: 6
    },
    title: TSY_INV
  }, "Yields move opposite of bond prices \u24D8 \xB7 green = yield falling"));
}
function TsyOv10Y({
  core
}) {
  const c = (core.d && core.d.yields || []).find(x => x.tenor === "10Y");
  if (!c) return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "10Y Treasury"
  }, /*#__PURE__*/React.createElement(TsyNA, null));
  const span = c.hi52w - c.lo52w;
  const pos = span > 0 ? Math.max(0, Math.min(100, (c.yield - c.lo52w) / span * 100)) : 50;
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "10Y Treasury \xB7 equity valuation benchmark"
  }, /*#__PURE__*/React.createElement("div", {
    className: "tsy-spot num"
  }, /*#__PURE__*/React.createElement("b", null, c.yield.toFixed(2), "%"), /*#__PURE__*/React.createElement(TsyBp, {
    v: c.bp1d,
    d: 0
  })), c.spark && /*#__PURE__*/React.createElement("div", {
    title: "Last ~90 trading days of the official EOD 10y yield. Red = yields rose over the window."
  }, /*#__PURE__*/React.createElement(TsySpark, {
    pts: c.spark,
    w: 220,
    h: 36
  })), /*#__PURE__*/React.createElement("div", {
    className: "tsy-52bar"
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      left: `${pos}%`
    }
  })), /*#__PURE__*/React.createElement("div", {
    className: "tsy-52lbl num"
  }, /*#__PURE__*/React.createElement("span", null, c.lo52w.toFixed(2)), /*#__PURE__*/React.createElement("span", null, "52W range"), /*#__PURE__*/React.createElement("span", null, c.hi52w.toFixed(2))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-spotrow num"
  }, /*#__PURE__*/React.createElement("span", null, "5d ", /*#__PURE__*/React.createElement(TsyBp, {
    v: c.bp5d,
    d: 0
  })), /*#__PURE__*/React.createElement("span", null, "1m ", /*#__PURE__*/React.createElement(TsyBp, {
    v: c.bp21d,
    d: 0
  })), /*#__PURE__*/React.createElement("span", null, "YTD ", /*#__PURE__*/React.createElement(TsyBp, {
    v: c.bp_ytd,
    d: 0
  }))));
}
function TsyOvFutures({
  mk
}) {
  const futs = (mk.d && mk.d.futures || []).filter(f => f.ok);
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "Treasury futures \xB7 delayed",
    right: /*#__PURE__*/React.createElement("span", {
      className: "tsy-mini-note"
    }, "price \u2191 = yields \u2193")
  }, futs.length ? /*#__PURE__*/React.createElement("table", {
    className: "tsy-matrix num"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    style: {
      textAlign: "left"
    }
  }), /*#__PURE__*/React.createElement("th", null, "Last"), /*#__PURE__*/React.createElement("th", null, "Chg"), /*#__PURE__*/React.createElement("th", null, "%"))), /*#__PURE__*/React.createElement("tbody", null, futs.map(f => /*#__PURE__*/React.createElement("tr", {
    key: f.code,
    title: `${f.label} front-month continuous, ${f.date}. Range ${f.day_lo}–${f.day_hi}, volume ${f.volume != null ? f.volume.toLocaleString() : "—"}. PRICE — moves opposite to yields.`
  }, /*#__PURE__*/React.createElement("td", {
    className: "tsy-mxt"
  }, f.code, " ", /*#__PURE__*/React.createElement("i", {
    className: "muted"
  }, f.code === "ZT" ? "2Y" : f.code === "ZF" ? "5Y" : f.code === "ZN" ? "10Y" : f.code === "ZB" ? "30Y" : "Ultra")), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, f.last)), /*#__PURE__*/React.createElement("td", {
    className: f.chg_abs != null ? f.chg_abs >= 0 ? "cu" : "cd" : ""
  }, f.chg_abs != null ? `${f.chg_abs >= 0 ? "+" : ""}${f.chg_abs.toFixed(3)}` : "—"), /*#__PURE__*/React.createElement("td", {
    className: f.chg_pct != null ? f.chg_pct >= 0 ? "cu" : "cd" : ""
  }, f.chg_pct != null ? `${f.chg_pct >= 0 ? "+" : ""}${f.chg_pct}%` : "—"))))) : /*#__PURE__*/React.createElement(TsyNA, {
    why: "Futures quote source unreachable \u2014 not estimated."
  }));
}
function TsyOvAnalysis({
  core
}) {
  const d = core.d || {};
  const shape = d.curve_shape,
    reg = d.regime,
    mv = d.curve_moves;
  const sp = {};
  (d.spreads || []).forEach(s => {
    sp[s.key] = s;
  });
  const inv = shape && (shape.label.startsWith("inverted") || shape.label.startsWith("partially"));
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "Yield curve analysis"
  }, shape && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: shape.detail
  }, /*#__PURE__*/React.createElement("em", null, "Curve shape"), /*#__PURE__*/React.createElement("b", {
    className: inv ? "cd" : "cu"
  }, shape.label)), ["2s10s", "3m10y", "5s30s", "10yff"].map(k => sp[k] ? /*#__PURE__*/React.createElement("div", {
    key: k,
    className: "tsy-kv",
    title: sp[k].label
  }, /*#__PURE__*/React.createElement("em", null, sp[k].label.split(" (")[0]), /*#__PURE__*/React.createElement("b", {
    className: `num ${sp[k].inverted ? "cd" : "cu"}`
  }, sp[k].bp >= 0 ? "+" : "", sp[k].bp.toFixed(0), " bp"), /*#__PURE__*/React.createElement("span", {
    className: "num"
  }, sp[k].trend === "steepening" ? "↗" : sp[k].trend === "flattening" ? "↘" : "")) : null), reg && /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: `Slope change (10y − 2y) over ${reg.window}.`
  }, /*#__PURE__*/React.createElement("em", null, "Steepness (5d)"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, reg.slope_chg_bp >= 0 ? "+" : "", reg.slope_chg_bp, " bp"), /*#__PURE__*/React.createElement("span", null, reg.slope_chg_bp > 1 ? "steepening" : reg.slope_chg_bp < -1 ? "flattening" : "flat")), mv && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: "Average 5-day bp change of 1m\u20132y tenors."
  }, /*#__PURE__*/React.createElement("em", null, "Front end (5d)"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, /*#__PURE__*/React.createElement(TsyBp, {
    v: mv.front_avg_bp5d
  }))), mv && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: "Average 5-day bp change of 10y\u201330y tenors."
  }, /*#__PURE__*/React.createElement("em", null, "Long end (5d)"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, /*#__PURE__*/React.createElement(TsyBp, {
    v: mv.long_avg_bp5d
  }))), mv && mv.biggest && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv"
  }, /*#__PURE__*/React.createElement("em", null, "Largest move"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, mv.biggest.tenor, " ", /*#__PURE__*/React.createElement(TsyBp, {
    v: mv.biggest.bp5d
  }))), /*#__PURE__*/React.createElement("div", {
    className: `tsy-sigbox ${reg.label.startsWith("bull") ? "up" : reg.label.startsWith("bear") ? "down" : ""}`,
    title: `2y ${reg.d2y_bp >= 0 ? "+" : ""}${reg.d2y_bp} bp, 10y ${reg.d10y_bp >= 0 ? "+" : ""}${reg.d10y_bp} bp over ${reg.window}. Bull = yields falling.`
  }, /*#__PURE__*/React.createElement("em", null, "REGIME \xB7 ", reg.window), /*#__PURE__*/React.createElement("b", null, reg.label.toUpperCase()), /*#__PURE__*/React.createElement("span", null, "2y ", reg.d2y_bp >= 0 ? "+" : "", reg.d2y_bp, " \xB7 10y ", reg.d10y_bp >= 0 ? "+" : "", reg.d10y_bp, " bp"))));
}
function TsyOvAuctions({
  au
}) {
  const d = au.d || {};
  const rows = [...(d.recent_coupons || []), ...(d.recent_bills || [])].sort((a, b) => (b.date || "").localeCompare(a.date || "")).slice(0, 8);
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "Recent auctions \xB7 TreasuryDirect"
  }, au.loading ? /*#__PURE__*/React.createElement(TsyLoading, null) : rows.length ? rows.map((a, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "tsy-kv",
    title: a.vs_prior ? `$${a.offering ? (a.offering / 1e9).toFixed(0) : "—"}B. Bid-to-cover ${a.btc} vs ${a.vs_prior.btc_avg10} avg of prior ${a.vs_prior.n}; indirect ${a.indirect_pct}% vs ${a.vs_prior.indirect_avg10}%.` : `${a.term} ${a.type} auctioned ${a.date}${a.offering ? `, $${(a.offering / 1e9).toFixed(0)}B` : ""}.`
  }, /*#__PURE__*/React.createElement("em", null, a.date && a.date.slice(5), " ", /*#__PURE__*/React.createElement("i", null, a.term)), /*#__PURE__*/React.createElement("span", {
    className: "num muted"
  }, a.offering ? `$${(a.offering / 1e9).toFixed(0)}B` : ""), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, a.high_yield != null ? a.high_yield.toFixed(3) + "%" : "—"), /*#__PURE__*/React.createElement("span", {
    className: "num"
  }, a.btc != null ? a.btc.toFixed(2) + "×" : ""), a.strength && /*#__PURE__*/React.createElement("span", {
    className: `tsy-pill ${a.strength === "strong" ? "up" : a.strength === "weak" ? "down" : "mut"}`
  }, a.strength.slice(0, 4).toUpperCase()))) : /*#__PURE__*/React.createElement(TsyNA, {
    why: "TreasuryDirect unreachable."
  }));
}
function TsyOvSpreads({
  core
}) {
  const sp = (core.d && core.d.spreads || []).filter(s => s.key !== "10yff");
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "Important Treasury spreads"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tsy-matrix num"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    style: {
      textAlign: "left"
    }
  }, "Spread"), /*#__PURE__*/React.createElement("th", null, "bps"), /*#__PURE__*/React.createElement("th", null, "1D"), /*#__PURE__*/React.createElement("th", null, "1W"), /*#__PURE__*/React.createElement("th", null, "%ile"), /*#__PURE__*/React.createElement("th", null, "Status"))), /*#__PURE__*/React.createElement("tbody", null, sp.map(s => /*#__PURE__*/React.createElement("tr", {
    key: s.key,
    title: `${s.label} · 1m ${s.d21 != null ? s.d21 + " bp" : "—"} · percentile over ~3y of daily history · ${s.trend || ""}`
  }, /*#__PURE__*/React.createElement("td", {
    className: "tsy-mxt"
  }, s.key), /*#__PURE__*/React.createElement("td", {
    className: s.inverted ? "cd" : "cu"
  }, /*#__PURE__*/React.createElement("b", null, s.bp >= 0 ? "+" : "", s.bp.toFixed(0))), /*#__PURE__*/React.createElement("td", {
    className: tsyBpCls(s.d1)
  }, s.d1 != null ? `${s.d1 >= 0 ? "+" : ""}${s.d1.toFixed(0)}` : "—"), /*#__PURE__*/React.createElement("td", {
    className: tsyBpCls(s.d5)
  }, s.d5 != null ? `${s.d5 >= 0 ? "+" : ""}${s.d5.toFixed(0)}` : "—"), /*#__PURE__*/React.createElement("td", null, s.pctile != null ? s.pctile.toFixed(0) : "—"), /*#__PURE__*/React.createElement("td", null, s.inverted ? /*#__PURE__*/React.createElement("span", {
    className: "tsy-pill down"
  }, "INV") : /*#__PURE__*/React.createElement("span", {
    className: "tsy-pill up"
  }, "POS")))))));
}
function TsyOvMatrix({
  core
}) {
  const cards = core.d && core.d.yields || [];
  const rows = ["2Y", "5Y", "10Y", "30Y"].map(t => cards.find(c => c.tenor === t)).filter(Boolean);
  const cols = [["bp1d", "1D"], ["bp5d", "5D"], ["bp21d", "1M"], ["bp63d", "3M"], ["bp_ytd", "YTD"]];
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "Rate change (bp)",
    right: /*#__PURE__*/React.createElement("span", {
      className: "tsy-mini-note",
      title: TSY_INV
    }, "red = yields up")
  }, /*#__PURE__*/React.createElement("table", {
    className: "tsy-matrix num"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null), cols.map(([k, l]) => /*#__PURE__*/React.createElement("th", {
    key: k
  }, l)))), /*#__PURE__*/React.createElement("tbody", null, rows.map(c => /*#__PURE__*/React.createElement("tr", {
    key: c.tenor
  }, /*#__PURE__*/React.createElement("td", {
    className: "tsy-mxt"
  }, c.tenor), cols.map(([k]) => /*#__PURE__*/React.createElement("td", {
    key: k,
    className: tsyBpCls(c[k])
  }, c[k] != null ? `${c[k] >= 0 ? "+" : ""}${c[k].toFixed(0)}` : "—")))))));
}
function TsyOvMove({
  core
}) {
  const m = core.d && core.d.move;
  const BANDS = [["low", "<80"], ["normal", "80–100"], ["elevated", "100–130"], ["high", "130–160"], ["extreme", ">180"]];
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "Treasury volatility \xB7 MOVE",
    right: m && /*#__PURE__*/React.createElement("span", {
      className: `tsy-pill ${m.regime === "low" || m.regime === "normal" ? "up" : m.regime === "elevated" ? "mut" : "down"}`
    }, m.regime.toUpperCase())
  }, m ? /*#__PURE__*/React.createElement("div", {
    title: `MOVE = Treasury option implied vol (NOT the equity VIX). 52w percentile ${m.pct52w != null ? m.pct52w.toFixed(0) : "—"}.`
  }, /*#__PURE__*/React.createElement("div", {
    className: "tsy-spot num"
  }, /*#__PURE__*/React.createElement("b", null, m.value), /*#__PURE__*/React.createElement(TsyBp, {
    v: m.d1
  })), m.spark && /*#__PURE__*/React.createElement(TsySpark, {
    pts: m.spark,
    w: 200,
    h: 30,
    tone: "var(--warn)"
  }), /*#__PURE__*/React.createElement("div", {
    className: "tsy-spotrow num"
  }, /*#__PURE__*/React.createElement("span", null, "5d ", /*#__PURE__*/React.createElement(TsyBp, {
    v: m.d5
  })), /*#__PURE__*/React.createElement("span", null, "1m ", /*#__PURE__*/React.createElement(TsyBp, {
    v: m.d21
  })), /*#__PURE__*/React.createElement("span", null, "52w %ile ", /*#__PURE__*/React.createElement("b", null, m.pct52w != null ? m.pct52w.toFixed(0) : "—"))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-bands"
  }, BANDS.map(([k, range]) => /*#__PURE__*/React.createElement("span", {
    key: k,
    className: m.regime === k ? "on" : "",
    title: `${k}: ${range}`
  }, k))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-mini-note"
  }, "MOVE index \xB7 not the VIX")) : /*#__PURE__*/React.createElement(TsyNA, {
    why: "^MOVE quote unreachable \u2014 not estimated."
  }));
}
function TsyOvCorr({
  mk
}) {
  const c = mk.d && mk.d.correlations;
  const rows = (c && c.rows || []).filter(r => ["SPY", "QQQ", "IWM", "GLD", "UUP", "CL=F"].includes(r.sym));
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "Correlation vs \u039410y \xB7 60d"
  }, c && c.ok && rows.length ? rows.map(r => /*#__PURE__*/React.createElement("div", {
    key: r.sym,
    className: "tsy-kv",
    title: `${r.label}: 60-day correlation of daily returns vs the daily CHANGE in the 10y yield. Also 20d ${r.w20 != null ? r.w20 : "—"}, 120d ${r.w120 != null ? r.w120 : "—"}. Correlation ≠ causation.`
  }, /*#__PURE__*/React.createElement("em", null, r.sym === "CL=F" ? "OIL" : r.sym === "UUP" ? "USD" : r.sym === "GLD" ? "GOLD" : r.sym), /*#__PURE__*/React.createElement("b", {
    className: `num ${r.w60 != null ? r.w60 >= 0 ? "cu" : "cd" : ""}`
  }, r.w60 != null ? (r.w60 >= 0 ? "+" : "") + r.w60.toFixed(2) : "—"))) : /*#__PURE__*/React.createElement(TsyNA, {
    why: "Correlation inputs unreachable."
  }));
}
function TsyOvCpiSummary({
  inf
}) {
  const rows = inf.d && inf.d.ok && inf.d.rows || [];
  const head = rows.find(r => r.key === "headline"),
    cc = rows.find(r => r.key === "core");
  if (!head || !head.ok) return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "CPI summary"
  }, /*#__PURE__*/React.createElement(TsyNA, {
    why: "CPI data unreachable."
  }));
  const line = (label, cur, prev, pct, tip) => /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: tip || `${label}: latest ${cur != null ? cur + "%" : "—"}, previous ${prev != null ? prev + "%" : "—"}. Consensus: no free feed — not estimated.`
  }, /*#__PURE__*/React.createElement("em", null, label), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, cur != null ? `${cur >= 0 ? "+" : ""}${cur.toFixed(2)}%` : "—"), /*#__PURE__*/React.createElement("span", {
    className: "num muted"
  }, "prev ", prev != null ? `${prev >= 0 ? "+" : ""}${prev.toFixed(2)}` : "—"), /*#__PURE__*/React.createElement("span", {
    className: `num ${cur != null && prev != null ? cur < prev ? "cu" : cur > prev ? "cd" : "" : ""}`
  }, cur != null && prev != null ? cur < prev ? "▼" : cur > prev ? "▲" : "→" : ""), pct != null && /*#__PURE__*/React.createElement("span", {
    className: "num muted"
  }, pct.toFixed(0), "%ile"));
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: `CPI summary · ${head.month}`,
    right: /*#__PURE__*/React.createElement("span", {
      className: "tsy-mini-note"
    }, "est: no free consensus feed")
  }, line("Headline YoY", head.yoy, head.yoy_prev, head.yoy_pctile_10y), line("Core YoY", cc && cc.yoy, cc && cc.yoy_prev, cc && cc.yoy_pctile_10y), line("Headline MoM", head.mom, head.mom_prev, null), line("Core MoM", cc && cc.mom, cc && cc.mom_prev, null), cc && cc.ann3m != null && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: "Compounded 3-month core CPI, annualized \u2014 the near-term run rate."
  }, /*#__PURE__*/React.createElement("em", null, "Core 3m annualized"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, cc.ann3m.toFixed(2), "%")), cc && cc.ann6m != null && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: "Compounded 6-month core CPI, annualized."
  }, /*#__PURE__*/React.createElement("em", null, "Core 6m annualized"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, cc.ann6m.toFixed(2), "%")));
}
function TsyOvCpiTrend({
  inf
}) {
  const ch = inf.d && inf.d.charts || {};
  const series = [["headline_yoy", "Headline", "#4E9CF5"], ["core_yoy", "Core", "#E8A33D"], ["core_3m_ann", "3m ann.", "#3BD996"]].filter(([k]) => ch[k]).map(([k, label, color]) => ({
    key: k,
    label,
    color,
    pts: ch[k].slice(-60)
  }));
  if (!series.length) return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "CPI trend"
  }, /*#__PURE__*/React.createElement(TsyNA, {
    why: "CPI series unreachable."
  }));
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "CPI trend \xB7 5y",
    right: /*#__PURE__*/React.createElement("span", {
      className: "tsy-legend"
    }, series.map(s => /*#__PURE__*/React.createElement("i", {
      key: s.key,
      style: {
        color: s.color
      }
    }, "\u2014 ", s.label)))
  }, /*#__PURE__*/React.createElement(TsySeriesSvg, {
    series: series.map(s => ({
      ...s,
      pts: s.pts
    })),
    period: "max"
  }));
}
function TsyOvExpectations({
  core
}) {
  const e = core.d && core.d.expectations || {};
  const dec = core.d && core.d.decomposition;
  const rows = [["be5", "5y breakeven"], ["be10", "10y breakeven"], ["f5y5y", "5y5y fwd"], ["real5", "5y TIPS real"], ["real10", "10y TIPS real"], ["real30", "30y TIPS real"]];
  const driver = dec ? dec.verdict.includes("both") ? "both" : dec.verdict.includes("real") ? "real" : dec.verdict.includes("expectations") ? "infl" : "unclear" : null;
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "Inflation expectations \xB7 FRED daily"
  }, /*#__PURE__*/React.createElement("table", {
    className: "tsy-matrix num"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    style: {
      textAlign: "left"
    }
  }), /*#__PURE__*/React.createElement("th", null, "Value"), /*#__PURE__*/React.createElement("th", null, "1D"), /*#__PURE__*/React.createElement("th", null, "1W"), /*#__PURE__*/React.createElement("th", null, "1M"), /*#__PURE__*/React.createElement("th", null, "%ile"))), /*#__PURE__*/React.createElement("tbody", null, rows.map(([k, label]) => {
    const s = e[k];
    if (!s) return null;
    const bp = v => v != null ? v * 100 : null;
    const cell = v => /*#__PURE__*/React.createElement("td", {
      className: tsyBpCls(bp(v))
    }, v != null ? `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)}` : "—");
    return /*#__PURE__*/React.createElement("tr", {
      key: k,
      title: `${s.label} — 52w range ${s.lo52w.toFixed(2)}–${s.hi52w.toFixed(2)}%.`
    }, /*#__PURE__*/React.createElement("td", {
      className: "tsy-mxt"
    }, label), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, s.value.toFixed(2), "%")), cell(s.d1), cell(s.d5), cell(s.d21), /*#__PURE__*/React.createElement("td", null, s.pct52w != null ? s.pct52w.toFixed(0) : "—"));
  }))), dec && /*#__PURE__*/React.createElement("div", {
    className: "tsy-drivers",
    title: `Δ10y nominal ${dec.nominal_bp} bp = real ${dec.real_bp} + breakeven ${dec.breakeven_bp} bp over ${dec.window} (FRED identity, common dates).`
  }, /*#__PURE__*/React.createElement("em", null, "10y driver (", dec.window, "):"), [["real", "Real yields"], ["infl", "Inflation exp."], ["both", "Both"], ["unclear", "Unclear"]].map(([k, l]) => /*#__PURE__*/React.createElement("span", {
    key: k,
    className: driver === k ? "on" : ""
  }, l))));
}
function TsyOvCot({
  ct
}) {
  const rows = (ct.d && ct.d.rows || []).filter(r => r.ok);
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "COT positioning \xB7 CFTC weekly",
    right: rows[0] && /*#__PURE__*/React.createElement("span", {
      className: "tsy-datechip num"
    }, rows[0].date)
  }, ct.loading ? /*#__PURE__*/React.createElement(TsyLoading, null) : rows.length ? /*#__PURE__*/React.createElement("table", {
    className: "tsy-matrix num"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null), /*#__PURE__*/React.createElement("th", null, "Asset mgr"), /*#__PURE__*/React.createElement("th", null, "wk \u0394"), /*#__PURE__*/React.createElement("th", null, "Lev funds"), /*#__PURE__*/React.createElement("th", null, "wk \u0394"), /*#__PURE__*/React.createElement("th", null))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => {
    const net = k => r[k] ? `${r[k].net >= 0 ? "+" : ""}${(r[k].net / 1000).toFixed(0)}k` : "—";
    const wk = k => r[k] && r[k].wk_chg != null ? `${r[k].wk_chg >= 0 ? "+" : ""}${(r[k].wk_chg / 1000).toFixed(0)}k` : "—";
    const crowded = ["asset_mgr", "lev_funds"].map(k => r[k] && r[k].crowded ? `${k === "asset_mgr" ? "AM" : "LEV"} ${r[k].crowded}` : null).filter(Boolean)[0];
    return /*#__PURE__*/React.createElement("tr", {
      key: r.code,
      title: `${r.code}: asset managers ${r.asset_mgr ? r.asset_mgr.net.toLocaleString() : "—"}${r.asset_mgr && r.asset_mgr.pctile != null ? ` (${r.asset_mgr.pctile.toFixed(0)}th pctile 3y)` : ""} · leveraged ${r.lev_funds ? r.lev_funds.net.toLocaleString() : "—"} · dealers ${r.dealer ? r.dealer.net.toLocaleString() : "—"}.${r.fallback ? ` Source: ${r.fallback}.` : ""}`
    }, /*#__PURE__*/React.createElement("td", {
      className: "tsy-mxt"
    }, r.code), /*#__PURE__*/React.createElement("td", {
      className: r.asset_mgr && r.asset_mgr.net >= 0 ? "cu" : "cd"
    }, net("asset_mgr")), /*#__PURE__*/React.createElement("td", {
      className: "muted"
    }, wk("asset_mgr")), /*#__PURE__*/React.createElement("td", {
      className: r.lev_funds && r.lev_funds.net >= 0 ? "cu" : "cd"
    }, net("lev_funds")), /*#__PURE__*/React.createElement("td", {
      className: "muted"
    }, wk("lev_funds")), /*#__PURE__*/React.createElement("td", null, crowded && /*#__PURE__*/React.createElement("span", {
      className: `tsy-pill ${crowded.includes("long") ? "up" : "down"}`
    }, crowded.toUpperCase())));
  }))) : /*#__PURE__*/React.createElement(TsyErr, {
    err: ct.err || "CFTC unavailable",
    retry: ct.retry
  }));
}
function TsyOvEvents({
  core,
  inf
}) {
  const ev = core.d && core.d.events || {};
  const cpi = ev.next_cpi,
    fomc = ev.next_fomc,
    jobs = ev.next_jobs;
  const rows = inf.d && inf.d.ok && inf.d.rows || [];
  const head = rows.find(r => r.key === "headline"),
    cc = rows.find(r => r.key === "core");
  const avg = inf.d && inf.d.reactions && inf.d.reactions.avg_abs;
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "Next CPI release \xB7 event risk"
  }, cpi && cpi.date && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv big",
    title: `Next CPI per the BLS schedule, ${cpi.time_et}. Consensus estimates: no free reliable feed — not estimated.`
  }, /*#__PURE__*/React.createElement("em", null, "CPI"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, cpi.date), /*#__PURE__*/React.createElement("span", {
    className: "num warn"
  }, cpi.countdown ? `${cpi.countdown.days}d ${cpi.countdown.hours}h` : "")), cpi && !cpi.date && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv big",
    title: ev.schedule && ev.schedule.note || "Maintained schedule exhausted."
  }, /*#__PURE__*/React.createElement("em", null, "CPI"), /*#__PURE__*/React.createElement("b", null, "\u2014"), /*#__PURE__*/React.createElement("span", {
    className: "warn"
  }, "schedule update needed")), head && head.ok && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: "Previous print (the June data). Est column: no free consensus source \u2014 not estimated."
  }, /*#__PURE__*/React.createElement("em", null, "Prev headline / core YoY"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, head.yoy != null ? head.yoy.toFixed(2) : "—", "% / ", cc && cc.yoy != null ? cc.yoy.toFixed(2) : "—", "%"), /*#__PURE__*/React.createElement("span", {
    className: "num muted"
  }, "est \u2014")), avg && (avg.spy != null || avg.qqq != null) && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: `Average ABSOLUTE close-to-close move on the last ${avg.n} CPI release days (delayed Yahoo closes). A magnitude read, not a direction forecast.`
  }, /*#__PURE__*/React.createElement("em", null, "Avg CPI-day move"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, "SPY \xB1", avg.spy != null ? avg.spy.toFixed(2) : "—", "% \xB7 QQQ \xB1", avg.qqq != null ? avg.qqq.toFixed(2) : "—", "%")), avg && avg.y10_bp != null && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: "Average absolute 10y yield move on CPI days (FRED daily)."
  }, /*#__PURE__*/React.createElement("em", null, "Avg CPI-day 10y move"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, "\xB1", avg.y10_bp.toFixed(1), " bp")), fomc && fomc.date && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: fomc.source
  }, /*#__PURE__*/React.createElement("em", null, "FOMC"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, fomc.date), /*#__PURE__*/React.createElement("span", {
    className: "num"
  }, fomc.days, "d")), fomc && !fomc.date && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: ev.schedule && ev.schedule.note || "Maintained schedule exhausted."
  }, /*#__PURE__*/React.createElement("em", null, "FOMC"), /*#__PURE__*/React.createElement("b", null, "\u2014"), /*#__PURE__*/React.createElement("span", {
    className: "warn"
  }, "schedule update needed")), jobs && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: jobs.source
  }, /*#__PURE__*/React.createElement("em", null, "Jobs report"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, jobs.date)), (ev.upcoming_auctions || []).slice(0, 2).map((a, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "tsy-kv",
    title: `${a.term} ${a.type} auction${a.offering ? `, $${(a.offering / 1e9).toFixed(0)}B` : ""} (TreasuryDirect).`
  }, /*#__PURE__*/React.createElement("em", null, "Auction"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, a.auction_date), /*#__PURE__*/React.createElement("span", null, a.term, " ", a.type))));
}
function TsyOvFed({
  fd
}) {
  const d = fd.d || {};
  const t = d.target,
    nm = d.next_meeting;
  const path = (d.implied_path || []).slice(0, 4);
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "Fed policy \xB7 rate expectations",
    right: t && /*#__PURE__*/React.createElement("span", {
      className: "tsy-mini-note"
    }, "official range")
  }, fd.loading ? /*#__PURE__*/React.createElement(TsyLoading, null) : /*#__PURE__*/React.createElement("div", null, t && /*#__PURE__*/React.createElement("div", {
    className: "tsy-spot num",
    title: `Official target range as of ${t.date} (${t.source}).`
  }, /*#__PURE__*/React.createElement("b", null, t.lower.toFixed(2), "\u2013", t.upper.toFixed(2), "%")), nm && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv"
  }, /*#__PURE__*/React.createElement("em", null, "Next FOMC"), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, nm.date), /*#__PURE__*/React.createElement("span", {
    className: "num warn"
  }, nm.days, "d")), path.length > 0 ? path.map(p => /*#__PURE__*/React.createElement("div", {
    key: p.month,
    className: "tsy-kv",
    title: "Implied average fed funds = 100 \u2212 ZQ futures price (CME 30-day FF futures via Yahoo, delayed)."
  }, /*#__PURE__*/React.createElement("em", null, p.month), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, p.implied_rate.toFixed(2), "%"), /*#__PURE__*/React.createElement("span", {
    className: `num ${tsyBpCls(p.d1_bp)}`
  }, p.d1_bp != null ? `${p.d1_bp >= 0 ? "+" : ""}${p.d1_bp.toFixed(0)}` : ""))) : /*#__PURE__*/React.createElement("div", {
    className: "tsy-mini-note"
  }, "Implied path: ", /*#__PURE__*/React.createElement(TsyNA, {
    why: "Fed funds futures unreachable \u2014 not estimated. Per-meeting probabilities need CME FedWatch."
  })), d.yearend && /*#__PURE__*/React.createElement("div", {
    className: "tsy-kv",
    title: "Vs the current target midpoint."
  }, /*#__PURE__*/React.createElement("em", null, "Priced by ", d.yearend.month), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, d.yearend.implied_rate.toFixed(2), "%"), /*#__PURE__*/React.createElement("span", {
    className: "num"
  }, Math.abs(d.yearend.cuts_25bp).toFixed(1), "\xD725bp ", d.yearend.cuts_25bp >= 0 ? "cuts" : "hikes"))));
}
function TsyOvSense({
  apiFetch,
  onOpenTicker
}) {
  const [board, setBoard] = useState(null);
  useEffect(() => {
    sharedJson(apiFetch, "/api/treasury/sense", 300000).then(setBoard).catch(() => {});
  }, []);
  const rows = (board && board.rows || []).map(r => ({
    ticker: r.ticker,
    f: r.y10
  })).filter(r => r.f && r.f.ok).sort((a, b) => a.f.beta10bp - b.f.beta10bp).slice(0, 6);
  return /*#__PURE__*/React.createElement(TsyMini, {
    kicker: "Rate sensitivity \xB7 most hurt by rising 10y",
    right: /*#__PURE__*/React.createElement("span", {
      className: "tsy-mini-note"
    }, "from your watchlist scan")
  }, rows.length ? rows.map(r => /*#__PURE__*/React.createElement("div", {
    key: r.ticker,
    className: "tsy-kv tsy-rowlink",
    onClick: () => onOpenTicker && onOpenTicker(r.ticker),
    title: `${r.ticker}: ${r.f.beta10bp}% avg move per +10bp in the 10y (n=${r.f.n}, t=${r.f.t}, ${r.f.conf} confidence). Click to open in Analyze.`
  }, /*#__PURE__*/React.createElement("em", null, r.ticker), /*#__PURE__*/React.createElement("b", {
    className: `num ${r.f.beta10bp >= 0 ? "cu" : "cd"}`
  }, r.f.beta10bp >= 0 ? "+" : "", r.f.beta10bp, "%/10bp"), /*#__PURE__*/React.createElement("span", {
    className: "num muted"
  }, "r ", r.f.corr))) : /*#__PURE__*/React.createElement("div", {
    className: "tsy-mini-note"
  }, "No scan yet \u2014 run it in the Rate Sensitivity section below. Only statistically meaningful names (|t| \u2265 2) are shown."));
}

/* ── The tab ───────────────────────────────────────────────────────────── */
function TreasuriesTab({
  apiFetch,
  onOpenTicker
}) {
  const core = useTsy(apiFetch, "core", 900000);
  const mk = useTsy(apiFetch, "markets", 900000);
  const fd = useTsy(apiFetch, "fed", 1800000);
  const au = useTsy(apiFetch, "auctions", 3600000);
  const ct = useTsy(apiFetch, "cot", 3600000);
  const inf = useTsy(apiFetch, "inflation", 3600000);
  if (core.loading) return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement(TsyLoading, null));
  if (!core.d || !core.d.ok) return /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "US Treasuries"), /*#__PURE__*/React.createElement(TsyErr, {
    err: core.err || "Treasury data unavailable",
    retry: core.retry
  }));
  return /*#__PURE__*/React.createElement("div", {
    className: "tsy"
  }, /*#__PURE__*/React.createElement("div", {
    className: "tsy-ovrow r1"
  }, /*#__PURE__*/React.createElement(TsyOvYields, {
    core: core
  }), /*#__PURE__*/React.createElement(TsyOv10Y, {
    core: core
  }), /*#__PURE__*/React.createElement(TsyOvFutures, {
    mk: mk
  })), /*#__PURE__*/React.createElement("div", {
    className: "tsy-ovrow r2"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card tsy-mini"
  }, /*#__PURE__*/React.createElement("div", {
    className: "tsy-mini-h"
  }, /*#__PURE__*/React.createElement("em", null, "Treasury yield curve"), /*#__PURE__*/React.createElement("span", {
    className: "tsy-mini-note"
  }, "vs 1 month ago (dashed)")), /*#__PURE__*/React.createElement(TsyCurveSvg, {
    snaps: core.d.snapshots || {},
    cmp: "1m"
  })), /*#__PURE__*/React.createElement(TsyOvAnalysis, {
    core: core
  }), /*#__PURE__*/React.createElement(TsyOvAuctions, {
    au: au
  })), /*#__PURE__*/React.createElement("div", {
    className: "tsy-ovrow r3"
  }, /*#__PURE__*/React.createElement(TsyOvSpreads, {
    core: core
  }), /*#__PURE__*/React.createElement(TsyOvMatrix, {
    core: core
  }), /*#__PURE__*/React.createElement(TsyOvMove, {
    core: core
  }), /*#__PURE__*/React.createElement(TsyOvCorr, {
    mk: mk
  })), /*#__PURE__*/React.createElement("div", {
    className: "tsy-ovrow r3b"
  }, /*#__PURE__*/React.createElement(TsyOvCpiSummary, {
    inf: inf
  }), /*#__PURE__*/React.createElement(TsyOvCpiTrend, {
    inf: inf
  }), /*#__PURE__*/React.createElement(TsyOvExpectations, {
    core: core
  })), /*#__PURE__*/React.createElement("div", {
    className: "tsy-ovrow r4"
  }, /*#__PURE__*/React.createElement(TsyOvCot, {
    ct: ct
  }), /*#__PURE__*/React.createElement(TsyOvEvents, {
    core: core,
    inf: inf
  }), /*#__PURE__*/React.createElement(TsyOvFed, {
    fd: fd
  }), /*#__PURE__*/React.createElement(TsyOvSense, {
    apiFetch: apiFetch,
    onOpenTicker: onOpenTicker
  })), /*#__PURE__*/React.createElement(TsySignalsCard, {
    core: core
  }), /*#__PURE__*/React.createElement(TsyCpiCard, {
    apiFetch: apiFetch
  }), /*#__PURE__*/React.createElement(TsyFold, {
    kicker: "Interactive curve \xB7 compare any period, table view",
    title: "Yield curve workbench"
  }, /*#__PURE__*/React.createElement(TsyCurveCard, {
    core: core
  })), /*#__PURE__*/React.createElement(TsyFold, {
    kicker: "History \xB7 how markets traded past prints",
    title: "CPI releases & market reaction",
    hint: "expand to load"
  }, /*#__PURE__*/React.createElement(TsyCpiReactions, {
    apiFetch: apiFetch
  })), /*#__PURE__*/React.createElement(TsyMarketsCards, {
    apiFetch: apiFetch,
    onOpenTicker: onOpenTicker
  }), /*#__PURE__*/React.createElement(TsyFold, {
    kicker: "Full spread detail \xB7 changes and percentiles",
    title: "Treasury spreads detail"
  }, /*#__PURE__*/React.createElement(TsySpreadsCard, {
    core: core
  }), /*#__PURE__*/React.createElement(TsyExpectationsCard, {
    core: core
  })), /*#__PURE__*/React.createElement(TsyFold, {
    kicker: "Supply \xB7 every result vs its prior 10",
    title: "Treasury auctions detail"
  }, /*#__PURE__*/React.createElement(TsyAuctions, {
    apiFetch: apiFetch
  })), /*#__PURE__*/React.createElement(TsyFold, {
    kicker: "CFTC weekly detail \xB7 percentiles, weekly changes",
    title: "COT detail"
  }, /*#__PURE__*/React.createElement(TsyCot, {
    apiFetch: apiFetch
  })), /*#__PURE__*/React.createElement(TsyFold, {
    kicker: "Rolling correlation vs \u039410y \xB7 all windows",
    title: "Cross-asset relationships"
  }, /*#__PURE__*/React.createElement(TsyCorrTable, {
    apiFetch: apiFetch
  })), /*#__PURE__*/React.createElement(TsyFold, {
    kicker: "Your watchlist \xD7 yield factors",
    title: "Rate sensitivity watchlist",
    hint: "expand to scan"
  }, /*#__PURE__*/React.createElement(TsySense, {
    apiFetch: apiFetch,
    onOpenTicker: onOpenTicker
  })), /*#__PURE__*/React.createElement(TsyFold, {
    kicker: "Threshold rules on the displayed data",
    title: "Rates alerts",
    hint: "expand to configure"
  }, /*#__PURE__*/React.createElement(TsyAlertsCard, {
    core: core
  })), /*#__PURE__*/React.createElement(TsyFold, {
    kicker: "Fed detail \xB7 market-implied path by month",
    title: "Fed rate expectations detail"
  }, /*#__PURE__*/React.createElement(TsyFedCard, {
    apiFetch: apiFetch
  })));
}
Object.assign(window, {
  TreasuriesTab: React.memo(TreasuriesTab)
});
})();
