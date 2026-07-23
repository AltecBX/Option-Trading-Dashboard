// tab-patterns.jsx — LAZY CHUNK (v3.64), split from app-cards.jsx.
// Loaded on demand the first time the Patterns tab opens (see LazyTab in
// app-lib.jsx); never part of the initial page load. Loads after every
// eager file, so all window exports are available.

// ── Per-stock Pattern Discovery (v3.44) ─────────────────────────────────────
// Event-study sweep over the selected stock's OWN history: thresholds adapt
// to its return/gap/drawdown distributions, claims are fitted in-sample
// (first 70%) and validated out-of-sample (last 30%), every hit rate is
// compared with the baseline chance of the same move after any random day,
// and weak/small-sample edges are flagged instead of hidden. One click sends
// any pattern to the Backtest Lab or registers it as a live watch/alert.
function PDPathChart({ chart, claim }) {
  if (!chart || !chart.avg_path) return null;
  const { lead, avg_path, median_path, p25_path, p75_path, occurrences } = chart;
  const W = 620, H = 150, PAD = 8;
  const all = [];
  avg_path.forEach(v => { if (v != null) all.push(v); });
  (occurrences || []).forEach(o => o.path.forEach(v => { if (v != null) all.push(v); }));
  if (!all.length) return null;
  const lo = Math.min(...all), hi = Math.max(...all);
  const span = Math.max(0.5, hi - lo);
  const n = avg_path.length;
  const x = k => PAD + (W - 2 * PAD) * (k / (n - 1));
  const y = v => H - PAD - (H - 2 * PAD) * ((v - lo) / span);
  const line = (path) => (path || []).map((v, k) => v == null ? null : `${x(k).toFixed(1)},${y(v).toFixed(1)}`)
    .filter(Boolean).join(" ");
  return (
    <div className="pd-chart" title={`Every historical occurrence (grey), the AVERAGE path (bold), the MEDIAN path (solid thin), and the 25th–75th percentile band (dashed), from ${lead} days before the signal (dashed vertical line) through ${n - 1 - lead} days after. Y-axis: % change from the signal-day reference price.`}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <line x1={x(lead)} x2={x(lead)} y1={PAD} y2={H - PAD} className="pd-chart-sig" />
        <line x1={PAD} x2={W - PAD} y1={y(0)} y2={y(0)} className="pd-chart-zero" />
        {(occurrences || []).map((o, i) => (
          <polyline key={i} points={line(o.path)} className="pd-chart-occ" />
        ))}
        {p25_path && <polyline points={line(p25_path)} className="pd-chart-band" />}
        {p75_path && <polyline points={line(p75_path)} className="pd-chart-band" />}
        {median_path && <polyline points={line(median_path)} className="pd-chart-med" />}
        <polyline points={line(avg_path)} className={`pd-chart-avg ${claim && claim.dir === "up" ? "up" : "down"}`} />
      </svg>
      <div className="pd-chart-lbls">
        <span>day −{lead}</span><span>signal</span><span>day +{n - 1 - lead}</span>
      </div>
    </div>
  );
}

const PD_LABEL_TIP = {
  "reliable": "Survived every check: enough occurrences, out-of-sample held up, stable across time folds, and beat baseline after multiple-testing correction.",
  "unstable": "The edge exists in some periods but swings widely across time folds or drops sharply out-of-sample — position sizing should not trust the headline rate.",
  "weakening": "The long-run rate is solid but the most recent fold is performing well below it — the behavior may be fading.",
  "likely random": "Did not beat the baseline convincingly after correcting for the hundreds of candidates searched — treat as noise.",
  "insufficient sample": "Too few independent occurrences to say anything honest.",
};

