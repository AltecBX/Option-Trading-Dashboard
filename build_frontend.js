#!/usr/bin/env node
/* build_frontend.js (v2.0)
 *
 * Stage 1 — compile: every .jsx file becomes a plain, readable .js file
 * via @babel/preset-react (CLASSIC runtime — the page loads React as UMD
 * globals, so the automatic runtime's `import "react/jsx-runtime"` would
 * crash). Each output is wrapped in an IIFE to reproduce per-file private
 * scope (files re-declare the same top-level names, e.g. `useState`
 * destructures). No "use strict" injected. These readable artifacts stay
 * committed and are what verify_frontend.js lints/loads.
 *
 * Stage 2 — production assets (new in v2.0): every served script + the
 * stylesheet is minified with esbuild into dist/<name>.min.js|css and
 * pre-compressed to a .gz sibling. The server serves dist/* with
 * immutable long-lived caching; HTML stays no-cache. config.js is
 * deliberately NOT minified/versioned — the user edits it after deploy.
 *
 * Stage 3 — single version source (new in v2.0): APP_VERSION is read
 * from app.jsx and stamped into every `?v=` marker in index.html, and
 * local script/link tags are pointed at dist/*.min.*. Bumping a version
 * is now: edit APP_VERSION in app.jsx, run this script. No manual sed.
 *
 * Run:  node build_frontend.js         (deploy machines never run node)
 */
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const babel = require("@babel/core");
const esbuild = require("esbuild");

const HERE = __dirname;
const JSX_FILES = ["strategies.jsx", "tweaks-panel.jsx", "tooltips.jsx", "charts.jsx",
  "app-lib.jsx", "app-cards.jsx", "app.jsx",
  // Lazy tab chunks (v3.64) — compiled + minified like everything else but
  // NOT referenced by index.html; LazyTab injects them on first tab open.
  "tab-patterns.jsx", "tab-backtest.jsx", "tab-treasuries.jsx", "tab-earnops.jsx"];
// Everything index.html loads locally, in load order. config.js excluded on purpose.
const SERVED_JS = ["data.js", "recommendation.js", "weather.js", "journal.js",
  "strategies.js", "tweaks-panel.js", "tooltips.js", "charts.js",
  "app-lib.js", "app-cards.js", "app.js"];
// On-demand chunks: emitted to dist/ (immutable, ?v= comes from the app tag)
// but never stamped into index.html.
const CHUNK_JS = ["tab-patterns.js", "tab-backtest.js", "tab-treasuries.js", "tab-earnops.js"];
const SERVED_CSS = ["styles.css"];

let failed = false;

/* ── Stage 1: JSX → readable .js ─────────────────────────────────────── */
for (const f of JSX_FILES) {
  const srcPath = path.join(HERE, f);
  const outPath = srcPath.replace(/\.jsx$/, ".js");
  try {
    const result = babel.transformFileSync(srcPath, {
      presets: [["@babel/preset-react", { runtime: "classic" }]],
      compact: false,
      babelrc: false,
      configFile: false,
    });
    const wrapped = "(function () {\n" + result.code + "\n})();\n";
    fs.writeFileSync(outPath, wrapped);
    console.log(`compiled ${f} -> ${path.basename(outPath)} (${(wrapped.length / 1024).toFixed(0)}K)`);
  } catch (e) {
    failed = true;
    console.error(`FAILED ${f}: ${e.message.split("\n")[0]}`);
  }
}
if (failed) process.exit(1);

/* ── Stage 2: minify + precompress into dist/ ────────────────────────── */
const DIST = path.join(HERE, "dist");
fs.mkdirSync(DIST, { recursive: true });
let rawTotal = 0, minTotal = 0, gzTotal = 0;
function emit(name, code, loader) {
  const min = esbuild.transformSync(code, { minify: true, loader, target: "es2019" }).code;
  const outName = loader === "css"
    ? name.replace(/\.css$/, ".min.css")
    : name.replace(/\.js$/, ".min.js");
  const outPath = path.join(DIST, outName);
  fs.writeFileSync(outPath, min);
  const gz = zlib.gzipSync(Buffer.from(min), { level: 9 });
  fs.writeFileSync(outPath + ".gz", gz);
  rawTotal += code.length; minTotal += min.length; gzTotal += gz.length;
  console.log(`minified ${name} -> dist/${outName} (${(code.length / 1024).toFixed(0)}K -> ${(min.length / 1024).toFixed(0)}K, gz ${(gz.length / 1024).toFixed(0)}K)`);
  return outName;
}
try {
  for (const f of SERVED_JS) emit(f, fs.readFileSync(path.join(HERE, f), "utf8"), "js");
  for (const f of CHUNK_JS) emit(f, fs.readFileSync(path.join(HERE, f), "utf8"), "js");
  for (const f of SERVED_CSS) emit(f, fs.readFileSync(path.join(HERE, f), "utf8"), "css");
} catch (e) {
  console.error(`MINIFY FAILED: ${e.message.split("\n")[0]}`);
  process.exit(1);
}
console.log(`dist totals: raw ${(rawTotal / 1024).toFixed(0)}K -> min ${(minTotal / 1024).toFixed(0)}K -> gz ${(gzTotal / 1024).toFixed(0)}K`);

/* ── Stage 3: stamp APP_VERSION into index.html + point at dist ──────── */
const appSrc = fs.readFileSync(path.join(HERE, "app.jsx"), "utf8");
const vm = appSrc.match(/const APP_VERSION = "([^"]+)"/);
if (!vm) { console.error("APP_VERSION not found in app.jsx"); process.exit(1); }
const VER = vm[1];
let html = fs.readFileSync(path.join(HERE, "index.html"), "utf8");
// Point local assets at their minified dist builds (idempotent).
for (const f of SERVED_JS) {
  const base = f.replace(/\.js$/, "");
  html = html.replace(
    new RegExp(`(src=")(?:dist/)?${base}(?:\\.min)?\\.js(\\?v=[^"]*)?(")`, "g"),
    `$1dist/${base}.min.js?v=${VER}$3`);
}
html = html.replace(
  /(href=")(?:dist\/)?styles(?:\.min)?\.css(\?v=[^"]*)?(")/g,
  `$1dist/styles.min.css?v=${VER}$3`);
// Stamp every remaining ?v= marker (config.js, favicons, manifest).
html = html.replace(/\?v=\d+\.\d+(\.\d+)?(-[a-z]+)?/g, `?v=${VER}`);
fs.writeFileSync(path.join(HERE, "index.html"), html);
console.log(`index.html stamped to v${VER}, local assets -> dist/*.min.*`);
process.exit(0);
