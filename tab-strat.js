(function () {
// tab-strat.jsx — LAZY CHUNK. Three dashboards that all read candle states,
// so they share one chunk: opening any of them loads the code once and the
// other two are instant.
//
//   SectorsTab        the eleven SPDR sectors, their breadth, their names
//   MarketContextTab  whole-market breadth, the intraday line, the indices
//   GexTab            gamma exposure by strike, and the sector market map
//
// A CANDLE STATE is what one bar did against the bar before it, on the same
// timeframe, using nothing but the two highs and the two lows:
//   1   inside      neither extreme taken out
//   2U  up          prior high exceeded, prior low held
//   2D  down        prior low broken, prior high held
//   3   outside     both taken out in the one bar
// Nothing here says a 2U is bullish. The state is a description of what
// happened; whether it means anything depends on the timeframe above it.
//
// Endpoints: /api/strat/sectors · /api/strat/sector · /api/strat/context
// · /api/strat/indices · /api/market_map · /api/gex

// ── shared vocabulary ─────────────────────────────────────────────────────
const ST_ORDER = ["1", "2U", "2D", "3"];
const ST_META = {
  "1": {
    label: "Inside",
    short: "1",
    tone: "inside",
    tip: "Inside bar (1) — the whole range sits inside the prior bar's range. Neither the prior high nor the prior low was taken out."
  },
  "2U": {
    label: "Up",
    short: "2U",
    tone: "up",
    tip: "Directional up (2U) — the prior high was exceeded while the prior low held."
  },
  "2D": {
    label: "Down",
    short: "2D",
    tone: "down",
    tip: "Directional down (2D) — the prior low was broken while the prior high held."
  },
  "3": {
    label: "Outside",
    short: "3",
    tone: "outside",
    tip: "Outside bar (3) — both the prior high and the prior low were taken out in the same bar."
  }
};
const ST_TF_TIP = {
  D: "Daily candle — today's session against yesterday's.",
  W: "Weekly candle — this calendar week against last week. Weeks are ISO weeks, Monday to Friday.",
  M: "Monthly candle — this calendar month against last month.",
  Q: "Quarterly candle — this calendar quarter against last quarter.",
  Y: "Yearly candle — this calendar year against last year.",
  "60m": "Sixty-minute candle, built from thirty-minute bars anchored to the 9:30 AM Eastern open.",
  "4H": "Four-hour candle — 9:30 AM to 1:30 PM Eastern, then 1:30 PM to the close."
};
const stNum = (v, d = 1) => v == null || !isFinite(v) ? "—" : Number(v).toFixed(d);
const stPct = (v, d = 1) => v == null || !isFinite(v) ? "—" : `${Number(v).toFixed(d)}%`;
const stChg = (v, d = 2) => v == null || !isFinite(v) ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
const stCap = v => {
  if (v == null || !isFinite(v) || v <= 0) return "—";
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)} trillion`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)} billion`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)} million`;
  return `$${Math.round(v).toLocaleString()}`;
};
// House rule: dates read "August 21, 2026". Never ISO on screen.
const stDate = s => {
  if (!s) return "—";
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric"
  });
};
const stTime = s => {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit"
  });
};
const stWhen = s => s ? `${stDate(s)} at ${stTime(s)}` : "—";

// Dollar figures on the gamma view are large and signed; "$1.42B" reads
// faster than a twelve-digit number and the sign is the whole point.
const stMoney = (v, d = 2) => {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v),
    sign = v < 0 ? "−" : "+";
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(d)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(d)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(0)}K`;
  return `${sign}$${a.toFixed(0)}`;
};

// ── shared pieces ─────────────────────────────────────────────────────────

function StChip({
  state,
  live,
  small
}) {
  if (!state) {
    return /*#__PURE__*/React.createElement("span", {
      className: `st-chip st-none${small ? " sm" : ""}`,
      title: "No state \u2014 this symbol does not have two candles on this timeframe yet, so there is nothing to compare."
    }, "\u2014");
  }
  const m = ST_META[state] || ST_META["1"];
  return /*#__PURE__*/React.createElement("span", {
    className: `st-chip st-${m.tone}${small ? " sm" : ""}${live ? " st-live" : ""}`,
    title: `${m.tip}${live ? " Live — includes today's session so far." : ""}`
  }, m.short);
}

// One stacked breadth bar. Segments are drawn in the fixed 1 / 2U / 2D / 3
// order so the eye can compare two bars without reading the legend twice.
function StBar({
  counts,
  pct,
  n,
  label,
  tip
}) {
  const total = n || 0;
  const segs = ST_ORDER.map(k => ({
    k,
    pct: (pct || {})[k] || 0,
    count: (counts || {})[k] || 0
  })).filter(s => s.pct > 0);
  return /*#__PURE__*/React.createElement("div", {
    className: "st-barrow"
  }, label ? /*#__PURE__*/React.createElement("span", {
    className: "st-barlabel",
    title: tip || ST_TF_TIP[label] || label
  }, label) : null, /*#__PURE__*/React.createElement("div", {
    className: "st-bar",
    role: "img",
    "aria-label": total ? `${label || "Breadth"}: ${ST_ORDER.map(k => `${(counts || {})[k] || 0} ${ST_META[k].label}`).join(", ")} of ${total}` : `${label || "Breadth"}: no data`
  }, total === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "st-seg st-empty",
    style: {
      width: "100%"
    },
    title: "No symbol on this timeframe has two candles to compare yet."
  }) : segs.map(s => /*#__PURE__*/React.createElement("div", {
    key: s.k,
    className: `st-seg st-${ST_META[s.k].tone}`,
    style: {
      width: `${s.pct}%`
    },
    title: `${ST_META[s.k].label} (${s.k}): ${s.count} of ${total} — ${stPct(s.pct)}. ${ST_META[s.k].tip}`
  }))), /*#__PURE__*/React.createElement("span", {
    className: "st-barn",
    title: `${total} symbols classified on this timeframe.`
  }, total || "—"));
}
function StLegend() {
  return /*#__PURE__*/React.createElement("div", {
    className: "st-legend"
  }, ST_ORDER.map(k => /*#__PURE__*/React.createElement("span", {
    key: k,
    className: "st-legend-item",
    title: ST_META[k].tip
  }, /*#__PURE__*/React.createElement("i", {
    className: `st-swatch st-${ST_META[k].tone}`
  }), ST_META[k].short, " \xB7 ", ST_META[k].label)));
}

