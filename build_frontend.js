#!/usr/bin/env node
/* build_frontend.js (v1.38)
 *
 * Compiles every .jsx file to a plain .js file with @babel/preset-react
 * so the browser never runs Babel. Before this, index.html loaded
 * @babel/standalone and transformed roughly 700K of JSX on the main
 * thread on every page load, which was seconds of blank screen.
 *
 * Each compiled file is wrapped in an IIFE. This matters: under Babel
 * standalone every text/babel script evaluated in its own scope, so
 * files could declare the same top level names, for example app.jsx and
 * charts.jsx both destructure useState from React. As plain classic
 * scripts those would collide at global scope and the page would die on
 * load. The IIFE reproduces the original file private scoping exactly.
 * Cross file symbols already travel through explicit window assignments
 * (Object.assign(window, ...)) so they are unaffected.
 *
 * No "use strict" is injected, preserving the original sloppy mode
 * semantics byte for byte.
 *
 * Run from the bundle directory:  node build_frontend.js
 * Requires @babel/core and @babel/preset-react resolvable from the
 * working directory or a parent (the packaging environment has them).
 * The shipped zip contains the compiled .js, so nothing needs to run
 * on the deployment machine.
 */
const fs = require("fs");
const path = require("path");
const babel = require("@babel/core");

const FILES = ["strategies.jsx", "tweaks-panel.jsx", "tooltips.jsx", "charts.jsx", "app-lib.jsx", "app-cards.jsx", "app.jsx"];

let failed = false;
for (const f of FILES) {
  const srcPath = path.join(__dirname, f);
  const outPath = srcPath.replace(/\.jsx$/, ".js");
  try {
    const result = babel.transformFileSync(srcPath, {
      // Pin the CLASSIC runtime (React.createElement). The page loads React
      // via UMD globals, not a module bundler, so the automatic runtime —
      // which newer Babel defaults to — would emit `import ... from
      // "react/jsx-dev-runtime"` and crash every page in the browser.
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
process.exit(failed ? 1 : 0);
