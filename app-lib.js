(function () {
// Shared helpers, constants, and error boundaries — split out of the app.jsx monolith (v1.40).
// Loads before app.js; every binding is published to window so later
// files resolve bare references exactly as they did in one file.

const {
  useState,
  useEffect,
  useMemo,
  useRef
} = React;
const skipWhenHidden = fn => (...args) => {
  if (typeof document !== "undefined" && document.hidden) return;
  return fn(...args);
};
const ACCENT_PRESETS = {
  emerald: {
    h: 152,
    c: 0.16,
    l: 0.55,
    name: "Emerald"
  },
  indigo: {
    h: 264,
    c: 0.17,
    l: 0.55,
    name: "Indigo"
  },
  amber: {
    h: 70,
    c: 0.16,
    l: 0.62,
    name: "Amber"
  },
  rose: {
    h: 12,
    c: 0.18,
    l: 0.58,
    name: "Rose"
  },
  teal: {
    h: 195,
    c: 0.13,
    l: 0.55,
    name: "Teal"
  }
};
function fmt$M(v) {
  const n = typeof v === "number" ? v : v == null ? null : Number(v);
  if (n == null || !isFinite(n) || n === 0) return "—";
  const a = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(0)}K`;
  return `${sign}$${a.toFixed(0)}`;
}
function fmtPct(v, opts) {
  const n = typeof v === "number" ? v : v == null ? null : Number(v);
  if (n == null || !isFinite(n)) return "—";
  // Accept both calling conventions:
  //   fmtPct(v)                              → signed, 2 decimals
  //   fmtPct(v, 0)                           → signed, 0 decimals (legacy)
  //   fmtPct(v, {digits: 0, signed: false})  → object form
  let digits = 2;
  let signed = true;
  if (typeof opts === "number") {
    digits = opts;
  } else if (opts && typeof opts === "object") {
    if (typeof opts.digits === "number") digits = opts.digits;
    if (opts.signed === false) signed = false;
  }
  const prefix = signed && n >= 0 ? "+" : "";
  return prefix + n.toFixed(digits) + "%";
}
function fmtVol(v) {
  const n = typeof v === "number" ? v : v == null ? null : Number(v);
  if (n == null || !isFinite(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return Math.round(n).toLocaleString();
}
function fmt$(v, digits) {
  const n = typeof v === "number" ? v : v == null ? null : Number(v);
  if (n == null || !isFinite(n)) return "—";
  const d = typeof digits === "number" ? digits : 2;
  return "$" + n.toFixed(d);
}
window.fmt$M = fmt$M;
window.fmtPct = fmtPct;
window.fmtVol = fmtVol;
window.fmt$ = fmt$;
class CardErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null
    };
  }
  static getDerivedStateFromError(error) {
    return {
      error
    };
  }
  componentDidCatch(error, info) {
    console.error("Card crashed:", error, info);
  }
  render() {
    if (this.state.error) {
      return /*#__PURE__*/React.createElement("div", {
        className: "card card-error"
      }, /*#__PURE__*/React.createElement("div", {
        className: "kicker"
      }, this.props.label || "Card", " failed to render"), /*#__PURE__*/React.createElement("div", {
        className: "card-error-msg"
      }, String(this.state.error.message || this.state.error)), /*#__PURE__*/React.createElement("button", {
        className: "card-error-btn",
        onClick: () => this.setState({
          error: null
        })
      }, "Retry"));
    }
    return this.props.children;
  }
}
const TABS = [{
  id: "trade",
  label: "Trade"
}, {
  id: "discover",
  label: "Discover"
}, {
  id: "analyze",
  label: "Analyze"
}, {
  id: "patterns",
  label: "Patterns"
}, {
  id: "news",
  label: "News"
}, {
  id: "finviz",
  label: "Finviz"
}, {
  id: "tview",
  label: "TradingView"
}, {
  id: "whales",
  label: "Unusual Whales"
}, {
  id: "flow",
  label: "Flow"
}, {
  id: "scanners",
  label: "Scanners"
}, {
  id: "juice",
  label: "0DTE Juice"
}, {
  id: "backtest",
  label: "Backtest"
}, {
  id: "breadth",
  label: "Breadth"
}, {
  id: "journal",
  label: "Journal"
}, {
  id: "watchlist",
  label: "Watchlist"
}, {
  id: "streaks",
  label: "Streaks"
}, {
  id: "calendar",
  label: "Market Calendar"
}, {
  id: "manage",
  label: "Manage"
}];
const TAB_KEY = "jerry_active_tab_v1";
class RootErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null
    };
  }
  static getDerivedStateFromError(error) {
    return {
      error
    };
  }
  componentDidCatch(error, info) {
    console.error("App crashed:", error, info);
  }
  render() {
    if (this.state.error) {
      return /*#__PURE__*/React.createElement("div", {
        style: {
          padding: 32,
          fontFamily: "system-ui",
          background: "#0b0d12",
          color: "#fafafa",
          minHeight: "100vh"
        }
      }, /*#__PURE__*/React.createElement("h2", {
        style: {
          color: "#dc2626"
        }
      }, "Dashboard crashed"), /*#__PURE__*/React.createElement("pre", {
        style: {
          whiteSpace: "pre-wrap",
          fontSize: 12,
          color: "#9ca3af",
          maxWidth: 800
        }
      }, String(this.state.error?.stack || this.state.error?.message || this.state.error)), /*#__PURE__*/React.createElement("button", {
        style: {
          padding: "8px 16px",
          marginTop: 16,
          background: "#16a34a",
          color: "white",
          border: "none",
          borderRadius: 6,
          cursor: "pointer"
        },
        onClick: () => location.reload()
      }, "Reload page"), /*#__PURE__*/React.createElement("button", {
        style: {
          padding: "8px 16px",
          marginTop: 16,
          marginLeft: 8,
          background: "#374151",
          color: "white",
          border: "none",
          borderRadius: 6,
          cursor: "pointer"
        },
        onClick: () => this.setState({
          error: null
        })
      }, "Try again"));
    }
    return this.props.children;
  }
}

// ── Shared JSON fetch (v3.07) ──────────────────────────────────────────────
// Several components poll the SAME endpoints on their own timers (the 1,285-row
// watchlist board alone had 6 independent pollers; broker/owned had 4). This
// dedupes them: identical GETs within the TTL share one cached result, and
// concurrent calls share one in-flight request. Components keep their own
// polling loops — only the network call is coalesced.
const _SJ_CACHE = new Map(); // url -> { t, data }
const _SJ_INFLIGHT = new Map(); // url -> promise
function sharedJson(apiFetch, url, ttlMs = 15000) {
  const hit = _SJ_CACHE.get(url);
  if (hit && Date.now() - hit.t < ttlMs) return Promise.resolve(hit.data);
  if (_SJ_INFLIGHT.has(url)) return _SJ_INFLIGHT.get(url);
  const p = apiFetch(url).then(r => r.json()).then(d => {
    _SJ_CACHE.set(url, {
      t: Date.now(),
      data: d
    });
    _SJ_INFLIGHT.delete(url);
    return d;
  }).catch(e => {
    _SJ_INFLIGHT.delete(url);
    throw e;
  });
  _SJ_INFLIGHT.set(url, p);
  return p;
}

// ── Finviz embed helper (v3.25) ─────────────────────────────────────────────
// The Finviz tab renders finviz.com INSIDE the dashboard via an iframe. That
// only works when the user has installed the JerryTrade Finviz Helper — a
// tiny user-consented browser extension (official declarativeNetRequest API)
// whose sole capability is letting THIS dashboard embed finviz.com. The
// helper's content script announces itself by setting
// documentElement.dataset.finvizHelper and firing 'finviz-helper-ready'.
// No proxying, no scraping: Finviz loads straight from Finviz's servers with
// the user's own cookies, so the real Elite login and account data apply.
const FINVIZ = {
  base() {
    return localStorage.getItem("jerry_finviz_base") === "free" ? "https://finviz.com" : "https://elite.finviz.com";
  },
  setBase(v) {
    try {
      localStorage.setItem("jerry_finviz_base", v);
    } catch (e) {}
  },
  follow() {
    return localStorage.getItem("jerry_finviz_follow") !== "0";
  },
  // default ON
  setFollow(v) {
    try {
      localStorage.setItem("jerry_finviz_follow", v ? "1" : "0");
    } catch (e) {}
  },
  quoteUrl(sym) {
    return `${this.base()}/quote.ashx?t=${encodeURIComponent(sym)}&p=d`;
  },
  helperPresent() {
    try {
      return document.documentElement.dataset.finvizHelper === "1";
    } catch (e) {
      return false;
    }
  },
  isMobile() {
    try {
      return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
    } catch (e) {
      return false;
    }
  }
};

// ── TradingView embed helper (v3.33) ────────────────────────────────────────
// Mirror of FINVIZ for tradingview.com — same helper extension (v2.0+)
// unlocks the frame; the in-frame script reports the active chart symbol.
const TVIEW = {
  chartUrl(sym) {
    return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(sym)}`;
  },
  url(path) {
    return `https://www.tradingview.com${path}`;
  },
  follow() {
    return localStorage.getItem("jerry_tv_follow") !== "0";
  },
  // default ON
  setFollow(v) {
    try {
      localStorage.setItem("jerry_tv_follow", v ? "1" : "0");
    } catch (e) {}
  },
  helperVersion() {
    try {
      return parseFloat(document.documentElement.dataset.finvizHelperVersion || "0");
    } catch (e) {
      return 0;
    }
  }
};

