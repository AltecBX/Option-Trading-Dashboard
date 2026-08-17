(function () {
// tab-gap.jsx — LAZY CHUNK, loaded on first Gap Scan open.
// Premarket Gap Fade & Rebound scanner: which gap ups historically fade,
// which gap downs historically rebound, with measured same-ticker history
// (sample sizes + Wilson intervals) behind every number. The board stays
// compact by design — evidence lives one click away in the detail view.
// Endpoints: GET /api/gap · /api/gap/scan · /api/gap/detail
// · /api/gap/events · /api/gap/backtest · /api/gap/config

const GAP_SIG_TONE = {
  "STRONG FADE": "up",
  "FADE": "up",
  "STRONG REBOUND": "up",
  "REBOUND": "up",
  "MIXED": "warn",
  "HOLD / CONTINUATION RISK": "down",
  "CONTINUATION LOWER RISK": "down",
  "NO DATA": "mut"
};
const gapPct = (v, d = 1) => v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
const gapNum = (v, d = 1) => v == null ? "—" : Number(v).toFixed(d);
// Dates render as "Oct 28, 2026" — house rule: Month Day, Year, never ISO.
const gapDate = s => {
  if (!s) return "—";
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  });
};
const gapWhen = s => {
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

// "82% ·44" — a probability NEVER renders without its sample size.
function GapProb({
  r
}) {
  if (!r || r.p == null) return /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\u2014");
  return /*#__PURE__*/React.createElement("span", {
    title: `${r.k} of ${r.n} events · conservative (Wilson) range ${r.lo}–${r.hi}%`
  }, Math.round(r.p), "%", /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, " \xB7", r.n));
}
function GapSigPill({
  signal,
  held
}) {
  const tone = GAP_SIG_TONE[signal] || "mut";
  return /*#__PURE__*/React.createElement("span", {
    className: `gap-sig gap-sig-${tone}`,
    title: held ? "signal held by hysteresis — the raw signal differs but hasn't persisted long enough to flip the display" : undefined
  }, signal || "—", held ? " ·" : "");
}
function GapQualityDot({
  q
}) {
  const cls = q === "HIGH" ? "hi" : q === "MODERATE" ? "mid" : "lo";
  return /*#__PURE__*/React.createElement("i", {
    className: `gap-qdot gap-qdot-${cls}`,
    title: `Analog quality: ${q || "unknown"} — how similar the historical examples are to today's setup`
  });
}

// ── detail view (§27: enough evidence, no analytics dashboard) ──────────────