function PDScanBox({ apiFetch, p }) {
  const [rows, setRows] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = () => {
    setBusy(true);
    apiFetch("/api/patterns/scan", { method: "POST", body: JSON.stringify({ family: p.family, params: p.params }) })
      .then(r => r.json()).then(d => { setBusy(false); setRows(d.rows || []); })
      .catch(() => setBusy(false));
  };
  return (
    <span className="pd-scanbox">
      <button className="rr-btn" disabled={busy} onClick={run}
              title="Scan your starred watchlist for this same setup: which symbols are triggered RIGHT NOW, and how the identical event has resolved on each symbol's own history (compare across stocks).">
        {busy ? "scanning…" : "⌕ Scan watchlist"}</button>
      {rows && (
        <span className="pd-scan-res" title="triggered = setup true on the latest bar · % = share of that symbol's own occurrences closing higher 5 days later.">
          {rows.length === 0 ? "no matches" : rows.slice(0, 8).map(r =>
            `${r.symbol}${r.triggered ? "●" : ""} ${r.up_rate_5d == null ? "—" : r.up_rate_5d + "%"}`).join(" · ")}
        </span>
      )}
    </span>
  );
}

function PDRow({ p, sym, onBacktest, onOptBacktest, onWatch, watching, apiFetch }) {
  const [open, setOpen] = useState(false);
  const act = p.actionability != null ? p.actionability : p.confidence;
  const actCls = act >= 70 ? "hi" : act >= 50 ? "mid" : "lo";
  const M = p.move || {};
  const FT = p.first_touch;
  const label = p.label || (p.confidence >= 70 ? "reliable" : "unstable");
  return (
    <div className={`pd-row ${open ? "open" : ""}`}>
      <button className="pd-head" onClick={() => setOpen(!open)}
              title="Click to expand: full statistics, first-touch analysis, validation detail, condition breakdown, and the occurrence chart.">
        <span className={`pd-conf ${actCls}`}
              title={`ACTIONABILITY ${act}/100 — ranks how tradeable this is, not just how often it hit: net expected value after estimated spread+slippage (${p.ev_net_pct != null ? p.ev_net_pct + "%" : "n/a"}/trade), out-of-sample performance, sample size, fold consistency, reward-vs-risk (MFE/MAE), speed, and liquidity. Statistical confidence is ${p.confidence}/100.`}>
          {act}
        </span>
        <span className={`pd-label pd-l-${label.replace(/[^a-z]/g, "")}`} title={PD_LABEL_TIP[label] || label}>{label}</span>
        <span className="pd-kinds">
          {p.kind.map(k => <em key={k} className={`pd-kind pd-k-${k.replace(/[^a-z]/g, "")}`}>{k}</em>)}
          {p.triggered_now && <em className="pd-kind pd-k-now" title="This setup is TRUE on the latest bar — see the Current Setup section above.">active now</em>}
        </span>
        <span className="pd-sentence">{p.sentence}</span>
        <span className="pd-arrow">{open ? "▾" : "▸"}</span>
      </button>
      {p.flags.length > 0 && (
        <div className="pd-flags" title="Statistical health warnings — reasons to distrust this pattern.">
          {p.flags.map((f, i) => <span key={i}>⚠ {f}</span>)}
        </div>
      )}
      {open && (
        <div className="pd-body">
          <div className="pd-stats">
            <div title="Independent occurrences across ~10 years (overlap-purged: outcome windows never stack, so the same episode isn't counted twice)."><span>occurrences</span><b>{p.n}</b></div>
            <div title="How often the claimed move followed, across ALL occurrences."><span>hit rate</span><b>{p.hit_rate}%</b></div>
            <div title="Hit rate on the first 70% of history — the data the claim was FITTED on."><span>in-sample</span><b>{p.hit_rate_is}%</b></div>
            <div title="Hit rate on the last 30% — data the claim never saw."><span>out-of-sample</span><b>{p.hit_rate_oos == null ? "n/a" : p.hit_rate_oos + "%"}</b></div>
            <div title="How often the SAME move happens after any random day — the bar to beat."><span>baseline</span><b>{p.baseline_rate}%</b></div>
            <div title={`Binomial p-value vs baseline; q-value after Benjamini-Hochberg correction across ALL candidates searched. A pattern must survive q≤0.10 or it is labeled likely random.`}><span>p / q</span><b>{p.p_value}{p.q_value != null ? ` / ${p.q_value}` : ""}</b></div>
            <div title="Bootstrap 5–95% confidence interval on the hit rate (400 resamples). Wide interval = small sample, don't trust the point estimate."><span>hit-rate CI</span><b>{p.boot_ci ? `${p.boot_ci[0]}–${p.boot_ci[1]}%` : "—"}</b></div>
            <div title="Walk-forward: hit rate in each chronological quarter of the occurrences. Stable numbers = the edge persisted; wild swings = regime-dependent."><span>folds</span><b>{(p.folds || []).join(" · ") || "—"}</b></div>
            <div title="Average / median move in the claimed direction."><span>avg / med</span><b>{M.avg}% / {M.median}%</b></div>
            <div title="25th–75th percentile of the outcome distribution in the claimed direction."><span>p25 / p75</span><b>{M.p25}% / {M.p75}%</b></div>
            <div title="Best and worst outcomes across occurrences."><span>max / min</span><b>{M.max}% / {M.min}%</b></div>
            <div title="Net expected value per trade after estimated bid/ask spread and slippage on both sides — a 70% hit rate with negative net EV is untradeable."><span>EV (net)</span><b className={p.ev_net_pct >= 0 ? "up" : "down"}>{p.ev_net_pct}%</b></div>
            <div title="Median trading days until the claimed move was reached."><span>days to move</span><b>{p.days_to_move_median == null ? "—" : p.days_to_move_median}</b></div>
            <div title="Average maximum favorable excursion inside the window (daily highs; intraday order approximate)."><span>avg MFE</span><b className="up">{p.mfe_avg}%</b></div>
            <div title="Average maximum adverse excursion inside the window (daily lows; approximate)."><span>avg MAE</span><b className="down">{p.mae_avg}%</b></div>
          </div>
          {FT && (
            <div className="pd-ft" title={`FIRST-TOUCH race: which level got hit first after the signal — the ${FT.target_pct}% target or the ${FT.stop_pct}% stop. 'Ambiguous' = both inside the same daily bar (order unknowable from daily data) and is counted AGAINST the pattern. Median ${FT.median_days_to_target ?? "—"} days to target / ${FT.median_days_to_stop ?? "—"} days to stop.`}>
              <b>First touch:</b> target ({FT.target_pct}%) first in <b className="up">{FT.p_target_first}%</b> ·
              stop ({FT.stop_pct}%) first in <b className="down">{FT.p_stop_first}%</b> ·
              neither {FT.p_neither}% · ambiguous {FT.p_ambiguous}%
            </div>
          )}
          {p.context_note && <div className="pd-ctxnote" title="The largest works-vs-fails split across the context buckets below.">{p.context_note}</div>}
          <div className="pd-ctx" title="Occurrences bucketed by SPY trend, QQQ trend, the stock's sector-ETF trend, market volatility state, the stock's own volatility state, the event day's gap direction, relative volume, and calendar year. Buckets under 5 occurrences are hidden. Historical earnings/news/IV/flow context is not available in this app's data.">
            {Object.entries(p.context || {}).map(([cat, buckets]) => (
              Object.keys(buckets).length > 0 && (
                <span key={cat} className="pd-ctx-cat">{cat}: {Object.entries(buckets)
                  .map(([lbl, d]) => `${lbl} ${d.rate}% (${d.n})`).join(", ")}</span>
              )
            ))}
          </div>
          <PDPathChart chart={p.chart} claim={p.claim} />
          {(p.chart && p.chart.occurrences || []).length > 0 && (
            <div className="pd-occs" title="Sampled historical occurrences (up to 30) with each one's forward move over the window.">
              {p.chart.occurrences.slice(-12).map((o, i) => (
                <span key={i} className={o.fwd >= 0 ? "up" : "down"}>{o.date} {o.fwd > 0 ? "+" : ""}{o.fwd}%</span>
              ))}
            </div>
          )}
          {p.options_idea && (
            <div className="pd-opt" title="A starting options structure sized to the pattern's expected move and time window — NOT a recommendation. Premiums in the backtester are modeled (no historical option quotes).">
              <b>Options idea:</b> {p.options_idea.note}
            </div>
          )}
          <div className="pd-actions">
            <button className="rr-btn" onClick={() => onBacktest(p)}
                    title="Open this pattern in the Backtest Lab with entries, direction, profit target, stop and time exit prefilled — edit anything, then run with full cost/liquidity modeling.">→ Backtest</button>
            {onOptBacktest && p.options_idea && (
              <button className="rr-btn" onClick={() => onOptBacktest(p)}
                      title="Same conversion, but as an OPTION strategy: long calls/puts matching the claim direction, DTE sized to the window. Premiums are model-priced — the backtest says so loudly.">→ Options backtest</button>
            )}
            <button className="rr-btn" onClick={() => onWatch(p)}
                    title={watching ? "Stop watching this pattern." : "Watch this pattern live: checked against fresh daily data every 30 min in market hours, with a push alert the day the setup fires again."}>
              {watching ? "★ watching — remove" : "⚑ Watch / alert"}</button>
            <PDScanBox apiFetch={apiFetch} p={p} />
          </div>
        </div>
      )}
    </div>
  );
}

