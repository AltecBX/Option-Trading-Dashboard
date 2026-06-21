#!/usr/bin/env node
/* verify_frontend.js (v1.40)
 *
 * Two verification layers for the split, precompiled frontend. Run
 * after build_frontend.js, before packaging:  node verify_frontend.js
 *
 * Layer 1, free variable lint. Parses every compiled .js file and
 * collects all identifiers that resolve outside the file's own scope.
 * Each must be a browser or JS global, React, or a window export
 * published by a file earlier in the page load order. This statically
 * proves every cross file reference resolves, which node --check
 * cannot, because a missing global is a runtime ReferenceError, not a
 * syntax error.
 *
 * Layer 2, load harness. Executes all compiled files in page order in
 * one shared vm sandbox with stubbed browser globals (document,
 * localStorage, React, ReactDOM). All top level code runs for real:
 * definitions, window exports, the mount call. Catches load order
 * breaks, missing exports, and load time crashes that static analysis
 * cannot, for example a component reading a global at module scope.
 * Components' render bodies do not run here; the lint covers those
 * statically. Neither layer replaces a real browser render.
 *
 * Exit code 0 only if both layers pass.
 */
const fs = require("fs");
const vm = require("vm");
const parser = require("@babel/parser");
const traverse = require("@babel/traverse").default;

const ORDER = ["config.js", "data.js", "recommendation.js", "weather.js",
               "journal.js", "strategies.js", "tweaks-panel.js", "tooltips.js",
               "charts.js", "app-lib.js", "app-cards.js", "app.js"];

// ── Layer 1: free variable lint ─────────────────────────────────────
const ENV = new Set(("window document navigator localStorage sessionStorage fetch console " +
  "setTimeout clearTimeout setInterval clearInterval requestAnimationFrame cancelAnimationFrame " +
  "Date Math JSON Object Array String Number Boolean Promise Map Set WeakMap WeakSet Symbol " +
  "RegExp Error TypeError RangeError SyntaxError isNaN isFinite parseInt parseFloat NaN Infinity " +
  "undefined encodeURIComponent decodeURIComponent encodeURI decodeURI atob btoa Blob URL URLSearchParams Response Request Headers " +
  "AbortController Intl performance crypto location history alert confirm prompt getComputedStyle " +
  "ResizeObserver IntersectionObserver MutationObserver CustomEvent Event KeyboardEvent MouseEvent " +
  "React ReactDOM globalThis structuredClone queueMicrotask matchMedia FileReader Image Audio " +
  "WebSocket XMLHttpRequest Notification screen devicePixelRatio innerWidth innerHeight " +
  "addEventListener removeEventListener dispatchEvent arguments " +
  // Dual export pattern in recommendation/weather/journal: typeof
  // guarded, safe by construction.
  "module exports require process global").split(/\s+/));

let lintFailed = false;
const exported = new Set();
for (const f of ORDER) {
  const src = fs.readFileSync(f, "utf8");
  const ast = parser.parse(src, { sourceType: "script" });
  const free = new Set();
  traverse(ast, {
    Program(path) {
      for (const name of Object.keys(path.scope.globals)) free.add(name);
    }
  });
  const missing = [...free].filter(n => !ENV.has(n) && !exported.has(n));
  if (missing.length) {
    lintFailed = true;
    console.log(`LINT FAIL ${f}: unresolved -> ${missing.join(", ")}`);
  }
  for (const m of src.matchAll(/window\.([A-Za-z_$][\w$]*)\s*=/g)) exported.add(m[1]);
  for (const m of src.matchAll(/Object\.assign\(window,\s*\{([\s\S]*?)\}\)/g))
    for (const t of m[1].split(",")) {
      const n = t.trim().split(":")[0].trim();
      if (/^[A-Za-z_$][\w$]*$/.test(n)) exported.add(n);
    }
}
console.log(lintFailed ? "LAYER 1 free variable lint: FAIL"
                       : "LAYER 1 free variable lint: PASS (every reference resolves in load order)");

// ── Layer 2: load harness ───────────────────────────────────────────
const storage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const elStub = () => ({ style: {}, classList: { add() {}, remove() {}, toggle() {} },
  addEventListener() {}, removeEventListener() {}, appendChild() {}, setAttribute() {},
  getContext: () => ({ measureText: () => ({ width: 10 }), fillRect() {}, clearRect() {} }) });
const documentStub = {
  hidden: false, getElementById: () => elStub(), querySelector: () => elStub(),
  querySelectorAll: () => [], createElement: () => elStub(),
  addEventListener() {}, removeEventListener() {},
  documentElement: elStub(), body: elStub(), head: elStub(),
};
let rendered = 0;
const ReactStub = new Proxy({
  createElement: (...a) => ({ _el: a[0] }),
  Component: class { constructor(p) { this.props = p; } setState() {} },
  Fragment: "frag", StrictMode: "strict",
  useState: (i) => [i, () => {}], useEffect: () => {}, useMemo: (f) => f(),
  useRef: (i) => ({ current: i }), useCallback: (f) => f, useContext: () => ({}),
  useReducer: (r, i) => [i, () => {}], useLayoutEffect: () => {},
}, { get: (t, k) => (k in t ? t[k] : (() => {})) });
const ReactDOMStub = { createRoot: () => ({ render: () => { rendered++; } }) };

const sandbox = {
  console, Date, Math, JSON, Object, Array, String, Number, Boolean, Promise,
  Map, Set, RegExp, Error, TypeError, isNaN, isFinite, parseInt, parseFloat,
  Intl, URL, URLSearchParams, setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: () => 0, fetch: () => new Promise(() => {}),
  navigator: { userAgent: "harness", language: "en-US", geolocation: { getCurrentPosition() {} } },
  localStorage: storage, sessionStorage: storage, document: documentStub,
  React: ReactStub, ReactDOM: ReactDOMStub, performance: { now: () => 0 },
  location: { href: "http://localhost/", origin: "http://localhost" },
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

let loadFailed = false;
for (const f of ORDER) {
  try {
    vm.runInContext(fs.readFileSync(f, "utf8"), sandbox, { filename: f, timeout: 30000 });
  } catch (e) {
    loadFailed = true;
    console.log(`LOAD FAIL ${f}: ${e.message}`);
  }
}
const expects = ["fmt$", "fmtPct", "CardErrorBoundary", "RootErrorBoundary",
                 "skipWhenHidden", "LevelRepriceCard", "WinRateCard", "TweaksPanel",
                 "PriceChart", "Term", "OptionStrats", "APP_VERSION"];
const missing = expects.filter(n => !(n in sandbox));
if (missing.length) { loadFailed = true; console.log("MISSING EXPORTS: " + missing.join(", ")); }
if (rendered !== 1) { loadFailed = true; console.log(`MOUNT: createRoot render ran ${rendered} times, expected 1`); }
console.log(loadFailed ? "LAYER 2 load harness: FAIL"
                       : "LAYER 2 load harness: PASS (all files executed, exports present, mount ran once)");

process.exit(lintFailed || loadFailed ? 1 : 0);
