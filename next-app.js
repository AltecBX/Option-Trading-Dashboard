(function(){
const {
  useState,
  useEffect,
  useRef,
  useMemo
} = React;
const NEXT_VERSION = "4.0.3-next";
const CFG = typeof window !== "undefined" && window.__APP_CONFIG || {};
function api(path) {
  const headers = {};
  if (CFG.apiKey) headers["X-API-Key"] = CFG.apiKey;
  return fetch(path, {
    headers
  }).then(r => r.json());
}
function usePoll(path, ms, enabled = true) {
  const [state, setState] = useState({
    data: null,
    stale: false,
    at: null
  });
  useEffect(() => {
    if (!enabled) return;
    let dead = false,
      t;
    const tick = () => {
      if (document.hidden) {
        t = setTimeout(tick, ms);
        return;
      }
      api(path).then(d => {
        if (dead) return;
        if (d && !d.error) setState({
          data: d,
          stale: false,
          at: new Date()
        });else setState(s => ({
          ...s,
          stale: true
        }));
      }).catch(() => {
        if (!dead) setState(s => ({
          ...s,
          stale: true
        }));
      }).finally(() => {
        if (!dead) t = setTimeout(tick, ms);
      });
    };
    tick();
    return () => {
      dead = true;
      clearTimeout(t);
    };
  }, [path, ms, enabled]);
  return state;
}
const pick = (o, ...keys) => {
  for (const k of keys) if (o && o[k] != null) return o[k];
  return null;
};
const asArr = v => Array.isArray(v) ? v : v && typeof v === "object" ? Object.values(v) : [];
const fmtPct = v => v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
const SPARK_KEY = "next_spark_v1";
let SPARKS = {};
try {
  SPARKS = JSON.parse(localStorage.getItem(SPARK_KEY) || "{}") || {};
} catch (e) {}
function pushSpark(label, v) {
  if (v == null || !isFinite(v)) return [];
  const a = SPARKS[label] = SPARKS[label] || [];
  if (a.length === 0 || a[a.length - 1] !== v) {
    a.push(v);
    if (a.length > 90) a.shift();
    try {
      localStorage.setItem(SPARK_KEY, JSON.stringify(SPARKS));
    } catch (e) {}
  }
  return a;
}
const fmtN = v => v == null ? "—" : Number(v).toLocaleString(undefined, {
  maximumFractionDigits: 2
});
function Spark({
  vals,
  up,
  w = 76,
  h = 38
}) {
  if (!vals || vals.length < 2) return null;
  const lo = Math.min(...vals),
    hi = Math.max(...vals),
    span = Math.max(1e-9, hi - lo);
  const pts = vals.map((v, i) => [i / (vals.length - 1) * w, h - 5 - (v - lo) / span * (h - 10)]);
  const line = "M" + pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("L");
  const col = up ? "#3BD996" : "#F56D77";
  const gid = "sg" + Math.abs(vals[0] * 7919 + vals.length | 0) + (up ? "u" : "d");
  return React.createElement("svg", {
    viewBox: `0 0 ${w} ${h}`,
    preserveAspectRatio: "none"
  }, React.createElement("defs", null, React.createElement("linearGradient", {
    id: gid,
    x1: "0",
    y1: "0",
    x2: "0",
    y2: "1"
  }, React.createElement("stop", {
    offset: "0",
    stopColor: col,
    stopOpacity: ".22"
  }), React.createElement("stop", {
    offset: "1",
    stopColor: col,
    stopOpacity: "0"
  }))), React.createElement("path", {
    d: `${line}L${w},${h}L0,${h}Z`,
    fill: `url(#${gid})`
  }), React.createElement("path", {
    d: line,
    fill: "none",
    stroke: col,
    strokeWidth: "1.5"
  }), React.createElement("circle", {
    r: "2.2",
    fill: col,
    cx: pts[pts.length - 1][0],
    cy: pts[pts.length - 1][1]
  }));
}
function Clock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const et = now.toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit"
  });
  const d = now.toLocaleDateString("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    month: "numeric",
    day: "numeric"
  });
  return React.createElement("div", {
    className: "stat",
    title: "Live session clock (Eastern)"
  }, React.createElement("em", null, "Live · ", d), React.createElement("b", null, et));
}
function Weather() {
  const [wx, setWx] = useState(null);
  const [mode, setMode] = useState(() => {
    try {
      return localStorage.getItem("next_wx") || "yonkers";
    } catch (e) {
      return "yonkers";
    }
  });
  useEffect(() => {
    let dead = false;
    const load = (lat, lon) => fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,weather_code&temperature_unit=fahrenheit`).then(r => r.json()).then(d => {
      if (!dead && d && d.current) setWx(d.current);
    }).catch(() => {});
    if (mode === "device" && navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(p => load(p.coords.latitude, p.coords.longitude), () => load(40.93, -73.9));
    } else load(40.93, -73.9);
    return () => {
      dead = true;
    };
  }, [mode]);
  const icon = wx ? wx.weather_code === 0 ? "☀" : wx.weather_code < 50 ? "☁" : "🌧" : "☁";
  return React.createElement("span", {
    className: "pill",
    style: {
      cursor: "pointer"
    },
    title: `Weather — ${mode === "yonkers" ? "Yonkers" : "your location"}. Tap to toggle, same as the classic app.`,
    onClick: () => {
      const m = mode === "yonkers" ? "device" : "yonkers";
      setMode(m);
      try {
        localStorage.setItem("next_wx", m);
      } catch (e) {}
    }
  }, icon, " ", wx ? Math.round(wx.temperature_2m) + "°" : "—");
}
function CommandBar({
  ticker,
  setTicker,
  alerts
}) {
  const [input, setInput] = useState(ticker);
  const q = usePoll(`/api/quote?symbol=${encodeURIComponent(ticker)}`, 15000);
  const mo = usePoll("/api/market_overview", 30000);
  const wt = usePoll("/api/watchlist_table", 300000);
  const quote = q.data || {};
  const px = pick(quote, "last", "price", "mark", "close");
  const chg = pick(quote, "change_pct", "chg_pct", "pct", "percent_change");
  const wlRow = useMemo(() => asArr(wt.data && (wt.data.rows || wt.data.board)).find(r => (pick(r, "symbol", "sym") || "").toUpperCase() === ticker) || null, [wt.data, ticker]);
  const earn = wlRow && pick(wlRow, "next_earnings", "earnings");
  const earnDays = wlRow && pick(wlRow, "days_to_earnings");
  const insts = asArr(mo.data && mo.data.instruments);
  const spx = insts.find(i => /s&p/i.test(pick(i, "label", "name") || ""));
  const spxChg = spx && pick(spx, "change_pct", "chg_pct");
  const postureStr = spxChg == null ? "—" : spxChg > 0.25 ? "BULLISH" : spxChg < -0.25 ? "BEARISH" : "NEUTRAL";
  return React.createElement("div", {
    className: "cmd"
  }, React.createElement("div", {
    className: "brand",
    title: "JerryTrade /next — the parallel Decision Cockpit. Your classic site is untouched at /"
  }, React.createElement("div", {
    className: "logo",
    style: {
      overflow: "hidden"
    }
  }, React.createElement("img", {
    src: "/assets/app-logo.png",
    alt: "Jerry",
    style: {
      width: "100%",
      height: "100%",
      objectFit: "contain"
    }
  })), React.createElement("div", {
    className: "brandtx"
  }, React.createElement("b", null, React.createElement("span", {
    style: {
      color: "var(--fg)"
    }
  }, "Jer"), React.createElement("span", {
    className: "ry",
    style: {
      color: "var(--acc)"
    }
  }, "ry"), "Trade"), React.createElement("span", null, "DECISION COCKPIT · /next"))), React.createElement("form", {
    className: "search",
    style: {
      cursor: "text"
    },
    title: "Type a ticker and press Enter — every card follows it (Phase 2 wires full search + ask).",
    onSubmit: e => {
      e.preventDefault();
      const s = input.trim().toUpperCase();
      if (s) setTicker(s);
    }
  }, "⌕\xA0", React.createElement("input", {
    value: input,
    onChange: e => setInput(e.target.value),
    style: {
      background: "none",
      border: "none",
      outline: "none",
      color: "var(--fg)",
      font: "inherit",
      width: "100%"
    },
    placeholder: "Ticker… (Enter)",
    spellCheck: false
  }), React.createElement("span", {
    className: "k"
  }, "⏎")), React.createElement("div", {
    className: "tkr",
    title: `Live quote for ${ticker}${q.stale ? " — STALE (last good kept)" : ""}`
  }, React.createElement("span", {
    className: "sym"
  }, ticker), React.createElement("span", {
    className: "px num"
  }, px != null ? fmtN(px) : "—"), React.createElement("span", {
    className: "chgp",
    style: chg != null && chg < 0 ? {
      color: "var(--down)",
      background: "var(--down-dim)"
    } : null
  }, fmtPct(chg)), q.stale && React.createElement("span", {
    className: "chip wn",
    title: "Quote fetch failed — showing the last good value"
  }, "STALE")), React.createElement("div", {
    className: "spacer"
  }), React.createElement(Clock, null), React.createElement("div", {
    className: "vdiv"
  }), React.createElement("div", {
    className: "stat",
    title: "Market posture — S&P futures direction, refined by watchlist breadth"
  }, React.createElement("em", null, "Posture"), React.createElement("b", {
    className: /BULL/.test(postureStr) ? "cu" : /BEAR/.test(postureStr) ? "cd" : "cw"
  }, postureStr)), React.createElement("div", {
    className: "vdiv"
  }), React.createElement("div", {
    className: "stat",
    title: earn ? `Next earnings for ${ticker}: ${earn}` : "Next earnings for the active ticker (from the watchlist board)"
  }, React.createElement("em", null, "Earnings"), React.createElement("b", null, earn ? `${String(earn).slice(5).replace("-", "/")}${earnDays != null ? ` · ${earnDays}d` : ""}` : "—")), React.createElement("div", {
    className: "vdiv"
  }), React.createElement("span", {
    className: "pill live",
    title: "Schwab data link"
  }, React.createElement("span", {
    className: "dot"
  }), "SCHWAB"), React.createElement("span", {
    className: "pill uwp",
    title: "Unusual Whales link"
  }, React.createElement("span", {
    className: "dot"
  }), "UW"), React.createElement(Weather, null), React.createElement("span", {
    className: "pill",
    style: {
      color: "var(--fg3)"
    },
    title: "Parallel-app build — the classic site keeps its own version pill"
  }, "v", NEXT_VERSION), React.createElement("div", {
    className: "bell",
    title: `${alerts} live signals on the Today tab right now`
  }, "🔔", alerts > 0 && React.createElement("i", null, alerts)));
}
function MarketStrip() {
  const mo = usePoll("/api/market_overview", 30000);
  const list = asArr(mo.data && (mo.data.instruments || mo.data.rows));
  return React.createElement("div", {
    className: "mkt",
    style: {
      gridTemplateColumns: `repeat(${Math.max(1, list.length || 10)},1fr)`
    }
  }, list.length === 0 && React.createElement("div", {
    className: "mk"
  }, React.createElement("div", {
    className: "lbl"
  }, "MARKET STRIP"), React.createElement("div", {
    className: "row"
  }, React.createElement("span", {
    className: "v",
    style: {
      color: "var(--fg3)"
    }
  }, "loading…"))), list.map((m, i) => {
    const label = pick(m, "label", "name", "sym") || "";
    const last = pick(m, "last", "price", "value");
    const pct = pick(m, "chg_pct", "pct", "change_pct");
    let spark = asArr(pick(m, "spark", "history", "closes"));
    const buf = pushSpark(label, last);
    if (spark.length < 3) spark = buf;
    if (spark.length === 1) spark = [spark[0], spark[0]];
    const up = pct != null ? pct >= 0 : spark.length > 1 ? spark[spark.length - 1] >= spark[0] : true;
    return React.createElement("div", {
      className: "mk",
      key: i,
      title: `${label} — permanently visible on every tab.${mo.stale ? " (STALE — last good kept)" : ""}`
    }, spark.length > 1 && React.createElement(Spark, {
      vals: spark.slice(-60),
      up: up
    }), React.createElement("div", {
      className: "lbl"
    }, String(label).toUpperCase()), React.createElement("div", {
      className: "row"
    }, React.createElement("span", {
      className: "v"
    }, fmtN(last)), React.createElement("span", {
      className: `c ${up ? "cu" : "cd"}`
    }, fmtPct(pct))));
  }));
}
const TABS = [["today", "Today"], ["trade", "Trade"], ["discover", "Discover"], ["analyze", "Analyze"], ["patterns", "Patterns"], ["news", "News"], ["flow", "Flow"], ["scanners", "Scanners"], ["juice", "0DTE Juice"], ["backtest", "Backtest"], ["breadth", "Breadth"], ["journal", "Journal"], ["watchlist", "Watchlist"], ["streaks", "Streaks"], ["calendar", "Market Calendar"], ["manage", "Manage"]];
const SITES = [["finviz", "Finviz", "fvz"], ["tview", "TradingView", "tvw"], ["whales", "Unusual Whales", "uww"]];
function TabBar({
  active,
  onChange,
  earn
}) {
  return React.createElement("div", {
    className: "tabbar"
  }, React.createElement("div", {
    className: "trow"
  }, TABS.map(([id, label]) => React.createElement("button", {
    key: id,
    className: `tab ${active === id ? "on" : ""}`,
    onClick: () => onChange(id),
    title: id === "today" ? "NEW, additive — the morning cockpit. Every other tab is exactly yours." : `${label} — same tab as the classic site`
  }, label)), earn && React.createElement("span", {
    className: "earn",
    title: "Next earnings for the active ticker"
  }, earn)), React.createElement("div", {
    className: "trow sites"
  }, React.createElement("span", {
    className: "slbl"
  }, "Sites -"), SITES.map(([id, label, cls]) => React.createElement("button", {
    key: id,
    className: `tab site ${cls} ${active === id ? "on" : ""}`,
    onClick: () => onChange(id)
  }, label))));
}
function Card({
  title,
  color,
  info,
  more,
  chip,
  children,
  span = 3
}) {
  return React.createElement("div", {
    className: "card",
    style: {
      gridColumn: `span ${span}`
    }
  }, React.createElement("div", {
    className: "chd"
  }, React.createElement("h3", {
    style: color ? {
      color
    } : null
  }, title), info && React.createElement("span", {
    className: "i",
    title: info
  }, "i"), chip, more && React.createElement("span", {
    className: "more"
  }, more)), children);
}
function OpRow({
  badge,
  badgeCls,
  nm,
  why,
  px,
  pc,
  onClick
}) {
  return React.createElement("div", {
    className: "op",
    onClick: onClick,
    style: {
      cursor: onClick ? "pointer" : "default",
      gridTemplateColumns: "36px 1fr auto"
    }
  }, React.createElement("span", {
    className: `score ${badgeCls || "s-hi"}`
  }, badge), React.createElement("div", null, React.createElement("div", {
    className: "nm"
  }, nm), React.createElement("div", {
    className: "why"
  }, why)), React.createElement("div", null, React.createElement("div", {
    className: "px num"
  }, px), React.createElement("div", {
    className: `pc ${String(pc).startsWith("+") ? "cu" : "cd"}`
  }, pc)));
}
function RadarCard({
  side,
  onOpen
}) {
  const r = usePoll("/api/radar", 30000);
  const rows = asArr(r.data && r.data[side]).slice(0, 5);
  const col = side === "long" ? "var(--up)" : "var(--down)";
  return React.createElement(Card, {
    title: `${side === "long" ? "▲ Top Long" : "▼ Top Short"} — Radar`,
    color: col,
    info: `Two-stage scan of your $5B+ watchlist. Score = stretch+exhaustion+location+confirmation+context. Push ≥85, toast ≥80.${r.stale ? " STALE — last scan kept." : ""}`,
    chip: r.stale ? React.createElement("span", {
      className: "chip wn"
    }, "STALE") : r.data && r.data.market_open === false ? React.createElement("span", {
      className: "chip mut",
      title: "Radar scans during market hours"
    }, "OFF-HOURS") : null
  }, React.createElement("div", {
    className: "oplist"
  }, rows.length === 0 && React.createElement("div", {
    style: {
      padding: "6px 10px 10px",
      fontSize: 11.5,
      color: "var(--fg3)"
    }
  }, "No ", side, " candidates right now — the radar only surfaces real setups."), rows.map((s, i) => React.createElement(OpRow, {
    key: i,
    badge: Math.round(pick(s, "score", "total") || 0),
    badgeCls: (pick(s, "score", "total") || 0) >= 80 ? "s-hi" : (pick(s, "score", "total") || 0) >= 65 ? "s-md" : "s-lo",
    nm: pick(s, "symbol", "sym"),
    why: pick(s, "note", "reason", "why") || `${side === "long" ? "near day low" : "near day high"} · vs VWAP ${fmtN(pick(s, "vwap_dist", "vwap_sigma"))}σ`,
    px: fmtN(pick(s, "price", "last")),
    pc: fmtPct(pick(s, "chg_pct", "day_pct", "pct")),
    onClick: () => onOpen(pick(s, "symbol", "sym"))
  }))));
}
function JuiceCard({
  onOpen
}) {
  const j = usePoll("/api/juice", 60000);
  const rows = asArr(j.data && j.data.rows).slice(0, 5);
  return React.createElement(Card, {
    title: "◈ 0DTE Juice",
    color: "var(--info)",
    info: "Juice Score: straddle premium vs expected move, IV rank, spread quality, volume/OI. DEFINED-risk first when earnings inside the window or spot > $400.",
    chip: j.data && j.data.note ? React.createElement("span", {
      className: "chip wn",
      title: j.data.note
    }, "LAST SCAN KEPT") : null
  }, React.createElement("div", {
    className: "oplist"
  }, rows.length === 0 && React.createElement("div", {
    style: {
      padding: "6px 10px 10px",
      fontSize: 11.5,
      color: "var(--fg3)"
    }
  }, "Juice board fills during market hours."), rows.map((s, i) => React.createElement(OpRow, {
    key: i,
    badge: Math.round(pick(s, "juice", "score") || 0),
    badgeCls: (pick(s, "juice", "score") || 0) >= 80 ? "s-hi" : "s-md",
    nm: pick(s, "symbol", "sym"),
    why: `${pick(s, "expiry", "exp") || ""} · IV ${fmtN(pick(s, "iv", "atm_iv"))} · EM ${fmtN(pick(s, "em_pct"))}%`,
    px: fmtN(pick(s, "price", "spot", "last")),
    pc: pick(s, "straddle") != null ? `$${fmtN(s.straddle)}` : fmtPct(pick(s, "chg_pct")),
    onClick: () => onOpen(pick(s, "symbol", "sym"))
  }))));
}
function WatchesCard({
  onOpen
}) {
  const w = usePoll("/api/patterns/watches", 120000);
  const rows = asArr(w.data && w.data.watches).slice(0, 5);
  return React.createElement(Card, {
    title: "⚑ Pattern watches",
    color: "var(--purple)",
    info: "Your watched behaviors — re-checked against fresh daily data every 30 minutes in market hours; push fires the day a setup triggers."
  }, React.createElement("div", {
    className: "oplist"
  }, rows.length === 0 && React.createElement("div", {
    style: {
      padding: "6px 10px 10px",
      fontSize: 11.5,
      color: "var(--fg3)"
    }
  }, "No watches yet — add them from any discovered pattern (Patterns tab · classic site until Phase 2)."), rows.map((s, i) => React.createElement(OpRow, {
    key: i,
    badge: s.triggered ? "⚑" : "·",
    badgeCls: s.triggered ? "s-hi" : "s-md",
    nm: s.symbol,
    why: (s.sentence || "").slice(0, 64) + "…",
    px: s.triggered ? "TRIGGERED" : "quiet",
    pc: s.checked ? String(s.checked).slice(5) : "",
    onClick: () => onOpen(s.symbol)
  }))));
}
function BreadthCard() {
  const b = usePoll("/api/market_breadth", 120000);
  const stocks = asArr(b.data && b.data.stocks);
  const {
    adv,
    dec,
    flat
  } = useMemo(() => {
    let a = 0,
      d = 0,
      f = 0;
    for (const s of stocks) {
      const c = pick(s, "chg_pct", "day_pct", "pct", "change_pct");
      if (c == null) continue;
      if (c > 0.05) a++;else if (c < -0.05) d++;else f++;
    }
    return {
      adv: a,
      dec: d,
      flat: f
    };
  }, [stocks]);
  const total = adv + dec + flat;
  const n = Math.max(1, total);
  const pctA = Math.round(adv / n * 100),
    pctD = Math.round(dec / n * 100);
  const quiet = total < 5;
  const dash = pctA / 100 * 264;
  return React.createElement(Card, {
    title: "Market breadth",
    info: `Advancers vs decliners across your watchlist universe (${n} scored).`
  }, React.createElement("div", {
    className: "gauge"
  }, React.createElement("svg", {
    width: "104",
    height: "104",
    viewBox: "0 0 104 104"
  }, React.createElement("circle", {
    cx: "52",
    cy: "52",
    r: "42",
    fill: "none",
    stroke: "var(--bg4)",
    strokeWidth: "10"
  }), React.createElement("circle", {
    cx: "52",
    cy: "52",
    r: "42",
    fill: "none",
    stroke: "#3BD996",
    strokeWidth: "10",
    strokeDasharray: `${dash} 264`,
    strokeLinecap: "round",
    transform: "rotate(-90 52 52)",
    style: {
      filter: "drop-shadow(0 0 6px rgba(56,225,160,.4))"
    }
  }), React.createElement("text", {
    x: "52",
    y: "50",
    textAnchor: "middle",
    fill: "var(--fg)",
    fontFamily: "JetBrains Mono,monospace",
    fontSize: "22",
    fontWeight: "800"
  }, quiet ? "—" : pctA), React.createElement("text", {
    x: "52",
    y: "66",
    textAnchor: "middle",
    fill: "var(--fg3)",
    fontFamily: "JetBrains Mono,monospace",
    fontSize: "7.5"
  }, quiet ? "OFF-HOURS" : pctA >= 55 ? "BULLISH" : pctA <= 45 ? "BEARISH" : "MIXED")), React.createElement("div", {
    className: "dlegend"
  }, React.createElement("div", null, React.createElement("span", {
    className: "dot2",
    style: {
      background: "var(--up)"
    }
  }), "Advancing", React.createElement("b", null, quiet ? "—" : pctA + "%")), React.createElement("div", null, React.createElement("span", {
    className: "dot2",
    style: {
      background: "var(--down)"
    }
  }), "Declining", React.createElement("b", null, quiet ? "—" : pctD + "%")), React.createElement("div", null, React.createElement("span", {
    className: "dot2",
    style: {
      background: "var(--fg4)"
    }
  }), "Flat", React.createElement("b", null, quiet ? "—" : Math.max(0, 100 - pctA - pctD) + "%")))));
}
function EventsCard() {
  const ec = usePoll("/api/market_calendar/economic", 300000);
  const events = asArr(ec.data && ec.data.events).slice(0, 6);
  return React.createElement(Card, {
    title: "Today's events",
    info: "Macro prints + market schedule — the Market Calendar tab has the full week.",
    more: "calendar →"
  }, React.createElement("div", {
    className: "tl"
  }, events.length === 0 && React.createElement("div", {
    style: {
      fontSize: 11.5,
      color: "var(--fg3)",
      padding: "4px 0 10px"
    }
  }, "No scheduled events loaded."), events.map((e, i) => React.createElement("div", {
    className: "ev",
    key: i
  }, React.createElement("b", null, (pick(e, "time", "when") || "").slice(0, 6) || "—"), React.createElement("span", {
    className: "nd"
  }), React.createElement("span", null, pick(e, "event", "title", "name"))))));
}
function PositionsCard({
  onOpen
}) {
  const p = usePoll("/api/broker/owned", 120000);
  const syms = asArr(p.data && p.data.symbols);
  return React.createElement(Card, {
    title: "Positions",
    info: "Symbols currently held at Schwab (import detail + journal land here in Phase 2).",
    more: "classic →"
  }, React.createElement("div", {
    className: "kpis"
  }, React.createElement("div", {
    className: "kpi"
  }, React.createElement("em", null, "Configured"), React.createElement("b", {
    className: p.data && p.data.configured ? "cu" : "cw"
  }, p.data ? p.data.configured ? "YES" : "NO" : "—")), React.createElement("div", {
    className: "kpi"
  }, React.createElement("em", null, "Held symbols"), React.createElement("b", null, syms.length))), React.createElement("div", {
    className: "xchips",
    style: {
      paddingTop: 0
    }
  }, syms.slice(0, 10).map((s, i) => React.createElement("span", {
    key: i,
    className: "chip in",
    style: {
      cursor: "pointer"
    },
    onClick: () => onOpen(typeof s === "string" ? s : s.symbol)
  }, typeof s === "string" ? s : s.symbol)), syms.length === 0 && React.createElement("span", {
    style: {
      fontSize: 11.5,
      color: "var(--fg3)"
    }
  }, "No positions loaded.")));
}
function AlertsCard({
  onOpen
}) {
  const a = usePoll("/api/watchlist_alerts", 180000);
  const rows = asArr(a.data && a.data.alerts).slice(0, 5);
  return React.createElement(Card, {
    title: "Watchlist alerts",
    info: "Analyst moves and high-impact changes across your watchlist (background scan)."
  }, rows.length === 0 && React.createElement("div", {
    style: {
      padding: "0 16px 12px",
      fontSize: 11.5,
      color: "var(--fg3)"
    }
  }, "No active alerts."), rows.map((al, i) => React.createElement("div", {
    className: "alrow",
    key: i,
    style: {
      cursor: "pointer"
    },
    onClick: () => onOpen(pick(al, "symbol", "sym"))
  }, React.createElement("span", {
    className: "nm"
  }, pick(al, "symbol", "sym")), React.createElement("span", {
    style: {
      flex: 1,
      margin: "0 10px",
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap"
    }
  }, pick(al, "title", "note", "kind", "text")), React.createElement("span", {
    className: "chip in"
  }, String(pick(al, "kind", "type") || "alert").slice(0, 10)))));
}
function ExtremesBoard({
  onOpen
}) {
  const hi = usePoll("/api/daily_highs", 30000);
  const lo = usePoll("/api/daily_lows", 30000);
  const wt = usePoll("/api/watchlist_table", 300000);
  const rowsHi = asArr(hi.data && hi.data.rows).slice(0, 8);
  const rowsLo = asArr(lo.data && lo.data.rows).slice(0, 8);
  const wtPoll = 120000;
  const {
    near52H,
    near52L
  } = useMemo(() => {
    const rows = asArr(wt.data && (wt.data.rows || wt.data.board));
    const H = [],
      L = [];
    for (const r of rows) {
      const px = pick(r, "price", "last"),
        h = pick(r, "high52", "week52_high", "high_52w"),
        l = pick(r, "low52", "week52_low", "low_52w");
      if (px && h && px >= h * 0.98) H.push(r);
      if (px && l && l > 0 && px <= l * 1.02) L.push(r);
    }
    return {
      near52H: H.slice(0, 6),
      near52L: L.slice(0, 6)
    };
  }, [wt.data]);
  const Col = ({
    head,
    cls,
    rows,
    title
  }) => React.createElement("div", {
    className: "excol"
  }, React.createElement("div", {
    className: `exh ${cls}`,
    title: title
  }, head), rows.length === 0 && React.createElement("div", {
    style: {
      padding: "4px 10px",
      fontFamily: "var(--mono)",
      fontSize: 10,
      color: "var(--fg4)"
    }
  }, "none right now"), rows.map((r, i) => {
    const sym = pick(r, "symbol", "sym");
    const last = pick(r, "price", "last");
    const chg = pick(r, "change", "chg_pct", "day_pct", "pct", "change_pct");
    return React.createElement("div", {
      className: "exr live",
      key: `${sym}:${last}`,
      style: {
        cursor: "pointer"
      },
      onClick: () => onOpen(sym),
      title: `${sym} — click to load. ${pick(r, "company", "name") || ""}`
    }, React.createElement("span", {
      className: "s"
    }, sym), React.createElement("span", {
      className: "p"
    }, fmtN(last)), React.createElement("span", {
      className: `c ${(chg || 0) >= 0 ? "cu" : "cd"}`
    }, fmtPct(chg)));
  }));
  return React.createElement("div", {
    className: "card exgrid"
  }, React.createElement(Col, {
    head: "▲ Daily high",
    cls: "cu",
    rows: rowsHi,
    title: "Watchlist names printing new session highs"
  }), React.createElement(Col, {
    head: "▼ Daily low",
    cls: "cd",
    rows: rowsLo,
    title: "Watchlist names printing new session lows"
  }), React.createElement(Col, {
    head: "◆ Near 52W high",
    cls: "cu",
    rows: near52H,
    title: "Within 2% of the 52-week high — full watchlist"
  }), React.createElement(Col, {
    head: "◇ Near 52W low",
    cls: "cd",
    rows: near52L,
    title: "Within 2% of the 52-week low — full watchlist, your bottom-fishing pool"
  }));
}
function Today({
  onOpen
}) {
  return React.createElement("section", {
    className: "ws on"
  }, React.createElement("div", {
    className: "tgrid"
  }, React.createElement(RadarCard, {
    side: "long",
    onOpen: onOpen
  }), React.createElement(RadarCard, {
    side: "short",
    onOpen: onOpen
  }), React.createElement(JuiceCard, {
    onOpen: onOpen
  }), React.createElement(WatchesCard, {
    onOpen: onOpen
  }), React.createElement(BreadthCard, null), React.createElement(EventsCard, null), React.createElement(PositionsCard, {
    onOpen: onOpen
  }), React.createElement(AlertsCard, {
    onOpen: onOpen
  }), React.createElement(ExtremesBoard, {
    onOpen: onOpen
  })));
}
const CLASSIC_TABS = {
  trade: 1,
  discover: 1,
  analyze: 1,
  patterns: 1,
  news: 1,
  flow: 1,
  scanners: 1,
  juice: 1,
  backtest: 1,
  breadth: 1,
  journal: 1,
  watchlist: 1,
  streaks: 1,
  calendar: 1,
  manage: 1,
  finviz: 1,
  tview: 1,
  whales: 1
};
function ClassicDock({
  visible,
  tab,
  ticker,
  onClassicState
}) {
  const frameRef = useRef(null);
  const readyRef = useRef(false);
  const queueRef = useRef(null);
  const [loaded, setLoaded] = useState(false);
  const initial = useRef({
    tab: CLASSIC_TABS[tab] ? tab : "trade",
    ticker
  });
  const send = msg => {
    const w = frameRef.current && frameRef.current.contentWindow;
    if (!w) return;
    if (!readyRef.current) {
      queueRef.current = {
        ...(queueRef.current || {}),
        ...msg
      };
      return;
    }
    try {
      w.postMessage({
        jt: "next",
        ...msg
      }, location.origin);
    } catch (e) {}
  };
  useEffect(() => {
    const onMsg = e => {
      if (e.origin !== location.origin || !e.data || e.data.jt !== "classic") return;
      if (e.data.ready) {
        readyRef.current = true;
        setLoaded(true);
        if (queueRef.current) {
          send(queueRef.current);
          queueRef.current = null;
        }
        return;
      }
      onClassicState && onClassicState(e.data);
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);
  useEffect(() => {
    if (CLASSIC_TABS[tab]) send({
      tab
    });
  }, [tab]);
  useEffect(() => {
    if (ticker) send({
      symbol: ticker
    });
  }, [ticker]);
  return React.createElement("div", {
    style: {
      display: visible ? "block" : "none",
      position: "relative",
      height: "100%",
      minHeight: "calc(100vh - 174px)"
    }
  }, !loaded && React.createElement("div", {
    style: {
      position: "absolute",
      inset: 0,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      flexDirection: "column",
      gap: 12,
      color: "var(--fg3)",
      fontFamily: "var(--mono)",
      fontSize: 12,
      letterSpacing: ".1em"
    }
  }, React.createElement("div", {
    className: "pill live"
  }, React.createElement("span", {
    className: "dot"
  }), "LOADING YOUR FULL APP…"), React.createElement("div", {
    style: {
      fontSize: 10
    }
  }, "every feature, live — the new shell around your complete classic app")), React.createElement("iframe", {
    ref: frameRef,
    title: "JerryTrade classic",
    src: `/?embed=1&tab=${encodeURIComponent(initial.current.tab)}&symbol=${encodeURIComponent(initial.current.ticker)}`,
    onLoad: () => setLoaded(true),
    style: {
      width: "100%",
      height: "100%",
      minHeight: "calc(100vh - 174px)",
      border: "none",
      display: "block",
      background: "var(--bg0)",
      opacity: loaded ? 1 : 0,
      transition: "opacity .25s"
    }
  }));
}
function Tape() {
  const n = usePoll("/api/finviz_news?limit=40", 120000);
  const fvItems = asArr(n.data && n.data.items);
  const fb = usePoll("/api/news?symbol=SPY", 300000, n.data != null && fvItems.length === 0);
  const items = (fvItems.length ? fvItems : asArr(fb.data && fb.data.items)).slice(0, 30);
  const track = items.map((it, i) => React.createElement("span", {
    className: "titem",
    key: i
  }, React.createElement("span", {
    className: "t"
  }, String(pick(it, "date", "ts", "time") || "").slice(-8, -3) || ""), pick(it, "ticker") ? React.createElement("span", {
    className: "ttk"
  }, it.ticker) : null, React.createElement("span", {
    className: "hl"
  }, pick(it, "title", "headline"))));
  return React.createElement("div", {
    className: "tape",
    title: "Live market headlines — continuously scrolling; hover to pause. Refreshes every 2 minutes."
  }, React.createElement("span", {
    className: "nlab"
  }, "NEWS"), React.createElement("div", {
    className: "tape-view"
  }, items.length === 0 ? React.createElement("span", {
    className: "hl",
    style: {
      color: "var(--fg3)",
      padding: "0 12px"
    }
  }, "headlines loading…") : React.createElement("div", {
    className: "tape-track",
    style: {
      animationDuration: `${Math.max(40, items.length * 7)}s`
    }
  }, track, track.map((el, i) => React.cloneElement(el, {
    key: "b" + i
  })))));
}
function App() {
  const [tab, setTab] = useState(() => {
    try {
      return localStorage.getItem("next_tab") || "today";
    } catch (e) {
      return "today";
    }
  });
  const [ticker, setTicker] = useState(() => {
    try {
      return localStorage.getItem("next_ticker") || "SPY";
    } catch (e) {
      return "SPY";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("next_tab", tab);
    } catch (e) {}
  }, [tab]);
  useEffect(() => {
    try {
      localStorage.setItem("next_ticker", ticker);
    } catch (e) {}
  }, [ticker]);
  const r = usePoll("/api/radar", 45000);
  const alerts = asArr(r.data && r.data.long).concat(asArr(r.data && r.data.short)).filter(s => (pick(s, "score", "total") || 0) >= 80).length;
  const openSym = s => {
    if (s) {
      setTicker(String(s).toUpperCase());
    }
  };
  return React.createElement("div", {
    className: "frame",
    style: {
      minWidth: 0,
      maxWidth: "none",
      border: "none",
      borderRadius: 0,
      boxShadow: "none",
      minHeight: "100vh",
      display: "flex",
      flexDirection: "column"
    }
  }, React.createElement(CommandBar, {
    ticker: ticker,
    setTicker: setTicker,
    alerts: alerts
  }), React.createElement(MarketStrip, null), React.createElement(TabBar, {
    active: tab,
    onChange: setTab
  }), React.createElement("div", {
    className: "body",
    style: {
      flex: 1
    }
  }, React.createElement("div", {
    className: "view"
  }, tab === "today" && React.createElement(Today, {
    onOpen: openSym
  }), React.createElement(ClassicDock, {
    visible: tab !== "today",
    tab: tab,
    ticker: ticker,
    onClassicState: st => {
      if (st.ticker && st.ticker !== ticker) setTicker(String(st.ticker).toUpperCase());
      if (st.tab && st.tab !== tab && CLASSIC_TABS[st.tab] && tab !== "today") setTab(st.tab);
    }
  }))), React.createElement(Tape, null));
}
const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(React.createElement(App, null));
})();
