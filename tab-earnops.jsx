// tab-earnops.jsx — LAZY CHUNK (v3.64), split from app-cards.jsx.
// Earnings Opportunities scanner; loaded on first Earnings-Ops-tab open.

// ═══════════════════════════════════════════════════════════════════════════
// EARNINGS OPPORTUNITIES TAB (v3.63) — Market-Chameleon-style earnings
// opportunity scanner on our own providers. NOT a calendar: every watchlist
// name reporting −4…+8 days gets scored 0–100, classified into a setup /
// status / best action, and given an explainable trade plan or an explicit
// NO TRADE. Data: /api/earnings_scan (board pattern). Demo rows are labeled.
// ═══════════════════════════════════════════════════════════════════════════

const EOP_SECTIONS = [
  ["all", "All"],
  ["today", "Today"],
  ["pre", "Pre-earnings"],
  ["post", "Post movers"],
  ["premium", "Premium ops"],
  ["waiting", "Waiting confirm"],
  ["extended", "Extended"],
  ["no_trade", "No trade"],
];
const EOP_ACTION = {
  watch: ["WATCH", "mut"], enter_on_confirmation: ["ENTER ON CONFIRM", "mut"],
  confirmed_entry: ["CONFIRMED", "up"], sell_premium: ["SELL PREMIUM", "up"],
  already_extended: ["EXTENDED", "down"], avoid: ["AVOID", "down"], no_trade: ["NO TRADE", "mut"],
};
const EOP_SETUP_LABEL = {
  high_premium: "High premium", cheap_implied: "Cheap implied", put_selling: "Put selling",
  covered_call: "Covered call", post_earnings_continuation: "Continuation",
  post_earnings_reversal: "Reversal", gap_and_go: "Gap & go", gap_fill: "Gap fill",
  vwap_reclaim: "VWAP reclaim", vwap_rejection: "VWAP reject", breakout: "Breakout",
  breakdown: "Breakdown", pre_earnings_momentum: "Pre-E momentum", pre_earnings_fade: "Pre-E fade",
  short_candidate: "Short", no_trade: "No trade",
};
function eopMcap(v) {
  if (v == null) return "—";
  return v >= 1e12 ? (v / 1e12).toFixed(1) + "T" : v >= 1e9 ? (v / 1e9).toFixed(1) + "B" : (v / 1e6).toFixed(0) + "M";
}
function eopPct(v, d = 1) {
  return v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}%`;
}

function EarnOpsAlerts({ rows }) {
  // Toasts for new row alerts (spec list computed server-side per row).
  const [toasts, setToasts] = useState([]);
  const seenKey = () => "jerry_eop_seen_" + new Date().toISOString().slice(0, 10);
  useEffect(() => {
    if (!rows || !rows.length) return;
    let seen;
    try { seen = new Set(JSON.parse(localStorage.getItem(seenKey())) || []); } catch (e) { seen = new Set(); }
    const fresh = [];
    for (const r of rows) {
      for (const a of (r.alerts || [])) {
        const k = `${r.ticker}|${a}`;
        if (!seen.has(k)) { fresh.push({ id: k + Date.now(), sym: r.ticker, msg: a }); seen.add(k); }
      }
    }
    try { localStorage.setItem(seenKey(), JSON.stringify([...seen])); } catch (e) {}
    if (fresh.length) setToasts(ts => [...ts, ...fresh.slice(0, 3)].slice(-3));
  }, [rows]);
  useEffect(() => {
    if (!toasts.length) return undefined;
    const id = setTimeout(() => setToasts(ts => ts.slice(1)), 12000);
    return () => clearTimeout(id);
  }, [toasts]);
  if (!toasts.length) return null;
  return (
    <div className="toast-stack" aria-live="polite">
      {toasts.map(t => (
        <button key={t.id} className="toast toast-radar toast-long" onClick={() => setToasts(ts => ts.filter(x => x.id !== t.id))}>
          <span className="toast-ico">◧</span>
          <span><b>{t.sym}</b> — {t.msg}</span>
        </button>
      ))}
    </div>
  );
}

function EarnOpsRow({ r, open, onToggle, onOpenTicker, onOpenIntraday, demo }) {
  const act = EOP_ACTION[r.action] || [r.action, "mut"];
  const em = r.implied, hist = r.hist, ave = r.actual_vs_expected, ivh = r.iv_vs_hist;
  const plan = r.plan, sd = r.score_detail || {};
  return (
    <React.Fragment>
      <tr className={`eop-row ${open ? "open" : ""}`} onClick={onToggle}
          title="Click to expand the score breakdown and trade plan.">
        <td className="eop-tk"><b>{r.ticker}</b>{demo || r.demo ? <span className="eop-demo">DEMO</span> : null}
          <div className="eop-co">{r.company || ""}</div></td>
        <td><div className="eop-scorebar" title={`Opportunity score ${r.score}/100`}>
          <i style={{ width: `${r.score}%` }} className={r.score >= 65 ? "hi" : r.score >= 45 ? "mid" : "lo"}></i>
          <b className="num">{r.score}</b></div></td>
        <td><span className={`tsy-pill ${act[1]}`}>{act[0]}</span></td>
        <td className="eop-setup">{EOP_SETUP_LABEL[r.setup] || r.setup}<div className="eop-sub">{(r.status || "").replace(/_/g, " ")}</div></td>
        <td className="num">{r.report_date ? r.report_date.slice(5) : "—"}<div className="eop-sub">{r.timing || "—"}{r.days_to != null ? ` · ${r.days_to === 0 ? "today" : r.days_to > 0 ? `in ${r.days_to}d` : `${-r.days_to}d ago`}` : ""}</div></td>
        <td className="num">{r.price != null ? fmt$(r.price, r.price >= 1000 ? 0 : 2) : "—"}</td>
        <td className={`num ${r.change_pct != null ? (r.change_pct >= 0 ? "cu" : "cd") : ""}`}>{eopPct(r.change_pct)}</td>
        <td className="num">{r.rel_volume != null ? r.rel_volume.toFixed(1) + "×" : "—"}</td>
        <td className="num" title={em ? `Expected move ±${em.pct}% (±$${em.dollars}) → range ${em.lower}–${em.upper}` : "Implied move unavailable"}>
          {em ? `±${em.pct}%` : "—"}<div className="eop-sub">{em ? `$${em.lower}–${em.upper}` : ""}</div></td>
        <td className="num" title={hist ? `Historical earnings reactions (n=${hist.n}): avg |${hist.avg_abs}|%, median |${hist.med_abs}|%, last ${hist.last >= 0 ? "+" : ""}${hist.last}%` : "Needs ≥3 past reactions"}>
          {hist ? `${hist.avg_abs}%` : "—"}<div className="eop-sub">{hist ? `med ${hist.med_abs} · last ${hist.last >= 0 ? "+" : ""}${hist.last}` : ""}</div></td>
        <td title={ivh ? `Implied ${ivh.ratio}× the historical average move` : ""}>
          {ivh ? <span className={`tsy-pill ${ivh.label === "rich" ? "down" : ivh.label === "cheap" ? "up" : "mut"}`}>{ivh.label.toUpperCase()} {ivh.ratio}×</span> : "—"}</td>
        <td title={ave ? `|actual| ${ave.actual}% vs expected ${ave.expected}% — basis: ${ave.basis}` : "Not reported yet (or no basis)"}>
          {ave ? <span className={`tsy-pill ${ave.label === "exceeded" ? "up" : ave.label === "undershot" ? "down" : "mut"}`}>{ave.label.toUpperCase()} {ave.ratio}×</span> : "—"}</td>
        <td className="num">{r.options_volume != null ? (r.options_volume / 1000).toFixed(0) + "k" : "—"}
          <div className="eop-sub">OI {r.open_interest != null ? (r.open_interest / 1000).toFixed(0) + "k" : "—"}</div></td>
        <td className="num">{r.atm_iv != null ? r.atm_iv.toFixed(0) : "—"}
          <div className="eop-sub">{r.spread ? `spr ${r.spread.label}` : ""}</div></td>
        <td>{r.weekly_options === true ? "✓" : r.weekly_options === false ? "—" : "?"}</td>
        <td className="num">{eopMcap(r.market_cap)}<div className="eop-sub">{r.sector || ""}</div></td>
      </tr>
      {open && (
        <tr className="eop-detail"><td colSpan="16">
          <div className="eop-dgrid">
            <div>
              <em>WHY IT RANKS ({r.score}/100)</em>
              {(sd.reasons || []).map((x, i) => <div key={i} className="eop-li up">▸ {x}</div>)}
              <em style={{ marginTop: 8 }}>RISKS</em>
              {(sd.risks || []).length ? (sd.risks || []).map((x, i) => <div key={i} className="eop-li down">▸ {x}</div>) : <div className="eop-li">—</div>}
              <em style={{ marginTop: 8 }}>CONFIRMS / INVALIDATES</em>
              <div className="eop-li">confirm: {r.confirm_text || "—"}</div>
              <div className="eop-li">invalidate: {r.invalidate_text || "—"}</div>
            </div>
            <div>
              <em>TRADE PLAN</em>
              {plan ? (
                <div className="eop-plan num">
                  <span>bias <b className={plan.bias === "long" ? "cu" : plan.bias === "short" ? "cd" : ""}>{plan.bias}</b></span>
                  {plan.entry != null && <span>entry <b>{plan.entry}</b></span>}
                  {plan.confirmation != null && <span>confirm <b>{plan.confirmation}</b></span>}
                  {plan.max_chase != null && <span>max chase <b>{plan.max_chase}</b></span>}
                  {plan.invalidation != null && <span>stop <b>{plan.invalidation}</b></span>}
                  {plan.target1 != null && <span>T1 <b>{plan.target1}</b></span>}
                  {plan.target2 != null && <span>T2 <b>{plan.target2}</b></span>}
                  {plan.rr != null && <span>R:R <b>{plan.rr}</b></span>}
                  <span>hold <b>{plan.holding}</b></span>
                  {plan.note && <span className="eop-plannote">{plan.note}</span>}
                </div>
              ) : <div className="eop-li">No actionable plan — {r.action === "no_trade" ? "explicit NO TRADE" : "not confirmed yet"}.</div>}
              <em style={{ marginTop: 8 }}>PAST EARNINGS REACTIONS</em>
              <div className="eop-hist num">
                {hist && hist.moves ? hist.moves.map((m, i) => (
                  <span key={i} className={m >= 0 ? "cu" : "cd"}>{m >= 0 ? "+" : ""}{m}</span>
                )) : "—"}
              </div>
              <em style={{ marginTop: 8 }}>LEVELS</em>
              <div className="eop-plan num">
                {r.prev_high != null && <span>PDH <b>{r.prev_high}</b></span>}
                {r.prev_low != null && <span>PDL <b>{r.prev_low}</b></span>}
                {r.pm_high != null && <span>PM H <b>{r.pm_high}</b></span>}
                {r.pm_low != null && <span>PM L <b>{r.pm_low}</b></span>}
                {r.or_high != null && <span>OR H <b>{r.or_high}</b></span>}
                {r.or_low != null && <span>OR L <b>{r.or_low}</b></span>}
                {r.vwap && <span>VWAP <b>{r.vwap.vwap}</b> ({r.vwap.above ? "above" : "below"}{r.vwap.event ? ` · ${r.vwap.event}` : ""})</span>}
                {r.gap && <span>gap <b>{eopPct(r.gap.gap_pct)}</b> fill @ <b>{r.gap.fill_level}</b>{r.gap.filled != null ? (r.gap.filled ? " (filled)" : " (open)") : ""}</span>}
                {r.day_high != null && <span>post-E H/L <b>{r.day_high}/{r.day_low}</b></span>}
              </div>
              <div className="eop-actions">
                <button type="button" className="scan-run-btn" onClick={(e) => { e.stopPropagation(); onOpenIntraday && onOpenIntraday(r.ticker); }}>Intraday chart (VWAP + levels)</button>
                <button type="button" className="scan-run-btn" onClick={(e) => { e.stopPropagation(); onOpenTicker && onOpenTicker(r.ticker); }}>Open in Analyze (chart + EM + chain)</button>
              </div>
            </div>
          </div>
        </td></tr>
      )}
    </React.Fragment>
  );
}

function EarningsOpsTab({ apiFetch, onOpenTicker, onOpenIntraday }) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [section, setSection] = useState("all");
  const [sortK, setSortK] = useState("score");
  const [sortD, setSortD] = useState(-1);
  const [openTk, setOpenTk] = useState(null);
  const [flt, setFlt] = useState({ window: "all", timing: "all", watchOnly: false,
    weeklyOnly: false, hiRelVol: false, hiOptVol: false, hiIV: false,
    largeCap: false, confirmedOnly: false, hideNoTrade: true });
  const pollRef = useRef(null);

  const load = async () => {
    try { const r = await apiFetch("/api/earnings_scan"); const d = await r.json(); setBoard(d); return d; }
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
      if (age > 30 * 60000) { apiFetch("/api/earnings_scan/scan").catch(() => {}); watchScan(); }
    });
    return () => pollRef.current && clearInterval(pollRef.current);
  }, []);
  const rescan = async () => {
    setErr(null);
    try { await apiFetch("/api/earnings_scan/scan?force=1"); } catch (e) { setErr(String(e)); return; }
    await load(); watchScan();
  };

  const status = (board && board.status) || {};
  const allRows = (board && board.rows) || [];
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
        case "ticker": return r.ticker || "";
        case "report": return r.report_date || "9999";
        case "price": return r.price ?? -1;
        case "chg": return r.change_pct ?? -999;
        case "relvol": return r.rel_volume ?? -1;
        case "im": return (r.implied && r.implied.pct) ?? -1;
        case "hist": return (r.hist && r.hist.avg_abs) ?? -1;
        case "ivh": return (r.iv_vs_hist && r.iv_vs_hist.ratio) ?? -1;
        case "ave": return (r.actual_vs_expected && r.actual_vs_expected.ratio) ?? -1;
        case "optvol": return r.options_volume ?? -1;
        case "iv": return r.atm_iv ?? -1;
        case "mcap": return r.market_cap ?? -1;
        default: return r.score ?? 0;
      }
    };
    return [...filtered].sort((a, b) => {
      const ka = key(a), kb = key(b);
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * -sortD;
    });
  }, [filtered, sortK, sortD]);

  const th = (label, k, tip) => (
    <th className={sortK === k ? "on" : ""} title={tip || `Sort by ${label}`}
        onClick={() => { if (sortK === k) setSortD(d => -d); else { setSortK(k); setSortD(-1); } }}>
      {label}{sortK === k ? (sortD === -1 ? " ↓" : " ↑") : ""}
    </th>
  );
  const chip = (k, label) => (
    <button key={k} type="button" className={`tsy-serbtn ${flt[k] ? "on" : ""}`}
            onClick={() => setFlt(f => ({ ...f, [k]: !f[k] }))}>{label}</button>
  );
  const counts = useMemo(() => {
    const c = {};
    for (const r of allRows) c[r.bucket] = (c[r.bucket] || 0) + 1;
    c.all = allRows.length;
    return c;
  }, [allRows]);

  return (
    <div className="eop">
      <EarnOpsAlerts rows={allRows} />
      <div className="card tsy-card">
        <div className="card-head">
          <div>
            <div className="kicker">Earnings opportunity scanner · your watchlist · −4 to +8 days</div>
            <div className="card-title">Earnings Opportunities</div>
          </div>
          <div className="tsy-ctrl">
            {board && board.spy_chg != null && <span className="tsy-datechip num" title="SPY day change — market alignment input to the score.">SPY {eopPct(board.spy_chg, 2)}</span>}
            <button className="scan-run-btn" onClick={rescan} disabled={!!status.scanning}>
              {status.scanning ? `Scanning… ${status.scanned || 0}/${status.total || 0}` : "Rescan"}
            </button>
          </div>
        </div>
        {demo && <div className="eop-demobar">DEMO DATA — live providers unavailable; rows are seeded examples so the workflow stays testable. Nothing here is a real quote.</div>}
        {err && <div className="tsy-err">{err}</div>}
        {status.last_scan && <div className="ab-status">Last scan {new Date(status.last_scan).toLocaleString()} · {allRows.length} candidates{status.error ? ` · ${status.error}` : ""}</div>}

        <div className="eop-sections">
          {EOP_SECTIONS.map(([k, label]) => (
            <button key={k} type="button" className={`eop-sec ${section === k ? "on" : ""}`} onClick={() => setSection(k)}>
              {label} <b>{counts[k] || 0}</b>
            </button>
          ))}
        </div>
        <div className="tsy-ctrl eop-filters">
          <select className="sb-select" value={flt.window} onChange={e => setFlt(f => ({ ...f, window: e.target.value }))}>
            <option value="all">Any date</option><option value="today">Today</option>
            <option value="tomorrow">Tomorrow</option><option value="week">This week</option>
            <option value="reported">Recently reported</option>
          </select>
          <select className="sb-select" value={flt.timing} onChange={e => setFlt(f => ({ ...f, timing: e.target.value }))}>
            <option value="all">BMO + AMC</option><option value="bmo">Before open</option><option value="amc">After close</option>
          </select>
          {chip("weeklyOnly", "Weeklys")}
          {chip("hiRelVol", "RelVol ≥1.5×")}
          {chip("hiOptVol", "OptVol ≥5k")}
          {chip("hiIV", "IV ≥60")}
          {chip("largeCap", "Large cap")}
          {chip("confirmedOnly", "Confirmed")}
          {chip("hideNoTrade", "Hide no-trade")}
          <span className="muted" style={{ fontSize: 11.5 }}>{sorted.length} shown</span>
        </div>

        <div className="eop-wrap">
          <table className="eop-table">
            <thead><tr>
              {th("Ticker", "ticker")}
              {th("Score", "score", "Earnings Opportunity Score 0–100 — liquidity, rel volume, options liquidity, weeklys, implied-vs-historical edge, move-vs-expected, confirmation, spread, market alignment, R:R. Click a row for the full breakdown.")}
              <th>Action</th><th>Setup · status</th>
              {th("Report", "report", "Report date + BMO/AMC (from the earnings-dates timestamp).")}
              {th("Price", "price")}
              {th("Day %", "chg", "Session-aware change (includes pre/after-market when that's the latest print).")}
              {th("RelVol", "relvol", "Today's volume ÷ average volume.")}
              {th("Imp move", "im", "ATM straddle mid — the option market's expected move. ± range shown beneath.")}
              {th("Hist move", "hist", "This name's own past earnings reactions: average |move|, median, last.")}
              {th("Imp/Hist", "ivh", "Implied ÷ historical average — RICH ≥1.3×, CHEAP ≤0.75×.")}
              {th("Act/Exp", "ave", "Post-print: |actual| ÷ expected. Basis = pre-print implied recorded by this scanner, else historical average (labeled in tooltip).")}
              {th("OptVol", "optvol", "Front-expiry options volume + open interest.")}
              {th("IV", "iv", "ATM implied volatility + ATM spread quality.")}
              <th title="Weekly options available">Wkly</th>
              {th("MCap", "mcap")}
            </tr></thead>
            <tbody>
              {sorted.map(r => (
                <EarnOpsRow key={r.ticker} r={r} demo={demo}
                            open={openTk === r.ticker}
                            onToggle={() => setOpenTk(openTk === r.ticker ? null : r.ticker)}
                            onOpenTicker={onOpenTicker} onOpenIntraday={onOpenIntraday} />
              ))}
            </tbody>
          </table>
          {!sorted.length && <div className="research-empty">{allRows.length ? "Nothing matches the filters." : status.scanning ? "Scanning your watchlist for earnings names…" : "No earnings candidates in the −4…+8 day window. Rescan after the watchlist board refreshes."}</div>}
        </div>
        <div className="tsy-foot">{board && board.note} Sources: watchlist board (earnings dates, mcap, sector) · Schwab quotes/chains/intraday (VWAP, levels) · yfinance history (past reactions, fallback). EPS consensus/surprise: no free source — not shown.</div>
      </div>
    </div>
  );
}

Object.assign(window, { EarningsOpsTab: React.memo(EarningsOpsTab) });
