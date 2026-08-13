// tab-recovery.jsx — LAZY CHUNK (v3.91), loaded on first Recovery-tab open.

// ═══════════════════════════════════════════════════════════════════════════
// PRIOR HIGH RECOVERY TAB (v3.91) — stocks that made a significant high,
// corrected 10%+, and now show evidence the correction is ending — surfaced
// BEFORE they get back to that high. Levels and structure are measured from
// daily bars; probabilities are empirical hit rates from the historical
// study shipped in recovery_model.json (sample sizes always shown; when a
// number can't be backed by history the UI says so instead of inventing
// one). Data: /api/recovery (board) · /api/recovery/detail · /research.
// ═══════════════════════════════════════════════════════════════════════════

const RCV_STAGES = [
  ["all", "All"],
  ["early", "Early Recovery"],
  ["confirmed", "Confirmed"],
  ["approaching", "Approaching High"],
  ["prior_high_test", "Testing High"],
  ["bottoming", "Bottoming"],
];
const RCV_STAGE_LABEL = {
  bottoming: ["BOTTOMING", "mut"], early: ["EARLY", "up"],
  confirmed: ["CONFIRMED", "up"], approaching: ["APPROACHING", "warn"],
  prior_high_test: ["AT HIGH", "warn"], breakout: ["BREAKOUT", "warn"],
  failed: ["FAILED", "down"],
};
// Presets = one-tap views of the same board (section + sort + filter combo).
const RCV_PRESETS = [
  ["early", "Early Recovery", "Fresh turns with the most room left — Early Recovery stage, ranked by Opportunity."],
  ["confirmed", "Confirmed Turn", "Higher low + bounce-high break — Confirmed stage, ranked by Opportunity."],
  ["rr", "Best Risk/Reward", "Most upside per unit of risk to the invalidation level."],
  ["prob", "Highest Probability", "Ranked by historical odds of reaching the prior high before invalidation."],
  ["trigger", "Closest to Trigger", "Not yet broken out — sorted by how close price sits to the bounce high it needs to clear."],
];

// Short sector tags so the column stays narrow; full name in the tooltip.
const RCV_SECTOR_SHORT = {
  Technology: "Tech", "Information Technology": "Tech",
  "Financial Services": "Fin", Financials: "Fin",
  Healthcare: "Health", "Health Care": "Health",
  Energy: "Energy", "Consumer Cyclical": "Cyclical",
  "Consumer Discretionary": "Cyclical", "Consumer Defensive": "Staples",
  "Consumer Staples": "Staples", Industrials: "Indust",
  "Basic Materials": "Materls", Materials: "Materls",
  Utilities: "Utils", "Real Estate": "REIT",
  "Communication Services": "Comms",
};