function PDCurrentSetup({ cs, lastClose, earnDays }) {
  if (!cs || !cs.active || cs.active.length === 0) {
    return (
      <div className="pd-cs pd-cs-empty" title="None of the discovered patterns is triggered on the latest daily bar. That's an answer too: no statistical setup is active right now.">
        <b>Current setup:</b> no discovered pattern is active on the latest bar ({cs && cs.as_of}).
      </div>
    );
  }
  return (
    <div className="pd-cs">
      <div className="bt-sec-title" title="Patterns whose setup is TRUE on the latest daily bar, ranked by actionability adjusted for how closely today's conditions match the conditions in which each pattern historically worked. Top 3 shown expanded.">
        Current setup — active now ({cs.as_of}){earnDays != null && earnDays >= 0 && earnDays <= 7 ? ` · ⚠ earnings in ${earnDays}d` : ""}
      </div>
      {cs.top3.map((a, rank) => (
        <div key={a.id} className="pd-cs-row">
          <span className="pd-cs-rank" title="Rank by actionability × today's similarity to past occurrences.">#{rank + 1}</span>
          <div className="pd-cs-main">
            <div className="pd-cs-sent">{a.sentence}</div>
            <div className="pd-cs-grid">
              <span title="Actionability adjusted for today's context match."><em>score</em><b>{a.actionability_now}</b></span>
              <span title="How closely today's market/volatility context matches the conditions in which this pattern historically worked (context-bucket match)."><em>similarity</em><b>{a.similarity}%</b></span>
              <span title="Most likely move (median of all historical occurrences), with the 25th–75th percentile band."><em>expected</em><b>{a.expected.median_pct > 0 ? "+" : ""}{a.expected.median_pct}% ({a.expected.p25_pct}…{a.expected.p75_pct}%)</b></span>
              <span title={`Probability the target level is touched before the stop level (first-touch race, ambiguous bars counted against).`}><em>target {fmt$(a.levels.target_px)}</em><b className="up">{a.levels.target_prob}%</b></span>
              <span title="Probability the stop level is touched first."><em>stop {fmt$(a.levels.stop_px)}</em><b className="down">{a.levels.stop_prob}%</b></span>
              <span title="Below this price the move would be worse than ~75% of all historical occurrences — the pattern is statistically invalidated."><em>invalid &lt;</em><b>{fmt$(a.levels.invalidation_px)}</b></span>
              <span title="Median trading days the move historically needed."><em>typical</em><b>{a.typical_days == null ? "—" : a.typical_days + "d"}</b></span>
            </div>
          </div>
        </div>
      ))}
      {cs.active.length > 3 && (
        <div className="pd-cs-more" title="Additional active patterns, in the ranked list below (marked 'active now').">
          +{cs.active.length - 3} more active — marked in the list below.
        </div>
      )}
    </div>
  );
}

