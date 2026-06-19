// All card and panel components — split out of the app.jsx monolith (v1.40).
// Loads before app.js; every binding is published to window so later
// files resolve bare references exactly as they did in one file.

function TickerLogo({ ticker }) {
  // Fallback chain — try several free logo CDNs in order, fall back to
  // text if all fail. We track loaded/error state explicitly because
  // mobile Safari sometimes renders the broken-image placeholder briefly
  // before firing onError, which produces an ugly "ticker?" box flash.
  // Until the image confirms it loaded, we render the text fallback so
  // users never see the broken-image glyph.
  const sources = React.useMemo(() => [
    `https://logo.synthfinance.com/ticker/${ticker}`,
    `https://financialmodelingprep.com/image-stock/${ticker}.png`,
    `https://assets.parqet.com/logos/symbol/${ticker}`,
  ], [ticker]);
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
    return <div className="sb-ticker-symbol-fallback">{ticker}</div>;
  }
  // While loading: show text underneath, but render an invisible img to
  // probe the URL. Once it loads, swap to the image. This prevents the
  // broken-image glyph from ever flashing.
  return (
    <>
      {!loaded && <div className="sb-ticker-symbol-fallback">{ticker}</div>}
      <img
        key={`${ticker}-${idx}`}
        src={sources[idx]}
        alt=""
        aria-hidden="true"
        className="sb-ticker-logo"
        loading="eager"
        decoding="async"
        referrerPolicy="no-referrer"
        style={loaded ? undefined : { display: "none" }}
        onLoad={() => setLoaded(true)}
        onError={() => { setLoaded(false); setIdx(i => i + 1); }}
      />
    </>
  );
}

