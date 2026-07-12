/* JerryTrade /next — Phase 1 of the Decision Cockpit (v4.0.1-next).
 *
 * A PARALLEL app: its own bundle + stylesheet, served at /next, reading the
 * SAME live APIs as the classic site. Nothing in the classic frontend is
 * imported or modified — if this page has a bug, / is unaffected.
 *
 * Phase 1 scope (the frame, live):
 *   • Command bar: real logo, ticker quote, live ET clock, posture, earnings
 *     chip, SCHWAB/UW pills, weather, version, alerts count.
 *   • Permanent market strip — the 10 macro instruments with sparklines,
 *     visible on every tab (from /api/market_overview).
 *   • YOUR tab bar — every tab, your names, your order + the Sites row.
 *   • Today: radar top long/short, 0DTE juice, pattern watches, breadth,
 *     today's events, positions, watchlist alerts, daily highs/lows +
 *     52-week extremes — all real data, stale-not-blank.
 *   • Bottom tape: market headlines.
 *   • Every other tab shows exactly what lands there in Phase 2 and links
 *     back to the classic site meanwhile — nothing is lost at any moment.
 */

const { useState, useEffect, useRef, useMemo } = React;
const NEXT_VERSION = "4.0.1-next";

/* ── api ─────────────────────────────────────────────────────────────────── */
const CFG = (typeof window !== "undefined" && window.__APP_CONFIG) || {};
function api(path) {
  const headers = {};
  if (CFG.apiKey) headers["X-API-Key"] = CFG.apiKey;
  return fetch(path, { headers }).then(r => r.json());
}
/* Poll helper with stale-keep: on any failure the previous data stays and a
   `stale` flag flips — boards never blank (same contract as the classic app). */
function usePoll(path, ms, enabled = true) {
  const [state, setState] = useState({ data: null, stale: false, at: null });
  useEffect(() => {
    if (!enabled) return;
    let dead = false, t;
    const tick = () => {
      if (document.hidden) { t = setTimeout(tick, ms); return; }
      api(path).then(d => {
        if (dead) return;
        if (d && !d.error) setState({ data: d, stale: false, at: new Date() });
        else setState(s => ({ ...s, stale: true }));
      }).catch(() => { if (!dead) setState(s => ({ ...s, stale: true })); })
        .finally(() => { if (!dead) t = setTimeout(tick, ms); });
    };
    tick();
    return () => { dead = true; clearTimeout(t); };
  }, [path, ms, enabled]);
  return state;
}
const pick = (o, ...keys) => { for (const k of keys) if (o && o[k] != null) return o[k]; return null; };
/* API list fields vary (array, or keyed object like market_breadth.stocks) —
   coerce to an array so no card can crash on shape. */
const asArr = (v) => Array.isArray(v) ? v : (v && typeof v === "object" ? Object.values(v) : []);
const fmtPct = v => (v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`);
const fmtN = v => (v == null ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }));

/* ── tiny SVG sparkline ──────────────────────────────────────────────────── */
function Spark({ vals, up, w = 76, h = 38 }) {
  if (!vals || vals.length < 3) return null;
  const lo = Math.min(...vals), hi = Math.max(...vals), span = Math.max(1e-9, hi - lo);
  const pts = vals.map((v, i) => [i / (vals.length - 1) * w, h - 5 - (v - lo) / span * (h - 10)]);
  const line = "M" + pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("L");
  const col = up ? "#3BD996" : "#F56D77";
  const gid = "sg" + Math.abs((vals[0] * 7919 + vals.length) | 0) + (up ? "u" : "d");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <defs><linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor={col} stopOpacity=".22" /><stop offset="1" stopColor={col} stopOpacity="0" /></linearGradient></defs>
      <path d={`${line}L${w},${h}L0,${h}Z`} fill={`url(#${gid})`} />
      <path d={line} fill="none" stroke={col} strokeWidth="1.5" />
      <circle r="2.2" fill={col} cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} />
    </svg>
  );
}

