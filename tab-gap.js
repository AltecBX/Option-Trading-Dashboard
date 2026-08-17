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
// Historical rows also carry the weekday — "Apr 8, 2026 (Wed)" — because a
// Monday gap and a Friday gap are not the same animal.
const gapDateDow = s => {
  if (!s) return "—";
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  if (Number.isNaN(d.getTime())) return String(s);
  return `${gapDate(s)} (${d.toLocaleDateString("en-US", {
    weekday: "short"
  })})`;
};
// Catalyst names are spelled out — no insider shorthand in the UI.
const GAP_CATALYST_LABEL = {
  EARNINGS: "Earnings",
  "ANALYST ACTION": "Analyst action",
  MACRO: "Macro event",
  OFFERING: "Offering",
  UNTAGGED: "None tagged"
};
const gapCatalyst = k => GAP_CATALYST_LABEL[k] || k || "None tagged";
const gapTime = s => {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit"
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

// Every historical event, sortable — one click to see the biggest gaps, the
// worst squeezes, or every earnings day grouped together.
function GapAnalogTable({
  events
}) {
  const [k, setK] = useState("date");
  const [dir, setDir] = useState(1);
  const sorted = useMemo(() => {
    const key = e => {
      switch (k) {
        case "gap":
          return -Math.abs(e.official_gap_pct ?? 0);
        case "pmmax":
          return -Math.abs(e.pm_gap_max_pct ?? 0);
        case "dir":
          return e.direction || "";
        case "via":
          return (e.qualified_by || []).join("+");
        case "cat":
          return e.catalyst_kind || "";
        case "fav":
          return -(e.fav_pct ?? -99);
        case "adv":
          return -(e.adv_pct ?? -99);
        case "basis":
          return e.exclusion || e.basis || "";
        default:
          return e.date || "";
      }
    };
    const s = [...(events || [])];
    s.sort((a, b) => {
      const ka = key(a),
        kb = key(b);
      return (ka < kb ? 1 : ka > kb ? -1 : 0) * (k === "date" ? dir : -dir);
    });
    return s;
  }, [events, k, dir]);
  const th = (label, key_, tip, cls) => /*#__PURE__*/React.createElement("th", {
    className: cls,
    title: tip,
    onClick: () => {
      if (k === key_) setDir(-dir);else {
        setK(key_);
        setDir(1);
      }
    }
  }, label, k === key_ ? dir === 1 ? " ↓" : " ↑" : "");
  return /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table gap-ev-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, th("Date", "date", "The historical session this gap happened on, with its weekday — click to sort oldest/newest."), th("Direction", "dir", "Green ▲ = the stock gapped UP that morning (a fade candidate). Red ▼ = it gapped DOWN (a rebound candidate). Click to group the two together."), th("Open gap", "gap", "How far the official 9:30 opening price was from the prior day's close. Click to sort by the biggest gaps.", "scan-th-num"), th("Premarket peak", "pmmax", "The largest gap reached during that day's premarket session, where minute data exists. A dash means we have the daily bars but not that morning's premarket tape.", "scan-th-num"), th("Qualified by", "via", "Why this day is in the database. OFFICIAL = the opening gap alone cleared the threshold. PREMARKET = the stock reached the threshold before the open (even if it opened small — those faded-before-the-open days are exactly what this scanner studies). OFFICIAL+PREMARKET = both."), th("Catalyst", "cat", "Earnings = the company reported that day or the night before. Those days are kept in a separate statistical population and never mixed with ordinary gaps. Click to group all earnings days together."), th("Favorable", "fav", "How far the stock moved in the profitable direction from the open — down for a gap up (the fade), up for a gap down (the rebound).", "scan-th-num"), th("Adverse", "adv", "How far it moved AGAINST the trade from the open before doing anything good. Click to sort by the worst squeezes.", "scan-th-num"), th("Data basis", "basis", "MINUTE PATH = we have that day minute by minute, so target-vs-stop ordering is measured. DAILY ONLY = we know how far it moved but not in what order. An EXCLUDE_ label means the day was thrown out (split, dividend or unreliable data) and contributes to nothing."))), /*#__PURE__*/React.createElement("tbody", null, sorted.map((e, i) => /*#__PURE__*/React.createElement("tr", {
    key: i,
    className: e.exclusion ? "gap-row-excl" : ""
  }, /*#__PURE__*/React.createElement("td", {
    title: e.exclusion ? `Excluded: ${e.exclusion}` : e.delayed_open ? "This session opened late (halt or delayed first print)." : ""
  }, gapDateDow(e.date), e.delayed_open ? " *" : ""), /*#__PURE__*/React.createElement("td", {
    className: e.direction === "up" ? "up" : "down",
    title: e.direction === "up" ? "Gapped up — fade candidate" : "Gapped down — rebound candidate"
  }, e.direction === "up" ? "▲ Up" : "▼ Down"), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: "Official open vs prior close"
  }, gapPct(e.official_gap_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num",
    title: e.pm_gap_max_pct == null ? "No premarket minute data stored for this day" : "Largest premarket gap that morning"
  }, gapPct(e.pm_gap_max_pct)), /*#__PURE__*/React.createElement("td", {
    title: (e.qualified_by || []).includes("PM") ? "Reached the threshold during premarket" : "Qualified on the official opening gap"
  }, (e.qualified_by || []).map(q => q === "PM" ? "Premarket" : "Official").join(" + ") || "—"), /*#__PURE__*/React.createElement("td", {
    title: e.catalyst_kind === "EARNINGS" ? "Earnings day — counted only against other earnings gaps" : "No earnings on or before this session"
  }, e.catalyst_kind === "EARNINGS" ? /*#__PURE__*/React.createElement("b", null, "Earnings") : /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\u2014")), /*#__PURE__*/React.createElement("td", {
    className: "scan-num up",
    title: "Best move in the trade's favor"
  }, gapPct(e.fav_pct)), /*#__PURE__*/React.createElement("td", {
    className: "scan-num down",
    title: "Worst move against the trade"
  }, gapPct(e.adv_pct)), /*#__PURE__*/React.createElement("td", {
    className: "muted gap-basis-cell",
    title: e.exclusion ? "This day is excluded from every statistic" : e.basis === "MINUTE PATH" ? "Measured minute by minute" : "Daily bars only — no ordering claims"
  }, e.exclusion || (e.basis === "MINUTE PATH" ? "Minute path" : "Daily only")))))));
}

