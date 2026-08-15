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
  "app-lib.jsx", "timing.jsx", "app-cards.jsx", "app.jsx",
  // Lazy tab chunks (v3.64) — compiled + minified like everything else but
  // NOT referenced by index.html; LazyTab injects them on first tab open.
  "tab-patterns.jsx", "tab-backtest.jsx", "tab-treasuries.jsx", "tab-earnops.jsx",
  "tab-recovery.jsx", "tab-ask.jsx"];
// Everything index.html loads locally, in load order. config.js excluded on purpose.
const SERVED_JS = ["data.js", "recommendation.js", "weather.js", "journal.js",
  "strategies.js", "tweaks-panel.js", "tooltips.js", "charts.js",
  "app-lib.js", "timing.js", "app-cards.js", "app.js"];
// On-demand chunks: emitted to dist/ (immutable, ?v= comes from the app tag)
// but never stamped into index.html.
const CHUNK_JS = ["tab-patterns.js", "tab-backtest.js", "tab-treasuries.js", "tab-earnops.js",
  "tab-recovery.js", "tab-ask.js"];
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

/* ── Stage 3: stamp CONTENT HASHES into index.html + point at dist ─────
 *
 * These `?v=` markers used to carry APP_VERSION. That silently served stale
 * code: v3.73, v3.73a, v3.73b, v3.73c and v3.73d all stamped "3.73", so the
 * URL never changed, so every browser kept the bundle it had cached from the
 * FIRST v3.73 deploy — users saw none of the follow-up fixes and no amount of
 * merging or deploying could reach them.
 *
 * The marker is now an 8-char hash of each file's own built bytes. Change a
 * file and only that file's URL changes; change nothing and the URL is
 * stable (so the build stays idempotent and caches stay warm). A release no
 * longer depends on remembering to bump a version string.
 */
const crypto = require("crypto");
const appSrc = fs.readFileSync(path.join(HERE, "app.jsx"), "utf8");
const vm = appSrc.match(/const APP_VERSION = "([^"]+)"/);
if (!vm) { console.error("APP_VERSION not found in app.jsx"); process.exit(1); }
const VER = vm[1];                       // still the human-facing badge

const hashOf = (absPath) => {
  try {
    return crypto.createHash("sha1")
      .update(fs.readFileSync(absPath)).digest("hex").slice(0, 8);
  } catch (e) {
    return null;                         // missing file: fall back to VER
  }
};

let html = fs.readFileSync(path.join(HERE, "index.html"), "utf8");
// Point local assets at their minified dist builds (idempotent), each tagged
// with the hash of the built file it actually points at.
for (const f of SERVED_JS) {
  const base = f.replace(/\.js$/, "");
  const h = hashOf(path.join(DIST, `${base}.min.js`)) || VER;
  html = html.replace(
    new RegExp(`(src=")(?:dist/)?${base}(?:\\.min)?\\.js(\\?v=[^"]*)?(")`, "g"),
    `$1dist/${base}.min.js?v=${h}$3`);
}
const cssHash = hashOf(path.join(DIST, "styles.min.css")) || VER;
html = html.replace(
  /(href=")(?:dist\/)?styles(?:\.min)?\.css(\?v=[^"]*)?(")/g,
  `$1dist/styles.min.css?v=${cssHash}$3`);

// Remaining ?v= markers are non-dist assets (config.js, favicons, manifest).
// Hash them from their own source file where we can find one, so they bust
// independently too; anything unresolvable keeps the app version.
html = html.replace(/(?:src|href)="([^"?]+)\?v=[^"]*"/g, (m, asset) => {
  if (asset.startsWith("dist/")) return m;      // already handled above
  const h = hashOf(path.join(HERE, asset)) || VER;
  return m.replace(/\?v=[^"]*"/, `?v=${h}"`);
});

fs.writeFileSync(path.join(HERE, "index.html"), html);
console.log(`index.html stamped with content hashes (app v${VER}), local assets -> dist/*.min.*`);
process.exit(0);