// ── Unusual Whales embed helper (v3.34) ─────────────────────────────────────
// UW does NOT block framing, so the embed works even without the helper;
// helper v2.1+ makes the LOGIN persist inside the frame (cookie handling).
const UWHALES = {
  stockUrl(sym) {
    return `https://unusualwhales.com/stock/${encodeURIComponent(sym)}/overview`;
  },
  url(path) {
    return `https://unusualwhales.com${path}`;
  },
  follow() {
    return localStorage.getItem("jerry_uw_follow") !== "0";
  },
  // default ON
  setFollow(v) {
    try {
      localStorage.setItem("jerry_uw_follow", v ? "1" : "0");
    } catch (e) {}
  }
};

// Shared US date format (M-D-YYYY, e.g. 6-19-2026) used app-wide.
function fmtUSDate(s) {
  if (!s) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s));
  if (!m) return String(s);
  return `${+m[2]}-${+m[3]}-${m[1]}`;
}
Object.assign(window, {
  useState,
  useEffect,
  useMemo,
  useRef,
  skipWhenHidden,
  ACCENT_PRESETS,
  fmt$M,
  fmtPct,
  fmtVol,
  fmt$,
  CardErrorBoundary,
  TABS,
  TAB_KEY,
  RootErrorBoundary,
  fmtUSDate,
  sharedJson,
  FINVIZ,
  TVIEW,
  UWHALES
});
})();