// Recent headlines — the "why is it moving" the statistics can't tell you.
function GapNews({
  apiFetch,
  sym
}) {
  const [news, setNews] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let dead = false;
    setNews(null);
    setErr(null);
    apiFetch(`/api/news?symbol=${sym}`).then(r => r.json()).then(x => {
      if (!dead) x.items ? setNews(x) : setErr(x.error || "no news");
    }).catch(e => !dead && setErr(String(e)));
    return () => {
      dead = true;
    };
  }, [sym]);
  const items = news && news.items || [];
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: "Latest headlines for this ticker, newest first. The statistics tell you what usually happens after a gap this size; the news tells you what kind of gap this one is."
  }, "Latest news ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 why it's moving")), err && /*#__PURE__*/React.createElement("div", {
    className: "gap-note"
  }, "news unavailable: ", err), !news && !err && /*#__PURE__*/React.createElement("div", {
    className: "card-loading"
  }, "Loading headlines\u2026"), news && !items.length && /*#__PURE__*/React.createElement("div", {
    className: "research-empty"
  }, "No recent headlines found for ", sym, "."), items.length > 0 && /*#__PURE__*/React.createElement("ul", {
    className: "gap-news"
  }, items.slice(0, 8).map((n, i) => /*#__PURE__*/React.createElement("li", {
    key: i
  }, /*#__PURE__*/React.createElement("span", {
    className: "gap-news-when",
    title: `Published ${n.date_label || ""} ${n.time_label || ""} ET`
  }, n.age || n.date_label || ""), n.url ? /*#__PURE__*/React.createElement("a", {
    href: n.url,
    target: "_blank",
    rel: "noopener noreferrer",
    title: "Open the full story in a new tab"
  }, n.title) : /*#__PURE__*/React.createElement("span", null, n.title), /*#__PURE__*/React.createElement("span", {
    className: "gap-news-src",
    title: "Publisher"
  }, n.source), n.day_change != null && /*#__PURE__*/React.createElement("span", {
    className: `gap-news-chg ${n.day_change >= 0 ? "up" : "down"}`,
    title: "How the stock closed on the day of this headline"
  }, gapPct(n.day_change))))));
}
function GapDetail({
  apiFetch,
  sym,
  onClose,
  onOpenTicker,
  liveQ
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
  // Price and the gap it implies move every second; the statistics behind
  // them are history and cannot. Overlay the live quote on the fetched row.
  const r = d && d.row && (liveQ ? {
    ...d.row,
    ...liveQ
  } : d.row);
  const st = d && d.stats;
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
    className: "gap-sechead",
    title: "Where this stock stands right now. Prices refresh every 15 seconds."
  }, "Current setup"), /*#__PURE__*/React.createElement("div", {
    className: "gap-hero"
  }, /*#__PURE__*/React.createElement(GapSigPill, {
    signal: r.signal,
    held: r.signal_held
  }), /*#__PURE__*/React.createElement("div", {
    className: "gap-hero-nums"
  }, /*#__PURE__*/React.createElement("div", {
    title: "Current premarket price. Updates every 15 seconds while this tab is open."
  }, /*#__PURE__*/React.createElement("span", null, "Price"), /*#__PURE__*/React.createElement("b", null, "$", gapNum(r.price, 2))), /*#__PURE__*/React.createElement("div", {
    title: "How far the current price is from yesterday's regular-session close. This keeps moving until 9:30 \u2014 it is not the official opening gap."
  }, /*#__PURE__*/React.createElement("span", null, "Premarket gap"), /*#__PURE__*/React.createElement("b", {
    className: r.pm_gap_pct >= 0 ? "up" : "down"
  }, gapPct(r.pm_gap_pct))), /*#__PURE__*/React.createElement("div", {
    title: r.direction === "up" ? "How far the price has pulled back from the highest premarket price seen SO FAR this morning. The final premarket high doesn't exist yet — this is what's known right now." : "How far the price has bounced off the lowest premarket price seen so far this morning."
  }, /*#__PURE__*/React.createElement("span", null, r.direction === "up" ? "Off premarket high" : "Off premarket low"), /*#__PURE__*/React.createElement("b", null, gapPct(r.direction === "up" ? r.from_pm_high_pct : r.from_pm_low_pct))), /*#__PURE__*/React.createElement("div", {
    title: "Which way the premarket tape has drifted over the last 30 minutes. Negative on a gap up means it's already rolling over."
  }, /*#__PURE__*/React.createElement("span", null, "Last 30 min"), /*#__PURE__*/React.createElement("b", null, gapPct(r.trend_30m_pct))), /*#__PURE__*/React.createElement("div", {
    title: "What is known to be driving this move, from real data sources only: Earnings (with reporting time), Analyst action, or a Macro event day. 'None tagged' means no earnings, analyst action or macro event was found \u2014 not that nothing happened."
  }, /*#__PURE__*/React.createElement("span", null, "Catalyst"), /*#__PURE__*/React.createElement("b", null, gapCatalyst(r.catalyst_kind), r.catalyst_label ? ` · ${r.catalyst_label}` : "")), /*#__PURE__*/React.createElement("div", {
    title: r.days_to_earnings == null ? "No scheduled earnings date found for this ticker." : `Next scheduled report: ${gapDateDow(r.next_earnings)}. A fade that has to survive an earnings report is a different trade from one with a clear runway.`
  }, /*#__PURE__*/React.createElement("span", null, "Next earnings"), /*#__PURE__*/React.createElement("b", {
    className: r.days_to_earnings != null && r.days_to_earnings <= 7 ? "down" : ""
  }, r.days_to_earnings == null ? "—" : r.days_to_earnings === 0 ? "today" : r.days_to_earnings < 0 ? gapDate(r.next_earnings) : `${r.days_to_earnings} day${r.days_to_earnings === 1 ? "" : "s"}`), r.next_earnings && r.days_to_earnings > 0 && /*#__PURE__*/React.createElement("small", {
    className: "muted"
  }, gapDate(r.next_earnings))), r.sector && /*#__PURE__*/React.createElement("div", {
    title: `The stock's sector ETF (${r.sector.etf}) gapped ${gapPct(r.sector.etf_gap_pct)} this morning. SECTOR DRIVEN means the whole group is moving together — the stock isn't doing anything special. ISOLATED means this move is its own story.`
  }, /*#__PURE__*/React.createElement("span", null, "Sector (", r.sector.etf, ")"), /*#__PURE__*/React.createElement("b", null, gapPct(r.sector.etf_gap_pct), " \xB7 ", r.sector.label)), r.quote_age_s != null && /*#__PURE__*/React.createElement("div", {
    title: "Seconds since the last actual trade printed. A premarket quote that is minutes old can make a stock look like it's moving when nothing is trading \u2014 signals are blocked past the freshness limit."
  }, /*#__PURE__*/React.createElement("span", null, "Quote age"), /*#__PURE__*/React.createElement("b", null, Math.round(r.quote_age_s), "s"))), r.signal_why && /*#__PURE__*/React.createElement("div", {
    className: "gap-why",
    title: "The evidence behind the signal above, in plain numbers."
  }, r.signal_why), r.what_changed && /*#__PURE__*/React.createElement("div", {
    className: "gap-changed",
    title: "What materially moved since the previous evaluation of this ticker."
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
    className: "gap-bt",
    title: r.direction === "up" ? "Of the comparable historical gap ups, how often the stock dropped at least this far from the opening price. The bar is the rate; the white tick is the conservative (statistically cautious) end of the range." : "Of the comparable historical gap downs, how often the stock rose at least this far from the opening price. The white tick marks the conservative end of the range."
  }, r.direction === "up" ? "Faded at least…" : "Rebounded at least…"), ["1", "2", "3", "5"].map(lv => /*#__PURE__*/React.createElement("div", {
    key: lv,
    className: "gap-prow",
    title: `How often it moved ${lv}% or more in the profitable direction`
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
    className: "gap-bt",
    title: "How much pain came first. A setup that eventually works but squeezes hard against you on the way is not the same trade as one that goes your way immediately."
  }, "Risk first"), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "How often the 2% profit target printed BEFORE a 3% stop would have been hit \u2014 measured minute by minute on the real historical paths. If the two happened inside the same minute, the tie is counted as a loss."
  }, /*#__PURE__*/React.createElement("span", null, "2% target before 3% stop"), /*#__PURE__*/React.createElement("b", null, st.tbs ? /*#__PURE__*/React.createElement(GapProb, {
    r: st.tbs
  }) : /*#__PURE__*/React.createElement("span", {
    className: "muted",
    title: "Only daily bars exist for these events. Daily bars show how far the stock moved but not in what order, so no honest before/after claim can be made."
  }, "Unknown \xB7 daily bars only"))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "The typical (middle) move against the trade before it resolved. Half the historical events were worse than this, half better."
  }, /*#__PURE__*/React.createElement("span", null, "Typical move against you"), /*#__PURE__*/React.createElement("b", null, gapPct(st.mae_med_pct != null ? st.mae_med_pct : st.med_adv_pct))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "The bad day: 9 out of 10 historical events moved against you LESS than this. Roughly where a stop needs to sit to survive normal noise."
  }, /*#__PURE__*/React.createElement("span", null, "Bad day (90th percentile)"), /*#__PURE__*/React.createElement("b", null, gapPct(st.mae_p90_pct != null ? st.mae_p90_pct : st.adv_p90_pct))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "The very bad day: only 1 in 20 historical events went against you further than this."
  }, /*#__PURE__*/React.createElement("span", null, "Very bad day (95th percentile)"), /*#__PURE__*/React.createElement("b", null, gapPct(st.mae_p95_pct != null ? st.mae_p95_pct : st.adv_p95_pct))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv gap-worst",
    title: "The single worst comparable day in this stock's history. Tail risk is never hidden here \u2014 if one day ran 18% against the trade, you see it."
  }, /*#__PURE__*/React.createElement("span", null, "Worst single day"), /*#__PURE__*/React.createElement("b", null, "moved ", gapPct(st.worst_adv_pct), " against \xB7 ", gapDateDow(st.worst_adv_date))), st.mae_before_target_med_pct != null && /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "On the days that DID reach the target, this is how far the stock typically pushed against you first \u2014 the heat you had to sit through to collect."
  }, /*#__PURE__*/React.createElement("span", null, "Typical heat before it worked"), /*#__PURE__*/React.createElement("b", null, gapPct(st.mae_before_target_med_pct)))), /*#__PURE__*/React.createElement("div", {
    className: "gap-block"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gap-bt",
    title: "How fast it usually happens, and what the stock tends to do with the rest of the day."
  }, "Timing & tendencies"), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "On the days it reached 2%, how many minutes after the open it typically took. A fade that needs all afternoon ties up capital differently from one that's done by 10am."
  }, /*#__PURE__*/React.createElement("span", null, "Typical time to 2%"), /*#__PURE__*/React.createElement("b", null, st.med_time_to_min && st.med_time_to_min["2"] != null ? `${st.med_time_to_min["2"]} min` : "—")), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "How often the stock traded all the way back to the prior day's closing price \u2014 completely closing the gap."
  }, /*#__PURE__*/React.createElement("span", null, "Gap closed completely"), /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement(GapProb, {
    r: st.gap_fill
  }))), /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: `How often the stock kept going ${r.direction === "up" ? "up" : "down"} instead of reversing — it closed the day beyond where it opened. This is the fade/rebound failing.`
  }, /*#__PURE__*/React.createElement("span", null, "Kept going ", r.direction === "up" ? "higher" : "lower"), /*#__PURE__*/React.createElement("b", null, /*#__PURE__*/React.createElement(GapProb, {
    r: st.continuation
  }))), st.ev && st.ev.mean_pct != null && /*#__PURE__*/React.createElement("div", {
    className: "gap-kv",
    title: "Average profit or loss per trade if you had taken every one of these historical setups with the 2% target and 3% stop, including modeled trading costs and stops filling worse than the stop price. Positive means the edge survived the costs."
  }, /*#__PURE__*/React.createElement("span", null, "Average result per trade"), /*#__PURE__*/React.createElement("b", {
    className: st.ev.mean_pct > 0 ? "up" : "down"
  }, gapPct(st.ev.mean_pct, 2))), st.ev && st.ev.basis && /*#__PURE__*/React.createElement("div", {
    className: "gap-note",
    title: "Trading costs and stop slippage are modeled, not measured \u2014 real fills will differ."
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
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", {
    title: "Which trade this row tests: fade = shorting a gap up, rebound = buying a gap down."
  }, "Trade"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "How far from the opening price you'd take profit."
  }, "Target"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "How far against you before you'd cut the trade. Stops are modeled to fill slightly WORSE than the stop price, because in reality they do."
  }, "Stop"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "How many historical events this combination was tested on."
  }, "Events"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Percent of those events where the target printed before the stop. A high win rate with terrible losses is not a good strategy \u2014 read the Average and Worst columns too."
  }, "Win rate"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Average profit or loss per trade after modeled costs. This is the number that decides whether the combination is worth using."
  }, "Average"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Average result over the OLDER half of the history."
  }, "First half"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "Average result over the NEWER half. A combination that only worked in one half is curve-fitting, not an edge."
  }, "Second half"), /*#__PURE__*/React.createElement("th", {
    className: "scan-th-num",
    title: "The single worst simulated trade for this combination."
  }, "Worst"), /*#__PURE__*/React.createElement("th", {
    title: "A checkmark means this target/stop pair made money in BOTH halves of the history independently \u2014 it survived the walk-forward test rather than being tuned to the whole sample."
  }, "Holds up"))), /*#__PURE__*/React.createElement("tbody", null, bt.grid.slice(0, 10).map((g, i) => /*#__PURE__*/React.createElement("tr", {
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
  }, bt.note)), /*#__PURE__*/React.createElement(GapNews, {
    apiFetch: apiFetch,
    sym: sym
  }), evs && evs.events && evs.events.length > 0 && /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "gap-sechead",
    title: "Every historical day behind the percentages above. Click any column header to sort \u2014 biggest gaps, worst squeezes, or all the earnings days together."
  }, "The analogs ", /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\xB7 every event behind the numbers \xB7 click a header to sort")), /*#__PURE__*/React.createElement(GapAnalogTable, {
    events: evs.events
  }))));
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
  const [live, setLive] = useState({});
  const [liveAt, setLiveAt] = useState(null);
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

  // Live price ticker: one batched quote call, prices only. A full scan is
  // expensive and runs every few minutes, so without this the board would
  // show the price frozen at the last scan while the stock keeps moving.
  useEffect(() => {
    const tick = async () => {
      try {
        const r = await apiFetch("/api/gap/live", {
          noCache: true
        });
        const d = await r.json();
        if (d && d.ok && d.quotes) {
          setLive(d.quotes);
          setLiveAt(d.as_of);
        }
      } catch (e) {/* keep the last known price */}
    };
    tick();
    const iv = setInterval(skipWhenHidden(tick), 15 * 1000);
    return () => clearInterval(iv);
  }, []);
  const rows = useMemo(() => {
    const base = board && board.rows || [];
    if (!Object.keys(live).length) return base;
    return base.map(r => live[r.symbol] ? {
      ...r,
      ...live[r.symbol]
    } : r);
  }, [board, live]);
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
    liveQ: live[gapSym],
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
  }, board.session === "premarket" ? "premarket" : "market hours", " \xB7 SPY ", gapPct(ctx.spy_gap_pct), " \xB7 QQQ ", gapPct(ctx.qqq_gap_pct), " \xB7", " ", /*#__PURE__*/React.createElement("span", {
    className: "gap-livedot",
    title: "Prices refresh every 15 seconds; the statistics update on each full scan."
  }), "prices live ", gapTime(liveAt || board.price_as_of), " \xB7 statistics as of ", gapWhen(board.as_of)), /*#__PURE__*/React.createElement("div", {
    className: "scan-table-wrap"
  }, /*#__PURE__*/React.createElement("table", {
    className: "scan-table gap-table"
  }, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, th("Ticker", "symbol", "The premarket mover. Click any row to open the full evidence: every historical gap, the news, and the target/stop grid. The colored dot shows how closely today's setup matches the historical examples — green is a close match, grey is a loose one."), th("Price", "rank", "Current premarket price. Refreshes every 15 seconds.", true), th("Premarket gap", "gap", "How far the current price is from yesterday's closing price. This is the LIVE gap and it keeps moving until 9:30 — it is not the official opening gap the history is measured from."), th("Off high/low", "rank", "For a gap up: how far the price has already pulled back from the highest premarket price so far. For a gap down: how far it has bounced off the low. Note 'so far' — the final premarket high doesn't exist until 9:30.", true), th("Catalyst", "rank", "What is known to be driving the move, from real data only: Earnings (the company reported), Analyst action, or a Macro event day. 'None tagged' means none of those were found — it does not mean nothing is happening. Earnings gaps are only ever compared against this stock's other earnings gaps.", true), th("Fades 2%", "p2", "How often this stock's comparable past gaps moved at least 2% in the profitable direction from the opening price — down for a gap up, up for a gap down. The small number after the dot is how many historical examples that rate is based on. Hover the value for the conservative range."), th("Hits target first", "tbs", "How often the 2% profit target printed BEFORE a 3% stop would have been hit, measured minute by minute on real historical paths. A dash means only daily bars exist for those days — daily bars show how far a stock moved but not in what order, so no honest claim is made."), th("Moves against", "adv", "The typical move AGAINST the trade before it resolved. A gap that eventually fades but squeezes 4% higher first is not a comfortable short.", true), th("Examples", "n", "How many comparable historical events back these numbers. No sample size, no probability — a rate from 3 events is not a rate.", true), th("Signal", "rank", "The call. STRONG requires the favorable rate AND the target-before-stop rate AND controlled tail risk AND enough examples — all judged on the conservative end of each range, never on one flattering number. NO DATA means the evidence or the live quote isn't good enough to say anything."))), /*#__PURE__*/React.createElement("tbody", null, shown.map(r => /*#__PURE__*/React.createElement("tr", {
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
    className: "gap-hidemobile",
    title: r.catalyst_label || gapCatalyst(r.catalyst_kind)
  }, r.catalyst_kind === "UNTAGGED" ? /*#__PURE__*/React.createElement("span", {
    className: "muted"
  }, "\u2014") : gapCatalyst(r.catalyst_kind)), /*#__PURE__*/React.createElement("td", {
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
