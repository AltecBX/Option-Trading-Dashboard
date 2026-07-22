(function () {
// tab-earnops.jsx — LAZY CHUNK (v3.64), split from app-cards.jsx.
// Earnings Opportunities scanner; loaded on first Earnings-Ops-tab open.

// ═══════════════════════════════════════════════════════════════════════════
// EARNINGS OPPORTUNITIES TAB (v3.63) — Market-Chameleon-style earnings
// opportunity scanner on our own providers. NOT a calendar: every watchlist
// name reporting −4…+8 days gets scored 0–100, classified into a setup /
// status / best action, and given an explainable trade plan or an explicit
// NO TRADE. Data: /api/earnings_scan (board pattern). Demo rows are labeled.
// ═══════════════════════════════════════════════════════════════════════════

const EOP_SECTIONS = [["all", "All"], ["today", "Today"], ["pre", "Pre-earnings"], ["post", "Post movers"], ["premium", "Premium ops"], ["waiting", "Waiting confirm"], ["extended", "Extended"], ["no_trade", "No trade"]];
const EOP_ACTION = {
  watch: ["WATCH", "mut"],
  enter_on_confirmation: ["ENTER ON CONFIRM", "mut"],
  confirmed_entry: ["CONFIRMED", "up"],
  sell_premium: ["SELL PREMIUM", "up"],
  already_extended: ["EXTENDED", "down"],
  avoid: ["AVOID", "down"],
  no_trade: ["NO TRADE", "mut"]
};
const EOP_SETUP_LABEL = {
  high_premium: "High premium",
  cheap_implied: "Cheap implied",
  put_selling: "Put selling",
  covered_call: "Covered call",
  post_earnings_continuation: "Continuation",
  post_earnings_reversal: "Reversal",
  gap_and_go: "Gap & go",
  gap_fill: "Gap fill",
  vwap_reclaim: "VWAP reclaim",
  vwap_rejection: "VWAP reject",
  breakout: "Breakout",
  breakdown: "Breakdown",
  pre_earnings_momentum: "Pre-E momentum",
  pre_earnings_fade: "Pre-E fade",
  short_candidate: "Short",
  no_trade: "No trade"
};
function eopMcap(v) {
  if (v == null) return "—";
  return v >= 1e12 ? (v / 1e12).toFixed(1) + "T" : v >= 1e9 ? (v / 1e9).toFixed(1) + "B" : (v / 1e6).toFixed(0) + "M";
}
function eopPct(v, d = 1) {
  return v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}%`;
}
function EarnOpsAlerts({
  rows
}) {
  // Toasts for new row alerts (spec list computed server-side per row).
  const [toasts, setToasts] = useState([]);
  const seenKey = () => "jerry_eop_seen_" + new Date().toISOString().slice(0, 10);
  useEffect(() => {
    if (!rows || !rows.length) return;
    let seen;
    try {
      seen = new Set(JSON.parse(localStorage.getItem(seenKey())) || []);
    } catch (e) {
      seen = new Set();
    }
    const fresh = [];
    for (const r of rows) {
      for (const a of r.alerts || []) {
        const k = `${r.ticker}|${a}`;
        if (!seen.has(k)) {
          fresh.push({
            id: k + Date.now(),
            sym: r.ticker,
            msg: a
          });
          seen.add(k);
        }
      }
    }
    try {
      localStorage.setItem(seenKey(), JSON.stringify([...seen]));
    } catch (e) {}
    if (fresh.length) setToasts(ts => [...ts, ...fresh.slice(0, 3)].slice(-3));
  }, [rows]);
  useEffect(() => {
    if (!toasts.length) return undefined;
    const id = setTimeout(() => setToasts(ts => ts.slice(1)), 12000);
    return () => clearTimeout(id);
  }, [toasts]);
  if (!toasts.length) return null;
  return /*#__PURE__*/React.createElement("div", {
    className: "toast-stack",
    "aria-live": "polite"
  }, toasts.map(t => /*#__PURE__*/React.createElement("button", {
    key: t.id,
    className: "toast toast-radar toast-long",
    onClick: () => setToasts(ts => ts.filter(x => x.id !== t.id))
  }, /*#__PURE__*/React.createElement("span", {
    className: "toast-ico"
  }, "\u25E7"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("b", null, t.sym), " \u2014 ", t.msg))));
}
function EarnOpsRow({
  r,
  open,
  onToggle,
  onOpenTicker,
  onOpenIntraday,
  demo
}) {
  const act = EOP_ACTION[r.action] || [r.action, "mut"];
  const em = r.implied,
    hist = r.hist,
    ave = r.actual_vs_expected,
    ivh = r.iv_vs_hist;
  const plan = r.plan,
    sd = r.score_detail || {};
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("tr", {
    className: `eop-row ${open ? "open" : ""}`,
    onClick: onToggle,
    title: "Click to expand the score breakdown and trade plan."
  }, /*#__PURE__*/React.createElement("td", {
    className: "eop-tk"
  }, /*#__PURE__*/React.createElement("b", null, r.ticker), demo || r.demo ? /*#__PURE__*/React.createElement("span", {
    className: "eop-demo"
  }, "DEMO") : null, /*#__PURE__*/React.createElement("div", {
    className: "eop-co"
  }, r.company || "")), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("div", {
    className: "eop-scorebar",
    title: `Opportunity score ${r.score}/100`
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      width: `${r.score}%`
    },
    className: r.score >= 65 ? "hi" : r.score >= 45 ? "mid" : "lo"
  }), /*#__PURE__*/React.createElement("b", {
    className: "num"
  }, r.score))), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("span", {
    className: `tsy-pill ${act[1]}`
  }, act[0])), /*#__PURE__*/React.createElement("td", {
    className: "eop-setup"
  }, EOP_SETUP_LABEL[r.setup] || r.setup, /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, (r.status || "").replace(/_/g, " "))), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.report_date ? r.report_date.slice(5) : "—", /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, r.timing || "—", r.days_to != null ? ` · ${r.days_to === 0 ? "today" : r.days_to > 0 ? `in ${r.days_to}d` : `${-r.days_to}d ago`}` : "")), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.price != null ? fmt$(r.price, r.price >= 1000 ? 0 : 2) : "—"), /*#__PURE__*/React.createElement("td", {
    className: `num ${r.change_pct != null ? r.change_pct >= 0 ? "cu" : "cd" : ""}`
  }, eopPct(r.change_pct)), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.rel_volume != null ? r.rel_volume.toFixed(1) + "×" : "—"), /*#__PURE__*/React.createElement("td", {
    className: "num",
    title: em ? `Expected move ±${em.pct}% (±$${em.dollars}) → range ${em.lower}–${em.upper}` : "Implied move unavailable"
  }, em ? `±${em.pct}%` : "—", /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, em ? `$${em.lower}–${em.upper}` : "")), /*#__PURE__*/React.createElement("td", {
    className: "num",
    title: hist ? `Historical earnings reactions (n=${hist.n}): avg |${hist.avg_abs}|%, median |${hist.med_abs}|%, last ${hist.last >= 0 ? "+" : ""}${hist.last}%` : "Needs ≥3 past reactions"
  }, hist ? `${hist.avg_abs}%` : "—", /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, hist ? `med ${hist.med_abs} · last ${hist.last >= 0 ? "+" : ""}${hist.last}` : "")), /*#__PURE__*/React.createElement("td", {
    title: ivh ? `Implied ${ivh.ratio}× the historical average move` : ""
  }, ivh ? /*#__PURE__*/React.createElement("span", {
    className: `tsy-pill ${ivh.label === "rich" ? "down" : ivh.label === "cheap" ? "up" : "mut"}`
  }, ivh.label.toUpperCase(), " ", ivh.ratio, "\xD7") : "—"), /*#__PURE__*/React.createElement("td", {
    title: ave ? `|actual| ${ave.actual}% vs expected ${ave.expected}% — basis: ${ave.basis}` : "Not reported yet (or no basis)"
  }, ave ? /*#__PURE__*/React.createElement("span", {
    className: `tsy-pill ${ave.label === "exceeded" ? "up" : ave.label === "undershot" ? "down" : "mut"}`
  }, ave.label.toUpperCase(), " ", ave.ratio, "\xD7") : "—"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.options_volume != null ? (r.options_volume / 1000).toFixed(0) + "k" : "—", /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, "OI ", r.open_interest != null ? (r.open_interest / 1000).toFixed(0) + "k" : "—")), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, r.atm_iv != null ? r.atm_iv.toFixed(0) : "—", /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, r.spread ? `spr ${r.spread.label}` : "")), /*#__PURE__*/React.createElement("td", null, r.weekly_options === true ? "✓" : r.weekly_options === false ? "—" : "?"), /*#__PURE__*/React.createElement("td", {
    className: "num"
  }, eopMcap(r.market_cap), /*#__PURE__*/React.createElement("div", {
    className: "eop-sub"
  }, r.sector || ""))), open && /*#__PURE__*/React.createElement("tr", {
    className: "eop-detail"
  }, /*#__PURE__*/React.createElement("td", {
    colSpan: "16"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eop-dgrid"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("em", null, "WHY IT RANKS (", r.score, "/100)"), (sd.reasons || []).map((x, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "eop-li up"
  }, "\u25B8 ", x)), /*#__PURE__*/React.createElement("em", {
    style: {
      marginTop: 8
    }
  }, "RISKS"), (sd.risks || []).length ? (sd.risks || []).map((x, i) => /*#__PURE__*/React.createElement("div", {
    key: i,
    className: "eop-li down"
  }, "\u25B8 ", x)) : /*#__PURE__*/React.createElement("div", {
    className: "eop-li"
  }, "\u2014"), /*#__PURE__*/React.createElement("em", {
    style: {
      marginTop: 8
    }
  }, "CONFIRMS / INVALIDATES"), /*#__PURE__*/React.createElement("div", {
    className: "eop-li"
  }, "confirm: ", r.confirm_text || "—"), /*#__PURE__*/React.createElement("div", {
    className: "eop-li"
  }, "invalidate: ", r.invalidate_text || "—")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("em", null, "TRADE PLAN"), plan ? /*#__PURE__*/React.createElement("div", {
    className: "eop-plan num"
  }, /*#__PURE__*/React.createElement("span", null, "bias ", /*#__PURE__*/React.createElement("b", {
    className: plan.bias === "long" ? "cu" : plan.bias === "short" ? "cd" : ""
  }, plan.bias)), plan.entry != null && /*#__PURE__*/React.createElement("span", null, "entry ", /*#__PURE__*/React.createElement("b", null, plan.entry)), plan.confirmation != null && /*#__PURE__*/React.createElement("span", null, "confirm ", /*#__PURE__*/React.createElement("b", null, plan.confirmation)), plan.max_chase != null && /*#__PURE__*/React.createElement("span", null, "max chase ", /*#__PURE__*/React.createElement("b", null, plan.max_chase)), plan.invalidation != null && /*#__PURE__*/React.createElement("span", null, "stop ", /*#__PURE__*/React.createElement("b", null, plan.invalidation)), plan.target1 != null && /*#__PURE__*/React.createElement("span", null, "T1 ", /*#__PURE__*/React.createElement("b", null, plan.target1)), plan.target2 != null && /*#__PURE__*/React.createElement("span", null, "T2 ", /*#__PURE__*/React.createElement("b", null, plan.target2)), plan.rr != null && /*#__PURE__*/React.createElement("span", null, "R:R ", /*#__PURE__*/React.createElement("b", null, plan.rr)), /*#__PURE__*/React.createElement("span", null, "hold ", /*#__PURE__*/React.createElement("b", null, plan.holding)), plan.note && /*#__PURE__*/React.createElement("span", {
    className: "eop-plannote"
  }, plan.note)) : /*#__PURE__*/React.createElement("div", {
    className: "eop-li"
  }, "No actionable plan \u2014 ", r.action === "no_trade" ? "explicit NO TRADE" : "not confirmed yet", "."), /*#__PURE__*/React.createElement("em", {
    style: {
      marginTop: 8
    }
  }, "PAST EARNINGS REACTIONS"), /*#__PURE__*/React.createElement("div", {
    className: "eop-hist num"
  }, hist && hist.moves ? hist.moves.map((m, i) => /*#__PURE__*/React.createElement("span", {
    key: i,
    className: m >= 0 ? "cu" : "cd"
  }, m >= 0 ? "+" : "", m)) : "—"), /*#__PURE__*/React.createElement("em", {
    style: {
      marginTop: 8
    }
  }, "LEVELS"), /*#__PURE__*/React.createElement("div", {
    className: "eop-plan num"
  }, r.prev_high != null && /*#__PURE__*/React.createElement("span", null, "PDH ", /*#__PURE__*/React.createElement("b", null, r.prev_high)), r.prev_low != null && /*#__PURE__*/React.createElement("span", null, "PDL ", /*#__PURE__*/React.createElement("b", null, r.prev_low)), r.pm_high != null && /*#__PURE__*/React.createElement("span", null, "PM H ", /*#__PURE__*/React.createElement("b", null, r.pm_high)), r.pm_low != null && /*#__PURE__*/React.createElement("span", null, "PM L ", /*#__PURE__*/React.createElement("b", null, r.pm_low)), r.or_high != null && /*#__PURE__*/React.createElement("span", null, "OR H ", /*#__PURE__*/React.createElement("b", null, r.or_high)), r.or_low != null && /*#__PURE__*/React.createElement("span", null, "OR L ", /*#__PURE__*/React.createElement("b", null, r.or_low)), r.vwap && /*#__PURE__*/React.createElement("span", null, "VWAP ", /*#__PURE__*/React.createElement("b", null, r.vwap.vwap), " (", r.vwap.above ? "above" : "below", r.vwap.event ? ` · ${r.vwap.event}` : "", ")"), r.gap && /*#__PURE__*/React.createElement("span", null, "gap ", /*#__PURE__*/React.createElement("b", null, eopPct(r.gap.gap_pct)), " fill @ ", /*#__PURE__*/React.createElement("b", null, r.gap.fill_level), r.gap.filled != null ? r.gap.filled ? " (filled)" : " (open)" : ""), r.day_high != null && /*#__PURE__*/React.createElement("span", null, "post-E H/L ", /*#__PURE__*/React.createElement("b", null, r.day_high, "/", r.day_low))), /*#__PURE__*/React.createElement("div", {
    className: "eop-actions"
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "scan-run-btn",
    onClick: e => {
      e.stopPropagation();
      onOpenIntraday && onOpenIntraday(r.ticker);
    }
  }, "Intraday chart (VWAP + levels)"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "scan-run-btn",
    onClick: e => {
      e.stopPropagation();
      onOpenTicker && onOpenTicker(r.ticker);
    }
  }, "Open in Analyze (chart + EM + chain)")))))));
}
function EarningsOpsTab({
  apiFetch,
  onOpenTicker,
  onOpenIntraday
}) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [section, setSection] = useState("all");
  const [sortK, setSortK] = useState("score");
  const [sortD, setSortD] = useState(-1);
  const [openTk, setOpenTk] = useState(null);
  const [flt, setFlt] = useState({
    window: "all",
    timing: "all",
    watchOnly: false,
    weeklyOnly: false,
    hiRelVol: false,
    hiOptVol: false,
    hiIV: false,
    largeCap: false,
    confirmedOnly: false,
    hideNoTrade: true
  });
  const pollRef = useRef(null);
  const load = async () => {
    try {
      const r = await apiFetch("/api/earnings_scan");
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
      if (!d || !d.status || !d.status.scanning) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 4000);
  };
  useEffect(() => {
    load().then(d => {
      if (!d) return;
      if (d.status && d.status.scanning) {
        watchScan();
        return;
      }
      const age = d.status && d.status.last_scan ? Date.now() - new Date(d.status.last_scan).getTime() : Infinity;
      if (age > 30 * 60000) {
        apiFetch("/api/earnings_scan/scan").catch(() => {});
        watchScan();
      }
    });
    return () => pollRef.current && clearInterval(pollRef.current);
  }, []);
  const rescan = async () => {
    setErr(null);
    try {
      await apiFetch("/api/earnings_scan/scan?force=1");
    } catch (e) {
      setErr(String(e));
      return;
    }
    await load();
    watchScan();
  };
  const status = board && board.status || {};
  const allRows = board && board.rows || [];
  const demo = !!(board && board.demo);
  const filtered = useMemo(() => allRows.filter(r => {
    if (section !== "all" && r.bucket !== section) return false;
    const d = r.days_to;
    if (flt.window === "today" && d !== 0) return false;
    if (flt.window === "tomorrow" && d !== 1) return false;
    if (flt.window === "week" && !(d != null && d >= 0 && d <= 5)) return false;
    if (flt.window === "reported" && !r.reported_recently) return false;
    if (flt.timing === "bmo" && r.timing !== "BMO") return false;
    if (flt.timing === "amc" && r.timing !== "AMC") return false;
    if (flt.weeklyOnly && r.weekly_options !== true) return false;
    if (flt.hiRelVol && !(r.rel_volume != null && r.rel_volume >= 1.5)) return false;
    if (flt.hiOptVol && !(r.options_volume != null && r.options_volume >= 5000)) return false;
    if (flt.hiIV && !(r.atm_iv != null && r.atm_iv >= 60)) return false;
    if (flt.largeCap && !(r.market_cap != null && r.market_cap >= 10e9)) return false;
    if (flt.confirmedOnly && !(r.status === "confirmed_long" || r.status === "confirmed_short")) return false;
    if (flt.hideNoTrade && r.status === "no_trade" && section !== "no_trade") return false;
    return true;
  }), [allRows, section, flt]);
  const sorted = useMemo(() => {
    const key = r => {
      switch (sortK) {
        case "ticker":
          return r.ticker || "";
        case "report":
          return r.report_date || "9999";
        case "price":
          return r.price ?? -1;
        case "chg":
          return r.change_pct ?? -999;
        case "relvol":
          return r.rel_volume ?? -1;
        case "im":
          return (r.implied && r.implied.pct) ?? -1;
        case "hist":
          return (r.hist && r.hist.avg_abs) ?? -1;
        case "ivh":
          return (r.iv_vs_hist && r.iv_vs_hist.ratio) ?? -1;
        case "ave":
          return (r.actual_vs_expected && r.actual_vs_expected.ratio) ?? -1;
        case "optvol":
          return r.options_volume ?? -1;
        case "iv":
          return r.atm_iv ?? -1;
        case "mcap":
          return r.market_cap ?? -1;
        default:
          return r.score ?? 0;
      }
    };
    return [...filtered].sort((a, b) => {
      const ka = key(a),
        kb = key(b);
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * -sortD;
    });
  }, [filtered, sortK, sortD]);
  const th = (label, k, tip) => /*#__PURE__*/React.createElement("th", {
    className: sortK === k ? "on" : "",
    title: tip || `Sort by ${label}`,
    onClick: () => {
      if (sortK === k) setSortD(d => -d);else {
        setSortK(k);
        setSortD(-1);
      }
    }
  }, label, sortK === k ? sortD === -1 ? " ↓" : " ↑" : "");
  const chip = (k, label) => /*#__PURE__*/React.createElement("button", {
    key: k,
    type: "button",
    className: `tsy-serbtn ${flt[k] ? "on" : ""}`,
    onClick: () => setFlt(f => ({
      ...f,
      [k]: !f[k]
    }))
  }, label);
  const counts = useMemo(() => {
    const c = {};
    for (const r of allRows) c[r.bucket] = (c[r.bucket] || 0) + 1;
    c.all = allRows.length;
    return c;
  }, [allRows]);
  return /*#__PURE__*/React.createElement("div", {
    className: "eop"
  }, /*#__PURE__*/React.createElement(EarnOpsAlerts, {
    rows: allRows
  }), /*#__PURE__*/React.createElement("div", {
    className: "card tsy-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "kicker"
  }, "Earnings opportunity scanner \xB7 your watchlist \xB7 \u22124 to +8 days"), /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Earnings Opportunities")), /*#__PURE__*/React.createElement("div", {
    className: "tsy-ctrl"
  }, board && board.spy_chg != null && /*#__PURE__*/React.createElement("span", {
    className: "tsy-datechip num",
    title: "SPY day change \u2014 market alignment input to the score."
  }, "SPY ", eopPct(board.spy_chg, 2)), /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    onClick: rescan,
    disabled: !!status.scanning
  }, status.scanning ? `Scanning… ${status.scanned || 0}/${status.total || 0}` : "Rescan"))), demo && /*#__PURE__*/React.createElement("div", {
    className: "eop-demobar"
  }, "DEMO DATA \u2014 live providers unavailable; rows are seeded examples so the workflow stays testable. Nothing here is a real quote."), err && /*#__PURE__*/React.createElement("div", {
    className: "tsy-err"
  }, err), status.last_scan && /*#__PURE__*/React.createElement("div", {
    className: "ab-status"
  }, "Last scan ", new Date(status.last_scan).toLocaleString(), " \xB7 ", allRows.length, " candidates", status.error ? ` · ${status.error}` : ""), /*#__PURE__*/React.createElement("div", {
    className: "eop-sections"
  }, EOP_SECTIONS.map(([k, label]) => /*#__PURE__*/React.createElement("button", {
    key: k,
    type: "button",
    className: `eop-sec ${section === k ? "on" : ""}`,
    onClick: () => setSection(k)
  }, label, " ", /*#__PURE__*/React.createElement("b", null, counts[k] || 0)))), /*#__PURE__*/React.createElement("div", {
    className: "tsy-ctrl eop-filters"
  }, /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: flt.window,
    onChange: e => setFlt(f => ({
      ...f,
      window: e.target.value
    }))
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "Any date"), /*#__PURE__*/React.createElement("option", {
    value: "today"
  }, "Today"), /*#__PURE__*/React.createElement("option", {
    value: "tomorrow"
  }, "Tomorrow"), /*#__PURE__*/React.createElement("option", {
    value: "week"
  }, "This week"), /*#__PURE__*/React.createElement("option", {
    value: "reported"
  }, "Recently reported")), /*#__PURE__*/React.createElement("select", {
    className: "sb-select",
    value: flt.timing,
    onChange: e => setFlt(f => ({
      ...f,
      timing: e.target.value
    }))
  }, /*#__PURE__*/React.createElement("option", {
    value: "all"
  }, "BMO + AMC"), /*#__PURE__*/React.createElement("option", {
    value: "bmo"
  }, "Before open"), /*#__PURE__*/React.createElement("option", {
    value: "amc"
  }, "After close")), chip("weeklyOnly", "Weeklys"), chip("hiRelVol", "RelVol ≥1.5×"), chip("hiOptVol", "OptVol ≥5k"), chip("hiIV", "IV ≥60"), chip("largeCap", "Large cap"), chip("confirmedOnly", "Confirmed"), chip("hideNoTrade", "Hide no-trade"), /*#__PURE__*/React.createElement("span", {
    className: "muted",
    style: {
      fontSize: 11.5
    }
  }, sorted.length, " shown")), /*#__PURE__*/React.createElement("div", {
    className: "eop-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "eop-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, th("Ticker", "ticker"), th("Score", "score", "Earnings Opportunity Score 0–100 — liquidity, rel volume, options liquidity, weeklys, implied-vs-historical edge, move-vs-expected, confirmation, spread, market alignment, R:R. Click a row for the full breakdown."), /*#__PURE__*/React.createElement("th", null, "Action"), /*#__PURE__*/React.createElement("th", null, "Setup \xB7 status"), th("Report", "report", "Report date + BMO/AMC (from the earnings-dates timestamp)."), th("Price", "price"), th("Day %", "chg", "Session-aware change (includes pre/after-market when that's the latest print)."), th("RelVol", "relvol", "Today's volume ÷ average volume."), th("Imp move", "im", "ATM straddle mid — the option market's expected move. ± range shown beneath."), th("Hist move", "hist", "This name's own past earnings reactions: average |move|, median, last."), th("Imp/Hist", "ivh", "Implied ÷ historical average — RICH ≥1.3×, CHEAP ≤0.75×."), th("Act/Exp", "ave", "Post-print: |actual| ÷ expected. Basis = pre-print implied recorded by this scanner, else historical average (labeled in tooltip)."), th("OptVol", "optvol", "Front-expiry options volume + open interest."), th("IV", "iv", "ATM implied volatility + ATM spread quality."), /*#__PURE__*/React.createElement("th", {
    title: "Weekly options available"
  }, "Wkly"), th("MCap", "mcap"))), /*#__PURE__*/React.createElement("tbody", null, sorted.map(r => /*#__PURE__*/React.createElement(EarnOpsRow, {
    key: r.ticker,
    r: r,
    demo: demo,
    open: openTk === r.ticker,
    onToggle: () => setOpenTk(openTk === r.ticker ? null : r.ticker),
    onOpenTicker: onOpenTicker,
    onOpenIntraday: onOpenIntraday
  })))), !sorted.length && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, allRows.length ? "Nothing matches the filters." : status.scanning ? "Scanning your watchlist for earnings names…" : "No earnings candidates in the −4…+8 day window. Rescan after the watchlist board refreshes.")), /*#__PURE__*/React.createElement("div", {
    className: "tsy-foot"
  }, board && board.note, " Sources: watchlist board (earnings dates, mcap, sector) \xB7 Schwab quotes/chains/intraday (VWAP, levels) \xB7 yfinance history (past reactions, fallback). EPS consensus/surprise: no free source \u2014 not shown.")));
}
Object.assign(window, {
  EarningsOpsTab: React.memo(EarningsOpsTab)
});
})();