function GapDetail({
  apiFetch,
  sym,
  onClose,
  onOpenTicker
}) {
  const [d, setD] = useState(null);
  const [evs, setEvs] = useState(null);
  const [bt, setBt] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let dead = false;
    setD(null);
    setEvs(null);
    setBt(null);
    setErr(null);
    apiFetch(`/api/gap/detail?symbol=${sym}`, {
      noCache: true
    }).then(r => r.json()).then(x => {
      if (!dead) x.row || x.offline ? setD(x) : setErr(x.error || "no data");
    }).catch(e => !dead && setErr(String(e)));
    apiFetch(`/api/gap/events?symbol=${sym}`).then(r => r.json()).then(x => !dead && setEvs(x)).catch(() => {});
    apiFetch(`/api/gap/backtest?symbol=${sym}`).then(r => r.json()).then(x => !dead && setBt(x)).catch(() => {});
    return () => {
      dead = true;
    };
  }, [sym]);
  const r = d && d.row;
  const st = d && d.stats;
  const pm = d && d.pm;
  return /*#__PURE__*/React.createElement("div", {
    className: "card gap-detail"
  }, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, sym, " \xB7 gap evidence", /*#__PURE__*/React.createElement("button", {
    className: "rr-btn gap-back",
    onClick: onClose
  }, "\u2190 board"), /*#__PURE__*/React.createElement("button", {
    className: "rr-btn",
    onClick: () => onOpenTicker && onOpenTicker(sym),
    title: "Open this ticker on the Trade tab."
  }, "Trade tab \u2192")), r && /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, r.population === "EARNINGS" ? "earnings-gap history" : "non-earnings history", " \xB7", " ", r.data_basis, " \xB7 store: ", d.store_meta && d.store_meta.events, " events (", d.store_meta && d.store_meta.minute_scanned, " minute-scanned)"))), err && /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, err), !d && !err && /*#__PURE__*/React.createElement("div", {
    className: "card-loading"
  }, "Loading ", sym, " evidence\u2026"), d && d.offline && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, "Live quote unavailable \u2014 showing stored history only."), r && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead"
  }, "Current setup"), /*#__PURE__*/React.createElement("div", {
    className: "gap-hero"
  }, /*#__PURE__*/React.createElement(GapSigPill, {
    signal: r.signal,
    held: r.signal_held
  }), /*#__PURE__*/React.createElement("div", {
    className: "gap-hero-nums"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Price"), /*#__PURE__*/React.createElement("b", null, "$", gapNum(r.price, 2))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "PM gap"), /*#__PURE__*/React.createElement("b", {
    className: r.pm_gap_pct >= 0 ? "up" : "down"
  }, gapPct(r.pm_gap_pct))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, r.direction === "up" ? "From PM high" : "From PM low"), /*#__PURE__*/React.createElement("b", null, gapPct(r.direction === "up" ? r.from_pm_high_pct : r.from_pm_low_pct))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "PM trend 30m"), /*#__PURE__*/React.createElement("b", null, gapPct(r.trend_30m_pct))), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Catalyst"), /*#__PURE__*/React.createElement("b", null, r.catalyst_kind, r.catalyst_label ? ` · ${r.catalyst_label}` : "")), r.sector && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Sector (", r.sector.etf, ")"), /*#__PURE__*/React.createElement("b", null, gapPct(r.sector.etf_gap_pct), " \xB7 ", r.sector.label)), r.quote_age_s != null && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("span", null, "Quote age"), /*#__PURE__*/React.createElement("b", null, Math.round(r.quote_age_s), "s"))), r.signal_why && /*#__PURE__*/React.createElement("div", {
    className: "gap-why"
  }, r.signal_why), r.what_changed && /*#__PURE__*/React.createElement("div", {
    className: "gap-changed"
  }, "Changed: ", r.what_changed)), st && st.n > 0 && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead"
  }, "What happened after similar gaps", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, " \xB7 n=", st.n, " ", /*#__PURE__*/React.createElement(GapQualityDot, {
    q: r.cohort_quality
  }), r.cohort_scope === "all_same_direction" ? " (widened to all same-direction gaps)" : " (size-matched)")), /*#__PURE__*/React.createElement("div", {
    className: "gap-grid2"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-block"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-bt"
  }, r.direction === "up" ? "Faded at least…" : "Rebounded at least…"), ["1", "2", "3", "5"].map(lv => /*#__PURE__*/React.createElement("div", {
    key: lv,
    className: "gap-prow"
  }, /*#__PURE__*/React.createElement("span", null, lv, "%"), /*#__PURE__*/React.createElement("div", {
    className: "gap-ptrack"
  }, st.p_fav[lv] && /*#__PURE__*/React.createElement("i", {
    style: {
      width: `${st.p_fav[lv].p}%`
    }
  }), st.p_fav[lv] && /*#__PURE__*/React.createElement("em", {
    style: {
      left: `${st.p_fav[lv].lo}%`
    },
    title: `conservative bound ${st.p_fav[lv].lo}%`
  })), /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement(GapProb, {
    r: st.p_fav[lv]
  }))))), /*#__PURE__*/React.createElement("div", {
    className: "gap-block"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-bt"
  }, "Risk first"), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv"
  }, /*#__PURE__*/React.createElement("span", null, "2% target before 3% stop"), /*#__PURE__*/React.createElement("b", null, st.tbs ? /*#__PURE__*/React.createElement(GapProb, {
    r: st.tbs
  }) : /*#__PURE__*/React.createElement("span", {
    className: "muted",
    title: "No minute-path events yet \u2014 daily bars cannot order target vs stop."
  }, "UNKNOWN / DAILY ONLY"))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv"
  }, /*#__PURE__*/React.createElement("span", null, "Median adverse first (MAE)"), /*#__PURE__*/React.createElement("b", null, gapPct(st.mae_med_pct != null ? st.mae_med_pct : st.med_adv_pct))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv"
  }, /*#__PURE__*/React.createElement("span", null, "90th pct adverse"), /*#__PURE__*/React.createElement("b", null, gapPct(st.mae_p90_pct != null ? st.mae_p90_pct : st.adv_p90_pct))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv"
  }, /*#__PURE__*/React.createElement("span", null, "95th pct adverse"), /*#__PURE__*/React.createElement("b", null, gapPct(st.mae_p95_pct != null ? st.mae_p95_pct : st.adv_p95_pct))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv gap-worst"
  }, /*#__PURE__*/React.createElement("span", null, "Worst analog"), /*#__PURE__*/React.createElement("b", null, "moved ", gapPct(st.worst_adv_pct), " against \xB7 ", gapDate(st.worst_adv_date))), st.mae_before_target_med_pct != null && /*#__PURE__*/React.createElement("div", {
    className: "gap-kv"
  }, /*#__PURE__*/React.createElement("span", null, "Median squeeze before target"), /*#__PURE__*/React.createElement("b", null, gapPct(st.mae_before_target_med_pct)))), /*#__PURE__*/React.createElement("div", {
    className: "gap-block"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-bt"
  }, "Timing & tendencies"), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv"
  }, /*#__PURE__*/React.createElement("span", null, "Median time to 2%"), /*#__PURE__*/React.createElement("b", null, st.med_time_to_min && st.med_time_to_min["2"] != null ? `${st.med_time_to_min["2"]} min` : "—")), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv"
  }, /*#__PURE__*/React.createElement("span", null, "Gap filled"), /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement(GapProb, {
    r: st.gap_fill
  }))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv"
  }, /*#__PURE__*/React.createElement("span", null, "Continued ", r.direction === "up" ? "higher" : "lower"), /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement(GapProb, {
    r: st.continuation
  }))), st.ev && st.ev.mean_pct != null && /*#__PURE__*/React.createElement("div", {
    className: "gap-kv"
  }, /*#__PURE__*/React.createElement("span", null, "Empirical EV per trade"), /*#__PURE__*/React.createElement("b", {
    className: st.ev.mean_pct > 0 ? "up" : "down"
  }, gapPct(st.ev.mean_pct, 2))), st.ev && st.ev.basis && /*#__PURE__*/React.createElement("div", {
    className: "gap-note"
  }, st.ev.basis))), /*#__PURE__*/React.createElement("div", {
    className: "gap-note"
  }, "Evidence basis: ", st.basis, st.tbs && st.tbs.intrabar_modeled_share > 0 && ` · ${Math.round(st.tbs.intrabar_modeled_share * 100)}% of orderings INTRABAR MODELED (same-minute ties resolved against the trade)`, " ", "\xB7 probabilities show conservative Wilson ranges on hover")), st && !st.n && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, "No comparable", r.population === "EARNINGS" ? " earnings-gap" : "", " history for this setup \u2014 the store fills as mornings accumulate."), bt && bt.grid && bt.grid.length > 0 && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead"
  }, "Target / stop grid ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 walk-forward, this ticker's measured paths")), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table gap-bt-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Dir"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Take-profit distance from the open."
  }, "Target"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Stop distance. Stops model fill-through, not a fill at the stop price."
  }, "Stop"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num"
  }, "n"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Percent of events where the target printed before the stop."
  }, "Win %"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Mean simulated net return per trade over the actual paths."
  }, "Expectancy"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "First chronological half."
  }, "H1"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Second chronological half. A pair is only trustworthy when both halves are positive."
  }, "H2"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Worst single simulated trade."
  }, "Worst"), /*#__PURE__*/React.createElement("th", {
    title: "Positive expectancy in BOTH halves \u2014 survives walk-forward."
  }, "Robust"))), /*#__PURE__*/React.createElement("tbody", null, bt.grid.slice(0, 10).map((g, i) => /*#__PURE__*/React.createElement("tr", {
    key: i,
    className: g.robust ? "gap-row-robust" : ""
  }, /*#__PURE__*/React.createElement("td", null, g.direction === "up" ? "fade" : "rebound"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.target_pct, "%"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.stop_pct, "%"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.n), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, g.win_rate, "%"), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${g.expectancy_pct > 0 ? "up" : "down"}`
  }, gapPct(g.expectancy_pct, 2)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, gapPct(g.h1_pct, 2)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, gapPct(g.h2_pct, 2)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num down"
  }, gapPct(g.worst_pct, 2)), /*#__PURE__*/React.createElement("td", null, g.robust ? "✓" : "—")))))), /*#__PURE__*/React.createElement("div", {
    className: "gap-note"
  }, bt.note)), evs && evs.events && evs.events.length > 0 && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead"
  }, "The analogs ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 every event behind the numbers")), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table gap-ev-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Date"), /*#__PURE__*/React.createElement("th", null, "Dir"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Official open gap."
  }, "Gap"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Biggest premarket gap that day (where minute data exists)."
  }, "PM max"), /*#__PURE__*/React.createElement("th", {
    title: "OFFICIAL = qualified by the open gap \xB7 PM = reached the premarket threshold (even if it opened small)."
  }, "Via"), /*#__PURE__*/React.createElement("th", null, "Catalyst"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Favorable move from the open (fade for gap ups, rebound for gap downs)."
  }, "Fav"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Adverse move from the open."
  }, "Adv"), /*#__PURE__*/React.createElement("th", {
    title: "MINUTE PATH = real ordering measured \xB7 DAILY ONLY = no ordering claims."
  }, "Basis"))), /*#__PURE__*/React.createElement("tbody", null, evs.events.slice(0, 30).map((e, i) => /*#__PURE__*/React.createElement("tr", {
    key: i,
    className: e.exclusion ? "gap-row-excl" : "",
    title: e.exclusion ? `excluded: ${e.exclusion}` : e.delayed_open ? "delayed open" : ""
  }, /*#__PURE__*/React.createElement("td", null, gapDate(e.date)), /*#__PURE__*/React.createElement("td", null, e.direction === "up" ? "▲" : "▼"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, gapPct(e.official_gap_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, gapPct(e.pm_gap_max_pct)), /*#__PURE__*/React.createElement("td", null, (e.qualified_by || []).join("+")), /*#__PURE__*/React.createElement("td", null, e.catalyst_kind === "EARNINGS" ? /*#__PURE__*/React.createElement("b", null, "EARN") : "—"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num up"
  }, gapPct(e.fav_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num down"
  }, gapPct(e.adv_pct)), /*#__PURE__*/React.createElement("td", {
    className: "muted"
  }, e.exclusion || e.basis)))))))));
}

// ── main tab ────────────────────────────────────────────────────────────────

function GapTab({
  apiFetch,
  onOpenTicker
}) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [gapSym, setGapSym] = useState(null);
  const [sortK, setSortK] = useState("rank");
  const [sortD, setSortD] = useState(1);
  const pollRef = useRef(null);
  const load = async () => {
    try {
      const r = await apiFetch("/api/gap", {
        noCache: true
      });
      const d = await r.json();
      setBoard(d);
      setErr(null);
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
      if (d && !(d.status && d.status.scanning)) {
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
      const age = d.as_of ? Date.now() - new Date(d.as_of).getTime() : Infinity;
      if (!d.rows || !d.rows.length || age > 20 * 60000) {
        apiFetch("/api/gap/scan").catch(() => {});
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
  const sorted = useMemo(() => {
    const key = r => {
      switch (sortK) {
        case "symbol":
          return r.symbol || "";
        case "gap":
          return -Math.abs(r.pm_gap_pct ?? 0);
        case "p2":
          return -((r.p_fav && r.p_fav.p) ?? -1);
        case "tbs":
          return -(r.tbs_p ?? -1);
        case "adv":
          return r.med_adverse_pct ?? 99;
        case "n":
          return -(r.n ?? 0);
        default:
          return 0;
        // rank = server order
      }
    };
    const s = [...rows];
    if (sortK !== "rank") s.sort((a, b) => {
      const ka = key(a),
        kb = key(b);
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * sortD;
    });
    return s;
  }, [rows, sortK, sortD]);
  const [shown, moreControls] = useBoundedList(sorted, 40, 60);
  const th = (label, k, tip, hideMobile) => /*#__PURE__*/React.createElement("th", {
    className: `${k === "symbol" ? "" : "scan-th-num"}${hideMobile ? " gap-hidemobile" : ""}`,
    title: tip,
    onClick: () => {
      if (sortK === k) setSortD(-sortD);else {
        setSortK(k);
        setSortD(1);
      }
    }
  }, label, sortK === k ? sortD === 1 ? " ↓" : " ↑" : "");
  const scanning = board && board.status && board.status.scanning;
  const ctx = board && board.context || {};
  return /*#__PURE__*/React.createElement("div", {
    className: "card gap-card"
  }, gapSym ? /*#__PURE__*/React.createElement(GapDetail, {
    apiFetch: apiFetch,
    sym: gapSym,
    onClose: () => setGapSym(null),
    onOpenTicker: onOpenTicker
  }) : /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "card-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "card-title"
  }, "Gap Scan \xB7 premarket fade & rebound"), /*#__PURE__*/React.createElement("div", {
    className: "card-sub"
  }, "When this stock moved like this before the open, what happened next \u2014 measured history, not \"gaps usually fill\".")), /*#__PURE__*/React.createElement("button", {
    className: "scan-run-btn",
    disabled: !!scanning,
    title: "Re-scan premarket movers now (quote sweep \u2192 minute history \u2192 same-ticker gap statistics).",
    onClick: () => {
      apiFetch("/api/gap/scan?force=1").catch(() => {});
      watchScan();
    }
  }, scanning ? `Scanning ${board.status.scanned}/${board.status.total || "…"}` : "Scan now")), err && /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, err), board && board.status && board.status.error && /*#__PURE__*/React.createElement("div", {
    className: "card-error"
  }, "last scan failed: ", board.status.error), !board && !err && /*#__PURE__*/React.createElement("div", {
    className: "card-loading"
  }, "Loading board\u2026"), board && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-ctxline muted"
  }, board.session === "premarket" ? "premarket" : "market hours", " \xB7 SPY ", gapPct(ctx.spy_gap_pct), " \xB7 QQQ ", gapPct(ctx.qqq_gap_pct), " \xB7 as of ", gapWhen(board.as_of)), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table gap-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, th("Ticker", "symbol", "Premarket mover. Click for the full evidence view."), th("Price", "rank", "Current premarket reference price.", true), th("PM Gap", "gap", "Live premarket gap vs prior regular close. This is NOT the official open gap — it moves until 9:30."), th("Off Hi/Lo", "rank", "Gap ups: % below the premarket high seen so far. Gap downs: % above the premarket low. 'So far' — the final PM high/low doesn't exist yet.", true), th("Catalyst", "rank", "EARNINGS / ANALYST ACTION / MACRO from real data sources; UNTAGGED otherwise. Earnings gaps are judged only against this stock's other earnings gaps.", true), th("P 2%", "p2", "How often this stock's similar gaps faded (gap up) or rebounded (gap down) at least 2% from the open. Always shown with sample size; hover for the conservative range."), th("Tgt<Stop", "tbs", "Probability the 2% target printed BEFORE a 3% stop, from real minute-by-minute paths. Blank = only daily bars, and daily bars cannot order events."), th("Med Adv", "adv", "Median move AGAINST the trade first (MAE). A fade that squeezes +4% before working is not a comfortable short.", true), th("n", "n", "Number of comparable historical events. No n, no probability.", true), th("Signal", "rank", "Evidence-gated: STRONG needs the favorable rate AND target-before-stop AND tail control on the conservative bounds. Never from one probability alone."))), /*#__PURE__*/React.createElement("tbody", null, shown.map(r => /*#__PURE__*/React.createElement("tr", {
    key: r.symbol,
    className: "scan-row gap-row",
    onClick: () => r.data_ok !== false && setGapSym(r.symbol),
    title: r.what_changed || r.signal_why || r.error || ""
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("b", null, r.symbol), " ", /*#__PURE__*/React.createElement(GapQualityDot, {
    q: r.cohort_quality
  })), /*#__PURE__*/React.createElement("td", {
    className: "scan-num gap-hidemobile"
  }, r.price != null ? `$${gapNum(r.price, 2)}` : "—"), /*#__PURE__*/React.createElement("td", {
    className: `scan-num ${(r.pm_gap_pct ?? 0) >= 0 ? "up" : "down"}`
  }, /*#__PURE__*/React.createElement("b", null, gapPct(r.pm_gap_pct))), /*#__PURE__*/React.createElement("td", {
    className: "scan-num gap-hidemobile"
  }, gapPct(r.direction === "up" ? r.from_pm_high_pct : r.from_pm_low_pct)), /*#__PURE__*/React.createElement("td", {
    className: "gap-hidemobile"
  }, r.catalyst_kind === "UNTAGGED" ? /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\u2014") : r.catalyst_kind), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, /*#__PURE__*/React.createElement(GapProb, {
    r: r.p_fav
  })), /*#__PURE__*/React.createElement("td", {
    className: "scan-num"
  }, r.tbs_p != null ? `${Math.round(r.tbs_p)}%` : /*#__PURE__*/React.createElement("span", {
    className: "muted",
    title: "UNKNOWN / DAILY ONLY \u2014 no minute paths yet for this cohort"
  }, "\u2014")), /*#__PURE__*/React.createElement("td", {
    className: "scan-num gap-hidemobile"
  }, gapPct(r.med_adverse_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num gap-hidemobile muted"
  }, r.n ?? 0), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(GapSigPill, {
    signal: r.signal,
    held: r.signal_held
  }))))))), moreControls, !rows.length && !scanning && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, board.as_of ? "No premarket movers past the gap threshold right now — the board fills when stocks actually gap. Auto-scans run every few minutes from 7:00 AM ET." : "No scan yet — hit “Scan now”. Most useful 7:00–9:30 AM ET when premarket movers exist."), board.note && /*#__PURE__*/React.createElement("div", {
    className: "gap-note"
  }, board.note))));
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, {
  GapTab: React.memo(GapTab)
});
})();
