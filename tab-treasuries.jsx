// tab-treasuries.jsx — LAZY CHUNK (v3.64), split from app-cards.jsx.
// US Treasuries rates terminal; loaded on first Treasuries-tab open.

// ═══════════════════════════════════════════════════════════════════════════
// US TREASURIES TAB (v3.59) — rates terminal for a stock & options trader.
// Data: /api/treasury/* (Treasury.gov, FRED, TreasuryDirect, CFTC official;
// Yahoo delayed for MOVE/futures/ETFs). Anything a source can't provide
// renders "Data unavailable" — nothing is estimated in its place.
// ═══════════════════════════════════════════════════════════════════════════

const TSY_TENORS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"];
const TSY_KEY4 = { "2Y": 1, "5Y": 1, "10Y": 1, "30Y": 1 };

function TsyNA({ why }) {
  return <span className="tsy-na" title={why || "This field's source has no reliable value right now — nothing is estimated in its place."}>Data unavailable</span>;
}
// Yield-move coloring: red = yields RISING (bond prices falling), green =
// yields FALLING (bond prices rising). Every use carries the inverse-price tooltip.
function tsyBpCls(v) { return v == null ? "" : v > 0.05 ? "cd" : v < -0.05 ? "cu" : ""; }
const TSY_INV = "Yields and bond PRICES move in opposite directions: red = yield up = Treasury prices down.";
function TsyBp({ v, d = 1 }) {
  if (v == null) return <span className="tsy-na">—</span>;
  return <span className={`num ${tsyBpCls(v)}`} title={TSY_INV}>{v >= 0 ? "+" : ""}{v.toFixed(d)} bp</span>;
}
function TsyFoot({ src, at, delayed }) {
  return (
    <div className="tsy-foot">
      Source: {src}{at ? ` · updated ${at}` : ""}{delayed ? " · delayed" : ""}
    </div>
  );
}
function useTsy(apiFetch, section, ttl) {
  const [st, setSt] = useState({ d: null, err: null, loading: true });
  const load = () => {
    sharedJson(apiFetch, `/api/treasury/${section}`, ttl)
      .then(d => setSt({ d, err: d && d.error && !d.ok ? d.error : null, loading: false }))
      .catch(e => setSt({ d: null, err: String(e), loading: false }));
  };
  useEffect(() => { load(); }, []);
  return { ...st, retry: load };
}
function TsyLoading() { return <div className="tsy-loading"><span className="skel skel-line" style={{ width: "60%" }}></span><span className="skel skel-line" style={{ width: "85%" }}></span><span className="skel skel-line" style={{ width: "40%" }}></span></div>; }
function TsyErr({ err, retry }) {
  return <div className="tsy-err">Failed to load — {String(err).slice(0, 120)} <button type="button" onClick={retry}>Retry</button></div>;
}
// Collapsed-by-default heavy section: children mount (and fetch) on expand.
function TsyFold({ kicker, title, hint, children, defaultOpen }) {
  const [open, setOpen] = useState(!!defaultOpen);
  return (
    <div className="card tsy-card">
      <button type="button" className="tsy-foldhead" onClick={() => setOpen(o => !o)} aria-expanded={open}>
        <div>
          <div className="kicker">{kicker}</div>
          <div className="card-title">{title}</div>
        </div>
        <span className="tsy-foldarrow">{open ? "▾" : "▸"}{!open && hint ? <em>{hint}</em> : null}</span>
      </button>
      {open && children}
    </div>
  );
}

/* ── 1. Maturity cards ─────────────────────────────────────────────────── */
function TsyYieldCards({ core }) {
  if (core.loading) return <div className="card tsy-card"><TsyLoading /></div>;
  if (!core.d || !core.d.ok) return <div className="card tsy-card"><div className="kicker">Treasury market summary</div><TsyErr err={core.err || "no data"} retry={core.retry} /></div>;
  const cards = core.d.yields || [];
  return (
    <div className="card tsy-card">
      <div className="card-head">
        <div>
          <div className="kicker">Treasury market summary · official EOD curve</div>
          <div className="card-title">Yields by maturity</div>
        </div>
        <span className="tsy-datechip num" title="U.S. Treasury publishes the daily par yield curve after each trading day.">{core.d.curve_date}</span>
      </div>
      <div className="tsy-cards">
        {cards.map(c => {
          const span = c.hi52w - c.lo52w;
          const pos = span > 0 ? Math.max(0, Math.min(100, (c.yield - c.lo52w) / span * 100)) : 50;
          return (
            <div key={c.tenor} className={`tsy-ycard ${TSY_KEY4[c.tenor] ? "key" : ""}`}
                 title={`${c.tenor} Treasury par yield ${c.yield.toFixed(2)}% (as of ${core.d.curve_date}).\n52-week range ${c.lo52w.toFixed(2)}–${c.hi52w.toFixed(2)}%, currently the ${c.pct52w != null ? c.pct52w.toFixed(0) + "th percentile" : "—"}.\n${c.key ? "Why it matters: " + c.key + ".\n" : ""}${TSY_INV}`}>
              <div className="tsy-ycard-t">{c.tenor}{c.key && <i title={c.key}>★</i>}</div>
              <div className="tsy-ycard-y num">{c.yield.toFixed(2)}%</div>
              <div className="tsy-ycard-chg">
                <span className={`num ${tsyBpCls(c.bp1d)}`}>{c.bp1d != null ? `${c.bp1d >= 0 ? "+" : ""}${c.bp1d.toFixed(0)}` : "—"}<em>1d</em></span>
                <span className={`num ${tsyBpCls(c.bp5d)}`}>{c.bp5d != null ? `${c.bp5d >= 0 ? "+" : ""}${c.bp5d.toFixed(0)}` : "—"}<em>5d</em></span>
                <span className={`num ${tsyBpCls(c.bp21d)}`}>{c.bp21d != null ? `${c.bp21d >= 0 ? "+" : ""}${c.bp21d.toFixed(0)}` : "—"}<em>1m</em></span>
              </div>
              <div className="tsy-52bar"><i style={{ left: `${pos}%` }}></i></div>
              <div className="tsy-52lbl num"><span>{c.lo52w.toFixed(2)}</span><span>{c.pct52w != null ? `${c.pct52w.toFixed(0)}%ile` : "—"}</span><span>{c.hi52w.toFixed(2)}</span></div>
            </div>
          );
        })}
      </div>
      <TsyFoot src={core.d.source} at={core.d.curve_date} />
    </div>
  );
}