/* ── command bar ─────────────────────────────────────────────────────────── */
function Clock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);
  const et = now.toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit", second: "2-digit" });
  const d = now.toLocaleDateString("en-US", { timeZone: "America/New_York", weekday: "short", month: "numeric", day: "numeric" });
  return <div className="stat" title="Live session clock (Eastern)"><em>Live · {d}</em><b>{et}</b></div>;
}

function Weather() {
  const [wx, setWx] = useState(null);
  const [mode, setMode] = useState(() => { try { return localStorage.getItem("next_wx") || "yonkers"; } catch (e) { return "yonkers"; } });
  useEffect(() => {
    let dead = false;
    const load = (lat, lon) =>
      fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,weather_code&temperature_unit=fahrenheit`)
        .then(r => r.json()).then(d => { if (!dead && d && d.current) setWx(d.current); }).catch(() => {});
    if (mode === "device" && navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(p => load(p.coords.latitude, p.coords.longitude), () => load(40.93, -73.9));
    } else load(40.93, -73.9); // Yonkers
    return () => { dead = true; };
  }, [mode]);
  const icon = wx ? (wx.weather_code === 0 ? "☀" : wx.weather_code < 50 ? "☁" : "🌧") : "☁";
  return (
    <span className="pill" style={{ cursor: "pointer" }}
          title={`Weather — ${mode === "yonkers" ? "Yonkers" : "your location"}. Tap to toggle, same as the classic app.`}
          onClick={() => { const m = mode === "yonkers" ? "device" : "yonkers"; setMode(m); try { localStorage.setItem("next_wx", m); } catch (e) {} }}>
      {icon} {wx ? Math.round(wx.temperature_2m) + "°" : "—"}
    </span>
  );
}

function CommandBar({ ticker, setTicker, alerts }) {
  const [input, setInput] = useState(ticker);
  const q = usePoll(`/api/quote?symbol=${encodeURIComponent(ticker)}`, 15000);
  const ctx = usePoll("/api/market_context", 120000);
  const quote = q.data || {};
  const px = pick(quote, "last", "price", "mark", "close");
  const chg = pick(quote, "chg_pct", "change_pct", "pct", "percent_change");
  const earn = pick(quote, "next_earnings", "earnings");
  const posture = pick(ctx.data || {}, "posture", "regime", "today") || "—";
  const postureStr = typeof posture === "string" ? posture : pick(posture, "posture", "label") || "—";
  return (
    <div className="cmd">
      <div className="brand" title="JerryTrade /next — the parallel Decision Cockpit. Your classic site is untouched at /">
        <div className="logo" style={{ overflow: "hidden" }}><img src="/assets/app-logo.png" alt="Jerry" style={{ width: "100%", height: "100%", objectFit: "contain" }} /></div>
        <div className="brandtx"><b><span style={{ color: "var(--fg)" }}>Jer</span><span className="ry" style={{ color: "var(--acc)" }}>ry</span>Trade</b><span>DECISION COCKPIT · /next</span></div>
      </div>
      <form className="search" style={{ cursor: "text" }} title="Type a ticker and press Enter — every card follows it (Phase 2 wires full search + ask)."
            onSubmit={e => { e.preventDefault(); const s = input.trim().toUpperCase(); if (s) setTicker(s); }}>
        ⌕&nbsp;<input value={input} onChange={e => setInput(e.target.value)}
               style={{ background: "none", border: "none", outline: "none", color: "var(--fg)", font: "inherit", width: "100%" }}
               placeholder="Ticker… (Enter)" spellCheck={false} />
        <span className="k">⏎</span>
      </form>
      <div className="tkr" title={`Live quote for ${ticker}${q.stale ? " — STALE (last good kept)" : ""}`}>
        <span className="sym">{ticker}</span>
        <span className="px num">{px != null ? fmtN(px) : "—"}</span>
        <span className="chgp" style={chg != null && chg < 0 ? { color: "var(--down)", background: "var(--down-dim)" } : null}>{fmtPct(chg)}</span>
        {q.stale && <span className="chip wn" title="Quote fetch failed — showing the last good value">STALE</span>}
      </div>
      <div className="spacer"></div>
      <Clock />
      <div className="vdiv"></div>
      <div className="stat" title="Market posture — SPY vs its 50/200-day averages"><em>Posture</em><b className={/bull/i.test(postureStr) ? "cu" : /bear|down/i.test(postureStr) ? "cd" : "cw"}>{String(postureStr).toUpperCase().slice(0, 14)}</b></div>
      <div className="vdiv"></div>
      <div className="stat" title="Next earnings for the active ticker"><em>Earnings</em><b>{earn ? String(earn).slice(5, 10).replace("-", "/") : "—"}</b></div>
      <div className="vdiv"></div>
      <span className="pill live" title="Schwab data link"><span className="dot"></span>SCHWAB</span>
      <span className="pill uwp" title="Unusual Whales link"><span className="dot"></span>UW</span>
      <Weather />
      <span className="pill" style={{ color: "var(--fg3)" }} title="Parallel-app build — the classic site keeps its own version pill">v{NEXT_VERSION}</span>
      <div className="bell" title={`${alerts} live signals on the Today tab right now`}>🔔{alerts > 0 && <i>{alerts}</i>}</div>
    </div>
  );
}

/* ── permanent market strip ──────────────────────────────────────────────── */
function MarketStrip() {
  const mo = usePoll("/api/market_overview", 30000);
  const list = asArr(mo.data && (mo.data.instruments || mo.data.rows));
  return (
    <div className="mkt" style={{ gridTemplateColumns: `repeat(${Math.max(1, list.length || 10)},1fr)` }}>
      {list.length === 0 && <div className="mk"><div className="lbl">MARKET STRIP</div><div className="row"><span className="v" style={{ color: "var(--fg3)" }}>loading…</span></div></div>}
      {list.map((m, i) => {
        const label = pick(m, "label", "name", "sym") || "";
        const last = pick(m, "last", "price", "value");
        const pct = pick(m, "chg_pct", "pct", "change_pct");
        const spark = asArr(pick(m, "spark", "history", "closes"));
        const up = pct != null ? pct >= 0 : (spark.length > 1 ? spark[spark.length - 1] >= spark[0] : true);
        return (
          <div className="mk" key={i} title={`${label} — permanently visible on every tab.${mo.stale ? " (STALE — last good kept)" : ""}`}>
            {spark.length > 2 && <Spark vals={spark.slice(-40)} up={up} />}
            <div className="lbl">{String(label).toUpperCase()}</div>
            <div className="row">
              <span className="v">{fmtN(last)}</span>
              <span className={`c ${up ? "cu" : "cd"}`}>{fmtPct(pct)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── tab bar (YOUR tabs) ─────────────────────────────────────────────────── */
const TABS = [
  ["today", "Today"], ["trade", "Trade"], ["discover", "Discover"], ["analyze", "Analyze"],
  ["patterns", "Patterns"], ["news", "News"], ["flow", "Flow"], ["scanners", "Scanners"],
  ["juice", "0DTE Juice"], ["backtest", "Backtest"], ["breadth", "Breadth"], ["journal", "Journal"],
  ["watchlist", "Watchlist"], ["streaks", "Streaks"], ["calendar", "Market Calendar"], ["manage", "Manage"],
];
const SITES = [["finviz", "Finviz", "fvz"], ["tview", "TradingView", "tvw"], ["whales", "Unusual Whales", "uww"]];

function TabBar({ active, onChange, earn }) {
  return (
    <div className="tabbar">
      <div className="trow">
        {TABS.map(([id, label]) => (
          <button key={id} className={`tab ${active === id ? "on" : ""}`} onClick={() => onChange(id)}
                  title={id === "today" ? "NEW, additive — the morning cockpit. Every other tab is exactly yours." : `${label} — same tab as the classic site`}>
            {label}
          </button>
        ))}
        {earn && <span className="earn" title="Next earnings for the active ticker">{earn}</span>}
      </div>
      <div className="trow sites">
        <span className="slbl">Sites -</span>
        {SITES.map(([id, label, cls]) => (
          <button key={id} className={`tab site ${cls} ${active === id ? "on" : ""}`} onClick={() => onChange(id)}>{label}</button>
        ))}
      </div>
    </div>
  );
}

/* ── Today cards ─────────────────────────────────────────────────────────── */
function Card({ title, color, info, more, chip, children, span = 3 }) {
  return (
    <div className="card" style={{ gridColumn: `span ${span}` }}>
      <div className="chd">
        <h3 style={color ? { color } : null}>{title}</h3>
        {info && <span className="i" title={info}>i</span>}
        {chip}
        {more && <span className="more">{more}</span>}
      </div>
      {children}
    </div>
  );
}

function OpRow({ badge, badgeCls, nm, why, px, pc, onClick }) {
  return (
    <div className="op" onClick={onClick} style={{ cursor: onClick ? "pointer" : "default", gridTemplateColumns: "36px 1fr auto" }}>
      <span className={`score ${badgeCls || "s-hi"}`}>{badge}</span>
      <div><div className="nm">{nm}</div><div className="why">{why}</div></div>
      <div><div className="px num">{px}</div><div className={`pc ${String(pc).startsWith("+") ? "cu" : "cd"}`}>{pc}</div></div>
    </div>
  );
}

function RadarCard({ side, onOpen }) {
  const r = usePoll("/api/radar", 30000);
  const rows = asArr(r.data && r.data[side]).slice(0, 5);
  const col = side === "long" ? "var(--up)" : "var(--down)";
  return (
    <Card title={`${side === "long" ? "▲ Top Long" : "▼ Top Short"} — Radar`} color={col}
          info={`Two-stage scan of your $5B+ watchlist. Score = stretch+exhaustion+location+confirmation+context. Push ≥85, toast ≥80.${r.stale ? " STALE — last scan kept." : ""}`}
          chip={r.stale ? <span className="chip wn">STALE</span> : (r.data && r.data.market_open === false ? <span className="chip mut" title="Radar scans during market hours">OFF-HOURS</span> : null)}>
      <div className="oplist">
        {rows.length === 0 && <div style={{ padding: "6px 10px 10px", fontSize: 11.5, color: "var(--fg3)" }}>No {side} candidates right now — the radar only surfaces real setups.</div>}
        {rows.map((s, i) => (
          <OpRow key={i}
                 badge={Math.round(pick(s, "score", "total") || 0)}
                 badgeCls={(pick(s, "score", "total") || 0) >= 80 ? "s-hi" : (pick(s, "score", "total") || 0) >= 65 ? "s-md" : "s-lo"}
                 nm={pick(s, "symbol", "sym")}
                 why={pick(s, "note", "reason", "why") || `${side === "long" ? "near day low" : "near day high"} · vs VWAP ${fmtN(pick(s, "vwap_dist", "vwap_sigma"))}σ`}
                 px={fmtN(pick(s, "price", "last"))} pc={fmtPct(pick(s, "chg_pct", "day_pct", "pct"))}
                 onClick={() => onOpen(pick(s, "symbol", "sym"))} />
        ))}
      </div>
    </Card>
  );
}

function JuiceCard({ onOpen }) {
  const j = usePoll("/api/juice", 60000);
  const rows = asArr(j.data && j.data.rows).slice(0, 5);
  return (
    <Card title="◈ 0DTE Juice" color="var(--info)"
          info="Juice Score: straddle premium vs expected move, IV rank, spread quality, volume/OI. DEFINED-risk first when earnings inside the window or spot > $400."
          chip={j.data && j.data.note ? <span className="chip wn" title={j.data.note}>LAST SCAN KEPT</span> : null}>
      <div className="oplist">
        {rows.length === 0 && <div style={{ padding: "6px 10px 10px", fontSize: 11.5, color: "var(--fg3)" }}>Juice board fills during market hours.</div>}
        {rows.map((s, i) => (
          <OpRow key={i} badge={Math.round(pick(s, "juice", "score") || 0)}
                 badgeCls={(pick(s, "juice", "score") || 0) >= 80 ? "s-hi" : "s-md"}
                 nm={pick(s, "symbol", "sym")}
                 why={`${pick(s, "expiry", "exp") || ""} · IV ${fmtN(pick(s, "iv", "atm_iv"))} · EM ${fmtN(pick(s, "em_pct"))}%`}
                 px={fmtN(pick(s, "price", "spot", "last"))}
                 pc={pick(s, "straddle") != null ? `$${fmtN(s.straddle)}` : fmtPct(pick(s, "chg_pct"))}
                 onClick={() => onOpen(pick(s, "symbol", "sym"))} />
        ))}
      </div>
    </Card>
  );
}

function WatchesCard({ onOpen }) {
  const w = usePoll("/api/patterns/watches", 120000);
  const rows = asArr(w.data && w.data.watches).slice(0, 5);
  return (
    <Card title="⚑ Pattern watches" color="var(--purple)"
          info="Your watched behaviors — re-checked against fresh daily data every 30 minutes in market hours; push fires the day a setup triggers.">
      <div className="oplist">
        {rows.length === 0 && <div style={{ padding: "6px 10px 10px", fontSize: 11.5, color: "var(--fg3)" }}>No watches yet — add them from any discovered pattern (Patterns tab · classic site until Phase 2).</div>}
        {rows.map((s, i) => (
          <OpRow key={i} badge={s.triggered ? "⚑" : "·"} badgeCls={s.triggered ? "s-hi" : "s-md"}
                 nm={s.symbol} why={(s.sentence || "").slice(0, 64) + "…"}
                 px={s.triggered ? "TRIGGERED" : "quiet"} pc={s.checked ? String(s.checked).slice(5) : ""}
                 onClick={() => onOpen(s.symbol)} />
        ))}
      </div>
    </Card>
  );
}

function BreadthCard() {
  const b = usePoll("/api/market_breadth", 120000);
  const stocks = asArr(b.data && b.data.stocks);
  const { adv, dec, flat } = useMemo(() => {
    let a = 0, d = 0, f = 0;
    for (const s of stocks) {
      const c = pick(s, "chg_pct", "day_pct", "pct", "change_pct");
      if (c == null) continue;
      if (c > 0.05) a++; else if (c < -0.05) d++; else f++;
    }
    return { adv: a, dec: d, flat: f };
  }, [stocks]);
  const n = Math.max(1, adv + dec + flat);
  const pctA = Math.round(adv / n * 100), pctD = Math.round(dec / n * 100);
  const dash = (pctA / 100) * 264;
  return (
    <Card title="Market breadth" info={`Advancers vs decliners across your watchlist universe (${n} scored).`}>
      <div className="gauge">
        <svg width="104" height="104" viewBox="0 0 104 104">
          <circle cx="52" cy="52" r="42" fill="none" stroke="var(--bg4)" strokeWidth="10" />
          <circle cx="52" cy="52" r="42" fill="none" stroke="#3BD996" strokeWidth="10"
                  strokeDasharray={`${dash} 264`} strokeLinecap="round" transform="rotate(-90 52 52)"
                  style={{ filter: "drop-shadow(0 0 6px rgba(56,225,160,.4))" }} />
          <text x="52" y="50" textAnchor="middle" fill="var(--fg)" fontFamily="JetBrains Mono,monospace" fontSize="22" fontWeight="800">{pctA}</text>
          <text x="52" y="66" textAnchor="middle" fill="var(--fg3)" fontFamily="JetBrains Mono,monospace" fontSize="7.5">{pctA >= 55 ? "BULLISH" : pctA <= 45 ? "BEARISH" : "MIXED"}</text>
        </svg>
        <div className="dlegend">
          <div><span className="dot2" style={{ background: "var(--up)" }}></span>Advancing<b>{pctA}%</b></div>
          <div><span className="dot2" style={{ background: "var(--down)" }}></span>Declining<b>{pctD}%</b></div>
          <div><span className="dot2" style={{ background: "var(--fg4)" }}></span>Flat<b>{Math.max(0, 100 - pctA - pctD)}%</b></div>
        </div>
      </div>
    </Card>
  );
}

function EventsCard() {
  const ec = usePoll("/api/market_calendar/economic", 300000);
  const events = asArr(ec.data && ec.data.events).slice(0, 6);
  return (
    <Card title="Today's events" info="Macro prints + market schedule — the Market Calendar tab has the full week." more="calendar →">
      <div className="tl">
        {events.length === 0 && <div style={{ fontSize: 11.5, color: "var(--fg3)", padding: "4px 0 10px" }}>No scheduled events loaded.</div>}
        {events.map((e, i) => (
          <div className="ev" key={i}>
            <b>{(pick(e, "time", "when") || "").slice(0, 6) || "—"}</b><span className="nd"></span>
            <span>{pick(e, "event", "title", "name")}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function PositionsCard({ onOpen }) {
  const p = usePoll("/api/broker/owned", 120000);
  const syms = asArr(p.data && p.data.symbols);
  return (
    <Card title="Positions" info="Symbols currently held at Schwab (import detail + journal land here in Phase 2)." more="classic →">
      <div className="kpis">
        <div className="kpi"><em>Configured</em><b className={p.data && p.data.configured ? "cu" : "cw"}>{p.data ? (p.data.configured ? "YES" : "NO") : "—"}</b></div>
        <div className="kpi"><em>Held symbols</em><b>{syms.length}</b></div>
      </div>
      <div className="xchips" style={{ paddingTop: 0 }}>
        {syms.slice(0, 10).map((s, i) => <span key={i} className="chip in" style={{ cursor: "pointer" }} onClick={() => onOpen(typeof s === "string" ? s : s.symbol)}>{typeof s === "string" ? s : s.symbol}</span>)}
        {syms.length === 0 && <span style={{ fontSize: 11.5, color: "var(--fg3)" }}>No positions loaded.</span>}
      </div>
    </Card>
  );
}

function AlertsCard({ onOpen }) {
  const a = usePoll("/api/watchlist_alerts", 180000);
  const rows = asArr(a.data && a.data.alerts).slice(0, 5);
  return (
    <Card title="Watchlist alerts" info="Analyst moves and high-impact changes across your watchlist (background scan).">
      {rows.length === 0 && <div style={{ padding: "0 16px 12px", fontSize: 11.5, color: "var(--fg3)" }}>No active alerts.</div>}
      {rows.map((al, i) => (
        <div className="alrow" key={i} style={{ cursor: "pointer" }} onClick={() => onOpen(pick(al, "symbol", "sym"))}>
          <span className="nm">{pick(al, "symbol", "sym")}</span>
          <span style={{ flex: 1, margin: "0 10px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{pick(al, "title", "note", "kind", "text")}</span>
          <span className="chip in">{String(pick(al, "kind", "type") || "alert").slice(0, 10)}</span>
        </div>
      ))}
    </Card>
  );
}

function ExtremesBoard({ onOpen }) {
  const hi = usePoll("/api/daily_highs", 120000);
  const lo = usePoll("/api/daily_lows", 120000);
  const wt = usePoll("/api/watchlist_table", 300000);
  const rowsHi = asArr(hi.data && hi.data.rows).slice(0, 6);
  const rowsLo = asArr(lo.data && lo.data.rows).slice(0, 6);
  const { near52H, near52L } = useMemo(() => {
    const rows = asArr(wt.data && (wt.data.rows || wt.data.board));
    const H = [], L = [];
    for (const r of rows) {
      const px = pick(r, "price", "last"), h = pick(r, "high52", "week52_high", "high_52w"), l = pick(r, "low52", "week52_low", "low_52w");
      if (px && h && px >= h * 0.98) H.push(r);
      if (px && l && l > 0 && px <= l * 1.02) L.push(r);
    }
    return { near52H: H.slice(0, 6), near52L: L.slice(0, 6) };
  }, [wt.data]);
  const Col = ({ head, cls, rows, title }) => (
    <div className="excol">
      <div className={`exh ${cls}`} title={title}>{head}</div>
      {rows.length === 0 && <div style={{ padding: "4px 10px", fontFamily: "var(--mono)", fontSize: 10, color: "var(--fg4)" }}>none right now</div>}
      {rows.map((r, i) => (
        <div className="exr" key={i} style={{ cursor: "pointer" }} onClick={() => onOpen(pick(r, "symbol", "sym"))}>
          <span className="s">{pick(r, "symbol", "sym")}</span>
          <span className="p">{fmtN(pick(r, "price", "last"))}</span>
          <span className={`c ${(pick(r, "chg_pct", "day_pct", "pct") || 0) >= 0 ? "cu" : "cd"}`}>{fmtPct(pick(r, "chg_pct", "day_pct", "pct"))}</span>
        </div>
      ))}
    </div>
  );
  return (
    <div className="card exgrid">
      <Col head="▲ Daily high" cls="cu" rows={rowsHi} title="Watchlist names printing new session highs" />
      <Col head="▼ Daily low" cls="cd" rows={rowsLo} title="Watchlist names printing new session lows" />
      <Col head="◆ Near 52W high" cls="cu" rows={near52H} title="Within 2% of the 52-week high — full watchlist" />
      <Col head="◇ Near 52W low" cls="cd" rows={near52L} title="Within 2% of the 52-week low — full watchlist, your bottom-fishing pool" />
    </div>
  );
}

function Today({ onOpen }) {
  return (
    <section className="ws on">
      <div className="tgrid">
        <RadarCard side="long" onOpen={onOpen} />
        <RadarCard side="short" onOpen={onOpen} />
        <JuiceCard onOpen={onOpen} />
        <WatchesCard onOpen={onOpen} />
        <BreadthCard />
        <EventsCard />
        <PositionsCard onOpen={onOpen} />
        <AlertsCard onOpen={onOpen} />
        <ExtremesBoard onOpen={onOpen} />
      </div>
    </section>
  );
}

/* ── placeholder for Phase-2 tabs ────────────────────────────────────────── */
const PHASE2 = {
  trade: "Verdict banner · levels ladder · chart with EM band/VWAP/OI walls · Expected Move full card · CSP/CC · Strategy menu with every profile (incl. Calendar Spread P/L) · Trade Builder · options chain workbench · theta/skew · level reprice · max pain · % calc",
  discover: "Analyst calls · Movers · Trend · Vol Rank boards",
  analyze: "Analyst targets · valuation · basing · pullback profile · company profile",
  patterns: "Full discovery engine · Current Setup · Ask box · intraday sequences · watch manager",
  news: "Ticker + market news hub with impact tags",
  flow: "Flow score · market tide · sector flow · Greek exposure · dark pool",
  scanners: "Reversal Radar full boards · tickets · report/self-tuning · open-reclaim",
  juice: "Full Juice board · strategy suggestions (DEFINED-first)",
  backtest: "The natural-language Backtest Lab",
  breadth: "Full breadth + market overview detail",
  journal: "Picks + trade journal, linked to originating signals",
  watchlist: "The full 1,276-row board · tags/sector/industry/weekly filters",
  streaks: "Streak board with what-followed stats",
  calendar: "Week game plan + watchlist earnings ladder",
  manage: "Watchlist manager (removal lives ONLY here) · broker import · push · Schwab reconnect · win rate",
  finviz: "Embedded Finviz (helper, two-way sync) — unchanged",
  tview: "Embedded TradingView — unchanged",
  whales: "Embedded Unusual Whales — unchanged",
};
function Phase2({ id }) {
  const label = (TABS.find(t => t[0] === id) || SITES.find(t => t[0] === id) || ["", id])[1];
  return (
    <section className="ws on">
      <div className="card" style={{ maxWidth: 860, margin: "40px auto", textAlign: "center", padding: "42px 40px 38px" }}>
        <div style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: ".2em", color: "var(--acc)" }}>PHASE 2 · COMING TO /NEXT</div>
        <h2 style={{ fontSize: 22, margin: "10px 0 8px" }}>{label}</h2>
        <p style={{ color: "var(--fg2)", fontSize: 13.5, lineHeight: 1.65, maxWidth: "58ch", margin: "0 auto" }}>{PHASE2[id]}</p>
        <p style={{ color: "var(--fg3)", fontSize: 12, marginTop: 14 }}>Until then it's exactly where it's always been:</p>
        <a className="btn pri" href="/" target="_blank" rel="noopener" style={{ marginTop: 10, textDecoration: "none", display: "inline-flex" }}>Open {label} on the classic site ↗</a>
      </div>
    </section>
  );
}

/* ── tape ────────────────────────────────────────────────────────────────── */
function Tape({ ticker }) {
  const n = usePoll(`/api/news?symbol=${encodeURIComponent(ticker)}`, 180000);
  const items = asArr(n.data && n.data.items).slice(0, 4);
  return (
    <div className="tape">
      <span className="nlab">NEWS</span>
      {items.length === 0 && <span className="hl" style={{ color: "var(--fg3)" }}>headlines load with the market…</span>}
      {items.map((it, i) => (
        <React.Fragment key={i}>
          <span className="t">{(pick(it, "time", "published", "date") || "").slice(11, 16)}</span>
          <span className="hl">{pick(it, "title", "headline")}</span>
        </React.Fragment>
      ))}
      <span className="spacer"></span>
      <span className="q" title="Phase 2 adds the index quote tape here">SPY · QQQ · IWM · VIX</span>
    </div>
  );
}

/* ── root ────────────────────────────────────────────────────────────────── */
function App() {
  const [tab, setTab] = useState(() => { try { return localStorage.getItem("next_tab") || "today"; } catch (e) { return "today"; } });
  const [ticker, setTicker] = useState(() => { try { return localStorage.getItem("next_ticker") || "SPY"; } catch (e) { return "SPY"; } });
  useEffect(() => { try { localStorage.setItem("next_tab", tab); } catch (e) {} }, [tab]);
  useEffect(() => { try { localStorage.setItem("next_ticker", ticker); } catch (e) {} }, [ticker]);
  const r = usePoll("/api/radar", 45000);
  const alerts = asArr(r.data && r.data.long).concat(asArr(r.data && r.data.short)).filter(s => (pick(s, "score", "total") || 0) >= 80).length;
  const openSym = (s) => { if (s) { setTicker(String(s).toUpperCase()); } };
  return (
    <div className="frame" style={{ minWidth: 0, maxWidth: "none", border: "none", borderRadius: 0, boxShadow: "none", minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <CommandBar ticker={ticker} setTicker={setTicker} alerts={alerts} />
      <MarketStrip />
      <TabBar active={tab} onChange={setTab} />
      <div className="body" style={{ flex: 1 }}>
        <div className="view">
          {tab === "today" ? <Today onOpen={openSym} /> : <Phase2 id={tab} />}
        </div>
      </div>
      <Tape ticker={ticker} />
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