function rcvPct(v, d = 1) {
  return v == null ? "—" : `${(v * 100).toFixed(d)}%`;
}
function rcvChg(v, d = 1) {
  return v == null ? "" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`;
}

// The row-level group tag. Preferred: the user's own watchlist tag (from
// the Manage tab's CSV import) with ▲/▼ when the tag's members are up/down
// 1%+ (median) over 20 sessions — the quick "is this group moving up"
// read. Untagged stocks fall back to a muted sector abbreviation.
function RcvGroupTag({ r, groups }) {
  if (r.tag) {
    const g = groups && groups[r.tag];
    const chg = g && g.chg20;
    const dir = chg == null ? "" : chg >= 0.01 ? "up" : chg <= -0.01 ? "down" : "";
    const arrow = dir === "up" ? " ▲" : dir === "down" ? " ▼" : "";
    const tip = `Your tag: ${r.tag}` +
      (chg != null ? ` · group median ${rcvChg(chg)} over 20 sessions (${rcvChg(g.chg5)} over 5) across ${g.members} watchlist members` : "") +
      `${r.sector ? ` · sector ${r.sector}` : ""}. Several rows sharing a rising tag = your group recovering together.`;
    return <span className={`rcv-sectag ${dir}`} title={tip}>
      {r.tag.length > 11 ? r.tag.slice(0, 10) + "…" : r.tag}{arrow}
    </span>;
  }
  if (r.sector) {
    return <span className="rcv-sectag rcv-notag"
      title={`${r.sector} (sector shown — this stock has no tag in your watchlist; tags come from the CSV you import in Manage).`}>
      {RCV_SECTOR_SHORT[r.sector] || r.sector.slice(0, 7)}
    </span>;
  }
  return <span className="mut">—</span>;
}
function rcvNum(v, d = 2) {
  return v == null ? "—" : Number(v).toFixed(d);
}
function rcvProbCell(p) {
  if (!p || !p.available) return <span className="mut" title={(p && p.reason) || "insufficient historical data"}>—</span>;
  return <span className={p.p_win >= 0.45 ? "up" : p.p_win >= 0.25 ? "" : "down"}>{rcvPct(p.p_win, 0)}</span>;
}

function RcvStagePill({ stage }) {
  const [label, cls] = RCV_STAGE_LABEL[stage] || [stage, "mut"];
  return <span className={`rcv-stage rcv-stage-${cls}`}>{label}</span>;
}

// ── expansion row: full detail + probability ladder + chart hand-off ────────

function RcvDetail({ r, onOpenTicker, onMarkLevels }) {
  const p = r.prob && r.prob.available ? r.prob : null;
  const levels = [
    { price: r.prior_high, label: "Prior high", kind: "prior_high" },
    { price: r.corr_low, label: "Correction low", kind: "correction_low" },
    { price: r.invalidation, label: "Invalidation", kind: "invalidation" },
    r.bounce_high != null && { price: r.bounce_high, label: "Bounce high", kind: "bounce_high" },
    r.higher_low != null && { price: r.higher_low, label: "Higher low", kind: "higher_low" },
  ].filter(Boolean);
  const lv = (label, val, date, cls) => (
    <div className="rcv-q">
      <div className="rcv-q-k">{label}</div>
      <div className={`rcv-q-v num ${cls || ""}`}>{val == null ? "—" : `$${Number(val).toFixed(2)}`}</div>
      {date && <div className="rcv-q-d">{date}</div>}
    </div>
  );
  return (
    <div className="rcv-detail">
      <div className="rcv-explain">{r.explain}</div>
      <div className="rcv-qgrid">
        {lv("Prior high", r.prior_high, r.prior_high_date, "up")}
        {lv("Correction low", r.corr_low, r.corr_low_date, "warn")}
        {lv("Bounce high", r.bounce_high, r.bounce_high_date)}
        {lv("Higher low", r.higher_low, r.higher_low_date)}
        {lv(`Invalidation (${r.inval_basis})`, r.invalidation, null, "down")}
        <div className="rcv-q">
          <div className="rcv-q-k">Risk → Reward</div>
          <div className="rcv-q-v num">{rcvPct(r.risk_pct)} → {rcvPct(r.upside_pct)}</div>
          <div className="rcv-q-d">{r.reward_risk != null ? `${rcvNum(r.reward_risk)}:1` : "—"}</div>
        </div>
      </div>
      {p ? (
        <div className="rcv-ladder" title={`Empirical outcomes of the ${p.n} historical Early/Confirmed signals in this model-score decile (decile ${p.decile} of 10).`}>
          <span className="rcv-lad-head">History (n={p.n}):</span>
          <span>reach high ≤{p.horizon || 60}d <b className="up">{rcvPct(p.p_win, 0)}</b></span>
          <span>≤5d <b>{rcvPct(p.hit5, 0)}</b></span>
          <span>≤10d <b>{rcvPct(p.hit10, 0)}</b></span>
          <span>≤20d <b>{rcvPct(p.hit20, 0)}</b></span>
          <span>≤30d <b>{rcvPct(p.hit30, 0)}</b></span>
          <span>exceed high <b>{rcvPct(p.p_exceed, 0)}</b></span>
          <span>median {p.median_days != null ? `${p.median_days}d` : "—"} to target</span>
          <span>MFE {p.median_mfe != null ? `+${p.median_mfe}%` : "—"} / MAE {p.median_mae != null ? `${p.median_mae}%` : "—"}</span>
        </div>
      ) : (
        <div className="rcv-ladder rcv-ladder-none">Historical probability: {(r.prob && r.prob.reason) || "insufficient historical data"}.</div>
      )}
      <div className="rcv-cta">
        <button className="scan-run-btn" onClick={(e) => {
          e.stopPropagation();
          if (onMarkLevels) onMarkLevels({ symbol: r.ticker, levels });
          if (onOpenTicker) onOpenTicker(r.ticker);
        }} title="Switch the whole app to this ticker and draw prior high / correction low / higher low / invalidation on the price chart.">
          Open chart with levels
        </button>
        {r.sector && <span className="mut rcv-sec">{r.sector}</span>}
        <span className="mut rcv-sec">high stood {r.significance}d · {r.days_since_high}d since high · {r.days_since_low}d since low</span>
      </div>
    </div>
  );
}

function RcvRow({ r, open, onToggle, onOpenTicker, onMarkLevels, groups }) {
  const p = r.prob && r.prob.available ? r.prob : null;
  return (
    <React.Fragment>
      <tr className={`scan-row rcv-row ${open ? "rcv-open" : ""}`} onClick={onToggle}
          title="Click to expand the full setup breakdown.">
        <td className="rcv-tk"><b>{r.ticker}</b></td>
        <td><RcvStagePill stage={r.stage} /></td>
        <td><RcvGroupTag r={r} groups={groups} /></td>
        <td className="scan-num">{r.opportunity != null
          ? <b className={r.opportunity >= 45 ? "up" : ""}>{r.opportunity.toFixed(0)}</b>
          : <span className="mut" title={(r.prob && r.prob.reason) || ""}>—</span>}</td>
        <td className="scan-num">{p ? <span className={p.recovery_score >= 60 ? "up" : ""}>{p.recovery_score.toFixed(0)}</span> : <span className="mut">—</span>}</td>
        <td className="scan-num">{rcvProbCell(r.prob)}</td>
        <td className="scan-num">{p ? rcvPct(p.hit20, 0) : "—"}</td>
        <td className="scan-num up">{rcvPct(r.upside_pct)}</td>
        <td className="scan-num">{r.reward_risk != null ? `${rcvNum(r.reward_risk, 1)}:1` : "—"}</td>
        <td className="scan-num">{rcvPct(r.dist_to_high)}</td>
        <td className="scan-num">{rcvPct(r.recovery_ratio, 0)}</td>
        <td className="scan-num">{rcvPct(r.depth)}</td>
        <td className="rcv-struct" title={`${r.has_higher_low ? "Higher low formed. " : "No higher low yet. "}${r.broke_bounce ? "Broke above the bounce high." : "Still below the bounce high."}`}>
          <span className={r.has_higher_low ? "up" : "mut"}>HL</span>
          <span className={r.broke_bounce ? "up" : "mut"}>BB</span>
        </td>
        <td className="scan-num">{r.relvol != null ? `${rcvNum(r.relvol, 1)}×` : "—"}</td>
        <td className="scan-num">{p ? p.n : "—"}</td>
      </tr>
      {open && (
        <tr className="rcv-detail-row"><td colSpan={15}>
          <RcvDetail r={r} onOpenTicker={onOpenTicker} onMarkLevels={onMarkLevels} />
        </td></tr>
      )}
    </React.Fragment>
  );
}

// ── research view: the historical study behind the probabilities ────────────

function RcvCohortTable({ title, rows, tip }) {
  if (!rows || !rows.length) return null;
  const any = rows.some(r => r.n);
  if (!any) return null;
  return (
    <div className="rcv-cohort">
      <div className="rcv-cohort-t" title={tip}>{title}</div>
      <div className="scan-table-wrap">
        <table className="scan-table rcv-rtable">
          <thead><tr>
            <th>Bucket</th><th className="scan-th-num" title="Historical signals in this bucket.">n</th>
            <th className="scan-th-num" title="Reached the prior high before the invalidation level within the horizon.">Reach high</th>
            <th className="scan-th-num" title="Hit the invalidation level first.">Fail first</th>
            <th className="scan-th-num" title="Reached the prior high within 20 trading days.">≤20d</th>
            <th className="scan-th-num" title="Went on to exceed the prior high by 1%+.">Exceed</th>
            <th className="scan-th-num" title="Median close-to-close return 30 trading days after the signal.">30d ret</th>
            <th className="scan-th-num" title="Median trading days to reach the prior high (winners only).">Days</th>
          </tr></thead>
          <tbody>
            {rows.filter(r => r.n).map((r, i) => (
              <tr key={i}>
                <td>{r.bucket}</td>
                <td className="scan-num">{r.n}</td>
                <td className="scan-num"><span className={r.p_win >= 0.35 ? "up" : ""}>{rcvPct(r.p_win, 0)}</span></td>
                <td className="scan-num">{rcvPct(r.p_fail, 0)}</td>
                <td className="scan-num">{rcvPct(r.hit20, 0)}</td>
                <td className="scan-num">{rcvPct(r.p_exceed, 0)}</td>
                <td className="scan-num">{r.median_fwd30 != null ? `${r.median_fwd30 > 0 ? "+" : ""}${r.median_fwd30}%` : "—"}</td>
                <td className="scan-num">{r.median_days != null ? r.median_days : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RcvResearch({ apiFetch }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let stop = false;
    sharedJson(apiFetch, "/api/recovery/research", 10 * 60 * 1000)
      .then(d => { if (!stop) setData(d); })
      .catch(e => { if (!stop) setErr(String((e && e.message) || e)); });
    return () => { stop = true; };
  }, []);
  if (err) return <div className="card-note card-note-error">{err}</div>;
  if (!data) return <div className="card-note card-note-loading">Loading the historical study…</div>;
  if (!data.available) return <div className="card-note card-note-empty">{data.reason}</div>;
  const m = data.meta || {};
  const dec = data.deciles || [];
  const co = data.cohorts || {};
  const reg = data.regimes || {};
  const fr = data.feature_report || {};
  return (
    <div className="rcv-research">
      <div className="rcv-meta">
        <span title="Every Early/Confirmed Recovery signal found by replaying the watchlist's daily history through the same detector the scanner runs.">{m.n_signals} signals · {m.n_symbols} stocks · {m.data_from} → {m.data_to}</span>
        <span title="Model quality on the untouched out-of-sample period (fit on data through 2023, tested on 2024+). AUC 0.5 = coin flip, 1.0 = perfect ranking.">out-of-sample AUC {m.test_auc} · Brier {m.test_brier}</span>
      </div>
      <div className="rcv-limit">{m.limitations}</div>

      <RcvCohortTable title="By model-score decile (what the scanner's probability column is built from)"
        tip="Signals ranked by the fitted model, split into ten equal buckets. The scanner looks up a live setup's decile and quotes these measured rates."
        rows={dec.map(d => ({ ...d, bucket: `decile ${d.decile}` }))} />
      <RcvCohortTable title="By Recovery Ratio (how much of the decline is recovered)"
        tip="The central question: where is the sweet spot between 'proven it can recover' and 'upside already gone'?"
        rows={co.recovery_ratio} />
      <RcvCohortTable title="By correction depth" rows={co.depth}
        tip="Shallower corrections recover to their prior high far more often than deep ones." />
      <RcvCohortTable title="By distance below the prior high" rows={co.dist_to_high}
        tip="The closer the entry is to the old high, the more often it gets there — but the less upside remains. Pair with the Recovery Ratio table." />
      <RcvCohortTable title="Higher low" rows={co.higher_low}
        tip="Does a formed higher low actually improve outcomes? (Raw comparison, all else NOT held equal.)" />
      <RcvCohortTable title="Bounce-high break" rows={co.broke_bounce}
        tip="Breaking the first bounce high is the single strongest structural confirmation in the study." />
      <RcvCohortTable title="By stage at signal" rows={co.stage} />
      <RcvCohortTable title="By days since the correction low" rows={co.days_since_low} />
      <RcvCohortTable title="By relative volume on signal day" rows={co.relvol} />
      <RcvCohortTable title="SPY regime at signal" rows={reg.spy}
        tip="Trailing SPY vs its 50/200-day averages on the signal day — no future data." />
      <RcvCohortTable title="VIX at signal" rows={reg.vix} />
      {reg.sector && reg.sector.length > 0 &&
        <RcvCohortTable title="By sector (n ≥ 30 only)" rows={reg.sector} />}

      {data.stability && (
        <div className="rcv-cohort">
          <div className="rcv-cohort-t" title="Does the model's edge survive across bull and bear years? Top-3-decile hit rate vs all signals, per year.">Stability by year</div>
          <div className="scan-table-wrap">
            <table className="scan-table rcv-rtable">
              <thead><tr><th>Year</th><th className="scan-th-num">n</th><th className="scan-th-num">All signals</th><th className="scan-th-num">Top 3 deciles</th></tr></thead>
              <tbody>{data.stability.map(r => (
                <tr key={r.year}><td>{r.year}</td><td className="scan-num">{r.n}</td>
                  <td className="scan-num">{rcvPct(r.p_win, 0)}</td>
                  <td className="scan-num"><span className={r.top3_p_win >= (r.p_win * 1.5) ? "up" : ""}>{r.top3_p_win != null ? rcvPct(r.top3_p_win, 0) : "—"}</span></td></tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}
      {fr.dropped && fr.dropped.length > 0 && (
        <div className="rcv-limit">
          Indicators tested and DROPPED for adding no out-of-sample value once structure is accounted for: {fr.dropped.join(", ")}.
        </div>
      )}
      {(data.notes || []).map((n, i) => <div key={i} className="rcv-note">{n}</div>)}
    </div>
  );
}

// ── the tab ─────────────────────────────────────────────────────────────────

function RecoveryTab({ apiFetch, onOpenTicker, onMarkLevels }) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [section, setSection] = useState("all");
  const [preset, setPreset] = useState(null);
  const [sortK, setSortK] = useState("opp");
  const [sortD, setSortD] = useState(-1);
  const [openTk, setOpenTk] = useState(null);
  const [showResearch, setShowResearch] = useState(false);
  const [fltSector, setFltSector] = useState(null);
  const [fltTag, setFltTag] = useState(null);
  const [flt, setFlt] = useState({ hlOnly: false, bbOnly: false, rsPos: false,
    upside8: false, rr15: false, p40: false, fresh: false });
  const pollRef = useRef(null);

  const load = async () => {
    try { const r = await apiFetch("/api/recovery"); const d = await r.json(); setBoard(d); return d; }
    catch (e) { setErr(String(e)); return null; }
  };
  const watchScan = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) { clearInterval(pollRef.current); pollRef.current = null; }
    }, 4000);
  };
  useEffect(() => {
    load().then(d => {
      if (!d) return;
      if (d.status && d.status.scanning) { watchScan(); return; }
      const age = d.status && d.status.last_scan ? Date.now() - new Date(d.status.last_scan).getTime() : Infinity;
      if (age > 60 * 60000) { apiFetch("/api/recovery/scan").catch(() => {}); watchScan(); }
    });
    return () => pollRef.current && clearInterval(pollRef.current);
  }, []);
  const rescan = async () => {
    setErr(null);
    try { await apiFetch("/api/recovery/scan?force=1"); } catch (e) { setErr(String(e)); return; }
    await load(); watchScan();
  };

  const status = (board && board.status) || {};
  const allRows = (board && board.rows) || [];
  const model = (board && board.model) || {};
  const sectors = (board && board.sectors) || [];
  const groups = (board && board.groups) || [];      // user-tag clusters
  const hasTags = groups.length > 0;
  // tag → {chg20, chg5, members} for the row tags (covers tags whose
  // stocks have setups AND the full tag_trend so fallbacks resolve too)
  const tagTrend = useMemo(() => {
    const m = { ...((board && board.tag_trend) || {}) };
    for (const g of groups) m[g.tag] = { ...(m[g.tag] || {}), ...g };
    return m;
  }, [board, groups]);

  const applyPreset = (k) => {
    setPreset(k); setOpenTk(null);
    if (k === "early") { setSection("early"); setSortK("opp"); setSortD(-1); setFlt(f => ({ ...f, hlOnly: false, bbOnly: false })); }
    else if (k === "confirmed") { setSection("confirmed"); setSortK("opp"); setSortD(-1); }
    else if (k === "rr") { setSection("all"); setSortK("rr"); setSortD(-1); }
    else if (k === "prob") { setSection("all"); setSortK("p"); setSortD(-1); }
    else if (k === "trigger") { setSection("all"); setSortK("trigger"); setSortD(1); }
  };

  const filtered = useMemo(() => allRows.filter(r => {
    if (section !== "all" && r.stage !== section) return false;
    if (fltTag && r.tag !== fltTag) return false;
    if (fltSector && (r.sector || "Other") !== fltSector) return false;
    if (preset === "trigger" && (r.broke_bounce || r.bounce_high == null)) return false;
    if (flt.hlOnly && !r.has_higher_low) return false;
    if (flt.bbOnly && !r.broke_bounce) return false;
    if (flt.rsPos && !(r.rs_spy != null && r.rs_spy > 0)) return false;
    if (flt.upside8 && !(r.upside_pct != null && r.upside_pct >= 0.08)) return false;
    if (flt.rr15 && !(r.reward_risk != null && r.reward_risk >= 1.5)) return false;
    if (flt.p40 && !(r.prob && r.prob.available && r.prob.p_win >= 0.4)) return false;
    if (flt.fresh && !(r.days_since_low != null && r.days_since_low <= 15)) return false;
    return true;
  }), [allRows, section, preset, flt, fltSector, fltTag]);

  const sorted = useMemo(() => {
    const key = r => {
      switch (sortK) {
        case "ticker": return r.ticker || "";
        case "tag": return r.tag || `zz ${r.sector || "zz"}`;
        case "score": return (r.prob && r.prob.available && r.prob.recovery_score) ?? -1;
        case "p": return (r.prob && r.prob.available && r.prob.p_win) ?? -1;
        case "h20": return (r.prob && r.prob.available && r.prob.hit20) ?? -1;
        case "upside": return r.upside_pct ?? -1;
        case "rr": return r.reward_risk ?? -1;
        case "dist": return r.dist_to_high ?? 999;
        case "recov": return r.recovery_ratio ?? -1;
        case "depth": return r.depth ?? -1;
        case "relvol": return r.relvol ?? -1;
        case "n": return (r.prob && r.prob.available && r.prob.n) ?? -1;
        case "trigger": return (r.bounce_high != null && r.close != null && r.close > 0)
          ? (r.bounce_high - r.close) / r.close : 999;
        default: return r.opportunity ?? -1;
      }
    };
    return [...filtered].sort((a, b) => {
      const ka = key(a), kb = key(b);
      // sortD −1 = descending (big first), +1 = ascending — matches the ↓/↑
      // arrows. (ka<kb → −1 puts a first, so multiply by sortD directly.)
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * sortD;
    });
  }, [filtered, sortK, sortD]);

  const th = (label, k, tip) => (
    <th className={`${sortK === k ? "on" : ""} ${k !== "ticker" ? "scan-th-num" : ""}`} title={tip}
        onClick={() => { if (sortK === k) setSortD(d => -d); else { setSortK(k); setSortD(k === "ticker" || k === "tag" ? 1 : -1); } }}>
      {label}{sortK === k ? (sortD === -1 ? " ↓" : " ↑") : ""}
    </th>
  );
  const chip = (k, label, tip) => (
    <button key={k} type="button" className={`tsy-serbtn ${flt[k] ? "on" : ""}`} title={tip}
            onClick={() => setFlt(f => ({ ...f, [k]: !f[k] }))}>{label}</button>
  );
  const counts = useMemo(() => {
    const c = { all: allRows.length };
    for (const r of allRows) c[r.stage] = (c[r.stage] || 0) + 1;
    return c;
  }, [allRows]);

  return (
    <div className="rcv">
      <div className="card rcv-card">
        <div className="card-head">
          <div>
            <div className="kicker" title="Universe = your watchlist. A qualifying setup needs a significant high (stood ≥15 days on both sides), a 10%+ correction, and price between the correction low and the prior high.">
              Prior high recovery scanner · your watchlist · measured levels, historical odds
            </div>
            <div className="card-title">Which pullbacks are turning back up?</div>
          </div>
          <div className="tsy-ctrl rcv-ctrl">
            {board && board.spy_regime && (
              <span className={`tsy-datechip rcv-regime-${board.spy_regime}`}
                    title="SPY vs its 50/200-day averages right now. The study shows recoveries succeed ~1.5× as often in an uptrend as in a downtrend.">
                SPY {board.spy_regime}
              </span>
            )}
            <button className={`tsy-serbtn ${showResearch ? "on" : ""}`} onClick={() => setShowResearch(s => !s)}
                    title="The historical study behind every probability on this page: cohort tables, per-year stability, what was tested and dropped.">
              Research
            </button>
            <button className="scan-run-btn" onClick={rescan} disabled={!!status.scanning}>
              {status.scanning ? `Scanning… ${status.scanned || 0}/${status.total || 0}` : "Rescan"}
            </button>
          </div>
        </div>
        {err && <div className="tsy-err">{err}</div>}
        {!model.available && <div className="rcv-nomodel">{model.reason || "No historical model — structure shows, probabilities don't."}</div>}
        {status.last_scan && (
          <div className="ab-status">
            Last scan {new Date(status.last_scan).toLocaleString()} · {allRows.length} setups{status.universe_size ? ` from ${status.universe_size} stocks` : ""}
            {model.available && ` · model: ${model.n_signals} historical signals, out-of-sample AUC ${model.test_auc}`}
            {status.error ? ` · ${status.error}` : ""}
          </div>
        )}

        <div className="eop-sections">
          {RCV_STAGES.map(([k, label]) => (
            <button key={k} type="button" className={`eop-sec ${section === k ? "on" : ""}`}
                    onClick={() => { setSection(k); setPreset(null); }}>
              {label} <b>{counts[k] || 0}</b>
            </button>
          ))}
        </div>
        {hasTags ? (
          <div className="rcv-secstrip">
            {groups.slice(0, 28).map(g => {
              const dir = g.chg20 == null ? "" : g.chg20 >= 0.01 ? "up" : g.chg20 <= -0.01 ? "down" : "";
              return (
                <button key={g.tag} type="button"
                        className={`rcv-secchip ${dir} ${fltTag === g.tag ? "on" : ""}`}
                        title={`${g.count} setup${g.count === 1 ? "" : "s"} in your "${g.tag}" tag` +
                          (g.chg20 != null ? ` · group median ${rcvChg(g.chg20)} over 20 sessions (${rcvChg(g.chg5)} over 5) across ${g.members} members` : "") +
                          ". A big count on a rising group = your group recovering together. Click to show only this tag."}
                        onClick={() => { setFltTag(fltTag === g.tag ? null : g.tag); setFltSector(null); }}>
                  {g.tag} <b>{g.count}</b>
                  {g.chg20 != null && <span className="rcv-secchg">{rcvChg(g.chg20)}</span>}
                </button>
              );
            })}
            {fltTag && <button type="button" className="rcv-secchip rcv-secclear"
                               onClick={() => setFltTag(null)}>✕ clear</button>}
          </div>
        ) : sectors.length > 0 && (
          <div className="rcv-secstrip">
            <span className="rcv-secnote" title="Tags come from the CSV you import in the Manage tab. Once your stocks carry tags, this strip shows YOUR groups instead of broad sectors.">
              no watchlist tags yet — showing sectors
            </span>
            {sectors.map(s => {
              const dir = s.chg20 == null ? "" : s.chg20 >= 0.01 ? "up" : s.chg20 <= -0.01 ? "down" : "";
              return (
                <button key={s.sector} type="button"
                        className={`rcv-secchip ${dir} ${fltSector === s.sector ? "on" : ""}`}
                        title={`${s.count} setup${s.count === 1 ? "" : "s"} in ${s.sector}` +
                          (s.etf && s.chg20 != null ? ` · ${s.etf} ${rcvChg(s.chg20)} over 20 sessions (${rcvChg(s.chg5)} over 5)` : "") +
                          ". Click to show only this sector."}
                        onClick={() => setFltSector(fltSector === s.sector ? null : s.sector)}>
                  {RCV_SECTOR_SHORT[s.sector] || s.sector} <b>{s.count}</b>
                  {s.chg20 != null && <span className="rcv-secchg">{rcvChg(s.chg20)}</span>}
                </button>
              );
            })}
            {fltSector && <button type="button" className="rcv-secchip rcv-secclear"
                                  onClick={() => setFltSector(null)}>✕ clear</button>}
          </div>
        )}
        <div className="tsy-ctrl rcv-presets">
          {RCV_PRESETS.map(([k, label, tip]) => (
            <button key={k} type="button" className={`preset-pill ${preset === k ? "active" : ""}`}
                    title={tip} onClick={() => applyPreset(k)}>{label}</button>
          ))}
        </div>
        <div className="tsy-ctrl rcv-filters">
          {chip("hlOnly", "Higher low", "Only setups that already formed a higher low above the correction low.")}
          {chip("bbOnly", "Broke bounce", "Only setups that broke above their first bounce high — the strongest structural confirmation in the study.")}
          {chip("rsPos", "RS+ vs SPY", "Only names outperforming SPY over the last 20 sessions.")}
          {chip("upside8", "Upside ≥8%", "At least 8% left between here and the prior high.")}
          {chip("rr15", "R:R ≥1.5", "At least 1.5× as much upside to the prior high as downside to the invalidation level.")}
          {chip("p40", "P ≥40%", "Historical reach-the-high probability at least 40%.")}
          {chip("fresh", "Fresh turn", "Correction low was 15 trading days ago or less.")}
          <span className="muted" style={{ fontSize: 11.5 }}>{sorted.length} shown</span>
        </div>

        <div className="scan-table-wrap rcv-wrap">
          <table className="scan-table rcv-table">
            <thead><tr>
              {th("Ticker", "ticker", "Click a row to expand the full breakdown with levels and history.")}
              <th title="Where this stock sits in the recovery: Bottoming → Early → Confirmed → Approaching → Testing the prior high. The scanner's focus is Early and Confirmed — before the move is obvious.">Stage</th>
              {th("Tag", "tag", "Your watchlist tag for this stock (from the CSV imported in Manage), with ▲/▼ when the tag's members are up/down 1%+ (median) over 20 sessions. Several rows sharing a rising tag = your group recovering together — click to sort, or use the group chips above to filter. Untagged stocks show their sector, muted.")}
              {th("Opp", "opp", "Opportunity Score 0–100 — the headline rank: 45% historical probability + 25% remaining upside + 20% reward-to-risk + 10% structure quality. Requires a probability; rows outside the model show —.")}
              {th("Score", "score", "Recovery Score 0–100 — technical quality of the recovery alone (calibrated to the historical study; top decile ≈ 100). Probability and upside are scored separately.")}
              {th("P(high)", "p", "Measured share of similar historical setups that touched the prior high BEFORE the invalidation level within 60 trading days. From the shipped study — sample size in the n column.")}
              {th("≤20d", "h20", "Share of those historical setups that reached the prior high within 20 trading days.")}
              {th("Upside", "upside", "Distance from here up to the prior high.")}
              {th("R:R", "rr", "Upside to the prior high ÷ downside to the invalidation level.")}
              {th("To high", "dist", "How far below the prior high price sits now.")}
              {th("Recov", "recov", "Recovery Ratio — share of the correction already recovered. 0% = at the low, 100% = back at the high.")}
              {th("Depth", "depth", "How deep the correction cut from the prior high to the correction low.")}
              <th title="Structure flags: HL = higher low formed above the correction low · BB = broke above the first bounce high. Lit green when true.">Struct</th>
              {th("RelVol", "relvol", "Latest day's volume ÷ 20-day average.")}
              {th("n", "n", "Historical sample size behind this row's probabilities. A 90% on 7 examples is not a 90% on 2,000 — the study never quotes buckets under 30.")}
            </tr></thead>
            <tbody>
              {sorted.map(r => (
                <RcvRow key={r.ticker} r={r} open={openTk === r.ticker}
                        onToggle={() => setOpenTk(openTk === r.ticker ? null : r.ticker)}
                        onOpenTicker={onOpenTicker} onMarkLevels={onMarkLevels}
                        groups={tagTrend} />
              ))}
            </tbody>
          </table>
          {!sorted.length && (
            <div className="research-empty">
              {allRows.length ? "Nothing matches the filters." :
               status.scanning ? "Scanning your watchlist for prior-high recovery setups…" :
               "No qualifying setups on the board yet. Rescan to sweep the watchlist."}
            </div>
          )}
        </div>
        <div className="tsy-foot">{board && board.note}</div>
      </div>

      {showResearch && (
        <div className="card rcv-card">
          <div className="card-head">
            <div>
              <div className="kicker">The historical study behind the probabilities · train ≤2021 · validate 2022–23 · test 2024+</div>
              <div className="card-title">Where do recoveries actually succeed?</div>
            </div>
          </div>
          <CardErrorBoundary label="Recovery research">
            <RcvResearch apiFetch={apiFetch} />
          </CardErrorBoundary>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { RecoveryTab: React.memo(RecoveryTab) });