/* ── 2. Yield curve chart ──────────────────────────────────────────────── */
function TsyCurveSvg({ snaps, cmp }) {
  const W = 820, H = 280, L = 46, R = 12, T = 14, B = 28;
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
  return (
    <svg className="tsy-curvesvg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Treasury yield curve">
      {ticks.map(v => (
        <g key={v}>
          <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="tsy-grid" />
          <text x={L - 6} y={y(v) + 3.5} className="tsy-axis" textAnchor="end">{v.toFixed(2)}</text>
        </g>
      ))}
      {ts.map((t, i) => <text key={t} x={x(i)} y={H - 8} className="tsy-axis" textAnchor="middle">{t}</text>)}
      {old && <path d={path(old)} className="tsy-line-old" />}
      <path d={path(cur)} className="tsy-line-cur" />
      {ts.map((t, i) => (
        <circle key={t} cx={x(i)} cy={y(cur[t])} r="4" className="tsy-dot">
          <title>{`${t}: ${cur[t].toFixed(2)}%${old && old[t] != null ? `\n${cmp} ago: ${old[t].toFixed(2)}% → ${((cur[t] - old[t]) * 100).toFixed(0)} bp change` : ""}`}</title>
        </circle>
      ))}
    </svg>
  );
}
function TsyCurveCard({ core }) {
  const [cmp, setCmp] = useState("1m");
  const [view, setView] = useState("chart");
  if (core.loading) return <div className="card tsy-card"><TsyLoading /></div>;
  if (!core.d || !core.d.ok) return null;
  const snaps = core.d.snapshots || {};
  const reg = core.d.regime, mv = core.d.curve_moves;
  const cmps = [["1d", "1 day"], ["1w", "1 week"], ["1m", "1 month"], ["3m", "3 months"], ["1y", "1 year"]];
  return (
    <div className="card tsy-card">
      <div className="card-head">
        <div>
          <div className="kicker">Yield curve · all maturities</div>
          <div className="card-title">Treasury yield curve</div>
        </div>
        <div className="tsy-ctrl">
          <select className="sb-select" value={cmp} onChange={e => setCmp(e.target.value)} title="Overlay the curve as of this long ago (dashed).">
            <option value="none">No compare</option>
            {cmps.map(([k, l]) => snaps[k] ? <option key={k} value={k}>vs {l} ago</option> : null)}
          </select>
          <div className="tsy-toggle">
            <button type="button" className={view === "chart" ? "on" : ""} onClick={() => setView("chart")}>Chart</button>
            <button type="button" className={view === "table" ? "on" : ""} onClick={() => setView("table")}>Table</button>
          </div>
        </div>
      </div>
      {reg && (
        <div className="tsy-regime" title={`Classified from the 5-day change: 2y ${reg.d2y_bp >= 0 ? "+" : ""}${reg.d2y_bp} bp, 10y ${reg.d10y_bp >= 0 ? "+" : ""}${reg.d10y_bp} bp → slope ${reg.slope_chg_bp >= 0 ? "+" : ""}${reg.slope_chg_bp} bp. "Bull" = yields falling (prices rallying), "bear" = yields rising. Steepener = long end rising vs short end.`}>
          {core.d.curve_shape && (
            <span title={`Curve shape from today's official curve: ${core.d.curve_shape.detail}.`}>
              SHAPE <b className={core.d.curve_shape.label.startsWith("inverted") || core.d.curve_shape.label.startsWith("partially") ? "cd" : "cu"}>{core.d.curve_shape.label.toUpperCase()}</b> ·
            </span>
          )}
          <b className={reg.label.startsWith("bull") ? "cu" : reg.label.startsWith("bear") ? "cd" : ""}>{reg.label.toUpperCase()}</b>
          <span>2y <TsyBp v={reg.d2y_bp} /> · 10y <TsyBp v={reg.d10y_bp} /> over {reg.window}</span>
          {mv && mv.biggest && <span>· biggest mover <b className="num">{mv.biggest.tenor}</b> <TsyBp v={mv.biggest.bp5d} /></span>}
          {mv && <span>· front end <TsyBp v={mv.front_avg_bp5d} /> / long end <TsyBp v={mv.long_avg_bp5d} /></span>}
        </div>
      )}
      {view === "chart" ? (
        <TsyCurveSvg snaps={snaps} cmp={cmp} />
      ) : (
        <div className="tsy-tablewrap">
          <table className="tsy-table">
            <thead><tr><th>Maturity</th><th>Now</th>{cmps.map(([k, l]) => snaps[k] ? <th key={k}>{l} ago</th> : null)}<th>Δ vs {cmp !== "none" ? cmp : "—"}</th></tr></thead>
            <tbody>
              {TSY_TENORS.filter(t => snaps.current && snaps.current.points[t] != null).map(t => {
                const cur = snaps.current.points[t];
                const oldv = cmp !== "none" && snaps[cmp] ? snaps[cmp].points[t] : null;
                return (
                  <tr key={t}>
                    <td className="num">{t}</td>
                    <td className="num"><b>{cur.toFixed(2)}%</b></td>
                    {cmps.map(([k]) => snaps[k] ? <td key={k} className="num">{snaps[k].points[t] != null ? snaps[k].points[t].toFixed(2) : "—"}</td> : null)}
                    <td>{oldv != null ? <TsyBp v={(cur - oldv) * 100} d={0} /> : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <TsyFoot src={core.d.source} at={core.d.curve_date} />
    </div>
  );
}

/* ── 3. Spreads ────────────────────────────────────────────────────────── */
function TsySpreadsCard({ core }) {
  if (core.loading || !core.d || !core.d.ok) return null;
  const sp = core.d.spreads || [];
  return (
    <div className="card tsy-card">
      <div className="card-head">
        <div>
          <div className="kicker">Curve spreads · positive = normal slope, negative = inverted</div>
          <div className="card-title">Important Treasury spreads</div>
        </div>
      </div>
      <div className="tsy-tablewrap">
        <table className="tsy-table">
          <thead><tr><th>Spread</th><th>Now</th><th>1d</th><th>1w</th><th>1m</th><th>%ile (3y)</th><th>State</th><th>Direction</th></tr></thead>
          <tbody>
            {sp.map(s => (
              <tr key={s.key} title={s.note || `${s.label}. Percentile over ~3 years of daily history. Direction from the 1-week change.`}>
                <td>{s.label}</td>
                <td className="num"><b className={s.inverted ? "cd" : ""}>{s.bp >= 0 ? "+" : ""}{s.bp.toFixed(0)} bp</b></td>
                <td><TsyBp v={s.d1} d={0} /></td>
                <td><TsyBp v={s.d5} d={0} /></td>
                <td><TsyBp v={s.d21} d={0} /></td>
                <td className="num">{s.pctile != null ? s.pctile.toFixed(0) : "—"}</td>
                <td>{s.inverted ? <span className="tsy-pill down">INVERTED</span> : <span className="tsy-pill up">POSITIVE</span>}</td>
                <td className="num">{s.trend ? (s.trend === "steepening" ? "↗ steepening" : s.trend === "flattening" ? "↘ flattening" : "→ flat") : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TsyFoot src="U.S. Treasury daily curve; EFFR from FRED" at={core.d.curve_date} />
    </div>
  );
}

/* ── 4. Trader interpretation ──────────────────────────────────────────── */
function TsySignalsCard({ core }) {
  if (core.loading || !core.d || !core.d.ok) return null;
  const sig = core.d.signals || [];
  return (
    <div className="card tsy-card">
      <div className="card-head">
        <div>
          <div className="kicker">Rules-based read · every signal cites the numbers that fired it</div>
          <div className="card-title">What rates imply for your trading</div>
        </div>
      </div>
      <div className="tsy-sigs">
        {sig.map((s, i) => (
          <div key={i} className="tsy-sig">
            <span className={`tsy-sigdot ${s.tone}`}></span>
            <div>
              <div className="tsy-sigl">{s.label} <b className={`tsy-pill ${s.tone === "up" ? "up" : s.tone === "down" ? "down" : "mut"}`}>{s.level}</b></div>
              <div className="tsy-sigd">{s.detail}</div>
            </div>
          </div>
        ))}
      </div>
      <TsyFoot src="Derived from the displayed Treasury/FRED data — fixed rules, no AI summarization" at={core.d.curve_date} />
    </div>
  );
}

/* ── 7. Inflation expectations + decomposition ─────────────────────────── */
function TsyExpectationsCard({ core }) {
  if (core.loading || !core.d || !core.d.ok) return null;
  const e = core.d.expectations || {};
  const dec = core.d.decomposition;
  const rows = [["be5", "5y breakeven"], ["be10", "10y breakeven"], ["f5y5y", "5y5y forward"],
                ["real5", "5y TIPS real"], ["real10", "10y TIPS real"], ["real30", "30y TIPS real"]];
  return (
    <div className="card tsy-card">
      <div className="card-head">
        <div>
          <div className="kicker">Breakevens & TIPS real yields (FRED, daily)</div>
          <div className="card-title">Inflation expectations</div>
        </div>
      </div>
      <div className="tsy-tablewrap">
        <table className="tsy-table">
          <thead><tr><th>Series</th><th>Now</th><th>1d</th><th>1w</th><th>1m</th><th>52w %ile</th></tr></thead>
          <tbody>
            {rows.map(([k, l]) => {
              const s = e[k];
              return (
                <tr key={k}>
                  <td>{l}</td>
                  {s ? (
                    <React.Fragment>
                      <td className="num"><b>{s.value.toFixed(2)}%</b></td>
                      <td><TsyBp v={s.d1 != null ? s.d1 * 100 : null} d={0} /></td>
                      <td><TsyBp v={s.d5 != null ? s.d5 * 100 : null} d={0} /></td>
                      <td><TsyBp v={s.d21 != null ? s.d21 * 100 : null} d={0} /></td>
                      <td className="num">{s.pct52w != null ? s.pct52w.toFixed(0) : "—"}</td>
                    </React.Fragment>
                  ) : <td colSpan="5"><TsyNA /></td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {dec && (
        <div className="tsy-decomp" title="Δ10y nominal = Δ10y TIPS real + Δ10y breakeven (identity, FRED daily closes).">
          Nominal 10y {dec.nominal_bp >= 0 ? "+" : ""}{dec.nominal_bp} bp over {dec.window} = real {dec.real_bp >= 0 ? "+" : ""}{dec.real_bp} bp + breakeven {dec.breakeven_bp >= 0 ? "+" : ""}{dec.breakeven_bp} bp → <b>driven by {dec.verdict}</b>
        </div>
      )}
      <TsyFoot src="FRED T5YIE / T10YIE / T5YIFR / DFII5 / DFII10 / DFII30" />
    </div>
  );
}

/* ── 8. CPI countdown & event risk ─────────────────────────────────────── */
function TsyEventsCard({ core }) {
  if (core.loading || !core.d || !core.d.ok) return null;
  const ev = core.d.events || {};
  const cpi = ev.next_cpi, fomc = ev.next_fomc, jobs = ev.next_jobs;
  return (
    <div className="card tsy-card">
      <div className="card-head">
        <div>
          <div className="kicker">Event risk · scheduled macro catalysts</div>
          <div className="card-title">CPI countdown & upcoming events</div>
        </div>
      </div>
      <div className="tsy-events">
        {cpi && cpi.date && (
          <div className="tsy-cd" title={`Next CPI release per the BLS schedule: ${cpi.date} at ${cpi.time_et}. Consensus estimates need a paid feed — never estimated here.`}>
            <em>NEXT CPI · {cpi.date} · {cpi.time_et}</em>
            {cpi.countdown
              ? <b className="num">{cpi.countdown.days}d {cpi.countdown.hours}h {cpi.countdown.minutes}m</b>
              : <b>—</b>}
            <span>Consensus: <TsyNA why="No free reliable consensus feed — not estimated." /></span>
          </div>
        )}
        <div className="tsy-evrows">
          {fomc && fomc.date && <div className="tsy-evrow"><em>FOMC decision</em><b className="num">{fomc.date}</b><span>{fomc.days} days · {fomc.source}</span></div>}
          {jobs && <div className="tsy-evrow"><em>Employment report</em><b className="num">{jobs.date}</b><span>{jobs.source}</span></div>}
          <div className="tsy-evrow"><em>PPI / PCE</em><b>—</b><span>{ev.note_ppi_pce}</span></div>
        </div>
        {(ev.upcoming_auctions || []).length > 0 && (
          <div className="tsy-upauc">
            <em>UPCOMING TREASURY AUCTIONS</em>
            {(ev.upcoming_auctions || []).slice(0, 8).map((a, i) => (
              <span key={i} className="tsy-aucchip num" title={`${a.term} ${a.type} auction ${a.auction_date}${a.offering ? `, offering $${(a.offering / 1e9).toFixed(0)}B` : ""}`}>
                {a.auction_date && a.auction_date.slice(5)} {a.term} {a.type}
              </span>
            ))}
          </div>
        )}
      </div>
      <TsyFoot src="BLS / Federal Reserve schedules · auctions from TreasuryDirect (official)" />
    </div>
  );
}

/* ── 11. MOVE ──────────────────────────────────────────────────────────── */
function TsyMoveCard({ core }) {
  if (core.loading || !core.d || !core.d.ok) return null;
  const m = core.d.move;
  return (
    <div className="card tsy-card">
      <div className="card-head">
        <div>
          <div className="kicker">Treasury market volatility — NOT the stock-market VIX</div>
          <div className="card-title">MOVE index</div>
        </div>
        {m && <span className={`tsy-pill ${m.regime === "low" || m.regime === "normal" ? "up" : m.regime === "elevated" ? "mut" : "down"}`}>{m.regime.toUpperCase()}</span>}
      </div>
      {m ? (
        <div className="tsy-move">
          <b className="num">{m.value}</b>
          <div className="tsy-move-chg">
            <span>1d <TsyBp v={m.d1} /></span><span>5d <TsyBp v={m.d5} /></span><span>1m <TsyBp v={m.d21} /></span>
            <span>52w %ile <b className="num">{m.pct52w != null ? m.pct52w.toFixed(0) : "—"}</b></span>
          </div>
          <div className="tsy-sigd">MOVE measures implied volatility of Treasury options — rates uncertainty, not equity volatility. Bands: {m.bands}.</div>
          <TsyFoot src={m.source} at={m.date} delayed />
        </div>
      ) : <div style={{ padding: "8px 0" }}><TsyNA why="^MOVE quote source unreachable — not estimated." /></div>}
    </div>
  );
}

/* ── 5. CPI dashboard + trend chart ────────────────────────────────────── */
function TsySeriesSvg({ series, period }) {
  const W = 820, H = 260, L = 46, R = 10, T = 12, B = 24;
  const cut = period === "max" ? 0 : { "1y": 12, "2y": 24, "5y": 60, "10y": 120 }[period] || 24;
  const shown = series.map(s => ({ ...s, pts: cut ? s.pts.slice(-cut) : s.pts })).filter(s => s.pts.length > 1);
  if (!shown.length) return null;
  const all = shown.flatMap(s => s.pts.map(p => p.v));
  const lo = Math.min(...all), hi = Math.max(...all);
  const pad = Math.max(0.2, (hi - lo) * 0.06);
  const y = v => T + (1 - (v - (lo - pad)) / Math.max(0.01, (hi + pad) - (lo - pad))) * (H - T - B);
  const n = Math.max(...shown.map(s => s.pts.length));
  const x = (i, len) => L + (i + (n - len)) / Math.max(1, n - 1) * (W - L - R);
  const step = Math.max(0.5, Math.round((hi - lo + 2 * pad) / 5 * 2) / 2);
  const ticks = [];
  for (let v = Math.ceil((lo - pad) / step) * step; v <= hi + pad; v += step) ticks.push(v);
  const xs = shown[0].pts;
  const xevery = Math.max(1, Math.floor(xs.length / 6));
  return (
    <svg className="tsy-curvesvg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="CPI trend">
      {ticks.map(v => (
        <g key={v}>
          <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="tsy-grid" />
          <text x={L - 6} y={y(v) + 3.5} className="tsy-axis" textAnchor="end">{v.toFixed(1)}</text>
        </g>
      ))}
      {xs.map((p, i) => i % xevery === 0 ? <text key={p.d} x={x(i, xs.length)} y={H - 6} className="tsy-axis" textAnchor="middle">{p.d}</text> : null)}
      <line x1={L} x2={W - R} y1={y(2)} y2={y(2)} className="tsy-target" />
      {shown.map(s => (
        <path key={s.key} d={s.pts.map((p, i) => `${i ? "L" : "M"}${x(i, s.pts.length).toFixed(1)},${y(p.v).toFixed(1)}`).join("")}
              className="tsy-seriesline" style={{ stroke: s.color }}>
          <title>{s.label}</title>
        </path>
      ))}
    </svg>
  );
}
// Fixed distinct colors — theme accent is green, which collided with the
// green "up" color when both series were shown (user report).
const TSY_CPI_SERIES = [
  ["headline_yoy", "Headline YoY", "#4E9CF5"],
  ["core_yoy", "Core YoY", "#E8A33D"],
  ["headline_mom", "Headline MoM", "#8b5cf6"],
  ["core_mom", "Core MoM", "#06b6d4"],
  ["core_3m_ann", "Core 3m ann.", "#3BD996"],
  ["core_6m_ann", "Core 6m ann.", "#F56D77"],
];
function TsyCpiCard({ apiFetch }) {
  const inf = useTsy(apiFetch, "inflation", 3600000);
  const [period, setPeriod] = useState("2y");
  const [on, setOn] = useState({ headline_yoy: true, core_yoy: true, core_3m_ann: true });
  if (inf.loading) return <div className="card tsy-card"><div className="kicker">CPI & inflation</div><TsyLoading /></div>;
  if (!inf.d || !inf.d.ok) return <div className="card tsy-card"><div className="kicker">CPI & inflation</div><TsyErr err={inf.err || "no data"} retry={inf.retry} /></div>;
  const rows = (inf.d.rows || []).filter(r => r.ok);
  const core = rows.find(r => r.key === "core");
  const charts = inf.d.charts || {};
  const series = TSY_CPI_SERIES.filter(([k]) => on[k] && charts[k]).map(([k, label, color]) => ({ key: k, label, color, pts: charts[k] }));
  return (
    <div className="card tsy-card">
      <div className="card-head">
        <div>
          <div className="kicker">CPI & inflation · BLS data via FRED (seasonally adjusted)</div>
          <div className="card-title">Inflation dashboard</div>
        </div>
        {core && <span className="tsy-datechip num" title="Most recent CPI data month.">{core.month}</span>}
      </div>
      <div className="tsy-cpigrid">
        {rows.map(r => (
          <div key={r.key} className={`tsy-cpicell ${r.key === "headline" || r.key === "core" ? "big" : ""}`}
               title={`${r.label} — data month ${r.month}.\nMoM ${r.mom != null ? r.mom + "%" : "—"} (prev ${r.mom_prev != null ? r.mom_prev + "%" : "—"})\nYoY ${r.yoy != null ? r.yoy + "%" : "—"} (prev ${r.yoy_prev != null ? r.yoy_prev + "%" : "—"})\nYoY sits at the ${r.yoy_pctile_10y != null ? r.yoy_pctile_10y.toFixed(0) + "th percentile of the last 10 years" : "—"}.\nConsensus: no free reliable feed — not estimated.`}>
            <em>{r.label}</em>
            <b className="num">{r.yoy != null ? `${r.yoy.toFixed(2)}%` : "—"}<i>YoY</i></b>
            <span className="num">MoM {r.mom != null ? `${r.mom >= 0 ? "+" : ""}${r.mom.toFixed(2)}%` : "—"}
              <i className={r.yoy != null && r.yoy_prev != null ? (r.yoy < r.yoy_prev ? "cu" : r.yoy > r.yoy_prev ? "cd" : "") : ""}>
                {r.yoy != null && r.yoy_prev != null ? (r.yoy < r.yoy_prev ? "▼ cooling" : r.yoy > r.yoy_prev ? "▲ heating" : "flat") : ""}
              </i>
            </span>
            {r.key === "core" && <span className="num tsy-annrow">3m ann <b>{r.ann3m != null ? r.ann3m.toFixed(2) + "%" : "—"}</b> · 6m ann <b>{r.ann6m != null ? r.ann6m.toFixed(2) + "%" : "—"}</b></span>}
          </div>
        ))}
        <div className="tsy-cpicell" title={inf.d.supercore && inf.d.supercore.note}>
          <em>Supercore (svcs ex-shelter)</em>
          <TsyNA why={inf.d.supercore && inf.d.supercore.note} />
        </div>
      </div>
      <div className="tsy-ctrl tsy-chartctrl">
        {TSY_CPI_SERIES.map(([k, label, color]) => charts[k] ? (
          <button key={k} type="button" className={`tsy-serbtn ${on[k] ? "on" : ""}`} style={on[k] ? { borderColor: color, color } : null}
                  onClick={() => setOn(o => ({ ...o, [k]: !o[k] }))}>{label}</button>
        ) : null)}
        <select className="sb-select" value={period} onChange={e => setPeriod(e.target.value)}>
          {[["1y", "1 year"], ["2y", "2 years"], ["5y", "5 years"], ["10y", "10 years"], ["max", "Max"]].map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>
      <TsySeriesSvg series={series} period={period} />
      <div className="tsy-sigd">Dashed line = 2% (Fed target, on PCE — CPI shown here typically runs a bit above PCE). Consensus estimates: no free reliable source — differences vs consensus are not shown rather than guessed.</div>
      <TsyFoot src={inf.d.source} at={core ? core.month : null} />
    </div>
  );
}

/* ── 6. CPI releases & market reaction ─────────────────────────────────── */
function TsyCpiReactions({ apiFetch }) {
  const inf = useTsy(apiFetch, "inflation", 3600000);
  const [flt, setFlt] = useState("all");
  if (inf.loading) return <TsyLoading />;
  const rx = inf.d && inf.d.reactions;
  if (!rx || !rx.ok) return <div style={{ padding: "8px 0" }}><TsyNA why="No reaction history available." /></div>;
  const rows = (rx.rows || []).filter(r => flt === "all" || r.class === flt);
  return (
    <div>
      <div className="tsy-ctrl" style={{ marginBottom: 8 }}>
        <select className="sb-select" value={flt} onChange={e => setFlt(e.target.value)}>
          <option value="all">All releases</option>
          <option value="hot">Hot core CPI</option>
          <option value="cool">Cool core CPI</option>
          <option value="inline">In-line core CPI</option>
        </select>
        <span className="muted" style={{ fontSize: 11.5 }}>{rows.length} releases</span>
      </div>
      <div className="tsy-tablewrap">
        <table className="tsy-table">
          <thead><tr><th>Release</th><th>Data mo.</th><th>Head MoM</th><th>Core MoM</th><th>vs trend</th><th>2y</th><th>10y</th><th>SPY</th><th>QQQ</th><th>IWM</th><th>TLT</th><th>GLD</th><th>UUP</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.date}>
                <td className="num">{r.date}</td>
                <td className="num">{r.data_month}</td>
                <td className="num">{r.headline_mom != null ? `${r.headline_mom >= 0 ? "+" : ""}${r.headline_mom.toFixed(2)}%` : "—"}</td>
                <td className="num">{r.core_mom != null ? `${r.core_mom >= 0 ? "+" : ""}${r.core_mom.toFixed(2)}%` : "—"}</td>
                <td>{r.class ? <span className={`tsy-pill ${r.class === "hot" ? "down" : r.class === "cool" ? "up" : "mut"}`}>{r.class.toUpperCase()}</span> : "—"}</td>
                <td><TsyBp v={r.y2_bp} d={0} /></td>
                <td><TsyBp v={r.y10_bp} d={0} /></td>
                {["spy", "qqq", "iwm", "tlt", "gld", "uup"].map(k => (
                  <td key={k} className={`num ${r[k] != null ? (r[k] >= 0 ? "cu" : "cd") : ""}`}>{r[k] != null ? `${r[k] >= 0 ? "+" : ""}${r[k].toFixed(2)}%` : "—"}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="tsy-sigd">{rx.note} {rx.intraday}</div>
      <TsyFoot src="Release dates: BLS schedule · CPI values: FRED · market closes: Yahoo (delayed)" />
    </div>
  );
}

/* ── 9/10. Futures + ETF proxies ───────────────────────────────────────── */
function TsyMarketsCards({ apiFetch, onOpenTicker }) {
  const mk = useTsy(apiFetch, "markets", 900000);
  if (mk.loading) return <div className="card tsy-card"><div className="kicker">Treasury futures & ETFs</div><TsyLoading /></div>;
  if (!mk.d || !mk.d.ok) return <div className="card tsy-card"><div className="kicker">Treasury futures & ETFs</div><TsyErr err={mk.err || mk.d && mk.d.error || "no data"} retry={mk.retry} /></div>;
  const futs = mk.d.futures || [];
  const etfs = mk.d.etfs || [];
  return (
    <React.Fragment>
      <div className="card tsy-card">
        <div className="card-head">
          <div>
            <div className="kicker">Bond ETF proxies · click a row to open it in the Analyze workflow</div>
            <div className="card-title">Treasury ETFs</div>
          </div>
        </div>
        <div className="tsy-tablewrap">
          <table className="tsy-table">
            <thead><tr><th>ETF</th><th>Price</th><th>1d</th><th>5d</th><th>1m</th><th>Duration≈</th><th>Vol</th><th>RelVol</th><th>vs 20d</th><th>vs 50d</th><th>vs 200d</th></tr></thead>
            <tbody>
              {etfs.map(t => (
                <tr key={t.sym} className="tsy-rowlink" onClick={() => t.ok && onOpenTicker && onOpenTicker(t.sym)}
                    title={`Open ${t.sym} on the Analyze tab. Duration ≈ ${t.duration} yrs: a +10bp yield move ≈ ${t.duration != null ? (-t.duration * 0.1).toFixed(1) : "—"}% price move.`}>
                  <td><b>{t.sym}</b></td>
                  {t.ok ? (
                    <React.Fragment>
                      <td className="num">{fmt$(t.last, 2)}</td>
                      {["d1", "d5", "d21"].map(k => <td key={k} className={`num ${t[k] != null ? (t[k] >= 0 ? "cu" : "cd") : ""}`}>{t[k] != null ? `${t[k] >= 0 ? "+" : ""}${t[k]}%` : "—"}</td>)}
                      <td className="num">{t.duration}y</td>
                      <td className="num">{t.volume != null ? (t.volume / 1e6).toFixed(1) + "M" : "—"}</td>
                      <td className="num">{t.rel_volume != null ? t.rel_volume + "×" : "—"}</td>
                      {["dma20", "dma50", "dma200"].map(k => <td key={k} className={`num ${t[k] != null ? (t[k] >= 0 ? "cu" : "cd") : ""}`}>{t[k] != null ? `${t[k] >= 0 ? "+" : ""}${t[k]}%` : "—"}</td>)}
                    </React.Fragment>
                  ) : <td colSpan="10"><TsyNA /></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="tsy-sigd">{mk.d.etf_note} Distribution yields: <TsyNA why="No reliable free source for current distribution yields — not estimated." /></div>
        <TsyFoot src={mk.d.source} delayed />
      </div>
    </React.Fragment>
  );
}

/* ── 15. Cross-asset correlations ──────────────────────────────────────── */
function TsyCorrTable({ apiFetch }) {
  const mk = useTsy(apiFetch, "markets", 900000);
  const [w, setW] = useState(60);
  if (mk.loading) return <TsyLoading />;
  const c = mk.d && mk.d.correlations;
  if (!c || !c.ok) return <div style={{ padding: "8px 0" }}><TsyNA why="Correlation inputs unavailable." /></div>;
  return (
    <div>
      <div className="tsy-ctrl" style={{ marginBottom: 8 }}>
        {c.windows.map(win => (
          <button key={win} type="button" className={`tsy-serbtn ${w === win ? "on" : ""}`} onClick={() => setW(win)}>{win}d</button>
        ))}
      </div>
      <div className="tsy-corrbars">
        {c.rows.map(r => {
          const v = r[`w${w}`];
          return (
            <div key={r.sym} className="tsy-corrrow" title={`${r.label}: ${v != null ? v : "—"} correlation of daily returns vs daily CHANGE in the 10y yield over the last ${w} trading days. Positive = tends to rise when yields rise. Correlation ≠ causation.`}>
              <em>{r.label}</em>
              <div className="tsy-corrbar">
                <i className={v != null && v >= 0 ? "pos" : "neg"} style={{ width: `${Math.abs(v || 0) * 50}%`, [v != null && v >= 0 ? "left" : "right"]: "50%" }}></i>
              </div>
              <b className={`num ${v != null ? (v >= 0 ? "cu" : "cd") : ""}`}>{v != null ? v.toFixed(2) : "—"}</b>
            </div>
          );
        })}
      </div>
      <div className="tsy-sigd">{c.note}</div>
      <TsyFoot src="FRED DGS10 + Yahoo closes (delayed)" />
    </div>
  );
}

/* ── 12. Auctions ──────────────────────────────────────────────────────── */
function TsyAuctions({ apiFetch }) {
  const au = useTsy(apiFetch, "auctions", 3600000);
  if (au.loading) return <TsyLoading />;
  if (!au.d || !au.d.ok) return <TsyErr err={au.err || "TreasuryDirect unavailable"} retry={au.retry} />;
  const strengthPill = a => a.strength
    ? <span className={`tsy-pill ${a.strength === "strong" ? "up" : a.strength === "weak" ? "down" : "mut"}`}
            title={a.vs_prior ? `Rule: bid-to-cover ${a.btc} vs ${a.vs_prior.btc_avg10} avg of prior ${a.vs_prior.n}; indirect ${a.indirect_pct}% vs ${a.vs_prior.indirect_avg10}% avg. Strong = both above; weak = both below.` : ""}>{a.strength.toUpperCase()}</span>
    : <span className="muted">—</span>;
  return (
    <div>
      <div className="tsy-tablewrap">
        <table className="tsy-table">
          <thead><tr><th>Auction</th><th>Date</th><th>Settle</th><th>Size</th><th>High yield</th><th>Bid-to-cover</th><th>Indirect</th><th>Direct</th><th>Dealers</th><th>Read</th></tr></thead>
          <tbody>
            {(au.d.recent_coupons || []).map((a, i) => (
              <tr key={i}>
                <td><b>{a.term}</b> {a.type}</td>
                <td className="num">{a.date}</td>
                <td className="num">{a.settle}</td>
                <td className="num">{a.offering ? `$${(a.offering / 1e9).toFixed(0)}B` : "—"}</td>
                <td className="num">{a.high_yield != null ? a.high_yield.toFixed(3) + "%" : "—"}</td>
                <td className="num">{a.btc != null ? a.btc.toFixed(2) : "—"}{a.vs_prior ? <span className="muted"> /{a.vs_prior.btc_avg10}</span> : null}</td>
                <td className="num">{a.indirect_pct != null ? a.indirect_pct + "%" : "—"}{a.vs_prior && a.vs_prior.indirect_avg10 != null ? <span className="muted"> /{a.vs_prior.indirect_avg10}%</span> : null}</td>
                <td className="num">{a.direct_pct != null ? a.direct_pct + "%" : "—"}</td>
                <td className="num">{a.dealer_pct != null ? a.dealer_pct + "%" : "—"}</td>
                <td>{strengthPill(a)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="tsy-sigd">{au.d.note} Small figures after "/" = average of the prior 10 auctions of the same security. Tail / when-issued comparison: <TsyNA why="When-issued yields need dealer quotes (no free source) — not estimated." /></div>
      <TsyFoot src={au.d.source} />
    </div>
  );
}

/* ── 13. Fed expectations ──────────────────────────────────────────────── */
function TsyFedCard({ apiFetch }) {
  const fd = useTsy(apiFetch, "fed", 1800000);
  if (fd.loading) return <div className="card tsy-card"><div className="kicker">Fed rate expectations</div><TsyLoading /></div>;
  if (!fd.d || !fd.d.ok) return <div className="card tsy-card"><div className="kicker">Fed rate expectations</div><TsyErr err={fd.err || "no data"} retry={fd.retry} /></div>;
  const t = fd.d.target, nm = fd.d.next_meeting, path = fd.d.implied_path || [];
  return (
    <div className="card tsy-card">
      <div className="card-head">
        <div>
          <div className="kicker">Policy rate · market-implied path</div>
          <div className="card-title">Fed rate expectations</div>
        </div>
        {nm && <span className="tsy-datechip num" title="Next scheduled FOMC decision.">{nm.date} · {nm.days}d</span>}
      </div>
      <div className="tsy-fed">
        {t && <div className="tsy-cd"><em>CURRENT TARGET RANGE</em><b className="num">{t.lower.toFixed(2)}–{t.upper.toFixed(2)}%</b><span>{t.source} · as of {t.date}</span></div>}
        {fd.d.yearend && (
          <div className="tsy-cd" title="Implied avg fed funds for December from CME 30-day FF futures (100 − price), vs the current target midpoint.">
            <em>MARKET-IMPLIED BY {fd.d.yearend.month}</em>
            <b className="num">{fd.d.yearend.implied_rate.toFixed(2)}%</b>
            <span>≈ {Math.abs(fd.d.yearend.cuts_25bp).toFixed(1)} × 25bp {fd.d.yearend.cuts_25bp >= 0 ? "of cuts" : "of hikes"} priced</span>
          </div>
        )}
        {path.length > 0 ? (
          <div className="tsy-tablewrap">
            <table className="tsy-table">
              <thead><tr><th>Month</th><th>Implied avg rate</th><th>1d Δ</th></tr></thead>
              <tbody>
                {path.map(p => (
                  <tr key={p.month}><td className="num">{p.month}</td><td className="num"><b>{p.implied_rate.toFixed(2)}%</b></td><td><TsyBp v={p.d1_bp} d={0} /></td></tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div style={{ padding: "6px 0" }}>Implied path: <TsyNA why="Fed funds futures quotes unreachable — not estimated." /></div>}
        <div className="tsy-sigd">{fd.d.implied_note} Per-meeting probabilities: <TsyNA why="Requires CME FedWatch data — not estimated." /></div>
      </div>
      <TsyFoot src="FRED (target range, official) · CME ZQ futures via Yahoo (path, delayed)" />
    </div>
  );
}

/* ── 14. COT positioning ───────────────────────────────────────────────── */
function TsyCot({ apiFetch }) {
  const ct = useTsy(apiFetch, "cot", 3600000);
  if (ct.loading) return <TsyLoading />;
  if (!ct.d || !ct.d.ok) return <TsyErr err={ct.err || "CFTC unavailable"} retry={ct.retry} />;
  const g = (grp) => grp
    ? <span className="num">{(grp.net >= 0 ? "+" : "") + grp.net.toLocaleString()}
        <em className="muted"> wk {grp.wk_chg != null ? (grp.wk_chg >= 0 ? "+" : "") + grp.wk_chg.toLocaleString() : "—"} · {grp.pctile != null ? grp.pctile.toFixed(0) + "%ile" : "—"}</em>
        {grp.crowded && <b className={`tsy-pill ${grp.crowded === "long" ? "up" : "down"}`} title="Net position at a 3-year extreme (≥90th or ≤10th percentile). Context, not a signal by itself — crowded positioning can persist or unwind violently.">CROWDED {grp.crowded.toUpperCase()}</b>}
      </span>
    : <TsyNA />;
  return (
    <div>
      <div className="tsy-tablewrap">
        <table className="tsy-table">
          <thead><tr><th>Futures</th><th>Report</th><th>Asset managers</th><th>Leveraged funds</th><th>Dealers</th><th>Non-comm. (AM+Lev)</th></tr></thead>
          <tbody>
            {(ct.d.rows || []).map(r => (
              <tr key={r.code}>
                <td><b>{r.code}</b></td>
                {r.ok ? (
                  <React.Fragment>
                    <td className="num">{r.date}</td>
                    <td>{g(r.asset_mgr)}</td>
                    <td>{g(r.lev_funds)}</td>
                    <td>{g(r.dealer)}</td>
                    <td className="num">{r.noncommercial ? `${r.noncommercial.net >= 0 ? "+" : ""}${r.noncommercial.net.toLocaleString()} (${r.noncommercial.pctile != null ? r.noncommercial.pctile.toFixed(0) + "%ile" : "—"})` : "—"}</td>
                  </React.Fragment>
                ) : <td colSpan="5"><TsyNA /></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="tsy-sigd">{ct.d.note}</div>
      <TsyFoot src={ct.d.source} />
    </div>
  );
}

/* ── 16. Rate sensitivity of the watchlist ─────────────────────────────── */
const TSY_FACTORS = [["y10", "10y yield"], ["y2", "2y yield"], ["y30", "30y yield"], ["curve", "2s10s steepening"], ["real10", "10y real yield"]];
function TsySense({ apiFetch, onOpenTicker }) {
  const [board, setBoard] = useState(null);
  const [factor, setFactor] = useState("y10");
  const [dir, setDir] = useState("neg");
  const pollRef = useRef(null);
  const load = async () => {
    try { const r = await apiFetch("/api/treasury/sense"); const d = await r.json(); setBoard(d); return d; }
    catch (e) { return null; }
  };
  useEffect(() => { load(); return () => pollRef.current && clearInterval(pollRef.current); }, []);
  const scan = async () => {
    try { await apiFetch("/api/treasury/sense/scan?force=1"); } catch (e) { return; }
    await load();
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) { clearInterval(pollRef.current); pollRef.current = null; }
    }, 5000);
  };
  const st = (board && board.status) || {};
  const rows = ((board && board.rows) || [])
    .map(r => ({ ticker: r.ticker, f: r[factor] }))
    .filter(r => r.f && r.f.ok)
    .sort((a, b) => dir === "neg" ? a.f.beta10bp - b.f.beta10bp : b.f.beta10bp - a.f.beta10bp)
    .slice(0, 25);
  return (
    <div>
      <div className="tsy-ctrl" style={{ marginBottom: 8 }}>
        <select className="sb-select" value={factor} onChange={e => setFactor(e.target.value)}>
          {TSY_FACTORS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <select className="sb-select" value={dir} onChange={e => setDir(e.target.value)}>
          <option value="neg">Most hurt by rising factor</option>
          <option value="pos">Most helped by rising factor</option>
        </select>
        <button type="button" className="scan-run-btn" onClick={scan} disabled={!!st.scanning}>
          {st.scanning ? `Scanning… ${st.scanned || 0}/${st.total || 0}` : (rows.length ? "Rescan watchlist" : "Scan watchlist")}
        </button>
        {st.last_scan && <span className="muted" style={{ fontSize: 11.5 }}>last scan {new Date(st.last_scan).toLocaleString()}</span>}
      </div>
      {rows.length > 0 ? (
        <div className="tsy-tablewrap">
          <table className="tsy-table">
            <thead><tr><th>Ticker</th><th>β per +10bp</th><th>Corr</th><th>n</th><th>Confidence</th></tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.ticker} className="tsy-rowlink" onClick={() => onOpenTicker && onOpenTicker(r.ticker)}
                    title={`${r.ticker}: moves ${r.f.beta10bp >= 0 ? "+" : ""}${r.f.beta10bp}% on average when the ${(TSY_FACTORS.find(f => f[0] === factor) || [])[1]} rises 10bp (last ${r.f.n} sessions, t=${r.f.t}). Click to open in Analyze.`}>
                  <td><b>{r.ticker}</b></td>
                  <td className={`num ${r.f.beta10bp >= 0 ? "cu" : "cd"}`}><b>{r.f.beta10bp >= 0 ? "+" : ""}{r.f.beta10bp}%</b></td>
                  <td className="num">{r.f.corr}</td>
                  <td className="num">{r.f.n}</td>
                  <td><span className={`tsy-pill ${r.f.conf === "high" ? "up" : "mut"}`}>{r.f.conf.toUpperCase()}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <div style={{ padding: "6px 0" }} className="muted">{st.scanning ? "Scanning your watchlist…" : "No scan yet — click Scan watchlist. Names without a statistically meaningful relationship (|t| < 2) are excluded rather than shown with a fake conclusion."}</div>}
      {board && <div className="tsy-sigd">{board.note}</div>}
      <TsyFoot src="FRED daily yield changes × your watchlist's daily returns (Yahoo, delayed)" />
    </div>
  );
}

/* ── 17. Alerts (client-side rules on the displayed data) ──────────────── */
const TSY_ALERT_DEFS = [
  ["y2_abs1d", "2y daily move ≥ (bp)", 8],
  ["y10_above", "10y yield crosses above (%)", 4.75],
  ["y10_below", "10y yield crosses below (%)", 4.25],
  ["y30_above", "30y yield crosses above (%)", 5.25],
  ["s2s10_uninvert", "2s10s uninverts (no value needed)", 0],
  ["s2s10_chg21", "2s10s 1-month change ≥ (bp, abs)", 15],
  ["move_above", "MOVE crosses above", 130],
];
function TsyAlertsCard({ core }) {
  const KEY = "tsy_alerts_v1";
  const [rules, setRules] = useState(() => { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { return []; } });
  const [sel, setSel] = useState(TSY_ALERT_DEFS[0][0]);
  const [val, setVal] = useState(String(TSY_ALERT_DEFS[0][2]));
  const [fired, setFired] = useState([]);
  const save = (rs) => { setRules(rs); try { localStorage.setItem(KEY, JSON.stringify(rs)); } catch (e) {} };
  const d = core.d;
  useEffect(() => {
    if (!d || !d.ok || !rules.length) return;
    const y = {}; (d.yields || []).forEach(c => { y[c.tenor] = c; });
    const s210 = (d.spreads || []).find(s => s.key === "2s10s");
    const mv = d.move;
    const hits = [];
    for (const r of rules) {
      let hit = false, why = "";
      if (r.k === "y2_abs1d" && y["2Y"] && y["2Y"].bp1d != null && Math.abs(y["2Y"].bp1d) >= r.v) { hit = true; why = `2y moved ${y["2Y"].bp1d} bp today`; }
      if (r.k === "y10_above" && y["10Y"] && y["10Y"].yield >= r.v) { hit = true; why = `10y at ${y["10Y"].yield.toFixed(2)}% ≥ ${r.v}%`; }
      if (r.k === "y10_below" && y["10Y"] && y["10Y"].yield <= r.v) { hit = true; why = `10y at ${y["10Y"].yield.toFixed(2)}% ≤ ${r.v}%`; }
      if (r.k === "y30_above" && y["30Y"] && y["30Y"].yield >= r.v) { hit = true; why = `30y at ${y["30Y"].yield.toFixed(2)}% ≥ ${r.v}%`; }
      if (r.k === "s2s10_uninvert" && s210 && !s210.inverted && s210.d21 != null && s210.bp - s210.d21 < 0) { hit = true; why = `2s10s now ${s210.bp >= 0 ? "+" : ""}${s210.bp} bp (was inverted a month ago)`; }
      if (r.k === "s2s10_chg21" && s210 && s210.d21 != null && Math.abs(s210.d21) >= r.v) { hit = true; why = `2s10s ${s210.d21 >= 0 ? "steepened" : "flattened"} ${Math.abs(s210.d21)} bp over 1 month`; }
      if (r.k === "move_above" && mv && mv.value >= r.v) { hit = true; why = `MOVE at ${mv.value} ≥ ${r.v}`; }
      if (hit) hits.push({ id: r.id, label: (TSY_ALERT_DEFS.find(x => x[0] === r.k) || [])[1], why });
    }
    setFired(hits);
  }, [d, rules]);
  return (
    <div>
      <div className="tsy-ctrl" style={{ marginBottom: 8, flexWrap: "wrap" }}>
        <select className="sb-select" value={sel} onChange={e => { setSel(e.target.value); const def = TSY_ALERT_DEFS.find(x => x[0] === e.target.value); if (def) setVal(String(def[2])); }}>
          {TSY_ALERT_DEFS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <input className="sb-select" style={{ width: 90 }} value={val} onChange={e => setVal(e.target.value)} inputMode="decimal" />
        <button type="button" className="scan-run-btn"
                onClick={() => { const v = parseFloat(val); if (sel !== "s2s10_uninvert" && !(v === v)) return; save([...rules, { id: Date.now(), k: sel, v: v || 0 }]); }}>
          Add alert
        </button>
      </div>
      {rules.length === 0 && <div className="muted" style={{ fontSize: 12.5 }}>No alerts yet. Rules are checked against the official EOD curve each time this tab refreshes (and shown below when triggered). CPI-surprise, auction-strength and Fed-probability alerts need consensus/CME feeds that have no free source — those trigger types are intentionally absent rather than faked.</div>}
      {rules.map(r => {
        const def = TSY_ALERT_DEFS.find(x => x[0] === r.k) || [r.k, r.k];
        const hit = fired.find(f => f.id === r.id);
        return (
          <div key={r.id} className={`tsy-alertrow ${hit ? "hit" : ""}`}>
            <span>{def[1]}{r.k !== "s2s10_uninvert" ? <b className="num"> {r.v}</b> : null}</span>
            {hit ? <b className="tsy-pill down" title={hit.why}>TRIGGERED · {hit.why}</b> : <span className="muted">armed</span>}
            <button type="button" className="tsy-x" onClick={() => save(rules.filter(x => x.id !== r.id))} aria-label="Remove">✕</button>
          </div>
        );
      })}
    </div>
  );
}

/* ── Overview grid (v3.62) — the glance terminal, mockup density ──────────
   Every mini carries the numbers on-screen (percentiles, changes, status),
   not just in tooltips. Detail sections remain below. */
function TsyMini({ kicker, title, children, right }) {
  return (
    <div className="card tsy-mini">
      <div className="tsy-mini-h"><em>{kicker}</em>{right}</div>
      {title && <div className="tsy-mini-t">{title}</div>}
      {children}
    </div>
  );
}
function TsySpark({ pts, w = 150, h = 34, tone }) {
  if (!pts || pts.length < 5) return null;
  const lo = Math.min(...pts), hi = Math.max(...pts);
  const x = i => i / (pts.length - 1) * w;
  const y = v => 3 + (1 - (v - lo) / Math.max(1e-9, hi - lo)) * (h - 6);
  const up = pts[pts.length - 1] >= pts[0];
  const col = tone || (up ? "var(--down)" : "var(--up)");   // yields rising = red
  return (
    <svg className="tsy-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("")}
            fill="none" style={{ stroke: col }} strokeWidth="1.6" />
    </svg>
  );
}
function TsyOvYields({ core }) {
  const cards = (core.d && core.d.yields) || [];
  return (
    <TsyMini kicker="Key Treasury yields · official EOD curve" right={<span className="tsy-datechip num">{core.d && core.d.curve_date}</span>}>
      <div className="tsy-ystrip">
        {cards.map(c => (
          <div key={c.tenor} className={`tsy-ycell ${TSY_KEY4[c.tenor] ? "key" : ""}`}
               title={`${c.tenor}: ${c.yield.toFixed(2)}% · 1d ${c.bp1d != null ? c.bp1d + " bp" : "—"} · 5d ${c.bp5d != null ? c.bp5d + " bp" : "—"} · 1m ${c.bp21d != null ? c.bp21d + " bp" : "—"} · YTD ${c.bp_ytd != null ? c.bp_ytd + " bp" : "—"}\n52w range ${c.lo52w.toFixed(2)}–${c.hi52w.toFixed(2)}%${c.key ? "\n" + c.key : ""}\n${TSY_INV}`}>
            <em>{c.tenor}</em>
            <b className="num">{c.yield.toFixed(2)}%</b>
            <span className={`num ${tsyBpCls(c.bp1d)}`}>{c.bp1d != null ? `${c.bp1d >= 0 ? "+" : ""}${c.bp1d.toFixed(0)}${c.bp1d > 0 ? " ▲" : c.bp1d < 0 ? " ▼" : ""}` : "—"}</span>
            <i className="num">{c.pct52w != null ? `${c.pct52w.toFixed(0)}% 52w` : ""}</i>
          </div>
        ))}
      </div>
      <div className="tsy-mini-note" style={{ marginTop: 6 }} title={TSY_INV}>Yields move opposite of bond prices ⓘ · green = yield falling</div>
    </TsyMini>
  );
}
function TsyOv10Y({ core }) {
  const c = ((core.d && core.d.yields) || []).find(x => x.tenor === "10Y");
  if (!c) return <TsyMini kicker="10Y Treasury"><TsyNA /></TsyMini>;
  const span = c.hi52w - c.lo52w;
  const pos = span > 0 ? Math.max(0, Math.min(100, (c.yield - c.lo52w) / span * 100)) : 50;
  return (
    <TsyMini kicker="10Y Treasury · equity valuation benchmark">
      <div className="tsy-spot num"><b>{c.yield.toFixed(2)}%</b><TsyBp v={c.bp1d} d={0} /></div>
      {c.spark && <div title="Last ~90 trading days of the official EOD 10y yield. Red = yields rose over the window."><TsySpark pts={c.spark} w={220} h={36} /></div>}
      <div className="tsy-52bar"><i style={{ left: `${pos}%` }}></i></div>
      <div className="tsy-52lbl num"><span>{c.lo52w.toFixed(2)}</span><span>52W range</span><span>{c.hi52w.toFixed(2)}</span></div>
      <div className="tsy-spotrow num">
        <span>5d <TsyBp v={c.bp5d} d={0} /></span>
        <span>1m <TsyBp v={c.bp21d} d={0} /></span>
        <span>YTD <TsyBp v={c.bp_ytd} d={0} /></span>
      </div>
    </TsyMini>
  );
}
function TsyOvFutures({ mk }) {
  const futs = ((mk.d && mk.d.futures) || []).filter(f => f.ok);
  return (
    <TsyMini kicker="Treasury futures · delayed" right={<span className="tsy-mini-note">price ↑ = yields ↓</span>}>
      {futs.length ? (
        <table className="tsy-matrix num">
          <thead><tr><th style={{ textAlign: "left" }}></th><th>Last</th><th>Chg</th><th>%</th></tr></thead>
          <tbody>
            {futs.map(f => (
              <tr key={f.code} title={`${f.label} front-month continuous, ${f.date}. Range ${f.day_lo}–${f.day_hi}, volume ${f.volume != null ? f.volume.toLocaleString() : "—"}. PRICE — moves opposite to yields.`}>
                <td className="tsy-mxt">{f.code} <i className="muted">{f.code === "ZT" ? "2Y" : f.code === "ZF" ? "5Y" : f.code === "ZN" ? "10Y" : f.code === "ZB" ? "30Y" : "Ultra"}</i></td>
                <td><b>{f.last}</b></td>
                <td className={f.chg_abs != null ? (f.chg_abs >= 0 ? "cu" : "cd") : ""}>{f.chg_abs != null ? `${f.chg_abs >= 0 ? "+" : ""}${f.chg_abs.toFixed(3)}` : "—"}</td>
                <td className={f.chg_pct != null ? (f.chg_pct >= 0 ? "cu" : "cd") : ""}>{f.chg_pct != null ? `${f.chg_pct >= 0 ? "+" : ""}${f.chg_pct}%` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <TsyNA why="Futures quote source unreachable — not estimated." />}
    </TsyMini>
  );
}
function TsyOvAnalysis({ core }) {
  const d = core.d || {};
  const shape = d.curve_shape, reg = d.regime, mv = d.curve_moves;
  const sp = {};
  (d.spreads || []).forEach(s => { sp[s.key] = s; });
  const inv = shape && (shape.label.startsWith("inverted") || shape.label.startsWith("partially"));
  return (
    <TsyMini kicker="Yield curve analysis">
      {shape && <div className="tsy-kv" title={shape.detail}><em>Curve shape</em><b className={inv ? "cd" : "cu"}>{shape.label}</b></div>}
      {["2s10s", "3m10y", "5s30s", "10yff"].map(k => sp[k] ? (
        <div key={k} className="tsy-kv" title={sp[k].label}>
          <em>{sp[k].label.split(" (")[0]}</em>
          <b className={`num ${sp[k].inverted ? "cd" : "cu"}`}>{sp[k].bp >= 0 ? "+" : ""}{sp[k].bp.toFixed(0)} bp</b>
          <span className="num">{sp[k].trend === "steepening" ? "↗" : sp[k].trend === "flattening" ? "↘" : ""}</span>
        </div>
      ) : null)}
      {reg && (
        <React.Fragment>
          <div className="tsy-kv" title={`Slope change (10y − 2y) over ${reg.window}.`}>
            <em>Steepness (5d)</em>
            <b className="num">{reg.slope_chg_bp >= 0 ? "+" : ""}{reg.slope_chg_bp} bp</b>
            <span>{reg.slope_chg_bp > 1 ? "steepening" : reg.slope_chg_bp < -1 ? "flattening" : "flat"}</span>
          </div>
          {mv && <div className="tsy-kv" title="Average 5-day bp change of 1m–2y tenors."><em>Front end (5d)</em><b className="num"><TsyBp v={mv.front_avg_bp5d} /></b></div>}
          {mv && <div className="tsy-kv" title="Average 5-day bp change of 10y–30y tenors."><em>Long end (5d)</em><b className="num"><TsyBp v={mv.long_avg_bp5d} /></b></div>}
          {mv && mv.biggest && <div className="tsy-kv"><em>Largest move</em><b className="num">{mv.biggest.tenor} <TsyBp v={mv.biggest.bp5d} /></b></div>}
          <div className={`tsy-sigbox ${reg.label.startsWith("bull") ? "up" : reg.label.startsWith("bear") ? "down" : ""}`}
               title={`2y ${reg.d2y_bp >= 0 ? "+" : ""}${reg.d2y_bp} bp, 10y ${reg.d10y_bp >= 0 ? "+" : ""}${reg.d10y_bp} bp over ${reg.window}. Bull = yields falling.`}>
            <em>REGIME · {reg.window}</em>
            <b>{reg.label.toUpperCase()}</b>
            <span>2y {reg.d2y_bp >= 0 ? "+" : ""}{reg.d2y_bp} · 10y {reg.d10y_bp >= 0 ? "+" : ""}{reg.d10y_bp} bp</span>
          </div>
        </React.Fragment>
      )}
    </TsyMini>
  );
}
function TsyOvAuctions({ au }) {
  const d = au.d || {};
  const rows = [...(d.recent_coupons || []), ...(d.recent_bills || [])]
    .sort((a, b) => (b.date || "").localeCompare(a.date || "")).slice(0, 8);
  return (
    <TsyMini kicker="Recent auctions · TreasuryDirect">
      {au.loading ? <TsyLoading /> : rows.length ? rows.map((a, i) => (
        <div key={i} className="tsy-kv" title={a.vs_prior ? `$${a.offering ? (a.offering / 1e9).toFixed(0) : "—"}B. Bid-to-cover ${a.btc} vs ${a.vs_prior.btc_avg10} avg of prior ${a.vs_prior.n}; indirect ${a.indirect_pct}% vs ${a.vs_prior.indirect_avg10}%.` : `${a.term} ${a.type} auctioned ${a.date}${a.offering ? `, $${(a.offering / 1e9).toFixed(0)}B` : ""}.`}>
          <em>{a.date && a.date.slice(5)} <i>{a.term}</i></em>
          <span className="num muted">{a.offering ? `$${(a.offering / 1e9).toFixed(0)}B` : ""}</span>
          <b className="num">{a.high_yield != null ? a.high_yield.toFixed(3) + "%" : "—"}</b>
          <span className="num">{a.btc != null ? a.btc.toFixed(2) + "×" : ""}</span>
          {a.strength && <span className={`tsy-pill ${a.strength === "strong" ? "up" : a.strength === "weak" ? "down" : "mut"}`}>{a.strength.slice(0, 4).toUpperCase()}</span>}
        </div>
      )) : <TsyNA why="TreasuryDirect unreachable." />}
    </TsyMini>
  );
}
function TsyOvSpreads({ core }) {
  const sp = ((core.d && core.d.spreads) || []).filter(s => s.key !== "10yff");
  return (
    <TsyMini kicker="Important Treasury spreads">
      <table className="tsy-matrix num">
        <thead><tr><th style={{ textAlign: "left" }}>Spread</th><th>bps</th><th>1D</th><th>1W</th><th>%ile</th><th>Status</th></tr></thead>
        <tbody>
          {sp.map(s => (
            <tr key={s.key} title={`${s.label} · 1m ${s.d21 != null ? s.d21 + " bp" : "—"} · percentile over ~3y of daily history · ${s.trend || ""}`}>
              <td className="tsy-mxt">{s.key}</td>
              <td className={s.inverted ? "cd" : "cu"}><b>{s.bp >= 0 ? "+" : ""}{s.bp.toFixed(0)}</b></td>
              <td className={tsyBpCls(s.d1)}>{s.d1 != null ? `${s.d1 >= 0 ? "+" : ""}${s.d1.toFixed(0)}` : "—"}</td>
              <td className={tsyBpCls(s.d5)}>{s.d5 != null ? `${s.d5 >= 0 ? "+" : ""}${s.d5.toFixed(0)}` : "—"}</td>
              <td>{s.pctile != null ? s.pctile.toFixed(0) : "—"}</td>
              <td>{s.inverted ? <span className="tsy-pill down">INV</span> : <span className="tsy-pill up">POS</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TsyMini>
  );
}
function TsyOvMatrix({ core }) {
  const cards = (core.d && core.d.yields) || [];
  const rows = ["2Y", "5Y", "10Y", "30Y"].map(t => cards.find(c => c.tenor === t)).filter(Boolean);
  const cols = [["bp1d", "1D"], ["bp5d", "5D"], ["bp21d", "1M"], ["bp63d", "3M"], ["bp_ytd", "YTD"]];
  return (
    <TsyMini kicker="Rate change (bp)" right={<span className="tsy-mini-note" title={TSY_INV}>red = yields up</span>}>
      <table className="tsy-matrix num">
        <thead><tr><th></th>{cols.map(([k, l]) => <th key={k}>{l}</th>)}</tr></thead>
        <tbody>
          {rows.map(c => (
            <tr key={c.tenor}>
              <td className="tsy-mxt">{c.tenor}</td>
              {cols.map(([k]) => (
                <td key={k} className={tsyBpCls(c[k])}>{c[k] != null ? `${c[k] >= 0 ? "+" : ""}${c[k].toFixed(0)}` : "—"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </TsyMini>
  );
}
function TsyOvMove({ core }) {
  const m = core.d && core.d.move;
  const BANDS = [["low", "<80"], ["normal", "80–100"], ["elevated", "100–130"], ["high", "130–160"], ["extreme", ">180"]];
  return (
    <TsyMini kicker="Treasury volatility · MOVE" right={m && <span className={`tsy-pill ${m.regime === "low" || m.regime === "normal" ? "up" : m.regime === "elevated" ? "mut" : "down"}`}>{m.regime.toUpperCase()}</span>}>
      {m ? (
        <div title={`MOVE = Treasury option implied vol (NOT the equity VIX). 52w percentile ${m.pct52w != null ? m.pct52w.toFixed(0) : "—"}.`}>
          <div className="tsy-spot num"><b>{m.value}</b><TsyBp v={m.d1} /></div>
          {m.spark && <TsySpark pts={m.spark} w={200} h={30} tone="var(--warn)" />}
          <div className="tsy-spotrow num"><span>5d <TsyBp v={m.d5} /></span><span>1m <TsyBp v={m.d21} /></span><span>52w %ile <b>{m.pct52w != null ? m.pct52w.toFixed(0) : "—"}</b></span></div>
          <div className="tsy-bands">
            {BANDS.map(([k, range]) => (
              <span key={k} className={m.regime === k ? "on" : ""} title={`${k}: ${range}`}>{k}</span>
            ))}
          </div>
          <div className="tsy-mini-note">MOVE index · not the VIX</div>
        </div>
      ) : <TsyNA why="^MOVE quote unreachable — not estimated." />}
    </TsyMini>
  );
}
function TsyOvCorr({ mk }) {
  const c = mk.d && mk.d.correlations;
  const rows = ((c && c.rows) || []).filter(r => ["SPY", "QQQ", "IWM", "GLD", "UUP", "CL=F"].includes(r.sym));
  return (
    <TsyMini kicker="Correlation vs Δ10y · 60d">
      {c && c.ok && rows.length ? rows.map(r => (
        <div key={r.sym} className="tsy-kv" title={`${r.label}: 60-day correlation of daily returns vs the daily CHANGE in the 10y yield. Also 20d ${r.w20 != null ? r.w20 : "—"}, 120d ${r.w120 != null ? r.w120 : "—"}. Correlation ≠ causation.`}>
        <em>{r.sym === "CL=F" ? "OIL" : r.sym === "UUP" ? "USD" : r.sym === "GLD" ? "GOLD" : r.sym}</em>
          <b className={`num ${r.w60 != null ? (r.w60 >= 0 ? "cu" : "cd") : ""}`}>{r.w60 != null ? (r.w60 >= 0 ? "+" : "") + r.w60.toFixed(2) : "—"}</b>
        </div>
      )) : <TsyNA why="Correlation inputs unreachable." />}
    </TsyMini>
  );
}
function TsyOvCpiSummary({ inf }) {
  const rows = (inf.d && inf.d.ok && inf.d.rows) || [];
  const head = rows.find(r => r.key === "headline"), cc = rows.find(r => r.key === "core");
  if (!head || !head.ok) return <TsyMini kicker="CPI summary"><TsyNA why="CPI data unreachable." /></TsyMini>;
  const line = (label, cur, prev, pct, tip) => (
    <div className="tsy-kv" title={tip || `${label}: latest ${cur != null ? cur + "%" : "—"}, previous ${prev != null ? prev + "%" : "—"}. Consensus: no free feed — not estimated.`}>
      <em>{label}</em>
      <b className="num">{cur != null ? `${cur >= 0 ? "+" : ""}${cur.toFixed(2)}%` : "—"}</b>
      <span className="num muted">prev {prev != null ? `${prev >= 0 ? "+" : ""}${prev.toFixed(2)}` : "—"}</span>
      <span className={`num ${cur != null && prev != null ? (cur < prev ? "cu" : cur > prev ? "cd" : "") : ""}`}>{cur != null && prev != null ? (cur < prev ? "▼" : cur > prev ? "▲" : "→") : ""}</span>
      {pct != null && <span className="num muted">{pct.toFixed(0)}%ile</span>}
    </div>
  );
  return (
    <TsyMini kicker={`CPI summary · ${head.month}`} right={<span className="tsy-mini-note">est: no free consensus feed</span>}>
      {line("Headline YoY", head.yoy, head.yoy_prev, head.yoy_pctile_10y)}
      {line("Core YoY", cc && cc.yoy, cc && cc.yoy_prev, cc && cc.yoy_pctile_10y)}
      {line("Headline MoM", head.mom, head.mom_prev, null)}
      {line("Core MoM", cc && cc.mom, cc && cc.mom_prev, null)}
      {cc && cc.ann3m != null && <div className="tsy-kv" title="Compounded 3-month core CPI, annualized — the near-term run rate."><em>Core 3m annualized</em><b className="num">{cc.ann3m.toFixed(2)}%</b></div>}
      {cc && cc.ann6m != null && <div className="tsy-kv" title="Compounded 6-month core CPI, annualized."><em>Core 6m annualized</em><b className="num">{cc.ann6m.toFixed(2)}%</b></div>}
    </TsyMini>
  );
}
function TsyOvCpiTrend({ inf }) {
  const ch = (inf.d && inf.d.charts) || {};
  const series = [["headline_yoy", "Headline", "#4E9CF5"], ["core_yoy", "Core", "#E8A33D"], ["core_3m_ann", "3m ann.", "#3BD996"]]
    .filter(([k]) => ch[k]).map(([k, label, color]) => ({ key: k, label, color, pts: ch[k].slice(-60) }));
  if (!series.length) return <TsyMini kicker="CPI trend"><TsyNA why="CPI series unreachable." /></TsyMini>;
  return (
    <TsyMini kicker="CPI trend · 5y" right={
      <span className="tsy-legend">{series.map(s => <i key={s.key} style={{ color: s.color }}>— {s.label}</i>)}</span>
    }>
      <TsySeriesSvg series={series.map(s => ({ ...s, pts: s.pts }))} period="max" />
    </TsyMini>
  );
}
function TsyOvExpectations({ core }) {
  const e = (core.d && core.d.expectations) || {};
  const dec = core.d && core.d.decomposition;
  const rows = [["be5", "5y breakeven"], ["be10", "10y breakeven"], ["f5y5y", "5y5y fwd"],
                ["real5", "5y TIPS real"], ["real10", "10y TIPS real"], ["real30", "30y TIPS real"]];
  const driver = dec ? (dec.verdict.includes("both") ? "both" : dec.verdict.includes("real") ? "real" : dec.verdict.includes("expectations") ? "infl" : "unclear") : null;
  return (
    <TsyMini kicker="Inflation expectations · FRED daily">
      <table className="tsy-matrix num">
        <thead><tr><th style={{ textAlign: "left" }}></th><th>Value</th><th>1D</th><th>1W</th><th>1M</th><th>%ile</th></tr></thead>
        <tbody>
          {rows.map(([k, label]) => {
            const s = e[k];
            if (!s) return null;
            const bp = v => v != null ? v * 100 : null;
            const cell = v => <td className={tsyBpCls(bp(v))}>{v != null ? `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)}` : "—"}</td>;
            return (
              <tr key={k} title={`${s.label} — 52w range ${s.lo52w.toFixed(2)}–${s.hi52w.toFixed(2)}%.`}>
                <td className="tsy-mxt">{label}</td>
                <td><b>{s.value.toFixed(2)}%</b></td>
                {cell(s.d1)}{cell(s.d5)}{cell(s.d21)}
                <td>{s.pct52w != null ? s.pct52w.toFixed(0) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {dec && (
        <div className="tsy-drivers" title={`Δ10y nominal ${dec.nominal_bp} bp = real ${dec.real_bp} + breakeven ${dec.breakeven_bp} bp over ${dec.window} (FRED identity, common dates).`}>
          <em>10y driver ({dec.window}):</em>
          {[["real", "Real yields"], ["infl", "Inflation exp."], ["both", "Both"], ["unclear", "Unclear"]].map(([k, l]) => (
            <span key={k} className={driver === k ? "on" : ""}>{l}</span>
          ))}
        </div>
      )}
    </TsyMini>
  );
}
function TsyOvCot({ ct }) {
  const rows = ((ct.d && ct.d.rows) || []).filter(r => r.ok);
  return (
    <TsyMini kicker="COT positioning · CFTC weekly" right={rows[0] && <span className="tsy-datechip num">{rows[0].date}</span>}>
      {ct.loading ? <TsyLoading /> : rows.length ? (
        <table className="tsy-matrix num">
          <thead><tr><th></th><th>Asset mgr</th><th>wk Δ</th><th>Lev funds</th><th>wk Δ</th><th></th></tr></thead>
          <tbody>
            {rows.map(r => {
              const net = k => r[k] ? `${r[k].net >= 0 ? "+" : ""}${(r[k].net / 1000).toFixed(0)}k` : "—";
              const wk = k => r[k] && r[k].wk_chg != null ? `${r[k].wk_chg >= 0 ? "+" : ""}${(r[k].wk_chg / 1000).toFixed(0)}k` : "—";
              const crowded = ["asset_mgr", "lev_funds"].map(k => r[k] && r[k].crowded ? `${k === "asset_mgr" ? "AM" : "LEV"} ${r[k].crowded}` : null).filter(Boolean)[0];
              return (
                <tr key={r.code} title={`${r.code}: asset managers ${r.asset_mgr ? r.asset_mgr.net.toLocaleString() : "—"}${r.asset_mgr && r.asset_mgr.pctile != null ? ` (${r.asset_mgr.pctile.toFixed(0)}th pctile 3y)` : ""} · leveraged ${r.lev_funds ? r.lev_funds.net.toLocaleString() : "—"} · dealers ${r.dealer ? r.dealer.net.toLocaleString() : "—"}.${r.fallback ? ` Source: ${r.fallback}.` : ""}`}>
                  <td className="tsy-mxt">{r.code}</td>
                  <td className={r.asset_mgr && r.asset_mgr.net >= 0 ? "cu" : "cd"}>{net("asset_mgr")}</td>
                  <td className="muted">{wk("asset_mgr")}</td>
                  <td className={r.lev_funds && r.lev_funds.net >= 0 ? "cu" : "cd"}>{net("lev_funds")}</td>
                  <td className="muted">{wk("lev_funds")}</td>
                  <td>{crowded && <span className={`tsy-pill ${crowded.includes("long") ? "up" : "down"}`}>{crowded.toUpperCase()}</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : <TsyErr err={ct.err || "CFTC unavailable"} retry={ct.retry} />}
    </TsyMini>
  );
}
function TsyOvEvents({ core, inf }) {
  const ev = (core.d && core.d.events) || {};
  const cpi = ev.next_cpi, fomc = ev.next_fomc, jobs = ev.next_jobs;
  const rows = (inf.d && inf.d.ok && inf.d.rows) || [];
  const head = rows.find(r => r.key === "headline"), cc = rows.find(r => r.key === "core");
  const avg = inf.d && inf.d.reactions && inf.d.reactions.avg_abs;
  return (
    <TsyMini kicker="Next CPI release · event risk">
      {cpi && cpi.date && (
        <div className="tsy-kv big" title={`Next CPI per the BLS schedule, ${cpi.time_et}. Consensus estimates: no free reliable feed — not estimated.`}>
          <em>CPI</em><b className="num">{cpi.date}</b>
          <span className="num warn">{cpi.countdown ? `${cpi.countdown.days}d ${cpi.countdown.hours}h` : ""}</span>
        </div>
      )}
      {head && head.ok && (
        <div className="tsy-kv" title="Previous print (the June data). Est column: no free consensus source — not estimated.">
          <em>Prev headline / core YoY</em>
          <b className="num">{head.yoy != null ? head.yoy.toFixed(2) : "—"}% / {cc && cc.yoy != null ? cc.yoy.toFixed(2) : "—"}%</b>
          <span className="num muted">est —</span>
        </div>
      )}
      {avg && (avg.spy != null || avg.qqq != null) && (
        <div className="tsy-kv" title={`Average ABSOLUTE close-to-close move on the last ${avg.n} CPI release days (delayed Yahoo closes). A magnitude read, not a direction forecast.`}>
          <em>Avg CPI-day move</em>
          <b className="num">SPY ±{avg.spy != null ? avg.spy.toFixed(2) : "—"}% · QQQ ±{avg.qqq != null ? avg.qqq.toFixed(2) : "—"}%</b>
        </div>
      )}
      {avg && avg.y10_bp != null && (
        <div className="tsy-kv" title="Average absolute 10y yield move on CPI days (FRED daily)."><em>Avg CPI-day 10y move</em><b className="num">±{avg.y10_bp.toFixed(1)} bp</b></div>
      )}
      {fomc && fomc.date && <div className="tsy-kv" title={fomc.source}><em>FOMC</em><b className="num">{fomc.date}</b><span className="num">{fomc.days}d</span></div>}
      {jobs && <div className="tsy-kv" title={jobs.source}><em>Jobs report</em><b className="num">{jobs.date}</b></div>}
      {(ev.upcoming_auctions || []).slice(0, 2).map((a, i) => (
        <div key={i} className="tsy-kv" title={`${a.term} ${a.type} auction${a.offering ? `, $${(a.offering / 1e9).toFixed(0)}B` : ""} (TreasuryDirect).`}>
          <em>Auction</em><b className="num">{a.auction_date}</b><span>{a.term} {a.type}</span>
        </div>
      ))}
    </TsyMini>
  );
}
function TsyOvFed({ fd }) {
  const d = fd.d || {};
  const t = d.target, nm = d.next_meeting;
  const path = (d.implied_path || []).slice(0, 4);
  return (
    <TsyMini kicker="Fed policy · rate expectations" right={t && <span className="tsy-mini-note">official range</span>}>
      {fd.loading ? <TsyLoading /> : (
        <div>
          {t && <div className="tsy-spot num" title={`Official target range as of ${t.date} (${t.source}).`}><b>{t.lower.toFixed(2)}–{t.upper.toFixed(2)}%</b></div>}
          {nm && <div className="tsy-kv"><em>Next FOMC</em><b className="num">{nm.date}</b><span className="num warn">{nm.days}d</span></div>}
          {path.length > 0 ? path.map(p => (
            <div key={p.month} className="tsy-kv" title="Implied average fed funds = 100 − ZQ futures price (CME 30-day FF futures via Yahoo, delayed).">
              <em>{p.month}</em><b className="num">{p.implied_rate.toFixed(2)}%</b>
              <span className={`num ${tsyBpCls(p.d1_bp)}`}>{p.d1_bp != null ? `${p.d1_bp >= 0 ? "+" : ""}${p.d1_bp.toFixed(0)}` : ""}</span>
            </div>
          )) : <div className="tsy-mini-note">Implied path: <TsyNA why="Fed funds futures unreachable — not estimated. Per-meeting probabilities need CME FedWatch." /></div>}
          {d.yearend && (
            <div className="tsy-kv" title="Vs the current target midpoint.">
              <em>Priced by {d.yearend.month}</em><b className="num">{d.yearend.implied_rate.toFixed(2)}%</b>
              <span className="num">{Math.abs(d.yearend.cuts_25bp).toFixed(1)}×25bp {d.yearend.cuts_25bp >= 0 ? "cuts" : "hikes"}</span>
            </div>
          )}
        </div>
      )}
    </TsyMini>
  );
}
function TsyOvSense({ apiFetch, onOpenTicker }) {
  const [board, setBoard] = useState(null);
  useEffect(() => {
    sharedJson(apiFetch, "/api/treasury/sense", 300000).then(setBoard).catch(() => {});
  }, []);
  const rows = ((board && board.rows) || [])
    .map(r => ({ ticker: r.ticker, f: r.y10 }))
    .filter(r => r.f && r.f.ok)
    .sort((a, b) => a.f.beta10bp - b.f.beta10bp)
    .slice(0, 6);
  return (
    <TsyMini kicker="Rate sensitivity · most hurt by rising 10y" right={<span className="tsy-mini-note">from your watchlist scan</span>}>
      {rows.length ? rows.map(r => (
        <div key={r.ticker} className="tsy-kv tsy-rowlink" onClick={() => onOpenTicker && onOpenTicker(r.ticker)}
             title={`${r.ticker}: ${r.f.beta10bp}% avg move per +10bp in the 10y (n=${r.f.n}, t=${r.f.t}, ${r.f.conf} confidence). Click to open in Analyze.`}>
          <em>{r.ticker}</em>
          <b className={`num ${r.f.beta10bp >= 0 ? "cu" : "cd"}`}>{r.f.beta10bp >= 0 ? "+" : ""}{r.f.beta10bp}%/10bp</b>
          <span className="num muted">r {r.f.corr}</span>
        </div>
      )) : <div className="tsy-mini-note">No scan yet — run it in the Rate Sensitivity section below. Only statistically meaningful names (|t| ≥ 2) are shown.</div>}
    </TsyMini>
  );
}

/* ── The tab ───────────────────────────────────────────────────────────── */
function TreasuriesTab({ apiFetch, onOpenTicker }) {
  const core = useTsy(apiFetch, "core", 900000);
  const mk = useTsy(apiFetch, "markets", 900000);
  const fd = useTsy(apiFetch, "fed", 1800000);
  const au = useTsy(apiFetch, "auctions", 3600000);
  const ct = useTsy(apiFetch, "cot", 3600000);
  const inf = useTsy(apiFetch, "inflation", 3600000);
  if (core.loading) return <div className="card tsy-card"><TsyLoading /></div>;
  if (!core.d || !core.d.ok) return <div className="card tsy-card"><div className="kicker">US Treasuries</div><TsyErr err={core.err || "Treasury data unavailable"} retry={core.retry} /></div>;
  return (
    <div className="tsy">
      {/* ── Glance terminal ── */}
      <div className="tsy-ovrow r1">
        <TsyOvYields core={core} />
        <TsyOv10Y core={core} />
        <TsyOvFutures mk={mk} />
      </div>
      <div className="tsy-ovrow r2">
        <div className="card tsy-mini">
          <div className="tsy-mini-h"><em>Treasury yield curve</em><span className="tsy-mini-note">vs 1 month ago (dashed)</span></div>
          <TsyCurveSvg snaps={core.d.snapshots || {}} cmp="1m" />
        </div>
        <TsyOvAnalysis core={core} />
        <TsyOvAuctions au={au} />
      </div>
      <div className="tsy-ovrow r3">
        <TsyOvSpreads core={core} />
        <TsyOvMatrix core={core} />
        <TsyOvMove core={core} />
        <TsyOvCorr mk={mk} />
      </div>
      <div className="tsy-ovrow r3b">
        <TsyOvCpiSummary inf={inf} />
        <TsyOvCpiTrend inf={inf} />
        <TsyOvExpectations core={core} />
      </div>
      <div className="tsy-ovrow r4">
        <TsyOvCot ct={ct} />
        <TsyOvEvents core={core} inf={inf} />
        <TsyOvFed fd={fd} />
        <TsyOvSense apiFetch={apiFetch} onOpenTicker={onOpenTicker} />
      </div>

      {/* ── Depth ── */}
      <TsySignalsCard core={core} />
      <TsyCpiCard apiFetch={apiFetch} />
      <TsyFold kicker="Interactive curve · compare any period, table view" title="Yield curve workbench">
        <TsyCurveCard core={core} />
      </TsyFold>
      <TsyFold kicker="History · how markets traded past prints" title="CPI releases & market reaction" hint="expand to load">
        <TsyCpiReactions apiFetch={apiFetch} />
      </TsyFold>
      <TsyMarketsCards apiFetch={apiFetch} onOpenTicker={onOpenTicker} />
      <TsyFold kicker="Full spread detail · changes and percentiles" title="Treasury spreads detail">
        <TsySpreadsCard core={core} />
        <TsyExpectationsCard core={core} />
      </TsyFold>
      <TsyFold kicker="Supply · every result vs its prior 10" title="Treasury auctions detail">
        <TsyAuctions apiFetch={apiFetch} />
      </TsyFold>
      <TsyFold kicker="CFTC weekly detail · percentiles, weekly changes" title="COT detail">
        <TsyCot apiFetch={apiFetch} />
      </TsyFold>
      <TsyFold kicker="Rolling correlation vs Δ10y · all windows" title="Cross-asset relationships">
        <TsyCorrTable apiFetch={apiFetch} />
      </TsyFold>
      <TsyFold kicker="Your watchlist × yield factors" title="Rate sensitivity watchlist" hint="expand to scan">
        <TsySense apiFetch={apiFetch} onOpenTicker={onOpenTicker} />
      </TsyFold>
      <TsyFold kicker="Threshold rules on the displayed data" title="Rates alerts" hint="expand to configure">
        <TsyAlertsCard core={core} />
      </TsyFold>
      <TsyFold kicker="Fed detail · market-implied path by month" title="Fed rate expectations detail">
        <TsyFedCard apiFetch={apiFetch} />
      </TsyFold>
    </div>
  );
}

Object.assign(window, { TreasuriesTab: React.memo(TreasuriesTab) });
