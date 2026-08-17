// tab-gap.jsx — LAZY CHUNK, loaded on first Gap Scan open.
// Premarket Gap Fade & Rebound scanner: which gap ups historically fade,
// which gap downs historically rebound, with measured same-ticker history
// (sample sizes + Wilson intervals) behind every number. The board stays
// compact by design — evidence lives one click away in the detail view.
// Endpoints: GET /api/gap · /api/gap/scan · /api/gap/detail
// · /api/gap/events · /api/gap/backtest · /api/gap/config

const GAP_SIG_TONE = {
  "STRONG FADE": "up", "FADE": "up",
  "STRONG REBOUND": "up", "REBOUND": "up",
  "MIXED": "warn",
  "HOLD / CONTINUATION RISK": "down", "CONTINUATION LOWER RISK": "down",
  "NO DATA": "mut",
};

const gapPct = (v, d = 1) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
const gapNum = (v, d = 1) => (v == null ? "—" : Number(v).toFixed(d));
// Dates render as "Oct 28, 2026" — house rule: Month Day, Year, never ISO.
const gapDate = (s) => {
  if (!s) return "—";
  const d = new Date(String(s).length <= 10 ? `${s}T12:00:00` : s);
  return Number.isNaN(d.getTime()) ? String(s)
    : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
};
const gapTime = (s) => {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s)
    : d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" });
};
const gapWhen = (s) => {
  if (!s) return "—";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return String(s);
  return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })} ` +
    d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
};

// "82% ·44" — a probability NEVER renders without its sample size.
function GapProb({ r }) {
  if (!r || r.p == null) return <span className="muted">—</span>;
  return (
    <span title={`${r.k} of ${r.n} events · conservative (Wilson) range ${r.lo}–${r.hi}%`}>
      {Math.round(r.p)}%<small className="muted"> ·{r.n}</small>
    </span>
  );
}

function GapSigPill({ signal, held }) {
  const tone = GAP_SIG_TONE[signal] || "mut";
  return (
    <span className={`gap-sig gap-sig-${tone}`}
      title={held ? "signal held by hysteresis — the raw signal differs but hasn't persisted long enough to flip the display" : undefined}>
      {signal || "—"}{held ? " ·" : ""}
    </span>
  );
}

function GapQualityDot({ q }) {
  const cls = q === "HIGH" ? "hi" : q === "MODERATE" ? "mid" : "lo";
  return <i className={`gap-qdot gap-qdot-${cls}`}
    title={`Analog quality: ${q || "unknown"} — how similar the historical examples are to today's setup`} />;
}

// ── detail view (§27: enough evidence, no analytics dashboard) ──────────────