function VolSkewCard({ calls, puts, currentPrice, ticker, sugCall, sugPut, activeExpDate, chartColors }) {
  const svgRef = useRef(null);
  const [hover, setHover] = useState(null);  // {strike, callIv, putIv, x, y}

  // Reset hover whenever the underlying data changes so a stale hover
  // never points to a strike no longer in scope. This MUST run before any
  // of the early `return null` guards below — otherwise the hook count
  // changes between renders (sparse data returns early and skips it),
  // which throws React error #300 and trips the card's error boundary
  // until the user clicks Retry.
  useEffect(() => { setHover(null); }, [ticker, calls.length, puts.length]);

  // Filter to plausible IVs. Anything > 500% is almost certainly a stale
  // quote on a deep-ITM/OTM strike with no real bid; exclude it.
  const callsWithIv = calls.filter(c => c.iv && c.iv > 0 && c.iv < 5);
  const putsWithIv = puts.filter(p => p.iv && p.iv > 0 && p.iv < 5);
  if (callsWithIv.length < 4 && putsWithIv.length < 4) return null;

  const allStrikesK = Array.from(new Set([
    ...callsWithIv.map(c => c.strike),
    ...putsWithIv.map(p => p.strike),
  ])).sort((a, b) => a - b);
  const lo = currentPrice * 0.75, hi = currentPrice * 1.25;
  const ks = allStrikesK.filter(k => k >= lo && k <= hi);
  if (ks.length < 4) return null;

  const W = 1200, H = 240, pL = 56, pR = 16, pT = 24, pB = 32;
  const innerW = W - pL - pR, innerH = H - pT - pB;
  const xMin = ks[0], xMax = ks[ks.length - 1];
  const xScale = v => pL + ((v - xMin) / (xMax - xMin)) * innerW;

  // Y-scale ONLY uses IVs from the visible (±25% of spot) strike range,
  // and trims the top 2% percentile so a single freak quote doesn't
  // dictate the whole y-axis. This was the bug Jerry hit — y-axis went
  // to 450% even though no visible point came near that.
  const visCallIvs = callsWithIv.filter(c => c.strike >= lo && c.strike <= hi).map(c => c.iv);
  const visPutIvs  = putsWithIv .filter(p => p.strike >= lo && p.strike <= hi).map(p => p.iv);
  const visIvs = [...visCallIvs, ...visPutIvs].sort((a, b) => a - b);
  if (!visIvs.length) return null;
  const trimIdx = Math.max(0, Math.floor(visIvs.length * 0.98) - 1);
  const ivCeiling = visIvs[trimIdx];
  const iMin = Math.max(0, visIvs[0] * 0.92);
  const iMax = ivCeiling * 1.06;
  // Clamp values at iMax so any clipped freak quote sits on the top
  // edge instead of disappearing.
  const yScale = v => pT + (1 - (Math.min(v, iMax) - iMin) / (iMax - iMin)) * innerH;

  const buildPath = (rows) => {
    const sorted = [...rows].sort((a, b) => a.strike - b.strike).filter(r => r.strike >= lo && r.strike <= hi);
    if (!sorted.length) return "";
    return sorted.map((r, i) =>
      `${i === 0 ? "M" : "L"} ${xScale(r.strike)} ${yScale(r.iv)}`
    ).join(" ");
  };
  const callPath = buildPath(callsWithIv);
  const putPath = buildPath(putsWithIv);

  const nearestK = ks.reduce((a, b) => Math.abs(a - currentPrice) < Math.abs(b - currentPrice) ? a : b);
  const nearestCall = callsWithIv.find(c => c.strike === nearestK);
  const nearestPut = putsWithIv.find(p => p.strike === nearestK);
  const atmIv = (nearestCall && nearestPut)
    ? (nearestCall.iv + nearestPut.iv) / 2
    : (nearestCall ? nearestCall.iv : nearestPut ? nearestPut.iv : 0);

  const lookupIv = (rows, target) => {
    if (!rows.length) return null;
    const r = rows.reduce((a, b) => Math.abs(a.strike - target) < Math.abs(b.strike - target) ? a : b);
    return r.iv;
  };
  const otmPutIv = lookupIv(putsWithIv, currentPrice * 0.95);
  const otmCallIv = lookupIv(callsWithIv, currentPrice * 1.05);
  const skew25 = (otmPutIv && otmCallIv) ? (otmPutIv - otmCallIv) * 100 : null;

  // Persist daily snapshot for the trend sparkline (unchanged from v17).
  const SKEW_KEY = "weeklyOptionsTimer.skewHistory.v1";
  let skewHistory = [];
  try {
    const raw = localStorage.getItem(SKEW_KEY);
    const all = raw ? JSON.parse(raw) : {};
    const todayStr = new Date().toISOString().slice(0, 10);
    const list = all[ticker] || [];
    const last = list[list.length - 1];
    const sample = { d: todayStr, atm: atmIv, sk: skew25 };
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
  const onMove = (e) => {
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const vbX = ((e.clientX - rect.left) / rect.width) * W;
    if (vbX < pL || vbX > W - pR) { setHover(null); return; }
    const targetK = xMin + ((vbX - pL) / innerW) * (xMax - xMin);
    const nearestVisK = ks.reduce((a, b) =>
      Math.abs(a - targetK) < Math.abs(b - targetK) ? a : b);
    const c = callsWithIv.find(x => x.strike === nearestVisK);
    const p = putsWithIv.find(x => x.strike === nearestVisK);
    if (!c && !p) { setHover(null); return; }
    setHover({ strike: nearestVisK, callRow: c, putRow: p });
  };

  return (
    <div className="card" style={{marginBottom: "var(--row-gap)"}}>
      <div className="card-head">
        <div>
          <div className="kicker">IV by strike · {activeExpDate.toLocaleDateString("en-US", {weekday: "short", month: "short", day: "numeric"})}</div>
          <div className="card-title">Volatility skew</div>
        </div>
        <div className="vs-stats">
          <div className="vs-stat">
            <div className="vs-stat-lbl">ATM IV</div>
            <div className="vs-stat-val">{(atmIv * 100).toFixed(1)}%</div>
          </div>
          {skew25 != null && (
            <div className="vs-stat">
              <div className="vs-stat-lbl">Skew (95% put · 105% call)</div>
              <div className={`vs-stat-val ${skew25 >= 0 ? "down" : "up"}`}>
                {skew25 >= 0 ? "+" : ""}{skew25.toFixed(1)} pts
              </div>
            </div>
          )}
          {skewHistory.length >= 2 && (() => {
            const sw = 110, sh = 36, sp = 3;
            const vals = skewHistory.map(h => h.sk).filter(v => v != null);
            if (vals.length < 2) return null;
            const vMin = Math.min(...vals), vMax = Math.max(...vals);
            const range = vMax - vMin || 1;
            const xS = i => sp + (i / Math.max(1, skewHistory.length - 1)) * (sw - 2 * sp);
            const yS = v => sp + (1 - (v - vMin) / range) * (sh - 2 * sp);
            const path = skewHistory.map((h, i) =>
              h.sk == null ? null : `${i === 0 ? "M" : "L"} ${xS(i)} ${yS(h.sk)}`
            ).filter(Boolean).join(" ");
            const last = skewHistory[skewHistory.length - 1];
            const first = skewHistory[0];
            const change = (last && first && last.sk != null && first.sk != null) ? (last.sk - first.sk) : 0;
            return (
              <div className="vs-stat">
                <div className="vs-stat-lbl">Skew · {skewHistory.length}d trend</div>
                <svg width={sw} height={sh} style={{display: "block"}}>
                  <line x1={sp} x2={sw - sp} y1={yS(0)} y2={yS(0)}
                        stroke={chartColors.fg3} strokeWidth="1" strokeDasharray="2 3" opacity="0.4" />
                  <path d={path} fill="none" stroke={chartColors.accent} strokeWidth="1.6" />
                  {skewHistory.length > 0 && (
                    <circle cx={xS(skewHistory.length - 1)} cy={yS(last.sk || 0)} r="2.5" fill={chartColors.accent} />
                  )}
                </svg>
                <div style={{fontSize: 10, color: change >= 0 ? "var(--down)" : "var(--up)", fontFamily: "var(--font-mono)"}}>
                  {change >= 0 ? "▲" : "▼"} {Math.abs(change).toFixed(1)} pts since first sample
                </div>
              </div>
            );
          })()}
        </div>
      </div>
      <div className="vs-svg-wrap" style={{position: "relative"}}>
        <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="vs-svg"
             onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
          <rect x="0" y="0" width={W} height={H} fill="transparent" />
          {yTicks.map(t => (
            <g key={`yg${t}`}>
              <line x1={pL} x2={W - pR} y1={yScale(t)} y2={yScale(t)} stroke="currentColor" opacity="0.06" />
              <text x={pL - 8} y={yScale(t) + 3} fontSize="10" textAnchor="end" fill={chartColors.fg3} fontFamily="ui-monospace, monospace">
                {(t * 100).toFixed(0)}%
              </text>
            </g>
          ))}
          {xTicks.map(t => (
            <g key={`xg${t}`}>
              <line x1={xScale(t)} x2={xScale(t)} y1={pT} y2={H - pB} stroke="currentColor" opacity="0.04" />
              <text x={xScale(t)} y={H - pB + 14} fontSize="10" textAnchor="middle" fill={chartColors.fg3} fontFamily="ui-monospace, monospace">
                ${t}
              </text>
            </g>
          ))}
          <line x1={xScale(currentPrice)} x2={xScale(currentPrice)} y1={pT} y2={H - pB}
                stroke={chartColors.fg2} strokeWidth="1" strokeDasharray="2 3" opacity="0.6" />
          <text x={xScale(currentPrice)} y={pT - 6} fontSize="10" textAnchor="middle"
                fill={chartColors.fg2} fontFamily="ui-monospace, monospace">spot</text>
          {sugCall > 0 && sugCall >= xMin && sugCall <= xMax && (
            <line x1={xScale(sugCall)} x2={xScale(sugCall)} y1={pT} y2={H - pB}
                  stroke={chartColors.up} strokeWidth="1" strokeDasharray="3 3" opacity="0.7" />
          )}
          {sugPut > 0 && sugPut >= xMin && sugPut <= xMax && (
            <line x1={xScale(sugPut)} x2={xScale(sugPut)} y1={pT} y2={H - pB}
                  stroke={chartColors.down} strokeWidth="1" strokeDasharray="3 3" opacity="0.7" />
          )}
          {callPath && <path d={callPath} fill="none" stroke={chartColors.up} strokeWidth="1.8" />}
          {putPath  && <path d={putPath}  fill="none" stroke={chartColors.down} strokeWidth="1.8" />}
          {callsWithIv.filter(c => c.strike >= lo && c.strike <= hi).map(c => (
            <circle key={`vc${c.strike}`} cx={xScale(c.strike)} cy={yScale(c.iv)} r="2.5"
                    fill={chartColors.up} opacity="0.85" />
          ))}
          {putsWithIv.filter(p => p.strike >= lo && p.strike <= hi).map(p => (
            <circle key={`vp${p.strike}`} cx={xScale(p.strike)} cy={yScale(p.iv)} r="2.5"
                    fill={chartColors.down} opacity="0.85" />
          ))}
          {/* Hover crosshair + emphasized markers on the closest strike */}
          {hover && (
            <g pointerEvents="none">
              <line x1={xScale(hover.strike)} x2={xScale(hover.strike)} y1={pT} y2={H - pB}
                    stroke={chartColors.fg2} strokeWidth="1" opacity="0.55" />
              {hover.callRow && (
                <circle cx={xScale(hover.strike)} cy={yScale(hover.callRow.iv)} r="5"
                        fill={chartColors.up} stroke="white" strokeWidth="1.5" />
              )}
              {hover.putRow && (
                <circle cx={xScale(hover.strike)} cy={yScale(hover.putRow.iv)} r="5"
                        fill={chartColors.down} stroke="white" strokeWidth="1.5" />
              )}
            </g>
          )}
        </svg>
        {hover && (
          <div className="vs-tooltip"
               style={{
                 left: `${(xScale(hover.strike) / W) * 100}%`,
                 top: 8,
               }}>
            <div className="vs-tt-head">${hover.strike.toFixed(2)}</div>
            {hover.callRow && (
              <div className="vs-tt-row">
                <span className="vs-tt-lbl up">Call IV</span>
                <span className="vs-tt-val">{(hover.callRow.iv * 100).toFixed(1)}%</span>
              </div>
            )}
            {hover.putRow && (
              <div className="vs-tt-row">
                <span className="vs-tt-lbl down">Put IV</span>
                <span className="vs-tt-val">{(hover.putRow.iv * 100).toFixed(1)}%</span>
              </div>
            )}
            {hover.callRow && hover.putRow && (
              <div className="vs-tt-row vs-tt-spread">
                <span className="vs-tt-lbl">P − C</span>
                <span className={`vs-tt-val ${(hover.putRow.iv - hover.callRow.iv) >= 0 ? "down" : "up"}`}>
                  {((hover.putRow.iv - hover.callRow.iv) * 100).toFixed(1)} pts
                </span>
              </div>
            )}
          </div>
        )}
      </div>
      <div className="legend" style={{marginTop: 8}}>
        <span className="item"><span className="swatch" style={{background: chartColors.up, height: 2}}></span>Call IV</span>
        <span className="item"><span className="swatch" style={{background: chartColors.down, height: 2}}></span>Put IV</span>
        <span className="item"><span className="swatch dashed" style={{borderColor: chartColors.fg2}}></span>Spot</span>
        {skew25 != null && skew25 > 0.5 && (
          <span className="item" style={{color: "var(--down)"}}>
            Put skew — downside is more expensive than upside
          </span>
        )}
        {skew25 != null && skew25 < -0.5 && (
          <span className="item" style={{color: "var(--up)"}}>
            Call skew — upside is more expensive than downside
          </span>
        )}
      </div>
    </div>
  );
}

function AnalystBoardCard({ apiFetch, onSwitchTicker }) {
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
    } catch (e) { setErr(String(e)); return null; }
  };

  useEffect(() => {
    load();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const startScan = async () => {
    setErr(null);
    try { await apiFetch(`/api/analyst_board/scan?days=${days}&force=1`); }
    catch (e) { setErr(String(e)); return; }
    await load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) {
        clearInterval(pollRef.current); pollRef.current = null;
      }
    }, 4000);
  };

  const status = (board && board.status) || {};
  const actions = (board && board.actions) || [];
  const summary = (board && board.summary) || {};
  const scanning = !!status.scanning;

  const sectors = useMemo(
    () => Array.from(new Set(actions.map(a => a.sector).filter(Boolean))).sort(),
    [actions]
  );
  const capBucket = (mc) => {
    if (!mc) return "unknown";
    const b = mc / 1e9;
    if (b >= 200) return "mega"; if (b >= 50) return "large";
    if (b >= 10) return "mid"; return "small";
  };
  const filtered = useMemo(() => actions.filter(a => {
    if (fType !== "all" && a.action_class !== fType) return false;
    if (fDir !== "all" && a.direction !== fDir) return false;
    if (fSector !== "all" && a.sector !== fSector) return false;
    if (fCap !== "all" && capBucket(a.market_cap) !== fCap) return false;
    if (fHigh && a.importance !== "high") return false;
    if (fToday) {
      const dt = new Date(String(a.date || "").slice(0, 10) + "T00:00:00");
      const today = new Date(); today.setHours(0, 0, 0, 0);
      if (isNaN(dt) || Math.round((today - dt) / 86400000) !== 0) return false;
    }
    if (q) {
      const s = q.toLowerCase();
      if (!String(a.ticker || "").toLowerCase().includes(s) &&
          !String(a.firm || "").toLowerCase().includes(s)) return false;
    }
    return true;
  }), [actions, fType, fDir, fSector, fCap, fHigh, fToday, q]);

  const fmtPct = (v) => v == null ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(2) + "%";
  const fmtCap = fmtMktCap;
  const fmt$ = (v) => fmtUsd(v);
  const fmtDate = (d) => {
    if (!d) return "";
    const s = String(d).slice(0, 10);
    const dt = new Date(s + "T00:00:00");
    if (isNaN(dt)) return s;
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const days = Math.round((today - dt) / 86400000);
    const rel = days <= 0 ? "today" : days === 1 ? "yesterday" : `${days}d ago`;
    const md = dt.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    return `${md} · ${rel}`;
  };
  const actLabel = { upgrade: "Upgrade", downgrade: "Downgrade", initiate: "Initiation", reiterate: "Reiterate", target_change: "PT change" };

  const Chips = ({ rows, withScore }) => (
    <div className="ab-chips">
      {(rows || []).length === 0 && <span className="muted" style={{ fontSize: 12 }}>—</span>}
      {(rows || []).map((a, i) => (
        <button key={a.ticker + i} className={`ab-chip ab-${a.direction || "neutral"}`}
                onClick={() => onSwitchTicker(a.ticker)} title={(a.reasons || []).join(" · ")}>
          {a.ticker}{a.multi_count > 1 ? ` ·${a.multi_count}` : ""}
          {withScore && <b>{Math.round(a.score)}</b>}
        </button>
      ))}
    </div>
  );

  const SummaryBox = ({ title, children, tone }) => (
    <div className={`ab-sumbox ${tone || ""}`}>
      <div className="ab-sumbox-title">{title}</div>
      {children}
    </div>
  );

  return (
    <div className="card ab-card">
      <div className="card-head">
        <div>
          <div className="kicker">Pre-market game plan</div>
          <div className="card-title">Analyst actions that matter</div>
        </div>
        <div className="ab-controls">
          <select className="sb-select ab-days" value={days} onChange={e => setDays(+e.target.value)} title="How far back to look for fresh actions">
            <option value={1}>Today</option>
            <option value={2}>2 days</option>
            <option value={3}>3 days</option>
            <option value={7}>1 week</option>
          </select>
          <button className="scan-run-btn" onClick={startScan} disabled={scanning}>
            {scanning ? "Scanning…" : "Scan now"}
          </button>
        </div>
      </div>

      <div className="ab-status">
        {status.last_scan
          ? <span>Last scan {new Date(status.last_scan).toLocaleString()} · {status.universe_size || 0} names · {actions.length} actions</span>
          : <span className="muted">No scan yet — click <b>Scan now</b> (a full ~600-name scan takes a few minutes).</span>}
        <span className="muted"> · Auto-scans weekdays 8:00 AM ET</span>
        {err && <span className="ab-err"> · {err}</span>}
      </div>
      {scanning && (
        <div className="ab-progress">
          <div className="ab-progress-bar" style={{ width: `${status.total ? (status.scanned / status.total * 100) : 0}%` }}></div>
          <span className="ab-progress-txt">{status.scanned || 0} / {status.total || 0}</span>
        </div>
      )}

      {actions.length > 0 && (
        <div className="ab-summary">
          <SummaryBox title="Top bullish" tone="up"><Chips rows={summary.top_bullish} withScore /></SummaryBox>
          <SummaryBox title="Top bearish" tone="down"><Chips rows={summary.top_bearish} withScore /></SummaryBox>
          <SummaryBox title="Multiple firms"><Chips rows={summary.multi_action} /></SummaryBox>
          <SummaryBox title="Biggest pre-market"><Chips rows={summary.biggest_premarket} /></SummaryBox>
          <SummaryBox title="Looks meaningful" tone="up"><Chips rows={summary.meaningful} withScore /></SummaryBox>
          <SummaryBox title="Weak / suspicious" tone="warn"><Chips rows={summary.suspicious} /></SummaryBox>
          <SummaryBox title="Sectors — bullish">
            <div className="ab-sectors">{(summary.sectors_positive || []).map(s => <span key={s.sector} className="ab-sectchip up">{s.sector}<b>{s.count}</b></span>)}</div>
          </SummaryBox>
          <SummaryBox title="Sectors — bearish">
            <div className="ab-sectors">{(summary.sectors_negative || []).map(s => <span key={s.sector} className="ab-sectchip down">{s.sector}<b>{s.count}</b></span>)}</div>
          </SummaryBox>
          <SummaryBox title="Watch after open"><Chips rows={summary.watch_after_open} withScore /></SummaryBox>
        </div>
      )}

      {actions.length > 0 && (
        <div className="ab-filters">
          <input className="sb-select ab-search" placeholder="Ticker or firm…" value={q} onChange={e => setQ(e.target.value)} />
          <select className="sb-select" value={fType} onChange={e => setFType(e.target.value)}>
            <option value="all">All actions</option>
            <option value="upgrade">Upgrades</option>
            <option value="downgrade">Downgrades</option>
            <option value="initiate">Initiations</option>
            <option value="target_change">PT changes</option>
            <option value="reiterate">Reiterations</option>
          </select>
          <select className="sb-select" value={fDir} onChange={e => setFDir(e.target.value)}>
            <option value="all">Bull & bear</option>
            <option value="bull">Bullish</option>
            <option value="bear">Bearish</option>
          </select>
          <select className="sb-select" value={fCap} onChange={e => setFCap(e.target.value)}>
            <option value="all">Any cap</option>
            <option value="mega">Mega (≥$200B)</option>
            <option value="large">Large ($50–200B)</option>
            <option value="mid">Mid ($10–50B)</option>
            <option value="small">Small (&lt;$10B)</option>
          </select>
          <select className="sb-select" value={fSector} onChange={e => setFSector(e.target.value)}>
            <option value="all">All sectors</option>
            {sectors.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <label className="ab-toggle"><input type="checkbox" checked={fToday} onChange={e => setFToday(e.target.checked)} /> Today only</label>
          <label className="ab-toggle"><input type="checkbox" checked={fHigh} onChange={e => setFHigh(e.target.checked)} /> High impact only</label>
        </div>
      )}

      <div className="ab-board">
        {actions.length === 0 && !scanning && (
          <div className="ab-empty">No analyst actions yet. Run a scan to build this morning's board.</div>
        )}
        {filtered.map((a, i) => (
          <div key={a.ticker + a.firm + i} className="ab-row" onClick={() => onSwitchTicker(a.ticker)} title="Open this ticker on the Trade tab">
            <div className={`ab-scorebadge imp-${a.importance}`}>{Math.round(a.score)}</div>
            <div className="ab-rowmain">
              <div className="ab-rowtop">
                <span className="ab-tk">{a.ticker}</span>
                <span className={`ab-pill ab-${a.direction}`}>{actLabel[a.action_class] || a.action_class}</span>
                {a.multi_count > 1 && <span className="ab-pill ab-multi">{a.multi_count} firms</span>}
                {a.suspicious && <span className="ab-pill ab-warn">weak move</span>}
                {a.date && <span className="ab-datepill" title={`Analyst action dated ${a.date}`}>{fmtDate(a.date)}</span>}
                {a.company && <span className="ab-company">{a.company}</span>}
                <span className="ab-sector">{a.sector}</span>
              </div>
              <div className="ab-rowsub">
                <span className="ab-firm">{a.firm || "—"}</span>
                {(a.prior_grade || a.new_grade) && <span>{a.prior_grade || "—"} → <b>{a.new_grade || "—"}</b></span>}
                {(a.prior_target || a.new_target) && <span>PT {fmt$(a.prior_target)} → <b>{fmt$(a.new_target)}</b>{a.target_change_pct != null ? ` (${fmtPct(a.target_change_pct)})` : ""}</span>}
                <span className={`ab-pm ${(a.premarket_pct || 0) >= 0 ? "up" : "down"}`}>{fmtPct(a.premarket_pct)} pre</span>
                <span className="ab-cap">{fmtCap(a.market_cap)}</span>
              </div>
              {a.reasons && a.reasons.length > 0 && <div className="ab-reasons">{a.reasons.join(" · ")}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Shared money formatters for the Discover boards. Comma thousands
// separators everywhere; market caps roll up to T / B / M.
function fmtUsd(v, dp) {
  if (v == null || isNaN(v)) return "—";
  const d = dp == null ? 2 : dp;
  return "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtMktCap(v) {
  if (!v) return "—";
  if (v >= 1e12) return "$" + (v / 1e12).toLocaleString(undefined, { maximumFractionDigits: 2 }) + "T";
  if (v >= 1e9)  return "$" + (v / 1e9).toLocaleString(undefined, { maximumFractionDigits: 1 }) + "B";
  if (v >= 1e6)  return "$" + (v / 1e6).toLocaleString(undefined, { maximumFractionDigits: 0 }) + "M";
  return "$" + Number(v).toLocaleString();
}

// MM-DD-YYYY (e.g. 6-19-2026) from an ISO YYYY-MM-DD string.
function fmtSwingDate(s) {
  if (!s) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s));
  if (!m) return String(s);
  return `${+m[2]}-${+m[3]}-${m[1]}`;
}

function SwingPatternCard({ apiFetch, ticker }) {
  const Term = window.Term || (({ children }) => <span>{children}</span>);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [sens, setSens] = useState("0.12");   // zig-zag % threshold
  const [tab, setTab] = useState("up");        // history table: up | down
  const [fMove, setFMove] = useState("all");   // size filter
  const [fDur, setFDur] = useState("all");     // duration filter
  const [fVol, setFVol] = useState("all");     // volume filter
  const [fCat, setFCat] = useState("all");     // catalyst filter
  const [fStruct, setFStruct] = useState("all"); // structure filter

  const load = async (sym, pct) => {
    if (!sym) return;
    setLoading(true); setErr(null);
    try {
      const r = await apiFetch(`/api/swings?symbol=${encodeURIComponent(sym)}&pct=${pct}`);
      const d = await r.json();
      if (d.error) setErr(d.error); else setData(d);
    } catch (e) { setErr(String(e)); }
    setLoading(false);
  };
  useEffect(() => { load(ticker, sens); /* eslint-disable-next-line */ }, [ticker, sens]);

  const fmtUsd2 = (v) => v == null ? "—" : "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sgn = (v) => (v == null ? "" : (v >= 0 ? "+" : ""));

  const a = data && data.analysis;
  const ind = data && data.indicators;
  const upRhythm = data && data.rhythm;
  const downRhythm = data && data.down_rhythm;
  const upSwings = (data && data.swings) || [];
  const downSwings = (data && data.down_swings) || [];
  const isUp = a && a.direction === "up";
  const dirTone = a ? (isUp ? "up" : "down") : "";

  const matTone = (m) => ({ early: "up", developing: "up", mature: "", extended: "warn", exhausted: "down" }[m] || "");
  const confTone = (c) => ({ high: "up", medium: "", low: "warn" }[c] || "");

  const ScoreBar = ({ label, k, score, tone, factors }) => (
    <div className="swing-score">
      <div className="swing-score-row">
        <span><Term k={k}>{label}</Term></span>
        <b className={tone}>{score == null ? "—" : Math.round(score)}<small> / 100</small></b>
      </div>
      <div className="swing-bar"><div className={`swing-bar-fill ${tone}`} style={{ width: `${Math.max(0, Math.min(100, score || 0))}%` }} /></div>
      {factors && factors.length > 0 && <div className="swing-factors">{factors.slice(0, 3).join(" · ")}</div>}
    </div>
  );

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

  return (
    <div className="card ab-card">
      <div className="card-head">
        <div>
          <div className="kicker">Pattern recognition · {ticker}</div>
          <div className="card-title">Swing decision — where am I in this move?</div>
        </div>
        <div className="ab-controls">
          <select className="sb-select ab-days" value={sens} onChange={e => setSens(e.target.value)}
                  title="How big a reversal counts as a new swing">
            <option value="0.15">Major swings</option>
            <option value="0.12">Standard</option>
            <option value="0.08">Sensitive</option>
          </select>
          <button className="scan-run-btn" onClick={() => load(ticker, sens)} disabled={loading}>
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </div>

      {err && <div className="ab-status"><span className="ab-err">{err}</span></div>}

      {/* ── Live decision box ───────────────────────────────────────────── */}
      {a && a.status === "ok" && (
        <div className={`swing-live swing-${dirTone}`}>
          <div className="swing-live-head">
            <span className={`swing-badge ${dirTone}`}>{isUp ? "LONG setup ▲" : "SHORT setup ▼"}</span>
            <span className="swing-state" title="Plain-English read of the move">
              <Term k="trend_state">{a.trend_state}</Term>
            </span>
            <span className={`swing-maturity ${matTone(a.maturity)}`} title="Where this move sits in the stock's history">
              <Term k="maturity">{a.maturity}</Term>
            </span>
            {a.do_not_sell_yet && <span className="swing-flag up"><Term k="do_not_sell_yet">Don't sell yet</Term></span>}
            {a.cover_too_early_risk && <span className="swing-flag down"><Term k="cover_too_early">Don't cover yet</Term></span>}
          </div>

          <div className="swing-live-grid">
            <div><span><Term k={isUp ? "swing_low" : "swing_high"}>From {a.from_label}</Term></span>
              <b>{fmtUsd2(a.from_price)} <small>· {fmtSwingDate(a.from_date)}</small></b></div>
            <div><span>Current price</span><b>{fmtUsd2(a.current_price)}</b></div>
            <div><span><Term k="current_move">Move so far</Term></span>
              <b className={dirTone}>{sgn(a.current_move_pct)}{a.current_move_pct}% <small>· {a.days_active}d</small></b></div>
            <div><span>vs typical move</span>
              <b>{a.vs_history.pct_of_median_move}% of median <small>(med {a.vs_history.median_pct}% / {a.vs_history.median_days}d)</small></b></div>
            <div><span>Next target (median)</span>
              <b className={dirTone}>{fmtUsd2(a.targets[1].price)} <small>{sgn(a.targets[1].from_here_pct)}{a.targets[1].from_here_pct}% away</small></b></div>
            <div><span>RSI · rel-vol</span>
              <b><Term k="rsi14">{ind && ind.rsi14 != null ? ind.rsi14 : "—"}</Term> · <Term k="rel_vol">{ind && ind.rel_vol != null ? ind.rel_vol + "x" : "—"}</Term></b></div>
            {a.relative_strength && (
              <div><span><Term k="relative_strength">vs market (SPY)</Term></span>
                <b className={a.relative_strength.leading ? "up" : a.relative_strength.lagging ? "down" : ""}>
                  {sgn(a.relative_strength.vs_spy)}{a.relative_strength.vs_spy}% <small>{a.relative_strength.leading ? "leading" : a.relative_strength.lagging ? "lagging" : "tracking"}</small>
                </b></div>
            )}
          </div>

          {(a.broke_resistance || a.after_earnings) && (
            <div className="swing-tags">
              {a.broke_resistance && <span className="swing-tag up"><Term k="broke_resistance">⤴ Broke {isUp ? "resistance" : "support"}</Term></span>}
              {a.after_earnings && <span className="swing-tag"><Term k="after_earnings">⚡ Post-earnings move</Term></span>}
            </div>
          )}

          <div className="swing-signal">{a.signal_note}</div>

          <div className="swing-scores">
            <ScoreBar label="Continuation" k="continuation_score" score={a.continuation_score} tone={isUp ? "up" : "down"} factors={a.continuation_factors} />
            <ScoreBar label="Exhaustion" k="exhaustion_score" score={a.exhaustion_score} tone="warn" factors={a.exhaustion_factors} />
          </div>
        </div>
      )}

      {a && a.status === "no_rhythm" && (
        <div className="ab-status muted">{a.note}</div>
      )}

      {/* ── Target ladder ───────────────────────────────────────────────── */}
      {a && a.status === "ok" && (
        <div className="scan-table-wrap" style={{ marginTop: 12 }}>
          <div className="swing-subtitle"><Term k="target_ladder">Projected target ladder</Term> — from {a.from_label} {fmtUsd2(a.from_price)}</div>
          <table className="scan-table swing-table">
            <thead>
              <tr>
                <th>Target</th>
                <th className="scan-th-num">{isUp ? "Upside" : "Downside"} %</th>
                <th className="scan-th-num">Price</th>
                <th className="scan-th-num">From here</th>
                <th className="scan-th-num">By (est.)</th>
                <th className="scan-th-num"><Term k="confidence_rating">Confidence</Term></th>
              </tr>
            </thead>
            <tbody>
              {a.targets.map((t, i) => (
                <tr key={i} className="scan-row">
                  <td style={{ textTransform: "capitalize" }}>{t.label}{t.reached ? " ✓" : ""}</td>
                  <td className="scan-num">{sgn(isUp ? t.pct_move : -t.pct_move)}{isUp ? t.pct_move : -t.pct_move}%</td>
                  <td className="scan-num">{fmtUsd2(t.price)}</td>
                  <td className={`scan-num ${t.reached ? "muted" : dirTone}`}>{t.reached ? "reached" : `${sgn(t.from_here_pct)}${t.from_here_pct}%`}</td>
                  <td className="scan-num">{fmtSwingDate(t.eta_date)}</td>
                  <td className={`scan-num ${confTone(t.confidence)}`} title={`Matched ${t.matched} past move${t.matched === 1 ? "" : "s"}`}>{t.confidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Trade plan ──────────────────────────────────────────────────── */}
      {a && a.status === "ok" && a.trade_plan && (
        <div className="swing-plan">
          <div className="swing-subtitle">{a.trade_plan.side === "long" ? "Long" : "Short"} trade plan</div>
          <div className="swing-plan-grid">
            <div><span>Entry zone</span><b>{fmtUsd2(a.trade_plan.entry_zone[0])} – {fmtUsd2(a.trade_plan.entry_zone[1])}</b></div>
            <div><span>Invalidation</span><b className="down">{fmtUsd2(a.trade_plan.invalidation)}</b></div>
            <div><span>Target 1 (median)</span><b className={dirTone}>{fmtUsd2(a.trade_plan.t1)}</b></div>
            <div><span>Target 2 (stretch)</span><b className={dirTone}>{fmtUsd2(a.trade_plan.t2)}</b></div>
            <div><span>Extreme</span><b className={dirTone}>{fmtUsd2(a.trade_plan.stretch)}</b></div>
            <div><span>Holding window</span><b>{a.trade_plan.holding_window}</b></div>
          </div>
          <div className="swing-plan-note">{a.trade_plan.entry_note}</div>
          <div className="swing-plan-note muted">{a.trade_plan.invalidation_note}</div>
          <div className="swing-plan-cols">
            <div>
              <div className="swing-plan-h up">Reasons to stay</div>
              <ul>{a.trade_plan.reason_to_stay.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
            <div>
              <div className="swing-plan-h warn">Exit warnings</div>
              <ul>{a.trade_plan.exit_warnings.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
          </div>
          {a.similar_move && <div className="swing-plan-note"><b><Term k="similar_move">Similar past move:</Term></b> {a.similar_move.note}</div>}
        </div>
      )}

      {/* ── History table (up / down toggle + filters) ──────────────────── */}
      <div className="swing-histnav" style={{ marginTop: 14 }}>
        <button type="button" className={tab === "up" ? "active" : ""} onClick={() => setTab("up")}>Up-swings ({upSwings.length})</button>
        <button type="button" className={tab === "down" ? "active" : ""} onClick={() => setTab("down")}>Down-swings ({downSwings.length})</button>
      </div>

      {(upSwings.length > 0 || downSwings.length > 0) && (
        <div className="swing-filters" title="Narrow the history to setups like the one happening now">
          <span className="swing-filters-label"><Term k="swing_filters">Filter</Term></span>
          <select className="sb-select" value={fMove} onChange={e => setFMove(e.target.value)}>
            <option value="all">Any size</option>
            <option value="10">≥ 10%</option>
            <option value="20">≥ 20%</option>
          </select>
          <select className="sb-select" value={fDur} onChange={e => setFDur(e.target.value)}>
            <option value="all">Any length</option>
            <option value="short">1–3 days</option>
            <option value="mid">4–8 days</option>
            <option value="long">9+ days</option>
          </select>
          <select className="sb-select" value={fVol} onChange={e => setFVol(e.target.value)}>
            <option value="all">Any volume</option>
            <option value="high">Above-avg vol</option>
          </select>
          <select className="sb-select" value={fCat} onChange={e => setFCat(e.target.value)}>
            <option value="all">Any catalyst</option>
            <option value="earnings">After earnings</option>
          </select>
          <select className="sb-select" value={fStruct} onChange={e => setFStruct(e.target.value)}>
            <option value="all">Any structure</option>
            <option value="broke">Broke {tab === "up" ? "resistance" : "support"}</option>
            <option value="failed">Failed breakout</option>
          </select>
          {filtersOn && <button type="button" className="swing-filters-clear" onClick={() => { setFMove("all"); setFDur("all"); setFVol("all"); setFCat("all"); setFStruct("all"); }}>Clear</button>}
        </div>
      )}

      {histRhythm && (
        <div className="ab-status">
          <b>{histRhythm.count}</b> {tab === "up" ? "up" : "down"}-swings · usually <b>{histRhythm.days_p25}–{histRhythm.days_p75} trading days</b>
          {" "}· <b className={tab === "up" ? "up" : "down"}>{tab === "up" ? "+" : "−"}{histRhythm.pct_p25}% to {tab === "up" ? "+" : "−"}{histRhythm.pct_p75}%</b>
          {" "}(median <b>{tab === "up" ? "+" : "−"}{histRhythm.pct_median}%</b>, ~{histRhythm.days_median}d)
          {" "}· full range {histRhythm.days_min}–{histRhythm.days_max}d / {histRhythm.pct_min}–{histRhythm.pct_max}%
        </div>
      )}

      {histSwings.length > 0 ? (
        <div className="scan-table-wrap" style={{ marginTop: 8 }}>
          <table className="scan-table swing-table">
            <thead>
              {tab === "up" ? (
                <tr>
                  <th><Term k="swing_low">Swing low</Term></th><th className="scan-th-num">Low $</th>
                  <th><Term k="swing_high">Swing high</Term></th><th className="scan-th-num">High $</th>
                  <th className="scan-th-num">Days</th><th className="scan-th-num">$ chg</th>
                  <th className="scan-th-num">% chg</th><th className="scan-th-num">Avg/day</th>
                  <th className="scan-th-num">Rhythm</th><th>Flags</th>
                </tr>
              ) : (
                <tr>
                  <th><Term k="swing_high">Swing high</Term></th><th className="scan-th-num">High $</th>
                  <th><Term k="swing_low">Swing low</Term></th><th className="scan-th-num">Low $</th>
                  <th className="scan-th-num">Days</th><th className="scan-th-num">$ chg</th>
                  <th className="scan-th-num">% drop</th><th className="scan-th-num">Avg/day</th>
                  <th className="scan-th-num">Rhythm</th><th>Flags</th>
                </tr>
              )}
            </thead>
            <tbody>
              {histSwings.slice().reverse().map((s, i) => (
                <tr key={i} className="scan-row">
                  {tab === "up" ? (
                    <React.Fragment>
                      <td>{fmtSwingDate(s.low_date)}</td>
                      <td className="scan-num">{fmtUsd2(s.low_price)}</td>
                      <td>{fmtSwingDate(s.high_date)}</td>
                      <td className="scan-num">{fmtUsd2(s.high_price)}</td>
                    </React.Fragment>
                  ) : (
                    <React.Fragment>
                      <td>{fmtSwingDate(s.high_date)}</td>
                      <td className="scan-num">{fmtUsd2(s.high_price)}</td>
                      <td>{fmtSwingDate(s.low_date)}</td>
                      <td className="scan-num">{fmtUsd2(s.low_price)}</td>
                    </React.Fragment>
                  )}
                  <td className="scan-num">{s.trading_days}</td>
                  <td className={`scan-num ${tab === "up" ? "" : "down"}`}>{fmtUsd2(s.dollar_change)}</td>
                  <td className={`scan-num ${tab === "up" ? "up" : "down"}`}>{s.pct_change}%</td>
                  <td className="scan-num">{s.avg_daily_pct}%</td>
                  <td className="scan-num">{s.matches_rhythm ? "✓" : "·"}</td>
                  <td className="swing-flagcell">
                    {s.above_avg_vol && <span title={`Above-average volume${s.vol_ratio ? ` (${s.vol_ratio}x)` : ""}`}>🔥</span>}
                    {s.broke_resistance && <span title={`Broke prior ${tab === "up" ? "resistance" : "support"}`}>⤴</span>}
                    {s.failed_breakout && <span title="Failed breakout — level didn't hold">⚠</span>}
                    {s.after_earnings && <span title="Launched after an earnings report">⚡</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (!err && !loading && (
        <div className="ab-empty">
          {filtersOn && allHistSwings.length > 0
            ? `No ${tab === "up" ? "up" : "down"}-swings match these filters — adjust or clear them.`
            : `No major ${tab === "up" ? "up" : "down"}-swings found for ${ticker} in this window.`}
        </div>
      ))}
    </div>
  );
}

function ScreenersHub({ apiFetch, onSwitchTicker }) {
  const KEY = "jerry_screener_sub_v1";
  const [sub, setSub] = useState(() => {
    try { return localStorage.getItem(KEY) || "analyst"; } catch { return "analyst"; }
  });
  const pick = (id) => { setSub(id); try { localStorage.setItem(KEY, id); } catch {} };
  const SUBS = [
    { id: "analyst", label: "Analyst calls" },
    { id: "movers", label: "Movers" },
    { id: "trend", label: "Trend" },
    { id: "ivrank", label: "Vol Rank" },
  ];
  return (
    <div>
      <div className="screener-subnav" role="tablist" aria-label="Discovery screeners">
        {SUBS.map(s => (
          <button key={s.id} type="button" role="tab" aria-selected={sub === s.id}
                  className={sub === s.id ? "active" : ""} onClick={() => pick(s.id)}>
            {s.label}
          </button>
        ))}
      </div>
      {sub === "analyst" && <AnalystBoardCard apiFetch={apiFetch} onSwitchTicker={onSwitchTicker} />}
      {sub === "movers" && <MoversCard apiFetch={apiFetch} onSwitchTicker={onSwitchTicker} />}
      {sub === "trend" && <TrendCard apiFetch={apiFetch} onSwitchTicker={onSwitchTicker} />}
      {sub === "ivrank" && <IVRankCard apiFetch={apiFetch} onSwitchTicker={onSwitchTicker} />}
    </div>
  );
}

function IVRankCard({ apiFetch, onSwitchTicker }) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [fReg, setFReg] = useState("all");
  const [fVolTrend, setFVolTrend] = useState("all");
  const [q, setQ] = useState("");
  const pollRef = useRef(null);

  const load = async () => {
    try { const r = await apiFetch("/api/ivrank"); const d = await r.json(); setBoard(d); return d; }
    catch (e) { setErr(String(e)); return null; }
  };
  useEffect(() => { load(); return () => { if (pollRef.current) clearInterval(pollRef.current); }; }, []);
  const startScan = async () => {
    setErr(null);
    try { await apiFetch("/api/ivrank/scan?force=1"); } catch (e) { setErr(String(e)); return; }
    await load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) { clearInterval(pollRef.current); pollRef.current = null; }
    }, 4000);
  };

  const status = (board && board.status) || {};
  const rows = (board && board.rows) || [];
  const summary = (board && board.summary) || {};
  const scanning = !!status.scanning;

  const filtered = useMemo(() => rows.filter(r => {
    if (fReg !== "all" && r.regime !== fReg) return false;
    if (fVolTrend === "expanding" && !r.expanding) return false;
    if (fVolTrend === "contracting" && !r.contracting) return false;
    if (q && !String(r.ticker || "").toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  }), [rows, fReg, fVolTrend, q]);

  const regimeTone = (rg) => rg === "rich" ? "bull" : rg === "cheap" ? "bear" : "neutral";
  const Chips = ({ rows }) => (
    <div className="ab-chips">
      {(rows || []).length === 0 && <span className="muted" style={{ fontSize: 12 }}>—</span>}
      {(rows || []).map((r, i) => (
        <button key={r.ticker + i} className={`ab-chip ab-${regimeTone(r.regime)}`}
                onClick={() => onSwitchTicker(r.ticker)} title={(r.reasons || []).join(" · ")}>
          {r.ticker} <b>{Math.round(r.rank)}</b>
        </button>
      ))}
    </div>
  );
  const SummaryBox = ({ title, tone, children }) => (
    <div className={`ab-sumbox ${tone || ""}`}><div className="ab-sumbox-title">{title}</div>{children}</div>
  );

  return (
    <div className="card ab-card">
      <div className="card-head">
        <div>
          <div className="kicker">Premium selling</div>
          <div className="card-title">Volatility rank</div>
        </div>
        <div className="ab-controls">
          <button className="scan-run-btn" onClick={startScan} disabled={scanning}>
            {scanning ? "Scanning…" : "Scan now"}
          </button>
        </div>
      </div>
      <div className="ab-status">
        {status.last_scan
          ? <span>Last scan {new Date(status.last_scan).toLocaleString()} · {status.universe_size || 0} names · {rows.length} ranked</span>
          : <span className="muted">No scan yet — ranks ~600 names by where their volatility sits in its 1-year range (rich = good for selling premium).</span>}
        {status.error && <span className="ab-err"> · {status.error}</span>}
        {err && <span className="ab-err"> · {err}</span>}
      </div>
      <div className="ab-status muted" style={{ marginTop: -6 }}>
        Free realized-vol proxy for IV rank — exact option IV shows on the Trade tab per name.
      </div>
      {scanning && (
        <div className="ab-progress">
          <div className="ab-progress-bar" style={{ width: `${status.total ? (status.scanned / status.total * 100) : 0}%` }}></div>
          <span className="ab-progress-txt">{status.scanned || 0} / {status.total || 0}</span>
        </div>
      )}

      {rows.length > 0 && (
        <div className="ab-summary">
          <SummaryBox title="Richest premium (sell)" tone="up"><Chips rows={summary.richest} /></SummaryBox>
          <SummaryBox title="Cheapest vol (buy)" tone="down"><Chips rows={summary.cheapest} /></SummaryBox>
          <SummaryBox title="Vol expanding" tone="warn"><Chips rows={summary.expanding} /></SummaryBox>
          <SummaryBox title="Vol contracting"><Chips rows={summary.contracting} /></SummaryBox>
        </div>
      )}

      {rows.length > 0 && (
        <div className="ab-filters">
          <input className="sb-select ab-search" placeholder="Ticker…" value={q} onChange={e => setQ(e.target.value)} />
          <select className="sb-select" value={fReg} onChange={e => setFReg(e.target.value)}>
            <option value="all">Any regime</option>
            <option value="rich">Rich (rank ≥70)</option>
            <option value="elevated">Elevated</option>
            <option value="normal">Normal</option>
            <option value="cheap">Cheap (rank &lt;30)</option>
          </select>
          <select className="sb-select" value={fVolTrend} onChange={e => setFVolTrend(e.target.value)}>
            <option value="all">Any vol trend</option>
            <option value="expanding">Vol expanding</option>
            <option value="contracting">Vol contracting</option>
          </select>
        </div>
      )}

      <div className="ab-board">
        {rows.length === 0 && !scanning && (
          <div className="ab-empty">No vol data yet. Run a scan to rank the universe by volatility.</div>
        )}
        {filtered.map((r, i) => (
          <div key={r.ticker + i} className="ab-row" onClick={() => onSwitchTicker(r.ticker)} title="Open this ticker on the Trade tab">
            <div className={`ab-scorebadge imp-${r.importance}`}>{Math.round(r.rank)}</div>
            <div className="ab-rowmain">
              <div className="ab-rowtop">
                <span className="ab-tk">{r.ticker}</span>
                <span className={`ab-pill ab-${regimeTone(r.regime)}`}>{r.regime}</span>
                {r.expanding && <span className="ab-pill ab-warn">vol ↑</span>}
                {r.contracting && <span className="ab-pill ab-multi">vol ↓</span>}
                <span className="ab-sector">{fmtUsd(r.last)}</span>
              </div>
              <div className="ab-rowsub">
                <span>HV <b>{r.hv}%</b></span>
                <span>1y range <b>{r.hv_low}–{r.hv_high}%</b></span>
                <span>Vol rank <b>{Math.round(r.rank)}</b></span>
                <span>Pctile <b>{Math.round(r.percentile)}</b></span>
              </div>
              {r.reasons && r.reasons.length > 0 && <div className="ab-reasons">{r.reasons.join(" · ")}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TrendCard({ apiFetch, onSwitchTicker }) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [fDir, setFDir] = useState("all");
  const [fRsi, setFRsi] = useState("all");   // overbought / oversold
  const [fExt, setFExt] = useState("all");   // new_high / new_low
  const [minStr, setMinStr] = useState(0);
  const [q, setQ] = useState("");
  const pollRef = useRef(null);

  const load = async () => {
    try { const r = await apiFetch("/api/trend"); const d = await r.json(); setBoard(d); return d; }
    catch (e) { setErr(String(e)); return null; }
  };
  useEffect(() => { load(); return () => { if (pollRef.current) clearInterval(pollRef.current); }; }, []);
  const startScan = async () => {
    setErr(null);
    try { await apiFetch("/api/trend/scan?force=1"); } catch (e) { setErr(String(e)); return; }
    await load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) { clearInterval(pollRef.current); pollRef.current = null; }
    }, 4000);
  };

  const status = (board && board.status) || {};
  const rows = (board && board.rows) || [];
  const summary = (board && board.summary) || {};
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

  const Chips = ({ rows }) => (
    <div className="ab-chips">
      {(rows || []).length === 0 && <span className="muted" style={{ fontSize: 12 }}>—</span>}
      {(rows || []).map((r, i) => (
        <button key={r.ticker + i} className={`ab-chip ab-${r.direction === "up" ? "bull" : "bear"}`}
                onClick={() => onSwitchTicker(r.ticker)} title={(r.reasons || []).join(" · ")}>
          {r.ticker} <b>{Math.round(r.score)}</b>
        </button>
      ))}
    </div>
  );
  const SummaryBox = ({ title, tone, children }) => (
    <div className={`ab-sumbox ${tone || ""}`}><div className="ab-sumbox-title">{title}</div>{children}</div>
  );

  return (
    <div className="card ab-card">
      <div className="card-head">
        <div>
          <div className="kicker">Trend &amp; momentum</div>
          <div className="card-title">What's still trending</div>
        </div>
        <div className="ab-controls">
          <button className="scan-run-btn" onClick={startScan} disabled={scanning}>
            {scanning ? "Scanning…" : "Scan now"}
          </button>
        </div>
      </div>
      <div className="ab-status">
        {status.last_scan
          ? <span>Last scan {new Date(status.last_scan).toLocaleString()} · {status.universe_size || 0} names · {rows.length} ranked</span>
          : <span className="muted">No scan yet — click <b>Scan now</b> (pulls ~1y of daily data for ~600 names; takes a few minutes).</span>}
        {status.error && <span className="ab-err"> · {status.error}</span>}
        {err && <span className="ab-err"> · {err}</span>}
      </div>
      {scanning && (
        <div className="ab-progress">
          <div className="ab-progress-bar" style={{ width: `${status.total ? (status.scanned / status.total * 100) : 0}%` }}></div>
          <span className="ab-progress-txt">{status.scanned || 0} / {status.total || 0}</span>
        </div>
      )}

      {rows.length > 0 && (
        <div className="ab-summary">
          <SummaryBox title="Strongest uptrends" tone="up"><Chips rows={summary.strongest_up} /></SummaryBox>
          <SummaryBox title="Strongest downtrends" tone="down"><Chips rows={summary.strongest_down} /></SummaryBox>
          <SummaryBox title="New 52wk highs" tone="up"><Chips rows={summary.new_highs} /></SummaryBox>
          <SummaryBox title="New 52wk lows" tone="down"><Chips rows={summary.new_lows} /></SummaryBox>
          <SummaryBox title="Overbought (RSI≥70)" tone="warn"><Chips rows={summary.overbought} /></SummaryBox>
          <SummaryBox title="Oversold (RSI≤30)" tone="warn"><Chips rows={summary.oversold} /></SummaryBox>
        </div>
      )}

      {rows.length > 0 && (
        <div className="ab-filters">
          <input className="sb-select ab-search" placeholder="Ticker…" value={q} onChange={e => setQ(e.target.value)} />
          <select className="sb-select" value={fDir} onChange={e => setFDir(e.target.value)}>
            <option value="all">Up &amp; down</option>
            <option value="up">Uptrends</option>
            <option value="down">Downtrends</option>
          </select>
          <select className="sb-select" value={fRsi} onChange={e => setFRsi(e.target.value)}>
            <option value="all">Any RSI</option>
            <option value="overbought">Overbought</option>
            <option value="oversold">Oversold</option>
          </select>
          <select className="sb-select" value={fExt} onChange={e => setFExt(e.target.value)}>
            <option value="all">Any level</option>
            <option value="new_high">Near 52wk high</option>
            <option value="new_low">Near 52wk low</option>
          </select>
          <select className="sb-select" value={minStr} onChange={e => setMinStr(+e.target.value)}>
            <option value={0}>Any strength</option>
            <option value={40}>≥ 40</option>
            <option value={55}>≥ 55 (strong)</option>
          </select>
        </div>
      )}

      <div className="ab-board">
        {rows.length === 0 && !scanning && (
          <div className="ab-empty">No trend data yet. Run a scan to rank the universe by trend strength.</div>
        )}
        {filtered.map((r, i) => (
          <div key={r.ticker + i} className="ab-row" onClick={() => onSwitchTicker(r.ticker)} title="Open this ticker on the Trade tab">
            <div className={`ab-scorebadge imp-${r.importance}`}>{Math.round(r.score)}</div>
            <div className="ab-rowmain">
              <div className="ab-rowtop">
                <span className="ab-tk">{r.ticker}</span>
                <span className={`ab-pill ab-${r.direction === "up" ? "bull" : "bear"}`}>{r.direction === "up" ? "Uptrend" : "Downtrend"}</span>
                {r.new_high && <span className="ab-pill ab-multi">52wk high</span>}
                {r.new_low && <span className="ab-pill ab-warn">52wk low</span>}
                {r.overbought && <span className="ab-pill ab-warn">overbought</span>}
                {r.oversold && <span className="ab-pill ab-multi">oversold</span>}
                <span className="ab-sector">{fmtUsd(r.last)}</span>
              </div>
              <div className="ab-rowsub">
                {r.rsi != null && <span>RSI <b>{r.rsi}</b></span>}
                {r.from_high != null && <span>From 52wk hi <b>{r.from_high}%</b></span>}
                {r.streak ? <span>Streak <b>{r.streak > 0 ? `+${r.streak}` : r.streak}d</b></span> : null}
                <span>200-DMA <b>{r.above_ma200 ? "above" : "below"}</b></span>
              </div>
              {r.reasons && r.reasons.length > 0 && <div className="ab-reasons">{r.reasons.join(" · ")}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function MoversCard({ apiFetch, onSwitchTicker }) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [fDir, setFDir] = useState("all");
  const [fCap, setFCap] = useState("all");
  const [minGap, setMinGap] = useState(0);
  const [fCat, setFCat] = useState(false);
  const [q, setQ] = useState("");
  const pollRef = useRef(null);

  const load = async () => {
    try { const r = await apiFetch("/api/movers"); const d = await r.json(); setBoard(d); return d; }
    catch (e) { setErr(String(e)); return null; }
  };
  useEffect(() => { load(); return () => { if (pollRef.current) clearInterval(pollRef.current); }; }, []);

  const startScan = async () => {
    setErr(null);
    try { await apiFetch("/api/movers/scan?force=1"); } catch (e) { setErr(String(e)); return; }
    await load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) { clearInterval(pollRef.current); pollRef.current = null; }
    }, 4000);
  };

  const status = (board && board.status) || {};
  const movers = (board && board.movers) || [];
  const summary = (board && board.summary) || {};
  const scanning = !!status.scanning;

  const capBucket = (mc) => { if (!mc) return "unknown"; const b = mc / 1e9; if (b >= 200) return "mega"; if (b >= 50) return "large"; if (b >= 10) return "mid"; return "small"; };
  const filtered = useMemo(() => movers.filter(m => {
    if (fDir !== "all" && m.direction !== fDir) return false;
    if (fCap !== "all" && capBucket(m.market_cap) !== fCap) return false;
    if (minGap && Math.abs(m.gap_pct || 0) < minGap) return false;
    if (fCat && !m.has_analyst) return false;
    if (q && !String(m.ticker || "").toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  }), [movers, fDir, fCap, minGap, fCat, q]);

  const fmtPct = (v) => v == null ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(2) + "%";
  const fmtCap = fmtMktCap;
  const fmtVol = (v) => { if (!v) return "—"; if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`; if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`; return String(v); };

  const Chips = ({ rows }) => (
    <div className="ab-chips">
      {(rows || []).length === 0 && <span className="muted" style={{ fontSize: 12 }}>—</span>}
      {(rows || []).map((m, i) => (
        <button key={m.ticker + i} className={`ab-chip ab-${m.direction === "up" ? "bull" : "bear"}`}
                onClick={() => onSwitchTicker(m.ticker)} title={(m.reasons || []).join(" · ")}>
          {m.ticker} <b>{fmtPct(m.gap_pct)}</b>
        </button>
      ))}
    </div>
  );
  const SummaryBox = ({ title, tone, children }) => (
    <div className={`ab-sumbox ${tone || ""}`}><div className="ab-sumbox-title">{title}</div>{children}</div>
  );

  return (
    <div className="card ab-card">
      <div className="card-head">
        <div>
          <div className="kicker">Pre-market game plan</div>
          <div className="card-title">What's moving today</div>
        </div>
        <div className="ab-controls">
          <button className="scan-run-btn" onClick={startScan} disabled={scanning}>
            {scanning ? "Scanning…" : "Scan now"}
          </button>
        </div>
      </div>
      <div className="ab-status">
        {status.last_scan
          ? <span>Last scan {new Date(status.last_scan).toLocaleString()} · {status.universe_size || 0} names · {movers.length} movers</span>
          : <span className="muted">No scan yet — click <b>Scan now</b> (needs Schwab; most useful during pre-market hours).</span>}
        {status.error && <span className="ab-err"> · {status.error}</span>}
        {err && <span className="ab-err"> · {err}</span>}
      </div>
      {scanning && (
        <div className="ab-progress">
          <div className="ab-progress-bar" style={{ width: `${status.total ? (status.scanned / status.total * 100) : 0}%` }}></div>
          <span className="ab-progress-txt">{status.scanned || 0} / {status.total || 0}</span>
        </div>
      )}

      {movers.length > 0 && (
        <div className="ab-summary">
          <SummaryBox title="Top gainers" tone="up"><Chips rows={summary.top_gainers} /></SummaryBox>
          <SummaryBox title="Top losers" tone="down"><Chips rows={summary.top_losers} /></SummaryBox>
          <SummaryBox title="Heaviest volume"><Chips rows={summary.high_relvol} /></SummaryBox>
          <SummaryBox title="Moving + analyst call" tone="up"><Chips rows={summary.with_catalyst} /></SummaryBox>
        </div>
      )}

      {movers.length > 0 && (
        <div className="ab-filters">
          <input className="sb-select ab-search" placeholder="Ticker…" value={q} onChange={e => setQ(e.target.value)} />
          <select className="sb-select" value={fDir} onChange={e => setFDir(e.target.value)}>
            <option value="all">Up & down</option>
            <option value="up">Gainers</option>
            <option value="down">Losers</option>
          </select>
          <select className="sb-select" value={fCap} onChange={e => setFCap(e.target.value)}>
            <option value="all">Any cap</option>
            <option value="mega">Mega (≥$200B)</option>
            <option value="large">Large ($50–200B)</option>
            <option value="mid">Mid ($10–50B)</option>
            <option value="small">Small (&lt;$10B)</option>
          </select>
          <select className="sb-select" value={minGap} onChange={e => setMinGap(+e.target.value)}>
            <option value={0}>Any move</option>
            <option value={2}>≥ 2%</option>
            <option value={5}>≥ 5%</option>
            <option value={10}>≥ 10%</option>
          </select>
          <label className="ab-toggle"><input type="checkbox" checked={fCat} onChange={e => setFCat(e.target.checked)} /> Has analyst call</label>
        </div>
      )}

      <div className="ab-board">
        {movers.length === 0 && !scanning && (
          <div className="ab-empty">No movers yet. Run a scan (best during pre-market hours, with Schwab connected).</div>
        )}
        {filtered.map((m, i) => (
          <div key={m.ticker + i} className="ab-row" onClick={() => onSwitchTicker(m.ticker)} title="Open this ticker on the Trade tab">
            <div className={`ab-scorebadge imp-${m.importance}`}>{Math.round(m.score)}</div>
            <div className="ab-rowmain">
              <div className="ab-rowtop">
                <span className="ab-tk">{m.ticker}</span>
                <span className={`ab-pill ab-${m.direction === "up" ? "bull" : "bear"}`}>{fmtPct(m.gap_pct)}</span>
                {m.has_analyst && <span className="ab-pill ab-multi">analyst call</span>}
                {m.company && <span className="ab-company">{m.company}</span>}
                <span className="ab-sector">{m.sector}</span>
              </div>
              <div className="ab-rowsub">
                <span>Last <b>{fmtUsd(m.last)}</b></span>
                <span>Pre-mkt vol <b>{fmtVol(m.premarket_vol)}</b></span>
                {m.rel_vol != null && <span>Rel vol <b>{m.rel_vol}x</b></span>}
                <span className="ab-cap">{fmtCap(m.market_cap)}</span>
              </div>
              {m.reasons && m.reasons.length > 0 && <div className="ab-reasons">{m.reasons.join(" · ")}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function WatchlistAlertsCard({ apiFetch, onSwitchTicker }) {
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

  const dismiss = async (alertId) => {
    // Optimistic remove. Backend write is best-effort.
    setAlerts(prev => prev.filter(a => a.id !== alertId));
    try {
      await apiFetch("/api/watchlist_alerts/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: alertId }),
      });
    } catch (e) {
      // If write fails, the alert will reappear on next poll. Acceptable.
      console.warn("dismiss failed", e);
    }
  };

  if (alerts.length === 0 && !loading && !error) return null;

  const kindLabel = (k) => ({
    upgrade: "Upgrade",
    downgrade: "Downgrade",
    target_raise: "Target raised",
    target_cut: "Target cut",
  }[k] || k);
  const kindClass = (k) => ({
    upgrade: "wa-up",
    target_raise: "wa-up",
    downgrade: "wa-down",
    target_cut: "wa-down",
  }[k] || "");

  return (
    <div className="card watchlist-alerts-card"
         style={{marginBottom: "var(--row-gap)"}}>
      <div className="card-head">
        <div>
          <div className="kicker"
               title="Fresh analyst signals on tickers in your watchlist within the last 7 days. Polled every 5 minutes. Dismissed alerts do not reappear.">
            Watchlist · last 7 days · {alerts.length} fresh signal{alerts.length === 1 ? "" : "s"}
          </div>
          <div className="card-title">Analyst alerts</div>
        </div>
        <div style={{display: "flex", gap: 8, alignItems: "center"}}>
          {lastFetched && (
            <div className="muted" style={{fontSize: 11}}
                 title="Time of last poll. Auto-refreshes every 5 minutes.">
              Updated {lastFetched.toLocaleTimeString("en-US", {hour: "numeric", minute: "2-digit"})}
            </div>
          )}
          <button className="wa-collapse-btn"
                  onClick={() => setCollapsed(v => !v)}
                  title={collapsed ? "Expand the alerts list." : "Collapse the alerts list."}>
            {collapsed ? "Expand" : "Collapse"}
          </button>
        </div>
      </div>
      {error && (
        <div className="wa-error">Error loading alerts: {error}</div>
      )}
      {!collapsed && (
        <div className="wa-list">
          {alerts.map(a => (
            <div key={a.id} className={`wa-row ${kindClass(a.kind)}`}>
              <div className="wa-row-left">
                <span className="wa-symbol"
                      title={`${a.symbol}. Click Switch to load it on the dashboard.`}>
                  {a.symbol}
                </span>
                <span className={`wa-kind ${kindClass(a.kind)}`}
                      title={`${kindLabel(a.kind)} from ${a.firm} on ${a.date}.`}>
                  {kindLabel(a.kind)}
                </span>
                <span className="wa-firm" title={`Originating firm: ${a.firm}`}>
                  {a.firm}
                </span>
                {(a.from_grade && a.to_grade) && (
                  <span className="wa-grades"
                        title={`Rating change: ${a.from_grade} → ${a.to_grade}`}>
                    {a.from_grade} → {a.to_grade}
                  </span>
                )}
                <span className="wa-date" title="Date the signal was issued.">
                  {a.date}
                </span>
              </div>
              <div className="wa-row-right">
                <button className="wa-switch"
                        onClick={() => onSwitchTicker && onSwitchTicker(a.symbol)}
                        title={`Switch the dashboard to ${a.symbol} so you can review the chart, chain, and rec verdicts.`}>
                  Switch
                </button>
                <button className="wa-dismiss"
                        onClick={() => dismiss(a.id)}
                        title="Dismiss this alert. It will not reappear on subsequent polls.">
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TabBar({ active, onChange }) {
  return (
    <div className="tab-bar" role="tablist" aria-label="Dashboard sections"
         title="Switch sections. Each tab shows only its own cards. Cards stay live in the background, so switching is instant and nothing reloads. Your tab choice is remembered.">
      {TABS.map(t => (
        <button key={t.id} type="button" role="tab"
                aria-selected={active === t.id}
                className={`tab-btn ${active === t.id ? "active" : ""}`}
                onClick={() => onChange(t.id)}
                title={`Show the ${t.label} section.`}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

function TabPanel({ tab, active, children }) {
  return (
    <div className="tab-panel" role="tabpanel" data-tab={tab}
         style={active === tab ? undefined : { display: "none" }}>
      {children}
    </div>
  );
}

function WeatherBadge() {
  const WX_KEY = "jerry_weather_v1";
  const persisted = (() => {
    try { return JSON.parse(localStorage.getItem(WX_KEY)) || {}; }
    catch { return {}; }
  })();
  const [useGeo, setUseGeo] = useState(persisted.useGeo === true);
  const [wx, setWx] = useState(null);   // { temp, code, time } or null
  const [place, setPlace] = useState(persisted.useGeo ? "your location" : WeatherUtil.DEFAULT_COORDS.label);
  const [err, setErr] = useState(false);

  const load = React.useCallback((coords, label) => {
    const url = WeatherUtil.buildForecastUrl(coords.lat, coords.lon, "fahrenheit");
    fetch(url)
      .then(r => r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)))
      .then(j => {
        const cur = WeatherUtil.parseCurrent(j);
        if (!cur) throw new Error("bad shape");
        setWx(cur); setErr(false);
        if (label) setPlace(label);
      })
      .catch(() => setErr(true));
  }, []);

  // Device geolocation when opted in, else the Yonkers default. A denied
  // or failed geolocation falls back to Yonkers rather than going blank.
  const resolveAndLoad = React.useCallback(() => {
    if (useGeo && typeof navigator !== "undefined" && navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => load({ lat: pos.coords.latitude, lon: pos.coords.longitude }, "your location"),
        () => load(WeatherUtil.DEFAULT_COORDS, WeatherUtil.DEFAULT_COORDS.label),
        { timeout: 8000, maximumAge: 600000 }
      );
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
    try { localStorage.setItem(WX_KEY, JSON.stringify({ useGeo: next })); } catch {}
  };

  const meta = wx ? WeatherUtil.wxFromCode(wx.code) : null;
  const temp = wx ? WeatherUtil.formatTemp(wx.temp) : "—";
  const title = err
    ? "Weather unavailable. Open-Meteo did not respond. Tap to retry."
    : `${meta ? meta.label : "Loading"}, ${temp} at ${place}. Source Open-Meteo. Tap to ${useGeo ? "switch to Yonkers" : "use your location"}.`;

  return (
    <button type="button" className={`sb-weather-pill${err ? " wx-err" : ""}`} onClick={toggleGeo} title={title}>
      <span className="wx-icon">{err ? "⚠️" : (meta ? meta.icon : "🌡️")}</span>
      <span className="wx-temp">{err ? "wx" : temp}</span>
    </button>
  );
}

function LevelRepriceCard({ ticker, currentPrice, calls, puts, sugCall, sugPut, expectedMove, weeklyRows, activeExpDate, frontDte, apiFetch, strategyMode, livePrice }) {
  const [mode, setMode] = useState("gap");
  const [kind, setKind] = useState(strategyMode === "csp" ? "put" : "call");
  useEffect(() => { setKind(strategyMode === "csp" ? "put" : "call"); }, [strategyMode]);

  // This week's expirations with their own chains, so names like AAPL
  // and the index ETFs can pick Mon/Wed/Fri or 0DTE. Falls back to the
  // front-weekly chain passed in props if the fetch fails or is empty.
  const [weekChains, setWeekChains] = useState(null);  // {expirations:[{date,dte}], chains:{date:{calls,puts}}}
  const [expDate, setExpDate] = useState(null);        // selected expiration ISO
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
          setWeekChains(null);  // fall back to props chain
        }
      } catch (e) {
        if (alive) setWeekChains(null);
      }
    })();
    return () => { alive = false; };
  }, [ticker]);  // eslint-disable-line

  const expList = weekChains?.expirations || (activeExpDate ? [{ date: expISOprop, dte: frontDte || 7 }] : []);
  const selExp = expDate || expISOprop || (expList[0] && expList[0].date) || null;
  // Legs for the selected expiration and side. Use the fetched week chain
  // when available, else the front-weekly props.
  const legs = React.useMemo(() => {
    if (weekChains && selExp && weekChains.chains[selExp]) {
      const side = weekChains.chains[selExp][kind === "put" ? "puts" : "calls"] || [];
      if (side.length) return side;
    }
    return kind === "put" ? (puts || []) : (calls || []);
  }, [weekChains, selExp, kind, calls, puts]);
  const strikes = React.useMemo(
    () => legs.map(l => l.strike).filter(s => s != null).sort((a, b) => a - b),
    [legs]);
  // Default the strike to the expected-move level: the implied move up
  // for a call, down for a put, snapped to the nearest listed strike.
  // This is usually where Jerry wants to sell, so it saves retyping per
  // stock. Falls back to the suggested 0.20 delta strike, then spot.
  const expMoveStrike = (expectedMove && currentPrice)
    ? (kind === "put" ? currentPrice - expectedMove : currentPrice + expectedMove)
    : null;
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
  useEffect(() => { expMoveStrikeRef.current = expMoveStrike; strikesRef.current = strikes; });
  const snapDefaultStrike = React.useCallback(() => {
    const ss = strikesRef.current;
    if (!ss.length) return;
    const want = expMoveStrikeRef.current || suggested || currentPrice || ss[0];
    const near = ss.reduce((a, b) => Math.abs(b - want) < Math.abs(a - want) ? b : a, ss[0]);
    setStrike(near);
  }, [currentPrice, suggested]);
  useEffect(() => {  // reset the hold when side/expiry/ticker changes
    strikeEdited.current = false;
    clearTimeout(strikeTimer.current);
  }, [kind, selExp, ticker]);
  useEffect(() => {
    if (strikeEdited.current) return;  // do not clobber a held manual pick
    snapDefaultStrike();
  }, [kind, selExp, strikes.length, expMoveStrike, snapDefaultStrike]);
  const onStrikePick = (v) => {
    setStrike(Number(v));
    strikeEdited.current = true;
    clearTimeout(strikeTimer.current);
    strikeTimer.current = setTimeout(() => {
      strikeEdited.current = false;
      snapDefaultStrike();
    }, 20000);
  };

  const leg = React.useMemo(
    () => legs.find(l => l.strike === Number(strike)) || null, [legs, strike]);
  const legMid = leg ? ((leg.bid > 0 && leg.ask > 0) ? (leg.bid + leg.ask) / 2 : (leg.last || 0)) : 0;

  // Days to exp derived from the SELECTED expiration, not a manual field.
  const selExpMeta = expList.find(e => e.date === selExp);
  const dte = selExpMeta ? selExpMeta.dte : (frontDte || 7);
  const expLabel = selExp
    ? new Date(selExp + "T00:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })
    : "front weekly";
  const multiExp = expList.length > 1;

  const [rate, setRate] = useState("4.00");
  const [spotNow, setSpotNow] = useState(currentPrice ? currentPrice.toFixed(2) : "");
  const [target, setTarget] = useState("");
  const [gapHours, setGapHours] = useState(17);
  useEffect(() => { if (currentPrice) setSpotNow(currentPrice.toFixed(2)); }, [currentPrice]);

  // Jerry's default target: average Monday high percent plus average
  // Tuesday high percent across the weekly history, applied to the
  // current price. day_breakdown[day].high is the day's high versus its
  // prior close, in percent. This is the same data the Day of week card
  // reads. The target pre-fills per ticker and stays editable, so it no
  // longer carries the last stock's number over.
  const monTueTarget = React.useMemo(() => {
    if (!Array.isArray(weeklyRows) || !weeklyRows.length || !currentPrice) return null;
    const avgHigh = (day) => {
      const v = [];
      for (const r of weeklyRows) {
        const db = r.day_breakdown && r.day_breakdown[day];
        if (db && db.high != null && isFinite(db.high)) v.push(db.high);
      }
      return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
    };
    const mon = avgHigh("Mon"), tue = avgHigh("Tue");
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
  useEffect(() => { monTueRef.current = monTueTarget; });
  const applyDefaultTarget = React.useCallback(() => {
    const d = monTueRef.current;
    setTarget(d != null ? String(d) : "");
  }, []);
  useEffect(() => {  // reset the hold on ticker change
    targetEdited.current = false;
    clearTimeout(targetTimer.current);
  }, [ticker]);
  useEffect(() => {
    if (targetEdited.current) return;  // do not clobber a held manual edit
    applyDefaultTarget();
  }, [ticker, monTueTarget, applyDefaultTarget]);
  const onTargetChange = (v) => {
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

  const fmt = (n, d = 2) => (n == null || isNaN(n)) ? "—" : Number(n).toFixed(d);
  const base = () => ({
    ticker, kind, strike: Number(strike), days_to_exp: dte,
    r: parseFloat(rate) / 100, expiration: selExp || expISOprop,
    current_price: legMid > 0 ? +legMid.toFixed(4) : undefined,
  });
  const toSpot = (v, ref) => { const n = parseFloat(v); if (isNaN(n)) return null; return pctMode ? +(ref * (1 + n / 100)).toFixed(2) : n; };

  const runGap = async () => {
    setErr(null); setBusy(true); setOut(null);
    try {
      if (!leg || legMid <= 0) throw new Error("no quote on the selected strike; pick another strike");
      const sNow = parseFloat(spotNow), tgt = parseFloat(target);
      if (isNaN(sNow) || isNaN(tgt)) throw new Error("enter stock now and a target price");
      const hrs = parseFloat(gapHours) || 0;
      const levels = [
        { label: "now", target_spot: sNow, hours_from_now: 0, iv_shift: 0 },
        { label: "flat", target_spot: tgt, hours_from_now: hrs, iv_shift: 0 },
        { label: "ivup", target_spot: tgt, hours_from_now: hrs, iv_shift: 0.05 },
        { label: "ivdn5", target_spot: tgt, hours_from_now: hrs, iv_shift: -0.05 },
        { label: "ivdn10", target_spot: tgt, hours_from_now: hrs, iv_shift: -0.10 },
      ];
      const r = await apiFetch("/api/reprice", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...base(), spot_now: sNow, levels }),
      });
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || "reprice failed");
      const rows = {}; d.levels.forEach(x => { rows[x.label] = x; });
      const q = d.current_price_used != null ? d.current_price_used : legMid;
      const sd = rows.now ? rows.now.delta : (leg.delta ?? null);
      const deltaEst = (q != null && sd != null) ? q + sd * (tgt - sNow) : null;
      setOut({
        iv: d.implied_vol_now, q, startDelta: sd, deltaEst,
        flat: rows.flat ? rows.flat.price : null,
        sweep: [
          { lbl: "IV +5 pts (vol expands)", v: rows.ivup ? rows.ivup.price : null },
          { lbl: "IV flat", v: rows.flat ? rows.flat.price : null },
          { lbl: "IV -5 pts (crush)", v: rows.ivdn5 ? rows.ivdn5.price : null },
          { lbl: "IV -10 pts (hard crush)", v: rows.ivdn10 ? rows.ivdn10.price : null },
        ],
        tgt, sNow,
      });
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const runFade = async () => {
    setErr(null); setBusy(true); setFade(null); setSaved(false);
    try {
      if (!leg || legMid <= 0) throw new Error("no quote on the selected strike; pick another strike");
      const o = parseFloat(spotNow);
      if (isNaN(o)) throw new Error("enter the open price");
      const sellS = toSpot(sell, o), coverS = toSpot(cover, o), stopS = toSpot(stop, o);
      if (sellS == null || coverS == null) throw new Error("enter sell and cover levels");
      const r = await apiFetch("/api/fade", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...base(), spot_now: o, sell_spot: sellS, cover_spot: coverS,
          stop_spot: stopS, hours_held: parseFloat(hoursHeld) || 0, contracts: parseInt(contracts, 10) || 1 }),
      });
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || "fade failed");
      d._sellS = sellS; d._coverS = coverS; d._stopS = stopS;
      setFade(d);
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const saveFade = async () => {
    try {
      const r = await apiFetch("/api/fade/save", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker, kind, strike: Number(strike), days_to_exp: dte,
          sell_spot: fade._sellS, cover_spot: fade._coverS, stop_spot: fade._stopS,
          contracts: parseInt(contracts, 10) || 1, fade }),
      });
      if (r.ok) setSaved(true);
    } catch (e) { /* non-fatal */ }
  };

  const live = livePrice ?? currentPrice;
  let status = "Waiting", statusCls = "lr-wait";
  if (mode === "fade" && fade && fade._sellS != null && live) {
    const dist = Math.abs(live - fade._sellS) / fade._sellS;
    if ((kind === "call" && live >= fade._sellS) || (kind === "put" && live <= fade._sellS)) { status = "Tagged"; statusCls = "lr-tagged"; }
    else if (dist <= 0.005) { status = "Approaching"; statusCls = "lr-approach"; }
  }

  const noChain = !strikes.length;

  return (
    <div className="card level-reprice-card" style={{marginBottom: "var(--row-gap)"}}>
      <div className="card-head">
        <div>
          <div className="kicker" title="Pick a strike from the live chain, the quote and expiration come with it. Gap shows what the contract should be worth at the open or at a target price you set. Level fade stages a sell at a high and a cover at a settle. IV is backed out of the live mid so every number is self-consistent.">
            Where the premium goes · {kind === "call" ? "call" : "put"}
          </div>
          <div className="card-title" title="Reprice this contract at a target stock level using Black Scholes, not the delta shortcut.">Level Reprice</div>
        </div>
        {mode === "fade" && fade && (
          <div className={`lr-status ${statusCls}`} title="Live trigger. Waiting until the underlying nears your sell level, Approaching within 0.50 percent, Tagged once reached.">{status}</div>
        )}
      </div>

      <div className="lr-modebar">
        <button className={mode === "gap" ? "active" : ""} onClick={() => setMode("gap")} title="What is the contract worth at the open or at a target price.">Gap</button>
        <button className={mode === "fade" ? "active" : ""} onClick={() => setMode("fade")} title="Stage an intraday fade, sell at a high and cover at a settle.">Level fade</button>
      </div>

      {noChain ? (
        <div className="lr-err" title="The option chain has not loaded for this ticker yet.">Chain not loaded yet for {ticker}. Give it a moment, then try again.</div>
      ) : (
      <>
      <div className="lr-stage">
        <div className="lr-field">
          <label title="Call or put. Defaults from the strategy toggle.">Kind</label>
          <div className="lr-seg">
            <button className={kind === "call" ? "active" : ""} onClick={() => setKind("call")} title="Price a call.">Call</button>
            <button className={kind === "put" ? "active" : ""} onClick={() => setKind("put")} title="Price a put.">Put</button>
          </div>
        </div>
        <div className="lr-field">
          <label title="Strike from the live option chain, pre-set to the expected-move level. Selecting one pulls its quote automatically.">Strike</label>
          <select value={strike ?? ""} onChange={e => onStrikePick(e.target.value)} title="Valid strikes from the current chain. Defaults to the expected-move level; a manual pick is held for 20 seconds before the default reapplies.">
            {strikes.map(s => <option key={s} value={s}>{s.toFixed(2)}</option>)}
          </select>
          {expMoveStrike != null && (
            <div className="lr-hint" title={`Expected move ${kind === "put" ? "down" : "up"} from the ATM straddle, snapped to the nearest listed strike.`}>Exp move {kind === "put" ? "↓" : "↑"} ${expMoveStrike.toFixed(2)}</div>
          )}
        </div>
        <div className="lr-field">
          <label title="Expiration this week. For names with Monday, Wednesday, Friday, or 0DTE options, pick which one. Single-expiry weeks show the front weekly.">Expiry</label>
          {multiExp ? (
            <select value={selExp || ""} onChange={e => setExpDate(e.target.value)} title="Pick which expiration this week to price against. Days to expiration and the chain update with it.">
              {expList.map(e => {
                const lbl = new Date(e.date + "T00:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
                return <option key={e.date} value={e.date}>{lbl} · {e.dte}d{e.dte === 0 ? " (0DTE)" : ""}</option>;
              })}
            </select>
          ) : (
            <div className="lr-readout" title={`Expiring ${expLabel}, ${dte} days out.`}>{expLabel} · {dte}d</div>
          )}
        </div>
        <div className="lr-field">
          <label title="Live option mid for the selected strike, pulled from the chain. Read-only.">Live quote</label>
          <div className="lr-readout num" title="Current mid of the selected contract from the live chain.">{legMid > 0 ? "$" + fmt(legMid) : "no quote"}</div>
        </div>
        <div className="lr-field">
          <label title="Risk free rate, annualized percent.">Rate %</label>
          <input type="number" step="0.01" value={rate} onChange={e => setRate(e.target.value)} title="Annualized risk free rate in percent." />
        </div>
      </div>

      {mode === "gap" ? (
        <div className="lr-levels">
          <div className="lr-stage">
            <div className="lr-field">
              <label title="The stock price the live quote reflects, usually the prior close. Auto-filled, editable.">Stock now</label>
              <input type="number" step="0.01" value={spotNow} onChange={e => setSpotNow(e.target.value)} title="Spot the current quote reflects." />
            </div>
            <div className="lr-field">
              <label title="Pre-filled with the Monday plus Tuesday average high target from your weekly history, applied to the current price. This is usually where you sell. Editable for any other premarket open or target.">If stock reaches</label>
              <input type="number" step="0.01" value={target} placeholder="target price" onChange={e => onTargetChange(e.target.value)} title="Target stock price to reprice the contract at. Pre-filled from the Mon plus Tue high; a manual edit is held for 20 seconds before the default reapplies." />
              {monTueTarget != null && (
                <div className="lr-hint" title="Average Monday high percent plus average Tuesday high percent, applied to the current price.">Mon+Tue high ${monTueTarget.toFixed(2)}</div>
              )}
            </div>
            <div className="lr-field">
              <label title="Hours of decay between now and the move. Overnight to the open is about 17, an intraday target is 1 to 3.">Hours to move</label>
              <input type="number" step="0.5" value={gapHours} onChange={e => setGapHours(e.target.value)} title="Hours of time decay before the move." />
            </div>
          </div>
          <button className="lr-run" onClick={runGap} disabled={busy} title="Back out IV from the live mid and reprice the contract at the target.">{busy ? "Working…" : "Reprice at target"}</button>
        </div>
      ) : (
        <div className="lr-levels">
          <div className="lr-levels-head">
            <span title="Enter each level as a price, or as a percent move from the open.">Levels</span>
            <div className="lr-seg lr-seg-sm">
              <button className={!pctMode ? "active" : ""} onClick={() => setPctMode(false)} title="Enter levels as absolute prices.">Price</button>
              <button className={pctMode ? "active" : ""} onClick={() => setPctMode(true)} title="Enter levels as percent move from the open.">% from open</button>
            </div>
          </div>
          <div className="lr-stage">
            <div className="lr-field">
              <label title="The day's open price, the reference for percent levels and the spot the quote reflects.">Open</label>
              <input type="number" step="0.01" value={spotNow} onChange={e => setSpotNow(e.target.value)} title="Opening stock price." />
            </div>
            <div className="lr-field">
              <label title="Where you sell to open, typically near the expected high.">Sell {pctMode ? "%" : "$"}</label>
              <input type="number" step="0.01" value={sell} onChange={e => setSell(e.target.value)} title="Stock level where you sell to open." />
            </div>
            <div className="lr-field">
              <label title="Where you buy to close, typically near the expected settle.">Cover {pctMode ? "%" : "$"}</label>
              <input type="number" step="0.01" value={cover} onChange={e => setCover(e.target.value)} title="Stock level where you buy to close." />
            </div>
            <div className="lr-field">
              <label title="The adverse level that defines max risk. Optional.">Stop {pctMode ? "%" : "$"}</label>
              <input type="number" step="0.01" value={stop} onChange={e => setStop(e.target.value)} title="Adverse level used to price max risk." />
            </div>
            <div className="lr-field">
              <label title="Hours between the sell and the cover.">Hours held</label>
              <input type="number" step="0.5" value={hoursHeld} onChange={e => setHoursHeld(e.target.value)} title="Hours between selling and covering." />
            </div>
            <div className="lr-field">
              <label title="Number of contracts, scales the totals.">Contracts</label>
              <input type="number" value={contracts} onChange={e => setContracts(e.target.value)} title="Contract count for totals." />
            </div>
          </div>
          <button className="lr-run" onClick={runFade} disabled={busy} title="Back out IV from the live mid and price the sell, cover, and stop.">{busy ? "Working…" : "Price the fade"}</button>
        </div>
      )}
      </>
      )}

      {err && <div className="lr-err" title="The pricer could not produce a result, often when the quote is at or below intrinsic.">{err}</div>}

      {mode === "gap" && out && (
        <div className="lr-results">
          <div className="lr-iv" title="Implied vol backed out of the live mid at the stock-now price, used for every projection below.">Backed out IV {out.iv != null ? (out.iv * 100).toFixed(2) + "%" : "—"} · start delta {fmt(out.startDelta, 3)} · live mid ${fmt(out.q)}</div>
          <div className="lr-compare">
            <div className="lr-cmp lr-cmp-old" title="Your delta shortcut: quote plus starting delta times the move. It ignores gamma, so it undershoots on big moves.">
              <span>Delta shortcut</span><b className="num">${fmt(out.deltaEst)}</b>
            </div>
            <div className="lr-cmp-arrow" aria-hidden="true">→</div>
            <div className="lr-cmp lr-cmp-true" title="Full Black Scholes reprice at the target. Captures delta, gamma, and time decay exactly. This is where to set your sell.">
              <span>Contract at {fmt(out.tgt)}</span><b className="num">${fmt(out.flat)}</b>
            </div>
          </div>
          <table className="lr-table lr-sweep">
            <thead>
              <tr>
                <th title="IV assumption at the target, in vol points off the backed out IV.">IV scenario</th>
                <th title="Repriced contract value at the target under this IV assumption.">Contract at {fmt(out.tgt)}</th>
              </tr>
            </thead>
            <tbody>
              {out.sweep.map((s, i) => (
                <tr key={i} className={s.lbl === "IV flat" ? "lr-row-hot" : ""}>
                  <td>{s.lbl}</td>
                  <td className="num">${fmt(s.v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {mode === "fade" && fade && (
        <div className="lr-fade">
          <div className="lr-iv" title="Implied vol backed out of the live mid, used for the sell, cover, and stop.">Backed out IV {fade.implied_vol_now != null ? (fade.implied_vol_now * 100).toFixed(2) + "%" : "—"}</div>
          <div className="lr-fade-cap" title="Net premium captured per contract, sell price minus cover price times 100.">
            <span className="lr-fade-cap-num">${fmt(fade.capture_per_contract)}</span>
            <span className="lr-fade-cap-lbl">capture per contract{fade.capture_total != null ? " · $" + fmt(fade.capture_total) + " total" : ""}</span>
          </div>
          <div className="lr-fade-grid">
            <div title="Model option price at the sell level. You sell to open here."><span>Sell at</span><b className="num">${fmt(fade.sell_price)}</b></div>
            <div title="Model option price at the cover level. You buy to close here."><span>Cover at</span><b className="num">${fmt(fade.cover_price)}</b></div>
            <div title="Cost to cover at the stop minus the sell price, per contract. Defined max risk."><span>Max risk</span><b className="num down">{fade.max_risk_per_contract != null ? "$" + fmt(fade.max_risk_per_contract) : "—"}</b></div>
            <div title="Capture divided by max risk."><span>Risk reward</span><b className="num">{fade.risk_reward != null ? fade.risk_reward.toFixed(2) : "—"}</b></div>
          </div>
          {status === "Tagged" && fade.live_quote && fade.live_quote.mid && (
            <div className="lr-trigger" title="The underlying reached your sell level. Suggested limit is the live mid; the cover target is locked to the model cover price.">
              Tagged. Suggested sell limit ${fmt(fade.live_quote.mid)} · lock cover target ${fmt(fade.cover_price)}
            </div>
          )}
          {fade.iv_sweep && fade.iv_sweep.length > 0 && (
            <table className="lr-table lr-sweep">
              <thead>
                <tr>
                  <th title="IV change in vol points applied at the sell.">IV shift</th>
                  <th title="Net capture per contract at this IV shift.">Capture / contract</th>
                  <th title="Net capture across all contracts at this IV shift.">Capture total</th>
                </tr>
              </thead>
              <tbody>
                {fade.iv_sweep.map((s, i) => (
                  <tr key={i}>
                    <td className="num">{s.iv_shift >= 0 ? "+" : ""}{s.iv_shift.toFixed(2)}</td>
                    <td className="num">${fmt(s.capture_per_contract)}</td>
                    <td className="num">${fmt(s.capture_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <button className="lr-save" onClick={saveFade} title="Save this staged fade to disk so it persists across reloads.">{saved ? "Saved" : "Save fade"}</button>
        </div>
      )}
    </div>
  );
}

function WinRateCard({ apiFetch }) {
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
    let wins = 0, losses = 0, breakeven = 0;
    let totalPnl = 0, totalPremiumCollected = 0;
    let best = null, worst = null;
    let deltaSum = 0, deltaCount = 0;
    for (const t of opt) {
      // Shared formula (v1.21): JournalUtil.tradePnl / premiumCollected
      // so the tiles, CSV export, and P/L chart never diverge.
      const pnl = JournalUtil.tradePnl(t);
      const premCollected = JournalUtil.premiumCollected(t);
      totalPnl += pnl;
      totalPremiumCollected += premCollected;
      if (pnl > 0.01) wins++;
      else if (pnl < -0.01) losses++;
      else breakeven++;
      if (best == null || pnl > best.pnl) best = { ...t, pnl };
      if (worst == null || pnl < worst.pnl) worst = { ...t, pnl };
      if (t.entry_delta != null) {
        deltaSum += Math.abs(t.entry_delta);
        deltaCount++;
      }
    }
    const total = wins + losses + breakeven;
    const winRate = total > 0 ? (wins / total) * 100 : 0;
    const avgDelta = deltaCount > 0 ? deltaSum / deltaCount : null;
    const avgPnl = total > 0 ? totalPnl / total : 0;
    return { wins, losses, breakeven, total, totalPnl, totalPremiumCollected,
             winRate, best, worst, avgDelta, avgPnl };
  }, [trades]);

  // Cumulative realized P/L over time for the chart (v1.21). Same
  // per-trade formula as the tiles, via JournalUtil, so they agree.
  const series = React.useMemo(
    () => JournalUtil.buildCumulativePnlSeries(trades), [trades]);

  // CSV export for tax prep (v1.21). Built client-side from the journal
  // already in memory, so there is no new backend endpoint. The export
  // includes every closed row, not just options. Downloads via a Blob.
  const exportCsv = () => {
    try {
      const csv = JournalUtil.buildJournalCsv(trades);
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
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
    return (
      <div className="card win-rate-card"
           style={{marginBottom: "var(--row-gap)"}}>
        <div className="card-head">
          <div>
            <div className="kicker"
                 title="Realized performance on closed positions. Populates as you close trades in the position tracker.">
              Realized performance
            </div>
            <div className="card-title">Win rate</div>
          </div>
        </div>
        <div className="muted" style={{padding: "12px 0", fontSize: 13}}>
          {loading ? "Loading…" : error ? `Error: ${error}` : "No closed trades yet. The win rate populates as you close positions in the tracker below."}
        </div>
      </div>
    );
  }

  const sgn = (v) => v >= 0 ? "+" : "";
  return (
    <div className="card win-rate-card"
         style={{marginBottom: "var(--row-gap)"}}>
      <div className="card-head">
        <div>
          <div className="kicker"
               title={`Realized performance from ${stats.total} closed option trade${stats.total === 1 ? "" : "s"}. Stock positions excluded so the metric reflects premium-selling skill specifically.`}>
            Closed trades · {stats.total}
          </div>
          <div className="card-title">Win rate</div>
        </div>
        <button type="button" className="wr-export-btn" onClick={exportCsv}
                title="Download the full closed-trade journal as a CSV for tax prep. One row per closed trade with realized P/L and premium collected computed per contract. Includes stock rows; P/L is filled for option rows only.">
          Export CSV
        </button>
      </div>
      <div className="wr-grid">
        <div className="wr-tile" title="Percentage of closed option trades that finished profitable. A trade is a 'win' if realized P/L exceeded $0.01. Breakevens excluded from the win count but included in the total.">
          <div className="wr-tile-lbl">Win rate</div>
          <div className={`wr-tile-val ${stats.winRate >= 70 ? "up" : stats.winRate >= 50 ? "" : "down"}`}>
            {stats.winRate.toFixed(1)}%
          </div>
          <div className="wr-tile-sub">
            {stats.wins}W · {stats.losses}L{stats.breakeven > 0 ? ` · ${stats.breakeven}BE` : ""}
          </div>
        </div>
        <div className="wr-tile" title="Total realized P/L across all closed option trades. Per-contract P/L = (entry premium − exit premium) × 100 × contracts for short positions.">
          <div className="wr-tile-lbl">Total P/L</div>
          <div className={`wr-tile-val ${stats.totalPnl >= 0 ? "up" : "down"}`}>
            {sgn(stats.totalPnl)}${stats.totalPnl.toFixed(0)}
          </div>
          <div className="wr-tile-sub">
            avg {sgn(stats.avgPnl)}${stats.avgPnl.toFixed(0)}/trade
          </div>
        </div>
        <div className="wr-tile" title="Total premium collected on short-option entries. Independent of P/L since some of this gets returned at close. Useful for tracking gross income before assignment costs and roll debits.">
          <div className="wr-tile-lbl">Premium collected</div>
          <div className="wr-tile-val">
            ${stats.totalPremiumCollected.toFixed(0)}
          </div>
        </div>
        {stats.avgDelta != null && (
          <div className="wr-tile" title="Average absolute delta at entry across closed trades. Drift away from the 0.20 target indicates strike picking is creeping more or less aggressive over time.">
            <div className="wr-tile-lbl">Avg entry Δ</div>
            <div className={`wr-tile-val ${Math.abs(stats.avgDelta - 0.20) <= 0.04 ? "up" : "warn"}`}>
              {stats.avgDelta.toFixed(2)}
            </div>
            <div className="wr-tile-sub">target 0.20</div>
          </div>
        )}
      </div>
      <div className="wr-extremes">
        {stats.best && stats.best.pnl > 0 && (
          <div className="wr-extreme wr-extreme-best"
               title={`Best closed trade: ${stats.best.ticker} ${stats.best.type} ${stats.best.strike != null ? "$" + stats.best.strike : ""} ${stats.best.expiration || ""}, opened ${stats.best.opened_at}, closed ${stats.best.closed_at}.`}>
            <span className="wr-extreme-lbl">Best</span>
            <span className="wr-extreme-sym">{stats.best.ticker}</span>
            <span className="wr-extreme-val up">+${stats.best.pnl.toFixed(0)}</span>
          </div>
        )}
        {stats.worst && stats.worst.pnl < 0 && (
          <div className="wr-extreme wr-extreme-worst"
               title={`Worst closed trade: ${stats.worst.ticker} ${stats.worst.type} ${stats.worst.strike != null ? "$" + stats.worst.strike : ""} ${stats.worst.expiration || ""}, opened ${stats.worst.opened_at}, closed ${stats.worst.closed_at}.`}>
            <span className="wr-extreme-lbl">Worst</span>
            <span className="wr-extreme-sym">{stats.worst.ticker}</span>
            <span className="wr-extreme-val down">${stats.worst.pnl.toFixed(0)}</span>
          </div>
        )}
      </div>
      {series.length >= 2 && (() => {
        const VW = 320, VH = 90, padX = 6, padY = 10;
        const cums = series.map(p => p.cum);
        const lo = Math.min(0, ...cums);
        const hi = Math.max(0, ...cums);
        const span = (hi - lo) || 1;
        const n = series.length;
        const px = (i) => padX + (n === 1 ? 0 : (i * (VW - 2 * padX) / (n - 1)));
        const py = (v) => padY + (hi - v) / span * (VH - 2 * padY);
        const pts = series.map((p, i) => [px(i), py(p.cum)]);
        const line = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
        const area = line + " L" + px(n - 1).toFixed(1) + " " + py(0).toFixed(1)
                          + " L" + px(0).toFixed(1) + " " + py(0).toFixed(1) + " Z";
        const last = series[n - 1].cum;
        const up = last >= 0;
        const zeroY = py(0).toFixed(1);
        return (
          <div className="wr-chart"
               title="Cumulative realized P/L across closed option trades, ordered by close date. Each step is one closed trade. The dashed line is breakeven. Realized only; open positions are excluded.">
            <div className="wr-chart-head">
              <span className="wr-chart-lbl">Cumulative P/L · {n} trades</span>
              <span className={`wr-chart-now ${up ? "up" : "down"}`}>{up ? "+" : ""}${last.toFixed(0)}</span>
            </div>
            <svg className="wr-chart-svg" viewBox={`0 0 ${VW} ${VH}`} preserveAspectRatio="none"
                 role="img" aria-label="Cumulative realized P/L over time">
              <path d={area} className={`wr-area ${up ? "up" : "down"}`} />
              <line x1={padX} x2={VW - padX} y1={zeroY} y2={zeroY} className="wr-zero" />
              <path d={line} className={`wr-line ${up ? "up" : "down"}`} />
              <circle cx={px(n - 1).toFixed(1)} cy={py(last).toFixed(1)} r="3.5"
                      className={`wr-dot ${up ? "up" : "down"}`} />
            </svg>
          </div>
        );
      })()}
    </div>
  );
}

function EarningsCrushCard({ apiFetch, onSwitchTicker }) {
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

  return (
    <div className="card earnings-crush-card"
         style={{marginBottom: "var(--row-gap)"}}>
      <div className="card-head">
        <div>
          <div className="kicker"
               title="Watchlist tickers with earnings inside 14 days, ranked by proximity. The crush figure is HEURISTIC: it uses pre vs post realized vol around past earnings as a proxy for implied vol crush since historical IV is paid data. Treat as directional not exact.">
            Watchlist · next 14 days · heuristic
          </div>
          <div className="card-title">Earnings vol crush</div>
        </div>
        <button className="ec-refresh-btn"
                disabled={loading}
                onClick={fetchCrush}
                title="Re-fetch earnings dates and recompute crush samples. Slower than the rest of the dashboard since it pulls daily history per ticker.">
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>
      {error && <div className="ec-error">Error: {error}</div>}
      {rows.length > 0 && (
        <div className="ec-table">
          <div className="ec-head">
            <span title="Ticker symbol. Click any row to load it on the dashboard.">Ticker</span>
            <span title="Next earnings date.">Earnings</span>
            <span title="Days until the next earnings event.">In</span>
            <span title="Median post-earnings IV crush across past prints. Calculated as 1 minus the ratio of post-earnings 5-day realized vol over pre-earnings 5-day realized vol. Higher = more typical crush, which means short premium going into earnings tends to work but you are giving back vega the day after.">Median crush</span>
            <span title="Average post-earnings crush across past prints. Compared to median this shows whether one outlier earnings move skewed the average.">Avg crush</span>
            <span title="Number of past earnings events sampled for the crush calculation. More = more reliable.">Samples</span>
          </div>
          {rows.map(r => (
            <div key={r.symbol} className="ec-row"
                 onClick={() => onSwitchTicker && onSwitchTicker(r.symbol)}
                 title={`Click to switch the dashboard to ${r.symbol}. Past samples: ${r.samples.map(s => s.toFixed(0) + "%").join(", ")}`}>
              <span className="ec-sym">{r.symbol}</span>
              <span>{r.next_earnings}</span>
              <span className={r.days_to_earnings <= 3 ? "warn" : ""}>{r.days_to_earnings}d</span>
              <span className={r.median_crush_pct >= 30 ? "up" : r.median_crush_pct < 10 ? "warn" : ""}>
                {r.median_crush_pct >= 0 ? "" : "+"}{r.median_crush_pct.toFixed(1)}%
              </span>
              <span>{r.avg_crush_pct.toFixed(1)}%</span>
              <span className="muted">{r.sample_count}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PushSettingsCard({ apiFetch }) {
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
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sendTest = async () => {
    if (!apiFetch) return;
    setTesting(true);
    setTestResult(null);
    try {
      const r = await apiFetch("/api/push/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: "Test push from your dashboard. If you see this, Pushover is wired correctly." }),
      });
      const j = await r.json();
      setTestResult(j);
    } catch (e) {
      setTestResult({ ok: false, error: String(e.message || e) });
    } finally {
      setTesting(false);
    }
  };

  if (!status) return null;
  const configured = status.configured;

  return (
    <div className="card push-settings-card"
         style={{marginBottom: "var(--row-gap)"}}>
      <div className="card-head">
        <div>
          <div className="kicker"
               title={configured ? "Pushover env vars detected. Roll flag alerts will fire to your phone with 12-hour dedupe per position." : "Pushover env vars missing. Configure PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY via 'jerry env set' to enable phone alerts."}>
            Phone alerts · Pushover · {configured ? "configured" : "not configured"}
          </div>
          <div className="card-title">Push notifications</div>
        </div>
        <button className="ps-collapse-btn"
                onClick={() => setCollapsed(v => !v)}
                title={collapsed ? "Show setup details and test button." : "Hide setup details."}>
          {collapsed ? "Details" : "Hide"}
        </button>
      </div>
      {!collapsed && (
        <div className="ps-body">
          {configured ? (
            <>
              <div className="ps-row">
                <span className="ps-row-lbl"
                      title="Pushover application token. Set via 'jerry env set PUSHOVER_APP_TOKEN xxx'.">
                  App token
                </span>
                <span className="ps-row-val ps-ok">set</span>
              </div>
              <div className="ps-row">
                <span className="ps-row-lbl"
                      title="Pushover user key. Set via 'jerry env set PUSHOVER_USER_KEY xxx'.">
                  User key
                </span>
                <span className="ps-row-val ps-ok">set</span>
              </div>
              <div className="ps-actions">
                <button className="ps-test-btn"
                        disabled={testing}
                        onClick={sendTest}
                        title="Send a test push to your phone right now to confirm Pushover is wired correctly.">
                  {testing ? "Sending…" : "Send test push"}
                </button>
                {testResult && (
                  <span className={`ps-test-result ${testResult.ok ? "ps-ok" : "ps-err"}`}
                        title={testResult.ok ? "Pushover accepted the request. Check your phone." : `Pushover rejected: ${testResult.error || testResult.response}`}>
                    {testResult.ok ? "✓ sent · check phone" : `✕ ${testResult.error || "failed"}`}
                  </span>
                )}
              </div>
              <div className="ps-help">
                Roll flag alerts fire when an open short option position has DTE ≤ 7 and |delta| ≥ 0.40. Dedupe window is 12 hours per position so you get reminded once per day, not every poll.
              </div>
            </>
          ) : (
            <>
              <div className="ps-help">
                To enable phone alerts on roll-flag triggers, install the Pushover app, then set the two env vars from terminal.
              </div>
              <pre className="ps-code">{`jerry env set PUSHOVER_APP_TOKEN <token-from-pushover-dashboard>
jerry env set PUSHOVER_USER_KEY <user-key-from-pushover-account>
jerry restart`}</pre>
              <div className="ps-help">
                Pushover app is a one-time $5 purchase. Once configured, this card flips to show a "Send test push" button.
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function BrokerImportCard({ apiFetch, positions, setPositions }) {
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

  const fetchPositions = async (hash) => {
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
  const isAlreadyTracked = (bp) => {
    return positions.some(p => {
      if ((p.ticker || "").toUpperCase() !== (bp.ticker || "").toUpperCase()) return false;
      if (p.type !== bp.type) return false;
      if (p.type === "stock") return true;
      if (Math.abs((p.strike || 0) - (bp.strike || 0)) > 0.01) return false;
      if ((p.expiration || "") !== (bp.expiration || "")) return false;
      return true;
    });
  };

  const importPosition = (bp) => {
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
      notes: "Imported from Schwab",
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

  return (
    <div className="card broker-import-card"
         style={{marginBottom: "var(--row-gap)"}}>
      <div className="card-head">
        <div>
          <div className="kicker"
               title="Phase 1 of broker import: read-only fetch from Schwab. You review and click Add on positions you want tracked. Phase 2 will add auto-reconciliation on fills and rolls.">
            Schwab · phase 1 · manual import
          </div>
          <div className="card-title">Broker import</div>
        </div>
        <div style={{display: "flex", gap: 8, alignItems: "center"}}>
          {lastFetched && (
            <div className="muted" style={{fontSize: 11}}
                 title="Time of last fetch from Schwab.">
              Updated {lastFetched.toLocaleTimeString("en-US", {hour: "numeric", minute: "2-digit"})}
            </div>
          )}
          <button className="bi-collapse-btn"
                  onClick={() => setCollapsed(v => !v)}
                  title={collapsed ? "Expand the broker import panel." : "Collapse the panel."}>
            {collapsed ? "Details" : "Hide"}
          </button>
        </div>
      </div>
      {!collapsed && (
        <div className="bi-body">
          {accountsState === null && (
            <div className="muted" style={{fontSize: 12, padding: "8px 0"}}>
              {loadingAccounts ? "Loading accounts…" : "Initializing…"}
            </div>
          )}
          {accountsState && !configured && (
            <div className="bi-help">
              Schwab is not configured. Run <code>jerry auth</code> from terminal to authenticate, then click Refresh below.
              <button className="bi-refresh-btn"
                      style={{marginTop: 8}}
                      onClick={fetchAccounts}
                      title="Re-check Schwab configuration.">
                Refresh status
              </button>
            </div>
          )}
          {accountsState && configured && accounts.length === 0 && (
            <div className="muted" style={{fontSize: 12, padding: "8px 0"}}>
              No accounts returned by Schwab. Verify your OAuth scope includes account read.
            </div>
          )}
          {accountsState && configured && accounts.length > 0 && (
            <>
              {accounts.length > 1 && (
                <div className="bi-account-picker">
                  <span className="bi-row-lbl"
                        title="Schwab returns one or more linked accounts. Select which one to import positions from.">
                    Account
                  </span>
                  <select className="bi-account-select"
                          value={selectedHash || ""}
                          onChange={e => setSelectedHash(e.target.value)}>
                    <option value="">Select account…</option>
                    {accounts.map(a => (
                      <option key={a.hash} value={a.hash}>
                        Account ending {a.masked}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {accounts.length === 1 && (
                <div className="muted" style={{fontSize: 11, marginBottom: 8}}>
                  Account ending {accounts[0].masked} (auto-selected, only one linked)
                </div>
              )}
              <div className="bi-actions">
                <button className="bi-refresh-btn"
                        disabled={loadingPositions || !selectedHash}
                        onClick={() => fetchPositions(selectedHash)}
                        title="Re-fetch positions from Schwab. Cached server-side for 60 seconds so back-to-back clicks return the same data.">
                  {loadingPositions ? "Loading…" : "Refresh from broker"}
                </button>
                {brokerPositions.length > 0 && (
                  <button className="bi-import-all-btn"
                          onClick={importAll}
                          title="Add all broker positions to the local tracker that are not already in it. Existing positions are skipped (no duplicates).">
                    Import all new ({brokerPositions.filter(bp => !isAlreadyTracked(bp)).length})
                  </button>
                )}
              </div>
              {error && <div className="bi-error">Error: {error}</div>}
              {brokerPositions.length === 0 && !loadingPositions && lastFetched && (
                <div className="muted" style={{fontSize: 12, padding: "8px 0"}}>
                  Schwab returned 0 positions for this account. If you have open positions, this may indicate the position is in a non-equity, non-option asset class that the dashboard does not yet handle.
                </div>
              )}
              {brokerPositions.length > 0 && (
                <div className="bi-table">
                  <div className="bi-head">
                    <span title="Underlying ticker symbol.">Ticker</span>
                    <span title="Position type. stock = shares, call/put = single-leg option.">Type</span>
                    <span title="Strike for options. Empty for stock.">Strike</span>
                    <span title="Expiration for options. Empty for stock.">Exp</span>
                    <span title="Quantity. Negative = short.">Qty</span>
                    <span title="Average entry price per share.">Avg cost</span>
                    <span title="Status vs local tracker.">Status</span>
                  </div>
                  {brokerPositions.map((bp, i) => {
                    const tracked = isAlreadyTracked(bp);
                    return (
                      <div key={`${bp.ticker}-${bp.type}-${bp.strike || "x"}-${bp.expiration || "x"}-${i}`}
                           className={`bi-row ${tracked ? "bi-row-tracked" : ""}`}
                           title={tracked
                             ? "This position is already in the local tracker. Skipped on import all."
                             : "Click Add to import this position into the local tracker."}>
                        <span className="bi-sym">{bp.ticker}</span>
                        <span>{bp.type}</span>
                        <span>{bp.strike != null ? "$" + bp.strike.toFixed(2) : "—"}</span>
                        <span>{bp.expiration || "—"}</span>
                        <span className={bp.qty < 0 ? "down" : "up"}>{bp.qty}</span>
                        <span>${(bp.entryPrice || 0).toFixed(2)}</span>
                        <span>
                          {tracked ? (
                            <span className="bi-status-tracked">tracked</span>
                          ) : (
                            <button className="bi-add-btn"
                                    onClick={() => importPosition(bp)}
                                    title="Add this position to the local tracker.">
                              Add
                            </button>
                          )}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
              <div className="bi-help">
                Phase 1 is read-only manual import. Imported positions show <code>source: schwab</code> in their notes. Phase 2 will add auto-reconciliation on fills and rolls.
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function StrategyReferenceCard() {
  const strategies = window.OptionStrats?.STRATEGIES || [];
  const docs = window.OptionStrats?.STRATEGY_DOCS || {};
  const [query, setQuery] = useState("");
  const [openKey, setOpenKey] = useState(null);
  const [filter, setFilter] = useState("all");  // all | income | speculation | volatility | synthetic | system

  // Only show strategies that have docs (sanity check)
  const items = strategies.filter(s => docs[s.key]);
  const familyOf = (key) => {
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

  const families = [
    ["all", "All"],
    ["income", "Income"],
    ["speculation", "Direction"],
    ["volatility", "Volatility"],
    ["synthetic", "Synthetic"],
    ["system", "Systems"],
  ];

  return (
    <div className="sref-modal-body">
      <div className="sref-toolbar">
        <input className="sref-search" type="text"
               placeholder="Search by name, view, family."
               value={query}
               onChange={(e) => setQuery(e.target.value)} />
        <div className="sref-filter">
          {families.map(([k, l]) => (
            <button key={k} className={filter === k ? "active" : ""}
                    onClick={() => setFilter(k)}>{l}</button>
          ))}
        </div>
      </div>
      {filtered.length === 0 && (
        <div className="sref-empty">No strategies match.</div>
      )}
      <div className="sref-grid">
        {filtered.map(s => {
          const d = docs[s.key];
          const isOpen = openKey === s.key;
          return (
            <div key={s.key} className={`sref-tile ${isOpen ? "open" : ""}`}
                 onClick={() => setOpenKey(prev => prev === s.key ? null : s.key)}>
              <div className="sref-tile-head">
                <div className="sref-tile-name">{s.name}</div>
                <div className="sref-tile-fam">{d.family}</div>
              </div>
              <div className="sref-tile-summary">{d.summary}</div>
              {isOpen && (
                <div className="sref-tile-detail" onClick={(e) => e.stopPropagation()}>
                  <div className="sref-row">
                    <span className="sref-lbl">Market view</span>
                    <span className="sref-val">{d.market_view}</span>
                  </div>
                  <div className="sref-row">
                    <span className="sref-lbl">When to use</span>
                    <span className="sref-val">{d.when_to_use}</span>
                  </div>
                  <div className="sref-row">
                    <span className="sref-lbl">Max profit</span>
                    <span className="sref-val">{d.max_profit}</span>
                  </div>
                  <div className="sref-row">
                    <span className="sref-lbl">Max loss</span>
                    <span className="sref-val">{d.max_loss}</span>
                  </div>
                  <div className="sref-row">
                    <span className="sref-lbl">Break-even</span>
                    <span className="sref-val">{d.breakeven}</span>
                  </div>
                  <div className="sref-row">
                    <span className="sref-lbl">Ideal IV</span>
                    <span className="sref-val">{d.ideal_iv}</span>
                  </div>
                  <div className="sref-row">
                    <span className="sref-lbl">Time decay</span>
                    <span className="sref-val">{d.time_decay}</span>
                  </div>
                  <div className="sref-row">
                    <span className="sref-lbl">Assignment</span>
                    <span className="sref-val">{d.assignment}</span>
                  </div>
                  <div className="sref-row sref-row-risk">
                    <span className="sref-lbl">Risks</span>
                    <span className="sref-val">{d.risks}</span>
                  </div>
                </div>
              )}
              {!isOpen && (
                <div className="sref-tile-foot">Tap to read full breakdown</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function WatchlistManager({ data, onAdd, onRemove, onToggleStar, onUpdate, onBulkAdd, onSwitchTicker }) {
  const [search, setSearch] = useState("");
  const [tagFilter, setTagFilter] = useState(null);
  const [bulkText, setBulkText] = useState("");
  const [bulkOpen, setBulkOpen] = useState(false);
  const [editing, setEditing] = useState(null); // symbol being edited
  const [sortBy, setSortBy] = useState("starred"); // starred | symbol | added
  // Derived: all unique tags with counts
  const allTags = useMemo(() => {
    const counts = {};
    for (const s of data.symbols) {
      for (const t of (s.tags || [])) counts[t] = (counts[t] || 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [data.symbols]);
  // Filtered + sorted view
  const visible = useMemo(() => {
    const q = search.trim().toUpperCase();
    let rows = data.symbols.filter(s => {
      if (q && !s.symbol.includes(q)
            && !(s.notes || "").toUpperCase().includes(q)
            && !(s.tags || []).some(t => t.toUpperCase().includes(q))) {
        return false;
      }
      if (tagFilter && !(s.tags || []).includes(tagFilter)) return false;
      return true;
    });
    if (sortBy === "starred") {
      rows.sort((a, b) => (b.starred ? 1 : 0) - (a.starred ? 1 : 0)
        || a.symbol.localeCompare(b.symbol));
    } else if (sortBy === "symbol") {
      rows.sort((a, b) => a.symbol.localeCompare(b.symbol));
    } else if (sortBy === "added") {
      rows.sort((a, b) => (b.added_at || 0) - (a.added_at || 0));
    }
    return rows;
  }, [data.symbols, search, tagFilter, sortBy]);
  return (
    <div className="wlm-body">
      {/* Toolbar */}
      <div className="wlm-toolbar">
        <input className="wlm-search" type="text"
               placeholder="Search symbol, tag, or note."
               value={search}
               onChange={e => setSearch(e.target.value)} />
        <select className="wlm-sort" value={sortBy}
                onChange={e => setSortBy(e.target.value)}
                title="Sort">
          <option value="starred">★ Starred first</option>
          <option value="symbol">A-Z</option>
          <option value="added">Recently added</option>
        </select>
        <button className={`wlm-bulk-toggle${bulkOpen ? " active" : ""}`}
                onClick={() => setBulkOpen(o => !o)}>
          {bulkOpen ? "Close bulk add" : "+ Bulk add"}
        </button>
      </div>
      {/* Tag filter chips */}
      {allTags.length > 0 && (
        <div className="wlm-tags-row">
          <button className={`wlm-tag-chip${!tagFilter ? " active" : ""}`}
                  onClick={() => setTagFilter(null)}>
            All ({data.symbols.length})
          </button>
          {allTags.map(([t, n]) => (
            <button key={t}
                    className={`wlm-tag-chip${tagFilter === t ? " active" : ""}`}
                    onClick={() => setTagFilter(tagFilter === t ? null : t)}>
              {t} ({n})
            </button>
          ))}
        </div>
      )}
      {/* Bulk add panel */}
      {bulkOpen && (
        <div className="wlm-bulk-panel">
          <textarea className="wlm-bulk-input"
                    rows={4}
                    placeholder="Paste tickers separated by commas, spaces, or new lines.&#10;Example: AAPL, NVDA, MSFT, AMD&#10;Or one per line:&#10;TSLA&#10;META"
                    value={bulkText}
                    onChange={e => setBulkText(e.target.value)} />
          <div className="wlm-bulk-actions">
            <button className="wlm-bulk-add"
                    onClick={() => {
                      const n = onBulkAdd(bulkText);
                      if (n > 0) {
                        setBulkText("");
                        setBulkOpen(false);
                      }
                    }}>
              Add to watchlist
            </button>
          </div>
        </div>
      )}
      {/* Quick add single */}
      <QuickAddRow onAdd={(s) => onAdd(s)} />
      {/* Symbol list */}
      <div className="wlm-list">
        {visible.length === 0 && (
          <div className="wlm-empty">
            {data.symbols.length === 0
              ? "Watchlist is empty. Add symbols above."
              : "No matches for current filters."}
          </div>
        )}
        {visible.map(s => (
          <WatchlistRow
            key={s.symbol}
            entry={s}
            isEditing={editing === s.symbol}
            onSwitchTicker={onSwitchTicker}
            onToggleStar={() => onToggleStar(s.symbol)}
            onRemove={() => onRemove(s.symbol)}
            onEdit={() => setEditing(s.symbol)}
            onCloseEdit={() => setEditing(null)}
            onUpdate={(patch) => onUpdate(s.symbol, patch)}
          />
        ))}
      </div>
    </div>
  );
}

function QuickAddRow({ onAdd }) {
  const [val, setVal] = useState("");
  const submit = () => {
    if (!val.trim()) return;
    onAdd(val);
    setVal("");
  };
  return (
    <div className="wlm-quick-add">
      <input type="text" className="wlm-quick-input"
             placeholder="Add a symbol."
             value={val}
             onChange={e => setVal(e.target.value)}
             onKeyDown={e => { if (e.key === "Enter") submit(); }} />
      <button className="wlm-quick-btn" onClick={submit}>Add</button>
    </div>
  );
}

function WatchlistRow({ entry, isEditing, onSwitchTicker, onToggleStar, onRemove, onEdit, onCloseEdit, onUpdate }) {
  const [tagsInput, setTagsInput] = useState((entry.tags || []).join(", "));
  const [notesInput, setNotesInput] = useState(entry.notes || "");
  const [strategyInput, setStrategyInput] = useState(entry.preferred_strategy || "");
  // Reset local state when entering edit mode
  useEffect(() => {
    if (isEditing) {
      setTagsInput((entry.tags || []).join(", "));
      setNotesInput(entry.notes || "");
      setStrategyInput(entry.preferred_strategy || "");
    }
  }, [isEditing, entry.symbol]);
  const saveEdits = () => {
    const tags = tagsInput.split(/[,;\n]/)
      .map(t => t.trim().toLowerCase())
      .filter(t => t && t.length <= 32);
    onUpdate({
      tags: Array.from(new Set(tags)),
      notes: notesInput.slice(0, 500),
      preferred_strategy: strategyInput.trim() || null,
    });
    onCloseEdit();
  };
  const STRATEGY_OPTIONS = [
    { value: "", label: "(none)" },
    { value: "covered_call", label: "Covered Call" },
    { value: "cash_secured_put", label: "Cash-Secured Put" },
    { value: "short_strangle", label: "Short Strangle" },
    { value: "iron_condor", label: "Iron Condor" },
    { value: "bull_put_spread", label: "Bull Put Spread" },
    { value: "jade_lizard", label: "Jade Lizard" },
    { value: "wheel", label: "Wheel" },
  ];
  return (
    <div className={`wlm-row${entry.starred ? " starred" : ""}${isEditing ? " editing" : ""}`}>
      <div className="wlm-row-main">
        <button className={`wlm-star-btn${entry.starred ? " on" : ""}`}
                onClick={onToggleStar}
                title={entry.starred ? "Unstar" : "Star (pin to sidebar)"}>
          {entry.starred ? "★" : "☆"}
        </button>
        <button className="wlm-sym-btn" onClick={() => onSwitchTicker(entry.symbol)}
                title="Switch dashboard to this ticker">
          {entry.symbol}
        </button>
        <div className="wlm-row-meta">
          {(entry.tags || []).map(t => (
            <span key={t} className="wlm-tag-pill">{t}</span>
          ))}
          {entry.preferred_strategy && (
            <span className="wlm-strategy-pill">{entry.preferred_strategy.replace(/_/g, " ")}</span>
          )}
          {entry.notes && (
            <span className="wlm-note-snip" title={entry.notes}>
              {entry.notes.length > 40 ? entry.notes.slice(0, 40) + "." : entry.notes}
            </span>
          )}
        </div>
        <div className="wlm-row-actions">
          <button className="wlm-edit-btn" onClick={isEditing ? onCloseEdit : onEdit}>
            {isEditing ? "Cancel" : "Edit"}
          </button>
          <button className="wlm-del-btn" onClick={onRemove} title="Remove from watchlist">×</button>
        </div>
      </div>
      {isEditing && (
        <div className="wlm-edit-panel">
          <div className="wlm-edit-row">
            <label>Tags</label>
            <input type="text" value={tagsInput}
                   placeholder="comma-separated. e.g. semis, mega-cap, earnings-soon"
                   onChange={e => setTagsInput(e.target.value)} />
          </div>
          <div className="wlm-edit-row">
            <label>Strategy</label>
            <select value={strategyInput}
                    onChange={e => setStrategyInput(e.target.value)}>
              {STRATEGY_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <div className="wlm-edit-row">
            <label>Notes</label>
            <textarea value={notesInput}
                      rows={2}
                      maxLength={500}
                      placeholder="Personal notes, conviction context, recent observations."
                      onChange={e => setNotesInput(e.target.value)} />
          </div>
          <div className="wlm-edit-actions">
            <button className="wlm-save-btn" onClick={saveEdits}>Save</button>
          </div>
        </div>
      )}
    </div>
  );
}

function FlashOnChange({ value, className = "", children }) {
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
  return (
    <span className={`${className}${flash ? ` price-flash-${flash}` : ""}`}>
      {children}
    </span>
  );
}

function SortableTh({ label, sortKey, current, onSort, className = "" }) {
  const isActive = current && current.key === sortKey;
  const arrow = !isActive ? "" : current.dir === "desc" ? " ▾" : " ▴";
  return (
    <th className={`${className} sortable-th${isActive ? " active" : ""}`}
        onClick={() => onSort(sortKey)}>
      <span>{label}{arrow}</span>
    </th>
  );
}

function PercentCalc({ activeTicker, livePrice, accentColor }) {
  const STORAGE_KEY = "weeklyOptionsTimer.calc.v1";
  const persisted = (() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  })();
  const [open, setOpen] = useState(persisted?.open ?? false);
  const [fromOverride, setFromOverride] = useState(""); // empty = auto from livePrice
  // Mode: "p2p" = price-to-percent (enter target price → see % move).
  //       "pct2p" = percent-to-price (enter % → see target price).
  // Both modes share the FROM input. Persisted so the user's preferred
  // mode survives reloads.
  const [mode, setMode] = useState(persisted?.mode || "p2p");
  const [rows, setRows] = useState(persisted?.rows ?? [{ id: 1, value: "" }]);
  const [pctRows, setPctRows] = useState(persisted?.pctRows ?? [{ id: 1, value: "" }]);
  const nextIdRef = useRef(persisted?.rows?.length ? Math.max(...persisted.rows.map(r => r.id)) + 1 : 2);
  const nextPctIdRef = useRef(persisted?.pctRows?.length ? Math.max(...persisted.pctRows.map(r => r.id)) + 1 : 2);

  // Persist
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ open, mode, rows, pctRows }));
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
    setRows(prev => [...prev, { id: nextIdRef.current++, value: "" }]);
  };
  const updateRow = (id, value) => {
    setRows(prev => prev.map(r => r.id === id ? { ...r, value } : r));
  };
  const removeRow = (id) => {
    setRows(prev => prev.length > 1 ? prev.filter(r => r.id !== id) : prev);
  };
  const addPctRow = () => {
    setPctRows(prev => [...prev, { id: nextPctIdRef.current++, value: "" }]);
  };
  const updatePctRow = (id, value) => {
    setPctRows(prev => prev.map(r => r.id === id ? { ...r, value } : r));
  };
  const removePctRow = (id) => {
    setPctRows(prev => prev.length > 1 ? prev.filter(r => r.id !== id) : prev);
  };

  // p2p: target price → % move + $ diff
  const calc = (toStr) => {
    const to = parseFloat(toStr);
    if (!isFinite(to) || fromNum == null || fromNum <= 0) return null;
    const diff = to - fromNum;
    const pct = (diff / fromNum) * 100;
    return { diff, pct };
  };

  // pct2p: % move → target price + $ diff
  const calcPct = (pctStr) => {
    const pct = parseFloat(pctStr);
    if (!isFinite(pct) || fromNum == null || fromNum <= 0) return null;
    const diff = fromNum * (pct / 100);
    const to = fromNum + diff;
    return { diff, to };
  };

  return (
    <>
      <button
        className={`pcalc-tab${open ? " pcalc-tab-open" : ""}`}
        onClick={() => setOpen(o => !o)}
        title={open ? "Hide % calculator" : "Show % calculator"}>
        {open ? "✕" : "%"}
      </button>
      <aside className={`pcalc-panel${open ? " pcalc-panel-open" : ""}`}
             aria-hidden={!open}>
        <div className="pcalc-head">
          <div className="pcalc-title">Percent calculator</div>
          <div className="pcalc-sub">{mode === "p2p" ? "Price → percent" : "Percent → price"}</div>
          <div className="pcalc-mode-toggle" title="Switch direction">
            <button className={mode === "p2p" ? "active" : ""}
                    onClick={() => setMode("p2p")}
                    title="Enter a target price, see the percent move from FROM">$ → %</button>
            <button className={mode === "pct2p" ? "active" : ""}
                    onClick={() => setMode("pct2p")}
                    title="Enter a percent, see the target price">% → $</button>
          </div>
        </div>
        <div className="pcalc-body">
          <div className="pcalc-from-row">
            <div className="pcalc-label">FROM</div>
            <div className="pcalc-from-input-wrap">
              <span className="pcalc-currency">$</span>
              <input
                type="text"
                inputMode="decimal"
                className="pcalc-from-input"
                value={fromOverride}
                placeholder={livePrice != null ? livePrice.toFixed(2) : "—"}
                onChange={e => setFromOverride(e.target.value)} />
              {fromOverride !== "" && (
                <button className="pcalc-clear-btn"
                        onClick={() => setFromOverride("")}
                        title="Reset to live price">↺</button>
              )}
            </div>
            <div className="pcalc-from-meta">
              {fromOverride === "" && livePrice != null
                ? `live · ${activeTicker || "—"}`
                : fromOverride !== ""
                  ? "manual"
                  : "no live price"}
            </div>
          </div>
          <div className="pcalc-divider" />
          {mode === "p2p" ? (
            <div className="pcalc-to-section">
              <div className="pcalc-label">TO ($)</div>
              {rows.map((row) => {
                const result = calc(row.value);
                return (
                  <div key={row.id} className="pcalc-to-row">
                    <div className="pcalc-to-input-wrap">
                      <span className="pcalc-currency">$</span>
                      <input
                        type="text"
                        inputMode="decimal"
                        className="pcalc-to-input"
                        value={row.value}
                        placeholder="0.00"
                        onChange={e => updateRow(row.id, e.target.value)} />
                    </div>
                    <div className="pcalc-result">
                      {result ? (
                        <>
                          <div className={`pcalc-pct ${result.pct >= 0 ? "up" : "down"}`}>
                            {result.pct >= 0 ? "+" : ""}{result.pct.toFixed(2)}%
                          </div>
                          <div className={`pcalc-dollar ${result.diff >= 0 ? "up" : "down"}`}>
                            {result.diff >= 0 ? "+" : ""}${result.diff.toFixed(2)}
                          </div>
                        </>
                      ) : (
                        <div className="pcalc-empty-result">—</div>
                      )}
                    </div>
                    {rows.length > 1 && (
                      <button className="pcalc-remove-btn"
                              onClick={() => removeRow(row.id)}
                              title="Remove row">×</button>
                    )}
                  </div>
                );
              })}
              <button className="pcalc-add-btn" onClick={addRow}>+ Add row</button>
            </div>
          ) : (
            <div className="pcalc-to-section">
              <div className="pcalc-label">TO (%)</div>
              {pctRows.map((row) => {
                const result = calcPct(row.value);
                return (
                  <div key={row.id} className="pcalc-to-row">
                    <div className="pcalc-to-input-wrap">
                      <input
                        type="text"
                        inputMode="decimal"
                        className="pcalc-to-input"
                        value={row.value}
                        placeholder="0.00"
                        onChange={e => updatePctRow(row.id, e.target.value)} />
                      <span className="pcalc-currency pcalc-pct-suffix">%</span>
                    </div>
                    <div className="pcalc-result">
                      {result ? (
                        <>
                          <div className={`pcalc-pct ${result.to >= fromNum ? "up" : "down"}`}>
                            ${result.to.toFixed(2)}
                          </div>
                          <div className={`pcalc-dollar ${result.diff >= 0 ? "up" : "down"}`}>
                            {result.diff >= 0 ? "+" : ""}${result.diff.toFixed(2)}
                          </div>
                        </>
                      ) : (
                        <div className="pcalc-empty-result">—</div>
                      )}
                    </div>
                    {pctRows.length > 1 && (
                      <button className="pcalc-remove-btn"
                              onClick={() => removePctRow(row.id)}
                              title="Remove row">×</button>
                    )}
                  </div>
                );
              })}
              <button className="pcalc-add-btn" onClick={addPctRow}>+ Add row</button>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}

function RollManagerCard({ ticker, positions, currentPrice, livePrice, apiFetch, uwHealth }) {
  const [quotes, setQuotes] = useState({}); // key: "exp|strike" -> {mid, delta, ...}
  const [loading, setLoading] = useState(false);
  // UW flow context — used to color roll suggestions with current flow read.
  const [flowScore, setFlowScore] = useState(null);
  // Clear stale flow score the moment ticker changes — the fetch
  // below will repopulate. Without this, the previous ticker's
  // flow read briefly bleeds into the new ticker's view.
  useEffect(() => { setFlowScore(null); }, [ticker]);
  useEffect(() => {
    if (!ticker || !uwHealth?.connected) {
      setFlowScore(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const r = await apiFetch(`/api/uw/flow_score?symbol=${encodeURIComponent(ticker)}&price=${currentPrice || 0}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (!cancelled) setFlowScore(j);
      } catch {}
    })();
    return () => { cancelled = true; };
  }, [ticker, currentPrice, uwHealth?.connected]);

  // Active short calls on the displayed ticker
  const shortCalls = (positions || [])
    .filter(p => p.status === "open" && p.ticker === ticker)
    .flatMap(p => (p.legs || [])
      .filter(l => l.type === "call" && l.qty < 0)
      .map(l => ({
        ...l,
        positionId: p.id,
        entryDate: p.entryDate || p.openedAt || null,
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
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, JSON.stringify(shortCalls.map(s => `${s.expiration}|${s.strike}`))]);

  if (!shortCalls.length) return null;
  const live = livePrice ?? currentPrice;

  return (
    <div className="card roll-manager">
      <div className="card-head">
        <div>
          <div className="kicker">Active short calls · roll choices</div>
          <div className="card-title">Roll Manager{loading && <span className="muted" style={{fontSize: 12, marginLeft: 8}}>fetching quotes…</span>}</div>
        </div>
      </div>
      {/* UW flow context — surfaces the read above the roll list so
          rolling decisions are made WITH flow awareness, not blind. */}
      {uwHealth?.connected && flowScore?.data_available && (() => {
        // Decide what flow says about rolling. The most dangerous case
        // is bullish flow targeting strikes ABOVE the short — that
        // means rolling same strike (or only slightly higher) is
        // walking into the targeted zone.
        const cls = flowScore.cc_risk >= 70 ? "verdict-avoid"
          : flowScore.cc_risk >= 50 ? "verdict-partial"
          : flowScore.bearish >= 60 ? "verdict-partial"
          : "verdict-sell";
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
        return (
          <div className={`roll-flow-context flow-verdict ${cls}`}>
            <div className="flow-verdict-label" title="Unusual Whales flow read for the active ticker. Drives roll-decision context.">
              UW FLOW · {flowScore.verdict}
            </div>
            <div className="flow-verdict-reason">{line}</div>
          </div>
        );
      })()}
      <div className="roll-list">
        {shortCalls.map((sc, i) => {
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
            } catch { return null; }
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
              return d.toLocaleDateString("en-US", {month: "short", day: "numeric"});
            } catch { return nextWeek; }
          })();
          const buildChoice = (label, strike) => {
            const rk = `${nextWeek}|${strike}`;
            const r = quotes[rk];
            if (!r || currentMid == null) {
              return { label, strike, exp: nextWeekLabel, netCredit: null, available: false };
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
              available: true,
            };
          };
          const choices = [
            buildChoice("Same strike", sc.strike),
            buildChoice("+$5 strike", sc.strike + 5),
            buildChoice("+$10 strike", sc.strike + 10),
          ];
          const buyback = currentMid != null ? -currentMid * Math.abs(sc.qty) * 100 : null;

          return (
            <div className="roll-item" key={`${sc.positionId}-${i}`}>
              <div className="roll-head">
                <div className="roll-strike">
                  <span className="roll-qty">{Math.abs(sc.qty)}x</span>
                  <span className={itm ? "roll-strike-itm" : ""}>${sc.strike.toFixed(2)} call</span>
                  <span className="muted"> · {sc.expiration}</span>
                  {dte != null && <span className="roll-dte">{dte}d</span>}
                  {itm && <span className="roll-itm-badge">ITM</span>}
                </div>
                <div className="roll-pl">
                  {currentPL != null && (
                    <span className={currentPL >= 0 ? "up" : "down"}>
                      {currentPL >= 0 ? "+" : ""}${currentPL.toFixed(0)}
                    </span>
                  )}
                </div>
              </div>
              <div className="roll-stats">
                <span><span className="muted">Entry</span> <b>${entryCredit.toFixed(2)}</b></span>
                <span><span className="muted">Now</span> <b>{currentMid != null ? "$" + currentMid.toFixed(2) : "—"}</b></span>
                <span><span className="muted">Intrinsic</span> <b>${intrinsic.toFixed(2)}</b></span>
                <span><span className="muted">Extrinsic</span> <b>{extrinsic != null ? "$" + extrinsic.toFixed(2) : "—"}</b></span>
                {q?.delta != null && <span><span className="muted">Δ</span> <b>{q.delta.toFixed(2)}</b></span>}
              </div>
              <div className="roll-choices">
                {choices.map((c, j) => (
                  <div key={j} className={`roll-choice${c.available ? "" : " unavailable"}`}>
                    <div className="roll-choice-label">Roll {c.label} → {c.exp}</div>
                    <div className="roll-choice-strike">${c.strike.toFixed(2)}</div>
                    {c.available ? (
                      <>
                        <div className={`roll-choice-credit ${c.netCredit >= 0 ? "up" : "down"}`}>
                          {c.netCredit >= 0 ? "+" : ""}${c.netCredit.toFixed(0)}
                        </div>
                        <div className="muted" style={{fontSize: 10.5}}>
                          {c.netCredit >= 0 ? "credit" : "debit"} · Δ {c.newDelta != null ? c.newDelta.toFixed(2) : "—"}
                        </div>
                      </>
                    ) : (
                      <div className="muted" style={{fontSize: 11}}>quote unavailable</div>
                    )}
                  </div>
                ))}
                <div className="roll-choice">
                  <div className="roll-choice-label">Buy back · close</div>
                  <div className="roll-choice-strike">—</div>
                  {buyback != null ? (
                    <>
                      <div className={`roll-choice-credit ${buyback >= 0 ? "up" : "down"}`}>
                        {buyback >= 0 ? "+" : ""}${buyback.toFixed(0)}
                      </div>
                      <div className="muted" style={{fontSize: 10.5}}>realize P/L</div>
                    </>
                  ) : (
                    <div className="muted" style={{fontSize: 11}}>—</div>
                  )}
                </div>
              </div>

              {/* ─── Roll P/L modeling (v1.16) ─────────────────────
                  Four side-by-side scenarios so the user can decide
                  what to do with this short call. Each scenario
                  shows the dollar P/L outcome at expiration plus
                  any reasoning notes. */}
              {(() => {
                const fourWeek = (() => {
                  const d = new Date(sc.expiration + "T12:00:00");
                  d.setDate(d.getDate() + 28);
                  return d.toISOString().slice(0, 10);
                })();
                const fourWeekLabel = (() => {
                  try {
                    const d = new Date(fourWeek + "T12:00:00");
                    return d.toLocaleDateString("en-US", {month: "short", day: "numeric"});
                  } catch { return fourWeek; }
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
                  reasoning: itm
                    ? "ITM. Same-strike roll typically only works when next-week's premium exceeds the current intrinsic. Watch the credit closely."
                    : "OTM. Standard 1-week roll. Adds another week of theta to the position.",
                } : { label: "Roll +1 week", detail: "Quote unavailable", available: false };
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
                    reasoning: "Longer DTE means more theta but also more time for price to keep moving against you. Consider only if you have conviction the stock pulls back.",
                  };
                })() : { label: "Roll +4 weeks", detail: "Quote unavailable", available: false };
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
                  detail: itm
                    ? `Stock called away at $${sc.strike.toFixed(2)} on ${sc.expiration}. Option P/L: +$${optionAssignmentPL.toFixed(0)} (full credit kept). Lost upside on shares: $${lostUpside.toFixed(0)} vs current price.`
                    : `Currently OTM. If price stays below $${sc.strike.toFixed(2)} at expiration, the option expires worthless and you keep the full $${optionAssignmentPL.toFixed(0)} credit.`,
                  pnl: optionAssignmentPL,
                  positive: true,
                  available: true,
                  reasoning: itm
                    ? "Acceptable if you wanted to exit the stock at this price anyway. Otherwise the lost upside cost may make rolling more attractive."
                    : "Often the best outcome for OTM short calls. No additional action required.",
                };
                // Scenario 4: close at current debit (buyback realized P/L)
                const sc4 = currentMid != null ? {
                  label: "Close now",
                  detail: `Buy back at $${currentMid.toFixed(2)} mid. Realized P/L $${buyback >= 0 ? "+" : ""}${buyback.toFixed(0)}.`,
                  pnl: buyback,
                  positive: buyback >= 0,
                  available: true,
                  reasoning: buyback >= 0
                    ? "Locks in profit. Frees the short for a fresh setup at a different strike or expiration."
                    : "Locks in a loss. Only worth it when the trade thesis has clearly broken and rolling would compound the risk.",
                } : { label: "Close now", detail: "Quote unavailable", available: false };
                const scenarios = [sc1, sc2, sc3, sc4];
                return (
                  <div className="roll-pl-section"
                       title="Side-by-side P/L modeling for four scenarios. Helps choose between roll, assignment, and close. P/L figures are per the underlying short call only — your stock leg P/L is separate.">
                    <div className="roll-pl-head">
                      <span className="roll-pl-kicker">Decision support · per contract option P/L</span>
                    </div>
                    <div className="roll-pl-grid">
                      {scenarios.map((s, idx) => (
                        <div key={idx}
                             className={`roll-pl-card ${!s.available ? "unavailable" : ""}`}
                             title={s.reasoning || ""}>
                          <div className="roll-pl-label">{s.label}</div>
                          {s.available ? (
                            <>
                              <div className={`roll-pl-pnl ${s.positive ? "up" : "down"}`}>
                                {s.pnl >= 0 ? "+" : ""}${s.pnl.toFixed(0)}
                              </div>
                              <div className="roll-pl-detail">{s.detail}</div>
                            </>
                          ) : (
                            <div className="muted" style={{fontSize: 11}}>{s.detail}</div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function FlowScoreCard({ ticker, currentPrice, apiFetch, uwHealth }) {
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
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hour12: false,
      }).format(new Date()).replace(",", ""));
      const day = nowET.getDay();  // 0=Sun
      if (day === 0 || day === 6) return false;
      const h = nowET.getHours(), m = nowET.getMinutes();
      const minutes = h * 60 + m;
      return minutes >= (9 * 60 + 30) && minutes < (16 * 60);
    } catch { return false; }
  };

  useEffect(() => {
    if (!ticker || !uwHealth?.connected) return;
    let cancelled = false;
    const poll = async () => {
      try {
        setLoading(true);
        const url = `/api/uw/flow_score?symbol=${encodeURIComponent(ticker)}`
                  + (currentPrice ? `&price=${currentPrice}` : "");
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
    return () => { cancelled = true; clearInterval(id); };
  }, [ticker, currentPrice, uwHealth?.connected]);

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
    return (
      <div className="card flow-score-card">
        <div className="card-head">
          <div>
            <div className="kicker">Unusual Whales · real-time options flow</div>
            <div className="card-title">Flow Score</div>
          </div>
        </div>
        <div className="muted" style={{padding: "16px 0"}}>
          UW connection error: {uwHealth?.error || "unknown"}
        </div>
      </div>
    );
  }
  if (!score && loading) {
    return (
      <div className="card flow-score-card">
        <div className="card-head">
          <div>
            <div className="kicker">Unusual Whales · real-time options flow</div>
            <div className="card-title">Flow Score</div>
          </div>
        </div>
        <div className="muted" style={{padding: "16px 0"}}>Loading flow data.</div>
      </div>
    );
  }
  if (!score) return null;
  if (!score.data_available) {
    return (
      <div className="card flow-score-card">
        <div className="card-head">
          <div>
            <div className="kicker">Unusual Whales · real-time options flow</div>
            <div className="card-title">Flow Score · {ticker}</div>
          </div>
        </div>
        <div className="muted" style={{padding: "16px 0"}}>
          {score.reason || "No unusual flow detected for this ticker today."}
        </div>
      </div>
    );
  }

  const overallCls = score.overall >= 65 ? "up" : score.overall <= 35 ? "down" : "";

  // Sub-score bar component
  const SubBar = ({label, value, tone, tip}) => {
    const cls = tone === "good" ? "sub-good"
      : tone === "bad" ? "sub-bad"
      : tone === "neutral" ? "sub-neutral"
      : "sub-default";
    return (
      <div className="flow-sub" title={tip}>
        <div className="flow-sub-head">
          <span className="flow-sub-lbl">{label}</span>
          <span className="flow-sub-val">{value}</span>
        </div>
        <div className="flow-sub-bar">
          <div className={`flow-sub-fill ${cls}`} style={{width: value + "%"}}></div>
        </div>
      </div>
    );
  };

  return (
    <div className="card flow-score-card">
      <div className="card-head">
        <div>
          <div className="kicker">Unusual Whales · real-time options flow</div>
          <div className="card-title">Flow Score · {ticker}</div>
        </div>
        <div className="kicker" title={`${score.stats.alert_count} unusual flow alerts in today's session`}>
          {score.stats.alert_count} alerts today
        </div>
      </div>

      {/* Verdict block — overrides the standard CC verdict when flow signal is strong */}
      <div className={`flow-verdict ${score.verdict_class}`}>
        <div className="flow-verdict-label" title="UW decision-engine verdict for selling covered calls right now. Overrides standard verdict when bullish flow ≥ 70 AND CC Risk ≥ 70.">
          UW VERDICT
        </div>
        <div className="flow-verdict-text">{score.verdict}</div>
        <div className="flow-verdict-reason">{score.reason}</div>
      </div>

      {/* Overall score gauge — large circle */}
      <div className="flow-overall">
        <div className="flow-overall-circle" title="Overall flow score from 0 to 100. 50 = neutral. Above 50 = bullish flow lean. Below 50 = bearish flow lean. Quality-weighted: noisy flow tilts back toward 50.">
          <div className={`flow-overall-num ${overallCls}`}>{score.overall}</div>
          <div className="flow-overall-cap">OVERALL</div>
        </div>
        <div className="flow-overall-stats">
          <div className="flow-stat-row" title="Total call premium traded today across all unusual flow alerts">
            <span className="flow-stat-lbl">Call premium</span>
            <span className="flow-stat-val up">{fmt$M(score.stats.total_call_premium)}</span>
          </div>
          <div className="flow-stat-row" title="Total put premium traded today across all unusual flow alerts">
            <span className="flow-stat-lbl">Put premium</span>
            <span className="flow-stat-val down">{fmt$M(score.stats.total_put_premium)}</span>
          </div>
          <div className="flow-stat-row" title="Ask-side call premium specifically targeting strikes at or above current price — the dangerous zone for covered-call writers">
            <span className="flow-stat-lbl">Above strike (calls)</span>
            <span className="flow-stat-val">{fmt$M(score.stats.call_above_strike_premium)}</span>
          </div>
          <div className="flow-stat-row" title="Number of sweep orders detected. Sweeps are aggressive, multi-exchange ask-side fills — typically institutional.">
            <span className="flow-stat-lbl">Sweeps (call/put)</span>
            <span className="flow-stat-val">{score.stats.call_sweeps}/{score.stats.put_sweeps}</span>
          </div>
        </div>
      </div>

      {/* Four sub-scores */}
      <div className="flow-subs">
        <SubBar label="Bullish flow" value={score.bullish}
                tone={score.bullish >= 70 ? "good" : score.bullish >= 50 ? "neutral" : "default"}
                tip="0-100. Driven by ask-side call premium share, call sweep concentration, and total bullish premium magnitude. Higher = more aggressive bullish flow." />
        <SubBar label="Bearish flow" value={score.bearish}
                tone={score.bearish >= 70 ? "bad" : score.bearish >= 50 ? "neutral" : "default"}
                tip="0-100. Mirror of bullish. Driven by ask-side put premium share, put sweeps, and total bearish premium. Higher = more aggressive downside positioning." />
        <SubBar label="Flow quality" value={score.quality}
                tone={score.quality >= 70 ? "good" : score.quality >= 40 ? "neutral" : "default"}
                tip="0-100. Conviction of the flow. Total premium magnitude, sweep prevalence, and number of distinct alerts. Low quality = noise that should be ignored." />
        <SubBar label="CC risk"
                value={score.cc_risk}
                tone={score.cc_risk >= 70 ? "bad" : score.cc_risk >= 50 ? "neutral" : "default"}
                tip="0-100. Risk that selling covered calls right now leads to fast assignment. Driven by ask-side call premium concentrated AT or ABOVE current price. ≥70 means aggressive bullish flow is targeting your potential strike zone." />
      </div>

      {/* Trade-by-trade flow feed — collapsible to keep card compact. */}
      <div className="flow-trades-section">
        <button className="flow-trades-toggle"
                onClick={async () => {
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
                }}
                title="Show or hide the trade-by-trade flow list. Each row is one unusual options trade detected by Unusual Whales today.">
          {showFlow ? "▾" : "▸"} {showFlow ? "Hide" : "Show"} flow trades ({score.stats.alert_count})
        </button>
        {showFlow && (
          <div className="flow-trades-list">
            {flowTradesLoading && (!flowTrades || flowTrades.length === 0) && (
              <div className="muted" style={{padding: "10px 0"}}>Loading trades.</div>
            )}
            {flowTrades && flowTrades.length === 0 && !flowTradesLoading && (
              <div className="muted" style={{padding: "10px 0"}}>No trades returned.</div>
            )}
            {flowTrades && flowTrades.length > 0 && (
              <>
                <div className="flow-trades-head" title="Each row is one unusual options trade today. Sort is most-recent-first (UW default).">
                  <span title="Time of execution">Time</span>
                  <span title="Call or put">Side</span>
                  <span title="Strike price">Strike</span>
                  <span title="Expiration date">Exp</span>
                  <span title="Trade size in contracts">Size</span>
                  <span title="Total premium paid (size × price × 100)">Premium</span>
                  <span title="IV at the contract">IV</span>
                  <span title="Where the trade printed: ask = aggressive buyer, bid = aggressive seller, mid = uncertain">Side fill</span>
                  <span title="Sentiment: bullish = ask-side calls or bid-side puts; bearish = ask-side puts or bid-side calls">Bias</span>
                  <span title="S = sweep (multi-exchange aggressive fill, usually institutional)">Flag</span>
                </div>
                {flowTrades.map((t, i) => {
                  const fmtTs = (ts) => {
                    if (!ts) return "—";
                    try {
                      const d = new Date(ts);
                      return new Intl.DateTimeFormat("en-US", {
                        timeZone: "America/New_York",
                        hour: "2-digit", minute: "2-digit", second: "2-digit",
                        hour12: false,
                      }).format(d);
                    } catch { return "—"; }
                  };
                  const sideCls = t.side === "call" ? "side-call" : t.side === "put" ? "side-put" : "";
                  const fillCls = t.side_label === "ask" ? "fill-ask" : t.side_label === "bid" ? "fill-bid" : "fill-mid";
                  const biasCls = t.sentiment === "bullish" ? "bias-bull" : t.sentiment === "bearish" ? "bias-bear" : "bias-neutral";
                  return (
                    <div key={i} className="flow-trade-row">
                      <span className="muted">{fmtTs(t.ts)}</span>
                      <span className={sideCls}>{t.side?.toUpperCase()}</span>
                      <span>{t.strike != null ? "$" + t.strike.toFixed(2) : "—"}</span>
                      <span className="muted">{t.expiry || "—"}</span>
                      <span>{t.size != null ? t.size.toLocaleString() : "—"}</span>
                      <span className="num-strong">{fmt$M(t.premium)}</span>
                      <span>{t.iv != null ? (t.iv * 100).toFixed(0) + "%" : "—"}</span>
                      <span className={fillCls}>{t.side_label}</span>
                      <span className={biasCls}>{t.sentiment}</span>
                      <span>{t.is_sweep ? <span className="sweep-flag" title="Sweep — aggressive multi-exchange fill, typically institutional">S</span> : ""}</span>
                    </div>
                  );
                })}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function PullbackBacktest({ ticker, direction, defaultTarget, apiFetch }) {
  const isShort = direction === "short";
  const PB_BACKTEST_KEY = "weeklyOptionsTimer.pullbackBacktest.v1";
  const persisted = (() => {
    try {
      const raw = localStorage.getItem(PB_BACKTEST_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
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
        days: days,
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
      const url = `/api/pullback_backtest?symbol=${encodeURIComponent(ticker)}`
                + `&direction=${direction}&target=${tgt}&min_gap=${gap}&days=${days}`;
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

  const hitRateColor = result?.hit_rate != null
    ? result.hit_rate >= 70 ? "up"
    : result.hit_rate >= 50 ? ""
    : "down"
    : "";

  return (
    <div className="pullback-backtest">
      <div className="pullback-backtest-title" title="Run a custom hit-rate test against the historical bars. Asks: how often did the stock pull back (or pop) at least your target percent from the open?">
        Custom backtest
      </div>
      <div className="pullback-backtest-controls">
        <div className="pbb-field">
          <label className="pbb-label" title={isShort
            ? "Pullback target as a percentage. E.g. 1.50 means count days where the stock dropped at least 1.50% below the open at some point."
            : "Pop target as a percentage. E.g. 1.50 means count days where the stock rose at least 1.50% above the open at some point."}>
            {isShort ? "Pullback target %" : "Pop target %"}
          </label>
          <input className="pbb-input"
                 type="text" inputMode="decimal"
                 value={targetStr}
                 onChange={e => setTargetStr(e.target.value)}
                 placeholder="1.00" />
        </div>
        <div className="pbb-field">
          <label className="pbb-label" title={isShort
            ? "Only count days where today's open gapped UP at least this percent from prior close. 0 = include all days."
            : "Only count days where today's open gapped at least this percent (up or down) from prior close. 0 = include all days."}>
            Min gap %
          </label>
          <input className="pbb-input"
                 type="text" inputMode="decimal"
                 value={minGapStr}
                 onChange={e => setMinGapStr(e.target.value)}
                 placeholder="0" />
        </div>
        <div className="pbb-field">
          <label className="pbb-label" title="How many trading days back to test against. Default 180.">Days history</label>
          <input className="pbb-input"
                 type="number"
                 min="5" max="500"
                 value={days}
                 onChange={e => setDays(parseInt(e.target.value || "180", 10))}
                 placeholder="180" />
        </div>
        <button className="pbb-run"
                onClick={runBacktest}
                disabled={loading}
                title="Run the backtest with the values above">
          {loading ? "Running." : "Run"}
        </button>
      </div>
      {error && <div className="research-error" style={{marginTop: 8}}>Error: {error}</div>}
      {result && !error && (
        <div className="pullback-backtest-result">
          {result.qualified_days === 0 ? (
            <div className="muted" style={{padding: "8px 0"}}>
              No qualifying days in the lookback. Try lowering the min gap filter.
            </div>
          ) : (
            <>
              <div className="pbb-meta" title="The lookback length the server actually used and how many days passed your filters">
                <span>Tested <b>{result.samples}</b> bars over the last {result.lookback_days} days. <b>{result.qualified_days}</b> met your filters.</span>
                {result.qualified_days < 20 && (
                  <span className="pbb-warning" title="Small sample. Hit rate is highly sensitive to one or two outlier days.">
                    Small sample. Treat as directional, not statistical.
                  </span>
                )}
              </div>
              <div className="pbb-headline">
                <div className="pbb-stat">
                  <div className="pbb-stat-label" title="Percent of qualifying days where the target was reached intraday">Hit rate</div>
                  <div className={`pbb-stat-val ${hitRateColor}`}>{result.hit_rate}%</div>
                </div>
                <div className="pbb-stat">
                  <div className="pbb-stat-label" title="Days that met the gap filter and were tested">Qualified days</div>
                  <div className="pbb-stat-val">{result.qualified_days}</div>
                </div>
                <div className="pbb-stat">
                  <div className="pbb-stat-label" title="Days where the target was reached intraday">Hits</div>
                  <div className="pbb-stat-val up">{result.hits}</div>
                </div>
                <div className="pbb-stat">
                  <div className="pbb-stat-label" title="Days where the target was NOT reached">Misses</div>
                  <div className="pbb-stat-val down">{result.misses}</div>
                </div>
              </div>
              <div className="pbb-secondary">
                <div className="pbb-sec-row">
                  <span title={isShort
                    ? "Average pullback size on hit days. Tells you whether hit days typically stretched well past the target or barely tagged it."
                    : "Average pop size on hit days. Tells you whether hit days typically stretched well past the target or barely tagged it."}>
                    <span style={{color: "var(--fg-3)"}}>Avg hit size</span> <b>{result.avg_win_size != null ? result.avg_win_size.toFixed(2) + "%" : "—"}</b>
                  </span>
                  <span title={isShort
                    ? "Largest single-day pullback in the hit set"
                    : "Largest single-day pop in the hit set"}>
                    <span style={{color: "var(--fg-3)"}}>Max hit</span> <b>{result.max_win_size != null ? result.max_win_size.toFixed(2) + "%" : "—"}</b>
                  </span>
                  <span title={isShort
                    ? "Average pullback size on miss days. If close to the target, raising stop tolerance helps. If far, the target is unrealistic."
                    : "Average pop size on miss days. If close to the target, raising stop tolerance helps. If far, the target is unrealistic."}>
                    <span style={{color: "var(--fg-3)"}}>Avg miss</span> <b>{result.avg_miss_size != null ? result.avg_miss_size.toFixed(2) + "%" : "—"}</b>
                  </span>
                  <span title={isShort
                    ? "How close the closest miss got to the pullback target"
                    : "How close the closest miss got to the pop target"}>
                    <span style={{color: "var(--fg-3)"}}>Closest miss</span> <b>{result.max_miss_size != null ? result.max_miss_size.toFixed(2) + "%" : "—"}</b>
                  </span>
                </div>
              </div>
              {result.recent && result.recent.length > 0 && (
                <div className="pbb-timeline" title="Most recent qualifying days. Green = target hit, red = target missed. Each cell shows the actual move that day.">
                  <div className="pbb-timeline-label">Recent {result.recent.length} qualifying days (oldest → newest)</div>
                  <div className="pbb-timeline-bar">
                    {result.recent.map((d, i) => (
                      <div key={i}
                           className={`pbb-day ${d.hit ? "hit" : "miss"}`}
                           title={`${d.date} · gap ${d.gap_pct >= 0 ? "+" : ""}${d.gap_pct}% · ${isShort ? "pullback" : "pop"} ${d.move_pct.toFixed(2)}% · ${d.hit ? "HIT" : "miss"}`} />
                    ))}
                  </div>
                </div>
              )}

              {result.weekday_breakdown && result.weekday_breakdown.some(w => w.n > 0) && (() => {
                // Best weekday call-out for the headline. Need ≥3 samples
                // to even consider it; otherwise the rate is meaningless.
                const eligible = result.weekday_breakdown.filter(w => w.n >= 3);
                let best = null;
                if (eligible.length > 0) {
                  best = eligible.reduce((a, b) => (b.hit_rate > (a?.hit_rate ?? -1) ? b : a), null);
                }
                const overallRate = result.hit_rate;
                return (
                  <div className="pbb-weekday" title="Same backtest split by day of the week. Helps spot day-of-week patterns. Hover any cell for detail.">
                    <div className="pbb-weekday-title">
                      Day-of-week breakdown
                      {best && best.hit_rate > overallRate && (
                        <span className="pbb-weekday-callout" title={`${best.weekday}s have a higher hit rate than the overall sample. ${best.hits}/${best.n} = ${best.hit_rate}%`}>
                          {" · "}<b>{best.weekday}s</b> lead at <b>{best.hit_rate}%</b>
                        </span>
                      )}
                    </div>
                    <div className="pbb-weekday-grid">
                      {result.weekday_breakdown.map(w => {
                        const isLowSample = w.n > 0 && w.n < 5;
                        const isBest = best && best.weekday === w.weekday && best.hit_rate > overallRate;
                        const cls = w.n === 0 ? "empty"
                          : w.hit_rate >= 70 ? "good"
                          : w.hit_rate >= 50 ? "ok"
                          : "weak";
                        return (
                          <div key={w.weekday}
                               className={`pbb-wd ${cls}${isBest ? " is-best" : ""}`}
                               title={w.n === 0
                                 ? `${w.weekday}: no qualifying days in this lookback`
                                 : `${w.weekday}: ${w.hits} hits / ${w.n} samples = ${w.hit_rate}%. Avg ${isShort ? "pullback" : "pop"} ${w.avg_move}%${isLowSample ? ". Low sample, treat as directional." : ""}`}>
                            <div className="pbb-wd-day">{w.weekday}</div>
                            <div className="pbb-wd-rate">{w.n === 0 ? "—" : w.hit_rate + "%"}</div>
                            <div className="pbb-wd-sub">
                              {w.n === 0 ? "no data" : `${w.hits}/${w.n}${isLowSample ? " · small" : ""}`}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })()}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function TradeBuilderCard({
  ticker, currentPrice, callAtSug, putAtSug, FRONT_DTE, activeExpDate,
  expHigh, expLow, analystData, rec, callSafePct, putSafePct, apiFetch,
  strategyMode,
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
  const mid = (q) => q && q.bid > 0 ? q.bid : (q && (q.bid + q.ask) / 2) || (q && q.last) || 0;
  const callMid = mid(callAtSug);
  const putMid = mid(putAtSug);

  // Math for the call-side trade (selling covered calls)
  const callStrike = callAtSug?.strike || 0;
  const callDelta = Math.abs(callAtSug?.delta ?? 0.20);
  const callBreakeven = callStrike + callMid; // assignment-adjusted breakeven
  const callPctOfStock = currentPrice > 0 ? (callMid / currentPrice) * 100 : 0;
  const callAnnualizedPct = FRONT_DTE > 0 && currentPrice > 0
    ? (callMid / currentPrice) * (365 / FRONT_DTE) * 100 : 0;
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
  const putPctOfStock = currentPrice > 0 ? (putMid / currentPrice) * 100 : 0;
  const putAnnualizedPct = FRONT_DTE > 0 && putStrike > 0
    ? (putMid / putStrike) * (365 / FRONT_DTE) * 100 : 0;
  const putPoP = (1 - putDelta) * 100;
  const putMaxProfit = putMid * 100;
  const putCapitalRequired = putStrike * 100; // 1 contract = 100 shares
  const putBreakevenDiscount = currentPrice > 0
    ? ((currentPrice - putBreakeven) / currentPrice) * 100 : 0;

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
  if (rec?.kind === "success") { callScore += 30; callReasons.push("Favorable timing per recommendation"); }
  else if (rec?.kind === "danger") { callScore -= 50; callReasons.push("Recommendation flagged as caution"); }
  if (aVerdict.fresh_downgrade) { callScore += 20; callReasons.push("Fresh downgrade: bearish backdrop favors short calls"); }
  if (aVerdict.fresh_upgrade) { callScore -= 30; callReasons.push("Fresh upgrade: re-rating risk for short calls"); }
  if (aTargets.upside_pct != null && aTargets.upside_pct < 0) { callScore += 15; callReasons.push("Trading above avg target: upside priced in"); }
  if (aTargets.upside_to_high_pct != null && aTargets.upside_to_high_pct < -5) { callScore += 15; callReasons.push("Above highest target: mean-reversion likely"); }
  if (callPoP > 70) { callScore += 20; callReasons.push(`PoP ${callPoP.toFixed(0)}%`); }
  if (callAnnualizedPct > 25) { callScore += 10; callReasons.push(`Annualized ${callAnnualizedPct.toFixed(0)}%`); }
  if (callPctOfStock > 1.0) { callScore += 10; callReasons.push(`Premium ${callPctOfStock.toFixed(2)}% of stock`); }

  let putScore = 0;
  const putReasons = [];
  if (rec?.kind === "info") { putScore += 30; putReasons.push("Recommendation suggests waiting on calls — put side may be live"); }
  else if (rec?.kind === "success") { putScore += 10; putReasons.push("Favorable timing"); }
  if (aVerdict.fresh_upgrade) { putScore += 25; putReasons.push("Fresh upgrade: bullish catalyst supports put strike"); }
  if (aVerdict.fresh_downgrade) { putScore -= 50; putReasons.push("Fresh downgrade: dropping price = high assignment risk"); }
  if (aTargets.upside_pct != null && aTargets.upside_pct > 15) { putScore += 20; putReasons.push("Significant analyst upside: bullish backdrop"); }
  if (aTargets.upside_to_high_pct != null && aTargets.upside_to_high_pct < -5) { putScore -= 25; putReasons.push("Above highest target: drop into put strike risk"); }
  if (putPoP > 70) { putScore += 20; putReasons.push(`PoP ${putPoP.toFixed(0)}%`); }
  if (putAnnualizedPct > 25) { putScore += 10; putReasons.push(`Annualized ${putAnnualizedPct.toFixed(0)}%`); }
  if (putPctOfStock > 1.0) { putScore += 10; putReasons.push(`Premium ${putPctOfStock.toFixed(2)}% of strike`); }

  // Front-runner pick — Phase C follow-up (v1.13). Honors strategyMode:
  //   "cc"   → only the call is eligible. Put score ignored even if higher.
  //   "csp"  → only the put is eligible. Call score ignored even if higher.
  //   "both" → existing logic, picks the higher of the two if both pass.
  const _mode = strategyMode || "both";
  const _ccEligible = (_mode === "both" || _mode === "cc");
  const _cspEligible = (_mode === "both" || _mode === "csp");
  let frontRunner = null;
  if (_ccEligible && callScore >= 30 && (!_cspEligible || callScore > putScore)) {
    frontRunner = {
      side: "call", score: callScore, reasons: callReasons,
      label: "Sell the covered call",
      detail: `Sell the $${callStrike.toFixed(2)} call expiring ${activeExpDate.toLocaleDateString("en-US", {month: "short", day: "numeric"})} for $${callMid.toFixed(2)} (delta ${callDelta.toFixed(2)}, PoP ${callPoP.toFixed(0)}%, $${callMaxProfit.toFixed(0)} per contract).`,
    };
  } else if (_cspEligible && putScore >= 30 && (!_ccEligible || putScore > callScore)) {
    frontRunner = {
      side: "put", score: putScore, reasons: putReasons,
      label: "Sell the cash-secured put",
      detail: `Sell the $${putStrike.toFixed(2)} put expiring ${activeExpDate.toLocaleDateString("en-US", {month: "short", day: "numeric"})} for $${putMid.toFixed(2)} (delta ${putDelta.toFixed(2)}, PoP ${putPoP.toFixed(0)}%, $${putMaxProfit.toFixed(0)} per contract). Effective cost basis if assigned: $${putBreakeven.toFixed(2)}.`,
    };
  } else if (_ccEligible && _cspEligible && callScore >= 30 && putScore >= 30) {
    // Both pass threshold and tied on score — pick whichever has higher annualized.
    if (callAnnualizedPct > putAnnualizedPct) {
      frontRunner = {
        side: "call", score: callScore, reasons: callReasons,
        label: "Sell the covered call",
        detail: `Both strategies scored, call has higher annualized return. Sell the $${callStrike.toFixed(2)} call for $${callMid.toFixed(2)}.`,
      };
    } else {
      frontRunner = {
        side: "put", score: putScore, reasons: putReasons,
        label: "Sell the cash-secured put",
        detail: `Both strategies scored, put has higher annualized return. Sell the $${putStrike.toFixed(2)} put for $${putMid.toFixed(2)}.`,
      };
    }
  }

  // No-trade verdict if both score below threshold
  const noTrade = !frontRunner;

  return (
    <CardErrorBoundary label="Trade Builder">
    <div className="card trade-builder-card">
      <div className="card-head">
        <div>
          <div className="kicker">
            Decision engine · 0.20 delta strikes · {activeExpDate.toLocaleDateString("en-US", {month: "short", day: "numeric"})} ({FRONT_DTE}d)
          </div>
          <div className="card-title">Trade Builder</div>
        </div>
        <div className="muted" style={{fontSize: 11, textAlign: "right"}}>
          ${currentPrice.toFixed(2)} live<br/>
          Score basis: rec + analyst + PoP + return
        </div>
      </div>

      {/* ── Front-runner banner ── */}
      {frontRunner && (
        <div className={`trade-front-runner trade-front-runner-${frontRunner.side}`}>
          <div className="tfr-header">
            <span className="tfr-label">Front-runner</span>
            <span className="tfr-score" title="Composite score from recommendation, analyst signals, PoP, and annualized return.">
              Score: {frontRunner.score}
            </span>
          </div>
          <div className="tfr-action">{frontRunner.label}</div>
          <div className="tfr-detail">{frontRunner.detail}</div>
          {frontRunner.reasons.length > 0 && (
            <div className="tfr-reasons">
              {frontRunner.reasons.map((r, i) => (
                <span key={i} className="tfr-reason-pill">{r}</span>
              ))}
            </div>
          )}
        </div>
      )}
      {noTrade && (
        <div className="trade-no-trade">
          <div className="tnt-label">No clear trade today</div>
          <div className="tnt-detail">
            {_mode === "cc" && `The call side did not score above threshold for ${ticker} at the current price and expiration. Premium, probability, and analyst backdrop are not combining into a strong CC signal here. Wait for a better setup or look at a different expiration.`}
            {_mode === "csp" && `The put side did not score above threshold for ${ticker} at the current price and expiration. Premium, probability, and analyst backdrop are not combining into a strong CSP signal here. Wait for a better setup or look at a different expiration.`}
            {_mode === "both" && `Neither strategy scored above threshold for ${ticker} at the current price and expiration. The premium, probability, and analyst backdrop do not combine into a strong signal. Wait for a better setup or look at a different expiration.`}
          </div>
          <div className="tnt-scores">
            {_ccEligible && (<span title="Composite score for selling covered calls. Below 30 = no signal.">Call: {callScore}</span>)}
            {_cspEligible && (<span title="Composite score for selling cash-secured puts. Below 30 = no signal.">Put: {putScore}</span>)}
          </div>
        </div>
      )}

      {/* ── Side-by-side comparison ── Mode-aware (v1.13): hides the
          irrelevant side when strategyMode is "cc" or "csp". The .single
          class flips the 2-col grid to 1-col so the visible side spans
          full width. */}
      <div className={`trade-builder-compare${_mode !== "both" ? " single" : ""}`}>
        {/* CALL side */}
        {_ccEligible && (
        <div className={`trade-side trade-side-call${frontRunner?.side === "call" ? " is-front" : ""}`}>
          <div className="trade-side-head">
            <div className="trade-side-title">Sell covered call</div>
            <div className="trade-side-score" title="Composite score: rec timing + analyst overlay + PoP + annualized return + premium.">
              {callScore}
            </div>
          </div>
          <div className="trade-side-strike" title="Strike picked by the dashboard's 0.20-delta target. The call you'd sell.">
            ${callStrike.toFixed(2)} <span className="trade-side-strike-meta">{((callStrike / currentPrice - 1) * 100).toFixed(1)}% OTM · {callDelta.toFixed(2)}Δ</span>
          </div>
          <div className="trade-side-rows">
            <div className="trade-row" title="Premium you collect per share. Multiply by 100 for per-contract dollar amount.">
              <span className="trade-lbl">Premium</span>
              <span className="trade-val">${callMid.toFixed(2)} <span className="muted">/ ${callMaxProfit.toFixed(0)} per contract</span></span>
            </div>
            <div className="trade-row" title="Premium as percentage of current stock price. Quick read on richness for this strike.">
              <span className="trade-lbl">% of stock</span>
              <span className="trade-val">{callPctOfStock.toFixed(2)}%</span>
            </div>
            <div className="trade-row" title="Annualized return assuming you collect this premium over the holding period and roll continuously. NOT a guarantee — assumes no early assignment, no IV expansion.">
              <span className="trade-lbl">Annualized</span>
              <span className={`trade-val ${callAnnualizedPct > 25 ? "up" : ""}`}>{callAnnualizedPct.toFixed(1)}%</span>
            </div>
            <div className="trade-row" title="Probability the option expires worthless (you keep all premium). Approximation: 1 - |delta|. Real-world PoP also depends on IV and time decay.">
              <span className="trade-lbl">PoP</span>
              <span className={`trade-val ${callPoP > 70 ? "up" : ""}`}>{callPoP.toFixed(0)}%</span>
            </div>
            <div className="trade-row" title="Stock price at which the trade breaks even on assignment. = strike + premium collected. Above this, you start losing on the underlying.">
              <span className="trade-lbl">Breakeven</span>
              <span className="trade-val">${callBreakeven.toFixed(2)}</span>
            </div>
            <div className="trade-row" title="Maximum upside if the stock rises to your strike and gets called away. = (strike - current price) × 100 + premium. Past the strike, you give up further upside.">
              <span className="trade-lbl">Max if assigned</span>
              <span className="trade-val">${callMaxUpsideIfAssigned.toFixed(0)}</span>
            </div>
            <div className="trade-row" title="Historical probability that the weekly high stayed below this strike, measured against the same baseline as the rest of the dashboard. Independent confirmation of the delta-based PoP.">
              <span className="trade-lbl">Historical safe</span>
              <span className="trade-val">{callSafePct.toFixed(0)}%</span>
            </div>
          </div>
        </div>
        )}

        {/* PUT side */}
        {_cspEligible && (
        <div className={`trade-side trade-side-put${frontRunner?.side === "put" ? " is-front" : ""}`}>
          <div className="trade-side-head">
            <div className="trade-side-title">Sell cash-secured put</div>
            <div className="trade-side-score" title="Composite score: rec timing + analyst overlay + PoP + annualized return + premium.">
              {putScore}
            </div>
          </div>
          <div className="trade-side-strike" title="Strike picked by the dashboard's 0.20-delta target. The put you'd sell.">
            ${putStrike.toFixed(2)} <span className="trade-side-strike-meta">{((1 - putStrike / currentPrice) * 100).toFixed(1)}% OTM · {putDelta.toFixed(2)}Δ</span>
          </div>
          <div className="trade-side-rows">
            <div className="trade-row" title="Premium you collect per share. Multiply by 100 for per-contract dollar amount.">
              <span className="trade-lbl">Premium</span>
              <span className="trade-val">${putMid.toFixed(2)} <span className="muted">/ ${putMaxProfit.toFixed(0)} per contract</span></span>
            </div>
            <div className="trade-row" title="Premium as percentage of strike (the capital you'd commit). Standard CSP yield metric.">
              <span className="trade-lbl">% of strike</span>
              <span className="trade-val">{putPctOfStock.toFixed(2)}%</span>
            </div>
            <div className="trade-row" title="Annualized return on capital committed if you collect this premium over the holding period and roll continuously. Assumes no assignment.">
              <span className="trade-lbl">Annualized</span>
              <span className={`trade-val ${putAnnualizedPct > 25 ? "up" : ""}`}>{putAnnualizedPct.toFixed(1)}%</span>
            </div>
            <div className="trade-row" title="Probability the option expires worthless (you keep all premium and don't get assigned the stock). Approximation: 1 - |delta|.">
              <span className="trade-lbl">PoP</span>
              <span className={`trade-val ${putPoP > 70 ? "up" : ""}`}>{putPoP.toFixed(0)}%</span>
            </div>
            <div className="trade-row" title="Effective cost basis if assigned: strike - premium. The price per share you'd own the stock at if put to you.">
              <span className="trade-lbl">If assigned at</span>
              <span className="trade-val">${putBreakeven.toFixed(2)}</span>
            </div>
            <div className="trade-row" title="Discount vs current price if assigned. Higher = more cushion below current price before assignment hurts.">
              <span className="trade-lbl">Discount</span>
              <span className={`trade-val ${putBreakevenDiscount > 5 ? "up" : ""}`}>{putBreakevenDiscount.toFixed(1)}%</span>
            </div>
            <div className="trade-row" title="Capital required to secure 1 contract = strike × 100. The cash you'd need parked to back this put.">
              <span className="trade-lbl">Capital</span>
              <span className="trade-val">${putCapitalRequired.toFixed(0)}</span>
            </div>
            <div className="trade-row" title="Historical probability that the weekly low stayed above this strike, measured against the same baseline as the rest of the dashboard. Independent confirmation of the delta-based PoP.">
              <span className="trade-lbl">Historical safe</span>
              <span className="trade-val">{putSafePct.toFixed(0)}%</span>
            </div>
          </div>
        </div>
        )}
      </div>

      {/* ── Cross-expiration comparison ── lazy fetch on click since
          loading 8 chains can take 3-10 seconds. Once loaded, lets
          the user see how the 0.20-delta strikes score across the
          next several weekly expirations side-by-side. */}
      <div className="trade-multi-exp-section">
        {!multiExpExpanded && !multiExp && (
          <button className="trade-multi-exp-toggle"
                  onClick={() => { setMultiExpExpanded(true); fetchMultiExp(); }}
                  title="Load and compare 0.20-delta strike scoring across the next 8 weekly expirations. Fetches all chains from the broker — typically 3-10 seconds.">
            Compare across expirations →
          </button>
        )}
        {multiExpExpanded && (
          <>
            <div className="trade-multi-exp-head">
              <div>
                <div className="kicker">Cross-expiration · 0.20 delta · 8 weeks out</div>
                <div className="trade-multi-exp-title">Compare across expirations</div>
              </div>
              <button className="trade-multi-exp-refresh"
                      disabled={multiExpLoading}
                      onClick={fetchMultiExp}
                      title="Re-fetch all chains. Use after market open or when prices have moved meaningfully.">
                {multiExpLoading ? "Loading…" : "Refresh"}
              </button>
            </div>
            {multiExpError && (
              <div className="trade-multi-exp-error">Error: {multiExpError}</div>
            )}
            {multiExpLoading && !multiExp && (
              <div className="trade-multi-exp-loading">
                Loading {ticker} chains across expirations… (3-10 seconds)
              </div>
            )}
            {multiExp && multiExp.rows && multiExp.rows.length > 0 && (() => {
              // Find the best annualized for each side to highlight
              const bestCallAnn = Math.max(...multiExp.rows
                .filter(r => r.call?.annualized_pct != null)
                .map(r => r.call.annualized_pct));
              const bestPutAnn = Math.max(...multiExp.rows
                .filter(r => r.put?.annualized_pct != null)
                .map(r => r.put.annualized_pct));
              return (
                <div className="trade-multi-exp-table">
                  <div className={`trade-multi-head trade-multi-mode-${_mode}`}>
                    <span title="Expiration date for this row.">Exp</span>
                    <span title="Days to expiration.">DTE</span>
                    {_ccEligible && (<>
                    <span title="Call strike at the 0.20 delta target for this expiration.">Call $</span>
                    <span title="Call premium (mid price). Multiply by 100 for per-contract.">C Prem</span>
                    <span title="Annualized return on the call premium. Higher is better, but front-week numbers are inflated by low DTE.">C Ann%</span>
                    <span title="Probability of profit for the call. Approximation: 1 - |delta|.">C PoP</span>
                    </>)}
                    {_cspEligible && (<>
                    <span title="Put strike at the 0.20 delta target for this expiration.">Put $</span>
                    <span title="Put premium (mid price). Multiply by 100 for per-contract.">P Prem</span>
                    <span title="Annualized return on capital required for the put. Higher is better.">P Ann%</span>
                    <span title="Probability of profit for the put. Approximation: 1 - |delta|.">P PoP</span>
                    </>)}
                  </div>
                  {multiExp.rows.map((r, i) => {
                    const c = r.call || {};
                    const p = r.put || {};
                    const isCallBest = c.annualized_pct === bestCallAnn;
                    const isPutBest = p.annualized_pct === bestPutAnn;
                    const expShort = r.expiration ? new Date(r.expiration + "T16:00:00")
                      .toLocaleDateString("en-US", {month: "short", day: "numeric"}) : "—";
                    return (
                      <div key={i} className={`trade-multi-row trade-multi-mode-${_mode}`}>
                        <span className="trade-multi-exp">{expShort}</span>
                        <span className="muted">{r.dte}d</span>
                        {_ccEligible && (<>
                        <span>{c.strike != null ? "$" + c.strike.toFixed(0) : "—"}</span>
                        <span>{c.mid != null ? "$" + c.mid.toFixed(2) : "—"}</span>
                        <span className={isCallBest ? "trade-multi-best" : ""}
                              title={isCallBest ? "Best call annualized return across expirations." : ""}>
                          {c.annualized_pct != null ? c.annualized_pct.toFixed(1) + "%" : "—"}
                        </span>
                        <span>{c.pop_pct != null ? c.pop_pct.toFixed(0) + "%" : "—"}</span>
                        </>)}
                        {_cspEligible && (<>
                        <span>{p.strike != null ? "$" + p.strike.toFixed(0) : "—"}</span>
                        <span>{p.mid != null ? "$" + p.mid.toFixed(2) : "—"}</span>
                        <span className={isPutBest ? "trade-multi-best" : ""}
                              title={isPutBest ? "Best put annualized return across expirations." : ""}>
                          {p.annualized_pct != null ? p.annualized_pct.toFixed(1) + "%" : "—"}
                        </span>
                        <span>{p.pop_pct != null ? p.pop_pct.toFixed(0) + "%" : "—"}</span>
                        </>)}
                      </div>
                    );
                  })}
                </div>
              );
            })()}
            {multiExp && (!multiExp.rows || multiExp.rows.length === 0) && !multiExpLoading && (
              <div className="trade-multi-exp-empty">
                No usable strikes found for {ticker} in the next 8 weeks.
              </div>
            )}
          </>
        )}
      </div>

      <div className="trade-builder-disclaimer" title="The score combines several heuristics and isn't backtested. Treat it as a structured second opinion, not a signal to blindly follow.">
        Heuristic score, not backtested. Decisions remain yours.
      </div>
    </div>
    </CardErrorBoundary>
  );
}

function AnalystCard({ ticker, currentPrice, apiFetch, onData, strategyMode }) {
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

  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    const fetchData = async () => {
      try {
        setLoading(true);
        const url = `/api/analyst?symbol=${encodeURIComponent(ticker)}`
                  + (currentPrice ? `&price=${currentPrice}` : "")
                  + (refreshKey > 0 ? `&force=1` : "");
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
    return () => { cancelled = true; };
  }, [ticker, currentPrice, refreshKey]);

  // Color-coded action pills
  const actionClass = (action) => ({
    upgrade: "analyst-action-upgrade",
    downgrade: "analyst-action-downgrade",
    initiate: "analyst-action-initiate",
    target_change: "analyst-action-target",
    reiterate: "analyst-action-reiterate",
    unknown: "analyst-action-unknown",
  }[action] || "analyst-action-unknown");

  const actionLabel = (action) => ({
    upgrade: "Upgrade",
    downgrade: "Downgrade",
    initiate: "Initiate",
    target_change: "Target",
    reiterate: "Reiterate",
    unknown: "—",
  }[action] || "—");

  const gradeClass = (g) => {
    if (!g) return "";
    if (["Strong Buy", "Buy", "Outperform", "Overweight"].includes(g)) return "grade-bull";
    if (["Strong Sell", "Sell", "Underperform", "Underweight"].includes(g)) return "grade-bear";
    return "grade-neutral";
  };

  // Verdict pill class — color the tag based on its sentiment
  const tagClass = (tag) => {
    const t = tag.toLowerCase();
    if (t.includes("bullish") || t.includes("upgrade") || t.includes("more bullish") || t.includes("upside continuation")) return "verdict-pill-bull";
    if (t.includes("bearish") || t.includes("downgrade") || t.includes("above average") || t.includes("overextension") || t.includes("far above")) return "verdict-pill-bear";
    if (t.includes("no recent")) return "verdict-pill-neutral";
    return "verdict-pill-info";
  };

  return (
    <CardErrorBoundary label="Analyst price targets">
    <div className="card analyst-card">
      <div className="card-head">
        <div>
          <div className="kicker">Analyst price targets · ratings · catalysts</div>
          <div className="card-title">Analyst price targets</div>
        </div>
        <div className="research-controls">
          <button className="research-run-btn"
                  disabled={loading}
                  onClick={() => setRefreshKey(k => k + 1)}
                  title="Force-refresh analyst data (bypasses 30 min cache).">
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div className="research-error">Error: {error}</div>
      )}

      {!data && !loading && !error && (
        <div className="research-empty">Loading analyst data for {ticker}…</div>
      )}

      {data && !data.data_available && (
        <div className="research-empty">
          No analyst data available for {ticker}. This is normal for very small caps,
          recent IPOs, or international tickers.
        </div>
      )}

      {data && data.data_available && (() => {
        const t = data.targets || {};
        const c = data.consensus || {};
        const v = data.verdict || {};
        const upside = t.upside_pct;
        const upsideCls = upside == null ? "" : upside > 5 ? "up" : upside < -5 ? "down" : "";
        const consensusCls = !c.label ? ""
          : ["Strong Buy", "Buy"].includes(c.label) ? "up"
          : ["Strong Sell", "Sell"].includes(c.label) ? "down" : "";
        return (
          <>
            {/* ── Stats grid ── */}
            <div className="analyst-stats-grid">
              <div className="analyst-stat" title="Current stock price the dashboard is using.">
                <div className="analyst-stat-lbl">Price</div>
                <div className="analyst-stat-val">{data.current_price != null ? "$" + Number(data.current_price).toFixed(2) : "—"}</div>
              </div>
              <div className="analyst-stat" title="Average analyst price target across all covering firms.">
                <div className="analyst-stat-lbl">Avg target</div>
                <div className="analyst-stat-val">{t.mean != null ? "$" + Number(t.mean).toFixed(2) : "—"}</div>
              </div>
              <div className="analyst-stat" title="Highest individual analyst target.">
                <div className="analyst-stat-lbl">High</div>
                <div className="analyst-stat-val up">{t.high != null ? "$" + Number(t.high).toFixed(2) : "—"}</div>
              </div>
              <div className="analyst-stat" title="Lowest individual analyst target.">
                <div className="analyst-stat-lbl">Low</div>
                <div className="analyst-stat-val down">{t.low != null ? "$" + Number(t.low).toFixed(2) : "—"}</div>
              </div>
              <div className="analyst-stat" title="Percentage from current price to average target. Positive = upside expected by analysts. Negative = trading above average target.">
                <div className="analyst-stat-lbl">Upside</div>
                <div className={`analyst-stat-val ${upsideCls}`}>
                  {upside != null ? (upside >= 0 ? "+" : "") + upside.toFixed(1) + "%" : "—"}
                </div>
              </div>
              <div className="analyst-stat" title="Aggregate consensus rating from the most recent month's recommendation breakdown. Requires FINNHUB_API_KEY in .env to populate.">
                <div className="analyst-stat-lbl">Consensus</div>
                <div className={`analyst-consensus ${consensusCls}`}>
                  {c.label && c.label !== "—" ? (
                    <span className="analyst-stat-val">
                      {c.label}
                      {c.score != null && <span className="analyst-score-num"> {c.score}</span>}
                    </span>
                  ) : (
                    <span className="analyst-finnhub-hint" title="Set FINNHUB_API_KEY in .env to enable consensus, analyst count, and trend.">
                      —<span className="analyst-needs-finnhub">needs Finnhub</span>
                    </span>
                  )}
                </div>
              </div>
              <div className="analyst-stat" title="Number of analysts contributing to the price target consensus. Comes from Finnhub.">
                <div className="analyst-stat-lbl">Analysts</div>
                <div className="analyst-stat-val">
                  {t.num_analysts != null
                    ? t.num_analysts
                    : <span className="analyst-needs-finnhub">needs Finnhub</span>}
                </div>
              </div>
              <div className="analyst-stat" title="Whether sentiment has shifted bullish, bearish, or stayed stable across the last 3 months. Requires Finnhub.">
                <div className="analyst-stat-lbl">Trend</div>
                <div className={`analyst-stat-val analyst-trend-${c.trend || "none"}`}>
                  {c.trend === "more_bullish" ? "↑ Bullish"
                   : c.trend === "more_bearish" ? "↓ Bearish"
                   : c.trend === "stable" ? "→ Stable"
                   : <span className="analyst-needs-finnhub">needs Finnhub</span>}
                </div>
              </div>
            </div>

            {/* ── Consensus breakdown bar ── */}
            {c.breakdown && c.breakdown.total > 0 && (() => {
              const bd = c.breakdown;
              const pct = (n) => bd.total > 0 ? (n / bd.total * 100) : 0;
              return (
                <div className="analyst-consensus-bar" title={`Latest consensus: ${bd.strong_buy} Strong Buy, ${bd.buy} Buy, ${bd.hold} Hold, ${bd.sell} Sell, ${bd.strong_sell} Strong Sell`}>
                  <div className="analyst-bar-segment analyst-bar-strong-buy" style={{width: pct(bd.strong_buy) + "%"}} title={`Strong Buy: ${bd.strong_buy}`} />
                  <div className="analyst-bar-segment analyst-bar-buy" style={{width: pct(bd.buy) + "%"}} title={`Buy: ${bd.buy}`} />
                  <div className="analyst-bar-segment analyst-bar-hold" style={{width: pct(bd.hold) + "%"}} title={`Hold: ${bd.hold}`} />
                  <div className="analyst-bar-segment analyst-bar-sell" style={{width: pct(bd.sell) + "%"}} title={`Sell: ${bd.sell}`} />
                  <div className="analyst-bar-segment analyst-bar-strong-sell" style={{width: pct(bd.strong_sell) + "%"}} title={`Strong Sell: ${bd.strong_sell}`} />
                </div>
              );
            })()}

            {/* ── Verdict tag pills ── */}
            {v.tags && v.tags.length > 0 && (
              <div className="analyst-verdict-row">
                {v.tags.map((tag, i) => (
                  <span key={i} className={`verdict-pill ${tagClass(tag)}`}>{tag}</span>
                ))}
              </div>
            )}

            {/* ── Trading warnings ── two-column layout: covered calls
                on the left, cash-secured puts on the right. Same root
                analyst data, strategy-specific implications. Hidden if
                the underlying data triggers no warnings for that side. */}
            {((v.call_warnings && v.call_warnings.length > 0 && (strategyMode === "both" || strategyMode === "cc" || !strategyMode)) ||
              (v.put_warnings && v.put_warnings.length > 0 && (strategyMode === "both" || strategyMode === "csp" || !strategyMode))) && (
              <div className="analyst-warnings-row">
                {v.call_warnings && v.call_warnings.length > 0 && (strategyMode === "both" || strategyMode === "cc" || !strategyMode) && (
                  <div className="analyst-warnings analyst-warnings-cc">
                    <div className="analyst-warnings-title">Selling covered calls</div>
                    <ul>
                      {v.call_warnings.map((w, i) => (<li key={i}>{w}</li>))}
                    </ul>
                  </div>
                )}
                {v.put_warnings && v.put_warnings.length > 0 && (strategyMode === "both" || strategyMode === "csp" || !strategyMode) && (
                  <div className="analyst-warnings analyst-warnings-csp">
                    <div className="analyst-warnings-title">Selling cash-secured puts</div>
                    <ul>
                      {v.put_warnings.map((w, i) => (<li key={i}>{w}</li>))}
                    </ul>
                  </div>
                )}
              </div>
            )}
            {(v.intraday_signals && v.intraday_signals.length > 0) && (
              <div className="analyst-warnings analyst-warnings-intraday">
                <div className="analyst-warnings-title">Intraday catalyst signals</div>
                <ul>
                  {v.intraday_signals.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* ── History table ── */}
            {data.history && data.history.length > 0 && (
              <div className="analyst-history">
                <div className="analyst-history-title">
                  Recent analyst updates ({data.history.length})
                  <span className="analyst-source-tag" title={`Data source: ${data.source}`}> · {data.source}</span>
                </div>
                <div className="analyst-history-table">
                  <div className="analyst-history-head">
                    <span title="Date the analyst published this rating change or price target update. Most recent first.">Date</span>
                    <span title="Investment bank or research firm that issued the call (e.g. Morgan Stanley, JP Morgan, Wedbush).">Firm</span>
                    <span title="Type of update. Upgrade = rating raised. Downgrade = rating lowered. Initiate = first time covering. Target = price target changed but rating unchanged. Reiterate = no change to either.">Action</span>
                    <span title="Rating change. Shows prior rating → new rating when the rating moved. Bullish ratings (Buy, Outperform, Overweight) in green. Bearish (Sell, Underperform) in red. Hold/Neutral in gray.">Rating</span>
                    <span title="Price target. Shows prior target → new target when changed. Single value when only the rating moved or this is an initiation.">Target</span>
                    <span title="Percentage change in the price target from prior to new. Positive (green) = target raised. Negative (red) = target lowered. Blank = no prior target available (initiations or rating-only changes).">Δ</span>
                  </div>
                  {data.history.slice(0, 30).map((h, i) => {
                    const tcCls = h.target_change_pct == null ? ""
                      : h.target_change_pct > 0 ? "up"
                      : h.target_change_pct < 0 ? "down" : "";
                    return (
                      <div key={i} className="analyst-history-row">
                        <span className="muted">{h.date || "—"}</span>
                        <span className="analyst-firm">{h.firm || "—"}</span>
                        <span className={`analyst-action-pill ${actionClass(h.action_class)}`}>
                          {actionLabel(h.action_class)}
                        </span>
                        <span className="analyst-grade-cell">
                          {h.prior_grade && h.new_grade && h.prior_grade !== h.new_grade ? (
                            <>
                              <span className={`grade-pill ${gradeClass(h.prior_grade)}`}>{h.prior_grade}</span>
                              <span className="grade-arrow"> → </span>
                              <span className={`grade-pill ${gradeClass(h.new_grade)}`}>{h.new_grade}</span>
                            </>
                          ) : h.new_grade ? (
                            <span className={`grade-pill ${gradeClass(h.new_grade)}`}>{h.new_grade}</span>
                          ) : "—"}
                        </span>
                        <span className="analyst-target-cell">
                          {h.prior_target && h.new_target && h.prior_target !== h.new_target ? (
                            <>
                              <span className="muted">${h.prior_target.toFixed(0)}</span>
                              <span className="muted"> → </span>
                              <span>${h.new_target.toFixed(0)}</span>
                            </>
                          ) : h.new_target ? (
                            <span>${h.new_target.toFixed(0)}</span>
                          ) : "—"}
                        </span>
                        <span className={`analyst-target-change ${tcCls}`}>
                          {h.target_change_pct != null
                            ? (h.target_change_pct > 0 ? "+" : "") + h.target_change_pct.toFixed(1) + "%"
                            : "—"}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        );
      })()}
    </div>
    </CardErrorBoundary>
  );
}

function PullbackProfileCard({ ticker, currentPrice, livePrice, apiFetch }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [basingData, setBasingData] = useState(null);
  // Direction toggle — "short" = sell at open, cover lower (open→low pullback);
  // "long" = buy at open, sell higher (open→high pop). Persisted in localStorage.
  const PB_DIR_KEY = "weeklyOptionsTimer.pullback.direction.v1";
  const [direction, setDirection] = useState(() => {
    try { return localStorage.getItem(PB_DIR_KEY) || "short"; } catch { return "short"; }
  });
  useEffect(() => {
    try { localStorage.setItem(PB_DIR_KEY, direction); } catch {}
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
    return () => { cancelled = true; };
  }, [ticker]);

  // Today's session OHL — same endpoint as BasingCard, polled every 30s
  // during market hours. Schwab cache makes this cheap.
  const isMarketOpen = () => {
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
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
    return () => { cancelled = true; if (timer) clearInterval(timer); };
  }, [ticker]);

  if (loading && !data) {
    return (
      <div className="card pullback-card">
        <div className="card-head">
          <div>
            <div className="kicker">Open behavior · pullback profile</div>
            <div className="card-title">Open-to-low / open-to-high</div>
          </div>
        </div>
        <div className="muted" style={{padding: "16px 0"}}>Loading historical pullback stats.</div>
      </div>
    );
  }
  if (error || !data || data.samples === 0) {
    return null;  // hide if not enough data
  }
  const fmt$ = (v) => v == null ? "—" : "$" + v.toFixed(2);
  const isShort = direction === "short";

  // Today's setup
  const live = livePrice ?? currentPrice;
  const sessionOpen = basingData?.session_open;
  const sessionLow = basingData?.session_low;
  const sessionHigh = basingData?.session_high;
  const prevClose = basingData?.prev_close;
  const todayGapPct = (sessionOpen && prevClose) ? ((sessionOpen - prevClose) / prevClose) * 100 : null;
  const todayPullbackSoFar = (sessionOpen && sessionLow) ? Math.max(0, ((sessionOpen - sessionLow) / sessionOpen) * 100) : null;
  const todayPopSoFar = (sessionOpen && sessionHigh) ? Math.max(0, ((sessionHigh - sessionOpen) / sessionOpen) * 100) : null;
  const fromOpenNow = (sessionOpen && live) ? ((live - sessionOpen) / sessionOpen) * 100 : null;

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
  const targetPrice = sessionOpen
    ? (isShort ? sessionOpen * (1 - targetPct / 100) : sessionOpen * (1 + targetPct / 100))
    : null;
  const stretchPrice = sessionOpen
    ? (isShort ? sessionOpen * (1 - stretchPct / 100) : sessionOpen * (1 + stretchPct / 100))
    : null;
  const stopPrice = sessionOpen
    ? (isShort ? sessionOpen * (1 + (primary.median * 0.5) / 100)
                : sessionOpen * (1 - (primary.median * 0.5) / 100))
    : null;

  // Verdict — direction-specific reasoning
  let verdict = null, verdictReason = null, verdictCls = null;
  const goAwayPct = isShort
    ? (primaryGroup.gap_and_go_pct ?? primary.open_eq_low_pct)  // % chance of running away (open=low)
    : primary.open_eq_high_pct;                                  // % chance open=high (no buy opportunity)
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
    verdictReason = isShort
      ? `${goAwayPct.toFixed(0)}% of similar days were gap-and-go (open = low). Risk of running away.`
      : `${goAwayPct.toFixed(0)}% of similar days had open = high (no pop). Risk of fading immediately.`;
    verdictCls = "verdict-avoid";
  } else if (todaySoFar != null && todaySoFar >= primary.median * 0.8) {
    verdict = isShort ? "Already pulled back" : "Already popped";
    verdictReason = isShort
      ? `Already ${todaySoFar.toFixed(2)}% below open. Most of the typical pullback (${primary.median.toFixed(2)}% median) is already done.`
      : `Already ${todaySoFar.toFixed(2)}% above open. Most of the typical pop (${primary.median.toFixed(2)}% median) is already done.`;
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
    verdictReason = isShort
      ? `Typical pullback ${primary.median.toFixed(2)}% (median) on ${primaryLabel.toLowerCase()}. Open-eq-low rate ${goAwayPct.toFixed(0)}%.`
      : `Typical pop ${primary.median.toFixed(2)}% (median) on ${primaryLabel.toLowerCase()}. Open-eq-high rate ${goAwayPct.toFixed(0)}%.`;
    verdictCls = "verdict-sell";
  }

  const thresholds = primary.thresholds || {};

  return (
    <div className="card pullback-card">
      <div className="card-head">
        <div>
          <div className="kicker">{isShort ? "Short the open · pullback profile" : "Buy the open · pop profile"}</div>
          <div className="card-title">{isShort ? "Open-to-low pullback" : "Open-to-high pop"} · {data.lookback_days}d history</div>
        </div>
        <div className="pullback-card-tools">
          <div className="kicker" title="Sample size for the primary distribution">
            {primaryLabel}
          </div>
          <div className="basing-toggle" title="Switch between short-the-open (pullback) and buy-the-open (pop) views">
            <button className={isShort ? "active" : ""}
                    onClick={() => setDirection("short")}
                    title="Short the open. Sell at open, cover at the typical intraday low.">Short</button>
            <button className={!isShort ? "active" : ""}
                    onClick={() => setDirection("long")}
                    title="Buy the open. Buy at open, sell at the typical intraday high.">Long</button>
          </div>
        </div>
      </div>

      <div className={`basing-verdict ${verdictCls}`}>
        <div className="basing-verdict-label">{verdict}</div>
        <div className="basing-verdict-reason">{verdictReason}</div>
      </div>

      {/* Primary stats grid */}
      <div className="pullback-grid">
        <div className="pullback-stat">
          <div className="pullback-stat-label" title={isShort ? "Median percentage drop from open to intraday low" : "Median percentage rise from open to intraday high"}>{isShort ? "Median pullback" : "Median pop"}</div>
          <div className="pullback-stat-val">{fmtPct(primary.median)}</div>
        </div>
        <div className="pullback-stat">
          <div className="pullback-stat-label" title={isShort ? "75th percentile — pullback exceeded on 25% of days" : "75th percentile — pop exceeded on 25% of days"}>75th %ile</div>
          <div className="pullback-stat-val">{fmtPct(primary.p75)}</div>
        </div>
        <div className="pullback-stat">
          <div className="pullback-stat-label" title={isShort ? "90th percentile — pullback exceeded on 10% of days" : "90th percentile — pop exceeded on 10% of days"}>90th %ile</div>
          <div className="pullback-stat-val">{fmtPct(primary.p90)}</div>
        </div>
        <div className="pullback-stat">
          <div className="pullback-stat-label" title={isShort
            ? "Frequency of days where the open was the intraday low (gap-and-go rate). Higher = more risk for shorts."
            : "Frequency of days where the open was the intraday high (no pop). Higher = more risk for longs."}>{isShort ? "Open = low" : "Open = high"}</div>
          <div className="pullback-stat-val">{fmtPct(goAwayPct)}</div>
        </div>
      </div>

      {/* Threshold table — frequency of pullback ≥ X% */}
      <div className="pullback-thresholds">
        <div className="pullback-thresholds-title" title={isShort
          ? "How often the stock pulled back at least X% from the open over the lookback period"
          : "How often the stock popped at least X% above the open over the lookback period"}>{isShort ? "Pullback frequency" : "Pop frequency"}</div>
        <div className="pullback-thresholds-grid">
          {[0.25, 0.50, 0.75, 1.00, 1.50, 2.00].map(t => {
            const v = thresholds[t.toString()];
            return (
              <div key={t} className="pullback-thresh">
                <div className="pullback-thresh-label" title={isShort
                  ? `Frequency of days where the open-to-low pullback exceeded ${t.toFixed(2)}%`
                  : `Frequency of days where the open-to-high pop exceeded ${t.toFixed(2)}%`}>≥ {t.toFixed(2)}%</div>
                <div className="pullback-thresh-val">{v ? v.pct.toFixed(0) + "%" : "—"}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Today's setup */}
      <div className="pullback-today">
        <div className="pullback-today-title" title="Live values for today's session vs the historical pullback profile">Today</div>
        <div className="pullback-today-grid">
          <div className="pullback-today-stat">
            <div className="pullback-today-label" title="Today's opening print at 9:30am ET">Open</div>
            <div className="pullback-today-val">{fmt$(sessionOpen)}</div>
          </div>
          <div className="pullback-today-stat">
            <div className="pullback-today-label" title="Percent gap from yesterday's close to today's open">Gap</div>
            <div className={`pullback-today-val ${todayGapPct != null && todayGapPct >= 0 ? "up" : "down"}`}>
              {fmtPct(todayGapPct)}
            </div>
          </div>
          <div className="pullback-today-stat">
            <div className="pullback-today-label" title="Current price as a percentage of today's open. Negative = pulled back below open">Now</div>
            <div className={`pullback-today-val ${fromOpenNow != null && fromOpenNow >= 0 ? "up" : "down"}`}>
              {fromOpenNow != null ? (fromOpenNow >= 0 ? "+" : "") + fromOpenNow.toFixed(2) + "%" : "—"}
            </div>
          </div>
          <div className="pullback-today-stat">
            <div className="pullback-today-label" title={isShort
              ? "Open-to-low pullback so far today. Compare to historical median to see if a typical pullback has already happened"
              : "Open-to-high pop so far today. Compare to historical median to see if a typical pop has already happened"}>{isShort ? "LoD pullback" : "HoD pop"}</div>
            <div className={`pullback-today-val ${isShort ? "down" : "up"}`}>
              {(() => {
                const v = isShort ? todayPullbackSoFar : todayPopSoFar;
                if (v == null) return "—";
                return (isShort ? "-" : "+") + v.toFixed(2) + "%";
              })()}
            </div>
          </div>
        </div>
      </div>

      {/* Suggested levels — only show when verdict is short-friendly */}
      {(verdictCls === "verdict-sell" || verdictCls === "verdict-partial") && sessionOpen && (
        <div className="pullback-levels">
          <div className="pullback-levels-title" title={isShort
            ? "Price levels for entering and managing a short-the-open trade based on historical pullback statistics"
            : "Price levels for entering and managing a buy-the-open trade based on historical pop statistics"}>
            {isShort ? "Suggested short levels" : "Suggested long levels"}
          </div>
          <div className="pullback-levels-grid">
            <div className="pullback-level">
              <div className="pullback-level-label" title={isShort
                ? "Best price area to enter a short. At or above the open captures the largest typical pullback"
                : "Best price area to enter a long. At or below the open captures the largest typical pop"}>Entry zone</div>
              <div className="pullback-level-val">{isShort ? "≥" : "≤"} {fmt$(sessionOpen)}</div>
              <div className="pullback-level-sub">{isShort ? "at or above open" : "at or below open"}</div>
            </div>
            <div className="pullback-level">
              <div className="pullback-level-label" title={isShort
                ? "Conservative cover price — the median historical open-to-low pullback. Half of historical days reach this level"
                : "Conservative profit target — the median historical open-to-high pop. Half of historical days reach this level"}>{isShort ? "Cover target" : "Profit target"}</div>
              <div className={`pullback-level-val ${isShort ? "down" : "up"}`}>{fmt$(targetPrice)}</div>
              <div className="pullback-level-sub">{isShort ? "median pullback" : "median pop"}</div>
            </div>
            <div className="pullback-level">
              <div className="pullback-level-label" title={isShort
                ? "Aggressive cover price — the 75th percentile pullback. Only 25% of historical days reach this level"
                : "Aggressive profit target — the 75th percentile pop. Only 25% of historical days reach this level"}>Stretch target</div>
              <div className={`pullback-level-val ${isShort ? "down" : "up"}`}>{fmt$(stretchPrice)}</div>
              <div className="pullback-level-sub">75th %ile</div>
            </div>
            <div className="pullback-level">
              <div className="pullback-level-label" title={isShort
                ? "If price pushes this far above open with no pullback yet, the short-the-open thesis has failed. Stop loss level"
                : "If price drops this far below open with no pop yet, the buy-the-open thesis has failed. Stop loss level"}>Stop</div>
              <div className={`pullback-level-val ${isShort ? "up" : "down"}`}>{fmt$(stopPrice)}</div>
              <div className="pullback-level-sub">{isShort ? "half median above" : "half median below"}</div>
            </div>
          </div>
          {live && targetPrice && stopPrice && (() => {
            // R:R = reward / risk. For short: reward = live - target (cover lower), risk = stop - live (stop above).
            //                     For long: reward = target - live (sell higher), risk = live - stop (stop below).
            const reward = isShort ? (live - targetPrice) : (targetPrice - live);
            const risk = isShort ? (stopPrice - live) : (live - stopPrice);
            const rr = risk > 0 ? (reward / risk) : null;
            return (
              <div className="pullback-rr">
                R:R from current {fmt$(live)} = <b>{rr != null ? rr.toFixed(2) : "—"}</b>
                {rr != null && rr >= 2 && <span className="rr-good"> · good</span>}
                {rr != null && rr < 1 && <span className="rr-bad"> · poor</span>}
              </div>
            );
          })()}
        </div>
      )}

      {/* Conditional — show strong-gap and high-rvol blocks when sample sufficient */}
      {(data.strong_gap || data.high_rvol) && (() => {
        const sg = data.strong_gap ? (isShort ? data.strong_gap.short : data.strong_gap.long) : null;
        const hr = data.high_rvol ? (isShort ? data.high_rvol.short : data.high_rvol.long) : null;
        const eqKey = isShort ? "open_eq_low_pct" : "open_eq_high_pct";
        const eqLabel = isShort ? "Open=low" : "Open=high";
        return (
          <div className="pullback-conditions">
            {sg && (
              <div className="pullback-cond">
                <div className="pullback-cond-label" title="Days where the open gapped up at least 3% from prior close. Strong gaps tend to behave differently than normal gap-ups">Strong gap (≥3%, n={data.strong_gap.n})</div>
                <div className="pullback-cond-vals">
                  <span title={isShort ? "Median open-to-low pullback on strong-gap days" : "Median open-to-high pop on strong-gap days"}><span style={{color: "var(--fg-3)"}}>Median</span> <b>{fmtPct(sg.median)}</b></span>
                  <span title={isShort ? "75th percentile pullback on strong-gap days" : "75th percentile pop on strong-gap days"}><span style={{color: "var(--fg-3)"}}>p75</span> <b>{fmtPct(sg.p75)}</b></span>
                  <span title={isShort ? "Frequency the open was the day's low on strong-gap days" : "Frequency the open was the day's high on strong-gap days"}><span style={{color: "var(--fg-3)"}}>{eqLabel}</span> <b>{fmtPct(sg[eqKey])}</b></span>
                </div>
              </div>
            )}
            {hr && (
              <div className="pullback-cond">
                <div className="pullback-cond-label" title="Days where today's volume was in the top 25% of recent volume distribution. High relative volume often signals a catalyst is driving the stock">High rel. volume (top 25%, n={data.high_rvol.n})</div>
                <div className="pullback-cond-vals">
                  <span title={isShort ? "Median open-to-low pullback on high relative volume days" : "Median open-to-high pop on high relative volume days"}><span style={{color: "var(--fg-3)"}}>Median</span> <b>{fmtPct(hr.median)}</b></span>
                  <span title={isShort ? "75th percentile pullback on high relative volume days" : "75th percentile pop on high relative volume days"}><span style={{color: "var(--fg-3)"}}>p75</span> <b>{fmtPct(hr.p75)}</b></span>
                  <span title={isShort ? "Frequency the open was the day's low on high volume days" : "Frequency the open was the day's high on high volume days"}><span style={{color: "var(--fg-3)"}}>{eqLabel}</span> <b>{fmtPct(hr[eqKey])}</b></span>
                </div>
              </div>
            )}
          </div>
        );
      })()}

      {/* Custom backtest — user-driven win rate at a specific target */}
      <PullbackBacktest
        ticker={ticker}
        direction={direction}
        defaultTarget={primary.median}
        apiFetch={apiFetch}
      />

      {/* Gap behavior split — only when gap-up sample exists */}
      {data.gap_up && data.gap_up.n >= 10 && data.gap_up.gap_and_go_pct != null && (
        <div className="pullback-split">
          <div className="pullback-split-title" title="How gap-up days resolved historically: ran straight up (gap-and-go), pulled back then closed near or above open (normal pullback), or faded all day (gap fade)">Gap-up day breakdown</div>
          <div className="pullback-split-bar">
            <div className="pullback-split-seg seg-gandgo"
                 style={{width: `${data.gap_up.gap_and_go_pct}%`}}
                 title={`Gap-and-go: ${data.gap_up.gap_and_go_pct.toFixed(0)}% — open was the low`}>
              {data.gap_up.gap_and_go_pct >= 10 && data.gap_up.gap_and_go_pct.toFixed(0) + "%"}
            </div>
            <div className="pullback-split-seg seg-normal"
                 style={{width: `${data.gap_up.normal_pullback_pct}%`}}
                 title={`Normal pullback: ${data.gap_up.normal_pullback_pct.toFixed(0)}% — pulled back then closed near or above open`}>
              {data.gap_up.normal_pullback_pct >= 10 && data.gap_up.normal_pullback_pct.toFixed(0) + "%"}
            </div>
            <div className="pullback-split-seg seg-fade"
                 style={{width: `${data.gap_up.gap_fade_pct}%`}}
                 title={`Gap fade: ${data.gap_up.gap_fade_pct.toFixed(0)}% — closed below open by ≥0.5%`}>
              {data.gap_up.gap_fade_pct >= 10 && data.gap_up.gap_fade_pct.toFixed(0) + "%"}
            </div>
          </div>
          <div className="pullback-split-legend">
            <span><i className="legend-sw seg-gandgo"></i>Gap & go</span>
            <span><i className="legend-sw seg-normal"></i>Normal pullback</span>
            <span><i className="legend-sw seg-fade"></i>Gap fade</span>
          </div>
        </div>
      )}
    </div>
  );
}

function BasingCard({ ticker, weeks, apiFetch, livePrice }) {
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
    } catch { return null; }
  })();
  const [viewMode, setViewMode] = useState(_basingPrefs?.viewMode ?? "time");
  // Overlay: when true, draw the OTHER mode's heatmap as a horizontal bar
  // beneath each price row so user can scan extreme volume / time levels.
  const [showOverlay, setShowOverlay] = useState(_basingPrefs?.showOverlay ?? false);
  // Persist any pref change
  useEffect(() => {
    try {
      localStorage.setItem(BASING_PREFS_KEY, JSON.stringify({ viewMode, showOverlay }));
    } catch {}
  }, [viewMode, showOverlay]);

  const isMarketOpen = () => {
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
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
    return () => { cancelled = true; if (timer) clearInterval(timer); };
  }, [ticker, weeks]);

  if (error) {
    return (
      <div className="card basing-card">
        <div className="card-head">
          <div>
            <div className="kicker">Mean reversion · today's basing</div>
            <div className="card-title">Intraday basing levels</div>
          </div>
        </div>
        <div className="muted" style={{padding: "16px 0"}}>Couldn't load profile: {error}</div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="card basing-card">
        <div className="card-head">
          <div>
            <div className="kicker">Mean reversion · today's basing</div>
            <div className="card-title">Intraday basing levels</div>
          </div>
        </div>
        <div className="muted" style={{padding: "16px 0"}}>{loading ? "Loading." : "No data."}</div>
      </div>
    );
  }
  const fmt$ = (v) => v == null ? "—" : "$" + v.toFixed(2);

  // Live today % — recomputed every 5s as livePrice updates.
  // Falls back to server value if prev_close or livePrice missing.
  const livePct = (livePrice != null && data.prev_close)
    ? ((livePrice - data.prev_close) / data.prev_close) * 100
    : data.today_pct;

  // Histogram bin scaling
  const bins = data.bins || [];
  const maxTime = Math.max(1, ...bins.map(b => b.time_min));
  const maxVol  = Math.max(1, ...bins.map(b => b.volume));

  // Show in price-descending order so highest price is at the top
  const binsTopDown = [...bins].reverse();

  return (
    <div className="card basing-card">
      <div className="card-head">
        <div>
          <div className="kicker">Mean reversion · today's basing</div>
          <div className="card-title">
            Intraday basing levels {data.bounce_signal && <span className="basing-signal">Possible bounce setup</span>}
          </div>
        </div>
      </div>

      {/* Verdict banner — one-line covered-call timing recommendation */}
      {data.verdict && (() => {
        const cls = data.verdict === "Sell now" ? "verdict-sell"
                  : data.verdict === "Sell partial" ? "verdict-partial"
                  : data.verdict === "Avoid" ? "verdict-avoid"
                  : "verdict-wait";
        return (
          <div className={`basing-verdict ${cls}`}>
            <div className="basing-verdict-label">{data.verdict}</div>
            <div className="basing-verdict-reason">{data.verdict_reason}</div>
          </div>
        );
      })()}

      {/* Section 1: today vs typical weekday */}
      <div className="basing-row1">
        <div className="basing-stat">
          <div className="basing-stat-label" title="Today's percent change from yesterday's close">Today</div>
          <div className={`basing-stat-val ${livePct >= 0 ? "up" : "down"}`}>
            {fmtPct(livePct)}
          </div>
        </div>
        <div className="basing-stat">
          <div className="basing-stat-label" title={`Median close-to-prior-close % move on ${data.today_dow}s across the lookback window`}>Typical {data.today_dow} close</div>
          <div className="basing-stat-val">
            {fmtPct(data.typical_dow.median)}
          </div>
          <div className="basing-stat-sub" title={`10th to 90th percentile range of ${data.today_dow} closes`}>
            range {fmtPct(data.typical_dow.p10)} to {fmtPct(data.typical_dow.p90)}
            {" · "}n={data.typical_dow.samples}
          </div>
        </div>
        <div className="basing-stat">
          <div className="basing-stat-label" title="Whether today is moving more than 1.5x the typical range for this weekday">Status</div>
          <div className={`basing-stat-val ${data.stretched ? "down" : ""}`}
               title={data.stretched
                 ? "Today's move is 1.5x larger than the typical range for this weekday — a potential mean-reversion candidate"
                 : "Today's move is within the typical range for this weekday"}>
            {data.stretched ? "Stretched" : "Normal"}
          </div>
          <div className="basing-stat-sub"
               title={data.holding_base
                 ? "The last 30 minutes have stayed within 0.5% of the Point of Control — a bounce or breakdown setup may be forming"
                 : "Price is not yet consolidating near a high-volume level"}>
            {data.holding_base ? "holding base near POC" : "not basing yet"}
          </div>
        </div>
        <div className="basing-stat basing-ohlv">
          <div className="basing-ohlv-line" title="Today's open price">
            <span className="basing-ohlv-key">Open:</span>
            <span className="basing-ohlv-val">{fmt$(data.session_open)}</span>
          </div>
          <div className="basing-ohlv-line" title="Today's intraday high so far">
            <span className="basing-ohlv-key">High:</span>
            <span className="basing-ohlv-val up">
              {fmt$(data.session_high)}
              {data.prev_close && data.session_high
                ? <span className="basing-ohlv-pct"> ({((data.session_high - data.prev_close) / data.prev_close * 100).toFixed(2)}%)</span>
                : null}
            </span>
          </div>
          <div className="basing-ohlv-line" title="Today's intraday low so far">
            <span className="basing-ohlv-key">Low:</span>
            <span className="basing-ohlv-val down">
              {fmt$(data.session_low)}
              {data.prev_close && data.session_low
                ? <span className="basing-ohlv-pct"> ({((data.session_low - data.prev_close) / data.prev_close * 100).toFixed(2)}%)</span>
                : null}
            </span>
          </div>
          <div className="basing-ohlv-line" title="Total shares traded so far today">
            <span className="basing-ohlv-key">Volume:</span>
            <span className="basing-ohlv-val">{(data.session_volume || 0).toLocaleString()}</span>
          </div>
          <div className="basing-ohlv-line" title="Today's percent change from yesterday's close">
            <span className="basing-ohlv-key">Change:</span>
            <span className={`basing-ohlv-val ${livePct >= 0 ? "up" : "down"}`}>
              {fmtPct(livePct)}
            </span>
          </div>
        </div>
      </div>

      {/* Section 2: price-level heatmap — when did price visit each level */}
      {bins.length > 0 ? (
        <div className="basing-profile">
          {/* Header row: title + Time/Volume toggle + overlay toggle */}
          <div className="basing-profile-header">
            <div className="basing-profile-title">
              {viewMode === "time" ? "Time at price" : "Volume at price"}
              {showOverlay && viewMode === "time" ? " · with volume overlay" : ""}
              {" · today (15-min cells across session)"}
            </div>
            <div className="basing-toolbar">
              <div className="basing-toggle">
                <button className={viewMode === "time" ? "active" : ""}
                        onClick={() => setViewMode("time")}
                        title="Show minutes spent at each price">Time</button>
                <button className={viewMode === "volume" ? "active" : ""}
                        onClick={() => setViewMode("volume")}
                        title="Show shares traded at each price">Volume</button>
              </div>
              <button className={`basing-overlay-switch${showOverlay ? " on" : ""}`}
                      onClick={() => setShowOverlay(o => !o)}
                      title={viewMode === "time"
                        ? "Overlay total volume per price level"
                        : "Overlay total time per price level"}>
                <span className="basing-overlay-switch-label">Overlay</span>
                <span className="basing-overlay-switch-track">
                  <span className="basing-overlay-switch-knob"></span>
                </span>
              </button>
            </div>
          </div>
          <div className="basing-heatmap-timeaxis">
            <div className="basing-heatmap-time-spacer"></div>
            <div className="basing-heatmap-time-track">
              {/* Labels live in actual grid cells matching the heatmap
               * cell grid below (26 cols, 1px gap). This guarantees the
               * label sits over the correct cell regardless of width.
               * Labels mark the START of each hour, except 4:00 which
               * marks the END of the session (right edge of last cell). */}
              <span style={{gridColumn: "1"}}>9:30</span>
              <span style={{gridColumn: "3"}}>10:00</span>
              <span style={{gridColumn: "7"}}>11:00</span>
              <span style={{gridColumn: "11"}}>12:00</span>
              <span style={{gridColumn: "15"}}>1:00</span>
              <span style={{gridColumn: "19"}}>2:00</span>
              <span style={{gridColumn: "23"}}>3:00</span>
              <span style={{gridColumn: "26", justifySelf: "end"}}>4:00</span>
            </div>
            <div className="basing-heatmap-marker-spacer"></div>
          </div>
          <div className="basing-profile-rows">
            {(() => {
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
                  if (d < bestDist) { bestDist = d; closestIdx = k; }
                }
              }
              return binsTopDown.map((b, i) => {
                const isPOC = data.poc_price && Math.abs(b.price - data.poc_price) < 0.0001;
                const isTPO = data.tpo_price && Math.abs(b.price - data.tpo_price) < 0.0001 && !isPOC;
                const inVA  = data.value_area_low != null && data.value_area_high != null
                  && b.price >= data.value_area_low && b.price <= data.value_area_high;
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
                const overlayPct = showOverlay ? (rowOverlayTotal / maxOverlayTotal) * 100 : 0;
                const overlayLabel = viewMode === "volume"
                  ? `${rowOverlayTotal.toFixed(1)} min total`
                  : `${Math.round(rowOverlayTotal).toLocaleString()} shares total`;
                return (
                  <div key={i} className={`basing-profile-row${inVA ? " in-va" : ""}${isPOC ? " is-poc" : ""}${isTPO ? " is-tpo" : ""}${isCurrent ? " is-current" : ""}`}>
                    <div className="basing-profile-price">{fmt$(b.price)}</div>
                    <div className="basing-heatmap-cells">
                      {showOverlay && rowOverlayTotal > 0 && (
                        <div className="basing-row-overlay"
                             style={{ width: `${Math.max(1, overlayPct)}%` }}
                             title={`${fmt$(b.price)} · ${overlayLabel}`} />
                      )}
                      {heat.map((v, j) => {
                        const ratio = v / maxHeat;
                        const opacity = ratio === 0 ? 0 : Math.max(0.08, ratio);
                        const totalMins = 9 * 60 + 30 + j * 15;
                        const hh = Math.floor(totalMins / 60);
                        const mm = totalMins % 60;
                        const clock = `${hh > 12 ? hh - 12 : hh}:${mm.toString().padStart(2, "0")} ${hh >= 12 ? "PM" : "AM"}`;
                        const primaryStr = viewMode === "volume"
                          ? `${(v).toLocaleString()} shares`
                          : `${v.toFixed(1)} min`;
                        const tip = v > 0 ? `${clock} · ${primaryStr} at ${fmt$(b.price)}` : `${clock} · no activity`;
                        return (
                          <div key={j}
                               className="basing-heatmap-cell"
                               style={{
                                 backgroundColor: opacity > 0
                                   ? `rgba(29, 158, 117, ${opacity})`
                                   : "transparent",
                               }}
                               title={tip} />
                        );
                      })}
                    </div>
                    <div className="basing-profile-marker">
                      {isPOC && <span className="basing-tag tag-poc" title="Point of Control — price level with the most volume traded today">POC</span>}
                      {isTPO && <span className="basing-tag tag-tpo" title="Time Price Opportunity — price level where price spent the most time today">TPO</span>}
                      {isCurrent && <span className="basing-tag tag-now" title="Current price">●</span>}
                    </div>
                  </div>
                );
              });
            })()}
          </div>
          <div className="basing-legend">
            <span><i className="legend-sw legend-heat-light"></i>brief {viewMode === "volume" ? "trading" : "visit"}</span>
            <span><i className="legend-sw legend-heat-mid"></i>some {viewMode === "volume" ? "volume" : "time"}</span>
            <span><i className="legend-sw legend-heat-dark"></i>most {viewMode === "volume" ? "volume" : "time"}</span>
            <span><i className="legend-sw legend-va"></i>70% value area</span>
            {showOverlay && (
              <span><i className="legend-sw legend-overlay"></i>{viewMode === "volume" ? "time" : "volume"} overlay</span>
            )}
          </div>
        </div>
      ) : (
        <div className="muted" style={{padding: "16px 0"}}>No intraday data yet.</div>
      )}

      {/* Section 3: levels summary */}
      <div className="basing-levels">
        <div title="Point of Control — price level with the most volume traded today"><span className="basing-levels-label">POC</span><span className="basing-levels-val">{fmt$(data.poc_price)}</span></div>
        <div title="Time Price Opportunity — price level where price spent the most time today"><span className="basing-levels-label">TPO</span><span className="basing-levels-val">{fmt$(data.tpo_price)}</span></div>
        <div title="Value Area High — top of the 70% volume zone"><span className="basing-levels-label">VAH</span><span className="basing-levels-val">{fmt$(data.value_area_high)}</span></div>
        <div title="Value Area Low — bottom of the 70% volume zone"><span className="basing-levels-label">VAL</span><span className="basing-levels-val">{fmt$(data.value_area_low)}</span></div>
        <div title="Current live price"><span className="basing-levels-label">Now</span><span className="basing-levels-val">{fmt$(livePrice ?? data.last_price)}</span></div>
      </div>
    </div>
  );
}

function Recommendation({ rec }) {
  const icons = { success: "✓", warn: "!", info: "i", danger: "⚠" };
  return (
    <div className={`rec ${rec.kind}`}>
      <div className="icon">{icons[rec.kind]}</div>
      <div>
        <div className="title">{rec.title}</div>
        <div className="body">{rec.body}</div>
      </div>
    </div>
  );
}

function RecommendationPair({ rec, strategyMode }) {
  const mode = strategyMode || "both";
  const showCC = mode === "both" || mode === "cc";
  const showCSP = mode === "both" || mode === "csp";
  const icons = { success: "✓", warn: "!", info: "i", danger: "⚠" };
  const cc = rec && rec.cc ? rec.cc : { kind: rec?.kind || "info", title: rec?.title || "", body: rec?.body || "" };
  const csp = rec && rec.csp ? rec.csp : null;
  return (
    <div className="rec-pair">
      {showCC && (
        <div className={`rec rec-with-kicker ${cc.kind}`}
             title="Timing verdict for selling covered calls. Combines weekly price-vs-median historicals with the analyst overlay (fresh upgrades, target proximity, trend).">
          <div className="rec-kicker">For covered calls</div>
          <div className="rec-row">
            <div className="icon">{icons[cc.kind]}</div>
            <div>
              <div className="title">{cc.title}</div>
              <div className="body">{cc.body}</div>
            </div>
          </div>
        </div>
      )}
      {showCSP && csp && (
        <div className={`rec rec-with-kicker ${csp.kind}`}
             title="Timing verdict for selling cash-secured puts. Mirrors the CC engine with inverted directional bias: weakness favors short puts (rich premium, bounce bias), strength means wait. Analyst overlay also flipped: fresh upgrade reduces danger, fresh downgrade escalates it.">
          <div className="rec-kicker">For cash-secured puts</div>
          <div className="rec-row">
            <div className="icon">{icons[csp.kind]}</div>
            <div>
              <div className="title">{csp.title}</div>
              <div className="body">{csp.body}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StrategyCard({ rank, score, reason, tag, name, termKey, structure, stats, note, tone, legs, frontExpLabel, backExpLabel, frontDte, selected, onSelect, Term }) {
  const toneColor = tone === "up" ? "var(--up)" : tone === "down" ? "var(--down)" : tone === "warn" ? "var(--warn)" : "var(--accent)";
  const isTop3 = rank <= 3;
  // Format each leg into a concrete trade ticket line.
  const tradeLines = (legs || []).map(L => {
    const action = L.qty < 0 ? "SELL" : "BUY";
    const qty = Math.abs(L.qty / 100);  // contracts (or 100-share blocks for stock)
    const side = (L.type || "").toUpperCase();
    const isStock = side === "STOCK";
    // Front vs back: front_dte ± a few days = front, otherwise back.
    const isFront = !frontDte || Math.abs((L.dte || 0) - frontDte) <= 7;
    const expLabel = isStock ? "" : (isFront ? frontExpLabel : backExpLabel);
    const dteText = isStock ? "" : (L.dte != null ? `${L.dte}d` : "");
    return {
      action,
      qty,
      side,
      strike: L.strike,
      expLabel,
      dteText,
      premium: L.premium,
    };
  });
  return (
    <div className={`strat-card ${selected ? "selected" : ""} ${isTop3 ? `top-${rank}` : ""}`} style={{borderTop: `2px solid ${toneColor}`}}>
      <div className="strat-head">
        <span className={`strat-rank ${isTop3 ? "top" : ""}`}>#{rank}</span>
        <span className="strat-fit" title={`fit score ${score} of 100`}>
          <span className="strat-fit-bar"><span className="strat-fit-bar-fill" style={{width: `${score}%`, background: toneColor}}></span></span>
          <span className="strat-fit-num">{score}</span>
        </span>
        <span className="strat-tag" style={{color: toneColor, borderColor: `color-mix(in oklch, ${toneColor}, transparent 70%)`}}>{tag}</span>
      </div>
      <div className="strat-name">
        {Term && termKey ? <Term k={termKey}>{name}</Term> : name}
      </div>
      {reason && <div className="strat-why">{reason}</div>}
      {tradeLines.length > 0 && (
        <div className="strat-legs">
          {tradeLines.map((L, i) => (
            <div key={i} className={`strat-leg ${L.action === "SELL" ? "sell" : "buy"}`}>
              <span className="leg-action">{L.action}</span>
              {L.qty !== 1 && <span className="leg-qty">{L.qty}×</span>}
              <span className={`leg-side ${L.side === "CALL" ? "call" : L.side === "PUT" ? "put" : "stock"}`}>{L.side}</span>
              <span className="leg-strike">{L.side === "STOCK" ? "" : `$${L.strike.toFixed(2)}`}</span>
              <span className="leg-exp">{L.expLabel}{L.dteText ? ` · ${L.dteText}` : ""}</span>
              {L.premium != null && L.premium > 0 && (
                <span className="leg-prem">${L.premium.toFixed(2)}</span>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="strat-stats">
        {stats.map(([k, v]) => (
          <div key={k} className="strat-stat"><span>{k}</span><b>{v}</b></div>
        ))}
      </div>
      <div className="strat-note">{note}</div>
      <button className="strat-pl-btn" onClick={onSelect}>
        {selected ? "● Showing P/L" : "Show P/L →"}
      </button>
    </div>
  );
}

function PositionsCard({ positions, setPositions, showAdd, setShowAdd, filter, setFilter, ticker, currentPrice, calls, puts, activeExpDate, sugCall, sugPut, callAtSug, putAtSug, FRONT_DTE, Term, fmt$, apiFetch }) {
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
      return { currentPremium: p.closedPremium, pnl, pnlPct: pnl / Math.max(0.01, Math.abs(p.qty * p.entryPremium)) * 100, dte: 0, status: "closed" };
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
        const mid = row.bid > 0 ? (row.bid + row.ask) / 2 : (row.last || row.ask || 0);
        currentPremium = mid;
        status = "live";
      } else if (bsPrice && p.iv && dte > 0) {
        const T = dte / 365.0;
        currentPremium = bsPrice(currentPrice, p.strike, T, p.iv, p.type === "call");
        status = "estimate";
      }
    }
    let pnl = 0, pnlPct = 0;
    if (currentPremium != null) {
      pnl = p.qty * (currentPremium - (p.entryPremium ?? 0));
      const cost = Math.abs(p.qty * (p.entryPremium ?? 0));
      pnlPct = cost > 0 ? (pnl / cost) * 100 : 0;
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
    if (!p.closed && p.type !== "stock" && p.qty < 0
        && currentDelta != null && dte > 0 && dte <= 7
        && Math.abs(currentDelta) >= 0.40) {
      rollFlag = `Approaching assignment: |Δ|=${Math.abs(currentDelta).toFixed(2)} with ${dte}d left. Consider rolling out.`;
    }
    return { currentPremium, pnl, pnlPct, dte, status, currentDelta, rollFlag };
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker: p.ticker,
          position_id: p.id,
          strike: p.strike,
          expiration: p.expiration,
          dte: v.dte,
          delta: v.currentDelta != null ? Math.abs(v.currentDelta).toFixed(2) : null,
          type: p.type,
        }),
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
  }, { total: 0 });

  function addPosition(p) {
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    setPositions(list => [...list, { ...p, id, closed: false, entryDate: p.entryDate || new Date().toISOString().slice(0, 10) }]);
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
    if (isNaN(closedPremium)) { alert("Invalid number."); return; }
    setPositions(list => list.map(x => x.id === p.id ? {
      ...x, closed: true, closedPremium, closedDate: new Date().toISOString().slice(0, 10)
    } : x));
  }

  return (
    <div className="card" style={{marginBottom: "var(--row-gap)"}}>
      <div className="card-head">
        <div>
          <div className="kicker">My positions</div>
          <div className="card-title">
            {visible.length === 0
              ? "No positions logged"
              : `${visible.filter(p => !p.closed).length} open · live P/L `}
            {visible.length > 0 && (
              <span className={aggPnl.total >= 0 ? "up" : "down"} style={{fontFamily: "var(--font-mono)"}}>
                {aggPnl.total >= 0 ? "+" : ""}${aggPnl.total.toFixed(2)}
              </span>
            )}
          </div>
        </div>
        <div className="pos-toolbar">
          <div className="seg">
            <button className={filter === "open" ? "active" : ""} onClick={() => setFilter("open")}>Open</button>
            <button className={filter === "this" ? "active" : ""} onClick={() => setFilter("this")}>{ticker}</button>
            <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>All</button>
          </div>
          <button className="pos-add-btn" onClick={() => setShowAdd(s => !s)}>
            {showAdd ? "× Cancel" : "+ Add position"}
          </button>
        </div>
      </div>

      {showAdd && (
        <AddPositionForm
          ticker={ticker}
          activeExpDate={activeExpDate}
          sugCall={sugCall}
          sugPut={sugPut}
          callAtSug={callAtSug}
          putAtSug={putAtSug}
          FRONT_DTE={FRONT_DTE}
          onAdd={addPosition}
          onCancel={() => setShowAdd(false)}
        />
      )}

      {visible.length === 0 && !showAdd && (
        <div className="pos-empty">
          <div>Log a position to track live P&amp;L, days to expiration, and net Greeks.</div>
          <button className="pos-add-btn" onClick={() => setShowAdd(true)}>+ Add your first position</button>
        </div>
      )}

      {visible.length > 0 && (
        <div className="pos-list">
          {visible.map(p => {
            const v = valuate(p);
            const sideLabel = p.qty < 0 ? "SHORT" : "LONG";
            const typeLabel = (p.type || "").toUpperCase();
            const expLabel = p.expiration
              ? new Date(p.expiration + "T16:00:00").toLocaleDateString("en-US", {month: "short", day: "numeric"})
              : "";
            return (
              <div key={p.id} className={`pos-row ${p.closed ? "closed" : ""}`}>
                <div className="pos-row-main">
                  <div className="pos-line1">
                    <span className="pos-tk">{p.ticker}</span>
                    <span className={`pos-side ${p.qty < 0 ? "short" : "long"}`}>{sideLabel}</span>
                    <span className={`pos-type ${typeLabel === "CALL" ? "call" : typeLabel === "PUT" ? "put" : "stock"}`}>{typeLabel}</span>
                    {p.strike != null && <span className="pos-k">${p.strike.toFixed(2)}</span>}
                    {expLabel && <span className="pos-exp">{expLabel}</span>}
                    {!p.closed && v.dte != null && p.expiration && <span className="pos-dte">{v.dte}d</span>}
                    <span className="pos-qty">{Math.abs(p.qty / (p.type === "stock" ? 1 : 100))}× {p.type === "stock" ? "shares" : "ctr"}</span>
                  </div>
                  <div className="pos-line2">
                    <span>Entry <b>${(p.entryPremium ?? 0).toFixed(2)}</b></span>
                    {v.currentPremium != null && (
                      <span>Now <b>${v.currentPremium.toFixed(2)}</b></span>
                    )}
                    {v.currentDelta != null && (
                      <span title="Live |delta| of the position. For short OTM options 0.20-0.30 is the entry zone; > 0.40 with low DTE is a roll trigger.">
                        |Δ| <b>{Math.abs(v.currentDelta).toFixed(2)}</b>
                      </span>
                    )}
                    <span className="pos-status">{v.status === "live" ? "● live" : v.status === "estimate" ? "○ estimate" : v.status === "closed" ? "✓ closed" : "load " + p.ticker}</span>
                  </div>
                  {v.rollFlag && (
                    <div className="pos-roll-flag" title="Position is approaching in-the-money near expiration. Common short-options heuristic: roll out (and possibly down for puts, up for calls) when DTE < 7 and |Δ| > 0.40 to defer assignment and collect more premium.">
                      ⚠ {v.rollFlag}
                    </div>
                  )}
                </div>
                <div className="pos-row-pnl">
                  {v.currentPremium != null ? (
                    <>
                      <div className={`pos-pnl ${v.pnl >= 0 ? "up" : "down"}`}>
                        {v.pnl >= 0 ? "+" : ""}${v.pnl.toFixed(2)}
                      </div>
                      <div className={`pos-pnl-pct ${v.pnl >= 0 ? "up" : "down"}`}>
                        {v.pnl >= 0 ? "+" : ""}{v.pnlPct.toFixed(1)}%
                      </div>
                    </>
                  ) : (
                    <div className="pos-pnl-na">—</div>
                  )}
                </div>
                <div className="pos-row-actions">
                  {!p.closed && (
                    <button className="pos-action" onClick={() => closePosition(p)}>Close</button>
                  )}
                  <button className="pos-action danger" onClick={() => deletePosition(p.id)}>×</button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AddPositionForm({ ticker, activeExpDate, sugCall, sugPut, callAtSug, putAtSug, FRONT_DTE, onAdd, onCancel }) {
  const [form, setForm] = React.useState({
    ticker: ticker,
    type: "call",
    side: "short",   // long | short
    qty: 1,          // number of contracts (or 100-share blocks for stock)
    strike: "",
    entryPremium: "",
    expiration: activeExpDate.toISOString().slice(0, 10),
    iv: "",
  });

  function setField(k, v) { setForm(f => ({ ...f, [k]: v })); }

  function fillFromSuggestion(side) {
    if (side === "call") {
      setForm(f => ({
        ...f, type: "call", side: "short", qty: 1,
        strike: sugCall.toFixed(2),
        entryPremium: ((callAtSug.bid + callAtSug.ask) / 2 || callAtSug.last || 0).toFixed(2),
        expiration: activeExpDate.toISOString().slice(0, 10),
        iv: callAtSug.iv ? callAtSug.iv.toFixed(3) : "",
      }));
    } else {
      setForm(f => ({
        ...f, type: "put", side: "short", qty: 1,
        strike: sugPut.toFixed(2),
        entryPremium: ((putAtSug.bid + putAtSug.ask) / 2 || putAtSug.last || 0).toFixed(2),
        expiration: activeExpDate.toISOString().slice(0, 10),
        iv: putAtSug.iv ? putAtSug.iv.toFixed(3) : "",
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
      iv: form.iv ? parseFloat(form.iv) : null,
    });
  }

  return (
    <div className="pos-add-form">
      <div className="pos-form-quick">
        Quick fill from current setup:
        <button className="pos-quick-btn" onClick={() => fillFromSuggestion("call")}>Suggested call (${sugCall.toFixed(2)})</button>
        <button className="pos-quick-btn" onClick={() => fillFromSuggestion("put")}>Suggested put (${sugPut.toFixed(2)})</button>
      </div>
      <div className="pos-form-grid">
        <label>
          <span>Ticker</span>
          <input value={form.ticker} onChange={e => setField("ticker", e.target.value.toUpperCase())} />
        </label>
        <label>
          <span>Type</span>
          <select value={form.type} onChange={e => setField("type", e.target.value)}>
            <option value="call">Call</option>
            <option value="put">Put</option>
            <option value="stock">Stock</option>
          </select>
        </label>
        <label>
          <span>Side</span>
          <select value={form.side} onChange={e => setField("side", e.target.value)}>
            <option value="short">Short / Sold</option>
            <option value="long">Long / Bought</option>
          </select>
        </label>
        <label>
          <span>Qty ({form.type === "stock" ? "shares × 100" : "contracts"})</span>
          <input type="number" min="1" step="1" value={form.qty} onChange={e => setField("qty", e.target.value)} />
        </label>
        {form.type !== "stock" && (
          <label>
            <span>Strike</span>
            <input type="number" step="0.5" value={form.strike} onChange={e => setField("strike", e.target.value)} />
          </label>
        )}
        <label>
          <span>Entry premium {form.type === "stock" ? "(share price)" : "($/share)"}</span>
          <input type="number" step="0.01" value={form.entryPremium} onChange={e => setField("entryPremium", e.target.value)} />
        </label>
        {form.type !== "stock" && (
          <label>
            <span>Expiration</span>
            <input type="date" value={form.expiration} onChange={e => setField("expiration", e.target.value)} />
          </label>
        )}
        {form.type !== "stock" && (
          <label>
            <span>IV at entry (optional, e.g. 0.30)</span>
            <input type="number" step="0.01" min="0" max="5" value={form.iv} onChange={e => setField("iv", e.target.value)} placeholder="for BS pricing" />
          </label>
        )}
      </div>
      <div className="pos-form-actions">
        <button className="pos-add-btn primary" onClick={submit}>Add position</button>
        <button className="pos-add-btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

Object.assign(window, { TickerLogo, VolSkewCard, SwingPatternCard, ScreenersHub, AnalystBoardCard, MoversCard, TrendCard, IVRankCard, WatchlistAlertsCard, TabBar, TabPanel, WeatherBadge, LevelRepriceCard, WinRateCard, EarningsCrushCard, PushSettingsCard, BrokerImportCard, StrategyReferenceCard, WatchlistManager, QuickAddRow, WatchlistRow, FlashOnChange, SortableTh, PercentCalc, RollManagerCard, FlowScoreCard, PullbackBacktest, TradeBuilderCard, AnalystCard, PullbackProfileCard, BasingCard, Recommendation, RecommendationPair, StrategyCard, PositionsCard, AddPositionForm });