function PDAskBox({ apiFetch, ticker, onBacktest, onWatch, watches }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const ask = () => {
    if (!q.trim()) return;
    setBusy(true); setRes(null);
    apiFetch("/api/patterns/ask", { method: "POST", body: JSON.stringify({ text: q, symbol: ticker }) })
      .then(r => r.json()).then(d => { setBusy(false); setRes(d); })
      .catch(e => { setBusy(false); setRes({ error: String(e) }); });
  };
  return (
    <div className="pd-ask">
      <div className="pd-ask-row">
        <input value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => { if (e.key === "Enter") ask(); }}
               placeholder={`Ask: "What does ${ticker} usually do after rising more than 10% in 3 days?"`}
               title="Natural-language research: the question is parsed into visible rules (same grammar as the Backtest Lab) and answered with the full event-study machinery — occurrences, hit rate vs baseline, first-touch race, validation labels. Questions needing data the app doesn't have (news days, historical earnings dates, historical IV) get an honest 'can't test' answer." />
        <button className="rr-btn bt-go" disabled={busy || !q.trim()} onClick={ask}
                title="Run the question against ~10 years of history.">{busy ? "researching…" : "Ask →"}</button>
      </div>
      {res && res.error && <div className="bt-warn bt-err">{res.error}</div>}
      {res && (res.warnings || []).filter(w => w.indexOf("exit") === -1 && w.indexOf("entry condition") === -1).map((w, i) =>
        <div key={i} className="bt-warn">⚠ {w}</div>)}
      {res && res.conditions && !res.error && (
        <div className="pd-ask-conds" title="The exact rules your question was translated into — nothing is guessed silently.">
          understood as: {res.conditions.map(c => c.label || c.type).join(" AND ")} (on {res.symbol})
        </div>
      )}
      {res && res.answer && !res.pattern && <div className="pd-ask-ans">{res.answer}</div>}
      {res && res.pattern && (
        <PDRow p={res.pattern} sym={res.symbol} apiFetch={apiFetch}
               onBacktest={onBacktest} onOptBacktest={null}
               onWatch={onWatch} watching={watches.some(w => w.id === `${res.symbol}::${res.pattern.id}`)} />
      )}
    </div>
  );
}