// Market status + how fresh the numbers are. Every one of the three tabs
// carries this, because "is this live or is it Friday's close" is the first
// question any of these screens raises.
function StStatus({
  status,
  boardAsOf,
  scanning,
  staleCount,
  candleDate,
  extra
}) {
  const s = status || {};
  const tone = s.is_open ? "up" : s.phase === "pre" || s.phase === "post" ? "warn" : "mut";
  // A candle older than the session the clock says we are in means the scan
  // has not caught up. Showing the last settled candle is the right call —
  // saying which one it is, is the rest of it.
  const behind = staleCount > 0 && candleDate && candleDate !== s.session_date;
  return /*#__PURE__*/React.createElement("div", {
    className: "st-status"
  }, /*#__PURE__*/React.createElement("span", {
    className: `st-dot st-dot-${tone}`,
    "aria-hidden": "true"
  }), /*#__PURE__*/React.createElement("span", {
    className: "st-status-label",
    title: s.reason || ""
  }, s.label || "Market status unknown"), s.session_date ? /*#__PURE__*/React.createElement("span", {
    className: "st-status-sep",
    title: s.live_ok ? "States include today's session so far, from the regular-session high and low." : "The regular session has not begun (or has fully ended), so these are the settled states from the last close. Pre-market prints are deliberately excluded: a hundred shares through yesterday's high should not flip a name to 2U."
  }, "candles as of ", stDate(s.session_date), s.live_ok ? " · live" : " · last close") : null, boardAsOf ? /*#__PURE__*/React.createElement("span", {
    className: "st-status-sep",
    title: "When the watchlist scan last rebuilt the period highs and lows these states are measured against. The scan runs at 9 AM and 6 PM Eastern."
  }, "bars from ", stWhen(boardAsOf)) : null, scanning ? /*#__PURE__*/React.createElement("span", {
    className: "st-status-sep st-scanning"
  }, "scan running\u2026") : null, behind ? /*#__PURE__*/React.createElement("span", {
    className: "st-status-sep st-behind",
    title: "The daily candles on screen are the last settled ones, not the current session's \u2014 the watchlist scan has not run since. They are shown rather than blanked, but they are not today's."
  }, "showing ", stDate(candleDate), " candles \xB7 scan is behind") : null, extra);
}
function StLoading({
  label
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "card st-loading",
    "aria-busy": "true",
    "aria-label": `Loading ${label}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "30%"
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "92%"
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "78%"
    }
  }), /*#__PURE__*/React.createElement("div", {
    className: "skel skel-line",
    style: {
      width: "85%"
    }
  }));
}
function StError({
  error,
  onRetry
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "research-error"
  }, String(error)), onRetry ? /*#__PURE__*/React.createElement("button", {
    className: "card-error-btn st-retry",
    onClick: onRetry
  }, "Try again") : null);
}
function StEmpty({
  children
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, children);
}

// Shared polling loader. One place so all three tabs behave identically:
// fetch on mount, refetch on an interval, skip the refetch while the tab is
// hidden, and never let a slow response from an old request overwrite a new
// one (the sequence guard).
function useStFeed(apiFetch, url, refreshMs) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  const load = React.useCallback(async quiet => {
    const mine = ++seq.current;
    if (!quiet) setBusy(true);
    try {
      const r = await apiFetch(url);
      const d = await r.json();
      if (mine !== seq.current) return null; // a newer request won
      setData(d);
      setErr(d && d.error && d.ok === false ? d.error : null);
      return d;
    } catch (e) {
      if (mine === seq.current) setErr(String(e && e.message ? e.message : e));
      return null;
    } finally {
      if (mine === seq.current) setBusy(false);
    }
  }, [apiFetch, url]);
  useEffect(() => {
    load(false);
    if (!refreshMs) return undefined;
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      load(true);
    }, refreshMs);
    return () => clearInterval(id);
  }, [load, refreshMs]);
  return {
    data,
    err,
    busy,
    reload: () => load(false)
  };
}

// ══════════════════════════════════════════════════════════════════════════
// SECTORS
// ══════════════════════════════════════════════════════════════════════════

const SEC_SORTS = [["leaders", "Leaders first", "Most of the sector's directional names are 2U today."], ["laggards", "Laggards first", "Most of the sector's directional names are 2D today."], ["move", "Biggest move", "By the median percentage change of the sector's names today."], ["size", "Most names", "By how many watchlist names are classified in the sector."], ["name", "Alphabetical", "By sector name."]];
function SectorCard({
  card,
  timeframes,
  onOpen
}) {
  const br = card.breadth || {};
  const up = card.up_share_d;
  return /*#__PURE__*/React.createElement("button", {
    className: "card st-seccard",
    onClick: () => onOpen(card.etf),
    "aria-label": `Open ${card.name}, ${card.constituents} names`
  }, /*#__PURE__*/React.createElement("div", {
    className: "st-seccard-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker",
    title: "The sector ETF whose sector this is. Membership is the classification of each company, not the ETF's published holdings."
  }, card.etf), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, card.name)), /*#__PURE__*/React.createElement("div", {
    className: "st-seccard-right"
  }, /*#__PURE__*/React.createElement("div", {
    className: `st-seccard-chg ${(card.median_change_pct || 0) >= 0 ? "up" : "down"}`,
    title: "Median percentage change today across the sector's names. The median, not the average, so one name cannot carry the sector."
  }, stChg(card.median_change_pct)), /*#__PURE__*/React.createElement("div", {
    className: "st-seccard-n",
    title: "How many watchlist names are classified in this sector."
  }, card.constituents, " ", card.constituents === 1 ? "name" : "names"))), /*#__PURE__*/React.createElement("div", {
    className: "st-seccard-bars"
  }, timeframes.map(tf => /*#__PURE__*/React.createElement(StBar, {
    key: tf.key,
    label: tf.key,
    tip: ST_TF_TIP[tf.key],
    counts: (br[tf.key] || {}).counts,
    pct: (br[tf.key] || {}).pct,
    n: (br[tf.key] || {}).n
  }))), /*#__PURE__*/React.createElement("div", {
    className: "st-seccard-foot"
  }, /*#__PURE__*/React.createElement("span", {
    title: "Share of the sector's DIRECTIONAL daily candles that are 2U \u2014 that is, 2U out of 2U plus 2D. Inside and outside bars are excluded because an inside bar has no direction and an outside bar took out both sides."
  }, up == null ? "No directional names today" : `${stPct(up, 0)} of directional names up`), /*#__PURE__*/React.createElement("span", {
    className: "st-seccard-open"
  }, "Open \u2192")));
}
const SEC_DETAIL_SORTS = [["cap", "Market value"], ["chg", "Change today"], ["sym", "Symbol"], ["align", "Timeframe agreement"]];
function SectorModal({
  apiFetch,
  etf,
  onClose,
  onOpenTicker
}) {
  const {
    data,
    err,
    busy,
    reload
  } = useStFeed(apiFetch, `/api/strat/sector?name=${encodeURIComponent(etf)}`, 30000);
  const [sortK, setSortK] = useState("cap");
  const [q, setQ] = useState("");
  const [tfFilter, setTfFilter] = useState("");
  const [stFilter, setStFilter] = useState("");
  const boxRef = useRef(null);
  useEffect(() => {
    const onKey = e => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    if (boxRef.current) {
      try {
        boxRef.current.focus();
      } catch (e) {/* focus is best effort */}
    }
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const tfs = data && data.timeframes || [];
  const rows = useMemo(() => {
    let out = (data && data.rows || []).slice();
    const needle = q.trim().toUpperCase();
    if (needle) {
      out = out.filter(r => r.symbol.indexOf(needle) >= 0 || String(r.company || "").toUpperCase().indexOf(needle) >= 0);
    }
    if (tfFilter && stFilter) out = out.filter(r => (r.states || {})[tfFilter] === stFilter);
    const dir = r => r.continuity && r.continuity.aligned || "";
    const rank = {
      up: 0,
      down: 1,
      mixed: 2,
      "": 3
    };
    if (sortK === "cap") out.sort((a, b) => (b.market_cap || 0) - (a.market_cap || 0));else if (sortK === "chg") out.sort((a, b) => (b.change_pct == null ? -1e9 : b.change_pct) - (a.change_pct == null ? -1e9 : a.change_pct));else if (sortK === "sym") out.sort((a, b) => a.symbol.localeCompare(b.symbol));else if (sortK === "align") out.sort((a, b) => rank[dir(a)] - rank[dir(b)] || (b.market_cap || 0) - (a.market_cap || 0));
    return out;
  }, [data, q, sortK, tfFilter, stFilter]);
  // useBoundedList returns a PAIR, not an object.
  const [shown, controls] = useBoundedList(rows, 120, 240);
  const card = data && data.sector || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "st-modal-wrap",
    onClick: onClose
  }, /*#__PURE__*/React.createElement("div", {
    className: "st-modal",
    role: "dialog",
    "aria-modal": "true",
    tabIndex: -1,
    ref: boxRef,
    "aria-label": `${card.name || etf} constituents`,
    onClick: e => e.stopPropagation()
  }, /*#__PURE__*/React.createElement("div", {
    className: "st-modal-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, etf), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, card.name || "Sector"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, card.constituents ? `${card.constituents} names` : "", data && data.status && data.status.live_ok ? " · live" : data ? " · last close" : "")), /*#__PURE__*/React.createElement("button", {
    className: "st-modal-close",
    onClick: onClose,
    "aria-label": "Close",
    title: "Close (Escape)"
  }, "\u2715")), err ? /*#__PURE__*/React.createElement(StError, {
    error: err,
    onRetry: reload
  }) : null, !data && !err ? /*#__PURE__*/React.createElement(StLoading, {
    label: "sector constituents"
  }) : null, data && data.ok ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "st-modal-tools"
  }, /*#__PURE__*/React.createElement("input", {
    className: "st-search",
    value: q,
    onChange: e => setQ(e.target.value),
    placeholder: "Filter by symbol or company",
    "aria-label": "Filter constituents",
    title: "Type part of a ticker or company name to narrow the list."
  }), /*#__PURE__*/React.createElement("div", {
    className: "seg",
    role: "group",
    "aria-label": "Sort constituents"
  }, SEC_DETAIL_SORTS.map(([k, lab]) => /*#__PURE__*/React.createElement("button", {
    key: k,
    className: sortK === k ? "active" : "",
    onClick: () => setSortK(k),
    title: k === "align" ? "Group the names whose daily, weekly and monthly candles all point the same way." : `Sort by ${lab.toLowerCase()}.`
  }, lab))), /*#__PURE__*/React.createElement("div", {
    className: "st-modal-filter"
  }, /*#__PURE__*/React.createElement("select", {
    value: tfFilter,
    onChange: e => setTfFilter(e.target.value),
    "aria-label": "Filter timeframe",
    title: "Show only the names in a particular state on one timeframe."
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "Any timeframe"), tfs.map(t => /*#__PURE__*/React.createElement("option", {
    key: t.key,
    value: t.key
  }, t.label))), /*#__PURE__*/React.createElement("select", {
    value: stFilter,
    onChange: e => setStFilter(e.target.value),
    "aria-label": "Filter state",
    disabled: !tfFilter,
    title: tfFilter ? "Show only names in this state." : "Pick a timeframe first."
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "Any state"), ST_ORDER.map(k => /*#__PURE__*/React.createElement("option", {
    key: k,
    value: k
  }, ST_META[k].short, " \xB7 ", ST_META[k].label))))), /*#__PURE__*/React.createElement("div", {
    className: "st-modal-body"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table st-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Ticker symbol. Click a row to load it on the Trade tab."
  }, "Symbol"), /*#__PURE__*/React.createElement("th", {
    title: "Company name."
  }, "Company"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Last traded price."
  }, "Price"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Percentage change today."
  }, "Change"), tfs.map(t => /*#__PURE__*/React.createElement("th", {
    key: t.key,
    className: "st-th-tf",
    title: ST_TF_TIP[t.key] || t.label
  }, t.label)), /*#__PURE__*/React.createElement("th", {
    title: "Whether the daily, weekly and monthly candles point the same way. Agreement needs at least two of the three to be directional."
  }, "Agreement"))), /*#__PURE__*/React.createElement("tbody", null, shown.map(r => {
    const al = r.continuity && r.continuity.aligned || null;
    return /*#__PURE__*/React.createElement("tr", {
      key: r.symbol,
      className: "scan-row",
      onClick: () => onOpenTicker && onOpenTicker(r.symbol)
    }, /*#__PURE__*/React.createElement("td", {
      className: "scan-sym"
    }, r.symbol), /*#__PURE__*/React.createElement("td", {
      className: "st-co",
      title: r.company || ""
    }, r.company || "—"), /*#__PURE__*/React.createElement("td", {
      className: "scan-num"
    }, r.price == null ? "—" : `$${stNum(r.price, 2)}`), /*#__PURE__*/React.createElement("td", {
      className: `scan-num ${(r.change_pct || 0) >= 0 ? "up" : "down"}`
    }, stChg(r.change_pct)), tfs.map(t => {
      const ex = (r.extremes || {})[t.key] || {};
      return /*#__PURE__*/React.createElement("td", {
        key: t.key,
        className: "st-td-tf",
        title: ex.prev_high != null ? `${t.label}: prior candle ${stNum(ex.prev_low, 2)} to ${stNum(ex.prev_high, 2)}, current candle ${stNum(ex.cur_low, 2)} to ${stNum(ex.cur_high, 2)}.` : `${t.label}: no prior candle to compare against.`
      }, /*#__PURE__*/React.createElement(StChip, {
        state: (r.states || {})[t.key],
        live: r.live,
        small: true
      }));
    }), /*#__PURE__*/React.createElement("td", {
      className: `st-align st-align-${al || "none"}`,
      title: al ? `Daily, weekly and monthly: ${r.continuity.up} up, ${r.continuity.down} down.` : "Fewer than two of the daily, weekly and monthly candles are directional, so there is nothing to agree."
    }, al === "up" ? "All up" : al === "down" ? "All down" : al === "mixed" ? "Mixed" : "—"));
  }))), controls, !rows.length ? /*#__PURE__*/React.createElement(StEmpty, null, q || stFilter ? "No name in this sector matches that filter." : "No names are classified in this sector yet. The board fills after a watchlist scan.") : null), /*#__PURE__*/React.createElement("div", {
    className: "st-modal-foot"
  }, /*#__PURE__*/React.createElement(StLegend, null), busy ? /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "refreshing\u2026") : null)) : null));
}
function SectorsTab({
  apiFetch,
  onOpenTicker
}) {
  const {
    data,
    err,
    busy,
    reload
  } = useStFeed(apiFetch, "/api/strat/sectors", 30000);
  const [sortK, setSortK] = useState("leaders");
  const [open, setOpen] = useState(null);
  const tfs = data && data.timeframes || [];
  const cards = useMemo(() => {
    const out = (data && data.sectors || []).slice();
    const up = c => c.up_share_d == null ? -1 : c.up_share_d;
    if (sortK === "leaders") out.sort((a, b) => up(b) - up(a) || b.constituents - a.constituents);else if (sortK === "laggards") out.sort((a, b) => (up(a) === -1 ? 1e9 : up(a)) - (up(b) === -1 ? 1e9 : up(b)) || b.constituents - a.constituents);else if (sortK === "move") out.sort((a, b) => (b.median_change_pct || 0) - (a.median_change_pct || 0));else if (sortK === "size") out.sort((a, b) => b.constituents - a.constituents);else out.sort((a, b) => a.name.localeCompare(b.name));
    return out;
  }, [data, sortK]);
  const populated = cards.filter(c => c.constituents > 0);
  const leaders = data && data.leaders || [];
  const laggards = data && data.laggards || [];
  return /*#__PURE__*/React.createElement("div", {
    className: "col-list st-tab st-tab-sectors"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Sectors"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Candle states across the eleven sectors"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "How many names in each sector are inside, up, down or outside \u2014 on the daily, weekly, monthly, quarterly and yearly candle. Click a sector for its names.")), /*#__PURE__*/React.createElement("div", {
    className: "toolbar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "seg",
    role: "group",
    "aria-label": "Sort sectors"
  }, SEC_SORTS.map(([k, lab, tip]) => /*#__PURE__*/React.createElement("button", {
    key: k,
    className: sortK === k ? "active" : "",
    onClick: () => setSortK(k),
    title: tip
  }, lab))), /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    onClick: reload,
    disabled: busy,
    title: "Re-read the board and the live quotes now."
  }, busy ? "Refreshing…" : "Refresh"))), /*#__PURE__*/React.createElement(StStatus, {
    status: data && data.status,
    boardAsOf: data && data.board_as_of,
    scanning: data && data.scanning,
    staleCount: data && data.stale_symbols,
    candleDate: data && data.candle_date
  }), /*#__PURE__*/React.createElement(StLegend, null), data && data.membership_note ? /*#__PURE__*/React.createElement("div", {
    className: "st-note",
    title: "Sector classification comes from the company's own reported sector, carried on the watchlist board."
  }, data.membership_note) : null, data && (leaders.length || laggards.length) ? /*#__PURE__*/React.createElement("div", {
    className: "st-rankline"
  }, /*#__PURE__*/React.createElement("span", {
    title: "The sectors with the highest share of directional daily candles pointing up."
  }, /*#__PURE__*/React.createElement("b", null, "Leading:"), " ", leaders.join(" · ") || "—"), /*#__PURE__*/React.createElement("span", {
    title: "The sectors with the lowest share of directional daily candles pointing up."
  }, /*#__PURE__*/React.createElement("b", null, "Lagging:"), " ", laggards.join(" · ") || "—")) : null, data && data.unclassified ? /*#__PURE__*/React.createElement("div", {
    className: "st-note muted",
    title: "These names are on your watchlist but their sector is not one of the eleven, or the board has not filled it in yet. They are counted in Market Breadth and left out of the sector cards rather than being put in a twelfth bucket."
  }, data.unclassified, " of ", data.universe, " watchlist names are not classified into one of the eleven sectors.") : null), err ? /*#__PURE__*/React.createElement(StError, {
    error: err,
    onRetry: reload
  }) : null, !data && !err ? /*#__PURE__*/React.createElement(StLoading, {
    label: "sectors"
  }) : null, data && !populated.length && !err ? /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement(StEmpty, null, "No sector has any classified names yet. The candle states are built during the watchlist scan, which runs at 9 AM and 6 PM Eastern \u2014 open the Watchlist tab and hit \u201CScan now\u201D to fill the board immediately.")) : null, populated.length ? /*#__PURE__*/React.createElement("div", {
    className: "st-secgrid"
  }, populated.map(c => /*#__PURE__*/React.createElement(SectorCard, {
    key: c.etf,
    card: c,
    timeframes: tfs,
    onOpen: setOpen
  }))) : null, open ? /*#__PURE__*/React.createElement(SectorModal, {
    apiFetch: apiFetch,
    etf: open,
    onClose: () => setOpen(null),
    onOpenTicker: onOpenTicker
  }) : null);
}

// ══════════════════════════════════════════════════════════════════════════
// MARKET CONTEXT
// ══════════════════════════════════════════════════════════════════════════

// Intraday breadth, drawn as one line per state. Plain SVG: four polylines
// over a shared scale, no dependency, and it reads at phone width.
function BreadthLines({
  series,
  timeframe
}) {
  const pts = (series || []).filter(p => p && p.counts && p.counts[timeframe]);
  if (pts.length < 2) {
    return /*#__PURE__*/React.createElement(StEmpty, null, pts.length === 1 ? "One sample so far today — the line needs a second reading before it can be drawn. Samples are taken every couple of minutes while the market is open." : "No samples yet today. This line fills in while the regular session is open; it deliberately records nothing overnight, because a flat line through the small hours is an idle poller, not market data.");
  }
  const W = 720,
    H = 200,
    PADL = 34,
    PADB = 22,
    PADT = 10,
    PADR = 8;
  const iw = W - PADL - PADR,
    ih = H - PADT - PADB;
  let maxV = 0;
  for (const p of pts) for (const k of ST_ORDER) maxV = Math.max(maxV, p.counts[timeframe][k] || 0);
  maxV = Math.max(maxV, 1);
  const x = i => PADL + (pts.length === 1 ? iw / 2 : i * iw / (pts.length - 1));
  const y = v => PADT + ih - v / maxV * ih;
  const ticks = [0, Math.round(maxV / 2), maxV].filter((v, i, a) => a.indexOf(v) === i);
  return /*#__PURE__*/React.createElement("div", {
    className: "st-linewrap"
  }, /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${W} ${H}`,
    className: "st-linechart",
    role: "img",
    preserveAspectRatio: "none",
    "aria-label": `Intraday count of symbols in each state on the ${timeframe} candle, ${pts.length} samples today`
  }, ticks.map(v => /*#__PURE__*/React.createElement("g", {
    key: v
  }, /*#__PURE__*/React.createElement("line", {
    x1: PADL,
    x2: W - PADR,
    y1: y(v),
    y2: y(v),
    className: "st-gridline"
  }), /*#__PURE__*/React.createElement("text", {
    x: PADL - 6,
    y: y(v) + 4,
    className: "st-axis",
    textAnchor: "end"
  }, v))), ST_ORDER.map(k => /*#__PURE__*/React.createElement("polyline", {
    key: k,
    className: `st-line st-line-${ST_META[k].tone}`,
    points: pts.map((p, i) => `${x(i)},${y(p.counts[timeframe][k] || 0)}`).join(" ")
  })), /*#__PURE__*/React.createElement("text", {
    x: PADL,
    y: H - 6,
    className: "st-axis"
  }, pts[0].et || ""), /*#__PURE__*/React.createElement("text", {
    x: W - PADR,
    y: H - 6,
    className: "st-axis",
    textAnchor: "end"
  }, pts[pts.length - 1].et || "")), /*#__PURE__*/React.createElement("div", {
    className: "st-linefoot"
  }, ST_ORDER.map(k => {
    const last = pts[pts.length - 1].counts[timeframe][k] || 0;
    const first = pts[0].counts[timeframe][k] || 0;
    return /*#__PURE__*/React.createElement("span", {
      key: k,
      className: "st-legend-item",
      title: `${ST_META[k].label} (${k}): ${first} at ${pts[0].et}, ${last} now. ${ST_META[k].tip}`
    }, /*#__PURE__*/React.createElement("i", {
      className: `st-swatch st-${ST_META[k].tone}`
    }), ST_META[k].short, " ", last, /*#__PURE__*/React.createElement("b", {
      className: last - first > 0 ? "up" : last - first < 0 ? "down" : "muted"
    }, last - first === 0 ? "" : ` ${last - first > 0 ? "+" : ""}${last - first}`));
  })));
}
function IndicesMatrix({
  apiFetch
}) {
  const {
    data,
    err,
    busy,
    reload
  } = useStFeed(apiFetch, "/api/strat/indices", 60000);
  const cols = data && data.columns || [];
  const rows = data && data.rows || [];
  return /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Indices"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "The four index funds, across every timeframe"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "S&P 500, Nasdaq 100, Russell 2000 and the Dow, from the sixty-minute candle out to the yearly one.")), /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    onClick: reload,
    disabled: busy,
    title: "Re-read the index candles now."
  }, busy ? "Refreshing…" : "Refresh")), err ? /*#__PURE__*/React.createElement(StError, {
    error: err,
    onRetry: reload
  }) : null, !data && !err ? /*#__PURE__*/React.createElement(StLoading, {
    label: "the indices matrix"
  }) : null, data && !rows.length ? /*#__PURE__*/React.createElement(StEmpty, null, "No index data came back. The intraday candles need the broker connection \u2014 check it under the Manage tab.") : null, rows.length ? /*#__PURE__*/React.createElement("div", {
    className: "st-scroll"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table st-matrix"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "The index fund. These are the tradable ETFs, not the index itself."
  }, "Index"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Last traded price."
  }, "Price"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Percentage change today."
  }, "Change"), cols.map(c => /*#__PURE__*/React.createElement("th", {
    key: c.key,
    className: "st-th-tf",
    title: ST_TF_TIP[c.key] || c.label
  }, c.label)))), /*#__PURE__*/React.createElement("tbody", null, rows.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.symbol
  }, /*#__PURE__*/React.createElement("td", {
    className: "scan-sym",
    title: r.name
  }, r.symbol, " ", /*#__PURE__*/React.createElement("span", {
    className: "muted st-idxname"
  }, r.name)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, r.price == null ? "—" : `$${stNum(r.price, 2)}`), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${(r.change_pct || 0) >= 0 ? "up" : "down"}`
  }, stChg(r.change_pct)), cols.map(c => {
    const cell = (r.cells || {})[c.key] || {};
    return /*#__PURE__*/React.createElement("td", {
      key: c.key,
      className: "st-td-tf",
      title: cell.state ? `${c.label}: prior candle ${stNum(cell.prev_low, 2)} to ${stNum(cell.prev_high, 2)}, current candle ${stNum(cell.cur_low, 2)} to ${stNum(cell.cur_high, 2)}.` : cell.reason || `${c.label}: no state available.`
    }, /*#__PURE__*/React.createElement(StChip, {
      state: cell.state,
      live: cell.live,
      small: true
    }));
  })))))) : null, data && data.note ? /*#__PURE__*/React.createElement("div", {
    className: "st-note muted"
  }, data.note) : null);
}
function MarketContextTab({
  apiFetch
}) {
  const {
    data,
    err,
    busy,
    reload
  } = useStFeed(apiFetch, "/api/strat/context", 30000);
  const [lineTf, setLineTf] = useState("D");
  const tfs = data && data.timeframes || [];
  const br = data && data.breadth || {};
  const snap = data && data.snapshot || [];
  return /*#__PURE__*/React.createElement("div", {
    className: "col-list st-tab st-tab-context"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Market context"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "What the whole watchlist's candles are doing"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "One read of every name you track, on five timeframes at once.")), /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    onClick: reload,
    disabled: busy,
    title: "Re-read the board and the live quotes now."
  }, busy ? "Refreshing…" : "Refresh")), /*#__PURE__*/React.createElement(StStatus, {
    status: data && data.status,
    boardAsOf: data && data.board_as_of,
    scanning: data && data.scanning,
    staleCount: data && data.stale_symbols,
    candleDate: data && data.candle_date,
    extra: data ? /*#__PURE__*/React.createElement("span", {
      className: "st-status-sep",
      title: "How many of the watchlist's names have a live quote behind their state right now. The rest are showing their settled state from the last close."
    }, data.quoted, " of ", data.universe, " quoted live") : null
  })), err ? /*#__PURE__*/React.createElement(StError, {
    error: err,
    onRetry: reload
  }) : null, !data && !err ? /*#__PURE__*/React.createElement(StLoading, {
    label: "market context"
  }) : null, data ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Market breadth"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Share of the watchlist in each state"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "Every name you track, by what its candle is doing on each timeframe.")), /*#__PURE__*/React.createElement(StLegend, null)), /*#__PURE__*/React.createElement("div", {
    className: "st-breadth"
  }, tfs.map(t => {
    const b = br[t.key] || {};
    return /*#__PURE__*/React.createElement("div", {
      key: t.key,
      className: "st-breadth-row"
    }, /*#__PURE__*/React.createElement("div", {
      className: "st-breadth-head"
    }, /*#__PURE__*/React.createElement("span", {
      className: "st-breadth-tf",
      title: ST_TF_TIP[t.key]
    }, t.label), /*#__PURE__*/React.createElement("span", {
      className: "st-breadth-nums"
    }, ST_ORDER.map(k => /*#__PURE__*/React.createElement("span", {
      key: k,
      className: `st-num st-${ST_META[k].tone}`,
      title: `${ST_META[k].label} (${k}): ${(b.counts || {})[k] || 0} of ${b.n || 0} names — ${stPct((b.pct || {})[k])}.`
    }, ST_META[k].short, " ", stPct((b.pct || {})[k], 0))))), /*#__PURE__*/React.createElement(StBar, {
      counts: b.counts,
      pct: b.pct,
      n: b.n
    }));
  })), data.symbols_without_states ? /*#__PURE__*/React.createElement("div", {
    className: "st-note muted",
    title: "These names are on the board but the scan has not produced candle extremes for them yet \u2014 usually a symbol whose bars failed to download on the last pass."
  }, data.symbols_without_states, " watchlist names have no candle states yet and are left out of these counts.") : null), /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Daily breadth"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "How the states have shifted through today"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "Number of names in each state, sampled every ", Math.round((data.series_sample_seconds || 120) / 60), " minutes while the regular session is open.")), /*#__PURE__*/React.createElement("div", {
    className: "toolbar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "seg",
    role: "group",
    "aria-label": "Timeframe for the intraday line"
  }, tfs.map(t => /*#__PURE__*/React.createElement("button", {
    key: t.key,
    className: lineTf === t.key ? "active" : "",
    onClick: () => setLineTf(t.key),
    title: ST_TF_TIP[t.key]
  }, t.key))))), /*#__PURE__*/React.createElement(BreadthLines, {
    series: data.series,
    timeframe: lineTf
  })), /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Current candle"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "State totals by timeframe"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "The same counts as a table, when the exact number matters more than the shape."))), /*#__PURE__*/React.createElement("div", {
    className: "st-scroll"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table st-snap"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "The candle being read."
  }, "Timeframe"), ST_ORDER.map(k => /*#__PURE__*/React.createElement("th", {
    key: k,
    className: "scan-num",
    title: ST_META[k].tip
  }, ST_META[k].short, " \xB7 ", ST_META[k].label)), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "How many names could be classified on this timeframe."
  }, "Classified"), /*#__PURE__*/React.createElement("th", {
    className: "scan-num",
    title: "Share of the DIRECTIONAL names pointing up \u2014 2U out of 2U plus 2D. Inside and outside bars are excluded."
  }, "Up share"))), /*#__PURE__*/React.createElement("tbody", null, snap.map(s => /*#__PURE__*/React.createElement("tr", {
    key: s.timeframe
  }, /*#__PURE__*/React.createElement("td", {
    className: "scan-sym",
    title: ST_TF_TIP[s.timeframe]
  }, s.label), ST_ORDER.map(k => /*#__PURE__*/React.createElement("td", {
    key: k,
    className: `scan-num st-${ST_META[k].tone}`,
    title: `${(s.counts || {})[k] || 0} of ${s.n} names — ${stPct((s.pct || {})[k])}.`
  }, (s.counts || {})[k] || 0, /*#__PURE__*/React.createElement("span", {
    className: "muted st-snappct"
  }, " ", stPct((s.pct || {})[k], 0)))), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, s.n), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${s.up_share == null ? "" : s.up_share >= 50 ? "up" : "down"}`
  }, s.up_share == null ? "—" : stPct(s.up_share, 0))))))), !snap.length ? /*#__PURE__*/React.createElement(StEmpty, null, "No candle states yet. They are built during the watchlist scan.") : null), /*#__PURE__*/React.createElement(IndicesMatrix, {
    apiFetch: apiFetch
  })) : null);
}

