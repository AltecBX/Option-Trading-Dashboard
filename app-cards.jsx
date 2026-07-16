// All card and panel components — split out of the app.jsx monolith (v1.40).
// Loads before app.js; every binding is published to window so later
// files resolve bare references exactly as they did in one file.

// Shared in-card state block — one consistent look for loading / empty / error
// across every card, with an optional retry. Replaces ad-hoc inline strings.
function CardNote({ kind = "empty", onRetry, retryLabel = "Try again", children }) {
  return (
    <div className={`card-note card-note-${kind}`} role={kind === "error" ? "alert" : undefined}>
      {kind === "loading" && <span className="card-note-spin" aria-hidden="true" />}
      <div className="card-note-msg">{children}</div>
      {onRetry && (
        <button type="button" className="card-note-retry" onClick={onRetry}>{retryLabel}</button>
      )}
    </div>
  );
}

// Tiny inline sparkline (SVG). Stroke + faint area fill, colored by direction.
function Spark({ data, up }) {
  if (!data || data.length < 2) return null;
  const w = 100, h = 30;
  let min = Infinity, max = -Infinity;
  for (const v of data) { if (v < min) min = v; if (v > max) max = v; }
  const rng = (max - min) || 1;
  const pts = data.map((v, i) =>
    `${((i / (data.length - 1)) * w).toFixed(1)},${(h - ((v - min) / rng) * (h - 2) - 1).toFixed(1)}`);
  const line = pts.join(" ");
  const color = up ? "var(--up)" : "var(--down)";
  return (
    <svg className="mko-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <polygon points={`0,${h} ${line} ${w},${h}`} fill={color} opacity="0.10" />
      <polyline points={line} fill="none" stroke={color} strokeWidth="1.5"
                vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// Tradable proxy for each macro instrument — clicking a tile loads it on the
// chart (the dashboard can't chart futures/indices directly, but these ETFs
// track them closely).
const MKO_PROXY = {
  "ES=F": ["SPY", "S&P 500 ETF"], "NQ=F": ["QQQ", "Nasdaq-100 ETF"],
  "YM=F": ["DIA", "Dow ETF"], "^VIX": ["VIXY", "VIX short-term futures ETF"],
  "BTC=F": ["IBIT", "spot-Bitcoin ETF"], "GC=F": ["GLD", "gold ETF"],
  "^TNX": ["IEF", "7-10yr Treasury ETF"], "CL=F": ["USO", "crude-oil ETF"],
  "DX-Y.NYB": ["UUP", "US Dollar Index ETF"], "HYG": ["HYG", "high-yield bond ETF"],
};

// One-line read of the macro tape: risk-on / risk-off / mixed. Built from a
// small vote model because the inputs move on wildly different scales (VIX
// swings whole percent, HYG barely ticks) so a raw average can't blend them:
//   · equity futures (ES/NQ/YM avg) — DIRECT, the anchor, double-weighted
//   · VIX  — INVERSE (fear bid = risk-off)
//   · DXY  — INVERSE (dollar bid = risk-off / tighter liquidity)
//   · HYG  — DIRECT  (high-yield credit firm = risk-on; credit leads equities)
//   · gold — descriptive tell only, never voted
// Each indicator votes +1 (risk-on) / −1 (risk-off) / 0 on its own threshold;
// net score ≥ +2 = Risk-on, ≤ −2 = Risk-off, else Mixed. So the credit/dollar
// tells can confirm a move or knock it down to Mixed when they diverge from
// equities.
function mkoRegime(items) {
  const by = {};
  for (const i of items) if (i.change_pct != null) by[i.key] = i.change_pct;
  const eq = ["ES=F", "NQ=F", "YM=F"].map(k => by[k]).filter(v => v != null);
  if (!eq.length) return null;
  const eqAvg = eq.reduce((a, b) => a + b, 0) / eq.length;
  const vix = by["^VIX"], dxy = by["DX-Y.NYB"], hyg = by["HYG"], gold = by["GC=F"];

  let score = 0;
  // Equities (anchor) — GRADED so a mild drag (e.g. one index red pulling the
  // average to −0.18%) isn't treated the same as a real −0.5% rout. Needs
  // genuine weakness for the full −2.
  score += eqAvg >= 0.25 ? 2 : eqAvg >= 0.10 ? 1 : eqAvg <= -0.25 ? -2 : eqAvg <= -0.10 ? -1 : 0;
  // VIX (inverse) — easing = risk-on, spiking = risk-off. Thresholds match the
  // "easing/bid" wording below so the score agrees with the words (a −0.67% VIX
  // reads "easing" AND now votes risk-on).
  if (vix != null) score += vix <= -1.5 ? 2 : vix <= -0.5 ? 1 : vix >= 1.5 ? -2 : vix >= 0.5 ? -1 : 0;
  if (dxy != null) score += dxy <= -0.3 ? 1 : dxy >= 0.3 ? -1 : 0;   // dollar (inverse)
  if (hyg != null) score += hyg >= 0.10 ? 1 : hyg <= -0.10 ? -1 : 0; // credit (direct)

  let tone, label;
  if (score >= 2) { tone = "on"; label = "Risk-on"; }
  else if (score <= -2) { tone = "off"; label = "Risk-off"; }
  else { tone = "mixed"; label = "Mixed"; }

  const bits = [`futures ${eqAvg >= 0.05 ? "bid" : eqAvg <= -0.05 ? "soft" : "flat"}`];
  if (vix != null) bits.push(`VIX ${vix >= 0.5 ? "bid" : vix <= -0.5 ? "easing" : "flat"}`);
  if (dxy != null && Math.abs(dxy) >= 0.2) bits.push(`dollar ${dxy > 0 ? "bid" : "soft"}`);
  if (hyg != null && Math.abs(hyg) >= 0.1) bits.push(`credit ${hyg > 0 ? "firm" : "soft"}`);
  if (gold != null && Math.abs(gold) >= 0.4) bits.push(`gold ${gold > 0 ? "bid" : "soft"}`);
  return { tone, label, text: bits.join(" · ") };
}

// Top-of-page macro command strip: futures, VIX, 10Y, gold, oil, bitcoin.
function MarketOverview({ apiFetch, onSwitchTicker }) {
  const [items, setItems] = useState([]);
  useEffect(() => {
    let stop = false, t = null;
    const load = async () => {
      try {
        const d = await sharedJson(apiFetch, "/api/market_overview", 12000);
        if (!stop && d && Array.isArray(d.instruments)) setItems(d.instruments);
      } catch (_) { /* strip is best-effort */ }
      if (!stop) t = setTimeout(load, document.hidden ? 60000 : 20000);
    };
    load();
    const onVis = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { stop = true; if (t) clearTimeout(t); document.removeEventListener("visibilitychange", onVis); };
  }, []);
  const regime = useMemo(() => mkoRegime(items), [items]);
  if (!items.length) return null;
  const fmt = (v, suffix) => v == null ? "—"
    : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + (suffix || "");
  return (
    <React.Fragment>
      {regime && (
        <div className={`mko-regime regime-${regime.tone}`}
             title="A quick read of the tape. Indicators vote risk-on/off: equity futures (the anchor), VIX and the US dollar inversely (a bid in either = risk-off), and high-yield credit (HYG) directly (firm credit = risk-on). Gold is shown as a defensive tell. Net of the votes = Risk-on / Mixed / Risk-off.">
          <span className="mko-regime-dot" aria-hidden="true" />
          <b>{regime.label}</b><span className="mko-regime-why"> — {regime.text}</span>
        </div>
      )}
      <div className="mko-grid" aria-label="Market overview">
        {items.map((it) => {
          const up = (it.change_pct || 0) >= 0;
          const has = it.last != null;
          const proxy = MKO_PROXY[it.key];
          const click = (proxy && onSwitchTicker) ? () => onSwitchTicker(proxy[0]) : null;
          const live = it.source === "schwab" || it.source === "yahoo";
          const srcNote = it.source === "schwab" ? "real-time (Schwab)"
            : it.source === "yahoo" ? "live (Yahoo)"
            : it.source === "yfinance" ? "delayed (yfinance)" : "";
          const title = `${it.label} — last ${fmt(it.last, it.suffix)}, ${up ? "up" : "down"} ${Math.abs(it.change_pct || 0).toFixed(2)}% on the day`
            + (srcNote ? ` · ${srcNote}` : "")
            + (click ? ` · click to open ${proxy[0]} (${proxy[1]}) on the chart` : "");
          return (
            <div key={it.key}
                 className={`mko-tile${has ? "" : " mko-empty"}${click ? " mko-click" : ""}`}
                 title={title}
                 role={click ? "button" : undefined} tabIndex={click ? 0 : undefined}
                 onClick={click || undefined}
                 onKeyDown={click ? (e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); click(); } }) : undefined}>
              <div className="mko-head">
                <span className="mko-label">
                  {has && <span className={`mko-dot ${live ? "live" : "delayed"}`} aria-hidden="true" />}
                  {it.label}
                </span>
                {has && (
                  <span className={`mko-chg ${up ? "up" : "down"}`}>
                    {up ? "▲" : "▼"} {Math.abs(it.change_pct).toFixed(2)}%
                  </span>
                )}
              </div>
              <div className="mko-row2">
                <span className="mko-price">{fmt(it.last, it.suffix)}</span>
                {has && (
                  <span className={`mko-pts ${up ? "up" : "down"}`}>
                    {up ? "+" : ""}{Number(it.change_pts).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </span>
                )}
              </div>
              <Spark data={it.spark} up={up} />
              {click && <span className="mko-proxy">{proxy[0]}</span>}
            </div>
          );
        })}
      </div>
    </React.Fragment>
  );
}

// ── Market posture card ────────────────────────────────────────────────────
// Fills the empty top-left block. Answers "what do I do today?" BEFORE drilling
// into a name: a premium-selling favorability verdict + 0-100 opportunity score,
// three real stats, and the top live click-to-load candidates. Everything reuses
// data the app already computes and caches — the IV-rank board (premium
// richness), the watchlist EDGE board (via computeWatchlistEdges → the same
// numbers the Watchlist tab shows), and the macro regime (VIX / risk tone). No
// mocked numbers: a signal that isn't available shows "—", never a fake value.
function MarketPosture({ apiFetch, onSwitchTicker }) {
  const [board, setBoard] = useState(null);   // watchlist EDGE board
  const [iv, setIv] = useState(null);         // IV-rank board
  const [mkt, setMkt] = useState(null);       // macro strip (for regime + VIX)
  const [loading, setLoading] = useState(true);
  const [logged, setLogged] = useState(() => new Set());   // journaled this session
  const [chainTk, setChainTk] = useState({}); // sym -> chain-validated contract

  useEffect(() => {
    let stop = false, t = null;
    const load = async () => {
      const grab = (u, ttl) => sharedJson(apiFetch, u, ttl).catch(() => null);
      const [b, v, m] = await Promise.all([
        grab("/api/watchlist_table", 20000), grab("/api/ivrank", 60000), grab("/api/market_overview", 12000),
      ]);
      if (!stop) { setBoard(b); setIv(v); setMkt(m); setLoading(false); }
      // If the IV-rank board is empty, kick a background scan so it populates
      // for next time (best-effort; the card works without it).
      if (!stop && v && (!v.rows || !v.rows.length) && !(v.status && v.status.scanning)) {
        apiFetch("/api/ivrank/scan").catch(() => {});
      }
      if (!stop) t = setTimeout(load, document.hidden ? 120000 : 45000);
    };
    load();
    const onVis = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { stop = true; if (t) clearTimeout(t); document.removeEventListener("visibilitychange", onVis); };
  }, []);

  const view = useMemo(() => {
    const clip = (x, a, b) => Math.max(a, Math.min(b, x));
    // ── IV rank (premium richness) ─────────────────────────────────────────
    const ivRows = (iv && iv.rows) || [];
    let ivMedian = null, ripe = 0, ivTotal = ivRows.length;
    const ivBySym = {};
    if (ivRows.length) {
      ivRows.forEach(r => { if (r.ticker) ivBySym[String(r.ticker).toUpperCase()] = r.rank; });
      const ranks = ivRows.map(r => r.rank).filter(v => v != null).sort((a, b) => a - b);
      if (ranks.length) ivMedian = Math.round(ranks[Math.floor(ranks.length / 2)]);
      ripe = ivRows.filter(r => (r.rank || 0) >= 50).length;
    }
    // ── Options ticket per candidate ───────────────────────────────────────
    // Turns "which name" into "which exact trade": BUY the directional option
    // when premium is still cheap (early move + low/mid IV rank → capture delta
    // AND the IV pop as the move accelerates), or SELL premium when IV is
    // already rich. Strike = near-money in the move's direction (buys) or at the
    // swing origin / a resistance cushion (sells). Expiration is sized to how
    // long the move NORMALLY takes: buys get ~2.5× the days left so theta isn't
    // the enemy; sells get a shorter tenor to harvest it. So a 3-day mover shows
    // a few days out, a multi-week mover shows weeks out.
    const roundStrike = (px) => {
      if (!(px > 0)) return null;
      const inc = px < 25 ? 0.5 : px < 200 ? 1 : 5;
      return Math.round(px / inc) * inc;
    };
    const fmtK = (s) => s == null ? "?" : (s % 1 ? s.toFixed(1) : String(s));
    const dteLabel = (d) => d < 10 ? `${Math.round(d)}d` : `${Math.round(d / 7)}wk`;
    const buildTicket = (p) => {
      const px = p.last;
      if (!(px > 0)) return null;
      const bull = p.swing_dir === "long";
      // typical move duration: historical median days, else extrapolate from the
      // current pace (we're swing_pct% in after swing_days days).
      let medDays = p.swing_med_days;
      if (!(medDays > 0) && p.swing_pct > 0 && p.swing_days > 0 && p.swing_med_pct > 0)
        medDays = p.swing_days * (p.swing_med_pct / p.swing_pct);
      if (!(medDays > 0)) medDays = 10;
      const remainDays = Math.max(1, medDays - (p.swing_days || 0));
      const ivr = ivBySym[String(p.symbol).toUpperCase()];
      const rich = ivr != null && ivr >= 60;          // premium already expensive → sell it
      const buy = !rich;                               // else buy it cheap for the vega pop
      let strike, right, dte;
      if (buy) {
        right = bull ? "C" : "P";
        strike = roundStrike(bull ? px * 1.02 : px * 0.98);   // just OTM: cheap + convex
        dte = clip(Math.round(remainDays * 2.5), 5, 90);      // room + theta cushion
      } else {
        right = bull ? "P" : "C";
        // sell OTM: puts down at the swing origin (support), calls up at a cushion
        const base = bull ? Math.max(p.swing_from || px * 0.9, px * 0.90)
                          : Math.min(p.swing_from || px * 1.1, px * 1.10);
        strike = roundStrike(base);
        dte = clip(Math.round(remainDays * 1.2), 3, 45);      // shorter → harvest theta
      }
      const tgt = roundStrike((p.swing_from || px) * (1 + (bull ? 1 : -1) * (p.swing_med_pct || 0) / 100));
      return {
        buy, right, strike, dte, tgt, ivr,
        text: `${buy ? "Buy" : "Sell"} $${fmtK(strike)}${right} · ${dteLabel(dte)}`,
        why: `${p.symbol}: ${bull ? "long" : "short"} setup, ${Math.round(p.swing_pct || 0)}% into a typical `
           + `${Math.round(p.swing_med_pct || 0)}% / ${Math.round(medDays)}d move (~${Math.round(remainDays)}d left). `
           + (buy ? `IV ${ivr != null ? "rank " + ivr + " " : ""}still cheap — buy the ${bull ? "call" : "put"} `
                  + `near the money and let vega + delta expand as it accelerates. ${Math.round(dte)}d out (~2.5× the days left) so theta isn't the enemy.`
                : `IV rank ${ivr} is rich — sell the ${bull ? "put" : "call"} at ${fmtK(strike)} to harvest it, ${Math.round(dte)}d out. `)
           + ` Target ~$${fmtK(tgt)}.`,
      };
    };
    // ── EDGE board (direction + tilt), via the shared formula ──────────────
    const scored = board && board.rows ? edgesFor(board.rows) : [];
    const actionable = scored.filter(r => r.edge != null && Math.abs(r.edge) >= 25);
    const strongLong = actionable.filter(r => r.edge >= 50).length;
    const strongShort = actionable.filter(r => r.edge <= -50).length;
    // ── EARLY-move candidates (the whole point) ────────────────────────────
    // Only names whose swing is just STARTING — swing_stage "early", not
    // extended/exhausted — so premium is still cheap and there's room to run.
    // A name already +70% into its move (BIOA) is "late" and never shows here.
    // Rank by conviction × flow/price confluence + how much of the typical move
    // is still AHEAD (room). That's "get in before it explodes", not "chase".
    const earlyScore = (r) => {
      const conf = (r.swing_dir === "long" && r.edge_dir === "long")
                || (r.swing_dir === "short" && r.edge_dir === "short");
      const room = r.swing_med_pct ? Math.max(0, 1 - (r.swing_pct || 0) / r.swing_med_pct) : 0.4;
      return Math.abs(r.edge) * (conf ? 1.4 : 0.85) + room * 35;
    };
    const earlyPool = scored.filter(r =>
      r.swing_stage === "early" && r.edge != null && Math.abs(r.edge) >= 15);
    const earlyCount = earlyPool.length;
    const picks = earlyPool.map(r => ({ ...r, _es: earlyScore(r) }))
      .sort((a, b) => b._es - a._es).slice(0, 3)
      .map(r => ({ ...r, ticket: buildTicket(r) }));
    // ── Sector rotation (from the board, no extra fetch) ───────────────────
    // Net strong-long minus strong-short EDGE per sector → which sector the
    // flow is rotating INTO (leader) and OUT OF (laggard). A compact read so
    // you catch rotation without opening the Breadth tab.
    const secAgg = {};
    scored.forEach(r => {
      if (r.edge == null || !r.sector) return;
      const s = secAgg[r.sector] || (secAgg[r.sector] = { sector: r.sector, net: 0, n: 0 });
      s.n++;
      if (r.edge >= 25) s.net++; else if (r.edge <= -25) s.net--;
    });
    const secList = Object.values(secAgg).filter(s => s.n >= 3).sort((a, b) => b.net - a.net);
    const rotUp = secList.length && secList[0].net > 0 ? secList[0].sector : null;
    const rotDown = secList.length && secList[secList.length - 1].net < 0 ? secList[secList.length - 1].sector : null;
    // ── Regime / VIX (macro tape) ──────────────────────────────────────────
    const items = (mkt && mkt.instruments) || [];
    const regime = mkoRegime(items);
    const vixIt = items.find(i => i.key === "^VIX");
    const vixLast = vixIt && vixIt.last != null ? vixIt.last : null;
    const vixChg = vixIt ? (vixIt.change_pct || 0) : null;
    const vixBit = vixChg == null ? "—" : vixChg >= 0.5 ? "spiking" : vixChg <= -0.5 ? "easing" : "flat";
    // ── Opportunity score for selling premium (0-100) ──────────────────────
    // Anchor on IV-rank richness when we have it; nudge by VIX direction (a
    // spiking VIX punishes short-vol, easing rewards it) and by how much of the
    // book is throwing off actionable premium-sell setups.
    const haveIv = ivMedian != null;
    let score = haveIv ? ivMedian : 50;
    if (vixChg != null) score += vixChg <= -0.5 ? 8 : vixChg >= 1.5 ? -18 : vixChg >= 0.5 ? -10 : 0;
    score += clip((strongLong + strongShort) - 6, -6, 10) * 0.8;   // breadth of clean setups
    score = Math.round(clip(score, 0, 100));
    // ── Verdict ────────────────────────────────────────────────────────────
    const vixSpiking = vixChg != null && vixChg >= 1.5;
    let level, label, why;
    if (vixSpiking) { level = "off"; label = "Defensive"; why = "VIX spiking — premium is a trap, size down"; }
    else if (score >= 60 && (!haveIv || ivMedian >= 50)) {
      level = "on"; label = "Favorable"; why = haveIv ? `IV rank elevated · ${ripe} names ripe` : "vol easing · setups clean"; }
    else if (score <= 35 || (haveIv && ivMedian <= 30)) {
      level = "off"; label = "Thin premium"; why = haveIv ? "IV rank low — little to sell" : "quiet tape"; }
    else { level = "mixed"; label = "Neutral"; why = haveIv ? `IV rank middling · ${ripe} ripe` : "mixed conditions"; }
    // Directional tilt one-liner from the board.
    const tilt = strongLong > strongShort * 1.5 ? "favors longs"
      : strongShort > strongLong * 1.5 ? "favors shorts" : "balanced";
    const tiltTone = tilt === "favors longs" ? "up" : tilt === "favors shorts" ? "down" : "muted";
    return { level, label, why, score, ivMedian, ripe, ivTotal, haveIv, strongLong,
             strongShort, tilt, tiltTone, vixLast, vixBit, vixChg, picks, earlyCount,
             rotUp, rotDown, regime };
  }, [board, iv, mkt]);

  // Validate each pick's computed ticket against the LIVE option chain: the
  // real listed expiration (buys round up for theta cushion, sells round
  // down), the nearest listed strike, and the live bid/ask — so the ticket is
  // literally the order to place. Keyed on the pick symbols so it re-runs
  // only when the picks actually change; sharedJson caches per-contract 2min.
  const _picksKey = view && view.picks ? view.picks.map(p => p.symbol).join(",") : "";
  useEffect(() => {
    if (!_picksKey) return undefined;
    let stop = false;
    (async () => {
      const out = {};
      await Promise.all(view.picks.map(async (p) => {
        const tk = p.ticket;
        if (!tk || tk.strike == null) return;
        try {
          const d = await sharedJson(apiFetch,
            `/api/pick_ticket?symbol=${encodeURIComponent(p.symbol)}&right=${tk.right}&buy=${tk.buy ? 1 : 0}&strike=${tk.strike}&dte=${tk.dte}`, 120000);
          if (d && d.ticket) out[p.symbol] = d.ticket;
        } catch (_) { /* ticket falls back to the computed values */ }
      }));
      if (!stop) setChainTk(out);
    })();
    return () => { stop = true; };
  }, [_picksKey]);

  if (loading) return <div className="posture-card"><CardNote kind="loading">Reading the tape…</CardNote></div>;

  const v = view;
  const sellSide = (p) => p.prem_sell && p.prem_sell !== "—" ? p.prem_sell : p.setup;
  const fmtStrike = (s) => s == null ? "?" : (s % 1 ? s.toFixed(1) : String(s));
  const fmtExp = (e) => e ? `${parseInt(e.slice(5, 7))}/${parseInt(e.slice(8, 10))}` : "";
  // Snapshot a pick to the journal — captures everything on screen (price,
  // ticket, the move context, the reasoning, the posture at that moment) plus a
  // server timestamp, so accuracy can be reviewed later on the Journal tab.
  const journalPick = async (p) => {
    const tk = p.ticket || {};
    const ct = chainTk[p.symbol];   // chain-validated contract, when resolved
    const snap = {
      symbol: p.symbol, company: p.company || p.name || "", sector: p.sector || "",
      price: p.last, dir: p.swing_dir, swing_pct: p.swing_pct, swing_med_pct: p.swing_med_pct,
      swing_days: p.swing_days, swing_med_days: p.swing_med_days, edge: p.edge, stage: p.swing_stage,
      ticket: ct
        ? `${tk.buy ? "Buy" : "Sell"} $${ct.strike}${ct.right} ${ct.expiration}${ct.mid != null ? ` ~$${ct.mid}` : ""}`
        : (tk.text || sellSide(p)),
      action: tk.buy === undefined ? null : (tk.buy ? "buy" : "sell"),
      right: (ct && ct.right) || tk.right, strike: (ct && ct.strike) != null ? ct.strike : tk.strike,
      dte: (ct && ct.dte) != null ? ct.dte : tk.dte, target: tk.tgt,
      expiration: ct ? ct.expiration : null, entry_mid: ct ? ct.mid : null,
      why: tk.why || p.edge_tip || "",
      posture: v.label, score: v.score, regime: v.regime ? v.regime.label : null,
    };
    try {
      await apiFetch("/api/pick_journal", { method: "POST", body: JSON.stringify(snap) });
      setLogged(s => { const n = new Set(s); n.add(p.symbol); return n; });
    } catch (_) { /* best-effort */ }
  };
  return (
    <div className="posture-card">
      <div className="pc-head">
        <span className="pc-kicker" title="A market-wide read before you dig into names — reuses your Patterns favorability engine, IV-rank board, EDGE board and the macro tape.">Market posture</span>
        <span className="pc-src" title="Live — built from your own scans (watchlist EDGE, IV rank) and the macro strip.">live</span>
      </div>
      <div className={`pc-verdict pc-${v.level}`}>
        <span className="pc-badge">{v.label}</span>
        <span className="pc-why">{v.why}</span>
      </div>
      <div className="pc-scorewrap" title="0-100 favorability for selling premium right now: IV-rank richness, adjusted for VIX direction and how many clean setups your book is showing.">
        <div className="pc-score-row"><span>Opportunity</span><b className={`pc-${v.level}`}>{v.score}<small>/100</small></b></div>
        <div className="pc-bar"><div className={`pc-bar-fill pc-fill-${v.level}`} style={{ width: `${v.score}%` }} /></div>
      </div>
      <div className="pc-stats">
        <div title="Median volatility rank across your scanned universe (premium richness), and how many names are ripe (rank ≥ 50).">
          <span>IV rank</span>
          <b>{v.haveIv ? v.ivMedian : "—"}</b>
          <small>{v.haveIv ? `${v.ripe}/${v.ivTotal} ripe` : "scan pending"}</small>
        </div>
        <div title="Net directional lean of your EDGE board — count of strong long vs short setups.">
          <span>Flow tilt</span>
          <b className={`pc-${v.tiltTone === "up" ? "on" : v.tiltTone === "down" ? "off" : "mixed"}`}>{v.tilt.replace("favors ", "")}</b>
          <small>{v.strongLong}L · {v.strongShort}S</small>
        </div>
        <div title="Names whose move is just STARTING (early stage) with flow confirming — fresh entries with room to run and cheap premium. This is the pool your top picks come from.">
          <span>Early setups</span>
          <b className={v.earlyCount > 0 ? "pc-on" : ""}>{v.earlyCount}</b>
          <small>fresh entries</small>
        </div>
      </div>
      {(v.rotUp || v.rotDown) && (
        <div className="pc-rot" title="Where the options flow is rotating: the sector with the most net-bullish EDGE (into) vs the most net-bearish (out of). Full picture on the Breadth tab.">
          <span className="pc-rot-lbl">Rotation</span>
          {v.rotUp && <span className="pc-rot-in">▲ {v.rotUp}</span>}
          {v.rotDown && <span className="pc-rot-out">▼ {v.rotDown}</span>}
        </div>
      )}
      <div className="pc-picks">
        <div className="pc-picks-h" title="Names whose move is just starting — ranked by conviction and room left to run, NOT names already extended. Click to load one on the chart.">Early movers — get in cheap</div>
        {v.picks.length ? v.picks.map((p, i) => {
          const long = p.swing_dir === "long";
          const tk = p.ticket;
          const isLogged = logged.has(p.symbol);
          const ct = chainTk[p.symbol];
          const tktText = ct
            ? `${tk && tk.buy ? "Buy" : "Sell"} $${fmtStrike(ct.strike)}${ct.right} ${fmtExp(ct.expiration)}${ct.mid != null ? ` ~$${ct.mid.toFixed(2)}` : ""}`
            : (tk ? tk.text : sellSide(p));
          const tktWhy = (tk ? tk.why : "")
            + (ct ? ` — LISTED CONTRACT (validated against the live chain): $${fmtStrike(ct.strike)}${ct.right} exp ${ct.expiration} (${ct.dte}d)`
              + (ct.bid != null && ct.ask != null ? `, bid $${ct.bid} / ask $${ct.ask}` : "")
              + (ct.oi ? `, open interest ${ct.oi}` : "") + ". This is the exact order to place." : "");
          return (
          <div key={i} className="pc-pick" role="button" tabIndex={0} title={tktWhy}
               onClick={() => onSwitchTicker && onSwitchTicker(p.symbol)}
               onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSwitchTicker && onSwitchTicker(p.symbol); } }}>
            <div className="pc-pick-l1">
              <span className="pc-pick-sym">{p.symbol}</span>
              <span className={`pc-pick-stage ${long ? "up" : "down"}`}>{long ? "▲" : "▼"} {p.swing_pct != null ? Math.round(p.swing_pct) : "?"}% in</span>
            </div>
            <div className="pc-pick-l2">
              <span className={`pc-tkt ${tk && tk.buy ? "buy" : "sell"}`}>{tktText}</span>
              {tk && <span className="pc-tkt-tgt">{long ? "→" : "↓"} ${tk.tgt != null ? (tk.tgt % 1 ? tk.tgt.toFixed(1) : tk.tgt) : "?"}</span>}
            </div>
            <button className={`pc-pick-log${isLogged ? " done" : ""}`}
                    title={isLogged ? "Logged to your Picks Journal — snapshotted price, time & thesis. Click to log again." : "Log this pick to your Picks Journal (captures price, time, the ticket and the full reasoning so you can score it later)"}
                    onClick={e => { e.stopPropagation(); journalPick(p); }}>{isLogged ? "✓" : "＋"}</button>
          </div>
          );
        }) : <div className="pc-empty">No fresh setups — tape's extended, sit tight.</div>}
      </div>
    </div>
  );
}

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

// Finviz-style market-cap buckets for the watchlist screener. Each is
// [value, label, predicate(marketCapInDollars)].
const MCAP_BUCKETS = [
  ["all",    "All caps",            () => true],
  ["mega",   "Mega ($200B+)",       mc => mc >= 200e9],
  ["large",  "Large ($10–200B)",    mc => mc >= 10e9 && mc < 200e9],
  ["mid",    "Mid ($2–10B)",        mc => mc >= 2e9 && mc < 10e9],
  ["small",  "Small ($300M–2B)",    mc => mc >= 300e6 && mc < 2e9],
  ["micro",  "Micro ($50–300M)",    mc => mc >= 50e6 && mc < 300e6],
  ["nano",   "Nano (<$50M)",        mc => mc > 0 && mc < 50e6],
  ["plarge", "+Large (>$10B)",      mc => mc >= 10e9],
  ["pmid",   "+Mid (>$2B)",         mc => mc >= 2e9],
  ["psmall", "+Small (>$300M)",     mc => mc >= 300e6],
  ["pmicro", "+Micro (>$50M)",      mc => mc >= 50e6],
  ["nlarge", "-Large (<$200B)",     mc => mc > 0 && mc < 200e9],
  ["nmid",   "-Mid (<$10B)",        mc => mc > 0 && mc < 10e9],
  ["nsmall", "-Small (<$2B)",       mc => mc > 0 && mc < 2e9],
  ["nmicro", "-Micro (<$300M)",     mc => mc > 0 && mc < 300e6],
];
const MCAP_PRED = Object.fromEntries(MCAP_BUCKETS.map(([v, , fn]) => [v, fn]));

// Watchlist Edge model. Collapses the per-stock options-flow fields into a
// single signed conviction score in [-100, +100]: positive = long candidate,
// negative = short. All from data already loaded — no extra UW cost.
//
// Quant principles applied:
//  - Normalize before comparing: premium is judged size-free (lean ratios)
//    and by cross-sectional rank of net premium / market cap, so a small-cap
//    whale bet outranks mega-cap background noise.
//  - Confluence: direction blends flow $ lean, ask-side aggression, sweeps,
//    UW flow score, the stock's SECTOR tilt, and price-trend structure.
//  - Direction x conviction: a clean stacked setup scores high; a muddy one
//    scores low even if it leans the same way (quality, premium rank, rel-vol,
//    alert count, and price agreement drive conviction).
//  - Risk gate: earnings within 7 days halves the score and flags the row.
// How fresh is a row's options-flow? Names in the per-scan live-flow budget are
// fetched fresh; the rest carry their last fetch time. Returns a tone + label
// for the little dot next to EDGE so live vs cached is glanceable.
function flowFreshness(ts) {
  if (!ts) return null;
  let ms;
  try { ms = Date.now() - new Date(ts).getTime(); } catch (_) { return null; }
  if (!(ms >= 0)) return null;
  const min = ms / 60000;
  if (min < 20) return { tone: "fresh", label: "is live" };
  if (min < 60) return { tone: "ok", label: `${Math.round(min)}m old` };
  const hr = min / 60;
  if (hr < 24) return { tone: hr >= 6 ? "stale" : "ok", label: `${Math.round(hr)}h old` };
  return { tone: "stale", label: `${Math.round(hr / 24)}d old` };
}

// Memoized front for computeWatchlistEdges. Since sharedJson gives every
// component the SAME board object, keying by the rows array reference means
// the 1,285-row EDGE pass runs ONCE per board version instead of once per
// component (posture card, context bar, watchlist table all use it).
const _EDGE_MEMO = new WeakMap();
function edgesFor(rows) {
  if (!rows || !rows.length) return [];
  let v = _EDGE_MEMO.get(rows);
  if (!v) { v = computeWatchlistEdges(rows); _EDGE_MEMO.set(rows, v); }
  return v;
}

function computeWatchlistEdges(rows) {
  if (!rows || !rows.length) return rows || [];
  const clip = (x, a, b) => Math.max(a, Math.min(b, x));

  // Sector tilt: net-premium lean per sector, size-free, in [-1, +1].
  const secAgg = new Map();
  rows.forEach(r => {
    if (!r.flow_available) return;
    const k = r.sector || "—";
    const s = secAgg.get(k) || { bull: 0, bear: 0 };
    s.bull += r.call_prem || 0; s.bear += r.put_prem || 0;
    secAgg.set(k, s);
  });
  const sectorTilt = new Map();
  secAgg.forEach((v, k) => {
    const tot = v.bull + v.bear;
    sectorTilt.set(k, tot > 0 ? (v.bull - v.bear) / tot : 0);
  });

  // Cross-sectional rank of premium intensity (|net prem| / market cap).
  const intens = rows.filter(r => r.flow_available)
    .map(r => (r.market_cap > 0 ? Math.abs(r.net_prem || 0) / r.market_cap : 0))
    .sort((a, b) => a - b);
  const pctRank = (x) => {
    if (!intens.length) return 0;
    let lo = 0, hi = intens.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (intens[m] <= x) lo = m + 1; else hi = m; }
    return lo / intens.length;
  };

  return rows.map(r => {
    if (!r.flow_available) return { ...r, edge: null, setup: null, prem_sell: null, edge_er: false, edge_tip: "No flow data — run a scan" };
    const cp = r.call_prem || 0, pp = r.put_prem || 0;
    const ac = r.ask_call_prem || 0, ap = r.ask_put_prem || 0;
    const cs = r.call_sweeps || 0, ps = r.put_sweeps || 0;

    // Direction: size-free leans, positive = bullish.
    const premTilt  = (cp - pp) / (cp + pp + 1);
    const askTilt   = (ac - ap) / (ac + ap + 1);
    const sweepTilt = (cs - ps) / (cs + ps + 1);
    const flowTilt  = clip((r.flow_net || 0) / 60, -1, 1);
    const secTilt   = sectorTilt.get(r.sector || "—") || 0;
    const trendTilt = clip((r.from_ma50 != null ? r.from_ma50 : 0) / 15, -1, 1);
    const D = 0.28 * premTilt + 0.20 * askTilt + 0.16 * flowTilt
            + 0.08 * sweepTilt + 0.16 * secTilt + 0.12 * trendTilt;

    // Price-trend confirmation shrinks (never flips) conviction on divergence.
    const agreeMult = r.flow_agree === "agrees" ? 1.0 : r.flow_agree === "disagrees" ? 0.55 : 0.8;

    // Conviction (cleanliness of the signal), ~0.15..1.0.
    const intensityPct = pctRank(r.market_cap > 0 ? Math.abs(r.net_prem || 0) / r.market_cap : 0);
    const quality = clip((r.flow_quality || 0) / 100, 0, 1);
    const relvol  = clip((r.rel_vol || 0) / 2, 0, 1);
    const alerts  = clip((r.flow_alerts || 0) / 15, 0, 1);
    const K = 0.15 + 0.25 * quality + 0.25 * intensityPct + 0.20 * relvol + 0.15 * alerts;

    let edge = 100 * D * agreeMult * (0.4 + 0.6 * K);
    const er = r.days_to_earnings != null && r.days_to_earnings >= 0 && r.days_to_earnings <= 7;
    if (er) edge *= 0.5;                       // earnings: flag + dampen
    edge = Math.round(clip(edge, -100, 100));

    const dir = edge >= 15 ? "long" : edge <= -15 ? "short" : "mixed";
    const strength = Math.abs(edge) >= 50 ? "strong" : Math.abs(edge) >= 25 ? "building" : "weak";
    let setup = dir === "long" ? "Long" : dir === "short" ? "Short" : "Mixed";
    if (dir !== "mixed") setup += " · " + strength;

    // Premium-selling lens (both lenses): sell puts under bullish flow, sell
    // calls under bearish — flag squeeze risk when CC-Risk is high.
    let prem_sell = "—";
    if (dir === "long") prem_sell = "Sell puts";
    else if (dir === "short") prem_sell = (r.flow_cc_risk != null && r.flow_cc_risk >= 60) ? "Sell calls ⚠" : "Sell calls";

    // Driver breakdown for the hover tooltip.
    const parts = [];
    const tag = (label, v) => { if (Math.abs(v) >= 0.08) parts.push((v > 0 ? "+" : "−") + label); };
    tag("flow$", premTilt); tag("ask-side", askTilt); tag("sweeps", sweepTilt);
    tag("flow-score", flowTilt); tag("sector", secTilt); tag("trend", trendTilt);
    let tip = `Edge ${edge > 0 ? "+" : ""}${edge} (${setup}). Drivers: ${parts.join(", ") || "balanced"}.`;
    tip += ` Conviction: quality ${Math.round(quality * 100)}, premium-rank ${Math.round(intensityPct * 100)}, ${r.rel_vol || 0}× vol`;
    tip += r.flow_agree === "agrees" ? ", price confirms" : r.flow_agree === "disagrees" ? ", price diverges" : "";
    if (er) tip += `. ⚠ Earnings in ${r.days_to_earnings}d — score halved`;
    return { ...r, edge, setup, prem_sell, edge_er: er, edge_dir: dir, edge_tip: tip };
  });
}


// MM-DD-YYYY (e.g. 6-19-2026) from an ISO YYYY-MM-DD string.
function fmtSwingDate(s) {
  if (!s) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s));
  if (!m) return String(s);
  return `${+m[2]}-${+m[3]}-${m[1]}`;
}

function NewsCard({ apiFetch, ticker, companyName }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [src, setSrc] = useState("all");

  const load = async (sym) => {
    if (!sym) return;
    setLoading(true); setErr(null);
    try {
      const nm = companyName ? `&name=${encodeURIComponent(companyName)}` : "";
      const r = await apiFetch(`/api/news?symbol=${encodeURIComponent(sym)}${nm}`);
      const d = await r.json();
      if (d.error && !(d.items || []).length) setErr(d.error); else setData(d);
    } catch (e) { setErr(String(e)); }
    setLoading(false);
  };
  useEffect(() => { setSrc("all"); load(ticker); /* eslint-disable-next-line */ }, [ticker, companyName]);

  const items = (data && data.items) || [];
  const sources = (data && data.sources) || [];
  const shown = items.filter(i => src === "all" || i.source === src);

  return (
    <div className="card ab-card">
      <div className="card-head">
        <div>
          <div className="kicker">News · {ticker}</div>
          <div className="card-title">Latest headlines</div>
        </div>
        <div className="ab-controls">
          <button className="scan-run-btn" onClick={() => load(ticker)} disabled={loading}>
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </div>

      {err && <div className="ab-status"><span className="ab-err">{err}</span></div>}
      {loading && !data && (
        <div className="skel-list">
          {[0, 1, 2, 3, 4].map(i => (
            <div key={i} className="skel-row">
              <span className="skel skel-when" /><span className="skel skel-line" /><span className="skel skel-tag" />
            </div>
          ))}
        </div>
      )}
      {data && (
        <div className="ab-status">
          {items.length} headlines from {sources.length} source{sources.length === 1 ? "" : "s"}
          {" "}· aggregated from Yahoo Finance &amp; Finnhub (free)
        </div>
      )}

      {sources.length > 1 && (
        <div className="news-srcnav">
          <button type="button" className={src === "all" ? "active" : ""} onClick={() => setSrc("all")}>All ({items.length})</button>
          {sources.map(s => (
            <button key={s} type="button" className={src === s ? "active" : ""} onClick={() => setSrc(s)}>
              {s} ({items.filter(i => i.source === s).length})
            </button>
          ))}
        </div>
      )}

      {shown.length > 0 ? (
        <div className="news-list">
          {shown.map((it, i) => (
            <a key={i} className="news-row" href={it.url || "#"} target="_blank" rel="noopener noreferrer"
               title={it.summary || it.title}>
              <span className="news-when">
                <span className="news-abs">{it.date_label || "—"}</span>
                <span className="news-age">{it.time_label || it.age || ""}</span>
              </span>
              <span className="news-body">
                <span className="news-title">
                  {it.title}
                  {it.day_change != null && (
                    <span className={`news-chg ${it.day_change >= 0 ? "up" : "down"}`}>
                      {it.day_change >= 0 ? "+" : ""}{it.day_change}%
                    </span>
                  )}
                </span>
                {it.summary && <span className="news-summary">{it.summary}</span>}
              </span>
              <span className="news-src">{it.source}</span>
            </a>
          ))}
        </div>
      ) : (!err && !loading && <div className="ab-empty">No recent headlines for {ticker}.</div>)}
    </div>
  );
}

// ── TradingView Charting Library integration ────────────────────────────
// Activates ONLY when the licensed library files are present at
// /charting_library/charting_library.standalone.js (committed by the owner
// after TradingView grants access). Until then, callers fall back to the
// open-source Lightweight Charts SwingChart below. Untested until the real
// library files are in the repo — will be tuned once they are.
const _dms = (d) => Date.parse(String(d) + "T00:00:00Z");

function makeSwingDatafeed(apiFetch) {
  return {
    onReady: (cb) => setTimeout(() => cb({
      supported_resolutions: ["1D"], supports_time: true,
      supports_marks: false, supports_timescale_marks: false,
    }), 0),
    searchSymbols: (_u, _e, _t, onResult) => onResult([]),
    resolveSymbol: (name, onResolve) => setTimeout(() => onResolve({
      name, ticker: name, description: name, type: "stock",
      session: "0930-1600", timezone: "America/New_York", exchange: "",
      minmov: 1, pricescale: 100, has_intraday: false,
      supported_resolutions: ["1D"], volume_precision: 0, data_status: "streaming",
    }), 0),
    getBars: (symbolInfo, _res, periodParams, onResult, onError) => {
      apiFetch(`/api/swings?symbol=${encodeURIComponent(symbolInfo.name)}`)
        .then(r => r.json())
        .then(d => {
          const bars = (d.bars || [])
            .map(b => ({ time: _dms(b.t), open: b.o, high: b.h, low: b.l, close: b.c, volume: b.v }))
            .filter(x => x.time / 1000 >= periodParams.from && x.time / 1000 <= periodParams.to)
            .sort((a, b) => a.time - b.time);
          onResult(bars, { noData: bars.length === 0 });
        })
        .catch(e => onError(String(e)));
    },
    subscribeBars: () => {}, unsubscribeBars: () => {},
  };
}

function TVAdvancedChart({ apiFetch, ticker, data, fallback }) {
  const ref = useRef(null);
  const widgetRef = useRef(null);
  const [mode, setMode] = useState("loading");   // loading | tv | fallback
  const [collapsed, setCollapsed] = useState(() => (typeof window !== "undefined" && window.innerWidth <= 900));

  // Detect whether the licensed library is available (load the script once).
  useEffect(() => {
    let cancelled = false;
    const done = (ok) => { if (!cancelled) setMode(ok ? "tv" : "fallback"); };
    if (window.TradingView && window.TradingView.widget) { done(true); return; }
    // Only attempt to load the licensed Charting Library when explicitly
    // enabled (set window.__CHARTING_LIBRARY = true in config.js once the
    // files are committed). Otherwise go straight to the Lightweight chart —
    // no wasted 404 request or loading flash for everyone else.
    const enabled = window.__CHARTING_LIBRARY === true ||
                    (window.__APP_CONFIG && window.__APP_CONFIG.chartingLibrary === true);
    if (!enabled) { done(false); return; }
    const timer = setTimeout(() => done(false), 5000);
    const finish = (ok) => { clearTimeout(timer); done(ok); };
    const existing = document.getElementById("tv-charting-lib");
    if (existing) {
      if (existing.dataset.loaded === "1") { finish(true); return () => { cancelled = true; clearTimeout(timer); }; }
      existing.addEventListener("load", () => finish(true));
      existing.addEventListener("error", () => finish(false));
      return () => { cancelled = true; clearTimeout(timer); };
    }
    const s = document.createElement("script");
    s.id = "tv-charting-lib";
    s.src = "/charting_library/charting_library.standalone.js";
    s.onload = () => { s.dataset.loaded = "1"; finish(true); };
    s.onerror = () => finish(false);
    document.head.appendChild(s);
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

  // Create the TradingView widget + draw the swing overlays.
  useEffect(() => {
    if (mode !== "tv" || collapsed || !ref.current || !window.TradingView) return;
    let widget;
    try {
      widget = new window.TradingView.widget({
        container: ref.current, library_path: "/charting_library/",
        datafeed: makeSwingDatafeed(apiFetch), symbol: ticker, interval: "1D",
        theme: "dark", autosize: true, timezone: "America/New_York",
        disabled_features: ["use_localstorage_for_settings", "header_symbol_search", "header_compare"],
      });
      widgetRef.current = widget;
      widget.onChartReady(() => {
        try {
          const chart = widget.activeChart();
          const lastT = data && data.bars && data.bars.length ? _dms(data.bars[data.bars.length - 1].t) / 1000 : null;
          const drawSwing = (s, color) => chart.createMultipointShape(
            [{ time: _dms(s.low_date) / 1000, price: s.low_price },
             { time: _dms(s.high_date) / 1000, price: s.high_price }],
            { shape: "trend_line", lock: true, disableSave: true, disableSelection: true,
              overrides: { linecolor: color, linewidth: 2, linestyle: 0 } });
          (data.swings || []).forEach(s => drawSwing(s, "#22c55e"));
          (data.down_swings || []).forEach(s => drawSwing(s, "#ef4444"));
          const a = data && data.analysis;
          if (a && a.status === "ok" && lastT) {
            const hline = (price, color, txt) => { if (price == null) return; chart.createShape(
              { time: lastT, price }, { shape: "horizontal_line", lock: true, disableSelection: true,
                overrides: { linecolor: color, linestyle: 2, showLabel: true, text: txt } }); };
            if (a.targets) { hline(a.targets[1] && a.targets[1].price, "#22c55e", "median"); hline(a.targets[2] && a.targets[2].price, "#15803d", "aggr"); }
            if (a.trade_plan) hline(a.trade_plan.invalidation, "#ef4444", "invalidation");
            hline(a.current_price, "rgba(255,255,255,0.6)", "now");
          }
        } catch (e) { console.warn("[swing-tv] overlay draw failed:", e); }
      });
    } catch (e) {
      console.warn("[swing-tv] widget init failed, falling back:", e);
      setMode("fallback");
    }
    return () => { try { if (widgetRef.current) widgetRef.current.remove(); } catch (e) {} widgetRef.current = null; };
    /* eslint-disable-next-line */
  }, [mode, ticker, collapsed]);

  if (mode === "fallback") return fallback;
  return (
    <div className="swing-chart-block">
      <div className="swing-chart-head">
        <button className="swing-chart-toggle" onClick={() => setCollapsed(c => !c)}>
          {collapsed ? "▸" : "▾"} Swing chart <span className="swing-tv-badge">TradingView</span>
        </button>
      </div>
      {!collapsed && mode === "loading" && <div className="ab-status muted">Loading TradingView charting library…</div>}
      {!collapsed && mode === "tv" && <div className="swing-chart swing-chart-tv" ref={ref} />}
    </div>
  );
}

function SwingChart({ data, focusKey, onPickSwing, onClearFocus }) {
  const LC = window.LightweightCharts;
  const wrapRef = useRef(null);
  const chartRef = useRef(null);
  const candleRef = useRef(null);
  const volRef = useRef(null);
  const overlayRef = useRef({ lines: [], priceLines: [] });
  const [show, setShow] = useState({ markers: true, lines: true, up: true, down: false, current: true, targets: true, labels: true });
  const [ohlc, setOhlc] = useState(null);  // crosshair hover readout (O/H/L/C/Chg/Vol)
  const [collapsed, setCollapsed] = useState(() => (typeof window !== "undefined" && window.innerWidth <= 900));

  const bars = (data && data.bars) || [];
  const upSw = (data && data.swings) || [];
  const downSw = (data && data.down_swings) || [];
  const a = data && data.analysis;

  const UPC = "#22c55e", DNC = "#ef4444";

  // Default "home" view = last ~6 months (126 trading days), not the full year.
  const applyHome = () => {
    const n = bars.length;
    if (!n || !chartRef.current) return;
    try { chartRef.current.timeScale().setVisibleRange({ from: bars[Math.max(0, n - 126)].t, to: bars[n - 1].t }); }
    catch (e) { try { chartRef.current.timeScale().fitContent(); } catch (e2) {} }
  };

  // Create the chart once (re-create when uncollapsed so the container exists).
  useEffect(() => {
    if (!LC || !wrapRef.current || collapsed) return;
    const el = wrapRef.current;
    const chart = LC.createChart(el, {
      width: el.clientWidth, height: el.clientHeight || 420,
      layout: { background: { type: "solid", color: "transparent" }, textColor: "#9aa4b2", fontFamily: "JetBrains Mono, ui-monospace, monospace" },
      grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.06)" } },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.1)" },
      timeScale: { borderColor: "rgba(255,255,255,0.1)", rightOffset: 14, fixLeftEdge: true },
      crosshair: { mode: LC.CrosshairMode.Normal },
    });
    const candle = chart.addCandlestickSeries({
      upColor: UPC, downColor: DNC, borderUpColor: UPC, borderDownColor: DNC,
      wickUpColor: UPC, wickDownColor: DNC,
      // We draw our own "now" price line, so hide the candle's built-in
      // last-value label + price line (they duplicated/overlapped the
      // now/median/aggr/inval labels and made the right edge unreadable).
      lastValueVisible: false, priceLineVisible: false,
    });
    const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    chartRef.current = chart; candleRef.current = candle; volRef.current = vol;
    if (onPickSwing) chart.subscribeClick(p => { if (p && p.time) onPickSwing(p.time); });
    // Crosshair readout: surface the hovered bar's OHLC / change% / volume.
    chart.subscribeCrosshairMove(p => {
      if (!p || !p.time || !p.seriesData) { setOhlc(null); return; }
      const c = p.seriesData.get(candle);
      if (!c) { setOhlc(null); return; }
      const vd = p.seriesData.get(vol);
      setOhlc({ time: p.time, o: c.open, h: c.high, l: c.low, c: c.close, v: vd ? vd.value : null });
    });
    const ro = new window.ResizeObserver(() => { if (wrapRef.current) chart.applyOptions({ width: wrapRef.current.clientWidth }); });
    ro.observe(el);
    return () => { ro.disconnect(); try { chart.remove(); } catch (e) {} chartRef.current = null; candleRef.current = null; volRef.current = null; };
    /* eslint-disable-next-line */
  }, [LC, collapsed]);

  // Candles + volume whenever bars change.
  useEffect(() => {
    if (!candleRef.current || !bars.length) return;
    candleRef.current.setData(bars.map(b => ({ time: b.t, open: b.o, high: b.h, low: b.l, close: b.c })));
    volRef.current.setData(bars.map(b => ({ time: b.t, value: b.v, color: b.c >= b.o ? "rgba(34,197,94,0.30)" : "rgba(239,68,68,0.30)" })));
    applyHome();
    /* eslint-disable-next-line */
  }, [data, collapsed]);

  // Swing overlay: markers + connector lines + current-swing price lines.
  useEffect(() => {
    const chart = chartRef.current, candle = candleRef.current;
    if (!chart || !candle || !bars.length) return;
    overlayRef.current.lines.forEach(ls => { try { chart.removeSeries(ls); } catch (e) {} });
    overlayRef.current.priceLines.forEach(pl => { try { candle.removePriceLine(pl); } catch (e) {} });
    overlayRef.current = { lines: [], priceLines: [] };

    const fStart = focusKey && focusKey.start, fEnd = focusKey && focusKey.end;
    const DIMUP = "rgba(34,197,94,0.22)", DIMDN = "rgba(239,68,68,0.22)";
    const markers = [];
    const addSwing = (s, dir) => {
      const lo = s.low_date < s.high_date ? s.low_date : s.high_date;
      const hi = s.low_date < s.high_date ? s.high_date : s.low_date;
      const focused = fStart && lo === fStart && hi === fEnd;
      const dim = fStart && !focused;            // something selected, not this
      const c = dir === "up" ? UPC : DNC;
      if (show.markers && !dim) {
        const lbl = show.labels ? `${s.pct_change > 0 ? "+" : ""}${Math.round(s.pct_change)}%` : "";
        markers.push({ time: s.low_date, position: "belowBar", color: c, shape: "arrowUp", text: dir === "down" ? lbl : "" });
        markers.push({ time: s.high_date, position: "aboveBar", color: c, shape: "arrowDown", text: dir === "up" ? lbl : "" });
      }
      if (show.lines) {
        const lineColor = dim ? (dir === "up" ? DIMUP : DIMDN) : c;
        const ls = chart.addLineSeries({ color: lineColor, lineWidth: focused ? 3 : 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        const pts = [{ time: s.low_date, value: s.low_price }, { time: s.high_date, value: s.high_price }].sort((x, y) => x.time < y.time ? -1 : 1);
        ls.setData(pts);
        overlayRef.current.lines.push(ls);
      }
    };
    if (show.up) upSw.forEach(s => addSwing(s, "up"));
    if (show.down) downSw.forEach(s => addSwing(s, "down"));
    markers.sort((x, y) => x.time < y.time ? -1 : x.time > y.time ? 1 : 0);
    candle.setMarkers(markers);

    if (a && a.status === "ok") {
      // Draw the level lines only — no on-chart/axis labels (they piled up
      // on the right edge over the candles). The values live in the HTML
      // legend rendered over the top-left of the chart instead.
      const mk = (price, color, style) => {
        if (price == null) return;
        overlayRef.current.priceLines.push(candle.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: false }));
      };
      if (show.current) mk(a.current_price, "rgba(255,255,255,0.55)", LC.LineStyle.Solid);
      if (show.targets && a.targets) {
        mk(a.targets[1] && a.targets[1].price, UPC, LC.LineStyle.Dashed);
        mk(a.targets[2] && a.targets[2].price, "#15803d", LC.LineStyle.Dotted);
      }
      if (show.current && a.trade_plan) mk(a.trade_plan.invalidation, DNC, LC.LineStyle.Dashed);
    }
    /* eslint-disable-next-line */
  }, [data, show, collapsed, focusKey]);

  // Focus the chart on a selected swing (from a table-row click), or zoom
  // back out to the home view when the selection is cleared.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (focusKey && focusKey.start && focusKey.end) {
      try { chart.timeScale().setVisibleRange({ from: focusKey.start, to: focusKey.end }); } catch (e) {}
    } else {
      applyHome();
    }
    /* eslint-disable-next-line */
  }, [focusKey, collapsed]);

  const TOGGLES = [["markers", "Markers"], ["labels", "Labels"], ["lines", "Lines"], ["up", "Up"], ["down", "Down"], ["current", "Current"], ["targets", "Targets"]];

  // Crosshair OHLC readout — hovered bar, falling back to the latest bar.
  const lastBar = bars.length ? bars[bars.length - 1] : null;
  const ro = ohlc || (lastBar ? { time: lastBar.t, o: lastBar.o, h: lastBar.h, l: lastBar.l, c: lastBar.c, v: lastBar.v } : null);
  const fmtVol = (v) => v == null ? "—" : v >= 1e9 ? (v / 1e9).toFixed(2) + "B" : v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(0) + "K" : String(Math.round(v));
  const fmtBarDate = (t) => typeof t === "string" ? fmtSwingDate(t) : (t && t.year ? `${t.month}-${t.day}-${t.year}` : String(t));
  const roChg = ro && ro.o ? (ro.c - ro.o) / ro.o * 100 : null;

  // Level legend (rendered as HTML over the chart so the now/median/aggr/
  // inval prices don't overlap the candles on the right axis).
  const legend = [];
  if (a && a.status === "ok") {
    if (show.current && a.current_price != null) legend.push({ name: "now", price: a.current_price, color: "#cbd5e1" });
    if (show.targets && a.targets) {
      if (a.targets[1] && a.targets[1].price != null) legend.push({ name: "median", price: a.targets[1].price, color: UPC });
      if (a.targets[2] && a.targets[2].price != null) legend.push({ name: "aggr", price: a.targets[2].price, color: "#15803d" });
    }
    if (show.current && a.trade_plan && a.trade_plan.invalidation != null) legend.push({ name: "inval", price: a.trade_plan.invalidation, color: DNC });
  }

  return (
    <div className="swing-chart-block">
      <div className="swing-chart-head">
        <button className="swing-chart-toggle" onClick={() => setCollapsed(c => !c)}>
          {collapsed ? "▸" : "▾"} Swing chart
        </button>
        {!collapsed && LC && (
          <div className="swing-chart-toggles">
            {TOGGLES.map(([k, lbl]) => (
              <button key={k} className={show[k] ? "on" : ""} onClick={() => setShow(s => ({ ...s, [k]: !s[k] }))}>{lbl}</button>
            ))}
            <button onClick={() => { applyHome(); if (onClearFocus) onClearFocus(); }}>Reset</button>
          </div>
        )}
      </div>
      {!collapsed && !LC && <div className="ab-status muted">Chart library didn't load (offline?). The swing table above has the full data.</div>}
      {!collapsed && LC && (
        <div className="swing-chart-wrap">
          <div className="swing-chart-overlay">
            {ro && (
              <div className="swing-chart-ohlc">
                <span className="muted">{fmtBarDate(ro.time)}</span>
                <span>O <b>{ro.o.toFixed(2)}</b></span>
                <span>H <b>{ro.h.toFixed(2)}</b></span>
                <span>L <b>{ro.l.toFixed(2)}</b></span>
                <span>C <b className={ro.c >= ro.o ? "up" : "down"}>{ro.c.toFixed(2)}</b></span>
                {roChg != null && <span className={roChg >= 0 ? "up" : "down"}>{roChg >= 0 ? "+" : ""}{roChg.toFixed(2)}%</span>}
                <span className="muted">Vol {fmtVol(ro.v)}</span>
              </div>
            )}
            {legend.length > 0 && (
              <div className="swing-chart-legend">
                {legend.map(l => (
                  <span key={l.name} className="swing-legend-item">
                    <i style={{ background: l.color }} />{l.name} <b>{fmtUsd(l.price, 2)}</b>
                  </span>
                ))}
              </div>
            )}
          </div>
          <div className="swing-chart" ref={wrapRef} />
        </div>
      )}
      {!collapsed && LC && <div className="swing-chart-hint">Tap a candle near a swing to open its row · tap a table row to highlight + zoom to that move · Reset = 6-month view</div>}
    </div>
  );
}

// Forward-looking swing projection, derived entirely from the analysis the
// backend already returns (targets, rhythm percentiles, continuation/
// exhaustion scores, structural levels) plus the completed swing list — no
// hardcoded predictions. Adds the pieces not already on the card: Fibonacci
// pullback/bounce zones, the 3 most-similar past moves + what followed, and
// three probability-weighted forward paths.
function computeSwingPrediction(data) {
  const a = data && data.analysis;
  if (!a || a.status !== "ok" || a.current_price == null) return null;
  const up = a.direction === "up";
  const fromP = a.from_price, extP = a.extreme_price;
  const vh = a.vs_history || {};
  const levels = a.key_levels || {};
  const tp = a.trade_plan || {};
  const inval = tp.invalidation != null ? tp.invalidation : null;
  const completed = up ? (data.swings || []) : (data.down_swings || []);
  const opp = up ? (data.down_swings || []) : (data.swings || []);
  const nSw = Math.max(1, completed.length);
  const r2 = (x) => Math.round(x * 100) / 100;
  const curAbs = Math.abs(a.current_move_pct || 0);
  const days = a.days_active || 0;
  const median = (arr) => {
    const v = arr.filter(x => x != null).slice().sort((x, y) => x - y);
    if (!v.length) return 0;
    const m = Math.floor(v.length / 2);
    return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
  };

  // 1 — current move read
  const moveRead = {
    dir: up ? "Up" : "Down", fromLabel: a.from_label, fromDate: a.from_date,
    pct: a.current_move_pct, days, perDay: r2(days ? curAbs / days : 0),
    typicalPct: vh.median_pct, typicalDays: vh.median_days,
    maturity: a.maturity, pctOfMedian: vh.pct_of_median_move, signal: a.signal_note,
  };

  // 2 — projected targets (probability = share of past swings that ran this far)
  const tgts = (a.targets || []).map(t => ({
    label: t.label, price: t.price, fromPct: t.from_here_pct, reached: t.reached,
    eta: t.eta_date, prob: Math.min(100, Math.round((t.matched || 0) / nSw * 100)), conf: t.confidence,
  }));

  // 3 — pullback / bounce zones, calibrated to THIS stock's OWN retracement
  // history (how far it actually pulls back), so the ranges are tight and
  // realistic instead of a generic Fibonacci grid. opp = the opposite-
  // direction swings = the actual pullbacks/bounces this name has made.
  const span = Math.abs(extP - fromP);
  const depths = opp.map(s => Math.abs(s.pct_change)).filter(x => x > 0).sort((x, y) => x - y);
  const pctile = (arr, q) => { if (!arr.length) return null; const i = (arr.length - 1) * q; const lo = Math.floor(i), hi = Math.ceil(i); return arr[lo] + (arr[hi] - arr[lo]) * (i - lo); };
  const fibBand = (lo, hi) => { const x = up ? [extP - span * hi, extP - span * lo] : [extP + span * lo, extP + span * hi]; return [r2(Math.min(x[0], x[1])), r2(Math.max(x[0], x[1]))]; };
  let pullback = null;
  if (depths.length >= 3) {
    const iqr = (pctile(depths, 0.75) - pctile(depths, 0.25)) || 0;
    const bw = Math.max(0.75, Math.min(3, iqr * 0.15));   // tight ± band (percent)
    const zone = (d) => {
      const a0 = extP * (1 + (up ? -1 : 1) * (d + bw) / 100);
      const b0 = extP * (1 + (up ? -1 : 1) * (d - bw) / 100);
      return [r2(Math.min(a0, b0)), r2(Math.max(a0, b0))];
    };
    pullback = { shallow: zone(pctile(depths, 0.25)), normal: zone(pctile(depths, 0.5)),
                 deep: zone(pctile(depths, 0.75)), invalidation: inval,
                 basis: "history", n: depths.length, medDepth: r2(pctile(depths, 0.5)) };
  } else if (span > 0) {
    pullback = { shallow: fibBand(0.30, 0.40), normal: fibBand(0.44, 0.54),
                 deep: fibBand(0.60, 0.70), invalidation: inval, basis: "fib", n: depths.length };
  }
  // Exact structural levels market makers defend (prior pivots) — the tightest
  // reference of all: real prices where this stock has turned before.
  const struct = up ? (levels.supports || []) : (levels.resistances || []);
  const keyLevels = struct.slice(0, 3).map(l => ({ price: l.price, pctAway: l.pct_away }));

  // 4 — continuation / exhaustion scores + reasons
  const scores = {
    continuation: a.continuation_score, contFactors: (a.continuation_factors || []).slice(0, 4),
    exhaustion: a.exhaustion_score, exhFactors: (a.exhaustion_factors || []).slice(0, 4),
  };

  // 5 — the 3 most-similar completed moves + what happened next
  const medPct = median(completed.map(s => Math.abs(s.pct_change))) || 1;
  const medDays = median(completed.map(s => s.trading_days)) || 1;
  const dist = (s) => Math.abs(Math.abs(s.pct_change) - curAbs) / medPct + Math.abs(s.trading_days - days) / medDays;
  const activeKey = a.from_date;
  const similar = completed
    .filter(s => (up ? s.low_date : s.high_date) !== activeKey)
    .slice().sort((x, y) => dist(x) - dist(y)).slice(0, 3)
    .map(s => {
      const sAbs = Math.abs(s.pct_change), sDays = s.trading_days;
      let outcome = null;
      if (up) { const d = opp.find(o => o.high_date === s.high_date); if (d) outcome = { kind: "fell", pct: r2(Math.abs(d.pct_change)), days: d.trading_days }; }
      else { const u = opp.find(o => o.low_date === s.low_date); if (u) outcome = { kind: "rose", pct: r2(Math.abs(u.pct_change)), days: u.trading_days }; }
      return { lowDate: s.low_date, highDate: s.high_date, pct: r2(sAbs), days: sDays, perDay: r2(sDays ? sAbs / sDays : 0), outcome };
    });

  // 6 — decision (from the backend), 7 — three probability-weighted paths
  const decision = { action: (a.decision && a.decision.action) || "—", drivers: (a.decision && a.decision.drivers) || [], note: a.signal_note };
  const contS = a.continuation_score != null ? a.continuation_score : 50;
  const exhS = a.exhaustion_score != null ? a.exhaustion_score : 50;
  const tot = (contS + exhS + ((contS + exhS) / 2 + 10)) || 1;
  const contProb = Math.round(contS / tot * 100);
  const revProb = Math.round(exhS / tot * 100);
  const pullProb = 100 - contProb - revProb;
  const next = levels.next;
  const find = (l) => tgts.find(t => t.label === l) || {};
  const agg = find("aggressive"), ext = find("extreme");
  const zone = (z) => z ? `$${z[0]}–$${z[1]}` : "—";
  const w = (lo, hi) => `${lo || "?"}–${hi || "?"} days`;
  const md = vh.median_days || 6;
  const paths = up ? [
    { name: "Bullish continuation", prob: contProb,
      trigger: next ? `Holds support, breaks $${next.price}` : `Breaks aggressive $${agg.price || "—"}`,
      target: ext.price ? `$${ext.price}` : (agg.price ? `$${agg.price}` : "—"),
      days: w(vh.p25_days, vh.p75_days), inval: pullback ? `loses $${pullback.normal[1]}` : (inval ? `loses $${inval}` : "—") },
    { name: "Normal pullback", prob: pullProb,
      trigger: next ? `Stalls near $${next.price}` : `Fails near $${agg.price || "—"}`,
      target: pullback ? zone(pullback.normal) : "—",
      days: w(Math.max(1, Math.round(md / 3)), Math.max(2, Math.round(md / 1.5))), inval: inval ? `loses $${inval}` : "—" },
    { name: "Bearish reversal", prob: revProb,
      trigger: inval ? `Closes below $${inval} on volume` : "Breaks the swing low on volume",
      target: pullback ? zone(pullback.deep) : "—", days: w(vh.median_days, vh.p75_days), inval: "reclaims the highs" },
  ] : [
    { name: "Bearish continuation", prob: contProb,
      trigger: next ? `Stays weak, breaks $${next.price}` : `Breaks aggressive $${agg.price || "—"}`,
      target: ext.price ? `$${ext.price}` : (agg.price ? `$${agg.price}` : "—"),
      days: w(vh.p25_days, vh.p75_days), inval: pullback ? `reclaims $${pullback.normal[0]}` : (inval ? `reclaims $${inval}` : "—") },
    { name: "Normal bounce", prob: pullProb,
      trigger: next ? `Holds near $${next.price}` : `Stalls near $${agg.price || "—"}`,
      target: pullback ? zone(pullback.normal) : "—",
      days: w(Math.max(1, Math.round(md / 3)), Math.max(2, Math.round(md / 1.5))), inval: inval ? `reclaims $${inval}` : "—" },
    { name: "Bullish reversal", prob: revProb,
      trigger: inval ? `Closes above $${inval} on volume` : "Breaks the swing high on volume",
      target: pullback ? zone(pullback.deep) : "—", days: w(vh.median_days, vh.p75_days), inval: "loses the lows" },
  ];

  return { up, moveRead, tgts, pullback, keyLevels, scores, similar, decision, paths, sampleSize: completed.length, symbol: data.symbol };
}

const SWING_DECISION_TONE = {
  "Add on breakout": "go", "Add on pullback": "go", "Hold": "go",
  "Short trigger active": "short", "Short watch": "watch", "Reversal watch": "watch",
  "Take partial": "warn", "Trim": "warn", "Trail stop": "warn", "Cover partial": "warn",
  "Do not chase": "warn", "Wait": "muted", "No trade": "muted",
};

function SwingPrediction({ data }) {
  const p = computeSwingPrediction(data);
  if (!p) return null;
  const { up, moveRead: m, tgts, pullback, keyLevels, scores, similar, decision, paths } = p;
  const dirCls = up ? "up" : "down";
  const sgn = (v) => v == null ? "—" : `${v >= 0 ? "+" : ""}${v}%`;
  const matTone = ({ early: "up", developing: "up", mature: "", extended: "warn", exhausted: "down" })[m.maturity] || "";
  // Entry-timing read — the whole point: be at the START of the move, never chase.
  const early = ["early", "developing"].includes(m.maturity);
  const late = ["extended", "exhausted"].includes(m.maturity);
  const entryRead = early
    ? { cls: "up", txt: up ? "Early — good spot to be long; you're near the start" : "Early — good spot to be short; you're near the start" }
    : late
      ? { cls: "down", txt: up ? "Late — don't chase; wait for the pullback zone to go long" : "Late — don't chase; wait for the bounce zone to short" }
      : { cls: "", txt: "Mid-move — enter on a pullback, not here" };
  const sym = p.symbol || "this stock";

  return (
    <div className="swing-pred">
      <div className="swing-pred-title">Swing Prediction
        <span className="swing-pred-sub">based on this stock's {p.sampleSize} past {up ? "up" : "down"}-swings — most likely path, not a guarantee</span>
      </div>

      {/* Decision banner */}
      <div className={`swing-pred-decision tone-${SWING_DECISION_TONE[decision.action] || "muted"}`}>
        <div className="swing-pred-decision-action">{decision.action}</div>
        {decision.drivers.length > 0 && <div className="swing-pred-decision-why">{decision.drivers.join(" · ")}</div>}
        <div className={`swing-pred-timing ${entryRead.cls}`}>Entry timing: {entryRead.txt}</div>
        {decision.note && <div className="swing-pred-decision-note">{decision.note}</div>}
      </div>

      <div className="swing-pred-grid">
        {/* 1 — Current move read */}
        <div className="swing-pred-box">
          <div className="swing-pred-h">1 · Current move read</div>
          <ul className="swing-pred-list">
            <li>Move: <b className={dirCls}>{m.dir} from {fmtSwingDate(m.fromDate)} {m.fromLabel}</b></li>
            <li>So far: <b className={dirCls}>{sgn(m.pct)}</b> over <b>{m.days}d</b> ({sgn(m.perDay)}/day)</li>
            <li>Typical {up ? "up" : "down"}-swing: <b>{up ? "+" : "−"}{m.typicalPct}%</b> over <b>{m.typicalDays}d</b></li>
            {m.pctOfMedian != null && <li>This move = <b>{m.pctOfMedian}%</b> of the median</li>}
            <li>Status: <b className={matTone}>{m.maturity}</b></li>
          </ul>
        </div>

        {/* 2 — Projected targets */}
        <div className="swing-pred-box">
          <div className="swing-pred-h">2 · Projected next targets</div>
          <table className="swing-pred-tbl">
            <thead><tr><th>Target</th><th>Price</th><th>From here</th><th>By</th><th>Hit rate</th><th>Conf</th></tr></thead>
            <tbody>
              {tgts.map(t => (
                <tr key={t.label}>
                  <td className="cap">{t.label}</td>
                  <td className="num">{fmtUsd(t.price, 2)}</td>
                  <td className={`num ${t.reached ? "muted" : dirCls}`}>{t.reached ? "reached" : sgn(t.fromPct)}</td>
                  <td className="num muted">{t.reached ? "—" : fmtSwingDate(t.eta)}</td>
                  <td className="num">{t.prob}%</td>
                  <td className="cap muted">{t.conf}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 3 — Pullback / bounce zones (tight, calibrated to this stock) */}
        {pullback && (
          <div className="swing-pred-box">
            <div className="swing-pred-h">3 · Expected {up ? "pullback" : "bounce"} zone</div>
            <ul className="swing-pred-list">
              <li>Shallow{up ? " (best re-entry)" : " (best re-short)"}: <b className={dirCls}>${pullback.shallow[0]} – ${pullback.shallow[1]}</b></li>
              <li>Normal: <b>${pullback.normal[0]} – ${pullback.normal[1]}</b></li>
              <li>Deep: <b>${pullback.deep[0]} – ${pullback.deep[1]}</b></li>
              {pullback.invalidation != null && <li className="muted">Invalidation: {up ? "below" : "above"} <b className="down">${pullback.invalidation}</b></li>}
            </ul>
            <div className="swing-pred-factors">
              {pullback.basis === "history"
                ? `Tuned to ${sym}'s own history — it usually ${up ? "pulls back" : "bounces"} ~${pullback.medDepth}% (median of ${pullback.n} past ${up ? "pullbacks" : "bounces"}).`
                : "Few past pullbacks to learn from — using a tight retracement of the current move."}
            </div>
            {keyLevels.length > 0 && (
              <div className="swing-pred-levels">
                <span className="muted">{up ? "Support MMs defend" : "Resistance MMs defend"}:</span>
                {keyLevels.map((k, i) => <span key={i} className="swing-pred-lvl">${k.price}<small className="muted"> {k.pctAway > 0 ? "+" : ""}{k.pctAway}%</small></span>)}
              </div>
            )}
          </div>
        )}

        {/* 4 — Continuation vs exhaustion */}
        <div className="swing-pred-box">
          <div className="swing-pred-h">4 · Continuation vs exhaustion</div>
          <div className="swing-pred-score">
            <div className="swing-pred-score-row"><span>Continuation</span><b className="up">{scores.continuation}/100</b></div>
            <div className="swing-bar"><div className="swing-bar-fill up" style={{ width: `${Math.max(0, Math.min(100, scores.continuation || 0))}%` }} /></div>
            <div className="swing-pred-factors">{scores.contFactors.join(" · ") || "—"}</div>
          </div>
          <div className="swing-pred-score">
            <div className="swing-pred-score-row"><span>Exhaustion</span><b className="down">{scores.exhaustion}/100</b></div>
            <div className="swing-bar"><div className="swing-bar-fill down" style={{ width: `${Math.max(0, Math.min(100, scores.exhaustion || 0))}%` }} /></div>
            <div className="swing-pred-factors">{scores.exhFactors.join(" · ") || "—"}</div>
          </div>
        </div>

        {/* 5 — Similar past moves */}
        {similar.length > 0 && (
          <div className="swing-pred-box">
            <div className="swing-pred-h">5 · Most-similar past moves</div>
            <table className="swing-pred-tbl">
              <thead><tr><th>Move</th><th>Size</th><th>Days</th><th>/day</th><th>What followed</th></tr></thead>
              <tbody>
                {similar.map((s, i) => (
                  <tr key={i}>
                    <td className="muted">{fmtSwingDate(up ? s.lowDate : s.highDate)}</td>
                    <td className={`num ${dirCls}`}>{up ? "+" : "−"}{s.pct}%</td>
                    <td className="num">{s.days}</td>
                    <td className="num muted">{s.perDay}%</td>
                    <td className="num">{s.outcome ? <span className={s.outcome.kind === "fell" ? "down" : "up"}>{s.outcome.kind} {s.outcome.pct}% / {s.outcome.days}d</span> : <span className="muted">extended further</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 7 — Three possible paths */}
        <div className="swing-pred-box swing-pred-wide">
          <div className="swing-pred-h">6 · Three possible paths next</div>
          <div className="swing-pred-paths">
            {paths.map((pt, i) => (
              <div key={i} className={`swing-pred-path ${i === 0 ? (up ? "up" : "down") : i === 2 ? (up ? "down" : "up") : ""}`}>
                <div className="swing-pred-path-head"><span>{pt.name}</span><b>{pt.prob}%</b></div>
                <div className="swing-bar"><div className="swing-bar-fill" style={{ width: `${pt.prob}%` }} /></div>
                <div className="swing-pred-path-row"><span className="muted">Trigger</span> {pt.trigger}</div>
                <div className="swing-pred-path-row"><span className="muted">Target</span> <b>{pt.target}</b></div>
                <div className="swing-pred-path-row"><span className="muted">Time</span> {pt.days}</div>
                <div className="swing-pred-path-row"><span className="muted">Invalid if</span> {pt.inval}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function SwingPatternCard({ apiFetch, ticker }) {
  const Term = window.Term || (({ children }) => <span>{children}</span>);
  const cardRef = useRef(null);
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
  const [openRow, setOpenRow] = useState(null);  // expanded history row key
  const [focusKey, setFocusKey] = useState(null); // chart focus range {start,end}

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
  // Clear the previous symbol's swings the instant the ticker changes so the
  // card shows its loading skeleton instead of stale data from another symbol
  // (which could be misread). Not cleared on a sensitivity tweak.
  useEffect(() => { setData(null); setErr(null); }, [ticker]);
  useEffect(() => { load(ticker, sens); /* eslint-disable-next-line */ }, [ticker, sens]);

  // Resizable columns (desktop only). Adds a drag handle to each header cell
  // of the swing tables. Idempotent + re-runs when the tables change.
  useEffect(() => {
    if (typeof window === "undefined" || window.innerWidth <= 900) return;
    const root = cardRef.current; if (!root) return;
    const cleanups = [];
    root.querySelectorAll("table.swing-table thead").forEach(thead => {
      const ths = Array.from(thead.querySelectorAll("th"));
      ths.forEach((th, i) => {
        if (i === ths.length - 1 || th.querySelector(".col-resize-handle")) return;
        th.style.position = "relative";
        const h = document.createElement("span");
        h.className = "col-resize-handle";
        const onDown = (e) => {
          e.preventDefault(); e.stopPropagation();
          const startX = e.clientX, startW = th.offsetWidth;
          document.body.style.userSelect = "none";
          const move = (ev) => { th.style.width = Math.max(44, startW + ev.clientX - startX) + "px"; };
          const up = () => {
            window.removeEventListener("mousemove", move);
            window.removeEventListener("mouseup", up);
            document.body.style.userSelect = "";
          };
          window.addEventListener("mousemove", move);
          window.addEventListener("mouseup", up);
        };
        h.addEventListener("mousedown", onDown);
        th.appendChild(h);
        cleanups.push(() => { h.removeEventListener("mousedown", onDown); h.remove(); });
      });
    });
    return () => cleanups.forEach(fn => fn());
  }, [data, tab, sens]);

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
  const DECISION_TONE = {
    "Add on breakout": "go", "Add on pullback": "go", "Hold": "go",
    "Short trigger active": "short", "Short watch": "watch",
    "Take partial": "warn", "Cover partial": "warn", "Trail only": "warn",
    "Cover fully": "down", "No new trade": "muted",
  };
  // How each ladder target is derived (shown as a tooltip on the label).
  const TARGET_BASIS = {
    conservative: "25th percentile of this stock's past moves from a swing — most moves clear this.",
    median: "The typical (median) past move projected off the swing price.",
    aggressive: "75th percentile — only the stronger past moves reached this far.",
    extreme: "The single LARGEST prior move in the lookback, projected off the swing. An outlier ceiling — rarely repeated, hence the low confidence and 0 matches. Not a base case.",
  };

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

  // Zoom the chart to a swing (earliest→latest date, padded a touch).
  const focusSwingOnChart = (s) => {
    const lo = s.low_date, hi = s.high_date;
    const start = lo < hi ? lo : hi, end = lo < hi ? hi : lo;
    setFocusKey({ start, end, k: start + end + Date.now() });
  };
  // Chart click → find the swing whose span contains that date, open its row.
  const pickSwingByTime = (t) => {
    const inSpan = (s) => {
      const a0 = s.low_date < s.high_date ? s.low_date : s.high_date;
      const b0 = s.low_date < s.high_date ? s.high_date : s.low_date;
      return t >= a0 && t <= b0;
    };
    let hit = upSwings.findIndex(inSpan);
    if (hit >= 0) { setTab("up"); setOpenRow(`up-${upSwings.length - 1 - hit}`); focusSwingOnChart(upSwings[hit]); return; }
    hit = downSwings.findIndex(inSpan);
    if (hit >= 0) { setTab("down"); setOpenRow(`down-${downSwings.length - 1 - hit}`); focusSwingOnChart(downSwings[hit]); }
  };

  return (
    <div className="card ab-card" ref={cardRef}>
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

      {loading && !data && (
        <div className="skel-block">
          <div className="skel skel-banner" />
          <div className="skel-grid">{[0,1,2,3,4,5].map(i => <div key={i} className="skel skel-cell" />)}</div>
          <div className="skel skel-bar" /><div className="skel skel-bar" />
        </div>
      )}

      {/* ── Decision banner ─────────────────────────────────────────────── */}
      {a && a.decision && (
        <div className={`swing-decision tone-${DECISION_TONE[a.decision.action] || "muted"}`}
             title="The decision engine's recommended action for this setup, with the drivers behind it">
          <span className="swing-decision-action">{a.decision.action}</span>
          {(a.decision.drivers || []).length > 0 && (
            <span className="swing-decision-because">because {a.decision.drivers.join(" · ")}</span>
          )}
        </div>
      )}

      {/* ── Avoid-this-trade veto: fires only when several negatives align ── */}
      {a && a.status === "ok" && (() => {
        const reasons = [];
        if (a.odds && a.odds.expectancy_r != null && a.odds.expectancy_r < 0)
          reasons.push(`negative expectancy (${a.odds.expectancy_r}R)`);
        if (a.flow && a.flow.agrees_with_price === "disagrees")
          reasons.push("options flow disagrees with the move");
        if (a.exhaustion_score != null && a.exhaustion_score >= 60)
          reasons.push(`high exhaustion risk (${a.exhaustion_score}/100)`);
        if (a.maturity && /late|extend|over/i.test(a.maturity))
          reasons.push(`move is ${a.maturity}`);
        if (reasons.length < 2) return null;
        return (
          <div className="swing-veto" role="alert"
               title="Shown only when several independent negatives line up — a one-glance 'don't chase this' signal. Wait for a cleaner setup or a better price.">
            <span className="swing-veto-tag">⚠ Avoid / wait</span>
            <span className="swing-veto-why">Low-quality entry — {reasons.slice(0, 3).join(" · ")}.</span>
          </div>
        );
      })()}

      {/* ── Live decision box ───────────────────────────────────────────── */}
      {a && (a.status === "ok" || a.status === "no_rhythm") && (
        <div className={`swing-live swing-${dirTone}`}>
          <div className="swing-live-head">
            <span className={`swing-badge ${dirTone}`}>{isUp ? "LONG setup ▲" : "SHORT setup ▼"}</span>
            {a.trend_state && (
              <span className="swing-state" title="Plain-English read of the move">
                <Term k="trend_state">{a.trend_state}</Term>
              </span>
            )}
            {a.maturity && (
              <span className={`swing-maturity ${matTone(a.maturity)}`} title="Where this move sits in the stock's history">
                <Term k="maturity">{a.maturity}</Term>
              </span>
            )}
            {a.status === "no_rhythm" && <span className="swing-maturity">live move</span>}
            {a.do_not_sell_yet && <span className="swing-flag up"><Term k="do_not_sell_yet">Don't sell yet</Term></span>}
            {a.cover_too_early_risk && <span className="swing-flag down"><Term k="cover_too_early">Don't cover yet</Term></span>}
          </div>

          <div className="swing-live-grid">
            <div><span><Term k={isUp ? "swing_low" : "swing_high"}>From {a.from_label}</Term></span>
              <b>{fmtUsd2(a.from_price)} <small>· {fmtSwingDate(a.from_date)}</small></b></div>
            <div><span title="The latest traded price">Current price</span><b>{fmtUsd2(a.current_price)}</b></div>
            <div><span><Term k="current_move">Move so far</Term></span>
              <b className={dirTone}>{sgn(a.current_move_pct)}{a.current_move_pct}% <small>· {a.days_active}{a.days_active === 1 ? "day" : "days"}</small></b></div>
            {a.vs_history && (
              <div><span title="How this move's size compares to the stock's median historical swing (100% = typical)">vs typical move</span>
                <b>{a.vs_history.pct_of_median_move}% of median</b>
                <small className="swing-sub">med {a.vs_history.median_pct}% / {a.vs_history.median_days}d</small></div>
            )}
            {a.targets && (
              <div><span title="Where past moves of this size typically ended — the middle (most likely) projection">Median target</span>
                <b className={dirTone}>{fmtUsd2(a.targets[1].price)} <small>{sgn(a.targets[1].from_here_pct)}{a.targets[1].from_here_pct}% away</small></b></div>
            )}
            <div><span>RSI · rel-vol</span>
              <b><Term k="rsi14">{ind && ind.rsi14 != null ? ind.rsi14 : "—"}</Term> · <Term k="rel_vol">{ind && ind.rel_vol != null ? ind.rel_vol + "x" : "—"}</Term></b></div>
            {a.relative_strength && (
              <div><span><Term k="relative_strength">vs market (SPY)</Term></span>
                <b className={a.relative_strength.leading ? "up" : a.relative_strength.lagging ? "down" : ""}>
                  {sgn(a.relative_strength.vs_spy)}{a.relative_strength.vs_spy}% <small>{a.relative_strength.leading ? "leading" : a.relative_strength.lagging ? "lagging" : "tracking"}</small>
                </b></div>
            )}
            {a.flow && a.flow.data_available && (
              <div><span><Term k="swing_flow">Options flow</Term></span>
                <b className={a.flow.label === "bullish" ? "up" : a.flow.label === "bearish" ? "down" : ""}>
                  {a.flow.label} <small>quality {a.flow.quality}</small>
                </b></div>
            )}
            {a.key_levels && a.key_levels.next && (
              <div><span><Term k="key_levels">Next {a.key_levels.next.kind}</Term></span>
                <b className="warn">{fmtUsd2(a.key_levels.next.price)} <small>{sgn(a.key_levels.next.pct_away)}{a.key_levels.next.pct_away}% · {fmtSwingDate(a.key_levels.next.date)}</small></b></div>
            )}
          </div>

          {a.key_levels && a.key_levels.note && (
            <div className="swing-levelnote"><Term k="key_levels">⊟ Level read:</Term> {a.key_levels.note}</div>
          )}

          {a.level_stats && (() => {
            const ls = a.level_stats;
            const held = ls.hold_rate >= 0.5;
            return (
              <div className="swing-levelstat"
                   title={`From this stock's own history: of ${ls.touches} times price reached the ${ls.kind} near $${ls.level}, it ${held ? "held (bounced)" : "broke through"} more often. 'Held' = reversed away from the level before closing decisively through it.`}>
                <span className="swing-levelstat-ico" aria-hidden="true">⟲</span>
                At ${ls.level}: held <b className={held ? "up" : "down"}>{Math.round(ls.hold_rate * 100)}%</b> of {ls.touches} past touch{ls.touches === 1 ? "" : "es"}
                {ls.median_bounce_pct != null && held && (
                  <span className="muted"> · typical bounce +{ls.median_bounce_pct}%{ls.median_bounce_days ? ` over ${ls.median_bounce_days}d` : ""}</span>
                )}
                {!held && <span className="muted"> · breaks through more than it holds</span>}
              </div>
            );
          })()}

          {(a.broke_resistance || a.after_earnings) && (
            <div className="swing-tags">
              {a.broke_resistance && <span className="swing-tag up"><Term k="broke_resistance">⤴ Broke {isUp ? "resistance" : "support"}</Term></span>}
              {a.after_earnings && <span className="swing-tag"><Term k="after_earnings">⚡ Post-earnings move</Term></span>}
            </div>
          )}

          {a.flow && a.flow.data_available && (
            <div className={`swing-flowagree agree-${a.flow.agrees_with_price}`}>
              <div className="swing-flowagree-head">
                <span><Term k="swing_flow">Options flow agreement</Term></span>
                <b className={a.flow.agrees_with_price === "agrees" ? (isUp ? "up" : "down") : a.flow.agrees_with_price === "disagrees" ? "warn" : ""}>
                  {a.flow.label} · flow {a.flow.agrees_with_price === "agrees" ? "agrees with price" : a.flow.agrees_with_price === "disagrees" ? "disagrees with price" : "neutral vs price"}
                </b>
              </div>
              <div className="swing-flowagree-grid">
                <div><span title="Total premium in bullish options flow today (calls bought / puts sold)">Bullish premium</span><b className="up">{fmtUsd(a.flow.bull_premium, 1)}</b></div>
                <div><span title="Total premium in bearish options flow today (puts bought / calls sold)">Bearish premium</span><b className="down">{fmtUsd(a.flow.bear_premium, 1)}</b></div>
                <div><span title="How aggressive call buying is — sweeps hit several exchanges at once (sweep count in parentheses)">Call sweep pressure</span><b>{a.flow.call_sweep_pressure} <small>({a.flow.call_sweeps})</small></b></div>
                <div><span title="How aggressive put buying / hedging is (sweep count in parentheses)">Put hedge pressure</span><b>{a.flow.put_hedge_pressure} <small>({a.flow.put_sweeps})</small></b></div>
              </div>
            </div>
          )}

          {a.signal_note && <div className="swing-signal">{a.signal_note}</div>}
          {a.status === "no_rhythm" && a.note && <div className="swing-signal">{a.note}</div>}

          {a.continuation_score != null && (
            <div className="swing-scores">
              <ScoreBar label="Continuation" k="continuation_score" score={a.continuation_score} tone={isUp ? "up" : "down"} factors={a.continuation_factors} />
              <ScoreBar label="Exhaustion" k="exhaustion_score" score={a.exhaustion_score} tone="warn" factors={a.exhaustion_factors} />
            </div>
          )}
        </div>
      )}

      {/* ── Odds & risk/reward — decision synthesis from this stock's history ─ */}
      {a && a.status === "ok" && a.odds && (() => {
        const o = a.odds;
        const vClass = ({ favorable: "up", unfavorable: "down", balanced: "warn" }[o.verdict]) || "muted";
        return (
          <div className={`swing-odds odds-${vClass}`}>
            <div className="swing-odds-head">
              <span className="swing-subtitle" title="A trade-decision read built only from this stock's OWN past swings of the same direction — how good the reward-to-risk is, how often a move this far went on to the next target, and the resulting expected value. Not generic; specific to this name's rhythm.">Odds &amp; risk / reward</span>
              <span className={`swing-odds-verdict ${vClass}`} title="Overall read combining reward:risk with the historical hit rate. Favorable = positive expected value and healthy R:R.">{o.verdict}</span>
            </div>
            <div className="swing-odds-grid">
              <div title="Reward-to-risk at the planned entry: distance from entry to the next target ÷ distance from entry to the invalidation (stop). 'from here' uses the current price instead of the planned entry.">
                <span>Reward : risk</span>
                <b className={o.reward_risk >= 1.5 ? "up" : o.reward_risk < 1 ? "down" : ""}>{o.reward_risk != null ? `${o.reward_risk} : 1` : "—"}</b>
                {o.reward_risk_now != null && <small className="swing-sub">{o.reward_risk_now} : 1 from here</small>}
              </div>
              <div title={`Of the ${o.sample} past ${o.target_label ? a.direction + "-" : ""}moves that ran at least this far, the share that went on to reach the ${o.target_label} target.`}>
                <span>Hit rate</span>
                <b className={o.win_pct >= 55 ? "up" : o.win_pct < 40 ? "down" : ""}>{o.win_pct != null ? `${o.win_pct}%` : "—"}</b>
                <small className="swing-sub">{o.sample} similar move{o.sample === 1 ? "" : "s"}</small>
              </div>
              <div title="Expected value per attempt in R (risk units): hit-rate × reward:risk − miss-rate. Above 0 means the setup pays out over many tries.">
                <span>Expected value</span>
                <b className={o.expectancy_r > 0 ? "up" : o.expectancy_r < 0 ? "down" : ""}>{o.expectancy_r != null ? `${o.expectancy_r > 0 ? "+" : ""}${o.expectancy_r}R` : "—"}</b>
              </div>
              <div title="The reward leg (next ladder rung beyond the current move) and the stop where the thesis is invalidated.">
                <span>Target / stop</span>
                <b className={isUp ? "up" : "down"}>{fmtUsd2(o.target_price)}</b>
                <small className="swing-sub">stop {fmtUsd2(o.risk_price)}</small>
              </div>
            </div>
            <div className="swing-odds-note">{o.note}</div>
          </div>
        );
      })()}


      {/* ── Target ladder ───────────────────────────────────────────────── */}
      {a && a.status === "ok" && (
        <div style={{ marginTop: 12 }}>
          <div className="swing-subtitle"><Term k="target_ladder">Projected target ladder</Term> — from {a.from_label} {fmtUsd2(a.from_price)}</div>
          {a.key_levels && ((a.key_levels.supports || []).length > 0 || (a.key_levels.resistances || []).length > 0) && (
            <div className="swing-levels">
              <span className="swing-levels-lbl"><Term k="key_levels">Key levels</Term></span>
              {(a.key_levels.resistances || []).slice().reverse().map((l, i) => (
                <span key={"r" + i} className="swing-lvl res" title={`Resistance · prior swing high ${fmtSwingDate(l.date)}`}>{fmtUsd2(l.price)} <small>+{l.pct_away}%</small></span>
              ))}
              <span className="swing-lvl now">{fmtUsd2(a.current_price)} now</span>
              {(a.key_levels.supports || []).map((l, i) => (
                <span key={"s" + i} className="swing-lvl sup" title={`Support · prior swing low ${fmtSwingDate(l.date)}`}>{fmtUsd2(l.price)} <small>{l.pct_away}%</small></span>
              ))}
            </div>
          )}
          <div className="scan-table-wrap">
          <table className="scan-table swing-table mtable">
            <thead>
              <tr>
                <th title="Projection tier — conservative, median, aggressive, or extreme">Target</th>
                <th className="scan-th-num" title={`Percent ${isUp ? "gain" : "drop"} from the swing origin to this target`}>{isUp ? "Upside" : "Downside"} %</th>
                <th className="scan-th-num" title="Projected price at this target">Price</th>
                <th className="scan-th-num" title="Percent move still needed from the current price ('reached' if already hit)">From here</th>
                <th className="scan-th-num" title="Estimated date this target is reached, based on similar past moves">By (est.)</th>
                <th className="scan-th-num"><Term k="confidence_rating">Confidence</Term></th>
              </tr>
            </thead>
            <tbody>
              {a.targets.map((t, i) => (
                <tr key={i} className="scan-row">
                  <td data-label="Target" style={{ textTransform: "capitalize" }} title={TARGET_BASIS[t.label] || ""}>{t.label}{t.reached ? " ✓" : ""}</td>
                  <td data-label={isUp ? "Upside %" : "Downside %"} className="scan-num">{sgn(isUp ? t.pct_move : -t.pct_move)}{isUp ? t.pct_move : -t.pct_move}%</td>
                  <td data-label="Price" className="scan-num">{fmtUsd2(t.price)}</td>
                  <td data-label="From here" className={`scan-num ${t.reached ? "muted" : dirTone}`}>{t.reached ? "reached" : `${sgn(t.from_here_pct)}${t.from_here_pct}%`}</td>
                  <td data-label="By (est.)" className="scan-num">{fmtSwingDate(t.eta_date)}</td>
                  <td data-label="Confidence" className={`scan-num ${confTone(t.confidence)}`} title={`Matched ${t.matched} past move${t.matched === 1 ? "" : "s"} of this size or bigger`}>{t.confidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          {a.confidence && (
            <div className={`swing-confwhy conf-${a.confidence.level}`}>
              <b><Term k="confidence_rating">Confidence: {a.confidence.level}</Term></b>
              {" "}<span>because {(a.confidence.reasons || []).join(", ")}.</span>
            </div>
          )}
        </div>
      )}

      {/* ── Trade plan ──────────────────────────────────────────────────── */}
      {a && a.status === "ok" && a.trade_plan && (
        <div className="swing-plan">
          <div className="swing-subtitle" title="A concrete plan for this setup: where to enter, where it's wrong, targets, and how long to hold">{a.trade_plan.side === "long" ? "Long" : "Short"} trade plan</div>
          <div className="swing-plan-grid">
            <div><span><Term k="trade_entry_zone">Entry zone</Term></span><b>{fmtUsd2(a.trade_plan.entry_zone[0])} – {fmtUsd2(a.trade_plan.entry_zone[1])}</b></div>
            <div><span><Term k="trade_invalidation">Invalidation</Term></span><b className="down">{fmtUsd2(a.trade_plan.invalidation)}</b></div>
            <div><span><Term k="trade_t1">Target 1 (median)</Term></span><b className={dirTone}>{fmtUsd2(a.trade_plan.t1)}</b></div>
            <div><span><Term k="trade_t2">Target 2 (stretch)</Term></span><b className={dirTone}>{fmtUsd2(a.trade_plan.t2)}</b></div>
            <div><span><Term k="trade_extreme">Extreme</Term></span><b className={dirTone}>{fmtUsd2(a.trade_plan.stretch)}</b></div>
            <div><span><Term k="trade_holding">Holding window</Term></span>
              {(() => {
                const hw = a.trade_plan.holding_window || "";
                const m = /^(.*?)\s*\(through\s*(.+)\)\s*$/.exec(hw);
                return m
                  ? <b>{m[1]}<small className="swing-sub">through {m[2]}</small></b>
                  : <b>{hw}</b>;
              })()}</div>
          </div>
          <div className="swing-plan-note">{a.trade_plan.entry_note}</div>
          <div className="swing-plan-note muted">{a.trade_plan.invalidation_note}</div>
          <div className="swing-plan-cols">
            <div>
              <div className="swing-plan-h up" title="Factors that support staying in / adding to this trade">Reasons to stay</div>
              <ul>{a.trade_plan.reason_to_stay.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
            <div>
              <div className="swing-plan-h warn" title="Signs that argue for taking profits or exiting">Exit warnings</div>
              <ul>{a.trade_plan.exit_warnings.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </div>
          </div>
          {a.similar_move && <div className="swing-plan-note"><b><Term k="similar_move">Similar past move:</Term></b> {a.similar_move.note}</div>}
        </div>
      )}

      {/* ── History table (up / down toggle + filters) ──────────────────── */}
      <div className="swing-histnav" style={{ marginTop: 14 }}>
        <button type="button" className={tab === "up" ? "active" : ""} onClick={() => setTab("up")} title="Past upward swings in this stock's history (count)">Up-swings ({upSwings.length})</button>
        <button type="button" className={tab === "down" ? "active" : ""} onClick={() => setTab("down")} title="Past downward swings in this stock's history (count)">Down-swings ({downSwings.length})</button>
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
          <table className="scan-table swing-table mtable mtable-hist">
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
              {histSwings.slice().reverse().map((s, i) => {
                const rk = `${tab}-${i}`;
                const open = openRow === rk;
                const det = s.detail || {};
                return (
                  <React.Fragment key={rk}>
                    <tr className={`scan-row swing-exrow${open ? " open" : ""}`}
                        onClick={() => {
                          if (open) { setOpenRow(null); setFocusKey(null); }  // click again = collapse + zoom back out
                          else { setOpenRow(rk); focusSwingOnChart(s); }
                        }}
                        title="Click to expand & zoom to this move · click again to zoom back out">
                      {tab === "up" ? (
                        <React.Fragment>
                          <td data-label="Swing low"><span className="swing-caret">{open ? "▾" : "▸"}</span> {fmtSwingDate(s.low_date)}</td>
                          <td data-label="Low $" className="scan-num">{fmtUsd2(s.low_price)}</td>
                          <td data-label="Swing high">{fmtSwingDate(s.high_date)}</td>
                          <td data-label="High $" className="scan-num">{fmtUsd2(s.high_price)}</td>
                        </React.Fragment>
                      ) : (
                        <React.Fragment>
                          <td data-label="Swing high"><span className="swing-caret">{open ? "▾" : "▸"}</span> {fmtSwingDate(s.high_date)}</td>
                          <td data-label="High $" className="scan-num">{fmtUsd2(s.high_price)}</td>
                          <td data-label="Swing low">{fmtSwingDate(s.low_date)}</td>
                          <td data-label="Low $" className="scan-num">{fmtUsd2(s.low_price)}</td>
                        </React.Fragment>
                      )}
                      <td data-label="Days" className="scan-num">{s.trading_days}</td>
                      <td data-label="$ chg" className={`scan-num ${tab === "up" ? "" : "down"}`}>{fmtUsd2(s.dollar_change)}</td>
                      <td data-label={tab === "up" ? "% chg" : "% drop"} className={`scan-num ${tab === "up" ? "up" : "down"}`}>{s.pct_change}%</td>
                      <td data-label="Avg/day" className="scan-num">{s.avg_daily_pct}%</td>
                      <td data-label="Rhythm" className="scan-num">{s.matches_rhythm ? "✓" : "·"}</td>
                      <td data-label="Flags" className="swing-flagcell">
                        {s.above_avg_vol && <span title={`Above-average volume${s.vol_ratio ? ` (${s.vol_ratio}x)` : ""}`}>🔥</span>}
                        {s.broke_resistance && <span title={`Broke prior ${tab === "up" ? "resistance" : "support"}`}>⤴</span>}
                        {s.failed_breakout && <span title="Failed breakout — level didn't hold">⚠</span>}
                        {s.after_earnings && <span title="Launched after an earnings report">⚡</span>}
                      </td>
                    </tr>
                    {open && (
                      <tr className="swing-detailrow">
                        <td colSpan={10} className="mtable-full">
                          <div className="swing-detailgrid">
                            {det.before && <div><span>Before the move</span><b>{det.before}</b></div>}
                            {det.beyond_median && <div><span>Past the median target</span><b>{det.beyond_median}</b></div>}
                            {det.after && <div><span>After the {tab === "up" ? "high" : "low"}</span><b>{det.after}</b></div>}
                            {det.hold_vs_target && <div><span>Sell at target vs hold</span><b>{det.hold_vs_target}</b></div>}
                            {!det.before && !det.after && <div><span>Detail</span><b>Not enough surrounding history for this swing.</b></div>}
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
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

      {/* ── TradingView chart (Charting Library if licensed files present,
            else Lightweight Charts) with the swing overlay ──────────────── */}
      {data && (data.bars || []).length > 0 && (
        <TVAdvancedChart apiFetch={apiFetch} ticker={ticker} data={data}
          fallback={<SwingChart data={data} focusKey={focusKey} onPickSwing={pickSwingByTime} onClearFocus={() => { setFocusKey(null); setOpenRow(null); }} />} />
      )}
      {data && data.analysis && data.analysis.status === "ok" && <SwingPrediction data={data} />}
    </div>
  );
}

// Trade ticket: turn a swing read into a sized, EV-ranked order. Stop = swing
// origin (where the thesis dies), target = origin + the stock's typical move,
// size = risk-budget / per-share risk. EV is the expected R-multiple. All from
// fields already on the row — no extra cost.
function computeTicket(r, acct, riskPct) {
  if (!r || !r.swing_dir || r.swing_from == null || r.swing_med_pct == null || r.last == null) return {};
  const r2 = (x) => Math.round(x * 100) / 100;
  const long = r.swing_dir === "long";
  const stop = r.swing_from;
  const target = long ? stop * (1 + r.swing_med_pct / 100) : stop * (1 - r.swing_med_pct / 100);
  const price = r.last;
  const risk = Math.abs(price - stop);
  const reward = long ? (target - price) : (price - target);
  const base = { tk_target: r2(target), tk_stop: r2(stop) };
  if (!(risk > 0) || !(reward > 0)) return { ...base, tk_rr: null, tk_ev: null, tk_size: null };  // exhausted / no edge
  const rr = reward / risk;
  const wr = r.swing_winrate != null ? r.swing_winrate : 0.5;
  const ev = wr * rr - (1 - wr);
  const size = Math.floor(((acct || 0) * (riskPct || 0) / 100) / risk);
  return { ...base, tk_rr: r2(rr), tk_ev: r2(ev), tk_size: size > 0 ? size : null,
           tk_riskUsd: size > 0 ? Math.round(size * risk) : null,
           tk_rewardUsd: size > 0 ? Math.round(size * reward) : null,
           tk_wr: Math.round(wr * 100) };
}

// Global cooldown so the watchlist board's auto-reconcile scan can't thrash
// across tab switches / remounts.
let _wlLastAutoScan = 0;

function WatchlistTableCard({ apiFetch, onSwitchTicker, market, onRemoveSymbol, watchlistSymbols }) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [sort, setSort] = useState({ key: "edge", dir: "desc" });
  const [gsort, setGsort] = useState({ key: "net", dir: "desc" });
  const [view, setView] = useState("stocks"); // stocks | sectors | industries
  const [fSector, setFSector] = useState("all");
  const [fIndustry, setFIndustry] = useState("all");
  const [fTag, setFTag] = useState("all");
  const [q, setQ] = useState("");
  const [fMcap, setFMcap] = useState("all");
  const [primeOnly, setPrimeOnly] = useState(false);  // confluence shortlist
  const [acct, setAcct] = useState(() => { try { return Number(localStorage.getItem("jerry_acct")) || 100000; } catch { return 100000; } });
  const [riskPct, setRiskPct] = useState(() => { try { return Number(localStorage.getItem("jerry_riskpct")) || 1; } catch { return 1; } });
  useEffect(() => { try { localStorage.setItem("jerry_acct", String(acct)); } catch {} }, [acct]);
  useEffect(() => { try { localStorage.setItem("jerry_riskpct", String(riskPct)); } catch {} }, [riskPct]);
  const [removed, setRemoved] = useState(() => new Set()); // optimistic hide after delete
  const [ctx, setCtx] = useState(null); // right-click menu: { x, y, symbol }
  const [analystBy, setAnalystBy] = useState({}); // symbol -> fresh-action summary (badge)
  const pollRef = useRef(null);

  // Fresh-analyst-action map for the in-table badge (cheap, server-cached).
  useEffect(() => {
    let stop = false;
    const grab = async () => {
      try {
        const r = await apiFetch("/api/watchlist_analyst");
        const d = await r.json();
        if (!stop) setAnalystBy((d && d.by_symbol) || {});
      } catch (_) { /* badge is best-effort */ }
    };
    grab();
    const t = setInterval(grab, 5 * 60 * 1000);
    return () => { stop = true; clearInterval(t); };
  }, []);

  const load = async () => {
    try { const d = await sharedJson(apiFetch, "/api/watchlist_table", 20000); setBoard(d); setErr(null); return d; }
    catch (e) { setErr(String(e)); return null; }
  };
  useEffect(() => { load(); return () => { if (pollRef.current) clearInterval(pollRef.current); }; }, []);
  const startScan = async () => {
    setErr(null);
    try { await apiFetch("/api/watchlist_table/scan?force=1"); } catch (e) { setErr(String(e)); return; }
    await load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.status || !d.status.scanning) { clearInterval(pollRef.current); pollRef.current = null; }
    }, 4000);
  };

  const status = (board && board.status) || {};
  const allRows = useMemo(() => edgesFor((board && board.rows) || []), [board]);
  // Live watchlist set: the scan cache can lag behind the current watchlist
  // (a deleted symbol stays in the cache until the next scan). Filtering to
  // the live watchlist makes the table reflect reality immediately and keeps
  // right-click deletes from "coming back" on refresh.
  const wlSet = useMemo(() => (
    (watchlistSymbols && watchlistSymbols.length)
      ? new Set(watchlistSymbols.map(s => String(s).toUpperCase()))
      : null
  ), [watchlistSymbols]);
  const rows = useMemo(() => allRows
    .filter(r => !removed.has(r.symbol) && (!wlSet || wlSet.has(String(r.symbol).toUpperCase())))
    .map(r => ({ ...r, ...computeTicket(r, acct, riskPct) })),
    [allRows, removed, wlSet, acct, riskPct]);
  const mcapPass = (mc) => (MCAP_PRED[fMcap] || MCAP_PRED.all)(mc || 0);
  // Prime setup = the two independent lenses agree AND the move is early:
  // options-flow Edge direction == price-swing direction, swing just starting.
  // That's the highest-conviction "beginning of the move" trade.
  const isPrime = (r) => {
    if (r.swing_stage !== "early" || !r.swing_dir) return false;
    return (r.swing_dir === "long" && r.edge_dir === "long")
        || (r.swing_dir === "short" && r.edge_dir === "short");
  };
  const primeCount = useMemo(() => rows.filter(isPrime).length, [rows]);
  // Crowding check: if the Prime setups pile into one sector, that's really
  // one bet, not many — pros net correlated risk.
  const primeCrowd = useMemo(() => {
    const ps = rows.filter(isPrime);
    if (ps.length < 3) return null;
    const by = {};
    ps.forEach(r => { const s = r.sector || "—"; by[s] = (by[s] || 0) + 1; });
    let top = null, n = 0;
    Object.entries(by).forEach(([s, c]) => { if (c > n) { n = c; top = s; } });
    return (top && n >= 3 && n / ps.length >= 0.5) ? { sector: top, n, total: ps.length } : null;
  }, [rows]);
  const scanning = !!status.scanning;
  const sectors = (board && board.sectors) || [];
  const industries = (board && board.industries) || [];
  const tagOpts = (board && board.tags) || [];

  const COLS = [
    { k: "symbol", label: "Symbol" }, { k: "company", label: "Company" },
    { k: "tag", label: "Tag" }, { k: "weekly", label: "Weekly" },
    { k: "edge", label: "Edge", num: true }, { k: "setup", label: "Setup" },
    { k: "prem_sell", label: "Premium" },
    { k: "swing_dir", label: "Swing" }, { k: "swing_stage", label: "Timing" },
    { k: "tk_ev", label: "EV", num: true }, { k: "tk_size", label: "Size", num: true },
    { k: "rvol_rank", label: "Vol", num: true },
    { k: "last", label: "Price", num: true }, { k: "market_cap", label: "Mkt Cap", num: true },
    { k: "pe", label: "P/E", num: true }, { k: "forward_pe", label: "Fwd P/E", num: true },
    { k: "industry", label: "Industry" }, { k: "sector", label: "Sector" },
    { k: "rsi", label: "RSI", num: true }, { k: "rel_vol", label: "Rel Vol", num: true },
    { k: "flow_net", label: "Flow", num: true }, { k: "flow_agree", label: "Agree" },
    { k: "flow_bull", label: "Bull", num: true }, { k: "flow_bear", label: "Bear", num: true },
    { k: "call_prem", label: "Bull $", num: true }, { k: "put_prem", label: "Bear $", num: true },
    { k: "net_prem", label: "Net $", num: true }, { k: "pc_ratio", label: "P/C", num: true },
    { k: "ask_call_prem", label: "Ask C$", num: true }, { k: "ask_put_prem", label: "Ask P$", num: true },
    { k: "call_sweeps", label: "C Swp", num: true }, { k: "put_sweeps", label: "P Swp", num: true },
    { k: "flow_alerts", label: "Alerts", num: true }, { k: "flow_quality", label: "Conv", num: true },
    { k: "flow_cc_risk", label: "CC Risk", num: true }, { k: "flow_verdict", label: "Verdict" },
    { k: "next_earnings", label: "Earnings", num: true },
    { k: "change", label: "Chg%", num: true },
    { k: "from_open", label: "% From Open", num: true },
    { k: "wtd", label: "WTD%", num: true }, { k: "mtd", label: "MTD%", num: true },
    { k: "qtd", label: "QTD%", num: true }, { k: "ytd", label: "YTD%", num: true },
    { k: "from_ma20", label: "%20DMA", num: true }, { k: "from_ma50", label: "%50DMA", num: true },
    { k: "from_ma200", label: "%200DMA", num: true },
  ];
  const STR = new Set(["symbol", "company", "tag", "weekly", "industry", "sector", "flow_agree", "flow_verdict", "setup", "prem_sell", "swing_dir", "swing_stage"]);
  const setSortKey = (k) => setSort(s => s.key === k ? { key: k, dir: s.dir === "asc" ? "desc" : "asc" } : { key: k, dir: STR.has(k) ? "asc" : "desc" });

  // Per-column header tooltips.
  const COL_TIPS = {
    symbol: "Ticker symbol (★ = Prime setup). Click a row to open it.",
    company: "Company name",
    tag: "Your category from the imported CSV — use the Tag filter to group your list.",
    weekly: "Whether the stock has weekly options (from your CSV). Yes / No / blank.",
    edge: "Edge — signed options-flow conviction (+long / −short), size-normalized. Sort to rank morning buys vs sells. The dot shows flow freshness: green = live this scan, grey = minutes old, amber = hours old (cached — outside this scan's live-flow budget).",
    setup: "Plain-English read of the edge drivers", prem_sell: "Suggested premium-selling side",
    swing_dir: "Active price-swing direction (long/short)", swing_stage: "Where in the swing the move is (early/mid/late)",
    tk_ev: "Expected value per trade in R (win-rate × reward:risk − loss odds)", tk_size: "Risk-based position size (account × risk% ÷ stop distance)",
    rvol_rank: "Realized-vol rank 0-100 vs the stock's own year (↑ rich → sell premium, ↓ cheap → buy)",
    last: "Last price (live during market hours)", market_cap: "Market capitalization",
    pe: "Trailing P/E", forward_pe: "Forward P/E", industry: "Industry", sector: "Sector",
    rsi: "RSI(14)", rel_vol: "Relative volume vs 20-day average",
    flow_net: "Net options-flow direction/score", flow_agree: "Does flow agree with the price move?",
    flow_bull: "Bullish flow sub-score", flow_bear: "Bearish flow sub-score",
    call_prem: "Total call premium today", put_prem: "Total put premium today", net_prem: "Net (call − put) premium",
    pc_ratio: "Put/Call premium ratio", ask_call_prem: "Ask-side (aggressive) call premium", ask_put_prem: "Ask-side (aggressive) put premium",
    call_sweeps: "Call sweep count", put_sweeps: "Put sweep count", flow_alerts: "Unusual-flow alert count",
    flow_quality: "Flow conviction 0-100 (0 noise, 100 high-conviction)", flow_cc_risk: "Covered-call risk 0-100 (high = avoid selling calls)",
    flow_verdict: "Decision-engine verdict", next_earnings: "Next earnings date (days away)",
    change: "Change % today (live)", from_open: "% from today's open — (live price − open) / open. Sort to rank intraday gainers from the open (live).",
    wtd: "Week-to-date % (live)", mtd: "Month-to-date % (live)",
    qtd: "Quarter-to-date % (live)", ytd: "Year-to-date % (live)",
    from_ma20: "% from the 20-day moving average (live)", from_ma50: "% from the 50-day MA (live)", from_ma200: "% from the 200-day MA (live)",
  };
  // Movable columns — drag a header to reorder; order persists per device.
  const COL_ORDER_KEY = "jerry_wl_colorder_v1";
  const _defaultOrder = COLS.map(c => c.k);
  const [colOrder, setColOrder] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(COL_ORDER_KEY) || "null");
      if (Array.isArray(saved)) {
        const known = new Set(_defaultOrder);
        const kept = saved.filter(k => known.has(k));
        const added = _defaultOrder.filter(k => !kept.includes(k));  // surface new columns
        return [...kept, ...added];
      }
    } catch (_) {}
    return _defaultOrder;
  });
  useEffect(() => { try { localStorage.setItem(COL_ORDER_KEY, JSON.stringify(colOrder)); } catch (_) {} }, [colOrder]);
  const _colByKey = {}; COLS.forEach(c => { _colByKey[c.k] = c; });
  const orderedCols = colOrder.map(k => _colByKey[k]).filter(Boolean);
  const dragColKey = useRef(null);
  const onColDrop = (targetK) => {
    const from = dragColKey.current; dragColKey.current = null;
    if (!from || from === targetK) return;
    setColOrder(prev => {
      const arr = prev.filter(k => k !== from);
      const idx = arr.indexOf(targetK);
      arr.splice(idx < 0 ? arr.length : idx, 0, from);
      return arr;
    });
  };

  // Industry dropdown is scoped to the selected sector so it only lists
  // industries that actually live in that sector.
  const industryOpts = useMemo(() => {
    if (fSector === "all") return industries;
    const set = new Set();
    rows.forEach(r => { if (r.sector === fSector && r.industry) set.add(r.industry); });
    return Array.from(set).sort();
  }, [rows, industries, fSector]);
  // If the chosen industry isn't in the (newly) selected sector, reset it.
  useEffect(() => {
    if (fIndustry !== "all" && !industryOpts.includes(fIndustry)) setFIndustry("all");
  }, [industryOpts, fIndustry]);

  // Live-price overlay state + helpers. The batch poll that fills liveQ is
  // further down (it needs `shown`); the state lives here so `filtered` can
  // sort by the live "% From Open".
  const [liveQ, setLiveQ] = useState({});          // symbol -> live last price
  const liveLast = (r) => { const q = liveQ[r.symbol]; return (q && q.last != null) ? q.last : r.last; };
  const reb = (r, oldPct) => {
    const q = liveQ[r.symbol];
    const live = q && q.last != null ? q.last : null;
    if (live == null || !r.last || oldPct == null) return oldPct;
    return ((live / r.last) * (1 + oldPct / 100) - 1) * 100;
  };
  // % from today's open. Prefer the live quote's open (always today's), fall
  // back to the scan's open; rebase against the live price (open is fixed
  // intraday). Works as soon as quotes arrive — no re-scan needed.
  const foVal = (r) => {
    const q = liveQ[r.symbol];
    const open = (q && q.open != null) ? q.open : r.open;
    const last = liveLast(r);
    return (open && last != null) ? ((last - open) / open) * 100 : null;
  };
  // Daily change %. Use the live quote's own change (always measured vs the
  // PRIOR SESSION's close — Friday on a Monday) rather than rebasing the scan's
  // `change`, whose base is the scan's previous daily bar — which is Thursday
  // when the scan ran pre-open Monday, making CHG% wrongly include Friday.
  const chgVal = (r) => {
    const q = liveQ[r.symbol];
    return (q && q.chg != null) ? q.chg : reb(r, r.change);
  };
  // Columns whose displayed value is the LIVE-rebased % (not the raw scan
  // field). Sorting must use the same live value or the order won't match what
  // Every column that DISPLAYS a live value must SORT on that same live value,
  // or the order won't match what's on screen:
  //   last       -> liveLast (live price)
  //   change     -> chgVal   (live quote's daily change)
  //   from_open  -> foVal    (live, vs today's open)
  //   wtd..%DMAs -> reb      (live, rebased to the live price)
  // Everything else sorts on the scanned field.
  const REB_KEYS = new Set(["wtd", "mtd", "qtd", "ytd", "from_ma20", "from_ma50", "from_ma200"]);
  const sortValOf = (r, key) => key === "from_open" ? foVal(r)
    : key === "change" ? chgVal(r)
    : key === "last" ? liveLast(r)
    : (REB_KEYS.has(key) ? reb(r, r[key]) : r[key]);

  const filtered = useMemo(() => {
    let out = rows.filter(r => {
      if (primeOnly && !isPrime(r)) return false;
      if (fSector !== "all" && r.sector !== fSector) return false;
      if (fIndustry !== "all" && r.industry !== fIndustry) return false;
      if (fTag !== "all" && (r.tag || "") !== fTag) return false;
      if (fMcap !== "all" && !mcapPass(r.market_cap)) return false;
      if (q && !`${r.symbol} ${r.company || ""}`.toLowerCase().includes(q.toLowerCase())) return false;
      return true;
    });
    const { key, dir } = sort, mul = dir === "asc" ? 1 : -1;
    out = out.slice().sort((a, b) => {
      // Live %-columns (Chg/WTD/MTD/QTD/YTD/%DMAs/% From Open) sort on the LIVE
      // value so the order matches the live numbers shown; others sort the field.
      let av = sortValOf(a, key);
      let bv = sortValOf(b, key);
      if (av == null && bv == null) return 0;
      if (av == null) return 1; if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * mul;
      return (av - bv) * mul;
    });
    return out;
  }, [rows, fSector, fIndustry, fTag, fMcap, q, sort, primeOnly, liveQ]);

  // Progressive rendering: the stocks table can hold ~550 rows × 40+ columns.
  // Painting them all (and re-painting on every sort/filter) is the table's
  // biggest cost. Render a chunk and append more as you scroll — after a sort
  // you look at the top anyway, so this caps the expensive re-render at one
  // chunk while keeping auto column widths stable (the set only grows). True
  // row-windowing was avoided on purpose: this table is auto-layout with a
  // sticky first column, so windowing would make columns jitter horizontally.
  const WL_CHUNK = 120;
  const [visN, setVisN] = useState(WL_CHUNK);
  // Reset to the top when the result set genuinely changes (sort / filter /
  // search / new scan) — NOT on live-price re-sorts, which would otherwise
  // yank the scroll back to the top every poll.
  useEffect(() => { setVisN(WL_CHUNK); }, [sort, q, fSector, fIndustry, fTag, fMcap, primeOnly, rows]);
  const wlScrollRef = useRef(null);
  const onWlScroll = (e) => {
    if (visN >= filtered.length) return;
    const el = e.currentTarget;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 600) {
      setVisN(n => Math.min(n + WL_CHUNK, filtered.length));
    }
  };
  const shown = view === "stocks" ? filtered.slice(0, visN) : filtered;

  // ── Live price overlay: batch-poll /api/quote for the on-screen rows ────
  // CHG%/WTD/MTD/QTD/YTD, the %DMAs and % From Open are all (price − base)/base
  // with a base that's fixed intraday, so we rebase against the live price —
  // no re-scan. 25 symbols/call, Schwab-cached; pauses when hidden.
  const shownSymsKey = view === "stocks" ? shown.map(r => r.symbol).join(",") : "";
  useEffect(() => {
    if (view !== "stocks" || !shownSymsKey) return;
    const syms = shownSymsKey.split(",");
    let stop = false;
    const poll = async () => {
      if (document.hidden) return;
      for (let i = 0; i < syms.length && !stop; i += 25) {
        const batch = syms.slice(i, i + 25);
        try {
          const r = await apiFetch(`/api/quote?tickers=${encodeURIComponent(batch.join(","))}`);
          if (!r.ok) continue;
          const j = await r.json();
          if (stop) return;
          const res = j.results || {};
          setLiveQ(prev => {
            const next = { ...prev };
            for (const s of batch) { const q = res[s]; if (q && q.last) next[s] = { last: q.last, open: q.open != null ? q.open : null, chg: q.change_pct != null ? q.change_pct : null }; }
            return next;
          });
        } catch (_) {}
      }
    };
    poll();
    const id = setInterval(poll, 20000);   // 20s; quote cache is 25s
    return () => { stop = true; clearInterval(id); };
  }, [shownSymsKey, view]);

  // How many of the user's tracked symbols aren't in the last scan's board —
  // either added since the scan, or skipped because the data source returned
  // no price for them. Surfaced so the count gap never looks like missing data.
  const notScanned = useMemo(() => {
    if (!watchlistSymbols || !watchlistSymbols.length) return 0;
    const boardSet = new Set(allRows.map(r => String(r.symbol).toUpperCase()));
    return watchlistSymbols.filter(s => !boardSet.has(String(s).toUpperCase())).length;
  }, [allRows, watchlistSymbols]);

  // Auto-reconcile: if the board is badly out of sync with the watchlist (lots
  // of tracked symbols missing — e.g. after a restore or bulk add), kick a
  // scan automatically so the table fills itself in, instead of making the
  // user find "Scan now". Bounded by a 10-minute global cooldown so it can't
  // thrash, and only fires once the board has actually loaded.
  useEffect(() => {
    if (scanning || !board) return;
    if (notScanned < 25) return;
    const now = Date.now();
    if (now - _wlLastAutoScan < 10 * 60 * 1000) return;
    _wlLastAutoScan = now;
    startScan();
  }, [notScanned, scanning, board]);

  // Sector / industry rollup. Sums the per-stock premium fields (all from
  // the flow_alerts call already made — no extra UW cost) so you can see
  // where money is flowing in and out at the group level. Respects the
  // sector/industry dropdowns so you can drill the industry rollup into a
  // single sector. Stocks with no flow data are counted but contribute $0.
  const groupKey = view === "sectors" ? "sector" : "industry";
  const groups = useMemo(() => {
    if (view === "stocks") return [];
    const base = rows.filter(r => {
      if (fSector !== "all" && r.sector !== fSector) return false;
      if (fIndustry !== "all" && r.industry !== fIndustry) return false;
      if (fTag !== "all" && (r.tag || "") !== fTag) return false;
      if (fMcap !== "all" && !mcapPass(r.market_cap)) return false;
      return true;
    });
    const map = new Map();
    base.forEach(r => {
      const name = r[groupKey] || "—";
      let g = map.get(name);
      if (!g) { g = { name, stocks: 0, withFlow: 0, mcap: 0, nBull: 0, nBear: 0, bull: 0,
                      bear: 0, net: 0, askC: 0, askP: 0, cSwp: 0, pSwp: 0, alerts: 0 };
                map.set(name, g); }
      g.stocks++;
      g.mcap += r.market_cap || 0;
      if (r.flow_available) {
        g.withFlow++;
        g.bull += r.call_prem || 0; g.bear += r.put_prem || 0;
        g.net += r.net_prem || 0;
        g.askC += r.ask_call_prem || 0; g.askP += r.ask_put_prem || 0;
        g.cSwp += r.call_sweeps || 0; g.pSwp += r.put_sweeps || 0;
        g.alerts += r.flow_alerts || 0;
        if (r.net_prem > 0) g.nBull++; else if (r.net_prem < 0) g.nBear++;
      }
    });
    let arr = Array.from(map.values());
    arr.forEach(g => { g.pc = g.bull > 0 ? Math.round(g.bear / g.bull * 100) / 100 : null; });
    const { key, dir } = gsort, mul = dir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      let av = a[key], bv = b[key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1; if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * mul;
      return (av - bv) * mul;
    });
    return arr;
  }, [rows, view, groupKey, fSector, fIndustry, fTag, fMcap, gsort]);
  const GCOLS = [
    { k: "name", label: view === "sectors" ? "Sector" : "Industry" },
    { k: "stocks", label: "Stocks", num: true },
    { k: "mcap", label: "Mkt Cap", num: true },
    { k: "nBull", label: "Bull #", num: true }, { k: "nBear", label: "Bear #", num: true },
    { k: "bull", label: "Bull $", num: true }, { k: "bear", label: "Bear $", num: true },
    { k: "net", label: "Net $", num: true }, { k: "pc", label: "P/C", num: true },
    { k: "askC", label: "Ask C$", num: true }, { k: "askP", label: "Ask P$", num: true },
    { k: "cSwp", label: "C Swp", num: true }, { k: "pSwp", label: "P Swp", num: true },
    { k: "alerts", label: "Alerts", num: true },
  ];
  const setGsortKey = (k) => setGsort(s => s.key === k ? { key: k, dir: s.dir === "asc" ? "desc" : "asc" } : { key: k, dir: k === "name" ? "asc" : "desc" });
  // Drill a group row down into the per-stock view, pre-filtered.
  const drillGroup = (name) => {
    if (view === "sectors") setFSector(name === "—" ? "all" : name);
    else setFIndustry(name === "—" ? "all" : name);
    setView("stocks");
  };
  // Right-click delete: hide the row immediately and remove it from the
  // (server-synced) watchlist via the parent handler.
  const doRemove = (sym) => {
    setRemoved(prev => { const n = new Set(prev); n.add(sym); return n; });
    if (onRemoveSymbol) onRemoveSymbol(sym);
    setCtx(null);
  };
  useEffect(() => {
    if (!ctx) return undefined;
    const close = () => setCtx(null);
    const onKey = (e) => { if (e.key === "Escape") setCtx(null); };
    document.addEventListener("click", close);
    document.addEventListener("scroll", close, true);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("scroll", close, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [ctx]);

  const pctCls = (v) => v == null ? "" : v >= 0 ? "up" : "down";
  const pct = (v) => v == null ? "—" : `${v >= 0 ? "+" : ""}${Math.round(v * 100) / 100}%`;
  const flowCell = (r) => {
    if (!r.flow_available || r.flow_net == null) return <span className="muted">—</span>;
    const d = r.flow_dir;
    const cls = d === "bull" ? "up" : d === "bear" ? "down" : "muted";
    const lbl = d === "bull" ? "Bull" : d === "bear" ? "Bear" : "Mixed";
    return <span className={cls} title="Net options-flow direction (bullish − bearish premium share)">{lbl} {r.flow_net >= 0 ? "+" : ""}{r.flow_net}</span>;
  };
  const agreeCell = (r) => {
    if (!r.flow_available || !r.flow_agree) return <span className="muted">—</span>;
    if (r.flow_agree === "agrees") return <span className="up" title="Options flow agrees with the recent price trend">✓ agrees</span>;
    if (r.flow_agree === "disagrees") return <span className="down" title="Options flow disagrees with the recent price trend">✗ against</span>;
    return <span className="muted" title="Mixed / neutral flow">~ neutral</span>;
  };
  // Compact signed $ for premium columns (e.g. $1.2M, -$540K). Blank/0 → —
  const prem$ = (v) => (v == null) ? "—" : (v === 0 ? "—" : window.fmt$M(v));
  const numOr = (v) => v == null ? "—" : v;
  const edgeCell = (r) => {
    if (r.edge == null) return <span className="muted">—</span>;
    const cls = r.edge >= 15 ? "up" : r.edge <= -15 ? "down" : "muted";
    const f = flowFreshness(r.flow_ts);
    return (
      <span className="wt-edge">
        <b className={cls}>{r.edge > 0 ? "+" : ""}{r.edge}</b>
        {f && <span className={`wt-flowdot ${f.tone}`} title={`Options flow ${f.label}${r.flow_cached ? " (cached — outside this scan's live-flow budget)" : " (live this scan)"}`} />}
      </span>
    );
  };
  const setupCell = (r) => {
    if (!r.setup) return <span className="muted">—</span>;
    const cls = r.edge_dir === "long" ? "up" : r.edge_dir === "short" ? "down" : "muted";
    return <span className={cls}>{r.edge_er ? "⚠ " : ""}{r.setup}</span>;
  };
  // Price-swing direction (long/short bias) + how far along the move is.
  const swingCell = (r) => {
    if (!r.swing_dir) return <span className="muted">—</span>;
    const long = r.swing_dir === "long";
    const tip = r.swing_pct != null ? `${long ? "Up" : "Down"} move ${r.swing_pct}% over ${r.swing_days}d` : "";
    return <span className={long ? "up" : "down"} title={tip}>{long ? "Long" : "Short"}</span>;
  };
  const timingCell = (r) => {
    if (!r.swing_stage) return <span className="muted">—</span>;
    const cls = r.swing_stage === "early" ? "up" : r.swing_stage === "late" ? "down" : "muted";
    const tip = r.swing_stage === "early" ? "Near the start of the move — best entry"
      : r.swing_stage === "late" ? "Extended — don't chase; wait for a pullback"
      : "Mid-move — enter on a pullback";
    return <span className={cls} title={tip}>{r.swing_stage}</span>;
  };
  // Expected R-multiple — the desk-style ranker. >0 = positive expectancy.
  const evCell = (r) => {
    if (r.tk_ev == null) return <span className="muted" title={r.tk_target ? "Target already reached — no edge left here" : "—"}>—</span>;
    const cls = r.tk_ev >= 0.2 ? "up" : r.tk_ev < 0 ? "down" : "muted";
    return <b className={cls} title={`Expected value per trade. R:R ${r.tk_rr}, win-rate ${r.tk_wr}% → ${r.tk_ev >= 0 ? "+" : ""}${r.tk_ev}R expected`}>{r.tk_ev >= 0 ? "+" : ""}{r.tk_ev}R</b>;
  };
  // Realized-volatility regime → buy-vs-sell-premium read.
  const volCell = (r) => {
    if (r.rvol_rank == null) return <span className="muted">—</span>;
    const hot = r.rvol_rank >= 70, cold = r.rvol_rank <= 30;
    const cls = hot ? "down" : cold ? "up" : "muted";
    const tip = `Realized-vol rank ${r.rvol_rank} (20d vol ${r.rvol}% vs its own year). `
      + (hot ? "Elevated — premium likely rich → favor SELLING premium (credit spreads / CSPs)."
        : cold ? "Calm/cheap — favor BUYING premium (long calls/puts) or directional shares."
        : "Mid — no strong premium edge either way.");
    return <span className={cls} title={tip}>{r.rvol_rank}{hot ? " ↑" : cold ? " ↓" : ""}</span>;
  };
  // Risk-based position size for the current account / risk-per-trade.
  const sizeCell = (r) => {
    if (r.tk_size == null) return <span className="muted">—</span>;
    const dir = r.swing_dir === "long" ? "Buy" : "Short";
    const tip = `${dir} ${r.tk_size} sh · risk $${r.tk_riskUsd?.toLocaleString()} → reward $${r.tk_rewardUsd?.toLocaleString()} · R:R ${r.tk_rr} · target $${r.tk_target} / stop $${r.tk_stop}`;
    return <span title={tip}>{r.tk_size.toLocaleString()}<small className="muted"> sh</small></span>;
  };

  // One <td> for a (column, row) pair — data-driven so columns can be reordered.
  const renderCell = (c, r) => {
    const k = c.k;
    switch (k) {
      case "symbol": {
        const an = analystBy[r.symbol];
        const fresh = an && an.fresh_today;
        // Streak badge when the current run is near the stock's own record.
        const sdir = r.streak_dir, scount = r.streak_count || 0;
        const srec = sdir === "up" ? (r.longest_up || 0) : sdir === "down" ? (r.longest_down || 0) : 0;
        const sNear = (sdir === "up" || sdir === "down") && srec >= 4 && scount >= srec - 1;
        const sBadge = sNear ? (
          <span className={`wl-streak-badge ${sdir === "up" ? "up" : "down"}`}
                title={`${scount}-day ${sdir} streak — near its 2y record of ${srec}${sdir === "down" ? " (possible exhaustion / mean-reversion watch)" : ""}`}>
            {sdir === "up" ? "▲" : "▼"}{scount}
          </span>) : null;
        return <td key={k} className="wl-sym">{isPrime(r) && <span className="wl-prime-star" title="Prime setup — flow + swing agree, move is early">★ </span>}{r.symbol}{fresh && <span className={`wl-analyst-badge wl-an-${an.direction || "neutral"}`} title={`Fresh analyst action today: ${an.action_type || "action"}${an.count > 1 ? ` (${an.count} firms)` : ""} · impact ${Math.round(an.score || 0)}`}>⚡</span>}{sBadge}</td>;
      }
      case "company": return <td key={k} className="wl-co">{r.company || "—"}</td>;
      case "tag": return <td key={k} className="wl-txt">{r.tag
        ? <span className="wl-tag-chip" title={"Your category: " + r.tag}>{r.tag}</span> : "—"}</td>;
      case "weekly": return <td key={k} className="wl-txt" title={r.weekly === true ? "Has weekly options" : r.weekly === false ? "No weekly options" : "Unknown"}>{r.weekly === true ? "Yes" : r.weekly === false ? "No" : "—"}</td>;
      case "edge": return <td key={k} className="scan-num" title={r.edge_tip || ""}>{edgeCell(r)}</td>;
      case "setup": return <td key={k} className="wl-txt" title={r.edge_tip || ""}>{setupCell(r)}</td>;
      case "prem_sell": return <td key={k} className="wl-txt">{r.prem_sell || "—"}</td>;
      case "swing_dir": return <td key={k} className="wl-txt">{swingCell(r)}</td>;
      case "swing_stage": return <td key={k} className="wl-txt">{timingCell(r)}</td>;
      case "tk_ev": return <td key={k} className="scan-num">{evCell(r)}</td>;
      case "tk_size": return <td key={k} className="scan-num">{sizeCell(r)}</td>;
      case "rvol_rank": return <td key={k} className="scan-num">{volCell(r)}</td>;
      case "last": return <td key={k} className="scan-num" title={liveQ[r.symbol] != null ? "Live" : "Last scan"}>{fmtUsd(liveLast(r), 2)}</td>;
      case "market_cap": return <td key={k} className="scan-num">{fmtMktCap(r.market_cap)}</td>;
      case "pe": return <td key={k} className="scan-num">{r.pe != null ? r.pe : "—"}</td>;
      case "forward_pe": return <td key={k} className="scan-num">{r.forward_pe != null ? r.forward_pe : "—"}</td>;
      case "industry": return <td key={k} className="wl-txt">{r.industry || "—"}</td>;
      case "sector": return <td key={k} className="wl-txt">{r.sector || "—"}</td>;
      case "rsi": return <td key={k} className="scan-num">{r.rsi != null ? r.rsi : "—"}</td>;
      case "rel_vol": return <td key={k} className="scan-num">{r.rel_vol != null ? r.rel_vol + "x" : "—"}</td>;
      case "flow_net": return <td key={k} className="scan-num">{flowCell(r)}</td>;
      case "flow_agree": return <td key={k} className="wl-txt">{agreeCell(r)}</td>;
      case "flow_bull": return <td key={k} className="scan-num up">{numOr(r.flow_bull)}</td>;
      case "flow_bear": return <td key={k} className="scan-num down">{numOr(r.flow_bear)}</td>;
      case "call_prem": return <td key={k} className="scan-num up">{prem$(r.call_prem)}</td>;
      case "put_prem": return <td key={k} className="scan-num down">{prem$(r.put_prem)}</td>;
      case "net_prem": return <td key={k} className={`scan-num ${pctCls(r.net_prem)}`}>{prem$(r.net_prem)}</td>;
      case "pc_ratio": return <td key={k} className="scan-num">{r.pc_ratio != null ? r.pc_ratio : "—"}</td>;
      case "ask_call_prem": return <td key={k} className="scan-num">{prem$(r.ask_call_prem)}</td>;
      case "ask_put_prem": return <td key={k} className="scan-num">{prem$(r.ask_put_prem)}</td>;
      case "call_sweeps": return <td key={k} className="scan-num">{numOr(r.call_sweeps)}</td>;
      case "put_sweeps": return <td key={k} className="scan-num">{numOr(r.put_sweeps)}</td>;
      case "flow_alerts": return <td key={k} className="scan-num">{numOr(r.flow_alerts)}</td>;
      case "flow_quality": return <td key={k} className="scan-num">{numOr(r.flow_quality)}</td>;
      case "flow_cc_risk": return <td key={k} className={`scan-num ${r.flow_cc_risk != null && r.flow_cc_risk >= 60 ? "down" : ""}`}>{numOr(r.flow_cc_risk)}</td>;
      case "flow_verdict": return <td key={k} className="wl-txt" title={r.flow_verdict || ""}>{r.flow_verdict || "—"}</td>;
      case "next_earnings": return <td key={k} className="scan-num">{r.next_earnings ? fmtUSDate(r.next_earnings) : "—"}{r.days_to_earnings != null ? <span className="muted"> ({r.days_to_earnings}d)</span> : ""}</td>;
      case "from_open": {
        const v = foVal(r); return <td key={k} className={`scan-num ${pctCls(v)}`} title={r.open != null ? `Open ${fmtUsd(r.open, 2)}` : "Open n/a"}>{pct(v)}</td>;
      }
      case "change": {
        const v = chgVal(r); return <td key={k} className={`scan-num ${pctCls(v)}`}>{pct(v)}</td>;
      }
      case "wtd": case "mtd": case "qtd": case "ytd":
      case "from_ma20": case "from_ma50": case "from_ma200": {
        const v = reb(r, r[k]); return <td key={k} className={`scan-num ${pctCls(v)}`}>{pct(v)}</td>;
      }
      default: return <td key={k} className="scan-num">—</td>;
    }
  };

  return (
    <div className="card ab-card">
      <div className="card-head">
        <div>
          <div className="kicker">Watchlist</div>
          <div className="card-title">Tracked stocks — full metrics</div>
        </div>
        <div className="ab-controls">
          <button className="scan-run-btn" onClick={startScan} disabled={scanning}>{scanning ? "Scanning…" : "Scan now"}</button>
        </div>
      </div>
      <div className="ab-status">
        {status.last_scan
          ? <span>Last scan {new Date(status.last_scan).toLocaleString()} · {rows.length} stocks</span>
          : <span className="muted">No scan yet — Scan now pulls valuation, momentum, volume, earnings &amp; moving-average metrics for your tracked stocks (a few minutes for large lists).</span>}
        {notScanned > 0 && status.last_scan && !scanning && (
          <span className="wl-newhint" title="These are in your watchlist but not in the last scan — added since the scan, or the data source returned no price. Re-scan to include them.">
            {" "}· {notScanned} not in last scan — <button type="button" className="wl-rescan-link" onClick={startScan}>Scan now</button> to include
          </span>
        )}
        <span className="muted"> · <b>Edge</b> = signed flow conviction (+long / −short), size-normalized; sort it to rank morning buys vs sells · hover a row for the driver breakdown · Auto-refreshes 9 AM &amp; 6 PM ET · cached server-side</span>
        {status.error && <span className="ab-err"> · {status.error}</span>}
        {err && <span className="ab-err"> · {err}</span>}
      </div>
      {(() => {
        // Market-wide flow read (one UW call, whole market — not per row).
        const tide = market && market.tide;
        if (!tide) return null;
        const row = Array.isArray(tide) ? tide[tide.length - 1] : tide;
        if (!row) return null;
        const cp = row.net_call_premium ?? row.call_premium ?? null;
        const pp = row.net_put_premium ?? row.put_premium ?? null;
        if (cp == null && pp == null) return null;
        const net = (cp || 0) - (pp || 0);
        const tot = Math.abs(cp || 0) + Math.abs(pp || 0);
        const tilt = tot ? net / tot : 0;   // -1..+1 regime strength
        const regime = net > 0 ? "Bullish" : net < 0 ? "Bearish" : "Neutral";
        const cls = net > 0 ? "up" : net < 0 ? "down" : "muted";
        // Regime gate: don't fight the tape. Strong one-sided tape → favor that side.
        const gate = tilt > 0.15 ? { txt: "Risk-on — favor longs, go easy on shorts", cls: "up" }
          : tilt < -0.15 ? { txt: "Risk-off — favor shorts/cash, go easy on longs", cls: "down" }
          : { txt: "Mixed tape — be selective, trade only the cleanest setups", cls: "muted" };
        return (
          <div className="wl-market" title="Whole-market options flow (net call − put premium today). One UW call, same for every row.">
            <span className="wl-market-tag">Market flow</span>
            <b className={cls}>{regime}</b>
            <span className="muted">net call − put</span>
            <b className={cls}>{window.fmt$M(net)}</b>
            <span className="muted">· calls {window.fmt$M(cp)} / puts {window.fmt$M(pp)}</span>
            <span className={`wl-regime ${gate.cls}`}>· {gate.txt}</span>
          </div>
        );
      })()}
      {scanning && (
        <div className="ab-progress">
          <div className="ab-progress-bar" style={{ width: `${status.total ? (status.scanned / status.total * 100) : 0}%` }}></div>
          <span className="ab-progress-txt">{status.scanned || 0} / {status.total || 0}</span>
        </div>
      )}
      {rows.length > 0 && (
        <div className="ab-filters">
          <div className="wl-viewtabs" role="tablist" aria-label="Watchlist view">
            {[["stocks", "Stocks"], ["sectors", "Sectors"], ["industries", "Industries"]].map(([v, lbl]) => (
              <button key={v} type="button" role="tab" aria-selected={view === v}
                      className={view === v ? "active" : ""} onClick={() => setView(v)}
                      title={v === "stocks" ? "Per-stock metrics" : `Premiums aggregated by ${v === "sectors" ? "sector" : "industry"} — see where money flows in and out`}>
                {lbl}
              </button>
            ))}
          </div>
          {view === "stocks" && <input className="sb-select ab-search" placeholder="Symbol / company…" value={q} onChange={e => setQ(e.target.value)} />}
          <select className="sb-select" value={fSector} onChange={e => setFSector(e.target.value)}>
            <option value="all">All sectors</option>{sectors.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="sb-select" value={fIndustry} onChange={e => {
            const ind = e.target.value;
            setFIndustry(ind);
            // Auto-select the parent sector so the Sector filter reflects the
            // industry you picked (an industry lives in exactly one sector).
            if (ind !== "all") {
              const row = rows.find(r => r.industry === ind && r.sector);
              if (row) setFSector(row.sector);
            }
          }}>
            <option value="all">All industries</option>{industryOpts.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          {tagOpts.length > 0 && (
            <select className="sb-select" value={fTag} onChange={e => setFTag(e.target.value)} title="Filter by your Tag (category from CSV import)">
              <option value="all">All tags</option>{tagOpts.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          )}
          <select className="sb-select" value={fMcap} onChange={e => setFMcap(e.target.value)} title="Filter by market cap (Finviz-style buckets)">
            {MCAP_BUCKETS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
          <button type="button" className={`wl-prime-btn${primeOnly ? " on" : ""}`} onClick={() => setPrimeOnly(v => !v)}
                  title="Prime setups: options flow and price-swing agree on direction AND the move is just starting — your highest-conviction, beginning-of-move trades.">
            ★ Prime{primeCount ? ` (${primeCount})` : ""}
          </button>
          <label className="wl-acct-wrap" title="Account size — used to size each trade by risk">$<input className="wl-acct" type="number" inputMode="numeric" enterKeyHint="done" min="0" step="1000" value={acct} onChange={e => setAcct(Number(e.target.value) || 0)} /></label>
          <label className="wl-acct-wrap" title="Risk per trade (% of account). Position size = this ÷ stop distance.">risk<input className="wl-risk" type="number" inputMode="decimal" enterKeyHint="done" min="0" step="0.1" value={riskPct} onChange={e => setRiskPct(Number(e.target.value) || 0)} />%</label>
          <span className="muted" style={{ fontSize: 12 }}>{view === "stocks" ? `${filtered.length} shown` : `${groups.length} ${view}`}</span>
        </div>
      )}
      {primeCrowd && (
        <div className="wl-crowd" title="Correlated names move together — sizing 4 trades in one sector is really one position's worth of risk.">
          ⚠ Crowding: {primeCrowd.n} of {primeCrowd.total} Prime setups are <b>{primeCrowd.sector}</b> — that's really one bet. Spread risk across sectors or size each smaller.
        </div>
      )}
      {view !== "stocks" ? (
        groups.length > 0 ? (
          <div className="scan-table-wrap wl-scroll" style={{ marginTop: 10 }}>
            <table className="scan-table wl-table">
              <thead><tr>
                {GCOLS.map(c => (
                  <th key={c.k} className={`${c.num ? "scan-th-num" : ""} wl-th${gsort.key === c.k ? " active" : ""}`}
                      onClick={() => setGsortKey(c.k)} title="Click to sort">
                    {c.label}{gsort.key === c.k ? (gsort.dir === "asc" ? " ▲" : " ▼") : ""}
                  </th>
                ))}
              </tr></thead>
              <tbody>
                {groups.map(g => (
                  <tr key={g.name} className="scan-row wl-row" onClick={() => drillGroup(g.name)} title={`Show ${g.name} stocks`}>
                    <td className="wl-co">{g.name}</td>
                    <td className="scan-num">{g.stocks}{g.withFlow < g.stocks ? <span className="muted"> ({g.withFlow})</span> : ""}</td>
                    <td className="scan-num">{fmtMktCap(g.mcap)}</td>
                    <td className="scan-num up">{g.nBull || "—"}</td>
                    <td className="scan-num down">{g.nBear || "—"}</td>
                    <td className="scan-num up">{prem$(g.bull)}</td>
                    <td className="scan-num down">{prem$(g.bear)}</td>
                    <td className={`scan-num ${pctCls(g.net)}`}><b>{prem$(g.net)}</b></td>
                    <td className="scan-num">{g.pc != null ? g.pc : "—"}</td>
                    <td className="scan-num">{prem$(g.askC)}</td>
                    <td className="scan-num">{prem$(g.askP)}</td>
                    <td className="scan-num">{g.cSwp || "—"}</td>
                    <td className="scan-num">{g.pSwp || "—"}</td>
                    <td className="scan-num">{g.alerts || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (!scanning && status.last_scan && <div className="ab-empty">No flow data to aggregate yet — run a scan.</div>)
      ) : filtered.length > 0 ? (
        <div className="scan-table-wrap wl-scroll" style={{ marginTop: 10 }} ref={wlScrollRef} onScroll={onWlScroll}>
          <table className="scan-table wl-table">
            <thead><tr>
              {orderedCols.map(c => (
                <th key={c.k} draggable
                    onDragStart={(e) => { dragColKey.current = c.k; e.dataTransfer.effectAllowed = "move"; }}
                    onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}
                    onDrop={(e) => { e.preventDefault(); onColDrop(c.k); }}
                    className={`${c.num ? "scan-th-num" : ""} wl-th${sort.key === c.k ? " active" : ""}`}
                    onClick={() => setSortKey(c.k)}
                    title={`${COL_TIPS[c.k] || c.label} · click to sort · drag to reorder`}>
                  {c.label}{sort.key === c.k ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
                </th>
              ))}
            </tr></thead>
            <tbody>
              {shown.map(r => (
                <tr key={r.symbol} className="scan-row wl-row" onClick={() => onSwitchTicker && onSwitchTicker(r.symbol)}
                    onContextMenu={(e) => { e.preventDefault(); setCtx({ x: e.clientX, y: e.clientY, symbol: r.symbol }); }}
                    title={`Open ${r.symbol} · right-click to remove`}>
                  {orderedCols.map(c => renderCell(c, r))}
                </tr>
              ))}
            </tbody>
          </table>
          {visN < filtered.length && (
            <div className="wl-more" onClick={() => setVisN(n => Math.min(n + WL_CHUNK, filtered.length))}>
              Showing {visN} of {filtered.length} — scroll or click for more
            </div>
          )}
        </div>
      ) : (!scanning && status.last_scan && <div className="ab-empty">No stocks match these filters.</div>)}
      {ctx && (
        <div className="wl-ctx" onClick={e => e.stopPropagation()}
             style={{ left: Math.min(ctx.x, (typeof window !== "undefined" ? window.innerWidth : 9999) - 240), top: ctx.y }}>
          <div className="wl-ctx-head">{ctx.symbol}</div>
          <button type="button" onClick={() => { if (onSwitchTicker) onSwitchTicker(ctx.symbol); setCtx(null); }}>Open {ctx.symbol}</button>
          <button type="button" className="wl-ctx-danger" onClick={() => doRemove(ctx.symbol)}>Remove from watchlist</button>
        </div>
      )}
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

// ── Weekly range location scan (v3.55) ─────────────────────────────────────
// The Weekly Option Selling Setup panel's range math, run across the whole
// watchlist: which names sit near their N-week worst low (sell puts) or best
// high (sell calls) RIGHT NOW — instead of clicking one ticker at a time.
// Location measures only, from price history; premiums/greeks stay on the
// per-ticker panel.
function RangeEdgeScanCard({ apiFetch, onSwitchTicker, onOpenAnalyze }) {
  const [board, setBoard] = useState(null);
  const [err, setErr] = useState(null);
  const [weeks, setWeeks] = useState(16);
  const [fSide, setFSide] = useState("all");
  const [minEdge, setMinEdge] = useState(60);
  const [q, setQ] = useState("");
  const [showAll, setShowAll] = useState(false);
  const pollRef = useRef(null);
  const SHOW_CAP = 150;   // rows painted before "Show all" (1200+ names otherwise)

  const load = async () => {
    try { const r = await apiFetch("/api/range_scan"); const d = await r.json(); setBoard(d); return d; }
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
    load().then(d => { if (d && d.status && d.status.scanning) watchScan(); });
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);
  const startScan = async () => {
    setErr(null);
    try { await apiFetch(`/api/range_scan/scan?force=1&weeks=${weeks}`); } catch (e) { setErr(String(e)); return; }
    await load();
    watchScan();
  };

  const status = (board && board.status) || {};
  const rows = (board && board.rows) || [];
  const summary = (board && board.summary) || {};
  const scanning = !!status.scanning;
  const open = (sym) => { if (onOpenAnalyze) onOpenAnalyze(sym); else if (onSwitchTicker) onSwitchTicker(sym); };

  const filtered = useMemo(() => rows.filter(r => {
    if (fSide === "lows" && r.side !== "put") return false;
    if (fSide === "highs" && r.side !== "call") return false;
    if ((r.edge || 0) < minEdge) return false;
    if (q && !String(r.ticker || "").toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  }), [rows, fSide, minEdge, q]);

  const Chips = ({ rows, tone }) => (
    <div className="ab-chips">
      {(rows || []).length === 0 && <span className="muted" style={{ fontSize: 12 }}>—</span>}
      {(rows || []).map((r, i) => (
        <button key={r.ticker + i} className={`ab-chip ab-${tone}`} onClick={() => open(r.ticker)}
                title={`${r.ticker} — this week ${r.curr_return >= 0 ? "+" : ""}${r.curr_return}% · position ${r.pos}% of the ${r.weeks_used}w range · worst low ${r.worst_low}% / best high +${r.best_high}%`}>
          {r.ticker} <b>{Math.round(r.side === "put" ? r.bottom_prox : r.top_prox)}</b>
        </button>
      ))}
    </div>
  );
  const SummaryBox = ({ title, tone, children }) => (
    <div className={`ab-sumbox ${tone || ""}`}><div className="ab-sumbox-title">{title}</div>{children}</div>
  );

  return (
    <div className="card ab-card" style={{ marginBottom: "var(--row-gap)" }}>
      <div className="card-head">
        <div>
          <div className="kicker">Premium selling · your watchlist</div>
          <div className="card-title">Weekly range location scan</div>
        </div>
        <div className="ab-controls">
          <select className="sb-select" value={weeks} onChange={e => setWeeks(Number(e.target.value))}
                  title="Lookback: how many completed weeks define each name's worst low / best high.">
            {[8, 12, 16, 26, 52].map(w => <option key={w} value={w}>{w} weeks</option>)}
          </select>
          <button className="scan-run-btn" onClick={startScan} disabled={scanning}>
            {scanning ? "Scanning…" : "Scan now"}
          </button>
        </div>
      </div>
      <div className="ab-status">
        {status.last_scan
          ? <span>Last scan {new Date(status.last_scan).toLocaleString()} · {status.weeks}w lookback · {status.baseline} baseline · {rows.length} names</span>
          : <span className="muted">No scan yet — positions every watchlist name inside its own {weeks}-week range. Near the worst low → sell puts; near the best high → sell calls. Same math as the selling-setup panel.</span>}
        {status.error && <span className="ab-err"> · {status.error}</span>}
        {err && <span className="ab-err"> · {err}</span>}
      </div>
      <div className="ab-status muted" style={{ marginTop: -6 }}>
        Bottom/top proximity is a LOCATION measure from price history — not the probability an option expires worthless. Premiums and greeks live on the Analyze panel per name.
      </div>
      {scanning && (
        <div className="ab-progress">
          <div className="ab-progress-bar" style={{ width: `${status.total ? (status.scanned / status.total * 100) : 0}%` }}></div>
          <span className="ab-progress-txt">{status.scanned || 0} / {status.total || 0}</span>
        </div>
      )}

      {rows.length > 0 && (
        <div className="ab-summary">
          <SummaryBox title="Near range LOWS — sell puts" tone="up"><Chips rows={summary.near_lows} tone="bull" /></SummaryBox>
          <SummaryBox title="Near range HIGHS — sell calls" tone="down"><Chips rows={summary.near_highs} tone="bear" /></SummaryBox>
          <SummaryBox title="Late-week lows — your edge" tone="warn"><Chips rows={summary.late_week_lows} tone="bull" /></SummaryBox>
        </div>
      )}

      {rows.length > 0 && (
        <div className="ab-filters">
          <input className="sb-select ab-search" placeholder="Ticker…" value={q} onChange={e => setQ(e.target.value)} />
          <select className="sb-select" value={fSide} onChange={e => setFSide(e.target.value)}>
            <option value="all">Both sides</option>
            <option value="lows">Near lows (puts)</option>
            <option value="highs">Near highs (calls)</option>
          </select>
          <select className="sb-select" value={minEdge} onChange={e => setMinEdge(Number(e.target.value))}
                  title="Only show names at least this close to one of their range extremes (100 = sitting on the extreme).">
            {[0, 50, 60, 70, 80, 90].map(v => <option key={v} value={v}>edge ≥ {v}%</option>)}
          </select>
          <span className="muted" style={{ fontSize: 12 }}>{filtered.length} of {rows.length}</span>
        </div>
      )}

      {filtered.length > 0 && (
        <div className="rgs-wrap">
          <table className="rgs-table">
            <thead><tr>
              <th>Ticker</th><th>Last</th><th>This wk</th>
              <th title="This week's position inside the lookback range: worst weekly low on the left, best weekly high on the right.">Range location</th>
              <th title="How close to the LOW side of the range (100 = at the worst low). Location only — not P(OTM).">Bot&nbsp;prox</th>
              <th title="Worst weekly low of the lookback, and the price it maps to off this week's baseline.">Worst low</th>
              <th title="Best weekly high of the lookback, and the price it maps to.">Best high</th>
              <th title="% of lookback weeks whose weekly LOW had already printed by today's weekday. High + near the low = little room usually left below.">LOW in by</th>
              <th>Side</th>
            </tr></thead>
            <tbody>
              {(showAll ? filtered : filtered.slice(0, SHOW_CAP)).map(r => (
                <tr key={r.ticker} className="rgs-row" onClick={() => open(r.ticker)}
                    title={`Open ${r.ticker} on the Analyze tab — the selling-setup panel shows live premiums, greeks and breach rates.`}>
                  <td className="rgs-tk" data-label="">{r.ticker}{r.outside && <span className="rgs-out">{r.outside === "below" ? "▼ out" : "▲ out"}</span>}</td>
                  <td className="num" data-label="Last">{fmt$(r.last, r.last >= 1000 ? 0 : 2)}</td>
                  <td className={`num ${r.curr_return >= 0 ? "cu" : "cd"}`} data-label="This wk">{r.curr_return >= 0 ? "+" : ""}{r.curr_return.toFixed(1)}%</td>
                  <td className="rgs-barcell" data-label=""><div className="rgs-bar"><i style={{ left: `${r.pos}%` }}></i></div></td>
                  <td className="num rgs-big" data-label="Bot prox">{r.bottom_prox.toFixed(0)}%</td>
                  <td className="num cd" data-label="Worst low">{r.worst_low.toFixed(1)}% <span className="rgs-px">{fmt$(r.p_low, r.p_low >= 1000 ? 0 : 2)}</span></td>
                  <td className="num cu" data-label="Best high">+{r.best_high.toFixed(1)}% <span className="rgs-px">{fmt$(r.p_high, r.p_high >= 1000 ? 0 : 2)}</span></td>
                  <td className="num" data-label="LOW in by">{Math.round(r.lows_in_by)}%</td>
                  <td className="rgs-sidecell" data-label="Side"><span className={`rgs-side ${r.side}`}>{r.side === "put" ? "SELL PUT" : "SELL CALL"}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!showAll && filtered.length > SHOW_CAP && (
            <button type="button" className="rgs-more" onClick={() => setShowAll(true)}>
              Show all {filtered.length} (top {SHOW_CAP} shown)
            </button>
          )}
        </div>
      )}
      {rows.length > 0 && filtered.length === 0 && (
        <div className="research-empty">Nothing at edge ≥ {minEdge}% right now — loosen the filter or rescan.</div>
      )}
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
        <CardNote kind="error" onRetry={fetchAlerts}>Couldn't load alerts — {error}</CardNote>
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
                  {fmtUSDate(a.date)}
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

function TabBar({ active, onChange, ticker, earnDate, earnDays, tabs, onReorder }) {
  const hasEarn = earnDate != null;
  const soon = earnDays != null && earnDays >= 0 && earnDays <= 7;
  const list = (tabs && tabs.length) ? tabs : TABS;
  const [dragId, setDragId] = useState(null);
  const [overId, setOverId] = useState(null);
  // Mobile (v3.57): the bar is a horizontal scroll strip — keep the active
  // tab visible by centering it whenever it changes, otherwise the current
  // section can sit off-screen and the bar reads as random buttons.
  const barRef = useRef(null);
  useEffect(() => {
    try {
      if (!window.matchMedia("(max-width: 900px)").matches) return;
      const btn = barRef.current && barRef.current.querySelector('[aria-selected="true"]');
      if (btn && btn.scrollIntoView) btn.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
    } catch (e) { /* non-fatal */ }
  }, [active]);
  const drop = (targetId) => {
    if (!onReorder || !dragId || dragId === targetId) { setDragId(null); setOverId(null); return; }
    const ids = list.map(t => t.id);
    const from = ids.indexOf(dragId), to = ids.indexOf(targetId);
    if (from < 0 || to < 0) { setDragId(null); setOverId(null); return; }
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    onReorder(ids);
    setDragId(null); setOverId(null);
  };
  // Row split (v3.38): the app's own sections on line 1; the embedded
  // partner sites (Finviz / TradingView / Unusual Whales) on line 2 so the
  // bar reads as "my app" vs "linked sites" instead of one crowded wrap.
  const EXT = { finviz: 1, tview: 1, whales: 1 };
  const appTabs = list.filter(t => !EXT[t.id]);
  const extTabs = list.filter(t => EXT[t.id]);
  const renderBtn = (t) => (
    <button key={t.id} type="button" role="tab"
            aria-selected={active === t.id}
            className={`tab-btn ${active === t.id ? "active" : ""}${dragId === t.id ? " dragging" : ""}${overId === t.id && dragId && overId !== dragId ? " drop-target" : ""}`}
            onClick={() => onChange(t.id)}
            draggable={!!onReorder}
            onDragStart={(e) => { setDragId(t.id); try { e.dataTransfer.effectAllowed = "move"; } catch (_) {} }}
            onDragOver={(e) => { if (dragId) { e.preventDefault(); setOverId(t.id); } }}
            onDrop={(e) => { e.preventDefault(); drop(t.id); }}
            onDragEnd={() => { setDragId(null); setOverId(null); }}
            title={`Show the ${t.label} section. Drag to reorder.`}>
      {t.label}
    </button>
  );
  return (
    <div ref={barRef} className="tab-bar tab-bar-2row" role="tablist" aria-label="Dashboard sections"
         title="Switch sections. Drag a tab to reorder; the order is saved to all your devices. Cards stay live in the background, so switching is instant and nothing reloads.">
      <div className="tab-row">
        {appTabs.map(renderBtn)}
      </div>
      {(extTabs.length > 0 || hasEarn) && (
        <div className="tab-row tab-row-ext">
          {extTabs.length > 0 && (
            <React.Fragment>
              <span className="tab-row-lbl" title="Embedded partner sites — each renders inside the dashboard and follows the globally selected ticker both ways.">Sites -</span>
              {extTabs.map(renderBtn)}
            </React.Fragment>
          )}
          {/* Earnings chip rides the Sites row (right-aligned) instead of
              claiming its own line — reclaims the empty space above it. */}
          {hasEarn && (
            <div className={`tab-earn ${soon ? "soon" : ""}`}
                 title={`Next earnings report for ${ticker}${earnDays != null ? ` — in ${earnDays} day${earnDays === 1 ? "" : "s"}` : ""}.`}>
              <span className="tab-earn-lbl">{ticker} earnings</span>
              <b>{fmtSwingDate(earnDate)}</b>
              {earnDays != null && <span className="tab-earn-days">{earnDays === 0 ? "today" : earnDays > 0 ? `in ${earnDays}d` : `${-earnDays}d ago`}</span>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TabPanel({ tab, active, children }) {
  // Lazy-mount: render children only after the tab is first activated, then
  // keep them mounted (hidden) so they stay live. Avoids paying the mount /
  // fetch cost for sections you never open — faster initial load on mobile.
  const seen = useRef(active === tab);
  if (active === tab) seen.current = true;
  // tp-in re-applies on every activation → the enter animation (a 180ms fade
  // + rise, reduced-motion safe) replays on each tab switch.
  return (
    <div className={`tab-panel${active === tab ? " tp-in" : ""}`} role="tabpanel" data-tab={tab}
         style={active === tab ? undefined : { display: "none" }}>
      {seen.current ? children : null}
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
      {error && <CardNote kind="error" onRetry={fetchCrush}>Couldn't load earnings vol crush — {error}</CardNote>}
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
              <span>{fmtUSDate(r.next_earnings)}</span>
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

// ── CSV helpers for the Manage Stocks importer ─────────────────────
// Required columns (case-insensitive, any order): Symbol, Tag, Industry,
// Sector, Weekly. Symbol is mandatory per row; Tag/Industry/Sector may be
// blank; Weekly must be Yes/No (anything else is flagged before import).
const WLM_CSV_COLUMNS = ["Symbol", "Tag", "Industry", "Sector", "Weekly"];

// Minimal RFC-4180-ish CSV parser: handles quoted fields, escaped quotes
// (""), and commas/newlines inside quotes. Returns an array of string rows.
function parseCsv(text) {
  const rows = [];
  let row = [], field = "", inQuotes = false;
  const s = (text || "").replace(/^﻿/, ""); // strip BOM
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (inQuotes) {
      if (c === '"') {
        if (s[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += c;
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      row.push(field); field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && s[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.length > 1 || row[0] !== "") rows.push(row);
      row = [];
    } else field += c;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows;
}

// Normalize a Weekly cell to true / false / null(unknown) + a flag for
// "present but not Yes/No" so we can warn before importing.
function normalizeWeekly(raw) {
  const v = (raw || "").trim().toLowerCase();
  if (v === "" ) return { weekly: null, bad: false };
  if (["yes", "y", "true", "1"].includes(v)) return { weekly: true, bad: false };
  if (["no", "n", "false", "0"].includes(v)) return { weekly: false, bad: false };
  return { weekly: null, bad: true };
}

function csvEscape(v) {
  const s = String(v == null ? "" : v);
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

// Build the export CSV (same column format) from the current watchlist.
function watchlistToCsv(symbols) {
  const lines = [WLM_CSV_COLUMNS.join(",")];
  for (const s of symbols) {
    lines.push([
      csvEscape(s.symbol),
      csvEscape(s.tag || ""),
      csvEscape(s.industry || ""),
      csvEscape(s.sector || ""),
      csvEscape(s.weekly === true ? "Yes" : s.weekly === false ? "No" : ""),
    ].join(","));
  }
  return lines.join("\r\n");
}

function CsvImportPanel({ data, onImportCsv, onClose }) {
  const [stage, setStage] = useState("pick"); // pick | preview
  const [fileName, setFileName] = useState("");
  const [parsed, setParsed] = useState(null); // {rows, missing, badWeekly, dupes, mode}
  const [mode, setMode] = useState("update"); // update | replace
  const [error, setError] = useState("");
  const fileRef = React.useRef(null);

  const handleFile = (file) => {
    if (!file) return;
    setError(""); setFileName(file.name);
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const grid = parseCsv(String(e.target.result || ""));
        if (!grid.length) { setError("The file appears to be empty."); return; }
        const header = grid[0].map(h => h.trim().toLowerCase());
        const idx = {};
        for (const col of WLM_CSV_COLUMNS) idx[col] = header.indexOf(col.toLowerCase());
        const missing = WLM_CSV_COLUMNS.filter(c => idx[c] === -1);
        if (missing.length) {
          setError("Missing required column" + (missing.length > 1 ? "s" : "") +
                   ": " + missing.join(", ") + ". Found: " +
                   grid[0].map(h => h.trim()).filter(Boolean).join(", "));
          return;
        }
        const seen = new Set();
        const rows = [], dupes = [];
        let badWeekly = 0, skippedNoSymbol = 0;
        for (let r = 1; r < grid.length; r++) {
          const cells = grid[r];
          const symbol = (cells[idx.Symbol] || "").trim().toUpperCase();
          if (!symbol) { skippedNoSymbol++; continue; }
          const wk = normalizeWeekly(cells[idx.Weekly]);
          if (wk.bad) badWeekly++;
          if (seen.has(symbol)) { dupes.push(symbol); continue; }
          seen.add(symbol);
          rows.push({
            symbol,
            tag: (cells[idx.Tag] || "").trim(),
            industry: (cells[idx.Industry] || "").trim(),
            sector: (cells[idx.Sector] || "").trim(),
            weekly: wk.weekly,
            weeklyRaw: (cells[idx.Weekly] || "").trim(),
            weeklyBad: wk.bad,
          });
        }
        if (!rows.length) { setError("No valid rows with a Symbol were found."); return; }
        const existing = new Set(data.symbols.map(s => s.symbol));
        const fileSet = new Set(rows.map(r => r.symbol));
        // Exact symbol lists so we can tell the user precisely what changes.
        const addedSyms = rows.filter(r => !existing.has(r.symbol)).map(r => r.symbol);
        const updatedSyms = rows.filter(r => existing.has(r.symbol)).map(r => r.symbol);
        const removedSyms = data.symbols.map(s => s.symbol).filter(s => !fileSet.has(s));
        setParsed({ rows, dupes, badWeekly, skippedNoSymbol,
                    newCount: addedSyms.length, updateCount: updatedSyms.length,
                    addedSyms, updatedSyms, removedSyms });
        setStage("preview");
      } catch (err) {
        setError("Could not read the CSV: " + (err && err.message || err));
      }
    };
    reader.onerror = () => setError("Could not read the file.");
    reader.readAsText(file);
  };

  const doImport = () => {
    const n = onImportCsv(parsed.rows, mode);
    onClose(n);
  };
  // Compact symbol list: show all up to `max`, then "+N more".
  const symList = (arr, max = 40) => (
    arr.length <= max ? arr.join(", ")
      : arr.slice(0, max).join(", ") + ` +${arr.length - max} more`
  );

  return (
    <div className="wlm-csv-panel">
      {stage === "pick" && (
        <div className="wlm-csv-pick">
          <div className="wlm-csv-help">
            Import a CSV with columns <b>Symbol, Tag, Industry, Sector, Weekly</b>.
            Symbols are cleaned (trimmed + uppercased) and de-duplicated.
            Weekly must be <b>Yes</b> or <b>No</b>. Industry &amp; Sector from the
            file become the source of truth across the app.
          </div>
          <input ref={fileRef} type="file" accept=".csv,text/csv"
                 className="wlm-csv-file"
                 onChange={e => handleFile(e.target.files && e.target.files[0])} />
          <div className="wlm-csv-pick-actions">
            <button className="wlm-csv-btn"
                    title="Choose a .csv file from your device"
                    onClick={() => fileRef.current && fileRef.current.click()}>
              Choose CSV file.
            </button>
            <button className="wlm-csv-cancel" onClick={() => onClose(0)}>Cancel</button>
          </div>
          {fileName && <div className="wlm-csv-fname">{fileName}</div>}
          {error && <div className="wlm-csv-error">{error}</div>}
        </div>
      )}
      {stage === "preview" && parsed && (
        <div className="wlm-csv-preview">
          <div className="wlm-csv-summary">
            <b>{parsed.rows.length}</b> valid symbol{parsed.rows.length === 1 ? "" : "s"} in
            <span className="wlm-csv-fname"> {fileName}</span>
            {" — "}{parsed.newCount} new, {parsed.updateCount} already on your list.
          </div>
          {/* Exactly what this import changes — by symbol. */}
          <div className="wlm-csv-changes">
            {parsed.addedSyms.length > 0 && (
              <div className="wlm-csv-change add">
                <b>+ {parsed.addedSyms.length} added:</b> {symList(parsed.addedSyms)}
              </div>
            )}
            {parsed.updatedSyms.length > 0 && (
              <div className="wlm-csv-change upd">
                <b>↻ {parsed.updatedSyms.length} updated:</b> {symList(parsed.updatedSyms)}
              </div>
            )}
            {mode === "replace" && parsed.removedSyms.length > 0 && (
              <div className="wlm-csv-change rem">
                <b>− {parsed.removedSyms.length} removed:</b> {symList(parsed.removedSyms)}
              </div>
            )}
            {mode === "update" && parsed.removedSyms.length > 0 && (
              <div className="wlm-csv-change keep">
                <b>{parsed.removedSyms.length} kept</b> (not in file, left untouched):{" "}
                {symList(parsed.removedSyms)}
              </div>
            )}
          </div>
          {(parsed.badWeekly > 0 || parsed.dupes.length > 0 || parsed.skippedNoSymbol > 0) && (
            <div className="wlm-csv-warn">
              {parsed.badWeekly > 0 && (
                <div>⚠ {parsed.badWeekly} row{parsed.badWeekly === 1 ? "" : "s"} have a Weekly
                  value that isn't Yes/No — those will be imported as blank (unknown).</div>
              )}
              {parsed.dupes.length > 0 && (
                <div>⚠ {parsed.dupes.length} duplicate symbol{parsed.dupes.length === 1 ? "" : "s"} in
                  the file were collapsed: {Array.from(new Set(parsed.dupes)).slice(0, 10).join(", ")}
                  {parsed.dupes.length > 10 ? "…" : ""}.</div>
              )}
              {parsed.skippedNoSymbol > 0 && (
                <div>⚠ {parsed.skippedNoSymbol} row{parsed.skippedNoSymbol === 1 ? "" : "s"} had no
                  Symbol and were skipped.</div>
              )}
            </div>
          )}
          <div className="wlm-csv-table-wrap">
            <table className="wlm-csv-table">
              <thead>
                <tr>
                  <th title="Ticker symbol (cleaned: trimmed + uppercased)">Symbol</th>
                  <th title="Your custom category for grouping this stock">Tag</th>
                  <th title="Industry (becomes source of truth)">Industry</th>
                  <th title="Sector (becomes source of truth)">Sector</th>
                  <th title="Whether weekly options exist (Yes/No)">Weekly</th>
                </tr>
              </thead>
              <tbody>
                {parsed.rows.slice(0, 200).map((r, i) => (
                  <tr key={r.symbol + i}>
                    <td className="wlm-csv-sym">{r.symbol}</td>
                    <td>{r.tag || <span className="wlm-csv-blank">—</span>}</td>
                    <td>{r.industry || <span className="wlm-csv-blank">—</span>}</td>
                    <td>{r.sector || <span className="wlm-csv-blank">—</span>}</td>
                    <td className={r.weeklyBad ? "wlm-csv-badwk" : ""}>
                      {r.weekly === true ? "Yes" : r.weekly === false ? "No"
                        : r.weeklyBad ? (r.weeklyRaw + " ⚠") : <span className="wlm-csv-blank">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {parsed.rows.length > 200 && (
              <div className="wlm-csv-more">…and {parsed.rows.length - 200} more (all will be imported).</div>
            )}
          </div>
          <div className="wlm-csv-mode">
            <label title="Refresh matching symbols and add new ones; keep symbols not in the file">
              <input type="radio" name="wlm-csv-mode" checked={mode === "update"}
                     onChange={() => setMode("update")} />
              Update &amp; add <span className="wlm-csv-mode-note">(keep symbols not in the file)</span>
            </label>
            <label title="Make the watchlist exactly the imported list; symbols not in the file are removed">
              <input type="radio" name="wlm-csv-mode" checked={mode === "replace"}
                     onChange={() => setMode("replace")} />
              Replace all <span className="wlm-csv-mode-note">(remove symbols not in the file)</span>
            </label>
          </div>
          {mode === "replace" && parsed.removedSyms.length > 0 && (
            <div className="wlm-csv-warn">
              ⚠ Replace will remove {parsed.removedSyms.length} symbol
              {parsed.removedSyms.length === 1 ? "" : "s"} currently on your watchlist
              that aren't in this file: <b>{symList(parsed.removedSyms)}</b>
            </div>
          )}
          <div className="wlm-csv-actions">
            <button className="wlm-csv-btn wlm-csv-confirm" onClick={doImport}
                    title={mode === "replace" ? "Replace your watchlist with this file" : "Merge this file into your watchlist"}>
              {mode === "replace" ? "Replace watchlist" : "Import"} ({parsed.rows.length})
            </button>
            <button className="wlm-csv-cancel" onClick={() => { setStage("pick"); setParsed(null); }}>
              Back
            </button>
            <button className="wlm-csv-cancel" onClick={() => onClose(0)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

function WatchlistManager({ data, onAdd, onRemove, onToggleStar, onUpdate, onBulkAdd, onImportCsv, onSwitchTicker }) {
  const [search, setSearch] = useState("");
  const [tagFilter, setTagFilter] = useState(null);
  const [bulkText, setBulkText] = useState("");
  const [bulkOpen, setBulkOpen] = useState(false);
  const [editing, setEditing] = useState(null); // symbol being edited
  const [sortBy, setSortBy] = useState("starred"); // starred | symbol | added
  const [csvOpen, setCsvOpen] = useState(false);
  const [importMsg, setImportMsg] = useState("");
  const exportCsv = () => {
    const csv = watchlistToCsv(
      [...data.symbols].sort((a, b) => a.symbol.localeCompare(b.symbol)));
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "watchlist.csv";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const [catFilter, setCatFilter] = useState(""); // CSV "Tag" category filter
  // Derived: all unique free-form tags with counts
  const allTags = useMemo(() => {
    const counts = {};
    for (const s of data.symbols) {
      for (const t of (s.tags || [])) counts[t] = (counts[t] || 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [data.symbols]);
  // Derived: all unique CSV categories (the first-class "Tag" field)
  const allCats = useMemo(() => {
    const set = new Set();
    for (const s of data.symbols) if (s.tag) set.add(s.tag);
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [data.symbols]);
  // Filtered + sorted view
  const visible = useMemo(() => {
    const q = search.trim().toUpperCase();
    let rows = data.symbols.filter(s => {
      if (q && !s.symbol.includes(q)
            && !(s.notes || "").toUpperCase().includes(q)
            && !(s.tag || "").toUpperCase().includes(q)
            && !(s.sector || "").toUpperCase().includes(q)
            && !(s.industry || "").toUpperCase().includes(q)
            && !(s.tags || []).some(t => t.toUpperCase().includes(q))) {
        return false;
      }
      if (tagFilter && !(s.tags || []).includes(tagFilter)) return false;
      if (catFilter && (s.tag || "") !== catFilter) return false;
      return true;
    });
    if (sortBy === "starred") {
      rows.sort((a, b) => (b.starred ? 1 : 0) - (a.starred ? 1 : 0)
        || a.symbol.localeCompare(b.symbol));
    } else if (sortBy === "symbol") {
      rows.sort((a, b) => a.symbol.localeCompare(b.symbol));
    } else if (sortBy === "added") {
      rows.sort((a, b) => (b.added_at || 0) - (a.added_at || 0));
    } else if (sortBy === "tag") {
      rows.sort((a, b) => (a.tag || "~").localeCompare(b.tag || "~")
        || a.symbol.localeCompare(b.symbol));
    }
    return rows;
  }, [data.symbols, search, tagFilter, catFilter, sortBy]);
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
          <option value="tag">By tag</option>
        </select>
        {allCats.length > 0 && (
          <select className="wlm-sort" value={catFilter}
                  onChange={e => setCatFilter(e.target.value)}
                  title="Filter by your Tag (category)">
            <option value="">All tags</option>
            {allCats.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
        <button className={`wlm-bulk-toggle${bulkOpen ? " active" : ""}`}
                onClick={() => setBulkOpen(o => !o)}
                title="Paste many tickers at once">
          {bulkOpen ? "Close bulk add" : "+ Bulk add"}
        </button>
        <button className={`wlm-bulk-toggle${csvOpen ? " active" : ""}`}
                onClick={() => { setCsvOpen(o => !o); setImportMsg(""); }}
                title="Import a stock list from a CSV (Symbol, Tag, Industry, Sector, Weekly)">
          {csvOpen ? "Close import" : "⇪ Import CSV"}
        </button>
        <button className="wlm-bulk-toggle"
                onClick={exportCsv}
                title="Download your current list as a CSV you can edit and re-import">
          ⇩ Export CSV
        </button>
      </div>
      {/* CSV import panel */}
      {csvOpen && (
        <CsvImportPanel
          data={data}
          onImportCsv={onImportCsv}
          onClose={(n) => {
            setCsvOpen(false);
            if (n > 0) setImportMsg(`Imported ${n} symbol${n === 1 ? "" : "s"} from CSV.`);
          }}
        />
      )}
      {importMsg && <div className="wlm-csv-done">{importMsg}</div>}
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
  const [tagInput, setTagInput] = useState(entry.tag || "");
  const [sectorInput, setSectorInput] = useState(entry.sector || "");
  const [industryInput, setIndustryInput] = useState(entry.industry || "");
  const [weeklyInput, setWeeklyInput] = useState(
    entry.weekly === true ? "Yes" : entry.weekly === false ? "No" : "");
  // Reset local state when entering edit mode
  useEffect(() => {
    if (isEditing) {
      setTagsInput((entry.tags || []).join(", "));
      setNotesInput(entry.notes || "");
      setStrategyInput(entry.preferred_strategy || "");
      setTagInput(entry.tag || "");
      setSectorInput(entry.sector || "");
      setIndustryInput(entry.industry || "");
      setWeeklyInput(entry.weekly === true ? "Yes" : entry.weekly === false ? "No" : "");
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
      tag: tagInput.trim().slice(0, 40),
      sector: sectorInput.trim().slice(0, 80),
      industry: industryInput.trim().slice(0, 80),
      weekly: weeklyInput === "Yes" ? true : weeklyInput === "No" ? false : null,
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
          {entry.tag && (
            <span className="wlm-cat-pill" title="Your category (from CSV import)">{entry.tag}</span>
          )}
          {entry.weekly === true && (
            <span className="wlm-wk-pill" title="Has weekly options">Wk</span>
          )}
          {entry.sector && (
            <span className="wlm-sec-pill" title={"Sector: " + entry.sector}>{entry.sector}</span>
          )}
          {entry.industry && (
            <span className="wlm-ind-pill" title={"Industry: " + entry.industry}>{entry.industry}</span>
          )}
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
            <label title="Your custom category for grouping this stock">Tag</label>
            <input type="text" value={tagInput} maxLength={40}
                   placeholder="Your category, e.g. Core, Swing, AI"
                   onChange={e => setTagInput(e.target.value)} />
          </div>
          <div className="wlm-edit-row">
            <label title="Whether weekly options exist (source of truth)">Weekly</label>
            <select value={weeklyInput} onChange={e => setWeeklyInput(e.target.value)}>
              <option value="">(unknown)</option>
              <option value="Yes">Yes</option>
              <option value="No">No</option>
            </select>
          </div>
          <div className="wlm-edit-row">
            <label title="Sector (overrides external data across the app)">Sector</label>
            <input type="text" value={sectorInput} maxLength={80}
                   placeholder="e.g. Technology"
                   onChange={e => setSectorInput(e.target.value)} />
          </div>
          <div className="wlm-edit-row">
            <label title="Industry (overrides external data across the app)">Industry</label>
            <input type="text" value={industryInput} maxLength={80}
                   placeholder="e.g. Semiconductors"
                   onChange={e => setIndustryInput(e.target.value)} />
          </div>
          <div className="wlm-edit-row">
            <label title="Free-form labels for filtering (comma-separated)">Tags</label>
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
  // Latest price via ref so the fetch URL stays current WITHOUT making
  // currentPrice an effect dependency (which refetched on every 5s quote tick).
  const flowPriceRef = React.useRef(currentPrice);
  flowPriceRef.current = currentPrice;
  useEffect(() => {
    if (!ticker || !uwHealth?.connected) {
      setFlowScore(null);
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const r = await apiFetch(`/api/uw/flow_score?symbol=${encodeURIComponent(ticker)}&price=${flowPriceRef.current || 0}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (!cancelled) setFlowScore(j);
      } catch {}
    };
    load();
    const id = setInterval(() => { if (!document.hidden) load(); }, 60000);
    return () => { cancelled = true; clearInterval(id); };
  }, [ticker, uwHealth?.connected]);

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

  // Latest price via ref so polling uses the current price without making
  // currentPrice a dependency (which double-fetched on every 5s quote tick).
  const scorePriceRef = React.useRef(currentPrice);
  scorePriceRef.current = currentPrice;
  useEffect(() => {
    if (!ticker || !uwHealth?.connected) return;
    let cancelled = false;
    const poll = async () => {
      try {
        setLoading(true);
        const url = `/api/uw/flow_score?symbol=${encodeURIComponent(ticker)}`
                  + (scorePriceRef.current ? `&price=${scorePriceRef.current}` : "");
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
  }, [ticker, uwHealth?.connected]);

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
        <CardNote kind="error">
          Can't reach Unusual Whales right now — flow returns once the connection recovers.
        </CardNote>
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
        <CardNote kind="loading">Loading flow data…</CardNote>
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
          {loading ? "Running…" : "Run"}
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

  // Latest price via ref — analyst ratings barely move with intraday price,
  // so we don't want to refetch on every 5s quote tick (deps below).
  const analystPriceRef = React.useRef(currentPrice);
  analystPriceRef.current = currentPrice;
  useEffect(() => {
    if (!ticker) return;
    let cancelled = false;
    const fetchData = async () => {
      try {
        setLoading(true);
        const url = `/api/analyst?symbol=${encodeURIComponent(ticker)}`
                  + (analystPriceRef.current ? `&price=${analystPriceRef.current}` : "")
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
  }, [ticker, refreshKey]);

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
        <div className="muted" style={{padding: "16px 0"}}>{loading ? "Loading…" : "No data."}</div>
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

// ──────────────────────────────────────────────────────────────────────
// Market Calendar — watchlist earnings calendar (weekly grid of stock
// cards) + economic calendar. A scannable "market command center" for the
// week ahead. Backed by /api/market_calendar/{earnings,economic} and a
// lazy /api/market_calendar/earnings_extra for the heavy per-symbol moves.
// ──────────────────────────────────────────────────────────────────────

// ── shared little formatters ──────────────────────────────────────────
function mcEtToday() {
  // The trading day in US/Eastern, as YYYY-MM-DD, regardless of the
  // browser's own timezone.
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
  } catch (_) { return new Date().toISOString().slice(0, 10); }
}
function mcDateObj(s) { return new Date(String(s).slice(0, 10) + "T12:00:00"); }
function mcMondayOf(d) {
  const x = new Date(d.getTime());
  const wd = (x.getDay() + 6) % 7;     // 0 = Monday
  x.setDate(x.getDate() - wd);
  x.setHours(12, 0, 0, 0);
  return x;
}
function mcIso(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function mcWeekday(s) { return mcDateObj(s).toLocaleDateString(undefined, { weekday: "short" }); }
function mcDayLabel(s) { return mcDateObj(s).toLocaleDateString(undefined, { month: "short", day: "numeric" }); }
function mcPct(v, d = 2) { return (v == null || isNaN(v)) ? "—" : `${v >= 0 ? "+" : ""}${(Math.round(v * 100) / 100).toFixed(d)}%`; }
function mcEps(v) { return (v == null || isNaN(v)) ? "—" : `${v < 0 ? "-$" : "$"}${Math.abs(v).toFixed(2)}`; }
function mcBigUSD(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${Math.round(v).toLocaleString()}`;
}
function mcInt(v) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return String(Math.round(v));
}
// Report-time chip styling.
function mcTimeMeta(t, reported) {
  if (reported) return { cls: "mc-rt-reported", label: "Reported", dot: "●" };
  if (t === "BMO") return { cls: "mc-rt-bmo", label: "Before Open", dot: "☀" };
  if (t === "AMC") return { cls: "mc-rt-amc", label: "After Close", dot: "🌙" };
  return { cls: "mc-rt-tas", label: "Time TBD", dot: "•" };
}

// One stock card inside a weekday column.
function MarketEarningsCard({ e, expanded, extra, live, compact, onToggle, onSwitchTicker }) {
  const liveLast = (live && live.last != null) ? live.last : e.last;
  const liveOpen = (live && live.open != null) ? live.open : e.open;
  const liveChg = (live && live.chg != null) ? live.chg : e.change;
  const fromOpen = (liveOpen && liveLast != null) ? ((liveLast - liveOpen) / liveOpen) * 100 : null;
  const rt = mcTimeMeta(e.report_time, e.reported);
  const big = (e.market_cap || 0) >= 10e9;     // index-mover flag
  const mega = (e.market_cap || 0) >= 200e9;
  const open = expanded;
  return (
    <div className={`mc-ecard ${rt.cls} ${open ? "open" : ""}`}>
      <div className="mc-ecard-top" onClick={onToggle} title="Click to expand earnings detail">
        <div className="mc-ecard-id">
          <button className="mc-sym" onClick={(ev) => { ev.stopPropagation(); onSwitchTicker && onSwitchTicker(e.symbol); }}
                  title={`Open ${e.symbol} on the Trade tab`}>{e.symbol}</button>
          {mega ? <span className="mc-star" title="Mega-cap — high-importance print">★</span>
            : big ? <span className="mc-star dim" title="Large-cap — notable print">★</span> : null}
        </div>
        <span className={`mc-rt-badge ${rt.cls}`} title={rt.label}>
          <span className="mc-rt-dot">{rt.dot}</span>{rt.label}
        </span>
      </div>
      {!compact && e.company ? <div className="mc-co" title={e.company}>{e.company}</div> : null}
      <div className="mc-ecard-quick">
        <span className="mc-cap" title="Market cap">{fmtMktCap(e.market_cap)}</span>
        <span className={`mc-fo ${fromOpen == null ? "" : fromOpen >= 0 ? "up" : "down"}`} title="% from today's open">
          {mcPct(fromOpen)}
        </span>
      </div>
      {!compact ? (
        <div className="mc-badges">
          {e.sector ? <span className="mc-tag mc-tag-sector" title={`Sector: ${e.sector}`}>{e.sector}</span> : null}
          {e.industry ? <span className="mc-tag mc-tag-ind" title={`Industry: ${e.industry}`}>{e.industry}</span> : null}
        </div>
      ) : null}
      {open ? (
        <div className="mc-ecard-detail">
          <div className="mc-stat-grid">
            <div className="mc-stat"><span className="mc-stat-k">EPS est.</span><span className="mc-stat-v">{mcEps(e.eps_estimate)}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">EPS act.</span><span className="mc-stat-v">{mcEps(e.eps_actual)}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">EPS surp.</span><span className={`mc-stat-v ${e.eps_surprise == null ? "" : e.eps_surprise >= 0 ? "up" : "down"}`}>{mcPct(e.eps_surprise)}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">Rev est.</span><span className="mc-stat-v">{mcBigUSD(e.revenue_estimate)}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">Rev act.</span><span className="mc-stat-v">{mcBigUSD(e.revenue_actual)}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">Rev surp.</span><span className={`mc-stat-v ${e.revenue_surprise == null ? "" : e.revenue_surprise >= 0 ? "up" : "down"}`}>{mcPct(e.revenue_surprise)}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">Price</span><span className="mc-stat-v">{liveLast == null ? "—" : `$${Number(liveLast).toFixed(2)}`}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">Change</span><span className={`mc-stat-v ${liveChg == null ? "" : liveChg >= 0 ? "up" : "down"}`}>{mcPct(liveChg)}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">% from open</span><span className={`mc-stat-v ${fromOpen == null ? "" : fromOpen >= 0 ? "up" : "down"}`}>{mcPct(fromOpen)}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">WTD</span><span className={`mc-stat-v ${e.wtd == null ? "" : e.wtd >= 0 ? "up" : "down"}`}>{mcPct(e.wtd)}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">MTD</span><span className={`mc-stat-v ${e.mtd == null ? "" : e.mtd >= 0 ? "up" : "down"}`}>{mcPct(e.mtd)}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">YTD</span><span className={`mc-stat-v ${e.ytd == null ? "" : e.ytd >= 0 ? "up" : "down"}`}>{mcPct(e.ytd)}</span></div>
          </div>
          <div className="mc-stat-grid mc-stat-extra">
            <div className="mc-stat"><span className="mc-stat-k">Implied move</span><span className="mc-stat-v accent">{extra ? (extra.implied_move_pct == null ? "—" : `±${extra.implied_move_pct}%`) : "…"}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">Avg post-ER</span><span className="mc-stat-v">{extra ? (extra.avg_post_earnings_move_pct == null ? "—" : `${extra.avg_post_earnings_move_pct}%`) : "…"}</span></div>
            <div className="mc-stat"><span className="mc-stat-k">Options vol</span><span className="mc-stat-v">{extra ? mcInt(extra.options_volume) : "…"}</span></div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function MarketCalendarCard({ apiFetch, onSwitchTicker }) {
  // ── data ────────────────────────────────────────────────────────────
  const [earn, setEarn] = useState(null);
  const [econ, setEcon] = useState(null);
  const [loadingE, setLoadingE] = useState(false);
  const [loadingM, setLoadingM] = useState(false);
  const [err, setErr] = useState(null);
  const [extras, setExtras] = useState({});      // symbol -> {implied_move_pct, ...}
  const [liveQ, setLiveQ] = useState({});        // symbol -> {last, open, chg}
  const [expanded, setExpanded] = useState({});  // symbol -> bool

  // ── earnings controls ───────────────────────────────────────────────
  const [weekOff, setWeekOff] = useState(0);     // 0 = current week, 1 = next, ...
  const [view, setView] = useState("expanded");  // "compact" | "expanded"
  const [fSector, setFSector] = useState("all");
  const [fIndustry, setFIndustry] = useState("all");
  const [fMcap, setFMcap] = useState("all");
  const [sortKey, setSortKey] = useState("mcap"); // mcap | move | optvol | fromopen
  // ── economic controls ───────────────────────────────────────────────
  const [econImp, setEconImp] = useState("med");  // all | med | high

  const loadEarn = () => {
    setLoadingE(true); setErr(null);
    apiFetch("/api/market_calendar/earnings?days=35")
      .then(r => r.json())
      .then(d => { setEarn(d); })
      .catch(e => setErr(String(e)))
      .finally(() => setLoadingE(false));
  };
  const loadEcon = () => {
    setLoadingM(true);
    apiFetch("/api/market_calendar/economic?days=28")
      .then(r => r.json())
      .then(d => { setEcon(d); })
      .catch(() => {})
      .finally(() => setLoadingM(false));
  };
  useEffect(() => { loadEarn(); loadEcon(); }, []);

  const entries = (earn && Array.isArray(earn.entries)) ? earn.entries : [];
  const today = mcEtToday();

  // Week window — Mon..Fri for the selected offset.
  const weekDays = useMemo(() => {
    const base = mcMondayOf(new Date(today + "T12:00:00"));
    base.setDate(base.getDate() + weekOff * 7);
    const out = [];
    for (let i = 0; i < 5; i++) {
      const d = new Date(base.getTime());
      d.setDate(d.getDate() + i);
      out.push(mcIso(d));
    }
    return out;
  }, [today, weekOff]);
  const weekSet = useMemo(() => new Set(weekDays), [weekDays]);

  // Entries inside the selected week, after filters.
  const weekEntries = useMemo(() => {
    return entries.filter(e =>
      weekSet.has(e.earnings_date) &&
      (fSector === "all" || e.sector === fSector) &&
      (fIndustry === "all" || e.industry === fIndustry) &&
      (fMcap === "all" || (MCAP_PRED[fMcap] || MCAP_PRED.all)(e.market_cap || 0))
    );
  }, [entries, weekSet, fSector, fIndustry, fMcap]);

  // Sorting comparator shared by the grid columns and highlight rails.
  const sortVal = (e) => {
    if (sortKey === "move") { const x = extras[e.symbol]; return x && x.implied_move_pct != null ? x.implied_move_pct : -1; }
    if (sortKey === "optvol") { const x = extras[e.symbol]; return x && x.options_volume != null ? x.options_volume : -1; }
    if (sortKey === "fromopen") {
      const q = liveQ[e.symbol]; const op = q && q.open != null ? q.open : e.open; const la = q && q.last != null ? q.last : e.last;
      return (op && la != null) ? Math.abs((la - op) / op) : -1;
    }
    return e.market_cap || 0;
  };
  const sortEntries = (arr) => arr.slice().sort((a, b) => sortVal(b) - sortVal(a));

  // Background fill of the heavy per-symbol extras for the visible week,
  // throttled so we never hammer the option-chain endpoint.
  useEffect(() => {
    let cancelled = false;
    const need = weekEntries.map(e => e.symbol).filter(s => !(s in extras));
    if (!need.length) return;
    let i = 0;
    const CONC = 3;
    const runOne = async () => {
      while (!cancelled && i < need.length) {
        const sym = need[i++];
        try {
          const r = await apiFetch(`/api/market_calendar/earnings_extra?symbol=${encodeURIComponent(sym)}`);
          const d = await r.json();
          if (!cancelled) setExtras(prev => ({ ...prev, [sym]: d || {} }));
        } catch (_) { if (!cancelled) setExtras(prev => ({ ...prev, [sym]: {} })); }
      }
    };
    const ps = []; for (let k = 0; k < CONC; k++) ps.push(runOne());
    return () => { cancelled = true; };
    // eslint-disable-next-line
  }, [weekEntries]);

  // Live quote overlay for the visible week (current price + % from open).
  useEffect(() => {
    let stop = false, timer = null;
    const syms = Array.from(new Set(weekEntries.map(e => e.symbol)));
    if (!syms.length) return;
    const tick = async () => {
      try {
        const next = {};
        for (let i = 0; i < syms.length; i += 25) {
          const batch = syms.slice(i, i + 25);
          const r = await apiFetch(`/api/quote?tickers=${batch.join(",")}`);
          const d = await r.json();
          const res = (d && d.results) || {};
          for (const s of batch) {
            const q = res[s];
            if (q) next[s] = { last: q.last, open: q.open != null ? q.open : null, chg: q.change_pct != null ? q.change_pct : null };
          }
        }
        if (!stop) setLiveQ(next);
      } catch (_) {}
      if (!stop) timer = setTimeout(tick, 30000);
    };
    tick();
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, [weekEntries]);

  const toggle = (sym) => setExpanded(p => ({ ...p, [sym]: !p[sym] }));

  // Highlight rails (operate on the filtered week).
  const todayEntries = sortEntries(weekEntries.filter(e => e.earnings_date === today));
  const importantEntries = weekEntries.slice().sort((a, b) => (b.market_cap || 0) - (a.market_cap || 0)).slice(0, 6);
  const moveEntries = weekEntries
    .filter(e => extras[e.symbol] && extras[e.symbol].implied_move_pct != null)
    .sort((a, b) => extras[b.symbol].implied_move_pct - extras[a.symbol].implied_move_pct)
    .slice(0, 6);

  const sectors = (earn && earn.sectors) || [];
  const industries = (earn && earn.industries) || [];
  const weekLabel = weekOff === 0 ? "This week" : weekOff === 1 ? "Next week" : `+${weekOff} weeks`;
  const scanning = earn && earn.board_status && earn.board_status.scanning;

  // ── economic calendar grouped by date ───────────────────────────────
  const econEvents = (econ && Array.isArray(econ.events)) ? econ.events : [];
  const econFiltered = econEvents.filter(ev =>
    econImp === "all" ? true : econImp === "high" ? ev.importance === "high" : ev.importance !== "low"
  );
  const econByDate = useMemo(() => {
    const m = new Map();
    for (const ev of econFiltered) {
      if (!m.has(ev.date)) m.set(ev.date, []);
      m.get(ev.date).push(ev);
    }
    return Array.from(m.entries());
  }, [econFiltered]);
  const impMeta = (imp) => imp === "high" ? { cls: "mc-imp-high", label: "High" }
    : imp === "medium" ? { cls: "mc-imp-med", label: "Med" } : { cls: "mc-imp-low", label: "Low" };

  const miniCard = (e) => {
    const live = liveQ[e.symbol];
    const liveLast = live && live.last != null ? live.last : e.last;
    const liveOpen = live && live.open != null ? live.open : e.open;
    const fo = (liveOpen && liveLast != null) ? ((liveLast - liveOpen) / liveOpen) * 100 : null;
    const x = extras[e.symbol];
    const rt = mcTimeMeta(e.report_time, e.reported);
    return (
      <button key={e.symbol} className={`mc-mini ${rt.cls}`} onClick={() => onSwitchTicker && onSwitchTicker(e.symbol)}
              title={`${e.company || e.symbol} — ${mcDayLabel(e.earnings_date)} ${rt.label}`}>
        <span className="mc-mini-sym">{e.symbol}</span>
        <span className="mc-mini-meta">{fmtMktCap(e.market_cap)}</span>
        {x && x.implied_move_pct != null ? <span className="mc-mini-move">±{x.implied_move_pct}%</span>
          : <span className={`mc-mini-move ${fo == null ? "" : fo >= 0 ? "up" : "down"}`}>{mcPct(fo)}</span>}
      </button>
    );
  };

  return (
    <div className="mc-wrap">
      {/* ============ EARNINGS CALENDAR ============ */}
      <div className="card mc-card">
        <div className="card-head mc-head">
          <div>
            <span className="kicker">Watchlist</span>
            <div className="card-title">Earnings Calendar</div>
          </div>
          <div className="mc-head-controls">
            <div className="mc-weeknav">
              <button onClick={() => setWeekOff(w => Math.max(0, w - 1))} disabled={weekOff === 0} title="Previous week">‹</button>
              <span className="mc-week-label">{weekLabel}</span>
              <button onClick={() => setWeekOff(w => Math.min(3, w + 1))} disabled={weekOff >= 3} title="Next week">›</button>
            </div>
            <div className="seg">
              <button className={view === "compact" ? "active" : ""} onClick={() => setView("compact")}>Compact</button>
              <button className={view === "expanded" ? "active" : ""} onClick={() => setView("expanded")}>Expanded</button>
            </div>
            <button className="mc-refresh" onClick={loadEarn} disabled={loadingE} title="Reload earnings">{loadingE ? "…" : "↻"}</button>
          </div>
        </div>

        {err ? <div className="mc-error">Couldn't load earnings: {err}</div> : null}
        {scanning ? <div className="mc-hint">Watchlist board is still scanning — more names will appear as data fills in.</div> : null}

        {/* Filters + sort */}
        <div className="mc-filters">
          <select value={fSector} onChange={e => setFSector(e.target.value)} title="Filter by sector">
            <option value="all">All sectors</option>
            {sectors.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={fIndustry} onChange={e => setFIndustry(e.target.value)} title="Filter by industry">
            <option value="all">All industries</option>
            {industries.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={fMcap} onChange={e => setFMcap(e.target.value)} title="Filter by market cap">
            {MCAP_BUCKETS.map(b => <option key={b[0]} value={b[0]}>{b[1]}</option>)}
          </select>
          <select value={sortKey} onChange={e => setSortKey(e.target.value)} title="Sort cards within each day">
            <option value="mcap">Sort: Market cap</option>
            <option value="move">Sort: Expected move</option>
            <option value="optvol">Sort: Options volume</option>
            <option value="fromopen">Sort: % from open</option>
          </select>
        </div>

        {/* Highlight rails */}
        <div className="mc-rails">
          <div className="mc-rail">
            <div className="mc-rail-title">📅 Watchlist Earnings Today</div>
            <div className="mc-rail-body">{todayEntries.length ? todayEntries.map(miniCard) : <span className="mc-empty">No watchlist names report today.</span>}</div>
          </div>
          <div className="mc-rail">
            <div className="mc-rail-title">⭐ Most Important This Week</div>
            <div className="mc-rail-body">{importantEntries.length ? importantEntries.map(miniCard) : <span className="mc-empty">No earnings this week.</span>}</div>
          </div>
          <div className="mc-rail">
            <div className="mc-rail-title">🚀 Biggest Expected Move</div>
            <div className="mc-rail-body">{moveEntries.length ? moveEntries.map(miniCard) : <span className="mc-empty">Loading expected moves…</span>}</div>
          </div>
        </div>

        {/* Weekly grid */}
        <div className="mc-grid">
          {weekDays.map(day => {
            const dayEntries = sortEntries(weekEntries.filter(e => e.earnings_date === day));
            const isToday = day === today;
            return (
              <div key={day} className={`mc-col ${isToday ? "mc-col-today" : ""}`}>
                <div className="mc-col-head">
                  <span className="mc-col-wd">{mcWeekday(day)}</span>
                  <span className="mc-col-date">{mcDayLabel(day)}</span>
                  <span className="mc-col-count">{dayEntries.length || ""}</span>
                </div>
                <div className="mc-col-body">
                  {dayEntries.length ? dayEntries.map(e => (
                    <MarketEarningsCard key={e.symbol} e={e}
                      expanded={!!expanded[e.symbol]} extra={extras[e.symbol]}
                      live={liveQ[e.symbol]} compact={view === "compact"}
                      onToggle={() => toggle(e.symbol)} onSwitchTicker={onSwitchTicker} />
                  )) : <div className="mc-col-empty">—</div>}
                </div>
              </div>
            );
          })}
        </div>
        {!loadingE && entries.length === 0 ? (
          <div className="mc-empty-all">No watchlist earnings found in the next 4 weeks.</div>
        ) : null}
      </div>

      {/* ============ ECONOMIC CALENDAR ============ */}
      <div className="card mc-card mc-econ">
        <div className="card-head mc-head">
          <div>
            <span className="kicker">Macro</span>
            <div className="card-title">Economic Calendar</div>
          </div>
          <div className="mc-head-controls">
            <div className="seg">
              <button className={econImp === "high" ? "active" : ""} onClick={() => setEconImp("high")}>High</button>
              <button className={econImp === "med" ? "active" : ""} onClick={() => setEconImp("med")}>Med+</button>
              <button className={econImp === "all" ? "active" : ""} onClick={() => setEconImp("all")}>All</button>
            </div>
            <button className="mc-refresh" onClick={loadEcon} disabled={loadingM} title="Reload events">{loadingM ? "…" : "↻"}</button>
          </div>
        </div>
        {econ && econ.error ? <div className="mc-error">Economic data unavailable: {econ.error}</div> : null}
        {econByDate.length === 0 && !loadingM ? <div className="mc-empty-all">No events at this importance level.</div> : null}
        <div className="mc-econ-list">
          {econByDate.map(([date, evs]) => (
            <div key={date} className={`mc-econ-day ${date === today ? "mc-econ-today" : ""}`}>
              <div className="mc-econ-dayhead">
                <span className="mc-econ-wd">{mcWeekday(date)}</span>
                <span className="mc-econ-date">{mcDayLabel(date)}</span>
                {date === today ? <span className="mc-econ-todaytag">Today</span> : null}
              </div>
              <div className="mc-econ-rows">
                {evs.map((ev, i) => {
                  const im = impMeta(ev.importance);
                  return (
                    <div key={i} className={`mc-econ-row ${im.cls}`} title={ev.note || ""}>
                      <span className="mc-econ-time">{ev.time}</span>
                      <span className={`mc-imp-dot ${im.cls}`} title={`${im.label} importance`}></span>
                      <span className="mc-econ-name">{ev.event}{ev.period ? <span className="mc-econ-for"> ({ev.period})</span> : null}
                        <span className="mc-econ-ctry">{ev.country}</span>
                      </span>
                      <span className="mc-econ-vals">
                        <span className="mc-econ-val" title="Actual — the released figure (blank until reported)"><b>A</b>{ev.actual == null ? "—" : ev.actual}</span>
                        <span className="mc-econ-val" title="Forecast — consensus estimate"><b>F</b>{ev.forecast == null ? "—" : ev.forecast}</span>
                        <span className="mc-econ-val" title="Previous — last period's figure (r = revised)"><b>P</b>{ev.previous == null ? "—" : ev.previous}{ev.revised != null ? <span className="mc-econ-rev"> (r {ev.revised})</span> : null}</span>
                      </span>
                      {ev.note ? <span className="mc-econ-note">{ev.note}</span> : null}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// Watchlist Analyst Actions — fresh upgrades/downgrades/PT changes/initiations
// for watchlist names, drawn from the morning analyst-board scan. Today's
// actions are highlighted so the morning read is instant.
function WatchlistAnalystCard({ apiFetch, onSwitchTicker }) {
  const [data, setData] = useState(null);
  const [scope, setScope] = useState("today");   // today | recent
  const [type, setType] = useState("all");        // all|upgrade|downgrade|pt_up|pt_cut|initiate|high|multi
  const [sortKey, setSortKey] = useState("impact"); // legacy keys or any row field
  const [sortDir, setSortDir] = useState("desc");
  const waaSortBy = (field) => {
    const cur = { impact: "impact_score", upside: "upside_pct", date: "action_date", symbol: "symbol" }[sortKey] || sortKey;
    if (cur === field) setSortDir(d => d === "desc" ? "asc" : "desc");
    else { setSortKey(field); setSortDir(["symbol", "company", "firm", "action_type", "rating_from", "rating_to", "source"].includes(field) ? "asc" : "desc"); }
  };
  const [busy, setBusy] = useState(false);
  const pollRef = useRef(null);

  const load = async () => {
    try {
      const r = await apiFetch("/api/watchlist_analyst");
      const d = await r.json();
      setData(d);
      return d;
    } catch (_) { return null; }
  };
  useEffect(() => {
    load();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const startScan = async () => {
    setBusy(true);
    try { await apiFetch("/api/analyst_board/scan?days=2&force=1"); } catch (_) {}
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (!d || !d.scanning) { clearInterval(pollRef.current); pollRef.current = null; setBusy(false); }
    }, 4000);
  };

  const actions = (data && data.actions) || [];
  const isScanning = busy || (data && data.scanning);
  const detected = (data && data.detected_at)
    ? new Date(data.detected_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : null;

  const typePass = (a) => {
    switch (type) {
      case "upgrade": return a.action_type === "upgrade";
      case "downgrade": return a.action_type === "downgrade";
      case "pt_up": return a.target_change_pct != null && a.target_change_pct > 0;
      case "pt_cut": return a.target_change_pct != null && a.target_change_pct < 0;
      case "initiate": return a.action_type === "initiate";
      case "high": return a.importance === "high";
      case "multi": return (a.multi_count || 1) > 1;
      default: return true;
    }
  };
  const filtered = actions.filter(a => (scope === "today" ? a.fresh_today : true) && typePass(a));
  // Column-header sorting: any column, asc/desc toggle. The Sort dropdown
  // still works (it drives the same state via the legacy keys).
  const WAA_NUM = { prev_target: 1, new_target: 1, current_price: 1, upside_pct: 1, impact_score: 1 };
  const sortField = { impact: "impact_score", upside: "upside_pct", date: "action_date", symbol: "symbol" }[sortKey] || sortKey;
  const sorted = filtered.slice().sort((x, y) => {
    const a = x[sortField], b = y[sortField];
    const r = WAA_NUM[sortField]
      ? (a == null ? -1e18 : a) - (b == null ? -1e18 : b)
      : String(a || "").localeCompare(String(b || ""));
    return sortDir === "asc" ? r : -r;
  });
  const freshCount = actions.filter(a => a.fresh_today).length;

  const AT = { upgrade: "Upgrade", downgrade: "Downgrade", initiate: "Initiation", reiterate: "Reiteration", target_change: "PT change" };
  const ptf = (v) => v == null ? "—" : "$" + Number(v).toFixed(2);
  const usDate = (s) => {
    if (!s) return "—";
    const p = String(s).slice(0, 10).split("-");   // YYYY-MM-DD -> M-D-YYYY
    return p.length === 3 ? `${+p[1]}-${+p[2]}-${p[0]}` : s;
  };
  const pctf = (v) => v == null ? "—" : (v >= 0 ? "+" : "") + Number(v).toFixed(1) + "%";
  const FILTERS = [["all", "All"], ["upgrade", "Upgrades"], ["downgrade", "Downgrades"],
    ["pt_up", "PT raised"], ["pt_cut", "PT cut"], ["initiate", "New coverage"],
    ["high", "High impact"], ["multi", "Multi-firm"]];

  return (
    <div className="card waa-card">
      <div className="card-head waa-head">
        <div>
          <span className="kicker">Watchlist · {freshCount} fresh today{detected ? ` · scanned ${detected}` : ""}</span>
          <div className="card-title">Analyst Actions</div>
        </div>
        <div className="waa-head-controls">
          <div className="seg">
            <button className={scope === "today" ? "active" : ""} onClick={() => setScope("today")}>Today</button>
            <button className={scope === "recent" ? "active" : ""} onClick={() => setScope("recent")}>Recent</button>
          </div>
          <select value={sortKey} onChange={e => setSortKey(e.target.value)} title="Sort actions">
            <option value="impact">Sort: Impact</option>
            <option value="upside">Sort: Upside</option>
            <option value="date">Sort: Action date</option>
            <option value="symbol">Sort: Symbol</option>
          </select>
          <button className="scan-run-btn" onClick={startScan} disabled={isScanning}>{isScanning ? "Scanning…" : "Scan now"}</button>
        </div>
      </div>

      <div className="waa-filters">
        {FILTERS.map(([k, lbl]) => (
          <button key={k} className={`preset-pill ${type === k ? "active" : ""}`} onClick={() => setType(k)}>{lbl}</button>
        ))}
      </div>

      {sorted.length === 0 ? (
        <div className="waa-empty">
          {isScanning ? "Scanning for analyst actions…"
            : actions.length === 0
              ? <>No analyst actions cached yet — <button className="wl-rescan-link" onClick={startScan}>Scan now</button> to build today's board.</>
              : (scope === "today" && type === "all")
                ? <>No analyst actions dated today yet — {actions.length} recent {actions.length === 1 ? "action" : "actions"} on your watchlist. <button className="wl-rescan-link" onClick={() => setScope("recent")}>Show recent</button></>
                : "No actions match this filter."}
        </div>
      ) : (
        <div className="waa-table-wrap">
          <table className="waa-table">
            <colgroup>
              <col style={{ width: "7%" }} /><col style={{ width: "16%" }} />
              <col style={{ width: "8%" }} /><col style={{ width: "13%" }} />
              <col style={{ width: "9%" }} /><col style={{ width: "8%" }} />
              <col style={{ width: "9%" }} /><col style={{ width: "7%" }} />
              <col style={{ width: "7%" }} /><col style={{ width: "7%" }} />
              <col style={{ width: "5%" }} /><col style={{ width: "8%" }} />
            </colgroup>
            <thead><tr>
              {[["symbol", "Symbol", "Ticker — click a row to open it on the Trade tab"],
                ["company", "Company", "Company name"],
                ["action_date", "Date", "Date of the analyst action"],
                ["firm", "Firm", "Brokerage / research firm"],
                ["action_type", "Type", "Action type — upgrade, downgrade, initiation, reiteration, or price-target change"],
                ["rating_from", "From", "Prior analyst rating"],
                ["rating_to", "To", "New analyst rating"],
                ["current_price", "Now", "Current stock price — compare it to the targets at a glance", 1],
                ["prev_target", "Prev PT", "Previous price target", 1],
                ["new_target", "New PT", "New price target", 1],
                ["upside_pct", "Upside", "% upside/downside from the current price to the new target", 1],
                ["impact_score", "Impact", "Impact score (0–100) — firm tier, market cap, PT move size, multi-firm agreement", 1],
                ["source", "Source", "Data source"]].map(([f, label, tip, num]) => (
                <th key={f} className={num ? "num" : ""} style={{ cursor: "pointer" }}
                    title={`${tip} — click to sort`} onClick={() => waaSortBy(f)}>
                  {label}{sortField === f ? (sortDir === "desc" ? " ↓" : " ↑") : ""}
                </th>
              ))}
            </tr></thead>
            <tbody>
              {sorted.map((a, i) => (
                <tr key={a.symbol + i} className={`waa-row ${a.fresh_today ? "waa-fresh" : ""} waa-${a.direction || "neutral"}`}
                    onClick={() => onSwitchTicker && onSwitchTicker(a.symbol)} title={(a.reasons || []).join(" · ")}>
                  <td className="waa-sym">
                    {a.fresh_today && <span className="waa-bolt" title="Fresh today">⚡</span>}{a.symbol}
                    {(a.multi_count || 1) > 1 && <span className="waa-multi" title={`${a.multi_count} firms acted`}>×{a.multi_count}</span>}
                  </td>
                  <td className="waa-co" title={a.company || ""}>{a.company || "—"}</td>
                  <td className="waa-date">{usDate(a.action_date)}</td>
                  <td className="waa-firm">{a.firm}</td>
                  <td><span className={`waa-type waa-type-${a.direction || "neutral"}`}>{AT[a.action_type] || a.action_type || "—"}</span></td>
                  <td className="waa-grade">{a.rating_from || "—"}</td>
                  <td className="waa-grade">{a.rating_to || "—"}</td>
                  <td className="num" title="Current stock price">{ptf(a.current_price)}</td>
                  <td className="num">{ptf(a.prev_target)}</td>
                  <td className="num">{ptf(a.new_target)}</td>
                  <td className={`num ${a.upside_pct == null ? "" : a.upside_pct >= 0 ? "up" : "down"}`}>{pctf(a.upside_pct)}</td>
                  <td className="num"><span className={`waa-score waa-imp-${a.importance || "low"}`}>{a.impact_score != null ? Math.round(a.impact_score) : "—"}</span></td>
                  <td className="waa-src">{a.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Company profile (Yahoo "Profile" page) — shown inside the News tab so it
// doesn't add a top-row tab. Description, sector/industry, HQ, website, execs.
function StockProfileCard({ apiFetch, ticker, alwaysShow }) {
  const [p, setP] = useState(null);
  const [open, setOpen] = useState(true);   // description expanded by default
  useEffect(() => {
    let stop = false;
    setP(null); setOpen(true);
    if (!ticker) return;
    (async () => {
      try {
        const r = await apiFetch(`/api/profile?symbol=${encodeURIComponent(ticker)}`);
        const d = await r.json();
        if (!stop) setP(d);
      } catch (_) {}
    })();
    return () => { stop = true; };
  }, [ticker]);

  const empty = !p || (!p.summary && !p.sector && !p.industry);
  if (empty) {
    if (!alwaysShow) return null;
    return <div className="card prof-card"><div className="prof-summary">{p ? "No profile available for this symbol." : "Loading profile…"}</div></div>;
  }

  const fmtEmp = (n) => n == null ? null : Number(n).toLocaleString();
  const fmtPay = (n) => n == null ? null : n >= 1e6 ? "$" + (n / 1e6).toFixed(1) + "M" : "$" + Number(n).toLocaleString();
  const site = p.website ? (p.website.startsWith("http") ? p.website : `https://${p.website}`) : null;
  const summary = p.summary || "";
  const clamped = summary.length > 340 ? summary.slice(0, 340).trimEnd() + "…" : summary;

  return (
    <div className="card prof-card">
      <div className="card-head">
        <div>
          <div className="kicker">Company profile</div>
          <div className="card-title">{p.name || p.symbol}</div>
        </div>
        {site && <a className="prof-site" href={site} target="_blank" rel="noopener noreferrer">Website ↗</a>}
      </div>
      <div className="prof-tags">
        {p.sector && <span className="prof-tag">{p.sector}</span>}
        {p.industry && <span className="prof-tag prof-tag-ind">{p.industry}</span>}
        {p.exchange && <span className="prof-tag prof-muted">{p.exchange}</span>}
      </div>
      <div className="prof-meta">
        {fmtEmp(p.employees) && <div className="prof-m"><span>Employees</span><b>{fmtEmp(p.employees)}</b></div>}
        {p.address && <div className="prof-m"><span>Headquarters</span><b>
          <a className="prof-link" href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(p.address)}`}
             target="_blank" rel="noopener noreferrer">{p.address}</a></b></div>}
        {p.phone && <div className="prof-m"><span>Phone</span><b>
          <a className="prof-link" href={`tel:${String(p.phone).replace(/[^0-9+]/g, "")}`}>{p.phone}</a></b></div>}
      </div>
      {summary && (
        <div className="prof-summary">
          {open ? summary : clamped}
          {summary.length > 340 && (
            <button className="prof-more" onClick={() => setOpen(o => !o)}>{open ? " Show less" : " Show more"}</button>
          )}
        </div>
      )}
      {p.officers && p.officers.length > 0 && (
        <div className="prof-execs">
          <div className="prof-execs-title">Key executives</div>
          <div className="prof-exec prof-exec-head">
            <span>Name</span><span>Title</span><span className="prof-exec-pay">Pay</span>
          </div>
          {p.officers.map((o, i) => (
            <div className="prof-exec" key={i}>
              <span className="prof-exec-name">{o.name}</span>
              <span className="prof-exec-title">{o.title || "—"}</span>
              <span className="prof-exec-pay">{fmtPay(o.pay) || "—"}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Streaks scanner — consecutive up/down day runs for every watchlist name,
// judged against each stock's OWN history (not a fixed 5/6/8), to surface
// names that may be near exhaustion / due for mean reversion.
function WatchlistStreaksCard({ apiFetch, onSwitchTicker }) {
  const [board, setBoard] = useState(null);
  const [liveQ, setLiveQ] = useState({});
  const [dir, setDir] = useState("all");        // all | up | down
  const [fSector, setFSector] = useState("all");
  const [minCount, setMinCount] = useState(3);
  const [flagOnly, setFlagOnly] = useState(false);
  const [sortKey, setSortKey] = useState("extremity");
  const pollRef = useRef(null);

  const load = async () => {
    try { const d = await sharedJson(apiFetch, "/api/watchlist_table", 20000); setBoard(d); return d; }
    catch (_) { return null; }
  };
  useEffect(() => {
    load();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(load, 5 * 60 * 1000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const rows = (board && board.rows) || [];
  const status = (board && board.status) || {};

  // Flag/extremity logic — relative to each stock's own record.
  const decorate = (r) => {
    const longestSame = r.streak_dir === "up" ? (r.longest_up || 0) : (r.longest_down || 0);
    const ext = longestSame > 0 ? r.streak_count / longestSame : 0;
    const nearRecord = longestSame >= 4 && r.streak_count >= longestSame - 1;
    const atRecord = longestSame >= 4 && r.streak_count >= longestSame;
    const rare = r.streak_times_before != null && r.streak_times_before <= 3 && r.streak_count >= 4;
    const flags = [];
    if (r.streak_dir === "down" && nearRecord) { flags.push(atRecord ? "Record Down Streak" : "Near Record Down Streak"); flags.push("Possible Exhaustion Setup"); }
    if (r.streak_dir === "up" && nearRecord) flags.push(atRecord ? "Record Up Streak" : "Near Record Up Streak");
    if (rare) flags.push("Rare Streak");
    if (r.streak_dir === "down" && (nearRecord || rare)) flags.push("Mean Reversion Watch");
    return { ...r, ext, nearRecord, rare, flags };
  };

  const liveVal = (r) => {
    const q = liveQ[r.symbol] || {};
    const last = q.last != null ? q.last : r.last;
    const open = q.open != null ? q.open : r.open;
    const chg = q.change_pct != null ? q.change_pct : r.change;
    const fromOpen = (open && last != null) ? ((last - open) / open) * 100 : null;
    return { last, chg, fromOpen };
  };

  const sectors = useMemo(
    () => Array.from(new Set(rows.map(r => r.sector).filter(Boolean))).sort(), [rows]);

  const view = useMemo(() => {
    let v = rows
      .filter(r => r.streak_dir && r.streak_dir !== "flat" && (r.streak_count || 0) >= minCount)
      .filter(r => dir === "all" || r.streak_dir === dir)
      .filter(r => fSector === "all" || r.sector === fSector)
      .map(decorate);
    if (flagOnly) v = v.filter(r => r.flags.length > 0);
    const sv = (r) => {
      switch (sortKey) {
        case "count": return r.streak_count || 0;
        case "winrate": return r.streak_winrate == null ? -1 : r.streak_winrate;
        case "fwd5": return r.streak_fwd5 == null ? -1e9 : r.streak_fwd5;
        case "rsi": return r.rsi == null ? -1 : r.rsi;
        case "rare": return r.streak_times_before == null ? 1e9 : r.streak_times_before;  // fewest first
        default: return r.ext;   // extremity
      }
    };
    const asc = sortKey === "rare";
    return v.sort((a, b) => asc ? sv(a) - sv(b) : sv(b) - sv(a));
  }, [rows, dir, fSector, minCount, flagOnly, sortKey, liveQ]);

  // Live overlay for the visible names (price + % from open + day change).
  useEffect(() => {
    let stop = false, timer = null;
    const syms = Array.from(new Set(view.slice(0, 50).map(r => r.symbol)));
    if (!syms.length) return;
    const tick = async () => {
      try {
        const next = {};
        for (let i = 0; i < syms.length; i += 25) {
          const r = await apiFetch(`/api/quote?tickers=${syms.slice(i, i + 25).join(",")}`);
          const d = await r.json(); const res = (d && d.results) || {};
          for (const s of syms.slice(i, i + 25)) if (res[s]) next[s] = res[s];
        }
        if (!stop) setLiveQ(next);
      } catch (_) {}
      if (!stop) timer = setTimeout(tick, 30000);
    };
    tick();
    return () => { stop = true; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line
  }, [view.length]);

  const pct = (v, d = 2) => v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`;
  const fmtV = (v) => v == null ? "—" : v >= 1e9 ? (v / 1e9).toFixed(2) + "B" : v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(0) + "K" : String(Math.round(v));
  const FLAG_CLS = { "Possible Exhaustion Setup": "warn", "Mean Reversion Watch": "warn",
    "Rare Streak": "rare", "Near Record Down Streak": "bear", "Record Down Streak": "bear",
    "Near Record Up Streak": "bull", "Record Up Streak": "bull" };
  const scanning = !!status.scanning;
  const nearCount = view.filter(r => r.flags.length).length;

  return (
    <div className="card wstk-card">
      <div className="card-head wstk-head">
        <div>
          <span className="kicker">Watchlist · {view.length} streaks · {nearCount} flagged</span>
          <div className="card-title">Streak Exhaustion Scanner</div>
        </div>
        <div className="wstk-controls">
          <div className="seg">
            <button className={dir === "all" ? "active" : ""} onClick={() => setDir("all")}>All</button>
            <button className={dir === "up" ? "active" : ""} onClick={() => setDir("up")}>Up</button>
            <button className={dir === "down" ? "active" : ""} onClick={() => setDir("down")}>Down</button>
          </div>
          <select value={fSector} onChange={e => setFSector(e.target.value)} title="Filter by sector">
            <option value="all">All sectors</option>
            {sectors.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <label className="wstk-min" title="Minimum consecutive days">≥ <input type="number" min="1" max="20" value={minCount}
            onChange={e => setMinCount(Math.max(1, Math.min(20, +e.target.value || 1)))} /> days</label>
          <button className={`preset-pill ${flagOnly ? "active" : ""}`} onClick={() => setFlagOnly(f => !f)} title="Only stocks flagged near a historical extreme">Flagged only</button>
          <select value={sortKey} onChange={e => setSortKey(e.target.value)} title="Sort">
            <option value="extremity">Sort: Extremity</option>
            <option value="count">Sort: Streak length</option>
            <option value="rare">Sort: Rarest</option>
            <option value="winrate">Sort: Win rate</option>
            <option value="fwd5">Sort: Next-5d avg</option>
            <option value="rsi">Sort: RSI</option>
          </select>
        </div>
      </div>

      {scanning ? <div className="wstk-hint">Watchlist board is scanning — streaks fill in as data lands.</div> : null}
      {!scanning && rows.length === 0 ? <div className="wstk-empty">No board data yet. Open the Watchlist tab and run a scan to build it.</div> : null}

      {view.length > 0 && (
        <div className="wstk-wrap">
          <table className="wstk-table">
            <thead><tr>
              <th title="Ticker — click a row to open it on the Trade tab">Symbol</th>
              <th title="Company name">Company</th>
              <th title="Current consecutive up/down day streak (direction + number of days)">Streak</th>
              <th className="num" title="Longest up streak / longest down streak ever seen in the last 2 years">Rec ↑/↓</th>
              <th className="num" title="How many times this stock previously reached a streak this long in the same direction (★ = rare, ≤3 times)">Seen</th>
              <th className="num" title="Average next-1-day return after similar past streaks">Nx1</th>
              <th className="num" title="Average next-3-day return after similar past streaks">Nx3</th>
              <th className="num" title="Average next-5-day return after similar past streaks">Nx5</th>
              <th className="num" title="Win rate — % of similar past streaks that were higher 5 days later">Win5</th>
              <th className="num" title="Current price (live)">Price</th>
              <th className="num" title="% change from today's open (live)">%Open</th>
              <th className="num" title="Daily % change (live)">Day</th>
              <th className="num" title="Latest daily volume">Vol</th>
              <th className="num" title="Relative volume — today's volume vs its 20-day average">RVol</th>
              <th className="num" title="RSI(14)">RSI</th>
              <th className="num" title="Distance from the 20-day moving average">20DMA</th>
              <th className="num" title="Distance from the 50-day moving average">50DMA</th>
              <th title="Sector (hover a cell for industry)">Sector</th>
              <th title="Exhaustion / mean-reversion flags vs this stock's own record">Flags</th>
            </tr></thead>
            <tbody>
              {view.map((r, i) => {
                const lv = liveVal(r);
                const dirCls = r.streak_dir === "up" ? "up" : "down";
                return (
                  <tr key={r.symbol + i} className={`wstk-row ${r.flags.length ? "wstk-flagged" : ""}`}
                      onClick={() => onSwitchTicker && onSwitchTicker(r.symbol)}>
                    <td className="wstk-sym">{r.symbol}</td>
                    <td className="wstk-co" title={r.company || ""}>{r.company || "—"}</td>
                    <td><span className={`wstk-streak ${dirCls}`}>{r.streak_dir === "up" ? "▲" : "▼"} {r.streak_count}d</span></td>
                    <td className="num wstk-rec">{r.longest_up || "—"}/{r.longest_down || "—"}</td>
                    <td className="num">{r.streak_times_before == null ? "—" : r.streak_times_before}{r.rare ? "★" : ""}</td>
                    <td className={`num ${r.streak_fwd1 == null ? "" : r.streak_fwd1 >= 0 ? "up" : "down"}`}>{pct(r.streak_fwd1, 1)}</td>
                    <td className={`num ${r.streak_fwd3 == null ? "" : r.streak_fwd3 >= 0 ? "up" : "down"}`}>{pct(r.streak_fwd3, 1)}</td>
                    <td className={`num ${r.streak_fwd5 == null ? "" : r.streak_fwd5 >= 0 ? "up" : "down"}`}>{pct(r.streak_fwd5, 1)}</td>
                    <td className="num">{r.streak_winrate == null ? "—" : r.streak_winrate + "%"}</td>
                    <td className="num">{lv.last == null ? "—" : "$" + Number(lv.last).toFixed(2)}</td>
                    <td className={`num ${lv.fromOpen == null ? "" : lv.fromOpen >= 0 ? "up" : "down"}`}>{pct(lv.fromOpen, 1)}</td>
                    <td className={`num ${lv.chg == null ? "" : lv.chg >= 0 ? "up" : "down"}`}>{pct(lv.chg, 1)}</td>
                    <td className="num">{fmtV(r.volume)}</td>
                    <td className="num">{r.rel_vol == null ? "—" : r.rel_vol + "x"}</td>
                    <td className="num">{r.rsi == null ? "—" : Math.round(r.rsi)}</td>
                    <td className={`num ${r.from_ma20 == null ? "" : r.from_ma20 >= 0 ? "up" : "down"}`}>{pct(r.from_ma20, 1)}</td>
                    <td className={`num ${r.from_ma50 == null ? "" : r.from_ma50 >= 0 ? "up" : "down"}`}>{pct(r.from_ma50, 1)}</td>
                    <td className="wstk-sec" title={r.industry || ""}>{r.sector || "—"}</td>
                    <td className="wstk-flags">
                      {r.flags.map((f, j) => <span key={j} className={`wstk-flag wstk-f-${FLAG_CLS[f] || "warn"}`}>{f}</span>)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {!scanning && rows.length > 0 && view.length === 0 && <div className="wstk-empty">No streaks match these filters.</div>}
    </div>
  );
}

// Schwab in-app reconnect. Schwab refresh tokens die every 7 days; this turns
// the re-auth into a ~20s in-browser action: open login, paste the redirect
// URL back, done — no terminal, no Railway edits. Renders as a top banner
// only when reconnect is needed; as a full panel in the Manage tab always.
function SchwabReconnect({ apiFetch, placement }) {
  const [st, setSt] = useState(null);     // data_source.schwab
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);   // {ok, text}
  const load = async () => {
    try { const r = await apiFetch("/api/data_source"); const d = await r.json(); setSt((d && d.schwab) || null); }
    catch (_) {}
  };
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, []);

  const needs = !!(st && st.needs_reauth);
  if (placement === "banner" && !needs) return null;   // banner only when broken

  const openLogin = async () => {
    setMsg(null);
    try {
      const r = await apiFetch("/api/broker/schwab/authorize_url");
      const d = await r.json();
      if (d.url) window.open(d.url, "_blank", "noopener");
      else setMsg({ ok: false, text: d.error || "Could not start Schwab login" });
    } catch (e) { setMsg({ ok: false, text: String(e) }); }
  };
  const complete = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await apiFetch("/api/broker/schwab/exchange", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ redirect_url: url }),
      });
      const d = await r.json();
      if (d.ok) { setMsg({ ok: true, text: "Schwab reconnected ✓" }); setUrl(""); load(); }
      else setMsg({ ok: false, text: d.error || "Reconnect failed" });
    } catch (e) { setMsg({ ok: false, text: String(e) }); }
    setBusy(false);
  };

  const connected = !!(st && st.configured && !needs);
  const cls = placement === "banner" ? "schwab-reauth schwab-banner" : "card schwab-reauth";
  return (
    <div className={cls}>
      <div className="schwab-reauth-head">
        <span className="schwab-reauth-title">Schwab connection</span>
        <span className={`schwab-dot ${connected ? "ok" : needs ? "bad" : "warn"}`}>
          {connected ? "Connected" : needs ? "Disconnected — re-authorize" : "Checking…"}
        </span>
      </div>
      <ol className="schwab-steps">
        <li>
          <button className="scan-run-btn" onClick={openLogin}>Open Schwab login</button>
          <span className="schwab-hint"> log in &amp; approve. Your browser lands on a <b>127.0.0.1</b> page that won't load — that's expected.</span>
        </li>
        <li>
          Copy that full URL and paste it here:
          <div className="schwab-paste">
            <input value={url} onChange={e => setUrl(e.target.value)}
                   placeholder="https://127.0.0.1:8182/?code=…" />
            <button className="scan-run-btn" onClick={complete} disabled={busy || !url}>{busy ? "…" : "Complete"}</button>
          </div>
        </li>
      </ol>
      {msg && <div className={`schwab-msg ${msg.ok ? "ok" : "bad"}`}>{msg.text}</div>}
      {connected && st && st.refresh_remaining_days != null && (
        <div className="schwab-note">Re-authorization will be needed again within ~7 days.</div>
      )}
    </div>
  );
}

// News tab shell: headlines by default (so the News tab opens on the news),
// with the company profile tucked behind a toggle so it has its own view.
function NewsHub({ apiFetch, ticker, companyName }) {
  const [view, setView] = useState("news");   // news | profile
  return (
    <div className="newshub">
      <div className="seg newshub-seg">
        <button className={view === "news" ? "active" : ""} onClick={() => setView("news")}>Headlines</button>
        <button className={view === "profile" ? "active" : ""} onClick={() => setView("profile")}>Profile</button>
      </div>
      {view === "news"
        ? <NewsCard apiFetch={apiFetch} ticker={ticker} companyName={companyName} />
        : <StockProfileCard apiFetch={apiFetch} ticker={ticker} alwaysShow />}
    </div>
  );
}

// Top-of-app news ticker tape — the user's Finviz Elite feed. Hides itself
// entirely until FINVIZ_AUTH_TOKEN is configured and headlines arrive, so it
// never shows an empty strip. Headlines scroll right-to-left; hover pauses.
function NewsTicker({ apiFetch, onSwitchTicker, placement }) {
  const atBottom = placement === "bottom";
  const [items, setItems] = useState([]);
  const [quotes, setQuotes] = useState({});   // SYM -> {last, chg}
  const stackRef = useRef(null);

  useEffect(() => {
    let stop = false, timer = null;
    const tick = async () => {
      try {
        const r = await apiFetch("/api/finviz_news?limit=60");
        const d = await r.json();
        if (!stop) setItems(Array.isArray(d && d.items) ? d.items : []);
      } catch (_) { /* keep last items */ }
      if (!stop) timer = setTimeout(tick, 60000);
    };
    tick();
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, []);

  // Tickers mentioned across the headlines (Finviz tags them, comma-joined).
  const symbols = useMemo(() => {
    const seen = new Set(), out = [];
    for (const it of items) {
      for (const raw of String(it.ticker || "").split(/[,\s]+/)) {
        const s = raw.toUpperCase().trim();
        if (s && /^[A-Z][A-Z.\-]{0,5}$/.test(s) && !seen.has(s)) { seen.add(s); out.push(s); }
      }
      if (out.length >= 40) break;
    }
    return out;
  }, [items]);

  // Live quotes for the mentioned tickers — Bloomberg/CNBC-style tape below.
  useEffect(() => {
    let stop = false, timer = null;
    if (!symbols.length) { setQuotes({}); return; }
    const tick = async () => {
      try {
        const next = {};
        for (let i = 0; i < symbols.length; i += 25) {
          const batch = symbols.slice(i, i + 25);
          const r = await apiFetch(`/api/quote?tickers=${batch.join(",")}`);
          const d = await r.json();
          const res = (d && d.results) || {};
          for (const s of batch) if (res[s]) next[s] = { last: res[s].last, chg: res[s].change_pct };
        }
        if (!stop) setQuotes(next);
      } catch (_) {}
      if (!stop) timer = setTimeout(tick, 30000);
    };
    tick();
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, [symbols]);

  // Keep the tab bar parked flush against the bottom of the (sticky) stack so
  // there's no transparent seam between them — the stack's own padding-bottom
  // provides the visual gap. 0 when nothing renders. Measured each render.
  useEffect(() => {
    // Only the TOP placement drives --mn-h (the tab bar parks under it). The
    // bottom copy is a fixed footer on desktop — publish its height so the page
    // and the side rails reserve room and never hide behind it.
    if (atBottom) {
      document.documentElement.style.setProperty("--mn-h", "0px");
      const el = stackRef.current;
      document.documentElement.style.setProperty("--mn-footer-h", el ? `${el.offsetHeight + 12}px` : "0px");
      return;
    }
    const el = stackRef.current;
    const h = el ? el.offsetHeight : 0;
    document.documentElement.style.setProperty("--mn-h", h ? `${h}px` : "0px");
    return () => { document.documentElement.style.setProperty("--mn-h", "0px"); };
  });

  if (!items.length) return null;  // unconfigured / empty → no strip

  const dur = Math.max(55, items.length * 6.5);
  const Seq = ({ hidden }) => (
    <div className="nt-seq" aria-hidden={hidden || undefined}>
      {items.map((it, i) => (
        <a key={i} className="nt-item" href={it.url} target="_blank" rel="noopener noreferrer"
           title={`${it.source || ""}${it.date ? " · " + it.date : ""}`}>
          {it.ticker ? <span className="nt-tkr">{it.ticker}</span> : null}
          {it.source ? <span className="nt-src">{it.source}</span> : null}
          <span className="nt-ttl">{it.title}</span>
          <span className="nt-sep">●</span>
        </a>
      ))}
    </div>
  );

  const qsyms = symbols.filter(s => quotes[s] && quotes[s].last != null);
  const qdur = Math.max(31, qsyms.length * 3.0);   // a touch faster than the news tape
  const QSeq = ({ hidden }) => (
    <div className="nt-seq" aria-hidden={hidden || undefined}>
      {qsyms.map((s, i) => {
        const q = quotes[s], up = (q.chg || 0) >= 0;
        return (
          <button key={s + i} className="mnq-item" onClick={() => onSwitchTicker && onSwitchTicker(s)}
                  title={`Open ${s}`}>
            <b className="mnq-sym">{s}</b>
            <span className="mnq-px">${Number(q.last).toFixed(2)}</span>
            <span className={`mnq-chg ${up ? "up" : "down"}`}>
              {q.chg == null ? "" : `${up ? "▲" : "▼"} ${Math.abs(q.chg).toFixed(2)}%`}
            </span>
          </button>
        );
      })}
    </div>
  );

  return (
    <div className={`mn-stack${atBottom ? " mn-bottom" : ""}`} ref={stackRef} aria-label="Market news and ticker tape">
      <div className="newsticker" aria-label="Market news feed">
        <div className="nt-badge" title="Live market news feed"><span>Market</span><span>News</span></div>
        <div className="nt-viewport">
          <div className="nt-track" style={{ animationDuration: `${dur}s` }}>
            <Seq />
            <Seq hidden />
          </div>
        </div>
      </div>
      {qsyms.length > 0 && (
        <div className="newsticker mnq-bar" aria-label="Mentioned tickers">
          <div className="nt-badge mnq-badge" title="Live quotes for tickers in the news"><span>Tickers</span></div>
          <div className="nt-viewport">
            <div className="nt-track" style={{ animationDuration: `${qdur}s` }}>
              <QSeq />
              <QSeq hidden />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Most-frequent Tag among a rail's current rows → {tag, n} (or null). Powers
// the sub-header that tells you which group is dominating the list right now.
function lrailTopTag(rows) {
  const counts = {};
  for (const r of (rows || [])) {
    const t = r && r.tag;
    if (t) counts[t] = (counts[t] || 0) + 1;
  }
  let best = null, n = 0;
  for (const t in counts) if (counts[t] > n) { n = counts[t]; best = t; }
  return best ? { tag: best, n } : null;
}

// Left-margin vertical ticker (wide screens only): watchlist names closest to
// their 52-week high, scrolling top→bottom. Ticker · price · change · %-from-52WH.
function LeftRail52W({ apiFetch, onSwitchTicker }) {
  const [scanRows, setScanRows] = useState([]);
  const [liveQ, setLiveQ] = useState({});   // symbol -> {last, chg}
  const [owned, setOwned] = useState(() => new Set()); // Schwab-held symbols
  const [vpH, setVpH] = useState(0);
  const vpRef = useRef(null);
  // Pull the scan board (cheap, cached) for 52W-high context: high_52w plus a
  // candidate set near the high. We display LIVE price/change (below) so a
  // stale or corrupt scan `last` never shows a price the stock never traded.
  useEffect(() => {
    let stop = false, t = null;
    const load = async () => {
      try {
        const d = await sharedJson(apiFetch, "/api/watchlist_table", 30000);
        const all = (d && d.rows) || [];
        // Candidates: anything the scan thinks is within ~6% of its high (a
        // touch wider than the display threshold so a live intraday push to a
        // new high still qualifies once live prices arrive).
        const near = all
          .filter(x => x.from_52wh != null && x.from_52wh >= -6 && x.high_52w != null)
          .sort((a, b) => b.from_52wh - a.from_52wh)
          .slice(0, 60);
        if (!stop) setScanRows(near);
      } catch (_) {}
      if (!stop) t = setTimeout(load, 60000);
    };
    load();
    return () => { stop = true; if (t) clearTimeout(t); };
  }, []);

  // Owned symbols from the Schwab portfolio (cached server-side ~5 min).
  useEffect(() => {
    let stop = false, t = null;
    const grab = async () => {
      try {
        const d = await sharedJson(apiFetch, "/api/broker/owned", 120000);
        if (!stop && d && Array.isArray(d.symbols)) {
          setOwned(new Set(d.symbols.map(s => String(s).toUpperCase())));
        }
      } catch (_) { /* highlight is best-effort */ }
      if (!stop) t = setTimeout(grab, 5 * 60 * 1000);
    };
    grab();
    return () => { stop = true; if (t) clearTimeout(t); };
  }, []);

  // Live-quote overlay for the candidate set (batched, pauses when hidden).
  const candKey = scanRows.map(r => r.symbol).join(",");
  useEffect(() => {
    if (!candKey) return;
    const syms = candKey.split(",");
    let stop = false, t = null;
    const poll = async () => {
      if (!document.hidden) {
        for (let i = 0; i < syms.length && !stop; i += 25) {
          const batch = syms.slice(i, i + 25);
          try {
            const r = await apiFetch(`/api/quote?tickers=${encodeURIComponent(batch.join(","))}`);
            if (!r.ok) continue;
            const j = await r.json();
            if (stop) return;
            const res = j.results || {};
            setLiveQ(prev => {
              const next = { ...prev };
              for (const s of batch) {
                const q = res[s];
                if (q && q.last != null) next[s] = { last: q.last, chg: q.change_pct != null ? q.change_pct : null };
              }
              return next;
            });
          } catch (_) {}
        }
      }
      if (!stop) t = setTimeout(poll, 30000);
    };
    poll();
    return () => { stop = true; if (t) clearTimeout(t); };
  }, [candKey]);

  // Build display rows: prefer the live price/change; recompute "% from 52W
  // high" against the live price so the rail reflects reality intraday and a
  // bad scan bar can't keep a stock pinned at a high it isn't near. Then apply
  // the real display threshold (within 3% of the high) and sort by closeness.
  const rows = useMemo(() => {
    const out = [];
    for (const r of scanRows) {
      const q = liveQ[r.symbol];
      const last = q && q.last != null ? q.last : r.last;
      if (last == null) continue;
      const chg = q && q.chg != null ? q.chg : r.change;
      const hi = r.high_52w;
      const from = hi ? Math.round((last / hi - 1) * 1000) / 10 : r.from_52wh;
      if (from == null || from < -3) continue;
      out.push({ ...r, _last: last, _chg: chg, _from: from });
    }
    out.sort((a, b) => b._from - a._from);
    return out.slice(0, 40);
  }, [scanRows, liveQ]);

  // Measure the (full-height) viewport. Each list copy is forced to AT LEAST
  // this height (min-height + space-evenly), so the rail always fills top to
  // bottom AND the same symbol never shows twice (one copy = one viewport).
  useEffect(() => {
    const measure = () => { if (vpRef.current) setVpH(vpRef.current.offsetHeight); };
    measure();
    window.addEventListener("resize", measure);
    const id = setTimeout(measure, 80);
    return () => { window.removeEventListener("resize", measure); clearTimeout(id); };
  }, [rows]);

  if (!rows.length) return null;
  const colH = Math.max(vpH || 0, rows.length * 62);
  const dur = Math.max(16, Math.round(colH / 35));   // ~35 px/s (a hair slower)
  const topTag = lrailTopTag(rows);
  const Col = ({ hidden }) => (
    <div className="lr-col" aria-hidden={hidden || undefined} style={vpH ? { minHeight: `${vpH}px` } : undefined}>
      {rows.map((r, i) => {
        const isOwned = owned.has(String(r.symbol).toUpperCase());
        return (
        <button key={i} className={`lr-item${isOwned ? " owned" : ""}`} onClick={() => onSwitchTicker && onSwitchTicker(r.symbol)}
                title={`${r.company || r.symbol} — ${r._from >= 0 ? "at" : Math.abs(r._from) + "% below"} 52-week high ($${r.high_52w != null ? r.high_52w : "?"})${isOwned ? " · you own this (Schwab)" : ""}`}>
          <span className="lr-line1">
            <span className="lr-sym">{r.symbol}</span>
            <span className="lr-px">${Number(r._last).toFixed(2)}</span>
          </span>
          <span className="lr-line2">
            <span className={`lr-chg ${(r._chg || 0) >= 0 ? "up" : "down"}`}>
              {r._chg == null ? "—" : `${r._chg >= 0 ? "+" : ""}${Number(r._chg).toFixed(2)}%`}
            </span>
            <span className="lr-52" title="% from 52-week high">{r._from >= 0 ? "HIGH" : `${r._from}%`}</span>
          </span>
          <span className="lr-line3" title={r.tag ? `Tag: ${r.tag}` : "No tag"}>
            {r.tag || "—"}
          </span>
        </button>
        );
      })}
    </div>
  );
  return (
    <div className="lrail rrail" aria-label="Watchlist names near 52-week high">
      <div className="lrail-title" title="Watchlist stocks within 3% of their 52-week high">NEAR 52W HIGH</div>
      {topTag && (
        <div className="lrail-subtag" title={`Most-represented tag near the 52-week high right now: ${topTag.tag} (${topTag.n})`}>
          {topTag.tag} · {topTag.n}
        </div>
      )}
      <div className="lrail-vp" ref={vpRef}>
        <div className="lrail-track" style={{ animationDuration: `${dur}s` }}>
          <Col inner />
          <Col hidden />
        </div>
      </div>
    </div>
  );
}

// Twin of LeftRail52W, but for stocks AT or near TODAY'S session high. The
// server (/api/daily_highs) does the heavy lifting: it batches live quotes for
// the whole watchlist, computes "% from today's high", filters + ranks, and
// merges in each symbol's Tag. Everything else (owned-yellow highlight, the
// 3-line layout, the seamless scroll) mirrors the 52W rail exactly.
function LeftRailDailyHigh({ apiFetch, onSwitchTicker }) {
  const [rows, setRows] = useState([]);
  const [owned, setOwned] = useState(() => new Set());
  const [vpH, setVpH] = useState(0);
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  const vpRef = useRef(null);
  // Tick once every 5s so the "time since it hit the high" ages live without
  // re-fetching (CSS scroll animation keeps running — only text changes).
  useEffect(() => {
    const id = setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 5000);
    return () => clearInterval(id);
  }, []);
  // Compact "time since last at the high": 35s, 5m, 2h.
  const ageStr = (ts) => {
    if (!ts) return "";
    const s = Math.max(0, nowSec - Math.floor(ts));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    return `${Math.floor(s / 3600)}h`;
  };
  useEffect(() => {
    let stop = false, t = null;
    const load = async () => {
      try {
        const r = await apiFetch("/api/daily_highs");
        const d = await r.json();
        if (!stop) setRows((d && d.rows) || []);
      } catch (_) {}
      if (!stop) t = setTimeout(load, 30000);
    };
    load();
    return () => { stop = true; if (t) clearTimeout(t); };
  }, []);
  useEffect(() => {
    let stop = false, t = null;
    const grab = async () => {
      try {
        const d = await sharedJson(apiFetch, "/api/broker/owned", 120000);
        if (!stop && d && Array.isArray(d.symbols)) {
          setOwned(new Set(d.symbols.map(s => String(s).toUpperCase())));
        }
      } catch (_) {}
      if (!stop) t = setTimeout(grab, 5 * 60 * 1000);
    };
    grab();
    return () => { stop = true; if (t) clearTimeout(t); };
  }, []);
  useEffect(() => {
    const measure = () => { if (vpRef.current) setVpH(vpRef.current.offsetHeight); };
    measure();
    window.addEventListener("resize", measure);
    const id = setTimeout(measure, 80);
    return () => { window.removeEventListener("resize", measure); clearTimeout(id); };
  }, [rows]);

  // Keep the rail visible even with nothing at the daily high yet (e.g.
  // pre-market, before the 9:30 open the session high isn't set so no name
  // qualifies). Returning null made the whole rail vanish, which read as a
  // missing feature — show the frame with a short note instead.
  if (!rows.length) {
    return (
      <div className="lrail lrail--daily rrail rrail--daily" aria-label="Watchlist names at today's daily high">
        <div className="lrail-title lrail-title--daily" title="Watchlist stocks at or within 1% of today's session high">DAILY HIGH</div>
        <div className="lrail-empty" title="Nothing is at or near its intraday high right now — names appear once the session is underway.">No names at the daily high yet — they show up after the open.</div>
      </div>
    );
  }
  const colH = Math.max(vpH || 0, rows.length * 62);
  const dur = Math.max(16, Math.round(colH / 35));
  const topTag = lrailTopTag(rows);
  const Col = ({ hidden }) => (
    <div className="lr-col" aria-hidden={hidden || undefined} style={vpH ? { minHeight: `${vpH}px` } : undefined}>
      {rows.map((r, i) => {
        const isOwned = owned.has(String(r.symbol).toUpperCase());
        const from = r.from_high;
        return (
        <button key={i} className={`lr-item${isOwned ? " owned" : ""}`} onClick={() => onSwitchTicker && onSwitchTicker(r.symbol)}
                title={`${r.company || r.symbol} — ${from >= 0 ? "at" : Math.abs(from) + "% below"} today's high ($${r.day_high != null ? Number(r.day_high).toFixed(2) : "?"})${isOwned ? " · you own this (Schwab)" : ""}`}>
          <span className="lr-line1">
            <span className="lr-sym">{r.symbol}</span>
            <span className="lr-dash">-</span>
            <span className="lr-px">${Number(r.last).toFixed(2)}</span>
          </span>
          <span className="lr-line2">
            <span className={`lr-chg ${(r.change || 0) >= 0 ? "up" : "down"}`}>
              {r.change == null ? "—" : `${r.change >= 0 ? "+" : ""}${Number(r.change).toFixed(2)}%`}
            </span>
            <span className="lr-age" title="Time since it last touched today's high">{ageStr(r.hit_ts)}</span>
          </span>
          <span className="lr-line3" title={r.tag ? `Tag: ${r.tag}` : "No tag"}>
            {r.tag || "—"}
          </span>
        </button>
        );
      })}
    </div>
  );
  return (
    <div className="lrail lrail--daily rrail rrail--daily" aria-label="Watchlist names at today's daily high">
      <div className="lrail-title lrail-title--daily" title="Watchlist stocks at or within 1% of today's session high">DAILY HIGH</div>
      {topTag && (
        <div className="lrail-subtag" title={`Most-represented tag at the daily high right now: ${topTag.tag} (${topTag.n})`}>
          {topTag.tag} · {topTag.n}
        </div>
      )}
      <div className="lrail-vp" ref={vpRef}>
        <div className="lrail-track" style={{ animationDuration: `${dur}s` }}>
          <Col inner />
          <Col hidden />
        </div>
      </div>
    </div>
  );
}

// Right-side mirror of LeftRail52W: watchlist names closest to their 52-week
// LOW. `from_52wl` is how far ABOVE the low we are (>=0; 0 = at a new low).
function RightRail52WLow({ apiFetch, onSwitchTicker }) {
  const [scanRows, setScanRows] = useState([]);
  const [liveQ, setLiveQ] = useState({});
  const [owned, setOwned] = useState(() => new Set());
  const [vpH, setVpH] = useState(0);
  const vpRef = useRef(null);
  useEffect(() => {
    let stop = false, t = null;
    const load = async () => {
      try {
        const d = await sharedJson(apiFetch, "/api/watchlist_table", 30000);
        const all = (d && d.rows) || [];
        // Candidates: within ~6% above the 52W low (wider than the 3% display
        // threshold so a live intraday drop to a new low still qualifies).
        const near = all
          .filter(x => x.from_52wl != null && x.from_52wl <= 6 && x.low_52w != null)
          .sort((a, b) => a.from_52wl - b.from_52wl)
          .slice(0, 60);
        if (!stop) setScanRows(near);
      } catch (_) {}
      if (!stop) t = setTimeout(load, 60000);
    };
    load();
    return () => { stop = true; if (t) clearTimeout(t); };
  }, []);
  useEffect(() => {
    let stop = false, t = null;
    const grab = async () => {
      try {
        const d = await sharedJson(apiFetch, "/api/broker/owned", 120000);
        if (!stop && d && Array.isArray(d.symbols)) {
          setOwned(new Set(d.symbols.map(s => String(s).toUpperCase())));
        }
      } catch (_) {}
      if (!stop) t = setTimeout(grab, 5 * 60 * 1000);
    };
    grab();
    return () => { stop = true; if (t) clearTimeout(t); };
  }, []);
  const candKey = scanRows.map(r => r.symbol).join(",");
  useEffect(() => {
    if (!candKey) return;
    const syms = candKey.split(",");
    let stop = false, t = null;
    const poll = async () => {
      if (!document.hidden) {
        for (let i = 0; i < syms.length && !stop; i += 25) {
          const batch = syms.slice(i, i + 25);
          try {
            const r = await apiFetch(`/api/quote?tickers=${encodeURIComponent(batch.join(","))}`);
            if (!r.ok) continue;
            const j = await r.json();
            if (stop) return;
            const res = j.results || {};
            setLiveQ(prev => {
              const next = { ...prev };
              for (const s of batch) {
                const q = res[s];
                if (q && q.last != null) next[s] = { last: q.last, chg: q.change_pct != null ? q.change_pct : null };
              }
              return next;
            });
          } catch (_) {}
        }
      }
      if (!stop) t = setTimeout(poll, 30000);
    };
    poll();
    return () => { stop = true; if (t) clearTimeout(t); };
  }, [candKey]);
  const rows = useMemo(() => {
    const out = [];
    for (const r of scanRows) {
      const q = liveQ[r.symbol];
      const last = q && q.last != null ? q.last : r.last;
      if (last == null) continue;
      const chg = q && q.chg != null ? q.chg : r.change;
      const lo = r.low_52w;
      const from = lo ? Math.round((last / lo - 1) * 1000) / 10 : r.from_52wl;
      if (from == null || from > 3) continue;
      out.push({ ...r, _last: last, _chg: chg, _from: from });
    }
    out.sort((a, b) => a._from - b._from);
    return out.slice(0, 40);
  }, [scanRows, liveQ]);
  useEffect(() => {
    const measure = () => { if (vpRef.current) setVpH(vpRef.current.offsetHeight); };
    measure();
    window.addEventListener("resize", measure);
    const id = setTimeout(measure, 80);
    return () => { window.removeEventListener("resize", measure); clearTimeout(id); };
  }, [rows]);

  if (!rows.length) return null;
  const colH = Math.max(vpH || 0, rows.length * 62);
  const dur = Math.max(16, Math.round(colH / 35));
  const topTag = lrailTopTag(rows);
  const Col = ({ hidden }) => (
    <div className="lr-col" aria-hidden={hidden || undefined} style={vpH ? { minHeight: `${vpH}px` } : undefined}>
      {rows.map((r, i) => {
        const isOwned = owned.has(String(r.symbol).toUpperCase());
        return (
        <button key={i} className={`lr-item${isOwned ? " owned" : ""}`} onClick={() => onSwitchTicker && onSwitchTicker(r.symbol)}
                title={`${r.company || r.symbol} — ${r._from <= 0 ? "at" : r._from + "% above"} 52-week low ($${r.low_52w != null ? r.low_52w : "?"})${isOwned ? " · you own this (Schwab)" : ""}`}>
          <span className="lr-line1">
            <span className="lr-sym">{r.symbol}</span>
            <span className="lr-px">${Number(r._last).toFixed(2)}</span>
          </span>
          <span className="lr-line2">
            <span className={`lr-chg ${(r._chg || 0) >= 0 ? "up" : "down"}`}>
              {r._chg == null ? "—" : `${r._chg >= 0 ? "+" : ""}${Number(r._chg).toFixed(2)}%`}
            </span>
            <span className="lr-52" title="% above 52-week low">{r._from <= 0 ? "LOW" : `+${r._from}%`}</span>
          </span>
          <span className="lr-line3" title={r.tag ? `Tag: ${r.tag}` : "No tag"}>
            {r.tag || "—"}
          </span>
        </button>
        );
      })}
    </div>
  );
  return (
    <div className="lrail" aria-label="Watchlist names near 52-week low">
      <div className="lrail-title lrail-title--low" title="Watchlist stocks within 3% of their 52-week low">NEAR 52W LOW</div>
      {topTag && (
        <div className="lrail-subtag lrail-subtag--low" title={`Most-represented tag near the 52-week low right now: ${topTag.tag} (${topTag.n})`}>
          {topTag.tag} · {topTag.n}
        </div>
      )}
      <div className="lrail-vp" ref={vpRef}>
        <div className="lrail-track" style={{ animationDuration: `${dur}s` }}>
          <Col inner />
          <Col hidden />
        </div>
      </div>
    </div>
  );
}

// Right-side mirror of LeftRailDailyHigh: stocks AT or near TODAY'S session LOW.
function RightRailDailyLow({ apiFetch, onSwitchTicker }) {
  const [rows, setRows] = useState([]);
  const [owned, setOwned] = useState(() => new Set());
  const [vpH, setVpH] = useState(0);
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  const vpRef = useRef(null);
  useEffect(() => {
    const id = setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 5000);
    return () => clearInterval(id);
  }, []);
  const ageStr = (ts) => {
    if (!ts) return "";
    const s = Math.max(0, nowSec - Math.floor(ts));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    return `${Math.floor(s / 3600)}h`;
  };
  useEffect(() => {
    let stop = false, t = null;
    const load = async () => {
      try {
        const r = await apiFetch("/api/daily_lows");
        const d = await r.json();
        if (!stop) setRows((d && d.rows) || []);
      } catch (_) {}
      if (!stop) t = setTimeout(load, 30000);
    };
    load();
    return () => { stop = true; if (t) clearTimeout(t); };
  }, []);
  useEffect(() => {
    let stop = false, t = null;
    const grab = async () => {
      try {
        const d = await sharedJson(apiFetch, "/api/broker/owned", 120000);
        if (!stop && d && Array.isArray(d.symbols)) {
          setOwned(new Set(d.symbols.map(s => String(s).toUpperCase())));
        }
      } catch (_) {}
      if (!stop) t = setTimeout(grab, 5 * 60 * 1000);
    };
    grab();
    return () => { stop = true; if (t) clearTimeout(t); };
  }, []);
  useEffect(() => {
    const measure = () => { if (vpRef.current) setVpH(vpRef.current.offsetHeight); };
    measure();
    window.addEventListener("resize", measure);
    const id = setTimeout(measure, 80);
    return () => { window.removeEventListener("resize", measure); clearTimeout(id); };
  }, [rows]);

  // Keep the rail visible even with nothing at the daily low yet (pre-market
  // the session low isn't set, so no name qualifies). Returning null made the
  // whole rail vanish — show the frame with a short note instead.
  if (!rows.length) {
    return (
      <div className="lrail lrail--daily" aria-label="Watchlist names at today's daily low">
        <div className="lrail-title lrail-title--low lrail-title--lowdaily" title="Watchlist stocks at or within 1% of today's session low">DAILY LOW</div>
        <div className="lrail-empty" title="Nothing is at or near its intraday low right now — names appear once the session is underway.">No names at the daily low yet — they show up after the open.</div>
      </div>
    );
  }
  const colH = Math.max(vpH || 0, rows.length * 62);
  const dur = Math.max(16, Math.round(colH / 35));
  const topTag = lrailTopTag(rows);
  const Col = ({ hidden }) => (
    <div className="lr-col" aria-hidden={hidden || undefined} style={vpH ? { minHeight: `${vpH}px` } : undefined}>
      {rows.map((r, i) => {
        const isOwned = owned.has(String(r.symbol).toUpperCase());
        const from = r.from_low;
        return (
        <button key={i} className={`lr-item${isOwned ? " owned" : ""}`} onClick={() => onSwitchTicker && onSwitchTicker(r.symbol)}
                title={`${r.company || r.symbol} — ${from <= 0 ? "at" : from + "% above"} today's low ($${r.day_low != null ? Number(r.day_low).toFixed(2) : "?"})${isOwned ? " · you own this (Schwab)" : ""}`}>
          <span className="lr-line1">
            <span className="lr-sym">{r.symbol}</span>
            <span className="lr-dash">-</span>
            <span className="lr-px">${Number(r.last).toFixed(2)}</span>
          </span>
          <span className="lr-line2">
            <span className={`lr-chg ${(r.change || 0) >= 0 ? "up" : "down"}`}>
              {r.change == null ? "—" : `${r.change >= 0 ? "+" : ""}${Number(r.change).toFixed(2)}%`}
            </span>
            <span className="lr-age" title="Time since it last touched today's low">{ageStr(r.hit_ts)}</span>
          </span>
          <span className="lr-line3" title={r.tag ? `Tag: ${r.tag}` : "No tag"}>
            {r.tag || "—"}
          </span>
        </button>
        );
      })}
    </div>
  );
  return (
    <div className="lrail lrail--daily" aria-label="Watchlist names at today's daily low">
      <div className="lrail-title lrail-title--low lrail-title--lowdaily" title="Watchlist stocks at or within 1% of today's session low">DAILY LOW</div>
      {topTag && (
        <div className="lrail-subtag lrail-subtag--low" title={`Most-represented tag at the daily low right now: ${topTag.tag} (${topTag.n})`}>
          {topTag.tag} · {topTag.n}
        </div>
      )}
      <div className="lrail-vp" ref={vpRef}>
        <div className="lrail-track" style={{ animationDuration: `${dur}s` }}>
          <Col inner />
          <Col hidden />
        </div>
      </div>
    </div>
  );
}

// Memoize the heavy, self-contained ticker cards so unrelated App state
// changes (hovers, sidebar, other tabs) don't re-render them. Their props
// (apiFetch, switchTicker, ticker) are stable identities from App.
const SwingPatternCardM = React.memo(SwingPatternCard);
const NewsCardM = React.memo(NewsCard);
const ScreenersHubM = React.memo(ScreenersHub);
Object.assign(window, {
  SwingPatternCard: SwingPatternCardM, NewsCard: NewsCardM, ScreenersHub: ScreenersHubM,
});
// Heavy, prop-driven cards are wrapped in React.memo so they don't re-render
// every time the App re-renders for unrelated state (settings toggles, the 30s
// staleness tick, sibling-card updates). memo only ever SKIPS a render when
// props are shallow-equal, so it's safe for these pure components; small
// helpers/rows are left unwrapped.
// ── Market Breadth: grouped HOD/LOD rotation scanner ──────────────────────
// A pure derived view: reads a per-symbol snapshot (price + day/52w extremes +
// group meta) and aggregates it by sector / industry / tag to show which groups
// are broadly making new highs vs lows (rotation strength). No API calls of its
// own beyond fetching that one snapshot.
const BREADTH_KEY = "jerry_breadth_v1";
const BREADTH_THRESH = 0.0015;   // 0.15% — "at/near" tolerance for HOD/LOD
const BREADTH_COLS = [
  { k: "name", label: "Group", str: true, title: "Sector / industry / tag" },
  { k: "strength", label: "Str", title: "Rotation strength: (HOD−LOD)/total × 100, −100…+100" },
  { k: "hod", label: "HOD", title: "Names at/near their high of day" },
  { k: "lod", label: "LOD", title: "Names at/near their low of day" },
  { k: "net", label: "Net", title: "HOD − LOD" },
  { k: "hodPct", label: "HOD%", title: "Share of the group at its high of day" },
  { k: "lodPct", label: "LOD%", title: "Share of the group at its low of day" },
  { k: "total", label: "Stk", title: "Stocks in the group" },
  { k: "h52", label: "52H", title: "Names printing a new 52-week high" },
  { k: "l52", label: "52L", title: "Names printing a new 52-week low" },
];

function aggregateBreadth(livePrices, stockMeta, view) {
  const groups = {};
  for (const sym in livePrices) {
    if (sym[0] === "/" || sym[0] === "^") continue;         // futures / indices
    const p = livePrices[sym];
    if (!p || !(p.price > 0)) continue;
    const meta = stockMeta[sym];
    if (!meta) continue;
    const key = String(meta[view] || "").trim();
    if (!key || key === "ETF") continue;
    const isHOD = p.dayHigh > 0 && p.price >= p.dayHigh * (1 - BREADTH_THRESH);
    const isLOD = p.dayLow > 0 && p.price <= p.dayLow * (1 + BREADTH_THRESH);
    const is52H = p.high52 > 0 && p.dayHigh > 0 && p.dayHigh >= p.high52 * 0.998;
    const is52L = p.low52 > 0 && p.dayLow > 0 && p.dayLow <= p.low52 * 1.002;
    const g = groups[key] || (groups[key] = {
      name: key, total: 0, hod: 0, lod: 0, h52: 0, l52: 0, hodStocks: [], lodStocks: [] });
    g.total++;
    if (is52H) g.h52++;
    if (is52L) g.l52++;
    const rec = { sym, name: meta.name || sym, price: p.price, chg: p.changePct,
                  mktCap: p.marketCap, is52H, is52L };
    if (isHOD) { g.hod++; g.hodStocks.push(rec); }
    if (isLOD) { g.lod++; g.lodStocks.push(rec); }
  }
  return Object.values(groups).map(g => ({
    ...g, net: g.hod - g.lod,
    hodPct: g.total ? g.hod / g.total : 0,
    lodPct: g.total ? g.lod / g.total : 0,
    strength: g.total ? Math.round((g.hod - g.lod) / g.total * 100) : 0,
  }));
}

function strengthTier(s) {
  return s > 30 ? "hh" : s > 10 ? "h" : s < -30 ? "ll" : s < -10 ? "l" : "n";
}

function BreadthStockList({ title, tone, stocks, sort, setSort, onLoadTicker }) {
  const cols = [
    { k: "sym", label: "Sym", str: true }, { k: "name", label: "Name", str: true },
    { k: "price", label: "Price" }, { k: "chg", label: "Chg%" }, { k: "mktCap", label: "Cap" },
  ];
  const rows = stocks.slice().sort((a, b) => {
    const c = cols.find(x => x.k === sort.k);
    let av = a[sort.k], bv = b[sort.k], r;
    r = c && c.str ? String(av || "").localeCompare(String(bv || "")) : (av || 0) - (bv || 0);
    return sort.dir === "desc" ? -r : r;
  });
  const th = (c) => (
    <th key={c.k} className={c.str ? "" : "num"} onClick={() =>
      setSort(sort.k === c.k ? { k: c.k, dir: sort.dir === "desc" ? "asc" : "desc" }
                             : { k: c.k, dir: c.str ? "asc" : "desc" })}
      title="Sort">{c.label}{sort.k === c.k ? (sort.dir === "desc" ? " ↓" : " ↑") : ""}</th>
  );
  return (
    <div className={`mb-drill-col mb-${tone}`}>
      <div className="mb-drill-h">{title} <span className="mb-drill-n">{stocks.length}</span></div>
      <div className="scan-table-wrap">
        <table className="scan-table mb-drill-table">
          <thead><tr>{cols.map(th)}</tr></thead>
          <tbody>
            {rows.map(s => (
              <tr key={s.sym} className={`scan-row${s.is52H || s.is52L ? " mb-52" : ""}`}
                  onClick={() => onLoadTicker && onLoadTicker(s.sym)} title={`Load ${s.sym}`}>
                <td>
                  <b>{s.sym}</b>
                  {s.is52H && <span className="mb-badge mb-badge-h" title="New 52-week high">52H</span>}
                  {s.is52L && <span className="mb-badge mb-badge-l" title="New 52-week low">52L</span>}
                </td>
                <td className="mb-name">{s.name}</td>
                <td className="num">${s.price != null ? Number(s.price).toFixed(2) : "—"}</td>
                <td className={`num ${(s.chg || 0) >= 0 ? "up" : "down"}`}>{s.chg == null ? "—" : `${s.chg >= 0 ? "+" : ""}${Number(s.chg).toFixed(2)}%`}</td>
                <td className="num">{fmtMktCap(s.mktCap)}</td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={5} className="mb-empty-cell">none</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MarketBreadthCard({ apiFetch, onLoadTicker }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const saved = (() => { try { return JSON.parse(localStorage.getItem(BREADTH_KEY)) || {}; } catch (_) { return {}; } })();
  const [view, setView] = useState(saved.view || "sector");
  const [sortCol, setSortCol] = useState(saved.sortCol || "net");
  const [sortDir, setSortDir] = useState(saved.sortDir || "desc");
  const [minStocks, setMinStocks] = useState(saved.minStocks || 3);
  const [drill, setDrill] = useState(null);
  const [hodSort, setHodSort] = useState({ k: "chg", dir: "desc" });
  const [lodSort, setLodSort] = useState({ k: "chg", dir: "asc" });

  // Deep-link from the context bar's rotation chips: clicking "Technology"
  // there opens THIS tab already drilled into Technology (sector view), and
  // scrolls the drill panel into view. Handles both the first mount (handoff
  // via window.__breadthDrill) and later clicks (custom event) since this
  // panel stays mounted once visited.
  useEffect(() => {
    const apply = (name) => {
      if (!name) return;
      setView("sector");
      setDrill(name);
      setTimeout(() => {
        try { document.querySelector(".mb-drill") && document.querySelector(".mb-drill").scrollIntoView({ behavior: "smooth", block: "start" }); } catch (_) {}
      }, 350);
    };
    apply(window.__breadthDrill);
    window.__breadthDrill = null;
    const h = (e) => apply(e.detail);
    window.addEventListener("breadth-drill", h);
    return () => window.removeEventListener("breadth-drill", h);
  }, []);

  useEffect(() => {
    let stop = false, t = null;
    const load = async () => {
      try {
        const r = await apiFetch("/api/market_breadth");
        const d = await r.json();
        if (!stop) { setData(d); setErr(d && d.error ? d.error : null); setLoading(false); }
      } catch (e) { if (!stop) { setErr(String(e)); setLoading(false); } }
      if (!stop) t = setTimeout(load, document.hidden ? 60000 : 20000);
    };
    load();
    const onVis = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { stop = true; if (t) clearTimeout(t); document.removeEventListener("visibilitychange", onVis); };
  }, []);

  useEffect(() => {   // persist settings (debounced ~500ms)
    const id = setTimeout(() => {
      try { localStorage.setItem(BREADTH_KEY, JSON.stringify({ view, sortCol, sortDir, minStocks })); } catch (_) {}
    }, 500);
    return () => clearTimeout(id);
  }, [view, sortCol, sortDir, minStocks]);

  const { livePrices, stockMeta } = useMemo(() => {
    const lp = {}, sm = {}, stocks = (data && data.stocks) || {};
    for (const sym in stocks) {
      const s = stocks[sym];
      lp[sym] = { price: s.price, dayHigh: s.dayHigh, dayLow: s.dayLow, high52: s.high52,
                  low52: s.low52, changePct: s.changePct, marketCap: s.marketCap };
      sm[sym] = { name: s.name, sector: s.sector, industry: s.industry, tag: s.tag };
    }
    return { livePrices: lp, stockMeta: sm };
  }, [data]);

  const groups = useMemo(() => aggregateBreadth(livePrices, stockMeta, view), [livePrices, stockMeta, view]);
  const filtered = useMemo(() => groups.filter(g => g.total >= minStocks), [groups, minStocks]);
  const sorted = useMemo(() => {
    const col = BREADTH_COLS.find(c => c.k === sortCol) || BREADTH_COLS[0];
    const arr = filtered.slice().sort((a, b) => {
      const av = a[sortCol], bv = b[sortCol];
      const r = col.str ? String(av || "").localeCompare(String(bv || "")) : (av || 0) - (bv || 0);
      return sortDir === "desc" ? -r : r;
    });
    return arr;
  }, [filtered, sortCol, sortDir]);
  const byStrength = useMemo(() =>
    filtered.slice().sort((a, b) => b.strength - a.strength), [filtered]);
  const totals = useMemo(() => {
    let H = 0, L = 0, T = 0;
    filtered.forEach(g => { H += g.hod; L += g.lod; T += g.total; });
    return { groups: filtered.length, hod: H, lod: L, total: T };
  }, [filtered]);
  const drillGroup = drill ? filtered.find(g => g.name === drill) : null;

  const setSort = (k) => {
    if (sortCol === k) setSortDir(sortDir === "desc" ? "asc" : "desc");
    else { setSortCol(k); setSortDir(BREADTH_COLS.find(c => c.k === k) && BREADTH_COLS.find(c => c.k === k).str ? "asc" : "desc"); }
  };
  const maxStr = Math.max(1, ...byStrength.map(g => Math.abs(g.strength)));

  if (loading) return <div className="card mb-card"><CardNote kind="loading">Reading breadth…</CardNote></div>;
  if (err && !filtered.length) return <div className="card mb-card"><CardNote kind="error" onRetry={() => { setLoading(true); setData(null); }}>Breadth unavailable — {err}</CardNote></div>;

  return (
    <div className="card mb-card">
      <div className="mb-head">
        <div>
          <div className="mb-title" title="Which groups are broadly making new highs vs lows right now — sector/industry rotation strength, live.">Market Breadth <span className="mb-sub">rotation by {view}</span></div>
          <div className="mb-summary">{totals.groups} groups · <b className="up">{totals.hod} HOD</b> · <b className="down">{totals.lod} LOD</b> · {totals.total} stocks</div>
        </div>
        <div className="mb-controls">
          <div className="mb-seg" role="tablist">
            {["sector", "industry", "tag"].map(v => (
              <button key={v} className={view === v ? "active" : ""} onClick={() => { setView(v); setDrill(null); }}
                      title={`Group by ${v}`}>{v[0].toUpperCase() + v.slice(1)}</button>
            ))}
          </div>
          <label className="mb-min" title="Hide groups with fewer than this many stocks">
            min <b>{minStocks}</b>
            <input type="range" min="1" max="10" value={minStocks}
                   onChange={e => setMinStocks(Number(e.target.value))} />
          </label>
        </div>
      </div>

      {/* Rotation strength bar chart — top 15 by strength */}
      <div className="mb-bars" title="Top 15 groups by rotation strength. Click a bar to drill in.">
        {byStrength.slice(0, 15).map(g => (
          <button key={g.name} className="mb-bar-row" onClick={() => setDrill(g.name)} title={`${g.name}: strength ${g.strength}`}>
            <span className="mb-bar-lbl">{g.name}</span>
            <span className="mb-bar-track">
              <span className={`mb-bar-fill ${g.strength >= 0 ? "pos" : "neg"}`}
                    style={{ width: `${Math.abs(g.strength) / maxStr * 100}%` }} />
            </span>
            <span className={`mb-bar-val ${g.strength >= 0 ? "up" : "down"}`}>{g.strength > 0 ? "+" : ""}{g.strength}</span>
          </button>
        ))}
        {!byStrength.length && <div className="mb-empty">No groups meet the minimum — lower the slider or wait for the scan.</div>}
      </div>

      {/* Heatmap tiles */}
      {/* Heatmap: tile WIDTH ∝ √(stocks in group) — the market-weight feel of a
          real sector map — and background intensity is CONTINUOUS with rotation
          strength (not tiered), so +40 visibly outglows +10. */}
      <div className="mb-heat">
        {byStrength.map(g => {
          const s = g.strength;
          // srgb mix against a neutral dark keeps reds red and greens green
          // (an oklch mix with the blue-tinted bg goes muddy purple).
          const mag = Math.min(85, 28 + Math.abs(s) * 1.5);
          const bg = s > 0 ? `color-mix(in srgb, var(--up) ${mag}%, #0d1015)`
                   : s < 0 ? `color-mix(in srgb, var(--down) ${mag}%, #0d1015)` : undefined;
          return (
          <button key={g.name} className={`mb-heat-tile${Math.abs(s) >= 12 ? " mb-heat-hot" : ""}`}
                  style={{ flexGrow: Math.max(1, Math.sqrt(g.total)), ...(bg ? { background: bg, borderColor: "transparent" } : {}) }}
                  onClick={() => setDrill(g.name)} title={`${g.name} — strength ${s} · ${g.hod} at highs / ${g.lod} at lows of ${g.total} stocks · tile width ∝ group size, color ∝ strength · click to drill in`}>
            <span className="mb-heat-name">{g.name}</span>
            <span className="mb-heat-str">{s > 0 ? "+" : ""}{s}</span>
            <span className="mb-heat-cnt">{g.hod}H / {g.lod}L / {g.total}</span>
          </button>
          );
        })}
      </div>

      {/* Data table */}
      <div className="scan-table-wrap">
        <table className="scan-table mb-table">
          <thead>
            <tr>{BREADTH_COLS.map(c => (
              <th key={c.k} className={c.str ? "" : "scan-th-num"} title={c.title}
                  onClick={() => setSort(c.k)}>{c.k === "name" ? view[0].toUpperCase() + view.slice(1) : c.label}{sortCol === c.k ? (sortDir === "desc" ? " ↓" : " ↑") : ""}</th>
            ))}</tr>
          </thead>
          <tbody>
            {sorted.map(g => (
              <tr key={g.name} className="scan-row" onClick={() => setDrill(g.name)} title="Drill into this group">
                <td className="mb-gname">{g.name}</td>
                <td className="scan-num mb-strcell">
                  <span className={`mb-strbar ${g.strength >= 0 ? "pos" : "neg"}`} style={{ width: `${Math.min(100, Math.abs(g.strength))}%` }} />
                  <b className={g.strength >= 0 ? "up" : "down"}>{g.strength > 0 ? "+" : ""}{g.strength}</b>
                </td>
                <td className="scan-num up">{g.hod}</td>
                <td className="scan-num down">{g.lod}</td>
                <td className={`scan-num ${g.net >= 0 ? "up" : "down"}`}>{g.net > 0 ? "+" : ""}{g.net}</td>
                <td className="scan-num">{Math.round(g.hodPct * 100)}%</td>
                <td className="scan-num">{Math.round(g.lodPct * 100)}%</td>
                <td className="scan-num">{g.total}</td>
                <td className="scan-num up">{g.h52 || "·"}</td>
                <td className="scan-num down">{g.l52 || "·"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Drill-down */}
      {drillGroup && (
        <div className="mb-drill">
          <div className="mb-drill-top">
            <b>{drillGroup.name}</b>
            <span className="mb-drill-meta">strength {drillGroup.strength > 0 ? "+" : ""}{drillGroup.strength} · {drillGroup.hod} HOD / {drillGroup.lod} LOD / {drillGroup.total} stocks</span>
            <button className="mb-drill-x" onClick={() => setDrill(null)} title="Close">✕</button>
          </div>
          <div className="mb-drill-grid">
            <BreadthStockList title="At high of day" tone="hod" stocks={drillGroup.hodStocks} sort={hodSort} setSort={setHodSort} onLoadTicker={onLoadTicker} />
            <BreadthStockList title="At low of day" tone="lod" stocks={drillGroup.lodStocks} sort={lodSort} setSort={setLodSort} onLoadTicker={onLoadTicker} />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Market Context bar ────────────────────────────────────────────────────
// Fills the band under the futures strip: is premium safe today (SPY gamma
// regime), what can blow up today (macro events + watchlist earnings within a
// week), and where the flow is rotating (sector ribbon from the board). One
// always-on strip so you never trade blind into a Fed day or an earnings gap.
function MarketContextBar({ apiFetch, onSwitchTicker, onOpenBreadth }) {
  const [ctx, setCtx] = useState(null);
  const [board, setBoard] = useState(null);
  useEffect(() => {
    let stop = false, t1 = null, t2 = null;
    const grab = async (u, set, ms, ref) => {
      try { const d = await sharedJson(apiFetch, u, Math.min(ms / 2, 30000)); if (!stop) set(d); } catch (_) {}
      if (!stop) ref.id = setTimeout(() => grab(u, set, ms, ref), document.hidden ? ms * 3 : ms);
    };
    const r1 = {}, r2 = {};
    grab("/api/market_context", setCtx, 120000, r1);
    grab("/api/watchlist_table", setBoard, 60000, r2);
    return () => { stop = true; clearTimeout(r1.id); clearTimeout(r2.id); };
  }, []);

  const rotation = useMemo(() => {
    const scored = board && board.rows ? edgesFor(board.rows) : [];
    const agg = {};
    scored.forEach(r => {
      if (r.edge == null || !r.sector) return;
      const s = agg[r.sector] || (agg[r.sector] = { sector: r.sector, net: 0, n: 0 });
      s.n++; if (r.edge >= 25) s.net++; else if (r.edge <= -25) s.net--;
    });
    return Object.values(agg).filter(s => s.n >= 3).sort((a, b) => b.net - a.net);
  }, [board]);

  if (!ctx && !board) return <div className="mctx mctx-skel" />;
  const gamma = ctx && ctx.gamma;
  const macro = (ctx && ctx.macro) || [];
  const earn = (ctx && ctx.earnings_soon) || [];
  const quiet = !macro.length && !earn.length;
  const maxNet = Math.max(1, ...rotation.map(r => Math.abs(r.net)));
  return (
    <div className="mctx">
      <div className="mctx-line">
        {gamma
          ? <span className={`mctx-gamma mctx-g-${gamma.regime}`}
                  title={gamma.regime === "long"
                    ? `SPY dealers are LONG gamma (net GEX +$${gamma.net_gex}B) — expect pinning / mean-reversion. Premium-selling friendly.`
                    : `SPY dealers are SHORT gamma (net GEX $${gamma.net_gex}B) — expect trending / explosive moves. Premium is a trap; favor buying or sizing down.`}>
              <span className="mctx-dot" />GAMMA <b>{gamma.regime === "long" ? "Long γ · pinning" : "Short γ · explosive"}</b>
            </span>
          : <span className="mctx-gamma mctx-g-na" title="SPY gamma read unavailable (needs the option chain).">GAMMA <b>—</b></span>}
        <span className="mctx-events">
          {quiet && <span className="mctx-quiet">No major catalysts today</span>}
          {macro.map((e, i) => (
            <span key={i} className="mctx-ev" title={`${e.event} — ${e.today ? "today " + (e.time || "") : "tomorrow"}. High-impact macro; expect a volatility spike.`}>
              <span className="mctx-ev-dot" /><b>{e.event}</b> {e.today ? (e.time || "today") : "tmrw"}
            </span>
          ))}
          {earn.length > 0 && (
            <span className="mctx-earn" title="Watchlist names reporting earnings within a week — do NOT sell premium or open a directional ticket whose expiry crosses these dates.">
              ⚠ earnings {earn.slice(0, 5).map(x => (
                <button key={x.sym} className="mctx-earn-sym" onClick={() => onSwitchTicker && onSwitchTicker(x.sym)} title={`Load ${x.sym} — earnings in ${x.days}d`}>{x.sym}<small>{x.days}d</small></button>
              ))}
            </span>
          )}
        </span>
      </div>
      <div className="mctx-ribbon" onClick={onOpenBreadth} title="Sector rotation by options flow (net bullish − bearish EDGE). Click a sector to open it drilled-in on the Breadth tab.">
        <span className="mctx-rlbl">Rotation</span>
        {rotation.slice(0, 14).map(r => (
          <button key={r.sector} className={`mctx-chip ${r.net > 0 ? "pos" : r.net < 0 ? "neg" : "flat"}`}
                style={{ opacity: 0.55 + 0.45 * Math.abs(r.net) / maxNet }}
                title={`${r.sector}: net flow ${r.net > 0 ? "+" : ""}${r.net} across ${r.n} names — click to open ${r.sector} on the Breadth tab (drilled into its HOD/LOD lists)`}
                onClick={(e) => {
                  e.stopPropagation();
                  try { window.__breadthDrill = r.sector; window.dispatchEvent(new CustomEvent("breadth-drill", { detail: r.sector })); } catch (_) {}
                  onOpenBreadth && onOpenBreadth();
                }}>
            {r.sector}<b>{r.net > 0 ? "+" : ""}{r.net}</b>
          </button>
        ))}
        {!rotation.length && <span className="mctx-quiet">rotation pending scan…</span>}
      </div>
    </div>
  );
}

// ── Picks Journal ─────────────────────────────────────────────────────────
// The log of every early-mover pick you snapshotted from the posture card:
// what you saw (price, ticket, thesis, posture) at the moment you logged it,
// plus the live price and how far it has moved toward the thesis since — so you
// can actually score how good the picks were.
function pjWhen(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
      + ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  } catch (_) { return iso || "—"; }
}
const pj2 = (v) => v != null && v === v ? Number(v).toFixed(2) : "—";

function PicksJournalCard({ apiFetch, onSwitchTicker }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const load = React.useCallback(async () => {
    try { const r = await apiFetch("/api/pick_journal"); const d = await r.json(); setData(d); } catch (_) {}
    setLoading(false);
  }, [apiFetch]);
  useEffect(() => {
    let stop = false, t = null;
    const run = async () => { await load(); if (!stop) t = setTimeout(run, document.hidden ? 120000 : 45000); };
    run();
    const onVis = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { stop = true; if (t) clearTimeout(t); document.removeEventListener("visibilitychange", onVis); };
  }, [load]);
  const del = async (id) => { try { await apiFetch("/api/pick_journal/delete", { method: "POST", body: JSON.stringify({ id }) }); load(); } catch (_) {} };
  const picks = (data && data.picks) || [];
  const stats = useMemo(() => {
    const scored = picks.filter(p => p.pct_toward != null);
    const wins = scored.filter(p => p.pct_toward > 0).length;
    const avg = scored.length ? scored.reduce((a, p) => a + p.pct_toward, 0) / scored.length : null;
    return { total: picks.length, scored: scored.length,
             hit: scored.length ? Math.round(wins / scored.length * 100) : null,
             avg: avg != null ? Math.round(avg * 10) / 10 : null };
  }, [picks]);

  if (loading && !data) return <div className="card pj-card"><CardNote kind="loading">Loading your picks journal…</CardNote></div>;
  return (
    <div className="card pj-card">
      <div className="pj-head">
        <div className="pj-title" title="Every early-mover pick you logged from the Market Posture card, with how it has performed since.">Picks Journal <span className="pj-sub">score your early-mover picks</span></div>
        <div className="pj-summary">
          <b>{stats.total}</b> logged
          {stats.scored ? <> · hit rate <b className={stats.hit >= 50 ? "up" : "down"}>{stats.hit}%</b></> : null}
          {stats.avg != null ? <> · avg <b className={stats.avg >= 0 ? "up" : "down"}>{stats.avg > 0 ? "+" : ""}{stats.avg}%</b> toward thesis</> : null}
        </div>
      </div>
      {!picks.length
        ? <CardNote kind="empty">No picks logged yet. On the Market Posture card up top, hit the ＋ on an early mover to snapshot it here — price, time, the ticket and the full reasoning.</CardNote>
        : (
        <div className="pj-list">
          {picks.map(p => {
            const t = p.pct_toward;
            return (
            <div key={p.id} className="pj-entry">
              <div className="pj-row1">
                <button className="pj-sym" onClick={() => onSwitchTicker && onSwitchTicker(p.symbol)} title={`Load ${p.symbol}`}>{p.symbol}</button>
                <span className="pj-when" title="When you logged it">{pjWhen(p.saved_at)}</span>
                <span className={`pj-tkt ${p.action === "buy" ? "buy" : "sell"}`}>{p.ticket}</span>
                {t != null && <span className={`pj-move ${t >= 0 ? "up" : "down"}`} title="Move toward the pick's thesis since you logged it (long → up is good, short → down is good)">{t > 0 ? "+" : ""}{t}%</span>}
                <button className="pj-del" onClick={() => del(p.id)} title="Remove from journal" aria-label="Delete">✕</button>
              </div>
              <div className="pj-row2">
                logged @ <b>${pj2(p.price)}</b>{p.now_price != null && <> → now <b>${pj2(p.now_price)}</b></>}
                {" · "}{p.dir || "?"} · {p.swing_pct != null ? Math.round(p.swing_pct) : "?"}% into a {p.swing_med_pct != null ? Math.round(p.swing_med_pct) : "?"}% move
                {p.edge != null && <> · edge {p.edge > 0 ? "+" : ""}{p.edge}</>}
                {p.posture ? <> · {p.posture}{p.score != null ? ` (${p.score})` : ""}</> : null}
                {p.regime ? ` · ${p.regime}` : ""}
              </div>
              {p.why && <div className="pj-why">{p.why}</div>}
            </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Open-reversal scanner ─────────────────────────────────────────────────
// Finds the CRDO pattern: opened, sold off >= dip% below the REGULAR-SESSION
// open, then reclaimed the open — the failed-breakdown / trapped-shorts
// reversal. Order is guaranteed by construction (price is above the open NOW,
// so the session low came first). Reversal time = first 1-minute close back
// above the open after the low (server-side).
const OREV_COLS = [
  { k: "symbol", label: "Symbol", str: true, title: "Ticker — click a row to load it on the chart" },
  { k: "tag", label: "Tag", str: true, title: "Your watchlist tag for this name (from the CSV import)" },
  { k: "open", label: "Open", title: "Official regular-session opening price" },
  { k: "low", label: "Low", title: "Lowest price printed after the open (the flush)" },
  { k: "drop_pct", label: "Drop", title: "Depth of the sell-off from the open to the low" },
  { k: "last", label: "Now", title: "Current price" },
  { k: "above_pct", label: "Above open", title: "How far ABOVE the open it has reclaimed — the strength of the reversal" },
  { k: "reversal_time", label: "Reversal", str: true, title: "Time (ET) of the first intraday close back above the open after the low (~ = 5-minute resolution)" },
  { k: "volume", label: "Volume", title: "Session volume — conviction behind the reversal" },
];

function OpenReversalCard({ apiFetch, onSwitchTicker }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [dip, setDip] = useState(2);
  const [sort, setSort] = useState({ k: "above_pct", dir: "desc" });
  useEffect(() => {
    let stop = false, t = null;
    const load = async () => {
      try {
        const d = await sharedJson(apiFetch, `/api/scan/open_reversal?dip=${dip}`, 45000);
        if (!stop) { setData(d); setLoading(false); }
      } catch (_) { if (!stop) setLoading(false); }
      if (!stop) t = setTimeout(load, document.hidden ? 180000 : 60000);
    };
    load();
    const onVis = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { stop = true; if (t) clearTimeout(t); document.removeEventListener("visibilitychange", onVis); };
  }, [dip]);
  const rows = useMemo(() => {
    const arr = ((data && data.rows) || []).slice();
    const col = OREV_COLS.find(c => c.k === sort.k) || OREV_COLS[5];
    arr.sort((a, b) => {
      const av = a[sort.k], bv = b[sort.k];
      const r = col.str ? String(av || "").localeCompare(String(bv || ""))
                        : (av == null ? -1e18 : av) - (bv == null ? -1e18 : bv);
      return sort.dir === "desc" ? -r : r;
    });
    return arr;
  }, [data, sort]);
  return (
    <div className="card orev-card" style={{ marginBottom: "var(--row-gap)" }}>
      <div className="card-head">
        <div>
          <div className="kicker" title="Watchlist stocks that sold off below the regular-session open, then reclaimed it — the failed-breakdown reversal (like CRDO: opened $260, dipped to $250, ran to $277).">Watchlist · intraday reversal</div>
          <div className="card-title">Open reclaim — dip &amp; reverse</div>
        </div>
        <label className="orev-dip" title="Minimum sell-off below the open before the reclaim counts. Deeper dip = stronger trap, fewer hits.">
          min dip
          <select className="sb-select" value={dip} onChange={e => setDip(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map(v => <option key={v} value={v}>{v}%</option>)}
          </select>
        </label>
      </div>
      {loading && !data ? <CardNote kind="loading">Scanning for open reclaims…</CardNote>
      : !rows.length ? <CardNote kind="empty">No open-reclaim reversals right now — nothing on the watchlist has dipped {dip}% below its open and recovered it. Checks every minute during the session.</CardNote>
      : (
      <div className="scan-table-wrap">
        <table className="scan-table mtable">
          <thead>
            <tr>
              {OREV_COLS.map(c => (
                <th key={c.k} className={c.k === "symbol" || c.k === "tag" ? "" : "scan-th-num"}
                    style={{ cursor: "pointer" }} title={`${c.title} — click to sort`}
                    onClick={() => setSort(s => s.k === c.k
                      ? { k: c.k, dir: s.dir === "desc" ? "asc" : "desc" }
                      : { k: c.k, dir: c.str ? "asc" : "desc" })}>
                  {c.label}{sort.k === c.k ? (sort.dir === "desc" ? " ↓" : " ↑") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data && data.stage2_error && rows.every(r => !r.reversal_time) && (
              <tr><td colSpan={9} className="orev-warn" title={String(data.stage2_error)}>⚠ reversal times unavailable right now — {String(data.stage2_error).slice(0, 90)}</td></tr>
            )}
            {rows.map(r => (
              <tr key={r.symbol} className="scan-row" onClick={() => onSwitchTicker && onSwitchTicker(r.symbol)}
                  title={`${r.company || r.symbol} — opened $${r.open}, flushed to $${r.low} (${r.drop_pct}%), now $${r.last} (+${r.above_pct}% above the open)${r.reversal_time ? `, reclaimed at ${r.reversal_time} ET` : ""}. Click to load.`}>
                <td data-label="Symbol"><b>{r.symbol}</b></td>
                <td data-label="Tag" className="orev-tag" title={r.tag ? `Tag: ${r.tag}` : "No tag"}>{r.tag || "—"}</td>
                <td data-label="Open" className="scan-num">${r.open}</td>
                <td data-label="Low" className="scan-num down">${r.low}</td>
                <td data-label="Drop" className="scan-num down">{r.drop_pct}%</td>
                <td data-label="Now" className="scan-num">${r.last}</td>
                <td data-label="Above open" className="scan-num up"><b>+{r.above_pct}%</b></td>
                <td data-label="Reversal" className="scan-num">{r.reversal_time || "—"}</td>
                <td data-label="Volume" className="scan-num">{fmtVol(r.volume)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}
    </div>
  );
}

// ── Reversal alerts (site-wide toasts) ────────────────────────────────────
// Watches the open-reversal scanner from ANYWHERE in the app (mounted at the
// shell level, not inside the lazily-mounted Scanners tab) and pops a toast
// when a NEW symbol first reclaims its open. The first poll of the day
// baselines silently (no spam for everything already triggered before you
// opened the site); after that, each fresh reclaim gets one toast. Clicking a
// toast loads the ticker. Seen-set is day-keyed in localStorage.
function ReversalAlerts({ apiFetch, onSwitchTicker }) {
  const [toasts, setToasts] = useState([]);
  const baselined = useRef(false);
  useEffect(() => {
    let stop = false, t = null;
    const dayKey = () => "jerry_orev_seen_" + new Date().toISOString().slice(0, 10);
    const load = async () => {
      try {
        const d = await sharedJson(apiFetch, "/api/scan/open_reversal?dip=2", 45000);
        const rows = (d && d.rows) || [];
        if (!stop && rows.length) {
          let seen;
          try { seen = new Set(JSON.parse(localStorage.getItem(dayKey())) || []); } catch (_) { seen = new Set(); }
          const fresh = rows.filter(r => !seen.has(r.symbol));
          rows.forEach(r => seen.add(r.symbol));
          try { localStorage.setItem(dayKey(), JSON.stringify([...seen])); } catch (_) {}
          if (baselined.current && fresh.length) {
            setToasts(ts => [...ts, ...fresh.slice(0, 3).map(r => ({
              id: `${r.symbol}-${Date.now()}`, sym: r.symbol,
              msg: `reclaimed its open — dipped ${r.drop_pct}%, now +${r.above_pct}% above`,
            }))].slice(-3));
          }
          baselined.current = true;
        } else if (!stop) {
          baselined.current = true;
        }
      } catch (_) {}
      if (!stop) t = setTimeout(load, document.hidden ? 240000 : 90000);
    };
    load();
    return () => { stop = true; if (t) clearTimeout(t); };
  }, []);
  useEffect(() => {   // auto-dismiss oldest after 12s
    if (!toasts.length) return undefined;
    const id = setTimeout(() => setToasts(ts => ts.slice(1)), 12000);
    return () => clearTimeout(id);
  }, [toasts]);
  if (!toasts.length) return null;
  return (
    <div className="toast-stack" aria-live="polite">
      {toasts.map(t => (
        <button key={t.id} className="toast toast-rev"
                title={`${t.sym} triggered the open-reclaim reversal scanner. Click to load it on the chart.`}
                onClick={() => { setToasts(ts => ts.filter(x => x.id !== t.id)); onSwitchTicker && onSwitchTicker(t.sym); }}>
          <span className="toast-ico">⚡</span>
          <span className="toast-body"><b>{t.sym}</b> {t.msg}</span>
          <span className="toast-x" onClick={(e) => { e.stopPropagation(); setToasts(ts => ts.filter(x => x.id !== t.id)); }}>✕</span>
        </button>
      ))}
    </div>
  );
}

// ── Radar alerts (v3.20) — score-80 signals reach you anywhere in the app ──
// Mounted globally like ReversalAlerts. Polls the same /api/radar snapshot
// the Scanners card uses (sharedJson dedupes the request), remembers what it
// has already announced per day, and toasts new hot signals. Clicking a
// toast loads the symbol on the Trade tab in 1-Min chart mode.
function RadarAlerts({ apiFetch, onOpenIntraday }) {
  const [toasts, setToasts] = useState([]);
  const baselined = useRef(false);
  useEffect(() => {
    let stop = false, t = null;
    const dayKey = () => "jerry_radar_seen_" + new Date().toISOString().slice(0, 10);
    const load = async () => {
      try {
        const d = await sharedJson(apiFetch, "/api/radar", 20000);
        if (!stop && d && d.market_open) {
          const rows = [...(d.long || []), ...(d.short || [])].filter(r => r.score >= 80);
          let seen;
          try { seen = new Set(JSON.parse(localStorage.getItem(dayKey())) || []); } catch (_) { seen = new Set(); }
          const fresh = rows.filter(r => !seen.has(`${r.symbol}|${r.side}`));
          rows.forEach(r => seen.add(`${r.symbol}|${r.side}`));
          try { localStorage.setItem(dayKey(), JSON.stringify([...seen])); } catch (_) {}
          if (baselined.current && fresh.length) {
            setToasts(ts => [...ts, ...fresh.slice(0, 3).map(r => ({
              id: `${r.symbol}-${r.side}-${Date.now()}`, sym: r.symbol, side: r.side,
              score: r.score, msg: (r.reasons || [])[0] || "",
              rr: r.ticket && r.ticket.rr != null ? r.ticket.rr : null,
            }))].slice(-3));
          }
          baselined.current = true;
        } else if (!stop && d) {
          baselined.current = true;
        }
      } catch (_) {}
      if (!stop) t = setTimeout(load, document.hidden ? 240000 : 60000);
    };
    load();
    return () => { stop = true; if (t) clearTimeout(t); };
  }, []);
  useEffect(() => {
    if (!toasts.length) return undefined;
    const id = setTimeout(() => setToasts(ts => ts.slice(1)), 14000);
    return () => clearTimeout(id);
  }, [toasts]);
  if (!toasts.length) return null;
  return (
    <div className="toast-stack" aria-live="polite">
      {toasts.map(t => (
        <button key={t.id} className={`toast toast-radar ${t.side === "long" ? "toast-long" : "toast-short"}`}
                title={`Reversal Radar ${t.side} signal, score ${t.score}/100${t.rr != null ? `, ${t.rr}R to VWAP` : ""}. Click to open the 1-minute chart with VWAP and levels.`}
                onClick={() => { setToasts(ts => ts.filter(x => x.id !== t.id)); onOpenIntraday && onOpenIntraday(t.sym); }}>
          <span className="toast-ico">{t.side === "long" ? "▲" : "▼"}</span>
          <span className="toast-body"><b>{t.sym}</b> {t.side === "long" ? "LONG" : "SHORT"} {t.score} — {t.msg}{t.rr != null ? ` · ${t.rr}R` : ""}</span>
          <span className="toast-x" onClick={(e) => { e.stopPropagation(); setToasts(ts => ts.filter(x => x.id !== t.id)); }}>✕</span>
        </button>
      ))}
    </div>
  );
}

// ── Command palette (⌘K) ─────────────────────────────────────────────────
// One search box that reaches everything: tabs (with plain-English blurbs —
// doubles as first-time-user documentation), any watchlist symbol, any typed
// ticker, and quick actions. Desktop: centered dialog. Phone: bottom sheet
// (same component, CSS switches the presentation). Full keyboard support.
const TAB_BLURBS = {
  trade: "Strike cards, premium quotes and the covered-call / CSP workbench",
  discover: "Screeners and idea feeds across your watchlist",
  analyze: "Deep-dive analytics for the loaded symbol",
  patterns: "Swing decision — where price is in its move, odds & trade plan",
  news: "Headlines for the loaded symbol and your watchlist",
  flow: "Options-flow intelligence from Unusual Whales",
  scanners: "Open-reclaim reversals + market-wide unusual-flow scans",
  juice: "0-3 DTE premium-selling scanner — fattest same-week straddles, ranked by Juice Score with ready-made strangle/condor/spread structures",
  finviz: "Finviz rendered inside the dashboard (via the one-time helper extension) — follows the global ticker, real Elite login and account",
  tview: "TradingView Supercharts inside the dashboard (helper v2.0) — your real layouts, indicators and alerts, following the global ticker",
  whales: "Unusual Whales inside the dashboard — flow, sweeps, OI, IV and dark pool for the global ticker, with your real UW account",
  breadth: "Which sectors are making highs vs lows — rotation map",
  journal: "Your logged early-mover picks, scored against live prices",
  watchlist: "Every tracked stock with full metrics, EDGE and setups",
  streaks: "Consecutive up/down-day streaks vs each stock's history",
  calendar: "Earnings dates + macro events for the weeks ahead",
  manage: "Import/export the watchlist, tags and app settings",
};

function CommandPalette({ open, onClose, onSwitchTicker, onChangeTab, symbols, apiFetch }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const inputRef = useRef(null);
  useEffect(() => {
    if (open) { setQ(""); setSel(0); setTimeout(() => inputRef.current && inputRef.current.focus(), 30); }
  }, [open]);
  useEffect(() => {           // lock page scroll behind the palette
    if (!open) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  const items = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const score = (text) => {
      const t = text.toLowerCase();
      if (!needle) return 1;
      if (t === needle) return 100;
      if (t.startsWith(needle)) return 80;
      if (t.split(/[\s·/-]+/).some(w => w.startsWith(needle))) return 60;
      if (t.includes(needle)) return 40;
      let i = 0;                                  // subsequence
      for (const ch of t) if (ch === needle[i]) i++;
      return i === needle.length ? 15 : 0;
    };
    const out = [];
    // Symbols — watchlist matches, plus the raw query as a loadable ticker.
    const seen = new Set();
    if (needle && /^[a-z.\-]{1,6}$/i.test(needle)) {
      out.push({ kind: "sym", label: needle.toUpperCase(), hint: "Load on the chart", s: 90 });
      seen.add(needle.toUpperCase());
    }
    (symbols || []).forEach(sym => {
      if (seen.has(sym)) return;
      const s = score(sym);
      if (s > 0 && needle) out.push({ kind: "sym", label: sym, hint: "Watchlist — load on the chart", s: s + 2 });
    });
    // Tabs — always browsable when the query is empty.
    (window.TABS || []).forEach(t => {
      const s = Math.max(score(t.label), score(TAB_BLURBS[t.id] || "") * 0.5);
      if (s > 0) out.push({ kind: "tab", id: t.id, label: t.label, hint: TAB_BLURBS[t.id] || "", s });
    });
    // Actions.
    [{ id: "finviz", label: "Open in Finviz", hint: "Embedded Finviz on the active ticker", tab: "finviz" },
     { id: "tview", label: "Open in TradingView", hint: "Embedded Supercharts on the active ticker", tab: "tview" },
     { id: "whales", label: "Open in Unusual Whales", hint: "Embedded UW flow/OI/IV on the active ticker", tab: "whales" },
     { id: "rescan", label: "Rescan watchlist now", hint: "Kick a fresh full-metrics scan" },
     { id: "journal", label: "Open Picks Journal", hint: "Score your logged early movers", tab: "journal" },
     { id: "breadth", label: "Open Market Breadth", hint: "Sector rotation map", tab: "breadth" }]
      .forEach(a => { const s = score(a.label); if (s > 0) out.push({ kind: "act", ...a, s }); });
    out.sort((a, b) => b.s - a.s);
    return out.slice(0, 12);
  }, [q, symbols]);

  useEffect(() => { setSel(0); }, [q]);
  if (!open) return null;
  const run = (it) => {
    if (!it) return;
    if (it.kind === "sym") onSwitchTicker && onSwitchTicker(it.label);
    else if (it.kind === "tab") onChangeTab && onChangeTab(it.id);
    else if (it.kind === "act") {
      if (it.tab) onChangeTab && onChangeTab(it.tab);
      else if (it.id === "rescan") apiFetch("/api/watchlist_table/scan?force=1").catch(() => {});
    }
    onClose();
  };
  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel(s => Math.min(s + 1, items.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel(s => Math.max(s - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); run(items[sel]); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  };
  return (
    <div className="cp-backdrop" onClick={onClose}>
      <div className="cp-modal" role="dialog" aria-modal="true" aria-label="Command palette" onClick={e => e.stopPropagation()}>
        <div className="cp-inputrow">
          <span className="cp-glass" aria-hidden="true">⌕</span>
          <input ref={inputRef} className="cp-input" value={q} onChange={e => setQ(e.target.value)}
                 onKeyDown={onKey} placeholder="Type a ticker, tab or action…"
                 autoComplete="off" autoCorrect="off" autoCapitalize="characters" spellCheck="false" />
          <span className="cp-kbd">esc</span>
        </div>
        <div className="cp-list" role="listbox">
          {items.map((it, i) => (
            <button key={`${it.kind}-${it.label}-${i}`} role="option" aria-selected={i === sel}
                    className={`cp-item${i === sel ? " sel" : ""}`}
                    onMouseEnter={() => setSel(i)} onClick={() => run(it)}>
              <span className={`cp-tag cp-tag-${it.kind}`}>{it.kind === "sym" ? "TICKER" : it.kind === "tab" ? "TAB" : "ACTION"}</span>
              <span className="cp-label">{it.label}</span>
              <span className="cp-hint">{it.hint}</span>
            </button>
          ))}
          {!items.length && <div className="cp-empty">Nothing matches "{q}"</div>}
        </div>
        <div className="cp-foot">↑↓ navigate · enter select · <b>⌘K</b> or <b>/</b> opens this anywhere · <b>?</b> all shortcuts</div>
      </div>
    </div>
  );
}

// ── Keyboard-shortcuts sheet (?) ──────────────────────────────────────────
function ShortcutsSheet({ open, onClose }) {
  if (!open) return null;
  const rows = [
    ["⌘K  or  /", "Open the command palette (tickers, tabs, actions)"],
    ["[  and  ]", "Previous / next tab"],
    ["?", "This shortcuts sheet"],
    ["esc", "Close any dialog"],
  ];
  return (
    <div className="cp-backdrop" onClick={onClose}>
      <div className="cp-modal cp-help" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" onClick={e => e.stopPropagation()}>
        <div className="cp-help-title">Keyboard shortcuts</div>
        {rows.map(([k, d]) => (
          <div key={k} className="cp-help-row"><span className="cp-kbd">{k}</span><span>{d}</span></div>
        ))}
      </div>
    </div>
  );
}

// ── Valuation vs history & peers ──────────────────────────────────────────
// "Is this multiple cheap?" answered two ways: against the stock's OWN 5-year
// average trailing P/E (annual EPS × that year's average price — no feed has
// forward-P/E history, so this is the honest computable version), and against
// same-industry watchlist peers on forward P/E.
function ValuationCard({ apiFetch, ticker }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let stop = false;
    setLoading(true); setData(null);
    sharedJson(apiFetch, `/api/valuation?symbol=${encodeURIComponent(ticker)}`, 30 * 60 * 1000)
      .then(d => { if (!stop) { setData(d); setLoading(false); } })
      .catch(() => { if (!stop) setLoading(false); });
    return () => { stop = true; };
  }, [ticker]);
  const x1 = (v) => v == null ? "—" : `${Number(v).toFixed(1)}×`;
  if (loading) return <div className="card val-card"><CardNote kind="loading">Computing valuation history…</CardNote></div>;
  if (!data || data.error) return null;
  const h = data.history || {}, p = data.peers || {}, c = data.current || {};
  const years = h.years || [];
  const maxPe = Math.max(1, ...years.map(y => y.pe), h.avg_pe || 0);
  // Below average/median = cheaper = GREEN (a discount is good for a buyer).
  const tone = (v) => v == null ? "" : v < 0 ? "up" : "down";
  const verdictTxt = { cheap: "Historically cheap", fair: "Fairly valued", rich: "Historically rich" }[data.verdict] || "";
  return (
    <div className="card val-card" style={{ marginBottom: "var(--row-gap)" }}>
      <div className="card-head">
        <div>
          <div className="kicker" title="Compares today's earnings multiple against this stock's OWN 5-year average (annual EPS × each year's average price) and against same-industry watchlist peers — the classic 'MSFT at 22.9× fwd, below its 5-year average' read.">Valuation · {ticker}</div>
          <div className="card-title">Multiple vs history &amp; peers</div>
        </div>
        {data.verdict && (
          <span className={`val-verdict val-${data.verdict}`}
                title={`${verdictTxt}: ${h.vs_avg_pct != null ? `trading ${Math.abs(h.vs_avg_pct)}% ${h.vs_avg_pct < 0 ? "BELOW" : "above"} its own 5-year average multiple` : "based on the peer comparison"}${p.vs_median_pct != null ? ` and ${Math.abs(p.vs_median_pct)}% ${p.vs_median_pct < 0 ? "below" : "above"} the ${p.basis} median` : ""}.`}>
            {verdictTxt}
          </span>
        )}
      </div>
      <div className="val-now">
        <div title="Forward P/E — today's price over NEXT year's expected earnings. The multiple analysts quote.">
          <span>Fwd P/E</span><b>{x1(c.forward_pe)}</b>
        </div>
        <div title="Trailing P/E — today's price over the LAST 12 months' earnings. This is what the 5-year history below compares against (apples to apples).">
          <span>Trailing P/E</span><b>{x1(c.pe)}</b>
        </div>
        {h.avg_pe != null && (
          <div title="Average of the annual trailing P/E over the last five fiscal years.">
            <span>5-yr avg</span><b>{x1(h.avg_pe)}</b>
          </div>
        )}
      </div>
      {years.length >= 2 ? (
        <div className="val-hist" title="Trailing P/E by fiscal year (bar height = multiple). The dashed line is the 5-year average — where today's bar sits vs that line is the whole story.">
          <div className="val-bars">
            {years.map(y => (
              <div key={y.year} className="val-baru" title={`FY${y.year}: ${y.pe}× (EPS $${y.eps})`}>
                <div className="val-bar" style={{ height: `${Math.max(8, y.pe / maxPe * 100)}%` }} />
                <span className="val-yr">{String(y.year).slice(2)}</span>
              </div>
            ))}
            {c.pe != null && (
              <div className="val-baru val-baru-now" title={`Now: ${c.pe}× trailing`}>
                <div className="val-bar now" style={{ height: `${Math.max(8, Math.min(c.pe, maxPe) / maxPe * 100)}%` }} />
                <span className="val-yr">now</span>
              </div>
            )}
            {h.avg_pe != null && <div className="val-avgline" style={{ bottom: `${h.avg_pe / maxPe * 100}%` }} />}
          </div>
          {h.vs_avg_pct != null && (
            <div className="val-read" title="Percent above/below the stock's own 5-year average trailing multiple. Negative = cheaper than its own history.">
              vs its 5-yr average: <b className={tone(h.vs_avg_pct)}>{h.vs_avg_pct > 0 ? "+" : ""}{h.vs_avg_pct}%</b>
            </div>
          )}
        </div>
      ) : (
        <div className="val-empty">No positive-EPS fiscal years found — the history comparison needs a profitable track record (pre-profit names can't have a meaningful P/E).</div>
      )}
      {p.median != null && (
        <div className="val-peers" title={`Forward P/E across the ${p.count} ${p.basis} names on your watchlist. Percentile = share of peers cheaper than ${ticker}.`}>
          vs <b>{p.basis}</b> peers ({p.count}): median <b>{x1(p.median)}</b>
          {p.vs_median_pct != null && <> · <b className={tone(p.vs_median_pct)}>{p.vs_median_pct > 0 ? "+" : ""}{p.vs_median_pct}%</b></>}
          {p.percentile != null && <span className="val-pct"> · cheaper than {100 - p.percentile}% of peers</span>}
        </div>
      )}
    </div>
  );
}

// ── Expected Move card (v3.17) ─────────────────────────────────────────────
// Options-implied expected move for the selected ticker: pick an expiration
// (weeklies / monthly / earnings), see the ±range, how it compares with the
// stock's own realized behavior, and a plain-English summary. Reports the
// band back up via onBand so the price chart can draw the EM levels.
function ExpectedMoveCard({ apiFetch, ticker, onBand }) {
  const [data, setData] = useState(null);
  const [expiry, setExpiry] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { setExpiry(null); setData(null); setErr(null); }, [ticker]);
  useEffect(() => {
    let stop = false;
    const load = () => {
      const url = `/api/expected_move?symbol=${encodeURIComponent(ticker)}` + (expiry ? `&expiry=${encodeURIComponent(expiry)}` : "");
      sharedJson(apiFetch, url, 55 * 1000)
        .then(d => {
          if (stop) return;
          if (d && !d.error && d.em) {
            setData(d); setErr(d.stale ? (d.stale_reason || "stale") : null);
            if (onBand) onBand({ symbol: d.symbol, high: d.em.upper, low: d.em.lower, expiry: d.expiry });
          } else {
            // Transient failure (overnight quote gap, upstream hiccup):
            // KEEP the last good data on screen instead of flashing the
            // card into an error state — just record the reason.
            setErr((d && d.error) || "no data");
          }
        })
        .catch(e => { if (!stop) setErr(String((e && e.message) || e)); });
    };
    load();
    const t = setInterval(skipWhenHidden(load), 60 * 1000);
    return () => { stop = true; clearInterval(t); };
  }, [ticker, expiry]);

  if (!data) return (
    <div className="expected-move-card chart-em-section">
      <div className="card-head"><div><div className="kicker">options-implied range</div>
        <div className="card-title">Expected Move</div></div></div>
      <div className="emx-empty" title="The expected move needs a live option chain. It fills in once Schwab quotes are available for this symbol. Retries every minute.">
        {err ? `${err} — retrying…` : "Loading chain…"}
      </div>
    </div>
  );

  const em = data.em, cmp = data.compare || {}, sum = data.summary || {}, lv = data.levels || {};
  const updated = (() => { try { return new Date(data.as_of).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }); } catch (e) { return "—"; } })();
  const expLabel = (x) =>
    x.earnings ? "🎯 Earnings" : x.monthly ? "Monthly" : x.nearest ? "This wk" : x.next_weekly ? "Next wk" : fmtUSDate(x.date);
  // Pills: nearest, next weekly, monthly, earnings — plus the selected one if
  // it came from somewhere else. Dedupe by date, keep chain order.
  const pills = (data.expirations || []).filter(x =>
    x.nearest || x.next_weekly || x.monthly || x.earnings || x.date === data.expiry);
  const pos = (v) => { // position 0-100 inside the band, padded 6% each side
    if (v == null || em.upper === em.lower) return null;
    const raw = (v - em.lower) / (em.upper - em.lower);
    return Math.max(0, Math.min(100, 6 + raw * 88));
  };
  const nearRes = (lv.resistance || [])[0], nearSup = (lv.support || [])[0];
  const ratioChip = cmp.em_vs_hist == null ? null
    : cmp.em_vs_hist >= 1.15 ? { cls: "warn", txt: `${cmp.em_vs_hist}× — options pricing MORE than usual` }
    : cmp.em_vs_hist <= 0.85 ? { cls: "up", txt: `${cmp.em_vs_hist}× — options pricing LESS than usual` }
    : { cls: "", txt: `${cmp.em_vs_hist}× — in line with history` };
  const prevDelta = (cmp.prev && cmp.prev.em_pct != null && em.pct != null)
    ? +(em.pct - cmp.prev.em_pct).toFixed(2) : null;
  const volTone = sum.vol_state === "elevated" ? "warn" : sum.vol_state === "subdued" ? "up" : "";

  return (
    <div className="expected-move-card chart-em-section">
      <div className="card-head">
        <div>
          <div className="kicker" title={`How the range is computed. "ATM straddle" = at-the-money call mid + put mid — the market's own price for the move to expiration. "IV × √t" is the theoretical fallback when quotes are missing. ATM strike used: ${em.atm_strike != null ? "$" + em.atm_strike : "n/a"}.${err ? ` Latest refresh failed (${err}) — showing the last good reading and retrying every minute.` : ""}`}>
            {em.method} · ATM IV {em.iv != null ? (em.iv * 100).toFixed(1) + "%" : "—"} · updated {updated}
            {err && <span className="emx-stale"> · stale — retrying</span>}
          </div>
          <div className="card-title">Expected Move</div>
        </div>
        <div className="emx-exp-pills" title="Switch the expiration the expected move is computed for. 🎯 marks the first expiration that captures the earnings report.">
          {pills.map(x => (
            <button key={x.date}
                    className={`emx-pill ${x.date === data.expiry ? "active" : ""} ${x.earnings ? "earn" : ""}`}
                    onClick={() => setExpiry(x.date)}
                    title={`${fmtUSDate(x.date)} — ${x.dte} day${x.dte === 1 ? "" : "s"} out${x.earnings ? " · first expiry that includes earnings" : x.monthly ? " · standard monthly (3rd Friday)" : ""}`}>
              {expLabel(x)}<span className="emx-pill-dte">{x.dte}d</span>
            </button>
          ))}
        </div>
      </div>

      <div className="em-stats-grid">
        <div className="em-stat" title="Current stock price the range is anchored to (Schwab real-time quote).">
          <div className="em-stat-lbl">Price</div>
          <div className="em-stat-val">{fmt$(data.spot)}</div>
        </div>
        <div className="em-stat" title={`Selected expiration date. ${data.dte} calendar day${data.dte === 1 ? "" : "s"} remain — time decay accelerates in the final week.`}>
          <div className="em-stat-lbl">Expiry</div>
          <div className="em-stat-val">{fmtUSDate(data.expiry)}<span className="emx-dte-sub">{data.dte}d</span></div>
        </div>
        <div className="em-stat" title="Expected dollar move from now to expiration as priced by the options market. About 68% of the time the stock should stay within ± this amount.">
          <div className="em-stat-lbl">Expected $</div>
          <div className="em-stat-val">±{fmt$(em.dollars)}</div>
        </div>
        <div className="em-stat" title="Expected move as a percent of the stock price. 1-2% is calm, 3-5% active, 6%+ usually means earnings or a big catalyst inside the window.">
          <div className="em-stat-lbl">Expected %</div>
          <div className="em-stat-val">±{em.pct != null ? em.pct.toFixed(2) : "—"}%</div>
        </div>
        <div className="em-stat" title="Upper edge of the expected range = price + expected move. Only ~16% of outcomes should close above this by expiration.">
          <div className="em-stat-lbl">Up to</div>
          <div className="em-stat-val up">{fmt$(em.upper)}</div>
        </div>
        <div className="em-stat" title="Lower edge of the expected range = price − expected move. Only ~16% of outcomes should close below this by expiration.">
          <div className="em-stat-lbl">Down to</div>
          <div className="em-stat-val down">{fmt$(em.lower)}</div>
        </div>
      </div>

      {/* Range meter: where price sits inside the band right now, with the
          day's high/low ticked so "already used most of the move" is visible
          at a glance. */}
      <div className="emx-range" title={`Where the stock is trading inside its expected range right now. Band position ${sum.band_position_pct != null ? sum.band_position_pct + "%" : "—"} (50% = dead center). Small ticks mark today's high and low.`}>
        <span className="emx-range-edge down">{fmt$(em.lower)}</span>
        <div className="emx-range-bar">
          {pos(data.day_low) != null && <span className="emx-tick" style={{ left: pos(data.day_low) + "%" }} title={`Today's low ${fmt$(data.day_low)}`} />}
          {pos(data.day_high) != null && <span className="emx-tick" style={{ left: pos(data.day_high) + "%" }} title={`Today's high ${fmt$(data.day_high)}`} />}
          {pos(data.spot) != null && <span className="emx-dot" style={{ left: pos(data.spot) + "%" }} title={`Now ${fmt$(data.spot)}`} />}
        </div>
        <span className="emx-range-edge up">{fmt$(em.upper)}</span>
      </div>

      <div className="emx-cmp">
        <div className="emx-cmp-row" title={`The stock's AVERAGE actual move over ${data.dte}-day stretches during the past year (${cmp.avg_actual_windows || 0} samples). Comparing what options PRICE vs what the stock actually DOES tells you if premium is rich or cheap.`}>
          <span className="emx-cmp-lbl">Avg actual {data.dte}d move</span>
          <b>{cmp.avg_actual_pct != null ? "±" + cmp.avg_actual_pct.toFixed(2) + "%" : "—"}</b>
          {ratioChip && <span className={`emx-chip ${ratioChip.cls}`}>{ratioChip.txt}</span>}
        </div>
        {cmp.prev && cmp.prev.em_pct != null && (
          <div className="emx-cmp-row" title={`The last saved expected-move reading for this same expiration (taken ${cmp.prev.date}). A rising EM means the market is pricing MORE movement than before; falling means vol is bleeding out.`}>
            <span className="emx-cmp-lbl">Previous EM reading</span>
            <b>±{cmp.prev.em_pct.toFixed(2)}%</b>
            {prevDelta != null && prevDelta !== 0 && (
              <span className={`emx-chip ${prevDelta > 0 ? "warn" : "up"}`}>{prevDelta > 0 ? "+" : ""}{prevDelta} pts since {fmtUSDate(cmp.prev.date)}</span>
            )}
          </div>
        )}
        {cmp.post_earnings_avg_pct != null && (
          <div className="emx-cmp-row" title={`Average absolute move the stock ACTUALLY made the day after its recent earnings reports. Earnings in ${cmp.days_to_earnings} day${cmp.days_to_earnings === 1 ? "" : "s"} (${fmtUSDate(cmp.next_earnings)}). If the EM is far below this, the market may be underpricing the event.`}>
            <span className="emx-cmp-lbl">Avg post-earnings move</span>
            <b>±{Number(cmp.post_earnings_avg_pct).toFixed(2)}%</b>
            <span className="emx-chip earn">earnings in {cmp.days_to_earnings}d</span>
          </div>
        )}
        <div className="emx-cmp-row" title="Today's traded range so far, and how much of the full expected move today's swing (vs yesterday's close) has already consumed. If most of the EM is gone, chasing gets expensive.">
          <span className="emx-cmp-lbl">Today's range</span>
          <b>{data.day_low != null && data.day_high != null ? `${fmt$(data.day_low)} – ${fmt$(data.day_high)}` : "—"}</b>
          {sum.used_pct != null && (
            <span className={`emx-chip ${sum.used_pct >= 70 ? "warn" : ""}`}>{sum.used_pct}% of EM used today</span>
          )}
        </div>
        {(nearSup || nearRes) && (
          <div className="emx-cmp-row" title="Nearest support/resistance from daily-chart pivot levels (touch count in parentheses). If the expected range extends past a heavily-touched level, that level is where the move is likely to stall.">
            <span className="emx-cmp-lbl">Support / Resistance</span>
            <b>
              {nearSup ? <span className="up">{fmt$(nearSup.price)} ({nearSup.touches}×)</span> : "—"}
              {" / "}
              {nearRes ? <span className="down">{fmt$(nearRes.price)} ({nearRes.touches}×)</span> : "—"}
            </b>
            {nearRes && em.upper > nearRes.price && <span className="emx-chip">upper band sits past resistance</span>}
            {nearSup && em.lower < nearSup.price && <span className="emx-chip">lower band sits past support</span>}
          </div>
        )}
      </div>

      <div className="emx-summary">
        <span className={`emx-chip big ${sum.size_verdict === "unusually large" || sum.size_verdict === "large" ? "warn" : sum.size_verdict === "unusually small" || sum.size_verdict === "small" ? "up" : ""}`}
              title="Is this expected move big or small FOR THIS STOCK? Compares the options-implied % move against the stock's own average actual move over the same number of days.">
          EM {sum.size_verdict || "—"}
        </span>
        <span className={`emx-chip big ${volTone}`}
              title={`Is the options market pricing elevated volatility? ATM IV ${em.iv != null ? (em.iv * 100).toFixed(1) + "%" : "—"} vs 20-day realized vol ${cmp.hv20 != null ? cmp.hv20 + "%" : "—"} (${cmp.iv_vs_hv != null ? cmp.iv_vs_hv + "×" : "—"})${cmp.iv_rank != null ? ` · IV rank ${cmp.iv_rank}` : ""}${cmp.hv_percentile != null ? ` · realized vol at its ${cmp.hv_percentile}th percentile this year` : ""}. Elevated = premium selling has an edge; subdued = long options are cheap.`}>
          vol {sum.vol_state || "—"}
        </span>
        <span className="emx-chip big"
              title="Room left inside the expected range from the current price. Once one side is nearly exhausted, continuation trades in that direction fight the odds the options market has priced.">
          ↑ {fmt$(sum.remaining_up)} ({sum.remaining_up_pct != null ? sum.remaining_up_pct.toFixed(1) : "—"}%) · ↓ {fmt$(sum.remaining_down)} ({sum.remaining_down_pct != null ? sum.remaining_down_pct.toFixed(1) : "—"}%)
        </span>
        {sum.rr_up != null && (
          <span className={`emx-chip big ${sum.rr_up >= 1.5 ? "up" : sum.rr_up <= 0.67 ? "warn" : ""}`}
                title={`Upside room ÷ downside room inside the band, from the current price. ${sum.rr_up}:1 — above ~1.5 the entry favors longs (more room up than down); below ~0.67 it favors shorts/put buyers. Near 1.0 the price sits mid-range with no positioning edge.`}>
            R:R {sum.rr_up}:1 {sum.rr_up >= 1.5 ? "favors upside" : sum.rr_up <= 0.67 ? "favors downside" : "balanced"}
          </span>
        )}
      </div>
      {sum.headline && (
        <div className="emx-headline" title="One-line read of everything above: the priced move, whether it's unusual, how much is already used, and which side of the vol trade has the edge.">
          {sum.headline}
        </div>
      )}
    </div>
  );
}

// ── Reversal Radar (v3.19) ─────────────────────────────────────────────────
// The app's core mission on one screen: ranked LONG candidates parked near
// their low of day and SHORT candidates parked near their high, scored
// 0-100 on stretch / exhaustion / location / confirmation / context, with a
// trend-day guard, structure-derived trade tickets, and one-click journaling.

function RRSpark({ points, side }) {
  if (!points || points.length < 3) return null;
  const w = 88, h = 26;
  const mn = Math.min(...points), mx = Math.max(...points);
  const rng = mx - mn || 1;
  const pts = points.map((p, i) => `${(i / (points.length - 1)) * w},${h - 2 - ((p - mn) / rng) * (h - 4)}`).join(" ");
  return (
    <svg className="rr-spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <polyline points={pts} fill="none" stroke={side === "long" ? "var(--up)" : "var(--down)"} strokeWidth="1.5" opacity="0.9" />
    </svg>
  );
}

function RRRow({ r, expanded, onToggle, onSwitchTicker, onOpenIntraday, onOpenFinviz, apiFetch }) {
  const [logged, setLogged] = useState(false);
  const scoreCls = r.score >= 80 ? "rr-hot" : r.score >= 70 ? "rr-warm" : "rr-cool";
  const tk = r.ticket || {};
  const logPick = async () => {
    try {
      await apiFetch("/api/pick_journal", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: r.symbol, kind: "radar", side: r.side, score: r.score,
          price: tk.entry, stop: tk.stop, t1: tk.t1, t2: tk.t2,
          note: `Radar ${r.side} ${r.score}: ${(r.reasons || []).join("; ")}`,
        }),
      });
      setLogged(true);
    } catch (e) { console.warn("radar log failed", e); }
  };
  return (
    <div className={`rr-row ${expanded ? "rr-open" : ""}`}>
      <div className="rr-main" onClick={onToggle}
           title={`Score ${r.score}/100 — stretch ${r.groups?.stretch ?? "—"} · exhaustion ${r.groups?.exhaustion ?? "—"} · location ${r.groups?.location ?? "—"} · confirmation ${r.groups?.confirmation ?? "—"} · context ${r.groups?.context ?? "—"}. Click for the trade plan.`}>
        <span className={`rr-score ${scoreCls}`}>{r.score}</span>
        <span className="rr-sym">{r.symbol}</span>
        <span className="rr-px">{fmt$(r.last)}</span>
        <span className={`rr-stretch ${r.side === "long" ? "down" : "up"}`}
              title="Distance from session VWAP in volume-weighted standard deviations — the volatility-normalized measure of 'stretched'.">
          {r.stretch != null ? `${r.stretch > 0 ? "+" : ""}${r.stretch}σ` : "—"}
        </span>
        <RRSpark points={r.spark} side={r.side} />
        <span className="rr-reasons">
          {(r.reasons || []).slice(0, 2).map((x, i) => <span key={i} className="rr-chip">{x}</span>)}
          {(r.flags || []).length > 0 && <span className="rr-flag" title={r.flags.join("\n")}>⚠</span>}
        </span>
        {tk.rr != null && <span className={`rr-rr ${tk.rr >= 1.5 ? "up" : tk.rr < 1 ? "down" : ""}`}
              title="Reward-to-risk to target 1 (VWAP) from the current price against the structure stop.">{tk.rr}R</span>}
      </div>
      {expanded && (
        <div className="rr-detail">
          <div className="rr-ticket">
            {[["Entry", tk.entry, "Current price — the zone the signal fired from."],
              ["Trigger", tk.trigger, "Confirmation trigger: break of the most recent 5-minute swing against the extreme. Enter aggressive at the entry zone, or wait for this."],
              ["Stop", tk.stop, "Structure stop: the day's extreme padded by 0.25× the 5-minute ATR. If this trades, the reversal thesis is wrong — exit."],
              ["T1 · VWAP", tk.t1, "First target = session VWAP, the natural magnet for any mean-reversion bounce. Where the trade pays."],
              ["T2 · Open", tk.t2, "Stretch target = the session open. Only for the strongest reversals."],
              ["R:R", tk.rr != null ? `${tk.rr}:1` : "—", "Reward-to-risk to T1. Below 1.5 the entry is late — wait for a pullback or skip."]]
              .map(([lbl, v, tip]) => (
                <div key={lbl} className="rr-tk" title={tip}>
                  <span>{lbl}</span><b>{typeof v === "number" ? fmt$(v) : (v || "—")}</b>
                </div>
              ))}
          </div>
          {(r.reasons || []).length > 2 && (
            <div className="rr-all-reasons">{r.reasons.slice(2).map((x, i) => <span key={i} className="rr-chip">{x}</span>)}</div>
          )}
          {(r.flags || []).map((f, i) => <div key={i} className="rr-flagline">⚠ {f}</div>)}
          <div className="rr-actions">
            <button className="rr-btn" onClick={() => (onOpenIntraday ? onOpenIntraday(r.symbol) : onSwitchTicker && onSwitchTicker(r.symbol))}
                    title="Load this symbol on the Trade tab in 1-Min chart mode — VWAP bands, levels, and radar markers already drawn.">
              Chart →
            </button>
            {onOpenFinviz && (
              <button className="rr-btn" onClick={() => onOpenFinviz(r.symbol)}
                      title="Open this symbol in the embedded Finviz tab — fundamentals, news, insider activity, short interest.">
                Finviz →
              </button>
            )}
            <button className={`rr-btn ${logged ? "rr-logged" : ""}`} onClick={logPick} disabled={logged}
                    title="Write this signal + plan into the Picks Journal. (Signals scoring 70+ are also auto-logged server-side for the hit-rate report.)">
              {logged ? "Logged ✓" : "Log pick"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ReversalRadarCard({ apiFetch, onSwitchTicker, onOpenIntraday, onOpenFinviz }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState(null); // "SYM|side"
  useEffect(() => {
    let stop = false;
    const load = () => sharedJson(apiFetch, "/api/radar", 15 * 1000)
      .then(d => { if (!stop) { if (d && !d.error) { setData(d); setErr(null); } else setErr((d && d.error) || "no data"); } })
      .catch(e => { if (!stop) setErr(String((e && e.message) || e)); });
    load();
    const t = setInterval(skipWhenHidden(load), 20 * 1000);
    return () => { stop = true; clearInterval(t); };
  }, []);

  const regime = (data && data.regime) || {};
  const regimeCls = regime.verdict === "trend_down" || regime.verdict === "trend_up" ? "rr-trend"
    : regime.verdict === "rotational" ? "rr-rot" : "rr-unk";
  const updated = data && data.as_of ? (() => { try { return new Date(data.as_of).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" }); } catch (e) { return ""; } })() : "";

  const stack = (side, rows, title, sub) => (
    <div className="rr-col">
      <div className="rr-col-head" title={side === "long"
        ? "Stocks parked in the bottom of their day range showing reversal evidence — candidates to BUY before the bounce is obvious. Ranked by composite score."
        : "Stocks parked at the top of their day range showing exhaustion — candidates to SHORT before the fade is obvious. Ranked by composite score."}>
        <span className={`rr-col-title ${side === "long" ? "up" : "down"}`}>{title}</span>
        <span className="rr-col-sub">{sub}</span>
      </div>
      {(rows || []).length === 0 ? (
        <div className="rr-empty">{data && data.market_open ? "Nothing qualifying yet — the radar only surfaces real candidates." : "—"}</div>
      ) : rows.map(r => (
        <RRRow key={`${r.symbol}|${r.side}`} r={r} apiFetch={apiFetch}
               expanded={open === `${r.symbol}|${r.side}`}
               onToggle={() => setOpen(open === `${r.symbol}|${r.side}` ? null : `${r.symbol}|${r.side}`)}
               onSwitchTicker={onSwitchTicker} onOpenIntraday={onOpenIntraday} onOpenFinviz={onOpenFinviz} />
      ))}
    </div>
  );

  return (
    <div className="card rr-card" style={{ marginBottom: "var(--row-gap)" }}>
      <div className="card-head">
        <div>
          <div className="kicker" title="Two-stage scan: a free quote screen across the whole watchlist finds stocks parked near their day extreme, then minute-bar analysis (VWAP stretch, volume climax, level confluence, 5-minute structure) scores the best candidates 0-100. Sub-$5B market caps are excluded. Refreshes about once a minute during market hours. Signals 80+ toast in-app; 85+ also push to your phone (when push is configured).">
            reversal radar · $5B+ · {data ? `${data.universe} scanned` : "…"}{updated ? ` · ${updated}` : ""}
          </div>
          <div className="card-title">Bottoms &amp; Tops — live</div>
        </div>
        {data && !data.market_open && <span className="rr-closed" title="The radar only scans 9:30–16:00 ET on trading days. Last session's board stays visible.">market closed</span>}
      </div>
      {regime.label && (
        <div className={`rr-regime ${regimeCls}`} title={`${regime.detail || ""}${regime.spy_above_vwap_pct != null ? ` SPY above VWAP ${regime.spy_above_vwap_pct}% of the last 90 min; QQQ ${regime.qqq_above_vwap_pct}%.` : ""} On a trend day, counter-trend scores are capped at 60 — the radar will not talk you into fading a freight train.`}>
          {regime.label}
        </div>
      )}
      {err && !data && <div className="rr-empty">{err} — retrying…</div>}
      {data && data.error && (
        <div className="pj-note" title="The Schwab client caps the whole app at 110 requests/min. When a pass gets squeezed, the radar keeps the last good stacks instead of blanking.">{data.error}</div>
      )}
      {data && (
        <div className="rr-cols">
          {stack("long", data.long, "LONGS — near low of day", "buy the bounce")}
          {stack("short", data.short, "SHORTS — near high of day", "fade the rip")}
        </div>
      )}
    </div>
  );
}

// Hit-rate report: how the radar's own logged signals actually resolved.
function RadarReportCard({ apiFetch }) {
  const [rep, setRep] = useState(null);
  useEffect(() => {
    let stop = false;
    sharedJson(apiFetch, "/api/radar/report", 5 * 60 * 1000)
      .then(d => { if (!stop && d && !d.error) setRep(d); })
      .catch(() => {});
    return () => { stop = true; };
  }, []);
  if (!rep) return null;
  return (
    <div className="card rr-report" style={{ marginBottom: "var(--row-gap)" }}>
      <div className="card-head">
        <div>
          <div className="kicker" title="Every radar signal scoring 70+ is auto-logged with its plan, then resolved against the tape: did price hit T1 (VWAP) before the stop? This table is the evidence that tunes the score — trust buckets that prove themselves.">
            radar performance · {rep.total_signals} signals logged
          </div>
          <div className="card-title">Did the signals pay?</div>
        </div>
      </div>
      {(!rep.buckets || rep.buckets.length === 0) ? (
        <div className="rr-empty">No resolved signals yet — this fills in automatically as the radar logs live signals (score ≥ 70) and watches whether they hit target or stop.</div>
      ) : (
        <table className="rr-rep-table">
          <thead><tr>
            <th title="Signal score bucket">Score</th><th title="Long or short side">Side</th>
            <th className="num" title="Signals logged">N</th>
            <th className="num" title="Hit T1 (VWAP) before the stop">T1</th>
            <th className="num" title="Stopped out">Stop</th>
            <th className="num" title="Neither hit while watched; marked at that day's close">Exp</th>
            <th className="num" title="Still open / unresolved">Open</th>
            <th className="num" title="T1 hits as % of resolved signals">Hit %</th>
            <th className="num" title="Average R multiple across resolved signals (stop = −1R)">Avg R</th>
          </tr></thead>
          <tbody>
            {rep.buckets.map((b, i) => (
              <tr key={i}>
                <td>{b.bucket}</td>
                <td className={b.side === "long" ? "up" : "down"}>{b.side}</td>
                <td className="num">{b.signals}</td>
                <td className="num up">{b.t1}</td>
                <td className="num down">{b.stop}</td>
                <td className="num">{b.expired}</td>
                <td className="num">{b.open}</td>
                <td className="num">{b.hit_rate != null ? `${b.hit_rate}%` : "—"}</td>
                <td className={`num ${b.avg_r > 0 ? "up" : b.avg_r < 0 ? "down" : ""}`}>{b.avg_r != null ? b.avg_r : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {rep.hours && rep.hours.length > 0 && (
        <div className="rr-hours" title="Hit rate by the time band the signal fired in. Expect 9:30-10 to underperform — the open drive punishes fading; that is why early signals get a score penalty.">
          {rep.hours.map((h, i) => (
            <span key={i} className="rr-chip">{h.band}: {h.hit_rate != null ? `${h.hit_rate}%` : "—"} ({h.n})</span>
          ))}
        </div>
      )}
      {rep.tuning && (Object.keys(rep.tuning.learned_tod || {}).length > 0 || (rep.tuning.suggestions || []).length > 0) && (
        <div className="rr-tuning" title={`Self-tuning from evidence: once a time band has ${rep.tuning.min_n}+ resolved signals, its own hit rate adjusts the score automatically (bad band −5, great band +3). Suggestions below are the human-readable version of what the data says.`}>
          {Object.entries(rep.tuning.learned_tod || {}).map(([band, adj]) => (
            <span key={band} className={`rr-chip ${adj > 0 ? "up" : "down"}`}>auto: {band} {adj > 0 ? "+" : ""}{adj} pts</span>
          ))}
          {(rep.tuning.suggestions || []).map((s, i) => (
            <div key={i} className="rr-suggestion">→ {s}</div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Premium Juice scanner (v3.22) ──────────────────────────────────────────
// Which stocks still have the most premium left with almost no time on the
// clock? Ranked 0-3 DTE straddle richness with ready-made selling structures,
// defined vs undefined risk clearly separated. Sortable, filterable, fast.

const PJ_STRAT_NAMES = {
  short_strangle: "Short Strangle", iron_condor: "Iron Condor",
  iron_fly: "Iron Fly", put_credit_spread: "Put Credit Spread",
  call_credit_spread: "Call Credit Spread", csp: "Cash-Secured Put",
  covered_call: "Covered Call",
};

function PJStrategy({ s }) {
  const F = (v) => v == null ? "—" : (typeof v === "number" ? (Math.abs(v) >= 100 ? "$" + v.toLocaleString() : fmt$(v)) : v);
  // Strike formatter — strips floating-point noise (1026.6399999 → 1026.64).
  const K = (v) => v == null ? "—" : String(+(+v).toFixed(2));
  const rows = [];
  const push = (lbl, v, tip) => rows.push([lbl, v, tip]);
  if (s.kind === "short_strangle") {
    push("Strikes", `${K(s.put_strike)}P / ${K(s.call_strike)}C`, "Suggested short put and short call — ~18-delta or one expected move out, whichever the chain supports.");
    push("Credit", fmt$(s.credit), "Total credit for selling both sides (per share; ×100 per contract).");
    push("Break-evens", `${fmt$(s.be_low)} – ${fmt$(s.be_high)}`, "Profitable at expiration anywhere inside this range.");
    push("Max profit", F(s.max_profit), "Full credit if both sides expire worthless.");
    push("Buying power", F(s.bp), "Approximate margin requirement (max(20% spot − OTM, 10% strike) + credit — broker formulas vary).");
    push("POP", s.pop != null ? s.pop + "%" : "—", "Probability of profit ≈ 1 − (short call delta + |short put delta|). Approximation, not a fill.");
    push("EM coverage", s.em_coverage != null ? s.em_coverage + "×" : "—", "Nearest strike distance ÷ the expected move. Above 1.0 = strikes sit outside the priced move.");
    push("Strike distance", `−${s.put_dist_pct}% / +${s.call_dist_pct}%`, "How far each short strike sits from the current price.");
    push("Exit / stop", `take ${fmt$(s.exit_target)} · stop ${fmt$(s.stop_level)}`, "Suggested management: buy back at 50% of the credit; stop or adjust if the position marks at 2× the credit received.");
  } else if (s.kind === "iron_condor" || s.kind === "iron_fly") {
    push("Strikes", s.kind === "iron_condor"
      ? `${K(s.put_wing)}/${K(s.put_strike)}P · ${K(s.call_strike)}/${K(s.call_wing)}C`
      : `${K(s.put_wing)}/${K(s.short_strike)}/${K(s.call_wing)}`,
      s.kind === "iron_condor" ? "Short strangle with protective wings." : "Short the ATM straddle, wings ~1 expected move out.");
    push("Credit", fmt$(s.credit), "Net credit after buying the wings.");
    push("Break-evens", `${fmt$(s.be_low)} – ${fmt$(s.be_high)}`, "Profitable range at expiration.");
    push("Max profit / loss", `${F(s.max_profit)} / ${F(s.max_loss)}`, "Defined risk: worst case is the wing width minus the credit — known before entry.");
    push("Return on risk", s.ror != null ? s.ror + "%" : "—", "Max profit ÷ max loss.");
    push("POP", s.pop != null ? s.pop + "%" : "—", "Probability of profit approximation.");
  } else if (s.kind.endsWith("credit_spread")) {
    push("Strikes", `${K(s.short_strike)} / ${K(s.long_strike)}`, "Sell the short strike, buy the long strike for protection.");
    push("Credit", fmt$(s.credit), "Net credit received.");
    push("Break-even", fmt$(s.be), "Short strike adjusted by the credit.");
    push("Max profit / loss", `${F(s.max_profit)} / ${F(s.max_loss)}`, "Defined risk — width minus credit is the most you can lose.");
    push("Return on risk", s.ror != null ? s.ror + "%" : "—", "Max profit ÷ max loss.");
    push("POP", s.pop != null ? s.pop + "%" : "—", "≈ 1 − |short strike delta|.");
  } else {
    push("Strike", K(s.short_strike), s.kind === "csp" ? "Sell this put with cash to cover assignment." : "Sell this call against 100 shares.");
    push("Credit", fmt$(s.credit), "Premium collected per share.");
    push("Break-even", fmt$(s.be), s.kind === "csp" ? "Effective cost basis if assigned." : "Shares called away above this = still profitable.");
    if (s.bp != null) push("Cash required", F(s.bp), "Cash to secure the put (strike × 100).");
    push("Yield", s.yield_pct != null ? s.yield_pct + "%" : "—", "Credit as % of the collateral, for this expiration alone.");
    push("POP", s.pop != null ? s.pop + "%" : "—", "≈ 1 − |delta|.");
  }
  return (
    <div className={`pj-strat ${s.risk === "undefined" ? "pj-undef" : "pj-def"}`}>
      <div className="pj-strat-head">
        <b>{PJ_STRAT_NAMES[s.kind] || s.kind}</b>
        <span className={`pj-risk ${s.risk === "undefined" ? "warn" : "ok"}`}
              title={s.risk === "undefined"
                ? "UNDEFINED RISK: losses are theoretically unlimited (calls) or down to zero (puts). Only appropriate with a margin account, small size, and active management."
                : "DEFINED RISK: the maximum loss is fixed and known before entry."}>
          {s.risk === "undefined" ? "UNDEFINED RISK" : "defined risk"}
        </span>
      </div>
      <div className="pj-strat-grid">
        {rows.map(([lbl, v, tip], i) => (
          <div key={i} className="pj-strat-row" title={tip}><span>{lbl}</span><b>{v}</b></div>
        ))}
      </div>
    </div>
  );
}

function PremiumJuiceCard({ apiFetch, onSwitchTicker, onOpenFinviz }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState(null);
  const [sortKey, setSortKey] = useState("score");
  const [sortDir, setSortDir] = useState(-1);
  const [flt, setFlt] = useState({ dte: "all", minIV: "", minPrem: "", minVol: "",
                                   maxSpread: "", minOI: "", earnings: "all", definedOnly: false });
  useEffect(() => {
    let stop = false;
    const load = () => sharedJson(apiFetch, "/api/juice", 30 * 1000)
      .then(d => { if (!stop) { if (d && !d.error) { setData(d); setErr(null); } else setErr((d && d.error) || "no data"); } })
      .catch(e => { if (!stop) setErr(String((e && e.message) || e)); });
    load();
    const t = setInterval(skipWhenHidden(load), 45 * 1000);
    return () => { stop = true; clearInterval(t); };
  }, []);

  const sortBy = (k) => { if (sortKey === k) setSortDir(d => -d); else { setSortKey(k); setSortDir(-1); } };
  const arrow = (k) => sortKey === k ? (sortDir < 0 ? " ↓" : " ↑") : "";
  const num = (v, f) => v == null ? "—" : f(v);

  const rows = useMemo(() => {
    let r = (data && data.rows) || [];
    if (flt.dte !== "all") r = r.filter(x => x.dte === Number(flt.dte));
    if (flt.minIV) r = r.filter(x => x.atm_iv != null && x.atm_iv * 100 >= Number(flt.minIV));
    if (flt.minPrem) r = r.filter(x => x.em_pct != null && x.em_pct >= Number(flt.minPrem));
    if (flt.minVol) r = r.filter(x => (x.total_vol || 0) >= Number(flt.minVol));
    if (flt.maxSpread) r = r.filter(x => x.spread_pct != null && x.spread_pct <= Number(flt.maxSpread));
    if (flt.minOI) r = r.filter(x => (x.total_oi || 0) >= Number(flt.minOI));
    if (flt.earnings === "with") r = r.filter(x => x.earnings_inside);
    if (flt.earnings === "without") r = r.filter(x => !x.earnings_inside);
    const get = (x) => { const v = x[sortKey]; return v == null ? -Infinity : v; };
    return [...r].sort((a, b) => (get(a) < get(b) ? 1 : get(a) > get(b) ? -1 : 0) * (sortDir < 0 ? 1 : -1));
  }, [data, flt, sortKey, sortDir]);

  const upd = data && data.as_of ? (() => { try { return new Date(data.as_of).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }); } catch (e) { return ""; } })() : "";
  const setF = (k, v) => setFlt(f => ({ ...f, [k]: v }));

  const HEADERS = [
    ["score", "Juice", "Juice Score 0-100: premium richness per day left (40) + liquidity (25) + options activity (10) + support/resistance structure (15) + context (10). Higher = more premium for the time and risk."],
    ["symbol", "Sym", "Ticker — click a row for full detail and ready-made structures."],
    ["spot", "Price", "Current stock price (chain underlying quote)."],
    ["dte", "DTE", "Calendar days to the nearest expiration in the 0-3 day window. 0 = expires today."],
    ["atm_iv", "ATM IV", "At-the-money implied volatility for this expiration, with IV rank underneath (falls back to realized-vol rank while IV history builds)."],
    ["em_pct", "Straddle", "ATM call mid + put mid — the market's expected move AND the max collectible double-sided premium, as $ and % of the stock price."],
    ["prem_per_day", "$/day", "Straddle % of spot ÷ trading days remaining — THE ranking stat: how much premium is left per unit of time. 0DTE uses hours to the close."],
    ["iv_vs_hv", "IV/HV", "ATM IV ÷ 20-day realized vol. Above ~1.25 the market is paying more than the stock has been moving — the seller's edge."],
    ["total_vol", "Vol", "Total option volume at this expiration (calls + puts)."],
    ["total_oi", "OI", "Total open interest at this expiration."],
    ["spread_pct", "Sprd", "ATM bid-ask spread as % of mid, averaged across call and put. Above 5% is hard to exit."],
  ];

  return (
    <div className="card pj-card" style={{ marginBottom: "var(--row-gap)" }}>
      <div className="card-head">
        <div>
          <div className="kicker" title="Two-stage scan: the $5B+ watchlist board ranked by realized-vol richness, earnings proximity and day movement, then one light chain call (today through +3 days) per candidate. Rescans every ~4 minutes while you watch. All POP and buying-power figures are standard approximations — verify in your broker before entering.">
            premium juice · 0-3 DTE · {data ? `${data.scanned || 0}/${data.universe || 0} scanned` : "…"}{upd ? ` · ${upd}` : ""}
          </div>
          <div className="card-title">Who still has premium left?</div>
        </div>
        {data && !data.market_open && <span className="rr-closed" title="Scans 9:30–16:00 ET. Last session's board stays visible.">market closed</span>}
      </div>

      <div className="pj-filters">
        <div className="seg" title="Filter by days to expiration. 0 DTE = expires today (Thursday/Friday juice).">
          {["all", "0", "1", "2", "3"].map(v => (
            <button key={v} className={flt.dte === v ? "active" : ""} onClick={() => setF("dte", v)}>
              {v === "all" ? "All" : v + " DTE"}
            </button>
          ))}
        </div>
        <label className="pj-f" title="Minimum at-the-money implied volatility, in percent (e.g. 60).">
          IV≥ <input type="number" value={flt.minIV} onChange={e => setF("minIV", e.target.value)} placeholder="%" />
        </label>
        <label className="pj-f" title="Minimum straddle premium as a percent of the stock price (e.g. 1.5).">
          Prem≥ <input type="number" value={flt.minPrem} onChange={e => setF("minPrem", e.target.value)} placeholder="%" />
        </label>
        <label className="pj-f" title="Minimum total option volume at the expiration.">
          Vol≥ <input type="number" value={flt.minVol} onChange={e => setF("minVol", e.target.value)} placeholder="#" />
        </label>
        <label className="pj-f" title="Maximum ATM bid-ask spread as % of mid — your entry AND exit cost.">
          Sprd≤ <input type="number" value={flt.maxSpread} onChange={e => setF("maxSpread", e.target.value)} placeholder="%" />
        </label>
        <label className="pj-f" title="Minimum total open interest at the expiration.">
          OI≥ <input type="number" value={flt.minOI} onChange={e => setF("minOI", e.target.value)} placeholder="#" />
        </label>
        <div className="seg" title="Earnings filter: 'earnings' = report lands BEFORE this expiration (IV-crush setups — highest premium, highest risk); 'clean' = no earnings in the window.">
          {[["all", "All"], ["with", "Earnings"], ["without", "Clean"]].map(([v, l]) => (
            <button key={v} className={flt.earnings === v ? "active" : ""} onClick={() => setF("earnings", v)}>{l}</button>
          ))}
        </div>
        <button className={`pj-toggle ${flt.definedOnly ? "active" : ""}`}
                onClick={() => setF("definedOnly", !flt.definedOnly)}
                title="Show only defined-risk structures (spreads, condors, flies, CSP/CC) in the strategy panels — hides the short strangle.">
          Defined risk only
        </button>
      </div>

      {err && !data && <div className="rr-empty">{err} — retrying…</div>}
      {data && data.error && (
        <div className="pj-note" title="The Schwab client caps the whole app at 110 requests/min. When a scan cycle gets squeezed (radar + juice + browsing at once), the board keeps the last good rows instead of blanking and refreshes on the next cycle.">
          {data.error}
        </div>
      )}
      {data && rows.length === 0 && (
        <div className="rr-empty">{data.market_open ? (data.rows || []).length === 0 ? "Scanning chains… first pass takes ~30 seconds." : "Nothing passes the current filters." : "Market closed — the board fills during the session."}</div>
      )}
      {rows.length > 0 && (
        <div className="pj-table-wrap">
          <table className="pj-table">
            <thead><tr>
              {HEADERS.map(([k, l, tip]) => (
                <th key={k} className={k !== "symbol" ? "num" : ""} onClick={() => sortBy(k)} title={tip + " Click to sort."}>{l}{arrow(k)}</th>
              ))}
              <th title="Earnings before this expiration?">Earn</th>
            </tr></thead>
            <tbody>
              {rows.map(r => {
                const key = r.symbol + r.expiry;
                const strategies = (r.strategies || []).filter(s => !flt.definedOnly || s.risk === "defined");
                return (
                  <React.Fragment key={key}>
                    <tr className={`pj-row ${open === key ? "pj-open" : ""} ${r.stale ? "pj-stale" : ""}`} onClick={() => setOpen(open === key ? null : key)}
                        title={`${r.company || r.symbol} — ${(r.reasons || []).join(" · ") || "click for detail"}`}>
                      <td className="num"><span className={`rr-score ${r.score >= 80 ? "rr-hot" : r.score >= 65 ? "rr-warm" : "rr-cool"}`}>{r.score}</span></td>
                      <td className="pj-sym">{r.symbol}{(r.flags || []).length > 0 && <span className="rr-flag" title={r.flags.join("\n")}> ⚠</span>}</td>
                      <td className="num">{fmt$(r.spot)}</td>
                      <td className="num">{r.dte === 0 ? <b className="pj-0dte">0d</b> : r.dte + "d"}</td>
                      <td className="num">{r.atm_iv != null ? (r.atm_iv * 100).toFixed(0) + "%" : "—"}
                        <div className="pj-sub">{r.iv_rank != null ? "rank " + Math.round(r.iv_rank) : ""}</div></td>
                      <td className="num">{fmt$(r.straddle)}<div className="pj-sub">±{r.em_pct}%</div></td>
                      <td className="num"><b>{num(r.prem_per_day, v => v.toFixed(1) + "%")}</b></td>
                      <td className={`num ${r.iv_vs_hv >= 1.25 ? "up" : ""}`}>{num(r.iv_vs_hv, v => v.toFixed(2) + "×")}</td>
                      <td className="num">{fmtVol(r.total_vol)}</td>
                      <td className="num">{fmtVol(r.total_oi)}</td>
                      <td className={`num ${r.spread_pct > 5 ? "down" : ""}`}>{num(r.spread_pct, v => v.toFixed(1) + "%")}</td>
                      <td>{r.earnings_inside ? <span className="emx-chip earn" title={`Earnings ${r.next_earnings || ""} lands BEFORE this expiration — that is why the premium is fat. IV crush play; defined risk preferred.`}>📊</span> : ""}</td>
                    </tr>
                    {open === key && (
                      <tr className="pj-detail-row"><td colSpan={12}>
                        <div className="pj-detail">
                          <div className="pj-quotes">
                            <div className="pj-q" title="ATM call market: bid × ask (mid), with this strike's volume and open interest.">
                              <span>Call {r.atm_strike}</span>
                              <b>{fmt$(r.call_bid)} × {fmt$(r.call_ask)} <i>({fmt$(r.call_mid)})</i></b>
                              <em>vol {fmtVol(r.call_vol)} · OI {fmtVol(r.call_oi)}</em>
                            </div>
                            <div className="pj-q" title="ATM put market: bid × ask (mid), with this strike's volume and open interest.">
                              <span>Put {r.atm_strike}</span>
                              <b>{fmt$(r.put_bid)} × {fmt$(r.put_ask)} <i>({fmt$(r.put_mid)})</i></b>
                              <em>vol {fmtVol(r.put_vol)} · OI {fmtVol(r.put_oi)}</em>
                            </div>
                            <div className="pj-q" title="Relative options activity: today's total volume ÷ total open interest at this expiration. Above 1× = unusually busy.">
                              <span>Vol / OI</span><b>{num(r.vol_oi, v => v.toFixed(2) + "×")}</b>
                              <em>HV20 {r.hv20 != null ? r.hv20 + "%" : "—"}</em>
                            </div>
                            <div className="pj-q" title="Nearest daily-chart support and resistance pivots, with distance from the current price. Strikes beyond a defended level are safer to sell.">
                              <span>S / R</span>
                              <b>
                                {r.support ? <span className="up">{fmt$(r.support.price)}</span> : "—"}{" / "}
                                {r.resistance ? <span className="down">{fmt$(r.resistance.price)}</span> : "—"}
                              </b>
                              <em>{r.support ? ((r.spot - r.support.price) / r.spot * 100).toFixed(1) + "% below" : ""}{r.resistance ? ` · ${((r.resistance.price - r.spot) / r.spot * 100).toFixed(1)}% above` : ""}</em>
                            </div>
                          </div>
                          {(r.reasons || []).length > 0 && (
                            <div className="rr-all-reasons">{r.reasons.map((x, i) => <span key={i} className="rr-chip">{x}</span>)}</div>
                          )}
                          {(r.flags || []).map((f, i) => <div key={i} className="rr-flagline">⚠ {f}</div>)}
                          <div className="pj-strats">
                            {strategies.map((s, i) => <PJStrategy key={i} s={s} />)}
                            {strategies.length === 0 && <div className="rr-empty">No structures pass the defined-risk filter for this name.</div>}
                          </div>
                          <div className="rr-actions">
                            <button className="rr-btn" onClick={(e) => { e.stopPropagation(); onSwitchTicker && onSwitchTicker(r.symbol); }}
                                    title="Load this symbol on the Trade tab for the full chain and strike workbench.">Trade tab →</button>
                            {onOpenFinviz && (
                              <button className="rr-btn" onClick={(e) => { e.stopPropagation(); onOpenFinviz(r.symbol); }}
                                      title="Open this symbol in the embedded Finviz tab — fundamentals, float, short interest, news.">Finviz →</button>
                            )}
                          </div>
                        </div>
                      </td></tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Cookie-setup chip (v3.37, reworked v3.41) ───────────────────────────────
// Two jobs now: (1) nudge ANY helper older than v2.7 to update — v2.7 both
// removes the Chrome frame-reload regression and adds the empirical cookie-
// header fallback that finally makes Comet/Brave logins stick; (2) once the
// fallback is actually active (the helper SAW this browser drop cookies on a
// frame request), show a quiet informational chip instead of a warning.
function CookieSetupChip() {
  const read = () => {
    try {
      const d = document.documentElement.dataset;
      return {
        present: d.finvizHelper === "1",
        ver: parseFloat(d.finvizHelperVersion || "0"),
        compat: d.jthCompat === "1",
      };
    } catch (e) { return { present: false, ver: 0, compat: false }; }
  };
  const [st, setSt] = useState(read);
  useEffect(() => {
    const on = () => setSt(read());
    window.addEventListener("finviz-helper-ready", on);
    let n = 0;
    const t = setInterval(() => { on(); if (++n > 15) clearInterval(t); }, 2000);
    return () => { window.removeEventListener("finviz-helper-ready", on); clearInterval(t); };
  }, []);
  if (!st.present) return null;
  if (st.ver > 0 && st.ver < 2.7) {
    return (
      <span className="emx-chip warn"
            title={"Site Helper v2.7 is an important update for every browser:\n• Chrome: fixes embedded TradingView sometimes reloading on a click and losing unsaved changes.\n• Comet / Brave: logins inside the embedded sites finally stick — the helper now detects the browser dropping cookies and compensates automatically. No settings, no prompts.\n\nUpdate: download finviz-helper.zip again (link on the Finviz tab), unzip it over the old folder, then click the ↻ reload icon on 'JerryTrade Site Helper' in the browser's extensions page."}>
        ⚠ update helper to v2.7 (hover)
      </span>
    );
  }
  if (!st.compat) return null;
  return (
    <span className="emx-chip"
          title={"This browser blocks cookies on embedded-site requests, so the helper is running its compat mode: it attaches your own login cookies to those requests itself (kept in memory inside your browser — nothing stored or sent anywhere).\n\nIf an embedded site ever shows you logged OUT: sign in once in a normal tab of that site (TradingView: the 'Sign in ↗' button here), then press Reload on this toolbar."}>
      cookies: compat mode
    </span>
  );
}

// ── Unusual Whales embedded view (v3.34) ────────────────────────────────────
// UW doesn't block framing, so the frame always renders; helper v2.1+ makes
// the login persist inside it (cookie SameSite + third-party exception).
// Two-way ticker sync via the /stock/SYMBOL URL reported by uw-sync.js.
function UWPanel({ ticker, onSwitchTicker, inWatchlist, onAddWatchlist,
                   onResearch, onResearch1m, apiFetch }) {
  const [follow, setFollow] = useState(UWHALES.follow());
  const [src, setSrc] = useState(() => UWHALES.stockUrl(ticker));
  const [nonce, setNonce] = useState(0);
  const frameSym = useRef(null);
  const tickerRef = useRef(ticker);
  tickerRef.current = ticker;
  const followRef = useRef(follow);
  followRef.current = follow;

  useEffect(() => {
    const onMsg = (e) => {
      if (!/^https:\/\/(www\.)?unusualwhales\.com$/.test(e.origin)) return;
      const d = e.data;
      if (!d || d.type !== "jth-uw-ticker" || typeof d.symbol !== "string") return;
      const sym = d.symbol.toUpperCase();
      if (!/^[A-Z]{1,5}(\.[A-Z])?$/.test(sym)) return;
      frameSym.current = sym;
      if (followRef.current && onSwitchTicker && sym !== tickerRef.current) onSwitchTicker(sym);
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [onSwitchTicker]);

  useEffect(() => {
    if (follow && ticker && ticker !== frameSym.current) setSrc(UWHALES.stockUrl(ticker));
  }, [ticker, follow]);

  const [radarHit, setRadarHit] = useState(null);
  useEffect(() => {
    if (!apiFetch) return undefined;
    let stop = false;
    const load = () => sharedJson(apiFetch, "/api/radar", 20 * 1000).then(d => {
      if (stop || !d) return;
      const hit = [...(d.long || []), ...(d.short || [])].find(r => r.symbol === ticker);
      setRadarHit(hit ? { side: hit.side, score: hit.score } : null);
    }).catch(() => {});
    load();
    const t = setInterval(skipWhenHidden(load), 45 * 1000);
    return () => { stop = true; clearInterval(t); };
  }, [ticker]);

  const helperVer = (() => { try { return parseFloat(document.documentElement.dataset.finvizHelperVersion || "0"); } catch (e) { return 0; } })();

  return (
    <div className="card fv-card fv-live" style={{ marginBottom: "var(--row-gap)" }}>
      <div className="fv-toolbar" style={{ marginTop: 0 }}>
        <span className="fv-now" title="The Unusual Whales stock page follows the dashboard's globally selected ticker — flow, sweeps, volume/OI, IV, expected move and dark pool for the symbol you're working. Navigating to another /stock page inside UW drives the app's ticker back.">{ticker}</span>
        {inWatchlist ? (
          <span className="fv-star on fv-star-static"
                title={`${ticker} is on your JerryTrade watchlist. Not a button — removal only happens in Manage.`}>
            ★ on watchlist
          </span>
        ) : (onAddWatchlist && (
          <button className="fv-star" onClick={onAddWatchlist}
                  title={`Add ${ticker} to your JerryTrade watchlist. Add-only — this control can never remove.`}>
            ☆ add to watchlist
          </button>
        ))}
        {radarHit && (
          <span className={`emx-chip ${radarHit.side === "long" ? "up" : "warn"}`}
                title={`Live Reversal Radar ${radarHit.side.toUpperCase()} signal on ${ticker} (score ${radarHit.score}/100).`}>
            radar {radarHit.side === "long" ? "▲" : "▼"}{radarHit.score}
          </span>
        )}
        {onResearch && (
          <button className="rr-btn" onClick={() => onResearch(ticker)}
                  title={`Jump to the Trade tab with ${ticker} loaded.`}>Trade →</button>
        )}
        {onResearch1m && (
          <button className="rr-btn" onClick={() => onResearch1m(ticker)}
                  title={`Jump to the app's 1-minute chart for ${ticker}.`}>1-Min →</button>
        )}
        <CookieSetupChip />
        {helperVer < 2.1 && (
          <a className="fv-upd" href="/finviz-helper.zip" download
             title="Unusual Whales renders without the helper, but your UW LOGIN only persists inside the frame with Site Helper v2.1+. Download, replace the unzipped folder's files, then click reload on the extension at chrome://extensions.">update helper for login</a>
        )}
      </div>
      <div className="fv-toolbar fv-row2">
        <button className={`pj-toggle ${follow ? "active" : ""}`}
                onClick={() => { UWHALES.setFollow(!follow); setFollow(!follow); }}
                title="Two-way sync. ON: ticker changes anywhere in the dashboard load that symbol's UW stock page, and browsing to another /stock page inside UW drives the app. OFF: browse UW freely (flow feed, screeners) with no effect either way.">
          Follow {follow ? "ON" : "OFF"}
        </button>
        <button className="rr-btn" onClick={() => setSrc(UWHALES.stockUrl(ticker))}
                title="Point the frame back at the active ticker's stock page.">↺ {ticker}</button>
        <button className="rr-btn" onClick={() => setNonce(n => n + 1)}
                title="Hard-reload the embedded view.">Reload</button>
        <a className="rr-btn fv-ext-link" href={src} target="_blank" rel="noopener noreferrer"
           title="Open the current view in a full browser tab.">⧉</a>
        <span className="fv-sep" />
        {[["Live Flow", "/live-options-flow", "The real-time options flow feed — sweeps, blocks, premium, sentiment."],
          ["Flow Alerts", "/option-flow-alerts", "Unusual Whales' curated flow alerts feed."],
          ["Overview", "/flow/overview", "Market-wide flow overview — tide, sectors, net premium."],
          ["Dark Pool", "/dark-pool-flow", "Dark pool prints feed."],
          ["Earnings", "/earnings", "Earnings calendar with implied moves."],
          ["Alerts", "/alerts", "Your configured UW alerts (account)."]].map(([l, p, tip]) => (
          <button key={l} className="fv-chip" onClick={() => setSrc(UWHALES.url(p))} title={tip}>{l}</button>
        ))}
      </div>
      <iframe key={nonce} className="fv-frame" src={src} title="Unusual Whales"
              referrerPolicy="no-referrer-when-downgrade" allow="clipboard-write; fullscreen" />
      <div className="fv-hint" title="It's the real unusualwhales.com with your account. If the login doesn't stick between visits, update the Site Helper to v2.1+ — it applies the same cookie handling that keeps Finviz and TradingView signed in.">
        Log into UW inside the frame once — account, watchlists and alert settings are all yours. Flow/sweeps/OI/IV live on the stock page's own tabs.
      </div>
    </div>
  );
}

// ── TradingView embedded view (v3.33) ───────────────────────────────────────
// Same architecture as the Finviz tab: the helper extension (v2.0+) lifts
// TradingView's frame-ancestors block and keeps its login cookies working
// inside the frame, so this is the REAL tradingview.com — your layouts,
// indicators, alerts and watchlists. Two-way ticker sync: the app drives the
// chart; changing the chart symbol inside TradingView drives the app.
function TVPanel({ ticker, onSwitchTicker, inWatchlist, onAddWatchlist,
                   onResearch, onResearch1m, apiFetch }) {
  const needVer = 2.0;
  const [helperVer, setHelperVer] = useState(TVIEW.helperVersion());
  const [follow, setFollow] = useState(TVIEW.follow());
  const [src, setSrc] = useState(() => TVIEW.chartUrl(ticker));
  const [nonce, setNonce] = useState(0);
  const frameSym = useRef(null);
  const tickerRef = useRef(ticker);
  tickerRef.current = ticker;
  const followRef = useRef(follow);
  followRef.current = follow;

  useEffect(() => {
    if (helperVer >= needVer) return undefined;
    const on = () => setHelperVer(TVIEW.helperVersion());
    window.addEventListener("finviz-helper-ready", on);
    let n = 0;
    const t = setInterval(() => {
      const v = TVIEW.helperVersion();
      if (v >= needVer) { setHelperVer(v); clearInterval(t); }
      if (++n > 20) clearInterval(t);
    }, 1500);
    return () => { window.removeEventListener("finviz-helper-ready", on); clearInterval(t); };
  }, [helperVer]);

  // Frame -> app: tv-sync.js reports the active chart symbol (from the
  // page title) whenever it changes. Validate origin + symbol.
  useEffect(() => {
    const onMsg = (e) => {
      if (!/^https:\/\/([a-z0-9-]+\.)?tradingview\.com$/.test(e.origin)) return;
      const d = e.data;
      if (!d || d.type !== "jth-tv-ticker" || typeof d.symbol !== "string") return;
      const sym = d.symbol.toUpperCase();
      if (!/^[A-Z]{1,5}(\.[A-Z])?$/.test(sym)) return;
      frameSym.current = sym;
      if (followRef.current && onSwitchTicker && sym !== tickerRef.current) onSwitchTicker(sym);
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [onSwitchTicker]);

  // App -> frame. NOTE: navigating reloads the whole TradingView chart app
  // (it is heavy) — the frameSym guard prevents pointless reloads when the
  // chart itself initiated the change.
  useEffect(() => {
    if (follow && ticker && ticker !== frameSym.current) setSrc(TVIEW.chartUrl(ticker));
  }, [ticker, follow]);

  // App-intel badges (same as the Finviz tab).
  const [radarHit, setRadarHit] = useState(null);
  useEffect(() => {
    if (!apiFetch) return undefined;
    let stop = false;
    const load = () => sharedJson(apiFetch, "/api/radar", 20 * 1000).then(d => {
      if (stop || !d) return;
      const hit = [...(d.long || []), ...(d.short || [])].find(r => r.symbol === ticker);
      setRadarHit(hit ? { side: hit.side, score: hit.score } : null);
    }).catch(() => {});
    load();
    const t = setInterval(skipWhenHidden(load), 45 * 1000);
    return () => { stop = true; clearInterval(t); };
  }, [ticker]);

  if (helperVer >= needVer) {
    return (
      <div className="card fv-card fv-live" style={{ marginBottom: "var(--row-gap)" }}>
        <div className="fv-toolbar">
          <span className="fv-now" title="The chart follows the dashboard's globally selected ticker — and changing the chart's symbol inside TradingView drives the app's ticker back (US-equity symbols only).">{ticker}</span>
          {inWatchlist ? (
            <span className="fv-star on fv-star-static"
                  title={`${ticker} is on your JerryTrade watchlist. Not a button — removal only happens in Manage so tags and metadata can't be lost by a stray click.`}>
              ★ on watchlist
            </span>
          ) : (onAddWatchlist && (
            <button className="fv-star" onClick={onAddWatchlist}
                    title={`Add ${ticker} to your JerryTrade watchlist. Add-only — this control can never remove.`}>
              ☆ add to watchlist
            </button>
          ))}
          {radarHit && (
            <span className={`emx-chip ${radarHit.side === "long" ? "up" : "warn"}`}
                  title={`Live Reversal Radar ${radarHit.side.toUpperCase()} signal on ${ticker} (score ${radarHit.score}/100) — see the Scanners tab for the ticket.`}>
              radar {radarHit.side === "long" ? "▲" : "▼"}{radarHit.score}
            </span>
          )}
          {onResearch && (
            <button className="rr-btn" onClick={() => onResearch(ticker)}
                    title={`Jump to the Trade tab with ${ticker} loaded.`}>Trade →</button>
          )}
          {onResearch1m && (
            <button className="rr-btn" onClick={() => onResearch1m(ticker)}
                    title={`Jump to the app's 1-minute chart for ${ticker} — VWAP bands, day levels, radar markers.`}>1-Min →</button>
          )}
          <CookieSetupChip />
        </div>
        <div className="fv-toolbar fv-row2">
          <button className={`pj-toggle ${follow ? "active" : ""}`}
                  onClick={() => { TVIEW.setFollow(!follow); setFollow(!follow); }}
                  title="Two-way sync. ON: ticker changes anywhere in the dashboard reload this chart to the new symbol, and symbol changes made inside TradingView drive the app. OFF: the chart stays put — recommended while you're drawing or working in one layout, since navigation reloads TradingView's (heavy) chart app.">
            Follow {follow ? "ON" : "OFF"}
          </button>
          <button className="rr-btn" onClick={() => setSrc(TVIEW.chartUrl(ticker))}
                  title="Point the chart back at the active ticker.">↺ {ticker}</button>
          <button className="rr-btn" onClick={() => setNonce(n => n + 1)}
                  title="Hard-reload the embedded TradingView.">Reload</button>
          <button className="rr-btn" onClick={() => {
                    // First-party sign-in: a normal top-level popup where the
                    // browser accepts TV's cookies unconditionally. The helper
                    // (v2.3+) then rewrites ONLY the named auth cookies so the
                    // embedded frame sends them too. When the popup closes,
                    // reload the frame — it comes back signed in.
                    const w = window.open("https://www.tradingview.com/accounts/signin/", "jerry_tv_login",
                                          "width=520,height=760,noopener=no");
                    if (!w) return;
                    const t = setInterval(() => {
                      try {
                        if (w.closed) { clearInterval(t); setNonce(n => n + 1); }
                      } catch (e) { clearInterval(t); }
                    }, 800);
                  }}
                  title="Getting asked to log in on every TradingView page? Sign in HERE once: this opens TradingView's real sign-in page as a normal popup (first-party, so the login always sticks), and with helper v2.3+ the embedded view picks the session up automatically when the popup closes.">
            Sign in ↗
          </button>
          <button className="rr-btn" onClick={() => {
                    // Ask the helper (v2.2+) to clear tradingview.com cookies —
                    // the fix for TV's 'Back before you know it' error page,
                    // which a corrupted cookie jar causes on every route.
                    const onDone = (e) => {
                      if (e.data && e.data.type === "jth-cmd-done" && e.data.cmd === "clear-cookies") {
                        window.removeEventListener("message", onDone);
                        setNonce(n => n + 1);
                      }
                    };
                    window.addEventListener("message", onDone);
                    window.postMessage({ type: "jth-cmd", cmd: "clear-cookies", domain: "tradingview.com" }, "*");
                    setTimeout(() => { window.removeEventListener("message", onDone); setNonce(n => n + 1); }, 1500);
                  }}
                  title="Seeing TradingView's 'Back before you know it' error on every page? That's a corrupted cookie jar. This clears your tradingview.com cookies through the helper (v2.2+) and reloads the frame — log in once afterwards and everything works again.">
            Repair session
          </button>
          {(() => {
            try {
              const v = document.documentElement.dataset.finvizHelperVersion;
              if (!v || parseFloat(v) < 2.3) {
                return <a className="fv-upd" href="/finviz-helper.zip" download
                          title="Helper v2.3 makes the TradingView LOGIN persist inside the embedded view (surgical rewrite of only TV's auth cookies — anti-abuse cookies stay untouched so the error-page corruption can't recur). Download, replace the folder's files, reload the extension at chrome://extensions.">update helper to v2.3</a>;
              }
            } catch (e) {}
            return null;
          })()}
          <a className="rr-btn fv-ext-link" href={src} target="_blank" rel="noopener noreferrer"
             title="Open the current chart in a full browser tab.">⧉</a>
          <span className="fv-sep" />
          {[["Supercharts", "/chart/", "The full TradingView chart app — your saved layouts load here."],
            ["Screener", "/screener/", "TradingView's stock screener."],
            ["Heatmap", "/heatmap/stock/", "Stock market heatmap."],
            ["Calendar", "/economic-calendar/", "Economic calendar."],
            ["News", "/news/", "TradingView news flow."]].map(([l, p, tip]) => (
            <button key={l} className="fv-chip" onClick={() => setSrc(TVIEW.url(p))} title={tip}>{l}</button>
          ))}
        </div>
        <iframe key={nonce} className="fv-frame" src={src} title="TradingView"
                referrerPolicy="no-referrer-when-downgrade" allow="clipboard-write; fullscreen" />
        <div className="fv-hint" title="If TradingView shows you logged out inside the frame while a normal tab is logged in, reload this tab once — the helper upgrades existing login cookies on install and as they change. Alerts fire server-side on TradingView regardless of where the chart is open.">
          Asked to log in repeatedly? Use 'Sign in ↗' above once — it signs you in on a normal TradingView page, and the embedded view (helper v2.3+) picks the session up automatically. Layouts, indicators and alerts are your real account.
        </div>
      </div>
    );
  }

  return (
    <div className="card fv-card" style={{ marginBottom: "var(--row-gap)" }}>
      <div className="card-head">
        <div>
          <div className="kicker" title="TradingView blocks embedding via CSP frame-ancestors — the same one-time helper that unlocks Finviz also unlocks TradingView from v2.0.">
            tradingview · embedded view · helper v2.0 needed
          </div>
          <div className="card-title">Show TradingView inside this tab</div>
        </div>
      </div>
      <div className="fv-setup">
        {FINVIZ.isMobile() ? (
          <>
            <p>Mobile browsers don't support extensions, so the embedded view is desktop-only. Fastest route on this device:</p>
            <a className="rr-btn fv-main" href={TVIEW.chartUrl(ticker)} target="_blank" rel="noopener noreferrer">Open TradingView — {ticker}</a>
          </>
        ) : (
          <>
            <p><b>Already have the Finviz helper?</b> Just update it: <a className="fv-dl" href="/finviz-helper.zip" download>download the new zip</a>, replace the folder's files, and click ↻ reload on the extension at <code>chrome://extensions</code> — v2.0 adds TradingView.</p>
            <p>New here? Follow the same 4 steps shown on the Finviz tab.</p>
          </>
        )}
      </div>
    </div>
  );
}

// ── Finviz embedded view (v3.25) ───────────────────────────────────────────
// Finviz rendered INSIDE the dashboard. Requires the JerryTrade Finviz
// Helper — a tiny user-installed extension whose only capability is letting
// this dashboard embed finviz.com (Chrome's official declarativeNetRequest
// API; no data access, no other sites). With the helper present, the tab is
// a full-height live Finviz frame that follows the global ticker; without
// it, a clean setup panel with the download and honest platform notes.
function FinvizPanel({ ticker, onSwitchTicker, inWatchlist, onAddWatchlist,
                      watchlistSymbols, onResearch, onResearch1m, apiFetch }) {
  const [helper, setHelper] = useState(FINVIZ.helperPresent());
  const [follow, setFollow] = useState(FINVIZ.follow());
  const [base, setBase] = useState(FINVIZ.base().includes("elite") ? "elite" : "free");
  const [src, setSrc] = useState(() => FINVIZ.quoteUrl(ticker));
  const [nonce, setNonce] = useState(0);      // manual reload counter
  // Two-way sync bookkeeping: the symbol the FRAME is currently showing
  // (reported by the helper's ticker-sync script) and the current app ticker,
  // both as refs so the message handler never sees stale values.
  const frameSym = useRef(null);
  const tickerRef = useRef(ticker);
  tickerRef.current = ticker;
  const followRef = useRef(follow);
  followRef.current = follow;

  // App-intel badges for the active symbol: radar signal + juice score.
  // Radar is already polled app-wide by the alerts loop (sharedJson dedupes);
  // juice is fetched at a slow cadence only while this tab is mounted.
  const [radarHit, setRadarHit] = useState(null);
  const [juiceHit, setJuiceHit] = useState(null);
  useEffect(() => {
    if (!apiFetch) return undefined;
    let stop = false;
    const load = () => {
      sharedJson(apiFetch, "/api/radar", 20 * 1000).then(d => {
        if (stop || !d) return;
        const hit = [...(d.long || []), ...(d.short || [])].find(r => r.symbol === ticker);
        setRadarHit(hit ? { side: hit.side, score: hit.score } : null);
      }).catch(() => {});
      sharedJson(apiFetch, "/api/juice", 240 * 1000).then(d => {
        if (stop || !d) return;
        const hit = (d.rows || []).find(r => r.symbol === ticker);
        setJuiceHit(hit ? { score: hit.score, dte: hit.dte } : null);
      }).catch(() => {});
    };
    load();
    const t = setInterval(skipWhenHidden(load), 45 * 1000);
    return () => { stop = true; clearInterval(t); };
  }, [ticker]);

  // Frame -> app: helper v1.3 posts {type:'fvh-ticker', symbol} from inside
  // the embedded Finviz page whenever it lands on a quote page (i.e. you
  // clicked a stock). Validate origin + symbol, then drive the global
  // ticker. frameSym doubles as the reload-loop guard below.
  useEffect(() => {
    const onMsg = (e) => {
      if (!/^https:\/\/(elite\.)?finviz\.com$/.test(e.origin)) return;
      const d = e.data;
      if (!d || d.type !== "fvh-ticker" || typeof d.symbol !== "string") return;
      const sym = d.symbol.toUpperCase();
      if (!/^[A-Z0-9.\-]{1,10}$/.test(sym)) return;
      frameSym.current = sym;
      if (followRef.current && onSwitchTicker && sym !== tickerRef.current) {
        onSwitchTicker(sym);
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [onSwitchTicker]);

  // Helper detection: the extension announces via a DOM dataset flag +
  // event at document_start; poll briefly too in case we mounted first.
  useEffect(() => {
    if (helper) return undefined;
    const on = () => setHelper(true);
    window.addEventListener("finviz-helper-ready", on);
    let n = 0;
    const t = setInterval(() => {
      if (FINVIZ.helperPresent()) { setHelper(true); clearInterval(t); }
      if (++n > 20) clearInterval(t);
    }, 1500);
    return () => { window.removeEventListener("finviz-helper-ready", on); clearInterval(t); };
  }, [helper]);

  // App -> frame: navigate on ticker change — but NOT when the frame itself
  // just reported this symbol (that would reload the page the user is
  // already reading and reset their scroll).
  useEffect(() => {
    if (follow && ticker && ticker !== frameSym.current) setSrc(FINVIZ.quoteUrl(ticker));
  }, [ticker, follow, base]);

  const navChip = (label, path, tip) => (
    <button key={label} className="fv-chip" onClick={() => setSrc(FINVIZ.base() + path)} title={tip}>{label}</button>
  );
  const wlScreenerPath = (() => {
    const syms = (watchlistSymbols || []).slice(0, 100);
    return syms.length ? `/screener.ashx?v=111&t=${syms.join(",")}` : null;
  })();
  const toolbar = (
    <div className="fv-toolbar">
      <span className="fv-now" title="The frame follows the dashboard's globally selected ticker — and clicking a stock inside Finviz drives it back. Change the symbol anywhere and this view navigates with it.">
        {ticker}
      </span>
      {inWatchlist ? (
        <span className="fv-star on fv-star-static"
              title={`${ticker} is on your JerryTrade watchlist. This badge is NOT a button — removing a symbol wipes its tags, sector, notes and weekly flags, so removal only happens deliberately in Manage, never from here.`}>
          ★ on watchlist
        </span>
      ) : (onAddWatchlist && (
        <button className="fv-star" onClick={onAddWatchlist}
                title={`Add ${ticker} to your JerryTrade watchlist (scanned by the board, radar and juice from the next pass). Add-only — this control can never remove.`}>
          ☆ add to watchlist
        </button>
      ))}
      {radarHit && (
        <span className={`emx-chip ${radarHit.side === "long" ? "up" : "warn"}`}
              title={`The Reversal Radar has a live ${radarHit.side.toUpperCase()} signal on ${ticker} right now (score ${radarHit.score}/100). See the Scanners tab for the ticket.`}>
          radar {radarHit.side === "long" ? "▲" : "▼"}{radarHit.score}
        </span>
      )}
      {juiceHit && (
        <span className="emx-chip earn"
              title={`${ticker} is on the 0-3 DTE Premium Juice board (score ${juiceHit.score}, ${juiceHit.dte}d to expiry) — fat same-week premium. See the 0DTE Juice tab for structures.`}>
          juice {juiceHit.score}
        </span>
      )}
      {onResearch && (
        <button className="rr-btn" onClick={() => onResearch(ticker)}
                title={`Jump to the Trade tab with ${ticker} loaded — chart, expected move, strikes, trade builder.`}>
          Trade →
        </button>
      )}
      {onResearch1m && (
        <button className="rr-btn" onClick={() => onResearch1m(ticker)}
                title={`Jump straight to the 1-minute chart for ${ticker} — VWAP bands, day levels, radar markers.`}>
          1-Min →
        </button>
      )}
      <CookieSetupChip />
      {(() => {
        try {
          const v = document.documentElement.dataset.finvizHelperVersion;
          if (!v || parseFloat(v) < 1.4) {
            return <a className="fv-upd" href="/finviz-helper.zip" download
                      title="A newer Site Helper is available (login persistence + theme toggle fixes). Download, replace the unzipped folder's files, then click the reload icon on the extension at chrome://extensions.">update helper</a>;
          }
        } catch (e) {}
        return null;
      })()}
    </div>
  );
  const toolbar2 = (
    <div className="fv-toolbar fv-row2">
      <button className={`pj-toggle ${follow ? "active" : ""}`}
              onClick={() => { FINVIZ.setFollow(!follow); setFollow(!follow); }}
              title="Two-way sync. ON: every symbol selected anywhere in the dashboard navigates this Finviz view — AND every stock you click inside Finviz (screener, maps, news) becomes the dashboard&#39;s active ticker, ready for research on any tab. OFF: browse Finviz freely with no effect either way.">
        Follow {follow ? "ON" : "OFF"}
      </button>
      <div className="seg" title="Elite = your paid real-time account at elite.finviz.com. Free = the public site.">
        {[["elite", "Elite"], ["free", "Free"]].map(([v, l]) => (
          <button key={v} className={base === v ? "active" : ""}
                  onClick={() => { FINVIZ.setBase(v); setBase(v); setSrc(FINVIZ.quoteUrl(ticker)); }}>{l}</button>
        ))}
      </div>
      <button className="rr-btn" onClick={() => setSrc(FINVIZ.quoteUrl(ticker))}
              title="Jump the frame back to the active ticker's quote page.">↺ {ticker}</button>
      <button className="rr-btn" onClick={() => setNonce(n => n + 1)}
              title="Hard-reload the embedded Finviz view.">Reload</button>
      <a className="rr-btn fv-ext-link" href={src} target="_blank" rel="noopener noreferrer"
         title="Open the current view in a full browser tab (useful for printing or very dense screener work).">⧉</a>
      <span className="fv-sep" />
      {navChip("Screener", "/screener.ashx", "Your Finviz screener — saved Elite presets included. (Clicking a result drives the app's ticker.)")}
      {navChip("Portfolio", "/portfolio.ashx", "Your Finviz portfolios and watchlists (account-synced).")}
      {navChip("Map", "/map.ashx?t=sec", "S&P 500 heat map by sector.")}
      {navChip("Earnings", "/calendar.ashx", "Economic & earnings calendar.")}
      {navChip("News", "/news.ashx", "Finviz market news and blogs.")}
      {wlScreenerPath && navChip("My watchlist", wlScreenerPath,
        `Open Finviz's screener filtered to YOUR JerryTrade watchlist symbols${(watchlistSymbols || []).length > 100 ? " (first 100 of " + watchlistSymbols.length + ")" : ""} — run Finviz's fundamental and technical columns over your own list, no re-typing.`)}
    </div>
  );

  if (helper) {
    return (
      <div className="card fv-card fv-live" style={{ marginBottom: "var(--row-gap)" }}>
        {toolbar}
        {toolbar2}
        <iframe key={nonce} className="fv-frame" src={src} title="Finviz"
                referrerPolicy="no-referrer-when-downgrade" allow="clipboard-write" />
        <div className="fv-hint" title="If Finviz shows you as logged out inside this frame while a normal Finviz tab is logged in, your browser is isolating third-party cookies. Either allow cookies for finviz.com in the browser's settings, or simply log in once right here — most browsers keep an in-frame login alive across visits.">
          Log into Elite inside the frame once if prompted — it's the real finviz.com, so your account, screens and watchlists are all there.
        </div>
      </div>
    );
  }

  // No helper: setup panel (desktop) / honest limitation note (mobile).
  return (
    <div className="card fv-card" style={{ marginBottom: "var(--row-gap)" }}>
      <div className="card-head">
        <div>
          <div className="kicker" title="Finviz sends X-Frame-Options: SAMEORIGIN — every browser refuses to render it inside another site, and no website can override that from its side. The one-time helper below is the official, user-consented way to grant YOUR browser that ability, scoped to this dashboard only.">
            finviz · embedded view · setup needed
          </div>
          <div className="card-title">Show Finviz inside this tab</div>
        </div>
      </div>
      {FINVIZ.isMobile() ? (
        <div className="fv-setup">
          <p>Mobile browsers don't support extensions, and Finviz itself blocks being displayed inside other sites — so the embedded view is desktop-only. On this device, the fastest route is Finviz directly:</p>
          <a className="rr-btn fv-main" href={FINVIZ.quoteUrl(ticker)} target="_blank" rel="noopener noreferrer">Open Finviz — {ticker}</a>
        </div>
      ) : (
        <div className="fv-setup">
          <p><b>One-time setup (~1 minute), Chrome / Edge / Brave:</b></p>
          <ol>
            <li><a className="fv-dl" href="/finviz-helper.zip" download title="A four-file extension: a rule that lets THIS dashboard embed finviz.com, and a one-line script that tells the dashboard it's installed. No data access, no other sites — the README inside explains every line.">Download the Finviz Helper</a> and unzip it.</li>
            <li>Open <code>chrome://extensions</code>, switch on <b>Developer mode</b> (top-right).</li>
            <li>Click <b>Load unpacked</b> and pick the unzipped <code>finviz-helper</code> folder.</li>
            <li>Come back here and reload — this panel becomes a live, full-height Finviz that follows every ticker you select.</li>
          </ol>
          <p className="fv-fineprint" title="Why is this needed? Finviz sends X-Frame-Options: SAMEORIGIN, which makes browsers refuse to render it inside any other website. The helper uses Chrome's official declarativeNetRequest API — installed and controlled by you — to permit exactly one thing: Finviz displayed inside this dashboard. Nothing is proxied or scraped; Finviz loads from Finviz with your own cookies, so your Elite login and account data work as normal.">
            Why a helper? Finviz blocks all embedding at the browser level; this is the official, user-consented way to allow it — for this dashboard only. Hover for the full story.
          </p>
        </div>
      )}
    </div>
  );
}


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

// ── Natural-language Backtest Lab (v3.43) ───────────────────────────────────
// Describe a strategy in plain English → the backend's deterministic trading
// grammar converts it to explicit JSON rules → review/edit every rule here →
// run. The engine fills at the NEXT bar's open (no look-ahead), models
// spread/slippage/commission, skips illiquid fills, and reports loud
// warnings whenever the data can't support the idea (options are
// model-priced; no historical news/IV). Results persist across reloads.
const BT_COND_TYPES = {
  gap_pct: { label: "Gap at open vs prior close (%)", make: () => ({ type: "gap_pct", op: "<=", value: -2 }) },
  cross_above_open: { label: "Crosses back ABOVE the open (intraday)", make: () => ({ type: "cross_above_open" }) },
  cross_below_open: { label: "Crosses back BELOW the open (intraday)", make: () => ({ type: "cross_below_open" }) },
  rel_volume: { label: "Volume ≥ N × average", make: () => ({ type: "rel_volume", mult: 2, lookback: 20 }) },
  drawdown_from_high: { label: "Drawdown from high (%)", make: () => ({ type: "drawdown_from_high", pct: 30, lookback: 252 }) },
  rsi: { label: "RSI", make: () => ({ type: "rsi", period: 14, op: "<=", value: 30 }) },
  sma_cross: { label: "MA cross", make: () => ({ type: "sma_cross", fast: 20, slow: 50, direction: "up" }) },
  price_vs_sma: { label: "Price vs moving average", make: () => ({ type: "price_vs_sma", op: ">=", period: 200 }) },
  new_high: { label: "New N-day high", make: () => ({ type: "new_high", lookback: 20 }) },
  new_low: { label: "New N-day low", make: () => ({ type: "new_low", lookback: 20 }) },
  consec_down: { label: "N consecutive down days", make: () => ({ type: "consec_down", n: 3 }) },
  consec_up: { label: "N consecutive up days", make: () => ({ type: "consec_up", n: 3 }) },
  day_change_pct: { label: "Change on the day (%)", make: () => ({ type: "day_change_pct", op: "<=", value: -3 }) },
  move_pct: { label: "Move over trailing N days (%)", make: () => ({ type: "move_pct", days: 5, op: ">=", value: 10 }) },
  price_abs: { label: "Price filter ($)", make: () => ({ type: "price_abs", op: ">=", value: 20 }) },
  market_regime: { label: "SPY regime filter", make: () => ({ type: "market_regime", regime: "uptrend" }) },
};
const BT_EXIT_TYPES = {
  profit_pct: { label: "Profit target (%)", make: () => ({ type: "profit_pct", value: 5 }) },
  stop_pct: { label: "Stop loss (%)", make: () => ({ type: "stop_pct", value: 2 }) },
  trailing_stop_pct: { label: "Trailing stop (%)", make: () => ({ type: "trailing_stop_pct", value: 8 }) },
  time_days: { label: "Time exit (trading days)", make: () => ({ type: "time_days", value: 10 }) },
  same_day_close: { label: "Exit by the close (same day)", make: () => ({ type: "same_day_close" }) },
  hold_to_expiry: { label: "Hold option to expiry", make: () => ({ type: "hold_to_expiry" }) },
};
const BT_EXAMPLES = [
  "Buy stocks that open down at least 2%, reverse above the opening price, and have volume at least twice the 20 day average. Exit at a 5% profit, a 2% stop loss, or before the market closes.",
  "Buy stock after a 30% drawdown from a recent high. Hold for 15 days with a 10% trailing stop. $5,000 per trade on AAPL, MSFT and NVDA.",
  "Buy 30 dte calls at the money when RSI 14 below 30 and price above the 200 day moving average, only when SPY is in an uptrend. 50% profit target, 25% stop loss.",
];

function BTParamInputs({ cond, onChange }) {
  // Generic param editor: every non-label key becomes a small typed input,
  // so any rule the parser (or the user via JSON) produces stays editable.
  const keys = Object.keys(cond).filter(k => k !== "type" && k !== "label");
  return (
    <span className="bt-params">
      {keys.map(k => (
        <label key={k} className="bt-param" title={`Edit the '${k}' parameter of this rule.`}>
          <span>{k}</span>
          {k === "op" ? (
            <select value={cond[k]} onChange={e => onChange({ ...cond, [k]: e.target.value })}>
              <option value="<=">≤</option><option value=">=">≥</option>
            </select>
          ) : k === "direction" || k === "regime" ? (
            <select value={cond[k]} onChange={e => onChange({ ...cond, [k]: e.target.value })}>
              {k === "direction"
                ? [<option key="u" value="up">up</option>, <option key="d" value="down">down</option>]
                : [<option key="u" value="uptrend">uptrend</option>, <option key="d" value="downtrend">downtrend</option>, <option key="c" value="chop">chop</option>]}
            </select>
          ) : (
            <input type="number" step="any" value={cond[k] ?? ""}
                   onChange={e => onChange({ ...cond, [k]: e.target.value === "" ? null : +e.target.value })} />
          )}
        </label>
      ))}
    </span>
  );
}

function BTEquityCurve({ curve, start }) {
  if (!curve || curve.length < 2) return null;
  const W = 640, H = 150, PAD = 6;
  const vals = curve.map(p => p.equity);
  const lo = Math.min(start, ...vals), hi = Math.max(start, ...vals);
  const span = Math.max(1e-9, hi - lo);
  const x = i => PAD + (W - 2 * PAD) * (i / (curve.length - 1));
  const y = v => H - PAD - (H - 2 * PAD) * ((v - lo) / span);
  const pts = curve.map((p, i) => `${x(i).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
  const up = vals[vals.length - 1] >= start;
  return (
    <div className="bt-curve" title={`Equity curve: $${Math.round(start).toLocaleString()} starting equity, stepped at each trade exit (realized P&L). ${curve.length} closed trades from ${curve[0].date} to ${curve[curve.length - 1].date}.`}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <line x1={PAD} x2={W - PAD} y1={y(start)} y2={y(start)} className="bt-curve-base" />
        <polyline points={pts} className={`bt-curve-line ${up ? "up" : "down"}`} />
      </svg>
      <div className="bt-curve-lbls">
        <span>{curve[0].date}</span>
        <span className={up ? "up" : "down"}>${Math.round(vals[vals.length - 1]).toLocaleString()}</span>
        <span>{curve[curve.length - 1].date}</span>
      </div>
    </div>
  );
}

function BacktestCard({ apiFetch }) {
  const [text, setText] = useState(() => { try { return localStorage.getItem("jerry_bt_text") || ""; } catch (e) { return ""; } });
  const [rules, setRules] = useState(null);
  const [parseWarns, setParseWarns] = useState([]);
  const [unparsed, setUnparsed] = useState([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const [showJson, setShowJson] = useState(false);
  const [jsonDraft, setJsonDraft] = useState("");
  const [showTrades, setShowTrades] = useState(false);
  const pollRef = useRef(null);

  useEffect(() => {
    // Restore the last completed backtest so results survive reloads.
    sharedJson(apiFetch, "/api/backtest/last", 60000)
      .then(d => { if (d && d.metrics && d.metrics.n_trades != null && !result) setResult(d); })
      .catch(() => {});
    // Accept rule sets sent from Pattern Discovery ("→ Backtest"): live via
    // event when this card is mounted, via localStorage when it wasn't yet.
    const onLoad = (e) => { if (e.detail) setRulesAnd(e.detail); };
    window.addEventListener("jerry-bt-load", onLoad);
    try {
      const pre = localStorage.getItem("jerry_bt_prefill");
      if (pre) { localStorage.removeItem("jerry_bt_prefill"); setRulesAnd(JSON.parse(pre)); }
    } catch (e) { /* no-op */ }
    return () => {
      window.removeEventListener("jerry-bt-load", onLoad);
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const setRulesAnd = (r) => { setRules(r); setJsonDraft(JSON.stringify(r, null, 2)); };

  const interpret = () => {
    setErr(null); setBusy(true); setResult(result); setProgress(null);
    try { localStorage.setItem("jerry_bt_text", text); } catch (e) {}
    apiFetch("/api/backtest/parse", { method: "POST", body: JSON.stringify({ text }) })
      .then(r => r.json())
      .then(d => {
        setBusy(false);
        if (d.error) { setErr(d.error); return; }
        setRulesAnd(d.rules); setParseWarns(d.warnings || []); setUnparsed(d.unparsed || []);
      })
      .catch(e => { setBusy(false); setErr(String(e)); });
  };

  const run = () => {
    if (!rules) return;
    setErr(null); setBusy(true); setProgress({ phase: "starting", done: 0, total: 1 });
    apiFetch("/api/backtest/run", { method: "POST", body: JSON.stringify({ rules }) })
      .then(r => r.json())
      .then(d => {
        if (d.error || !d.job) { setBusy(false); setErr(d.error || "could not start"); return; }
        pollRef.current = setInterval(() => {
          apiFetch(`/api/backtest/status?job=${d.job}`).then(r => r.json()).then(s => {
            if (s.progress) setProgress(s.progress);
            if (s.status === "done" || s.status === "error") {
              clearInterval(pollRef.current); pollRef.current = null;
              setBusy(false); setProgress(null);
              if (s.status === "error" || (s.result && s.result.error)) setErr((s.result && s.result.error) || "backtest failed");
              else setResult(s.result);
            }
          }).catch(() => {});
        }, 1500);
      })
      .catch(e => { setBusy(false); setErr(String(e)); });
  };

  const mutate = (fn) => { const r = JSON.parse(JSON.stringify(rules)); fn(r); setRulesAnd(r); };
  const M = (result && result.metrics) || {};
  const fmtD = (v) => (v == null ? "—" : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`);

  return (
    <div className="card bt-card">
      <div className="card-head">
        <div>
          <div className="kicker" title="Describe a strategy in plain English. A deterministic trading grammar (running in this app — your idea never leaves the server) converts it to explicit rules you can inspect and edit before anything runs. Fills happen at the NEXT bar's open (no look-ahead), with modeled spread, slippage, commissions and liquidity checks.">Backtest Lab</div>
          <h2 title="Type an idea like the examples below, press Interpret, review the exact rules it built, then Run.">Test any idea in plain English</h2>
        </div>
      </div>

      <textarea className="bt-input" rows={3} value={text} spellCheck={false}
                placeholder='e.g. "Buy stocks that open down at least 2%, reverse above the opening price, and have volume at least twice the 20 day average. Exit at a 5% profit, a 2% stop loss, or before the market closes."'
                onChange={e => setText(e.target.value)}
                title="Your strategy, in your words. Supported vocabulary: gaps, reversals over/under the open, volume vs average, drawdowns from highs, RSI, moving averages and crosses, new highs/lows, consecutive days, price filters, SPY regime, calls/puts with DTE and strike (ATM / % OTM / delta), profit targets, stops, trailing stops, time and same-day exits, position sizing, symbol lists ('on AAPL, MSFT'), and test windows ('last 2 years')." />
      <div className="bt-examples">
        {BT_EXAMPLES.map((ex, i) => (
          <button key={i} className="rr-btn" onClick={() => setText(ex)}
                  title={ex}>example {i + 1}</button>
        ))}
        <button className="rr-btn bt-go" disabled={busy || !text.trim()} onClick={interpret}
                title="Convert the text above into explicit, editable rules. Nothing runs yet — you review the rules first.">
          {busy && !progress ? "interpreting…" : "Interpret →"}
        </button>
      </div>

      {err && <div className="bt-warn bt-err" title="The last action failed — the message comes straight from the engine.">{err}</div>}

      {rules && (
        <div className="bt-rules">
          <div className="bt-sec-title" title="These are the EXACT rules the engine will run — edit any number, remove any rule, or add one. If a clause of your text was not understood it is listed below in amber, not silently guessed.">Rules (review & edit)</div>
          {(parseWarns.length > 0 || unparsed.length > 0) && (
            <div className="bt-warn">
              {parseWarns.map((w, i) => <div key={i} title="A limitation or assumption you should know about before trusting results.">⚠ {w}</div>)}
              {unparsed.map((u, i) => <div key={"u" + i} title="This part of your text matched no known rule pattern. Add it manually below or rephrase.">✎ not understood: “{u}”</div>)}
            </div>
          )}

          <div className="bt-row" title="Long buys first; Short sells first (stocks only — bearish option ideas become long puts). Instrument: stock shares or model-priced options.">
            <span className="bt-lbl">Setup</span>
            <select value={rules.direction} onChange={e => mutate(r => { r.direction = e.target.value; })}>
              <option value="long">Long</option><option value="short">Short</option>
            </select>
            <select value={rules.instrument} onChange={e => mutate(r => {
              r.instrument = e.target.value;
              if (e.target.value === "option" && !r.options) r.options = { right: "call", dte: 30, strike: { mode: "atm" } };
            })}>
              <option value="stock">Stock</option><option value="option">Option (modeled)</option>
            </select>
            {rules.instrument === "option" && rules.options && (
              <>
                <select value={rules.options.right} onChange={e => mutate(r => { r.options.right = e.target.value; })}
                        title="Call or put. Premiums are Black-Scholes estimates from realized volatility — no historical option quotes exist.">
                  <option value="call">calls</option><option value="put">puts</option>
                </select>
                <label className="bt-param" title="Days to expiration at entry."><span>dte</span>
                  <input type="number" value={rules.options.dte} onChange={e => mutate(r => { r.options.dte = +e.target.value || 30; })} /></label>
                <select value={rules.options.strike.mode} onChange={e => mutate(r => { r.options.strike = { mode: e.target.value, value: e.target.value === "atm" ? undefined : (r.options.strike.value || 5) }; })}
                        title="Strike selection: at-the-money, a % out/in of the money, or by delta.">
                  <option value="atm">ATM</option><option value="otm_pct">% OTM</option>
                  <option value="itm_pct">% ITM</option><option value="delta">by delta</option>
                </select>
                {rules.options.strike.mode !== "atm" && (
                  <label className="bt-param"><span>{rules.options.strike.mode === "delta" ? "delta" : "%"}</span>
                    <input type="number" step="any" value={rules.options.strike.value ?? ""}
                           onChange={e => mutate(r => { r.options.strike.value = +e.target.value; })} /></label>
                )}
              </>
            )}
          </div>

          <div className="bt-row" title="Which symbols to test. 'Starred watchlist' uses the tickers you starred in the sidebar; or list symbols explicitly. Universes are capped (50 daily / 15 intraday) to respect data rate limits — a warning tells you if clipped.">
            <span className="bt-lbl">Universe</span>
            <select value={rules.universe.source} onChange={e => mutate(r => { r.universe.source = e.target.value; })}>
              <option value="starred">Starred watchlist</option><option value="symbols">These symbols:</option>
            </select>
            {rules.universe.source === "symbols" && (
              <input className="bt-syms" value={(rules.universe.symbols || []).join(", ")}
                     onChange={e => mutate(r => { r.universe.symbols = e.target.value.split(/[\s,]+/).map(s => s.toUpperCase()).filter(Boolean); })}
                     placeholder="AAPL, MSFT, NVDA" />
            )}
            <label className="bt-param" title="Test window in calendar days back from today. Daily data reaches ~2 years; 1-minute data (intraday rules) ~6 months — the engine clips and warns.">
              <span>window (days)</span>
              <input type="number" value={rules.period_days} onChange={e => mutate(r => { r.period_days = +e.target.value || 365; })} /></label>
          </div>

          <div className="bt-cond-list">
            <div className="bt-sec-sub" title="ALL entry conditions must be true on the same bar. The position is opened at the NEXT bar's open — never on the bar that generated the signal.">Entry — all must be true</div>
            {rules.entry.map((c, i) => (
              <div key={i} className="bt-cond" title={BT_COND_TYPES[c.type] ? BT_COND_TYPES[c.type].label : c.type}>
                <span className="bt-cond-name">{(BT_COND_TYPES[c.type] || {}).label || c.type}</span>
                <BTParamInputs cond={c} onChange={nc => mutate(r => { r.entry[i] = nc; })} />
                <button className="bt-x" onClick={() => mutate(r => { r.entry.splice(i, 1); })} title="Remove this condition.">✕</button>
              </div>
            ))}
            <select className="bt-add" value="" onChange={e => { const t = e.target.value; if (t) mutate(r => { r.entry.push(BT_COND_TYPES[t].make()); }); }}
                    title="Add another entry condition.">
              <option value="">+ add entry condition…</option>
              {Object.entries(BT_COND_TYPES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </div>

          <div className="bt-cond-list">
            <div className="bt-sec-sub" title="First exit hit wins. When a stop and a target land inside the same bar, the engine assumes the STOP filled first — the conservative reading.">Exits — first hit wins (stop assumed first inside a bar)</div>
            {rules.exit.map((c, i) => (
              <div key={i} className="bt-cond">
                <span className="bt-cond-name">{(BT_EXIT_TYPES[c.type] || {}).label || c.type}</span>
                <BTParamInputs cond={c} onChange={nc => mutate(r => { r.exit[i] = nc; })} />
                <button className="bt-x" onClick={() => mutate(r => { r.exit.splice(i, 1); })} title="Remove this exit.">✕</button>
              </div>
            ))}
            <select className="bt-add" value="" onChange={e => { const t = e.target.value; if (t) mutate(r => { r.exit.push(BT_EXIT_TYPES[t].make()); }); }}
                    title="Add another exit.">
              <option value="">+ add exit…</option>
              {Object.entries(BT_EXIT_TYPES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </div>

          <div className="bt-row" title="Position sizing and realism knobs. Slippage is charged on both sides on top of an estimated bid/ask spread by price bucket; trades are SKIPPED (not filled) when the name's average dollar volume is under the liquidity multiple × position size.">
            <span className="bt-lbl">Sizing & costs</span>
            <label className="bt-param"><span>$ / trade</span>
              <input type="number" value={rules.sizing.value} onChange={e => mutate(r => { r.sizing.value = +e.target.value || 10000; })} /></label>
            <label className="bt-param"><span>max positions</span>
              <input type="number" value={rules.sizing.max_positions} onChange={e => mutate(r => { r.sizing.max_positions = +e.target.value || 5; })} /></label>
            <label className="bt-param"><span>slippage (bps)</span>
              <input type="number" value={rules.costs.slippage_bps} onChange={e => mutate(r => { r.costs.slippage_bps = +e.target.value || 0; })} /></label>
            <label className="bt-param"><span>commission $</span>
              <input type="number" step="any" value={rules.costs.commission} onChange={e => mutate(r => { r.costs.commission = +e.target.value || 0; })} /></label>
          </div>

          <div className="bt-actions">
            <button className="rr-btn bt-go" disabled={busy || rules.entry.length === 0} onClick={run}
                    title={rules.entry.length === 0 ? "Add at least one entry condition first." : "Run the backtest with exactly the rules shown above. Intraday rules fetch 1-minute bars and can take a few minutes — progress shows below."}>
              {busy && progress ? "running…" : "Run backtest ▶"}
            </button>
            <button className="rr-btn" onClick={() => setShowJson(!showJson)}
                    title="Power view: the full rule set as JSON. Edit anything and Apply — the structured editors above update to match.">{showJson ? "hide JSON" : "edit as JSON"}</button>
          </div>
          {showJson && (
            <div className="bt-json">
              <textarea rows={12} value={jsonDraft} spellCheck={false} onChange={e => setJsonDraft(e.target.value)} />
              <button className="rr-btn" onClick={() => { try { setRules(JSON.parse(jsonDraft)); setErr(null); } catch (e) { setErr("JSON error: " + e.message); } }}
                      title="Validate and apply the JSON above as the active rule set.">Apply JSON</button>
            </div>
          )}
        </div>
      )}

      {progress && (
        <div className="bt-progress" title="Backtests run on the server in the background; heavy intraday tests fetch one symbol-day of minute bars at a time inside the data provider's rate limit.">
          <div className="bt-progress-bar"><div style={{ width: `${Math.min(100, (progress.done / Math.max(1, progress.total)) * 100)}%` }} /></div>
          <span>{progress.phase} — {progress.done}/{progress.total}</span>
        </div>
      )}

      {result && result.metrics && (
        <div className="bt-results">
          <div className="bt-sec-title" title={`Mode: ${result.mode || "daily"}. Symbols: ${(result.symbols_tested || []).length}. Completed in ${result.elapsed_sec || "?"}s. Metrics are computed on realized trade P&L from $${Number(M.start_equity || 100000).toLocaleString()} starting equity.`}>Results</div>
          {(result.warnings || []).length > 0 && (
            <div className="bt-warn">
              {result.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
            </div>
          )}
          <div className="bt-tiles">
            <div className="bt-tile" title="Sum of all realized trade P&L as a % of starting equity ($100k), after modeled costs."><span>Total return</span><b className={M.total_return_pct >= 0 ? "up" : "down"}>{M.total_return_pct}%</b></div>
            <div className="bt-tile" title="Realized profit and loss in dollars, after spread, slippage and commissions."><span>Total P&L</span><b className={M.total_pnl >= 0 ? "up" : "down"}>{fmtD(M.total_pnl)}</b></div>
            <div className="bt-tile" title="Number of closed trades in the test. Skipped candidates (liquidity, max positions) are counted separately below."><span>Trades</span><b>{M.n_trades}</b></div>
            <div className="bt-tile" title="Share of trades that closed with a profit."><span>Win rate</span><b>{M.win_rate}%</b></div>
            <div className="bt-tile" title="Average dollar profit across winning trades."><span>Avg gain</span><b className="up">{fmtD(M.avg_gain)}</b></div>
            <div className="bt-tile" title="Average dollar loss across losing trades."><span>Avg loss</span><b className="down">{fmtD(M.avg_loss)}</b></div>
            <div className="bt-tile" title="Gross profits ÷ gross losses. Above 1.0 = the wins outweigh the losses; below 1.0 the strategy loses money overall."><span>Profit factor</span><b>{M.profit_factor == null ? "∞" : M.profit_factor}</b></div>
            <div className="bt-tile" title="Deepest peak-to-trough drop of the equity curve, as a % of the peak."><span>Max drawdown</span><b className="down">{M.max_drawdown_pct}%</b></div>
            <div className="bt-tile" title="Expected $ per trade: win-rate × avg gain − loss-rate × avg loss. Positive = the edge survives its costs."><span>Expectancy</span><b className={M.expectancy >= 0 ? "up" : "down"}>{fmtD(M.expectancy)}</b></div>
          </div>
          <BTEquityCurve curve={result.equity_curve} start={M.start_equity || 100000} />
          <div className="bt-detail">
            {result.best_trade && (
              <span title={`Best single trade: ${result.best_trade.symbol} ${result.best_trade.entry_date} → ${result.best_trade.exit_date} (${result.best_trade.reason}).`}>
                best <b className="up">{result.best_trade.symbol} {fmtD(result.best_trade.pnl)}</b>
              </span>
            )}
            {result.worst_trade && (
              <span title={`Worst single trade: ${result.worst_trade.symbol} ${result.worst_trade.entry_date} → ${result.worst_trade.exit_date} (${result.worst_trade.reason}).`}>
                worst <b className="down">{result.worst_trade.symbol} {fmtD(result.worst_trade.pnl)}</b>
              </span>
            )}
            <span title="Entry candidates skipped because the stock's average daily dollar volume was too small to absorb the position realistically — an unavailable fill, not a loss.">skipped (liquidity): <b>{result.skipped_no_liquidity || 0}</b></span>
            <span title="Signals ignored because the maximum number of simultaneous open positions was already reached.">skipped (max positions): <b>{result.skipped_max_positions || 0}</b></span>
          </div>
          {result.by_regime && Object.keys(result.by_regime).length > 0 && (
            <table className="bt-regime" title="The same trades bucketed by the S&P 500's condition on entry day (SPY vs its 50/200-day averages): does the edge only exist in one type of market?">
              <thead><tr><th>Market condition</th><th>Trades</th><th>Win rate</th><th>P&L</th></tr></thead>
              <tbody>
                {Object.entries(result.by_regime).map(([r, d]) => (
                  <tr key={r}><td>{r}</td><td>{d.n}</td><td>{d.win_rate}%</td>
                    <td className={d.pnl >= 0 ? "up" : "down"}>{fmtD(d.pnl)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          <button className="rr-btn" onClick={() => setShowTrades(!showTrades)}
                  title="Every closed trade with entry/exit dates, prices, exit reason and P&L (most recent 400).">
            {showTrades ? "hide trades" : `show trades (${(result.trades || []).length})`}</button>
          {showTrades && (
            <div className="bt-trades-wrap">
              <table className="bt-trades">
                <thead><tr><th>Sym</th><th>In</th><th>Out</th><th>Entry</th><th>Exit</th><th>Why</th><th>P&L</th><th>%</th></tr></thead>
                <tbody>
                  {(result.trades || []).slice().reverse().map((t, i) => (
                    <tr key={i}>
                      <td>{t.symbol}{t.option ? ` ${t.option.strike}${t.option.right[0].toUpperCase()}` : ""}</td>
                      <td>{t.entry_date}</td><td>{t.exit_date}</td>
                      <td>${t.entry_px}</td><td>${t.exit_px}</td><td>{t.reason}</td>
                      <td className={t.pnl >= 0 ? "up" : "down"}>{fmtD(t.pnl)}</td>
                      <td className={t.pnl >= 0 ? "up" : "down"}>{t.pnl_pct}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const _memo = React.memo;
Object.assign(window, { TickerLogo, MarketBreadthCard: _memo(MarketBreadthCard),
  WeeklySellSetupCard: _memo(WeeklySellSetupCard),
  BacktestCard: _memo(BacktestCard), PatternDiscoveryCard: _memo(PatternDiscoveryCard),
  PremiumJuiceCard: _memo(PremiumJuiceCard),
  FinvizPanel: _memo(FinvizPanel),
  TVPanel: _memo(TVPanel),
  UWPanel: _memo(UWPanel),
  ValuationCard: _memo(ValuationCard),
  ExpectedMoveCard: _memo(ExpectedMoveCard),
  ReversalRadarCard: _memo(ReversalRadarCard), RadarReportCard: _memo(RadarReportCard),
  RadarAlerts: _memo(RadarAlerts),
  OpenReversalCard: _memo(OpenReversalCard), ReversalAlerts: _memo(ReversalAlerts),
  CommandPalette, ShortcutsSheet,
  MarketContextBar: _memo(MarketContextBar), PicksJournalCard: _memo(PicksJournalCard),
  VolSkewCard: _memo(VolSkewCard), WatchlistTableCard: _memo(WatchlistTableCard),
  AnalystBoardCard: _memo(AnalystBoardCard), MoversCard: _memo(MoversCard),
  TrendCard: _memo(TrendCard), IVRankCard: _memo(IVRankCard),
  RangeEdgeScanCard: _memo(RangeEdgeScanCard),
  WatchlistAlertsCard: _memo(WatchlistAlertsCard), TabBar, TabPanel, WeatherBadge,
  LevelRepriceCard: _memo(LevelRepriceCard), WinRateCard: _memo(WinRateCard),
  EarningsCrushCard: _memo(EarningsCrushCard),
  PushSettingsCard: _memo(PushSettingsCard), BrokerImportCard: _memo(BrokerImportCard),
  StrategyReferenceCard: _memo(StrategyReferenceCard), WatchlistManager, QuickAddRow,
  WatchlistRow, FlashOnChange, SortableTh, PercentCalc: _memo(PercentCalc),
  RollManagerCard: _memo(RollManagerCard),
  FlowScoreCard: _memo(FlowScoreCard), PullbackBacktest,
  TradeBuilderCard: _memo(TradeBuilderCard), AnalystCard: _memo(AnalystCard),
  PullbackProfileCard: _memo(PullbackProfileCard), BasingCard: _memo(BasingCard),
  Recommendation, RecommendationPair, StrategyCard: _memo(StrategyCard),
  PositionsCard: _memo(PositionsCard), AddPositionForm,
  MarketCalendarCard: _memo(MarketCalendarCard), NewsTicker: _memo(NewsTicker),
  WatchlistAnalystCard: _memo(WatchlistAnalystCard), StockProfileCard: _memo(StockProfileCard),
  NewsHub: _memo(NewsHub), SchwabReconnect: _memo(SchwabReconnect),
  WatchlistStreaksCard: _memo(WatchlistStreaksCard), LeftRail52W: _memo(LeftRail52W),
  LeftRailDailyHigh: _memo(LeftRailDailyHigh),
  RightRail52WLow: _memo(RightRail52WLow), RightRailDailyLow: _memo(RightRailDailyLow),
  MarketOverview: _memo(MarketOverview), MarketPosture: _memo(MarketPosture) });

// ── Weekly Option Selling Setup (v3.48) ─────────────────────────────────────
// Compact glance panel under Weekly Returns History: where does THIS week sit
// between the selected period's worst weekly low and best weekly high, and
// what does the currently selected weekly put/call look like from here.
// Data rules (per spec): premiums/IV/greeks come from the live Schwab chain;
// when the chain's greeks are backfilled (delta_est/theta_est flags from the
// backend, or an invalid value) the panel falls back to Black-Scholes FROM THE
// CHAIN'S OWN IV and labels it est.; breach rates come from the displayed
// weekly history; expected move = the chain's own ATM straddle to the selected
// Friday (emBand from the EM engine wins when present). Anything missing
// renders "Data unavailable" — nothing is manufactured.
function _wosCdf(x) {
  // Abramowitz-Stegun normal CDF (no Math.erf in older engines)
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989423 * Math.exp(-x * x / 2);
  let p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return x > 0 ? 1 - p : p;
}
function _wosGreeks(spot, strike, ivPct, dteDays, side) {
  const iv = ivPct > 3 ? ivPct / 100 : ivPct;          // chain iv may be 0.42 or 42
  if (!spot || !strike || !iv || iv <= 0 || dteDays == null) return null;
  const T = Math.max(0.5, dteDays) / 365, r = 0.04;
  const d1 = (Math.log(spot / strike) + (r + iv * iv / 2) * T) / (iv * Math.sqrt(T));
  const d2 = d1 - iv * Math.sqrt(T);
  const delta = side === "put" ? _wosCdf(d1) - 1 : _wosCdf(d1);
  const pdf = Math.exp(-d1 * d1 / 2) / Math.sqrt(2 * Math.PI);
  const theta = (-(spot * pdf * iv) / (2 * Math.sqrt(T))
    + (side === "put" ? r * strike * Math.exp(-r * T) * _wosCdf(-d2)
                      : -r * strike * Math.exp(-r * T) * _wosCdf(d2))) / 365;
  return { delta, theta, probOTM: side === "put" ? (1 - Math.abs(delta)) * 100 : (1 - delta) * 100 };
}

function WeeklySellSetupCard({ rows, weeks, ticker, currentPrice, baselinePrice, currReturn,
                               putC, callC, emBand, emStraddle, expiration }) {
  if (!rows || rows.length < 4 || !currentPrice || !baselinePrice) {
    return null;
  }
  const lows = rows.map(r => r.low_return), highs = rows.map(r => r.high_return);
  const worstLow = Math.min(...lows), bestHigh = Math.max(...highs);
  const pLow = baselinePrice * (1 + worstLow / 100);
  const pHigh = baselinePrice * (1 + bestHigh / 100);
  const span = Math.max(0.01, bestHigh - worstLow);
  const rawPos = ((currReturn - worstLow) / span) * 100;
  const pos = Math.max(0, Math.min(100, rawPos));
  const outside = rawPos < 0 ? "below" : rawPos > 100 ? "above" : null;
  const bottomProx = 100 - pos;
  const dLow$ = currentPrice - pLow, dLowPts = currReturn - worstLow;
  const dHigh$ = pHigh - currentPrice, dHighPts = bestHigh - currReturn;

  // Day-of-week context (v3.51): CURRENT is a week-in-progress compared
  // against COMPLETED weeks' extremes — so how much week is left matters.
  // rows[].low_day / high_day say which weekday each week's extreme printed;
  // by mid-week most lows are usually already in, which is exactly why
  // "near the range low on Wed/Thu" historically has little room left below.
  const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
  const dow = (() => {
    try {
      const d = new Date().toLocaleDateString("en-US", { timeZone: "America/New_York", weekday: "short" });
      const i = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4 }[d.slice(0, 3)];
      return i == null ? 4 : i;             // weekend → treat week as complete
    } catch (e) { return 4; }
  })();
  const withDays = rows.filter(r => r.low_day != null && r.high_day != null);
  const lowsInBy = withDays.length ? withDays.filter(r => r.low_day <= dow).length / withDays.length * 100 : null;
  const highsInBy = withDays.length ? withDays.filter(r => r.high_day <= dow).length / withDays.length * 100 : null;

  // Days to the selected weekly expiration (calendar), 4pm ET close.
  let dte = null;
  if (expiration) {
    const ms = new Date(expiration + "T16:00:00-04:00") - new Date();
    dte = Math.max(0, Math.round(ms / 86400000 * 10) / 10);
  }

  // Expected move to the selected Friday. Prefer the EM engine's band when
  // it's loaded for this ticker; otherwise use the chain's own ATM straddle
  // mid (the option market's priced move to this expiry) — real quotes, not
  // an estimate. Only when neither exists does the row say Data unavailable.
  const emFromBand = emBand && emBand.high != null && emBand.low != null;
  let emUp = null, emDn = null;
  if (emFromBand) {
    emUp = emBand.high - currentPrice;
    emDn = currentPrice - emBand.low;
  } else if (emStraddle > 0) {
    emUp = emStraddle;
    emDn = emStraddle;
  }
  const emPct = emUp != null && emDn != null ? ((emUp + emDn) / 2) / currentPrice * 100 : null;

  const chainOK = c => c && c.strike != null && ((c.bid || 0) > 0 || (c.ask || 0) > 0);
  const midOf = c => (c.bid > 0 && c.ask > 0) ? (c.bid + c.ask) / 2 : (c.bid || c.ask || null);

  function sideStats(c, side) {
    if (!chainOK(c)) return { ok: false };
    const mid = midOf(c);
    const breakeven = side === "put" ? c.strike - mid : c.strike + mid;
    const beDistPct = side === "put"
      ? (currentPrice - breakeven) / currentPrice * 100
      : (breakeven - currentPrice) / currentPrice * 100;
    // Schwab's chain carries real greeks — use them and only fall back to a
    // Black-Scholes estimate (tagged est) when they're absent. The backend
    // flags backfilled greeks (delta_est/theta_est — e.g. Schwab's -999
    // sentinels outside market hours); sanity-bound values too for old
    // cached payloads that predate the flags.
    const liveDelta = (!c.delta_est && typeof c.delta === "number" && isFinite(c.delta) && Math.abs(c.delta) <= 1 && c.delta !== 0) ? c.delta : null;
    const liveTheta = (!c.theta_est && typeof c.theta === "number" && isFinite(c.theta) && Math.abs(c.theta) < 100 && c.theta !== 0) ? c.theta : null;
    const g = _wosGreeks(currentPrice, c.strike, c.iv, dte != null ? dte : 5, side);
    const delta = liveDelta != null ? liveDelta : (g && g.delta);
    const theta = liveTheta != null ? liveTheta : (g && g.theta);
    const probOTM = delta != null ? (side === "put" ? (1 - Math.abs(delta)) * 100 : (1 - Math.max(0, delta)) * 100) : null;
    const strikeEq = (c.strike / baselinePrice - 1) * 100;
    const breachN = side === "put"
      ? rows.filter(r => r.low_return <= strikeEq).length
      : rows.filter(r => r.high_return >= strikeEq).length;
    const rbp = side === "put"
      ? mid / (c.strike - mid) * 100                     // vs cash-secured collateral
      : mid / currentPrice * 100;                        // vs 100 shares held
    return { ok: true, strike: c.strike, bid: c.bid || null, mid, breakeven, beDistPct,
             delta, theta, probOTM, deltaLive: liveDelta != null, thetaLive: liveTheta != null,
             breachN, breachPct: breachN / rows.length * 100, rbp, iv: c.iv };
  }
  const P = sideStats(putC, "put");
  const C = sideStats(callC, "call");

  // ── Bias: location + breach + P(OTM) + EM cushion, scaled by time left.
  // Components missing from live data are excluded and weights renormalized.
  function fit(sideS, locScore) {
    const parts = [[0.35, locScore]];
    if (sideS.ok) {
      parts.push([0.20, 100 - sideS.breachPct]);
      if (sideS.probOTM != null) parts.push([0.20, sideS.probOTM]);
      if (emPct != null && sideS.beDistPct != null)
        parts.push([0.15, Math.min(100, (sideS.beDistPct / Math.max(0.2, emPct)) * 66)]);
    }
    const wsum = parts.reduce((a, p) => a + p[0], 0);
    return parts.reduce((a, p) => a + p[0] * p[1], 0) / wsum;
  }
  const putFit = fit(P, 100 - pos);
  const callFit = fit(C, pos);
  const timing = dte != null ? (dte <= 1 ? 1.15 : dte <= 2 ? 1.05 : 1) : 1;
  const diff = (putFit - callFit) * timing;
  const bias = diff >= 15 ? ["Strong Put Location", "up"]
    : diff >= 6 ? ["Moderate Put Location", "up"]
    : diff <= -15 ? ["Strong Call Location", "down"]
    : diff <= -6 ? ["Moderate Call Location", "down"]
    : ["Neutral", "mut"];

  const NA = <span className="wos-na" title="Live option-chain data for this field is unavailable right now — nothing is estimated in its place.">Data unavailable</span>;
  const f$ = v => v == null ? NA : fmt$(v, v >= 1000 ? 0 : 2);
  const fp = (v, d = 1) => v == null ? NA : `${v >= 0 ? "+" : ""}${v.toFixed(d)}%`;

  const Row = ({ l, v, hot, tip }) => (
    <div className={`wos-r ${hot ? "hot" : ""}`} title={tip}><em>{l}</em><b>{v}</b></div>
  );

  function SideCol({ label, S, side }) {
    const col = side === "put" ? "var(--up)" : "var(--down)";
    return (
      <div className="wos-side">
        <div className="wos-side-h" style={{ color: col }}>{label}
          {S.ok && <span className="wos-strike num">${S.strike}</span>}
        </div>
        {!S.ok ? <div className="wos-na" style={{ padding: "8px 0" }} title="No live bid/ask for the selected weekly contract.">Data unavailable</div> : (
          <React.Fragment>
            <Row l="Bid / mid" v={<span className="num">{S.bid != null ? fmt$(S.bid, 2) : "—"} / {fmt$(S.mid, 2)}</span>} tip="Live chain bid and mid for the selected weekly contract." />
            <Row hot l="Breakeven" v={<span className="num">{fmt$(S.breakeven, 2)} · {fp(S.beDistPct)}</span>} tip="Strike ∓ premium, and how far price is from it. Your real cushion." />
            <Row l="Delta" v={S.delta != null ? <span className="num">{S.delta.toFixed(2)}{!S.deltaLive && <i className="wos-est" title="Chain didn't include a live delta — Black-Scholes estimate from its IV.">est</i>}</span> : NA} tip={S.deltaLive ? "Live delta from the Schwab option chain." : "Black-Scholes delta derived from the chain's own IV — estimated, not quoted."} />
            <Row hot l="P(expire OTM)" v={S.probOTM != null ? <span className="num">{S.probOTM.toFixed(0)}% <i className="wos-est">est</i></span> : NA} tip={S.deltaLive ? "1 − |live Schwab delta| — the option market's implied odds of finishing out of the money. An estimate by nature, not a guarantee." : "Delta-based estimate from the chain's own IV. NOT a guarantee."} />
            <Row hot l={`Breach rate · ${weeks}w`} v={<span className="num">{S.breachPct.toFixed(0)}% ({S.breachN}/{rows.length})</span>} tip={side === "put" ? `How often, in the displayed ${rows.length} weeks, the stock traded BELOW this strike's equivalent level before Friday.` : `How often, in the displayed ${rows.length} weeks, the stock traded ABOVE this strike's equivalent level before Friday.`} />
            <Row l="EM to Friday" v={emPct != null ? <span className="num">±{emPct.toFixed(1)}% {emFromBand ? `(${f$(emDn)}/${f$(emUp)})` : `(±${fmt$(emUp, emUp >= 1000 ? 0 : 2)})`}</span> : NA} tip={emFromBand ? "Expected move from the EM engine for the selected expiry (down / up dollar band)." : "ATM straddle mid from the live chain — the option market's priced move to the selected Friday expiry."} />
            <Row hot l="Θ / day · DTE" v={<span className="num">{S.theta != null ? fmt$(Math.abs(S.theta), 2) + "/sh" : "—"} · {dte != null ? dte + "d" : "—"}{!S.thetaLive && <i className="wos-est">est</i>}</span>} tip={S.thetaLive ? "Live theta from the Schwab chain (per share, per day — decay in the seller's favor), and days to the weekly expiration." : "Daily decay in your favor (Black-Scholes from chain IV, per share) and days to the weekly expiration."} />
            <Row l="Return on BP" v={<span className="num">{S.rbp != null ? S.rbp.toFixed(2) + "%" : "—"}</span>} tip={side === "put" ? "Premium ÷ cash-secured collateral (strike − premium), for this week." : "Premium ÷ current share value (covered), for this week."} />
          </React.Fragment>
        )}
      </div>
    );
  }

  return (
    <div className="card wos-card">
      <div className="card-head">
        <div>
          <div className="kicker" title={`Where this week sits inside the last ${rows.length} weeks' range, and what the selected weekly contracts offer from here. Everything updates with the ticker, the weeks slider, the strike picker and the live chain.`}>Weekly option selling setup</div>
          <div className="card-title">Sell puts near the lows · calls near the highs</div>
        </div>
        {dte != null && expiration && (
          <span className="wos-dte" title={`Selected weekly expiration ${expiration} — ${dte} days remaining.`}>
            EXP FRI {expiration.slice(5).replace("-", "/")} · <b>{dte}d</b>
          </span>
        )}
        <span className={`wos-bias ${bias[1]}`}
              title={`Combines: range location (${pos.toFixed(1)}% from bottom), historical breach rates, delta-based P(OTM), breakeven cushion vs expected move, and time remaining (${dte != null ? dte + "d" : "n/a"}). It is a LOCATION read, not a trade instruction.`}>
          {bias[0]}
        </span>
      </div>

      <div className="wos-range" title={`This week's return (${fp(currReturn, 2)}) positioned between the worst weekly low and best weekly high of the displayed ${rows.length} weeks.`}>
        <div className="wos-rl-h">{rows.length} WEEK RANGE LOCATION</div>
        <div className="wos-trackwrap">
          <span className="wos-now-label" style={{ left: `${pos}%` }}
                title="This week, live.">NOW <b className="num">{fp(currReturn, 2)}</b>{outside ? ` · ${outside.toUpperCase()} RANGE` : ""}</span>
          <div className="wos-track">
            <i className="wos-marker" style={{ left: `${pos}%` }}></i>
          </div>
        </div>
        <div className="wos-ends">
          <span className="wos-end lo" title={`Worst weekly low of the ${rows.length} displayed weeks, and the price it maps to off this week's baseline.`}>
            <em>WORST LOW</em><b className="num">{fp(worstLow)}</b><span className="num">{f$(pLow)}</span>
          </span>
          <span className="wos-end hi" title={`Best weekly high of the ${rows.length} displayed weeks, and the price it maps to.`}>
            <em>BEST HIGH</em><b className="num">{fp(bestHigh)}</b><span className="num">{f$(pHigh)}</span>
          </span>
        </div>
        <div className="wos-prox-box"
             title="How close this week's return sits to the historical LOW side of the selected range. A location measure only — NOT the probability that a put expires worthless.">
          <em>BOTTOM PROXIMITY</em>
          <b className={`num ${bottomProx >= 66 ? "cu" : bottomProx <= 33 ? "cd" : ""}`}>{bottomProx.toFixed(1)}%</b>
          <span>{bottomProx >= 66 ? `close to the ${rows.length}-week low side` : bottomProx <= 33 ? `close to the ${rows.length}-week high side` : "middle of the range"}</span>
        </div>
        <div className="wos-posline">
          <span title={`Gap between this week's return (${fp(currReturn, 2)}) and each extreme, in dollars and weekly-return percentage (e.g. −8.8% vs −19.0% = ${Math.abs(dLowPts).toFixed(1)}% apart).`}>
            <b className="num cd">{f$(Math.abs(dLow$))}</b> · {Math.abs(dLowPts).toFixed(1)}% above the worst low
            &nbsp;·&nbsp; <b className="num cu">{f$(Math.abs(dHigh$))}</b> · {Math.abs(dHighPts).toFixed(1)}% below the best high
          </span>
        </div>
        {lowsInBy != null && (
          <div className="wos-dayctx"
               title={`CURRENT is this week IN PROGRESS measured against COMPLETED weeks' full Mon–Fri extremes. In the displayed ${withDays.length} weeks, the weekly LOW had already printed by ${DAY_NAMES[dow]} in ${lowsInBy.toFixed(0)}% of them (the HIGH in ${highsInBy.toFixed(0)}%). Late in the week + near the range low = historically little room left below — the setup you buy or sell puts into.`}>
            <em>{DAY_NAMES[dow].toUpperCase()}{dow === 4 ? " · WEEK NEARLY COMPLETE" : ""}</em>
            <span>weekly LOW already in by now: <b className={`num ${lowsInBy >= 70 ? "cu" : ""}`}>{lowsInBy.toFixed(0)}%</b> of weeks{"  ·  "}weekly HIGH already in: <b className="num">{highsInBy.toFixed(0)}%</b></span>
          </div>
        )}
      </div>

      <div className="wos-sides">
        <SideCol label="SELL PUT" S={P} side="put" />
        <SideCol label="SELL CALL" S={C} side="call" />
      </div>
    </div>
  );
}

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
            <div className="kicker">Front-month continuous · PRICES move opposite to yields · delayed</div>
            <div className="card-title">Treasury futures</div>
          </div>
        </div>
        <div className="tsy-tablewrap">
          <table className="tsy-table">
            <thead><tr><th>Contract</th><th>Last</th><th>Day %</th><th>Day range</th><th>Volume</th></tr></thead>
            <tbody>
              {futs.map(f => (
                <tr key={f.code}>
                  <td><b>{f.code}</b> <span className="muted">{f.label}</span></td>
                  {f.ok ? (
                    <React.Fragment>
                      <td className="num"><b>{f.last}</b></td>
                      <td className={`num ${f.chg_pct != null ? (f.chg_pct >= 0 ? "cu" : "cd") : ""}`} title="Futures PRICE change — price up means yields down.">{f.chg_pct != null ? `${f.chg_pct >= 0 ? "+" : ""}${f.chg_pct}%` : "—"}</td>
                      <td className="num">{f.day_lo} – {f.day_hi}</td>
                      <td className="num">{f.volume != null ? f.volume.toLocaleString() : "—"}</td>
                    </React.Fragment>
                  ) : <td colSpan="4"><TsyNA /></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="tsy-sigd">{mk.d.futures_note}</div>
        <TsyFoot src={mk.d.source} delayed />
      </div>
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

/* ── The tab ───────────────────────────────────────────────────────────── */
function TreasuriesTab({ apiFetch, onOpenTicker }) {
  const core = useTsy(apiFetch, "core", 900000);
  return (
    <div className="tsy">
      <TsyYieldCards core={core} />
      <TsyCurveCard core={core} />
      {/* Two independent column stacks (not grid rows) so a short card never
          leaves dead space beside a tall neighbor. */}
      <div className="tsy-grid2">
        <div className="tsy-col">
          <TsySpreadsCard core={core} />
          <TsyEventsCard core={core} />
          <TsyMoveCard core={core} />
        </div>
        <div className="tsy-col">
          <TsySignalsCard core={core} />
          <TsyExpectationsCard core={core} />
        </div>
      </div>
      <TsyCpiCard apiFetch={apiFetch} />
      <TsyFold kicker="History · how markets traded past prints" title="CPI releases & market reaction" hint="expand to load">
        <TsyCpiReactions apiFetch={apiFetch} />
      </TsyFold>
      <TsyMarketsCards apiFetch={apiFetch} onOpenTicker={onOpenTicker} />
      <TsyFedCard apiFetch={apiFetch} />
      <TsyFold kicker="Supply · demand at the margin" title="Treasury auctions" hint="expand to load">
        <TsyAuctions apiFetch={apiFetch} />
      </TsyFold>
      <TsyFold kicker="CFTC weekly positioning" title="COT — Treasury futures positioning" hint="expand to load">
        <TsyCot apiFetch={apiFetch} />
      </TsyFold>
      <TsyFold kicker="Rolling correlation vs Δ10y" title="Cross-asset relationships" hint="expand to load">
        <TsyCorrTable apiFetch={apiFetch} />
      </TsyFold>
      <TsyFold kicker="Your watchlist × yield factors" title="Rate sensitivity watchlist" hint="expand to scan">
        <TsySense apiFetch={apiFetch} onOpenTicker={onOpenTicker} />
      </TsyFold>
      <TsyFold kicker="Threshold rules on the displayed data" title="Rates alerts" hint="expand to configure" defaultOpen={false}>
        <TsyAlertsCard core={core} />
      </TsyFold>
    </div>
  );
}

Object.assign(window, { TreasuriesTab: _memo(TreasuriesTab) });
