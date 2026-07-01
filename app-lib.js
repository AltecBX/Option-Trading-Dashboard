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
  id: "flow",
  label: "Flow"
}, {
  id: "scanners",
  label: "Scanners"
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
  fmtUSDate
});
})();