// ══════════════════════════════════════════════════════════════════════════
// GAMMA EXPOSURE + MARKET MAP
// ══════════════════════════════════════════════════════════════════════════

const GEX_QUICK = ["SPY", "QQQ", "IWM", "DIA"];
function GexBars({
  strikes,
  spot,
  flip,
  mode
}) {
  const rows = (strikes || []).slice().sort((a, b) => b.strike - a.strike);
  if (!rows.length) return /*#__PURE__*/React.createElement(StEmpty, null, "No strikes to draw.");
  const val = r => mode === "oi" ? r.total_oi || 0 : r.net_gex || 0;
  const maxAbs = Math.max(1, ...rows.map(r => Math.abs(val(r))));
  // Open interest is a count and never negative, so it draws as a single bar
  // growing right. Exposure is signed and draws either side of a centre line.
  const centre = mode === "oi" ? 0 : 50;
  // Mark the listed strike nearest the last price, so the eye has an anchor
  // for "where are we now" without needing a second axis.
  let atSpot = null;
  if (spot) {
    let best = Infinity;
    for (const r of rows) {
      const d = Math.abs(r.strike - spot);
      if (d < best) {
        best = d;
        atSpot = r.strike;
      }
    }
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "gx-bars",
    role: "table",
    "aria-label": mode === "oi" ? "Open interest by strike" : "Net gamma exposure by strike"
  }, rows.map(r => {
    const v = val(r);
    const w = Math.abs(v) / maxAbs * (mode === "oi" ? 100 : 50);
    const isSpot = atSpot != null && r.strike === atSpot;
    return /*#__PURE__*/React.createElement("div", {
      key: r.strike,
      className: `gx-row${isSpot ? " gx-row-spot" : ""}`,
      role: "row",
      title: isSpot ? `Closest listed strike to the last price of $${stNum(spot, 2)}.` : undefined
    }, /*#__PURE__*/React.createElement("div", {
      className: "gx-strike",
      role: "cell",
      title: spot ? `${stNum((r.strike / spot - 1) * 100, 1)}% from the last price of $${stNum(spot, 2)}.` : ""
    }, stNum(r.strike, r.strike >= 100 ? 0 : 2)), /*#__PURE__*/React.createElement("div", {
      className: "gx-track",
      role: "cell",
      title: mode === "oi" ? `Strike ${stNum(r.strike, 2)}: ${(r.call_oi || 0).toLocaleString()} call contracts and ${(r.put_oi || 0).toLocaleString()} put contracts open, ${(r.total_oi || 0).toLocaleString()} in total.` : `Strike ${stNum(r.strike, 2)}: calls ${stMoney(r.call_gex)}, puts ${stMoney(r.put_gex)}, net ${stMoney(r.net_gex)} of dealer delta per 1% move.`
    }, /*#__PURE__*/React.createElement("span", {
      className: "gx-centre",
      style: {
        left: `${centre}%`
      }
    }), /*#__PURE__*/React.createElement("span", {
      className: `gx-fill ${v >= 0 ? "gx-pos" : "gx-neg"}`,
      style: v >= 0 ? {
        left: `${centre}%`,
        width: `${w}%`
      } : {
        left: `${centre - w}%`,
        width: `${w}%`
      }
    })), /*#__PURE__*/React.createElement("div", {
      className: "gx-val scan-num",
      role: "cell"
    }, mode === "oi" ? (r.total_oi || 0).toLocaleString() : stMoney(r.net_gex, 1)));
  }), /*#__PURE__*/React.createElement("div", {
    className: "gx-scalefoot muted"
  }, mode === "oi" ? "Bar length is total open interest at that strike, calls plus puts." : "Bars run right for positive net exposure and left for negative, scaled to the largest strike on the board.", spot ? ` Last price $${stNum(spot, 2)}.` : "", flip != null ? ` Estimated gamma flip $${stNum(flip, 2)}.` : ""));
}
function GexSummary({
  data
}) {
  const s = data && data.summary || {};
  const p = data && data.profile || {};
  const cards = [{
    k: "net",
    label: "Net gamma exposure",
    value: stMoney(s.net_gex),
    tone: s.net_gex > 0 ? "up" : s.net_gex < 0 ? "down" : "",
    tip: "Calls minus puts, in dollars of dealer delta per 1% move. Positive means dealers hedge against the move — pinning and mean reversion. Negative means they hedge with it — trending and amplified moves. Dealer positioning is not published; this is the standard modelling assumption, not a measurement."
  }, {
    k: "call",
    label: "Call gamma exposure",
    value: stMoney(s.call_gex),
    tone: "up",
    tip: "Total exposure from call open interest, counted positive under the standard convention that dealers are net long call gamma."
  }, {
    k: "put",
    label: "Put gamma exposure",
    value: stMoney(s.put_gex),
    tone: "down",
    tip: "Total exposure from put open interest, counted negative under the standard convention that dealers are net short put gamma."
  }, {
    k: "flip",
    label: "Estimated gamma flip",
    value: p.flip == null ? "None in range" : `$${stNum(p.flip, 2)}`,
    tone: "",
    tip: p.flip == null ? p.reason || "Net exposure does not change sign within the modelled range of spot prices, so there is no flip level to quote." : `The spot price at which net exposure crosses zero, found by re-computing every contract's Black-Scholes gamma across a grid of hypothetical prices — not by cumulatively summing today's per-strike figures, which holds gamma fixed at the one thing that moves. Built from ${p.contracts_used} contracts covering ${stPct(p.covered_oi_pct, 0)} of the open interest.`
  }, {
    k: "top",
    label: "Largest positive strike",
    value: s.largest_positive ? `${stNum(s.largest_positive.strike, 2)}` : "—",
    tone: "up",
    tip: s.largest_positive ? `Strike ${stNum(s.largest_positive.strike, 2)} carries ${stMoney(s.largest_positive.net_gex)} of net exposure — the level where dealer hedging most damps movement.` : "No strike carries net positive exposure on this board."
  }, {
    k: "bot",
    label: "Largest negative strike",
    value: s.largest_negative ? `${stNum(s.largest_negative.strike, 2)}` : "—",
    tone: "down",
    tip: s.largest_negative ? `Strike ${stNum(s.largest_negative.strike, 2)} carries ${stMoney(s.largest_negative.net_gex)} of net exposure — the level where dealer hedging most amplifies movement.` : "No strike carries net negative exposure on this board."
  }];
  return /*#__PURE__*/React.createElement("div", {
    className: "gx-summary"
  }, cards.map(c => /*#__PURE__*/React.createElement("div", {
    key: c.k,
    className: "gx-stat",
    title: c.tip
  }, /*#__PURE__*/React.createElement("span", {
    className: "gx-stat-label"
  }, c.label), /*#__PURE__*/React.createElement("span", {
    className: `gx-stat-value ${c.tone}`
  }, c.value))));
}

