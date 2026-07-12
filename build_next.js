#!/usr/bin/env node
/* build_next.js — compiles ONLY next-app.jsx → next-app.js for the parallel
 * /next app. Deliberately separate from build_frontend.js so the classic
 * site's build is never touched. Same babel setup (classic JSX runtime,
 * React via UMD globals, IIFE wrap, no "use strict").
 */
const fs = require("fs");
const path = require("path");
const babel = require("@babel/core");

const srcPath = path.join(__dirname, "next-app.jsx");
const outPath = path.join(__dirname, "next-app.js");
try {
  const result = babel.transformFileSync(srcPath, {
    presets: [["@babel/preset-react", { runtime: "classic" }]],
    babelrc: false, configFile: false, compact: false, comments: false,
  });
  fs.writeFileSync(outPath, "(function(){\n" + result.code + "\n})();\n");
  console.log(`compiled next-app.jsx -> next-app.js (${Math.round(fs.statSync(outPath).size / 1024)}K)`);
} catch (e) {
  console.error("BUILD FAILED:", e.message);
  process.exit(1);
}