function PDIntraday({ apiFetch, ticker }) {
  const [res, setRes] = useState(null);
  const [job, setJob] = useState(null);
  const [progress, setProgress] = useState(null);
  const [err, setErr] = useState(null);
  const pollRef = useRef(null);
  useEffect(() => {
    setRes(null); setErr(null);
    apiFetch(`/api/patterns/intraday?symbol=${encodeURIComponent(ticker)}`)
      .then(r => r.json()).then(d => { if (d && d.sequences) setRes(d); }).catch(() => {});
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [ticker]);
  const mine = () => {
    setErr(null); setProgress({ phase: "starting", done: 0, total: 1 });
    apiFetch("/api/patterns/intraday", { method: "POST", body: JSON.stringify({ symbol: ticker }) })
      .then(r => r.json()).then(d => {
        if (!d.job) { setProgress(null); setErr(d.error || "could not start"); return; }
        pollRef.current = setInterval(() => {
          apiFetch(`/api/patterns/intraday?job=${d.job}`).then(r => r.json()).then(s => {
            if (s.progress) setProgress(s.progress);
            if (s.status === "done" || s.status === "error") {
              clearInterval(pollRef.current); pollRef.current = null; setProgress(null);
              if (s.result && s.result.error) setErr(s.result.error);
              else setRes(s.result);
            }
          }).catch(() => {});
        }, 1500);
      }).catch(e => { setProgress(null); setErr(String(e)); });
  };
  return (
    <div className="pd-intra">
      <div className="pd-intra-head">
        <div className="bt-sec-title" title="Sequence discovery on 1-MINUTE bars: each session is tokenized into an ordered event grammar (gap direction, holds above open 30 min, opening-range breaks, pullback to VWAP, loses/reclaims VWAP, reclaims the morning high, power hour) and recurring ordered sequences are mined automatically — with EXACT intraday ordering, which daily bars cannot give. Outcomes = the move from the minute the sequence completed to the close. Minute data reaches ~6 months back, and every mined session is archived on disk, so coverage grows the longer the app runs.">
          Intraday sequences — exact order-of-events (mined, not preset)
        </div>
        <button className="rr-btn" disabled={!!progress} onClick={mine}
                title="Fetch and tokenize this symbol's recent minute-bar sessions (one API call per new day — a first run takes a minute or two), then mine recurring sequences. Re-runs only fetch days not yet archived.">
          {progress ? "mining…" : (res ? "↺ re-mine" : "⛏ mine intraday sequences")}</button>
      </div>
      {progress && (
        <div className="bt-progress">
          <div className="bt-progress-bar"><div style={{ width: `${Math.min(100, (progress.done / Math.max(1, progress.total)) * 100)}%` }} /></div>
          <span>{progress.phase} — {progress.done}/{progress.total}</span>
        </div>
      )}
      {err && <div className="bt-warn bt-err">{err}</div>}
      {res && (res.sequences || []).length === 0 && <div className="pd-empty">No recurring intraday sequences beat baseline yet ({res.sessions} sessions mined).</div>}
      {res && (res.sequences || []).slice(0, 10).map((s, i) => (
        <div key={i} className={`pd-seq ${s.label === "reliable" ? "" : "pd-seq-weak"}`}
             title={`n=${s.n} sessions · hit ${s.hit_rate}% vs baseline ${s.baseline}% · p=${s.p_value} q=${s.q_value} · recent dates: ${(s.dates || []).join(", ")}`}>
          <span className={`pd-label pd-l-${(s.label || "").replace(/[^a-z]/g, "")}`}>{s.label}</span>
          <span className="pd-seq-sent">{s.sentence}</span>
        </div>
      ))}
      {res && <div className="pd-notes">{(res.notes || []).map((nt, i) => <div key={i}>· {nt}</div>)}</div>}
    </div>
  );
}

function PatternDiscoveryCard({ apiFetch, ticker, onOpenBacktest }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [filter, setFilter] = useState("all");
  const [watches, setWatches] = useState([]);
  const symRef = useRef(null);

  const load = (sym) => {
    setLoading(true); setErr(null);
    apiFetch(`/api/patterns?symbol=${encodeURIComponent(sym)}`)
      .then(r => r.json())
      .then(d => { setLoading(false); if (d.error) setErr(d.error); else setData(d); })
      .catch(e => { setLoading(false); setErr(String(e)); });
  };
  const loadWatches = () => {
    apiFetch("/api/patterns/watches").then(r => r.json())
      .then(d => setWatches(d.watches || [])).catch(() => {});
  };
  useEffect(() => {
    if (ticker && ticker !== symRef.current) { symRef.current = ticker; load(ticker); }
    loadWatches();
  }, [ticker]);

  const toBacktest = (p) => {
    try { localStorage.setItem("jerry_bt_prefill", JSON.stringify(p.backtest_rules)); } catch (e) {}
    window.dispatchEvent(new CustomEvent("jerry-bt-load", { detail: p.backtest_rules }));
    if (onOpenBacktest) onOpenBacktest();
  };
  const toOptBacktest = (p) => {
    const idea = p.options_idea || {};
    const rules = { ...p.backtest_rules, instrument: "option", direction: "long",
                    options: { right: idea.right || (p.claim.dir === "up" ? "call" : "put"),
                               dte: idea.dte || 14, strike: { mode: "atm" } } };
    try { localStorage.setItem("jerry_bt_prefill", JSON.stringify(rules)); } catch (e) {}
    window.dispatchEvent(new CustomEvent("jerry-bt-load", { detail: rules }));
    if (onOpenBacktest) onOpenBacktest();
  };
  const toggleWatch = (p, symOverride) => {
    const symX = symOverride || (data && data.symbol) || ticker;
    const wid = `${symX}::${p.id}`;
    const existing = watches.find(w => w.id === wid);
    const body = existing
      ? { action: "remove", id: wid }
      : { symbol: symX, pattern: { id: p.id, family: p.family, params: p.params, sentence: p.sentence, claim: p.claim, confidence: p.confidence } };
    apiFetch("/api/patterns/watch", { method: "POST", body: JSON.stringify(body) })
      .then(r => r.json()).then(() => loadWatches()).catch(() => {});
  };

  const pats = ((data && data.patterns) || []).filter(p =>
    filter === "all" ? true : p.kind.includes(filter));

  return (
    <div className="card pd-card">
      <div className="card-head">
        <div>
          <div className="kicker" title="An event-study engine that learns THIS stock's recurring behavior from its own ~2-year history. Thresholds adapt to the stock's own return/gap/drawdown distributions — not preset chart patterns. Claims are fitted on the first 70% of history and validated on the last 30%; every edge is tested against the baseline chance of the same move on any random day.">Pattern Discovery</div>
          <h2 title="The strongest recurring behaviors found for the selected ticker, ranked by statistical confidence.">{(data && data.symbol) || ticker} — what this stock repeatedly does</h2>
        </div>
        <div className="pd-headright">
          {data && <span className="pd-meta" title={`History analyzed: ${data.from} → ${data.to} (${data.bars} daily bars). In-sample/out-of-sample split at ${data.split_date}. Cached ~6h.`}>{data.from} → {data.to}</span>}
          <button className="rr-btn" disabled={loading} onClick={() => load(ticker)}
                  title="Re-run discovery for the selected ticker (results are cached ~6 hours).">{loading ? "analyzing…" : "↺ analyze"}</button>
        </div>
      </div>

      {err && <div className="bt-warn bt-err">{err}</div>}
      {loading && !data && <div className="pd-empty">Analyzing {ticker}'s history…</div>}

      {data && <PDCurrentSetup cs={data.current_setup} lastClose={data.last_close}
                               earnDays={data.days_to_earnings} />}

      <PDAskBox apiFetch={apiFetch} ticker={(data && data.symbol) || ticker}
                onBacktest={toBacktest} onWatch={(p) => toggleWatch(p)} watches={watches} />

      <div className="pd-filters">
        {["all", "bullish", "bearish", "mean-reverting", "momentum"].map(f => (
          <button key={f} className={`rr-btn ${filter === f ? "pd-f-on" : ""}`} onClick={() => setFilter(f)}
                  title={f === "all" ? "Show every discovered pattern." : `Show only ${f} patterns.`}>{f}</button>
        ))}
        {data && <span className="pd-meta" title={`The engine searched ${data.candidates_searched} candidate patterns (events × windows × discovered shapes) — significance is corrected for exactly that multiple-testing burden.`}>{data.candidates_searched} candidates searched</span>}
      </div>

      {data && pats.length === 0 && !loading && (
        <div className="pd-empty" title="Either the stock's behavior is too random for any claim to beat baseline with statistical support, or there isn't enough history.">
          No statistically supported patterns found for this filter — that itself is information: nothing this stock does here repeats reliably.
        </div>
      )}
      {pats.map(p => (
        <PDRow key={p.id} p={p} sym={data.symbol} apiFetch={apiFetch}
               onBacktest={toBacktest} onOptBacktest={toOptBacktest} onWatch={(x) => toggleWatch(x)}
               watching={watches.some(w => w.id === `${data.symbol}::${p.id}`)} />
      ))}

      <PDIntraday apiFetch={apiFetch} ticker={(data && data.symbol) || ticker} />

      {data && (data.notes || []).length > 0 && (
        <div className="pd-notes" title="Methodology and data-coverage limits — read once so you know exactly what these statistics can and cannot claim.">
          {data.notes.map((nt, i) => <div key={i}>· {nt}</div>)}
        </div>
      )}

      {watches.length > 0 && (
        <div className="pd-watches">
          <div className="bt-sec-title" title="Patterns you're watching across all symbols. Each is re-checked against fresh daily data when you open this tab and every 30 minutes during market hours; a push alert fires the day a setup triggers again.">Watched patterns — live signals</div>
          {watches.map(w => (
            <div key={w.id} className={`pd-watch ${w.triggered ? "trig" : ""}`}>
              <b>{w.symbol}</b>
              <span className="pd-watch-sent">{w.sentence}</span>
              {w.triggered
                ? <span className="pd-trig" title={`The setup is TRUE on the latest daily bar (${w.checked}). The claimed move is what history says usually follows.`}>● TRIGGERED {w.checked}</span>
                : <span className="pd-quiet" title={`Not currently set up (last checked bar: ${w.checked || "n/a"}).`}>quiet</span>}
              <button className="bt-x" title="Stop watching this pattern."
                      onClick={() => apiFetch("/api/patterns/watch", { method: "POST", body: JSON.stringify({ action: "remove", id: w.id }) }).then(() => loadWatches())}>✕</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


Object.assign(window, { PatternDiscoveryCard: React.memo(PatternDiscoveryCard) });