// ── the sector market map ────────────────────────────────────────────────
// Squarified treemap: the classic Bruls/Huizing/van Wijk pass, which keeps
// rectangles close to square so their AREAS stay comparable. Laying them out
// in plain order instead produces slivers whose relative size nobody can
// read, which defeats the only thing a treemap is for.
function squarify(items, x, y, w, h) {
  const out = [];
  const total = items.reduce((s, it) => s + Math.max(0, it.value), 0);
  if (!(total > 0) || w <= 0 || h <= 0) return out;
  let list = items.filter(it => it.value > 0).map(it => ({
    ...it,
    area: it.value / total * w * h
  }));
  let cx = x,
    cy = y,
    cw = w,
    ch = h;
  const worst = (row, side) => {
    const sum = row.reduce((s, r) => s + r.area, 0);
    if (!(sum > 0) || !(side > 0)) return Infinity;
    const mx = Math.max(...row.map(r => r.area));
    const mn = Math.min(...row.map(r => r.area));
    return Math.max(side * side * mx / (sum * sum), sum * sum / (side * side * mn));
  };
  const place = row => {
    const sum = row.reduce((s, r) => s + r.area, 0);
    const horizontal = cw >= ch;
    const thickness = horizontal ? sum / ch : sum / cw;
    let off = horizontal ? cy : cx;
    for (const r of row) {
      const len = sum > 0 ? r.area / sum * (horizontal ? ch : cw) : 0;
      out.push(horizontal ? {
        ...r,
        x: cx,
        y: off,
        w: thickness,
        h: len
      } : {
        ...r,
        x: off,
        y: cy,
        w: len,
        h: thickness
      });
      off += len;
    }
    if (horizontal) {
      cx += thickness;
      cw -= thickness;
    } else {
      cy += thickness;
      ch -= thickness;
    }
  };
  let row = [];
  // Bounded by construction — every pass either extends the row or places it
  // and shortens the list, so it cannot spin.
  while (list.length) {
    const side = Math.min(cw, ch);
    const next = list[0];
    if (!row.length || worst([...row, next], side) <= worst(row, side)) {
      row.push(next);
      list = list.slice(1);
    } else {
      place(row);
      row = [];
    }
    if (!list.length && row.length) {
      place(row);
      row = [];
    }
  }
  return out;
}