function GapDetail({ apiFetch, sym, onClose, onOpenTicker, liveQ }) {
  const [d, setD] = useState(null);
  const [evs, setEvs] = useState(null);
  const [bt, setBt] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let dead = false;
    setD(null); setEvs(null); setBt(null); setErr(null);
    apiFetch(`/api/gap/detail?symbol=${sym}`, { noCache: true })
      .then((r) => r.json())
      .then((x) => { if (!dead) (x.row || x.offline ? setD(x) : setErr(x.error || "no data")); })
      .catch((e) => !dead && setErr(String(e)));
    apiFetch(`/api/gap/events?symbol=${sym}`).then((r) => r.json())
      .then((x) => !dead && setEvs(x)).catch(() => {});
    apiFetch(`/api/gap/backtest?symbol=${sym}`).then((r) => r.json())
      .then((x) => !dead && setBt(x)).catch(() => {});
    return () => { dead = true; };
  }, [sym]);
  // Price and the gap it implies move every second; the statistics behind
  // them are history and cannot. Overlay the live quote on the fetched row.
  const r = d && d.row && (liveQ ? { ...d.row, ...liveQ } : d.row);
  const st = d && d.stats;
  return (
    <div className="card gap-detail">
      <div className="card-head">
        <div>
          <div className="card-title">{sym} · gap evidence
            <button className="rr-btn gap-back" onClick={onClose}>← board</button>
            <button className="rr-btn" onClick={() => onOpenTicker && onOpenTicker(sym)}
              title="Open this ticker on the Trade tab.">Trade tab →</button>
          </div>
          {r && <div className="card-sub">
            {r.population === "EARNINGS" ? "earnings-gap history" : "non-earnings history"} ·
            {" "}{r.data_basis} · store: {d.store_meta && d.store_meta.events} events
            ({d.store_meta && d.store_meta.minute_scanned} minute-scanned)
          </div>}
        </div>
      </div>
      {err && <div className="card-error">{err}</div>}
      {!d && !err && <div className="card-loading">Loading {sym} evidence…</div>}
      {d && d.offline && <div className="research-empty">Live quote unavailable — showing stored history only.</div>}
      {r && (
        <div>
          <div className="gap-sechead">Current setup</div>
          <div className="gap-hero">
            <GapSigPill signal={r.signal} held={r.signal_held} />
            <div className="gap-hero-nums">
              <div><span>Price</span><b>${gapNum(r.price, 2)}</b></div>
              <div><span>PM gap</span><b className={r.pm_gap_pct >= 0 ? "up" : "down"}>{gapPct(r.pm_gap_pct)}</b></div>
              <div><span>{r.direction === "up" ? "From PM high" : "From PM low"}</span>
                <b>{gapPct(r.direction === "up" ? r.from_pm_high_pct : r.from_pm_low_pct)}</b></div>
              <div><span>PM trend 30m</span><b>{gapPct(r.trend_30m_pct)}</b></div>
              <div><span>Catalyst</span><b>{r.catalyst_kind}{r.catalyst_label ? ` · ${r.catalyst_label}` : ""}</b></div>
              {r.sector && <div><span>Sector ({r.sector.etf})</span>
                <b>{gapPct(r.sector.etf_gap_pct)} · {r.sector.label}</b></div>}
              {r.quote_age_s != null && <div><span>Quote age</span><b>{Math.round(r.quote_age_s)}s</b></div>}
            </div>
            {r.signal_why && <div className="gap-why">{r.signal_why}</div>}
            {r.what_changed && <div className="gap-changed">Changed: {r.what_changed}</div>}
          </div>

          {st && st.n > 0 && (
            <div>
              <div className="gap-sechead">What happened after similar gaps
                <span className="muted"> · n={st.n} <GapQualityDot q={r.cohort_quality} />
                  {r.cohort_scope === "all_same_direction" ? " (widened to all same-direction gaps)" : " (size-matched)"}</span>
              </div>
              <div className="gap-grid2">
                <div className="gap-block">
                  <div className="gap-bt">{r.direction === "up" ? "Faded at least…" : "Rebounded at least…"}</div>
                  {["1", "2", "3", "5"].map((lv) => (
                    <div key={lv} className="gap-prow">
                      <span>{lv}%</span>
                      <div className="gap-ptrack">
                        {st.p_fav[lv] && <i style={{ width: `${st.p_fav[lv].p}%` }} />}
                        {st.p_fav[lv] && <em style={{ left: `${st.p_fav[lv].lo}%` }}
                          title={`conservative bound ${st.p_fav[lv].lo}%`} />}
                      </div>
                      <b><GapProb r={st.p_fav[lv]} /></b>
                    </div>
                  ))}
                </div>
                <div className="gap-block">
                  <div className="gap-bt">Risk first</div>
                  <div className="gap-kv"><span>2% target before 3% stop</span>
                    <b>{st.tbs ? <GapProb r={st.tbs} /> :
                      <span className="muted" title="No minute-path events yet — daily bars cannot order target vs stop.">UNKNOWN / DAILY ONLY</span>}</b></div>
                  <div className="gap-kv"><span>Median adverse first (MAE)</span>
                    <b>{gapPct(st.mae_med_pct != null ? st.mae_med_pct : st.med_adv_pct)}</b></div>
                  <div className="gap-kv"><span>90th pct adverse</span>
                    <b>{gapPct(st.mae_p90_pct != null ? st.mae_p90_pct : st.adv_p90_pct)}</b></div>
                  <div className="gap-kv"><span>95th pct adverse</span>
                    <b>{gapPct(st.mae_p95_pct != null ? st.mae_p95_pct : st.adv_p95_pct)}</b></div>
                  <div className="gap-kv gap-worst"><span>Worst analog</span>
                    <b>moved {gapPct(st.worst_adv_pct)} against · {gapDate(st.worst_adv_date)}</b></div>
                  {st.mae_before_target_med_pct != null &&
                    <div className="gap-kv"><span>Median squeeze before target</span>
                      <b>{gapPct(st.mae_before_target_med_pct)}</b></div>}
                </div>
                <div className="gap-block">
                  <div className="gap-bt">Timing & tendencies</div>
                  <div className="gap-kv"><span>Median time to 2%</span>
                    <b>{st.med_time_to_min && st.med_time_to_min["2"] != null
                      ? `${st.med_time_to_min["2"]} min` : "—"}</b></div>
                  <div className="gap-kv"><span>Gap filled</span><b><GapProb r={st.gap_fill} /></b></div>
                  <div className="gap-kv"><span>Continued {r.direction === "up" ? "higher" : "lower"}</span>
                    <b><GapProb r={st.continuation} /></b></div>
                  {st.ev && st.ev.mean_pct != null &&
                    <div className="gap-kv"><span>Empirical EV per trade</span>
                      <b className={st.ev.mean_pct > 0 ? "up" : "down"}>{gapPct(st.ev.mean_pct, 2)}</b></div>}
                  {st.ev && st.ev.basis && <div className="gap-note">{st.ev.basis}</div>}
                </div>
              </div>
              <div className="gap-note">Evidence basis: {st.basis}
                {st.tbs && st.tbs.intrabar_modeled_share > 0 &&
                  ` · ${Math.round(st.tbs.intrabar_modeled_share * 100)}% of orderings INTRABAR MODELED (same-minute ties resolved against the trade)`}
                {" "}· probabilities show conservative Wilson ranges on hover</div>
            </div>
          )}
          {st && !st.n && <div className="research-empty">No comparable
            {r.population === "EARNINGS" ? " earnings-gap" : ""} history for this
            setup — the store fills as mornings accumulate.</div>}

          {bt && bt.grid && bt.grid.length > 0 && (
            <div>
              <div className="gap-sechead">Target / stop grid <span className="muted">· walk-forward, this ticker's measured paths</span></div>
              <div className="scan-table-wrap">
                <table className="scan-table gap-bt-table">
                  <thead><tr>
                    <th>Dir</th>
                    <th className="scan-th-num" title="Take-profit distance from the open.">Target</th>
                    <th className="scan-th-num" title="Stop distance. Stops model fill-through, not a fill at the stop price.">Stop</th>
                    <th className="scan-th-num">n</th>
                    <th className="scan-th-num" title="Percent of events where the target printed before the stop.">Win %</th>
                    <th className="scan-th-num" title="Mean simulated net return per trade over the actual paths.">Expectancy</th>
                    <th className="scan-th-num" title="First chronological half.">H1</th>
                    <th className="scan-th-num" title="Second chronological half. A pair is only trustworthy when both halves are positive.">H2</th>
                    <th className="scan-th-num" title="Worst single simulated trade.">Worst</th>
                    <th title="Positive expectancy in BOTH halves — survives walk-forward.">Robust</th>
                  </tr></thead>
                  <tbody>
                    {bt.grid.slice(0, 10).map((g, i) => (
                      <tr key={i} className={g.robust ? "gap-row-robust" : ""}>
                        <td>{g.direction === "up" ? "fade" : "rebound"}</td>
                        <td className="scan-num">{g.target_pct}%</td>
                        <td className="scan-num">{g.stop_pct}%</td>
                        <td className="scan-num">{g.n}</td>
                        <td className="scan-num">{g.win_rate}%</td>
                        <td className={`scan-num ${g.expectancy_pct > 0 ? "up" : "down"}`}>{gapPct(g.expectancy_pct, 2)}</td>
                        <td className="scan-num">{gapPct(g.h1_pct, 2)}</td>
                        <td className="scan-num">{gapPct(g.h2_pct, 2)}</td>
                        <td className="scan-num down">{gapPct(g.worst_pct, 2)}</td>
                        <td>{g.robust ? "✓" : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="gap-note">{bt.note}</div>
            </div>
          )}

          {evs && evs.events && evs.events.length > 0 && (
            <div>
              <div className="gap-sechead">The analogs <span className="muted">· every event behind the numbers</span></div>
              <div className="scan-table-wrap">
                <table className="scan-table gap-ev-table">
                  <thead><tr>
                    <th>Date</th><th>Dir</th>
                    <th className="scan-th-num" title="Official open gap.">Gap</th>
                    <th className="scan-th-num" title="Biggest premarket gap that day (where minute data exists).">PM max</th>
                    <th title="OFFICIAL = qualified by the open gap · PM = reached the premarket threshold (even if it opened small).">Via</th>
                    <th>Catalyst</th>
                    <th className="scan-th-num" title="Favorable move from the open (fade for gap ups, rebound for gap downs).">Fav</th>
                    <th className="scan-th-num" title="Adverse move from the open.">Adv</th>
                    <th title="MINUTE PATH = real ordering measured · DAILY ONLY = no ordering claims.">Basis</th>
                  </tr></thead>
                  <tbody>
                    {evs.events.slice(0, 30).map((e, i) => (
                      <tr key={i} className={e.exclusion ? "gap-row-excl" : ""}
                        title={e.exclusion ? `excluded: ${e.exclusion}` : (e.delayed_open ? "delayed open" : "")}>
                        <td>{gapDate(e.date)}</td>
                        <td>{e.direction === "up" ? "▲" : "▼"}</td>
                        <td className="scan-num">{gapPct(e.official_gap_pct)}</td>
                        <td className="scan-num">{gapPct(e.pm_gap_max_pct)}</td>
                        <td>{(e.qualified_by || []).join("+")}</td>
                        <td>{e.catalyst_kind === "EARNINGS" ? <b>EARN</b> : "—"}</td>
                        <td className="scan-num up">{gapPct(e.fav_pct)}</td>
                        <td className="scan-num down">{gapPct(e.adv_pct)}</td>
                        <td className="muted">{e.exclusion || e.basis}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── main tab ────────────────────────────────────────────────────────────────

function GapTab({ apiFetch, onOpenTicker }) {
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
      const r = await apiFetch("/api/gap", { noCache: true });
      const d = await r.json();
      setBoard(d); setErr(null);
      return d;
    } catch (e) { setErr(String(e)); return null; }
  };
  const watchScan = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (d && !(d.status && d.status.scanning)) {
        clearInterval(pollRef.current); pollRef.current = null;
      }
    }, 4000);
  };
  useEffect(() => {
    load().then((d) => {
      if (!d) return;
      if (d.status && d.status.scanning) { watchScan(); return; }
      const age = d.as_of ? Date.now() - new Date(d.as_of).getTime() : Infinity;
      if (!d.rows || !d.rows.length || age > 20 * 60000) {
        apiFetch("/api/gap/scan").catch(() => {});
        watchScan();
      }
    });
    const iv = setInterval(skipWhenHidden(load), 60 * 1000);
    return () => { clearInterval(iv); pollRef.current && clearInterval(pollRef.current); };
  }, []);

  // Live price ticker: one batched quote call, prices only. A full scan is
  // expensive and runs every few minutes, so without this the board would
  // show the price frozen at the last scan while the stock keeps moving.
  useEffect(() => {
    const tick = async () => {
      try {
        const r = await apiFetch("/api/gap/live", { noCache: true });
        const d = await r.json();
        if (d && d.ok && d.quotes) { setLive(d.quotes); setLiveAt(d.as_of); }
      } catch (e) { /* keep the last known price */ }
    };
    tick();
    const iv = setInterval(skipWhenHidden(tick), 15 * 1000);
    return () => clearInterval(iv);
  }, []);

  const rows = useMemo(() => {
    const base = (board && board.rows) || [];
    if (!Object.keys(live).length) return base;
    return base.map((r) => (live[r.symbol] ? { ...r, ...live[r.symbol] } : r));
  }, [board, live]);
  const sorted = useMemo(() => {
    const key = (r) => {
      switch (sortK) {
        case "symbol": return r.symbol || "";
        case "gap": return -Math.abs(r.pm_gap_pct ?? 0);
        case "p2": return -((r.p_fav && r.p_fav.p) ?? -1);
        case "tbs": return -(r.tbs_p ?? -1);
        case "adv": return r.med_adverse_pct ?? 99;
        case "n": return -(r.n ?? 0);
        default: return 0;               // rank = server order
      }
    };
    const s = [...rows];
    if (sortK !== "rank") s.sort((a, b) => {
      const ka = key(a), kb = key(b);
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * sortD;
    });
    return s;
  }, [rows, sortK, sortD]);
  const [shown, moreControls] = useBoundedList(sorted, 40, 60);

  const th = (label, k, tip, hideMobile) => (
    <th className={`${k === "symbol" ? "" : "scan-th-num"}${hideMobile ? " gap-hidemobile" : ""}`}
      title={tip}
      onClick={() => {
        if (sortK === k) setSortD(-sortD);
        else { setSortK(k); setSortD(1); }
      }}>
      {label}{sortK === k ? (sortD === 1 ? " ↓" : " ↑") : ""}
    </th>
  );

  const scanning = board && board.status && board.status.scanning;
  const ctx = (board && board.context) || {};
  return (
    <div className="card gap-card">
      {gapSym ? (
        <GapDetail apiFetch={apiFetch} sym={gapSym} liveQ={live[gapSym]}
          onClose={() => setGapSym(null)} onOpenTicker={onOpenTicker} />
      ) : (
        <div>
          <div className="card-head">
            <div>
              <div className="card-title">Gap Scan · premarket fade & rebound</div>
              <div className="card-sub">When this stock moved like this before the open,
                what happened next — measured history, not "gaps usually fill".</div>
            </div>
            <button className="scan-run-btn" disabled={!!scanning}
              title="Re-scan premarket movers now (quote sweep → minute history → same-ticker gap statistics)."
              onClick={() => { apiFetch("/api/gap/scan?force=1").catch(() => {}); watchScan(); }}>
              {scanning ? `Scanning ${board.status.scanned}/${board.status.total || "…"}` : "Scan now"}
            </button>
          </div>
          {err && <div className="card-error">{err}</div>}
          {board && board.status && board.status.error &&
            <div className="card-error">last scan failed: {board.status.error}</div>}
          {!board && !err && <div className="card-loading">Loading board…</div>}
          {board && (
            <div>
              <div className="gap-ctxline muted">
                {board.session === "premarket" ? "premarket" : "market hours"} ·
                SPY {gapPct(ctx.spy_gap_pct)} · QQQ {gapPct(ctx.qqq_gap_pct)} ·
                {" "}<span className="gap-livedot" title="Prices refresh every 15 seconds; the statistics update on each full scan." />
                prices live {gapTime(liveAt || board.price_as_of)} ·
                statistics as of {gapWhen(board.as_of)}
              </div>
              <div className="scan-table-wrap">
                <table className="scan-table gap-table">
                  <thead><tr>
                    {th("Ticker", "symbol", "Premarket mover. Click for the full evidence view.")}
                    {th("Price", "rank", "Current premarket reference price.", true)}
                    {th("PM Gap", "gap", "Live premarket gap vs prior regular close. This is NOT the official open gap — it moves until 9:30.")}
                    {th("Off Hi/Lo", "rank", "Gap ups: % below the premarket high seen so far. Gap downs: % above the premarket low. 'So far' — the final PM high/low doesn't exist yet.", true)}
                    {th("Catalyst", "rank", "EARNINGS / ANALYST ACTION / MACRO from real data sources; UNTAGGED otherwise. Earnings gaps are judged only against this stock's other earnings gaps.", true)}
                    {th("P 2%", "p2", "How often this stock's similar gaps faded (gap up) or rebounded (gap down) at least 2% from the open. Always shown with sample size; hover for the conservative range.")}
                    {th("Tgt<Stop", "tbs", "Probability the 2% target printed BEFORE a 3% stop, from real minute-by-minute paths. Blank = only daily bars, and daily bars cannot order events.")}
                    {th("Med Adv", "adv", "Median move AGAINST the trade first (MAE). A fade that squeezes +4% before working is not a comfortable short.", true)}
                    {th("n", "n", "Number of comparable historical events. No n, no probability.", true)}
                    {th("Signal", "rank", "Evidence-gated: STRONG needs the favorable rate AND target-before-stop AND tail control on the conservative bounds. Never from one probability alone.")}
                  </tr></thead>
                  <tbody>
                    {shown.map((r) => (
                      <tr key={r.symbol} className="scan-row gap-row"
                        onClick={() => r.data_ok !== false && setGapSym(r.symbol)}
                        title={r.what_changed || r.signal_why || r.error || ""}>
                        <td><b>{r.symbol}</b> <GapQualityDot q={r.cohort_quality} /></td>
                        <td className="scan-num gap-hidemobile">{r.price != null ? `$${gapNum(r.price, 2)}` : "—"}</td>
                        <td className={`scan-num ${(r.pm_gap_pct ?? 0) >= 0 ? "up" : "down"}`}><b>{gapPct(r.pm_gap_pct)}</b></td>
                        <td className="scan-num gap-hidemobile">{gapPct(r.direction === "up" ? r.from_pm_high_pct : r.from_pm_low_pct)}</td>
                        <td className="gap-hidemobile">{r.catalyst_kind === "UNTAGGED" ? <span className="muted">—</span> : r.catalyst_kind}</td>
                        <td className="scan-num"><GapProb r={r.p_fav} /></td>
                        <td className="scan-num">{r.tbs_p != null ? `${Math.round(r.tbs_p)}%` :
                          <span className="muted" title="UNKNOWN / DAILY ONLY — no minute paths yet for this cohort">—</span>}</td>
                        <td className="scan-num gap-hidemobile">{gapPct(r.med_adverse_pct)}</td>
                        <td className="scan-num gap-hidemobile muted">{r.n ?? 0}</td>
                        <td><GapSigPill signal={r.signal} held={r.signal_held} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {moreControls}
              {!rows.length && !scanning && (
                <div className="research-empty">{board.as_of
                  ? "No premarket movers past the gap threshold right now — the board fills when stocks actually gap. Auto-scans run every few minutes from 7:00 AM ET."
                  : "No scan yet — hit “Scan now”. Most useful 7:00–9:30 AM ET when premarket movers exist."}</div>
              )}
              {board.note && <div className="gap-note">{board.note}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Chunk registration (house pattern — verify_frontend checks this).
Object.assign(window, { GapTab: React.memo(GapTab) });