// Percentage change → colour. Diverging around zero, saturating at 3%,
// which is where an equity move stops being routine.
//
// The mix is toward --map-flat (a grey) and it happens in sRGB. Both halves
// of that matter, and both were got wrong before being got right.
//
// Mixing toward --bg-3, the panel behind the map, rotated every hue: --bg-3
// is a navy at hue ~228 with real chroma, so in oklch a green at 152 came
// out teal and a red at 25 came out purple. Mixing toward oklch(L 0 0) was
// no better — a hue of 0 is not "powerless", it is the red direction, so
// oklch walked 152 toward 0 and a +0.3% gainer rendered ORANGE.
//
// sRGB toward an equal-channel grey cannot rotate a hue: mixing (r,g,b)
// with (k,k,k) scales every channel difference by the same factor, and hue
// is a function of those differences alone.
function mapColour(chg) {
  const v = Math.max(-1, Math.min(1, (chg || 0) / 3));
  const a = 0.14 + Math.abs(v) * 0.68;
  return v >= 0 ? `color-mix(in srgb, var(--up) ${Math.round(a * 100)}%, var(--map-flat))` : `color-mix(in srgb, var(--down) ${Math.round(a * 100)}%, var(--map-flat))`;
}
const MAP_SIZES = [[20, "Fewer", "The twenty largest names in each sector — the biggest rectangles, the most readable."], [40, "Standard", "The forty largest names in each sector."], [80, "More", "The eighty largest names in each sector. Denser; the smallest rectangles carry no label, only a tooltip."]];
function MarketMap({
  apiFetch,
  onOpenTicker
}) {
  const [limit, setLimit] = useState(40);
  const {
    data,
    err,
    busy,
    reload
  } = useStFeed(apiFetch, `/api/market_map?limit=${limit}`, 30000);
  const [w, setW] = useState(960);
  const wrapRef = useRef(null);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => setW(Math.max(280, el.clientWidth)));
    ro.observe(el);
    setW(Math.max(280, el.clientWidth));
    return () => ro.disconnect();
  }, []);

  // Height scales with how many rectangles have to fit. At 520px the forty
  // names in eleven sectors averaged about 34px square, which is under every
  // label threshold — so most of the map rendered as unlabelled blocks and
  // read as missing data rather than as small holdings. Area per rectangle
  // is what decides whether a name can be written in it, so the height is
  // derived from the count instead of being a fixed number.
  const perSector = Math.max(1, data && data.limit_per_sector || limit);
  const sectorCount = Math.max(1, (data && data.sectors || []).length);
  const H = Math.round(Math.max(w < 620 ? 900 : 620, Math.min(2400, perSector * sectorCount * (w < 620 ? 2600 : 2100) / w)));
  const layout = useMemo(() => {
    const secs = data && data.sectors || [];
    if (!secs.length) return [];
    const outer = squarify(secs.map(s => ({
      sector: s,
      value: s.market_cap || 0
    })), 0, 0, w, H);
    const HEAD = 20,
      PAD = 2;
    return outer.map(o => {
      const inner = squarify((o.sector.children || []).map(c => ({
        child: c,
        value: c.market_cap || 0
      })), o.x + PAD, o.y + HEAD, Math.max(0, o.w - PAD * 2), Math.max(0, o.h - HEAD - PAD));
      return {
        ...o,
        inner
      };
    });
  }, [data, w, H]);
  return /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Market map"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Your watchlist by size and today's move"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "Grouped by sector. Rectangle area is market value, colour is the percentage change today. Click any name to load it.")), /*#__PURE__*/React.createElement("div", {
    className: "toolbar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "seg",
    role: "group",
    "aria-label": "How many names per sector"
  }, MAP_SIZES.map(([n, lab, tip]) => /*#__PURE__*/React.createElement("button", {
    key: n,
    className: limit === n ? "active" : "",
    onClick: () => setLimit(n),
    title: tip
  }, lab))), /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    onClick: reload,
    disabled: busy,
    title: "Re-read prices and rebuild the map now."
  }, busy ? "Refreshing…" : "Refresh"))), err ? /*#__PURE__*/React.createElement(StError, {
    error: err,
    onRetry: reload
  }) : null, !data && !err ? /*#__PURE__*/React.createElement(StLoading, {
    label: "the market map"
  }) : null, data && !layout.length && !err ? /*#__PURE__*/React.createElement(StEmpty, null, "Nothing to map yet \u2014 the map needs a market value and today's change for each name, which arrive with the watchlist scan.") : null, /*#__PURE__*/React.createElement("div", {
    className: "gx-mapwrap",
    ref: wrapRef
  }, layout.length ? /*#__PURE__*/React.createElement("svg", {
    viewBox: `0 0 ${w} ${H}`,
    className: "gx-map",
    style: {
      height: H
    },
    role: "img",
    "aria-label": "Treemap of watchlist names grouped by sector, sized by market value and coloured by today's change"
  }, layout.map(o => /*#__PURE__*/React.createElement("g", {
    key: o.sector.etf
  }, /*#__PURE__*/React.createElement("rect", {
    x: o.x,
    y: o.y,
    width: Math.max(0, o.w),
    height: Math.max(0, o.h),
    className: "gx-map-sector"
  }), /*#__PURE__*/React.createElement("text", {
    x: o.x + 6,
    y: o.y + 14,
    className: "gx-map-sectorlabel"
  }, o.w > 90 ? `${o.sector.etf} · ${o.sector.name}` : o.sector.etf, /*#__PURE__*/React.createElement("title", null, `${o.sector.name} (${o.sector.etf}) — ${o.sector.constituents} names, ${stCap(o.sector.market_cap)} of market value shown, median change ${stChg(o.sector.median_change_pct)}.${o.sector.dropped ? ` The ${o.sector.dropped} smallest are not drawn.` : ""}`)), o.inner.map(c => /*#__PURE__*/React.createElement("g", {
    key: c.child.symbol,
    className: "gx-map-cellwrap",
    onClick: () => onOpenTicker && onOpenTicker(c.child.symbol),
    role: "button",
    tabIndex: 0,
    onKeyDown: e => {
      if ((e.key === "Enter" || e.key === " ") && onOpenTicker) onOpenTicker(c.child.symbol);
    },
    "aria-label": `${c.child.symbol}, ${stChg(c.child.change_pct)}`
  }, /*#__PURE__*/React.createElement("rect", {
    x: c.x,
    y: c.y,
    width: Math.max(0, c.w - 1),
    height: Math.max(0, c.h - 1),
    className: "gx-map-cell",
    style: {
      fill: mapColour(c.child.change_pct)
    }
  }), (() => {
    const fs = Math.min(13, Math.max(6, c.w / (c.child.symbol.length * 0.72)));
    if (c.w < fs * c.child.symbol.length * 0.66 || c.h < fs * 1.5) return null;
    const room = c.h > fs * 2.9;
    return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("text", {
      x: c.x + c.w / 2,
      y: c.y + c.h / 2 + (room ? -1 : fs * 0.36),
      className: "gx-map-sym",
      style: {
        fontSize: `${fs}px`
      },
      textAnchor: "middle"
    }, c.child.symbol), room ? /*#__PURE__*/React.createElement("text", {
      x: c.x + c.w / 2,
      y: c.y + c.h / 2 + fs * 1.15,
      className: "gx-map-chg",
      style: {
        fontSize: `${Math.max(5.5, fs * 0.8)}px`
      },
      textAnchor: "middle"
    }, stChg(c.child.change_pct, 1)) : null);
  })(), /*#__PURE__*/React.createElement("title", null, `${c.child.symbol} — ${c.child.company || "name unavailable"}
${c.child.price == null ? "Price unavailable" : `$${stNum(c.child.price, 2)}`} · ${stChg(c.child.change_pct)} today
Market value ${stCap(c.child.market_cap)}
Daily candle ${c.child.state_d || "—"} · weekly ${c.child.state_w || "—"}`)))))) : null), data && data.sizing_note ? /*#__PURE__*/React.createElement("div", {
    className: "st-note muted"
  }, data.sizing_note) : null, data && (data.sectors || []).some(s => s.dropped) ? /*#__PURE__*/React.createElement("div", {
    className: "st-note muted",
    title: "A treemap with a thousand rectangles is a texture, not a chart. The tail is trimmed rather than rendered sub-pixel, and the count is shown so nothing is hidden silently."
  }, "The ", data.limit_per_sector, " largest names in each sector are drawn;", " ", (data.sectors || []).reduce((s, x) => s + (x.dropped || 0), 0), " smaller names are not.") : null);
}
function GexTab({
  apiFetch,
  ticker,
  onOpenTicker
}) {
  const [symbol, setSymbol] = useState((ticker || "SPY").toUpperCase());
  const [input, setInput] = useState((ticker || "SPY").toUpperCase());
  const [exp, setExp] = useState("");
  const [mode, setMode] = useState("gex");
  const url = `/api/gex?symbol=${encodeURIComponent(symbol)}${exp ? `&expiration=${encodeURIComponent(exp)}` : ""}`;
  const {
    data,
    err,
    busy,
    reload
  } = useStFeed(apiFetch, url, 60000);

  // A new underlying invalidates the expiry: last week's Friday does not
  // exist on every chain, and asking for it silently returns the nearest.
  useEffect(() => {
    setExp("");
  }, [symbol]);

  // Follow the app's ticker. Changing the symbol anywhere else — the
  // sidebar, a preset, a click-through from the market map — moves this tab
  // with it, the way every other per-symbol tab behaves. Typing a symbol
  // into the box here is still a local override until the app's ticker
  // changes again.
  useEffect(() => {
    const t = String(ticker || "").trim().toUpperCase();
    if (!t) return;
    setSymbol(t);
    setInput(t);
  }, [ticker]);
  const submit = e => {
    if (e && e.preventDefault) e.preventDefault();
    const s = String(input || "").trim().toUpperCase();
    if (s) setSymbol(s);
  };
  const exps = data && data.available_expirations || [];
  const expCap = data && data.max_expirations || 8;
  const fixture = data && data.source === "fixture";
  return /*#__PURE__*/React.createElement("div", {
    className: "col-list st-tab st-tab-gex"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "Gamma exposure"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Where dealer hedging is concentrated"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "Open interest times gamma, by strike. Positive exposure damps movement; negative exposure amplifies it.")), /*#__PURE__*/React.createElement("div", {
    className: "toolbar gx-tools"
  }, /*#__PURE__*/React.createElement("form", {
    onSubmit: submit,
    className: "gx-symform"
  }, /*#__PURE__*/React.createElement("input", {
    className: "st-search gx-sym",
    value: input,
    onChange: e => setInput(e.target.value.toUpperCase()),
    "aria-label": "Underlying symbol",
    placeholder: "Symbol",
    title: "Type a ticker and press Enter to load its option chain."
  }), /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    type: "submit",
    title: "Load this symbol's gamma exposure."
  }, "Go")), /*#__PURE__*/React.createElement("div", {
    className: "seg",
    role: "group",
    "aria-label": "Quick symbols"
  }, GEX_QUICK.map(s => /*#__PURE__*/React.createElement("button", {
    key: s,
    className: symbol === s ? "active" : "",
    onClick: () => {
      setSymbol(s);
      setInput(s);
    },
    title: `Load ${s}.`
  }, s))), /*#__PURE__*/React.createElement("select", {
    value: exp,
    onChange: e => setExp(e.target.value),
    "aria-label": "Expiration",
    className: "gx-exp",
    title: `Which expiration to measure. Nearest is the default. The multi-expiration option sums the nearest ${expCap} rather than every one listed — an index can list sixty expirations, and asking the broker for all of them at once returns nothing at all.`
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "Nearest expiration"), /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Nearest ", expCap, " expirations"), exps.map(e2 => /*#__PURE__*/React.createElement("option", {
    key: e2,
    value: e2
  }, stDate(e2)))), /*#__PURE__*/React.createElement("div", {
    className: "seg",
    role: "group",
    "aria-label": "What the bars show"
  }, /*#__PURE__*/React.createElement("button", {
    className: mode === "gex" ? "active" : "",
    onClick: () => setMode("gex"),
    title: "Draw net gamma exposure per strike, in dollars of dealer delta per 1% move."
  }, "Exposure"), /*#__PURE__*/React.createElement("button", {
    className: mode === "oi" ? "active" : "",
    onClick: () => setMode("oi"),
    title: "Draw raw open interest per strike, calls plus puts."
  }, "Open interest")), /*#__PURE__*/React.createElement("button", {
    className: "research-run-btn",
    onClick: reload,
    disabled: busy,
    title: "Re-fetch the chain now."
  }, busy ? "Loading…" : "Refresh"))), /*#__PURE__*/React.createElement("div", {
    className: "st-status"
  }, /*#__PURE__*/React.createElement("span", {
    className: "st-status-label"
  }, symbol), data && data.spot ? /*#__PURE__*/React.createElement("span", {
    className: "st-status-sep",
    title: "Last traded price of the underlying."
  }, "last $", stNum(data.spot, 2)) : null, data && data.selected_expirations ? /*#__PURE__*/React.createElement("span", {
    className: "st-status-sep",
    title: "The expirations these figures sum over."
  }, data.selected_expirations.length === 1 ? stDate(data.selected_expirations[0]) : `${data.selected_expirations.length} expirations`) : null, data && data.fetched_at ? /*#__PURE__*/React.createElement("span", {
    className: "st-status-sep",
    title: "When this chain was pulled from the broker. Open interest itself is settled overnight, so it is a day behind by construction."
  }, "chain fetched ", stTime(data.fetched_at)) : null, data && data.source ? /*#__PURE__*/React.createElement("span", {
    className: `st-source st-source-${data.source}`,
    title: fixture ? "This chain is SYNTHETIC development data, not a broker quote." : "Live option chain from the connected broker."
  }, fixture ? "development fixture" : data.source) : null), fixture ? /*#__PURE__*/React.createElement("div", {
    className: "gx-fixture-banner",
    role: "alert"
  }, /*#__PURE__*/React.createElement("b", null, "These are not real quotes."), " Every figure on this screen was generated from a synthetic chain for development. Nothing here reflects a traded market.") : null), err ? /*#__PURE__*/React.createElement(StError, {
    error: err,
    onRetry: reload
  }) : null, !data && !err ? /*#__PURE__*/React.createElement(StLoading, {
    label: "the option chain"
  }) : null, data && !data.ok ? /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement(StEmpty, null, data.error || "No gamma exposure could be built for this symbol.")) : null, data && data.ok ? /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement(GexSummary, {
    data: data
  }), /*#__PURE__*/React.createElement("div", {
    className: "gx-convention",
    title: "Dealer positioning is not published by anyone. Every gamma exposure figure \u2014 here or anywhere \u2014 is open interest times gamma times an assumption about who is on which side."
  }, data.convention), data.profile && data.profile.covered_oi_pct != null && data.profile.covered_oi_pct < 90 ? /*#__PURE__*/React.createElement("div", {
    className: "st-note muted",
    title: "Contracts without an implied volatility or a usable expiration cannot have their gamma re-computed at a hypothetical price, so they are excluded from the flip estimate. They still count in the per-strike totals."
  }, "The flip estimate covers ", stPct(data.profile.covered_oi_pct, 0), " of the open interest \u2014 ", data.profile.contracts_skipped, " contracts were missing an implied volatility or an expiration.") : null), /*#__PURE__*/React.createElement("div", {
    className: "card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", {
    className: "kicker"
  }, "By strike"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, mode === "oi" ? "Open interest at each strike" : "Net exposure at each strike"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, data.strikes.length, " strikes", data.strikes_outside_window ? `, ${data.strikes_outside_window} further out are not shown` : "", data.strike_window_pct ? ` — the ladder is trimmed to ${stPct(data.strike_window_pct, 0)} either side of the last price` : "", "."))), /*#__PURE__*/React.createElement("div", {
    className: "gx-barscroll"
  }, /*#__PURE__*/React.createElement(GexBars, {
    strikes: data.strikes,
    spot: data.spot,
    flip: (data.profile || {}).flip,
    mode: mode
  })))) : null, /*#__PURE__*/React.createElement(MarketMap, {
    apiFetch: apiFetch,
    onOpenTicker: onOpenTicker
  }));
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, {
  SectorsTab: React.memo(SectorsTab),
  MarketContextTab: React.memo(MarketContextTab),
  GexTab: React.memo(GexTab)
});
})();
